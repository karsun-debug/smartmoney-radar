"""Weekly report: a written summary of the week, by Claude or a template.

The data block handed to Claude is built only from what the database holds
(no web access), and the prompt tells it to stay within that data — the
report is a summary, not advice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from .consistency import check_trades
from .digest import build_digest
from .models import Investor, Verdict
from .sectors import SECTORS, signal_label
from .store import Store


@dataclass
class WeekData:
    start: date
    end: date
    payload: dict


def gather(store: Store, investors: dict[str, Investor], days: int = 7, end: date | None = None) -> WeekData:
    d = build_digest(store, investors, days, end=end)
    names = d.names
    trends = [{"sector": SECTORS.get(t.sector, t.sector), "investor_posts": t.investor_posts,
               "industry_news": t.industry_news, "sentiment": round(t.sentiment, 2),
               "change_pct": None if t.change_pct is None else round(t.change_pct),
               "investors": sorted(names.get(k, k) for k in t.investors)} for t in d.trends[:8]]
    consensus = [{"ticker": c.ticker, "views": {names.get(k, k): s.value for k, s in c.views.items()}}
                 for c in d.consensus[:10]]
    trades = [{"who": names.get(t.investor_key, t.investor_key), "side": t.side, "ticker": t.ticker,
               "issuer": t.issuer[:60], "amount": t.amount_text, "shares": t.shares, "traded": str(t.traded),
               "form": t.form} for t in d.trades[:30]]
    calls = []
    for post, sigs in d.feed:
        for s in sigs:
            if s.is_own_view and s.stance.value != "neutral" and not s.ticker.startswith("SECTOR:"):
                calls.append({"who": names.get(post.investor_key, post.investor_key), "date": str(post.published),
                              "ticker": signal_label(s.ticker, s.sector), "stance": s.stance.value,
                              "quote": post.text[:200]})
    own = [s for p, sigs in d.feed for s in sigs if s.is_own_view]
    recent_trades = store.trades_between(d.start - timedelta(days=60), d.end)
    contradictions = [{"who": names.get(c.signal.post.investor_key, c.signal.post.investor_key),
                       "ticker": c.signal.ticker, "said": c.signal.stance.value, "did": c.trade.side,
                       "trade_date": str(c.trade.traded)}
                      for c in check_trades(own, recent_trades) if c.verdict is Verdict.CONTRADICTS]
    clusters = [{"ticker": c.ticker, "issuer": c.issuer, "insiders": len(c.insiders),
                 "value_musd": round(c.value / 1e6, 2)} for c in d.clusters[:8]]
    payload = {"period": f"{d.start} ~ {d.end}", "sector_trends": trends, "consensus_tickers": consensus,
               "filed_trades": trades, "own_calls": calls[:40], "say_vs_do_contradictions": contradictions,
               "insider_clusters": clusters, "macro": _macro_snapshot(store),
               "broken_sources": [h.name for h in d.health if h.level == "fail"]}
    return WeekData(d.start, d.end, payload)


def _macro_snapshot(store: Store) -> dict:
    from .macro import load_views, signals
    views = load_views(store, years=1)
    return {"indicators": [{"name": m.series.name, "latest": round(m.latest[1], 2), "date": str(m.latest[0]),
                            "change_1m": None if m.change(30) is None else round(m.change(30), 2)}
                           for m in views if m.latest],
            "signals": [{"name": s.name, "level": s.level, "text": s.text} for s in signals(views)]}


def template_report(w: WeekData) -> str:
    p = w.payload
    side = {"buy": "買進", "sell": "賣出", "stake": "持股逾 5%", "report": "提交申報", "exchange": "交換"}
    lines = [f"# 大神週報（{p['period']}）", ""]
    lines += ["## 產業動向"] + ([f"- {t['sector']}：大神提及 {t['investor_posts']} 則、新聞 {t['industry_news']} 則，"
                                f"多空 {t['sentiment']:+.2f}" + (f"（{'、'.join(t['investors'])}）" if t['investors'] else "")
                                for t in p["sector_trends"]] or ["- 本週沒有資料"])
    lines += ["", "## 實際交易（申報）"] + ([f"- {t['who']} {side.get(t['side'], t['side'])} "
                                           f"{t['ticker'] or t['issuer']} {t['amount'] or ''}（{t['traded']}）"
                                           for t in p["filed_trades"][:15]] or ["- 本週沒有新申報"])
    lines += ["", "## 大神喊單"] + ([f"- {c['who']} {'看多' if c['stance'] == 'bullish' else '看空'} {c['ticker']}"
                                    for c in p["own_calls"][:15]] or ["- 本週沒有明確喊單"])
    if p["consensus_tickers"]:
        lines += ["", "## 多位大神同時在談"] + [
            f"- {c['ticker']}：" + "、".join(f"{k}（{'多' if v == 'bullish' else '空'}）" for k, v in c["views"].items())
            for c in p["consensus_tickers"]]
    if p["say_vs_do_contradictions"]:
        lines += ["", "## 言行不一"] + [f"- {c['who']} 公開{'看多' if c['said'] == 'bullish' else '看空'} {c['ticker']}，"
                                       f"但申報顯示{side.get(c['did'], c['did'])}（{c['trade_date']}）"
                                       for c in p["say_vs_do_contradictions"]]
    macro = p.get("macro") or {}
    if macro.get("signals"):
        mark = {"ok": "✅", "warn": "⚠️", "alert": "🚨"}
        lines += ["", "## 總經訊號"] + [f"- {mark.get(s['level'], '')} {s['name']}：{s['text']}"
                                       for s in macro["signals"]]
    if p["insider_clusters"]:
        lines += ["", "## 內部人群買"] + [f"- {c['ticker']}（{c['issuer']}）：{c['insiders']} 位內部人買進 "
                                         f"${c['value_musd']}M" for c in p["insider_clusters"]]
    lines += ["", "> 僅供研究參考，不構成投資建議。"]
    return "\n".join(lines)


SYSTEM = """You write a weekly briefing in Traditional Chinese (Taiwan usage) for a \
reader who follows famous US investors. You get one JSON object with everything \
known about the week: sector trends, investors' own calls, trades they filed \
with regulators (13F/Form 4/13D/8-K/congressional disclosures), say-vs-do \
contradictions, insider cluster buys and broken data sources.

Write Markdown with these sections, in order, skipping any with no data:
# 大神週報（period）
## 本週重點 — 3 to 5 bullets, the most notable facts of the week
## 產業動向
## 大神實際動作
## 言行不一
## 內部人群買
## 總經 — from the macro block: the signals and the biggest 1-month moves
## 資料提醒 — only if broken_sources is non-empty

Rules: use only facts present in the JSON; name the investor and ticker for each \
claim; say "申報顯示" for filed trades and "公開表示" for calls; do not predict \
prices or recommend buying or selling; keep it under 600 Chinese characters \
plus bullets. End with the line: > 僅供研究參考，不構成投資建議。"""


def claude_report(w: WeekData, client=None, model: str = "claude-opus-5-5") -> str:
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": "<week_data>\n" + json.dumps(w.payload, ensure_ascii=False)
                   + "\n</week_data>"}],
    )
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "refusal" or not text:
        return template_report(w)
    return text


def markdown_to_html(md: str) -> str:
    """Tiny Markdown subset (headings, bullets, quotes, bold, paragraphs) -> escaped HTML."""
    import html
    import re
    out, in_list = [], False

    def inline(t: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(t))
    for raw in md.splitlines():
        line = raw.rstrip()
        bullet = re.match(r"^\s*[-*]\s+(.*)", line)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(bullet.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line.strip():
            continue
        h = re.match(r"^(#{1,3})\s+(.*)", line)
        if h:
            level = len(h.group(1))
            out.append(f"<h{level}>{inline(h.group(2))}</h{level}>")
        elif line.startswith(">"):
            out.append(f'<p class="sub">{inline(line.lstrip("> "))}</p>')
        else:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)
