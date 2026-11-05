"""SQLite storage for collected posts and the signals extracted from them."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Post, Signal, Stance

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

    @staticmethod
    def _post(r) -> Post:
        return Post(investor_key=r[0], published=date.fromisoformat(r[1]), text=r[2],
                    source=r[3], url=r[4], kind=r[5])


def window(days: int, end: date | None = None) -> tuple[date, date]:
    end = end or date.today()
    return end - timedelta(days=days - 1), end
