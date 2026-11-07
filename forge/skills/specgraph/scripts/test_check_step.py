#!/usr/bin/env python3
"""test_check_step.py — шаг graph-check и приёмка графа (задача 3.5).

Пинит на фикстуре:
  • после шагов map и link шаг проходит до exit 0; все исходники (кроме исключённого черновика) — «перенесён
    полностью»;
  • гейт блокирует: ошибка E-*; устаревший отчёт; устаревшие вопросы; устаревший Obsidian-слой; проблема смоука
    (переход из конфига без пути, неизвестный узел); нет согласия; согласие на прежний отчёт;
  • гейт ничего не пишет;
  • блок без места — файл «не полностью»; удалённый исходник — W-source-missing, но не ошибка: шаг проходит.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
import graph_gate as G  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import config as C  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

OP = "business/operations/Регистрация.md"
_healthy = {}


def setUpModule():
    tmp = tempfile.mkdtemp()
    root = K.make_project(Path(tmp) / "repo")
    K.through_review(root)
    K.approve_edges(root)
    _healthy.update(tmp=tmp, root=root)


def tearDownModule():
    shutil.rmtree(_healthy["tmp"], ignore_errors=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(_healthy["root"], self.root, symlinks=True)
        self.vault = self.root / "docs" / "graph"

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.main(["check", "--project", str(self.root)])
        return code, out.getvalue()

    def edit(self, rel, **fm):
        p = self.vault / rel
        data, body = F.read(SP.read_note(p))
        data.update(fm)
        p.write_bytes(F.write(data, body).encode("utf-8"))

    def set_cfg(self, **kv):
        p = self.root / C.CONFIG_REL
        cfg = json.loads(p.read_text(encoding="utf-8"))
        cfg.update(kv)
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


class Passes(_Base):
    def test_fixture_accepted(self):
        res = T.call("graph_check", self.root, {})
        self.assertEqual((res["errors"], res["smoke"], res["consent"]), (0, [], "none"))
        self.assertEqual(len(res["transfer"]), 8)
        self.assertTrue(all(t["full"] for t in res["transfer"]), res["transfer"])
        self.assertNotIn("task-service-draft", json.dumps(res["transfer"]))
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("согласие graph-accept-adhoc: none", out)
        self.assertEqual(K.approve_accept(self.root)["consent"], "ok")
        self.assertEqual(self.gate()[0], 0)

    def test_gate_writes_nothing(self):
        K.approve_accept(self.root)
        before = {p: p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.gate()
        self.assertEqual({p: p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}, before)

    def test_deleted_source_is_not_an_error(self):
        (self.root / "docs/spec/business/типы-задач.md").unlink()
        res = T.call("graph_check", self.root, {})
        self.assertEqual(res["errors"], 0)
        self.assertIn("W-source-missing", res["counts"])
        self.assertIn("перенесён полностью (исходник удалён)", (self.vault / "_graph/report.md").read_text(encoding="utf-8"))
        K.approve_accept(self.root)
        self.assertEqual(self.gate()[0], 0)


class Blocks(_Base):
    def test_errors(self):
        self.edit(OP, related=["[[Нет такой]]"])
        res = T.call("graph_check", self.root, {})
        self.assertGreater(res["errors"], 0)
        self.assertIn("E-ghost", res["top"][0])
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("ошибок валидатора", out)

    def test_stale_report_questions_and_consent(self):
        K.approve_accept(self.root)
        self.edit(OP, screens=[])                    # новая дыра W-op-no-ui: отчёт и вопросы устарели
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("отчёт _graph/report.md устарел", out)
        self.assertIn("open-questions.md устарел", out)
        T.call("graph_check", self.root, {})
        code, out = self.gate()
        self.assertIn("согласие graph-accept-adhoc: stale", out)
        self.assertNotIn("устарел", out.split("согласие")[0])

    def test_stale_obsidian(self):
        K.approve_accept(self.root)
        (self.vault / "_graph" / "Начало.md").unlink()
        self.assertIn("Obsidian-слой устарел", self.gate()[1])
        self.assertFalse((self.vault / "_graph" / "Начало.md").exists(), "сухая сборка слоя в гейте ничего не пишет")

    def test_smoke_walks_from_config(self):
        self.set_cfg(walks=[{"from": "UI регистрации", "to": "pprbkid.upz.task.response.v1"},
                            {"from": "Нет узла", "to": "Регистрация"}])
        res = K.approve_accept(self.root)
        self.assertEqual(len(res["smoke"]), 1, res["smoke"])
        self.assertIn("«Нет узла» → «Регистрация» из конфига: нет узла", res["smoke"][0])
        self.assertIn("смоук обхода", self.gate()[1])
        self.set_cfg(walks=[{"from": "UI регистрации", "to": "Обработка ответа УПЗ о создании задачи"}])
        res = T.call("graph_check", self.root, {})
        self.assertIn("пути нет", res["smoke"][0])

    def test_bad_walks_config(self):
        self.set_cfg(walks=[{"from": "a"}])
        with self.assertRaises(T.ToolError):
            T.call("graph_check", self.root, {})

    def test_incomplete_transfer(self):
        p = next(p for p in sorted((self.root / AS.PLAN_REL).glob("*.json"))
                 if json.loads(p.read_text(encoding="utf-8"))["assign"])
        plan = json.loads(p.read_text(encoding="utf-8"))
        plan["assign"][-1]["blocks"].pop()
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        res = T.call("graph_check", self.root, {})
        bad = [t for t in res["transfer"] if not t["full"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["why"], ["блоков без места: 1"])
        self.assertIn("не полностью: блоков без места: 1", (self.vault / "_graph/report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
