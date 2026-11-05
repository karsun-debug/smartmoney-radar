"""Command line entry point: ``python -m spiderhead <command>``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .consistency import check, quarter_end
from .holdings import TickerResolver, diff_holdings, parse_infotable
from .posts import ClaudeAnalyzer, KeywordAnalyzer, load_posts_json
from .report import consistency_markdown, holdings_markdown
from .watchlist import load_watchlist


def _changes_from_edgar(investor, resolver_from_sec: bool):
    from .edgar import EdgarClient
    client = EdgarClient()
    filings = client.latest_13f_per_quarter(investor.cik, quarters=2)
    if len(filings) < 2:
        raise SystemExit(f"{investor.key}: fewer than two 13F filings found")
    curr_f, prev_f = filings
    curr = parse_infotable(client.fetch_infotable_xml(curr_f))
    prev = parse_infotable(client.fetch_infotable_xml(prev_f))
    resolver = TickerResolver(name_map=client.company_name_map() if resolver_from_sec else None)
    return str(curr_f.report_date), diff_holdings(prev, curr, resolver)


def _changes_from_files(prev_path: str, curr_path: str, quarter: str, tickers: str | None):
    prev = parse_infotable(Path(prev_path).read_bytes())
    curr = parse_infotable(Path(curr_path).read_bytes())
    return quarter, diff_holdings(prev, curr, _resolver_from_file(tickers))


def _resolver_from_file(path: str | None) -> TickerResolver:
    """JSON {"037833100": "AAPL", "Coca Cola Co": "KO"}: 9-char keys are CUSIPs."""
    if not path:
        return TickerResolver()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cusips = {k: v for k, v in raw.items() if len(k) == 9 and k.isalnum()}
    names = {k: v for k, v in raw.items() if k not in cusips}
    return TickerResolver(cusips, names)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="spiderhead", description="追蹤投資大神的言論與實際持倉")
    ap.add_argument("--watchlist", help="JSON file adding/overriding investors")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出追蹤名單")

    h = sub.add_parser("holdings", help="最新一季 13F 持倉變化")
    h.add_argument("investor")
    h.add_argument("--prev-xml", help="offline: previous quarter info table XML")
    h.add_argument("--curr-xml", help="offline: current quarter info table XML")
    h.add_argument("--quarter", default="(offline)")
    h.add_argument("--top", type=int, default=20)
    h.add_argument("--tickers", help="offline: JSON map of CUSIP/issuer name -> ticker")

    p = sub.add_parser("posts", help="分析貼文，抽出看多/看空訊號")
    p.add_argument("posts_file", help="JSON/JSONL posts file")
    p.add_argument("--llm", action="store_true", help="use Claude instead of keywords")

    r = sub.add_parser("report", help="言行一致報告：貼文 vs 13F")
    r.add_argument("investor")
    r.add_argument("posts_file")
    r.add_argument("--prev-xml")
    r.add_argument("--curr-xml")
    r.add_argument("--quarter", default="(offline)")
    r.add_argument("--tickers", help="offline: JSON map of CUSIP/issuer name -> ticker")
    r.add_argument("--llm", action="store_true")

    args = ap.parse_args(argv)
    investors = load_watchlist(args.watchlist)

    if args.cmd == "list":
        for inv in investors.values():
            print(f"{inv.key:15} {inv.name:24} {inv.fund:38} CIK {inv.cik}")
        return 0

    analyzer = None
    if args.cmd in ("posts", "report"):
        analyzer = ClaudeAnalyzer() if args.llm else KeywordAnalyzer()

    if args.cmd == "posts":
        for post in load_posts_json(args.posts_file):
            for s in analyzer.analyze(post):
                print(f"{post.published} {post.investor_key:12} {s.ticker:8} "
                      f"{s.stance.value:8} {s.confidence:.2f}  {s.rationale}")
        return 0

    inv = investors.get(args.investor)
    if inv is None:
        print(f"unknown investor {args.investor!r}; try `spiderhead list`", file=sys.stderr)
        return 2
    if args.prev_xml and args.curr_xml:
        quarter, changes = _changes_from_files(args.prev_xml, args.curr_xml, args.quarter,
                                               args.tickers)
    else:
        quarter, changes = _changes_from_edgar(inv, resolver_from_sec=True)

    if args.cmd == "holdings":
        print(holdings_markdown(inv, quarter, changes, top=args.top))
        return 0

    # report
    posts = [p for p in load_posts_json(args.posts_file) if p.investor_key == inv.key]
    if quarter != "(offline)":
        posts = [p for p in posts if str(quarter_end(p.published)) == quarter]
    signals = [s for p in posts for s in analyzer.analyze(p)]
    print(holdings_markdown(inv, quarter, changes, top=10))
    print()
    print(consistency_markdown(check(signals, changes)))
    return 0
