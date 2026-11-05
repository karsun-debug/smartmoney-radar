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

from .digest import build_digest, investor_profile, sector_series
from .models import ChangeType, ConsistencyItem, Investor, PositionChange, Post, Signal, Verdict
from .sectors import SECTORS, signal_label
from .store import Store, window

esc = html.escape

STANCE = {"bullish": ("▲", "看多", "bull"), "bearish": ("▼", "看空", "bear"),
          "neutral": ("●", "中性", "neutral")}
CHANGE = {ChangeType.NEW: "新建倉", ChangeType.ADD: "加碼", ChangeType.TRIM: "減碼",
          ChangeType.EXIT: "清倉", ChangeType.HOLD: "不變"}
VERDICT = {Verdict.CONSISTENT: ("✓", "說到做到", "bull"), Verdict.CONTRADICTS: ("!", "言行不一", "bear"),
           Verdict.HELD: ("–", "持有未動", "neutral"), Verdict.NOT_IN_13F: ("?", "13F 查無", "neutral")}
PROOF = {"13F": "13F 季報", "13F+daily": "13F + 每日交易", "8-K": "8-K 公告"}

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


def _page(title: str, body: str, root: str) -> str:
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="{root}assets/style.css"></head>'
            f'<body><main>{body}<footer>SpiderHead · 資料來自公開貼文、新聞與 SEC 申報 · '
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


def line_chart(labels: list[str], values: list[int], vmax: int, height: int = 64,
               unit: str = " 則") -> str:
    """Single-series area/line with a crosshair; y scale shared via ``vmax``."""
    n = len(values)
    vmax = max(vmax, 1)
    xs = [i / (n - 1) * 100 if n > 1 else 50 for i in range(n)]
    ys = [height - v / vmax * (height - 4) for v in values]
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
        src = "本人發言" if post.kind == "own" else "相關報導"
        link = f' · <a href="{esc(post.url)}" rel="noopener" target="_blank">原文</a>' if post.url else ""
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


def render_index(store: Store, investors: dict[str, Investor], days: int, step: int,
                 recent_days: int = 7) -> str:
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

    body = (f'<header class="top"><h1>SpiderHead 大神追蹤</h1>'
            f'<span class="sub">資料期間 {start} ～ {end}</span></header>{tiles}'
            f'<h2>產業熱度變化（每{"天" if step == 1 else f" {step} 天"}被提及的則數）</h2>{heat}{heat_table}'
            f'<h2>多位大神同時在談（近 {recent_days} 天）</h2>{cons}'
            f'<h2>大神</h2><div class="grid">{"".join(inv_cards)}</div>'
            f'<h2>最新動態</h2>{_feed_html(recent.feed, names, "")}')
    return _page("SpiderHead 大神追蹤", body, "")


def render_investor(store: Store, inv: Investor, names: dict[str, str], days: int, step: int,
                    holdings: tuple[str, list[PositionChange], list[ConsistencyItem]] | None = None) -> str:
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
        f'<tr><td><strong>{esc(t)}</strong></td><td class="num">{c["bullish"]}</td>'
        f'<td class="num">{c["bearish"]}</td><td class="num">{c["neutral"]}</td></tr>' for t, c in tick_rows[:20]))
    tick_html = (f'<div class="tablewrap"><table><tr><th>標的</th><th class="num">▲ 看多</th>'
                 f'<th class="num">▼ 看空</th><th class="num">● 中性</th></tr>{tick_html}</table></div>'
                 if tick_rows else '<p class="empty">這段期間沒有提到個股。</p>')

    if holdings:
        quarter, changes, items = holdings
        moves = [c for c in changes if c.change is not ChangeType.HOLD][:15]
        hrows = "".join(
            f'<tr><td>{esc(CHANGE[c.change])}</td><td>{esc(c.ticker or c.issuer)}'
            f'{" (" + esc(c.put_call) + ")" if c.put_call else ""}</td>'
            f'<td class="num">{c.prev_shares:,} → {c.curr_shares:,}</td>'
            f'<td class="num">{c.portfolio_weight * 100:.2f}%</td></tr>' for c in moves)
        h13 = (f'<p class="sub">申報季度 {esc(quarter)}，依交易金額排序。</p><div class="tablewrap"><table>'
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

    body = (f'<p class="sub"><a href="../index.html">← 全部大神</a></p>'
            f'<header class="top"><h1>{esc(inv.name)}</h1><span class="sub">{esc(inv.fund)} · '
            f'{" · ".join(links)}</span></header>'
            f'<p class="note">{esc(inv.note)}</p>'
            f'<div class="tiles"><div class="tile"><div class="v">{len(prof.posts)}</div><div class="l">{days} 天發文 / 報導</div></div>'
            f'<div class="tile"><div class="v">{own}</div><div class="l">本人發言</div></div>'
            f'<div class="tile"><div class="v">{len(prof.tickers)}</div><div class="l">提到的個股</div></div>'
            f'<div class="tile"><div class="v">{esc(PROOF.get(inv.proof, inv.proof))}</div><div class="l">對帳單</div></div></div>'
            f'<h2>發文頻率</h2><div class="card">{bar_chart(_labels(buckets, step, end), activity)}</div>'
            f'<h2>最常談的產業</h2><div class="card">{sec_html}</div>'
            f'<h2>提到的個股</h2>{tick_html}'
            f'<h2>實際持倉（對帳單）</h2>{h13}'
            f'<h2>發文紀錄</h2>{_feed_html(prof.posts, names, "../", limit=100, link_investor=False)}')
    return _page(f"{inv.name} · SpiderHead", body, "../")


def build_site(store: Store, investors: dict[str, Investor], out_dir: str | Path,
               days: int = 90, step: int | None = None,
               holdings: dict[str, tuple[str, list[PositionChange], list[ConsistencyItem]]] | None = None) -> Path:
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "investor").mkdir(exist_ok=True)
    step = step or (1 if days <= 31 else 7)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(JS, encoding="utf-8")
    (out / "index.html").write_text(render_index(store, investors, days, step), encoding="utf-8")
    from .digest import display_names
    names = display_names(investors)
    for inv in investors.values():
        page = render_investor(store, inv, names, days, step, (holdings or {}).get(inv.key))
        (out / "investor" / f"{inv.key}.html").write_text(page, encoding="utf-8")
    return out / "index.html"
