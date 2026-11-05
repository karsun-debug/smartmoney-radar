"""Regression tests for issues found in code review."""
from datetime import date, timedelta

from spiderhead.alerts import run_alerts
from spiderhead.ark import parse_ark_csv
from spiderhead.dashboard import build_site, safe_url
from spiderhead.house import PtrRow, rows_to_trades
from spiderhead.models import Post, Trade
from spiderhead.performance import PriceBook, score_calls
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.trades import parse_congress_rows
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


def test_feed_links_must_be_http(tmp_path):
    assert safe_url("javascript:alert(1)") is None and safe_url(" https://a.b/c ") == "https://a.b/c"
    store = Store(":memory:")
    store.add_posts([Post("ackman", TODAY, "We bought $GOOGL", "rss", "javascript:alert(1)")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    build_site(store, load_watchlist(), tmp_path, days=7)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "javascript:" not in html


def test_late_filed_congress_trade_kept():
    row = {"member": {"name": "Hon. Nancy Pelosi"}, "transactedAt": str(TODAY - timedelta(days=40)),
           "filedAt": str(TODAY - timedelta(days=1)), "ticker": "BE", "assetDescription": "Bloom (BE) [ST]",
           "side": "buy", "amountRange": {"text": "$1,001 - $15,000"}, "owner": "spouse",
           "provenance": {"sourceUrl": "https://disclosures-clerk.house.gov/x.pdf"}}
    assert len(parse_congress_rows([row], {"pelosi": "pelosi"}, TODAY - timedelta(days=30))) == 1


def _trade(days_ago=0):
    return Trade("pelosi", TODAY - timedelta(days=10), TODAY - timedelta(days=days_ago), "buy", "PTR",
                 "NVDA", "NVIDIA", amount_text="$1,001 - $15,000（配偶）", url="https://pdf/1")


def test_first_alert_not_swallowed_after_empty_first_run():
    store, inv, sent = Store(":memory:"), load_watchlist(), []
    assert run_alerts(store, inv, sent.append) == (0, "")       # first run: nothing pending
    store.add_trades([_trade()])
    n, _ = run_alerts(store, inv, sent.append)
    assert n == 1 and len(sent) == 1


def test_dry_run_first_run_records_nothing():
    store, inv = Store(":memory:"), load_watchlist()
    store.add_trades([_trade()])
    run_alerts(store, inv, None, dry_run=True)
    assert store.alerts_count() == 0


def test_ptr_uid_same_across_sources():
    house = rows_to_trades([PtrRow("SP", "Bloom Energy Corporation Class A Common Stock (BE) [ST]", "P",
                                   "07/28/2026", "$500,001 - $1,000,000")], "pelosi", date(2026, 8, 21),
                           "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf")
    lux = parse_congress_rows([{
        "member": {"name": "Hon. Nancy Pelosi"}, "transactedAt": "2026-07-28", "filedAt": "2026-08-21",
        "ticker": "BE", "side": "buy", "owner": "spouse",
        "assetDescription": "Bloom Energy Corporation Class A Common Stock (BE) [ST] F S: New D: Purchased 5,000 shares.",
        "amountRange": {"text": "$500,001 - $1,000,000"},
        "provenance": {"sourceUrl": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf"}}],
        {"pelosi": "pelosi"})
    assert house[0].uid == lux[0].uid
    option = Trade(**{**house[0].__dict__, "amount_text": house[0].amount_text + " · 選擇權"})
    assert option.uid != house[0].uid


def test_ark_cash_rows_ignored():
    csv = ('date,fund,company,ticker,cusip,shares,"market value ($)","weight (%)"\n'
           '11/04/2026,ARKK,"TESLA INC",TSLA,88160R101,"100","$30,000",1%\n'
           '11/04/2026,ARKK,"CASH & OTHER",,,"5,000,000","$5,000,000",2%\n')
    _, rows = parse_ark_csv(csv)
    assert [r.ticker for r in rows] == ["TSLA"]


def test_stooq_non_csv_is_an_error():
    pb = PriceBook(Store(":memory:"), lambda url, h: b"<html>Get your API key</html>")
    assert pb.series("AAPL") == [] and pb.errors and "no data" in pb.errors[0]


def test_echoed_call_counted_once():
    store = Store(":memory:")
    rows = "\n".join(["Date,Open,High,Low,Close,Volume"] +
                     [f"{TODAY - timedelta(days=60 - i)},1,1,1,{100 + i},1" for i in range(61)])
    pb = PriceBook(store, lambda url, h: rows.encode())
    posts = [Post("ackman", TODAY - timedelta(days=50 - d), f"Ackman says he bought $GOOGL ({d})", "news",
                  f"https://n/{d}", "news") for d in (0, 1, 2)]
    posts.append(Post("ackman", TODAY - timedelta(days=30), "Ackman says he bought more $GOOGL", "news",
                      "https://n/later", "news"))
    sigs = [s for p in posts for s in KeywordAnalyzer().analyze(p)]
    assert len(score_calls(sigs, pb)) == 2          # 3 echoes count once, a later call counts again
