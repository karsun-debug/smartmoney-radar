"""MCP server: let an AI assistant (Claude Desktop, Claude Code, ...) query the database.

Run ``spiderhead mcp`` (stdio). Every tool is read-only and only returns what
is already stored by ``spiderhead fetch`` / ``insiders`` / ``dashboard --with-13f``.

Claude Code:   claude mcp add smartmoney -- spiderhead mcp
Claude Desktop (claude_desktop_config.json):
    {"mcpServers": {"smartmoney": {"command": "spiderhead", "args": ["mcp"],
                                   "env": {"SPIDERHEAD_DB": "/path/to/data/spiderhead.db"}}}}
"""
from __future__ import annotations

from datetime import date, timedelta

from .store import Store
from .watchlist import load_watchlist


def build_server(store: Store | None = None, watchlist_path: str | None = None):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    store = store or Store()
    investors = load_watchlist(watchlist_path)
    names = {k: v.name for k, v in investors.items()}
    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    server = MCPServer(
        name="smartmoney-radar",
        version=__import__("spiderhead").__version__,
        instructions=("Data about famous US investors: their public calls, trades they filed with "
                      "regulators (Form 4, 13D/G, 8-K, congressional PTRs, ARK daily), 13F holdings, "
                      "market-wide insider cluster buys and retail chatter. Investor keys come from "
                      "list_investors. Dates are YYYY-MM-DD. This is research data, not advice."),
    )

    def _since(days: int) -> tuple[date, date]:
        days = max(1, min(int(days), 3650))
        end = date.today()
        return end - timedelta(days=days - 1), end

    def _key(investor: str | None) -> str | None:
        if not investor:
            return None
        inv = investor.strip().lower()
        if inv in investors:
            return inv
        for k, v in investors.items():                   # allow "Pelosi", "Cathie Wood"
            if inv in v.name.lower():
                return k
        raise ToolError(f"unknown investor {investor!r}; call list_investors for valid keys")

    @server.tool(annotations=ro)
    def list_investors() -> list[dict]:
        """All tracked investors with their key, fund and how their trades are verified."""
        return [{"key": i.key, "name": i.name, "fund": i.fund, "proof": i.proof, "note": i.note}
                for i in investors.values()]

    @server.tool(annotations=ro)
    def recent_calls(investor: str | None = None, days: int = 30, own_only: bool = True) -> list[dict]:
        """Public stock/sector calls (bullish/bearish) extracted from posts and news.

        own_only keeps the investor's own words and news that quotes them."""
        start, end = _since(days)
        key = _key(investor)
        out = []
        for s in store.signals_between(start, end):
            if s.post.kind == "industry" or (key and s.post.investor_key != key):
                continue
            if own_only and not s.is_own_view:
                continue
            out.append({"investor": names.get(s.post.investor_key, s.post.investor_key),
                        "date": str(s.post.published), "ticker": s.ticker, "stance": s.stance.value,
                        "confidence": round(s.confidence, 2), "source": s.post.source, "url": s.post.url,
                        "text": s.post.text[:300]})
        return sorted(out, key=lambda r: r["date"], reverse=True)[:200]

    @server.tool(annotations=ro)
    def filed_trades(investor: str | None = None, ticker: str | None = None, days: int = 90) -> list[dict]:
        """Trades disclosed in filings (Form 4, 13D/G, 8-K, congressional PTR, ARK daily), by filing date."""
        start, end = _since(days)
        rows = store.trades_between(start, end, _key(investor), ticker.upper() if ticker else None, by="filed")
        return [{"investor": names.get(t.investor_key, t.investor_key), "traded": str(t.traded),
                 "filed": str(t.filed), "side": t.side, "form": t.form, "ticker": t.ticker,
                 "issuer": t.issuer, "shares": t.shares, "price": t.price, "amount": t.amount_text,
                 "url": t.url} for t in rows[:300]]

    @server.tool(annotations=ro)
    def holdings_13f(investor: str, quarter: str | None = None, top: int = 25) -> dict:
        """An investor's 13F holdings for a stored quarter (latest by default), largest first."""
        key = _key(investor)
        quarters = store.quarters_13f(key)
        if not quarters:
            return {"investor": names.get(key, key), "quarters": [], "holdings": []}
        q = quarter or quarters[0]
        holdings, tickers = store.load_13f(key, q)
        total = sum(h.value_usd for h in holdings) or 1
        ranked = sorted(holdings, key=lambda h: -h.value_usd)[: max(1, min(top, 200))]
        return {"investor": names.get(key, key), "quarter": q, "quarters": quarters,
                "filed": str(store.filed_13f(key, q)),
                "holdings": [{"ticker": tickers.get(h.key), "issuer": h.issuer, "put_call": h.put_call,
                              "shares": h.shares, "value_usd": h.value_usd,
                              "weight_pct": round(h.value_usd / total * 100, 2)} for h in ranked]}

    @server.tool(annotations=ro)
    def consensus_13f(top: int = 20) -> dict:
        """Stocks most owned / most bought / most sold across investors' latest 13F."""
        from .consensus13f import build_consensus
        c = build_consensus(store, investors)

        def rows(rs):
            return [{"ticker": r.key, "issuer": r.issuer, "owners": {names.get(k, k): round(w * 100, 2)
                                                                      for k, w in r.owners.items()},
                     "bought": {names.get(k, k): v for k, v in r.bought.items()},
                     "sold": {names.get(k, k): v for k, v in r.sold.items()}} for r in rs]
        n = max(1, min(top, 100))
        return {"quarters": {names.get(k, k): q for k, q in c.quarters.items()},
                "most_owned": rows(c.most_owned(n)), "most_bought": rows(c.most_bought(n)),
                "most_sold": rows(c.most_sold(n))}

    @server.tool(annotations=ro)
    def insider_clusters(days: int = 14, min_insiders: int = 3) -> list[dict]:
        """Companies where several insiders bought shares on the open market recently (all of EDGAR)."""
        from .insiders import find_clusters
        start, end = _since(days)
        return [{"ticker": c.ticker, "issuer": c.issuer, "insiders": sorted(c.insiders),
                 "value_usd": round(c.value), "first": str(c.first), "last": str(c.last)}
                for c in find_clusters(store.insider_buys_between(start, end), max(2, min_insiders))]

    @server.tool(annotations=ro)
    def ticker_overview(ticker: str, days: int = 90) -> dict:
        """Everything known about one ticker: who talked about it, filed trades, 13F owners, retail mood."""
        from .consensus13f import build_consensus
        from .retail import retail_vs_gurus
        t = ticker.strip().upper()
        start, end = _since(days)
        calls = [{"investor": names.get(s.post.investor_key, s.post.investor_key), "date": str(s.post.published),
                  "stance": s.stance.value, "own_view": s.is_own_view}
                 for s in store.signals_between(start, end) if s.ticker == t]
        trades = filed_trades(ticker=t, days=days)
        row = build_consensus(store, investors).rows.get(t)
        retail = next((r for r in retail_vs_gurus(store, end - timedelta(days=6), end, top=500)
                       if r.ticker == t), None)
        return {"ticker": t, "calls": calls[:100], "filed_trades": trades,
                "owners_13f": {names.get(k, k): round(w * 100, 2) for k, w in (row.owners if row else {}).items()},
                "retail_7d": None if retail is None else {"mentions": retail.mentions,
                                                          "mood": round(retail.retail_mood, 2)}}

    @server.tool(annotations=ro)
    def latest_weekly_report() -> dict:
        """The most recent weekly briefing (Markdown, Traditional Chinese)."""
        reps = store.reports(1)
        if not reps:
            return {"available": False}
        s, e, text, author = reps[0]
        return {"available": True, "period": f"{s} ~ {e}", "author": author, "markdown": text}

    return server


def main(watchlist_path: str | None = None) -> None:
    build_server(watchlist_path=watchlist_path).run("stdio")
