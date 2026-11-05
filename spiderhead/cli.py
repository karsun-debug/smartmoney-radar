"""Command line entry point: ``python -m spiderhead <command>``."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
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


def _digest_markdown(store, investors, days: int, investor: str | None = None) -> str:
    from .digest import build_digest
    d = build_digest(store, investors, days, investor)
    feed = feed_markdown(d.feed, d.names)
    trend = trends_markdown(d.trends, d.consensus, d.names)
    return f"# Smartmoney Radar 動態摘要（{d.start} ～ {d.end}）\n\n{feed}\n\n{trend}\n"


def _changes_from_edgar(investor, resolver_from_sec: bool):
    from .edgar import EdgarClient
    client = EdgarClient()
    filings = client.latest_13f_per_quarter(investor.cik, quarters=2)
    if len(filings) < 2:
        raise SystemExit(f"{investor.key}: fewer than two 13F filings found")
    curr_f, prev_f = filings
    curr = parse_infotable(client.fetch_infotable_xml(curr_f))
    prev = parse_infotable(client.fetch_infotable_xml(prev_f))
    from .holdings import OpenFigi
    resolver = TickerResolver(name_map=client.company_name_map() if resolver_from_sec else None,
                              figi=OpenFigi())
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
    sub.add_parser("verify-ciks", help="向 SEC 確認每位大神的 CIK 對應到正確基金")
    sub.add_parser("doctor", help="檢查設定與每個資料來源是否連得上")

    al = sub.add_parser("alert", help="即時通知：新的喊單或申報交易立刻推 LINE")
    al.add_argument("--dry-run", action="store_true", help="只印出訊息，不送出也不記錄")
    al.add_argument("--backfill", action="store_true", help="第一次執行也送出（預設第一次只記錄不送）")
    al.add_argument("--include-news", action="store_true", help="新聞報導中的喊單也通知")
    al.add_argument("--lookback", type=int, default=3, help="只看最近 N 天的資料")

    pl = sub.add_parser("push-line", help="把摘要推播到 LINE")
    pl.add_argument("--days", type=int, default=1, help="摘要涵蓋最近 N 天（預設 1）")
    pl.add_argument("--dashboard-url", help="附在訊息最後的儀表板網址")
    pl.add_argument("--dry-run", action="store_true", help="只印出訊息，不送出")

    db = sub.add_parser("dashboard", help="產生網頁儀表板（靜態 HTML）")
    db.add_argument("--out", default="site", help="輸出資料夾")
    db.add_argument("--days", type=int, default=90)
    db.add_argument("--with-13f", action="store_true", help="從 EDGAR 抓最新 13F 放進大神頁面")
    db.add_argument("--no-prices", action="store_true", help="不抓股價（喊單勝率與個股走勢圖會留空）")

    f = sub.add_parser("fetch", help="自動抓取大神發文與產業新聞，存進資料庫並分析")
    f.add_argument("--days", type=int, default=7, help="只保留最近 N 天")
    f.add_argument("--sources", help="JSON file with extra RSS / X / news queries")
    f.add_argument("--no-news", action="store_true", help="skip Google News per investor")
    f.add_argument("--no-industry", action="store_true", help="skip industry news")
    f.add_argument("--llm", action="store_true", help="analyze with Claude")
    f.add_argument("--no-trades", action="store_true",
                   help="skip Form 4 / 13D / congress trade collection")

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

    if args.cmd == "doctor":
        from .doctor import format_checks, run_checks
        from .sources import _requests_get
        print(format_checks(run_checks(_requests_get)))
        return 0

    if args.cmd == "verify-ciks":
        from .edgar import EdgarClient
        client = EdgarClient()
        for inv in investors.values():
            if not inv.cik:
                continue
            try:
                name = client.entity_name(inv.cik)
            except Exception as exc:
                name = f"ERROR {exc}"
            print(f"{inv.key:14} CIK {inv.cik}  expected: {inv.fund:40} SEC: {name}")
        return 0

    if args.cmd in ("fetch", "import", "feed", "trends", "digest", "push-line", "dashboard", "alert"):
        return _store_commands(args, investors)

    analyzer = None
    if args.cmd in ("posts", "report"):
        analyzer = ClaudeAnalyzer() if args.llm else KeywordAnalyzer(names=_name_map(investors))

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
        analyzer = ClaudeAnalyzer() if args.llm else KeywordAnalyzer(names=_name_map(investors))
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
        if args.cmd == "fetch" and not args.no_trades:
            trades = _collect_trades(investors, max(args.days, 30), store)
            print(f"對帳單：抓到 {len(trades)} 筆，新增 {store.add_trades(trades)} 筆")
        return 0

    if args.cmd == "push-line":
        import os
        from .digest import build_digest
        from .notify import LineNotifier, format_line
        url = args.dashboard_url or os.environ.get("SPIDERHEAD_DASHBOARD_URL")
        text = format_line(build_digest(store, investors, args.days), url)
        if args.dry_run:
            print(text)
            return 0
        n = LineNotifier().send(text)
        print(f"已送出 LINE（{n} 次請求）")
        return 0

    if args.cmd == "alert":
        from .alerts import run_alerts
        send = None
        if not args.dry_run:
            from .notify import LineNotifier
            send = LineNotifier().send
        n, text = run_alerts(store, investors, send, dry_run=args.dry_run, backfill=args.backfill,
                             lookback_days=args.lookback, include_news=args.include_news)
        if text:
            print(text)
        if n == 0 and not text:
            print("沒有新的通知（第一次執行只會記錄現有資料，不會送出）")
        else:
            print(f"\n{'（試跑，未送出）' if args.dry_run else '已送出'} {n} 則")
        return 0

    if args.cmd == "dashboard":
        from .dashboard import build_site
        holdings = _holdings_for_dashboard(store, investors) if args.with_13f else None
        prices = None
        if not args.no_prices:
            from .performance import PriceBook
            from .sources import _requests_get
            prices = PriceBook(store, _requests_get)
        index = build_site(store, investors, args.out, days=args.days, holdings=holdings,
                           prices=prices)
        for err in (prices.errors if prices else [])[:3]:
            print(f"warning: {err}", file=sys.stderr)
        print(f"已產生 {index}")
        return 0

    text = _digest_markdown(store, investors, args.days, getattr(args, "investor", None))
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


def _holdings_for_dashboard(store, investors):
    """Latest 13F diff + say/do check for every 13F filer; failures are skipped."""
    from .store import window
    out = {}
    analyzer = KeywordAnalyzer(names=_name_map(investors))
    for inv in investors.values():
        if not inv.files_13f:
            continue
        try:
            quarter, changes = _changes_from_edgar(inv, resolver_from_sec=True)
        except Exception as exc:
            print(f"warning: 13F {inv.key}: {exc}", file=sys.stderr)
            continue
        q_end = date.fromisoformat(quarter)
        q_start = date(q_end.year, q_end.month - 2, 1)   # first day of that quarter
        posts = store.posts_between(q_start, q_end, inv.key, kinds=("own", "news"))
        signals = [s for p in posts for s in (store.signals_for(p) or analyzer.analyze(p))
                   if s.is_own_view]
        out[inv.key] = (quarter, changes, check(signals, changes))
    return out


def _collect_trades(investors, days: int, store=None):
    import os
    from .holdings import TickerResolver
    from .sources import _requests_get
    from .trades import TradeCollector
    edgar, resolver = None, None
    if os.environ.get("SPIDERHEAD_USER_AGENT"):
        from .edgar import EdgarClient
        edgar = EdgarClient()
        try:
            resolver = TickerResolver(name_map=edgar.company_name_map())
        except Exception as exc:
            print(f"warning: ticker map: {exc}", file=sys.stderr)
    else:
        print("warning: SPIDERHEAD_USER_AGENT not set; skipping SEC Form 4 / 13D", file=sys.stderr)
    collector = TradeCollector(edgar, _requests_get, resolver.name if resolver else None)
    trades = collector.collect(investors.values(), days)
    errors = list(collector.errors)
    if store is not None:
        from .ark import ARK_FUNDS, ArkCollector
        for inv in investors.values():
            funds = inv.handles.get("ark")
            if not funds:
                continue
            wanted = {f.strip().upper() for f in funds.split(",")}
            ark = ArkCollector(store, _requests_get,
                               {f: u for f, u in ARK_FUNDS.items() if f in wanted}, inv.key)
            trades += ark.collect()
            errors += ark.errors
    for err in errors:
        print(f"warning: {err}", file=sys.stderr)
    return trades


def _name_map(investors) -> dict[str, str]:
    return {k: v.name for k, v in investors.items()}
