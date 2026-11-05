import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")

from spiderhead.holdings import parse_infotable  # noqa: E402
from spiderhead.mcp_server import build_server  # noqa: E402
from spiderhead.models import Post, Trade  # noqa: E402
from spiderhead.posts import KeywordAnalyzer  # noqa: E402
from spiderhead.store import Store  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TODAY = date.today()


def _payload(result, many=False):
    """Lists come back as one TextContent per element; dicts as a single one."""
    items = [json.loads(c.text) for c in result.content]
    return items if many else items[0]


def test_mcp_tools_end_to_end():
    store = Store(":memory:")
    store.add_posts([Post("ackman", TODAY, "We bought a new position in $GOOGL", "x", "https://x.com/1")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    store.add_trades([Trade("pelosi", TODAY - timedelta(days=20), TODAY, "buy", "PTR", "NVDA", "NVIDIA",
                            amount_text="$1,001 - $15,000")])
    store.save_13f("buffett", "2026-09-30", "a", parse_infotable((FIX / "curr_q.xml").read_bytes()),
                   lambda h: {"037833100": "AAPL"}.get(h.cusip))
    server = build_server(store)

    async def run():
        tools = {t.name for t in await server.list_tools()}
        assert {"list_investors", "recent_calls", "filed_trades", "holdings_13f", "consensus_13f",
                "insider_clusters", "ticker_overview", "latest_weekly_report"} <= tools
        calls = _payload(await server.call_tool("recent_calls", {"investor": "Ackman", "days": 7}), many=True)
        assert calls[0]["ticker"] == "GOOGL" and calls[0]["investor"] == "Bill Ackman"
        trades = _payload(await server.call_tool("filed_trades", {"investor": "pelosi"}), many=True)
        assert trades[0]["ticker"] == "NVDA"
        h = _payload(await server.call_tool("holdings_13f", {"investor": "buffett", "top": 2}))
        assert h["holdings"][0]["ticker"] == "AAPL" and len(h["holdings"]) == 2
        ov = _payload(await server.call_tool("ticker_overview", {"ticker": "nvda"}))
        assert ov["ticker"] == "NVDA" and ov["filed_trades"]
        with pytest.raises(Exception, match="unknown investor"):
            await server.call_tool("recent_calls", {"investor": "nobody"})
    asyncio.run(run())
