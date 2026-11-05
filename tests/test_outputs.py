import json
from datetime import date, timedelta

import pytest

from spiderhead.dashboard import build_site
from spiderhead.digest import build_digest, sector_series
from spiderhead.models import Post
from spiderhead.notify import LineNotifier, format_line, split_text
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


@pytest.fixture
def store():
    s = Store(":memory:")
    s.add_posts([
        Post("ackman", TODAY, "We bought a new position in $GOOGL. Undervalued AI leader.", "x",
             "https://x.com/BillAckman/status/1"),
        Post("wood", TODAY - timedelta(days=1), "Adding to $GOOGL and $NVDA, bullish on AI <script>", "x"),
        Post("burry", TODAY - timedelta(days=20), "AI chips look like a bubble. Bought puts on $NVDA.", "x"),
        Post("industry:energy", TODAY, "OPEC cuts; crude oil climbs", "news", None, "industry"),
    ])
    for p in s.unanalyzed():
        s.save_signals(p, KeywordAnalyzer().analyze(p))
    return s


def test_line_message(store):
    text = format_line(build_digest(store, load_watchlist(), 7), "https://dash.example")
    assert "AI / 半導體" in text and "GOOGL：Bill Ackman🟢 Cathie Wood🟢" in text
    assert "https://x.com/BillAckman/status/1" in text and "儀表板：https://dash.example" in text
    assert "ai_semis" not in text          # sector keys are shown with their Chinese names


def test_line_split_and_send():
    chunks = split_text("\n".join(["x" * 100] * 120), limit=5000)
    assert all(len(c) <= 5000 for c in chunks) and len(chunks) == 3
    sent = []
    n = LineNotifier("tok", "U123", lambda url, h, body: sent.append((url, h, json.loads(body)))) \
        .send("\n".join(["y" * 4000] * 7))
    assert n == 2                                   # 7 chunks -> 5 + 2 messages
    url, headers, body = sent[0]
    assert url.endswith("/push") and body["to"] == "U123" and len(body["messages"]) == 5
    assert headers["Authorization"] == "Bearer tok"
    LineNotifier("tok", "", lambda url, h, b: sent.append((url, h, json.loads(b)))).send("hi")
    assert sent[-1][0].endswith("/broadcast") and "to" not in sent[-1][2]


def test_line_requires_token(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError):
        LineNotifier()


def test_sector_series_buckets(store):
    start = TODAY - timedelta(days=27)
    buckets, series = sector_series(store.signals_between(start, TODAY), start, TODAY, 7)
    assert len(buckets) == 4
    assert sum(series["ai_semis"]) == 3 and series["ai_semis"][-1] == 2


def test_dashboard_site(store, tmp_path):
    index = build_site(store, load_watchlist(), tmp_path, days=30)
    html = index.read_text(encoding="utf-8")
    assert "產業熱度變化" in html and "AI / 半導體" in html and "GOOGL" in html
    wood = (tmp_path / "investor" / "wood.html").read_text(encoding="utf-8")
    assert "Cathie Wood" in wood and "13F + 每日交易" in wood
    assert "<script>" not in wood.split("<main>")[1].split("<script src")[0]   # post text escaped
    assert (tmp_path / "investor" / "saylor.html").exists()
    assert (tmp_path / "assets" / "app.js").exists()
