"""SQLite storage for collected posts and the signals extracted from them."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Post, Signal, Stance, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    uid TEXT PRIMARY KEY, investor TEXT NOT NULL, published TEXT NOT NULL,
    source TEXT, kind TEXT, url TEXT, text TEXT NOT NULL,
    fetched_at TEXT NOT NULL, analyzed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS posts_published ON posts(published);
CREATE TABLE IF NOT EXISTS signals (
    post_uid TEXT NOT NULL REFERENCES posts(uid), ticker TEXT NOT NULL,
    sector TEXT, stance TEXT NOT NULL, confidence REAL, rationale TEXT
);
CREATE INDEX IF NOT EXISTS signals_post ON signals(post_uid);
CREATE TABLE IF NOT EXISTS trades (
    uid TEXT PRIMARY KEY, investor TEXT NOT NULL, traded TEXT NOT NULL, filed TEXT NOT NULL,
    side TEXT NOT NULL, form TEXT NOT NULL, ticker TEXT, issuer TEXT, shares REAL, price REAL,
    amount_text TEXT, url TEXT
);
CREATE INDEX IF NOT EXISTS trades_traded ON trades(traded);
CREATE TABLE IF NOT EXISTS ark_holdings (
    fund TEXT NOT NULL, day TEXT NOT NULL, key TEXT NOT NULL, ticker TEXT, company TEXT,
    cusip TEXT, shares REAL, value REAL, PRIMARY KEY (fund, day, key)
);
CREATE TABLE IF NOT EXISTS alerts_sent (
    uid TEXT PRIMARY KEY, kind TEXT NOT NULL, sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL, day TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY (ticker, day)
);
"""


def default_db_path() -> Path:
    return Path(os.environ.get("SPIDERHEAD_DB", "data/spiderhead.db"))


class Store:
    def __init__(self, path: str | Path | None = None):
        path = Path(path) if path else default_db_path()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def add_posts(self, posts: Iterable[Post]) -> int:
        """Insert posts, skipping ones already stored. Returns the number added."""
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.db.executemany(
            "INSERT OR IGNORE INTO posts (uid, investor, published, source, kind, url, text, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(p.uid, p.investor_key, p.published.isoformat(), p.source, p.kind, p.url, p.text, now)
             for p in posts])
        self.db.commit()
        return cur.rowcount

    def unanalyzed(self) -> list[Post]:
        rows = self.db.execute(
            "SELECT investor, published, text, source, url, kind FROM posts WHERE analyzed = 0"
            " ORDER BY published").fetchall()
        return [self._post(r) for r in rows]

    def save_signals(self, post: Post, signals: Iterable[Signal]) -> None:
        self.db.executemany(
            "INSERT INTO signals VALUES (?,?,?,?,?,?)",
            [(post.uid, s.ticker, s.sector, s.stance.value, s.confidence, s.rationale)
             for s in signals])
        self.db.execute("UPDATE posts SET analyzed = 1 WHERE uid = ?", (post.uid,))
        self.db.commit()

    def posts_between(self, start: date, end: date, investor: str | None = None,
                      kinds: tuple[str, ...] = ("own", "news", "industry")) -> list[Post]:
        q = (f"SELECT investor, published, text, source, url, kind FROM posts"
             f" WHERE published >= ? AND published <= ? AND kind IN ({','.join('?' * len(kinds))})")
        args: list = [start.isoformat(), end.isoformat(), *kinds]
        if investor:
            q += " AND investor = ?"
            args.append(investor)
        return [self._post(r) for r in self.db.execute(q + " ORDER BY published DESC", args)]

    def signals_between(self, start: date, end: date) -> list[Signal]:
        rows = self.db.execute(
            "SELECT p.investor, p.published, p.text, p.source, p.url, p.kind,"
            " s.ticker, s.stance, s.confidence, s.rationale, s.sector"
            " FROM signals s JOIN posts p ON p.uid = s.post_uid"
            " WHERE p.published >= ? AND p.published <= ?",
            (start.isoformat(), end.isoformat())).fetchall()
        return [Signal(r[6], Stance(r[7]), r[8], r[9], self._post(r[:6]), r[10] or "other")
                for r in rows]

    def signals_for(self, post: Post) -> list[Signal]:
        rows = self.db.execute("SELECT ticker, stance, confidence, rationale, sector FROM signals"
                               " WHERE post_uid = ?", (post.uid,)).fetchall()
        return [Signal(r[0], Stance(r[1]), r[2], r[3], post, r[4] or "other") for r in rows]

    # ------------------------------------------------------------- trades
    def add_trades(self, trades: Iterable[Trade]) -> int:
        cur = self.db.executemany(
            "INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(t.uid, t.investor_key, t.traded.isoformat(), t.filed.isoformat(), t.side, t.form,
              t.ticker, t.issuer, t.shares, t.price, t.amount_text, t.url) for t in trades])
        self.db.commit()
        return cur.rowcount

    def trades_between(self, start: date, end: date, investor: str | None = None,
                       ticker: str | None = None, by: str = "traded") -> list[Trade]:
        """Trades whose ``by`` date ("traded" or "filed") falls in [start, end]."""
        col = "filed" if by == "filed" else "traded"
        q = f"SELECT * FROM trades WHERE {col} >= ? AND {col} <= ?"
        args: list = [start.isoformat(), end.isoformat()]
        if investor:
            q += " AND investor = ?"
            args.append(investor)
        if ticker:
            q += " AND ticker = ?"
            args.append(ticker)
        return [Trade(r[1], date.fromisoformat(r[2]), date.fromisoformat(r[3]), r[4], r[5], r[6],
                      r[7] or "", r[8], r[9], r[10] or "", r[11])
                for r in self.db.execute(q + " ORDER BY traded DESC, filed DESC", args)]

    # ------------------------------------------------------------- prices
    def add_prices(self, ticker: str, rows: Iterable[tuple[date, float]]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)",
                            [(ticker, d.isoformat(), c) for d, c in rows])
        self.db.commit()

    def prices(self, ticker: str) -> list[tuple[date, float]]:
        return [(date.fromisoformat(d), c) for d, c in self.db.execute(
            "SELECT day, close FROM prices WHERE ticker = ? ORDER BY day", (ticker,))]

    # ------------------------------------------------------------- ARK
    def save_ark(self, fund: str, day: date, rows) -> None:
        self.db.executemany("INSERT OR REPLACE INTO ark_holdings VALUES (?,?,?,?,?,?,?,?)",
                            [(fund, day.isoformat(), h.key, h.ticker, h.company, h.cusip,
                              h.shares, h.value) for h in rows])
        self.db.commit()

    def ark_previous(self, fund: str, day: date):
        """Most recent stored holdings strictly before ``day``: (day, rows) or (None, [])."""
        from .ark import ArkHolding
        r = self.db.execute("SELECT MAX(day) FROM ark_holdings WHERE fund = ? AND day < ?",
                            (fund, day.isoformat())).fetchone()
        if not r or not r[0]:
            return None, []
        rows = self.db.execute("SELECT ticker, company, cusip, shares, value FROM ark_holdings"
                               " WHERE fund = ? AND day = ?", (fund, r[0])).fetchall()
        return date.fromisoformat(r[0]), [ArkHolding(*row) for row in rows]

    # ------------------------------------------------------------- alerts
    def alerted(self, uids: Iterable[str]) -> set[str]:
        uids = list(uids)
        if not uids:
            return set()
        out: set[str] = set()
        for i in range(0, len(uids), 500):
            chunk = uids[i:i + 500]
            out |= {r[0] for r in self.db.execute(
                f"SELECT uid FROM alerts_sent WHERE uid IN ({','.join('?' * len(chunk))})", chunk)}
        return out

    def mark_alerted(self, items: Iterable[tuple[str, str]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.db.executemany("INSERT OR IGNORE INTO alerts_sent VALUES (?,?,?)",
                            [(uid, kind, now) for uid, kind in items])
        self.db.commit()

    def alerts_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0]

    @staticmethod
    def _post(r) -> Post:
        return Post(investor_key=r[0], published=date.fromisoformat(r[1]), text=r[2],
                    source=r[3], url=r[4], kind=r[5])


def window(days: int, end: date | None = None) -> tuple[date, date]:
    end = end or date.today()
    return end - timedelta(days=days - 1), end
