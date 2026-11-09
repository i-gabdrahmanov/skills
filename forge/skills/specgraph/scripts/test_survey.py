#!/usr/bin/env python3
"""test_survey.py — разведка: обзор спеки и черновой каталог по оглавлению (задача 1.3).

Пинит:
  • `graph_survey` на фикстуре: `_graph/survey.md` (объём работ, scope, аномалии, исключённый черновик,
    оглавления), очередь draft-file — единица на файл, id единиц ASCII при кириллических путях;
  • во входе единицы — оглавление и кандидаты, но НЕ текст разделов (ни одной последовательности из 5
    слов из не-заголовочных блоков) и имена кандидатов всего корпуса (для общего шедулера закрытия);
  • вход + бриф укладываются в бюджет; на «раздутом» корпусе крупный файл режется на части;
  • «ответы модели» фикстуры через настоящую очередь принимаются, черновики дают все узлы эталона;
  • отказы `graph_draft_put` с кодами: неизвестный вид, запрещённые символы, endpoint без key, вид
    сервиса без service, чужой раздел, неучтённый кандидат, дубль имени, обратный слэш — и попытка
    списывается очередью;
  • оценка `draft-file` в объёме работ = фактическое число единиц.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import gen_variants as GV  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import extract as X  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import survey as SV  # noqa: E402
from sgraph import tools as T  # noqa: E402

G = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))


class _Proj(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def items(self):
        return json.loads((self.root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))["items"]


class Survey(_Proj):
    def test_report_and_queue(self):
        res = T.call("graph_survey", self.root, {})
        self.assertEqual(res["files"], 8)
        self.assertEqual(res["work"]["draft-file"], 8)
        self.assertEqual(res["queue"]["total"], res["work"]["draft-file"])
        self.assertEqual(res["excluded"], ["docs/spec/task-service/task-service-draft.md"])
        self.assertEqual(res["scope_hint"][0], "task-service")
        md = (self.root / "docs" / "graph" / "_graph" / "survey.md").read_text(encoding="utf-8")
        for needle in ("## Объём работ", "вызовов субагентов", "## Предлагаемый scope", "## Исключены конфигом",
                       "task-service-draft.md", "Шедулер закрытия регулярных задач"):
            self.assertIn(needle, md)
        for it in self.items():
            self.assertRegex(it["id"], r"^draft-[0-9a-f]{10}(-p\d+)?$")
            self.assertEqual(it["brief"], "draft-file.md")

    def test_input_has_outline_not_prose(self):
        T.call("graph_survey", self.root, {})
        for it in self.items():
            rel = it["refs"]["file"]
            text = (self.root / Q._input_path(self.root, it["id"]).relative_to(self.root)).read_text(encoding="utf-8")
            prose = " ".join(b["text"] for b in B.load(self.root, rel)["blocks"] if b["type"] != "heading")
            words = re.findall(r"\w+", prose)
            fives = {" ".join(words[i:i + 5]) for i in range(len(words) - 4)}
            names = [n["name"] for n in G["nodes"]] + [n.get("key", "") for n in G["nodes"]]
            for n in sorted(names, key=len, reverse=True):     # имена и ключи кандидатов — законно во входе
                if n:
                    text = text.replace(n, " ")
            flat = " ".join(re.findall(r"\w+", text))
            with self.subTest(file=rel):
                self.assertFalse([f for f in fives if f in flat], "текст разделов во входе")
                if "task-service" not in rel:
                    raw = Q._input_path(self.root, it["id"]).read_text(encoding="utf-8")
                    self.assertIn("## Имена кандидатов всего корпуса", raw)
                self.assertLessEqual(len(text) + SV.brief_chars(), Q.DEFAULTS["budget_chars"])

    def test_bloated_corpus_is_split_into_parts(self):
        big = Path(self.tmp.name) / "big"
        d = GV.bloated(big, services=2, sections=60)
        root = Path(self.tmp.name) / "bigrepo"
        import shutil
        shutil.copytree(d, root / "docs" / "spec")
        T.call("graph_init", root, {"sources": ["docs/spec"]})
        T.call("graph_survey", root, {})
        items = json.loads((root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))["items"]
        self.assertGreater(len(items), 2, "крупные файлы — частями")
        for it in items:
            text = Q._input_path(root, it["id"]).read_text(encoding="utf-8")
            self.assertLessEqual(len(text) + SV.brief_chars(), Q.DEFAULTS["budget_chars"])


class Drafts(_Proj):
    def test_fixture_answers_give_all_expected_nodes(self):
        T.call("graph_survey", self.root, {})
        done = K.run_queue(self.root, "draft-file", K.draft_answer(self.root))
        self.assertEqual(len(done), 8)
        self.assertTrue(all(r["accepted"] for r in done))
        got = {(n["kind"], n["name"]) for d in SV.load_drafts(self.root) for n in d["nodes"]}
        # узлы правил (auto) идут в каталог сами — черновик их повторять не обязан
        got |= {(c["kind"], c["name_hint"]) for p in (self.root / X.CANDIDATES_REL).rglob("*.json")
                for c in json.loads(p.read_text(encoding="utf-8"))["candidates"] if c.get("auto")}
        self.assertEqual(got, {(n["kind"], n["name"]) for n in G["nodes"]})
        self.assertEqual(T.call("graph_next", self.root, {"queue": "draft-file"})["status"], Q.EMPTY)

    def test_rejections(self):
        T.call("graph_survey", self.root, {})
        unit = T.call("graph_next", self.root, {"queue": "draft-file"})
        while "task-service.md" not in [it for it in self.items() if it["id"] == unit["item_id"]][0]["refs"]["file"]:
            T.call(unit["tool"], self.root, dict(K.draft_answer(self.root)(unit), item_id=unit["item_id"]))
            unit = T.call("graph_next", self.root, {"queue": "draft-file"})
        good = K.draft_answer(self.root)(unit)
        cases = {
            "E-draft-kind": lambda a: a["nodes"].__setitem__(0, dict(a["nodes"][0], kind="job")),
            "E-draft-name": lambda a: a["nodes"].__setitem__(0, dict(a["nodes"][0], name="Шедулер [1]")),
            "E-draft-key": lambda a: [n.pop("key") for n in a["nodes"] if n["kind"] == "endpoint"][:1],
            "E-draft-owner": lambda a: a["nodes"][0].pop("service"),
            "E-draft-section": lambda a: a["nodes"][0].__setitem__("sections", ["docs/spec/ui/регистрация.md#1"]),
            "E-draft-candidate": lambda a: a["nodes"].remove(next(n for n in a["nodes"] if n["kind"] == "scheduler")),
            "E-draft-dup": lambda a: a["nodes"].append(dict(a["nodes"][0])),
            "E-draft-text": lambda a: a["nodes"][0].__setitem__("why", "путь C:\\temp"),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                bad = json.loads(json.dumps(good))
                mutate(bad)
                q = json.loads((self.root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))
                for it in q["items"]:                     # снова в аренду: три отказа бросили бы единицу
                    if it["id"] == unit["item_id"]:
                        it.update(status="leased", attempts=0, lease_until=Q._clock() + 600)
                (self.root / Q.QUEUE_REL / "draft-file.json").write_text(json.dumps(q), encoding="utf-8")
                with self.assertRaises(T.ToolError) as cm:
                    T.call("graph_draft_put", self.root, dict(bad, item_id=unit["item_id"]))
                self.assertIn(code, cm.exception.codes, str(cm.exception))
        st = Q.status(self.root, "draft-file")
        self.assertEqual(st["leased"] + st["pending"] + st["done"], 8)


class Similar(unittest.TestCase):
    """Ближайшие имена корпуса: индекс по наборам слов даёт ровно то же, что наивный перебор, но без квадрата."""

    @staticmethod
    def naive(names, probes, limit=SV._KNOWN_MAX):
        pt = [SV._tokens(p) for p in probes]
        scored = []
        for n in names:
            nt = SV._tokens(n)
            best = max((len(nt & t) / max(1, len(nt | t)) for t in pt), default=0.0)
            if best > 0:
                scored.append((-best, n))
        return [n for _, n in sorted(scored)[:limit]]

    def test_matches_naive(self):
        names = sorted({g["name"] for g in json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))["nodes"]})
        names += [f"{n} {i}" for n in names[:10] for i in range(3)] + ["Шедулер", "шедулер закрытия"]
        index = SV._NameIndex(names)
        for probes in (["Шедулер закрытия регулярных задач"], ["UI регистрации", "задачи ВСП"], ["нет совпадений xyz"],
                       ["Передача упаковок", "Укомплектование", "передача"]):
            for subset in (names, names[::2]):
                with self.subTest(probes=probes, n=len(subset)):
                    self.assertEqual(SV._similar(subset, probes, index=index), self.naive(subset, probes))
                    self.assertEqual(SV._similar(subset, probes, limit=3), self.naive(subset, probes, limit=3))

    def test_matches_naive_on_random_corpus(self):
        import random
        rnd = random.Random(7)
        vocab = ["шедулер", "закрытия", "задачи", "передачи", "упаковок", "регистрации", "приёма", "сервис", "архив",
                 "алгоритм", "балансировки", "обработка", "ответа", "события"]
        names = sorted({" ".join(rnd.sample(vocab, rnd.randint(1, 5))) for _ in range(300)})
        index = SV._NameIndex(names)
        for _ in range(40):
            probes = [" ".join(rnd.sample(vocab, rnd.randint(1, 4))) for _ in range(rnd.randint(1, 4))]
            subset = [n for n in names if rnd.random() < 0.7]
            for limit in (1, 5, 60):
                self.assertEqual(SV._similar(subset, probes, limit=limit, index=index), self.naive(subset, probes, limit))

    def test_bloated_survey_is_fast(self):
        import shutil
        import time
        with tempfile.TemporaryDirectory() as td:
            d = GV.bloated(Path(td) / "big")
            root = Path(td) / "repo"
            shutil.copytree(d, root / "docs" / "spec")
            T.call("graph_init", root, {"sources": ["docs/spec"]})
            t0 = time.perf_counter()
            T.call("graph_survey", root, {})
            took = time.perf_counter() - t0
            self.assertLess(took, 15.0, f"разведка «раздутого» корпуса — {took:.1f} с (было 40+ с до индекса имён)")


if __name__ == "__main__":
    unittest.main()
