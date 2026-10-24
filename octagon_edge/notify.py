"""Phone alerts through ntfy, Telegram and Web Push to the installed PWA (all free)."""
from __future__ import annotations

import logging
import os
from typing import Callable

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings, http: httpx.Client | None = None,
                 webpush: Callable[[str, str, str, bool], int] | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=15)
        self.webpush = webpush
        self.public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")

    @property
    def channels(self) -> list[str]:
        out = []
        if self.s.ntfy_topic:
            out.append("ntfy")
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            out.append("telegram")
        if self.webpush:
            out.append("webpush")
        return out

    def send(self, title: str, body: str, urgent: bool = False, tags: str = "", path: str = "/") -> list[str]:
        """Send to every configured channel; returns the channels that succeeded.

        `path` is the app page the alert opens (e.g. /fight/3) when PUBLIC_URL is set.
        """
        ok = []
        link = f"{self.public_url}{path}" if self.public_url else ""
        if self.webpush:
            try:
                if self.webpush(title, body, path, urgent):
                    ok.append("webpush")
            except Exception as exc:  # noqa: BLE001 - never let one channel block the others
                log.warning("Web push failed: %s", exc)
        if self.s.ntfy_topic:
            try:
                resp = self.http.post(
                    f"{self.s.ntfy_url}/{self.s.ntfy_topic}", content=body.encode(),
                    headers={"Title": title.encode("ascii", "ignore").decode(),
                             "Priority": "5" if urgent else "3", "Tags": tags or "boxing_glove",
                             **({"Click": link} if link else {})})
                resp.raise_for_status()
                ok.append("ntfy")
            except httpx.HTTPError as exc:
                log.warning("ntfy failed: %s", exc)
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            try:
                resp = self.http.post(
                    f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
                    json={"chat_id": self.s.telegram_chat_id, "text": f"{title}\n\n{body}" + (f"\n{link}" if link else ""),
                          "disable_web_page_preview": True})
                resp.raise_for_status()
                ok.append("telegram")
            except httpx.HTTPError as exc:
                log.warning("Telegram failed: %s", exc)
        if not self.channels:
            log.warning("No alert channel configured; printing instead.\n%s\n%s", title, body)
        return ok
