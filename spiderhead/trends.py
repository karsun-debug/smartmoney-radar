"""Aggregate signals into industry heat and cross-investor consensus."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .models import Signal, Stance


@dataclass
class SectorTrend:
    sector: str
    investor_posts: int = 0          # distinct posts by / about investors
    industry_news: int = 0           # distinct industry-news articles
    bullish: float = 0.0             # confidence-weighted
    bearish: float = 0.0
    investors: set[str] = field(default_factory=set)
    prev_total: int = 0              # same metric in the previous window

    @property
    def total(self) -> int:
        return self.investor_posts + self.industry_news

    @property
    def sentiment(self) -> float:
        """-1 (all bearish) .. +1 (all bullish)."""
        s = self.bullish + self.bearish
        return 0.0 if s == 0 else (self.bullish - self.bearish) / s

    @property
    def change_pct(self) -> float | None:
        if self.prev_total == 0:
            return None
        return (self.total - self.prev_total) / self.prev_total * 100


@dataclass
class TickerConsensus:
    ticker: str
    views: dict[str, Stance]          # investor -> latest stance

    @property
    def investors(self) -> int:
        return len(self.views)


def _sector_counts(signals: Iterable[Signal]) -> dict[str, SectorTrend]:
    trends: dict[str, SectorTrend] = {}
    seen: set[tuple[str, str]] = set()
    for s in signals:
        t = trends.setdefault(s.sector, SectorTrend(s.sector))
        uid = s.post.uid
        if (s.sector, uid) not in seen:
            seen.add((s.sector, uid))
            if s.post.kind == "industry":
                t.industry_news += 1
            else:
                t.investor_posts += 1
                t.investors.add(s.post.investor_key)
        if s.stance is Stance.BULLISH:
            t.bullish += s.confidence
        elif s.stance is Stance.BEARISH:
            t.bearish += s.confidence
    return trends


def sector_trends(current: Iterable[Signal], previous: Iterable[Signal] = ()) -> list[SectorTrend]:
    """Sectors ranked by attention in the current window."""
    cur = _sector_counts(current)
    for sector, prev in _sector_counts(previous).items():
        cur.setdefault(sector, SectorTrend(sector)).prev_total = prev.total
    return sorted((t for t in cur.values() if t.total), key=lambda t: (-t.total, t.sector))


def consensus(signals: Iterable[Signal], min_investors: int = 2) -> list[TickerConsensus]:
    """Tickers that several different investors talked about in the window."""
    latest: dict[str, dict[str, tuple]] = defaultdict(dict)
    for s in signals:
        if s.post.kind == "industry" or s.stance is Stance.NEUTRAL:
            continue
        if s.ticker.startswith(("SECTOR:", "MACRO:")):
            continue
        prev = latest[s.ticker].get(s.post.investor_key)
        if prev is None or s.post.published >= prev[0]:
            latest[s.ticker][s.post.investor_key] = (s.post.published, s.stance)
    out = [TickerConsensus(t, {inv: v[1] for inv, v in views.items()})
           for t, views in latest.items() if len(views) >= min_investors]
    return sorted(out, key=lambda c: (-c.investors, c.ticker))
