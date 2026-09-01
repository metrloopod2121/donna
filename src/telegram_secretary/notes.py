from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from telegram_secretary.models import NoteItem, TaskItem, TaskPriority


class NotesProvider(Protocol):
    def notes_for_day(self, day: date) -> list[NoteItem]:
        ...

    def tasks_for_day(self, day: date) -> list[TaskItem]:
        ...


@dataclass(frozen=True)
class DailyContext:
    day: date
    notes: tuple[NoteItem, ...]
    tasks: tuple[TaskItem, ...]

    def open_tasks(self) -> tuple[TaskItem, ...]:
        return tuple(task for task in self.tasks if not task.completed)


class StaticNotesProvider:
    def __init__(
        self,
        notes: list[NoteItem] | None = None,
        tasks: list[TaskItem] | None = None,
    ) -> None:
        self._notes = notes or []
        self._tasks = tasks or []

    def notes_for_day(self, day: date) -> list[NoteItem]:
        return list(self._notes)

    def tasks_for_day(self, day: date) -> list[TaskItem]:
        return list(self._tasks)


class MarkdownExportNotesProvider:
    """Reads Craft-exported Markdown daily notes and checklist tasks."""

    task_re = re.compile(r"^\s*[-*]\s+\[(?P<done>[ xX])]\s+(?P<title>.+?)\s*$")

    def __init__(self, export_dir: Path | str) -> None:
        self.export_dir = Path(export_dir)

    def notes_for_day(self, day: date) -> list[NoteItem]:
        path = self._daily_path(day)
        if not path.exists():
            return []
        body = path.read_text(encoding="utf-8")
        return [
            NoteItem(
                note_id=f"craft-export:{path.name}",
                title=path.stem,
                body=body,
                source="craft-markdown-export",
                updated_at=datetime.fromtimestamp(path.stat().st_mtime),
            )
        ]

    def tasks_for_day(self, day: date) -> list[TaskItem]:
        path = self._daily_path(day)
        if not path.exists():
            return []

        tasks: list[TaskItem] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = self.task_re.match(line)
            if not match:
                continue
            title = match.group("title").strip()
            tasks.append(
                TaskItem(
                    task_id=f"craft-export:{path.name}:{index}",
                    title=title,
                    due_at=None,
                    priority=_priority_from_title(title),
                    source="craft-markdown-export",
                    should_call=_should_call(title),
                    completed=match.group("done").lower() == "x",
                )
            )
        return tasks

    def _daily_path(self, day: date) -> Path:
        return self.export_dir / f"{day.isoformat()}.md"


def build_daily_context(provider: NotesProvider, day: date) -> DailyContext:
    return DailyContext(
        day=day,
        notes=tuple(provider.notes_for_day(day)),
        tasks=tuple(provider.tasks_for_day(day)),
    )


def render_daily_context(context: DailyContext) -> str:
    open_tasks = context.open_tasks()
    lines = [f"Дела на {context.day:%d.%m}:"]
    if open_tasks:
        lines.extend(f"- {task.title}" for task in open_tasks[:10])
    else:
        lines.append("- Открытых задач нет.")

    if context.notes:
        lines.append(f"Заметки: {len(context.notes)} источник(а).")
    return "\n".join(lines)


def _priority_from_title(title: str) -> TaskPriority:
    lowered = title.lower()
    if "#high" in lowered or "#urgent" in lowered or "срочно" in lowered:
        return TaskPriority.HIGH
    if "#low" in lowered:
        return TaskPriority.LOW
    return TaskPriority.MEDIUM


def _should_call(title: str) -> bool:
    lowered = title.lower()
    return "#call" in lowered or "позвонить" in lowered or "напомнить звонком" in lowered

