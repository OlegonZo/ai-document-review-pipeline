"""Куда отправлять алерты. Все каналы работают на stdlib, без внешних SDK."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol


class Notifier(Protocol):
    name: str

    def send(self, text: str) -> bool: ...


class ConsoleNotifier:
    name = "console"

    def send(self, text: str) -> bool:
        print(text, flush=True)
        return True


class TelegramNotifier:
    """Бот-уведомления. Токен берётся у @BotFather, chat_id — у @userinfobot."""

    name = "telegram"

    def __init__(self, token: str, chat_id: str, timeout: float = 10.0) -> None:
        if not token or not chat_id:
            raise ValueError("нужны и token, и chat_id")
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "TelegramNotifier | None":
        token = os.getenv("RADAR_TELEGRAM_TOKEN")
        chat_id = os.getenv("RADAR_TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return None
        return cls(token, chat_id)

    def send(self, text: str) -> bool:
        return bool(
            self.call("sendMessage", {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
        )

    def call(self, method: str, payload: dict) -> dict | None:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"[telegram] отправка не удалась: {error}", flush=True)
            return None
        if not body.get("ok"):
            print(f"[telegram] API вернул ошибку: {body.get('description')}", flush=True)
            return None
        return body.get("result")


class WebhookNotifier:
    """POST в произвольный webhook — например, в n8n из workflows/."""

    name = "webhook"

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "WebhookNotifier | None":
        url = os.getenv("RADAR_WEBHOOK_URL")
        return cls(url) if url else None

    def send(self, text: str) -> bool:
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                return True
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"[webhook] отправка не удалась: {error}", flush=True)
            return False


def from_env(include_console: bool = True) -> list[Notifier]:
    channels: list[Notifier] = []
    telegram = TelegramNotifier.from_env()
    if telegram:
        channels.append(telegram)
    webhook = WebhookNotifier.from_env()
    if webhook:
        channels.append(webhook)
    if include_console or not channels:
        channels.append(ConsoleNotifier())
    return channels
