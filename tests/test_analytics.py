from datetime import date, timedelta

from spiderhead.analytics import below_guru_price, cluster_performance, movers, officer_buys
from spiderhead.dashboard import build_site
from spiderhead.insiders import Cluster, InsiderBuy
from spiderhead.models import Holding, Trade
from spiderhead.performance import PriceBook
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date.today()


def _csv(n, p0, step):
    start = TODAY - timedelta(days=n - 1)
    return "\n".join(["Date,Open,High,Low,Close,Volume"] +
                     [f"{start + timedelta(days=i)},1,1,1,{p0 + step * i:.4f},1" for i in range(n)]).encode()


def _book(store):
    feeds = {"nvda.us": _csv(200, 100, 1), "tsla.us": _csv(200, 300, -1), "spy.us": _csv(200, 400, 0.1),
             "acme.us": _csv(200, 10, 0.05)}
    return PriceBook(store, lambda url, h: feeds[url.split("s=")[1].split("&")[0]])


def test_movers_and_discounts_and_officers():
    store = Store(":memory:")
    pb = _book(store)
    ms = movers(pb, ["NVDA", "TSLA"])
    assert [m.ticker for m in ms] == ["NVDA", "TSLA"] and ms[0].change_pct > 0 > ms[1].change_pct
    store.save_13f("buffett", "2026-09-30", "a", [Holding("TESLA", "T1", "COM", 500 * 10, 10)],
                   lambda h: "TSLA")
    [d] = below_guru_price(store, load_watchlist(), pb)
    assert d.ticker == "TSLA" and d.ref_price == 500 and d.discount_pct < 0
    buys = [InsiderBuy("a", "ACME", "Acme", "Jane", "CEO", TODAY, TODAY, 100_000, 20),
            InsiderBuy("b", "ACME", "Acme", "Joe", "Director", TODAY, TODAY, 100_000, 20)]
    assert [b.owner for b in officer_buys(buys)] == ["Jane"]


def test_cluster_performance_vs_spy():
    store = Store(":memory:")
    pb = _book(store)
    last = TODAY - timedelta(days=100)
    c = Cluster("ACME", "Acme", [InsiderBuy(f"x{i}", "ACME", "Acme", f"O{i}", "Director", last, last, 1e4, 10)
                                 for i in range(3)])
    [r] = cluster_performance([c], pb)
    assert r.insiders == 3 and r.ret[30] > 0 and r.spy[30] > 0 and r.ret[90] is not None


def test_pages_have_new_sections_and_feeds(tmp_path):
    store = Store(":memory:")
    pb = _book(store)
    store.add_trades([Trade("pelosi", TODAY, TODAY, "buy", "PTR", "NVDA", "NVIDIA"),
                      Trade("burry", TODAY, TODAY, "sell", "4", "TSLA", "Tesla")])
    store.save_13f("buffett", "2026-06-30", "a", [Holding("TESLA", "T1", "COM", 5000, 10)], lambda h: "TSLA")
    store.save_13f("buffett", "2026-09-30", "b", [Holding("TESLA", "T1", "COM", 5000, 10)], lambda h: "TSLA")
    store.add_insider_buys([InsiderBuy("q", "ACME", "Acme", "Jane", "Chief Executive Officer", TODAY, TODAY, 1e5, 20)])
    build_site(store, load_watchlist(), tmp_path, days=7, prices=pb)
    assert "大神關注股：最新漲跌" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "比大神 13F 價位還便宜" in (tmp_path / "consensus.html").read_text(encoding="utf-8")
    assert "CEO / CFO 大額買進" in (tmp_path / "insiders.html").read_text(encoding="utf-8")
    feed = (tmp_path / "feeds" / "investor" / "pelosi.xml").read_text(encoding="utf-8")
    assert "Nancy Pelosi 買進 NVDA" in feed and "TSLA" not in feed
    assert "Michael Burry" in (tmp_path / "feeds" / "ticker" / "TSLA.xml").read_text(encoding="utf-8")
    assert 'feeds/ticker/NVDA.xml' in (tmp_path / "ticker" / "NVDA.html").read_text(encoding="utf-8")
