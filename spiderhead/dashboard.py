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
main{max-width:1120px;margin:0 auto;padding:24px 16px 64px;
padding-left:max(16px,env(safe-area-inset-left));padding-right:max(16px,env(safe-area-inset-right));
padding-top:max(24px,env(safe-area-inset-top))}
.tabbar{display:none}
.offline{display:none;position:sticky;top:0;z-index:30;background:var(--chip);color:var(--ink);font-size:13px;
padding:6px 12px;border-radius:8px;margin-bottom:8px}
button.star{font:inherit;font-size:14px;padding:6px 12px;border-radius:999px;border:1px solid var(--border);
background:var(--surface);color:var(--ink);cursor:pointer;min-height:36px}
button.star.on{background:var(--series);color:#fff;border-color:var(--series)}
.my-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.my-card h3{display:flex;justify-content:space-between;align-items:center;margin:0 0 6px}
.my-card ul{margin:4px 0 0;padding-left:18px}.my-card li{margin:2px 0}
.more-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.more-grid a{display:block;padding:14px;border-radius:12px;background:var(--surface);border:1px solid var(--border);
color:var(--ink);text-decoration:none;font-weight:600;min-height:44px}
.more-grid a span{display:block;font-weight:400;font-size:12px;color:var(--ink-2);margin-top:2px}
@media (max-width:760px){
 nav.top a:not(.brand){display:none}
 nav.top input{max-width:none}
 main{padding-bottom:calc(84px + env(safe-area-inset-bottom))}
 .tabbar{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:40;background:var(--surface);
  border-top:1px solid var(--border);padding-bottom:env(safe-area-inset-bottom)}
 .tabbar a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:7px 0 5px;min-height:50px;
  font-size:11px;color:var(--muted);text-decoration:none;-webkit-tap-highlight-color:transparent}
 .tabbar a.on{color:var(--series)}
 .tabbar svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
 h1{font-size:22px}
 .tiles{grid-template-columns:1fr 1fr}
 table td.wrap,table th.wrap{min-width:0;overflow-wrap:anywhere}
 table th,table td{padding:8px 7px}
 .tile .v{font-size:21px}
}
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
.bar.neg{fill:var(--bear)}
.fin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.cross{stroke:var(--ink-2);stroke-width:1;vector-effect:non-scaling-stroke;visibility:hidden}
.axis{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:6px;padding:4px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:9;white-space:nowrap}
.badge{display:inline-flex;gap:4px;align-items:center;border-radius:999px;padding:1px 8px;font-size:12px;
background:var(--chip);color:var(--ink);white-space:nowrap}
.badge.bull .i{color:var(--bull)}.badge.bear .i{color:var(--bear)}.badge.neutral .i{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top;white-space:nowrap}
td.wrap,th.wrap{white-space:normal;min-width:220px}
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
nav.top input{margin-left:auto;min-width:160px;max-width:240px;flex:1;padding:4px 8px;border-radius:6px;
border:1px solid var(--border);background:var(--surface);color:var(--ink);font:inherit;font-size:13px}
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


from .pwa import REGISTER_JS  # noqa: E402


def safe_url(url: str | None) -> str | None:
    """Only http(s) links from feeds become clickable (blocks javascript:/data: URLs)."""
    if url and url.strip().lower().startswith(("http://", "https://")):
        return url.strip()
    return None


NAV = [("index.html", "總覽"), ("my.html", "我的追蹤"), ("macro.html", "總經"), ("earnings.html", "財報"), ("banks.html", "投行 vs 大神"),
       ("consensus.html", "大神共同持股"), ("clone.html", "跟單回測"),
       ("insiders.html", "內部人群買"), ("activist.html", "維權持股"), ("retail.html", "散戶 vs 大神"),
       ("buzz.html", "聲量排行"), ("weekly.html", "週報")]


def _nav(root: str, active: str) -> str:
    links = "".join(f'<a href="{root}{href}"{" class=on" if href == active else ""}>{esc(label)}</a>'
                    for href, label in NAV)
    search = (f'<input id="sh-q" type="search" list="sh-list" placeholder="搜尋股票或大神…" '
              f'aria-label="搜尋" data-root="{root}"><datalist id="sh-list"></datalist>')
    return (f'<nav class="top"><a class="brand" href="{root}index.html">Smartmoney Radar</a>{links}'
            f'<a href="{root}data/index.json">資料</a>{search}</nav>')


SEARCH_JS = """
(function(){
const q=document.getElementById('sh-q'),list=document.getElementById('sh-list');
if(!q||!window.SH_INDEX)return;
const root=q.dataset.root||'';
window.SH_INDEX.forEach(([label])=>{const o=document.createElement('option');o.value=label;list.appendChild(o);});
function go(){const v=q.value.trim().toUpperCase();if(!v)return;
 const hit=window.SH_INDEX.find(([l])=>l.toUpperCase()===v)||window.SH_INDEX.find(([l])=>l.toUpperCase().startsWith(v))
  ||window.SH_INDEX.find(([l])=>l.toUpperCase().includes(v));
 if(hit)location.href=root+hit[1];}
q.addEventListener('change',go);q.addEventListener('keydown',e=>{if(e.key==='Enter')go();});
})();
"""


TAB_ICONS = {
    "home": '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
    "macro": '<path d="M3 20h18"/><path d="M4 16l5-6 4 3 7-8"/>',
    "bank": '<path d="M3 10l9-6 9 6"/><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8"/><path d="M3 20h18"/>',
    "earnings": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 15v2M12 11v6M16 7v10"/>',
    "more": '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    "star": '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
}
TABS = [("index.html", "總覽", "home"), ("my.html", "我的", "star"), ("macro.html", "總經", "macro"),
        ("banks.html", "投行", "bank"), ("more.html", "更多", "more")]


def _tabbar(root: str, active: str) -> str:
    on = active if active in {t[0] for t in TABS[:-1]} else "more.html"
    return '<nav class="tabbar" aria-label="主要分頁">' + "".join(
        f'<a href="{root}{href}"{" class=on aria-current=page" if href == on else ""}>'
        f'<svg viewBox="0 0 24 24" aria-hidden="true">{TAB_ICONS[icon]}</svg>{esc(label)}</a>'
        for href, label, icon in TABS) + '</nav>'


def _page(title: str, body: str, root: str, active: str = "") -> str:
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<link rel="manifest" href="{root}manifest.webmanifest">'
            f'<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
            f'<meta name="apple-mobile-web-app-status-bar-style" content="default">'
            f'<meta name="apple-mobile-web-app-title" content="大神雷達">'
            f'<meta name="theme-color" content="#f9f9f7" media="(prefers-color-scheme: light)">'
            f'<meta name="theme-color" content="#0d0d0d" media="(prefers-color-scheme: dark)">'
            f'<link rel="apple-touch-icon" href="{root}assets/icons/apple-touch-icon.png">'
            f'<link rel="icon" type="image/png" href="{root}assets/icons/favicon-32.png">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="{root}assets/style.css"></head>'
            f'<body><main><div class="offline" id="sh-offline">目前離線，顯示的是上次更新的資料</div>'
            f'{_nav(root, active)}{body}<footer>Smartmoney Radar · 資料來自公開貼文、新聞與 SEC 申報 · '
            f'僅供研究參考，不構成投資建議 · 產生於 {datetime.now():%Y-%m-%d %H:%M}</footer></main>'
            f'{_tabbar(root, active)}<script src="{root}assets/app.js"></script></body></html>')


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


def signed_bar_chart(labels: list[str], values: list[float | None], height: int = 110,
                     fmt=lambda v: f"{v:,.0f}") -> str:
    """Bars above/below a zero line (negative values in the 'bear' color)."""
    vals = [v if v is not None else 0.0 for v in values]
    top, bot = max(max(vals, default=0), 0), min(min(vals, default=0), 0)
    span = (top - bot) or 1
    zero = height * top / span
    n = len(vals) or 1
    slot = 100 / n
    gap = min(1.2, slot * 0.25)
    bars = []
    for i, (lab, raw, v) in enumerate(zip(labels, values, vals)):
        h = abs(v) / span * height
        y = zero - h if v >= 0 else zero
        tip = f"{lab}&#10;{'—' if raw is None else esc(fmt(raw))}"
        bars.append(f'<rect class="bar{" neg" if v < 0 else ""}" tabindex="0" x="{i * slot + gap / 2:.2f}" '
                    f'y="{y:.2f}" width="{slot - gap:.2f}" height="{max(h, 0.5):.2f}" data-tip="{tip}">'
                    f'<title>{esc(lab)} {"—" if raw is None else esc(fmt(raw))}</title></rect>')
    return (f'<div class="chart"><svg viewBox="0 0 100 {height}" preserveAspectRatio="none" height="{height}" role="img">'
            f'{"".join(bars)}<line class="baseline" x1="0" x2="100" y1="{zero:.2f}" y2="{zero:.2f}" '
            f'vector-effect="non-scaling-stroke"/></svg>'
            f'<div class="axis"><span>{esc(labels[0]) if labels else ""}</span>'
            f'<span>{esc(labels[-1]) if labels else ""}</span></div></div>')


def _money(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


def _eps(v: float | None) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _q(d: date) -> str:
    return f"{d.year % 100:02d}Q{(d.month - 1) // 3 + 1}"


def _ticker_financials(store: Store, ticker: str) -> str:
    from .financials import yoy
    snap = store.load_financials(ticker)
    news = store.earnings_news(ticker, 8)
    parts = []
    if snap and snap.quarters:
        qs = snap.quarters[-8:]
        labels = [_q(q.end) for q in qs]
        charts = [
            ("營收", [q.get("revenue") for q in qs], _money),
            ("淨利", [q.get("net_income") for q in qs], _money),
            ("自由現金流", [q.fcf for q in qs], _money),
            ("EPS（稀釋）", [q.get("eps") for q in qs], lambda v: f"${v:,.2f}"),
        ]
        cards = "".join(f'<div class="card"><h3>{name}</h3>{signed_bar_chart(labels, vals, fmt=fmt)}</div>'
                        for name, vals, fmt in charts if any(v is not None for v in vals))
        rows = "".join(
            f'<tr><td>{_q(q.end)}<div class="sub">{q.end}</div></td><td class="num">{_money(q.get("revenue"))}'
            f'<div class="sub">年增 {_pct(yoy(snap.quarters, q, "revenue"))}</div></td>'
            f'<td class="num">{_pct(q.margin("gross_profit")).lstrip("+")}</td>'
            f'<td class="num">{_pct(q.margin("operating_income")).lstrip("+")}</td>'
            f'<td class="num">{_money(q.get("net_income"))}<div class="sub">淨利率 {_pct(q.margin("net_income")).lstrip("+")}</div></td>'
            f'<td class="num">{_eps(q.get("eps"))}'
            f'<div class="sub">年增 {_pct(yoy(snap.quarters, q, "eps"))}</div></td>'
            f'<td class="num">{_money(q.fcf)}</td></tr>' for q in reversed(qs))
        parts.append(
            f'<h2>財報（SEC 申報資料）</h2><p class="sub">{esc(snap.name)} · 最近 {len(qs)} 季；'
            f'第四季由全年減前三季推算。</p><div class="fin-grid">{cards}</div>'
            f'<div class="tablewrap" style="margin-top:12px"><table><tr><th>季度</th><th class="num">營收</th>'
            f'<th class="num">毛利率</th><th class="num">營業利益率</th><th class="num">淨利</th>'
            f'<th class="num">EPS</th><th class="num">自由現金流</th></tr>{rows}</table></div>')
    if news:
        items = "".join(
            f'<li><div class="sub">{d}</div><div class="txt">'
            + (f'<a href="{esc(safe_url(u))}" rel="noopener" target="_blank">{esc(t)}</a>' if safe_url(u) else esc(t))
            + '</div></li>' for d, t, u in news)
        parts.append(f'<h2>財報分析與報導（別人怎麼看）</h2><ul class="feed">{items}</ul>')
    return "".join(parts)


def render_earnings(store: Store) -> str:
    snaps = [s for s in (store.load_financials(t) for t in store.financial_tickers()) if s and s.latest]
    note = ('<p class="sub">資料來自 SEC XBRL 財報（10-Q / 10-K，免費公開），涵蓋大神持有、喊單或投行有評等的公司，'
            '每週更新。點股票代號看圖表與財報分析報導。</p>')
    if not snaps:
        body = f'<h1>財報</h1>{note}<p class="empty">還沒有財報資料，執行 <code>spiderhead fetch</code>（需要 SPIDERHEAD_USER_AGENT）。</p>'
        return _page("財報 · Smartmoney Radar", body, "", "earnings.html")
    snaps.sort(key=lambda s: -(s.growth("revenue") if s.growth("revenue") is not None else -1e9))
    rows = []
    for s in snaps:
        q = s.latest
        g = s.growth("revenue")
        rows.append(
            f'<tr><td>{_tick(s.ticker, "")}<div class="sub">{esc(s.name[:28])}</div></td><td>{_q(q.end)}</td>'
            f'<td class="num">{_money(q.get("revenue"))}</td>'
            f'<td class="num">{_pct(g)}{_bar(None if g is None else max(0.0, min(1.0, (g + 50) / 100)))}</td>'
            f'<td class="num">{_pct(q.margin("gross_profit")).lstrip("+")}</td>'
            f'<td class="num">{_pct(q.margin("operating_income")).lstrip("+")}</td>'
            f'<td class="num">{_pct(s.growth("eps"))}</td><td class="num">{_money(q.fcf)}</td>'
            f'<td>{q.end + timedelta(days=91 + 35):%m/%d}</td></tr>')
    table = ('<div class="tablewrap"><table><tr><th>公司</th><th>最新季</th><th class="num">營收</th>'
             '<th class="num">營收年增</th><th class="num">毛利率</th><th class="num">營業利益率</th>'
             '<th class="num">EPS 年增</th><th class="num">自由現金流</th><th>預估下次財報</th></tr>'
             + "".join(rows) + '</table></div><p class="sub">「預估下次財報」＝最新季末 + 約 126 天，僅供參考。</p>')
    return _page("財報 · Smartmoney Radar", f"<h1>財報</h1>{note}{table}", "", "earnings.html")


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
                    f'<td class="num">{h.items_7d}</td><td class="wrap">{err}</td></tr>')
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
        f'<td class="num">{h.positions}</td><td class="wrap">'
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


def _ticker_shorts(store: Store, ticker: str) -> str:
    from .shorts import short_ratio
    got = short_ratio(store, ticker, days=10)
    if not got:
        return ""
    avg, rows = got
    trs = "".join(f'<tr><td>{d}</td><td class="num">{s / t * 100:.1f}%</td><td class="num">{t:,.0f}</td></tr>'
                  for d, s, t in reversed(rows))
    return (f'<h2>放空成交量比例（FINRA）</h2><p class="sub">最近 {len(rows)} 個交易日平均 {avg * 100:.1f}%。'
            f'包含造市商避險，請看趨勢而非單日數字。</p><div class="tablewrap"><table><tr><th>日期</th>'
            f'<th class="num">放空占比</th><th class="num">總成交量</th></tr>{trs}</table></div>')


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
        f'<td class="num">{p.coverage * 100:.0f}%</td><td class="wrap">'
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


def movers_html(prices, tickers) -> str:
    from .analytics import movers
    if prices is None or not tickers:
        return ""
    ms = movers(prices, sorted(tickers))
    if len(ms) < 2:
        return ""
    top, bottom = ms[:5], [m for m in ms[::-1][:5] if m not in ms[:5]]

    def rows(lst):
        return "".join(f'<tr><td>{_tick(m.ticker, "")}</td><td class="num">${m.close:,.2f}</td>'
                       f'<td class="num">{_badge("bullish" if m.change_pct >= 0 else "bearish", f"{m.change_pct:+.2f}%")}</td>'
                       f'<td class="sub">{m.day}</td></tr>' for m in lst)
    head = '<tr><th>標的</th><th class="num">收盤</th><th class="num">漲跌</th><th>日期</th></tr>'
    return (f'<h2>大神關注股：最新漲跌</h2><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">'
            f'<div><h3>漲最多</h3><div class="tablewrap"><table>{head}{rows(top)}</table></div></div>'
            f'<div><h3>跌最多</h3><div class="tablewrap"><table>{head}{rows(bottom)}</table></div></div></div>')


def discount_html(store, investors, names, prices) -> str:
    from .analytics import below_guru_price
    if store is None or prices is None:
        return ""
    ds = below_guru_price(store, investors, prices)[:25]
    if not ds:
        return ""
    trs = "".join(
        f'<tr><td>{_tick(d.ticker, "")}</td><td><a href="investor/{esc(d.investor)}.html">{esc(names.get(d.investor, d.investor))}</a>'
        f'<div class="sub">占組合 {d.weight * 100:.1f}%</div></td><td class="num">${d.ref_price:,.2f}<div class="sub">{esc(d.quarter)}</div></td>'
        f'<td class="num">${d.price:,.2f}</td><td class="num">{_badge("bearish", f"{d.discount_pct:.1f}%")}</td></tr>'
        for d in ds)
    return ('<h2>比大神 13F 價位還便宜</h2><p class="sub">現價低於大神最新 13F 季末的每股市值（13F 市值 ÷ 股數）。'
            '這是季末價格，不是大神的實際成本。</p><div class="tablewrap"><table><tr><th>標的</th><th>大神</th>'
            '<th class="num">13F 季末價</th><th class="num">現價</th><th class="num">差距</th></tr>' + trs + '</table></div>')


def insider_extras(store, prices) -> str:
    from .analytics import cluster_performance, officer_buys
    from .insiders import find_clusters
    end = date.today()
    parts = []
    big = officer_buys(store.insider_buys_between(end - timedelta(days=29), end))[:20]
    if big:
        trs = "".join(
            f'<tr><td>{b.traded}</td><td>{_tick(b.ticker, "")}<div class="sub">{esc(b.issuer[:30])}</div></td>'
            f'<td>{esc(b.owner)}<div class="sub">{esc(b.title)}</div></td><td class="num">${b.value / 1e6:,.2f}M</td>'
            f'<td><a href="{esc(b.url)}" rel="noopener" target="_blank">Form 4</a></td></tr>' for b in big)
        parts.append('<h2>CEO / CFO 大額買進（30 天、100 萬美元以上）</h2><div class="tablewrap"><table><tr>'
                     '<th>交易日</th><th>公司</th><th>內部人</th><th class="num">金額</th><th>原始申報</th></tr>'
                     + trs + '</table></div>')
    if prices is not None:
        # Clusters from the past year, each judged from its last buy.
        hist = []
        for k in range(0, 365, 14):
            hist += find_clusters(store.insider_buys_between(end - timedelta(days=k + 13), end - timedelta(days=k)))
        seen, uniq = set(), []
        for c in hist:
            if c.uid not in seen:
                seen.add(c.uid)
                uniq.append(c)
        perf = [r for r in cluster_performance(uniq, prices) if r.ret.get(30) is not None]
        if perf:
            trs = "".join(
                f'<tr><td>{_tick(r.ticker, "")}</td><td class="num">{r.insiders}</td><td>{r.last_buy}</td>'
                f'<td class="num">{_pct(r.ret.get(30))}<div class="sub">SPY {_pct(r.spy.get(30))}</div></td>'
                f'<td class="num">{_pct(r.ret.get(90))}<div class="sub">SPY {_pct(r.spy.get(90))}</div></td></tr>'
                for r in perf[:30])
            wins = [r for r in perf if r.ret.get(30) is not None and r.spy.get(30) is not None]
            beat = sum(r.ret[30] > r.spy[30] for r in wins)
            parts.append(f'<h2>群買之後的股價表現</h2><p class="sub">過去一年的群買，從最後一筆買進日起算；'
                         f'30 天後贏過 SPY 的比例：{beat}/{len(wins)}。</p><div class="tablewrap"><table><tr><th>標的</th>'
                         f'<th class="num">人數</th><th>最後買進</th><th class="num">30 天</th><th class="num">90 天</th></tr>'
                         + trs + '</table></div>')
    return "".join(parts)


def render_insiders(store: Store, window_days: int = 14, min_insiders: int = 3, prices=None) -> str:
    from .insiders import find_clusters
    end = date.today()
    start = end - timedelta(days=window_days - 1)
    clusters = find_clusters(store.insider_buys_between(start, end), min_insiders)
    note = (f'<p class="sub">最近 {window_days} 天內，同一家公司有 {min_insiders} 位以上不同的內部人（董事、高管、'
            f'10% 大股東）在公開市場用自己的錢買進，且合計金額超過 10 萬美元。資料來自 SEC 每日 Form 4。</p>')
    if not clusters:
        empty = ('<p class="empty">目前沒有符合的群買。執行 <code>spiderhead insiders</code> 掃描 SEC 每日申報。</p>')
        return _page("內部人群買 · Smartmoney Radar",
                     f"<h1>內部人群買</h1>{note}{empty}{insider_extras(store, prices)}", "", "insiders.html")
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
    return _page("內部人群買 · Smartmoney Radar",
                 f"<h1>內部人群買</h1>{note}{''.join(blocks)}{insider_extras(store, prices)}", "", "insiders.html")


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
    from .shorts import short_ratio

    def sr(t: str) -> str:
        got = short_ratio(store, t)
        return "—" if not got else f"{got[0] * 100:.0f}%"
    trs = "".join(
        f'<tr><td>{_tick(r.ticker, "")}</td><td class="num">{r.mentions}</td><td>{_mood(r.retail_mood)}</td>'
        f'<td class="num">{sr(r.ticker)}</td>'
        f'<td class="wrap">{" ".join(_badge(v, names.get(k, k)) for k, v in sorted(r.guru_views.items())) or "—"}</td>'
        f'<td>{_badge("bearish", "看法相反") if r.divergent else ""}</td></tr>' for r in rows)
    table = ('<div class="tablewrap"><table><tr><th>標的</th><th class="num">散戶討論數</th><th>散戶情緒</th>'
             '<th class="num">放空占比（5 日）</th><th>大神看法</th><th></th></tr>' + trs + '</table></div>')
    return _page("散戶 vs 大神 · Smartmoney Radar", f"<h1>散戶 vs 大神</h1>{note}{table}", "", "retail.html")


def reportcard_table(cards, names: dict[str, str], root: str = "") -> str:
    rows = sorted(cards.values(), key=lambda c: -(c.score if c.score is not None else -1))
    rows = [c for c in rows if c.parts or c.consistent + c.contradicts or c.calls_scored]
    if not rows:
        return '<p class="empty">資料還不夠，累積幾週的發言、申報與股價後才會出現。</p>'

    def part(c, key, detail):
        v = c.parts.get(key)
        return f'<td class="num">{"—" if v is None else f"{v:.0f}"}<div class="sub">{detail}</div></td>'
    trs = "".join(
        f'<tr><td><a href="{root}investor/{esc(c.investor)}.html">{esc(names.get(c.investor, c.investor))}</a></td>'
        f'<td class="num"><strong>{c.grade}</strong><div class="sub">'
        f'{"—" if c.score is None else f"{c.score:.0f} 分"}</div></td>'
        + part(c, "說到做到", f"{c.consistent} 對 / {c.contradicts} 不一")
        + part(c, "喊單準確", f"{c.calls_scored} 次·{c.call_horizon or '—'} 天")
        + part(c, "跟單價值", f"{_pct(c.clone_excess)} vs SPY")
        + "</tr>" for c in rows)
    return ('<div class="tablewrap"><table><tr><th>大神</th><th class="num">評等</th>'
            '<th class="num">說到做到</th><th class="num">喊單準確</th><th class="num">跟單價值</th></tr>'
            + trs + '</table></div><p class="sub">各項 0–100 分；資料少於 3 筆的項目不計入。'
            '說到做到＝公開看法與申報交易（或 13F 變化）同方向的比例；喊單準確＝看多後上漲、看空後下跌的比例；'
            '跟單價值＝照 13F 公布日跟買的報酬超過 SPY 的幅度（±20 個百分點對應 100／0 分）。</p>')


def next_13f_deadline(today: date | None = None) -> tuple[date, date]:
    """(quarter end, filing deadline = 45 days later) for the next 13F deadline on/after today."""
    today = today or date.today()
    for year in (today.year - 1, today.year, today.year + 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            qe = date(year, m, d)
            if qe + timedelta(days=45) >= today:
                return qe, qe + timedelta(days=45)
    raise AssertionError("unreachable")


def render_activist(store: Store, names: dict[str, str], days: int = 365) -> str:
    end = date.today()
    stakes = [t for t in store.trades_between(end - timedelta(days=days), end, by="filed")
              if t.side == "stake" or "13D" in t.form or "13G" in t.form]
    note = ('<p class="sub">持股跨過 5% 時必須申報 Schedule 13D（有意影響公司經營，即「維權」）或 13G（被動持有）。'
            '13D 通常在 5 個工作日內申報，是比 13F 快得多的訊號。</p>')
    if not stakes:
        return _page("維權持股 · Smartmoney Radar", f'<h1>維權持股（13D / 13G）</h1>{note}'
                     f'<p class="empty">最近一年沒有追蹤名單的 13D/13G 申報。</p>', "", "activist.html")
    return _page("維權持股 · Smartmoney Radar",
                 f'<h1>維權持股（13D / 13G）</h1>{note}{trades_table(stakes, names, "", limit=200)}',
                 "", "activist.html")


def render_buzz(store: Store, investors: dict[str, Investor], names: dict[str, str]) -> str:
    from .buzz import buzz_table
    rows = buzz_table(store, investors)
    note = ('<p class="sub">網路聲量＝維基百科條目每日瀏覽量（真人，排除機器人）＋新聞與本人發文則數。'
            '聲量突然暴增，常代表有重大發言、交易或爭議。</p>')
    has_views = any(b.series for b in rows)
    vmax = max((v for b in rows for _, v in b.series), default=1)
    trs = []
    for b in rows:
        spark = ""
        if len(b.series) > 1:
            spark = line_chart([f"{d:%m/%d}" for d, _ in b.series], [v for _, v in b.series], vmax,
                               height=36, unit=" 次")
        trs.append(
            f'<tr><td><a href="investor/{esc(b.investor)}.html">{esc(names.get(b.investor, b.investor))}</a></td>'
            f'<td class="num">{b.views_7d:,}<div class="sub">{_pct(b.views_change)}</div></td>'
            f'<td class="num">{b.news_7d}<div class="sub">{_pct(b.news_change)}</div></td>'
            f'<td class="wrap" style="min-width:200px">{spark or "—"}</td></tr>')
    table = ('<div class="tablewrap"><table><tr><th>大神</th><th class="num">維基瀏覽（7 天）</th>'
             '<th class="num">新聞/發文（7 天）</th><th class="wrap">近 60 天瀏覽走勢</th></tr>'
             + "".join(trs) + '</table></div>')
    if not has_views:
        note += '<p class="empty">還沒有瀏覽量資料，執行 <code>spiderhead fetch</code> 後就會出現。</p>'
    return _page("聲量排行 · Smartmoney Radar", f"<h1>聲量排行</h1>{note}{table}", "", "buzz.html")


def _thin(points, max_points: int = 160):
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    picked = [points[int(i * step)] for i in range(max_points)]
    return picked[:-1] + [points[-1]]


def _fmt(v: float, unit: str) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def render_macro(store: Store, names: dict[str, str]) -> str:
    from .macro import load_views, signals
    views = load_views(store)
    have = [m for m in views if len(m.points) > 1]
    lvl = {"ok": ("bull", "✓", "正常"), "warn": ("neutral", "!", "留意"), "alert": ("bear", "✕", "警訊")}
    sig_rows = "".join(
        f'<tr><td class="wrap">{esc(s.name)}</td><td><span class="badge {lvl[s.level][0]}"><span class="i">{lvl[s.level][1]}'
        f'</span>{lvl[s.level][2]}</span></td><td class="wrap">{esc(s.text)}</td></tr>' for s in signals(views))
    sig_html = (f'<div class="tablewrap"><table><tr><th>指標</th><th>狀態</th><th class="wrap">說明</th></tr>'
                f'{sig_rows}</table></div>') if sig_rows else ""
    cards = []
    for m in have:
        pts = _thin(m.points)
        vals = [round(v, 4) for _, v in pts]
        d, v = m.latest
        c1, c12 = m.change(30), m.change(365)
        chg = " · ".join(x for x in (
            f"1 個月 {c1:+,.2f}" if c1 is not None else "", f"1 年 {c12:+,.2f}" if c12 is not None else "") if x)
        cards.append(f'<div class="card"><h3>{esc(m.series.name)}</h3>'
                     f'<div class="meta"><span><strong style="font-size:20px;color:var(--ink)">'
                     f'{_fmt(v, m.series.unit)}</strong> {esc(m.series.unit)}</span><span>{d}</span></div>'
                     f'{line_chart([f"{x:%Y/%m/%d}" for x, _ in pts], vals, max(vals), height=72, unit=" " + m.series.unit, vmin=min(vals))}'
                     f'<div class="sub">{esc(chg)}{" · " if chg and m.series.note else ""}{esc(m.series.note)}</div></div>')
    grid = (f'<div class="grid">{"".join(cards)}</div>' if cards else
            '<p class="empty">還沒有總經資料，執行 <code>spiderhead fetch</code>（資料來自 FRED，免費）。</p>')
    end = date.today()
    views_rows = []
    seen = set()
    for s in sorted(store.signals_between(end - timedelta(days=60), end), key=lambda s: s.post.published, reverse=True):
        if not s.is_own_view or (s.sector != "macro" and not s.ticker.startswith("MACRO:")) or s.post.uid in seen:
            continue
        seen.add(s.post.uid)
        views_rows.append(f'<tr><td>{s.post.published}</td><td><a href="investor/{esc(s.post.investor_key)}.html">'
                          f'{esc(names.get(s.post.investor_key, s.post.investor_key))}</a></td>'
                          f'<td>{_badge(s.stance.value)}</td><td class="wrap">{esc(s.post.text[:160])}</td></tr>')
    guru = (f'<div class="tablewrap"><table><tr><th>日期</th><th>誰</th><th>看法</th><th class="wrap">內容</th></tr>'
            f'{"".join(views_rows[:30])}</table></div>' if views_rows
            else '<p class="empty">最近 60 天沒有大神談總經。</p>')
    body = (f'<h1>總經儀表板</h1><p class="sub">資料來自聖路易聯準銀行 FRED（免費公開）。圖表為近 3 年，'
            f'滑過圖表可看每個時間點的數值。</p><h2>總經訊號</h2>{sig_html}<h2>關鍵指標</h2>{grid}'
            f'<h2>大神怎麼看總經（近 60 天）</h2>{guru}')
    return _page("總經 · Smartmoney Radar", body, "", "macro.html")


ACTION_LABEL = {"upgrade": "升評", "downgrade": "降評", "initiate": "首次評等", "raise_pt": "調高目標價",
                "cut_pt": "調降目標價", "reiterate": "維持評等"}


def _bar(frac: float | None) -> str:
    if frac is None:
        return ""
    return (f'<div class="track" style="height:6px;margin-top:4px;background:var(--grid);border-radius:3px">'
            f'<div style="height:6px;border-radius:3px;background:var(--series);width:{max(0, min(1, frac)) * 100:.0f}%">'
            f'</div></div>')


def render_banks(store: Store, names: dict[str, str], prices: PriceBook | None, days: int = 365) -> str:
    from .banks import BANK_BY_KEY, bank_scores, says_vs_holds, score_bank_calls
    end = date.today()
    calls = store.bank_calls(end - timedelta(days=days), end)
    bname = lambda k: BANK_BY_KEY[k].name if k in BANK_BY_KEY else k  # noqa: E731
    note = ('<p class="sub">投行評等來自免費新聞標題（自動解析升評、降評與目標價），'
            '事後用股價驗證：方向正確率看 90 天後漲跌是否符合評等，超額報酬與 SPY 比較，'
            '目標價達成率看一年內是否碰到目標價。</p>')
    if not calls:
        body = (f'<h1>投行 vs 大神</h1>{note}<p class="empty">還沒有投行評等資料，執行 '
                f'<code>spiderhead fetch</code>。</p>')
        return _page("投行 vs 大神 · Smartmoney Radar", body, "", "banks.html")
    outcomes = score_bank_calls(calls, prices) if prices is not None else []
    scores = bank_scores(outcomes)
    rows = sorted(scores.values(), key=lambda s: -(s.hit_rate if s.hit_rate is not None else -1))
    score_trs = "".join(
        f'<tr><td>{esc(bname(s.bank))}</td><td class="num">{s.calls}</td>'
        f'<td class="num">{"—" if s.hit_rate is None else f"{s.hit_rate * 100:.0f}%"}'
        f'<div class="sub">{s.hits_90}/{s.scored_90}</div>{_bar(s.hit_rate)}</td>'
        f'<td class="num">{_pct(s.avg_excess_90)}</td>'
        f'<td class="num">{"—" if s.target_rate is None else f"{s.target_rate * 100:.0f}%"}'
        f'<div class="sub">{s.targets_hit}/{s.targets_resolved}</div>{_bar(s.target_rate)}</td>'
        f'<td class="num">{_pct(s.avg_upside)}</td></tr>' for s in rows)
    score_html = (
        '<div class="tablewrap"><table><tr><th>投行</th><th class="num">評等數</th>'
        '<th class="num">90 天方向正確率</th><th class="num">平均超額報酬（vs SPY）</th>'
        '<th class="num">目標價達成率</th><th class="num">喊出時的平均上檔空間</th></tr>'
        + score_trs + '</table></div><p class="sub">「平均上檔空間」越大、達成率越低，代表目標價越樂觀。'
        '需要股價資料（不要加 --no-prices）。</p>') if outcomes else '<p class="empty">需要股價資料才能驗證。</p>'

    mism = says_vs_holds(store, calls)
    chg = {"NEW": "新建倉", "ADD": "加碼", "TRIM": "減碼", "EXIT": "清倉"}
    mism_html = ("".join(
        f'<tr><td>{m.call.published}</td><td>{esc(bname(m.call.bank))}</td><td>{_tick(m.call.ticker, "")}</td>'
        f'<td>{_badge(m.call.stance, ACTION_LABEL.get(m.call.action, m.call.action))}</td>'
        f'<td>{esc(chg.get(m.change, m.change))}<div class="sub">{m.shares_before:,} → {m.shares_after:,} 股'
        f'（{esc(m.quarter)}）</div></td></tr>' for m in mism))
    mism_html = (f'<div class="tablewrap"><table><tr><th>評等日</th><th>投行</th><th>標的</th><th>研究報告說</th>'
                 f'<th>同季 13F 實際</th></tr>{mism_html}</table></div>' if mism else
                 '<p class="empty">目前沒有發現（或投行 13F 尚未下載：<code>spiderhead dashboard --with-13f</code>）。</p>')
    caveat = ('<p class="sub">⚠️ 投行的 13F 混合了客戶資產、造市與資產管理部位，研究部門與交易部門依法分隔。'
              '「說買卻在賣」值得留意，但<strong>不等於</strong>證明投行說謊或刻意出貨。</p>')

    # Banks vs gurus on the same tickers (last 90 days).
    recent = [c for c in calls if c.published >= end - timedelta(days=90) and c.stance != "neutral"]
    guru_views: dict[str, dict[str, str]] = {}
    for s in store.signals_between(end - timedelta(days=90), end):
        if s.is_own_view and s.stance.value != "neutral" and not s.ticker.startswith(("SECTOR:", "MACRO:")):
            guru_views.setdefault(s.ticker, {})[s.post.investor_key] = s.stance.value
    for t in store.trades_between(end - timedelta(days=90), end):
        if t.ticker and t.side in ("buy", "sell"):
            guru_views.setdefault(t.ticker, {})[t.investor_key] = "bullish" if t.side == "buy" else "bearish"
    vs_rows = []
    for tk in sorted({c.ticker for c in recent}):
        bviews = {}
        for c in sorted((c for c in recent if c.ticker == tk), key=lambda c: c.published):
            bviews[c.bank] = c.stance
        g = guru_views.get(tk, {})
        if not g:
            continue
        bmood = sum(1 if v == "bullish" else -1 for v in bviews.values())
        gmood = sum(1 if v == "bullish" else -1 for v in g.values())
        flag = _badge("bearish", "看法相反") if bmood * gmood < 0 else (_badge("bullish", "看法一致") if bmood * gmood > 0 else "")
        vs_rows.append(
            f'<tr><td>{_tick(tk, "")}</td><td class="wrap">'
            + " ".join(_badge(v, bname(k).split(" ")[0]) for k, v in bviews.items()) + '</td><td class="wrap">'
            + " ".join(_badge(v, names.get(k, k)) for k, v in sorted(g.items())) + f'</td><td>{flag}</td></tr>')
    vs_html = (f'<div class="tablewrap"><table><tr><th>標的</th><th class="wrap">投行（最新評等）</th>'
               f'<th class="wrap">大神（發言或申報交易）</th><th></th></tr>{"".join(vs_rows)}</table></div>'
               if vs_rows else '<p class="empty">近 90 天沒有投行和大神同時表態的股票。</p>')

    by_call = {id(o.call): o for o in outcomes}
    latest = []
    for c in calls[:60]:
        o = by_call.get(id(c))
        since = None
        if o and o.entry and prices is not None:
            now = prices.close_on_or_before(c.ticker, end)
            since = (now[1] / o.entry - 1) * 100 if now else None
        latest.append(
            f'<tr><td>{c.published}</td><td>{esc(bname(c.bank))}</td><td>{_tick(c.ticker, "")}</td>'
            f'<td>{_badge(c.stance, ACTION_LABEL.get(c.action, c.action))}</td><td>{esc(c.rating or "—")}</td>'
            f'<td class="num">{"—" if not c.target else f"${c.target:,.0f}"}'
            f'{"" if not (o and o.upside_at_call is not None) else f"<div class=sub>上檔 {o.upside_at_call:+.0f}%</div>"}</td>'
            f'<td class="num">{_pct(since)}</td>'
            f'<td>{"" if not safe_url(c.url) else f"<a href={chr(34)}{esc(safe_url(c.url))}{chr(34)} rel=noopener target=_blank>報導</a>"}</td></tr>')
    latest_html = ('<div class="tablewrap"><table><tr><th>日期</th><th>投行</th><th>標的</th><th>動作</th>'
                   '<th>評等</th><th class="num">目標價</th><th class="num">之後漲跌</th><th>來源</th></tr>'
                   + "".join(latest) + '</table></div>')
    body = (f'<h1>投行 vs 大神</h1>{note}<h2>投行準確度排行</h2>{score_html}'
            f'<h2>研究報告 vs 自家 13F（出貨嫌疑）</h2>{caveat}{mism_html}'
            f'<h2>投行 vs 大神：同一檔股票的看法</h2>{vs_html}<h2>最新評等變動</h2>{latest_html}')
    return _page("投行 vs 大神 · Smartmoney Radar", body, "", "banks.html")


def _ticker_banks(store: Store, ticker: str) -> str:
    from .banks import BANK_BY_KEY
    end = date.today()
    calls = store.bank_calls(end - timedelta(days=365), end, ticker)
    if not calls:
        return ""
    targets = [c.target for c in calls if c.target]
    trs = "".join(
        f'<tr><td>{c.published}</td><td>{esc(BANK_BY_KEY[c.bank].name if c.bank in BANK_BY_KEY else c.bank)}</td>'
        f'<td>{_badge(c.stance, ACTION_LABEL.get(c.action, c.action))}</td><td>{esc(c.rating or "—")}</td>'
        f'<td class="num">{"—" if not c.target else f"${c.target:,.0f}"}</td></tr>' for c in calls[:20])
    avg = f"，平均目標價 ${sum(targets) / len(targets):,.0f}" if targets else ""
    return (f'<h2>投行評等（近一年）</h2><p class="sub">{len(calls)} 則{avg}</p><div class="tablewrap"><table>'
            f'<tr><th>日期</th><th>投行</th><th>動作</th><th>評等</th><th class="num">目標價</th></tr>{trs}</table></div>')


MY_JS = """
(function(){
const KEY='smr-stars';
function load(){try{return JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){return []}}
function save(v){try{localStorage.setItem(KEY,JSON.stringify(v))}catch(e){}}
document.querySelectorAll('button.star[data-ticker]').forEach(b=>{
 const t=b.dataset.ticker;
 const paint=()=>{const on=load().includes(t);b.classList.toggle('on',on);b.setAttribute('aria-pressed',on);
  b.textContent=on?'★ 已追蹤':'☆ 加入追蹤';};
 b.addEventListener('click',()=>{let s=load();s=s.includes(t)?s.filter(x=>x!==t):[...s,t];save(s);paint();});
 paint();
});
const box=document.getElementById('my-list');
if(!box)return;
const root=box.dataset.root||'';
const stars=load();
const el=(tag,txt,cls)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;if(cls)e.className=cls;return e;};
if(!stars.length){box.replaceChildren(el('p','還沒有追蹤任何股票。到任何股票頁按「☆ 加入追蹤」。','empty'));return;}
const get=f=>fetch(root+'data/'+f).then(r=>r.ok?r.json():[]).catch(()=>[]);
Promise.all([get('signals.json'),get('trades.json'),get('bank_calls.json'),get('consensus13f.json'),get('investors.json')])
.then(([sig,tr,bk,cons,inv])=>{
 const names={};(inv||[]).forEach(i=>names[i.key]=i.name);
 const nm=k=>names[k]||k;
 box.replaceChildren();
 stars.forEach(t=>{
  const card=el('div',null,'my-card');const h=el('h3');const a=el('a',t);a.href=root+'ticker/'+t+'.html';h.appendChild(a);
  const rm=el('button','★ 取消','star on');rm.addEventListener('click',()=>{save(load().filter(x=>x!==t));card.remove();});
  h.appendChild(rm);card.appendChild(h);
  const newest=(a,b)=>String(b.date||b.traded||'').localeCompare(String(a.date||a.traded||''));
  const add=(title,items)=>{if(!items.length)return;card.appendChild(el('div',title,'sub'));const ul=el('ul');items.slice(0,5).forEach(x=>ul.appendChild(el('li',x)));card.appendChild(ul);};
  const side={buy:'買進',sell:'賣出',stake:'持股逾5%',report:'提交申報'};
  add('實際交易（申報）',tr.filter(x=>x.ticker===t).sort(newest).map(x=>`${x.traded} ${nm(x.investor)} ${side[x.side]||x.side} ${x.amount||''}`));
  add('大神看法',sig.filter(x=>x.ticker===t&&x.own_view).sort(newest).map(x=>`${x.date} ${nm(x.investor)} ${x.stance==='bullish'?'看多':x.stance==='bearish'?'看空':'中性'}`));
  const act={upgrade:'升評',downgrade:'降評',initiate:'首評',raise_pt:'調高目標價',cut_pt:'調降目標價',reiterate:'維持'};
  add('投行評等',bk.filter(x=>x.ticker===t).sort(newest).map(x=>`${x.date} ${x.bank_name||x.bank} ${act[x.action]||x.action}${x.target?' 目標 $'+x.target:''}`));
  const c=cons.find(x=>x.ticker===t);
  if(c&&Object.keys(c.owners||{}).length)add('13F 持有的大神',[Object.keys(c.owners).map(nm).join('、')]);
  if(card.children.length===1)card.appendChild(el('p','最近沒有相關動態。','sub'));
  box.appendChild(card);
 });
});
})();
"""


def render_my() -> str:
    body = ('<h1>我的追蹤</h1><p class="sub">追蹤清單存在這支手機（或這台電腦）的瀏覽器裡，不會上傳。'
            '想要「有人動到我的股票就通知」，在 GitHub Secrets 設定 <code>SPIDERHEAD_MY_TICKERS</code>（例如 NVDA,TSLA）。</p>'
            '<div id="my-list" data-root=""><p class="empty">載入中…（需要用網址開啟，直接開檔案時看不到）</p></div>')
    return _page("我的追蹤 · Smartmoney Radar", body, "", "my.html")


def render_more() -> str:
    """Phone menu: every page as a big button, plus how to install and get notifications."""
    desc = {"index.html": "大神動態、產業熱度、評分卡", "my.html": "你加星號的股票", "macro.html": "利率、通膨、就業、VIX",
            "earnings.html": "營收、獲利、毛利率", "banks.html": "投行評等準不準、有沒有出貨",
            "consensus.html": "最多大神持有的股票", "clone.html": "照抄 13F 能不能贏大盤",
            "insiders.html": "多位內部人同時買進", "activist.html": "13D / 13G 大額持股",
            "retail.html": "散戶熱門股 vs 大神看法", "buzz.html": "誰最受關注", "weekly.html": "每週重點整理"}
    tiles = "".join(f'<a href="{href}">{esc(label)}<span>{esc(desc.get(href, ""))}</span></a>' for href, label in NAV)
    tiles += '<a href="data/index.json">開放資料<span>JSON / CSV / RSS</span></a>'
    guide = (
        '<h2>加到 iPhone 主畫面（像 App 一樣用）</h2><ol>'
        '<li>用 <strong>Safari</strong> 打開這個網站（其他瀏覽器在 iPhone 上不能安裝）。</li>'
        '<li>點下方的<strong>分享</strong>按鈕（方框加向上箭頭）。</li>'
        '<li>往下滑，點<strong>加入主畫面</strong>，再點<strong>新增</strong>。</li>'
        '<li>之後從主畫面的「大神雷達」圖示開啟：全螢幕、底部分頁列，沒網路時也能看上次的資料。</li></ol>'
        '<h2>手機收通知</h2><p>每天早上會推送圖卡＋文字摘要，有新交易或群買時即時通知。'
        '推播走 <strong>Telegram</strong> 或 <strong>LINE</strong>（都免費），設定方式見 repo 的 docs/SETUP.md。</p>')
    body = f'<h1>更多</h1><div class="more-grid">{tiles}</div>{guide}'
    return _page("更多 · Smartmoney Radar", body, "", "more.html")


def render_weekly(store: Store) -> str:
    from .weekly import markdown_to_html
    reps = store.reports()
    if not reps:
        body = '<h1>週報</h1><p class="empty">還沒有週報。執行 <code>spiderhead weekly --llm</code>。</p>'
        return _page("週報 · Smartmoney Radar", body, "", "weekly.html")
    start, end, text, author = reps[0]
    by = "Claude 撰寫" if author == "claude" else "自動範本"
    archive = "".join(f'<details><summary>{s} ～ {e}</summary><div class="card">{markdown_to_html(t)}</div></details>'
                      for s, e, t, _ in reps[1:])
    body = (f'<p class="sub">{start} ～ {end} · {by}</p><div class="card">{markdown_to_html(text)}</div>'
            + (f"<h2>過去週報</h2>{archive}" if archive else ""))
    return _page("週報 · Smartmoney Radar", body, "", "weekly.html")


def render_consensus(c, names: dict[str, str], store=None, investors=None, prices=None) -> str:
    if not c.quarters:
        body = ('<h1>大神共同持股</h1><p class="empty">還沒有 13F 歷史資料。執行 '
                '<code>spiderhead dashboard --with-13f</code> 下載最近 4 季後就會出現。</p>')
        return _page("大神共同持股 · Smartmoney Radar", body, "", "consensus.html")

    def table(rows, cols) -> str:
        if not rows:
            return '<p class="empty">沒有資料。</p>'
        head = "".join(f'<th{" class=num" if n else ""}>{esc(h)}</th>' for h, n, _ in cols)
        cls = lambda h, n: " class=num" if n else (" class=wrap" if h.startswith(("誰", "同時")) else "")  # noqa: E731
        body = "".join("<tr>" + "".join(f'<td{cls(h, n)}>{f(r)}</td>' for h, n, f in cols)
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
            f'<h2>本季最多大神賣出（減碼或清倉）</h2>{sold}'
            f'{discount_html(store, investors, names, prices)}')
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
        f'<td class="wrap">{esc(s.rationale[:120])}</td></tr>' for s in rows[:50])
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
    body = (f'<p class="sub"><a href="../index.html">← 總覽</a></p><header class="top"><h1>{esc(ticker)} '
            f'<button class="star" data-ticker="{esc(ticker)}" aria-pressed="false">☆ 加入追蹤</button></h1>'
            f'<span class="sub">{days} 天內被提到 {len(rows)} 次 · ▲ {bulls} / ▼ {bears} · '
            f'<a href="../feeds/ticker/{esc(_slug(ticker))}.xml">RSS 訂閱</a></span></header>'
            f'{chart}<h2>誰在談</h2>{talk}<h2>大神的實際交易（對帳單）</h2>'
            f'{trades_table(trades, names, "../")}'
            f'{_ticker_13f(consensus, ticker, names)}'
            f'{_ticker_financials(store, ticker)}'
            f'{_ticker_banks(store, ticker)}'
            f'{_ticker_shorts(store, ticker)}')
    return _page(f"{ticker} · Smartmoney Radar", body, "../")


def render_index(store: Store, investors: dict[str, Investor], days: int, step: int,
                 recent_days: int = 7, calls: list[CallResult] | None = None, report_cards=None,
                 prices: PriceBook | None = None, tickers=None) -> str:
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
             f'<div class="tile"><div class="v">{len(recent.consensus)}</div><div class="l">多位大神同時在談的標的</div></div>'
             f'<div class="tile"><div class="v">{(next_13f_deadline()[1] - date.today()).days} 天</div>'
             f'<div class="l">距離下一次 13F 截止（{next_13f_deadline()[0]:%Y Q}{(next_13f_deadline()[0].month - 1) // 3 + 1}，'
             f'{next_13f_deadline()[1]}）</div></div></div>')

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
            f'<tr><td><strong>{esc(c.ticker)}</strong></td><td class="num">{c.investors}</td><td class="wrap">'
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
            f'{movers_html(prices, tickers)}'
            f'<h2>大神評分卡</h2>{reportcard_table(report_cards or {}, names)}'
            f'<h2>喊單勝率排行</h2>{scoreboard_table(calls or [], names)}'
            f'<h2>大神</h2><div class="grid">{"".join(inv_cards)}</div>'
            f'<h2>最新動態</h2>{_feed_html(recent.feed, names, "")}'
            f'<h2>資料來源狀態</h2>{health_table(store.source_health())}')
    return _page("Smartmoney Radar 大神追蹤", body, "", "index.html")


def render_investor(store: Store, inv: Investor, names: dict[str, str], days: int, step: int,
                    holdings: tuple[str, list[PositionChange], list[ConsistencyItem]] | None = None,
                    calls: list[CallResult] | None = None, clone=None, card=None) -> str:
    start, end = window(days)
    prof = investor_profile(store, inv, start, end, step)
    buckets, activity = prof.activity
    own = sum(1 for p, _ in prof.posts if p.kind == "own")

    links = [f'<a href="../feeds/investor/{esc(inv.key)}.xml">RSS 訂閱</a>']
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
            f'<div class="tile"><div class="v">{esc(PROOF.get(inv.proof, inv.proof))}</div><div class="l">對帳單</div></div>'
            f'<div class="tile"><div class="v">{card.grade if card else "—"}</div><div class="l">評分卡'
            f'{"（" + f"{card.score:.0f} 分" + "）" if card and card.score is not None else ""}</div></div></div>'
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
    from .clone import backtest_all
    from .consensus13f import build_consensus
    from .digest import display_names
    from .export import write_exports
    from .insiders import find_clusters
    from .pwa import write_pwa
    from .reportcard import build_cards

    out = Path(out_dir)
    for sub in ("assets", "investor", "ticker"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    step = step or (1 if days <= 31 else 7)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    names = display_names(investors)
    start, end = window(days)

    # Everything the pages share, computed once.
    calls = score_calls(store.signals_between(start - timedelta(days=270), end), prices) if prices else []
    clones = backtest_all(store, investors, prices) if prices is not None else []
    cards = build_cards(store, investors, calls, clones)
    consensus = build_consensus(store, investors)
    clusters = find_clusters(store.insider_buys_between(end - timedelta(days=13), end))
    tickers = {s.ticker for s in store.signals_between(start, end)
               if not s.ticker.startswith(("SECTOR:", "MACRO:"))}
    tickers |= {t.ticker for t in store.trades_between(start - timedelta(days=90), end) if t.ticker}
    tickers |= {r.key for r in consensus.most_owned(25) + consensus.most_bought() + consensus.most_sold()
                if r.key.isupper() and len(r.key) <= 6}
    tickers |= {c.ticker for c in clusters}
    tickers |= {c.ticker for c in store.bank_calls(end - timedelta(days=90), end)}
    tickers |= set(store.financial_tickers())

    def write(name: str, html: str) -> None:
        (out / name).write_text(html, encoding="utf-8")

    write("index.html", render_index(store, investors, days, step, calls=calls, report_cards=cards,
                                     prices=prices, tickers=tickers))
    write("consensus.html", render_consensus(consensus, names, store, investors, prices))
    write("insiders.html", render_insiders(store, prices=prices))
    write("retail.html", render_retail(store, names))
    write("weekly.html", render_weekly(store))
    write("buzz.html", render_buzz(store, investors, names))
    write("macro.html", render_macro(store, names))
    write("banks.html", render_banks(store, names, prices))
    write("earnings.html", render_earnings(store))
    write("more.html", render_more())
    write("my.html", render_my())
    write("activist.html", render_activist(store, names))
    write("clone.html", render_clone(clones, names, prices is not None))
    write_pwa(out, ["index.html", "more.html", "my.html", "macro.html", "earnings.html", "banks.html",
                    "consensus.html", "weekly.html"])
    clone_by = {r.investor: r for r in clones}
    for inv in investors.values():
        page = render_investor(store, inv, names, days, step, (holdings or {}).get(inv.key), calls,
                               clone_by.get(inv.key), cards.get(inv.key))
        (out / "investor" / f"{inv.key}.html").write_text(page, encoding="utf-8")
    for t in sorted(tickers):
        (out / "ticker" / f"{_slug(t)}.html").write_text(
            render_ticker(store, t, names, days, prices, consensus), encoding="utf-8")
    write_exports(store, investors, out, days, consensus=consensus, clusters=clusters, tickers=sorted(tickers))
    index = [[f"{t}", f"ticker/{_slug(t)}.html"] for t in sorted(tickers)]
    index += [[f"{inv.name}（{inv.fund}）", f"investor/{inv.key}.html"] for inv in investors.values()]
    (out / "assets" / "app.js").write_text(
        JS + "\nwindow.SH_INDEX=" + json.dumps(index, ensure_ascii=False) + ";\n" + SEARCH_JS + MY_JS + REGISTER_JS,
        encoding="utf-8")
    return out / "index.html"
