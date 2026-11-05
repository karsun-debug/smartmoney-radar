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


def _analyze_pending(store, analyzer, retail=None) -> int:
    done = 0
    for post in store.unanalyzed():
        if post.kind == "retail":
            if retail is None:
                from .retail import RetailAnalyzer, known_tickers
                from .store import window
                retail = RetailAnalyzer(known_tickers(store, *window(180)))
            signals = retail.analyze(post)        # cheap keywords; never sent to Claude
        else:
            signals = analyzer.analyze(post)
        if signals is None:            # deferred (LLM budget used up this run)
            continue
        store.save_signals(post, signals)
        done += 1
    if hasattr(analyzer, "llm_calls"):
        extra = f"，延後 {analyzer.deferred} 則到下次" if analyzer.deferred else ""
        print(f"Claude 分析 {analyzer.llm_calls} 則{extra}")
    return done


def _make_analyzer(args, investors):
    keyword = KeywordAnalyzer(names=_name_map(investors))
    if not getattr(args, "llm", False):
        return keyword
    from .posts import HybridAnalyzer
    return HybridAnalyzer(keyword, ClaudeAnalyzer(), getattr(args, "llm_budget", 200))


def _digest_markdown(store, investors, days: int, investor: str | None = None) -> str:
    from .digest import build_digest
    d = build_digest(store, investors, days, investor)
    feed = feed_markdown(d.feed, d.names)
    trend = trends_markdown(d.trends, d.consensus, d.names)
    return f"# Smartmoney Radar 動態摘要（{d.start} ～ {d.end}）\n\n{feed}\n\n{trend}\n"


def _sec_resolver(client) -> TickerResolver:
    from .holdings import OpenFigi
    return TickerResolver(name_map=client.company_name_map(), figi=OpenFigi())


def _changes_from_edgar(investor, client=None, resolver: TickerResolver | None = None):
    """Latest 13F vs the previous quarter. Pass ``client``/``resolver`` to reuse them."""
    from .edgar import EdgarClient
    client = client or EdgarClient()
    filings = client.latest_13f_per_quarter(investor.cik, quarters=2)
    if len(filings) < 2:
        raise ValueError(f"{investor.key}: fewer than two 13F filings found")
    curr_f, prev_f = filings
    curr = parse_infotable(client.fetch_infotable_xml(curr_f))
    prev = parse_infotable(client.fetch_infotable_xml(prev_f))
    resolver = resolver or _sec_resolver(client)
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

    sub.add_parser("mcp", help="啟動 MCP 伺服器（stdio），讓 Claude 等 AI 工具查詢資料庫")

    wk = sub.add_parser("weekly", help="產生大神週報（加 --llm 由 Claude 撰寫）")
    wk.add_argument("--llm", action="store_true")
    wk.add_argument("--push", action="store_true", help="推播到所有已設定的管道")
    wk.add_argument("--out", help="同時寫成 Markdown 檔")

    ins = sub.add_parser("insiders", help="全市場內部人群買（3 位以上內部人在公開市場買進）")
    ins.add_argument("--days", type=int, default=3, help="掃描最近 N 天的 Form 4（已掃過的日子會略過）")
    ins.add_argument("--window", type=int, default=14, help="群買判定的天數範圍")
    ins.add_argument("--min-insiders", type=int, default=3)
    ins.add_argument("--max-filings", type=int, default=3000, help="每天最多解析幾份 Form 4")
    ins.add_argument("--no-scan", action="store_true", help="只用資料庫裡的資料，不連 SEC")

    al = sub.add_parser("alert", help="即時通知：新的喊單或申報交易立刻推 LINE")
    al.add_argument("--dry-run", action="store_true", help="只印出訊息，不送出也不記錄")
    al.add_argument("--backfill", action="store_true", help="第一次執行也送出（預設第一次只記錄不送）")
    al.add_argument("--include-news", action="store_true", help="新聞報導中的喊單也通知")
    al.add_argument("--lookback", type=int, default=3, help="只看最近 N 天的資料")

    pl = sub.add_parser("push-line", aliases=["push"],
                        help="把摘要推播到所有已設定的管道（LINE / Telegram / Discord / Slack / Email）")
    pl.add_argument("--days", type=int, default=1, help="摘要涵蓋最近 N 天（預設 1）")
    pl.add_argument("--dashboard-url", help="附在訊息最後的儀表板網址")
    pl.add_argument("--dry-run", action="store_true", help="只印出訊息，不送出")

    db = sub.add_parser("dashboard", help="產生網頁儀表板（靜態 HTML）")
    db.add_argument("--out", default="site", help="輸出資料夾")
    db.add_argument("--days", type=int, default=90)
    db.add_argument("--with-13f", action="store_true",
                    help="先從 EDGAR 更新最近 4 季 13F（已抓過的季度會略過）")
    db.add_argument("--no-prices", action="store_true", help="不抓股價（喊單勝率與個股走勢圖會留空）")

    f = sub.add_parser("fetch", help="自動抓取大神發文與產業新聞，存進資料庫並分析")
    f.add_argument("--days", type=int, default=7, help="只保留最近 N 天")
    f.add_argument("--sources", help="JSON file with extra RSS / X / news queries")
    f.add_argument("--no-news", action="store_true", help="skip Google News per investor")
    f.add_argument("--no-industry", action="store_true", help="skip industry news")
    f.add_argument("--llm", action="store_true", help="analyze with Claude")
    f.add_argument("--llm-budget", type=int, default=200,
                   help="每次最多送幾則給 Claude（其餘延到下次，控制費用）")
    f.add_argument("--no-trades", action="store_true",
                   help="skip Form 4 / 13D / congress trade collection")
    f.add_argument("--no-retail", action="store_true", help="skip Reddit retail chatter")
    f.add_argument("--no-shorts", action="store_true", help="skip FINRA short-sale volume")

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

    if args.cmd == "mcp":
        try:
            from .mcp_server import main as mcp_main
        except ImportError:
            print('需要 MCP SDK：pip install -e ".[mcp]"', file=sys.stderr)
            return 1
        mcp_main(args.watchlist)
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

    if args.cmd == "push":
        args.cmd = "push-line"
    if args.cmd in ("fetch", "import", "feed", "trends", "digest", "push-line", "dashboard", "alert",
                    "insiders", "weekly"):
        return _store_commands(args, investors)

    analyzer = None
    if args.cmd in ("posts", "report"):
        analyzer = _make_analyzer(args, investors)

    if args.cmd == "posts":
        for post in load_posts_json(args.posts_file):
            for s in analyzer.analyze(post) or []:
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
        try:
            quarter, changes = _changes_from_edgar(inv)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1

    if args.cmd == "holdings":
        print(holdings_markdown(inv, quarter, changes, top=args.top))
        return 0

    # report
    posts = [p for p in load_posts_json(args.posts_file) if p.investor_key == inv.key]
    if quarter != "(offline)":
        posts = [p for p in posts if str(quarter_end(p.published)) == quarter]
    signals = [s for p in posts for s in analyzer.analyze(p) or []]
    print(holdings_markdown(inv, quarter, changes, top=10))
    print()
    print(consistency_markdown(check(signals, changes)))
    return 0


def _store_commands(args, investors) -> int:
    from .store import Store
    store = Store()
    if args.cmd in ("fetch", "import"):
        analyzer = _make_analyzer(args, investors)
        if args.cmd == "fetch":
            from .sources import Collector, load_sources
            collector = Collector(load_sources(investors, args.sources))
            posts = collector.collect(args.days, news=not args.no_news,
                                      industries=not args.no_industry)
            store.log_results(collector.health.results)
            for err in collector.errors:
                print(f"warning: {err}", file=sys.stderr)
            if not args.no_retail:
                from .health import HealthLog
                from .retail import collect_retail
                from .sources import _requests_get
                hl = HealthLog()
                posts += [p for p in collect_retail(_requests_get, health=hl)
                          if p.published >= date.today() - timedelta(days=args.days)]
                store.log_results(hl.results)
        else:
            posts = load_posts_json(args.posts_file)
        added = store.add_posts(posts)
        analyzed = _analyze_pending(store, analyzer)
        print(f"抓到 {len(posts)} 則，新增 {added} 則，分析 {analyzed} 則")
        if args.cmd == "fetch" and not args.no_trades:
            trades = _collect_trades(investors, max(args.days, 30), store)
            print(f"對帳單：抓到 {len(trades)} 筆，新增 {store.add_trades(trades)} 筆")
        if args.cmd == "fetch" and not args.no_shorts:
            from .health import HealthLog
            from .retail import known_tickers
            from .shorts import fetch_shorts
            from .sources import _requests_get
            from .store import window
            hl = HealthLog()
            n = fetch_shorts(store, _requests_get, known_tickers(store, *window(180)), health=hl)
            store.log_results(hl.results)
            print(f"放空成交量：{n} 筆")
        return 0

    if args.cmd == "weekly":
        from .weekly import claude_report, gather, template_report
        w = gather(store, investors)
        author = "template"
        text = template_report(w)
        if args.llm:
            try:
                text, author = claude_report(w), "claude"
            except Exception as exc:
                print(f"warning: Claude 週報失敗，改用範本：{exc}", file=sys.stderr)
        store.save_report(w.start, w.end, text, author)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return _broadcast(text) if args.push else 0

    if args.cmd == "insiders":
        from .health import SourceResult
        from .insiders import InsiderScanner, find_clusters
        from .store import window
        if not args.no_scan:
            from .edgar import EdgarClient
            scanner = InsiderScanner(store, EdgarClient(min_interval=0.11).get_text, args.max_filings)
            n = scanner.scan(args.days)
            store.log_results([SourceResult("insiders", "form4-daily", len(scanner.errors) < 5, n,
                                            "; ".join(scanner.errors[:2]))])
            print(f"新增 {n} 筆內部人公開市場買進")
        start, end = window(args.window)
        for c in find_clusters(store.insider_buys_between(start, end), args.min_insiders):
            print(f"{c.ticker:6} {len(c.insiders)} 位內部人  ${c.value / 1e6:,.2f}M  "
                  f"{c.first}~{c.last}  {c.issuer}")
        return 0

    if args.cmd == "push-line":
        import os
        from .digest import build_digest
        from .notify import format_line
        url = args.dashboard_url or os.environ.get("SPIDERHEAD_DASHBOARD_URL")
        text = format_line(build_digest(store, investors, args.days), url)
        if args.dry_run:
            print(text)
            return 0
        return _broadcast(text)

    if args.cmd == "alert":
        from .alerts import run_alerts
        send = None
        if not args.dry_run:
            from .channels import Broadcast, configured_channels
            chans = configured_channels()
            if not chans:
                print("沒有設定任何通知管道（LINE / Telegram / Discord / Slack / Email），略過", file=sys.stderr)
                return 0
            send = lambda text: _report(Broadcast(chans).send(text))  # noqa: E731
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
        if args.with_13f:
            _refresh_13f(store, investors)
        prices = None
        if not args.no_prices:
            from .performance import PriceBook
            from .sources import _requests_get
            prices = PriceBook(store, _requests_get)
        index = build_site(store, investors, args.out, days=args.days, prices=prices)
        for err in (prices.errors if prices else [])[:3]:
            print(f"warning: {err}", file=sys.stderr)
        if prices is not None:
            from .health import SourceResult
            store.log_results([SourceResult("prices", "stooq", not prices.errors, len(prices._cache),
                                            "; ".join(prices.errors[:2]))])
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


def _refresh_13f(store, investors, quarters: int = 4) -> None:
    """Download the last few 13F quarters for every filer into the store.

    Quarters already stored with the same accession number are skipped, so a
    daily run only downloads new filings (or amendments).
    """
    from .edgar import EdgarClient
    from .health import SourceResult
    try:
        client = EdgarClient()
    except Exception as exc:
        print(f"warning: 13F skipped: {exc}", file=sys.stderr)
        return
    resolver, results = None, []
    for inv in investors.values():
        if not inv.files_13f:
            continue
        try:
            new = 0
            for f in client.latest_13f_per_quarter(inv.cik, quarters=quarters):
                q = str(f.report_date)
                if store.accession_13f(inv.key, q) == f.accession:
                    continue
                if resolver is None:
                    resolver = _sec_resolver(client)     # one download, shared by everyone
                store.save_13f(inv.key, q, f.accession,
                               parse_infotable(client.fetch_infotable_xml(f)), resolver, f.filing_date)
                new += 1
            results.append(SourceResult("sec13f", inv.key, True, new))
        except Exception as exc:
            print(f"warning: 13F {inv.key}: {exc}", file=sys.stderr)
            results.append(SourceResult("sec13f", inv.key, False, 0, str(exc)))
    store.log_results(results)


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
        store.log_results(collector.health.results)
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
            store.log_results(ark.health.results)
    for err in errors:
        print(f"warning: {err}", file=sys.stderr)
    return trades


def _name_map(investors) -> dict[str, str]:
    return {k: v.name for k, v in investors.items()}


def _report(results) -> None:
    for name, err in results:
        print(f"{'✅' if not err else '❌'} {name}" + (f"：{err}" if err else ""),
              file=sys.stderr if err else sys.stdout)


def _broadcast(text: str) -> int:
    from .channels import Broadcast, configured_channels
    chans = configured_channels()
    if not chans:
        print("沒有設定任何通知管道（LINE / Telegram / Discord / Slack / Email），略過", file=sys.stderr)
        return 0
    try:
        _report(Broadcast(chans).send(text))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
