"""Industry taxonomy and an offline keyword classifier.

The keys double as the ``sector`` enum the Claude analyzer must choose from,
so trends from both analyzers line up.
"""
from __future__ import annotations

import re

SECTORS: dict[str, str] = {
    "ai_semis": "AI / 半導體",
    "software": "軟體 / 雲端",
    "internet": "網路 / 社群 / 電商",
    "financials": "金融 / 銀行 / 保險",
    "energy": "能源 / 石油",
    "healthcare": "醫療 / 生技",
    "consumer": "消費 / 零售",
    "industrials": "工業 / 國防 / 航太",
    "real_estate": "房地產",
    "crypto": "加密貨幣",
    "macro": "總經 / 利率 / 匯率",
    "other": "其他",
}

KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai_semis": ("ai", "artificial intelligence", "gpu", "chip", "semiconductor", "nvidia",
                 "tsmc", "data center", "llm", "foundry"),
    "software": ("software", "saas", "cloud", "cybersecurity", "enterprise software"),
    "internet": ("google", "alphabet", "meta", "amazon", "e-commerce", "social media",
                 "advertising", "streaming"),
    "financials": ("bank", "insurance", "insurer", "lender", "credit card", "brokerage",
                   "private credit", "regional bank"),
    "energy": ("oil", "natural gas", "lng", "opec", "crude", "energy", "pipeline", "uranium"),
    "healthcare": ("pharma", "biotech", "drug", "fda", "glp-1", "healthcare", "hospital"),
    "consumer": ("consumer", "retail", "restaurant", "beverage", "apparel", "luxury"),
    "industrials": ("railroad", "airline", "defense", "aerospace", "manufacturing",
                    "infrastructure", "industrial"),
    "real_estate": ("real estate", "reit", "housing", "mortgage", "commercial property"),
    "crypto": ("bitcoin", "crypto", "ethereum", "stablecoin", "blockchain"),
    "macro": ("fed", "interest rate", "inflation", "treasury", "treasuries", "bond yield",
              "recession", "dollar", "tariff", "deficit", "gdp", "rate cut", "rate hike"),
}

# A handful of tickers whose sector is unambiguous; extend freely.
TICKER_SECTOR: dict[str, str] = {
    "NVDA": "ai_semis", "AMD": "ai_semis", "TSM": "ai_semis", "AVGO": "ai_semis",
    "INTC": "ai_semis", "ASML": "ai_semis", "MSFT": "software", "ORCL": "software",
    "CRM": "software", "GOOGL": "internet", "GOOG": "internet", "META": "internet",
    "AMZN": "internet", "AAPL": "consumer", "KO": "consumer", "CMG": "consumer",
    "BAC": "financials", "JPM": "financials", "CB": "financials", "AXP": "financials",
    "BRK.B": "financials", "OXY": "energy", "CVX": "energy", "XOM": "energy",
    "UNH": "healthcare", "LLY": "healthcare", "NVO": "healthcare", "BA": "industrials",
    "UNP": "industrials", "BABA": "internet", "JD": "internet", "PDD": "internet",
    "TSLA": "consumer", "UBER": "internet", "NFLX": "internet", "GLD": "macro", "SPY": "macro", "TLT": "macro", "MSTR": "crypto", "COIN": "crypto", "BTC": "crypto", "ETH": "crypto",
}

_PATTERNS = {s: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")(?:s|es)?\b", re.I)
             for s, kws in KEYWORDS.items()}


def classify_text(text: str) -> list[str]:
    """Sectors mentioned in ``text``, most mentions first (``["other"]`` if none)."""
    counts = {s: len(p.findall(text)) for s, p in _PATTERNS.items()}
    hits = sorted((s for s, n in counts.items() if n), key=lambda s: -counts[s])
    return hits or ["other"]


def sector_for(ticker: str, fallback_text: str = "") -> str:
    if ticker.startswith("MACRO:"):
        return "macro"
    return TICKER_SECTOR.get(ticker.upper()) or classify_text(fallback_text)[0]


def signal_label(ticker: str, sector: str) -> str:
    """Human label for a signal: the ticker, or the sector name for SECTOR:/MACRO: views."""
    if ticker.startswith("SECTOR:"):
        return SECTORS.get(sector, sector)
    if ticker.startswith("MACRO:"):
        return "總經·" + ticker.split(":", 1)[1].replace("_", " ").title()
    return ticker
