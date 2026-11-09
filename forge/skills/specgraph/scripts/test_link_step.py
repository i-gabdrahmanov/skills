#!/usr/bin/env python3
"""test_link_step.py — отчёт связей, решения человека и гейт шага graph-link (задача 2.6).

Пинит на фикстуре с «ответами модели»:
  • шаг проходит до exit 0: связи → карточки → синк → отчёт → согласие → повторный отчёт принимает висящее;
  • отчёт «Укомплектования»: оба шедулера, UI, связанная операция, карточка; межсервисные вызовы и топики;
  • гейт блокирует: без согласия; с устаревшим отчётом (решение после «да»); с висящим proposed; с узлом дельты
    без решения; с грязным синком; с незакрытой очередью;
  • отклонённая связь — в _graph/decisions.md, уходит из FM и не возвращается механикой;
  • решения по дельте: drop — блоки в unplaced и заметка снята; merge — блоки и предложения уходят к цели;
    rename — заметка под новым именем.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
import graph_gate as G  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import review as RV  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

NEW = "Справочник статусов упаковок"
NEW_NOTE = f"data/enum/{NEW}.md"
SCHED = "Шедулер формирования задачи укомплектования"
DATA = "Динамические атрибуты укомплектования"


def inject_delta(root: Path) -> None:
    """Раскладка завела новый узел: блок rmoc-service уходит в «Справочник статусов упаковок» (как 1.5)."""
    for p in sorted((root / AS.PLAN_REL).glob("*.json")):
        plan = json.loads(p.read_text(encoding="utf-8"))
        svc = next((a for a in plan["assign"] if a["node"] == "rmoc-service"), None)
        if svc and len(svc["blocks"]) > 1:
            plan["assign"].append({"node": NEW, "blocks": [svc["blocks"].pop()]})
            p.write_text(json.dumps(plan, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            break
    rec = {"kind": "data", "subkind": "enum", "name": NEW, "why": "раздел описывает статусы, в каталоге узла нет"}
    rec.update(id=CT.node_id(CT.identity(rec)), sources=[], proposed_by="test")
    (root / AS.DELTA_REL).write_text(json.dumps([rec], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    T.call("graph_split", root, {})


class _Base(unittest.TestCase):
    before_link = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")
        self.first = K.through_review(self.root, type(self).before_link)
        self.vault = self.root / "docs" / "graph"

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.main(["link", "--project", str(self.root)])
        return code, out.getvalue()

    def report(self):
        return (self.root / self.first["report"]).read_text(encoding="utf-8")


class Step(_Base):
    def test_fixture_passes(self):
        self.assertEqual(self.first["consent"], "none")
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("согласие graph-edges-adhoc: none", out)
        res = K.approve_edges(self.root)
        self.assertEqual((res["consent"], res["accepted"]), ("ok", 33))   # + uses_data → task из SQL
        self.assertEqual(RV.pending(self.root), 0)
        self.assertEqual(self.gate(), (0, "✓ graph-link: связи решены, синк чистый, отчёт связей утверждён\n"))

    def test_report_content(self):
        text = self.report()
        op = text[text.index("**Укомплектование**"):]
        op = op[:op.index("\n\n")]
        for want in ("формирует — [[Шедулер формирования задачи укомплектования]]",
                     "закрывает — [[Шедулер закрытия регулярных задач]]", "UI — [[UI укомплектования]]",
                     "связана с — [[Передача упаковок в СЦ-архив]]", "карточка — готова"):
            self.assertIn(want, op)
        self.assertIn("- UI: [[UI укомплектования]] → rmoc-service: [[rmoc-service GET rmoc-komplekt-items]]", text)
        self.assertIn("- upz-adapter: [[upz-adapter POST api-v1-upz-tasks]] → Внешняя система: [[УПЗ]]", text)
        self.assertIn("читают — [[Обработка ответа УПЗ о создании задачи]] (task-service)", text)
        self.assertEqual(RV.report(self.root, T._need_cfg(self.root))[0], text, "отчёт детерминирован")

    def test_doubtful_listed_with_id(self):
        props = E.load(self.root, "Передача упаковок в СЦ-архив")
        rel = next(x for x in props if x["edge"] == "related")
        rel["confidence"] = "low"
        E.save(self.root, "Передача упаковок в СЦ-архив", props)
        T.call("graph_edges_review", self.root, {})
        self.assertIn(f"- `{RV.edge_id(rel)}` [[Передача упаковок в СЦ-архив]] — связана с операцией → [[Укомплектование]] "
                      f"(low): «", self.report())


class Blocks(_Base):
    def test_stale_report_after_decision(self):
        K.approve_edges(self.root)
        res = T.call("graph_edges_decide", self.root, {"reject": [{"from": "Шедулер закрытия регулярных задач",
                                                                    "edge": "closes_task", "to": "Регистрация"}],
                                                         "why": "регистрацию закрывает пользователь"})
        self.assertEqual(res["rejected"], 1)
        self.assertEqual(self.gate()[0], 1)          # синк не чистый
        T.call("graph_sync", self.root, {})
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("отчёт связей устарел", out)
        self.assertEqual(T.call("graph_edges_review", self.root, {})["consent"], "stale")
        self.assertIn("согласие graph-edges-adhoc: stale", self.gate()[1])

    def test_pending_proposed_after_consent(self):
        K.approve_edges(self.root)
        props = E.load(self.root, "Шедулер закрытия регулярных задач")
        props[0]["status"] = "proposed"
        E.save(self.root, "Шедулер закрытия регулярных задач", props)
        code, out = self.gate()
        self.assertEqual(code, 1)
        self.assertIn("висящих предложений 1", out)

    def test_open_queue(self):
        K.approve_edges(self.root)
        T.call("graph_card_start", self.root, {})
        data = json.loads((self.root / Q.QUEUE_REL / "card.json").read_text(encoding="utf-8"))
        data["items"][0]["status"] = "pending"
        (self.root / Q.QUEUE_REL / "card.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertIn("очередь card не закрыта", self.gate()[1])

    def test_dirty_sync(self):
        K.approve_edges(self.root)
        p = self.vault / "business/operations/Регистрация.md"
        data, body = F.read(SP.read_note(p))
        data["closed_by"] = []
        p.write_bytes(F.write(data, body).encode("utf-8"))
        self.assertIn("синк не чистый", self.gate()[1])
        self.assertEqual(F.read(SP.read_note(p))[0]["closed_by"], [], "гейт ничего не пишет")


class Rejected(_Base):
    def test_rejected_goes_to_decisions_and_stays(self):
        T.call("graph_edges_decide", self.root, {"reject": [{"from": "Шедулер закрытия регулярных задач",
                                                              "edge": "closes_task", "to": "Регистрация"}],
                                                   "why": "регистрацию закрывает пользователь"})
        T.call("graph_sync", self.root, {})
        md = (self.vault / "_graph" / "decisions.md").read_text(encoding="utf-8")
        self.assertIn("«Шедулер закрытия регулярных задач» —closes_task→ «Регистрация» → регистрацию закрывает", md)
        self.assertIn("## Каталог", md)
        T.call("graph_edges_mechanical", self.root, {})
        T.call("graph_sync", self.root, {})
        data, _ = F.read(SP.read_note(self.vault / "business/operations/Регистрация.md"))
        self.assertEqual(data["closed_by"], [])
        self.assertIn("регистрацию закрывает пользователь", T.call("graph_edges_review", self.root, {}) and self.report())

    def test_decide_needs_known_edge_and_why(self):
        for args, code in (({"reject": ["r-00000000"], "why": "x"}, "E-decide-unknown"),
                           ({"reject": [RV.edge_id(E.load(self.root, "Шедулер закрытия регулярных задач")[0])]}, "E-decide-why")):
            with self.assertRaises(T.ToolError) as cm:
                T.call("graph_edges_decide", self.root, args)
            self.assertEqual(cm.exception.codes, [code])


class Delta(_Base):
    before_link = staticmethod(inject_delta)

    def test_undecided_blocks_gate(self):
        self.assertTrue((self.vault / NEW_NOTE).is_file())
        self.assertIn(f"«{NEW}» (Данные", self.report())
        self.assertIn("**нет решения**", self.report())
        K.approve_edges(self.root)
        self.assertIn("узлы дельты без решения: 1", self.gate()[1])
        T.call("graph_delta_decide", self.root, {"decisions": [{"node": NEW, "action": "accept", "why": "нужен"}]})
        T.call("graph_sync", self.root, {})
        T.call("graph_edges_review", self.root, {})
        K.approve_edges(self.root)
        self.assertEqual(self.gate()[0], 0)

    def test_drop_moves_blocks_to_unplaced(self):
        res = T.call("graph_delta_decide", self.root, {"decisions": [{"node": NEW, "action": "drop", "why": "лишний"}]})
        self.assertEqual(res["undecided"], [])
        self.assertFalse((self.vault / NEW_NOTE).exists())
        self.assertIn(f"узел дельты «{NEW}» убран человеком", json.dumps(SP._assignment(self.root)[2], ensure_ascii=False))
        self.assertNotIn("_нет_", (self.vault / "_graph" / "unplaced.md").read_text(encoding="utf-8"))
        self.assertEqual(SP.verify(self.root, T._need_cfg(self.root)), [])

    def propose_uses(self, src):
        quote = (self.vault / "data/enum" / f"{NEW}.md").read_text(encoding="utf-8")
        body = quote[quote.index("-->", quote.index("graph:source")) + 3:].strip()
        E.add(self.root, src, [{"from": src, "edge": "uses_data", "to": NEW, "quote": body[:40], "origin": "model",
                                "confidence": "high", "status": "proposed"}])

    def test_merge_moves_blocks_to_target(self):
        self.propose_uses(SCHED)
        T.call("graph_delta_decide", self.root, {"decisions": [{"node": NEW, "action": "merge", "into": "rmoc-service",
                                                                "why": "это часть описания сервиса"}]})
        self.assertFalse((self.vault / NEW_NOTE).exists())
        self.assertNotIn(NEW, set(SP._assignment(self.root)[0].values()))
        self.assertEqual(SP.verify(self.root, T._need_cfg(self.root)), [])
        md = (self.vault / "_graph" / "decisions.md").read_text(encoding="utf-8")
        self.assertIn(f"- **merge** «{NEW}» → «rmoc-service» → это часть описания сервиса", md)
        for ps in RV._stores(self.root).values():
            self.assertFalse([x for x in ps if NEW in (x["from"], x["to"]) and x["status"] != "rejected"])
        moved = [x for x in E.load(self.root, SCHED) if x["edge"] == "uses_data" and x["to"] == "rmoc-service"]
        self.assertEqual([x["status"] for x in moved], ["rejected"], "uses_data на сервис недопустимо")
        self.assertIn("после решения по дельте", moved[0]["rejected_why"])

    def test_merge_into_same_kind_keeps_edge(self):
        self.propose_uses(SCHED)
        T.call("graph_delta_decide", self.root, {"decisions": [{"node": NEW, "action": "merge", "into": DATA,
                                                                "why": "те же атрибуты"}]})
        moved = [x for x in E.load(self.root, SCHED) if x["edge"] == "uses_data" and x["to"] == DATA]
        self.assertEqual(len(moved), 1, "с уже существующим ребром — одна запись, цитаты копятся")
        self.assertNotEqual(moved[0]["status"], "rejected")
        self.assertEqual(len(moved[0].get("quotes", [])), 2)

    def test_rename(self):
        T.call("graph_delta_decide", self.root, {"decisions": [{"node": NEW, "action": "rename", "name": "Статусы упаковок",
                                                                "why": "короче"}]})
        self.assertFalse((self.vault / NEW_NOTE).exists())
        self.assertTrue((self.vault / "data/enum/Статусы упаковок.md").is_file())

    def test_bad_decisions(self):
        for d in ({"node": "нет такого", "action": "accept", "why": "x"}, {"node": NEW, "action": "keep", "why": "x"},
                  {"node": NEW, "action": "accept"}, {"node": NEW, "action": "merge", "into": NEW, "why": "x"},
                  {"node": NEW, "action": "rename", "name": "rmoc-service", "why": "x"}):
            with self.assertRaises(T.ToolError) as cm:
                T.call("graph_delta_decide", self.root, {"decisions": [d]})
            self.assertEqual(cm.exception.codes, ["E-delta-decision"], d)


if __name__ == "__main__":
    unittest.main()
