#!/usr/bin/env python3
"""test_source_profiler.py — профайлер входа без содержимого (задача 4.2).

Пинит:
  • тест-утечка: профиль фикстуры и КАЖДОГО варианта стресс-корпуса не содержит ни одного слова
    исходника вне общего словаря (слова — хешами), ни последовательности из 4 слов; пути — хешами;
  • соль на проект: тот же проект — те же хеши, другой проект — другие;
  • `reveal` раскрывает хеш слова и файла локально;
  • по профилю видны отличия вариантов: английские групповые заголовки (общие слова overview/schedulers),
    жирные строки вместо заголовков, setext, CRLF/BOM, незакрытый фенс, HTML-таблицы, метод в теле;
  • XML (синтетический BPMN) — пути элементов, имена атрибутов и ТИПЫ значений без значений (имена
    тегов и атрибутов — словарь формата, показываются намеренно); CSV — колонки и типы без значений.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import gen_variants as GV  # noqa: E402
from sgraph import profile as PF  # noqa: E402
from sgraph import tools as T  # noqa: E402

FIX = SCRIPTS.parent / "fixtures" / "tz-mini" / "sources"

BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs1">
  <process id="Process_17" name="Укомплектование ВСП по графику">
    <startEvent id="Start_1" name="Наступил срок укомплектования"/>
    <task id="Task_2" name="Сверить состав укомплектования"/>
    <sequenceFlow id="Flow_3" sourceRef="Start_1" targetRef="Task_2"/>
    <documentation>Сотрудник сверяет позиции с накладной</documentation>
  </process>
</definitions>
"""
CSV = "Этап пути;Точка контакта;Канал\nЗаявка;Экран оформления заявки;мобильное приложение\nВыдача;Окно выдачи;офис\n"


def _four(text):
    w = re.findall(r"\w+", text)
    return {" ".join(w[i:i + 4]) for i in range(len(w) - 3)}


class _Proj(unittest.TestCase):
    def make(self, src: Path) -> Path:
        root = Path(self.tmp.name) / f"p{len(list(Path(self.tmp.name).iterdir()))}"
        shutil.copytree(src, root / "docs" / "spec")
        T.call("graph_init", root, {"sources": ["docs/spec"], "exclude": ["**/*-draft.md"]})
        return root

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()


class NoLeak(_Proj):
    def test_fixture_and_every_variant(self):
        vdir = Path(self.tmp.name) / "_variants"
        for name, src in [("base", FIX)] + [(v, GV.materialize(vdir, v)) for v in sorted(GV.VARIANTS)]:
            with self.subTest(variant=name):
                root = self.make(src)
                prof = T.call("graph_profile", root, {})
                texts = [p.read_bytes().decode("utf-8") for p in (root / "docs" / "spec").rglob("*.md")]
                self.assertEqual(PF.leaked_words(prof, texts), [])
                dumped = json.dumps(prof, ensure_ascii=False)
                runs = set().union(*(_four(t) for t in texts))
                self.assertFalse([r for r in runs if r in " ".join(re.findall(r"\w+", dumped))])
                self.assertNotIn("task-service", dumped)
                self.assertTrue(all(p["f"].startswith("f#") for p in prof["per_file"]))


class Salt(_Proj):
    def test_hashes_stable_per_project_and_differ_between_projects(self):
        a, b = self.make(FIX), self.make(FIX)
        pa1 = T.call("graph_profile", a, {})
        pa2 = T.call("graph_profile", a, {})
        pb = T.call("graph_profile", b, {})
        self.assertEqual(pa1, pa2)
        self.assertNotEqual(pa1["per_file"][0]["f"], pb["per_file"][0]["f"])

    def test_reveal_is_local(self):
        root = self.make(FIX)
        prof = T.call("graph_profile", root, {})
        token = next(w for w, _ in prof["totals"]["heading_words"] if w.startswith("w#"))
        words = T.call("graph_profile", root, {"reveal": token})["matches"]
        self.assertTrue(words)
        m = PF.Masker(root)
        self.assertTrue(all(m.h("w", w) == token for w in words))
        f = prof["per_file"][0]["f"]
        self.assertEqual(len(T.call("graph_profile", root, {"reveal": f})["matches"]), 1)


class Shapes(_Proj):
    def prof(self, variant):
        src = FIX if variant == "base" else GV.materialize(Path(self.tmp.name) / "_v", variant)
        return T.call("graph_profile", self.make(src), {})

    def test_variants_are_distinguishable(self):
        base = self.prof("base")
        self.assertIn("N.N", base["totals"]["heading_shapes"])
        self.assertIn("N.", base["totals"]["heading_shapes"])
        eng = {w for p in self.prof("english-groups")["per_file"] for w, _ in p["heading_words"]}
        self.assertIn("overview", eng)
        self.assertIn("schedulers", eng)
        self.assertGreater(self.prof("bold-h3")["totals"]["heading_shapes"].get("bold_line", 0), 0)
        self.assertGreater(self.prof("setext")["totals"]["heading_shapes"].get("setext", 0), 0)
        crlf = self.prof("crlf-bom")["per_file"]
        self.assertTrue(all(p["crlf"] and p["bom"] for p in crlf))
        unclosed = self.prof("unclosed-fence")["per_file"]
        self.assertEqual(sum(p["warnings"].get("W-fence-unclosed", 0) for p in unclosed), 1)
        self.assertGreater(self.prof("html-tables")["totals"]["blocks"].get("html", 0), 0)
        ts = next(p for p in base["per_file"] if p["hits"]["topic_like"])
        self.assertGreaterEqual(ts["hits"]["http"], 4)
        self.assertEqual(ts["fences"], {"json": 3, "sql": 1})
        self.assertEqual(ts["links"]["anchor"], 1)


class XmlCsv(unittest.TestCase):
    def test_xml_and_csv_have_structure_without_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "aris.bpmn").write_text(BPMN, encoding="utf-8")
            (root / "cjm.csv").write_text(CSV, encoding="utf-8")
            m = PF.Masker(root)
            x = PF.profile_xml(root / "aris.bpmn", m)
            paths = dict(x["elements"])
            self.assertIn("definitions/process/task", paths)
            self.assertIn("task@name", dict(x["attributes"]))
            self.assertEqual(x["value_types"]["sequenceFlow@sourceRef"], {"id": 1})
            # имена тегов и атрибутов — словарь формата (показываются намеренно); значения — никогда
            values = re.findall(r'="([^"]*)"', BPMN) + re.findall(r">([^<]+)<", BPMN)
            fmt = {w.lower() for w in re.findall(r"</?([A-Za-z]+)|\s([A-Za-z]+)=", BPMN) for w in w if w}
            self.assertEqual(PF.leaked_words(x, values, PF.generic_words() | fmt), [])
            c = PF.profile_csv(root / "cjm.csv", m)
            self.assertEqual(c["rows"], 2)
            self.assertEqual(len(c["columns"]), 3)
            self.assertEqual(PF.leaked_words(c, [CSV]), [])
            self.assertTrue(all(col["name"].startswith("w#") or " " in col["name"] for col in c["columns"]))


if __name__ == "__main__":
    unittest.main()
