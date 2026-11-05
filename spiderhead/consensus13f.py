"""Superinvestor consensus from 13F filings (the "Dataroma" view).

For every investor with at least two stored quarters, compare the latest
quarter with the one before, then aggregate per stock across investors:
who owns it (and at what portfolio weight), who bought, who sold.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .holdings import diff_holdings
from .models import ChangeType, Investor


@dataclass
class ConsensusRow:
    key: str                                   # ticker, or issuer name when no ticker is known
    issuer: str
    owners: dict[str, float] = field(default_factory=dict)      # investor -> weight (0..1)
    value: int = 0                                               # combined USD value
    bought: dict[str, str] = field(default_factory=dict)         # investor -> NEW/ADD
    sold: dict[str, str] = field(default_factory=dict)           # investor -> TRIM/EXIT

    @property
    def avg_weight(self) -> float:
        return sum(self.owners.values()) / len(self.owners) if self.owners else 0.0


@dataclass
class Consensus:
    rows: dict[str, ConsensusRow]
    quarters: dict[str, str]                   # investor -> latest quarter used

    def most_owned(self, n: int = 25) -> list[ConsensusRow]:
        rows = [r for r in self.rows.values() if r.owners]
        return sorted(rows, key=lambda r: (-len(r.owners), -sum(r.owners.values())))[:n]

    def most_bought(self, n: int = 20) -> list[ConsensusRow]:
        rows = [r for r in self.rows.values() if r.bought]
        return sorted(rows, key=lambda r: (-len(r.bought), -len(r.owners), -r.value))[:n]

    def most_sold(self, n: int = 20) -> list[ConsensusRow]:
        rows = [r for r in self.rows.values() if r.sold]
        return sorted(rows, key=lambda r: (-len(r.sold), -r.value))[:n]


def build_consensus(store, investors: dict[str, Investor]) -> Consensus:
    rows: dict[str, ConsensusRow] = {}
    quarters: dict[str, str] = {}
    for inv in investors.values():
        qs = store.quarters_13f(inv.key)
        if len(qs) < 2:
            continue
        curr, curr_t = store.load_13f(inv.key, qs[0])
        prev, prev_t = store.load_13f(inv.key, qs[1])
        tickers = {**prev_t, **curr_t}
        quarters[inv.key] = qs[0]
        for ch in diff_holdings(prev, curr, lambda h: tickers.get(h.key)):
            if ch.put_call:                    # options are hedges/bets, not ownership
                continue
            key = ch.ticker or ch.issuer.upper()
            row = rows.setdefault(key, ConsensusRow(key, ch.issuer))
            if ch.curr_shares:
                row.owners[inv.key] = ch.portfolio_weight
                row.value += ch.curr_value_usd
            if ch.change in (ChangeType.NEW, ChangeType.ADD):
                row.bought[inv.key] = ch.change.value
            elif ch.change in (ChangeType.TRIM, ChangeType.EXIT):
                row.sold[inv.key] = ch.change.value
    return Consensus(rows, quarters)
