"""Load investor posts and extract stock/macro views from them.

Two analyzers share one interface (``analyze(post) -> list[Signal]``):

* ``KeywordAnalyzer`` — offline, free, rough. Good for smoke tests.
* ``ClaudeAnalyzer`` — uses the Claude API with structured output to read
  sarcasm, hedging, and implicit references ("the Omaha railroad" -> BRK).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from .models import Post, Signal, Stance
from .sectors import SECTORS, classify_text, sector_for

# ---------------------------------------------------------------- loading


def load_posts_json(path: str | Path) -> list[Post]:
    """Load posts from a JSON array or JSONL file.

    Each record: {"investor": "ackman", "date": "2026-10-01", "text": "...",
    "source": "x", "url": "..."}. This is the hand-off format for any scraper
    or export (X API, newsletter, interview transcript).
    """
    raw = Path(path).read_text(encoding="utf-8").strip()
    records = json.loads(raw) if raw.startswith("[") else [
        json.loads(line) for line in raw.splitlines() if line.strip()]
    return [Post(investor_key=r["investor"], published=date.fromisoformat(r["date"][:10]),
                 text=r["text"], source=r.get("source", "manual"), url=r.get("url"),
                 kind=r.get("kind", "own"))
            for r in records]


def parse_rss(xml_text: str | bytes, investor_key: str) -> list[Post]:
    """Turn an RSS 2.0 / Atom feed (blog, Substack, YouTube channel) into posts."""
    root = ET.fromstring(xml_text)
    posts: list[Post] = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.rsplit("}", 1)[-1]: c for c in item}
        title = (fields["title"].text or "") if "title" in fields else ""
        body_el = _first(fields, "description", "summary", "content")
        body = re.sub(r"<[^>]+>", " ", body_el.text or "") if body_el is not None else ""
        when_el = _first(fields, "pubDate", "published", "updated")
        when = _parse_date(when_el.text if when_el is not None else None)
        link_el = fields.get("link")
        link = (link_el.get("href") or link_el.text) if link_el is not None else None
        posts.append(Post(investor_key, when, f"{title}\n{body}".strip(), "rss", link))
    return posts


def _first(fields: dict[str, ET.Element], *names: str) -> ET.Element | None:
    # Elements without children are falsy, so `a or b` would skip them.
    for n in names:
        if n in fields:
            return fields[n]
    return None


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


# ---------------------------------------------------------------- analyzers

CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")
BULLISH = ("buy", "bought", "long", "adding", "added", "undervalued", "cheap", "bullish",
           "accumulat", "increased our stake", "great business", "upside", "new position")
BEARISH = ("sell", "sold", "short", "exited", "overvalued", "bubble", "bearish", "crash",
           "trimmed", "reduced", "puts on", "bought puts", "avoid", "downside")


ATTRIB_VERBS = ("says", "said", "say", "buys", "bought", "buying", "sells", "sold", "selling",
                "bets", "betting", "warns", "warned", "calls", "called", "adds", "added", "adding",
                "dumps", "dumped", "exits", "exited", "trims", "trimmed", "slashes", "boosts",
                "shorts", "shorting", "predicts", "reveals", "discloses", "takes", "unveils",
                "likes", "loves", "is bullish", "is bearish", "thinks", "believes", "sees")


class KeywordAnalyzer:
    """Cashtag + keyword heuristic. ``aliases`` maps names to tickers, e.g. {"apple": "AAPL"}.

    ``names`` maps investor keys to full names; a news item counts as the
    investor's own view when it says "<surname> says/buys/sells/bets ...".
    """

    def __init__(self, aliases: dict[str, str] | None = None, names: dict[str, str] | None = None):
        self.aliases = {k.lower(): v.upper() for k, v in (aliases or {}).items()}
        if names is None:
            from .watchlist import DEFAULT_WATCHLIST
            names = {i.key: i.name for i in DEFAULT_WATCHLIST}
        self.surnames = {k: {k.lower(), v.split()[-1].lower()} for k, v in names.items()}
        verbs = "|".join(re.escape(v) for v in ATTRIB_VERBS)
        self._verbs = verbs

    def attributed(self, post: Post) -> bool:
        if post.kind == "own":
            return True
        if post.kind != "news":
            return False
        for sur in self.surnames.get(post.investor_key, {post.investor_key.lower()}):
            if re.search(rf"\b{re.escape(sur)}\b(?:'s)?[^.!?]{{0,60}}?\b(?:{self._verbs})\b",
                         post.text, re.I):
                return True
        return False

    def analyze(self, post: Post) -> list[Signal]:
        text = post.text
        low = text.lower()
        tickers = set(CASHTAG.findall(text))
        tickers |= {t for name, t in self.aliases.items()
                    if re.search(rf"\b{re.escape(name)}\b", low)}
        bull = sum(low.count(w) for w in BULLISH)
        bear = sum(low.count(w) for w in BEARISH)
        if bull > bear:
            stance = Stance.BULLISH
        elif bear > bull:
            stance = Stance.BEARISH
        else:
            stance = Stance.NEUTRAL
        conf = 0.0 if stance is Stance.NEUTRAL else min(0.9, 0.4 + 0.15 * abs(bull - bear))
        why = f"keywords bull={bull} bear={bear}"
        own = self.attributed(post) and post.kind == "news"
        signals = [Signal(t, stance, conf, why, post, sector_for(t, text), own)
                   for t in sorted(tickers)]
        # Industry-level views ("AI capex is a bubble") with no specific ticker.
        covered = {s.sector for s in signals}
        for sec in classify_text(text):
            if sec != "other" and sec not in covered:
                signals.append(Signal(f"SECTOR:{sec}", stance, conf * 0.8, why, post, sec, own))
        return signals


SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string",
                               "description": "US ticker in caps, MACRO:<theme> for macro views, "
                                              "or SECTOR:<sector> for industry-wide views"},
                    "sector": {"type": "string", "enum": list(SECTORS)},
                    "attributed": {"type": "boolean",
                                   "description": "True when this is the named investor's own view "
                                                  "or trade (their words, letter, interview, filing)"},
                    "stance": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string",
                                  "description": "One sentence, quoting the post where possible"},
                },
                "required": ["ticker", "sector", "stance", "confidence", "rationale", "attributed"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["signals"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You analyze public posts by professional investors for an \
investment-tracking tool. For each post, list every security or macro theme \
the author expresses a view on, with the direction of their view.

- Use US tickers (e.g. AAPL, BRK.B). Resolve company names and nicknames to tickers.
- Use MACRO:<theme> (e.g. MACRO:US_TREASURIES, MACRO:USD, MACRO:OIL) for views \
on rates, currencies, commodities or the market as a whole.
- Use SECTOR:<sector> (same key as the sector field) for views on a whole \
industry without a specific company, e.g. "AI capex is overdone" -> SECTOR:ai_semis.
- sector is the industry the security or theme belongs to.
- The text may be a news article *about* the investor rather than their own \
words; extract only views the article attributes to the investor (or, for \
industry news, the views the article reports).
- attributed is true only when the view is the investor's own (they said it, wrote \
it, or traded it); false for the journalist's or other people's opinions.
- stance is the author's own view, not the view they are quoting or mocking.
- confidence reflects how explicit the view is: 0.9 for "we bought X", \
0.3 for a vague hint. Hedged or ironic statements get lower confidence.
- If the post expresses no investment view, return an empty list."""


class ClaudeAnalyzer:
    """Extract signals with the Claude API (needs ``anthropic`` and credentials)."""

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "low", client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def analyze(self, post: Post) -> list[Signal]:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            # If a safety classifier declines, the API retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": SIGNAL_SCHEMA}},
            messages=[{"role": "user", "content":
                       f"Author: {post.investor_key}\nDate: {post.published}\n"
                       f"Source: {post.source} ({post.kind})\n\n<post>\n{post.text}\n</post>"}],
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return []
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text) if text else {"signals": []}
        return [Signal(_norm_ticker(s["ticker"]), Stance(s["stance"]),
                       max(0.0, min(1.0, float(s["confidence"]))), s["rationale"], post,
                       s["sector"] if s.get("sector") in SECTORS else "other",
                       bool(s.get("attributed")) and post.kind == "news")
                for s in data["signals"]]


def _norm_ticker(raw: str) -> str:
    prefix, sep, rest = raw.partition(":")
    if sep and prefix.upper() == "SECTOR":
        return f"SECTOR:{rest.lower()}"
    return raw.upper()
