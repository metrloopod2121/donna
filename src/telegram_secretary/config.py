from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _csv(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class AppConfig:
    runtime_mode: str
    public_host: str
    tls_webhook_enabled: bool
    health_host: str
    health_port: int
    telegram_api_id: str
    telegram_api_hash: str = field(repr=False)
    telegram_bot_token: str = field(repr=False)
    secretary_owner_telegram_id: str
    telegram_bot_delivery_mode: str
    telegram_webhook_enabled: bool
    telegram_webhook_url: str
    telegram_send_startup_test_message: bool
    telegram_user_ingest_enabled: bool
    telegram_user_client: str
    telegram_user_session_dir: Path
    telegram_user_files_dir: Path
    telegram_user_phone_number: str = field(repr=False)
    telegram_user_analysis_max_messages: int
    auto_reply_trusted_sender_ids: frozenset[str]
    auto_reply_enabled: bool
    auto_reply_min_confidence: float
    database_path: Path
    default_timezone: str
    calendar_provider: str
    apple_calendar_ids: frozenset[str]
    apple_calendar_lookahead_days: int
    apple_calendar_username: str
    apple_calendar_app_password: str = field(repr=False)
    apple_calendar_caldav_url: str
    craft_source_mode: str
    craft_export_dir: Path
    craft_today_document_id: str
    craft_tasks_document_id: str
    voice_provider: str
    voice_owner_phone_e164: str
    voice_outbound_enabled: bool
    voice_quiet_hours: str
    call_reminder_min_priority: str
    call_reminder_rate_limit_per_day: int
    voice_business_calls_enabled: bool
    voice_business_call_provider: str
    voice_business_call_allowed_prefixes: frozenset[str]
    voice_business_call_max_duration_seconds: int
    voice_webhook_base_url: str
    voice_webhook_secret: str = field(repr=False)
    twilio_account_sid: str
    twilio_auth_token: str = field(repr=False)
    twilio_from_phone_e164: str
    exolve_api_key: str = field(repr=False)
    exolve_source_phone: str
    exolve_tts_voice: int
    exolve_tts_lang: int
    exolve_tts_emotion: int
    exolve_tts_volume: int
    exolve_tts_speed: float
    voximplant_credentials_json: str = field(repr=False)
    voximplant_credentials_file: Path
    voximplant_rule_id: str
    voximplant_application_id: str
    voximplant_application_name: str
    voximplant_caller_id: str
    voximplant_worker_url: str
    voximplant_worker_secret_name: str
    voximplant_max_turns: int
    voximplant_asr_language: str
    voximplant_voice: str
    call_analysis_provider: str
    llm_worker_url: str
    llm_worker_bearer_token: str = field(repr=False)
    voice_recording_transcriber: str
    cloudflare_account_id: str
    cloudflare_api_token: str = field(repr=False)
    cloudflare_whisper_model: str
    openai_api_key: str | None = field(repr=False)

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            runtime_mode=os.getenv("SECRETARY_RUNTIME_MODE", "dry_run"),
            public_host=os.getenv("SECRETARY_PUBLIC_HOST", "secretary.vacanator.xyz"),
            tls_webhook_enabled=_bool(os.getenv("SECRETARY_TLS_WEBHOOK_ENABLED"), default=False),
            health_host=os.getenv("SECRETARY_HEALTH_HOST", "127.0.0.1"),
            health_port=int(os.getenv("SECRETARY_HEALTH_PORT", "18097")),
            telegram_api_id=os.getenv("TELEGRAM_API_ID", ""),
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            secretary_owner_telegram_id=os.getenv("SECRETARY_OWNER_TELEGRAM_ID", ""),
            telegram_bot_delivery_mode=os.getenv("TELEGRAM_BOT_DELIVERY_MODE", "polling"),
            telegram_webhook_enabled=_bool(os.getenv("TELEGRAM_WEBHOOK_ENABLED"), default=False),
            telegram_webhook_url=os.getenv("TELEGRAM_WEBHOOK_URL", ""),
            telegram_send_startup_test_message=_bool(
                os.getenv("TELEGRAM_SEND_STARTUP_TEST_MESSAGE"),
                default=False,
            ),
            telegram_user_ingest_enabled=_bool(
                os.getenv("TELEGRAM_USER_INGEST_ENABLED"),
                default=False,
            ),
            telegram_user_client=os.getenv("TELEGRAM_USER_CLIENT", "telethon"),
            telegram_user_session_dir=Path(
                os.getenv("TELEGRAM_USER_SESSION_DIR", "./data/telegram-user-telethon")
            ),
            telegram_user_files_dir=Path(
                os.getenv("TELEGRAM_USER_FILES_DIR", "./data/telegram-user-files")
            ),
            telegram_user_phone_number=os.getenv("TELEGRAM_USER_PHONE_NUMBER", ""),
            telegram_user_analysis_max_messages=int(
                os.getenv("TELEGRAM_USER_ANALYSIS_MAX_MESSAGES", "200")
            ),
            auto_reply_trusted_sender_ids=_csv(os.getenv("AUTO_REPLY_TRUSTED_SENDER_IDS")),
            auto_reply_enabled=_bool(os.getenv("AUTO_REPLY_ENABLED"), default=False),
            auto_reply_min_confidence=float(os.getenv("AUTO_REPLY_MIN_CONFIDENCE", "0.85")),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/secretary.sqlite3")),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow"),
            calendar_provider=os.getenv("CALENDAR_PROVIDER", "apple"),
            apple_calendar_ids=_csv(os.getenv("APPLE_CALENDAR_IDS")),
            apple_calendar_lookahead_days=int(os.getenv("APPLE_CALENDAR_LOOKAHEAD_DAYS", "7")),
            apple_calendar_username=os.getenv("APPLE_CALENDAR_USERNAME", ""),
            apple_calendar_app_password=os.getenv("APPLE_CALENDAR_APP_PASSWORD", ""),
            apple_calendar_caldav_url=os.getenv(
                "APPLE_CALENDAR_CALDAV_URL",
                "https://caldav.icloud.com",
            ),
            craft_source_mode=os.getenv("CRAFT_SOURCE_MODE", "markdown_export"),
            craft_export_dir=Path(os.getenv("CRAFT_EXPORT_DIR", "./data/craft-export")),
            craft_today_document_id=os.getenv("CRAFT_TODAY_DOCUMENT_ID", ""),
            craft_tasks_document_id=os.getenv("CRAFT_TASKS_DOCUMENT_ID", ""),
            voice_provider=os.getenv("VOICE_PROVIDER", "dry_run"),
            voice_owner_phone_e164=os.getenv("VOICE_OWNER_PHONE_E164", ""),
            voice_outbound_enabled=_bool(os.getenv("VOICE_OUTBOUND_ENABLED"), default=False),
            voice_quiet_hours=os.getenv("VOICE_QUIET_HOURS", "22:00-09:00"),
            call_reminder_min_priority=os.getenv("CALL_REMINDER_MIN_PRIORITY", "high"),
            call_reminder_rate_limit_per_day=int(os.getenv("CALL_REMINDER_RATE_LIMIT_PER_DAY", "3")),
            voice_business_calls_enabled=_bool(
                os.getenv("VOICE_BUSINESS_CALLS_ENABLED"),
                default=False,
            ),
            voice_business_call_provider=os.getenv("VOICE_BUSINESS_CALL_PROVIDER", "dry_run"),
            voice_business_call_allowed_prefixes=_csv(
                os.getenv("VOICE_BUSINESS_CALL_ALLOWED_PREFIXES", "+7")
            ),
            voice_business_call_max_duration_seconds=int(
                os.getenv("VOICE_BUSINESS_CALL_MAX_DURATION_SECONDS", "480")
            ),
            voice_webhook_base_url=os.getenv("VOICE_WEBHOOK_BASE_URL", ""),
            voice_webhook_secret=os.getenv("VOICE_WEBHOOK_SECRET", ""),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_from_phone_e164=os.getenv("TWILIO_FROM_PHONE_E164", ""),
            exolve_api_key=os.getenv("EXOLVE_API_KEY", ""),
            exolve_source_phone=os.getenv("EXOLVE_SOURCE_PHONE", ""),
            exolve_tts_voice=int(os.getenv("EXOLVE_TTS_VOICE", "1")),
            exolve_tts_lang=int(os.getenv("EXOLVE_TTS_LANG", "1")),
            exolve_tts_emotion=int(os.getenv("EXOLVE_TTS_EMOTION", "1")),
            exolve_tts_volume=int(os.getenv("EXOLVE_TTS_VOLUME", "-19")),
            exolve_tts_speed=float(os.getenv("EXOLVE_TTS_SPEED", "1.05")),
            voximplant_credentials_json=os.getenv("VOXIMPLANT_CREDENTIALS_JSON", ""),
            voximplant_credentials_file=Path(
                os.getenv(
                    "VOXIMPLANT_CREDENTIALS_FILE",
                    "/etc/telegram-secretary/voximplant-credentials.json",
                )
            ),
            voximplant_rule_id=os.getenv("VOXIMPLANT_RULE_ID", ""),
            voximplant_application_id=os.getenv("VOXIMPLANT_APPLICATION_ID", ""),
            voximplant_application_name=os.getenv("VOXIMPLANT_APPLICATION_NAME", ""),
            voximplant_caller_id=os.getenv("VOXIMPLANT_CALLER_ID", ""),
            voximplant_worker_url=os.getenv(
                "VOXIMPLANT_WORKER_URL",
                os.getenv("LLM_WORKER_URL", ""),
            ),
            voximplant_worker_secret_name=os.getenv(
                "VOXIMPLANT_WORKER_SECRET_NAME",
                "SECRETARY_AI_TOKEN",
            ),
            voximplant_max_turns=int(os.getenv("VOXIMPLANT_MAX_TURNS", "8")),
            voximplant_asr_language=os.getenv(
                "VOXIMPLANT_ASR_LANGUAGE",
                "ASRLanguage.RUSSIAN_RU",
            ),
            voximplant_voice=os.getenv(
                "VOXIMPLANT_VOICE",
                "VoiceList.Yandex.ru_RU_oksana",
            ),
            call_analysis_provider=os.getenv("CALL_ANALYSIS_PROVIDER", "rule_based"),
            llm_worker_url=os.getenv("LLM_WORKER_URL", ""),
            llm_worker_bearer_token=os.getenv("LLM_WORKER_BEARER_TOKEN", ""),
            voice_recording_transcriber=os.getenv("VOICE_RECORDING_TRANSCRIBER", "disabled"),
            cloudflare_account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
            cloudflare_api_token=os.getenv("CLOUDFLARE_API_TOKEN", ""),
            cloudflare_whisper_model=os.getenv("CLOUDFLARE_WHISPER_MODEL", "@cf/openai/whisper"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        )
