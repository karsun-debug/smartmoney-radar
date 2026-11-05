"""One report card per investor: are they worth listening to?

Three independent questions, each answered only when there is enough data:

* 說到做到 — of their public calls that can be matched to a filed trade (or a
  13F change), how often did the trade go the same way?
* 喊單準確 — after a public call, did the stock move their way (90 days, or
  30 days when 90-day outcomes aren't available yet)?
* 跟單價值 — copying their 13F on filing day, did it beat SPY?

The overall grade averages whichever of the three are available, mapped to
0–100. With fewer than ``MIN_SAMPLES`` data points a part is left out rather
than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .consistency import check_trades
from .models import Investor, Verdict
from .performance import scorecards

MIN_SAMPLES = 3


@dataclass
class ReportCard:
    investor: str
    consistent: int = 0
    contradicts: int = 0
    calls_scored: int = 0
    call_hit_rate: float | None = None
    call_horizon: int | None = None
    clone_excess: float | None = None          # percentage points vs SPY
    clone_quarters: int = 0

    @property
    def consistency(self) -> float | None:
        n = self.consistent + self.contradicts
        return self.consistent / n if n >= MIN_SAMPLES else None

    @property
    def parts(self) -> dict[str, float]:
        """Each available component on a 0..100 scale."""
        out = {}
        if self.consistency is not None:
            out["說到做到"] = self.consistency * 100
        if self.call_hit_rate is not None and self.calls_scored >= MIN_SAMPLES:
            out["喊單準確"] = self.call_hit_rate * 100
        if self.clone_excess is not None and self.clone_quarters >= 2:
            # +20pp or more over SPY -> 100, -20pp or worse -> 0, linear between.
            out["跟單價值"] = max(0.0, min(100.0, 50 + self.clone_excess * 2.5))
        return out

    @property
    def score(self) -> float | None:
        p = self.parts
        return sum(p.values()) / len(p) if p else None

    @property
    def grade(self) -> str:
        s = self.score
        if s is None:
            return "—"
        return "A" if s >= 75 else "B" if s >= 60 else "C" if s >= 45 else "D"


def build_cards(store, investors: dict[str, Investor], calls=None, clones=None,
                lookback_days: int = 365, today: date | None = None) -> dict[str, ReportCard]:
    from .dashboard import holdings_from_store
    today = today or date.today()
    start = today - timedelta(days=lookback_days)
    cards = {k: ReportCard(k) for k in investors}
    own = [s for s in store.signals_between(start, today) if s.is_own_view]
    trades = store.trades_between(start - timedelta(days=30), today)
    for c in check_trades(own, trades):
        card = cards.get(c.signal.post.investor_key)
        if card is None:
            continue
        if c.verdict is Verdict.CONSISTENT:
            card.consistent += 1
        elif c.verdict is Verdict.CONTRADICTS:
            card.contradicts += 1
    for k, inv in investors.items():
        h = holdings_from_store(store, inv) if inv.files_13f else None
        if h:
            for item in h[2]:
                if item.verdict is Verdict.CONSISTENT:
                    cards[k].consistent += 1
                elif item.verdict is Verdict.CONTRADICTS:
                    cards[k].contradicts += 1
    if calls:
        long, short = scorecards(calls, 90), scorecards(calls, 30)
        for k, card in cards.items():
            sc = long.get(k)
            horizon = 90
            if not sc or sc.scored < MIN_SAMPLES:
                sc, horizon = short.get(k), 30
            if sc and sc.scored:
                card.calls_scored, card.call_hit_rate, card.call_horizon = sc.scored, sc.hit_rate, horizon
    for r in clones or []:
        if r.investor in cards:
            cards[r.investor].clone_excess = r.excess
            cards[r.investor].clone_quarters = len(r.periods)
    return cards

