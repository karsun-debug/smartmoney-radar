"""``spiderhead doctor``: check settings and every data source in one go."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

OK, WARN, FAIL = "✅", "⚠️", "❌"


@dataclass
class Check:
    status: str
    name: str
    detail: str


Getter = Callable[[str, dict], bytes]


def _try(name: str, fn: Callable[[], str], fail: str = FAIL) -> Check:
    try:
        return Check(OK, name, fn())
    except Exception as exc:
        return Check(fail, name, f"{type(exc).__name__}: {str(exc)[:160]}")


def run_checks(get: Getter, env: dict[str, str] | None = None) -> list[Check]:
    env = dict(os.environ if env is None else env)
    checks: list[Check] = []

    ua = env.get("SPIDERHEAD_USER_AGENT", "")
    if "@" in ua:
        checks.append(Check(OK, "SEC User-Agent", ua))
        checks.append(_try("SEC EDGAR", lambda: json.loads(get(
            "https://data.sec.gov/submissions/CIK0001067983.json",
            {"User-Agent": ua}))["name"] + " 連線正常"))
    else:
        checks.append(Check(FAIL, "SEC User-Agent",
                            "請設定 SPIDERHEAD_USER_AGENT，例如 'SpiderHead you@example.com'（SEC 規定）"))

    token = env.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if token:
        def line() -> str:
            info = json.loads(get("https://api.line.me/v2/bot/info",
                                  {"Authorization": f"Bearer {token}"}))
            to = env.get("LINE_TO") or "（未設定 LINE_TO：會廣播給所有好友）"
            return f"官方帳號「{info.get('displayName', '?')}」({info.get('basicId', '')}) · 推給 {to}"
        checks.append(_try("LINE token", line))
    else:
        checks.append(Check(WARN, "LINE token", "未設定 LINE_CHANNEL_ACCESS_TOKEN，不會推播"))

    from .channels import configured_channels
    chans = [c.name for c in configured_channels(env)]
    if chans:
        checks.append(Check(OK, "通知管道", "、".join(chans)))
    else:
        checks.append(Check(WARN, "通知管道", "未設定任何管道，每日摘要與即時通知不會送出"))
    checks.append(_try("Google News", lambda: f"{len(get('https://news.google.com/rss/search?q=%22Bill+Ackman%22&hl=en-US&gl=US&ceid=US:en', {}))} bytes", WARN))
    def house() -> str:
        from datetime import date
        from .house import INDEX_URL, parse_index
        y = date.today().year
        rows = parse_index(get(INDEX_URL.format(year=y), {}), y)
        ptr = [r for r in rows if r.filing_type == "P" and r.filed]
        newest = max((r.filed for r in ptr), default=None)
        return f"{y} 年 {len(ptr)} 份交易申報，最新 {newest}"
    checks.append(_try("眾議院申報 (House Clerk)", house, WARN))
    checks.append(_congress_freshness(get))
    checks.append(_try("散戶論壇 (Reddit)", lambda: f"r/wallstreetbets {len(get('https://www.reddit.com/r/wallstreetbets/new/.rss?limit=5', {}))} bytes", WARN))
    from .ark import ARK_FUNDS
    checks.append(_try("ARK 每日持股", lambda: f"ARKK {len(get(ARK_FUNDS['ARKK'], {}))} bytes", WARN))
    checks.append(_try("股價 (Stooq)", lambda: (lambda b: "正常" if b.startswith(b"Date") else
                                              f"回應不是 CSV：{b[:60]!r}")(get("https://stooq.com/q/d/l/?s=aapl.us&i=d", {})), WARN))

    if env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"):
        checks.append(Check(OK, "Claude 分析", "已設定，可用 --llm"))
    else:
        checks.append(Check(WARN, "Claude 分析", "未設定 ANTHROPIC_API_KEY，將使用較粗糙的關鍵字分析"))
    if env.get("X_BEARER_TOKEN"):
        checks.append(_try("X API", lambda: json.loads(get(
            "https://api.x.com/2/users/by/username/BillAckman",
            {"Authorization": f"Bearer {env['X_BEARER_TOKEN']}"}))["data"]["id"] and "可讀取", WARN))
    else:
        checks.append(Check(WARN, "X API", "未設定 X_BEARER_TOKEN，改用新聞報導（不是本人原文）"))
    return checks


def _congress_freshness(get: Getter, max_age_days: int = 14) -> Check:
    from datetime import datetime, timezone
    name = "國會交易資料 (LuxAlgo)"
    try:
        m = json.loads(get("https://raw.githubusercontent.com/LuxAlgo/market-trackers-data/main/manifest.json", {}))
        d = m["datasets"]["congress-trades"]
        last = datetime.fromisoformat(d["lastIngestedAt"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - last).days
        detail = f"{d.get('rows', '?')} 筆，最後更新 {last:%Y-%m-%d}（{age} 天前）"
        if age > max_age_days:
            return Check(WARN, name, detail + "：資料集已久未更新，國會交易可能缺漏")
        return Check(OK, name, detail)
    except Exception as exc:
        return Check(WARN, name, f"{type(exc).__name__}: {str(exc)[:160]}")


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"{c.status} {c.name.ljust(width)}  {c.detail}" for c in checks]
    fails = sum(c.status == FAIL for c in checks)
    lines.append("")
    lines.append("全部就緒 🎉" if fails == 0 else f"有 {fails} 項必須修正（❌）；⚠️ 為選填或暫時連不上。")
    return "\n".join(lines)
