"""Daily summary card: one tall PNG that reads well on a phone.

Rendered with matplotlib (``pip install -e ".[charts]"``). Chinese text needs a
CJK font: GitHub Actions installs ``fonts-noto-cjk``; on a Mac/PC the system
fonts (PingFang TC / Microsoft JhengHei) are picked up automatically.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .digest import Digest
from .sectors import SECTORS

FONTS = ["Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans TC", "PingFang TC", "Heiti TC",
         "Microsoft JhengHei", "WenQuanYi Zen Hei", "Arial Unicode MS"]
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
SURFACE, PAGE, SERIES = "#fcfcfb", "#f9f9f7", "#2a78d6"
GOOD, BAD = "#006300", "#d03b3b"


def compact_amount(text: str) -> str:
    """'$1,000,001 - $5,000,000（配偶）' -> '$1M–5M'; other text is returned shortened."""
    import re
    nums = [float(n.replace(",", "")) for n in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", text)]

    def short(v: float) -> str:
        for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if v >= div:
                return f"{v / div:,.0f}{unit}" if v % div < div * 0.05 or v / div >= 10 else f"{v / div:,.1f}{unit}"
        return f"{v:,.0f}"
    if len(nums) >= 2:
        return f"${short(nums[0])}–{short(nums[1])}"
    if len(nums) == 1:
        return f"${short(nums[0])}"
    return text.split("（")[0][:16]


def _font() -> str | None:
    from matplotlib import font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    return next((f for f in FONTS if f in have), None)


def render_card(d: Digest, store, path: str | Path, title_date: date | None = None) -> Path | None:
    """Write the card; returns the path, or None when matplotlib isn't installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    from .banks import BANK_BY_KEY
    from .macro import load_views

    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    font = _font()
    if font:
        plt.rcParams["font.family"] = [font]
    plt.rcParams["axes.unicode_minus"] = False
    today = title_date or d.end

    W, H = 10.8, 19.2                      # 1080 x 1920 px at dpi=100
    fig = plt.figure(figsize=(W, H), dpi=100, facecolor=PAGE)

    def text(x, y, s, size=20, color=INK, weight="normal", ha="left"):
        fig.text(x, y, s, fontsize=size, color=color, weight=weight, ha=ha, va="top")

    text(0.05, 0.975, "Smartmoney Radar 每日快報", 34, weight="bold")
    text(0.05, 0.945, f"{today:%Y-%m-%d}　大神動態 · 實際交易 · 投行 · 總經", 18, INK2)

    # 1) Macro mini charts (2 x 2).
    views = {m.series.sid: m for m in load_views(store, years=1, today=today)}
    picks = [("SP500", "S&P 500"), ("DGS10", "10 年期殖利率"), ("VIXCLS", "VIX"), ("CPIAUCSL", "CPI 年增")]
    y0 = 0.905
    text(0.05, y0, "總經", 24, weight="bold")
    for i, (sid, label) in enumerate(picks):
        m = views.get(sid)
        col, row = i % 2, i // 2
        left, bottom = 0.05 + col * 0.47, y0 - 0.115 - row * 0.105
        ax = fig.add_axes([left, bottom, 0.42, 0.06], facecolor=SURFACE)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])
        if m and len(m.points) > 1:
            xs = list(range(len(m.points)))
            ys = [v for _, v in m.points]
            ax.plot(xs, ys, color=SERIES, linewidth=2)
            ax.fill_between(xs, ys, min(ys), color=SERIES, alpha=0.08)
            last = m.points[-1][1]
            chg = m.change(30)
            fig.text(left, bottom + 0.075, f"{label}", fontsize=16, color=INK2, va="bottom")
            fig.text(left + 0.42, bottom + 0.075,
                     f"{last:,.2f}" + ("" if chg is None else f"（1 月 {chg:+,.2f}）"),
                     fontsize=16, color=INK, ha="right", va="bottom")
        else:
            fig.text(left, bottom + 0.075, f"{label}：無資料", fontsize=16, color=MUTED, va="bottom")

    # 2) Sector heat (horizontal bars).
    y1 = 0.665
    text(0.05, y1, "產業熱度（大神提及＋新聞）", 24, weight="bold")
    trends = d.trends[:6]
    ax = fig.add_axes([0.30, y1 - 0.205, 0.62, 0.17], facecolor=PAGE)
    for s in ax.spines.values():
        s.set_visible(False)
    if trends:
        names = [SECTORS.get(t.sector, t.sector) for t in trends][::-1]
        vals = [t.total for t in trends][::-1]
        ax.barh(range(len(vals)), vals, color=SERIES, height=0.6)
        ax.set_yticks(range(len(vals)))
        ax.set_yticklabels(names, fontsize=16, color=INK)
        ax.tick_params(axis="y", length=0)
        ax.set_xticks([])
        for i, (v, t) in enumerate(zip(vals, trends[::-1])):
            mood = "偏多" if t.sentiment > 0.2 else "偏空" if t.sentiment < -0.2 else "中性"
            ax.text(v, i, f"  {v} 則 · {mood}", va="center", fontsize=14,
                    color=GOOD if mood == "偏多" else BAD if mood == "偏空" else INK2)
        ax.set_xlim(0, max(vals) * 1.6)
    else:
        ax.axis("off")
        ax.text(0, 0.5, "今天沒有資料", fontsize=16, color=MUTED)

    # 3) Tables: filed trades, bank calls, insider clusters.
    side = {"buy": ("買進", GOOD), "sell": ("賣出", BAD), "stake": ("持股逾5%", INK2),
            "report": ("提交申報", INK2), "exchange": ("交換", INK2)}
    act = {"upgrade": ("升評", GOOD), "downgrade": ("降評", BAD), "initiate": ("首評", INK2),
           "raise_pt": ("調高目標價", GOOD), "cut_pt": ("調降目標價", BAD), "reiterate": ("維持", INK2)}
    y = 0.43

    def section(title: str, rows: list[tuple[str, tuple[str, str], str, str]], empty: str):
        nonlocal y
        text(0.05, y, title, 24, weight="bold")
        y -= 0.035
        if not rows:
            text(0.05, y, empty, 16, MUTED)
            y -= 0.04
            return
        for who, (label, color), what, extra in rows:
            fig.add_artist(plt.Line2D([0.05, 0.95], [y + 0.006, y + 0.006], color=GRID, linewidth=1))
            text(0.05, y, who, 17)
            text(0.40, y, label, 17, color, weight="bold")
            text(0.60, y, what, 17, weight="bold")
            text(0.95, y, extra, 15, INK2, ha="right")
            y -= 0.03
        y -= 0.015

    trades = [(d.names.get(t.investor_key, t.investor_key)[:20], side.get(t.side, (t.side, INK2)),
               t.ticker or t.issuer[:10],
               compact_amount(t.amount_text) if t.amount_text else (f"{t.shares:,.0f} 股" if t.shares else ""))
              for t in d.trades[:5]]
    section("大神實際交易（申報）", trades, "今天沒有新的申報交易")
    banks = [((BANK_BY_KEY[c.bank].name.split(" ")[0] if c.bank in BANK_BY_KEY else c.bank),
              act.get(c.action, (c.action, INK2)), c.ticker, (f"目標 ${c.target:,.0f}" if c.target else (c.rating or "")))
             for c in d.bank_calls[:4]]
    section("投行評等", banks, "今天沒有新的投行評等")
    clusters = [(f"{len(c.insiders)} 位內部人", ("群買", GOOD), c.ticker, f"${c.value / 1e6:,.1f}M")
                for c in d.clusters[:3]]
    section("內部人群買（14 天）", clusters, "沒有符合條件的群買")

    text(0.05, 0.03, "資料：SEC、FRED、FINRA、眾議院、公開新聞　僅供研究參考，不構成投資建議", 13, MUTED)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, facecolor=PAGE)
    plt.close(fig)
    return out


def card_paths(site_dir: str | Path, day: date | None = None) -> tuple[Path, Path]:
    """(full image, small preview) paths under the site, named by date so caches never serve an old card."""
    day = day or date.today()
    base = Path(site_dir) / "img"
    return base / f"daily-{day:%Y%m%d}.png", base / f"daily-{day:%Y%m%d}-preview.png"


def write_preview(full: Path, preview: Path, width: int = 480) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(full) as im:
        h = int(im.height * width / im.width)
        im.convert("RGB").resize((width, h)).save(preview, optimize=True)
    return preview



