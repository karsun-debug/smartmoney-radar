"""Open data next to the dashboard: JSON + CSV files and an RSS feed.

Everything here is already public (posts, filings, prices); publishing it lets
other tools, spreadsheets or AI agents use the data without running the code.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .models import Investor
from .store import Store


def _trade_dict(t) -> dict:
    return {"investor": t.investor_key, "traded": str(t.traded), "filed": str(t.filed), "side": t.side,
            "form": t.form, "ticker": t.ticker, "issuer": t.issuer, "shares": t.shares, "price": t.price,
            "amount": t.amount_text, "url": t.url}


def _signal_dict(s) -> dict:
    return {"investor": s.post.investor_key, "date": str(s.post.published), "ticker": s.ticker,
            "sector": s.sector, "stance": s.stance.value, "confidence": round(s.confidence, 2),
            "own_view": s.is_own_view, "source": s.post.source, "url": s.post.url,
            "text": s.post.text[:500]}


def _write_csv(path: Path, rows: list[dict]) -> None:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8-sig")      # BOM so Excel opens Chinese text correctly


def write_exports(store: Store, investors: dict[str, Investor], out: Path, days: int = 90,
                  site_url: str = "", consensus=None, clusters=None) -> list[str]:
    out = Path(out)
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    end = date.today()
    start = end - timedelta(days=days - 1)
    trades = [_trade_dict(t) for t in store.trades_between(start, end, by="filed")]
    signals = [_signal_dict(s) for s in store.signals_between(start, end) if s.post.kind != "industry"]
    files = {
        "investors.json": [{"key": i.key, "name": i.name, "fund": i.fund, "cik": i.cik, "proof": i.proof}
                           for i in investors.values()],
        "trades.json": trades,
        "signals.json": signals,
    }
    if consensus is not None:
        files["consensus13f.json"] = [
            {"ticker": r.key, "issuer": r.issuer, "owners": r.owners, "bought": r.bought, "sold": r.sold,
             "value": r.value} for r in consensus.most_owned(200)]
    if clusters is not None:
        files["insider_clusters.json"] = [
            {"ticker": c.ticker, "issuer": c.issuer, "insiders": sorted(c.insiders), "value": round(c.value),
             "first": str(c.first), "last": str(c.last),
             "buys": [{"owner": b.owner, "title": b.title, "traded": str(b.traded), "shares": b.shares,
                       "price": b.price, "url": b.url} for b in c.buys]} for c in clusters]
    meta = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "days": days,
            "files": sorted(files) + ["trades.csv", "signals.csv"]}
    for name, rows in files.items():
        (data / name).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (data / "index.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_csv(data / "trades.csv", trades)
    _write_csv(data / "signals.csv", [{k: v for k, v in s.items() if k != "text"} for s in signals])
    (out / "feed.xml").write_text(rss_feed(store, investors, site_url), encoding="utf-8")
    return sorted(files) + ["trades.csv", "signals.csv", "index.json", "../feed.xml"]


def rss_feed(store: Store, investors: dict[str, Investor], site_url: str = "", days: int = 14) -> str:
    """RSS 2.0 of new filed trades and investors' own calls."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    names = {k: v.name for k, v in investors.items()}
    items = []
    for t in store.trades_between(start, end, by="filed"):
        side = {"buy": "買進", "sell": "賣出", "stake": "持股逾5%", "report": "提交申報"}.get(t.side, t.side)
        title = f"{names.get(t.investor_key, t.investor_key)} {side} {t.ticker or t.issuer[:40]}"
        items.append((t.filed, title, t.amount_text or t.issuer, t.url or site_url, f"trade:{t.uid}"))
    seen = set()
    for s in store.signals_between(start, end):
        if not s.is_own_view or s.ticker.startswith("SECTOR:") or s.post.uid in seen:
            continue
        seen.add(s.post.uid)
        title = f"{names.get(s.post.investor_key, s.post.investor_key)}：{s.post.text[:80]}"
        items.append((s.post.published, title, s.post.text[:500], s.post.url or site_url, f"post:{s.post.uid}"))
    items.sort(key=lambda i: i[0], reverse=True)
    body = "".join(
        f"<item><title>{escape(t)}</title><description>{escape(d)}</description>"
        + (f"<link>{escape(link)}</link>" if link else "")
        + f'<guid isPermaLink="false">{escape(g)}</guid>'
        f"<pubDate>{format_datetime(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))}</pubDate></item>"
        for day, t, d, link, g in items[:200])
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<title>Smartmoney Radar</title>"
            f"<link>{escape(site_url or 'https://github.com/karsun-debug/smartmoney-radar')}</link>"
            "<description>大神的實際交易與本人喊單</description>" + body + "</channel></rss>")
