from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from telegram_secretary.config import AppConfig
from telegram_secretary.health import health_payload, readiness_payload


class HealthPayloadTest(TestCase):
    def test_dry_run_readiness_does_not_require_real_secrets(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig.from_env()

        status, payload = readiness_payload(config)

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["runtime_mode"], "dry_run")

    def test_health_payload_does_not_include_secret_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = AppConfig.from_env()

        payload = health_payload(config)

        self.assertEqual(payload["service"], "telegram-secretary")
        self.assertNotIn("telegram_bot_token", payload)
        self.assertFalse(payload["tls_webhook_enabled"])
        self.assertFalse(payload["telegram_webhook_enabled"])
        self.assertFalse(payload["telegram_user_ingest_enabled"])
        self.assertFalse(payload["calendar_configured"])

    def test_live_polling_readiness_requires_bot_secrets_only(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRETARY_RUNTIME_MODE": "telegram_polling",
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        status, payload = readiness_payload(
            config,
            polling_status={"enabled": True, "running": True, "bot_ok": True},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["missing_live_settings"], [])

    def test_readiness_requires_business_call_secrets_when_twilio_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRETARY_RUNTIME_MODE": "telegram_polling",
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "twilio",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        status, payload = readiness_payload(
            config,
            polling_status={"enabled": True, "running": True, "bot_ok": True},
        )

        self.assertEqual(status, 503)
        self.assertIn("TWILIO_ACCOUNT_SID", payload["missing_live_settings"])
        self.assertIn("VOICE_WEBHOOK_SECRET", payload["missing_live_settings"])

    def test_readiness_requires_exolve_secrets_when_exolve_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRETARY_RUNTIME_MODE": "telegram_polling",
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "exolve",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        status, payload = readiness_payload(
            config,
            polling_status={"enabled": True, "running": True, "bot_ok": True},
        )

        self.assertEqual(status, 503)
        self.assertIn("EXOLVE_API_KEY", payload["missing_live_settings"])
        self.assertIn("EXOLVE_SOURCE_PHONE", payload["missing_live_settings"])

    def test_readiness_requires_asterisk_settings_when_sipnet_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRETARY_RUNTIME_MODE": "telegram_polling",
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "sipnet",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        status, payload = readiness_payload(
            config,
            polling_status={"enabled": True, "running": True, "bot_ok": True},
        )

        self.assertEqual(status, 503)
        self.assertIn("ASTERISK_AMI_USERNAME", payload["missing_live_settings"])
        self.assertIn("ASTERISK_AMI_PASSWORD", payload["missing_live_settings"])
        self.assertIn("LLM_WORKER_URL", payload["missing_live_settings"])
        self.assertIn("LLM_WORKER_BEARER_TOKEN", payload["missing_live_settings"])

    def test_readiness_requires_voximplant_settings_when_dialogue_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRETARY_RUNTIME_MODE": "telegram_polling",
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "voximplant_dialog",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        status, payload = readiness_payload(
            config,
            polling_status={"enabled": True, "running": True, "bot_ok": True},
        )

        self.assertEqual(status, 503)
        self.assertIn("VOXIMPLANT_RULE_ID", payload["missing_live_settings"])
        self.assertIn("VOXIMPLANT_CALLER_ID", payload["missing_live_settings"])
        self.assertIn(
            "VOXIMPLANT_CREDENTIALS_JSON or VOXIMPLANT_CREDENTIALS_FILE",
            payload["missing_live_settings"],
        )

    def test_readiness_requires_voximplant_sip_template_when_sip_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECRETARY_RUNTIME_MODE": "telegram_polling",
                "TELEGRAM_BOT_DELIVERY_MODE": "polling",
                "TELEGRAM_BOT_TOKEN": "token",
                "SECRETARY_OWNER_TELEGRAM_ID": "123",
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "voximplant_dialog",
                "VOXIMPLANT_OUTBOUND_TRANSPORT": "sip",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        status, payload = readiness_payload(
            config,
            polling_status={"enabled": True, "running": True, "bot_ok": True},
        )

        self.assertEqual(status, 503)
        self.assertIn("VOXIMPLANT_RULE_ID", payload["missing_live_settings"])
        self.assertIn("VOXIMPLANT_SIP_URI_TEMPLATE", payload["missing_live_settings"])
        self.assertNotIn("VOXIMPLANT_CALLER_ID", payload["missing_live_settings"])
        self.assertIn(
            "VOXIMPLANT_CREDENTIALS_JSON or VOXIMPLANT_CREDENTIALS_FILE",
            payload["missing_live_settings"],
        )
