"""Investment-bank analyst calls: what they say, whether it was right, and
whether the bank's own 13F moved the other way.

Sources (all free and public):

* Rating changes and price targets come from news headlines (Google News
  RSS), e.g. "Goldman Sachs upgrades Nvidia to Buy, raises price target to
  $200". Headlines are parsed with rules; anything ambiguous is dropped.
* Outcomes use daily prices (Stooq): direction after 30/90 days vs SPY, and
  whether the price target was reached within 12 months.
* Each bank's own 13F (only rows for stocks they made calls on) shows
  whether the firm's reported holdings went the same way as its research.

Caveat shown everywhere: a bank's 13F mixes client, market-making and asset-
management positions and research is walled off from trading, so a mismatch
is something to look at, not proof of wrongdoing.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote_plus

from .models import Investor


@dataclass(frozen=True)
class Bank:
    key: str
    name: str
    pattern: str            # regex matching the bank in a headline
    cik: str = ""           # 13F filer, for the "says vs holds" check

    def as_investor(self) -> Investor:
        return Investor(f"bank:{self.key}", self.name, self.name, self.cik, {}, "13F",
                        "投行 13F 包含客戶、造市與資產管理部位，不等於研究部門觀點。")


BANKS: list[Bank] = [
    Bank("goldman", "高盛 Goldman Sachs", r"goldman(?:\s+sachs)?", "0000886982"),
    Bank("morganstanley", "摩根士丹利 Morgan Stanley", r"morgan\s+stanley", "0000895421"),
    Bank("jpmorgan", "摩根大通 JPMorgan", r"j\.?\s?p\.?\s?morgan|jpmorgan", "0000019617"),
    Bank("bofa", "美國銀行 BofA", r"bank\s+of\s+america|\bbofa\b|\bb\s?of\s?a\b|merrill", "0000070858"),
    Bank("citi", "花旗 Citi", r"\bciti(?:group)?\b", "0000831001"),
    Bank("wellsfargo", "富國 Wells Fargo", r"wells\s+fargo", "0000072971"),
    Bank("ubs", "瑞銀 UBS", r"\bubs\b", "0001610520"),
    Bank("barclays", "巴克萊 Barclays", r"barclays", "0000312069"),
]
BANK_BY_KEY = {b.key: b for b in BANKS}
_BANK_RES = [(b, re.compile(b.pattern, re.I)) for b in BANKS]

NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

BULL_RATINGS = r"strong\s+buy|buy|outperform|overweight|positive|accumulate|add|market\s+outperform|sector\s+outperform"
BEAR_RATINGS = r"strong\s+sell|sell|underperform|underweight|reduce|negative|market\s+underperform|sector\s+underperform"
NEUTRAL_RATINGS = (r"neutral|hold|equal[\s-]?weight|market\s+perform|sector\s+perform|in[\s-]line|peer\s+perform|"
                   r"sector\s+weight")
_RATING = re.compile(rf"\b({BULL_RATINGS}|{BEAR_RATINGS}|{NEUTRAL_RATINGS})\b", re.I)

_ACTIONS = [
    ("upgrade", re.compile(r"\bupgrade[sd]?\b", re.I)),
    ("downgrade", re.compile(r"\bdowngrade[sd]?\b", re.I)),
    ("initiate", re.compile(r"\binitiat(?:es|ed|ing|e)\b|\bstarts?\s+coverage|\bbegins?\s+coverage", re.I)),
    ("raise_pt", re.compile(r"\b(?:raises?|raised|lifts?|lifted|boosts?|boosted|hikes?|hiked|ups)\b"
                            r"[^.;]{0,60}?\b(?:price\s+target|pt|target)\b", re.I)),
    ("cut_pt", re.compile(r"\b(?:cuts?|lowers?|lowered|trims?|trimmed|slashes|slashed|reduces?|reduced)\b"
                          r"[^.;]{0,60}?\b(?:price\s+target|pt|target)\b", re.I)),
    ("reiterate", re.compile(r"\b(?:reiterat\w+|maintain\w*|keeps?|reaffirm\w*)\b", re.I)),
]
_TARGET = re.compile(r"(?:price\s+target|\bpt\b|\btarget\b)[^$]{0,40}?\$\s?([\d,]+(?:\.\d+)?)", re.I)
_TARGET2 = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s+(?:price\s+target|target|pt)\b", re.I)
_FROM = re.compile(r"\bfrom\s+\$\s?([\d,]+(?:\.\d+)?)", re.I)
_PAREN_TICKER = re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|NYSEARCA|AMEX)?:?\s?([A-Z]{1,5}(?:\.[A-Z])?)\)")
_EXCH_TICKER = re.compile(r"\b(?:NYSE|NASDAQ|Nasdaq):\s?([A-Z]{1,5})\b")

# Brand names that differ from the SEC legal name.
ALIASES = {
    "google": "GOOGL", "alphabet": "GOOGL", "facebook": "META", "meta": "META", "meta platforms": "META",
    "amazon": "AMZN", "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "tesla": "TSLA",
    "netflix": "NFLX", "berkshire": "BRK.B", "berkshire hathaway": "BRK.B", "coca-cola": "KO",
    "coca cola": "KO", "pepsico": "PEP", "walmart": "WMT", "costco": "COST", "home depot": "HD",
    "disney": "DIS", "walt disney": "DIS", "intel": "INTC", "amd": "AMD", "advanced micro devices": "AMD",
    "broadcom": "AVGO", "oracle": "ORCL", "salesforce": "CRM", "adobe": "ADBE", "palantir": "PLTR",
    "jpmorgan": "JPM", "goldman": "GS", "morgan stanley": "MS", "bank of america": "BAC", "citigroup": "C",
    "wells fargo": "WFC", "exxon": "XOM", "exxon mobil": "XOM", "chevron": "CVX", "occidental": "OXY",
    "eli lilly": "LLY", "lilly": "LLY", "novo nordisk": "NVO", "pfizer": "PFE", "unitedhealth": "UNH",
    "boeing": "BA", "starbucks": "SBUX", "mcdonald's": "MCD", "mcdonalds": "MCD", "nike": "NKE",
    "uber": "UBER", "airbnb": "ABNB", "coinbase": "COIN", "micron": "MU", "qualcomm": "QCOM",
    "tsmc": "TSM", "taiwan semiconductor": "TSM", "alibaba": "BABA", "jd.com": "JD", "pdd": "PDD",
    "snowflake": "SNOW", "shopify": "SHOP", "paypal": "PYPL", "visa": "V", "mastercard": "MA",
    "american express": "AXP", "chubb": "CB", "chipotle": "CMG", "strategy": "MSTR", "microstrategy": "MSTR",
    "arm": "ARM", "super micro": "SMCI", "supermicro": "SMCI", "dell": "DELL", "ibm": "IBM", "cisco": "CSCO",
    "target": "TGT", "caterpillar": "CAT", "3m": "MMM", "ford": "F", "general motors": "GM", "gm": "GM",
    "rivian": "RIVN", "lucid": "LCID", "spotify": "SPOT", "roblox": "RBLX", "robinhood": "HOOD",
    "crowdstrike": "CRWD", "palo alto networks": "PANW", "servicenow": "NOW", "applied materials": "AMAT",
    "lam research": "LRCX", "asml": "ASML", "texas instruments": "TXN", "bloom energy": "BE",
}


@dataclass(frozen=True)
class BankCall:
    bank: str              # bank key
    published: date
    ticker: str
    action: str            # upgrade / downgrade / initiate / raise_pt / cut_pt / reiterate
    stance: str            # bullish / bearish / neutral
    rating: str | None
    target: float | None
    prior_target: float | None
    headline: str
    url: str | None


def _norm_company(s: str) -> str:
    s = re.sub(r"[^a-z0-9&.\-' ]", " ", s.lower())
    drop = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "company", "ltd", "plc", "holdings",
            "group", "the", "class", "a", "b", "common", "stock", "shares", "sa", "ag", "nv", "n.v.", "llc"}
    return " ".join(w for w in s.split() if w not in drop).strip()


class NameResolver:
    """Company name in a headline -> ticker (aliases first, then SEC legal names)."""

    def __init__(self, sec_names: dict[str, str] | None = None):
        self.exact = dict(ALIASES)
        for title, ticker in (sec_names or {}).items():
            n = _norm_company(title)
            if n and n not in self.exact:
                self.exact[n] = ticker.upper()

    def __call__(self, subject: str) -> str | None:
        m = _PAREN_TICKER.search(subject) or _EXCH_TICKER.search(subject)
        if m:
            return m.group(1).upper()
        n = _norm_company(subject)
        if not n:
            return None
        if n in self.exact:
            return self.exact[n]
        words = n.split()
        for k in range(len(words), 0, -1):              # longest prefix that is a known name
            cand = " ".join(words[:k])
            if cand in self.exact:
                return self.exact[cand]
        return None


def _stance_of(rating: str | None) -> str:
    if not rating:
        return "neutral"
    r = rating.lower()
    if re.fullmatch(BULL_RATINGS, r, re.I):
        return "bullish"
    if re.fullmatch(BEAR_RATINGS, r, re.I):
        return "bearish"
    return "neutral"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


_SUBJECT = re.compile(
    r"\b(?:upgrade[sd]?|downgrade[sd]?|initiat\w+(?:\s+coverage)?(?:\s+(?:on|of))?|starts?\s+coverage\s+(?:on|of)|"
    r"(?:raises?|raised|lifts?|boosts?|hikes?|cuts?|lowers?|lowered|trims?|slashes|reduces?)|"
    r"reiterat\w+|maintain\w*|keeps?)\s+(?:coverage\s+(?:on|of)\s+)?(.+?)"
    r"(?:'s|’s)?(?:\s+(?:to|at|with|price\s+target|pt|target|stock|shares|rating|on|as|amid|after|,)\b|[,;:\-–—]|$)",
    re.I)


def parse_headline(title: str, resolver: NameResolver, published: date, url: str | None = None) -> BankCall | None:
    bank = next((b for b, rx in _BANK_RES if rx.search(title)), None)
    if bank is None:
        return None
    action = next((name for name, rx in _ACTIONS if rx.search(title)), None)
    if action is None:
        return None
    # Subject: the company named right after the verb (headline minus the bank's name).
    rest = _BANK_RES[[b.key for b, _ in _BANK_RES].index(bank.key)][1].sub(" ", title)
    ticker = None
    m = _PAREN_TICKER.search(title) or _EXCH_TICKER.search(title)
    if m:
        ticker = m.group(1).upper()
    else:
        sm = _SUBJECT.search(rest)
        if sm:
            subj = re.sub(r"\b(?:price\s+target|pt|target)\b.*$", "", sm.group(1), flags=re.I).strip(" ,.'")
            ticker = resolver(subj)
    if not ticker:
        return None
    rating_m = None
    for rm in _RATING.finditer(title):
        before = title[max(0, rm.start() - 12):rm.start()].lower()
        if any(w in before for w in (" to ", " at ", " with ", "reiterat", "maintain", "keep", "as ")) or \
                rm.start() > len(title) * 0.4:
            rating_m = rm
    rating = rating_m.group(1).title() if rating_m else None
    stance = {"upgrade": "bullish", "downgrade": "bearish", "raise_pt": "bullish", "cut_pt": "bearish"}.get(action)
    if action in ("initiate", "reiterate"):
        stance = _stance_of(rating)
    if stance is None:
        stance = "neutral"
    tm = _TARGET.search(title) or _TARGET2.search(title)
    target = _num(tm.group(1)) if tm else None
    fm = _FROM.search(title)
    prior = _num(fm.group(1)) if fm and target else None
    if target is not None and not (0.5 <= target <= 100_000):
        target = None
    return BankCall(bank.key, published, ticker, action, stance, rating, target, prior, title.strip(), url)


def news_queries() -> list[tuple[str, str]]:
    """(label, query) pairs: one per bank plus generic analyst-action searches."""
    out = [(b.key, f'"{b.name.split(" ", 1)[1]}" (upgrades OR downgrades OR "price target" OR initiates)')
           for b in BANKS]
    out += [("generic", q) for q in ('analyst upgrades stock to buy', 'analyst downgrades stock',
                                    '"raises price target"', '"cuts price target"')]
    return out


def collect_bank_calls(get: Callable[[str, dict], bytes], resolver: NameResolver, days: int = 7,
                       today: date | None = None, health=None) -> list[BankCall]:
    from .posts import parse_rss
    today = today or date.today()
    since = today - timedelta(days=days)
    seen, out = set(), []
    for label, query in news_queries():
        try:
            items = parse_rss(get(NEWS_RSS.format(q=quote_plus(query)), {}), "bank")
        except Exception as exc:
            if health is not None:
                health.record("banks", label, False, 0, str(exc))
            continue
        n = 0
        for it in items:
            if it.published < since:
                continue
            title = it.text.split("\n", 1)[0]
            title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)          # drop " - Publisher"
            call = parse_headline(title, resolver, it.published, it.url)
            if call is None:
                continue
            key = (call.bank, call.ticker, call.published, call.action)
            if key in seen:
                continue
            seen.add(key)
            out.append(call)
            n += 1
        if health is not None:
            health.record("banks", label, True, n)
    return out


# ---------------------------------------------------------------- scoring

@dataclass
class CallOutcome:
    call: BankCall
    entry: float | None
    ret: dict[int, float | None] = field(default_factory=dict)        # % move after N days
    excess: dict[int, float | None] = field(default_factory=dict)     # vs SPY, direction-adjusted
    target_hit: bool | None = None                                     # None = not resolved yet
    upside_at_call: float | None = None                                # target / entry - 1, %

    def hit(self, h: int) -> bool | None:
        r = self.ret.get(h)
        if r is None or self.call.stance == "neutral":
            return None
        return r > 0 if self.call.stance == "bullish" else r < 0


def score_bank_calls(calls: Iterable[BankCall], prices, horizons=(30, 90),
                     today: date | None = None) -> list[CallOutcome]:
    today = today or date.today()
    out = []
    for c in calls:
        e = prices.close_on_or_after(c.ticker, c.published)
        o = CallOutcome(c, e[1] if e else None)
        if e:
            for h in horizons:
                t = c.published + timedelta(days=h)
                later = prices.close_on_or_before(c.ticker, t) if t <= today else None
                if later and later[0] > e[0]:
                    r = (later[1] / e[1] - 1) * 100
                    o.ret[h] = r
                    s0, s1 = prices.close_on_or_after("SPY", c.published), prices.close_on_or_before("SPY", t)
                    if s0 and s1 and s1[0] > s0[0] and c.stance != "neutral":
                        spy = (s1[1] / s0[1] - 1) * 100
                        o.excess[h] = (r - spy) * (1 if c.stance == "bullish" else -1)
            if c.target:
                o.upside_at_call = (c.target / e[1] - 1) * 100
                series = [(d, v) for d, v in prices.series(c.ticker)
                          if c.published <= d <= c.published + timedelta(days=365)]
                up = c.target >= e[1]
                reached = any((v >= c.target) if up else (v <= c.target) for _, v in series)
                if reached:
                    o.target_hit = True
                elif c.published + timedelta(days=365) <= today:
                    o.target_hit = False
        out.append(o)
    return out


@dataclass
class BankScore:
    bank: str
    calls: int = 0
    scored_90: int = 0
    hits_90: int = 0
    avg_excess_90: float | None = None
    targets_resolved: int = 0
    targets_hit: int = 0
    avg_upside: float | None = None

    @property
    def hit_rate(self) -> float | None:
        return self.hits_90 / self.scored_90 if self.scored_90 else None

    @property
    def target_rate(self) -> float | None:
        return self.targets_hit / self.targets_resolved if self.targets_resolved else None


def bank_scores(outcomes: Iterable[CallOutcome], horizon: int = 90) -> dict[str, BankScore]:
    by: dict[str, list[CallOutcome]] = {}
    for o in outcomes:
        by.setdefault(o.call.bank, []).append(o)
    out = {}
    for bank, os_ in by.items():
        s = BankScore(bank, len(os_))
        scored = [o for o in os_ if o.hit(horizon) is not None]
        s.scored_90 = len(scored)
        s.hits_90 = sum(1 for o in scored if o.hit(horizon))
        ex = [o.excess[horizon] for o in os_ if o.excess.get(horizon) is not None]
        s.avg_excess_90 = sum(ex) / len(ex) if ex else None
        res = [o for o in os_ if o.target_hit is not None]
        s.targets_resolved = len(res)
        s.targets_hit = sum(1 for o in res if o.target_hit)
        ups = [o.upside_at_call for o in os_ if o.upside_at_call is not None]
        s.avg_upside = sum(ups) / len(ups) if ups else None
        out[bank] = s
    return out


# ---------------------------------------------------------------- says vs holds

@dataclass(frozen=True)
class Mismatch:
    call: BankCall
    quarter: str
    change: str            # NEW / ADD / TRIM / EXIT
    shares_before: int
    shares_after: int


def _quarter_end(d: date) -> date:
    m = ((d.month - 1) // 3 + 1) * 3
    return date(d.year, m, 31 if m in (3, 12) else 30)


def says_vs_holds(store, calls: Iterable[BankCall]) -> list[Mismatch]:
    """Bullish call while the bank's 13F for that quarter shows a cut (or bearish while adding)."""
    from .holdings import diff_holdings
    out = []
    cache: dict[tuple[str, str], dict] = {}
    for c in calls:
        if c.stance == "neutral":
            continue
        inv = f"bank:{c.bank}"
        q = str(_quarter_end(c.published))
        quarters = store.quarters_13f(inv)
        if q not in quarters or quarters.index(q) + 1 >= len(quarters):
            continue
        if (inv, q) not in cache:
            prev_q = quarters[quarters.index(q) + 1]
            curr, ct = store.load_13f(inv, q)
            prev, pt = store.load_13f(inv, prev_q)
            tick = {**pt, **ct}
            cache[(inv, q)] = {ch.ticker: ch for ch in diff_holdings(prev, curr, lambda h: tick.get(h.key))
                               if ch.ticker and not ch.put_call}
        ch = cache[(inv, q)].get(c.ticker)
        if ch is None:
            continue
        bad = (c.stance == "bullish" and ch.change.value in ("TRIM", "EXIT")) or \
              (c.stance == "bearish" and ch.change.value in ("NEW", "ADD"))
        if bad:
            out.append(Mismatch(c, q, ch.change.value, ch.prev_shares, ch.curr_shares))
    return out
