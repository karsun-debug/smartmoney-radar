import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from spiderhead.cli import main
from spiderhead.consistency import check, quarter_end
from spiderhead.holdings import TickerResolver, diff_holdings, parse_infotable
from spiderhead.models import ChangeType, Post, Stance, Verdict
from spiderhead.posts import ClaudeAnalyzer, KeywordAnalyzer, load_posts_json, parse_rss

FIX = Path(__file__).parent / "fixtures"
TICKERS = TickerResolver({"037833100": "AAPL", "060505104": "BAC", "191216100": "KO",
                          "833445109": "SNOW", "78462F103": "SPY"}, {"Chubb Ltd": "CB"})


def _changes():
    prev = parse_infotable((FIX / "prev_q.xml").read_bytes())
    curr = parse_infotable((FIX / "curr_q.xml").read_bytes())
    return {c.ticker: c for c in diff_holdings(prev, curr, TICKERS)}


def test_parse_merges_split_rows():
    curr = {h.cusip: h for h in parse_infotable((FIX / "curr_q.xml").read_bytes())}
    assert curr["060505104"].shares == 500_000_000
    assert curr["060505104"].value_usd == 25_000_000_000
    assert curr["78462F103"].put_call == "PUT"


def test_diff_classifies_moves():
    ch = _changes()
    assert ch["AAPL"].change is ChangeType.TRIM
    assert round(ch["AAPL"].share_delta_pct, 1) == -33.3
    assert ch["CB"].change is ChangeType.NEW
    assert ch["SNOW"].change is ChangeType.EXIT
    assert ch["KO"].change is ChangeType.HOLD
    assert abs(sum(c.portfolio_weight for c in ch.values()) - 1) < 1e-9


def test_consistency_verdicts_and_puts():
    ch = list(_changes().values())
    posts = load_posts_json(FIX / "posts.json")
    items = {i.signal.ticker: i.verdict for i in check(
        [s for p in posts for s in KeywordAnalyzer().analyze(p)], ch)}
    assert items["AAPL"] is Verdict.CONTRADICTS   # praised but trimmed
    assert items["CB"] is Verdict.CONSISTENT
    assert items["SPY"] is Verdict.CONSISTENT     # bearish + new PUT position
    assert items["TSLA"] is Verdict.NOT_IN_13F
    assert items["KO"] is Verdict.HELD


def test_keyword_aliases():
    post = Post("x", date(2026, 1, 1), "We sold most of our Apple shares.")
    [sig] = KeywordAnalyzer({"apple": "AAPL"}).analyze(post)
    assert (sig.ticker, sig.stance) == ("AAPL", Stance.BEARISH)


def test_quarter_end():
    assert quarter_end(date(2026, 8, 10)) == date(2026, 9, 30)
    assert quarter_end(date(2026, 12, 1)) == date(2026, 12, 31)
    assert quarter_end(date(2026, 4, 1)) == date(2026, 6, 30)


def test_parse_rss():
    rss = b"""<rss><channel><item><title>Why I'm long $GOOG</title>
      <description>&lt;p&gt;Undervalued&lt;/p&gt;</description>
      <pubDate>Tue, 03 Nov 2026 14:00:00 GMT</pubDate><link>https://e.com/1</link>
      </item></channel></rss>"""
    [p] = parse_rss(rss, "ackman")
    assert p.published == date(2026, 11, 3) and "$GOOG" in p.text and p.url == "https://e.com/1"


class _FakeClient:
    def __init__(self, payload):
        self.calls = []
        msg = SimpleNamespace(stop_reason="end_turn",
                              content=[SimpleNamespace(type="text", text=json.dumps(payload))])
        self.beta = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: self.calls.append(kw) or msg))


def test_claude_analyzer_parses_structured_output():
    client = _FakeClient({"signals": [
        {"ticker": "brk.b", "stance": "bullish", "confidence": 1.4, "rationale": "buyback"}]})
    post = Post("buffett", date(2026, 5, 2), "We repurchased shares.")
    [sig] = ClaudeAnalyzer(client=client).analyze(post)
    assert (sig.ticker, sig.stance, sig.confidence) == ("BRK.B", Stance.BULLISH, 1.0)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"


def test_cli_offline_report(capsys):
    rc = main(["report", "buffett", str(FIX / "posts.json"),
               "--prev-xml", str(FIX / "prev_q.xml"), "--curr-xml", str(FIX / "curr_q.xml"),
               "--tickers", str(FIX / "tickers.json")])
    out = capsys.readouterr().out
    assert rc == 0 and "言行不一" in out and "說到做到" in out
