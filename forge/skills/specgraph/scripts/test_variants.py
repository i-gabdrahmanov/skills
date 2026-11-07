#!/usr/bin/env python3
"""test_variants.py — стресс-корпус: варианты стиля фикстуры ТЗ (задача 0.5).

Пинит:
  • генератор детерминирован: два прогона → побайтно одинаковые файлы;
  • КАЖДАЯ трансформация действительно что-то меняет хотя бы в одном файле (регулярка, тихо не
    нашедшая совпадений, — слепая зона, которую никто не заметит) и не трогает не-MD файлы;
  • матрица `variants.json` и реестр вариантов генератора совпадают; статусы — ok | known_gap:<причина> |
    pending:<задача>, и pending указывает только на НЕзакрытую задачу карты (README) — закрыл задачу,
    обнови её столбец;
  • проверка `quotes` (столбец 0.5): после трансформации цитаты-основания эталона и имена узлов на месте
    (с той же трансформацией текста для ё/е и неразрывных пробелов);
  • «раздутый» корпус 50 сервисов × 40 блоков шедулеров: строится быстро, файлы по 2+ тыс. строк — бюджет
    для 1.1/1.6.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "tools"))

import gen_variants as GV  # noqa: E402

FIX = SCRIPTS.parent / "fixtures" / "tz-mini"
MATRIX = json.loads((FIX / "variants.json").read_text(encoding="utf-8"))
GRAPH = json.loads((FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
README = SCRIPTS.parents[2] / "docs" / "specgraph" / "README.md"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("﻿", "")).strip()


def _task_status() -> dict:
    out = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| М\d \| [^|]+ \| \[(\S+) ", line)
        if m:
            out[m.group(1)] = line.rstrip(" |").rsplit("|", 1)[-1].strip()
    return out


class Generator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_deterministic(self):
        for v in GV.VARIANTS:
            a = GV.materialize(self.out / "a", v)
            b = GV.materialize(self.out / "b", v)
            for f in sorted(p for p in a.rglob("*") if p.is_file()):
                with self.subTest(variant=v, file=str(f.relative_to(a))):
                    self.assertEqual(f.read_bytes(), (b / f.relative_to(a)).read_bytes())

    def test_every_transform_changes_something(self):
        texts = {p: p.read_text(encoding="utf-8") for p in GV.BASE.rglob("*.md")}
        for name, fn in GV.TRANSFORMS.items():
            with self.subTest(transform=name):
                changed = [p for p, t in texts.items() if fn(t) != t]
                self.assertTrue(changed, f"{name} ничего не поменял ни в одном файле")
        v = GV.materialize(self.out, "crlf-bom")
        png = "task-service/resources/task-flow.png"
        self.assertEqual((v / png).read_bytes(), (GV.BASE / png).read_bytes(), "не-MD файлы копируются как есть")

    def test_specific_forms(self):
        ts = (GV.BASE / "task-service" / "task-service.md").read_text(encoding="utf-8")
        self.assertIn("**2.1 Шедулер формирования задачи укомплектования**", GV.bold_h3(ts))
        self.assertIn("### 4.1 Список задач сотрудника\n\nМетод: `GET /api/v1/tasks`", GV.api_in_body(ts))
        self.assertIn("paths:\n  /api/v1/tasks:\n    get:", GV.api_openapi(ts))
        self.assertIn("## 2 Schedulers", GV.english_groups(ts))
        self.assertIn("## Раздел 2. Шедулеры", GV.num_razdel(ts))
        self.assertIn("<th>код типа</th>", GV.html_tables(ts))
        broken = GV.unclosed_fence(ts)
        self.assertEqual(broken.count("```"), ts.count("```") - 1, "ровно один фенс потерял закрытие")
        self.assertIn("Находим задачу по requestId", GV.prose_in_fence(ts))
        self.assertTrue(GV.crlf(ts).count("\r\n") == ts.count("\n"))


class Matrix(unittest.TestCase):
    def test_matrix_matches_generator(self):
        self.assertEqual(set(MATRIX["variants"]), set(GV.VARIANTS))
        used = {t for ts in GV.VARIANTS.values() for t in ts}
        self.assertEqual(used, set(GV.TRANSFORMS), "каждая трансформация входит хотя бы в один вариант")

    def test_statuses_and_pending_tasks(self):
        status = _task_status()
        self.assertTrue(status, "карта задач README прочиталась")
        for v, row in MATRIX["variants"].items():
            self.assertEqual(set(row), set(MATRIX["checks"]), v)
            for check, st in row.items():
                with self.subTest(variant=v, check=check):
                    self.assertRegex(st, r"^(ok|known_gap:.+|pending:[0-9X]\.\d+)$")
                    if st.startswith("pending:"):
                        task = st.split(":", 1)[1]
                        self.assertNotEqual(status.get(task), "done",
                                            f"задача {task} закрыта — обнови столбец «{check}» (ok или known_gap)")


class Quotes(unittest.TestCase):
    def test_quotes_and_names_survive(self):
        with tempfile.TemporaryDirectory() as td:
            for v, row in MATRIX["variants"].items():
                if row["quotes"] != "ok":
                    continue
                d = GV.materialize(Path(td), v)
                fn = GV.text_fn(v)
                cache = {}
                for e in GRAPH["edges"]:
                    if "quote" not in e:
                        continue
                    with self.subTest(variant=v, quote=e["quote"][:40]):
                        text = cache.setdefault(e["file"], _norm((d / e["file"]).read_bytes().decode("utf-8")))
                        self.assertIn(_norm(fn(e["quote"])), text)
                for n in GRAPH["nodes"]:
                    if n["kind"] in ("operation", "scheduler", "algorithm"):
                        with self.subTest(variant=v, name=n["name"]):
                            text = _norm((d / n["source"].split("#")[0]).read_bytes().decode("utf-8"))
                            self.assertIn(_norm(fn(n["name"])).lower(), text.lower())


class Bloated(unittest.TestCase):
    def test_budget_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            t0 = time.monotonic()
            d = GV.bloated(Path(td))
            self.assertLess(time.monotonic() - t0, 30)
            files = sorted(d.rglob("*.md"))
            self.assertEqual(len(files), 50)
            lines = files[0].read_text(encoding="utf-8").count("\n")
            self.assertGreater(lines, 2000)


if __name__ == "__main__":
    unittest.main()
