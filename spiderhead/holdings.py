"""Parse 13F information tables and compute quarter-over-quarter changes."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .models import ChangeType, Holding, PositionChange


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem.iter():
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return None


def parse_infotable(xml_text: str | bytes) -> list[Holding]:
    """Parse a 13F-HR information table XML document.

    Rows for the same CUSIP and option type are summed (filers often split a
    position across managers / investment discretion). Values are in US
    dollars, which is what EDGAR has required since January 2023.
    """
    root = ET.fromstring(xml_text)
    merged: dict[tuple[str, str | None], Holding] = {}
    for row in root.iter():
        if _local(row.tag) != "infoTable":
            continue
        put_call = (_child_text(row, "putCall") or "").upper() or None
        h = Holding(
            issuer=_child_text(row, "nameOfIssuer") or "",
            cusip=(_child_text(row, "cusip") or "").upper(),
            title_of_class=_child_text(row, "titleOfClass") or "",
            value_usd=int(float(_child_text(row, "value") or 0)),
            shares=int(float(_child_text(row, "sshPrnamt") or 0)),
            share_type=_child_text(row, "sshPrnamtType") or "SH",
            put_call=put_call,
        )
        if h.key in merged:
            old = merged[h.key]
            h = Holding(old.issuer, old.cusip, old.title_of_class,
                        old.value_usd + h.value_usd, old.shares + h.shares,
                        old.share_type, old.put_call)
        merged[h.key] = h
    return list(merged.values())


def diff_holdings(
    prev: Iterable[Holding],
    curr: Iterable[Holding],
    resolve_ticker: Callable[[Holding], str | None] | None = None,
) -> list[PositionChange]:
    """Compare two quarters. Result is sorted by size of the move (USD)."""
    prev_map = {h.key: h for h in prev}
    curr_map = {h.key: h for h in curr}
    total = sum(h.value_usd for h in curr_map.values()) or 1

    changes: list[PositionChange] = []
    for key in prev_map.keys() | curr_map.keys():
        p, c = prev_map.get(key), curr_map.get(key)
        ref = c or p
        assert ref is not None
        ps, cs = (p.shares if p else 0), (c.shares if c else 0)
        if p is None:
            kind = ChangeType.NEW
        elif c is None:
            kind = ChangeType.EXIT
        elif cs > ps:
            kind = ChangeType.ADD
        elif cs < ps:
            kind = ChangeType.TRIM
        else:
            kind = ChangeType.HOLD
        changes.append(PositionChange(
            issuer=ref.issuer, cusip=ref.cusip, put_call=ref.put_call, change=kind,
            prev_shares=ps, curr_shares=cs,
            prev_value_usd=p.value_usd if p else 0,
            curr_value_usd=c.value_usd if c else 0,
            portfolio_weight=(c.value_usd / total) if c else 0.0,
            ticker=resolve_ticker(ref) if resolve_ticker else None,
        ))

    def magnitude(ch: PositionChange) -> float:
        # Estimate traded dollars using the current (or previous) price per share.
        price_ref = ch.curr_value_usd / ch.curr_shares if ch.curr_shares else (
            ch.prev_value_usd / ch.prev_shares if ch.prev_shares else 0)
        return abs(ch.curr_shares - ch.prev_shares) * price_ref

    changes.sort(key=magnitude, reverse=True)
    return changes


def _norm(name: str) -> str:
    drop = {"INC", "CORP", "LIMITED", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "HLDGS",
            "HOLDINGS", "GROUP", "THE", "CL", "CLASS", "A", "B", "NEW", "COM", "DEL"}
    words = "".join(ch if ch.isalnum() else " " for ch in name.upper()).split()
    return " ".join(w for w in words if w not in drop)


class TickerResolver:
    """Map 13F rows (which carry CUSIPs, not tickers) to tickers.

    Lookup order: explicit CUSIP map -> normalized issuer-name match against a
    name->ticker table (e.g. SEC's company_tickers.json, see ``EdgarClient``).
    """

    def __init__(self, cusip_map: dict[str, str] | None = None,
                 name_map: dict[str, str] | None = None,
                 figi: "OpenFigi | None" = None):
        self.cusip_map = {k.upper(): v.upper() for k, v in (cusip_map or {}).items()}
        self.name_map = {_norm(k): v.upper() for k, v in (name_map or {}).items()}
        self.figi = figi

    def __call__(self, h: Holding) -> str | None:
        if h.cusip in self.cusip_map:
            return self.cusip_map[h.cusip]
        by_name = self.name_map.get(_norm(h.issuer))
        if by_name:
            return by_name
        if self.figi is not None:
            ticker = self.figi.lookup(h.cusip)
            if ticker:
                self.cusip_map[h.cusip] = ticker
            return ticker
        return None

    def name(self, issuer: str) -> str | None:
        return self.name_map.get(_norm(issuer))


class OpenFigi:
    """CUSIP -> ticker through the free OpenFIGI mapping API (fallback for odd names).

    Keyless use is rate limited; set ``OPENFIGI_API_KEY`` for higher limits.
    """
    URL = "https://api.openfigi.com/v3/mapping"

    def __init__(self, http_post=None, api_key: str | None = None):
        import os
        self.post = http_post or self._post
        self.key = api_key or os.environ.get("OPENFIGI_API_KEY")
        self.failed = False

    def _post(self, url: str, headers: dict, body: bytes) -> bytes:
        import requests
        resp = requests.post(url, headers=headers, data=body, timeout=20)
        resp.raise_for_status()
        return resp.content

    def lookup(self, cusip: str) -> str | None:
        import json
        if self.failed:
            return None
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["X-OPENFIGI-APIKEY"] = self.key
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US"}]).encode()
        try:
            data = json.loads(self.post(self.URL, headers, body))
        except Exception:
            self.failed = True          # rate limited / offline: stop trying this run
            return None
        rows = (data[0] or {}).get("data") or [] if data else []
        return rows[0].get("ticker", "").upper() or None if rows else None


@dataclass(frozen=True)
class QuarterSummary:
    quarter: str
    total_value: int
    positions: int
    top: list[tuple[str, float]]      # (label, weight 0..1), largest first


def summarize_quarter(quarter: str, holdings: Iterable[Holding],
                      resolve: Callable[[Holding], str | None] | None = None,
                      top_n: int = 5) -> QuarterSummary:
    hs = [h for h in holdings if h.value_usd > 0]
    total = sum(h.value_usd for h in hs) or 1
    ranked = sorted(hs, key=lambda h: -h.value_usd)[:top_n]
    top = [((resolve(h) if resolve else None) or h.issuer) + (f" ({h.put_call})" if h.put_call else "")
           for h in ranked]
    return QuarterSummary(quarter, sum(h.value_usd for h in hs), len(hs),
                          [(label, h.value_usd / total) for label, h in zip(top, ranked)])
