"""Compare what investors say (signals) with what they do (13F changes)."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from .models import ChangeType, ConsistencyItem, PositionChange, Signal, Stance, Verdict

BUYING = {ChangeType.NEW, ChangeType.ADD}
SELLING = {ChangeType.TRIM, ChangeType.EXIT}


def quarter_end(d: date) -> date:
    """Quarter-end date (13F report period) that ``d`` falls in."""
    q_month = ((d.month - 1) // 3 + 1) * 3
    return date(d.year, q_month, 31 if q_month in (3, 12) else 30)


def check(signals: Iterable[Signal], changes: Iterable[PositionChange]) -> list[ConsistencyItem]:
    """Judge each signal against the position change for the same ticker.

    Pass the changes for the quarter in which the post was published: a 13F
    for quarter Q shows the net effect of trades made during Q. Put options
    are read as the opposite direction of the underlying.
    """
    by_ticker: dict[str, list[PositionChange]] = {}
    for ch in changes:
        if ch.ticker:
            by_ticker.setdefault(ch.ticker.upper(), []).append(ch)

    items: list[ConsistencyItem] = []
    for sig in signals:
        if sig.stance is Stance.NEUTRAL or sig.ticker.startswith("MACRO:"):
            continue
        rows = by_ticker.get(sig.ticker.upper())
        if not rows:
            items.append(ConsistencyItem(sig, Verdict.NOT_IN_13F, None))
            continue
        # Use the largest row (common stock usually) but let puts flip direction.
        row = max(rows, key=lambda r: max(r.curr_value_usd, r.prev_value_usd))
        direction = 0
        if row.change in BUYING:
            direction = 1
        elif row.change in SELLING:
            direction = -1
        if row.put_call == "PUT":
            direction = -direction
        want = 1 if sig.stance is Stance.BULLISH else -1
        if direction == 0:
            verdict = Verdict.HELD
        elif direction == want:
            verdict = Verdict.CONSISTENT
        else:
            verdict = Verdict.CONTRADICTS
        items.append(ConsistencyItem(sig, verdict, row))
    return items
