#!/usr/bin/env python3
"""test_state_paths.py — писатель и читатель evidence обязаны складывать ОДИН путь.

Файлы control-plane (ground/approvals, gates/, _origins/, overrides/) пишет один процесс,
а верифицирует другой. Расхождение в имени файла тут не падает, а молча теряет evidence:
гейт не находит маркер утверждения и либо блокирует прогон навсегда, либо (если проверка
необязательная) считает, что проверять нечего.

Прецедент, который этот тест фиксирует: record_approval писал имя через safe_key()
(санитайзер), а update._approval_marker_valid клеил СЫРОЙ ключ. Ключ вида
`brd-approved-<slug>` со слагом, который safe_slug пропускает, но санитайзер меняет
(пробел, `+`, кириллица), писался в один файл, а искался в другом — «да» пользователя
пропадало. Сейчас обе стороны берут путь из _project.approval_path.

Запуск: python3 -m unittest test_state_paths
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _util  # noqa: E402  (его импорт кладёт hooks/ в sys.path — только после него _project)
import _project  # noqa: E402
import record_approval  # noqa: E402
import record_gate  # noqa: E402
import update  # noqa: E402

# Ключи/идентификаторы, на которых сырое имя и санитайзнутое РАСХОДЯТСЯ.
GNARLY = ["brd-approved-my feature", "sdd-approved-a+b", "brd-approved-фича",
          "gate-result-04-build T1", "---"]


class TestWriterReaderAgreeOnPath(unittest.TestCase):
    """Один и тот же путь с обеих сторон — на уровне объектов и на уровне значения."""

    def test_approval_writer_and_reader_share_resolver(self):
        self.assertIs(record_approval.approval_path, _project.approval_path)
        self.assertIs(record_approval.safe_key, _project.safe_component)

    def test_gate_writer_and_reader_share_resolver(self):
        self.assertIs(record_gate.gate_result_path, _project.gate_result_path)
        self.assertIs(update._gate_result_path, _project.gate_result_path)

    def test_origin_writer_and_reader_share_resolver(self):
        self.assertIs(update._origins_dir, _project.origins_dir)

    def test_approval_roundtrip_on_gnarly_keys(self):
        """Записанный record_approval файл ДОЛЖЕН находиться читателем update."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            for key in GNARLY:
                with self.subTest(key=key):
                    path = record_approval.approval_path(project, key)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({
                        "produced_by": record_approval.PRODUCED_BY,
                        "key": key, "approved_by": "user", "reason": "test",
                    }, ensure_ascii=False), encoding="utf-8")
                    self.assertTrue(
                        update._approval_marker_valid(project, key),
                        f"маркер записан в {path}, но читатель его не нашёл (ключ {key!r})")

    def test_reader_still_rejects_marker_without_provenance(self):
        """Санитайз не должен размягчить сам гейт: без produced_by маркер не считается."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            key = "brd-approved-my feature"
            path = record_approval.approval_path(project, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"key": key, "approved_by": "user"}), encoding="utf-8")
            self.assertFalse(update._approval_marker_valid(project, key))

    def test_reader_rejects_renamed_foreign_marker(self):
        """key внутри файла обязан совпадать с запрошенным (переименование не проходит)."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            path = record_approval.approval_path(project, "brd-approved-x")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "produced_by": record_approval.PRODUCED_BY,
                "key": "sdd-approved-other", "approved_by": "user",
            }), encoding="utf-8")
            self.assertFalse(update._approval_marker_valid(project, "brd-approved-x"))


class TestSafeComponent(unittest.TestCase):
    def test_never_empty(self):
        """Пустой результат дал бы файл вида '.json' — скрытый и неотличимый от чужого."""
        for bad in ["", "---", "@@@", "///", "-", None]:
            with self.subTest(value=bad):
                out = _project.safe_component(bad)
                self.assertTrue(out, f"safe_component({bad!r}) вернул пустое имя")
                self.assertNotIn("/", out)

    def test_idempotent(self):
        """Санитайз санитайзнутого — то же самое (иначе двойной проход менял бы имя)."""
        for v in GNARLY + ["04-build-T1", "brd-judge", "KID-1"]:
            with self.subTest(value=v):
                once = _project.safe_component(v)
                self.assertEqual(_project.safe_component(once), once)

    def test_keeps_normal_ids_untouched(self):
        for v in ["04-build-T1", "brd-judge", "brd-approved-KID-1", "02-sdd"]:
            self.assertEqual(_project.safe_component(v), v)


class TestStatePathShape(unittest.TestCase):
    """Раскладка ground/ — единый резолвер, а не 60 ручных склеек по коду."""

    P = Path("/proj")

    def test_layout(self):
        s, f = "feature-pipeline", "KID-1"
        self.assertEqual(_project.state_dir(self.P, s, f),
                         self.P / "ground/statements/feature-pipeline/KID-1")
        self.assertEqual(_project.manifest_path(self.P, s, f),
                         _project.state_dir(self.P, s, f) / "manifest.json")
        self.assertEqual(_project.origin_path(self.P, s, f, "00-brd"),
                         _project.state_dir(self.P, s, f) / "_origins/00-brd.json")
        self.assertEqual(_project.gate_result_path(self.P, s, f, "05-tests"),
                         _project.state_dir(self.P, s, f) / "gates/05-tests.json")
        self.assertEqual(_project.judge_path(self.P, s, f, "design-judge"),
                         _project.state_dir(self.P, s, f) / "judges/design-judge.json")
        self.assertEqual(_project.journal_path(self.P, s, f),
                         _project.state_dir(self.P, s, f) / "journal/files.jsonl")
        self.assertEqual(_project.approvals_dir(self.P), self.P / "ground/approvals")

    def test_util_reexports_same_objects(self):
        for name in ("approval_path", "gate_result_path", "origin_path", "state_dir",
                     "manifest_path", "safe_component"):
            with self.subTest(name=name):
                self.assertIs(getattr(_util, name), getattr(_project, name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
