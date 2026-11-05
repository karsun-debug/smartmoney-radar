from datetime import date

from spiderhead.dashboard import build_site
from spiderhead.digest import build_digest
from spiderhead.models import Post
from spiderhead.posts import KeywordAnalyzer
from spiderhead.retail import RetailAnalyzer, collect_retail, retail_vs_gurus
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date.today()
FEED = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>NVDA calls to the moon \xf0\x9f\x9a\x80</title>'
        b'<link href="https://www.reddit.com/r/wallstreetbets/comments/1"/><updated>' + str(TODAY).encode() +
        b'T10:00:00+00:00</updated><content type="html">YOLO all in, DD inside, CEO is great</content></entry>'
        b'</feed>')


def test_retail_analyzer_avoids_false_tickers():
    ra = RetailAnalyzer({"NVDA", "DD", "CEO"})
    sigs = ra.analyze(Post("retail:wsb", TODAY, "NVDA calls, YOLO, DD says CEO buying $TSLA", "reddit", None, "retail"))
    assert {s.ticker for s in sigs} == {"NVDA", "TSLA"} and sigs[0].stance.value == "bullish"


def test_retail_flow_and_isolation(tmp_path):
    from spiderhead.health import HealthLog
    hl = HealthLog()
    posts = collect_retail(lambda url, h: FEED, ["wallstreetbets"], hl)
    assert posts[0].kind == "retail" and hl.results[0].ok
    store = Store(":memory:")
    store.add_posts(posts + [Post("burry", TODAY, "Burry: NVDA is a bubble, bought puts on $NVDA", "x")])
    ra = RetailAnalyzer({"NVDA"})
    for p in store.unanalyzed():
        store.save_signals(p, ra.analyze(p) if p.kind == "retail" else KeywordAnalyzer().analyze(p))
    # Retail never leaks into investor trends / digests.
    assert all(s.post.kind != "retail" for s in store.signals_between(TODAY, TODAY))
    d = build_digest(store, load_watchlist(), 1)
    assert all(p.kind != "retail" for p, _ in d.feed)
    [row] = retail_vs_gurus(store, TODAY, TODAY)
    assert row.ticker == "NVDA" and row.retail_mood > 0 and row.guru_views == {"burry": "bearish"}
    assert row.divergent
    build_site(store, load_watchlist(), tmp_path, days=7)
    html = (tmp_path / "retail.html").read_text(encoding="utf-8")
    assert "看法相反" in html and "Michael Burry" in html
