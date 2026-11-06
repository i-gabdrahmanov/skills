#!/usr/bin/env python3
"""test_validator.py — валидатор графа (задача 3.1).

Пинит:
  • vault фикстуры после шагов map и link — 0 ошибок E-*; предупреждения — только дыры полноты фикстуры (3.2);
  • по тесту на каждый код: здоровый vault, одна поломка построителем → ровно этот код в счётчиках;
  • scope режет нарушения по префиксу пути; CLI: 0 — нет ошибок, 1 — есть; отчёт _graph/report.md;
  • в validate.py нет имён видов и рёбер литералами (DoD п. 3): всё из онтологии.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
import graph_validate as GV  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

OP = "business/operations/Регистрация.md"
OP2 = "business/operations/Укомплектование.md"
CLOSER = "services/task-service/schedulers/Шедулер закрытия регулярных задач.md"
EP = "services/task-service/endpoints/task-service GET api-v1-tasks.md"
SCREEN = "ui/UI регистрации.md"

_healthy = {}
HOLES = {"W-op-no-creator": 2, "W-topic-side": 1}      # дыры самой фикстуры (спека их не описывает) — test_completeness


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

    def edit(self, rel, fn):
        p = self.vault / rel
        data, body = F.read(SP.read_note(p))
        data, body = fn(data, body)
        p.write_bytes(F.write(data, body).encode("utf-8"))

    def counts(self, scope=None):
        return T.call("graph_validate", self.root, {"scope": scope} if scope else {})["counts"]

    def assertOnly(self, code, *also):
        c = self.counts()
        self.assertIn(code, c, c)
        self.assertEqual(set(c) - {code, *also, *HOLES}, set(), f"лишние коды: {c}")


class Healthy(_Base):
    def test_fixture_is_clean(self):
        res = T.call("graph_validate", self.root, {})
        self.assertEqual((res["errors"], res["notes"], res["counts"]), (0, 29, HOLES), res["top"])
        issues = json.loads((self.root / ".spec-graph/validate.json").read_text(encoding="utf-8"))["issues"]
        self.assertTrue(all(i.get("hole") for i in issues))


class Errors(_Base):
    def test_fm_parse(self):
        (self.vault / OP).write_text("---\nkind: [\n---\n# Регистрация\n", encoding="utf-8")
        c = self.counts()
        self.assertEqual(c.get("E-fm-parse"), 1)          # и ссылки на неё — призраки: заметки больше нет
        self.assertIn("E-ghost", c)

    def test_fm_key_required_and_foreign_edge(self):
        self.edit(EP, lambda d, b: ({k: v for k, v in d.items() if k != "method"}, b))
        self.assertOnly("E-fm-key")
        self.setUp()
        self.edit(CLOSER, lambda d, b: (dict(d, consumes=["[[pprbkid.task.closed.v1]]"]), b))
        self.assertIn("E-fm-key", self.counts())

    def test_name(self):
        shutil.copy(self.vault / OP, self.vault / "ui" / "Регистрация.md")
        self.assertIn("E-name", self.counts())
        self.setUp()
        self.edit(EP, lambda d, b: (d, b))
        bad = self.vault / EP.replace(" GET ", " FETCH ")
        (self.vault / EP).rename(bad)
        self.assertIn("E-name", self.counts())

    def test_alias_unique(self):
        self.edit(OP, lambda d, b: (dict(d, aliases=["Укомплектование"]), b))
        self.assertOnly("E-name")
        self.setUp()
        self.edit(OP, lambda d, b: (dict(d, aliases=["задача ВСП"]), b))
        self.edit(OP2, lambda d, b: (dict(d, aliases=["задача ВСП"]), b))
        self.assertOnly("E-name")

    def test_path(self):
        dst = self.vault / CLOSER.replace("task-service/", "rmoc-service/", 1)
        dst.parent.mkdir(parents=True, exist_ok=True)
        (self.vault / CLOSER).rename(dst)
        self.assertOnly("E-path", "E-index")

    def test_key_dup(self):
        key = F.read(SP.read_note(self.vault / OP2))[0]["key"]
        self.edit(OP, lambda d, b: (dict(d, key=key), b))
        self.assertOnly("E-key-dup")

    def test_ghost(self):
        self.edit(OP, lambda d, b: (dict(d, related=["[[Нет такой операции]]"]), b))
        self.assertOnly("E-ghost")

    def test_edge_kind(self):
        self.edit(CLOSER, lambda d, b: (dict(d, closes_task=d["closes_task"] + ["[[UI регистрации]]"]), b))
        self.assertIn("E-edge-kind", self.counts())

    def test_inverse(self):
        self.edit(OP, lambda d, b: (dict(d, closed_by=[]), b))
        self.assertOnly("E-inverse", "W-op-no-closer")

    def test_symmetric_mirror(self):
        self.edit(OP2, lambda d, b: (dict(d, related=[]), b))
        self.assertOnly("E-inverse")

    def test_evidence(self):
        self.edit(CLOSER, lambda d, b: (d, F.set_block(b, "edges", "_рёбер нет_")))
        self.assertOnly("E-evidence")
        self.setUp()

        def fake(d, b):
            blk = F.get_block(b, "edges")["content"]
            rows = blk.split("\n")
            cells = rows[2].split(" | ")
            cells[2] = "выдуманная цитата, которой нет в тексте заметок"
            rows[2] = " | ".join(cells)
            return d, F.set_block(b, "edges", "\n".join(rows))
        self.edit(CLOSER, fake)
        self.assertOnly("E-evidence")

    def test_verbatim(self):
        def change(d, b):
            src = F.get_block(b, "source")
            return d, F.set_block(b, "source", src["content"].replace("а", "о", 1), src["attrs"])
        self.edit(OP, change)
        self.assertOnly("E-verbatim")

    def test_coverage(self):
        p = next(p for p in sorted((self.root / AS.PLAN_REL).glob("*.json"))
                 if json.loads(p.read_text(encoding="utf-8"))["assign"])
        plan = json.loads(p.read_text(encoding="utf-8"))
        plan["assign"][-1]["blocks"].pop()
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.assertIn("E-coverage", self.counts())

    def test_index(self):
        p = self.vault / "_graph" / "index.md"
        lines = p.read_text(encoding="utf-8").split("\n")
        p.write_text("\n".join(x for x in lines if "[[Регистрация]]" not in x), encoding="utf-8")
        self.assertOnly("E-index")

    def test_marker(self):
        p = self.vault / OP
        t = SP.read_note(p)
        i = t.index("<!-- graph:source:end -->")
        p.write_bytes((t[:i].rstrip("\n") + "\n" + t[i:]).encode("utf-8"))
        self.assertIn("E-marker", self.counts())


class Warnings(_Base):
    def test_orphan(self):
        self.edit(SCREEN, lambda d, b: ({k: ([] if isinstance(v, list) and k != "source" and k != "aliases" else v)
                                         for k, v in d.items()}, b))
        c = self.counts()
        self.assertIn("W-orphan", c)

    def test_scalar_link_is_an_edge(self):
        def scalar(d, b):
            d = {k: ([] if isinstance(v, list) and k not in ("source", "aliases") else v) for k, v in d.items()}
            return dict(d, ui_of="[[Регистрация]]"), b
        self.edit(SCREEN, scalar)
        self.assertNotIn("W-orphan", self.counts())

    def test_manual_inverse(self):
        self.edit(OP, lambda d, b: (dict(d, closed_by=d["closed_by"] + ["[[Шедулер формирования задачи укомплектования]]"]), b))
        self.assertOnly("W-manual-inverse")

    def test_unplaced(self):
        p = next(p for p in sorted((self.root / AS.PLAN_REL).glob("*.json"))
                 if json.loads(p.read_text(encoding="utf-8"))["assign"])
        plan = json.loads(p.read_text(encoding="utf-8"))
        b = plan["assign"][-1]["blocks"].pop()
        plan["unplaced"].append({"blocks": [b], "reason": "тест"})
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.assertIn("W-unplaced", self.counts())

    def test_source_missing(self):
        (self.root / "docs/spec/business/типы-задач.md").unlink()
        c = self.counts()
        self.assertIn("W-source-missing", c)
        self.assertNotIn("E-verbatim", c, "нет исходника — дословность не сверяется")


class Surface(_Base):
    def test_scope_and_cli(self):
        self.edit(OP, lambda d, b: (dict(d, related=["[[Нет такой операции]]"]), b))
        self.assertEqual(self.counts("ui/"), {})
        self.assertEqual(self.counts("business/"), {"E-ghost": 1, "W-op-no-creator": 2})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(GV.main(["--project", str(self.root)]), 1)
        self.assertIn("E-ghost", out.getvalue())
        self.assertIn("## E-ghost", (self.vault / "_graph" / "report.md").read_text(encoding="utf-8"))
        self.edit(OP, lambda d, b: (dict(d, related=[]), b))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(GV.main(["--project", str(self.root)]), 0)
        self.assertEqual(GV.main(["--project", str(Path(self.tmp.name) / "нет")]), 2)

    def test_no_kind_or_edge_literals(self):
        ont = O.load()
        names = sorted(set(ont.kinds) | set(ont.edges) | {e["inverse"] for e in ont.edges.values()}, key=len, reverse=True)
        rx = re.compile(r"""["'](%s)["']""" % "|".join(re.escape(n) for n in names))
        src = (SCRIPTS / "sgraph" / "validate.py").read_text(encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        # доступ к полю узла (n["service"], .get("data"), {"service": …}) — не ветвление по виду
        code = re.sub(r"""(\[\s*|\.get\(\s*|[{,]\s*)["'][\w-]+["'](\s*[\]:,)])""", "", code)
        self.assertEqual([m.group(0) for m in rx.finditer(code)], [])


if __name__ == "__main__":
    unittest.main()
