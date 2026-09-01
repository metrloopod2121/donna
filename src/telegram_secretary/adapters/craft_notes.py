from __future__ import annotations

from datetime import date

from telegram_secretary.models import NoteItem, TaskItem
from telegram_secretary.notes import NotesProvider


class CraftNotesProvider(NotesProvider):
    """Preferred notes/tasks provider.

    For a standalone service, implement this against a confirmed Craft access path:
    a headless API, MCP bridge, or scheduled Craft export. Until that is available, use
    `MarkdownExportNotesProvider` for exported Craft daily notes.
    """

    def notes_for_day(self, day: date) -> list[NoteItem]:
        raise NotImplementedError("Use a confirmed Craft API/MCP/export access path.")

    def tasks_for_day(self, day: date) -> list[TaskItem]:
        raise NotImplementedError("Use a confirmed Craft API/MCP/export access path.")

