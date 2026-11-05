"""Company financials from SEC XBRL "company facts" (free, no key).

``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`` has every
number a company has tagged in its 10-Q/10-K filings. We turn that into a
clean quarterly table:

* Income-statement and cash-flow items are reported as quarter values,
  year-to-date values (6/9 months) and annual values. Quarter values are
  taken directly when present and otherwise derived by differencing
  cumulative values with the same start date (that is how Q4 = FY − 9M).
* Balance-sheet items are point-in-time; the value at each quarter end is used.

Plus "what others say": recent earnings-coverage headlines from Google News.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote_plus

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

FLOW = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
PER_SHARE = {"eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"]}
INSTANT = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "equity": ["StockholdersEquity"],
}
FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F")


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _dedupe(rows: Iterable[dict], key) -> dict:
    out: dict = {}
    for r in rows:
        if r.get("form") not in FORMS:
            continue
        k = key(r)
        if k not in out or r.get("filed", "") >= out[k].get("filed", ""):
            out[k] = r
    return out


def quarterly_flow(rows: list[dict]) -> dict[date, float]:
    """Quarter-end date -> value for a duration concept (handles YTD and annual reports)."""
    facts = _dedupe((r for r in rows if r.get("start")), lambda r: (r["start"], r["end"]))
    quarters: dict[date, float] = {}
    spans = []
    for (s, e), r in facts.items():
        days = (_d(e) - _d(s)).days
        spans.append((_d(s), _d(e), float(r["val"])))
        if 80 <= days <= 100:
            quarters[_d(e)] = float(r["val"])
    by_start: dict[date, list[tuple[date, float]]] = {}
    for s, e, v in spans:
        by_start.setdefault(s, []).append((e, v))
    for s, items in by_start.items():
        items.sort()
        for (e1, v1), (e2, v2) in zip(items, items[1:]):
            if 80 <= (e2 - e1).days <= 100 and e2 not in quarters:
                quarters[e2] = v2 - v1
    return dict(sorted(quarters.items()))


def instants(rows: list[dict]) -> dict[date, float]:
    facts = _dedupe((r for r in rows if not r.get("start")), lambda r: r["end"])
    return {_d(e): float(r["val"]) for e, r in sorted(facts.items())}


def _pick(facts: dict, names: list[str], unit: str, reader) -> dict[date, float]:
    best: dict[date, float] = {}
    for name in names:
        units = facts.get("us-gaap", {}).get(name, {}).get("units", {})
        rows = units.get(unit) or []
        got = reader(rows)
        if got and (not best or max(got) > max(best) or (max(got) == max(best) and len(got) > len(best))):
            best = got
    return best


@dataclass
class Quarter:
    end: date
    values: dict[str, float] = field(default_factory=dict)

    def get(self, k: str) -> float | None:
        return self.values.get(k)

    def margin(self, k: str) -> float | None:
        rev, v = self.get("revenue"), self.get(k)
        return None if not rev or v is None else v / rev * 100

    @property
    def fcf(self) -> float | None:
        o, c = self.get("ocf"), self.get("capex")
        return None if o is None else o - (c or 0)


def parse_company_facts(data: dict | bytes | str) -> list[Quarter]:
    if not isinstance(data, dict):
        data = json.loads(data)
    facts = data.get("facts", {})
    series: dict[str, dict[date, float]] = {}
    for k, names in FLOW.items():
        series[k] = _pick(facts, names, "USD", quarterly_flow)
    for k, names in PER_SHARE.items():
        series[k] = _pick(facts, names, "USD/shares", quarterly_flow)
    for k, names in INSTANT.items():
        series[k] = _pick(facts, names, "USD", instants)
    ends = sorted(series["revenue"] or series["net_income"])
    out = []
    for e in ends:
        q = Quarter(e)
        for k, s in series.items():
            if k in INSTANT:
                v = s.get(e)
            else:
                v = s.get(e)
            if v is not None:
                q.values[k] = v
        out.append(q)
    return out


def yoy(quarters: list[Quarter], q: Quarter, key: str) -> float | None:
    """Year-over-year % change of ``key`` vs the quarter ending ~1 year earlier."""
    prev = next((p for p in quarters if abs((q.end - p.end).days - 365) <= 20), None)
    a, b = q.get(key), prev.get(key) if prev else None
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b) * 100


@dataclass
class Snapshot:
    ticker: str
    name: str
    quarters: list[Quarter]

    @property
    def latest(self) -> Quarter | None:
        return self.quarters[-1] if self.quarters else None

    def growth(self, key: str) -> float | None:
        return yoy(self.quarters, self.latest, key) if self.latest else None


def fetch_financials(store, get_json: Callable[[str], bytes], ticker_ciks: dict[str, tuple[str, str]],
                     tickers: Iterable[str], max_per_run: int = 10, refresh_days: int = 7,
                     health=None) -> int:
    """Refresh the stalest ``max_per_run`` tickers not updated within ``refresh_days``."""
    due = []
    for t in dict.fromkeys(x.upper() for x in tickers):
        if t not in ticker_ciks:
            continue
        fetched = store.financials_fetched(t)
        if fetched is None or fetched <= date.today() - timedelta(days=refresh_days):
            due.append((fetched or date.min, t))
    done = 0
    for _, t in sorted(due)[:max_per_run]:
        cik, name = ticker_ciks[t]
        try:
            qs = parse_company_facts(get_json(FACTS_URL.format(cik=str(cik).zfill(10))))
        except Exception as exc:
            if health is not None:
                health.record("financials", t, False, 0, str(exc))
            continue
        store.save_financials(t, str(cik), name, qs)
        done += 1
        if health is not None:
            health.record("financials", t, True, len(qs))
    return done


def fetch_earnings_news(store, get: Callable[[str, dict], bytes], tickers: Iterable[str],
                        names: dict[str, str] | None = None, per_run: int = 10, health=None) -> int:
    """Earnings coverage headlines (other people's analysis) for a few tickers per run."""
    from .posts import parse_rss
    stored = 0
    names = names or {}
    for t in list(dict.fromkeys(tickers))[:per_run]:
        q = f'"{t}" earnings' + (f' OR "{names[t]}" earnings' if t in names else "")
        try:
            items = parse_rss(get(NEWS_RSS.format(q=quote_plus(q)), {}), f"ticker:{t}")
        except Exception as exc:
            if health is not None:
                health.record("earnings_news", t, False, 0, str(exc))
            continue
        rows = [(t, it.published, it.text.split("\n", 1)[0][:300], it.url) for it in items[:20]]
        stored += store.add_earnings_news(rows)
        if health is not None:
            health.record("earnings_news", t, True, len(rows))
    return stored


def focus_tickers(store, investors, limit: int = 60) -> list[str]:
    """Tickers worth analysing, most relevant first: what investors hold, call, trade or banks rate."""
    import os
    from .consensus13f import build_consensus
    end = date.today()
    out: list[str] = []
    out += [t.strip().upper() for t in os.environ.get("SPIDERHEAD_MY_TICKERS", "").split(",") if t.strip()]
    out += [r.key for r in build_consensus(store, investors).most_owned(25) if r.key.isupper() and len(r.key) <= 6]
    out += [s.ticker for s in store.signals_between(end - timedelta(days=90), end)
            if s.is_own_view and not s.ticker.startswith(("SECTOR:", "MACRO:"))]
    out += [t.ticker for t in store.trades_between(end - timedelta(days=90), end) if t.ticker]
    out += [c.ticker for c in store.bank_calls(end - timedelta(days=90), end)]
    clean = [t for t in dict.fromkeys(out) if t and t.replace(".", "").isalpha() and t not in ("BTC", "ETH", "SPY")]
    return clean[:limit]
