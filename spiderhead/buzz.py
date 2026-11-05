"""Online buzz per investor: Wikipedia pageviews + how much news mentions them.

Wikipedia's public pageview API (no key, but a descriptive User-Agent is
required) gives daily human views of each investor's article — a steady,
manipulation-resistant proxy for public attention, the same idea Quiver
Quant and LuxAlgo use for companies. News volume comes from our own store.

Article titles default from the watchlist (handle ``wiki``); a wrong title
shows up as an error in the source-health table.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote

from .models import Investor

API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
       "{article}/daily/{start}/{end}")


def parse_pageviews(body: bytes | str) -> list[tuple[date, int]]:
    data = json.loads(body)
    out = []
    for item in data.get("items", []):
        ts = str(item.get("timestamp", ""))
        try:
            out.append((date(int(ts[:4]), int(ts[4:6]), int(ts[6:8])), int(item.get("views", 0))))
        except ValueError:
            continue
    return out


def fetch_pageviews(store, get: Callable[[str, dict], bytes], investors: dict[str, Investor],
                    days: int = 60, today: date | None = None, health=None) -> int:
    today = today or date.today()
    start, end = today - timedelta(days=days), today - timedelta(days=1)
    stored = 0
    for inv in investors.values():
        article = inv.handles.get("wiki")
        if not article:
            continue
        url = API.format(article=quote(article.replace(" ", "_"), safe=""), start=f"{start:%Y%m%d}00",
                         end=f"{end:%Y%m%d}00")
        try:
            rows = parse_pageviews(get(url, {}))
        except Exception as exc:
            if health is not None:
                health.record("buzz", inv.key, False, 0, f"{article}: {exc}")
            continue
        store.add_pageviews(inv.key, rows)
        stored += len(rows)
        if health is not None:
            health.record("buzz", inv.key, True, len(rows))
    return stored


@dataclass
class Buzz:
    investor: str
    views_7d: int = 0
    views_prev_7d: int = 0
    news_7d: int = 0
    news_prev_7d: int = 0
    series: list[tuple[date, int]] = field(default_factory=list)   # last 60 days

    @staticmethod
    def _chg(now: int, before: int) -> float | None:
        return None if before == 0 else (now - before) / before * 100

    @property
    def views_change(self) -> float | None:
        return self._chg(self.views_7d, self.views_prev_7d)

    @property
    def news_change(self) -> float | None:
        return self._chg(self.news_7d, self.news_prev_7d)


def buzz_table(store, investors: dict[str, Investor], today: date | None = None) -> list[Buzz]:
    today = today or date.today()
    out = []
    w1 = (today - timedelta(days=7), today)
    w0 = (today - timedelta(days=14), today - timedelta(days=8))
    for inv in investors.values():
        series = store.pageviews(inv.key, today - timedelta(days=60))
        b = Buzz(inv.key, series=series)
        b.views_7d = sum(v for d, v in series if w1[0] <= d <= w1[1])
        b.views_prev_7d = sum(v for d, v in series if w0[0] <= d <= w0[1])
        b.news_7d = len(store.posts_between(*w1, inv.key, kinds=("own", "news")))
        b.news_prev_7d = len(store.posts_between(*w0, inv.key, kinds=("own", "news")))
        out.append(b)
    return sorted(out, key=lambda b: (-b.views_7d, -b.news_7d))
