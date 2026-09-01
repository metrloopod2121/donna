from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from telegram_secretary.call_research import CallResearchService, DryRunBusinessCallProvider
from telegram_secretary.config import AppConfig
from telegram_secretary.telegram_bot import (
    TelegramBotPoller,
    TelegramPollingState,
    _safe_error,
    should_start_polling,
)


class TelegramBotPollingTest(TestCase):
    def test_should_start_polling_requires_token_owner_and_no_webhook(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_WEBHOOK_ENABLED": "false",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertTrue(should_start_polling(config))

    def test_should_not_start_polling_when_webhook_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_WEBHOOK_ENABLED": "true",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertFalse(should_start_polling(config))

    def test_safe_error_redacts_token(self) -> None:
        error = RuntimeError("https://api.telegram.org/botsecret-token/getMe failed")

        self.assertNotIn("secret-token", _safe_error(error, "secret-token"))

    def test_owner_free_command_uses_availability_reply(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        poller = TelegramBotPoller(config, TelegramPollingState())
        poller.availability.free_today_reply = lambda: "Свободные окна сегодня: 10:00-11:00."
        sent: list[dict[str, str]] = []
        poller._request_json = lambda method, params=None: sent.append(params or {}) or {}

        poller._handle_update(
            {
                "message": {
                    "chat": {"id": 123},
                    "from": {"id": 123},
                    "text": "/free",
                }
            }
        )

        self.assertEqual(sent[0]["text"], "Свободные окна сегодня: 10:00-11:00.")

    def test_owner_today_command_uses_day_summary_reply(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        poller = TelegramBotPoller(config, TelegramPollingState())
        poller.availability.today_reply = lambda: "Занятые интервалы сегодня: 10:00-11:00."
        sent: list[dict[str, str]] = []
        poller._request_json = lambda method, params=None: sent.append(params or {}) or {}

        poller._handle_update(
            {
                "message": {
                    "chat": {"id": 123},
                    "from": {"id": 123},
                    "text": "/today",
                }
            }
        )

        self.assertEqual(sent[0]["text"], "Занятые интервалы сегодня: 10:00-11:00.")

    def test_owner_tgstatus_command_uses_safe_status(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        poller = TelegramBotPoller(config, TelegramPollingState())
        sent: list[dict[str, str]] = []
        poller._request_json = lambda method, params=None: sent.append(params or {}) or {}

        poller._handle_update(
            {
                "message": {
                    "chat": {"id": 123},
                    "from": {"id": 123},
                    "text": "/tgstatus",
                }
            }
        )

        self.assertIn("Личный Telegram не включен", sent[0]["text"])
        self.assertNotIn("token", sent[0]["text"])

    def test_owner_dialog_command_uses_dialog_analysis(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        poller = TelegramBotPoller(config, TelegramPollingState())
        poller.telegram_user_account.analyze_dialog = lambda query: f"query={query}"
        sent: list[dict[str, str]] = []
        poller._request_json = lambda method, params=None: sent.append(params or {}) or {}

        poller._handle_update(
            {
                "message": {
                    "chat": {"id": 123},
                    "from": {"id": 123},
                    "text": "/dialog Alice",
                }
            }
        )

        self.assertEqual(sent[0]["text"], "query=Alice")

    def test_owner_call_command_uses_call_service(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        call_service = CallResearchService(provider=DryRunBusinessCallProvider())
        poller = TelegramBotPoller(config, TelegramPollingState(), call_service=call_service)
        sent: list[dict[str, str]] = []
        poller._request_json = lambda method, params=None: sent.append(params or {}) or {}

        poller._handle_update(
            {
                "message": {
                    "chat": {"id": 123},
                    "from": {"id": 123},
                    "text": "/call +79991234567 | Теннисный клуб | узнать стоимость",
                }
            }
        )

        self.assertIn("Звонок подготовлен", sent[0]["text"])
        self.assertIn("Теннисный клуб", sent[0]["text"])

    def test_non_owner_dialog_command_is_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
        poller = TelegramBotPoller(config, TelegramPollingState())
        sent: list[dict[str, str]] = []
        poller._request_json = lambda method, params=None: sent.append(params or {}) or {}

        poller._handle_update(
            {
                "message": {
                    "chat": {"id": 999},
                    "from": {"id": 999},
                    "text": "/dialog Alice",
                }
            }
        )

        self.assertEqual(sent, [])
