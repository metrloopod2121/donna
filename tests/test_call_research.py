from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs

from telegram_secretary.adapters.telephony import (
    ExolveBusinessCallProvider,
    VoximplantDialogueCallProvider,
    build_call_research_service,
    build_twilio_business_call_twiml,
    build_twilio_live_test_twiml,
    _exolve_transcription_text,
)
from telegram_secretary.call_research import (
    BusinessCallRequest,
    BusinessCallStatus,
    CallRecordingNotice,
    CallResearchService,
    DryRunBusinessCallProvider,
    RuleBasedConversationAnalyzer,
    _download_recording_url,
    parse_business_call_argument,
    parse_llm_call_extraction,
)
from telegram_secretary.config import AppConfig
from telegram_secretary.storage import SQLiteStore


class CallResearchTest(TestCase):
    def test_parse_call_command_extracts_phone_target_and_questions(self) -> None:
        parsed = parse_business_call_argument(
            "+7 (999) 123-45-67 | Теннисный клуб | "
            "узнать стоимость абонемента; "
            "какие дни свободны?"
        )

        self.assertTrue(parsed.is_valid)
        self.assertEqual(parsed.phone_e164, "+79991234567")
        self.assertEqual(parsed.target_name, "Теннисный клуб")
        self.assertEqual(
            parsed.questions,
            (
                "узнать стоимость абонемента",
                "какие дни свободны",
            ),
        )

    def test_rule_based_analyzer_extracts_facts_from_transcript(self) -> None:
        request = _request(
            questions=(
                "стоимость абонемента",
                "свободные окна",
                "нужна ли справка",
            )
        )
        transcript = (
            "Абонемент на 8 занятий стоит 12000 рублей. "
            "Свободные окна есть во вторник с 10 до 12. "
            "Тренер работает по будням."
        )

        extraction = RuleBasedConversationAnalyzer().analyze(request, transcript)

        self.assertTrue(any(fact.name == "стоимость" for fact in extraction.facts))
        self.assertTrue(any(fact.name == "доступность" for fact in extraction.facts))
        self.assertIn("нужна ли справка", extraction.missing_items)

    def test_parse_llm_call_extraction_accepts_json_fences(self) -> None:
        extraction = parse_llm_call_extraction(
            "call_1",
            """```json
            {
              "summary": "Стол можно забронировать.",
              "facts": [{"name": "бронь", "value": "20:00", "confidence": 0.8}],
              "missing_items": [],
              "next_actions": ["Подтвердить бронь"],
              "confidence": 0.76
            }
            ```""",
        )

        self.assertEqual(extraction.summary, "Стол можно забронировать.")
        self.assertEqual(extraction.facts[0].value, "20:00")
        self.assertEqual(extraction.next_actions, ("Подтвердить бронь",))

    def test_process_recording_notice_uses_provider_transcript_and_persists_result(self) -> None:
        with TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "secretary.sqlite3")
            store.initialize()
            service = CallResearchService(
                provider=DryRunBusinessCallProvider(),
                analyzer=RuleBasedConversationAnalyzer(),
                store=store,
            )
            request = service.create_request(
                target_name="Ресторан",
                phone_e164="+79991234567",
                goal="забронировать стол",
                questions=("есть ли стол на 20:00",),
            )

            result = service.process_recording_notice(
                CallRecordingNotice(
                    request_id=request.request_id,
                    provider="twilio",
                    provider_call_id="CA123",
                    recording_url="https://api.twilio.com/recording",
                    recording_status="completed",
                    transcription_text="Стол на 20:00 можно забронировать.",
                )
            )

            self.assertEqual(result.status, BusinessCallStatus.ANALYZED)
            self.assertIsNotNone(result.extraction)
            self.assertEqual(
                store.recent_business_call_extractions()[0].request_id,
                request.request_id,
            )

    def test_twilio_twiml_records_answer_and_uses_callback_secret(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOICE_WEBHOOK_BASE_URL": "https://secretary.example.com",
                "VOICE_WEBHOOK_SECRET": "hook-secret",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        twiml = build_twilio_business_call_twiml(_request(), config)

        self.assertIn("<Record ", twiml)
        self.assertIn("recordingStatusCallback", twiml)
        self.assertIn("request_id=call_test", twiml)
        self.assertIn("token=hook-secret", twiml)

    def test_twilio_live_test_twiml_does_not_require_public_callback(self) -> None:
        twiml = build_twilio_live_test_twiml(_request())

        self.assertIn("<Record ", twiml)
        self.assertIn("timeout=\"5\"", twiml)
        self.assertNotIn("recordingStatusCallback", twiml)

    def test_factory_uses_worker_for_transcription(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOICE_RECORDING_TRANSCRIBER": "cloudflare_worker",
                "LLM_WORKER_URL": "https://secretary-ai.example.workers.dev",
                "LLM_WORKER_BEARER_TOKEN": "secret",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        service = build_call_research_service(config)

        self.assertIsNotNone(service.transcriber)

    def test_factory_uses_exolve_provider_when_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "exolve",
                "EXOLVE_API_KEY": "exolve-secret",
                "EXOLVE_SOURCE_PHONE": "+79990000000",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        service = build_call_research_service(config)

        self.assertIsInstance(service.provider, ExolveBusinessCallProvider)

    def test_factory_uses_voximplant_dialog_provider_when_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "VOICE_BUSINESS_CALLS_ENABLED": "true",
                "VOICE_BUSINESS_CALL_PROVIDER": "voximplant_dialog",
                "VOXIMPLANT_CREDENTIALS_JSON": "{}",
                "VOXIMPLANT_RULE_ID": "12345",
                "VOXIMPLANT_CALLER_ID": "+79990000000",
                "LLM_WORKER_URL": "https://secretary-ai.example.workers.dev",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        service = build_call_research_service(config)

        self.assertIsInstance(service.provider, VoximplantDialogueCallProvider)

    def test_voximplant_provider_starts_dialogue_scenario(self) -> None:
        seen: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            url = getattr(request, "full_url")
            if url == "https://api.voximplant.com/platform_api/StartScenarios":
                body = request.data.decode("utf-8")  # type: ignore[attr-defined]
                form = parse_qs(body)
                seen["url"] = url
                seen["authorization"] = request.get_header(  # type: ignore[attr-defined]
                    "Authorization"
                )
                seen["payload"] = {
                    key: values[-1]
                    for key, values in form.items()
                    if values
                }
                seen["timeout"] = timeout
                return _FakeResponse(
                    b"{"
                    b'"result":1,'
                    b'"call_session_history_id":987654,'
                    b'"media_session_access_secure_url":"https://session.example/request"'
                    b"}"
                )

            seen["session_url"] = url
            seen["session_payload"] = json.loads(
                request.data.decode("utf-8")  # type: ignore[attr-defined]
            )
            return _FakeResponse(b"{}")

        with patch.dict(
            os.environ,
            {
                "VOXIMPLANT_CREDENTIALS_JSON": json.dumps(
                    {
                        "account_id": 100500,
                        "key_id": "key-id",
                        "private_key": "private-key",
                    }
                ),
                "VOXIMPLANT_RULE_ID": "12345",
                "VOXIMPLANT_CALLER_ID": "+79990000000",
                "LLM_WORKER_URL": "https://secretary-ai.example.workers.dev",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        with (
            patch(
                "telegram_secretary.adapters.telephony._voximplant_management_token",
                return_value="jwt",
            ),
            patch("telegram_secretary.adapters.telephony.urlopen", fake_urlopen),
        ):
            placement = VoximplantDialogueCallProvider(config).place_business_call(_request())

        payload = seen["payload"]  # type: ignore[assignment]
        start_data = json.loads(payload["script_custom_data"])  # type: ignore[index]
        session_payload = seen["session_payload"]  # type: ignore[assignment]

        self.assertEqual(placement.provider, "voximplant_dialog")
        self.assertEqual(placement.provider_call_id, "987654")
        self.assertEqual(seen["authorization"], "Bearer jwt")
        self.assertEqual(seen["url"], "https://api.voximplant.com/platform_api/StartScenarios")
        self.assertEqual(payload["rule_id"], "12345")  # type: ignore[index]
        self.assertEqual(start_data["r"], "call_test")
        self.assertEqual(start_data["u"], "https://secretary-ai.example.workers.dev")
        self.assertEqual(seen["session_url"], "https://session.example/request")
        self.assertEqual(session_payload["destination"], "+79991234567")  # type: ignore[index]
        self.assertEqual(session_payload["callerId"], "+79990000000")  # type: ignore[index]
        self.assertEqual(
            session_payload["workerUrl"],  # type: ignore[index]
            "https://secretary-ai.example.workers.dev",
        )
        self.assertEqual(
            session_payload["questions"],  # type: ignore[index]
            ["стоимость"],
        )

    def test_exolve_provider_posts_make_voice_message(self) -> None:
        seen: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            seen["url"] = getattr(request, "full_url")
            seen["authorization"] = request.get_header(  # type: ignore[attr-defined]
                "Authorization"
            )
            seen["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            seen["timeout"] = timeout
            return _FakeResponse(b'{"call_id": 701234567890}')

        with patch.dict(
            os.environ,
            {
                "EXOLVE_API_KEY": "exolve-secret",
                "EXOLVE_SOURCE_PHONE": "+79990000000",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        with patch("telegram_secretary.adapters.telephony.urlopen", fake_urlopen):
            placement = ExolveBusinessCallProvider(config).place_business_call(_request())

        self.assertEqual(placement.provider, "exolve")
        self.assertEqual(placement.provider_call_id, "701234567890")
        self.assertEqual(seen["authorization"], "Bearer exolve-secret")
        self.assertEqual(seen["url"], "https://api.exolve.ru/call/v1/MakeVoiceMessage")
        self.assertEqual(seen["payload"]["source"], "79990000000")  # type: ignore[index]
        self.assertEqual(seen["payload"]["destination"], "79991234567")  # type: ignore[index]
        self.assertIn("tts", seen["payload"])  # type: ignore[operator]

    def test_exolve_transcription_text_keeps_channel_roles(self) -> None:
        text = _exolve_transcription_text(
            {
                "transcribation": [
                    {
                        "chunks": [
                            {"channel_tag": 1, "text": "Подскажите стоимость."},
                            {
                                "channel_tag": 2,
                                "text": "Абонемент стоит 12000 рублей.",
                            },
                        ]
                    }
                ]
            }
        )

        self.assertIn("Робот: Подскажите стоимость.", text)
        self.assertIn(
            "Собеседник: Абонемент стоит 12000 рублей.",
            text,
        )

    def test_download_recording_url_supports_exolve_bearer_auth(self) -> None:
        seen: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            seen["url"] = getattr(request, "full_url")
            seen["authorization"] = request.get_header(  # type: ignore[attr-defined]
                "Authorization"
            )
            seen["timeout"] = timeout
            return _FakeResponse(b"audio")

        with patch("telegram_secretary.call_research.urlopen", fake_urlopen):
            audio = _download_recording_url(
                "https://api.exolve.ru/statistics/download/701234567890",
                timeout_seconds=20.0,
                bearer_token="exolve-secret",
            )

        self.assertEqual(audio, b"audio")
        self.assertEqual(seen["authorization"], "Bearer exolve-secret")
        self.assertEqual(seen["url"], "https://api.exolve.ru/statistics/download/701234567890")

    def test_download_recording_url_does_not_leak_bearer_to_other_hosts(self) -> None:
        seen: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            seen["authorization"] = request.get_header(  # type: ignore[attr-defined]
                "Authorization"
            )
            return _FakeResponse(b"audio")

        with patch("telegram_secretary.call_research.urlopen", fake_urlopen):
            _download_recording_url(
                "https://recordings.example.com/audio",
                timeout_seconds=20.0,
                bearer_token="exolve-secret",
            )

        self.assertIsNone(seen["authorization"])


def _request(questions: tuple[str, ...] = ("стоимость",)) -> BusinessCallRequest:
    return BusinessCallRequest(
        request_id="call_test",
        target_name="Теннисный клуб",
        phone_e164="+79991234567",
        goal="узнать условия",
        questions=questions,
        language="ru",
        max_duration_seconds=480,
        requested_at=datetime.now(timezone.utc),
    )


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body
