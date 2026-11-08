#!/usr/bin/env python3
"""test_edge_sync.py — синк рёбер в заметки vault (задача 2.4).

Пинит на фикстуре (механика + ответы модели):
  • прямые ключи у владельца, таблица оснований graph:edges; у общего шедулера закрытия три closes_task, а у
    каждой из трёх операций выведен closed_by на него (ТЗ §5); related — с обеих сторон; belongs_to →
    operations у сервиса;
  • повтор синка — 0 изменённых файлов (симметричное зеркало — не ручная ссылка);
  • снятое предложение снимает и обратный ключ;
  • правки человека в Obsidian: добавленная прямая ссылка — manual (с «внесено человеком»), убранная — предложение
    rejected и не возвращается; правка обратного ключа перезаписывается и попадает в manual_inverse;
  • frontmatter всех заметок читается кодеком, ссылки только на существующие узлы.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

OP = "business/operations/Укомплектование.md"
CLOSER = "services/task-service/schedulers/Шедулер закрытия регулярных задач.md"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")
        K.through_link(self.root)
        self.first = T.call("graph_sync", self.root, {})
        self.vault = self.root / "docs" / "graph"

    def tearDown(self):
        self.tmp.cleanup()

    def fm(self, rel):
        return F.read(SP.read_note(self.vault / rel))

    def write_fm(self, rel, data, body):
        (self.vault / rel).write_bytes(F.write(data, body).encode("utf-8"))


class Sync(_Base):
    def test_edges_written_and_inverses_derived(self):
        self.assertEqual(self.first["forward_edges"], 33)      # + uses_data → task из SQL
        data, body = self.fm(CLOSER)
        self.assertEqual(data["closes_task"], ["[[Приём транспортных упаковок]]", "[[Регистрация]]", "[[Укомплектование]]"])
        self.assertIn("`REGISTRATION` (Регистрация)", F.get_block(body, "edges")["content"])
        for op in ("Укомплектование", "Регистрация", "Приём транспортных упаковок"):
            d, _ = self.fm(f"business/operations/{op}.md")
            self.assertIn("[[Шедулер закрытия регулярных задач]]", d["closed_by"])
        op, _ = self.fm(OP)
        self.assertEqual(op["related"], ["[[Передача упаковок в СЦ-архив]]"])
        tr, _ = self.fm("business/operations/Передача упаковок в СЦ-архив.md")
        self.assertEqual(tr["related"], ["[[Укомплектование]]"])
        svc, _ = self.fm("services/rmoc-service/rmoc-service.md")
        self.assertEqual(len(svc["operations"]), 3)
        names = {p.stem for p in self.vault.rglob("*.md") if "_graph" not in p.parts}
        for p in self.vault.rglob("*.md"):
            if "_graph" in p.parts:
                continue
            d, _ = F.read(SP.read_note(p))
            for v in d.values():
                for x in (v if isinstance(v, list) else []):
                    t = F.link_target(x)
                    if t:
                        self.assertIn(t, names, f"{p.name}: ссылка на несуществующий узел {t}")

    def test_idempotent(self):
        self.assertEqual(T.call("graph_sync", self.root, {})["written"], 0)

    def test_withdrawn_proposal_drops_inverse(self):
        props = E.load(self.root, "Шедулер закрытия регулярных задач")
        for p in props:
            if p["to"] == "Регистрация":
                p["status"] = "rejected"
        E.save(self.root, "Шедулер закрытия регулярных задач", props)
        T.call("graph_sync", self.root, {})
        reg, _ = self.fm("business/operations/Регистрация.md")
        self.assertEqual(reg["closed_by"], [])


class HumanEdits(_Base):
    def test_added_link_is_manual_removed_is_rejected(self):
        data, body = self.fm(CLOSER)
        data["closes_task"] = ["[[Приём транспортных упаковок]]", "[[Укомплектование]]", "[[Передача упаковок в СЦ-архив]]"]
        self.write_fm(CLOSER, data, body)
        res = T.call("graph_sync", self.root, {})
        self.assertEqual((res["manual"], res["removed_by_human"]), (1, 1))
        data, body = self.fm(CLOSER)
        self.assertIn("[[Передача упаковок в СЦ-архив]]", data["closes_task"])
        self.assertNotIn("[[Регистрация]]", data["closes_task"])
        self.assertIn("внесено человеком", F.get_block(body, "edges")["content"])
        reg, _ = self.fm("business/operations/Регистрация.md")
        self.assertNotIn("[[Шедулер закрытия регулярных задач]]", reg["closed_by"])
        st = [p["status"] for p in E.load(self.root, "Шедулер закрытия регулярных задач") if p["to"] == "Регистрация"]
        self.assertEqual(st, ["rejected"])
        T.call("graph_sync", self.root, {})
        self.assertNotIn("[[Регистрация]]", self.fm(CLOSER)[0]["closes_task"], "убранное не возвращается")

    def test_inverse_edit_is_overwritten(self):
        data, body = self.fm(OP)
        data["closed_by"] = ["[[Шедулер формирования задачи укомплектования]]"]
        self.write_fm(OP, data, body)
        res = T.call("graph_sync", self.root, {})
        self.assertIn("Укомплектование.closed_by", res["manual_inverse"])
        self.assertEqual(self.fm(OP)[0]["closed_by"], ["[[Шедулер закрытия регулярных задач]]"])


if __name__ == "__main__":
    unittest.main()
