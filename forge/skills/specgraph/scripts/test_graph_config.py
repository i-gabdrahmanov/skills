#!/usr/bin/env python3
"""test_graph_config.py — каркас spec-graph (задача 0.1): конфиг проекта, первый запуск, гейт-заглушка.

Пинит:
  • схема `.spec-graph/config.json` строгая: неизвестный ключ, абсолютный путь, `..`, vault внутри
    исходника, неизвестный адаптер — ошибки, а не тихое чтение (урок прототипа: специфика репо в коде);
  • `detect` предлагает кандидатов с доказательствами и не лезет в служебные каталоги;
  • `graph_status` на пустом репо — вопросы первого запуска; `graph_init` пишет конфиг по ответам и
    НЕ перезаписывает существующий; несуществующий корень — отказ без записи;
  • ручная проверка аргументов инструментов: неизвестный аргумент и неверный тип — ToolError;
  • `graph_gate.py` нереализованного шага (check) — exit 3 «не реализовано»: шаг не закрывается; map и link
    без конфига — exit 1.
Данные — синтетические репо во временном каталоге.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import config as C  # noqa: E402
from sgraph import tools as T  # noqa: E402


def _good() -> dict:
    return {"version": 1, "vault": "docs/graph", "sources": [{"root": "docs/system-requirements", "adapter": "md"}],
            "exclude": ["**/*-draft.md"], "services": {"docs/system-requirements/upz": "upz-adapter"},
            "ontology_version": 1}


def _spec_repo(root: Path) -> None:
    for rel, n in (("docs/system-requirements/task-service", 3), ("docs/system-requirements/rmoc-service", 2),
                   ("notes", 1), (".gigacode/extensions/spec-graph/skills", 4), ("ground", 2)):
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (d / f"f{i}.md").write_text("# x\n" * (i + 1), encoding="utf-8")


class Schema(unittest.TestCase):
    def test_good_config_has_no_errors(self):
        self.assertEqual(C.validate(_good()), [])

    def test_violations_are_reported_together(self):
        bad = _good()
        bad.update({"extra": 1, "vault": "/abs/graph", "version": 2})
        bad["sources"] = [{"root": "../outside", "adapter": "md"}, {"root": "docs/a", "adapter": "pdf"},
                          {"root": "docs/a"}, "docs/b"]
        bad["exclude"] = ["ok", 3]
        bad["services"] = {"a": ""}
        errors = C.validate(bad)
        joined = "\n".join(errors)
        for needle in ("неизвестный ключ «extra»", "version", "vault:", "sources[0].root", "sources[1].adapter",
                       "указан дважды", "sources[3]", "exclude", "services"):
            self.assertIn(needle, joined)

    def test_vault_inside_a_source_is_refused(self):
        cfg = _good()
        cfg["vault"] = "docs/system-requirements/graph"
        self.assertTrue(any("вложены" in e for e in C.validate(cfg)))
        cfg["vault"] = "docs"
        self.assertTrue(any("вложены" in e for e in C.validate(cfg)))

    def test_empty_sources_is_an_error(self):
        cfg = _good()
        cfg["sources"] = []
        self.assertTrue(any(e.startswith("sources:") for e in C.validate(cfg)))
        self.assertEqual(C.validate([]), ["конфиг: ожидается JSON-объект"])

    def test_backslash_and_windows_paths_are_refused(self):
        for bad in ("docs\\graph", "C:/graph", "~/graph"):
            cfg = _good()
            cfg["vault"] = bad
            self.assertTrue(any(e.startswith("vault:") for e in C.validate(cfg)), bad)


class FirstRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _spec_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_ranks_spec_dirs_and_skips_service_dirs(self):
        cands = C.detect(self.root)
        roots = [c["root"] for c in cands]
        self.assertEqual(roots[0], "docs/system-requirements")
        self.assertIn("notes", roots)
        self.assertFalse(any(r.startswith((".gigacode", "ground")) for r in roots))
        self.assertIn("5 MD-файлов", cands[0]["evidence"])

    def test_status_asks_first_run_questions(self):
        st = T.call("graph_status", self.root, {})
        self.assertFalse(st["initialized"])
        self.assertEqual([q["id"] for q in st["questions"]], ["sources", "exclude", "vault"])
        self.assertEqual(st["questions"][0]["candidates"][0]["root"], "docs/system-requirements")

    def test_init_writes_and_never_overwrites(self):
        res = T.call("graph_init", self.root, {"sources": ["docs/system-requirements"], "exclude": ["**/*-draft.md"]})
        self.assertEqual(res["status"], "written")
        cfg = json.loads(C.config_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(cfg["vault"], "docs/graph")
        self.assertEqual(cfg["sources"], [{"adapter": "md", "root": "docs/system-requirements"}])
        before = C.config_path(self.root).read_text(encoding="utf-8")
        again = T.call("graph_init", self.root, {"sources": ["notes"]})
        self.assertEqual(again["status"], "exists")
        self.assertEqual(C.config_path(self.root).read_text(encoding="utf-8"), before)
        st = T.call("graph_status", self.root, {})
        self.assertTrue(st["initialized"])
        self.assertEqual(st["sources"], ["docs/system-requirements"])

    def test_init_refuses_missing_root_without_writing(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_init", self.root, {"sources": ["docs/nope"]})
        self.assertIn("не существует", str(cm.exception))
        self.assertFalse(C.config_path(self.root).exists())

    def test_broken_config_is_reported_not_overwritten(self):
        p = C.config_path(self.root)
        p.parent.mkdir(parents=True)
        p.write_text('{"version": 1, "vault": "/abs"}', encoding="utf-8")
        st = T.call("graph_status", self.root, {})
        self.assertFalse(st["initialized"])
        self.assertTrue(st["errors"])
        self.assertEqual(T.call("graph_init", self.root, {"sources": ["notes"]})["status"], "exists")


class ToolArgs(unittest.TestCase):
    def test_unknown_and_mistyped_args_are_refused_together(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(T.ToolError) as cm:
                T.call("graph_init", Path(td), {"sources": "docs", "vault": 3, "bogus": 1})
            msg = str(cm.exception)
            for needle in ("«bogus»", "«sources»: ожидается array", "«vault»: ожидается string"):
                self.assertIn(needle, msg)
            with self.assertRaises(T.ToolError):
                T.call("graph_init", Path(td), {"sources": ["a", 1]})
            with self.assertRaises(T.ToolError):
                T.call("graph_init", Path(td), {})
            with self.assertRaises(T.ToolError):
                T.call("graph_status", Path(td), {"x": 1})

    def test_every_tool_has_a_closed_schema(self):
        for name, tool in T.TOOLS.items():
            with self.subTest(tool=name):
                self.assertTrue(name.startswith("graph_"))
                self.assertIs(tool["schema"].get("additionalProperties"), False)
                self.assertTrue(tool["description"])


class GateStub(unittest.TestCase):
    def test_unimplemented_steps_stop_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "graph_gate.py"), "map", "--project", td],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 1, "map реализован (1.7): без конфига — не пройден")
            r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "graph_gate.py"), "link", "--project", td],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 1, "link реализован (2.6): без конфига — не пройден")
            for step in ("check",):
                with self.subTest(step=step):
                    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "graph_gate.py"), step,
                                        "--project", td], capture_output=True, text=True, encoding="utf-8")
                    self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                    self.assertIn("не реализован", r.stdout)
            r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "graph_gate.py"), "build"],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
