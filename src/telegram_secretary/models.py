from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Importance(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Intent(str, Enum):
    CALENDAR_AVAILABILITY = "calendar_availability"
    GENERAL_QUESTION = "general_question"
    ACTION_REQUEST = "action_request"
    FYI = "fyi"
    UNKNOWN = "unknown"


class ReplyAction(str, Enum):
    AUTO_SEND = "auto_send"
    DRAFT_FOR_APPROVAL = "draft_for_approval"
    IGNORE = "ignore"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    chat_id: str
    sender_id: str
    sender_name: str
    text: str
    received_at: datetime
    is_private: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Classification:
    importance: Importance
    intent: Intent
    confidence: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarWindow:
    start: datetime
    end: datetime

    def label(self) -> str:
        return f"{self.start:%d.%m %H:%M}-{self.end:%H:%M}"


@dataclass(frozen=True)
class ReplyDecision:
    action: ReplyAction
    draft_text: str
    reasons: tuple[str, ...]
    requires_confirmation: bool


@dataclass(frozen=True)
class TaskItem:
    task_id: str
    title: str
    due_at: datetime | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    source: str = "unknown"
    should_call: bool = False
    completed: bool = False


@dataclass(frozen=True)
class NoteItem:
    note_id: str
    title: str
    body: str
    source: str = "unknown"
    updated_at: datetime | None = None
