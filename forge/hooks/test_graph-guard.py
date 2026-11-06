#!/usr/bin/env python3
"""Тесты hooks/graph-guard.py — frontmatter и маркеры заметок графа пишут только инструменты spec-graph (2.4).

Каждый запрет — вместе с разрешённым двойником (иначе тест удовлетворил бы хук, запрещающий всё):
  • Edit/Write заметки vault, меняющие frontmatter или маркер graph:*, — отказ; правка прозы вне маркеров — можно;
  • новая заметка vault моделью — отказ; запись в `_graph/` и `.spec-graph/` — отказ; файл вне vault — можно;
  • shell-запись в заметку графа и в `.spec-graph/` — отказ; чтение и запись вне — можно;
  • вне проекта spec-graph (нет `.spec-graph/config.json`) — noop даже для «опасных» правок;
  • битый frontmatter после правки — отказ.
Мир — синтетический проект с одной заметкой, записанной кодеком движка.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
HOOK = HOOKS / "graph-guard.py"
NOTE = """---
kind: scheduler
belongs_to:
  - "[[task-service]]"
closes_task:
  - "[[Укомплектование]]"
status: draft
---
# Шедулер закрытия

<!-- graph:source:start {"file": "docs/spec/a.md"} -->

Запускается ежедневно в 23:00.

<!-- graph:source:end -->

Проза аналитика.
"""


def run(root: Path, tool: str, tool_input: dict):
    data = {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    r = subprocess.run([sys.executable, "-X", "utf8", str(HOOK)], input=json.dumps(data), capture_output=True,
                       text=True, encoding="utf-8")
    return r.returncode, r.stderr


class _World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)])
        (self.root / ".spec-graph").mkdir()
        (self.root / ".spec-graph" / "config.json").write_text(json.dumps({"vault": "docs/graph"}), encoding="utf-8")
        self.note = self.root / "docs/graph/services/task-service/schedulers/Шедулер закрытия.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text(NOTE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, old, new):
        return run(self.root, "edit", {"file_path": str(self.note), "old_string": old, "new_string": new})


class Edits(_World):
    def test_frontmatter_change_denied_prose_allowed(self):
        rc, err = self.edit('  - "[[Укомплектование]]"', '  - "[[Укомплектование]]"\n  - "[[Регистрация]]"')
        self.assertEqual(rc, 2)
        self.assertIn("closes_task", err)
        self.assertEqual(self.edit("Проза аналитика.", "Проза аналитика. Уточнение по расписанию.")[0], 0)
        self.assertEqual(self.edit("status: draft", "status: stable")[0], 2)
        rc, err = self.edit('  - "[[Укомплектование]]"', "  - [[Укомплектование]]")
        self.assertEqual(rc, 2, "некавычная ссылка ломает frontmatter")

    def test_marker_change_denied(self):
        self.assertEqual(self.edit("в 23:00", "в 22:00")[0], 2)
        self.assertEqual(self.edit('{"file": "docs/spec/a.md"}', '{"file": "docs/spec/b.md"}')[0], 2)

    def test_write_whole_file(self):
        rc, _ = run(self.root, "write_file", {"file_path": str(self.note), "content": NOTE.replace("Проза", "Новая проза")})
        self.assertEqual(rc, 0)
        rc, _ = run(self.root, "write_file", {"file_path": str(self.note), "content": NOTE.replace("status: draft", "status: x")})
        self.assertEqual(rc, 2)

    def test_new_note_state_and_reports(self):
        new = self.root / "docs/graph/services/task-service/schedulers/Новый.md"
        self.assertEqual(run(self.root, "write_file", {"file_path": str(new), "content": "# x\n"})[0], 2)
        self.assertEqual(run(self.root, "write_file", {"file_path": str(self.root / "docs/graph/_graph/catalog.md"),
                                                       "content": "x"})[0], 2)
        self.assertEqual(run(self.root, "write_file", {"file_path": str(self.root / ".spec-graph/catalog.json"),
                                                       "content": "{}"})[0], 2)
        self.assertEqual(run(self.root, "write_file", {"file_path": str(self.root / "docs/notes.md"), "content": "x"})[0], 0)


class Shell(_World):
    def test_shell_writes(self):
        self.assertEqual(run(self.root, "run_shell_command", {"command": f"echo x >> '{self.note}'"})[0], 2)
        self.assertEqual(run(self.root, "run_shell_command", {"command": "echo {} > .spec-graph/queue/x.json"})[0], 2)
        self.assertEqual(run(self.root, "run_shell_command", {"command": f"cat '{self.note}'"})[0], 0)
        self.assertEqual(run(self.root, "run_shell_command", {"command": "echo x > docs/other.txt"})[0], 0)


class Outside(unittest.TestCase):
    def test_noop_without_spec_graph(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)])
            note = root / "docs/graph/a.md"
            note.parent.mkdir(parents=True)
            note.write_text(NOTE, encoding="utf-8")
            rc, _ = run(root, "edit", {"file_path": str(note), "old_string": "status: draft", "new_string": "status: x"})
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
