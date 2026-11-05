"""Default watchlist of well-known US investors who file 13F reports.

CIKs are for the filing entity. Verify them on
https://www.sec.gov/edgar/search/ before relying on results — funds sometimes
re-register under a new CIK. Users can override or extend the list with a JSON
file (see ``load_watchlist``).
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Investor

DEFAULT_WATCHLIST: list[Investor] = [
    Investor("buffett", "Warren Buffett", "Berkshire Hathaway Inc", "0001067983",
             {"letters": "https://www.berkshirehathaway.com/letters/letters.html"}),
    Investor("ackman", "Bill Ackman", "Pershing Square Capital Management", "0001336528",
             {"x": "BillAckman"}),
    Investor("burry", "Michael Burry", "Scion Asset Management", "0001649339",
             {"x": "michaeljburry"}),
    Investor("druckenmiller", "Stanley Druckenmiller", "Duquesne Family Office", "0001536411"),
    Investor("dalio", "Ray Dalio", "Bridgewater Associates", "0001350694",
             {"x": "RayDalio"}),
    Investor("tepper", "David Tepper", "Appaloosa Management", "0001656456"),
    Investor("klarman", "Seth Klarman", "Baupost Group", "0001061768"),
    Investor("icahn", "Carl Icahn", "Icahn Carl C", "0000921669"),
    # Active posters. CIK left empty where we track only their words; add the
    # fund's CIK in your watchlist JSON to enable 13F comparison.
    Investor("wood", "Cathie Wood", "ARK Invest", "", {"x": "CathieDWood"}),
    Investor("marks", "Howard Marks", "Oaktree Capital", ""),
    Investor("chamath", "Chamath Palihapitiya", "Social Capital", "", {"x": "chamath"}),
]


def load_watchlist(path: str | Path | None = None) -> dict[str, Investor]:
    """Return investors keyed by ``Investor.key``.

    A JSON file, if given, is a list of objects with the Investor fields; its
    entries are added to (and override) the defaults.
    """
    investors = {inv.key: inv for inv in DEFAULT_WATCHLIST}
    if path:
        for raw in json.loads(Path(path).read_text(encoding="utf-8")):
            inv = Investor(
                key=raw["key"], name=raw["name"], fund=raw.get("fund", raw["name"]),
                cik=str(raw["cik"]).zfill(10) if raw.get("cik") else "", handles=raw.get("handles", {}),
            )
            investors[inv.key] = inv
    return investors
