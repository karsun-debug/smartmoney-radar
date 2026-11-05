from datetime import date

from spiderhead.dashboard import build_site
from spiderhead.health import HealthLog
from spiderhead.models import Trade
from spiderhead.shorts import fetch_shorts, parse_regsho, short_ratio
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

FILE = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
{d}|NVDA|4000000|1000|10000000|B,Q,N
{d}|AAPL|3000000|0|6000000|B,Q,N
{d}|ZZZZ|5|0|0|Q
"""


def test_parse_keeps_tracked_symbols():
    rows = parse_regsho(FILE.format(d="20261103"), {"NVDA"})
    assert rows == [(date(2026, 11, 3), "NVDA", 4_000_000, 10_000_000)]


def test_fetch_and_ratio_and_ticker_page(tmp_path):
    store = Store(":memory:")
    hl = HealthLog()

    def get(url, h):
        d = url.rsplit("shvol", 1)[1][:8]
        if d == "20261102":
            raise ConnectionError("403 holiday")
        return FILE.format(d=d).encode()
    n = fetch_shorts(store, get, ["NVDA", "AAPL"], days=4, today=date(2026, 11, 5), health=hl)
    assert n == 4                                   # Nov 3 + Nov 4, two tickers; Nov 1 is a Sunday
    assert store.short_day_done(date(2026, 11, 3)) and not store.short_day_done(date(2026, 11, 2))
    avg, rows = short_ratio(store, "NVDA", today=date(2026, 11, 5))
    assert round(avg, 2) == 0.4 and len(rows) == 2
    assert {r.ok for r in hl.results} == {True, False}
    store.add_trades([Trade("pelosi", date.today(), date.today(), "buy", "PTR", "NVDA", "NVIDIA")])
    store.add_short_volume([(date.today(), "NVDA", 1, 4)])
    build_site(store, load_watchlist(), tmp_path, days=7)
    assert "放空成交量比例" in (tmp_path / "ticker" / "NVDA.html").read_text(encoding="utf-8")
