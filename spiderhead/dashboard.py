"""Static HTML dashboard: industry heat over time + one page per investor.

``build_site`` writes plain files (index.html, investor/<key>.html, assets/)
that open from disk or can be hosted anywhere (GitHub Pages, S3, Netlify).
Charts are inline SVG with a tiny vanilla-JS hover layer — no CDN needed.
"""
from __future__ import annotations

import html
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from .consistency import TradeCheck, check_trades
from .digest import build_digest, investor_profile, sector_series
from .performance import CallResult, PriceBook, Scorecard, score_calls, scorecards
from .models import ChangeType, ConsistencyItem, Investor, PositionChange, Post, Signal, Trade, Verdict
from .sectors import SECTORS, signal_label
from .store import Store, window

esc = html.escape

STANCE = {"bullish": ("▲", "看多", "bull"), "bearish": ("▼", "看空", "bear"),
          "neutral": ("●", "中性", "neutral")}
CHANGE = {ChangeType.NEW: "新建倉", ChangeType.ADD: "加碼", ChangeType.TRIM: "減碼",
          ChangeType.EXIT: "清倉", ChangeType.HOLD: "不變"}
VERDICT = {Verdict.CONSISTENT: ("✓", "說到做到", "bull"), Verdict.CONTRADICTS: ("!", "言行不一", "bear"),
           Verdict.HELD: ("–", "持有未動", "neutral"), Verdict.NOT_IN_13F: ("?", "13F 查無", "neutral")}
PROOF = {"13F": "13F 季報", "13F+daily": "13F + 每日交易", "8-K": "8-K 公告",
         "STOCK Act": "國會交易申報"}
SIDE = {"buy": ("▲", "買進", "bull"), "sell": ("▼", "賣出", "bear"), "report": ("📄", "提交申報", "neutral"),
        "stake": ("◆", "持股逾 5%", "neutral"), "exchange": ("●", "交換", "neutral")}
FORM_LABEL = {"4": "Form 4", "4/A": "Form 4/A", "PTR": "國會申報"}

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--series:#2a78d6;
--series-soft:rgba(42,120,214,.12);--bull:#006300;--bear:#d03b3b;--chip:#f0efec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--series:#3987e5;--series-soft:rgba(57,135,229,.18);--bull:#0ca30c;
--bear:#e66767;--chip:#2c2c2a}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--series:#3987e5;--series-soft:rgba(57,135,229,.18);--bull:#0ca30c;--bear:#e66767;--chip:#2c2c2a}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",
"PingFang TC","Noto Sans TC",sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px 64px}
a{color:var(--series)}
header.top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between}
h1{font-size:26px;margin:0}h2{font-size:18px;margin:36px 0 12px}h3{font-size:15px;margin:0}
.sub{color:var(--ink-2);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:28px;font-weight:600}.tile .l{color:var(--ink-2);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.card .meta{display:flex;justify-content:space-between;gap:8px;color:var(--ink-2);font-size:13px;margin:2px 0 6px}
.chart{position:relative}.chart svg{display:block;width:100%;overflow:visible}
.gridline{stroke:var(--grid);stroke-width:1}.baseline{stroke:var(--axis);stroke-width:1}
.line{fill:none;stroke:var(--series);stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.area{fill:var(--series-soft)}
.bar{fill:var(--series)}.bar:hover,.bar:focus{opacity:.75;outline:none}
.cross{stroke:var(--ink-2);stroke-width:1;vector-effect:non-scaling-stroke;visibility:hidden}
.axis{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:6px;padding:4px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:9;white-space:nowrap}
.badge{display:inline-flex;gap:4px;align-items:center;border-radius:999px;padding:1px 8px;font-size:12px;
background:var(--chip);color:var(--ink);white-space:nowrap}
.badge.bull .i{color:var(--bull)}.badge.bear .i{color:var(--bear)}.badge.neutral .i{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink-2);font-weight:500;font-size:13px}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:10px}
.feed{list-style:none;margin:0;padding:0}
.feed li{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px}
.feed .who{font-weight:600}.feed .txt{margin:4px 0 6px;overflow-wrap:anywhere}
.hbar{display:grid;grid-template-columns:120px 1fr 32px;gap:8px;align-items:center;font-size:13px;margin:6px 0}
.hbar .track{height:10px}.hbar .fill{height:10px;background:var(--series);border-radius:0 4px 4px 0}
details{margin-top:8px}summary{cursor:pointer;color:var(--ink-2);font-size:13px}
.note{color:var(--ink-2);font-size:14px;margin:8px 0 0}
.empty{color:var(--muted);font-size:14px}
footer{margin-top:48px;color:var(--muted);font-size:12px}
nav.top{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:14px;margin:0 0 18px;padding-bottom:10px;border-bottom:1px solid var(--grid)}
nav.top a{text-decoration:none;color:var(--ink-2)}nav.top a.on{color:var(--ink);font-weight:600}
nav.top .brand{color:var(--ink);font-weight:700;margin-right:8px}
"""

JS = """
(function(){
const tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
function show(lines,x,y){tip.replaceChildren();lines.forEach((t,i)=>{const d=document.createElement('div');
 d.textContent=t;if(i===0)d.style.fontWeight='600';tip.appendChild(d)});tip.style.display='block';
 const w=tip.offsetWidth;tip.style.left=Math.min(x+12,innerWidth-w-8)+'px';tip.style.top=(y+12)+'px'}
function hide(){tip.style.display='none'}
document.querySelectorAll('.chart[data-labels]').forEach(c=>{
 const labels=JSON.parse(c.dataset.labels),values=JSON.parse(c.dataset.values),unit=c.dataset.unit||'';
 const svg=c.querySelector('svg'),cross=c.querySelector('.cross');
 function at(e){const r=svg.getBoundingClientRect();const f=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));
  const i=Math.round(f*(labels.length-1));const x=labels.length>1?i/(labels.length-1)*100:50;
  cross.setAttribute('x1',x);cross.setAttribute('x2',x);cross.style.visibility='visible';
  show([labels[i],values[i]+unit],e.clientX,e.clientY)}
 svg.addEventListener('pointermove',at);svg.addEventListener('pointerleave',()=>{cross.style.visibility='hidden';hide()});
});
document.querySelectorAll('[data-tip]').forEach(el=>{
 const lines=()=>el.dataset.tip.split('\\n');
 el.addEventListener('pointermove',e=>show(lines(),e.clientX,e.clientY));el.addEventListener('pointerleave',hide);
 el.addEventListener('focus',()=>{const r=el.getBoundingClientRect();show(lines(),r.right,r.top)});el.addEventListener('blur',hide);
});
})();
"""


def safe_url(url: str | None) -> str | None:
    """Only http(s) links from feeds become clickable (blocks javascript:/data: URLs)."""
    if url and url.strip().lower().startswith(("http://", "https://")):
        return url.strip()
    return None


NAV = [("index.html", "總覽"), ("consensus.html", "大神共同持股"), ("clone.html", "跟單回測"),
       ("insiders.html", "內部人群買"), ("retail.html", "散戶 vs 大神")]


def _nav(root: str, active: str) -> str:
    links = "".join(f'<a href="{root}{href}"{" class=on" if href == active else ""}>{esc(label)}</a>'
                    for href, label in NAV)
    return f'<nav class="top"><a class="brand" href="{root}index.html">Smartmoney Radar</a>{links}</nav>'


def _page(title: str, body: str, root: str, active: str = "") -> str:
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="{root}assets/style.css"></head>'
            f'<body><main>{_nav(root, active)}{body}<footer>Smartmoney Radar · 資料來自公開貼文、新聞與 SEC 申報 · '
            f'僅供研究參考，不構成投資建議 · 產生於 {datetime.now():%Y-%m-%d %H:%M}</footer></main>'
            f'<script src="{root}assets/app.js"></script></body></html>')


def _badge(stance: str, text: str | None = None) -> str:
    icon, label, cls = STANCE[stance]
    return f'<span class="badge {cls}"><span class="i">{icon}</span>{esc(text or label)}</span>'


def _labels(buckets: Sequence[date], step: int, end: date | None = None) -> list[str]:
    if step == 1:
        return [f"{b:%m/%d}" for b in buckets]
    end = end or date.max
    return [f"{b:%m/%d}–{min(b + timedelta(days=step - 1), end):%m/%d}" for b in buckets]


def line_chart(labels: list[str], values: list[float], vmax: float, height: int = 64,
               unit: str = " 則", vmin: float = 0) -> str:
    """Single-series area/line with a crosshair; y scale shared via ``vmax``."""
    n = len(values)
    vmax = max(vmax, max(values, default=0), vmin + 1e-9, 1 if vmin == 0 else vmin)
    span = (vmax - vmin) or 1
    xs = [i / (n - 1) * 100 if n > 1 else 50 for i in range(n)]
    ys = [height - (v - vmin) / span * (height - 4) for v in values]
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))
    area = f"0,{height} {pts} 100,{height}"
    return (f'<div class="chart" data-labels="{esc(json.dumps(labels, ensure_ascii=False))}" '
            f'data-values="{esc(json.dumps(values))}" data-unit="{esc(unit)}">'
            f'<svg viewBox="0 0 100 {height}" preserveAspectRatio="none" height="{height}" '
            f'role="img" aria-label="{esc(f"走勢，最高 {max(values, default=0)}{unit}")}">'
            f'<line class="gridline" x1="0" x2="100" y1="{height / 2}" y2="{height / 2}" vector-effect="non-scaling-stroke"/>'
            f'<polygon class="area" points="{area}"/><polyline class="line" points="{pts}"/>'
            f'<line class="baseline" x1="0" x2="100" y1="{height}" y2="{height}" vector-effect="non-scaling-stroke"/>'
            f'<line class="cross" x1="0" x2="0" y1="0" y2="{height}"/></svg>'
            f'<div class="axis"><span>{esc(labels[0])}</span><span>{esc(labels[-1])}</span></div></div>')


def bar_chart(labels: list[str], values: list[int], height: int = 120, unit: str = " 則") -> str:
    n = len(values) or 1
    vmax = max(max(values, default=0), 1)
    slot = 100 / n
    gap = min(0.6, slot * 0.2)
    bars = []
    for i, (lab, v) in enumerate(zip(labels, values)):
        h = v / vmax * (height - 6)
        x = i * slot + gap / 2
        bars.append(f'<rect class="bar" tabindex="0" x="{x:.2f}" y="{height - h:.2f}" '
                    f'width="{slot - gap:.2f}" height="{h:.2f}" rx="0.6" '
                    f'data-tip="{esc(lab)}&#10;{v}{esc(unit)}"><title>{esc(lab)} {v}{esc(unit)}</title></rect>')
    return (f'<div class="chart"><svg viewBox="0 0 100 {height}" preserveAspectRatio="none" height="{height}" '
            f'role="img" aria-label="每期發文數">'
            f'<line class="gridline" x1="0" x2="100" y1="{height / 2}" y2="{height / 2}" vector-effect="non-scaling-stroke"/>'
            f'{"".join(bars)}<line class="baseline" x1="0" x2="100" y1="{height}" y2="{height}" '
            f'vector-effect="non-scaling-stroke"/></svg>'
            f'<div class="axis"><span>{esc(labels[0]) if labels else ""}</span>'
            f'<span>最高 {vmax}{esc(unit)}</span><span>{esc(labels[-1]) if labels else ""}</span></div></div>')


def _feed_html(rows: list[tuple[Post, list[Signal]]], names: dict[str, str], root: str,
               limit: int = 40, link_investor: bool = True) -> str:
    if not rows:
        return '<p class="empty">這段期間沒有發文。</p>'
    items = []
    for post, sigs in rows[:limit]:
        who = esc(names.get(post.investor_key, post.investor_key))
        if link_investor:
            who = f'<a href="{root}investor/{esc(post.investor_key)}.html">{who}</a>'
        src = ("本人發言" if post.kind == "own" else
               "報導·引述本人" if any(s.attributed for s in sigs) else "相關報導")
        url = safe_url(post.url)
        link = f' · <a href="{esc(url)}" rel="noopener" target="_blank">原文</a>' if url else ""
        badges = " ".join(_badge(s.stance.value, signal_label(s.ticker, s.sector)) for s in sigs[:6])
        text = post.text if len(post.text) <= 400 else post.text[:399] + "…"
        items.append(f'<li><div class="sub"><span class="who">{who}</span> · {post.published} · '
                     f'{esc(src)}·{esc(post.source)}{link}</div><div class="txt">{esc(text)}</div>{badges}</li>')
    more = f'<p class="sub">只顯示最新 {limit} 則，共 {len(rows)} 則。</p>' if len(rows) > limit else ""
    return f'<ul class="feed">{"".join(items)}</ul>{more}'


def _mood(sentiment: float) -> str:
    if sentiment > 0.2:
        return _badge("bullish", f"偏多 {sentiment:+.2f}")
    if sentiment < -0.2:
        return _badge("bearish", f"偏空 {sentiment:+.2f}")
    return _badge("neutral", f"中性 {sentiment:+.2f}")


def _tick(t: str | None, root: str) -> str:
    if not t:
        return "—"
    return f'<a href="{root}ticker/{esc(_slug(t))}.html"><strong>{esc(t)}</strong></a>'


def _slug(t: str) -> str:
    return "".join(c if c.isalnum() or c in ".-" else "_" for c in t.upper())


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def trades_table(trades: list[Trade], names: dict[str, str], root: str, show_who: bool = True,
                 limit: int = 30) -> str:
    if not trades:
        return '<p class="empty">這段期間沒有新的申報交易。</p>'
    rows = []
    for t in trades[:limit]:
        icon, label, cls = SIDE.get(t.side, ("●", t.side, "neutral"))
        size = " · ".join(x for x in (f"{t.shares:,.0f} 股" if t.shares and "股" not in t.amount_text else "",
                                      t.amount_text) if x)
        if t.value_usd:
            size += f" · ${t.value_usd / 1e6:,.1f}M"
        form = FORM_LABEL.get(t.form, t.form)
        url = safe_url(t.url)
        src = f'<a href="{esc(url)}" rel="noopener" target="_blank">{esc(form)}</a>' if url else esc(form)
        who = f'<td><a href="{root}investor/{esc(t.investor_key)}.html">{esc(names.get(t.investor_key, t.investor_key))}</a></td>' if show_who else ""
        rows.append(f'<tr><td>{t.traded}</td>{who}<td><span class="badge {cls}"><span class="i">{icon}</span>{label}</span></td>'
                    f'<td>{_tick(t.ticker, root)}<div class="sub">{esc(t.issuer[:60])}</div></td>'
                    f'<td>{esc(size)}</td><td>{src}<div class="sub">申報 {t.filed}</div></td></tr>')
    head = "<th>交易日</th>" + ("<th>誰</th>" if show_who else "") + "<th>動作</th><th>標的</th><th>規模</th><th>來源</th>"
    return f'<div class="tablewrap"><table><tr>{head}</tr>{"".join(rows)}</table></div>'


def scoreboard_table(calls: list[CallResult], names: dict[str, str],
                     horizons: tuple[int, ...] = (30, 90)) -> str:
    cards = {h: scorecards(calls, h) for h in horizons}
    investors = sorted({k for c in cards.values() for k in c},
                       key=lambda k: (-(cards[horizons[-1]][k].hit_rate or -1),
                                      -(cards[horizons[0]][k].hit_rate or -1)))
    if not investors:
        return '<p class="empty">還沒有可評分的喊單（需要本人發言＋股價資料）。</p>'

    def cell(c: Scorecard) -> str:
        if not c.scored:
            return '<td class="num">—</td><td class="num">—</td>'
        return (f'<td class="num">{c.hit_rate * 100:.0f}%<div class="sub">{c.hits}/{c.scored}</div></td>'
                f'<td class="num">{_pct(c.avg_return)}</td>')
    rows = "".join(
        f'<tr><td><a href="investor/{esc(k)}.html">{esc(names.get(k, k))}</a></td>'
        f'<td class="num">{cards[horizons[0]][k].calls}</td>'
        + "".join(cell(cards[h][k]) for h in horizons) + "</tr>" for k in investors)
    head = "".join(f'<th class="num">{h} 天勝率</th><th class="num">{h} 天平均報酬</th>' for h in horizons)
    return (f'<div class="tablewrap"><table><tr><th>大神</th><th class="num">喊單數</th>{head}</tr>'
            f'{rows}</table></div>'
            f'<p class="sub">「喊單」＝本人發言中明確看多/看空某檔個股；看多後上漲、看空後下跌算對。'
            f'報酬以發言當日收盤為基準並依方向調整（看空時下跌記為正），未計股利與交易成本；'
            f'發言未滿該天數的不計入。</p>')


def calls_table(results: list[CallResult], root: str, horizons=(30, 90)) -> str:
    if not results:
        return '<p class="empty">還沒有可評分的喊單。</p>'
    rows = []
    for r in sorted(results, key=lambda r: r.signal.post.published, reverse=True)[:40]:
        cells = ""
        for h in horizons:
            hit = r.hit(h)
            mark = "" if hit is None else (" ✓" if hit else " ✗")
            cells += f'<td class="num">{_pct(r.returns.get(h))}{mark}</td>'
        rows.append(f'<tr><td>{r.signal.post.published}</td><td>{_tick(r.signal.ticker, root)}</td>'
                    f'<td>{_badge(r.signal.stance.value)}</td><td class="num">{r.entry:,.2f}</td>{cells}</tr>')
    hh = "".join(f'<th class="num">{h} 天後</th>' for h in horizons)
    return (f'<div class="tablewrap"><table><tr><th>日期</th><th>標的</th><th>喊單</th>'
            f'<th class="num">當日價</th>{hh}</tr>{"".join(rows)}</table></div>')


def tradecheck_table(items: list[TradeCheck], root: str) -> str:
    if not items:
        return '<p class="empty">發言前後 90 天內沒有對應的申報交易。</p>'
    rows = "".join(
        f'<tr><td>{it.signal.post.published}</td><td>{_tick(it.signal.ticker, root)}</td>'
        f'<td>{_badge(it.signal.stance.value)}</td>'
        f'<td>{SIDE.get(it.trade.side, ("", it.trade.side, ""))[1]} {it.trade.traded}'
        f'<div class="sub">發言{"後" if it.days_after >= 0 else "前"} {abs(it.days_after)} 天</div></td>'
        f'<td><span class="badge {VERDICT[it.verdict][2]}"><span class="i">{VERDICT[it.verdict][0]}</span>'
        f'{VERDICT[it.verdict][1]}</span></td></tr>' for it in items)
    return (f'<div class="tablewrap"><table><tr><th>發言日</th><th>標的</th><th>公開看法</th>'
            f'<th>實際交易</th><th>判定</th></tr>{rows}</table></div>')


def health_table(statuses) -> str:
    if not statuses:
        return '<p class="empty">尚無抓取紀錄。</p>'
    icon = {"ok": ("✓", "正常", "bull"), "warn": ("!", "部分失敗", "neutral"), "fail": ("✕", "異常", "bear")}
    rows = []
    for h in sorted(statuses, key=lambda h: ({"fail": 0, "warn": 1, "ok": 2}[h.level], h.name)):
        i, label, cls = icon[h.level]
        last_ok = f"{h.last_ok:%m/%d %H:%M}" if h.last_ok else "從未成功"
        err = f'<div class="sub">{esc(h.last_error[:140])}</div>' if h.last_error else ""
        rows.append(f'<tr><td>{esc(h.name)}</td><td><span class="badge {cls}"><span class="i">{i}</span>{label}</span></td>'
                    f'<td>{last_ok}</td><td class="num">{h.ok_now}/{h.ok_now + h.failing_now}</td>'
                    f'<td class="num">{h.items_7d}</td><td>{err}</td></tr>')
    return ('<div class="tablewrap"><table><tr><th>來源</th><th>狀態</th><th>最後成功</th>'
            '<th class="num">最近一次</th><th class="num">7 天筆數</th><th>錯誤</th></tr>'
            + "".join(rows) + '</table></div>')


def holdings_from_store(store: Store, inv: Investor):
    """(quarter, changes, 13F say/do checks, quarter summaries) from stored 13F history."""
    from .consistency import check
    from .holdings import diff_holdings, summarize_quarter
    quarters = store.quarters_13f(inv.key)
    if len(quarters) < 2:
        return None
    loaded = {q: store.load_13f(inv.key, q) for q in quarters}
    curr, curr_t = loaded[quarters[0]]
    prev, prev_t = loaded[quarters[1]]
    tickers = {**prev_t, **curr_t}
    resolve = lambda h: tickers.get(h.key)  # noqa: E731
    changes = diff_holdings(prev, curr, resolve)
    q_end = date.fromisoformat(quarters[0])
    q_start = date(q_end.year, q_end.month - 2, 1)
    sigs = [s for p in store.posts_between(q_start, q_end, inv.key, kinds=("own", "news"))
            for s in store.signals_for(p) if s.is_own_view]
    history = [summarize_quarter(q, loaded[q][0], lambda h, t=loaded[q][1]: t.get(h.key))
               for q in quarters]
    return quarters[0], changes, check(sigs, changes), history


def history_html(history) -> str:
    if not history:
        return ""
    vmax = max(h.total_value for h in history) or 1
    rows = "".join(
        f'<tr><td>{esc(h.quarter)}</td><td class="num">${h.total_value / 1e9:,.2f}B'
        f'<div class="track" style="height:6px;margin-top:4px"><div style="height:6px;border-radius:0 3px 3px 0;'
        f'background:var(--series);width:{h.total_value / vmax * 100:.1f}%"></div></div></td>'
        f'<td class="num">{h.positions}</td><td>'
        + "、".join(f"{esc(label)} {w * 100:.1f}%" for label, w in h.top) + "</td></tr>"
        for h in history)
    return ('<h3>各季持倉概況</h3><div class="tablewrap"><table><tr><th>季度</th>'
            '<th class="num">13F 申報市值</th><th class="num">持股數</th><th>前五大持股</th></tr>'
            f'{rows}</table></div>')


def _who(keys, names: dict[str, str], extra: dict[str, str] | None = None) -> str:
    label = {"NEW": "新建倉", "ADD": "加碼", "TRIM": "減碼", "EXIT": "清倉"}
    return "、".join(
        f'<a href="investor/{esc(k)}.html">{esc(names.get(k, k))}</a>'
        + (f'<span class="sub">（{label.get(extra[k], extra[k])}）</span>' if extra and k in extra else "")
        for k in sorted(keys, key=lambda k: names.get(k, k)))


def _ticker_13f(consensus, ticker: str, names: dict[str, str]) -> str:
    row = consensus.rows.get(ticker) if consensus else None
    if not row or not (row.owners or row.sold):
        return ""
    label = {"NEW": "新建倉", "ADD": "加碼", "TRIM": "減碼", "EXIT": "清倉"}
    keys = sorted(set(row.owners) | set(row.sold), key=lambda k: -row.owners.get(k, 0))
    trs = "".join(
        f'<tr><td><a href="../investor/{esc(k)}.html">{esc(names.get(k, k))}</a></td>'
        f'<td class="num">{row.owners.get(k, 0) * 100:.2f}%</td>'
        f'<td>{esc(label.get(row.bought.get(k) or row.sold.get(k) or "", "不變"))}</td>'
        f'<td>{esc(consensus.quarters.get(k, ""))}</td></tr>' for k in keys)
    return ('<h2>13F：哪些大神持有</h2><div class="tablewrap"><table><tr><th>大神</th>'
            '<th class="num">占其組合</th><th>最新一季動作</th><th>季度</th></tr>' + trs + '</table></div>')


def clone_periods_table(res, root: str) -> str:
    rows = "".join(
        f'<tr><td>{esc(p.quarter)}</td><td>{p.entry} → {p.exit}</td>'
        f'<td class="num">{_pct(p.ret)}</td><td class="num">{_pct(p.bench)}</td>'
        f'<td class="num">{p.coverage * 100:.0f}%</td><td>'
        + "、".join(f"{_tick(t, root)} {_pct(r)}" for t, _, r in sorted(p.holdings, key=lambda x: -x[1])[:5])
        + "</td></tr>" for p in reversed(res.periods))
    return ('<div class="tablewrap"><table><tr><th>13F 季度</th><th>持有期間（公布日起）</th>'
            '<th class="num">跟單報酬</th><th class="num">SPY</th><th class="num">有股價的權重</th>'
            '<th>主要持股表現</th></tr>' + rows + '</table></div>')


def render_clone(results, names: dict[str, str], has_prices: bool) -> str:
    note = ('<p class="sub">每季在 13F <strong>公布當天</strong>買進該大神前 10 大持股（依其權重），持有到下一季公布日；'
            '最新一季持有到今天。未計股利、交易成本與稅；沒有代號或股價的持股會略過並重新分配權重。'
            '過去績效不代表未來。</p>')
    if not results:
        why = ("還沒有 13F 歷史，執行 <code>spiderhead dashboard --with-13f</code>。" if has_prices
               else "需要股價資料（不要加 <code>--no-prices</code>）。")
        return _page("跟單回測 · Smartmoney Radar", f"<h1>跟單回測</h1>{note}<p class=empty>{why}</p>",
                     "", "clone.html")
    rows = "".join(
        f'<tr><td><a href="investor/{esc(r.investor)}.html">{esc(names.get(r.investor, r.investor))}</a></td>'
        f'<td class="num">{len(r.periods)}</td><td class="num">{_pct(r.total)}</td>'
        f'<td class="num">{_pct(r.bench_total)}</td><td class="num">{_pct(r.excess)}</td>'
        f'<td class="num">{"—" if r.beat_rate is None else f"{r.beat_rate * 100:.0f}%"}</td></tr>'
        for r in results)
    board = ('<div class="tablewrap"><table><tr><th>大神</th><th class="num">季數</th>'
             '<th class="num">跟單累積報酬</th><th class="num">同期 SPY</th><th class="num">超額報酬</th>'
             '<th class="num">贏過 SPY 的季度</th></tr>' + rows + '</table></div>')
    details = "".join(f'<h2>{esc(names.get(r.investor, r.investor))}</h2>{clone_periods_table(r, "")}'
                      for r in results)
    return _page("跟單回測 · Smartmoney Radar", f"<h1>跟單回測</h1>{note}{board}{details}", "", "clone.html")


def render_insiders(store: Store, window_days: int = 14, min_insiders: int = 3) -> str:
    from .insiders import find_clusters
    end = date.today()
    start = end - timedelta(days=window_days - 1)
    clusters = find_clusters(store.insider_buys_between(start, end), min_insiders)
    note = (f'<p class="sub">最近 {window_days} 天內，同一家公司有 {min_insiders} 位以上不同的內部人（董事、高管、'
            f'10% 大股東）在公開市場用自己的錢買進，且合計金額超過 10 萬美元。資料來自 SEC 每日 Form 4。</p>')
    if not clusters:
        empty = ('<p class="empty">目前沒有符合的群買。執行 <code>spiderhead insiders</code> 掃描 SEC 每日申報。</p>')
        return _page("內部人群買 · Smartmoney Radar", f"<h1>內部人群買</h1>{note}{empty}", "", "insiders.html")
    blocks = []
    for c in clusters:
        rows = "".join(
            f'<tr><td>{b.traded}</td><td>{esc(b.owner)}</td><td>{esc(b.title or "—")}</td>'
            f'<td class="num">{b.shares:,.0f}</td><td class="num">${b.price:,.2f}</td>'
            f'<td class="num">${b.value / 1e3:,.0f}K</td>'
            f'<td><a href="{esc(b.url)}" rel="noopener" target="_blank">Form 4</a></td></tr>'
            for b in sorted(c.buys, key=lambda b: b.traded, reverse=True))
        blocks.append(f'<h2>{_tick(c.ticker, "")} <span class="sub">{esc(c.issuer)} · {len(c.insiders)} 位內部人 · '
                      f'合計 ${c.value / 1e6:,.2f}M</span></h2><div class="tablewrap"><table><tr><th>交易日</th>'
                      f'<th>內部人</th><th>職位</th><th class="num">股數</th><th class="num">價格</th>'
                      f'<th class="num">金額</th><th>原始申報</th></tr>{rows}</table></div>')
    return _page("內部人群買 · Smartmoney Radar", f"<h1>內部人群買</h1>{note}{''.join(blocks)}", "", "insiders.html")


def render_retail(store: Store, names: dict[str, str], days: int = 7) -> str:
    from .retail import retail_vs_gurus
    end = date.today()
    rows = retail_vs_gurus(store, end - timedelta(days=days - 1), end)
    note = (f'<p class="sub">最近 {days} 天 Reddit（r/wallstreetbets、r/stocks、r/investing）最多人討論的股票，'
            f'對照大神本人的看法。「看法相反」= 散戶明顯偏多/偏空，而大神站在另一邊。</p>')
    if not rows:
        return _page("散戶 vs 大神 · Smartmoney Radar",
                     f'<h1>散戶 vs 大神</h1>{note}<p class="empty">還沒有散戶資料，執行 <code>spiderhead fetch</code>。</p>',
                     "", "retail.html")
    trs = "".join(
        f'<tr><td>{_tick(r.ticker, "")}</td><td class="num">{r.mentions}</td><td>{_mood(r.retail_mood)}</td>'
        f'<td>{" ".join(_badge(v, names.get(k, k)) for k, v in sorted(r.guru_views.items())) or "—"}</td>'
        f'<td>{_badge("bearish", "看法相反") if r.divergent else ""}</td></tr>' for r in rows)
    table = ('<div class="tablewrap"><table><tr><th>標的</th><th class="num">散戶討論數</th><th>散戶情緒</th>'
             '<th>大神看法</th><th></th></tr>' + trs + '</table></div>')
    return _page("散戶 vs 大神 · Smartmoney Radar", f"<h1>散戶 vs 大神</h1>{note}{table}", "", "retail.html")


def render_consensus(c, names: dict[str, str]) -> str:
    if not c.quarters:
        body = ('<h1>大神共同持股</h1><p class="empty">還沒有 13F 歷史資料。執行 '
                '<code>spiderhead dashboard --with-13f</code> 下載最近 4 季後就會出現。</p>')
        return _page("大神共同持股 · Smartmoney Radar", body, "", "consensus.html")

    def table(rows, cols) -> str:
        if not rows:
            return '<p class="empty">沒有資料。</p>'
        head = "".join(f'<th{" class=num" if n else ""}>{esc(h)}</th>' for h, n, _ in cols)
        body = "".join("<tr>" + "".join(f'<td{" class=num" if n else ""}>{f(r)}</td>' for _, n, f in cols)
                       + "</tr>" for r in rows)
        return f'<div class="tablewrap"><table><tr>{head}</tr>{body}</table></div>'

    tick = lambda r: (_tick(r.key, "") if r.key.isupper() and len(r.key) <= 6  # noqa: E731
                      else f"<strong>{esc(r.issuer[:40])}</strong>")
    owned = table(c.most_owned(), [
        ("標的", False, tick), ("持有人數", True, lambda r: str(len(r.owners))),
        ("平均占組合", True, lambda r: f"{r.avg_weight * 100:.1f}%"),
        ("合計市值", True, lambda r: f"${r.value / 1e9:,.2f}B"),
        ("誰持有", False, lambda r: _who(r.owners, names))])
    bought = table(c.most_bought(), [
        ("標的", False, tick), ("買進人數", True, lambda r: str(len(r.bought))),
        ("誰買", False, lambda r: _who(r.bought, names, r.bought)),
        ("同時有人賣", False, lambda r: _who(r.sold, names, r.sold) or "—")])
    sold = table(c.most_sold(), [
        ("標的", False, tick), ("賣出人數", True, lambda r: str(len(r.sold))),
        ("誰賣", False, lambda r: _who(r.sold, names, r.sold)),
        ("同時有人買", False, lambda r: _who(r.bought, names, r.bought) or "—")])
    qs = "、".join(f"{names.get(k, k)} {q}" for k, q in sorted(c.quarters.items()))
    body = (f'<h1>大神共同持股</h1><p class="sub">依各人最新一季 13F（{esc(qs)}）。選擇權部位不計入。'
            f'13F 有最多 135 天落後，請搭配「即時對帳單」看最新動作。</p>'
            f'<h2>最多大神持有</h2>{owned}<h2>本季最多大神買進（新建倉或加碼）</h2>{bought}'
            f'<h2>本季最多大神賣出（減碼或清倉）</h2>{sold}')
    return _page("大神共同持股 · Smartmoney Radar", body, "", "consensus.html")


def render_ticker(store: Store, ticker: str, names: dict[str, str], days: int,
                  prices: PriceBook | None, consensus=None) -> str:
    start, end = window(days)
    sigs = [s for s in store.signals_between(start, end) if s.ticker == ticker]
    trades = store.trades_between(start - timedelta(days=90), end, ticker=ticker)
    rows = sorted(sigs, key=lambda s: s.post.published, reverse=True)
    talk = "".join(
        f'<tr><td>{s.post.published}</td><td><a href="../investor/{esc(s.post.investor_key)}.html">'
        f'{esc(names.get(s.post.investor_key, s.post.investor_key))}</a></td><td>{_badge(s.stance.value)}</td>'
        f'<td>{esc(s.rationale[:120])}</td></tr>' for s in rows[:50])
    talk = (f'<div class="tablewrap"><table><tr><th>日期</th><th>誰</th><th>看法</th><th>依據</th></tr>{talk}</table></div>'
            if rows else '<p class="empty">期間內沒有大神提到。</p>')
    chart = ""
    if prices is not None:
        series = [(d, c) for d, c in prices.series(ticker) if start <= d <= end]
        if len(series) > 1:
            chart = (f'<h2>股價（收盤）</h2><div class="card">'
                     + line_chart([f"{d:%m/%d}" for d, _ in series], [round(c, 2) for _, c in series],
                                  0, height=120, unit=" 美元", vmin=min(c for _, c in series)) + "</div>")
    bulls = sum(1 for s in rows if s.stance.value == "bullish")
    bears = sum(1 for s in rows if s.stance.value == "bearish")
    body = (f'<p class="sub"><a href="../index.html">← 總覽</a></p><header class="top"><h1>{esc(ticker)}</h1>'
            f'<span class="sub">{days} 天內被提到 {len(rows)} 次 · ▲ {bulls} / ▼ {bears}</span></header>'
            f'{chart}<h2>誰在談</h2>{talk}<h2>大神的實際交易（對帳單）</h2>'
            f'{trades_table(trades, names, "../")}'
            f'{_ticker_13f(consensus, ticker, names)}')
    return _page(f"{ticker} · Smartmoney Radar", body, "../")


def render_index(store: Store, investors: dict[str, Investor], days: int, step: int,
                 recent_days: int = 7, calls: list[CallResult] | None = None) -> str:
    start, end = window(days)
    buckets, series = sector_series(store.signals_between(start, end), start, end, step)
    labels = _labels(buckets, step, end)
    recent = build_digest(store, investors, recent_days)
    names = recent.names
    trend_by = {t.sector: t for t in recent.trends}
    totals = {s: sum(v) for s, v in series.items()}
    active = [s for s in SECTORS if totals.get(s)]
    active.sort(key=lambda s: -totals[s])
    vmax = max((max(series[s]) for s in active), default=1)

    hottest = SECTORS.get(recent.trends[0].sector, "—") if recent.trends else "—"
    tiles = (f'<div class="tiles">'
             f'<div class="tile"><div class="v">{len(recent.feed)}</div><div class="l">近 {recent_days} 天發文 / 報導</div></div>'
             f'<div class="tile"><div class="v">{len({p.investor_key for p, _ in recent.feed})}</div>'
             f'<div class="l">近 {recent_days} 天活躍大神（共 {len(investors)} 位）</div></div>'
             f'<div class="tile"><div class="v">{esc(hottest)}</div><div class="l">近 {recent_days} 天最熱產業</div></div>'
             f'<div class="tile"><div class="v">{len(recent.consensus)}</div><div class="l">多位大神同時在談的標的</div></div></div>')

    cards = []
    for s in active:
        t = trend_by.get(s)
        chg = ""
        if t is not None:
            chg = "新出現" if t.change_pct is None else f"較前期 {t.change_pct:+.0f}%"
        who = "、".join(sorted(names.get(k, k) for k in t.investors)) if t else ""
        cards.append(f'<div class="card"><h3>{esc(SECTORS[s])}</h3>'
                     f'<div class="meta"><span>{days} 天共 {totals[s]} 則</span>'
                     f'<span>{_mood(t.sentiment) if t else ""}</span></div>'
                     f'{line_chart(labels, series[s], vmax)}'
                     f'<div class="sub">近 {recent_days} 天：{esc(chg) or "無"}{" · " + esc(who) if who else ""}</div></div>')
    heat = (f'<div class="grid">{"".join(cards)}</div>' if cards
            else '<p class="empty">還沒有資料，先執行 <code>spiderhead fetch</code>。</p>')
    table_rows = "".join(f'<tr><td>{esc(lab)}</td>' + "".join(
        f'<td class="num">{series[s][i]}</td>' for s in active) + "</tr>" for i, lab in enumerate(labels))
    heat_table = (f'<details><summary>表格檢視</summary><div class="tablewrap"><table><tr><th>期間</th>'
                  + "".join(f'<th class="num">{esc(SECTORS[s])}</th>' for s in active)
                  + f'</tr>{table_rows}</table></div></details>') if active else ""

    if recent.consensus:
        crow = "".join(
            f'<tr><td><strong>{esc(c.ticker)}</strong></td><td class="num">{c.investors}</td><td>'
            + " ".join(_badge(st.value, names.get(k, k)) for k, st in sorted(c.views.items()))
            + "</td></tr>" for c in recent.consensus)
        cons = (f'<div class="tablewrap"><table><tr><th>標的</th><th class="num">人數</th>'
                f'<th>誰、怎麼看</th></tr>{crow}</table></div>')
    else:
        cons = '<p class="empty">近期沒有兩位以上大神同時提到的個股。</p>'

    inv_cards = []
    counts = {k: 0 for k in investors}
    for p, _ in recent.feed:
        counts[p.investor_key] = counts.get(p.investor_key, 0) + 1
    for inv in sorted(investors.values(), key=lambda i: -counts.get(i.key, 0)):
        inv_cards.append(
            f'<a class="card" style="text-decoration:none;color:inherit" href="investor/{esc(inv.key)}.html">'
            f'<h3>{esc(inv.name)}</h3><div class="meta"><span>{esc(inv.fund)}</span></div>'
            f'<span class="badge">對帳單：{esc(PROOF.get(inv.proof, inv.proof))}</span> '
            f'<span class="badge">近 {recent_days} 天 {counts.get(inv.key, 0)} 則</span>'
            f'<p class="note">{esc(inv.note)}</p></a>')

    body = (f'<header class="top"><h1>Smartmoney Radar 大神追蹤</h1>'
            f'<span class="sub">資料期間 {start} ～ {end}</span></header>{tiles}'
            f'<h2>產業熱度變化（每{"天" if step == 1 else f" {step} 天"}被提及的則數）</h2>{heat}{heat_table}'
            f'<h2>多位大神同時在談（近 {recent_days} 天）</h2>{cons}'
            f'<h2>最新對帳單（近 30 天申報的實際交易）</h2>'
            f'{trades_table(store.trades_between(end - timedelta(days=29), end, by="filed"), names, "", limit=20)}'
            f'<h2>喊單勝率排行</h2>{scoreboard_table(calls or [], names)}'
            f'<h2>大神</h2><div class="grid">{"".join(inv_cards)}</div>'
            f'<h2>最新動態</h2>{_feed_html(recent.feed, names, "")}'
            f'<h2>資料來源狀態</h2>{health_table(store.source_health())}')
    return _page("Smartmoney Radar 大神追蹤", body, "", "index.html")


def render_investor(store: Store, inv: Investor, names: dict[str, str], days: int, step: int,
                    holdings: tuple[str, list[PositionChange], list[ConsistencyItem]] | None = None,
                    calls: list[CallResult] | None = None, clone=None) -> str:
    start, end = window(days)
    prof = investor_profile(store, inv, start, end, step)
    buckets, activity = prof.activity
    own = sum(1 for p, _ in prof.posts if p.kind == "own")

    links = []
    if inv.handles.get("x"):
        links.append(f'<a href="https://x.com/{esc(inv.handles["x"])}" rel="noopener" target="_blank">X @{esc(inv.handles["x"])}</a>')
    if inv.cik:
        links.append(f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK={esc(inv.cik)}'
                     f'&amp;type={"13F-HR" if inv.files_13f else "8-K"}" rel="noopener" target="_blank">SEC 申報</a>')

    top_sec = prof.sectors.most_common(8)
    smax = max((n for _, n in top_sec), default=1)
    sec_html = "".join(
        f'<div class="hbar"><span>{esc(SECTORS.get(s, s))}</span><span class="track">'
        f'<span class="fill" style="display:block;width:{n / smax * 100:.1f}%" data-tip="{esc(SECTORS.get(s, s))}&#10;{n} 則"></span>'
        f'</span><span class="num">{n}</span></div>' for s, n in top_sec) or '<p class="empty">尚無資料。</p>'

    tick_rows = sorted(prof.tickers.items(), key=lambda kv: -sum(kv[1].values()))
    tick_html = ("".join(
        f'<tr><td>{_tick(t, "../")}</td><td class="num">{c["bullish"]}</td>'
        f'<td class="num">{c["bearish"]}</td><td class="num">{c["neutral"]}</td></tr>' for t, c in tick_rows[:20]))
    tick_html = (f'<div class="tablewrap"><table><tr><th>標的</th><th class="num">▲ 看多</th>'
                 f'<th class="num">▼ 看空</th><th class="num">● 中性</th></tr>{tick_html}</table></div>'
                 if tick_rows else '<p class="empty">這段期間沒有提到個股。</p>')

    if holdings is None:
        holdings = holdings_from_store(store, inv)
    if holdings:
        quarter, changes, items = holdings[:3]
        history = holdings[3] if len(holdings) > 3 else []
        moves = [c for c in changes if c.change is not ChangeType.HOLD][:15]
        hrows = "".join(
            f'<tr><td>{esc(CHANGE[c.change])}</td><td>{esc(c.ticker or c.issuer)}'
            f'{" (" + esc(c.put_call) + ")" if c.put_call else ""}</td>'
            f'<td class="num">{c.prev_shares:,} → {c.curr_shares:,}</td>'
            f'<td class="num">{c.portfolio_weight * 100:.2f}%</td></tr>' for c in moves)
        h13 = history_html(history)
        h13 += (f'<h3 style="margin-top:16px">最新一季變動（{esc(quarter)}，依交易金額排序）</h3>'
                f'<div class="tablewrap"><table>'
                f'<tr><th>動作</th><th>標的</th><th class="num">股數</th><th class="num">占組合</th></tr>'
                f'{hrows}</table></div>')
        if items:
            crow = "".join(
                f'<tr><td>{it.signal.post.published}</td><td>{esc(it.signal.ticker)}</td>'
                f'<td>{_badge(it.signal.stance.value)}</td>'
                f'<td>{esc(CHANGE[it.change.change]) if it.change else "—"}</td>'
                f'<td><span class="badge {VERDICT[it.verdict][2]}"><span class="i">{VERDICT[it.verdict][0]}</span>'
                f'{VERDICT[it.verdict][1]}</span></td></tr>' for it in items)
            h13 += (f'<h3 style="margin-top:16px">言行一致檢查</h3><div class="tablewrap"><table><tr><th>日期</th>'
                    f'<th>標的</th><th>公開看法</th><th>13F 動作</th><th>判定</th></tr>{crow}</table></div>')
    elif inv.files_13f:
        h13 = '<p class="empty">尚未載入。執行 <code>spiderhead dashboard --with-13f</code> 取得最新 13F。</p>'
    else:
        h13 = f'<p class="empty">此人以 {esc(PROOF.get(inv.proof, inv.proof))} 驗證，尚未支援自動比對。</p>'

    clone_html = ""
    if clone is not None and clone.periods:
        clone_html = (f'<h2>跟單回測</h2><p class="sub">照 13F 公布日跟買前 10 大持股：累積 {_pct(clone.total)}，'
                      f'同期 SPY {_pct(clone.bench_total)}。<a href="../clone.html">說明與排行</a></p>'
                      f'{clone_periods_table(clone, "../")}')
    trades = store.trades_between(start - timedelta(days=90), end, inv.key)
    trades_html = trades_table(trades, names, "../", show_who=False)
    own_sigs = [sg for p, sigs in prof.posts for sg in sigs if sg.is_own_view]
    checks = check_trades(own_sigs, trades)
    my_calls = [c for c in (calls or []) if c.signal.post.investor_key == inv.key]

    body = (f'<p class="sub"><a href="../index.html">← 全部大神</a></p>'
            f'<header class="top"><h1>{esc(inv.name)}</h1><span class="sub">'
            f'{" · ".join([esc(inv.fund), *links])}</span></header>'
            f'<p class="note">{esc(inv.note)}</p>'
            f'<div class="tiles"><div class="tile"><div class="v">{len(prof.posts)}</div><div class="l">{days} 天發文 / 報導</div></div>'
            f'<div class="tile"><div class="v">{own}</div><div class="l">本人發言</div></div>'
            f'<div class="tile"><div class="v">{len(prof.tickers)}</div><div class="l">提到的個股</div></div>'
            f'<div class="tile"><div class="v">{esc(PROOF.get(inv.proof, inv.proof))}</div><div class="l">對帳單</div></div></div>'
            f'<h2>發文頻率</h2><div class="card">{bar_chart(_labels(buckets, step, end), activity)}</div>'
            f'<h2>最常談的產業</h2><div class="card">{sec_html}</div>'
            f'<h2>提到的個股</h2>{tick_html}'
            f'<h2>即時對帳單（Form 4 / 13D / 國會申報）</h2>{trades_html}'
            f'<h2>言行一致：發言 vs 實際交易</h2>{tradecheck_table(checks, "../")}'
            f'<h2>喊單成績</h2>{calls_table(my_calls, "../")}'
            f'<h2>13F 季度持倉</h2>{h13}'
            f'{clone_html}'
            f'<h2>發文紀錄</h2>{_feed_html(prof.posts, names, "../", limit=100, link_investor=False)}')
    return _page(f"{inv.name} · Smartmoney Radar", body, "../", "")


def build_site(store: Store, investors: dict[str, Investor], out_dir: str | Path,
               days: int = 90, step: int | None = None,
               holdings: dict[str, tuple[str, list[PositionChange], list[ConsistencyItem]]] | None = None,
               prices: PriceBook | None = None) -> Path:
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "investor").mkdir(exist_ok=True)
    step = step or (1 if days <= 31 else 7)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(JS, encoding="utf-8")
    (out / "ticker").mkdir(exist_ok=True)
    from .digest import display_names
    names = display_names(investors)
    start, end = window(days)
    # Score calls over a longer look-back so 90-day outcomes exist.
    calls = score_calls(store.signals_between(start - timedelta(days=270), end), prices) if prices else []
    (out / "index.html").write_text(render_index(store, investors, days, step, calls=calls),
                                    encoding="utf-8")
    from .consensus13f import build_consensus
    consensus = build_consensus(store, investors)
    (out / "consensus.html").write_text(render_consensus(consensus, names), encoding="utf-8")
    (out / "insiders.html").write_text(render_insiders(store), encoding="utf-8")
    (out / "retail.html").write_text(render_retail(store, names), encoding="utf-8")
    from .clone import backtest_all
    clones = backtest_all(store, investors, prices) if prices is not None else []
    (out / "clone.html").write_text(render_clone(clones, names, prices is not None), encoding="utf-8")
    clone_by = {r.investor: r for r in clones}
    for inv in investors.values():
        page = render_investor(store, inv, names, days, step, (holdings or {}).get(inv.key), calls,
                               clone_by.get(inv.key))
        (out / "investor" / f"{inv.key}.html").write_text(page, encoding="utf-8")
    tickers = {s.ticker for s in store.signals_between(start, end)
               if not s.ticker.startswith(("SECTOR:", "MACRO:"))}
    tickers |= {t.ticker for t in store.trades_between(start - timedelta(days=90), end) if t.ticker}
    tickers |= {r.key for r in consensus.most_owned(25) + consensus.most_bought() + consensus.most_sold()
                if r.key.isupper() and len(r.key) <= 6}
    from .insiders import find_clusters
    tickers |= {c.ticker for c in find_clusters(store.insider_buys_between(end - timedelta(days=13), end))}
    for t in sorted(tickers):
        (out / "ticker" / f"{_slug(t)}.html").write_text(
            render_ticker(store, t, names, days, prices, consensus), encoding="utf-8")
    return out / "index.html"
