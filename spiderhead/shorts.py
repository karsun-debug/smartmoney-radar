"""Daily short-sale volume from FINRA Reg SHO files (public, no key).

``https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt`` is a
pipe-delimited file: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market.
Short volume / total volume is a rough daily gauge of short-selling pressure
(it counts market-maker hedging too, so read trends, not single days).
Only tickers we already track are stored, to keep the database small.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"


def parse_regsho(text: str, keep: set[str] | None = None) -> list[tuple[date, str, float, float]]:
    out = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split("|")
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        sym = parts[1].upper()
        if keep is not None and sym not in keep:
            continue
        try:
            day = date(int(parts[0][:4]), int(parts[0][4:6]), int(parts[0][6:8]))
            short, total = float(parts[2]), float(parts[4])
        except ValueError:
            continue
        if total > 0:
            out.append((day, sym, short, total))
    return out


def fetch_shorts(store, get: Callable[[str, dict], bytes], tickers: Iterable[str], days: int = 7,
                 today: date | None = None, health=None) -> int:
    today = today or date.today()
    keep = {t.upper() for t in tickers}
    stored = 0
    for n in range(days, 0, -1):
        d = today - timedelta(days=n)
        if d.weekday() >= 5 or store.short_day_done(d):
            continue
        try:
            rows = parse_regsho(get(URL.format(d=f"{d:%Y%m%d}"), {}).decode("utf-8", "replace"), keep)
        except Exception as exc:              # holidays 403/404, network
            if health is not None:
                health.record("shorts", str(d), False, 0, str(exc))
            continue
        store.add_short_volume(rows)
        store.mark_short_day(d)
        stored += len(rows)
        if health is not None:
            health.record("shorts", str(d), True, len(rows))
    return stored


def short_ratio(store, ticker: str, days: int = 5, today: date | None = None) -> tuple[float, list] | None:
    """Average short-volume ratio over the last ``days`` stored sessions, plus the rows."""
    rows = store.short_volume(ticker, (today or date.today()) - timedelta(days=days * 2 + 4))
    rows = rows[-days:]
    if not rows:
        return None
    return sum(s for _, s, _ in rows) / sum(t for _, _, t in rows), rows
