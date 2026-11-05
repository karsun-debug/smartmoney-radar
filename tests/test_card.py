import json
from datetime import date

import pytest

from spiderhead.card import card_paths, compact_amount, render_card, write_preview
from spiderhead.channels import Broadcast, Discord, Email, Image, Slack, Telegram
from spiderhead.digest import build_digest
from spiderhead.models import Trade
from spiderhead.notify import LineNotifier
from spiderhead.store import Store
from spiderhead.watchlist import load_watchlist

pytest.importorskip("matplotlib")


def test_compact_amount():
    assert compact_amount("$1,000,001 - $5,000,000（配偶）") == "$1M–5M"
    assert compact_amount("$15,001 - $50,000") == "$15K–50K"
    assert compact_amount("ARKK +50,000 股") == "ARKK +50,000 股"


def test_render_card_png(tmp_path):
    store = Store(":memory:")
    store.add_trades([Trade("pelosi", date.today(), date.today(), "buy", "PTR", "NVDA", "NVIDIA",
                            amount_text="$1,000,001 - $5,000,000（配偶）")])
    full, preview = card_paths(tmp_path)
    out = render_card(build_digest(store, load_watchlist(), 1), store, full)
    assert out == full and full.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image as PILImage
    with PILImage.open(full) as im:
        assert im.size == (1080, 1920)
    assert write_preview(full, preview) == preview and preview.stat().st_size < full.stat().st_size


def test_channels_send_images(tmp_path):
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNG fake")
    img = Image(str(png), "https://site/img/c.png", "https://site/img/c-p.png", "快報")
    uploads, posts = [], []
    up = lambda url, data, files: uploads.append((url, data, list(files)))  # noqa: E731
    post = lambda url, h, body: posts.append((url, json.loads(body)))       # noqa: E731
    Telegram("tok", "42", post, up).send_image(img)
    Discord("https://hook", post, up).send_image(img)
    Slack("https://slack", post).send_image(img)
    assert uploads[0][0].endswith("/sendPhoto") and uploads[0][1]["chat_id"] == "42" and uploads[0][2] == ["photo"]
    assert uploads[1][2] == ["file"] and "payload_json" in uploads[1][1]
    assert posts[0][1]["blocks"][0]["image_url"] == "https://site/img/c.png"
    line = LineNotifier("t", "U1", post)
    line.send_image(img.url, img.preview_url)
    assert posts[1][1]["messages"][0] == {"type": "image", "originalContentUrl": "https://site/img/c.png",
                                          "previewImageUrl": "https://site/img/c-p.png"}
    sent = []

    class FakeSMTP:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append([p.get_filename() for p in msg.iter_attachments()])
    Email("h", 587, "u", "p", "to", smtp_factory=FakeSMTP).send_image(img)
    assert sent == [["c.png"]]
    # A channel that can't send images is reported, not fatal.
    res = Broadcast([Slack("https://s", post)]).send_image(Image(str(png)))
    assert res[0][1] and "public image URL" in res[0][1]
