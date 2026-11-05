"""Timely "對帳單": Form 4, Schedule 13D/G and congressional trade disclosures.

13F only shows quarter-end snapshots up to 135 days late. These filings land
within days of a trade:

* Form 4 — insiders and >10% owners, within 2 business days (e.g. Berkshire,
  Pershing Square when they own >10% of a company).
* Schedule 13D/13G — crossing 5% of a company; 13D within 5 business days.
* Congress Periodic Transaction Reports (STOCK Act), within 45 days — read from
  LuxAlgo's public CC0 dataset (github.com/LuxAlgo/market-trackers-data),
  which parses Senate eFD and House Clerk filings.
"""
from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from datetime import date, timedelta

from .models import Investor, Trade

FORM4_CODES = {"P": "buy", "S": "sell"}   # open-market purchase / sale; grants etc. ignored
FORMS_4 = ("4", "4/A")
FORMS_13DG = ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
              "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")
OWNER = {"self": "本人", "spouse": "配偶", "joint": "共同", "dependent": "受扶養人"}
CONGRESS_DATA = "https://raw.githubusercontent.com/LuxAlgo/market-trackers-data/main/congress/trades"


def _t(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    found = elem.find(path)
    if found is None:
        return None
    if found.text and found.text.strip():
        return found.text.strip()
    v = found.find("value")
    return v.text.strip() if v is not None and v.text else None


def parse_form4(xml_text: str | bytes, investor_key: str, filed: date,
                url: str | None = None) -> list[Trade]:
    """Open-market buys/sells (codes P/S) from a Form 4 ownershipDocument."""
    root = ET.fromstring(xml_text)
    issuer = root.find("issuer")
    name = _t(issuer, "issuerName") or ""
    ticker = (_t(issuer, "issuerTradingSymbol") or "").upper() or None
    trades = []
    for tx in root.iter("nonDerivativeTransaction"):
        side = FORM4_CODES.get(_t(tx, "transactionCoding/transactionCode") or "")
        if not side:
            continue
        when = _t(tx, "transactionDate")
        shares = _t(tx, "transactionAmounts/transactionShares")
        price = _t(tx, "transactionAmounts/transactionPricePerShare")
        trades.append(Trade(
            investor_key, date.fromisoformat(when[:10]) if when else filed, filed, side, "4",
            ticker, name, float(shares) if shares else None,
            float(price) if price else None, "", url))
    return trades


SUBJECT_RE = re.compile(r"SUBJECT COMPANY:.*?COMPANY CONFORMED NAME:\s*(.+?)\s*$", re.S | re.M)


def parse_subject_company(header_text: str) -> str | None:
    """Subject company name from an EDGAR filing header (for 13D/G)."""
    m = SUBJECT_RE.search(header_text)
    return m.group(1).strip() if m else None


ASSET_KIND = {"ST": "", "OP": "選擇權", "OT": "其他", "GS": "政府債", "CS": "公司債", "PS": "特別股",
              "EF": "ETF", "MF": "基金", "CT": "加密貨幣"}


def clean_asset(desc: str) -> tuple[str, str, str, float | None]:
    """Split a disclosure asset string into (name, kind label, detail note, shares).

    House PTRs read like "Bloom Energy Corp (BE) [ST] F S: New D: Purchased 10,000 shares."
    after the PDF text extraction (which also leaves NUL bytes behind).
    """
    desc = " ".join(desc.replace("\x00", "").split())
    kind_m = re.search(r"\[([A-Z]{2})\]", desc)
    name = desc[:kind_m.start()].strip() if kind_m else desc
    detail_m = re.search(r"\bD\s*:\s*(.+)$", desc)
    detail = detail_m.group(1).strip() if detail_m else ""
    shares_m = re.search(r"([\d,]+(?:\.\d+)?)\s+shares", detail, re.I)
    shares = float(shares_m.group(1).replace(",", "")) if shares_m else None
    kind = ASSET_KIND.get(kind_m.group(1), kind_m.group(1)) if kind_m else ""
    return name, kind, detail, shares


def parse_congress_rows(rows: Iterable[dict], members: dict[str, str],
                        since: date | None = None) -> list[Trade]:
    """Convert LuxAlgo congress-trade rows for watched members into Trades.

    ``members`` maps a case-insensitive substring of the member's name
    (e.g. "pelosi") to our investor key.
    """
    out = []
    for r in rows:
        name = (r.get("member") or {}).get("name", "").lower()
        key = next((k for sub, k in members.items() if sub.lower() in name), None)
        if key is None:
            continue
        traded = date.fromisoformat(r["transactedAt"])
        if since and traded < since:
            continue
        rng = r.get("amountRange") or {}
        owner = r.get("owner")
        name, kind, detail, shares = clean_asset(r.get("assetDescription", ""))
        text = rng.get("text", "") + (f"（{OWNER.get(owner, owner)}）" if owner else "")
        if kind:
            text += f" · {kind}"
        if detail and kind:                      # options: keep strike / expiry note
            text += f"：{detail}"
        out.append(Trade(
            key, traded, date.fromisoformat(r["filedAt"]), r["side"], "PTR",
            (r.get("ticker") or None) and r["ticker"].upper(), name,
            None if kind else shares, None, text, (r.get("provenance") or {}).get("sourceUrl")))
    return out


HttpGet = Callable[[str, dict[str, str]], bytes]


class TradeCollector:
    """Collect recent trades for every watched investor; errors are collected."""

    def __init__(self, edgar=None, http_get: HttpGet | None = None, name_to_ticker=None):
        self.edgar = edgar
        self.get = http_get
        self.name_to_ticker = name_to_ticker or (lambda name: None)
        self.errors: list[str] = []

    def collect(self, investors: Iterable[Investor], days: int = 30) -> list[Trade]:
        since = date.today() - timedelta(days=days)
        trades: list[Trade] = []
        house, senate = {}, {}
        for inv in investors:
            if inv.handles.get("congress"):
                chamber = house if inv.handles.get("chamber", "house") == "house" else senate
                chamber[inv.handles["congress"]] = inv.key
            elif inv.cik and self.edgar is not None:
                try:
                    trades += self._sec(inv, since)
                except Exception as exc:
                    self.errors.append(f"sec:{inv.key}: {exc}")
        if house and self.get is not None:
            # Primary: the House Clerk itself. Fallback: LuxAlgo's parsed dataset.
            from .house import HouseCollector
            hc = HouseCollector(self.get)
            try:
                trades += hc.collect(house, since)
            except Exception as exc:
                self.errors.append(f"house clerk: {exc}; falling back to LuxAlgo dataset")
                senate.update(house)
            self.errors += hc.errors
        if senate and self.get is not None:
            try:
                trades += self._congress(senate, since)
            except Exception as exc:
                self.errors.append(f"congress: {exc}")
        return trades

    def _sec(self, inv: Investor, since: date) -> list[Trade]:
        out: list[Trade] = []
        for f in self.edgar.list_filings(inv.cik, FORMS_4 + FORMS_13DG, limit=40):
            if f.filing_date < since:
                continue
            url = self.edgar.filing_index_url(f)
            if f.form in FORMS_4:
                xml = self.edgar.fetch_primary_xml(f)
                out += parse_form4(xml, inv.key, f.filing_date, url)
            else:
                issuer = parse_subject_company(self.edgar.fetch_header(f)) or "?"
                out.append(Trade(inv.key, f.filing_date, f.filing_date, "stake", f.form,
                                 self.name_to_ticker(issuer), issuer, url=url))
        return out

    def _congress(self, members: dict[str, str], since: date) -> list[Trade]:
        rows: list[dict] = []
        years = sorted({since.year, date.today().year})
        for y in years:
            raw = self.get(f"{CONGRESS_DATA}/snapshot-{y}.json.gz", {})
            rows += json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        return parse_congress_rows(rows, members, since)
