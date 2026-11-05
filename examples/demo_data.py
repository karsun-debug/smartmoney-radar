"""Fill a database with synthetic posts so the dashboard and LINE digest can be
previewed without network access. Every post is fake and marked source="demo".

    SPIDERHEAD_DB=data/demo.db python examples/demo_data.py
    SPIDERHEAD_DB=data/demo.db spiderhead dashboard --out site-demo
"""
import random
from datetime import date, timedelta

from spiderhead.models import Post
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store

TEMPLATES = {
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
    print(f"demo posts: {len(posts)}")


if __name__ == "__main__":
    main()
