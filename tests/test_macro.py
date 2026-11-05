from datetime import date, timedelta

from spiderhead.dashboard import build_site
from spiderhead.health import HealthLog
from spiderhead.macro import fetch_macro, load_views, parse_fred_csv, signals
from spiderhead.models import Post
from spiderhead.posts import KeywordAnalyzer
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

TODAY = date(2026, 11, 5)


def test_parse_fred_csv_handles_missing():
    rows = parse_fred_csv("observation_date,DGS10\n2026-10-01,4.12\n2026-10-02,.\n2026-10-03,4.20\n")
    assert rows == [(date(2026, 10, 1), 4.12), (date(2026, 10, 3), 4.2)]
    assert parse_fred_csv("DATE,X\n2026-01-01,1\n") == [(date(2026, 1, 1), 1.0)]


def _monthly(start_value, step, months=40):
    out, d, v = [], date(2023, 7, 1), start_value
    for _ in range(months):
        out.append((d, v))
        v += step
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def test_views_yoy_and_signals():
    store = Store(":memory:")
    store.add_macro("CPIAUCSL", _monthly(300, 1.0))
    store.add_macro("T10Y2Y", [(TODAY - timedelta(days=i), -0.3) for i in range(30)])
    store.add_macro("UNRATE", [(d, 3.5 if i < 30 else 4.3) for i, (d, _) in enumerate(_monthly(0, 0))])
    store.add_macro("VIXCLS", [(TODAY, 32.0)])
    views = {m.series.sid: m for m in load_views(store, today=TODAY)}
    cpi = views["CPIAUCSL"]
    assert cpi.points and 3.5 < cpi.latest[1] < 4.5            # ~12/330 ≈ 3.6–4%
    sig = {s.name: s for s in signals(list(views.values()))}
    assert sig["殖利率曲線"].level == "alert" and "倒掛" in sig["殖利率曲線"].text
    assert sig["薩姆規則（失業率）"].level == "alert"
    assert sig["市場情緒（VIX）"].level == "alert"


def test_fetch_and_page(tmp_path):
    store = Store(":memory:")
    hl = HealthLog()

    def get(url, h):
        sid = url.split("id=")[1].split("&")[0]
        if sid == "SP500":
            raise ConnectionError("down")
        days = [date.today() - timedelta(days=i) for i in range(400, 0, -7)]
        return ("observation_date," + sid + "\n" + "\n".join(f"{d},{4 + i * 0.01}" for i, d in enumerate(days))).encode()
    n = fetch_macro(store, get, health=hl)
    assert n > 0 and any(not r.ok for r in hl.results)
    store.add_posts([Post("dalio", date.today(), "Ray Dalio says Treasury yields and debt point to recession risk",
                          "news", "https://n/1", "news")])
    for p in store.unanalyzed():
        store.save_signals(p, KeywordAnalyzer().analyze(p))
    build_site(store, load_watchlist(), tmp_path, days=7)
    html = (tmp_path / "macro.html").read_text(encoding="utf-8")
    assert "10 年期美債殖利率" in html and "總經訊號" in html and "Ray Dalio" in html
