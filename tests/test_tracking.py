import json
from datetime import date, timedelta
from email.utils import format_datetime
from datetime import datetime, timezone

from spiderhead.cli import _feed_and_trends
from spiderhead.models import Post, Stance
from spiderhead.posts import KeywordAnalyzer
from spiderhead.sectors import classify_text
from spiderhead.sources import Collector, load_sources
from spiderhead.store import Store, window
from spiderhead.trends import consensus, sector_trends
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


def _rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>https://news.example/{i}</link>"
        f"<pubDate>{format_datetime(datetime.combine(d, datetime.min.time(), timezone.utc))}</pubDate></item>"
        for i, (t, d) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>".encode()


def fake_http(url, headers):
    if "users/by/username/BillAckman" in url:
        assert headers["Authorization"] == "Bearer tok"
        return json.dumps({"data": {"id": "42"}}).encode()
    if "users/42/tweets" in url:
        return json.dumps({"data": [{"id": "1", "created_at": f"{TODAY}T12:00:00Z",
                                     "text": "We bought a new position in $GOOGL. Undervalued AI leader."}]}).encode()
    if "news.google.com" in url and "Ackman" in url:
        return _rss(("Ackman says $GOOGL is cheap - Reuters", TODAY),
                    ("Old story", TODAY - timedelta(days=30)))
    if "news.google.com" in url and "Burry" in url:
        return _rss(("Burry bought puts on $NVDA, calls AI chips a bubble - CNBC", TODAY))
    if "news.google.com" in url and "Wood" in url:
        return _rss(("Cathie Wood adding $GOOGL and $NVDA, bullish on AI", TODAY))
    if "news.google.com" in url and "semiconductor" in url:
        return _rss(("Semiconductor stocks rally on data center demand", TODAY))
    if "news.google.com" in url:
        return _rss()
    raise ConnectionError("blocked")


def _collect(store):
    inv = load_watchlist()
    cfg = load_sources(inv)
    cfg.investors[[s.investor.key for s in cfg.investors].index("buffett")].rss.append("https://dead.example/feed")
    c = Collector(cfg, http_get=fake_http, x_token="tok")
    posts = c.collect(days=7)
    store.add_posts(posts)
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    return inv, c, posts


def test_collector_sources_and_errors():
    store = Store(":memory:")
    _, c, posts = _collect(store)
    kinds = {(p.investor_key, p.kind, p.source) for p in posts}
    assert ("ackman", "own", "x") in kinds
    assert ("ackman", "news", "news") in kinds
    assert ("industry:ai_semis", "industry", "news") in kinds
    assert all(p.published >= TODAY - timedelta(days=7) for p in posts)   # old story dropped
    assert any("dead.example" in e for e in c.errors)                    # dead feed reported
    assert store.add_posts(posts) == 0                                   # de-duplicated


def test_trends_and_consensus():
    store = Store(":memory:")
    _collect(store)
    start, end = window(7)
    sigs = store.signals_between(start, end)
    trends = {t.sector: t for t in sector_trends(sigs)}
    ai = trends["ai_semis"]
    assert {"burry", "wood"} <= ai.investors
    assert ai.industry_news == 1
    cons = {c.ticker: c for c in consensus(sigs)}
    assert cons["GOOGL"].views == {"ackman": Stance.BULLISH, "wood": Stance.BULLISH}
    assert cons["NVDA"].views["burry"] is Stance.BEARISH


def test_digest_renders():
    store = Store(":memory:")
    inv, _, _ = _collect(store)
    md = _feed_and_trends(store, inv, 7)
    assert "Bill Ackman" in md and "AI / 半導體" in md and "**GOOGL**（2 位）" in md


def test_sector_keywords():
    assert classify_text("The Fed will cut interest rates; oil is weak")[:2] == ["macro", "energy"]
    assert classify_text("nothing here") == ["other"]
    [sig] = KeywordAnalyzer().analyze(Post("x", TODAY, "AI chips look like a bubble"))
    assert (sig.ticker, sig.stance) == ("SECTOR:ai_semis", Stance.BEARISH)
