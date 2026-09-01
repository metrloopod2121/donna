from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Protocol

from telegram_secretary.models import TaskItem, TaskPriority


class VoiceProvider(Protocol):
    def answer_owner_call(self, prompt: str) -> str:
        ...

    def place_owner_reminder_call(self, reminder: "CallReminder") -> str:
        ...


@dataclass(frozen=True)
class CallReminder:
    task_id: str
    title: str
    priority: TaskPriority
    call_at: datetime


@dataclass(frozen=True)
class CallReminderPolicy:
    outbound_enabled: bool = False
    min_priority: TaskPriority = TaskPriority.HIGH
    quiet_hours: tuple[time, time] = (time(22), time(9))

    def should_call_for_task(self, task: TaskItem, now: datetime) -> bool:
        if not self.outbound_enabled or not task.should_call or task.completed:
            return False
        if _priority_rank(task.priority) < _priority_rank(self.min_priority):
            return False
        if self._inside_quiet_hours(now.time()):
            return False
        return True

    def _inside_quiet_hours(self, current: time) -> bool:
        start, end = self.quiet_hours
        if start <= end:
            return start <= current < end
        return current >= start or current < end


class DryRunVoiceProvider:
    def answer_owner_call(self, prompt: str) -> str:
        return f"VOICE_DRY_RUN inbound response: {prompt}"

    def place_owner_reminder_call(self, reminder: CallReminder) -> str:
        return f"VOICE_DRY_RUN outbound call for task {reminder.task_id}: {reminder.title}"


def _priority_rank(priority: TaskPriority) -> int:
    return {
        TaskPriority.LOW: 1,
        TaskPriority.MEDIUM: 2,
        TaskPriority.HIGH: 3,
    }[priority]

