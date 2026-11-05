"""Retail chatter (Reddit) vs smart money.

Reddit serves public Atom feeds per subreddit (``/r/<sub>/new/.rss``), no API
key needed. Posts are stored with kind ``retail`` so they never mix into
investor trends. Tickers are only counted when written as cashtags or when an
all-caps word is a ticker we already know (watchlist calls, filings, 13F),
which avoids counting words like YOLO, DD or CEO.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date

from .models import Post, Signal, Stance
from .posts import BEARISH, BULLISH, CASHTAG, parse_rss
from .sectors import sector_for

DEFAULT_SUBS = ("wallstreetbets", "stocks", "investing")
FEED = "https://www.reddit.com/r/{sub}/new/.rss?limit=100"
WORD = re.compile(r"\b[A-Z]{2,5}\b")
NOT_TICKERS = {"CEO", "CFO", "DD", "YOLO", "IPO", "ETF", "USA", "USD", "GDP", "FED", "AI", "EPS", "ATH",
               "IMO", "TLDR", "FOMO", "SEC", "LOL", "WSB", "OTM", "ITM", "EOD", "PE", "API", "EV",
               # common English words that are also real tickers
               "A", "AN", "AM", "ARE", "AT", "BE", "BIG", "BY", "CAN", "CAR", "CASH", "DO", "EAT", "EVER",
               "FAST", "FOR", "FUN", "GO", "GOOD", "HAS", "HE", "HIGH", "HOME", "IT", "KEY", "LIVE", "LOVE",
               "LOW", "MAN", "MAIN", "NEW", "NICE", "NOW", "ON", "ONE", "OPEN", "OR", "OUT", "PLAY", "POST",
               "REAL", "RUN", "SAFE", "SEE", "SO", "TRUE", "TWO", "UP", "WELL", "ALL", "ANY", "BEST", "CASH",
               "JUST", "MOST", "PAY", "RIDE", "SAVE", "TELL", "VERY", "WANT", "YOU", "ME", "MY", "WE", "US"}

BULL_EXTRA = ("calls", "moon", "rocket", "🚀", "to the moon", "diamond hands", "long")
BEAR_EXTRA = ("puts", "rug", "dump", "bag holder", "bagholder", "short")


class RetailAnalyzer:
    def __init__(self, universe: Iterable[str]):
        self.universe = {t.upper() for t in universe} - NOT_TICKERS

    def analyze(self, post: Post) -> list[Signal]:
        text = post.text
        tickers = set(CASHTAG.findall(text))
        letters = [c for c in text if c.isalpha()]
        shouting = letters and sum(c.isupper() for c in letters) / len(letters) > 0.7
        if not shouting:                  # ALL-CAPS posts make every word look like a ticker
            tickers |= {w for w in WORD.findall(text) if w in self.universe}
        if not tickers:
            return []
        low = text.lower()
        bull = sum(low.count(w) for w in BULLISH + BULL_EXTRA)
        bear = sum(low.count(w) for w in BEARISH + BEAR_EXTRA)
        stance = Stance.BULLISH if bull > bear else Stance.BEARISH if bear > bull else Stance.NEUTRAL
        conf = 0.0 if stance is Stance.NEUTRAL else min(0.8, 0.3 + 0.1 * abs(bull - bear))
        return [Signal(t, stance, conf, f"retail bull={bull} bear={bear}", post, sector_for(t, text))
                for t in sorted(tickers)]


def collect_retail(get: Callable[[str, dict], bytes], subs: Iterable[str] = DEFAULT_SUBS,
                   health=None) -> list[Post]:
    out: list[Post] = []
    for sub in subs:
        try:
            posts = parse_rss(get(FEED.format(sub=sub), {}), f"retail:{sub}")
        except Exception as exc:
            if health is not None:
                health.record("retail", sub, False, 0, str(exc))
            continue
        if health is not None:
            health.record("retail", sub, True, len(posts))
        out += [Post(p.investor_key, p.published, p.text, "reddit", p.url, "retail") for p in posts]
    return out


@dataclass
class RetailRow:
    ticker: str
    mentions: int = 0
    bull: int = 0
    bear: int = 0
    guru_views: dict[str, str] = field(default_factory=dict)   # investor -> latest stance value

    @property
    def retail_mood(self) -> float:
        n = self.bull + self.bear
        return 0.0 if n == 0 else (self.bull - self.bear) / n

    @property
    def guru_mood(self) -> float | None:
        vals = [1 if v == "bullish" else -1 for v in self.guru_views.values() if v != "neutral"]
        return sum(vals) / len(vals) if vals else None

    @property
    def divergent(self) -> bool:
        g = self.guru_mood
        return g is not None and abs(self.retail_mood) >= 0.3 and g * self.retail_mood < 0


def retail_vs_gurus(store, start: date, end: date, top: int = 30) -> list[RetailRow]:
    rows: dict[str, RetailRow] = {}
    seen: set[tuple[str, str]] = set()
    for s in store.signals_between(start, end, kinds=("retail",)):
        if (s.ticker, s.post.uid) in seen:
            continue
        seen.add((s.ticker, s.post.uid))
        r = rows.setdefault(s.ticker, RetailRow(s.ticker))
        r.mentions += 1
        r.bull += s.stance is Stance.BULLISH
        r.bear += s.stance is Stance.BEARISH
    latest: dict[tuple[str, str], tuple[date, str]] = {}
    for s in store.signals_between(start, end):
        if s.ticker in rows and s.is_own_view and s.stance is not Stance.NEUTRAL:
            k = (s.ticker, s.post.investor_key)
            if k not in latest or s.post.published >= latest[k][0]:
                latest[k] = (s.post.published, s.stance.value)
    for (t, inv), (_, v) in latest.items():
        rows[t].guru_views[inv] = v
    return sorted(rows.values(), key=lambda r: -r.mentions)[:top]


def known_tickers(store, start: date, end: date) -> set[str]:
    """Tickers seen in investor signals, filings and 13F — the 'universe' for retail matching."""
    out = {s.ticker for s in store.signals_between(start, end)
           if not s.ticker.startswith(("SECTOR:", "MACRO:"))}
    out |= {t.ticker for t in store.trades_between(start, end) if t.ticker}
    out |= {r[0] for r in store.db.execute("SELECT DISTINCT ticker FROM holdings_13f WHERE ticker IS NOT NULL")}
    from .sectors import TICKER_SECTOR
    return out | set(TICKER_SECTOR)
