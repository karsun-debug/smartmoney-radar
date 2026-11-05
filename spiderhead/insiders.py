"""Market-wide insider cluster buys (the openinsider.com idea).

When three or more different insiders (directors, officers, 10% owners) buy
their own company's stock on the open market within a couple of weeks, it is
one of the better-documented bullish signals. This scans every Form 4 filed
on EDGAR, not just the watchlist:

1. Daily form index ``daily-index/{YYYY}/QTR{q}/form.{YYYYMMDD}.idx`` lists
   every filing of the day; Form 4 rows give the submission file path.
2. Each submission's embedded ``ownershipDocument`` XML is parsed for
   open-market purchases (transaction code ``P``).
3. Purchases are stored and grouped per ticker over a rolling window.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d}.idx"
FILE_URL = "https://www.sec.gov/Archives/{path}"
_XML_RE = re.compile(r"<XML>\s*(.*?)\s*</XML>", re.S | re.I)


@dataclass(frozen=True)
class InsiderBuy:
    accession: str
    ticker: str
    issuer: str
    owner: str
    title: str                 # "Director", "CEO", "10% Owner", ...
    traded: date
    filed: date
    shares: float
    price: float

    @property
    def value(self) -> float:
        return self.shares * self.price

    @property
    def url(self) -> str:
        return f"https://www.sec.gov/Archives/{self.accession}"


def parse_form_index(text: str, forms: tuple[str, ...] = ("4",)) -> list[tuple[str, str]]:
    """(form, file path) for wanted forms, de-duplicated by path."""
    seen, out = set(), []
    for line in text.splitlines():
        m = re.match(r"^(\S+(?: \S+)?)\s{2,}.*?\s(edgar/data/\S+\.txt)\s*$", line)
        if not m or m.group(1) not in forms or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        out.append((m.group(1), m.group(2)))
    return out


def _t(elem, path: str) -> str:
    found = elem.find(path) if elem is not None else None
    if found is None:
        return ""
    if found.text and found.text.strip():
        return found.text.strip()
    v = found.find("value")
    return (v.text or "").strip() if v is not None else ""


def _title(owner) -> str:
    rel = owner.find("reportingOwnerRelationship")
    if rel is None:
        return ""
    parts = []
    if _t(rel, "isOfficer") in ("1", "true"):
        parts.append(_t(rel, "officerTitle") or "Officer")
    if _t(rel, "isDirector") in ("1", "true"):
        parts.append("Director")
    if _t(rel, "isTenPercentOwner") in ("1", "true"):
        parts.append("10% Owner")
    return ", ".join(parts)


def parse_submission(text: str, path: str, filed: date) -> list[InsiderBuy]:
    """Open-market purchases from one Form 4 submission (.txt with embedded XML)."""
    m = _XML_RE.search(text)
    if not m:
        return []
    try:
        root = ET.fromstring(m.group(1).encode("utf-8"))
    except ET.ParseError:
        return []
    ticker = _t(root.find("issuer"), "issuerTradingSymbol").upper()
    issuer = _t(root.find("issuer"), "issuerName")
    owners = root.findall("reportingOwner")
    owner = _t(owners[0].find("reportingOwnerId"), "rptOwnerName") if owners else ""
    title = _title(owners[0]) if owners else ""
    if not ticker or ticker in ("NONE", "N/A"):
        return []
    out = []
    for tx in root.iter("nonDerivativeTransaction"):
        if _t(tx, "transactionCoding/transactionCode") != "P":
            continue
        try:
            shares = float(_t(tx, "transactionAmounts/transactionShares") or 0)
            price = float(_t(tx, "transactionAmounts/transactionPricePerShare") or 0)
            when = date.fromisoformat(_t(tx, "transactionDate")[:10])
        except ValueError:
            continue
        if shares > 0 and price > 0:
            out.append(InsiderBuy(path, ticker, issuer, owner, title, when, filed, shares, price))
    return out


@dataclass
class Cluster:
    ticker: str
    issuer: str
    buys: list[InsiderBuy] = field(default_factory=list)

    @property
    def insiders(self) -> set[str]:
        return {b.owner for b in self.buys}

    @property
    def value(self) -> float:
        return sum(b.value for b in self.buys)

    @property
    def first(self) -> date:
        return min(b.traded for b in self.buys)

    @property
    def last(self) -> date:
        return max(b.traded for b in self.buys)

    @property
    def uid(self) -> str:
        return f"cluster:{self.ticker}:{self.first}"


def find_clusters(buys: Iterable[InsiderBuy], min_insiders: int = 3,
                  min_value: float = 100_000) -> list[Cluster]:
    by: dict[str, Cluster] = {}
    for b in buys:
        by.setdefault(b.ticker, Cluster(b.ticker, b.issuer)).buys.append(b)
    out = [c for c in by.values() if len(c.insiders) >= min_insiders and c.value >= min_value]
    return sorted(out, key=lambda c: (-len(c.insiders), -c.value))


Getter = Callable[[str], str]


class InsiderScanner:
    """Walk EDGAR daily indexes and store open-market insider purchases."""

    def __init__(self, store, get_text: Getter, max_filings: int = 3000):
        self.store = store
        self.get = get_text
        self.max_filings = max_filings
        self.errors: list[str] = []

    def scan(self, days: int = 3, today: date | None = None) -> int:
        today = today or date.today()
        found = 0
        for n in range(days, -1, -1):
            d = today - timedelta(days=n)
            if d.weekday() >= 5 or self.store.insider_day_done(d):
                continue
            url = INDEX_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, d=f"{d:%Y%m%d}")
            try:
                rows = parse_form_index(self.get(url))
            except Exception as exc:          # holiday (404) or network
                self.errors.append(f"index {d}: {exc}")
                continue
            buys = []
            for _, path in rows[: self.max_filings]:
                try:
                    buys += parse_submission(self.get(FILE_URL.format(path=path)), path, d)
                except Exception as exc:
                    self.errors.append(f"{path}: {exc}")
            self.store.add_insider_buys(buys)
            if len(rows) <= self.max_filings:
                self.store.mark_insider_day(d)
            found += len(buys)
        return found
