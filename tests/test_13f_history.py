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
