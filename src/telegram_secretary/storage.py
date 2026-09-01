from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from telegram_secretary.call_research import (
    BusinessCallPlacement,
    BusinessCallRequest,
    CallExtraction,
    CallRecordingNotice,
    CallTranscript,
    ExtractedCallFact,
)
from telegram_secretary.models import Classification, IncomingMessage, ReplyDecision


class SQLiteStore:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_messages (
                    message_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    is_private INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS reply_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    draft_text TEXT NOT NULL,
                    requires_confirmation INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    classification_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES inbound_messages(message_id)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS business_call_requests (
                    request_id TEXT PRIMARY KEY,
                    target_name TEXT NOT NULL,
                    phone_e164 TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    language TEXT NOT NULL,
                    max_duration_seconds INTEGER NOT NULL,
                    requested_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS business_call_placements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_call_id TEXT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES business_call_requests(request_id)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS business_call_recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_call_id TEXT,
                    recording_url TEXT,
                    recording_status TEXT NOT NULL,
                    duration_seconds INTEGER,
                    transcription_text TEXT,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES business_call_requests(request_id)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS business_call_transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    provider_call_id TEXT,
                    transcript_text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    recording_url TEXT,
                    duration_seconds INTEGER,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES business_call_requests(request_id)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS business_call_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    missing_items_json TEXT NOT NULL,
                    next_actions_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    analyzed_at TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES business_call_requests(request_id)
                )
                """
            )
            db.commit()

    def save_incoming(self, message: IncomingMessage) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO inbound_messages (
                    message_id, chat_id, sender_id, sender_name, text,
                    received_at, is_private, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.chat_id,
                    message.sender_id,
                    message.sender_name,
                    message.text,
                    message.received_at.isoformat(),
                    int(message.is_private),
                    json.dumps(message.metadata, ensure_ascii=False),
                ),
            )
            db.commit()

    def save_decision(
        self,
        message: IncomingMessage,
        classification: Classification,
        decision: ReplyDecision,
    ) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO reply_decisions (
                    message_id, action, draft_text, requires_confirmation, reasons_json,
                    classification_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    decision.action.value,
                    decision.draft_text,
                    int(decision.requires_confirmation),
                    json.dumps(decision.reasons, ensure_ascii=False),
                    json.dumps(
                        {
                            "importance": classification.importance.value,
                            "intent": classification.intent.value,
                            "confidence": classification.confidence,
                            "reasons": classification.reasons,
                        },
                        ensure_ascii=False,
                    ),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.commit()

    def recent_messages(self, limit: int = 20) -> list[dict[str, str]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT message_id, sender_name, text, received_at
                FROM inbound_messages
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def save_business_call_request(self, request: BusinessCallRequest) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO business_call_requests (
                    request_id, target_name, phone_e164, goal, questions_json,
                    language, max_duration_seconds, requested_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.target_name,
                    request.phone_e164,
                    request.goal,
                    json.dumps(request.questions, ensure_ascii=False),
                    request.language,
                    request.max_duration_seconds,
                    request.requested_at.isoformat(),
                    json.dumps(request.metadata, ensure_ascii=False),
                ),
            )
            db.commit()

    def save_business_call_placement(self, placement: BusinessCallPlacement) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO business_call_placements (
                    request_id, provider, status, provider_call_id, message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    placement.request_id,
                    placement.provider,
                    placement.status.value,
                    placement.provider_call_id,
                    placement.message,
                    placement.created_at.isoformat(),
                ),
            )
            db.commit()

    def save_business_call_recording_notice(self, notice: CallRecordingNotice) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO business_call_recordings (
                    request_id, provider, provider_call_id, recording_url, recording_status,
                    duration_seconds, transcription_text, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notice.request_id,
                    notice.provider,
                    notice.provider_call_id,
                    notice.recording_url,
                    notice.recording_status,
                    notice.duration_seconds,
                    notice.transcription_text,
                    notice.received_at.isoformat(),
                ),
            )
            db.commit()

    def save_business_call_transcript(self, transcript: CallTranscript) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO business_call_transcripts (
                    request_id, provider_call_id, transcript_text, source, recording_url,
                    duration_seconds, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.request_id,
                    transcript.provider_call_id,
                    transcript.transcript_text,
                    transcript.source,
                    transcript.recording_url,
                    transcript.duration_seconds,
                    transcript.received_at.isoformat(),
                ),
            )
            db.commit()

    def save_business_call_extraction(self, extraction: CallExtraction) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO business_call_extractions (
                    request_id, summary, facts_json, missing_items_json, next_actions_json,
                    confidence, analyzed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    extraction.request_id,
                    extraction.summary,
                    json.dumps(
                        [
                            {
                                "name": fact.name,
                                "value": fact.value,
                                "confidence": fact.confidence,
                                "evidence": fact.evidence,
                            }
                            for fact in extraction.facts
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(extraction.missing_items, ensure_ascii=False),
                    json.dumps(extraction.next_actions, ensure_ascii=False),
                    extraction.confidence,
                    extraction.analyzed_at.isoformat(),
                ),
            )
            db.commit()

    def find_business_call_request(self, request_id: str) -> BusinessCallRequest | None:
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT request_id, target_name, phone_e164, goal, questions_json,
                       language, max_duration_seconds, requested_at, metadata_json
                FROM business_call_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return _business_call_request_from_row(row)

    def recent_business_call_extractions(self, limit: int = 5) -> list[CallExtraction]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT request_id, summary, facts_json, missing_items_json, next_actions_json,
                       confidence, analyzed_at
                FROM business_call_extractions
                ORDER BY analyzed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_business_call_extraction_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        return db


def _business_call_request_from_row(row: sqlite3.Row) -> BusinessCallRequest:
    questions = _load_string_tuple(row["questions_json"])
    metadata = json.loads(row["metadata_json"])
    if not isinstance(metadata, dict):
        metadata = {}
    return BusinessCallRequest(
        request_id=row["request_id"],
        target_name=row["target_name"],
        phone_e164=row["phone_e164"],
        goal=row["goal"],
        questions=questions,
        language=row["language"],
        max_duration_seconds=int(row["max_duration_seconds"]),
        requested_at=datetime.fromisoformat(row["requested_at"]),
        metadata={str(key): str(value) for key, value in metadata.items()},
    )


def _business_call_extraction_from_row(row: sqlite3.Row) -> CallExtraction:
    facts: list[ExtractedCallFact] = []
    for item in json.loads(row["facts_json"]):
        if not isinstance(item, dict):
            continue
        facts.append(
            ExtractedCallFact(
                name=str(item.get("name", "")),
                value=str(item.get("value", "")),
                confidence=float(item.get("confidence", 0.0)),
                evidence=str(item.get("evidence", "")),
            )
        )
    return CallExtraction(
        request_id=row["request_id"],
        summary=row["summary"],
        facts=tuple(facts),
        missing_items=_load_string_tuple(row["missing_items_json"]),
        next_actions=_load_string_tuple(row["next_actions_json"]),
        confidence=float(row["confidence"]),
        analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
    )


def _load_string_tuple(raw_json: str) -> tuple[str, ...]:
    value = json.loads(raw_json)
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))
