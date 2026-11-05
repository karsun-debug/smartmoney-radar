from datetime import date

import pytest

from spiderhead.banks import NameResolver, parse_headline

R = NameResolver({"SNOWFLAKE INC.": "SNOW", "ADVANCED MICRO DEVICES INC": "AMD", "INTEL CORP": "INTC",
                  "BEST BUY CO INC": "BBY"})
D = date(2026, 11, 3)

CASES = [
    ("Goldman Sachs upgrades Nvidia to Buy, raises price target to $200",
     ("goldman", "NVDA", "upgrade", "bullish", "Buy", 200.0, None)),
    ("Morgan Stanley downgrades Tesla (TSLA) to Underweight",
     ("morganstanley", "TSLA", "downgrade", "bearish", "Underweight", None, None)),
    ("JPMorgan raises Apple price target to $250 from $230, keeps Overweight",
     ("jpmorgan", "AAPL", "raise_pt", "bullish", "Overweight", 250.0, 230.0)),
    ("Citi initiates coverage on Snowflake with Buy rating",
     ("citi", "SNOW", "initiate", "bullish", "Buy", None, None)),
    ("BofA cuts Intel price target to $25; reiterates Underperform",
     ("bofa", "INTC", "cut_pt", "bearish", "Underperform", 25.0, None)),
    ("UBS downgrades Advanced Micro Devices to Neutral",
     ("ubs", "AMD", "downgrade", "bearish", "Neutral", None, None)),
    ("Barclays initiates Palantir at Equal Weight, $90 price target",
     ("barclays", "PLTR", "initiate", "neutral", "Equal Weight", 90.0, None)),
    ("Wells Fargo lifts NASDAQ:MSFT target to $600",
     ("wellsfargo", "MSFT", "raise_pt", "bullish", None, 600.0, None)),
    ("Goldman Sachs upgrades Target to Buy, PT $150",
     ("goldman", "TGT", "upgrade", "bullish", "Buy", 150.0, None)),
    ("Morgan Stanley upgrades Best Buy to Overweight",
     ("morganstanley", "BBY", "upgrade", "bullish", "Overweight", None, None)),
]


@pytest.mark.parametrize("title,expected", CASES)
def test_parse_headlines(title, expected):
    c = parse_headline(title, R, D, "https://n")
    assert c is not None, title
    assert (c.bank, c.ticker, c.action, c.stance, c.rating, c.target, c.prior_target) == expected


@pytest.mark.parametrize("title", [
    "Stocks rally as Fed holds rates steady",                       # no bank
    "Goldman Sachs reports record trading revenue",                  # bank, no action
    "Morgan Stanley upgrades outlook for the economy",               # no company
])
def test_ignores_non_calls(title):
    assert parse_headline(title, R, D) is None


from datetime import timedelta  # noqa: E402

from spiderhead.banks import BankCall, bank_scores, says_vs_holds, score_bank_calls  # noqa: E402
from spiderhead.dashboard import build_site  # noqa: E402
from spiderhead.models import Holding, Post, Trade  # noqa: E402
from spiderhead.performance import PriceBook  # noqa: E402
from spiderhead.posts import KeywordAnalyzer  # noqa: E402
from spiderhead.store import Store  # noqa: E402
from spiderhead.watchlist import load_watchlist  # noqa: E402


def _csv(start, n, p0, step):
    return "\n".join(["Date,Open,High,Low,Close,Volume"] +
                     [f"{start + timedelta(days=i)},1,1,1,{p0 + step * i:.4f},1" for i in range(n)]).encode()


def _book(store, today):
    start = today - timedelta(days=500)
    feeds = {"nvda.us": _csv(start, 501, 100, 0.5), "intc.us": _csv(start, 501, 50, -0.05),
             "spy.us": _csv(start, 501, 400, 0.2)}
    return PriceBook(store, lambda url, h: feeds[url.split("s=")[1].split("&")[0]])


def test_scoring_targets_and_excess():
    today = date.today()
    store = Store(":memory:")
    pb = _book(store, today)
    d = today - timedelta(days=400)
    calls = [BankCall("goldman", d, "NVDA", "upgrade", "bullish", "Buy", 150.0, None, "h", None),     # hit
             BankCall("goldman", d, "INTC", "upgrade", "bullish", "Buy", 80.0, None, "h", None),      # wrong, no target
             BankCall("citi", today - timedelta(days=10), "NVDA", "downgrade", "bearish", "Sell", 10.0, None, "h", None)]
    outs = score_bank_calls(calls, pb, today=today)
    assert outs[0].hit(90) is True and outs[0].target_hit is True and outs[0].excess[90] > 0
    assert outs[1].hit(90) is False and outs[1].target_hit is False                # a year passed, never reached
    assert outs[2].hit(90) is None and outs[2].target_hit is None                  # too recent
    g = bank_scores(outs)["goldman"]
    assert (g.calls, g.scored_90, g.hits_90, g.targets_resolved, g.targets_hit) == (2, 2, 1, 2, 1)
    assert g.avg_upside > 0


def test_says_vs_holds_flags_selling_while_upgrading(tmp_path):
    store = Store(":memory:")
    inv = "bank:goldman"
    store.save_13f(inv, "2026-06-30", "a", [Holding("NVIDIA CORP", "67066G104", "COM", 1000, 1_000_000)],
                   lambda h: "NVDA")
    store.save_13f(inv, "2026-09-30", "b", [Holding("NVIDIA CORP", "67066G104", "COM", 300, 200_000)],
                   lambda h: "NVDA")
    call = BankCall("goldman", date(2026, 8, 12), "NVDA", "upgrade", "bullish", "Buy", 200.0, None,
                    "Goldman Sachs upgrades Nvidia to Buy", "https://n/1")
    [m] = says_vs_holds(store, [call])
    assert (m.change, m.shares_before, m.shares_after, m.quarter) == ("TRIM", 1_000_000, 200_000, "2026-09-30")
    # Bearish call while trimming is consistent -> no flag.
    assert says_vs_holds(store, [BankCall("goldman", date(2026, 8, 12), "NVDA", "downgrade", "bearish",
                                          "Sell", None, None, "h", None)]) == []


def test_banks_page(tmp_path):
    today = date.today()
    store = Store(":memory:")
    pb = _book(store, today)
    store.add_bank_calls([BankCall("goldman", today - timedelta(days=5), "NVDA", "downgrade", "bearish", "Sell",
                                   90.0, None, "Goldman Sachs downgrades Nvidia to Sell", "javascript:alert(1)")])
    store.add_posts([Post("wood", today - timedelta(days=3), "Adding to $NVDA, very bullish", "x")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    store.add_trades([Trade("pelosi", today - timedelta(days=20), today, "buy", "PTR", "NVDA", "NVIDIA")])
    build_site(store, load_watchlist(), tmp_path, days=7, prices=pb)
    html = (tmp_path / "banks.html").read_text(encoding="utf-8")
    assert "投行準確度排行" in html and "看法相反" in html and "Cathie Wood" in html and "Nancy Pelosi" in html
    assert "javascript:" not in html
    assert "投行評等（近一年）" in (tmp_path / "ticker" / "NVDA.html").read_text(encoding="utf-8")
