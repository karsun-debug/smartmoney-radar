import json
from datetime import date, timedelta
from types import SimpleNamespace

from spiderhead.dashboard import build_site
from spiderhead.insiders import InsiderBuy
from spiderhead.models import Post, Trade
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist
from spiderhead.weekly import claude_report, gather, markdown_to_html, template_report

TODAY = date.today()


def _store():
    store = Store(":memory:")
    store.add_posts([Post("ackman", TODAY - timedelta(days=2), "We bought a new position in $GOOGL", "x"),
                     Post("wood", TODAY - timedelta(days=1), "Bullish on $TSLA, adding more", "x")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    store.add_trades([Trade("wood", TODAY - timedelta(days=1), TODAY - timedelta(days=1), "sell", "ARK daily",
                            "TSLA", "Tesla", 50_000, 300.0, "ARKK -50,000 股")])
    store.add_insider_buys([InsiderBuy(f"a{i}", "ACME", "Acme", f"O{i}", "Director", TODAY, TODAY, 1e4, 20)
                            for i in range(3)])
    return store


def test_gather_and_template():
    w = gather(_store(), load_watchlist())
    p = w.payload
    assert {c["ticker"] for c in p["own_calls"]} >= {"GOOGL", "TSLA"}
    assert p["say_vs_do_contradictions"] == [{"who": "Cathie Wood", "ticker": "TSLA", "said": "bullish",
                                              "did": "sell", "trade_date": str(TODAY - timedelta(days=1))}]
    assert p["insider_clusters"][0]["ticker"] == "ACME"
    md = template_report(w)
    assert "## 言行不一" in md and "Cathie Wood 公開看多 TSLA，但申報顯示賣出" in md


def test_claude_report_payload_and_fallback():
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="# 大神週報\\n- 重點")])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    w = gather(_store(), load_watchlist())
    assert claude_report(w, client).startswith("# 大神週報")
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    data = json.loads(kw["messages"][0]["content"].split("<week_data>\n")[1].split("\n</week_data>")[0])
    assert data["insider_clusters"][0]["ticker"] == "ACME"

    def refuse(**kw):
        return SimpleNamespace(stop_reason="refusal", content=[])
    client2 = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=refuse)))
    assert claude_report(w, client2).startswith("# 大神週報（")         # template fallback


def test_markdown_rendering_escapes_and_weekly_page(tmp_path):
    html = markdown_to_html("# T\n- a **b** <script>\n\n> note")
    assert "<h1>T</h1>" in html and "<strong>b</strong>" in html and "&lt;script&gt;" in html
    store = _store()
    w = gather(store, load_watchlist())
    store.save_report(w.start, w.end, template_report(w), "template")
    build_site(store, load_watchlist(), tmp_path, days=7)
    page = (tmp_path / "weekly.html").read_text(encoding="utf-8")
    assert "大神週報" in page and "自動範本" in page
