"""Build the data behind every output (Markdown, LINE, dashboard) in one place."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .models import Investor, Post, Signal, Trade
from .sectors import SECTORS
from .store import Store, window
from .trends import SectorTrend, TickerConsensus, consensus, sector_trends


def display_names(investors: dict[str, Investor]) -> dict[str, str]:
    names = {k: v.name for k, v in investors.items()}
    names.update({f"industry:{k}": f"產業·{v}" for k, v in SECTORS.items()})
    return names


@dataclass
class Digest:
    start: date
    end: date
    feed: list[tuple[Post, list[Signal]]]     # investor posts/news, newest first
    trends: list[SectorTrend]
    consensus: list[TickerConsensus]
    names: dict[str, str]
    trades: list[Trade] = field(default_factory=list)  # filed in the window, newest first
    health: list = field(default_factory=list)          # FamilyStatus per data source
    clusters: list = field(default_factory=list)        # insider cluster buys, last 14 days


def build_digest(store: Store, investors: dict[str, Investor], days: int,
                 investor: str | None = None, end: date | None = None) -> Digest:
    start, end = window(days, end)
    prev_start, prev_end = window(days, start - timedelta(days=1))
    posts = store.posts_between(start, end, investor, kinds=("own", "news"))
    posts = [p for p in posts if p.investor_key in investors]
    cur = store.signals_between(start, end)
    return Digest(
        start, end, [(p, store.signals_for(p)) for p in posts],
        sector_trends(cur, store.signals_between(prev_start, prev_end)),
        consensus(cur), display_names(investors),
        [t for t in store.trades_between(start, end, by="filed") if t.investor_key in investors],
        store.source_health(),
        _clusters(store, end),
    )


def _clusters(store: Store, end: date):
    from .insiders import find_clusters
    return find_clusters(store.insider_buys_between(end - timedelta(days=13), end))


def bucket_starts(start: date, end: date, step_days: int) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=step_days)
    return out


def sector_series(signals: list[Signal], start: date, end: date,
                  step_days: int) -> tuple[list[date], dict[str, list[int]]]:
    """Distinct posts per sector per bucket (attention over time)."""
    buckets = bucket_starts(start, end, step_days)
    series: dict[str, list[int]] = {s: [0] * len(buckets) for s in SECTORS}
    seen: set[tuple[str, str]] = set()
    for s in signals:
        if not (start <= s.post.published <= end) or (s.sector, s.post.uid) in seen:
            continue
        seen.add((s.sector, s.post.uid))
        i = (s.post.published - start).days // step_days
        series.setdefault(s.sector, [0] * len(buckets))[i] += 1
    return buckets, series


@dataclass
class InvestorProfile:
    investor: Investor
    posts: list[tuple[Post, list[Signal]]]
    sectors: Counter                       # sector -> distinct posts
    tickers: dict[str, Counter]            # ticker -> Counter(stance -> n)
    activity: tuple[list[date], list[int]] # posts per bucket


def investor_profile(store: Store, inv: Investor, start: date, end: date,
                     step_days: int) -> InvestorProfile:
    posts = store.posts_between(start, end, inv.key, kinds=("own", "news"))
    rows = [(p, store.signals_for(p)) for p in posts]
    sectors: Counter = Counter()
    tickers: dict[str, Counter] = defaultdict(Counter)
    buckets = bucket_starts(start, end, step_days)
    activity = [0] * len(buckets)
    for p, sigs in rows:
        activity[(p.published - start).days // step_days] += 1
        for sec in {s.sector for s in sigs}:
            sectors[sec] += 1
        for s in sigs:
            if not s.ticker.startswith(("SECTOR:", "MACRO:")):
                tickers[s.ticker][s.stance.value] += 1
    return InvestorProfile(inv, rows, sectors, dict(tickers), (buckets, activity))
