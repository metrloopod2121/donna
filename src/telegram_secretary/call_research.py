from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BusinessCallStatus(str, Enum):
    DRY_RUN = "dry_run"
    QUEUED = "queued"
    STARTED = "started"
    RECORDING_READY = "recording_ready"
    TRANSCRIBED = "transcribed"
    ANALYZED = "analyzed"
    FAILED = "failed"


@dataclass(frozen=True)
class BusinessCallRequest:
    request_id: str
    target_name: str
    phone_e164: str
    goal: str
    questions: tuple[str, ...]
    language: str
    max_duration_seconds: int
    requested_at: datetime
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedBusinessCallCommand:
    phone_e164: str
    target_name: str
    goal: str
    questions: tuple[str, ...]
    error: str = ""

    @property
    def is_valid(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class BusinessCallPlacement:
    request_id: str
    provider: str
    status: BusinessCallStatus
    provider_call_id: str | None = None
    message: str = ""
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class CallRecordingNotice:
    request_id: str
    provider: str
    provider_call_id: str | None
    recording_url: str | None
    recording_status: str
    duration_seconds: int | None = None
    transcription_text: str | None = None
    received_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class CallTranscript:
    request_id: str
    provider_call_id: str | None
    transcript_text: str
    source: str
    recording_url: str | None = None
    duration_seconds: int | None = None
    received_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class ExtractedCallFact:
    name: str
    value: str
    confidence: float
    evidence: str = ""


@dataclass(frozen=True)
class CallExtraction:
    request_id: str
    summary: str
    facts: tuple[ExtractedCallFact, ...]
    missing_items: tuple[str, ...]
    next_actions: tuple[str, ...]
    confidence: float
    analyzed_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class RecordingProcessingResult:
    status: BusinessCallStatus
    message: str
    transcript: CallTranscript | None = None
    extraction: CallExtraction | None = None


class BusinessCallProvider(Protocol):
    def place_business_call(self, request: BusinessCallRequest) -> BusinessCallPlacement:
        ...


class ConversationAnalyzer(Protocol):
    def analyze(self, request: BusinessCallRequest, transcript: str) -> CallExtraction:
        ...


class RecordingTranscriber(Protocol):
    def transcribe(self, notice: CallRecordingNotice) -> str:
        ...


class CallResearchStore(Protocol):
    def save_business_call_request(self, request: BusinessCallRequest) -> None:
        ...

    def save_business_call_placement(self, placement: BusinessCallPlacement) -> None:
        ...

    def save_business_call_recording_notice(self, notice: CallRecordingNotice) -> None:
        ...

    def save_business_call_transcript(self, transcript: CallTranscript) -> None:
        ...

    def save_business_call_extraction(self, extraction: CallExtraction) -> None:
        ...

    def find_business_call_request(self, request_id: str) -> BusinessCallRequest | None:
        ...


class DryRunBusinessCallProvider:
    def place_business_call(self, request: BusinessCallRequest) -> BusinessCallPlacement:
        return BusinessCallPlacement(
            request_id=request.request_id,
            provider="dry_run",
            status=BusinessCallStatus.DRY_RUN,
            provider_call_id=f"dryrun-{request.request_id}",
            message=build_business_call_script(request),
        )


class RuleBasedConversationAnalyzer:
    def analyze(self, request: BusinessCallRequest, transcript: str) -> CallExtraction:
        normalized = " ".join(transcript.split())
        sentences = _split_sentences(normalized)
        facts: list[ExtractedCallFact] = []
        facts.extend(_extract_price_facts(sentences))
        facts.extend(_extract_schedule_facts(sentences))
        facts.extend(_extract_availability_facts(sentences))
        facts.extend(_extract_reservation_facts(sentences))
        facts.extend(_extract_subscription_facts(sentences))
        facts.extend(_extract_contact_facts(normalized))

        missing = tuple(
            question
            for question in request.questions
            if not _question_has_answer(question, sentences)
        )
        summary = _summary_from_sentences(
            sentences,
            fallback="Транскрипт получен, явных фактов мало.",
        )
        next_actions = _next_actions_from_missing(missing)
        confidence = 0.62 if facts else 0.25
        if missing:
            confidence = min(confidence, 0.5)

        return CallExtraction(
            request_id=request.request_id,
            summary=summary,
            facts=tuple(_dedupe_facts(facts)),
            missing_items=missing,
            next_actions=next_actions,
            confidence=confidence,
        )


class CloudflareWorkerConversationAnalyzer:
    """Call the existing secretary-llm Worker and require JSON back.

    The Worker contract documented in CLAUDE.md accepts {"messages": [...]} and
    returns {"text": "..."}; this adapter also tolerates direct Workers AI style
    envelopes with result.response.
    """

    def __init__(
        self,
        endpoint_url: str,
        bearer_token: str,
        fallback: ConversationAnalyzer | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.bearer_token = bearer_token
        self.fallback = fallback or RuleBasedConversationAnalyzer()
        self.timeout_seconds = timeout_seconds

    def analyze(self, request: BusinessCallRequest, transcript: str) -> CallExtraction:
        try:
            payload = json.dumps(
                {
                    "messages": [
                        {
                            "role": "system",
                            "content": _CALL_ANALYSIS_SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "target_name": request.target_name,
                                    "goal": request.goal,
                                    "questions": request.questions,
                                    "transcript": transcript,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")
            response = _post_json(
                self.endpoint_url,
                payload,
                token=self.bearer_token,
                timeout_seconds=self.timeout_seconds,
            )
            text = _llm_response_text(response)
            return parse_llm_call_extraction(request.request_id, text)
        except Exception:
            return self.fallback.analyze(request, transcript)


class CloudflareWhisperTranscriber:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = "@cf/openai/whisper",
        timeout_seconds: float = 90.0,
        twilio_account_sid: str | None = None,
        twilio_auth_token: str | None = None,
        recording_bearer_token: str | None = None,
    ) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.twilio_account_sid = twilio_account_sid
        self.twilio_auth_token = twilio_auth_token
        self.recording_bearer_token = recording_bearer_token

    def transcribe(self, notice: CallRecordingNotice) -> str:
        if not notice.recording_url:
            raise RuntimeError("recording_url is required for Cloudflare transcription")

        audio = self._download_recording(notice.recording_url)
        model_path = quote(self.model.strip("/"), safe="@/")
        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{model_path}"
        )
        request = Request(
            endpoint,
            data=audio,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/octet-stream",
                "User-Agent": "telegram-secretary/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"cloudflare transcription failed: {detail[:240]}") from exc
        except URLError as exc:
            raise RuntimeError(f"cloudflare transcription failed: {exc.reason}") from exc

        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise RuntimeError("cloudflare transcription response has no text")
        return result["text"].strip()

    def _download_recording(self, recording_url: str) -> bytes:
        return _download_recording_url(
            recording_url,
            timeout_seconds=self.timeout_seconds,
            twilio_account_sid=self.twilio_account_sid,
            twilio_auth_token=self.twilio_auth_token,
            bearer_token=self.recording_bearer_token,
        )


class WorkerWhisperTranscriber:
    def __init__(
        self,
        worker_url: str,
        bearer_token: str,
        timeout_seconds: float = 90.0,
        twilio_account_sid: str | None = None,
        twilio_auth_token: str | None = None,
        recording_bearer_token: str | None = None,
    ) -> None:
        self.worker_url = worker_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds
        self.twilio_account_sid = twilio_account_sid
        self.twilio_auth_token = twilio_auth_token
        self.recording_bearer_token = recording_bearer_token

    def transcribe(self, notice: CallRecordingNotice) -> str:
        if not notice.recording_url:
            raise RuntimeError("recording_url is required for Worker transcription")

        audio = _download_recording_url(
            notice.recording_url,
            timeout_seconds=self.timeout_seconds,
            twilio_account_sid=self.twilio_account_sid,
            twilio_auth_token=self.twilio_auth_token,
            bearer_token=self.recording_bearer_token,
        )
        request = Request(
            f"{self.worker_url}/asr",
            data=audio,
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/octet-stream",
                "User-Agent": "telegram-secretary/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"worker transcription failed: {detail[:240]}") from exc
        except URLError as exc:
            raise RuntimeError(f"worker transcription failed: {exc.reason}") from exc

        text = _transcription_text(payload)
        if not text:
            raise RuntimeError("worker transcription response has no text")
        return text


class CallResearchService:
    def __init__(
        self,
        provider: BusinessCallProvider,
        analyzer: ConversationAnalyzer | None = None,
        store: CallResearchStore | None = None,
        transcriber: RecordingTranscriber | None = None,
    ) -> None:
        self.provider = provider
        self.analyzer = analyzer or RuleBasedConversationAnalyzer()
        self.store = store
        self.transcriber = transcriber

    def create_request(
        self,
        *,
        target_name: str,
        phone_e164: str,
        goal: str,
        questions: tuple[str, ...],
        language: str = "ru",
        max_duration_seconds: int = 480,
        metadata: dict[str, str] | None = None,
    ) -> BusinessCallRequest:
        request = BusinessCallRequest(
            request_id=f"call_{uuid.uuid4().hex[:12]}",
            target_name=target_name.strip() or "Организация",
            phone_e164=normalize_phone_e164(phone_e164),
            goal=goal.strip(),
            questions=tuple(question.strip() for question in questions if question.strip()),
            language=language,
            max_duration_seconds=max_duration_seconds,
            requested_at=_utc_now(),
            metadata=metadata or {},
        )
        if self.store is not None:
            self.store.save_business_call_request(request)
        return request

    def create_request_from_command(
        self,
        command: ParsedBusinessCallCommand,
        *,
        max_duration_seconds: int = 480,
    ) -> BusinessCallRequest:
        if not command.is_valid:
            raise ValueError(command.error)
        return self.create_request(
            target_name=command.target_name,
            phone_e164=command.phone_e164,
            goal=command.goal,
            questions=command.questions,
            max_duration_seconds=max_duration_seconds,
        )

    def place_call(self, request: BusinessCallRequest) -> BusinessCallPlacement:
        placement = self.provider.place_business_call(request)
        if self.store is not None:
            self.store.save_business_call_placement(placement)
        return placement

    def analyze_transcript(
        self,
        request: BusinessCallRequest,
        transcript: CallTranscript,
    ) -> CallExtraction:
        if self.store is not None:
            self.store.save_business_call_transcript(transcript)
        extraction = self.analyzer.analyze(request, transcript.transcript_text)
        if self.store is not None:
            self.store.save_business_call_extraction(extraction)
        return extraction

    def process_recording_notice(self, notice: CallRecordingNotice) -> RecordingProcessingResult:
        if self.store is not None:
            self.store.save_business_call_recording_notice(notice)
            request = self.store.find_business_call_request(notice.request_id)
        else:
            request = None

        if request is None:
            return RecordingProcessingResult(
                status=BusinessCallStatus.FAILED,
                message=(
                    "Не найден исходный запрос звонка для записи."
                ),
            )

        transcript_text = (notice.transcription_text or "").strip()
        transcript_source = "provider_callback"
        if not transcript_text and self.transcriber is not None:
            transcript_text = self.transcriber.transcribe(notice)
            transcript_source = "cloudflare_whisper"

        if not transcript_text:
            return RecordingProcessingResult(
                status=BusinessCallStatus.RECORDING_READY,
                message=(
                    "Запись сохранена, транскрипт пока не получен."
                ),
            )

        transcript = CallTranscript(
            request_id=notice.request_id,
            provider_call_id=notice.provider_call_id,
            transcript_text=transcript_text,
            source=transcript_source,
            recording_url=notice.recording_url,
            duration_seconds=notice.duration_seconds,
            received_at=notice.received_at,
        )
        extraction = self.analyze_transcript(request, transcript)
        return RecordingProcessingResult(
            status=BusinessCallStatus.ANALYZED,
            message="Транскрипт разобран.",
            transcript=transcript,
            extraction=extraction,
        )


def parse_business_call_argument(argument: str) -> ParsedBusinessCallCommand:
    cleaned = argument.strip()
    if not cleaned:
        return ParsedBusinessCallCommand("", "", "", (), error=_CALL_COMMAND_HELP)

    phone_match = _PHONE_RE.search(cleaned)
    if phone_match is None:
        return ParsedBusinessCallCommand(
            "",
            "",
            "",
            (),
            error="Не нашёл номер телефона. " + _CALL_COMMAND_HELP,
        )

    try:
        phone = normalize_phone_e164(phone_match.group(0))
    except ValueError:
        return ParsedBusinessCallCommand(
            "",
            "",
            "",
            (),
            error="Номер должен быть в формате E.164, например +79991234567.",
        )
    without_phone = (cleaned[: phone_match.start()] + cleaned[phone_match.end() :]).strip(
        " |,-"
    )
    parts = [part.strip() for part in without_phone.split("|") if part.strip()]

    if len(parts) >= 2:
        target_name = parts[0]
        goal_text = " | ".join(parts[1:])
    elif len(parts) == 1:
        target_name = "Организация"
        goal_text = parts[0]
    else:
        return ParsedBusinessCallCommand(
            phone,
            "",
            "",
            (),
            error="После номера укажи цель звонка. " + _CALL_COMMAND_HELP,
        )

    questions = _split_questions(goal_text)
    goal = _goal_from_questions(goal_text, questions)
    if not questions:
        questions = (goal,)

    return ParsedBusinessCallCommand(
        phone_e164=phone,
        target_name=target_name,
        goal=goal,
        questions=questions,
    )


def call_command_help() -> str:
    return _CALL_COMMAND_HELP


def normalize_phone_e164(raw_phone: str) -> str:
    stripped = raw_phone.strip()
    digits = "".join(char for char in stripped if char.isdigit())
    if not digits:
        raise ValueError("phone number is empty")
    if stripped.startswith("+"):
        candidate = f"+{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        candidate = f"+7{digits[1:]}"
    elif len(digits) == 11 and digits.startswith("7"):
        candidate = f"+{digits}"
    elif len(digits) == 10:
        candidate = f"+7{digits}"
    else:
        candidate = f"+{digits}"
    if not re.fullmatch(r"\+[1-9]\d{7,14}", candidate):
        raise ValueError("phone number must be in E.164 format")
    return candidate


def build_business_call_script(request: BusinessCallRequest) -> str:
    question_text = " ".join(
        f"{index}. {question}"
        for index, question in enumerate(request.questions, start=1)
    )
    return (
        "Здравствуйте. Это автоматический помощник Матвея. "
        "Разговор записывается, чтобы точно передать ему ответ. "
        f"Цель звонка: {request.goal}. "
        f"Подскажите, пожалуйста: {question_text} "
        "После сигнала можно ответить одним сообщением. Спасибо."
    )


def parse_llm_call_extraction(request_id: str, text: str) -> CallExtraction:
    payload = _extract_json_object(text)
    if not isinstance(payload, dict):
        raise ValueError("LLM response is not a JSON object")

    facts: list[ExtractedCallFact] = []
    for item in payload.get("facts", payload.get("fields", [])):
        if not isinstance(item, dict):
            continue
        name = _as_string(item.get("name") or item.get("field") or item.get("key"))
        value = _as_string(item.get("value"))
        if not name or not value:
            continue
        facts.append(
            ExtractedCallFact(
                name=name,
                value=value,
                confidence=_as_confidence(item.get("confidence")),
                evidence=_as_string(item.get("evidence")),
            )
        )

    summary = _as_string(payload.get("summary")) or (
        "ИИ не вернул краткое резюме."
    )
    missing_items = tuple(_string_list(payload.get("missing_items")))
    next_actions = tuple(_string_list(payload.get("next_actions")))
    confidence = _as_confidence(payload.get("confidence"))
    return CallExtraction(
        request_id=request_id,
        summary=summary,
        facts=tuple(facts),
        missing_items=missing_items,
        next_actions=next_actions,
        confidence=confidence,
    )


def render_call_placement(request: BusinessCallRequest, placement: BusinessCallPlacement) -> str:
    if placement.status == BusinessCallStatus.DRY_RUN:
        return (
            "Звонок подготовлен в dry-run режиме.\n"
            f"ID: {request.request_id}\n"
            f"Кому: {request.target_name}, {request.phone_e164}\n"
            f"Сценарий: {placement.message}"
        )
    return (
        "Звонок поставлен в очередь.\n"
        f"ID: {request.request_id}\n"
        f"Провайдер: {placement.provider}\n"
        f"Call ID: {placement.provider_call_id or 'пока нет'}"
    )


def render_call_extraction(extraction: CallExtraction) -> str:
    lines = [f"Разбор звонка {extraction.request_id}", extraction.summary]
    if extraction.facts:
        lines.append("Факты:")
        lines.extend(f"- {fact.name}: {fact.value}" for fact in extraction.facts[:12])
    if extraction.missing_items:
        lines.append("Не выяснено:")
        lines.extend(f"- {item}" for item in extraction.missing_items[:8])
    if extraction.next_actions:
        lines.append("Дальше:")
        lines.extend(f"- {action}" for action in extraction.next_actions[:5])
    lines.append(f"Уверенность: {extraction.confidence:.2f}")
    return "\n".join(lines)


def _post_json(url: str, data: bytes, *, token: str, timeout_seconds: float) -> dict[str, Any]:
    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "telegram-secretary/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM worker failed: {detail[:240]}") from exc
    except URLError as exc:
        raise RuntimeError(f"LLM worker failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("LLM worker response is not an object")
    return payload


def _llm_response_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, str):
        return text
    result = payload.get("result")
    if isinstance(result, dict):
        response = result.get("response")
        if isinstance(response, str):
            return response
        nested_text = result.get("text")
        if isinstance(nested_text, str):
            return nested_text
    raise RuntimeError("LLM response text missing")


def _transcription_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, str):
        return text.strip()
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"].strip()
    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("text"), str):
        return response["text"].strip()
    return ""


def _download_recording_url(
    recording_url: str,
    *,
    timeout_seconds: float,
    twilio_account_sid: str | None = None,
    twilio_auth_token: str | None = None,
    bearer_token: str | None = None,
) -> bytes:
    url = recording_url
    if (
        twilio_account_sid
        and twilio_auth_token
        and not re.search(r"\.(mp3|wav|m4a)(\?.*)?$", url, flags=re.IGNORECASE)
    ):
        url = f"{url}.mp3"
    headers = {"User-Agent": "telegram-secretary/0.1"}
    if twilio_account_sid and twilio_auth_token:
        token = f"{twilio_account_sid}:{twilio_auth_token}".encode("utf-8")
        headers["Authorization"] = f"Basic {base64.b64encode(token).decode('ascii')}"
    elif bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"recording download failed: {detail[:240]}") from exc
    except URLError as exc:
        raise RuntimeError(f"recording download failed: {exc.reason}") from exc


def _extract_json_object(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def _as_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_confidence(value: Any) -> float:
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "."))
        except ValueError:
            number = 0.0
    else:
        number = 0.0
    return max(0.0, min(1.0, number))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _split_questions(text: str) -> tuple[str, ...]:
    candidates = re.split(r"[;\n]|\?(?:\s|$)", text)
    questions = tuple(
        candidate.strip(" .?-")
        for candidate in candidates
        if candidate.strip(" .?-")
    )
    return questions[:12]


def _goal_from_questions(goal_text: str, questions: tuple[str, ...]) -> str:
    if questions:
        return questions[0]
    return goal_text.strip(" .")


def _split_sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]


def _summary_from_sentences(sentences: list[str], fallback: str) -> str:
    if not sentences:
        return fallback
    summary = " ".join(sentences[:2])
    if len(summary) > 360:
        return summary[:357].rstrip() + "..."
    return summary


def _extract_price_facts(sentences: list[str]) -> list[ExtractedCallFact]:
    facts: list[ExtractedCallFact] = []
    price_re = re.compile(
        (
            r"(?P<value>(?:\d[\d\s]{1,8})(?:[,.]\d{1,2})?\s*"
            r"(?:руб(?:\.|лей|ля|ль)?|р\.?|₽|тыс\.?\s*руб))"
        ),
        flags=re.IGNORECASE,
    )
    for sentence in sentences:
        for match in price_re.finditer(sentence):
            facts.append(
                ExtractedCallFact(
                    "стоимость",
                    match.group("value").strip(),
                    0.68,
                    sentence,
                )
            )
    return facts


def _extract_schedule_facts(sentences: list[str]) -> list[ExtractedCallFact]:
    keywords = (
        "работ",
        "занят",
        "расписан",
        "время",
        "час",
        "будни",
        "выходн",
    )
    time_re = re.compile(
        r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b|\bс\s*\d{1,2}\s*до\s*\d{1,2}\b",
        re.IGNORECASE,
    )
    facts: list[ExtractedCallFact] = []
    for sentence in sentences:
        lowered = sentence.casefold()
        if any(keyword in lowered for keyword in keywords) and (
            time_re.search(sentence) or _contains_weekday(lowered)
        ):
            facts.append(ExtractedCallFact("расписание", sentence, 0.58, sentence))
    return facts


def _extract_availability_facts(sentences: list[str]) -> list[ExtractedCallFact]:
    keywords = (
        "свобод",
        "мест",
        "окн",
        "можно запис",
        "можем запис",
        "доступ",
    )
    facts: list[ExtractedCallFact] = []
    for sentence in sentences:
        lowered = sentence.casefold()
        if any(keyword in lowered for keyword in keywords):
            facts.append(ExtractedCallFact("доступность", sentence, 0.6, sentence))
    return facts


def _extract_reservation_facts(sentences: list[str]) -> list[ExtractedCallFact]:
    keywords = ("заброни", "бронь", "резерв", "стол")
    facts: list[ExtractedCallFact] = []
    for sentence in sentences:
        lowered = sentence.casefold()
        if any(keyword in lowered for keyword in keywords):
            facts.append(ExtractedCallFact("бронь", sentence, 0.62, sentence))
    return facts


def _extract_subscription_facts(sentences: list[str]) -> list[ExtractedCallFact]:
    keywords = ("абонем", "трениров", "заняти", "корт", "тренер")
    facts: list[ExtractedCallFact] = []
    for sentence in sentences:
        lowered = sentence.casefold()
        if any(keyword in lowered for keyword in keywords):
            facts.append(
                ExtractedCallFact("абонементы/занятия", sentence, 0.58, sentence)
            )
    return facts


def _extract_contact_facts(text: str) -> list[ExtractedCallFact]:
    facts: list[ExtractedCallFact] = []
    for email in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
        facts.append(ExtractedCallFact("email", email, 0.8, email))
    for url in re.findall(r"https?://[^\s,]+", text):
        facts.append(ExtractedCallFact("ссылка", url, 0.8, url))
    return facts


def _question_has_answer(question: str, sentences: list[str]) -> bool:
    terms = _meaningful_terms(question)
    if not terms:
        return bool(sentences)
    for sentence in sentences:
        lowered = sentence.casefold()
        matches = sum(1 for term in terms if term in lowered)
        if matches >= min(2, len(terms)):
            return True
    return False


def _meaningful_terms(text: str) -> tuple[str, ...]:
    stop_words = {
        "что",
        "как",
        "какие",
        "какой",
        "какая",
        "сколько",
        "можно",
        "есть",
        "про",
        "для",
        "или",
        "это",
        "там",
        "and",
        "the",
        "with",
    }
    terms = []
    for match in re.finditer(r"[a-zа-яё0-9]{4,}", text.casefold()):
        term = match.group(0)
        if term not in stop_words:
            terms.append(term)
    return tuple(dict.fromkeys(terms))


def _contains_weekday(text: str) -> bool:
    return any(
        day in text
        for day in (
            "понедель",
            "вторник",
            "сред",
            "четвер",
            "пятниц",
            "суббот",
            "воскрес",
            "пн",
            "вт",
            "ср",
            "чт",
            "пт",
            "сб",
            "вс",
        )
    )


def _next_actions_from_missing(missing: tuple[str, ...]) -> tuple[str, ...]:
    if not missing:
        return (
            "Передать владельцу результат и ждать подтверждения на действие.",
        )
    return ("Уточнить вопросы, на которые не получен ответ.",)


def _dedupe_facts(facts: list[ExtractedCallFact]) -> list[ExtractedCallFact]:
    seen: set[tuple[str, str]] = set()
    result: list[ExtractedCallFact] = []
    for fact in facts:
        key = (fact.name.casefold(), fact.value.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(fact)
    return result


_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")

_CALL_COMMAND_HELP = (
    "Формат: /call +79991234567 | Теннисный клуб | "
    "узнать стоимость абонемента; какие дни и часы свободны"
)

_CALL_ANALYSIS_SYSTEM_PROMPT = """
Ты разбираешь транскрипт телефонного разговора личного секретаря.
Верни только JSON без Markdown по схеме:
{
  "summary": "краткое резюме на русском",
  "facts": [
    {
      "name": "стоимость/расписание/бронь/условие/контакт",
      "value": "...",
      "confidence": 0.0,
      "evidence": "короткая цитата или пересказ"
    }
  ],
  "missing_items": ["что из заданных вопросов не выяснено"],
  "next_actions": ["что владельцу нужно сделать дальше"],
  "confidence": 0.0
}
Не придумывай факты. Если информации нет в транскрипте, укажи это в missing_items.
""".strip()
