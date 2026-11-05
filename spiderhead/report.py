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


STANCE_ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}


def _snippet(text: str, n: int = 110) -> str:
    one = " ".join(text.split()).replace("|", "/")
    return one if len(one) <= n else one[: n - 1] + "…"


def feed_markdown(rows, names: dict[str, str]) -> str:
    """``rows`` is a list of (Post, [Signal]) newest first."""
    lines = ["## 大神最新動態", "", "| 日期 | 誰 | 來源 | 內容 | 觀點 |", "|---|---|---|---|---|"]
    for post, signals in rows:
        from .sectors import signal_label
        views = " ".join(f"{STANCE_ICON[s.stance.value]}{signal_label(s.ticker, s.sector)}"
                         for s in signals) or "—"
        src = "本人" if post.kind == "own" else "報導"
        link = f"[{post.source}]({post.url})" if post.url else post.source
        lines.append(f"| {post.published} | {names.get(post.investor_key, post.investor_key)} | "
                     f"{src}·{link} | {_snippet(post.text)} | {views} |")
    if len(lines) == 4:
        lines.append("| — | 這段期間沒有新發文 | | | |")
    return "\n".join(lines)


def trends_markdown(trends, consensus_rows, names: dict[str, str]) -> str:
    from .sectors import SECTORS
    lines = ["## 產業熱度", "", "| 產業 | 大神提及 | 產業新聞 | 多空 | 熱度變化 | 誰在談 |",
             "|---|---|---|---|---|---|"]
    for t in trends:
        mood = "🟢 偏多" if t.sentiment > 0.2 else "🔴 偏空" if t.sentiment < -0.2 else "⚪ 中性"
        chg = "🆕" if t.change_pct is None else f"{t.change_pct:+.0f}%"
        who = "、".join(sorted(names.get(i, i) for i in t.investors)) or "—"
        lines.append(f"| {SECTORS.get(t.sector, t.sector)} | {t.investor_posts} | {t.industry_news} | "
                     f"{mood} ({t.sentiment:+.2f}) | {chg} | {who} |")
    if len(lines) == 4:
        lines.append("| — | 尚無資料 | | | | |")
    lines += ["", "## 多位大神同時在談的標的", ""]
    if not consensus_rows:
        lines.append("目前沒有兩位以上大神同時提到的個股。")
    for c in consensus_rows:
        views = "、".join(f"{names.get(i, i)} {STANCE_ICON[s.value]}" for i, s in sorted(c.views.items()))
        lines.append(f"- **{c.ticker}**（{c.investors} 位）：{views}")
    return "\n".join(lines)
