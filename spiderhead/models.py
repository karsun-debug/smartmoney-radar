"""Core data types shared across SpiderHead modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


@dataclass(frozen=True)
class Investor:
    key: str            # short id used on the command line, e.g. "buffett"
    name: str           # person, e.g. "Warren Buffett"
    fund: str           # filing entity, e.g. "Berkshire Hathaway Inc"
    cik: str            # SEC Central Index Key of the filing entity
    handles: dict[str, str] = field(default_factory=dict)  # {"x": "...", "rss": "..."}


@dataclass(frozen=True)
class Holding:
    """One aggregated line of a 13F information table."""
    issuer: str
    cusip: str
    title_of_class: str
    value_usd: int
    shares: int
    share_type: str = "SH"          # SH (shares) or PRN (principal amount)
    put_call: str | None = None     # "PUT" / "CALL" for option positions

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.cusip, self.put_call)


class ChangeType(str, Enum):
    NEW = "NEW"      # 新建倉
    ADD = "ADD"      # 加碼
    TRIM = "TRIM"    # 減碼
    EXIT = "EXIT"    # 清倉
    HOLD = "HOLD"    # 股數不變


@dataclass(frozen=True)
class PositionChange:
    issuer: str
    cusip: str
    put_call: str | None
    change: ChangeType
    prev_shares: int
    curr_shares: int
    prev_value_usd: int
    curr_value_usd: int
    portfolio_weight: float  # weight in the *current* portfolio (0 for exits)
    ticker: str | None = None

    @property
    def share_delta_pct(self) -> float | None:
        if self.prev_shares == 0:
            return None
        return (self.curr_shares - self.prev_shares) / self.prev_shares * 100


@dataclass(frozen=True)
class Filing:
    cik: str
    accession: str
    form: str
    filing_date: date
    report_date: date | None   # quarter end the holdings refer to


@dataclass(frozen=True)
class Post:
    investor_key: str
    published: date
    text: str
    source: str = "manual"     # x / rss / interview / letter / manual
    url: str | None = None


class Stance(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Signal:
    """A view on one ticker or theme extracted from a post."""
    ticker: str            # ticker, or "MACRO:<theme>" for non-stock views
    stance: Stance
    confidence: float      # 0..1
    rationale: str
    post: Post


class Verdict(str, Enum):
    CONSISTENT = "consistent"        # 說到做到
    CONTRADICTS = "contradicts"      # 嘴上看多卻在賣 / 嘴上看空卻在買
    HELD = "held"                    # 有持倉但沒動作
    NOT_IN_13F = "not_in_13f"        # 13F 看不到（未持有 / 空單 / 非美股 / 低於門檻）


@dataclass(frozen=True)
class ConsistencyItem:
    signal: Signal
    verdict: Verdict
    change: PositionChange | None
