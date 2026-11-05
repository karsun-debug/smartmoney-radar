"""Push the daily digest to LINE via the Messaging API.

LINE Notify was discontinued in March 2025, so this uses a LINE Official
Account + Messaging API channel:

* ``LINE_CHANNEL_ACCESS_TOKEN`` — long-lived channel access token
* ``LINE_TO`` — optional userId / groupId to push to. If unset, the message
  is broadcast to every friend of the official account.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable

from .digest import Digest
from .sectors import SECTORS, signal_label

PUSH_URL = "https://api.line.me/v2/bot/message/push"
BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
MAX_TEXT = 5000          # LINE text message limit (characters)
MAX_MESSAGES = 5         # messages per request

ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}


def _short(text: str, n: int) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 1] + "…"


def format_line(d: Digest, dashboard_url: str | None = None, max_posts: int = 8) -> str:
    """Plain-text digest (LINE does not render Markdown tables)."""
    period = f"{d.start:%m/%d}" if d.start == d.end else f"{d.start:%m/%d}–{d.end:%m/%d}"
    out = [f"📊 Smartmoney Radar 大神摘要 {period}", ""]

    out.append("🔥 產業熱度")
    if not d.trends:
        out.append("（這段期間沒有資料）")
    for i, t in enumerate(d.trends[:5], 1):
        mood = "🟢偏多" if t.sentiment > 0.2 else "🔴偏空" if t.sentiment < -0.2 else "⚪中性"
        chg = "" if t.change_pct is None else f"（{t.change_pct:+.0f}%）"
        who = "、".join(sorted(d.names.get(k, k) for k in t.investors))
        out.append(f"{i}. {SECTORS.get(t.sector, t.sector)} {t.total}則{chg} {mood}"
                   + (f"\n   {who}" if who else ""))

    if d.consensus:
        out += ["", "🤝 多位大神同時在談"]
        for c in d.consensus[:5]:
            views = " ".join(f"{d.names.get(k, k)}{ICON[s.value]}" for k, s in sorted(c.views.items()))
            out.append(f"• {c.ticker}：{views}")

    if d.trades:
        side = {"buy": "🟢買進", "sell": "🔴賣出", "stake": "🔷持股逾5%", "exchange": "⚪交換"}
        out += ["", "🧾 最新對帳單（實際申報交易）"]
        for t in d.trades[:8]:
            size = " ".join(x for x in (f"{t.shares:,.0f}股" if t.shares and "股" not in t.amount_text else "",
                                        t.amount_text) if x)
            out.append(f"• {d.names.get(t.investor_key, t.investor_key)} {side.get(t.side, t.side)} "
                       f"{t.ticker or t.issuer[:30]} {size}（{t.traded:%m/%d}）"
                       + (f"\n  {t.url}" if t.url else ""))
        if len(d.trades) > 8:
            out.append(f"…還有 {len(d.trades) - 8} 筆")

    out += ["", "👤 大神動態"]
    if not d.feed:
        out.append("（沒有新發文）")
    for post, sigs in d.feed[:max_posts]:
        tag = "本人" if post.kind == "own" else "報導"
        views = " ".join(f"{ICON[s.stance.value]}{signal_label(s.ticker, s.sector)}" for s in sigs[:4])
        out.append(f"• {d.names.get(post.investor_key, post.investor_key)}（{tag}·{post.source}）"
                   f"{post.published:%m/%d}\n  {_short(post.text, 90)}" + (f"\n  {views}" if views else ""))
        if post.url:
            out.append(f"  {post.url}")
    if len(d.feed) > max_posts:
        out.append(f"…還有 {len(d.feed) - max_posts} 則")
    if dashboard_url:
        out += ["", f"📈 儀表板：{dashboard_url}"]
    out += ["", "僅供研究參考，不構成投資建議。"]
    return "\n".join(out)


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Split on line boundaries into chunks LINE accepts."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:            # pathological single line
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip("\n"))
    return chunks


HttpPost = Callable[[str, dict[str, str], bytes], None]


def _requests_post(url: str, headers: dict[str, str], body: bytes) -> None:
    import requests
    resp = requests.post(url, headers=headers, data=body, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"LINE API {resp.status_code}: {resp.text[:300]}")


class LineNotifier:
    def __init__(self, token: str | None = None, to: str | None = None,
                 http_post: HttpPost = _requests_post):
        self.token = token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
        if not self.token:
            raise ValueError("set LINE_CHANNEL_ACCESS_TOKEN (LINE Messaging API channel token)")
        self.to = to if to is not None else os.environ.get("LINE_TO")
        self.post = http_post

    def send(self, text: str) -> int:
        """Send ``text`` (split as needed). Returns the number of API requests made."""
        chunks = split_text(text)
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        requests_made = 0
        for i in range(0, len(chunks), MAX_MESSAGES):
            messages = [{"type": "text", "text": c} for c in chunks[i:i + MAX_MESSAGES]]
            body: dict = {"messages": messages}
            url = BROADCAST_URL
            if self.to:
                body["to"] = self.to
                url = PUSH_URL
            self.post(url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8"))
            requests_made += 1
        return requests_made
