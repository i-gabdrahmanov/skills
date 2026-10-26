"""test_match.py — матчинг «макет ↔ страница» (задача 4.3, спека §14.4.2, INV-C26, QG-U15).

Пинит по синтетическим деревьям (боевых пар «макет ↔ страница» пока нет — выбор весов и порогов проверен только
ими) каждое правило: якоря (текст, компонент, мост плана, перекрытие текстов, краска), роль-вето, сверху вниз
(карточки-близнецы), один-к-одному, консистентность вложенности, обёртки страницы, «прозрачный» несматченный фрейм,
хром вне региона, ORDER с правильной виной, hard gate и LOW_MATCH_RATE, `read_plan` по трём носителям и имени «по
форме», детерминизм и неизменность входа. Деревья строят маленькие построители `D` / `T` / `P` ниже.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver.ingest import pixso as PX  # noqa: E402
from daiver.verify import match as M  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "pixso-modal" / "dsl.json"


def box(x=0, y=0, w=100, h=20):
    return {"x": x, "y": y, "w": w, "h": h}


def D(kind, id, children=None, **kw):
    n = {"id": id, "kind": kind}
    n.update(kw)
    if children is not None:
        n["children"] = children
    return n


def T(id, text, x=0, y=0, **kw):
    return D("text", id, text=text, box=box(x, y, 60, 14), **kw)


def P(id, text, x=0, y=0, **kw):
    """Текст страницы: тот же канон, kind text."""
    return D("text", id, text=text, box=box(x, y, 60, 14), tag="span", **kw)


def inst(id, name, children=None, **kw):
    return D("instance", id, children, component={"figmaName": name, **kw.pop("comp", {})}, box=kw.pop("box", box(0, 0, 80, 30)), **kw)


def comp(id, names, children=None, **kw):
    """Блок страницы с компонентом реализации."""
    return D("frame", id, children, component={"figmaName": names[0], "implName": names[0], "implNames": list(names)},
             box=kw.pop("box", box(0, 0, 80, 30)), tag="div", **kw)


def pairs_of(res):
    return {p["design"]: p["impl"] for p in res["pairs"]}


def screen(children, extra=None):
    return D("frame", "d:root", children, box=box(0, 0, 400, 300), **(extra or {}))


def page(children):
    """Обёртки страницы: body > div#root > div — корень макета спаривается на уровень глубже."""
    inner = D("frame", "p:wrap2", children, box=box(0, 0, 400, 300), tag="div")
    mid = D("frame", "p:root", [inner], box=box(0, 0, 400, 300), tag="div")
    return D("frame", "p:body", [mid], box=box(0, 0, 400, 300), tag="body")


def as_page(node, prefix="p:"):
    """Настоящий узел канона → его «реализация»: инстанс → frame с компонентом реализации и текстовыми детьми по слотам."""
    n = copy.deepcopy(node)
    stack = [n]
    row = [0]
    while stack:
        x = stack.pop()
        x["id"] = prefix + x["id"]
        if "box" not in x:                      # у настоящего канона боксы только у абсолютных: страница меряет каждый
            x["box"] = {"x": 0, "y": row[0] * 100, "w": 600, "h": 90}
            row[0] += 1
        if x["kind"] == "instance":
            c = x.pop("component")
            x["kind"] = "frame"
            x["component"] = {"figmaName": c["figmaName"], "implName": c["figmaName"], "implNames": [c["figmaName"]]}
            b = x["box"]
            x["children"] = [{"id": f"{x['id']}#{k}", "kind": "text", "text": slot["text"], "tag": "span",
                              "box": {"x": b["x"], "y": b["y"] + 14 * k, "w": b["w"], "h": 14}}
                             for k, slot in enumerate(c.get("content") or [])]
        elif x["kind"] == "icon":
            x["kind"] = "vector"
        x["tag"] = "div"
        stack.extend(x.get("children") or [])
    return n


class RealModal(unittest.TestCase):
    """Настоящий канон «Modal» (пиксо-образец после 1.3–1.5) против «страницы», построенной из него же."""

    @classmethod
    def setUpClass(cls):
        cls.design = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")["root"]

    def test_real_canon_pairs_with_its_own_rendering(self):
        res = M.match(self.design, page([as_page(self.design)]))
        self.assertTrue(res["hardGate"]["pass"])
        self.assertEqual(res["stats"]["matchRate"], 1.0)
        self.assertEqual((res["missing"], res["extra"], res["order"]), ([], [], []))
        self.assertEqual(res["stats"]["designContent"], 3, "три инстанса — три узла содержимого")

    def test_real_canon_without_the_action_block(self):
        pg = as_page(self.design)
        victim = pg["children"][-1]
        self.assertEqual(victim["component"]["figmaName"], "Modal Action")
        pg["children"].remove(victim)
        res = M.match(self.design, page([pg]))
        self.assertEqual([m["id"] for m in res["missing"]], ["2:153704"])
        self.assertIn("Modal Action", res["missing"][0]["branch"])
        self.assertNotIn("Текст11", res["missing"][0]["branch"], "содержимое инстанса одним узлом не называется")
        self.assertEqual(res["stats"]["matchRate"], 0.6667)
        self.assertEqual(res["extra"], [])

    def test_two_headers_with_one_component_name_are_told_apart_by_text(self):
        pg = as_page(self.design)
        pg["children"][0], pg["children"][1] = pg["children"][1], pg["children"][0]     # страница поменяла два «Modal Header»
        res = M.match(self.design, page([pg]))
        got = pairs_of(res)
        self.assertEqual(got["2:153703"], "p:2:153703")
        self.assertEqual(got["22:63141"], "p:22:63141")
        self.assertEqual(len(res["order"]), 1, "поменялись двое — сдвинулся один (второй на месте относительно неподвижных)")
        self.assertEqual(res["order"][0]["id"], "22:63141", "ничья: остаётся тот, кто раньше в макете")

    def test_slot_texts_of_a_matched_instance_are_not_extra(self):
        res = M.match(self.design, page([as_page(self.design)]))
        self.assertEqual(res["extra"], [])
        pg = as_page(self.design)
        pg["children"][0]["children"].append({"id": "p:stray", "kind": "text", "text": "Чужая приписка", "tag": "span",
                                              "box": {"x": 0, "y": 5, "w": 100, "h": 14}})
        res = M.match(self.design, page([pg]))
        self.assertEqual([e["id"] for e in res["extra"]], ["p:stray"], "текст, которого нет среди слотов инстанса, — лишний")


def M_texts(node):
    out, stack = [], [node]
    while stack:
        x = stack.pop()
        if x["kind"] == "text":
            out.append(x)
        stack.extend(reversed(x.get("children") or []))
    return out


def drop(node, node_id):
    for k in list(node.get("children") or []):
        if k["id"] == node_id:
            node["children"].remove(k)
            return True
        if drop(k, node_id):
            return True
    return False


class Basics(unittest.TestCase):
    def test_identical_trees_pair_completely(self):
        d = screen([T("d:a", "Заголовок"), T("d:b", "Описание", y=30), inst("d:btn", "Button", [T("d:bt", "Сохранить")])])
        i = page([P("p:a", "Заголовок"), P("p:b", "Описание", y=30), comp("p:btn", ["Button"], [P("p:bt", "Сохранить")])])
        res = M.match(d, i)
        self.assertTrue(res["hardGate"]["pass"])
        self.assertEqual(res["stats"]["matchRate"], 1.0)
        self.assertEqual((res["missing"], res["extra"], res["order"]), ([], [], []))
        self.assertEqual(pairs_of(res)["d:a"], "p:a")
        self.assertEqual(pairs_of(res)["d:bt"], "p:bt")
        self.assertEqual(pairs_of(res)["d:btn"], "p:btn")

    def test_envelopes_are_accepted(self):
        d = {"daiver": {}, "stats": {}, "root": screen([T("d:a", "Привет")])}
        i = {"daiver": {}, "root": page([P("p:a", "Привет")])}
        self.assertEqual(M.match(d, i)["stats"]["matchRate"], 1.0)

    def test_not_a_tree_is_refused(self):
        with self.assertRaises(M.MatchError):
            M.match({"x": 1}, screen([]))

    def test_deterministic_and_input_untouched(self):
        d = screen([T("d:a", "Один"), T("d:b", "Два", y=20), T("d:c", "Три", y=40)])
        i = page([P("p:c", "Три", y=40), P("p:a", "Один"), P("p:b", "Два", y=20), P("p:x", "Лишний", y=60)])
        before = copy.deepcopy((d, i))
        a, b = M.match(d, i), M.match(d, i)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual((d, i), before)


class Anchors(unittest.TestCase):
    def test_text_is_case_and_space_insensitive(self):
        d = screen([T("d:a", "Сохранить  изменения")])
        i = page([P("p:a", "сохранить изменения")])
        p = [x for x in M.match(d, i)["pairs"] if x["design"] == "d:a"][0]
        self.assertEqual((p["impl"], p["anchor"], p["confidence"]), ("p:a", "text", "high"))

    def test_twin_cards_are_not_cross_linked(self):
        def card(pref, n, title):
            return D("frame", f"{pref}:card{n}", [T(f"{pref}:t{n}", title, y=n * 50), T(f"{pref}:b{n}", "Купить", y=n * 50 + 20)],
                     box=box(0, n * 50, 200, 45))
        d = screen([card("d", n, f"Товар {n}") for n in range(5)])
        i = page([D("frame", f"p:card{n}", [P(f"p:t{n}", f"Товар {n}", y=n * 50), P(f"p:b{n}", "Купить", y=n * 50 + 20)],
                    box=box(0, n * 50, 200, 45), tag="div") for n in reversed(range(5))])
        got = pairs_of(M.match(d, i))
        for n in range(5):
            self.assertEqual(got[f"d:card{n}"], f"p:card{n}")
            self.assertEqual(got[f"d:t{n}"], f"p:t{n}")
            self.assertEqual(got[f"d:b{n}"], f"p:b{n}", "одинаковый текст «Купить» спарился внутри СВОЕЙ карточки")

    def test_component_name_ignores_case_and_separators(self):
        d = screen([inst("d:btn", "BasicButton")])
        i = page([comp("p:btn", ["basic-button"], [P("p:t", "?")])])
        res = M.match(d, i)
        self.assertEqual(pairs_of(res)["d:btn"], "p:btn")
        self.assertEqual([p for p in res["pairs"] if p["design"] == "d:btn"][0]["anchor"], "component")

    def test_text_props_are_the_instance_text(self):
        d = screen([inst("d:btn", "Whatever", comp={"textProps": {"label": "Отправить"}}),
                    inst("d:other", "Whatever", comp={"textProps": {"label": "Отмена"}}, box=box(100, 0, 80, 30))])
        i = page([D("frame", "p:o", [P("p:ot", "Отмена")], box=box(100, 0, 80, 30), tag="div", style={"background": "rgba(1, 2, 3, 1)"}),
                  D("frame", "p:s", [P("p:st", "Отправить")], box=box(0, 0, 80, 30), tag="div", style={"background": "rgba(1, 2, 3, 1)"})])
        got = pairs_of(M.match(d, i))
        self.assertEqual((got["d:btn"], got["d:other"]), ("p:s", "p:o"))

    def test_composite_cell_forgives_one_wrong_char(self):
        d = screen([inst("d:cell", "Cell", comp={"content": [{"id": "s1", "text": "Иванов", "overridden": True},
                                                            {"id": "s2", "text": "Менеджер", "overridden": True}]})])
        i = page([D("frame", "p:cell", [P("p:a", "Иванав"), P("p:b", "Менеджер", y=15)], box=box(0, 0, 80, 30), tag="div"),
                  D("frame", "p:far", [P("p:z", "Зайцев", y=100)], box=box(0, 100, 80, 30), tag="div")])
        res = M.match(d, i)
        self.assertEqual(pairs_of(res)["d:cell"], "p:cell")

    def test_one_wrong_char_in_a_single_slot_cell(self):
        d = screen([inst("d:cell", "Cell", comp={"content": [{"id": "s1", "text": "Иванов", "overridden": True}]})])
        i = page([D("frame", "p:cell", [P("p:a", "Иванав")], box=box(0, 0, 80, 30), tag="div"),
                  D("frame", "p:far", [P("p:z", "Зайцев", y=100)], box=box(0, 100, 80, 30), tag="div")])
        p = [x for x in M.match(d, i)["pairs"] if x["design"] == "d:cell"][0]
        self.assertEqual((p["impl"], p["anchor"]), ("p:cell", "textOverlap"))

    def test_geometry_is_only_a_tiebreaker(self):
        # два одинаковых «Да»: текст не различает, решает геометрия — но чужой текст она не спарит
        d = screen([T("d:y1", "Да", x=0, y=0), T("d:y2", "Да", x=200, y=0)])
        i = page([P("p:y2", "Да", x=200, y=0), P("p:y1", "Да", x=0, y=0)])
        got = pairs_of(M.match(d, i))
        self.assertEqual((got["d:y1"], got["d:y2"]), ("p:y1", "p:y2"))
        d2 = screen([T("d:a", "Альфа", x=0, y=0)])
        i2 = page([P("p:b", "Бета", x=0, y=0)])
        self.assertNotIn("d:a", pairs_of(M.match(d2, i2)), "рядом по геометрии, но другой текст — это находка, а не пара")
        self.assertFalse(M.match(d2, i2)["hardGate"]["pass"], "те же счёты, чужие тексты: coverage 0, а не 1")

    def test_empty_control_is_recognised_by_paint(self):
        d = screen([D("rect", "d:cb", box=box(10, 10, 16, 16), style={"background": "rgba(0, 120, 255, 1)"})])
        i = page([D("rect", "p:cb", box=box(10, 10, 16, 16), tag="span", style={"background": "rgba(0, 120, 255, 1)"}),
                  D("rect", "p:other", box=box(50, 10, 16, 16), tag="span", style={"background": "rgba(9, 9, 9, 1)"})])
        res = M.match(d, i)
        p = [x for x in res["pairs"] if x["design"] == "d:cb"][0]
        self.assertEqual((p["impl"], p["anchor"]), ("p:cb", "paint"))

    def test_react_wrapper_is_not_a_rival(self):
        d = screen([inst("d:btn", "Button", [T("d:t", "Ок")])])
        inner = comp("p:inner", ["Button"], [P("p:t", "Ок")])
        outer = comp("p:outer", ["Button"], [inner])
        res = M.match(d, page([outer]))
        self.assertEqual(pairs_of(res)["d:btn"], "p:inner")


class Constraints(unittest.TestCase):
    def test_role_veto_text_never_pairs_a_box(self):
        d = screen([T("d:a", "Заказ")])
        i = page([D("frame", "p:box", box=box(0, 0, 60, 14), tag="div", style={"background": "rgba(1, 1, 1, 1)"})])
        res = M.match(d, i)
        self.assertNotIn("d:a", pairs_of(res))
        self.assertEqual([m["id"] for m in res["missing"]], ["d:a"])

    def test_one_to_one(self):
        d = screen([T("d:a", "Сохранить"), T("d:b", "Сохранить", y=30)])
        i = page([P("p:a", "Сохранить")])
        res = M.match(d, i)
        self.assertEqual(len(res["pairs"]) - 1, 1, "один элемент страницы — одна пара")
        self.assertEqual(len(res["missing"]), 1)

    def test_nesting_consistency_blocks_nested_pairs(self):
        # макет: два соседа с текстами; страница: «Карточка» содержит оба текста
        d = screen([D("frame", "d:f1", [T("d:t1", "Один")], box=box(0, 0, 100, 40)),
                    D("frame", "d:f2", [T("d:t2", "Два")], box=box(0, 50, 100, 40))])
        i = page([D("frame", "p:big", [D("frame", "p:in", [P("p:t1", "Один"), P("p:t2", "Два", y=10)], box=box(0, 0, 100, 40), tag="div")],
                    box=box(0, 0, 100, 40), tag="div")])
        res = M.match(d, i)
        got = pairs_of(res)
        impls = [got[k] for k in ("d:f1", "d:f2") if k in got]
        self.assertEqual(len(impls), 1, "две несвязанные рамки не могут оказаться вложенными друг в друга (одна пара, другая — находка)")
        self.assertEqual(len(res["missing"]), 1)

    def test_children_are_searched_only_inside_the_matched_parent(self):
        def card(pref, n, with_buy):
            kids = [T(f"{pref}:t{n}", f"Товар {n}", y=n * 50)]
            if with_buy:
                kids.append(T(f"{pref}:b{n}", "Купить", y=n * 50 + 20))
            return D("frame", f"{pref}:c{n}", kids, box=box(0, n * 50, 200, 45))
        d = screen([card("d", 0, True), card("d", 1, True)])
        i = page([D("frame", "p:c0", [P("p:t0", "Товар 0", y=0)], box=box(0, 0, 200, 45), tag="div"),
                  D("frame", "p:c1", [P("p:t1", "Товар 1", y=50), P("p:b1", "Купить", y=70)], box=box(0, 50, 200, 45), tag="div")])
        res = M.match(d, i)
        got = pairs_of(res)
        self.assertEqual(got.get("d:b1"), "p:b1")
        self.assertNotIn("d:b0", got, "«Купить» первой карточки не уходит в соседнюю: там ему не место")
        self.assertEqual([m["id"] for m in res["missing"]], ["d:b0"])

    def test_unmatched_layout_frame_is_transparent(self):
        d = screen([D("frame", "d:wrap", [T("d:a", "Раз"), T("d:b", "Два", y=20)], box=box(0, 0, 200, 50))])
        i = page([P("p:a", "Раз"), P("p:b", "Два", y=20)])
        res = M.match(d, i)
        self.assertEqual(pairs_of(res)["d:a"], "p:a")
        self.assertEqual(pairs_of(res)["d:b"], "p:b")
        self.assertEqual(res["missing"], [], "фрейма нет, но весь его контент найден — потери нет")

    def test_top_down_restricts_children_to_matched_parent(self):
        d = screen([D("frame", "d:c1", [T("d:t", "Цена")], box=box(0, 0, 100, 40), style={"background": "rgba(1, 1, 1, 1)"}),
                    D("frame", "d:c2", [T("d:u", "Скидка")], box=box(0, 50, 100, 40), style={"background": "rgba(1, 1, 1, 1)"})])
        i = page([D("frame", "p:c2", [P("p:u", "Скидка", y=50), P("p:t", "Цена", y=52)], box=box(0, 50, 100, 40), tag="div",
                    style={"background": "rgba(1, 1, 1, 1)"})])
        res = M.match(d, i)
        got = pairs_of(res)
        self.assertEqual(got.get("d:c2"), "p:c2")
        self.assertEqual(got.get("d:u"), "p:u")
        # «Цена» страницы лежит в карточке, парной ДРУГОЙ карточке макета: её родитель сматчен иначе — перелинковки нет
        self.assertNotIn("d:t", got)
        self.assertEqual([m["id"] for m in res["missing"]], ["d:c1"])
        self.assertEqual([e["id"] for e in res["extra"]], ["p:t"])


class RootPair(unittest.TestCase):
    def test_descends_past_covering_wrappers_to_the_frame(self):
        d = screen([T("d:a", "Привет")], {"style": {"background": "rgba(255, 255, 255, 1)"}})
        i = page([P("p:a", "Привет")])
        res = M.match(d, i)
        self.assertEqual(res["stats"]["rootImpl"], "p:wrap2", "обёртки body > #root без краски пропущены до последней перед ветвлением")

    def test_stops_at_paint(self):
        inner = D("frame", "p:card", [P("p:a", "Привет")], box=box(0, 0, 400, 300), tag="div", style={"background": "rgba(1, 1, 1, 1)"})
        outer = D("frame", "p:body", [D("frame", "p:root", [inner], box=box(0, 0, 400, 300), tag="div")], box=box(0, 0, 400, 300), tag="body")
        res = M.match(screen([T("d:a", "Привет")]), outer)
        self.assertEqual(res["stats"]["rootImpl"], "p:card")

    def test_does_not_descend_into_a_lone_text_leaf(self):
        leaf = D("frame", "p:root", [D("text", "p:t", text="Привет", box=box(0, 0, 400, 300), tag="h2")], box=box(0, 0, 400, 300), tag="div")
        res = M.match(screen([T("d:a", "Привет")]), D("frame", "p:body", [leaf], box=box(0, 0, 400, 300), tag="body"))
        self.assertEqual(res["stats"]["rootImpl"], "p:root", "корень макета — рамка, а не текст")
        self.assertEqual(pairs_of(res)["d:a"], "p:t")

    def test_does_not_descend_past_a_non_covering_child(self):
        small = D("frame", "p:small", [P("p:a", "Привет")], box=box(10, 10, 50, 30), tag="div")
        outer = D("frame", "p:body", [small], box=box(0, 0, 400, 300), tag="body")
        res = M.match(screen([T("d:a", "Привет")]), outer)
        self.assertEqual(res["stats"]["rootImpl"], "p:body")


class PlanBridge(unittest.TestCase):
    def _trees(self):
        d = screen([inst("d:pb", "Primary Button Large", box=box(0, 0, 80, 30)), T("d:a", "Заказ", y=50)])
        i = page([comp("p:btn", ["Btn"], [D("vector", "p:ic", box=box(5, 5, 10, 10), tag="svg")]), P("p:a", "Заказ", y=50)])
        return d, i

    def test_without_plan_falls_back_to_sibling_order(self):
        d, i = self._trees()
        p = [x for x in M.match(d, i)["pairs"] if x["design"] == "d:pb"][0]
        self.assertEqual((p["impl"], p["anchor"], p["confidence"]), ("p:btn", "position", "low"))

    def test_plan_names_the_component(self):
        d, i = self._trees()
        plan = {"sections": [{"id": "s1", "nodes": [{"id": "d:pb", "kind": "instance", "component": "Btn", "confidence": "high"}]}]}
        res = M.match(d, i, plan)
        p = [x for x in res["pairs"] if x["design"] == "d:pb"][0]
        self.assertEqual((p["impl"], p["anchor"], p["confidence"]), ("p:btn", "plan", "high"))

    def test_kind_word_is_not_a_name_by_form(self):
        d, i = self._trees()
        plan = {"nodes": [{"id": "d:pb", "component": "div"}, {"id": "d:a", "resolution": "local"}]}
        self.assertEqual(M.read_plan(plan, ["d:pb", "d:a"]), {"d:a": {"resolution": "local", "name": None},
                                                                "d:pb": {"resolution": None, "name": None}})
        p = [x for x in M.match(d, i, plan)["pairs"] if x["design"] == "d:pb"][0]
        self.assertEqual((p["anchor"], p["confidence"]), ("position", "low"), "«div» — слово вида, мостом имён не стало")

    def test_ignore_is_skipped_local_is_kept(self):
        d = screen([T("d:a", "Здесь"), D("frame", "d:ign", [T("d:x", "Нет кода")], box=box(0, 40, 100, 20)),
                    D("frame", "d:loc", [T("d:y", "Нет на странице")], box=box(0, 80, 100, 20))])
        i = page([P("p:a", "Здесь")])
        plan = {"sections": [{"nodes": [{"id": "d:ign", "resolution": "ignore"}, {"id": "d:loc", "resolution": "local"}]}]}
        res = M.match(d, i, plan)
        self.assertEqual(res["stats"]["ignored"], 1)
        self.assertEqual([m["id"] for m in res["missing"]], ["d:loc"], "ignore не MISSING, local остаётся обычным узлом")

    def test_read_plan_three_carriers(self):
        known = ["a", "b", "c", "zz"]
        plan = {"resolutions": {"a": {"component": "Alpha"}},
                "sections": [{"nodes": [{"id": "b", "component": {"name": "Beta"}}]}],
                "pairs": [["c", "Gamma"], ["not-known", "Delta"]]}
        got = M.read_plan(plan, known)
        self.assertEqual({k: v["name"] for k, v in got.items()}, {"a": "Alpha", "b": "Beta", "c": "Gamma"})

    def test_garbage_plan_is_no_decision(self):
        self.assertEqual(M.read_plan("строка", ["a"]), {})
        self.assertEqual(M.read_plan({"a": 5, "id": 7}, ["a"]), {})


class Findings(unittest.TestCase):
    def test_extra_is_content_only(self):
        d = screen([T("d:a", "Раз"), T("d:b", "Два", y=30)])
        wrap = D("frame", "p:anon", [], box=box(0, 80, 10, 10), tag="div")
        i = page([P("p:a", "Раз"), P("p:b", "Два", y=30), P("p:x", "Лишний текст", y=15), wrap])
        res = M.match(d, i)
        self.assertEqual([e["id"] for e in res["extra"]], ["p:x"])
        self.assertEqual(res["extra"][0]["text"], "Лишний текст")

    def test_chrome_outside_region_is_not_extra(self):
        d = screen([T("d:a", "Раз"), T("d:b", "Два", y=30)])
        nav = D("frame", "p:nav", [P("p:n1", "Меню", x=300, y=0), P("p:n2", "Выход", x=300, y=300)], box=box(300, 0, 100, 320), tag="nav")
        i = page([P("p:a", "Раз"), P("p:b", "Два", y=30), nav])
        res = M.match(d, i)
        self.assertEqual(res["extra"], [])
        self.assertEqual(res["stats"]["outsideRegion"], 1, "вся навигация — один хром вне региона")

    def test_chrome_deeper_than_tolerance_is_reported(self):
        d = screen([T("d:a", "Раз", x=0, y=0), T("d:b", "Два", x=0, y=30)])
        deep = P("p:x", "Чужое", x=30, y=10)
        i = page([P("p:a", "Раз", x=0, y=0), P("p:b", "Два", x=0, y=30), deep])
        self.assertEqual([e["id"] for e in M.match(d, i)["extra"]], ["p:x"])

    def test_chrome_touching_the_region_edge_is_not_reported(self):
        d = screen([T("d:a", "Раз", x=0, y=0)])
        edge = P("p:x", "Шелл", x=61, y=0)                       # слово пары кончается на x=60: касается кромки
        i = page([P("p:a", "Раз", x=0, y=0), edge])
        res = M.match(d, i)
        self.assertEqual(res["extra"], [])
        self.assertEqual(res["stats"]["outsideRegion"], 1)

    def test_page_chrome_around_a_modal_is_outside_region(self):
        inner = D("frame", "p:modal", [P("p:a", "Окно", x=60, y=60)], box=box(50, 50, 200, 100), tag="div",
                  style={"background": "rgba(1, 1, 1, 1)"})
        outer = D("frame", "p:body", [inner, P("p:hdr", "Шапка сайта", x=0, y=0)], box=box(0, 0, 400, 300), tag="body")
        res = M.match(D("frame", "d:m", [T("d:a", "Окно")], box=box(0, 0, 200, 100)), outer)
        self.assertEqual(pairs_of(res)["d:a"], "p:a")
        self.assertEqual((res["extra"], res["stats"]["outsideRegion"]), ([], 1))

    def test_missing_names_the_branch_in_one_line(self):
        footer = D("frame", "d:foot", [T("d:f1", "Условия"), T("d:f2", "Политика", y=15), D("icon", "d:fi", box=box(0, 0, 8, 8))],
                   box=box(0, 100, 100, 40))
        d = screen([T("d:a", "Раз"), footer])
        res = M.match(d, page([P("p:a", "Раз")]))
        self.assertEqual(len(res["missing"]), 1)
        m = res["missing"][0]
        self.assertEqual((m["id"], m["contentCount"]), ("d:foot", 3))
        self.assertIn("Условия", m["branch"])
        self.assertNotIn("\n", m["branch"])

    def test_missing_instance_does_not_name_its_content(self):
        d = screen([T("d:a", "Раз"), inst("d:card", "Card", [T("d:s", "Секретный слот")], box=box(0, 100, 100, 40))])
        res = M.match(d, page([P("p:a", "Раз")]))
        self.assertEqual([m["id"] for m in res["missing"]], ["d:card"])
        self.assertNotIn("Секретный слот", res["missing"][0]["branch"])
        self.assertIn("Card", res["missing"][0]["branch"])

    def test_empty_frame_is_not_missing(self):
        d = screen([T("d:a", "Раз"), D("frame", "d:empty", box=box(0, 50, 100, 40)), D("frame", "d:e2", [D("frame", "d:e3")], box=box(0, 90, 10, 10))])
        res = M.match(d, page([P("p:a", "Раз")]))
        self.assertEqual(res["missing"], [])

    def test_order_blames_the_one_that_moved(self):
        d = screen([T("d:a", "Альфа", y=0), T("d:b", "Бета", y=20), T("d:c", "Гамма", y=40)])
        i = page([P("p:b", "Бета", y=0), P("p:c", "Гамма", y=20), P("p:a", "Альфа", y=40)])
        res = M.match(d, i)
        self.assertEqual([o["id"] for o in res["order"]], ["d:a"], "переехал один, а не оба, кого он перепрыгнул")

    def test_order_clean_when_sequence_is_kept(self):
        d = screen([T("d:a", "Альфа"), T("d:b", "Бета", y=20)])
        self.assertEqual(M.match(d, page([P("p:a", "Альфа"), P("p:b", "Бета", y=20)]))["order"], [])

    def test_lis_tie_is_deterministic(self):
        self.assertEqual(M._lis_keep([1, 0, 2]), M._lis_keep([1, 0, 2]))
        self.assertEqual(sorted(M._lis_keep([3, 4, 5, 0, 1, 2])), [0, 1, 2], "первые три остаются, остальные «сдвинулись»")
        big = list(range(1, M.LIS_EXACT_MAX + 5)) + [0]
        self.assertEqual(len(M._lis_keep(big)), M.LIS_EXACT_MAX + 4)


class Gates(unittest.TestCase):
    def test_hard_gate_fails_on_another_page(self):
        d = screen([T("d:a", "Раз"), T("d:b", "Два", y=20), T("d:c", "Три", y=40)])
        res = M.match(d, page([P("p:x", "Совсем", y=0), P("p:y", "Другое", y=20)]))
        self.assertFalse(res["hardGate"]["pass"])
        self.assertLess(res["stats"]["matchRate"], M.MIN_MATCH_RATE)
        self.assertIn("не этот кадр", res["hardGate"]["reason"])

    def test_gate_threshold_is_inclusive_at_half(self):
        d = screen([T("d:a", "Раз"), T("d:b", "Два", y=20)])
        res = M.match(d, page([P("p:a", "Раз")]))
        self.assertEqual(res["stats"]["matchRate"], 0.5)
        self.assertTrue(res["hardGate"]["pass"])

    def test_empty_page_fails_with_its_own_reason(self):
        d = screen([T("d:a", "Раз")])
        res = M.match(d, None)
        self.assertFalse(res["hardGate"]["pass"])
        self.assertIn("нет содержимого", res["hardGate"]["reason"])
        self.assertEqual(res["stats"]["matchRate"], 0.0)

    def test_layout_frames_and_occluded_do_not_count(self):
        d = screen([D("frame", "d:f", [D("frame", "d:g", [T("d:a", "Раз")], box=box(0, 0, 10, 10))], box=box(0, 0, 10, 10)),
                    D("frame", "d:occ", [T("d:o", "Скрыто")], occluded=True, box=box(0, 0, 10, 10))])
        res = M.match(d, page([P("p:a", "Раз")]))
        self.assertEqual((res["stats"]["designContent"], res["stats"]["matchRate"]), (1, 1.0))

    def test_empty_text_layer_is_not_content(self):
        d = screen([D("text", "d:e", text="  ", box=box(0, 0, 10, 10)), T("d:a", "Раз", y=20)])
        res = M.match(d, page([P("p:a", "Раз", y=20)]))
        self.assertEqual((res["stats"]["designContent"], res["missing"]), (1, []), "пустой слой не виден и не ищется")

    def test_design_without_content_has_rate_one(self):
        res = M.match(screen([D("frame", "d:f", box=box(0, 0, 10, 10))]), page([P("p:a", "Раз")]))
        self.assertEqual(res["stats"]["matchRate"], 1.0)

    def test_low_match_rate_collapses_missing_under_one_ancestor(self):
        texts = [T(f"d:t{n}", f"Текст{n}", y=n * 20) for n in range(10)]
        d = screen(texts)
        i = page([P(f"p:t{n}", f"Текст{n}", y=n * 20) for n in range(7)])
        res = M.match(d, i)
        self.assertEqual(res["stats"]["matchRate"], 0.7)
        self.assertTrue(res["hardGate"]["pass"])
        self.assertTrue(res["lowMatchRate"])
        self.assertEqual(len(res["missing"]), 1, "россыпь из трёх MISSING одним предком — одна запись")
        self.assertEqual(res["missing"][0]["ids"], ["d:t7", "d:t8", "d:t9"])
        self.assertEqual(res["missing"][0]["contentCount"], 3)

    def test_above_low_rate_missing_stays_individual(self):
        texts = [T(f"d:t{n}", f"Текст{n}", y=n * 20) for n in range(20)]
        i = page([P(f"p:t{n}", f"Текст{n}", y=n * 20) for n in range(18)])
        res = M.match(screen(texts), i)
        self.assertEqual(res["stats"]["matchRate"], 0.9)
        self.assertFalse(res["lowMatchRate"])
        self.assertEqual(len(res["missing"]), 2)


class CodeMode(unittest.TestCase):
    """Модель кода: корень макета — парой с `code:root` как есть, инлайн-компонент (frame с именем) — прозрачная обёртка."""

    def test_root_is_not_descended(self):
        sec = D("frame", "code:s1", [P("code:s1/0", "Привет")], tag="div")
        root = D("frame", "code:root", [sec])
        res = M.match(screen([T("d:a", "Привет")]), root, descend_root=False)
        self.assertEqual(res["stats"]["rootImpl"], "code:root")
        self.assertEqual(pairs_of(res)["d:a"], "code:s1/0")

    def test_inline_component_is_transparent_but_a_package_black_box_is_not(self):
        wrapped = D("frame", "code:s1", [P("code:s1/0", "Привет")], component={"figmaName": "Card", "implName": "Card"})
        res = M.match(screen([T("d:a", "Приветт")]), D("frame", "code:root", [wrapped]), descend_root=False)
        self.assertEqual(pairs_of(res).get("d:a"), "code:s1/0", "опечатку нашли через обёртку-компонент")
        box_ = D("instance", "code:s1", [P("code:s1/0", "Привет")], component={"figmaName": "Card", "implName": "Card"})
        free = M._Tree(D("frame", "code:root", [box_]), "impl", code=True).flat_children(0)
        self.assertEqual(len(free), 1, "чёрный ящик пакета остаётся компонентом: его содержимое не вытаскивается наружу")

    def test_instance_with_props_counts_as_page_content(self):
        root = D("frame", "code:root", [D("instance", "code:s1", component={"figmaName": "Btn", "implName": "Btn", "textProps": {"l": "Ок"}})])
        res = M.match(screen([inst("d:b", "Btn", comp={"origin": "library", "textProps": {"l": "Ок"}})]), root, descend_root=False)
        self.assertTrue(res["hardGate"]["pass"])
        self.assertEqual(pairs_of(res)["d:b"], "code:s1")


class Leftover(unittest.TestCase):
    def test_equal_counts_pair_by_position_with_low_confidence(self):
        d = screen([D("icon", "d:i1", box=box(0, 0, 10, 10)), D("icon", "d:i2", box=box(20, 0, 10, 10))])
        i = page([D("vector", "p:i1", box=box(0, 0, 10, 10), tag="svg"), D("vector", "p:i2", box=box(20, 0, 10, 10), tag="svg")])
        res = M.match(d, i)
        self.assertEqual(pairs_of(res)["d:i1"], "p:i1")
        self.assertEqual({p["confidence"] for p in res["pairs"] if p["design"] != "d:root"}, {"low"})
        self.assertEqual(res["stats"]["lowConfidence"], 2)

    def test_unequal_counts_pair_nothing(self):
        d = screen([D("icon", "d:i1", box=box(0, 0, 10, 10)), D("icon", "d:i2", box=box(20, 0, 10, 10))])
        i = page([D("vector", "p:i1", box=box(0, 0, 10, 10), tag="svg")])
        res = M.match(d, i)
        self.assertEqual(len([p for p in res["pairs"] if p["design"] != "d:root"]), 0)

    def test_typo_in_a_text_still_pairs_as_low_confidence(self):
        d = screen([T("d:a", "Сохранить")])
        res = M.match(d, page([P("p:a", "Сохранить!")]))
        p = [x for x in res["pairs"] if x["design"] == "d:a"][0]
        self.assertEqual((p["impl"], p["anchor"], p["confidence"]), ("p:a", "textSimilar", "low"))

    def test_typo_pairs_by_similarity_even_when_counts_differ_and_under_a_transparent_frame(self):
        d = screen([D("frame", "d:f", [T("d:a", "Сохранить изменения"), T("d:b", "Отмена", y=20), T("d:c", "Футер", y=40)], box=box(0, 0, 100, 60))])
        res = M.match(d, page([P("p:a", "Сохранить изменения!"), P("p:b", "Отмена", y=20)]))
        got = pairs_of(res)
        self.assertEqual((got["d:a"], got["d:b"]), ("p:a", "p:b"))
        self.assertEqual([m["id"] for m in res["missing"]], ["d:c"])

    def test_dissimilar_texts_are_never_paired_by_similarity(self):
        res = M.match(screen([T("d:a", "Альфа"), T("d:b", "Гамма", y=20)]), page([P("p:x", "Бета"), P("p:y", "Дельта", y=20)]))
        self.assertEqual([p["design"] for p in res["pairs"]], ["d:root"])


if __name__ == "__main__":
    unittest.main()
