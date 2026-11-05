"""ARK Invest daily trades, derived from the daily holdings files ARK publishes.

ARK posts every ETF's full holdings each trading day. Comparing two days gives
the shares bought or sold per stock — the same data ARK's daily trade e-mails
report. ETF creations/redemptions scale every position by roughly the same
percentage, so that common "flow" factor (the median change across holdings)
is removed before a change counts as a trade.

URL patterns follow github.com/NexVeridian/ark-invest-api-rust-data.
"""
from __future__ import annotations

import csv
import io
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from .models import Trade

BASE = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/"
ARK_FUNDS: dict[str, str] = {
    "ARKK": BASE + "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv",
    "ARKQ": BASE + "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
    "ARKW": BASE + "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv",
    "ARKG": BASE + "ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv",
    "ARKF": BASE + "ARK_ARK_BLOCKCHAIN_&_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv",
    "ARKX": BASE + "ARK_ARK_SPACE_&_DEFENSE_INNOVATION_ETF_ARKX_HOLDINGS.csv",
}
FUND_PAGE = "https://www.ark-funds.com/funds/{fund}"


@dataclass(frozen=True)
class ArkHolding:
    ticker: str
    company: str
    cusip: str
    shares: float
    value: float

    @property
    def key(self) -> str:
        return self.cusip or self.ticker or self.company


def _num(v: str | None) -> float:
    v = (v or "").replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(v)
    except ValueError:
        return 0.0


def _date(v: str) -> date | None:
    v = v.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def parse_ark_csv(text: str) -> tuple[date | None, list[ArkHolding]]:
    """Parse an ARK holdings CSV (tolerates header variants and footer notes)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    day, out = None, []
    for row in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        shares = _num(r.get("shares"))
        company = r.get("company", "")
        if not company or shares <= 0:
            continue                      # disclaimers, cash lines without shares
        if not r.get("ticker") and not r.get("cusip"):
            continue                      # cash / money-market sweep: not a stock position
        day = day or _date(r.get("date", ""))
        value = _num(r.get("market value ($)") or r.get("market value($)") or r.get("market value"))
        out.append(ArkHolding(r.get("ticker", "").upper().split(" ")[0], company,
                              r.get("cusip", "").upper(), shares, value))
    return day, out


def diff_ark(fund: str, prev: list[ArkHolding], curr: list[ArkHolding], day: date,
             investor_key: str = "wood", min_flow_dev: float = 0.01) -> list[Trade]:
    """Trades implied by two consecutive holdings files of one fund."""
    p = {h.key: h for h in prev}
    c = {h.key: h for h in curr}
    common = [k for k in p.keys() & c.keys() if p[k].shares]
    flow = statistics.median([c[k].shares / p[k].shares - 1 for k in common]) if common else 0.0
    trades = []
    for k in p.keys() | c.keys():
        before, after = p.get(k), c.get(k)
        ref = after or before
        expected = (before.shares * (1 + flow)) if before else 0.0
        actual = after.shares if after else 0.0
        delta = actual - expected
        base = max(expected, actual, 1.0)
        if before and after and abs(delta) / base < min_flow_dev:
            continue                       # explained by ETF flows / rounding
        if abs(delta) < 1:
            continue
        price = (ref.value / ref.shares) if ref.shares else None
        tag = "新建倉" if before is None else "清倉" if after is None else ""
        trades.append(Trade(
            investor_key, day, day, "buy" if delta > 0 else "sell", "ARK daily",
            ref.ticker or None, ref.company, round(abs(delta)), price,
            f"{fund} {'+' if delta > 0 else '-'}{abs(delta):,.0f} 股{(' ' + tag) if tag else ''}",
            FUND_PAGE.format(fund=fund.lower())))
    return trades


class ArkCollector:
    """Fetch today's files, store them, and diff against the previous stored day."""

    def __init__(self, store, http_get: Callable[[str, dict], bytes],
                 funds: dict[str, str] | None = None, investor_key: str = "wood"):
        self.store = store
        self.get = http_get
        self.funds = funds or ARK_FUNDS
        self.investor_key = investor_key
        self.errors: list[str] = []
        from .health import HealthLog
        self.health = HealthLog()

    def collect(self) -> list[Trade]:
        trades: list[Trade] = []
        for fund, url in self.funds.items():
            try:
                day, rows = parse_ark_csv(self.get(url, {}).decode("utf-8", "replace"))
            except Exception as exc:
                self.errors.append(f"ark {fund}: {exc}")
                self.health.record("ark", fund, False, 0, str(exc))
                continue
            if not day or not rows:
                self.errors.append(f"ark {fund}: no holdings parsed")
                self.health.record("ark", fund, False, 0, "no holdings parsed (file format changed?)")
                continue
            prev_day, prev = self.store.ark_previous(fund, day)
            self.store.save_ark(fund, day, rows)
            found = diff_ark(fund, prev, rows, day, self.investor_key) if prev else []
            trades += found
            self.health.record("ark", fund, True, len(found), f"{len(rows)} holdings on {day}")
        return trades
