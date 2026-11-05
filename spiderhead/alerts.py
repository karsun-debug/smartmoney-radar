"""Instant alerts: a LINE message as soon as a watched investor posts a stock
call or a new trade filing appears — no waiting for the daily digest.

Run it right after ``spiderhead fetch`` on a short schedule (e.g. hourly).
Each post/trade is sent once; the store remembers what was already sent.
On the very first run nothing is sent (everything already in the database is
marked as seen) so you don't get flooded with history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .digest import display_names
from .models import Investor, Post, Signal, Trade
from .sectors import signal_label
from .store import Store

ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
SIDE = {"buy": "🟢買進", "sell": "🔴賣出", "stake": "🔷持股逾5%", "exchange": "⚪交換",
        "report": "📄提交申報"}
FORM = {"4": "Form 4", "PTR": "國會申報", "ARK daily": "ARK 每日持股"}


@dataclass
class Pending:
    posts: list[tuple[Post, list[Signal]]]
    trades: list[Trade]

    def __bool__(self) -> bool:
        return bool(self.posts or self.trades)

    def keys(self) -> list[tuple[str, str]]:
        return [(p.uid, "post") for p, _ in self.posts] + [(t.uid, "trade") for t in self.trades]


def find_pending(store: Store, investors: dict[str, Investor], lookback_days: int = 3,
                 min_confidence: float = 0.5, include_news: bool = False) -> Pending:
    start, end = date.today() - timedelta(days=lookback_days), date.today()
    kinds = ("own", "news") if include_news else ("own",)
    posts = []
    for p in store.posts_between(start, end, kinds=kinds):
        if p.investor_key not in investors:
            continue
        sigs = [s for s in store.signals_for(p)
                if s.confidence >= min_confidence and not s.ticker.startswith("SECTOR:")
                and s.stance.value != "neutral"]
        if sigs:
            posts.append((p, sigs))
    trades = [t for t in store.trades_between(start, end, by="filed") if t.investor_key in investors]
    seen = store.alerted([p.uid for p, _ in posts] + [t.uid for t in trades])
    return Pending([(p, s) for p, s in posts if p.uid not in seen],
                   [t for t in trades if t.uid not in seen])


def format_alert(pending: Pending, investors: dict[str, Investor], limit: int = 15) -> str:
    names = display_names(investors)
    lines = ["🔔 Smartmoney Radar 即時通知"]
    for t in pending.trades[:limit]:
        size = " · ".join(x for x in (f"{t.shares:,.0f} 股" if t.shares and "股" not in t.amount_text else "",
                                      t.amount_text) if x)
        if t.value_usd and "股" in size:
            size += f"（約 ${t.value_usd / 1e6:,.1f}M）"
        lines += ["", f"🧾 {names.get(t.investor_key, t.investor_key)} {SIDE.get(t.side, t.side)} "
                      f"{t.ticker or t.issuer[:40]}",
                  f"  {size} · 交易日 {t.traded:%m/%d} · {FORM.get(t.form, t.form)}"]
        if t.url:
            lines.append(f"  {t.url}")
    room = max(0, limit - len(pending.trades))
    for p, sigs in pending.posts[:room]:
        views = " ".join(f"{ICON[s.stance.value]}{signal_label(s.ticker, s.sector)}" for s in sigs[:4])
        text = " ".join(p.text.split())
        lines += ["", f"💬 {names.get(p.investor_key, p.investor_key)}（{p.source}）{views}",
                  f"  {text[:140]}{'…' if len(text) > 140 else ''}"]
        if p.url:
            lines.append(f"  {p.url}")
    hidden = len(pending.trades) + len(pending.posts) - min(limit, len(pending.trades) + len(pending.posts))
    if hidden > 0:
        lines += ["", f"…還有 {hidden} 則，請看每日摘要或儀表板"]
    return "\n".join(lines)


def run_alerts(store: Store, investors: dict[str, Investor], send, dry_run: bool = False,
               backfill: bool = False, **kw) -> tuple[int, str]:
    """Send pending alerts. Returns (number of items alerted, message text)."""
    pending = find_pending(store, investors, **kw)
    if store.alerts_count() == 0 and not backfill:
        store.mark_alerted(pending.keys())     # first run: remember, don't flood
        return 0, ""
    if not pending:
        return 0, ""
    text = format_alert(pending, investors)
    if not dry_run:
        send(text)
        store.mark_alerted(pending.keys())
    return len(pending.keys()), text
