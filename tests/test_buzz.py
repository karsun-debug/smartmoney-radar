import json
from datetime import date, timedelta

from spiderhead.buzz import buzz_table, fetch_pageviews, parse_pageviews
from spiderhead.dashboard import build_site
from spiderhead.health import HealthLog
from spiderhead.models import Post
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date(2026, 11, 5)


def _api(start, n, base):
    return json.dumps({"items": [{"project": "en.wikipedia", "article": "x", "granularity": "daily",
                                  "timestamp": f"{start + timedelta(days=i):%Y%m%d}00", "access": "all-access",
                                  "agent": "user", "views": base + i * 10} for i in range(n)]}).encode()


def test_parse_pageviews():
    assert parse_pageviews(_api(date(2026, 10, 1), 2, 100)) == [(date(2026, 10, 1), 100), (date(2026, 10, 2), 110)]


def test_fetch_and_rank(tmp_path):
    store = Store(":memory:")
    hl = HealthLog()
    urls = []

    def get(url, h):
        urls.append(url)
        if "Nancy_Pelosi" in url:
            return _api(TODAY - timedelta(days=60), 60, 5000)
        if "Bill_Ackman" in url:
            return _api(TODAY - timedelta(days=60), 60, 1000)
        raise ConnectionError("404 Not Found")
    inv = load_watchlist()
    fetch_pageviews(store, get, inv, today=TODAY, health=hl)
    assert any("Daniel_S._Loeb" in u for u in urls)
    assert sum(r.ok for r in hl.results) == 2 and any(not r.ok for r in hl.results)
    store.add_posts([Post("ackman", TODAY - timedelta(days=1), "news", "news", f"https://n/{i}", "news")
                     for i in range(3)])
    rows = buzz_table(store, inv, today=TODAY)
    assert [b.investor for b in rows[:2]] == ["pelosi", "ackman"]
    ack = rows[1]
    assert ack.news_7d == 3 and ack.views_7d > ack.views_prev_7d > 0 and ack.views_change > 0
    build_site(store, inv, tmp_path, days=7)
    assert "聲量排行" in (tmp_path / "buzz.html").read_text(encoding="utf-8")
