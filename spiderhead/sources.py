"""Automatic collection of investor posts and industry news.

Source types (all optional, configured in a JSON file, see ``load_sources``):

* ``x``     — the investor's X account via the official X API v2. Needs a paid
              API plan and ``X_BEARER_TOKEN``; skipped otherwise.
* ``rss``   — any RSS/Atom feed: blog, Substack (``https://<name>.substack.com/feed``),
              YouTube channel (``https://www.youtube.com/feeds/videos.xml?channel_id=...``).
* ``news``  — a Google News RSS search, used by default for every investor so
              interviews, letters and viral posts get picked up without an X plan.
* ``industries`` — Google News searches per sector for 產業動態.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from .models import Investor, Post
from .posts import parse_rss

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
X_API = "https://api.x.com/2"

DEFAULT_INDUSTRY_QUERIES: dict[str, str] = {
    "ai_semis": "semiconductor OR \"AI chips\" OR \"data center\" industry",
    "software": "enterprise software OR SaaS industry outlook",
    "financials": "bank stocks OR insurers OR \"private credit\"",
    "energy": "oil prices OR OPEC OR natural gas market",
    "healthcare": "biotech OR pharma OR GLP-1 industry",
    "consumer": "consumer spending OR retail sales",
    "macro": "Federal Reserve OR inflation OR Treasury yields",
    "crypto": "bitcoin OR crypto market",
}


@dataclass
class InvestorSources:
    investor: Investor
    x: str | None = None
    rss: list[str] = field(default_factory=list)
    news: str | None = None          # Google News query; None disables


@dataclass
class SourceConfig:
    investors: list[InvestorSources]
    industries: dict[str, str]


def load_sources(investors: dict[str, Investor], path: str | Path | None = None) -> SourceConfig:
    """Build the source list.

    Defaults: every watchlist investor gets a Google News query on their name
    plus their X handle if known. A JSON file can override per investor::

        {"investors": {"ackman": {"x": "BillAckman", "rss": ["https://..."], "news": "\\"Bill Ackman\\""}},
         "industries": {"ai_semis": "AI chips", "energy": null}}

    Set a value to null to disable it.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    over = raw.get("investors", {})
    result = []
    for key, inv in investors.items():
        o = over.get(key, {})
        result.append(InvestorSources(
            investor=inv,
            x=o.get("x", inv.handles.get("x")),
            rss=list(o.get("rss", [])),
            news=o.get("news", f'"{inv.name}"'),
        ))
    industries = dict(DEFAULT_INDUSTRY_QUERIES)
    industries.update(raw.get("industries", {}))
    return SourceConfig(result, {k: v for k, v in industries.items() if v})


HttpGet = Callable[[str, dict[str, str]], bytes]


def _requests_get(url: str, headers: dict[str, str]) -> bytes:
    import requests
    h = {"User-Agent": os.environ.get("SPIDERHEAD_USER_AGENT", "SpiderHead/0.1")}
    h.update(headers)
    resp = requests.get(url, headers=h, timeout=30)
    resp.raise_for_status()
    return resp.content


class Collector:
    """Fetch every configured source; errors are collected, not raised."""

    def __init__(self, config: SourceConfig, http_get: HttpGet = _requests_get,
                 x_token: str | None = None):
        self.config = config
        self.get = http_get
        self.x_token = x_token if x_token is not None else os.environ.get("X_BEARER_TOKEN")
        self.errors: list[str] = []
        from .health import HealthLog
        self.health = HealthLog()

    def collect(self, days: int = 7, news: bool = True, industries: bool = True) -> list[Post]:
        since = date.today() - timedelta(days=days)
        posts: list[Post] = []
        for src in self.config.investors:
            key = src.investor.key
            if src.x and self.x_token:
                posts += self._try(f"x:{src.x}", lambda: self._fetch_x(key, src.x))
            for url in src.rss:
                posts += self._try(url, lambda u=url: parse_rss(self.get(u, {}), key))
            if news and src.news:
                posts += self._try(f"news:{key}", lambda: self._news(src.news, key, "news"))
        if industries:
            for sector, query in self.config.industries.items():
                posts += self._try(f"industry:{sector}",
                                   lambda q=query, s=sector: self._news(q, f"industry:{s}", "industry"))
        return [p for p in posts if p.published >= since]

    def _try(self, label: str, fn: Callable[[], list[Post]]) -> list[Post]:
        family = label.split(":", 1)[0] if label.split(":", 1)[0] in ("x", "news", "industry") else "rss"
        try:
            got = fn()
        except Exception as exc:  # one dead feed must not stop the run
            self.errors.append(f"{label}: {exc}")
            self.health.record(family, label, False, 0, str(exc))
            return []
        self.health.record(family, label, True, len(got))
        return got

    def _news(self, query: str, owner: str, kind: str) -> list[Post]:
        body = self.get(GOOGLE_NEWS_RSS.format(q=quote_plus(query)), {})
        return [Post(p.investor_key, p.published, p.text, "news", p.url, kind)
                for p in parse_rss(body, owner)]

    def _fetch_x(self, key: str, handle: str) -> list[Post]:
        auth = {"Authorization": f"Bearer {self.x_token}"}
        user = json.loads(self.get(f"{X_API}/users/by/username/{handle}", auth))
        uid = user["data"]["id"]
        tl = json.loads(self.get(
            f"{X_API}/users/{uid}/tweets?max_results=20&exclude=retweets"
            "&tweet.fields=created_at", auth))
        return [Post(key, datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).date(),
                     t["text"], "x", f"https://x.com/{handle}/status/{t['id']}", "own")
                for t in tl.get("data", [])]
