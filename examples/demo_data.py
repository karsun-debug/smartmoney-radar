"""Fill a database with synthetic posts so the dashboard and LINE digest can be
previewed without network access. Every post is fake and marked source="demo".

    SPIDERHEAD_DB=data/demo.db python examples/demo_data.py
    SPIDERHEAD_DB=data/demo.db spiderhead dashboard --out site-demo
"""
import random
from datetime import date, timedelta

import math

from spiderhead.models import Post, Trade
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store

TEMPLATES = {
    "pelosi": ["Pelosi says AI chips are the future; very bullish on $NVDA (interview)."],
    "ackman": ["We bought a new position in $GOOGL. Undervalued AI leader.",
               "Adding to $CMG, a great business with long runway.",
               "The Fed should cut interest rates now; Treasury yields are too high."],
    "wood": ["Adding to $TSLA and $NVDA. AI adoption is accelerating, very bullish.",
             "Bitcoin is undervalued, we bought more $COIN.", "We added $GOOGL on weakness."],
    "burry": ["AI chips look like a bubble. Bought puts on $NVDA.",
              "Bank stocks are overvalued; regional bank downside ahead.", "Sold $BABA."],
    "buffett": ["Insurance float keeps growing; $CB is a great business.",
                "We trimmed $AAPL and hold record cash."],
    "druckenmiller": ["Long $NVDA? I sold it too early. Still bullish on AI.",
                      "Bond yields will rise; short Treasuries."],
    "tepper": ["Buying $BABA and $JD, China is cheap.", "The Fed will cut; bullish on stocks."],
    "dalio": ["Debt levels and Treasury yields point to recession risk; avoid bonds.",
              "Gold is undervalued relative to the dollar."],
    "loeb": ["New position in $META, advertising is recovering.", "We exited $UNH."],
    "saylor": ["Strategy has acquired more bitcoin. Bullish on crypto."],
}
INDUSTRY = {"industry:ai_semis": "Data center chip demand drives semiconductor rally",
            "industry:energy": "OPEC extends output cuts; crude oil climbs",
            "industry:financials": "Regional bank stocks slide on credit worries"}


def main(days: int = 90, seed: int = 7) -> None:
    rnd = random.Random(seed)
    today = date.today()
    posts = []
    for d in range(days):
        day = today - timedelta(days=d)
        ai_boost = 3 if d < 21 else 1          # make AI chatter rise recently
        for inv, texts in TEMPLATES.items():
            if rnd.random() < 0.12 * (ai_boost if inv in ("wood", "burry", "ackman") else 1):
                t = rnd.choice(texts)
                posts.append(Post(inv, day, t, "demo", None, "own"))
        for owner, text in INDUSTRY.items():
            if rnd.random() < 0.15:
                posts.append(Post(owner, day, f"{text} (day {d})", "demo", None, "industry"))
    store = Store()
    store.add_posts(posts)
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))

    # Synthetic prices (random walks with a drift) so call scoring has data.
    drift = {"NVDA": 0.004, "GOOGL": 0.002, "TSLA": -0.001, "BABA": 0.001, "CB": 0.0005,
             "META": 0.0015, "COIN": -0.002, "CMG": 0.0, "JD": 0.0005, "AAPL": 0.0002, "UNH": -0.001,
             "SPY": 0.0004, "KO": 0.0002, "BAC": 0.0006, "SNOW": -0.0005, "OXY": 0.0001}
    for t, mu in drift.items():
        price, rows = 100.0, []
        for d in range(days + 400, -1, -1):
            price *= math.exp(mu + rnd.gauss(0, 0.015))
            rows.append((today - timedelta(days=d), round(price, 2)))
        store.add_prices(t, rows)

    # Synthetic filed trades ("對帳單").
    trades = [
        Trade("ackman", today - timedelta(days=12), today - timedelta(days=9), "buy", "4", "GOOGL",
              "Alphabet Inc.", 2_000_000, 172.4),
        Trade("buffett", today - timedelta(days=5), today - timedelta(days=3), "buy", "4", "OXY",
              "Occidental Petroleum", 1_500_000, 48.2),
        Trade("pelosi", today - timedelta(days=40), today - timedelta(days=2), "buy", "PTR", "NVDA",
              "NVIDIA Corporation", amount_text="$1,000,001 - $5,000,000（配偶）"),
        Trade("pelosi", today - timedelta(days=38), today - timedelta(days=2), "sell", "PTR", "TSLA",
              "Tesla Inc", amount_text="$250,001 - $500,000（配偶）"),
        Trade("loeb", today - timedelta(days=20), today - timedelta(days=15), "stake", "SCHEDULE 13D",
              "META", "Meta Platforms"),
        Trade("burry", today - timedelta(days=8), today - timedelta(days=6), "sell", "4", "BABA", "Alibaba"),
    ]
    store.add_trades(trades)

    # Two synthetic 13F quarters for Buffett (the test fixtures), for the history table.
    from pathlib import Path
    from spiderhead.holdings import TickerResolver, parse_infotable
    fix = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    tick = TickerResolver({"037833100": "AAPL", "060505104": "BAC", "191216100": "KO",
                           "833445109": "SNOW", "78462F103": "SPY"}, {"Chubb Ltd": "CB"})
    q2, q3 = f"{today.year}-06-30", f"{today.year}-09-30"
    store.save_13f("buffett", q2, "demo-q2", parse_infotable((fix / "prev_q.xml").read_bytes()), tick,
                   date(today.year, 8, 14))
    store.save_13f("buffett", q3, "demo-q3", parse_infotable((fix / "curr_q.xml").read_bytes()), tick,
                   min(today, date(today.year, 11, 3)))
    # Synthetic macro series (shape only; real data comes from FRED).
    from spiderhead.macro import SERIES
    base = {"SP500": 5000, "VIXCLS": 16, "DGS10": 4.2, "DGS2": 4.0, "T10Y2Y": 0.2, "DFF": 4.3,
            "CPIAUCSL": 310, "PCEPILFE": 120, "UNRATE": 4.1, "ICSA": 220000, "BAMLH0A0HYM2": 3.2,
            "DTWEXBGS": 120, "DCOILWTICO": 75, "MORTGAGE30US": 6.5, "M2SL": 21000}
    for s in SERIES:
        v, rows = base[s.sid], []
        monthly = s.sid in ("CPIAUCSL", "PCEPILFE", "UNRATE", "M2SL")
        step = 30 if monthly else (7 if s.sid in ("ICSA", "MORTGAGE30US") else 1)
        for d in range(365 * 4, -1, -step):
            if s.yoy:
                v *= 1 + rnd.gauss(0.0025, 0.001)
            else:
                v = max(0.01, v * (1 + rnd.gauss(0.0002, 0.01))) if v > 50 else v + rnd.gauss(0, 0.03)
            rows.append((today - timedelta(days=d), round(v, 4)))
        store.add_macro(s.sid, rows)

    print(f"demo posts: {len(posts)}, trades: {len(trades)}")


if __name__ == "__main__":
    main()
