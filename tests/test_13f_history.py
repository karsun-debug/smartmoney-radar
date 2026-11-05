from datetime import date
from pathlib import Path

import spiderhead.edgar as edgar_mod
from spiderhead.cli import _refresh_13f
from spiderhead.dashboard import build_site, holdings_from_store
from spiderhead.holdings import TickerResolver, parse_infotable, summarize_quarter
from spiderhead.models import Filing, Post
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

FIX = Path(__file__).parent / "fixtures"
TICK = TickerResolver({"037833100": "AAPL", "060505104": "BAC", "191216100": "KO",
                       "833445109": "SNOW", "78462F103": "SPY"}, {"Chubb Ltd": "CB"})


def _store():
    store = Store(":memory:")
    store.save_13f("buffett", "2026-06-30", "a1", parse_infotable((FIX / "prev_q.xml").read_bytes()), TICK)
    store.save_13f("buffett", "2026-09-30", "a2", parse_infotable((FIX / "curr_q.xml").read_bytes()), TICK)
    return store


def test_summary_top_holdings():
    s = summarize_quarter("q", parse_infotable((FIX / "curr_q.xml").read_bytes()), TICK)
    assert s.positions == 5 and s.top[0][0] == "AAPL" and round(sum(w for _, w in s.top), 6) == 1.0


def test_dashboard_uses_stored_13f(tmp_path):
    store = _store()
    store.add_posts([Post("buffett", date(2026, 8, 10), "Apple is a great business, we are long $AAPL", "letter")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    quarter, changes, checks, history = holdings_from_store(store, load_watchlist()["buffett"])
    assert quarter == "2026-09-30" and [h.quarter for h in history] == ["2026-09-30", "2026-06-30"]
    assert {c.ticker: c.change.value for c in changes}["AAPL"] == "TRIM"
    assert [c.verdict.value for c in checks] == ["contradicts"]
    build_site(store, load_watchlist(), tmp_path, days=7)
    page = (tmp_path / "investor" / "buffett.html").read_text(encoding="utf-8")
    assert "各季持倉概況" in page and "最新一季變動" in page and "言行不一" in page


def test_refresh_13f_skips_known_quarters(monkeypatch):
    fetched = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def latest_13f_per_quarter(self, cik, quarters):
            if cik != load_watchlist()["buffett"].cik:
                return []
            return [Filing(cik, "a2", "13F-HR", date(2026, 11, 14), date(2026, 9, 30)),
                    Filing(cik, "a1", "13F-HR", date(2026, 8, 14), date(2026, 6, 30))]

        def fetch_infotable_xml(self, f):
            fetched.append(f.accession)
            return (FIX / ("curr_q.xml" if f.accession == "a2" else "prev_q.xml")).read_bytes()

        def company_name_map(self):
            return {"Chubb Limited": "CB"}

    monkeypatch.setattr(edgar_mod, "EdgarClient", FakeClient)
    monkeypatch.setattr("spiderhead.holdings.OpenFigi.lookup", lambda self, cusip: None)
    store = Store(":memory:")
    inv = load_watchlist()
    _refresh_13f(store, inv)
    assert sorted(fetched) == ["a1", "a2"] and store.quarters_13f("buffett") == ["2026-09-30", "2026-06-30"]
    _refresh_13f(store, inv)
    assert len(fetched) == 2                               # nothing re-downloaded
    health = {h.family: h for h in store.source_health()}
    assert health["sec13f"].level == "ok"


def test_consensus_page_and_ticker_owners(tmp_path):
    from spiderhead.consensus13f import build_consensus
    store = _store()
    # Ackman: holds AAPL both quarters and adds; buys KO new.
    from spiderhead.models import Holding
    store.save_13f("ackman", "2026-06-30", "b1", [Holding("APPLE INC", "037833100", "COM", 10_000, 50)],
                   lambda h: "AAPL")
    store.save_13f("ackman", "2026-09-30", "b2", [Holding("APPLE INC", "037833100", "COM", 20_000, 90),
                                                 Holding("COCA COLA CO", "191216100", "COM", 5_000, 70)],
                   lambda h: {"037833100": "AAPL", "191216100": "KO"}[h.cusip])
    c = build_consensus(store, load_watchlist())
    top = c.most_owned()[0]
    assert top.key == "AAPL" and set(top.owners) == {"buffett", "ackman"}
    ko = c.rows["KO"]
    assert ko.bought == {"ackman": "NEW"} and set(ko.owners) == {"buffett", "ackman"}
    assert "SPY" not in c.rows                                 # options excluded
    assert c.rows["SNOW"].sold == {"buffett": "EXIT"}
    build_site(store, load_watchlist(), tmp_path, days=7)
    page = (tmp_path / "consensus.html").read_text(encoding="utf-8")
    assert "最多大神持有" in page and "Bill Ackman" in page
    tpage = (tmp_path / "ticker" / "AAPL.html").read_text(encoding="utf-8")
    assert "13F：哪些大神持有" in tpage and "Warren Buffett" in tpage


def _csv(start, days, p0, step):
    from datetime import timedelta
    rows = ["Date,Open,High,Low,Close,Volume"]
    rows += [f"{start + timedelta(days=i)},1,1,1,{p0 + step * i:.4f},1" for i in range(days)]
    return "\n".join(rows).encode()


def test_clone_backtest_vs_spy(tmp_path):
    from datetime import timedelta
    from spiderhead.clone import backtest
    from spiderhead.models import Holding
    from spiderhead.performance import PriceBook
    today = date(2026, 11, 5)
    store = Store(":memory:")
    q1 = [Holding("APPLE", "A1", "COM", 300, 1), Holding("COKE", "K1", "COM", 100, 1),
          Holding("SPY PUT", "S1", "COM", 999, 1, "SH", "PUT")]
    q2 = [Holding("APPLE", "A1", "COM", 100, 1)]
    tick = {"A1": "AAPL", "K1": "KO", "S1": "SPY"}
    store.save_13f("buffett", "2026-03-31", "x1", q1, lambda h: tick[h.cusip], date(2026, 5, 15))
    store.save_13f("buffett", "2026-06-30", "x2", q2, lambda h: tick[h.cusip], date(2026, 8, 14))
    start = date(2026, 5, 1)
    feeds = {"aapl.us": _csv(start, 200, 100, 0.5),     # rises
             "ko.us": _csv(start, 200, 100, -0.1),      # falls slightly
             "spy.us": _csv(start, 200, 100, 0.1)}
    pb = PriceBook(store, lambda url, h: feeds[url.split("s=")[1].split("&")[0]])
    res = backtest(store, load_watchlist()["buffett"], pb, today=today)
    assert [p.quarter for p in res.periods] == ["2026-03-31", "2026-06-30"]
    p1 = res.periods[0]
    assert (p1.entry, p1.exit) == (date(2026, 5, 15), date(2026, 8, 14))
    assert [t for t, _, _ in p1.holdings] == ["AAPL", "KO"]              # option ignored
    assert abs(sum(w for _, w, _ in p1.holdings) - 1) < 1e-9 and p1.holdings[0][1] == 0.75
    assert p1.ret > p1.bench > 0 and res.excess > 0 and res.beat_rate == 1.0
    build_site(store, load_watchlist(), tmp_path, days=7, prices=pb)
    assert "跟單累積報酬" in (tmp_path / "clone.html").read_text(encoding="utf-8")
    assert "跟單回測" in (tmp_path / "investor" / "buffett.html").read_text(encoding="utf-8")


def test_filed_date_migration_and_default(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE holdings_13f (investor TEXT NOT NULL, quarter TEXT NOT NULL, accession TEXT"
                " NOT NULL, cusip TEXT NOT NULL, put_call TEXT NOT NULL DEFAULT '', ticker TEXT, issuer TEXT,"
                " title TEXT, shares INTEGER, value INTEGER, PRIMARY KEY (investor, quarter, cusip, put_call))")
    con.execute("INSERT INTO holdings_13f VALUES ('b','2026-06-30','a','c','', 'X','I','T',1,1)")
    con.commit()
    con.close()
    store = Store(db)
    assert store.filed_13f("b", "2026-06-30") == date(2026, 8, 14)      # quarter end + 45 days


def test_clone_latest_quarter_uses_last_available_close():
    from spiderhead.clone import backtest
    from spiderhead.models import Holding
    from spiderhead.performance import PriceBook
    store = Store(":memory:")
    store.save_13f("buffett", "2026-09-30", "x", [Holding("APPLE", "A1", "COM", 10, 1)], lambda h: "AAPL",
                   date(2026, 11, 3))
    # Prices stop the day before "today" (normal data lag).
    feeds = {"aapl.us": _csv(date(2026, 11, 1), 4, 100, 1), "spy.us": _csv(date(2026, 11, 1), 4, 100, 0.5)}
    pb = PriceBook(store, lambda url, h: feeds[url.split("s=")[1].split("&")[0]])
    [p] = backtest(store, load_watchlist()["buffett"], pb, today=date(2026, 11, 5)).periods
    assert p.exit == date(2026, 11, 5) and round(p.ret, 2) == round((103 / 102 - 1) * 100, 2)
