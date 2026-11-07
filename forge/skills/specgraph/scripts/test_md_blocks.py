#!/usr/bin/env python3
"""test_md_blocks.py — Markdown исходника → неделимые блоки (задача 1.1).

Пинит:
  • склейка текстов блоков = файл ПОБАЙТНО — на всех файлах фикстуры tz-mini и всех вариантах
    стресс-корпуса (CRLF, BOM, неразрывные пробелы, HTML-таблицы, setext, незакрытый фенс);
  • повторный разбор — те же id и sha1;
  • фенс, таблица, пункт списка с вложенными пунктами и продолжениями — ОДИН блок; фенс внутри пункта
    (с отступом) — часть пункта; setext — заголовок; жирная строка — абзац;
  • незакрытый фенс не проглатывает следующий раздел: закрывается перед заголовком того же уровня,
    W-fence-unclosed с номером строки;
  • путь заголовков и раздел блока; frontmatter исходника — свой блок;
  • скан проекта: исключённый черновик не сканируется, сводка короткая; «раздутый» корпус — быстро
    (урок §G.1 про O(B²)).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import gen_variants as GV  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import tools as T  # noqa: E402

FIX = SCRIPTS.parent / "fixtures" / "tz-mini" / "sources"


def _types(text):
    return [b["type"] for b in B.parse(text, "t.md")[0]]


class Lossless(unittest.TestCase):
    def test_concat_equals_file_on_fixture_and_all_variants(self):
        with tempfile.TemporaryDirectory() as td:
            roots = [FIX] + [GV.materialize(Path(td), v) for v in sorted(GV.VARIANTS)]
            for root in roots:
                for f in sorted(root.rglob("*.md")):
                    with self.subTest(root=root.name, file=f.name):
                        text = f.read_bytes().decode("utf-8")
                        blocks, _ = B.parse(text, f.name)
                        self.assertEqual("".join(b["text"] for b in blocks), text)

    def test_stable_ids_and_hashes(self):
        text = (FIX / "task-service" / "task-service.md").read_text(encoding="utf-8")
        a, _ = B.parse(text, "x.md")
        b, _ = B.parse(text, "x.md")
        self.assertEqual([(x["id"], x["sha1"]) for x in a], [(x["id"], x["sha1"]) for x in b])
        self.assertEqual(a[0]["id"], "x.md#1")


class Indivisible(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(_types("# H\n\n```json\n{\n\n  \"a\": 1\n\n}\n```\n"), ["heading", "fence"])
        self.assertEqual(_types("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"), ["table"])
        self.assertEqual(_types("1. первый\n   продолжение\n   - вложенный\n2. второй\n"), ["list_item", "list_item"])
        self.assertEqual(_types("- пункт\n\n  абзац внутри пункта\n- второй\n"), ["list_item", "list_item"])
        self.assertEqual(_types("1. шаг\n\n   ```json\n   {}\n   ```\n\n2. шаг\n"), ["list_item", "list_item"])
        self.assertEqual(_types("Заголовок\n=========\n\nтекст\n"), ["heading", "paragraph"])
        self.assertEqual(_types("**2.1 Шедулер**\n\nтекст\n"), ["paragraph", "paragraph"])
        self.assertEqual(_types("<table>\n<tr><td>1</td></tr>\n</table>\n\nтекст\n"), ["html", "paragraph"])
        self.assertEqual(_types("абзац\nещё строка\n| a |\n"), ["paragraph", "table"])
        self.assertEqual(_types("---\nkind: x\n---\n# H\n"), ["frontmatter", "heading"])
        self.assertEqual(_types("текст\n\n---\n\nтекст\n"), ["paragraph", "hr", "paragraph"])

    def test_fixture_fences_and_tables_are_whole(self):
        text = (FIX / "task-service" / "task-service.md").read_text(encoding="utf-8")
        blocks, warnings = B.parse(text, "ts.md")
        self.assertEqual(warnings, [])
        fences = [b for b in blocks if b["type"] == "fence"]
        self.assertEqual(len(fences), text.count("```") // 2)
        for f in fences:
            self.assertTrue(f["text"].rstrip().endswith("```"))
        self.assertEqual({f["lang"] for f in fences}, {"json", "sql"})
        tables = [b for b in blocks if b["type"] == "table"]
        self.assertEqual(len(tables), 4)

    def test_paths_and_sections(self):
        text = "# svc\n\n## 2. Шедулеры\n\n### 2.1 Шедулер А\n\nтекст\n\n## 3. Kafka\n\nещё\n"
        blocks, _ = B.parse(text, "s.md")
        para = blocks[3]
        self.assertEqual(para["path"], ["svc", "2. Шедулеры", "2.1 Шедулер А"])
        self.assertEqual(para["section"], "s.md#3")
        self.assertEqual(blocks[5]["path"], ["svc", "3. Kafka"])
        self.assertEqual((para["start"], para["end"]), (7, 7))


class UnclosedFence(unittest.TestCase):
    def test_closes_before_next_heading_of_same_level(self):
        text = "## 3. Kafka\n\n### 3.1 Обработка\n\n```json\n{\"a\": 1}\n\nПри ответе — сохранить.\n\n### 3.2 Отправка\n\nтекст\n"
        blocks, warnings = B.parse(text, "k.md")
        self.assertEqual([b["type"] for b in blocks], ["heading", "heading", "fence", "heading", "paragraph"])
        self.assertEqual(warnings, [{"code": "W-fence-unclosed", "line": 5, "file": "k.md"}])
        self.assertIn("### 3.2 Отправка", blocks[3]["text"])

    def test_variant_of_the_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            d = GV.materialize(Path(td), "unclosed-fence")
            text = (d / "task-service" / "task-service.md").read_text(encoding="utf-8")
            blocks, warnings = B.parse(text, "ts.md")
            self.assertEqual([w["code"] for w in warnings], ["W-fence-unclosed"])
            heads = [b["title"] for b in blocks if b["type"] == "heading"]
            self.assertIn("3.2 Отправка события о закрытии задачи", heads, "следующий раздел не проглочен")


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(FIX, self.root / "docs" / "spec")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_respects_exclude_and_writes_blocks(self):
        T.call("graph_init", self.root, {"sources": ["docs/spec"], "exclude": ["**/*-draft.md"]})
        res = T.call("graph_scan", self.root, {})
        files = [s["file"] for s in res["summary"]]
        self.assertNotIn("docs/spec/task-service/task-service-draft.md", files)
        self.assertIn("docs/spec/task-service/task-service.md", files)
        self.assertEqual(res["files"], 8)
        data = B.load(self.root, "docs/spec/task-service/task-service.md")
        self.assertEqual("".join(b["text"] for b in data["blocks"]),
                         (self.root / "docs/spec/task-service/task-service.md").read_text(encoding="utf-8"))
        ts = next(s for s in res["summary"] if s["file"].endswith("task-service.md"))
        self.assertEqual(ts["outline"][:3], ["task-service", "  1. Общее описание", "  2. Шедулеры"])
        self.assertLess(len(json.dumps(res, ensure_ascii=False)), 6000, "ответ модели короткий")

    def test_scope_and_uninitialized(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_scan", self.root, {})
        self.assertEqual(cm.exception.codes, ["E-config-missing"])
        T.call("graph_init", self.root, {"sources": ["docs/spec"], "exclude": ["**/*-draft.md"]})
        res = T.call("graph_scan", self.root, {"scope": "docs/spec/ui"})
        self.assertEqual(res["files"], 4)

    def test_bloated_corpus_is_fast(self):
        d = GV.bloated(self.root / "big")
        cfg = {"sources": [{"root": d.relative_to(self.root).as_posix()}], "exclude": []}
        t0 = time.monotonic()
        res = B.scan(self.root, cfg)
        self.assertLess(time.monotonic() - t0, 30)
        self.assertEqual(res["files"], 50)
        self.assertEqual(res["more_files"], 20)


if __name__ == "__main__":
    unittest.main()
