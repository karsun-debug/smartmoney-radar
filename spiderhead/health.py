"""Per-source fetch results, so a silently broken feed gets noticed.

Collectors call ``record``; the CLI saves the results to the store after each
run; the dashboard and the daily LINE digest show which sources are stale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

FAMILY_NAMES = {
    "x": "X 貼文", "rss": "RSS", "news": "大神新聞", "industry": "產業新聞",
    "sec": "SEC 申報（Form 4 / 13D / 8-K）", "sec13f": "SEC 13F 季報", "house": "眾議院申報", "congress": "國會交易（LuxAlgo）",
    "ark": "ARK 每日持股", "prices": "股價", "insiders": "全市場內部人買進（Form 4）",
}


@dataclass(frozen=True)
class SourceResult:
    family: str
    label: str
    ok: bool
    items: int = 0
    detail: str = ""


@dataclass
class HealthLog:
    results: list[SourceResult] = field(default_factory=list)

    def record(self, family: str, label: str, ok: bool, items: int = 0, detail: str = "") -> None:
        self.results.append(SourceResult(family, label, ok, items, detail[:300]))


@dataclass(frozen=True)
class FamilyStatus:
    family: str
    last_run: datetime | None
    last_ok: datetime | None
    ok_now: int          # labels OK in the latest run
    failing_now: int     # labels failing in the latest run
    items_7d: int
    last_error: str

    @property
    def name(self) -> str:
        return FAMILY_NAMES.get(self.family, self.family)

    def stale(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.last_ok is None:
            return True
        return now - self.last_ok > timedelta(days=2)

    @property
    def level(self) -> str:
        """ok / warn / fail for display."""
        if self.last_ok is None or self.stale():
            return "fail"
        if self.failing_now:
            return "warn"
        return "ok"
