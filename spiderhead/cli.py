"""Command line entry point: ``python -m spiderhead <command>``."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from .consistency import check, quarter_end
from .holdings import TickerResolver, diff_holdings, parse_infotable
from .posts import ClaudeAnalyzer, KeywordAnalyzer, load_posts_json
from .report import consistency_markdown, feed_markdown, holdings_markdown, trends_markdown
from .watchlist import load_watchlist


def _analyze_pending(store, analyzer) -> int:
    pending = store.unanalyzed()
    for post in pending:
        store.save_signals(post, analyzer.analyze(post))
    return len(pending)


def _names(investors) -> dict[str, str]:
    from .sectors import SECTORS
    names = {k: v.name for k, v in investors.items()}
    names.update({f"industry:{k}": f"產業·{v}" for k, v in SECTORS.items()})
    return names


def _feed_and_trends(store, investors, days: int, investor: str | None = None) -> str:
    from .store import window
    from .trends import consensus, sector_trends
    start, end = window(days)
    prev_start, prev_end = window(days, start - timedelta(days=1))
    names = _names(investors)
    posts = store.posts_between(start, end, investor, kinds=("own", "news"))
    feed = feed_markdown([(p, store.signals_for(p)) for p in posts], names)
    cur = store.signals_between(start, end)
    trend = trends_markdown(sector_trends(cur, store.signals_between(prev_start, prev_end)),
                            consensus(cur), names)
    return f"# SpiderHead 動態摘要（{start} ～ {end}）\n\n{feed}\n\n{trend}\n"


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

    f = sub.add_parser("fetch", help="自動抓取大神發文與產業新聞，存進資料庫並分析")
    f.add_argument("--days", type=int, default=7, help="只保留最近 N 天")
    f.add_argument("--sources", help="JSON file with extra RSS / X / news queries")
    f.add_argument("--no-news", action="store_true", help="skip Google News per investor")
    f.add_argument("--no-industry", action="store_true", help="skip industry news")
    f.add_argument("--llm", action="store_true", help="analyze with Claude")

    im = sub.add_parser("import", help="匯入手動整理的貼文 JSON/JSONL")
    im.add_argument("posts_file")
    im.add_argument("--llm", action="store_true")

    for name, desc in (("feed", "大神最新發文"), ("trends", "產業熱度與共識標的"),
                       ("digest", "完整摘要（發文 + 產業熱度）")):
        c = sub.add_parser(name, help=desc)
        c.add_argument("--days", type=int, default=7)
        if name == "feed":
            c.add_argument("--investor")
        if name == "digest":
            c.add_argument("--out", help="write Markdown to this file")

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
            print(f"{inv.key:15} {inv.name:24} {inv.fund:38} CIK {inv.cik or '-'}")
        return 0

    if args.cmd in ("fetch", "import", "feed", "trends", "digest"):
        return _store_commands(args, investors)

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
    if not (args.prev_xml and args.curr_xml) and not inv.cik:
        print(f"{inv.key} has no CIK; add one with --watchlist to compare 13F", file=sys.stderr)
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


def _store_commands(args, investors) -> int:
    from .store import Store
    store = Store()
    if args.cmd in ("fetch", "import"):
        analyzer = ClaudeAnalyzer() if args.llm else KeywordAnalyzer()
        if args.cmd == "fetch":
            from .sources import Collector, load_sources
            collector = Collector(load_sources(investors, args.sources))
            posts = collector.collect(args.days, news=not args.no_news,
                                      industries=not args.no_industry)
            for err in collector.errors:
                print(f"warning: {err}", file=sys.stderr)
        else:
            posts = load_posts_json(args.posts_file)
        added = store.add_posts(posts)
        analyzed = _analyze_pending(store, analyzer)
        print(f"抓到 {len(posts)} 則，新增 {added} 則，分析 {analyzed} 則")
        return 0

    text = _feed_and_trends(store, investors, args.days, getattr(args, "investor", None))
    if args.cmd == "feed":
        text = text.split("\n## 產業熱度")[0]
    elif args.cmd == "trends":
        text = "## 產業熱度" + text.split("\n## 產業熱度", 1)[1]
    if getattr(args, "out", None):
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已寫入 {args.out}")
    else:
        print(text)
    return 0
