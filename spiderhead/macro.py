"""Macro dashboard data from FRED (Federal Reserve Bank of St. Louis).

``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>`` serves any
series as CSV with no API key. Values are stored per series/day; transforms
(year-over-year for price indices) are applied when reading.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"


@dataclass(frozen=True)
class Series:
    sid: str
    name: str
    unit: str
    yoy: bool = False          # show year-over-year % change instead of the level
    note: str = ""


SERIES: list[Series] = [
    Series("SP500", "S&P 500 指數", "點", note="美股大盤"),
    Series("VIXCLS", "VIX 恐慌指數", "", note="> 25 偏恐慌、< 15 偏樂觀"),
    Series("DGS10", "10 年期美債殖利率", "%", note="長期利率，影響估值與房貸"),
    Series("DGS2", "2 年期美債殖利率", "%", note="反映市場對升降息的預期"),
    Series("T10Y2Y", "殖利率曲線（10 年 − 2 年）", "百分點", note="< 0 為倒掛，常被視為衰退前兆"),
    Series("DFF", "聯邦基金利率", "%", note="Fed 的政策利率"),
    Series("CPIAUCSL", "CPI 通膨（年增率）", "%", yoy=True, note="Fed 目標約 2%"),
    Series("PCEPILFE", "核心 PCE（年增率）", "%", yoy=True, note="Fed 最重視的通膨指標"),
    Series("UNRATE", "失業率", "%", note="快速上升常伴隨衰退"),
    Series("ICSA", "初領失業救濟金（週）", "人", note="就業市場的即時溫度計"),
    Series("BAMLH0A0HYM2", "高收益債利差", "百分點", note="> 5 代表信用市場有壓力"),
    Series("DTWEXBGS", "美元指數（廣義）", "", note="美元強弱"),
    Series("DCOILWTICO", "WTI 原油", "美元/桶"),
    Series("MORTGAGE30US", "30 年房貸利率", "%"),
    Series("M2SL", "M2 貨幣供給（年增率）", "%", yoy=True, note="市場資金多寡"),
]
BY_ID = {s.sid: s for s in SERIES}


def parse_fred_csv(text: str) -> list[tuple[date, float]]:
    rows = []
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    next(reader, None)                                   # header: observation_date (or DATE), SID
    for r in reader:
        if len(r) < 2 or r[1].strip() in ("", "."):
            continue
        try:
            rows.append((date.fromisoformat(r[0].strip()), float(r[1])))
        except ValueError:
            continue
    return rows


def fetch_macro(store, get: Callable[[str, dict], bytes], years: int = 6, today: date | None = None,
                health=None) -> int:
    start = (today or date.today()) - timedelta(days=365 * years)
    total = 0
    for s in SERIES:
        try:
            rows = parse_fred_csv(get(CSV_URL.format(sid=s.sid, start=start), {}).decode("utf-8", "replace"))
            if not rows:
                raise ValueError("no observations")
        except Exception as exc:
            if health is not None:
                health.record("macro", s.sid, False, 0, str(exc))
            continue
        store.add_macro(s.sid, rows)
        total += len(rows)
        if health is not None:
            health.record("macro", s.sid, True, len(rows))
    return total


def _yoy(rows: list[tuple[date, float]]) -> list[tuple[date, float]]:
    out = []
    by_day = dict(rows)
    days = [d for d, _ in rows]
    import bisect
    for d, v in rows:
        target = d - timedelta(days=365)
        i = bisect.bisect_right(days, target + timedelta(days=16)) - 1
        if i >= 0 and abs((days[i] - target).days) <= 16 and by_day[days[i]]:
            out.append((d, (v / by_day[days[i]] - 1) * 100))
    return out


@dataclass
class MacroView:
    series: Series
    points: list[tuple[date, float]]

    @property
    def latest(self) -> tuple[date, float] | None:
        return self.points[-1] if self.points else None

    def change(self, days: int) -> float | None:
        if not self.points:
            return None
        d0 = self.points[-1][0] - timedelta(days=days)
        before = [v for d, v in self.points if d <= d0]
        return None if not before else self.points[-1][1] - before[-1]


def load_views(store, years: int = 3, today: date | None = None) -> list[MacroView]:
    since = (today or date.today()) - timedelta(days=365 * (years + 1))
    out = []
    for s in SERIES:
        rows = store.macro(s.sid, since)
        pts = _yoy(rows) if s.yoy else rows
        cutoff = (today or date.today()) - timedelta(days=365 * years)
        out.append(MacroView(s, [(d, v) for d, v in pts if d >= cutoff]))
    return out


@dataclass(frozen=True)
class MacroSignal:
    name: str
    level: str        # ok / warn / alert
    text: str


def signals(views: list[MacroView]) -> list[MacroSignal]:
    v = {m.series.sid: m for m in views}
    out = []

    def latest(sid):
        m = v.get(sid)
        return m.latest[1] if m and m.latest else None
    curve = latest("T10Y2Y")
    if curve is not None:
        out.append(MacroSignal("殖利率曲線", "alert" if curve < 0 else "ok",
                               f"10 年 − 2 年 = {curve:+.2f}，" + ("倒掛中" if curve < 0 else "正常（未倒掛）")))
    un = v.get("UNRATE")
    if un and len(un.points) >= 15:
        vals = [x for _, x in un.points]
        avg3 = sum(vals[-3:]) / 3
        low12 = min(sum(vals[i - 2:i + 1]) / 3 for i in range(len(vals) - 14, len(vals) - 3))
        gap = avg3 - low12
        out.append(MacroSignal("薩姆規則（失業率）", "alert" if gap >= 0.5 else "warn" if gap >= 0.3 else "ok",
                               f"近 3 個月平均比過去一年低點高 {gap:.2f} 個百分點（≥ 0.5 歷史上常對應衰退）"))
    cpi = latest("CPIAUCSL")
    if cpi is not None:
        out.append(MacroSignal("通膨", "alert" if cpi >= 4 else "warn" if cpi >= 2.5 else "ok",
                               f"CPI 年增 {cpi:.1f}%（Fed 目標約 2%）"))
    vix = latest("VIXCLS")
    if vix is not None:
        out.append(MacroSignal("市場情緒（VIX）", "alert" if vix >= 30 else "warn" if vix >= 20 else "ok",
                               f"VIX {vix:.1f}" + ("，恐慌" if vix >= 30 else "，偏緊張" if vix >= 20 else "，平靜")))
    hy = latest("BAMLH0A0HYM2")
    if hy is not None:
        out.append(MacroSignal("信用市場", "alert" if hy >= 6 else "warn" if hy >= 4.5 else "ok",
                               f"高收益債利差 {hy:.2f} 個百分點"))
    d10, ff = latest("DGS10"), latest("DFF")
    if d10 is not None and cpi is not None:
        real = d10 - cpi
        out.append(MacroSignal("實質利率", "warn" if real >= 2 else "ok",
                               f"10 年殖利率 − CPI 年增 = {real:+.2f} 個百分點" + ("，偏緊" if real >= 2 else "")))
    if ff is not None and d10 is not None:
        out.append(MacroSignal("政策利率", "ok", f"聯邦基金利率 {ff:.2f}%、10 年期 {d10:.2f}%"))
    return out
