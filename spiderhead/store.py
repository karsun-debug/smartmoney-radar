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
    sector TEXT, stance TEXT NOT NULL, confidence REAL, rationale TEXT,
    attributed INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS source_runs (
    at TEXT NOT NULL, family TEXT NOT NULL, label TEXT NOT NULL, ok INTEGER NOT NULL,
    items INTEGER NOT NULL DEFAULT 0, detail TEXT
);
CREATE INDEX IF NOT EXISTS source_runs_family ON source_runs(family, at);
CREATE TABLE IF NOT EXISTS holdings_13f (
    investor TEXT NOT NULL, quarter TEXT NOT NULL, accession TEXT NOT NULL, cusip TEXT NOT NULL,
    put_call TEXT NOT NULL DEFAULT '', ticker TEXT, issuer TEXT, title TEXT, shares INTEGER,
    value INTEGER, filed TEXT, PRIMARY KEY (investor, quarter, cusip, put_call)
);
CREATE TABLE IF NOT EXISTS insider_buys (
    accession TEXT NOT NULL, ticker TEXT NOT NULL, issuer TEXT, owner TEXT NOT NULL, title TEXT,
    traded TEXT NOT NULL, filed TEXT NOT NULL, shares REAL NOT NULL, price REAL NOT NULL,
    PRIMARY KEY (accession, owner, traded, shares, price)
);
CREATE INDEX IF NOT EXISTS insider_buys_traded ON insider_buys(traded);
CREATE TABLE IF NOT EXISTS insider_days (day TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS reports (
    period_end TEXT PRIMARY KEY, period_start TEXT NOT NULL, text TEXT NOT NULL,
    author TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS short_volume (
    ticker TEXT NOT NULL, day TEXT NOT NULL, short REAL NOT NULL, total REAL NOT NULL,
    PRIMARY KEY (ticker, day)
);
CREATE TABLE IF NOT EXISTS short_days (day TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS pageviews (
    investor TEXT NOT NULL, day TEXT NOT NULL, views INTEGER NOT NULL, PRIMARY KEY (investor, day)
);
CREATE TABLE IF NOT EXISTS macro (
    series TEXT NOT NULL, day TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (series, day)
);
CREATE TABLE IF NOT EXISTS bank_calls (
    bank TEXT NOT NULL, published TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL,
    stance TEXT NOT NULL, rating TEXT, target REAL, prior_target REAL, headline TEXT, url TEXT,
    PRIMARY KEY (bank, ticker, published, action)
);
CREATE TABLE IF NOT EXISTS fin_meta (
    ticker TEXT PRIMARY KEY, cik TEXT, name TEXT, fetched TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fin_values (
    ticker TEXT NOT NULL, period_end TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (ticker, period_end, metric)
);
CREATE TABLE IF NOT EXISTS earnings_news (
    ticker TEXT NOT NULL, published TEXT NOT NULL, title TEXT NOT NULL, url TEXT,
    PRIMARY KEY (ticker, title)
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
        # check_same_thread=False: the MCP server answers tool calls from worker
        # threads; SQLite itself is built serialized (thread-safe) in CPython.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(signals)")}
        if "attributed" not in cols:
            self.db.execute("ALTER TABLE signals ADD COLUMN attributed INTEGER NOT NULL DEFAULT 0")
            self.db.commit()
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(holdings_13f)")}
        if "filed" not in cols:
            self.db.execute("ALTER TABLE holdings_13f ADD COLUMN filed TEXT")
            self.db.commit()

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
            "INSERT INTO signals (post_uid, ticker, sector, stance, confidence, rationale, attributed)"
            " VALUES (?,?,?,?,?,?,?)",
            [(post.uid, s.ticker, s.sector, s.stance.value, s.confidence, s.rationale,
              int(s.attributed)) for s in signals])
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

    def signals_between(self, start: date, end: date,
                        kinds: tuple[str, ...] = ("own", "news", "industry")) -> list[Signal]:
        """Signals from posts in [start, end]. Retail chatter (kind "retail") is
        excluded unless asked for, so it never mixes into investor trends."""
        rows = self.db.execute(
            "SELECT p.investor, p.published, p.text, p.source, p.url, p.kind,"
            " s.ticker, s.stance, s.confidence, s.rationale, s.sector, s.attributed"
            " FROM signals s JOIN posts p ON p.uid = s.post_uid"
            f" WHERE p.published >= ? AND p.published <= ? AND p.kind IN ({','.join('?' * len(kinds))})",
            (start.isoformat(), end.isoformat(), *kinds)).fetchall()
        return [Signal(r[6], Stance(r[7]), r[8], r[9], self._post(r[:6]), r[10] or "other",
                       bool(r[11])) for r in rows]

    def signals_for(self, post: Post) -> list[Signal]:
        rows = self.db.execute("SELECT ticker, stance, confidence, rationale, sector, attributed"
                               " FROM signals WHERE post_uid = ?", (post.uid,)).fetchall()
        return [Signal(r[0], Stance(r[1]), r[2], r[3], post, r[4] or "other", bool(r[5]))
                for r in rows]

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

    # ------------------------------------------------------------- 13F history
    def save_13f(self, investor: str, quarter: str, accession: str, holdings,
                 resolve=None, filed: date | None = None) -> None:
        """Replace one quarter's 13F holdings (tickers resolved once, at save time)."""
        self.db.execute("DELETE FROM holdings_13f WHERE investor = ? AND quarter = ?", (investor, quarter))
        self.db.executemany(
            "INSERT OR REPLACE INTO holdings_13f (investor, quarter, accession, cusip, put_call, ticker,"
            " issuer, title, shares, value, filed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(investor, quarter, accession, h.cusip, h.put_call or "", resolve(h) if resolve else None,
              h.issuer, h.title_of_class, h.shares, h.value_usd, filed.isoformat() if filed else None)
             for h in holdings])
        self.db.commit()

    def filed_13f(self, investor: str, quarter: str) -> date:
        """Filing date of a stored quarter (quarter end + 45 days when unknown)."""
        r = self.db.execute("SELECT MAX(filed) FROM holdings_13f WHERE investor = ? AND quarter = ?",
                            (investor, quarter)).fetchone()
        if r and r[0]:
            return date.fromisoformat(r[0])
        return date.fromisoformat(quarter) + timedelta(days=45)

    def accession_13f(self, investor: str, quarter: str) -> str | None:
        r = self.db.execute("SELECT accession FROM holdings_13f WHERE investor = ? AND quarter = ? LIMIT 1",
                            (investor, quarter)).fetchone()
        return r[0] if r else None

    def quarters_13f(self, investor: str) -> list[str]:
        """Stored quarters, newest first."""
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT quarter FROM holdings_13f WHERE investor = ? ORDER BY quarter DESC", (investor,))]

    def load_13f(self, investor: str, quarter: str):
        """(holdings, {cusip/put_call key: ticker}) for one stored quarter."""
        from .models import Holding
        holdings, tickers = [], {}
        for cusip, pc, ticker, issuer, title, shares, value in self.db.execute(
                "SELECT cusip, put_call, ticker, issuer, title, shares, value FROM holdings_13f"
                " WHERE investor = ? AND quarter = ?", (investor, quarter)):
            h = Holding(issuer or "", cusip, title or "", value or 0, shares or 0, "SH", pc or None)
            holdings.append(h)
            if ticker:
                tickers[h.key] = ticker
        return holdings, tickers

    # ------------------------------------------------------------- insider buys
    def add_insider_buys(self, buys) -> int:
        cur = self.db.executemany(
            "INSERT OR IGNORE INTO insider_buys VALUES (?,?,?,?,?,?,?,?,?)",
            [(b.accession, b.ticker, b.issuer, b.owner, b.title, b.traded.isoformat(),
              b.filed.isoformat(), b.shares, b.price) for b in buys])
        self.db.commit()
        return cur.rowcount

    def insider_buys_between(self, start: date, end: date):
        from .insiders import InsiderBuy
        return [InsiderBuy(r[0], r[1], r[2] or "", r[3], r[4] or "", date.fromisoformat(r[5]),
                           date.fromisoformat(r[6]), r[7], r[8])
                for r in self.db.execute("SELECT * FROM insider_buys WHERE traded >= ? AND traded <= ?"
                                         " ORDER BY traded DESC", (start.isoformat(), end.isoformat()))]

    def insider_day_done(self, day: date) -> bool:
        return self.db.execute("SELECT 1 FROM insider_days WHERE day = ?", (day.isoformat(),)).fetchone() is not None

    def mark_insider_day(self, day: date) -> None:
        self.db.execute("INSERT OR IGNORE INTO insider_days VALUES (?)", (day.isoformat(),))
        self.db.commit()

    # ------------------------------------------------------------- weekly reports
    def save_report(self, start: date, end: date, text: str, author: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?)",
                        (end.isoformat(), start.isoformat(), text, author,
                         datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def reports(self, limit: int = 12) -> list[tuple[date, date, str, str]]:
        """(start, end, text, author), newest first."""
        return [(date.fromisoformat(r[1]), date.fromisoformat(r[0]), r[2], r[3]) for r in self.db.execute(
            "SELECT period_end, period_start, text, author FROM reports ORDER BY period_end DESC LIMIT ?",
            (limit,))]

    # ------------------------------------------------------------- short volume
    def add_short_volume(self, rows) -> None:
        """rows: (day, ticker, short, total). FINRA splits by venue file; we keep the consolidated one."""
        self.db.executemany("INSERT OR REPLACE INTO short_volume VALUES (?,?,?,?)",
                            [(t, d.isoformat(), s, tot) for d, t, s, tot in rows])
        self.db.commit()

    def short_volume(self, ticker: str, since: date) -> list[tuple[date, float, float]]:
        return [(date.fromisoformat(d), s, t) for d, s, t in self.db.execute(
            "SELECT day, short, total FROM short_volume WHERE ticker = ? AND day >= ? ORDER BY day",
            (ticker.upper(), since.isoformat()))]

    def short_day_done(self, day: date) -> bool:
        return self.db.execute("SELECT 1 FROM short_days WHERE day = ?", (day.isoformat(),)).fetchone() is not None

    def mark_short_day(self, day: date) -> None:
        self.db.execute("INSERT OR IGNORE INTO short_days VALUES (?)", (day.isoformat(),))
        self.db.commit()

    # ------------------------------------------------------------- buzz
    def add_pageviews(self, investor: str, rows) -> None:
        self.db.executemany("INSERT OR REPLACE INTO pageviews VALUES (?,?,?)",
                            [(investor, d.isoformat(), v) for d, v in rows])
        self.db.commit()

    def pageviews(self, investor: str, since: date) -> list[tuple[date, int]]:
        return [(date.fromisoformat(d), v) for d, v in self.db.execute(
            "SELECT day, views FROM pageviews WHERE investor = ? AND day >= ? ORDER BY day",
            (investor, since.isoformat()))]

    # ------------------------------------------------------------- macro
    def add_macro(self, series: str, rows) -> None:
        self.db.executemany("INSERT OR REPLACE INTO macro VALUES (?,?,?)",
                            [(series, d.isoformat(), v) for d, v in rows])
        self.db.commit()

    def macro(self, series: str, since: date) -> list[tuple[date, float]]:
        return [(date.fromisoformat(d), v) for d, v in self.db.execute(
            "SELECT day, value FROM macro WHERE series = ? AND day >= ? ORDER BY day", (series, since.isoformat()))]

    # ------------------------------------------------------------- bank calls
    def add_bank_calls(self, calls) -> int:
        cur = self.db.executemany(
            "INSERT OR IGNORE INTO bank_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(c.bank, c.published.isoformat(), c.ticker, c.action, c.stance, c.rating, c.target,
              c.prior_target, c.headline, c.url) for c in calls])
        self.db.commit()
        return cur.rowcount

    def bank_calls(self, start: date, end: date, ticker: str | None = None):
        from .banks import BankCall
        q = "SELECT * FROM bank_calls WHERE published >= ? AND published <= ?"
        args: list = [start.isoformat(), end.isoformat()]
        if ticker:
            q += " AND ticker = ?"
            args.append(ticker.upper())
        return [BankCall(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4], r[5], r[6], r[7], r[8] or "", r[9])
                for r in self.db.execute(q + " ORDER BY published DESC", args)]

    # ------------------------------------------------------------- financials
    def financials_fetched(self, ticker: str) -> date | None:
        r = self.db.execute("SELECT fetched FROM fin_meta WHERE ticker = ?", (ticker.upper(),)).fetchone()
        return date.fromisoformat(r[0]) if r else None

    def save_financials(self, ticker: str, cik: str, name: str, quarters) -> None:
        t = ticker.upper()
        self.db.execute("DELETE FROM fin_values WHERE ticker = ?", (t,))
        self.db.executemany("INSERT OR REPLACE INTO fin_values VALUES (?,?,?,?)",
                            [(t, q.end.isoformat(), k, v) for q in quarters for k, v in q.values.items()])
        self.db.execute("INSERT OR REPLACE INTO fin_meta VALUES (?,?,?,?)",
                        (t, cik, name, date.today().isoformat()))
        self.db.commit()

    def load_financials(self, ticker: str):
        from .financials import Quarter, Snapshot
        t = ticker.upper()
        meta = self.db.execute("SELECT name FROM fin_meta WHERE ticker = ?", (t,)).fetchone()
        if not meta:
            return None
        by_end: dict[str, dict[str, float]] = {}
        for end, metric, value in self.db.execute(
                "SELECT period_end, metric, value FROM fin_values WHERE ticker = ? ORDER BY period_end", (t,)):
            by_end.setdefault(end, {})[metric] = value
        return Snapshot(t, meta[0] or t, [Quarter(date.fromisoformat(e), v) for e, v in by_end.items()])

    def financial_tickers(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT ticker FROM fin_meta ORDER BY ticker")]

    def add_earnings_news(self, rows) -> int:
        cur = self.db.executemany("INSERT OR IGNORE INTO earnings_news VALUES (?,?,?,?)",
                                  [(t, d.isoformat(), title, url) for t, d, title, url in rows])
        self.db.commit()
        return cur.rowcount

    def earnings_news(self, ticker: str, limit: int = 10) -> list[tuple[date, str, str | None]]:
        return [(date.fromisoformat(d), title, url) for d, title, url in self.db.execute(
            "SELECT published, title, url FROM earnings_news WHERE ticker = ? ORDER BY published DESC LIMIT ?",
            (ticker.upper(), limit))]

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

    # ------------------------------------------------------------- health
    def log_results(self, results, at: datetime | None = None) -> None:
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        self.db.executemany("INSERT INTO source_runs VALUES (?,?,?,?,?,?)",
                            [(stamp, r.family, r.label, int(r.ok), r.items, r.detail) for r in results])
        # Keep 60 days of history.
        cutoff = ((at or datetime.now()) - timedelta(days=60)).isoformat(timespec="seconds")
        self.db.execute("DELETE FROM source_runs WHERE at < ?", (cutoff,))
        self.db.commit()

    def source_health(self):
        from .health import FamilyStatus
        out = []
        week = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        for (family,) in self.db.execute("SELECT DISTINCT family FROM source_runs ORDER BY family"):
            last_run = self.db.execute("SELECT MAX(at) FROM source_runs WHERE family = ?",
                                       (family,)).fetchone()[0]
            last_ok = self.db.execute("SELECT MAX(at) FROM source_runs WHERE family = ? AND ok = 1",
                                      (family,)).fetchone()[0]
            ok_now, bad_now = self.db.execute(
                "SELECT SUM(ok), SUM(1 - ok) FROM source_runs WHERE family = ? AND at = ?",
                (family, last_run)).fetchone()
            items = self.db.execute("SELECT COALESCE(SUM(items), 0) FROM source_runs"
                                    " WHERE family = ? AND at >= ?", (family, week)).fetchone()[0]
            err = self.db.execute("SELECT label || ': ' || detail FROM source_runs WHERE family = ?"
                                  " AND ok = 0 AND at = ? LIMIT 1", (family, last_run)).fetchone()
            out.append(FamilyStatus(family, datetime.fromisoformat(last_run),
                                    datetime.fromisoformat(last_ok) if last_ok else None,
                                    ok_now or 0, bad_now or 0, items, err[0] if err else ""))
        return out

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
