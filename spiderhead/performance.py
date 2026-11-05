"""Score calls: how did a ticker move after an investor publicly took a side?

Prices come from Stooq's free daily CSV (no key). Results are cached in the
store so each ticker is downloaded at most once per run.
"""
from __future__ import annotations

import bisect
import csv
import io
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from .models import Signal, Stance

STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}&i=d"
HORIZONS = (30, 90)


def parse_stooq_csv(text: str) -> list[tuple[date, float]]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            rows.append((date.fromisoformat(r["Date"]), float(r["Close"])))
        except (KeyError, ValueError):
            continue
    return rows


class PriceBook:
    def __init__(self, store, http_get: Callable[[str, dict], bytes] | None = None):
        self.store = store
        self.get = http_get
        self.errors: list[str] = []
        self._cache: dict[str, list[tuple[date, float]]] = {}

    def series(self, ticker: str) -> list[tuple[date, float]]:
        if ticker in self._cache:
            return self._cache[ticker]
        rows = self.store.prices(ticker)
        stale = not rows or rows[-1][0] < date.today() - timedelta(days=4)
        if stale and self.get is not None and len(self.errors) < 3:   # stop early when offline
            sym = ticker.lower().replace(".", "-") + ".us"
            try:
                fresh = parse_stooq_csv(self.get(STOOQ_URL.format(sym=sym), {}).decode("utf-8", "replace"))
                if fresh:
                    self.store.add_prices(ticker, fresh)
                    rows = self.store.prices(ticker)
            except Exception as exc:
                self.errors.append(f"price {ticker}: {exc}")
        self._cache[ticker] = rows
        return rows

    def close_on_or_after(self, ticker: str, day: date) -> tuple[date, float] | None:
        rows = self.series(ticker)
        i = bisect.bisect_left(rows, (day, float("-inf")))
        return rows[i] if i < len(rows) else None


@dataclass(frozen=True)
class CallResult:
    signal: Signal
    entry: float
    returns: dict[int, float | None]     # horizon days -> % move (None if not yet known)

    def hit(self, horizon: int) -> bool | None:
        r = self.returns.get(horizon)
        if r is None:
            return None
        return r > 0 if self.signal.stance is Stance.BULLISH else r < 0


def score_calls(signals: Iterable[Signal], prices: PriceBook,
                horizons: tuple[int, ...] = HORIZONS) -> list[CallResult]:
    """One result per (investor, ticker, day) directional call on a stock."""
    seen, out = set(), []
    for s in signals:
        if s.stance is Stance.NEUTRAL or s.ticker.startswith(("SECTOR:", "MACRO:")):
            continue
        if s.post.kind != "own" or s.confidence < 0.5:
            continue
        key = (s.post.investor_key, s.ticker, s.post.published)
        if key in seen:
            continue
        seen.add(key)
        entry = prices.close_on_or_after(s.ticker, s.post.published)
        if entry is None:
            continue
        rets: dict[int, float | None] = {}
        for h in horizons:
            target = s.post.published + timedelta(days=h)
            later = prices.close_on_or_after(s.ticker, target) if target <= date.today() else None
            rets[h] = (later[1] / entry[1] - 1) * 100 if later else None
        out.append(CallResult(s, entry[1], rets))
    return out


@dataclass
class Scorecard:
    investor: str
    calls: int
    scored: int
    hits: int
    avg_return: float | None      # direction-adjusted: positive = the call was right

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.scored if self.scored else None


def scorecards(results: Iterable[CallResult], horizon: int = 90) -> dict[str, Scorecard]:
    by: dict[str, list[CallResult]] = {}
    for r in results:
        by.setdefault(r.signal.post.investor_key, []).append(r)
    out = {}
    for inv, rs in by.items():
        scored = [r for r in rs if r.returns.get(horizon) is not None]
        adj = [r.returns[horizon] * (1 if r.signal.stance is Stance.BULLISH else -1) for r in scored]
        out[inv] = Scorecard(inv, len(rs), len(scored), sum(1 for r in scored if r.hit(horizon)),
                             sum(adj) / len(adj) if adj else None)
    return out
