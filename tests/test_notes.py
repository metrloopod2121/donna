from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from telegram_secretary.models import TaskPriority
from telegram_secretary.notes import MarkdownExportNotesProvider, build_daily_context, render_daily_context


class MarkdownExportNotesProviderTest(TestCase):
    def test_reads_today_tasks_from_craft_markdown_export(self) -> None:
        with TemporaryDirectory() as tmp:
            day = date(2026, 8, 22)
            path = Path(tmp) / "2026-08-22.md"
            path.write_text(
                "# Today\n\n- [ ] Подготовить сводку #high #call\n- [x] Готовая задача\n",
                encoding="utf-8",
            )
            provider = MarkdownExportNotesProvider(tmp)

            context = build_daily_context(provider, day)
            briefing = render_daily_context(context)

            self.assertEqual(len(context.tasks), 2)
            self.assertEqual(context.open_tasks()[0].priority, TaskPriority.HIGH)
            self.assertTrue(context.open_tasks()[0].should_call)
            self.assertIn("Подготовить сводку", briefing)

