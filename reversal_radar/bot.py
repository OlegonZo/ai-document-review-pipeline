"""Телеграм-бот: команды в чате плюс автоматические алерты из Watcher."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime

from .engine import summarize
from .models import DISCLAIMER, RadarReport
from .notifiers import TelegramNotifier
from .sources.base import SourceError
from .watcher import Watcher

HELP = (
    "Reversal Radar\n"
    "/status — свежий отчёт по монете\n"
    "/level 83.40 — задать уровень для анализа\n"
    "/level auto — вернуть автоопределение уровня\n"
    "/coin HYPE — сменить монету\n"
    "/watch on|off — включить/выключить авто-алерты\n"
    "/settings — текущие настройки\n"
    "/help — эта справка\n\n"
    "Бот не предсказывает цену: он проверяет условия (уровень, объём, funding, OI, стакан, "
    "RSI-дивергенцию, согласие таймфреймов) и показывает сценарий с точками инвалидации."
)


class RadarBot:
    def __init__(self, watcher: Watcher, telegram: TelegramNotifier, status_cache_seconds: int = 20) -> None:
        self.watcher = watcher
        self.telegram = telegram
        self.status_cache_seconds = status_cache_seconds
        self.offset: int | None = None
        self.watching = True
        self._last_report: RadarReport | None = None
        self._last_report_at: float = 0.0

    # --- команды ---------------------------------------------------------

    def handle(self, text: str) -> str:
        command, _, argument = text.strip().partition(" ")
        command = command.split("@")[0].lower()
        argument = argument.strip()

        if command in {"/start", "/help"}:
            return HELP
        if command == "/status":
            return self._status()
        if command == "/level":
            return self._set_level(argument)
        if command == "/coin":
            return self._set_coin(argument)
        if command == "/watch":
            return self._set_watch(argument)
        if command == "/settings":
            config = self.watcher.config
            level = f"{config.level:g}" if config.level is not None else "auto"
            return (
                f"Монета: {config.coin}\nУровень: {level}\n"
                f"Таймфреймы: {', '.join(config.timeframes)}\n"
                f"Опрос: раз в {config.poll_seconds} с\n"
                f"Авто-алерты: {'включены' if self.watching else 'выключены'}\n"
                f"Пауза между алертами: {config.alert_cooldown_seconds} с"
            )
        return "Не знаю такой команды. /help"

    def _status(self) -> str:
        fresh = self._last_report is not None and time.monotonic() - self._last_report_at < self.status_cache_seconds
        if not fresh:
            try:
                self._last_report = self.watcher.poll()
                self._last_report_at = time.monotonic()
            except SourceError as error:
                return f"Источник данных недоступен: {error}"
        assert self._last_report is not None
        return f"{summarize(self._last_report)}\n\n{DISCLAIMER}"

    def _set_level(self, argument: str) -> str:
        if argument.lower() in {"auto", "off", ""}:
            self.watcher.config = replace(self.watcher.config, level=None)
            return "Уровень будет определяться автоматически по пивотам."
        try:
            level = float(argument.replace(",", "."))
        except ValueError:
            return "Формат: /level 83.40 или /level auto"
        if level <= 0:
            return "Уровень должен быть больше нуля."
        self.watcher.config = replace(self.watcher.config, level=level)
        return f"Уровень для анализа: {level:g}"

    def _set_coin(self, argument: str) -> str:
        if not argument:
            return "Формат: /coin HYPE"
        self.watcher.config = replace(self.watcher.config, coin=argument.upper(), level=None)
        self._last_report = None
        return f"Монета: {argument.upper()}. Уровень сброшен на авто."

    def _set_watch(self, argument: str) -> str:
        if argument.lower() in {"on", "вкл", "1"}:
            self.watching = True
            return f"Авто-алерты включены, опрос раз в {self.watcher.config.poll_seconds} с."
        if argument.lower() in {"off", "выкл", "0"}:
            self.watching = False
            return "Авто-алерты выключены. /status по-прежнему работает."
        return "Формат: /watch on или /watch off"

    # --- цикл ------------------------------------------------------------

    def pump_updates(self, timeout: int = 0) -> int:
        """Забирает новые сообщения и отвечает на них. Возвращает число обработанных."""
        payload = {"timeout": timeout}
        if self.offset is not None:
            payload["offset"] = self.offset
        updates = self.telegram.call("getUpdates", payload) or []
        for update in updates:
            self.offset = update["update_id"] + 1
            message = update.get("message") or update.get("channel_post") or {}
            text = message.get("text")
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if not text or chat_id != str(self.telegram.chat_id):
                continue
            self.telegram.call("sendMessage", {"chat_id": chat_id, "text": self.handle(text)})
        return len(updates)

    def run(self, iterations: int | None = None) -> None:
        interval = self.watcher.config.poll_seconds
        next_poll = 0.0
        done = 0
        print(f"[bot] запущен: {self.watcher.config.coin}, опрос раз в {interval} с", flush=True)
        while iterations is None or done < iterations:
            self.pump_updates(timeout=0)
            now = time.monotonic()
            if self.watching and now >= next_poll:
                report = self.watcher.run_once()
                if report is not None:
                    self._last_report = report
                    self._last_report_at = now
                next_poll = now + interval
            done += 1
            if iterations is None or done < iterations:
                time.sleep(2)

    def announce_start(self) -> None:
        config = self.watcher.config
        level = f"{config.level:g}" if config.level is not None else "auto"
        self.telegram.send(
            f"Радар запущен: {config.coin}, уровень {level}, опрос раз в {config.poll_seconds} с "
            f"({datetime.now(UTC):%H:%M UTC}).\n{HELP}"
        )
