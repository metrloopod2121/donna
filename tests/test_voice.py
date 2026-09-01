from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from telegram_secretary.models import TaskItem, TaskPriority
from telegram_secretary.voice import CallReminderPolicy


class CallReminderPolicyTest(TestCase):
    def test_allows_high_priority_call_outside_quiet_hours(self) -> None:
        policy = CallReminderPolicy(outbound_enabled=True)
        task = TaskItem(
            task_id="t1",
            title="Напомнить звонком о встрече",
            priority=TaskPriority.HIGH,
            should_call=True,
        )

        self.assertTrue(policy.should_call_for_task(task, datetime(2026, 8, 22, 12, tzinfo=timezone.utc)))

    def test_blocks_calls_during_quiet_hours(self) -> None:
        policy = CallReminderPolicy(outbound_enabled=True)
        task = TaskItem(
            task_id="t1",
            title="Напомнить звонком о встрече",
            priority=TaskPriority.HIGH,
            should_call=True,
        )

        self.assertFalse(policy.should_call_for_task(task, datetime(2026, 8, 22, 23, tzinfo=timezone.utc)))

