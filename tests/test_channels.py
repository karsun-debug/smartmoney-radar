import json

import pytest

from spiderhead.channels import Broadcast, Discord, Email, Slack, Telegram, configured_channels


def test_configured_channels_from_env():
    env = {"LINE_CHANNEL_ACCESS_TOKEN": "t", "TELEGRAM_BOT_TOKEN": "b", "TELEGRAM_CHAT_ID": "1",
           "DISCORD_WEBHOOK_URL": "https://d", "SLACK_WEBHOOK_URL": "https://s",
           "SMTP_HOST": "smtp", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "EMAIL_TO": "me@x"}
    assert [c.name for c in configured_channels(env)] == ["LINE", "Telegram", "Discord", "Slack", "Email"]
    assert configured_channels({"TELEGRAM_BOT_TOKEN": "b"}) == []          # chat id missing


def test_chunking_per_channel_limits():
    sent = []
    post = lambda url, h, body: sent.append((url, json.loads(body)))  # noqa: E731
    text = "\n".join(["x" * 100] * 50)                                   # ~5,000 chars
    Telegram("tok", "42", post).send(text)
    assert sent[0][0] == "https://api.telegram.org/bottok/sendMessage" and len(sent) == 2
    assert all(len(b["text"]) <= 4000 and b["chat_id"] == "42" for _, b in sent)
    sent.clear()
    Discord("https://hook", post).send(text)
    assert len(sent) == 3 and all(len(b["content"]) <= 1900 for _, b in sent)
    assert sent[0][1]["allowed_mentions"] == {"parse": []}                 # no @everyone pings
    sent.clear()
    Slack("https://hook", post).send("hi")
    assert sent == [("https://hook", {"text": "hi"})]


def test_email_uses_smtp():
    log = []

    class FakeSMTP:
        def __init__(self, host, port):
            log.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, u, p):
            log.append(("login", u))

        def send_message(self, msg):
            log.append(("send", msg["Subject"], msg["To"]))
    Email("smtp.x", 587, "me@x", "pw", "you@y", smtp_factory=FakeSMTP).send("📊 摘要\nbody")
    assert log == [("connect", "smtp.x", 587), ("login", "me@x"), ("send", "📊 摘要", "you@y")]


def test_broadcast_isolates_failures():
    class Ok:
        name = "ok"

        def send(self, t):
            pass

    class Bad:
        name = "bad"

        def send(self, t):
            raise ConnectionError("down")
    assert Broadcast([Ok(), Bad()]).send("x") == [("ok", None), ("bad", "ConnectionError: down")]
    with pytest.raises(RuntimeError):
        Broadcast([Bad()]).send("x")
