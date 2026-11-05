import gzip
import json
from datetime import date, timedelta
from pathlib import Path

from spiderhead.consistency import check_trades
from spiderhead.dashboard import build_site
from spiderhead.digest import build_digest
from spiderhead.holdings import Holding, OpenFigi, TickerResolver
from spiderhead.models import Filing, Post, Stance, Trade, Verdict
from spiderhead.notify import format_line
from spiderhead.performance import PriceBook, parse_stooq_csv, score_calls, scorecards
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.trades import TradeCollector, parse_congress_rows, parse_form4, parse_subject_company
from spiderhead.watchlist import load_watchlist

FIX = Path(__file__).parent / "fixtures"
TODAY = date.today()


def test_parse_form4_keeps_open_market_trades_only():
    [t] = parse_form4((FIX / "form4.xml").read_bytes(), "buffett", date(2026, 10, 30), "u")
    assert (t.ticker, t.side, t.shares, t.price, t.traded) == ("OXY", "buy", 1_500_000, 48.25, date(2026, 10, 28))
    assert round(t.value_usd) == 72_375_000


def test_subject_company_header():
    header = """<SEC-HEADER>
SUBJECT COMPANY:
	COMPANY DATA:
		COMPANY CONFORMED NAME:			HOWARD HUGHES HOLDINGS INC.
		CENTRAL INDEX KEY:			0001981792
FILED BY:
	COMPANY DATA:
		COMPANY CONFORMED NAME:			Pershing Square Capital Management, L.P.
"""
    assert parse_subject_company(header) == "HOWARD HUGHES HOLDINGS INC."


ROW = {"id": "house:1:0", "chamber": "house", "docId": "1", "rowIndex": 0,
       "member": {"name": "Nancy Pelosi", "bioguideId": "P000197", "party": "D", "state": "CA"},
       "filedAt": str(TODAY - timedelta(days=2)), "transactedAt": str(TODAY - timedelta(days=20)),
       "ticker": "nvda", "assetDescription": "NVIDIA Corporation - Common Stock", "assetType": "stock",
       "side": "buy", "amountRange": {"min": 1000001, "max": 5000000, "text": "$1,000,001 - $5,000,000"},
       "owner": "spouse", "provenance": {"source": "house-clerk", "sourceUrl": "https://disclosures.example/1.pdf"}}


def test_congress_rows_filter_members():
    other = dict(ROW, member={"name": "Someone Else"})
    [t] = parse_congress_rows([ROW, other], {"pelosi": "pelosi"})
    assert (t.investor_key, t.ticker, t.side, t.form) == ("pelosi", "NVDA", "buy", "PTR")
    assert t.amount_text == "$1,000,001 - $5,000,000（配偶）"


class FakeEdgar:
    def list_filings(self, cik, forms, limit):
        return [Filing(cik, "0001-26-000001", "4", TODAY, None, "xslF345X05/doc.xml"),
                Filing(cik, "0001-26-000002", "SCHEDULE 13D", TODAY, None, "doc.xml"),
                Filing(cik, "0001-25-000003", "4", TODAY - timedelta(days=400), None, "x.xml")]

    def filing_index_url(self, f):
        return f"https://sec.example/{f.accession}"

    def fetch_primary_xml(self, f):
        return (FIX / "form4.xml").read_bytes()

    def fetch_header(self, f):
        return "SUBJECT COMPANY:\n COMPANY DATA:\n  COMPANY CONFORMED NAME: Howard Hughes Holdings Inc.\n"


def test_trade_collector():
    gz = gzip.compress(json.dumps([ROW]).encode())

    def get(url, h):
        if "disclosures-clerk.house.gov" in url:
            raise ConnectionError("house clerk down")
        return gz
    c = TradeCollector(FakeEdgar(), get, lambda name: "HHH" if "Hughes" in name else None)
    trades = c.collect(load_watchlist().values(), days=60)
    kinds = {(t.investor_key, t.form, t.ticker) for t in trades}
    assert ("ackman", "4", "OXY") in kinds and ("ackman", "SCHEDULE 13D", "HHH") in kinds
    assert ("pelosi", "PTR", "NVDA") in kinds                    # via LuxAlgo fallback
    assert len(c.errors) == 1 and "falling back" in c.errors[0]


def test_check_trades_verdicts():
    post = Post("pelosi", TODAY - timedelta(days=30), "Bullish on $NVDA", "x")
    [sig] = KeywordAnalyzer().analyze(post)
    trades = parse_congress_rows([ROW], {"pelosi": "pelosi"})
    [chk] = check_trades([sig], trades)
    assert chk.verdict is Verdict.CONSISTENT and chk.days_after == 10
    sell = [Trade("pelosi", TODAY - timedelta(days=25), TODAY, "sell", "PTR", "NVDA", "NVIDIA")]
    assert check_trades([sig], sell)[0].verdict is Verdict.CONTRADICTS


def _prices_csv(start, n, p0, step):
    lines = ["Date,Open,High,Low,Close,Volume"]
    for i in range(n):
        lines.append(f"{start + timedelta(days=i)},1,1,1,{p0 + step * i:.2f},100")
    return "\n".join(lines).encode()


def test_score_calls_and_scorecards():
    store = Store(":memory:")
    start = TODAY - timedelta(days=200)
    feeds = {"nvda.us": _prices_csv(start, 201, 100, 0.5), "tsla.us": _prices_csv(start, 201, 300, -0.5)}
    pb = PriceBook(store, lambda url, h: feeds[url.split("s=")[1].split("&")[0]])
    posts = [Post("wood", TODAY - timedelta(days=150), "Adding $NVDA, very bullish", "x"),
             Post("wood", TODAY - timedelta(days=150), "Bought more $TSLA, undervalued", "x"),
             Post("burry", TODAY - timedelta(days=10), "Short $NVDA, overvalued bubble", "x")]
    sigs = [s for p in posts for s in KeywordAnalyzer().analyze(p)]
    res = {(r.signal.post.investor_key, r.signal.ticker): r for r in score_calls(sigs, pb)}
    assert res[("wood", "NVDA")].hit(90) is True and res[("wood", "TSLA")].hit(90) is False
    assert res[("burry", "NVDA")].returns[90] is None          # too recent to score
    card = scorecards(res.values())["wood"]
    assert (card.calls, card.scored, card.hits, card.hit_rate) == (2, 2, 1, 0.5)
    assert store.prices("NVDA")                                  # cached


def test_stooq_csv_parsing_skips_bad_rows():
    rows = parse_stooq_csv("Date,Open,High,Low,Close,Volume\n2026-01-02,1,1,1,10.5,1\nbad,row\n")
    assert rows == [(date(2026, 1, 2), 10.5)]


def test_openfigi_fallback():
    calls = []

    def post(url, headers, body):
        calls.append(json.loads(body))
        return json.dumps([{"data": [{"ticker": "hhh", "exchCode": "US"}]}]).encode()
    r = TickerResolver(figi=OpenFigi(post))
    h = Holding("HOWARD HUGHES HLDGS INC", "44267T102", "COM", 1, 1)
    assert r(h) == "HHH" and r(h) == "HHH" and len(calls) == 1   # cached after first lookup

    def down(url, headers, body):
        raise ConnectionError
    r2 = TickerResolver(figi=OpenFigi(down))
    assert r2(h) is None and r2.figi.failed


def test_trades_in_line_and_dashboard(tmp_path):
    store = Store(":memory:")
    store.add_trades(parse_congress_rows([ROW], {"pelosi": "pelosi"}))
    store.add_posts([Post("pelosi", TODAY - timedelta(days=25), "Bullish on $NVDA chips", "x")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    inv = load_watchlist()
    text = format_line(build_digest(store, inv, 7))
    assert "🧾 最新對帳單" in text and "Nancy Pelosi 🟢買進 NVDA $1,000,001 - $5,000,000（配偶）" in text
    build_site(store, inv, tmp_path, days=30)
    page = (tmp_path / "investor" / "pelosi.html").read_text(encoding="utf-8")
    assert "即時對帳單" in page and "說到做到" in page and "國會申報" in page
    assert (tmp_path / "ticker" / "NVDA.html").exists()
