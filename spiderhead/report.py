"""Plain-text / Markdown rendering of holdings changes and consistency checks."""
from __future__ import annotations

from collections.abc import Iterable

from .models import ChangeType, ConsistencyItem, Investor, PositionChange, Verdict

CHANGE_LABEL = {
    ChangeType.NEW: "🆕 新建倉", ChangeType.ADD: "⬆️ 加碼", ChangeType.TRIM: "⬇️ 減碼",
    ChangeType.EXIT: "❌ 清倉", ChangeType.HOLD: "＝ 不變",
}
VERDICT_LABEL = {
    Verdict.CONSISTENT: "✅ 說到做到", Verdict.CONTRADICTS: "⚠️ 言行不一",
    Verdict.HELD: "➖ 持有未動", Verdict.NOT_IN_13F: "❔ 13F 查無",
}


def _usd(v: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,}"


def holdings_markdown(investor: Investor, quarter: str, changes: Iterable[PositionChange],
                      top: int = 20, include_hold: bool = False) -> str:
    lines = [f"## {investor.name}（{investor.fund}）— {quarter} 持倉變化", "",
             "| 動作 | 標的 | 股數變化 | 目前市值 | 占組合 |", "|---|---|---|---|---|"]
    shown = 0
    for ch in changes:
        if ch.change is ChangeType.HOLD and not include_hold:
            continue
        name = ch.ticker or ch.issuer
        if ch.put_call:
            name += f" ({ch.put_call})"
        pct = ch.share_delta_pct
        delta = f"{ch.prev_shares:,} → {ch.curr_shares:,}" + (f" ({pct:+.1f}%)" if pct is not None else "")
        lines.append(f"| {CHANGE_LABEL[ch.change]} | {name} | {delta} | "
                     f"{_usd(ch.curr_value_usd)} | {ch.portfolio_weight * 100:.2f}% |")
        shown += 1
        if shown >= top:
            break
    if shown == 0:
        lines.append("| — | 本季無變動 | | | |")
    return "\n".join(lines)


def consistency_markdown(items: Iterable[ConsistencyItem]) -> str:
    lines = ["## 言行一致檢查", "", "| 日期 | 標的 | 公開看法 | 實際動作 | 判定 | 依據 |",
             "|---|---|---|---|---|---|"]
    for it in items:
        s = it.signal
        action = CHANGE_LABEL[it.change.change] if it.change else "—"
        lines.append(f"| {s.post.published} | {s.ticker} | {s.stance.value} ({s.confidence:.1f}) | "
                     f"{action} | {VERDICT_LABEL[it.verdict]} | {s.rationale.replace('|', '/')} |")
    return "\n".join(lines)
