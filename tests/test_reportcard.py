from datetime import date, timedelta

from spiderhead.clone import CloneResult, Period
from spiderhead.dashboard import build_site
from spiderhead.models import Post, Trade
from spiderhead.performance import PriceBook, score_calls
from spiderhead.posts import KeywordAnalyzer
from spiderhead.reportcard import ReportCard, build_cards
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


def test_card_scoring_rules():
    c = ReportCard("x", consistent=3, contradicts=1, calls_scored=4, call_hit_rate=0.5, call_horizon=90,
                   clone_excess=8.0, clone_quarters=4)
    assert c.parts == {"說到做到": 75.0, "喊單準確": 50.0, "跟單價值": 70.0}
    assert round(c.score) == 65 and c.grade == "B"
    thin = ReportCard("y", consistent=1, contradicts=0, calls_scored=2, call_hit_rate=1.0, clone_excess=50,
                      clone_quarters=1)
    assert thin.parts == {} and thin.grade == "—"                  # too little data -> no grade
    assert ReportCard("z", clone_excess=-40, clone_quarters=3).parts["跟單價值"] == 0


def test_build_cards_from_store(tmp_path):
    store = Store(":memory:")
    posts = [Post("pelosi", TODAY - timedelta(days=30 + i), f"Bullish on $NVDA, buying more ({i})", "x",
                  f"https://x/{i}") for i in range(3)]
    posts.append(Post("pelosi", TODAY - timedelta(days=40), "Selling $TSLA, overvalued", "x", "https://x/t"))
    store.add_posts(posts)
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    store.add_trades([Trade("pelosi", TODAY - timedelta(days=25), TODAY - timedelta(days=5), "buy", "PTR",
                            "NVDA", "NVIDIA", amount_text="$1M"),
                      Trade("pelosi", TODAY - timedelta(days=35), TODAY - timedelta(days=5), "buy", "PTR",
                            "TSLA", "Tesla", amount_text="$1M")])
    rows = "\n".join(["Date,Open,High,Low,Close,Volume"] +
                     [f"{TODAY - timedelta(days=120 - i)},1,1,1,{100 + i},1" for i in range(121)])
    pb = PriceBook(store, lambda url, h: rows.encode())
    calls = score_calls(store.signals_between(TODAY - timedelta(days=200), TODAY), pb)
    clones = [CloneResult("buffett", [Period("q1", TODAY, TODAY, 10.0, 2.0, 1, []),
                                      Period("q2", TODAY, TODAY, 5.0, 1.0, 1, [])])]
    cards = build_cards(store, load_watchlist(), calls, clones)
    pel = cards["pelosi"]
    assert (pel.consistent, pel.contradicts) == (3, 1) and pel.parts["說到做到"] == 75
    assert cards["buffett"].parts["跟單價值"] > 50
    build_site(store, load_watchlist(), tmp_path, days=60, prices=pb)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "大神評分卡" in html and "Nancy Pelosi" in html
    assert "評分卡" in (tmp_path / "investor" / "pelosi.html").read_text(encoding="utf-8")
