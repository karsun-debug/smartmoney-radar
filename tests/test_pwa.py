import json

from spiderhead.dashboard import build_site
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist


def test_site_is_installable(tmp_path):
    build_site(Store(":memory:"), load_watchlist(), tmp_path, days=7)
    man = json.loads((tmp_path / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["display"] == "standalone" and man["start_url"] == "./index.html" and man["scope"] == "./"
    for icon in man["icons"]:
        assert (tmp_path / icon["src"]).read_bytes()[:4] == b"\x89PNG"
    assert (tmp_path / "assets" / "icons" / "apple-touch-icon.png").exists()
    sw = (tmp_path / "sw.js").read_text(encoding="utf-8")
    assert 'const CACHE = "smr-' in sw and "./index.html" in sw and "./more.html" in sw
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    for tag in ('rel="manifest"', 'apple-mobile-web-app-capable', 'apple-touch-icon', 'viewport-fit=cover',
                'class="tabbar"', 'id="sh-offline"'):
        assert tag in index, tag
    inv = (tmp_path / "investor" / "ackman.html").read_text(encoding="utf-8")
    assert 'href="../manifest.webmanifest"' in inv and 'href="../more.html"' in inv
    assert "serviceWorker" in (tmp_path / "assets" / "app.js").read_text(encoding="utf-8")
    assert "加入主畫面" in (tmp_path / "more.html").read_text(encoding="utf-8")
