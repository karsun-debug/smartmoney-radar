"""Notification channels. Every configured channel gets the same message.

| Channel  | Environment variables |
|----------|-----------------------|
| LINE     | LINE_CHANNEL_ACCESS_TOKEN (+ optional LINE_TO) |
| Telegram | TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID |
| Discord  | DISCORD_WEBHOOK_URL |
| Slack    | SLACK_WEBHOOK_URL |
| Email    | SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_TO (+ optional SMTP_PORT, EMAIL_FROM) |
"""
from __future__ import annotations

import json
import os
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage

from .notify import LineNotifier, split_text

HttpPost = Callable[[str, dict[str, str], bytes], None]
Upload = Callable[[str, dict, dict], None]          # (url, form fields, {name: (filename, bytes, mime)})


def _upload(url: str, data: dict, files: dict) -> None:
    import requests
    resp = requests.post(url, data=data, files=files, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")


@dataclass(frozen=True)
class Image:
    path: str                       # local PNG
    url: str | None = None          # public HTTPS copy (needed by LINE / Slack)
    preview_url: str | None = None
    caption: str = ""


def _post(url: str, headers: dict[str, str], body: bytes) -> None:
    import requests
    resp = requests.post(url, headers=headers, data=body, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")


class Telegram:
    name = "Telegram"

    def __init__(self, token: str, chat_id: str, post: HttpPost = _post, upload: Upload = _upload):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.photo_url = f"https://api.telegram.org/bot{token}/sendPhoto"
        self.chat_id = chat_id
        self.post = post
        self.upload = upload

    def send_image(self, img: "Image") -> None:
        with open(img.path, "rb") as fh:
            self.upload(self.photo_url, {"chat_id": self.chat_id, "caption": img.caption[:1000]},
                        {"photo": (os.path.basename(img.path), fh.read(), "image/png")})

    def send(self, text: str) -> None:
        for chunk in split_text(text, 4000):
            body = {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True}
            self.post(self.url, {"Content-Type": "application/json"}, json.dumps(body).encode())


class Discord:
    name = "Discord"

    def __init__(self, webhook: str, post: HttpPost = _post, upload: Upload = _upload):
        self.webhook = webhook
        self.post = post
        self.upload = upload

    def send_image(self, img: "Image") -> None:
        with open(img.path, "rb") as fh:
            payload = json.dumps({"content": img.caption[:1900], "allowed_mentions": {"parse": []}})
            self.upload(self.webhook, {"payload_json": payload},
                        {"file": (os.path.basename(img.path), fh.read(), "image/png")})

    def send(self, text: str) -> None:
        for chunk in split_text(text, 1900):
            body = {"content": chunk, "allowed_mentions": {"parse": []}}
            self.post(self.webhook, {"Content-Type": "application/json"}, json.dumps(body).encode())


class Slack:
    name = "Slack"

    def __init__(self, webhook: str, post: HttpPost = _post):
        self.webhook = webhook
        self.post = post

    def send(self, text: str) -> None:
        for chunk in split_text(text, 3500):
            self.post(self.webhook, {"Content-Type": "application/json"},
                      json.dumps({"text": chunk}).encode())

    def send_image(self, img: "Image") -> None:
        if not img.url:
            raise RuntimeError("Slack webhooks need a public image URL")
        blocks = [{"type": "image", "image_url": img.url, "alt_text": img.caption or "daily card"}]
        self.post(self.webhook, {"Content-Type": "application/json"},
                  json.dumps({"text": img.caption or "daily card", "blocks": blocks}).encode())


class Email:
    name = "Email"

    def __init__(self, host: str, port: int, user: str, password: str, to: str, sender: str | None = None,
                 smtp_factory=None):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.to, self.sender = to, sender or user
        self.smtp_factory = smtp_factory

    def send_image(self, img: "Image") -> None:
        self._send(img.caption or "Smartmoney Radar", img)

    def send(self, text: str) -> None:
        self._send(text, None)

    def _send(self, text: str, img: "Image | None") -> None:
        msg = EmailMessage()
        first = text.strip().splitlines()[0] if text.strip() else "Smartmoney Radar"
        msg["Subject"] = first[:120]
        msg["From"] = self.sender
        msg["To"] = self.to
        msg.set_content(text)
        if img is not None:
            with open(img.path, "rb") as fh:
                msg.add_attachment(fh.read(), maintype="image", subtype="png",
                                   filename=os.path.basename(img.path))
        if self.smtp_factory:
            smtp = self.smtp_factory(self.host, self.port)
        elif self.port == 465:
            smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=30)
            smtp.starttls()
        with smtp:
            smtp.login(self.user, self.password)
            smtp.send_message(msg)


class Line:
    name = "LINE"

    def __init__(self, notifier: LineNotifier):
        self.notifier = notifier

    def send(self, text: str) -> None:
        self.notifier.send(text)

    def send_image(self, img: "Image") -> None:
        if not img.url:
            raise RuntimeError("LINE needs a public HTTPS image URL (set --dashboard-url)")
        self.notifier.send_image(img.url, img.preview_url)


def configured_channels(env: dict[str, str] | None = None) -> list:
    env = dict(os.environ if env is None else env)
    out = []
    if env.get("LINE_CHANNEL_ACCESS_TOKEN"):
        out.append(Line(LineNotifier(env["LINE_CHANNEL_ACCESS_TOKEN"], env.get("LINE_TO") or "")))
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out.append(Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]))
    if env.get("DISCORD_WEBHOOK_URL"):
        out.append(Discord(env["DISCORD_WEBHOOK_URL"]))
    if env.get("SLACK_WEBHOOK_URL"):
        out.append(Slack(env["SLACK_WEBHOOK_URL"]))
    if env.get("SMTP_HOST") and env.get("SMTP_USER") and env.get("SMTP_PASSWORD") and env.get("EMAIL_TO"):
        out.append(Email(env["SMTP_HOST"], int(env.get("SMTP_PORT") or 587), env["SMTP_USER"],
                         env["SMTP_PASSWORD"], env["EMAIL_TO"], env.get("EMAIL_FROM")))
    return out


@dataclass
class Broadcast:
    channels: list

    def send(self, text: str) -> list[tuple[str, str | None]]:
        """Send to every channel; one failing channel doesn't stop the others.
        Returns (channel name, error or None). Raises if *every* channel failed."""
        results = []
        for ch in self.channels:
            try:
                ch.send(text)
                results.append((ch.name, None))
            except Exception as exc:
                results.append((ch.name, f"{type(exc).__name__}: {str(exc)[:200]}"))
        if results and all(err for _, err in results):
            raise RuntimeError("all notification channels failed: " + "; ".join(f"{n}: {e}" for n, e in results))
        return results

    def send_image(self, img: Image) -> list[tuple[str, str | None]]:
        """Best effort: channels without image support (or a public URL) are reported, never fatal."""
        results = []
        for ch in self.channels:
            fn = getattr(ch, "send_image", None)
            if fn is None:
                continue
            try:
                fn(img)
                results.append((ch.name, None))
            except Exception as exc:
                results.append((ch.name, f"{type(exc).__name__}: {str(exc)[:200]}"))
        return results
