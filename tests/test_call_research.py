from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from telegram_secretary.adapters.telephony import build_twilio_business_call_twiml
from telegram_secretary.call_research import (
    BusinessCallRequest,
    BusinessCallStatus,
    CallRecordingNotice,
    CallResearchService,
    DryRunBusinessCallProvider,
    RuleBasedConversationAnalyzer,
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
