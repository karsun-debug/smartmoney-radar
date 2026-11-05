"""13F clone backtest: what if you had copied each investor's 13F?

Each quarter you buy the investor's top holdings on the day the 13F becomes
public (not the quarter end — that information wasn't available yet), weighted
by their portfolio weights, and hold until the next 13F is filed. Returns are
chained across quarters and compared with SPY over the same periods.

Simplifications: no dividends, costs or taxes; holdings without a ticker or
price history are dropped and the remaining weights re-normalized; options
positions are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import Investor
from .performance import PriceBook

BENCHMARK = "SPY"


@dataclass(frozen=True)
class Period:
    quarter: str
    entry: date
    exit: date
    ret: float                 # portfolio return, %
    bench: float | None        # SPY return over the same dates, %
    coverage: float            # share of the top-N weight we could price (0..1)
    holdings: list[tuple[str, float, float]]   # (ticker, weight, return %)


@dataclass
class CloneResult:
    investor: str
    periods: list[Period] = field(default_factory=list)

    @staticmethod
    def _chain(values: list[float]) -> float:
        total = 1.0
        for v in values:
            total *= 1 + v / 100
        return (total - 1) * 100

    @property
    def total(self) -> float | None:
        return self._chain([p.ret for p in self.periods]) if self.periods else None

    @property
    def bench_total(self) -> float | None:
        if not self.periods or any(p.bench is None for p in self.periods):
            return None
        return self._chain([p.bench for p in self.periods])

    @property
    def excess(self) -> float | None:
        if self.total is None or self.bench_total is None:
            return None
        return self.total - self.bench_total

    @property
    def beat_rate(self) -> float | None:
        scored = [p for p in self.periods if p.bench is not None]
        return sum(p.ret > p.bench for p in scored) / len(scored) if scored else None


def _ret(prices: PriceBook, ticker: str, start: date, end: date) -> float | None:
    a = prices.close_on_or_after(ticker, start)
    b = prices.close_on_or_after(ticker, end)
    if not a or not b or b[0] < a[0] or a[0] > end:
        return None
    if b[0] == a[0] and end > start:
        return None                       # no price after entry yet
    return (b[1] / a[1] - 1) * 100


def backtest(store, inv: Investor, prices: PriceBook, top_n: int = 10,
             today: date | None = None) -> CloneResult:
    today = today or date.today()
    quarters = sorted(store.quarters_13f(inv.key))            # oldest first
    result = CloneResult(inv.key)
    for i, q in enumerate(quarters):
        entry = store.filed_13f(inv.key, q)
        exit_ = store.filed_13f(inv.key, quarters[i + 1]) if i + 1 < len(quarters) else today
        if exit_ <= entry or entry > today:
            continue
        holdings, tickers = store.load_13f(inv.key, q)
        stocks = sorted((h for h in holdings if not h.put_call and h.value_usd > 0),
                        key=lambda h: -h.value_usd)[:top_n]
        total_w = sum(h.value_usd for h in stocks) or 1
        priced = []
        for h in stocks:
            t = tickers.get(h.key)
            r = _ret(prices, t, entry, exit_) if t else None
            if r is not None:
                priced.append((t, h.value_usd / total_w, r))
        if not priced:
            continue
        cover = sum(w for _, w, _ in priced)
        ret = sum(w / cover * r for _, w, r in priced)
        result.periods.append(Period(q, entry, exit_, ret, _ret(prices, BENCHMARK, entry, exit_),
                                     cover, [(t, w / cover, r) for t, w, r in priced]))
    return result


def backtest_all(store, investors: dict[str, Investor], prices: PriceBook) -> list[CloneResult]:
    out = [backtest(store, inv, prices) for inv in investors.values() if inv.files_13f]
    out = [r for r in out if r.periods]
    return sorted(out, key=lambda r: -(r.excess if r.excess is not None else (r.total or -1e9)))
