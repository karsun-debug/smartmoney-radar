import json
from datetime import date

from spiderhead.financials import parse_company_facts, quarterly_flow, yoy


def f(start, end, val, form="10-Q", filed="2026-01-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def facts():
    rev = [
        # FY2025 (Jan–Dec): Q1 direct; 6M and 9M year-to-date; FY annual.
        f("2025-01-01", "2025-03-31", 100), f("2025-01-01", "2025-06-30", 210), f("2025-01-01", "2025-09-30", 330),
        f("2025-01-01", "2025-12-31", 460, "10-K"),
        # FY2026: quarters reported directly (+ a restated duplicate filed later wins).
        f("2026-01-01", "2026-03-31", 120), f("2026-04-01", "2026-06-30", 125),
        f("2026-04-01", "2026-06-30", 126, filed="2026-12-01"),
        f("2026-07-01", "2026-09-30", 150),
        f("2026-07-01", "2026-09-30", 999, form="8-K"),                       # ignored form
    ]
    ni = [f("2025-07-01", "2025-09-30", 20), f("2026-07-01", "2026-09-30", 30)]
    eps = [f("2025-07-01", "2025-09-30", 1.0), f("2026-07-01", "2026-09-30", 1.5)]
    ocf = [f("2026-01-01", "2026-06-30", 80), f("2026-01-01", "2026-09-30", 130)]
    capex = [f("2026-01-01", "2026-06-30", 20), f("2026-01-01", "2026-09-30", 35)]
    cash = [{"end": "2026-09-30", "val": 500, "form": "10-Q", "filed": "2026-11-01"}]
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": rev}},
        "NetIncomeLoss": {"units": {"USD": ni}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": capex}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": cash}},
    }}}


def test_quarterly_flow_derives_from_ytd_and_annual():
    q = quarterly_flow(facts()["facts"]["us-gaap"]["Revenues"]["units"]["USD"])
    assert q[date(2025, 6, 30)] == 110 and q[date(2025, 9, 30)] == 120 and q[date(2025, 12, 31)] == 130
    assert q[date(2026, 6, 30)] == 126                         # latest filing wins
    assert q[date(2026, 9, 30)] == 150                         # 8-K ignored


def test_parse_company_facts_and_ratios():
    qs = parse_company_facts(json.dumps(facts()))
    last = qs[-1]
    assert last.end == date(2026, 9, 30) and last.get("revenue") == 150
    assert last.get("ocf") == 50 and last.get("capex") == 15 and last.fcf == 35
    assert last.get("cash") == 500 and round(last.margin("net_income"), 1) == 20.0
    assert round(yoy(qs, last, "revenue"), 1) == 25.0           # 150 vs 120
    assert round(yoy(qs, last, "eps"), 1) == 50.0


def test_store_roundtrip_pages_and_focus(tmp_path, monkeypatch):
    from datetime import timedelta
    from spiderhead.dashboard import build_site
    from spiderhead.financials import fetch_earnings_news, fetch_financials, focus_tickers
    from spiderhead.models import Trade
    from spiderhead.store import Store
    from spiderhead.watchlist import load_watchlist
    store = Store(":memory:")
    today = date.today()
    store.add_trades([Trade("pelosi", today, today, "buy", "PTR", "NVDA", "NVIDIA")])
    monkeypatch.setenv("SPIDERHEAD_MY_TICKERS", "aapl")
    focus = focus_tickers(store, load_watchlist())
    assert focus[:2] == ["AAPL", "NVDA"]
    got = []
    n = fetch_financials(store, lambda url: got.append(url) or json.dumps(facts()).encode(),
                         {"NVDA": ("1045810", "NVIDIA CORP"), "AAPL": ("320193", "Apple Inc.")}, focus)
    assert n == 2 and got[0].endswith("CIK0000320193.json")
    assert fetch_financials(store, lambda url: b"{}", {"NVDA": ("1", "x")}, ["NVDA"]) == 0   # fresh: skipped
    snap = store.load_financials("NVDA")
    assert snap.latest.get("revenue") == 150 and round(snap.growth("revenue"), 1) == 25.0
    rss = (b"<rss><channel><item><title>Nvidia earnings beat estimates - Reuters</title>"
           b"<link>https://n/1</link><pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    assert fetch_earnings_news(store, lambda url, h: rss, ["NVDA"]) == 1
    build_site(store, load_watchlist(), tmp_path, days=7)
    page = (tmp_path / "ticker" / "NVDA.html").read_text(encoding="utf-8")
    assert "財報（SEC 申報資料）" in page and "營收" in page and "Nvidia earnings beat" in page
    overview = (tmp_path / "earnings.html").read_text(encoding="utf-8")
    assert "NVDA" in overview and "+25.0%" in overview
