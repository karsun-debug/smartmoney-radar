"""Smaller analytics shown around the site (all computed from stored data)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Move:
    ticker: str
    close: float
    change_pct: float
    day: date


def movers(prices, tickers: Iterable[str], max_age_days: int = 5) -> list[Move]:
    """Latest daily % change for each ticker with a fresh price (sorted by change, high to low)."""
    out = []
    for t in tickers:
        rows = prices.series(t)
        if len(rows) < 2 or rows[-1][0] < date.today() - timedelta(days=max_age_days):
            continue
        (d0, p0), (d1, p1) = rows[-2], rows[-1]
        if p0:
            out.append(Move(t, p1, (p1 / p0 - 1) * 100, d1))
    return sorted(out, key=lambda m: -m.change_pct)


OFFICER_WORDS = ("CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL", "PRESIDENT", "CHAIRMAN", "COO")


def officer_buys(buys, min_value: float = 1_000_000) -> list:
    """Open-market purchases by top officers worth at least ``min_value`` (openinsider's classic list)."""
    hits = [b for b in buys if b.value >= min_value and any(w in (b.title or "").upper() for w in OFFICER_WORDS)]
    return sorted(hits, key=lambda b: -b.value)


@dataclass(frozen=True)
class ClusterResult:
    ticker: str
    insiders: int
    last_buy: date
    ret: dict[int, float | None]
    spy: dict[int, float | None]


def cluster_performance(clusters, prices, horizons=(30, 90), today: date | None = None) -> list[ClusterResult]:
    """Return after the last buy of each cluster, vs SPY over the same days."""
    today = today or date.today()
    out = []
    for c in clusters:
        start = c.last
        rets, spys = {}, {}
        for h in horizons:
            end = start + timedelta(days=h)
            if end > today:
                rets[h] = spys[h] = None
                continue
            a, b = prices.close_on_or_after(c.ticker, start), prices.close_on_or_before(c.ticker, end)
            s0, s1 = prices.close_on_or_after("SPY", start), prices.close_on_or_before("SPY", end)
            rets[h] = (b[1] / a[1] - 1) * 100 if a and b and b[0] > a[0] else None
            spys[h] = (s1[1] / s0[1] - 1) * 100 if s0 and s1 and s1[0] > s0[0] else None
        out.append(ClusterResult(c.ticker, len(c.insiders), start, rets, spys))
    return out


@dataclass(frozen=True)
class Discount:
    ticker: str
    investor: str
    quarter: str
    ref_price: float          # 13F value / shares at quarter end
    price: float
    discount_pct: float       # negative = cheaper than the investor's quarter-end price
    weight: float


def below_guru_price(store, investors, prices, min_weight: float = 0.01) -> list[Discount]:
    """Stocks now cheaper than at the end of the quarter in which an investor held them (Dataroma's
    'low price' idea; quarter-end value is a proxy, not the investor's actual cost)."""
    out = []
    for inv in investors.values():
        qs = store.quarters_13f(inv.key)
        if not qs:
            continue
        holdings, tickers = store.load_13f(inv.key, qs[0])
        total = sum(h.value_usd for h in holdings) or 1
        for h in holdings:
            t = tickers.get(h.key)
            if not t or h.put_call or not h.shares or h.value_usd / total < min_weight:
                continue
            now = prices.close_on_or_before(t, date.today())
            if not now:
                continue
            ref = h.value_usd / h.shares
            disc = (now[1] / ref - 1) * 100
            if disc < 0:
                out.append(Discount(t, inv.key, qs[0], ref, now[1], disc, h.value_usd / total))
    return sorted(out, key=lambda d: d.discount_pct)
