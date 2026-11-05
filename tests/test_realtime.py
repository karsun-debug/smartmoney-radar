import json
from datetime import date, timedelta

from spiderhead.alerts import find_pending, format_alert, run_alerts
from spiderhead.ark import ArkCollector, diff_ark, parse_ark_csv
from spiderhead.doctor import FAIL, OK, WARN, format_checks, run_checks
from spiderhead.models import Post, Trade
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.trades import clean_asset, parse_congress_rows
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


def _csv(day, rows):
    head = 'date,fund,company,ticker,cusip,shares,"market value ($)","weight (%)"\n'
    body = "".join(f'{day:%m/%d/%Y},ARKK,"{c}",{t},{cu},"{s:,}","${s * p:,.2f}",1.00%\n'
                   for c, t, cu, s, p in rows)
    return (head + body + '"Investors should carefully consider the investment objectives..."\n').encode()


DAY1 = [("TESLA INC", "TSLA", "88160R101", 1_000_000, 300.0),
        ("ROKU INC", "ROKU", "77543R102", 2_000_000, 80.0),
        ("COINBASE GLOBAL INC -CLASS A", "COIN", "19260Q107", 500_000, 250.0),
        ("UIPATH INC - CLASS A", "PATH", "90364P105", 3_000_000, 12.0)]
# Every position +2% (ETF creation), plus: TSLA extra buy, ROKU sold off, PATH exited, CRSP new.
DAY2 = [("TESLA INC", "TSLA", "88160R101", 1_020_000 + 50_000, 300.0),
        ("ROKU INC", "ROKU", "77543R102", 1_500_000, 80.0),
        ("COINBASE GLOBAL INC -CLASS A", "COIN", "19260Q107", 510_000, 250.0),
        ("CRISPR THERAPEUTICS AG", "CRSP", "H17182108", 400_000, 55.0)]


def test_parse_ark_csv():
    day, rows = parse_ark_csv(_csv(date(2026, 11, 3), DAY1).decode())
    assert day == date(2026, 11, 3) and len(rows) == 4
    assert rows[0].ticker == "TSLA" and rows[0].shares == 1_000_000 and rows[0].value == 300_000_000


def test_diff_ark_removes_etf_flows():
    _, a = parse_ark_csv(_csv(date(2026, 11, 3), DAY1).decode())
    _, b = parse_ark_csv(_csv(date(2026, 11, 4), DAY2).decode())
    trades = {t.ticker: t for t in diff_ark("ARKK", a, b, date(2026, 11, 4))}
    assert "COIN" not in trades                         # +2% matches the flow factor
    assert trades["TSLA"].side == "buy" and abs(trades["TSLA"].shares - 50_000) <= 1
    assert trades["ROKU"].side == "sell"
    assert trades["PATH"].side == "sell" and "清倉" in trades["PATH"].amount_text
    assert trades["CRSP"].side == "buy" and "新建倉" in trades["CRSP"].amount_text
    assert all(t.investor_key == "wood" and t.form == "ARK daily" for t in trades.values())


def test_ark_collector_needs_two_days():
    store = Store(":memory:")
    pages = {"d": _csv(date(2026, 11, 3), DAY1)}
    col = ArkCollector(store, lambda url, h: pages["d"], {"ARKK": "u"})
    assert col.collect() == []                          # first day only stores the snapshot
    pages["d"] = _csv(date(2026, 11, 4), DAY2)
    assert {t.ticker for t in col.collect()} == {"TSLA", "ROKU", "PATH", "CRSP"}
    again = col.collect()                               # same day re-run: same diff vs Nov 3
    assert store.add_trades(again) == 4 and store.add_trades(again) == 0   # de-duplicated by uid


def test_clean_asset_from_real_house_ptr():
    raw = ("Bloom Energy Corporation Class A Common Stock (BE) [OP] F\x00\x00\x00 S\x00\x00: New "
           "D\x00\x00\x00: Purchased 100 call options with a strike price of $100.")
    name, kind, detail, shares = clean_asset(raw)
    assert name == "Bloom Energy Corporation Class A Common Stock (BE)" and kind == "選擇權"
    assert detail.startswith("Purchased 100 call options") and shares is None
    assert clean_asset("Intel Corp (INTC) [ST] F S: New D: Purchased 10,000 shares.")[3] == 10_000


def _store_with_activity():
    store = Store(":memory:")
    store.add_posts([Post("ackman", TODAY, "We bought a new position in $GOOGL. Undervalued.", "x", "https://x.com/1"),
                     Post("ackman", TODAY, "Great dinner tonight.", "x", "https://x.com/2"),
                     Post("wood", TODAY, "Coverage of Cathie: buys $TSLA", "news", None, "news")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    store.add_trades([Trade("buffett", TODAY - timedelta(days=2), TODAY, "buy", "4", "OXY", "Occidental",
                            1_000_000, 50.0, url="https://sec.example/1")])
    return store


def test_alerts_first_run_silent_then_only_new():
    store, inv, sent = _store_with_activity(), load_watchlist(), []
    assert run_alerts(store, inv, sent.append) == (0, "") and not sent      # first run: remember only
    store.add_trades([Trade("pelosi", TODAY - timedelta(days=20), TODAY, "buy", "PTR", "NVDA", "NVIDIA",
                            amount_text="$1,000,001 - $5,000,000（配偶）")])
    n, text = run_alerts(store, inv, sent.append)
    assert n == 1 and len(sent) == 1 and "Nancy Pelosi 🟢買進 NVDA" in text and "OXY" not in text
    assert run_alerts(store, inv, sent.append) == (0, "")                   # nothing new


def test_alert_content_and_filters():
    store, inv = _store_with_activity(), load_watchlist()
    pending = find_pending(store, inv)
    assert len(pending.posts) == 1 and len(pending.trades) == 1             # no-call post & news skipped
    text = format_alert(pending, inv)
    assert "🧾 Warren Buffett 🟢買進 OXY" in text and "約 $50.0M" in text
    assert "💬 Bill Ackman（x）🟢GOOGL" in text and "https://x.com/1" in text
    assert len(find_pending(store, inv, include_news=True).posts) == 2
    n, _ = run_alerts(store, inv, None, dry_run=True, backfill=True)
    assert n == 2 and store.alerts_count() == 0                             # dry run records nothing


def test_doctor_reports():
    def get(url, headers):
        if "sec.gov" in url:
            return json.dumps({"name": "BERKSHIRE HATHAWAY INC"}).encode()
        if "api.line.me" in url:
            assert headers["Authorization"] == "Bearer t"
            return json.dumps({"displayName": "大神通知", "basicId": "@123abc"}).encode()
        if "manifest.json" in url:
            return json.dumps({"datasets": {"congress-trades": {
                "rows": 9, "lastIngestedAt": f"{TODAY}T00:00:00Z"}}}).encode()
        if "stooq" in url:
            return b"Date,Open\n"
        raise ConnectionError("blocked")
    checks = {c.name: c for c in run_checks(get, {"SPIDERHEAD_USER_AGENT": "SH a@b.c",
                                                 "LINE_CHANNEL_ACCESS_TOKEN": "t"})}
    assert checks["SEC EDGAR"].status == OK and "BERKSHIRE" in checks["SEC EDGAR"].detail
    assert checks["LINE token"].status == OK and "大神通知" in checks["LINE token"].detail
    assert checks["國會交易資料 (LuxAlgo)"].status == OK
    assert checks["Google News"].status == WARN and checks["股價 (Stooq)"].status == OK
    bad = run_checks(get, {})
    assert bad[0].status == FAIL and "必須修正" in format_checks(bad)
