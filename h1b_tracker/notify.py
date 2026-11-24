"""Alert channels. Each one is free and set up by you: ntfy, Telegram, email (SMTP).

Secrets come from environment variables, never from the config file, so the
config can be shared or committed safely.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


class Notifier:
    name = "base"

    def send(self, title: str, body: str, url: str | None = None) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, title, body, url=None):
        print(f"\n=== {title} ===\n{body}" + (f"\n{url}" if url else ""), flush=True)


class NtfyNotifier(Notifier):
    """Push to the ntfy app. Env: NTFY_TOPIC (required), NTFY_SERVER, NTFY_TOKEN (optional)."""
    name = "ntfy"

    def __init__(self, priority: str = "high"):
        self.topic = os.environ.get("NTFY_TOPIC", "").strip()
        if not self.topic:
            raise ValueError("NTFY_TOPIC is not set")
        self.server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        self.token = os.environ.get("NTFY_TOKEN", "").strip()
        self.priority = priority

    def send(self, title, body, url=None):
        headers = {"Title": title.encode("utf-8"), "Priority": self.priority, "Tags": "passport_control"}
        if url:
            headers["Click"] = url
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        r = requests.post(f"{self.server}/{self.topic}", data=body.encode("utf-8"), headers=headers, timeout=20)
        r.raise_for_status()


class TelegramNotifier(Notifier):
    """Message from your own Telegram bot. Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID."""
    name = "telegram"

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (self.token and self.chat_id):
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set")

    def send(self, title, body, url=None):
        text = f"{title}\n\n{body}" + (f"\n{url}" if url else "")
        r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                          timeout=20)
        r.raise_for_status()


class EmailNotifier(Notifier):
    """Email over SMTP with STARTTLS. Env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO, EMAIL_FROM.

    For Gmail use an App Password (Google Account > Security > App passwords), not your normal password.
    """
    name = "email"

    def __init__(self):
        e = os.environ
        self.host = e.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(e.get("SMTP_PORT", "587"))
        self.user = e.get("SMTP_USER", "")
        self.password = e.get("SMTP_PASSWORD", "")
        self.to = e.get("EMAIL_TO", "")
        self.sender = e.get("EMAIL_FROM", self.user)
        if not (self.user and self.password and self.to):
            raise ValueError("SMTP_USER, SMTP_PASSWORD and EMAIL_TO must be set")

    def send(self, title, body, url=None):
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self.sender
        msg["To"] = self.to
        msg.set_content(body + (f"\n\n{url}" if url else ""))
        with smtplib.SMTP(self.host, self.port, timeout=30) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(msg)


REGISTRY = {"console": ConsoleNotifier, "ntfy": NtfyNotifier, "telegram": TelegramNotifier, "email": EmailNotifier}


def build(channels: list[str], ntfy_priority: str = "high") -> list[Notifier]:
    out = []
    for c in channels:
        cls = REGISTRY.get(c)
        if cls is None:
            raise ValueError(f"Unknown alert channel {c!r}. Use: {', '.join(REGISTRY)}")
        try:
            out.append(cls(ntfy_priority) if c == "ntfy" else cls())
        except ValueError as e:
            log.error("Alert channel %s is not set up: %s", c, e)
    return out


def send_all(notifiers: list[Notifier], title: str, body: str, url: str | None = None) -> int:
    ok = 0
    for n in notifiers:
        try:
            n.send(title, body, url)
            ok += 1
        except Exception as e:  # one broken channel must not stop the others
            log.error("Sending via %s failed: %s", n.name, e)
    return ok
