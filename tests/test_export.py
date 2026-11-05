import csv
import json
import xml.etree.ElementTree as ET
from datetime import date

from spiderhead.dashboard import build_site
from spiderhead.models import Post, Trade
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


def test_exports_feed_and_search_index(tmp_path):
    store = Store(":memory:")
    store.add_posts([Post("ackman", TODAY, "We bought a new position in $GOOGL & it's cheap <b>", "x",
                          "https://x.com/1")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    store.add_trades([Trade("pelosi", TODAY, TODAY, "buy", "PTR", "NVDA", "NVIDIA 中文", amount_text="$1,001 - $15,000",
                            url="https://pdf/1")])
    build_site(store, load_watchlist(), tmp_path, days=7)
    meta = json.loads((tmp_path / "data" / "index.json").read_text(encoding="utf-8"))
    assert "trades.csv" in meta["files"]
    trades = json.loads((tmp_path / "data" / "trades.json").read_text(encoding="utf-8"))
    assert trades[0]["ticker"] == "NVDA" and trades[0]["issuer"] == "NVIDIA 中文"
    rows = list(csv.DictReader((tmp_path / "data" / "trades.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["ticker"] == "NVDA"
    sigs = json.loads((tmp_path / "data" / "signals.json").read_text(encoding="utf-8"))
    assert {s["ticker"] for s in sigs} >= {"GOOGL"}
    root = ET.fromstring((tmp_path / "feed.xml").read_text(encoding="utf-8"))       # well-formed XML
    titles = [i.findtext("title") for i in root.iter("item")]
    assert any("Nancy Pelosi 買進 NVDA" in t for t in titles) and any("Bill Ackman" in t for t in titles)
    js = (tmp_path / "assets" / "app.js").read_text(encoding="utf-8")
    assert "window.SH_INDEX=" in js and '"ticker/GOOGL.html"' in js and "investor/ackman.html" in js
    assert 'id="sh-q"' in (tmp_path / "index.html").read_text(encoding="utf-8")
