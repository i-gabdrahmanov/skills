#!/usr/bin/env python3
"""test_materialize.py — Pixso → канон: материализация инстансов (задача 1.4; QG U2, INV B4/B5/B6, F8).

Фикстура «Modal» сверена с её картинкой: заголовок первой шапки без описания (Description скрыт
присвоением), у второй — описание; «Отмена» — настройка самой библиотеки (overridden=false), «Подтвердить»
— текст экрана (true); размеры — настоящие (560), а не размер мастера (600). Синтетика пинит каждое
правило модели оверрайдов (см. docstring ingest/materialize.py): слои, видимость, подмены, циклы,
происхождение, sized/paints, коллапс иконок, анатомия local, окклюзия.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
BLACK = {"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0, "a": 1}}


# ── построители ───────────────────────────────────────────────────────────────
def n(guid, typ="FRAME", name=None, w=None, h=None, left=0, top=0, kids=None, **kw):
    node = {"guid": guid, "type": typ, "name": name or guid, "left": left, "top": top}
    if w is not None:
        node["width"] = w
    if h is not None:
        node["height"] = h
    if kids:
        node["childNode"] = list(kids)
    node.update(kw)
    return node


def text(guid, value, name="Label", left=0, top=0, w=50, h=10, **kw):
    return n(guid, "PARAGRAPH", name=name, w=w, h=h, left=left, top=top, nodeText=value, **kw)


def comp(guid, name, kids=(), published=True, w=100, h=40, **kw):
    """Обычный компонент (без группы состояний): верхнеуровневое определение и есть вариант."""
    top = {"guid": guid, "type": "SYMBOL", "layerType": "COMPONENT", "name": name, "width": w, "height": h}
    if kids:
        top["childNode"] = list(kids)
    if published:
        top["publishFile"] = "lib"
    top.update(kw)
    return top


def inst(guid, main, name="Comp", w=100, h=40, left=0, top=0, props=(), **kw):
    node = n(guid, "INSTANCE", name=name, w=w, h=h, left=left, top=top, mainComponent=main)
    if props:
        node["props"] = list(props)
    node.update(kw)
    return node


def po(path, **kw):
    return dict({"pathString": path}, **kw)


def screen(*kids, defs=(), w=400, h=400):
    return {"dsl": {"pixTreeDslNodes": [n("root", w=w, h=h, kids=kids)], "pixComponentTreeDslNodes": list(defs)}}


def conv(*kids, defs=(), **kw) -> dict:
    return PX.normalize_doc(screen(*kids, defs=defs, **kw))


def by_id(env) -> dict:
    return C.index_nodes(env["root"])


def content(env, guid):
    return by_id(env)[guid]["component"].get("content", [])


def flat(slots):
    return [(s["id"], s["text"], s["overridden"]) for s in slots]


# ── образец ───────────────────────────────────────────────────────────────────
class OnFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.env = PX.normalize_doc(cls.doc, "sha")
        cls.ids = by_id(cls.env)

    def test_first_header_has_title_only(self):
        c = self.ids["2:153703"]["component"]
        self.assertEqual(flat(c["content"]), [("2:153703;2:170582", "Текст12 слово 1-105_2-слово.1", True)])
        self.assertEqual(c["content"][0]["name"], "Header")

    def test_second_header_has_title_and_visible_description(self):
        c = self.ids["22:63141"]["component"]
        self.assertEqual([s["name"] for s in c["content"]], ["Header", "Description"])
        self.assertEqual(c["content"][0]["text"], "Текст9 слово - 20")
        self.assertTrue(c["content"][1]["text"].startswith("Текст10 слово"))
        self.assertTrue(all(s["overridden"] for s in c["content"]))

    def test_action_buttons_placeholder_vs_screen_copy(self):
        c = self.ids["2:153704"]["component"]
        self.assertEqual(flat(c["content"]), [
            ("2:153704;2:170352/2:170446", "Текст3", False),     # настройка библиотеки у «Отмена»
            ("2:153704;2:170353/2:170486", "Текст11", True),     # копия этого экрана у «Подтвердить»
        ])

    def test_slot_ids_are_unique_across_the_screen(self):
        ids = [s["id"] for k in self.ids.values() for s in (k.get("component") or {}).get("content", [])]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        self.assertTrue(all(i.split(";", 1)[0] in self.ids for i in ids), "часть до «;» — id инстанса")

    def test_origin_role_and_sized(self):
        for g in ("2:153703", "22:63141", "2:153704"):
            c = self.ids[g]["component"]
            self.assertEqual((c["origin"], c["childrenRole"], c["sized"]), ("library", "content", "own"))
            self.assertNotIn("paints", c, "у мастеров образца нет собственной поверхности")
            self.assertNotIn("children", self.ids[g], "library — чёрный ящик, анатомия детьми не раскрывается")

    def test_real_sizes_come_from_the_root_override(self):
        self.assertEqual(self.ids["2:153704"]["size"], {"w": 560, "h": "hug"}, "узел говорит 600")

    def test_no_text_is_lost_and_nothing_is_double_counted(self):
        got = [(t["text"], t["via"], t["overridden"]) for t in C.collect_texts(self.env["root"])]
        self.assertEqual([g[1] for g in got], ["content"] * 5)
        self.assertEqual([g[0] for g in got][:1], ["Текст12 слово 1-105_2-слово.1"])
        self.assertEqual([g[0] for g in got][-2:], ["Текст3", "Текст11"])
        self.assertEqual(len(got), 5)

    def test_stats_and_diagnostics(self):
        m = self.env["stats"]["materialization"]
        self.assertEqual(m["counts"]["instancesResolved"], 3)
        self.assertEqual(m["counts"]["origin:library"], 3)
        self.assertEqual((m["counts"]["slots"], m["counts"]["slotsOverridden"], m["counts"]["slotsHidden"]), (5, 4, 4))
        self.assertEqual(m["diagnostics"]["MISSING_COMPONENT_DEFINITION"]["count"], 1,
                         "подмена иконки на компонент, которого нет в экспорте — остаётся умолчание мастера")
        self.assertNotIn("overridesUnmatched", m, "в образце все оверрайды легли на слоты")
        self.assertEqual(C.validate(self.env), [])

    def test_deterministic(self):
        again = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
        self.assertEqual(C.dumps(again), C.dumps(self.env))


# ── слои оверрайдов, видимость ────────────────────────────────────────────────
class Layers(unittest.TestCase):
    def test_screen_layer_beats_library_layer_and_marks_overridden(self):
        btn = comp("B", "Button", [text("Bt", "Библиотечный")])
        card = comp("C", "Card", [n("Ci", "INSTANCE", mainComponent="B", name="Btn", w=100, h=40,
                                   props=[po("Bt", nodeText="Настройка карточки")])])
        env = conv(inst("a", "C"), inst("b", "C", props=[po("Ci/Bt", nodeText="Текст экрана")]), defs=[btn, card])
        self.assertEqual(flat(content(env, "a")), [("a;Ci/Bt", "Настройка карточки", False)])
        self.assertEqual(flat(content(env, "b")), [("b;Ci/Bt", "Текст экрана", True)])

    def test_master_text_without_any_override_is_a_placeholder(self):
        env = conv(inst("a", "B"), defs=[comp("B", "Button", [text("Bt", "Плейсхолдер")])])
        self.assertEqual(flat(content(env, "a")), [("a;Bt", "Плейсхолдер", False)])

    def test_two_cells_of_one_component_do_not_share_ids(self):
        defs = [comp("B", "Cell", [text("Bt", "x")])]
        env = conv(inst("a", "B", props=[po("Bt", nodeText="Один")]), inst("b", "B", top=50,
                   props=[po("Bt", nodeText="Два")]), defs=defs)
        self.assertEqual([s["id"] for g in "ab" for s in content(env, g)], ["a;Bt", "b;Bt"])

    def test_slots_follow_position_not_array_order(self):
        defs = [comp("B", "Row", [text("T2", "нижний", top=30), text("T1", "верхний", top=0)])]
        env = conv(inst("a", "B"), defs=defs)
        self.assertEqual([s["text"] for s in content(env, "a")], ["верхний", "нижний"])

    def test_empty_override_text_clears_the_slot(self):
        env = conv(inst("a", "B", props=[po("Bt", nodeText="")]), defs=[comp("B", "Button", [text("Bt", "x")])])
        self.assertNotIn("content", by_id(env)["a"]["component"])
        self.assertNotIn("overridesUnmatched", env["stats"]["materialization"]["counts"])


class Visibility(unittest.TestCase):
    DEF = comp("V", "Opt", [text("Va", "Всегда"), text("Vb", "Опция", top=20, propRefMap={"visible": {"key": "k1"}})],
               propDefMap={"k1": {"key": "k1", "name": "Option", "type": "visible", "defaultValue": False}})

    def texts(self, i):
        env = conv(i, defs=[self.DEF])
        return [s["text"] for s in content(env, i["guid"])], env

    def test_property_default_hides_the_slot(self):
        self.assertEqual(self.texts(inst("a", "V"))[0], ["Всегда"])

    def test_instance_assignment_shows_it(self):
        got, _ = self.texts(inst("a", "V", propAssignMap={"k1": {"type": "visible", "value": True}}))
        self.assertEqual(got, ["Всегда", "Опция"])

    def test_assignment_false_overrides_default_true(self):
        defs = [dict(self.DEF, propDefMap={"k1": {"key": "k1", "type": "visible", "defaultValue": True}})]
        env = conv(inst("a", "V", propAssignMap={"k1": {"type": "visible", "value": False}}), defs=defs)
        self.assertEqual([s["text"] for s in content(env, "a")], ["Всегда"])

    def test_entry_level_visible_beats_the_property(self):
        got, _ = self.texts(inst("a", "V", props=[po("Vb", visible=True)]))
        self.assertEqual(got, ["Всегда", "Опция"])
        got, _ = self.texts(inst("a", "V", propAssignMap={"k1": {"type": "visible", "value": True}},
                                 props=[po("Vb", visible=False)]))
        self.assertEqual(got, ["Всегда"])

    def test_native_invisible_node_is_hidden_and_hides_its_subtree(self):
        wrap = n("W", w=50, h=20, visible=False, kids=[text("Wt", "внутри скрытого")])
        env = conv(inst("a", "V2"), defs=[comp("V2", "Box", [text("Va", "виден"), wrap])])
        self.assertEqual([s["text"] for s in content(env, "a")], ["виден"])

    def test_hidden_slot_override_is_not_unmatched(self):
        _, env = self.texts(inst("a", "V", props=[po("Vb", nodeText="спрятано")]))
        m = env["stats"]["materialization"]
        self.assertEqual(m["counts"]["slotsHidden"], 1)
        self.assertNotIn("overridesUnmatched", m)

    def test_visibility_of_a_nested_instance_node_itself(self):
        inner = comp("I", "Inner", [text("It", "вложенный")])
        outer = comp("O", "Outer", [n("Oi", "INSTANCE", mainComponent="I", w=10, h=10,
                                       propRefMap={"visible": {"key": "kk"}})],
                     propDefMap={"kk": {"key": "kk", "type": "visible", "defaultValue": True}})
        env = conv(inst("a", "O", propAssignMap={"kk": {"type": "visible", "value": False}}),
                   inst("b", "O", top=50), defs=[inner, outer])
        self.assertEqual(content(env, "a"), [])
        self.assertEqual([s["text"] for s in content(env, "b")], ["вложенный"])


class Unmatched(unittest.TestCase):
    def test_override_on_no_slot_is_kept_with_the_instance_name(self):
        env = conv(inst("a", "B", name="Кнопка", props=[po("Zz", nodeText="потерянный")]),
                   defs=[comp("B", "Button", [text("Bt", "x")])])
        m = env["stats"]["materialization"]
        self.assertEqual(m["counts"]["overridesUnmatched"], 1)
        self.assertEqual(m["overridesUnmatched"], [{"instance": "Кнопка", "id": "a", "path": "Zz", "text": "потерянный"}])

    def test_list_is_bounded_but_counted_in_full(self):
        props = [po("Z%d" % i, nodeText="t") for i in range(30)]
        env = conv(inst("a", "B", props=props), defs=[comp("B", "Button", [text("Bt", "x")])])
        m = env["stats"]["materialization"]
        self.assertEqual(m["counts"]["overridesUnmatched"], 30)
        self.assertEqual(len(m["overridesUnmatched"]), 20)


# ── подмены, циклы, происхождение ─────────────────────────────────────────────
class Swaps(unittest.TestCase):
    def defs(self):
        a = comp("A", "Default", [text("At", "умолчание")])
        b = comp("Bb", "Swapped", [text("Bt", "подмена")])
        host = comp("H", "Host", [n("Hn", "INSTANCE", mainComponent="A", w=10, h=10,
                                    propRefMap={"instance": {"key": "sw"}})])
        return [a, b, host]

    def test_default_without_assignment(self):
        env = conv(inst("x", "H"), defs=self.defs())
        self.assertEqual(flat(content(env, "x")), [("x;Hn/At", "умолчание", False)])

    def test_assigned_component_replaces_the_master(self):
        env = conv(inst("x", "H", propAssignMap={"sw": {"type": "instance", "value": "Bb"}}), defs=self.defs())
        self.assertEqual(flat(content(env, "x")), [("x;Hn/Bt", "подмена", False)])
        self.assertEqual(env["stats"]["materialization"]["counts"]["swapsApplied"], 1)

    def test_swap_by_publish_id(self):
        defs = self.defs()
        defs[1]["publishID"] = "pub-b"
        env = conv(inst("x", "H", propAssignMap={"sw": {"type": "instance", "value": "pub-b"}}), defs=defs)
        self.assertEqual([s["text"] for s in content(env, "x")], ["подмена"])

    def test_unresolved_swap_keeps_the_default_and_is_counted(self):
        env = conv(inst("x", "H", propAssignMap={"sw": {"type": "instance", "value": "ghost"}}), defs=self.defs())
        self.assertEqual([s["text"] for s in content(env, "x")], ["умолчание"])
        m = env["stats"]["materialization"]
        self.assertEqual(m["counts"]["swapsUnresolved"], 1)
        self.assertEqual(m["diagnostics"]["MISSING_COMPONENT_DEFINITION"]["examples"], [{"id": "Hn", "ref": "ghost"}])

    def test_swap_inside_a_hidden_branch_is_not_a_quality_signal(self):
        defs = self.defs()
        defs[2]["childNode"][0]["visible"] = False
        env = conv(inst("x", "H", propAssignMap={"sw": {"type": "instance", "value": "ghost"}}), defs=defs)
        self.assertNotIn("swapsUnresolved", env["stats"]["materialization"]["counts"])


class Cycles(unittest.TestCase):
    def test_self_nesting_terminates_and_is_reported(self):
        x = comp("X", "X", [text("Xt", "x"), n("Xi", "INSTANCE", mainComponent="X", w=5, h=5)])
        env = conv(inst("a", "X"), defs=[x])
        self.assertEqual([s["text"] for s in content(env, "a")], ["x"])
        self.assertEqual(env["stats"]["materialization"]["diagnostics"]["COMPONENT_EXPANSION_CYCLE"]["count"], 1)

    def test_mutual_nesting_terminates(self):
        a = comp("A", "A", [text("At", "a"), n("Ai", "INSTANCE", mainComponent="B", w=5, h=5, top=20)])
        b = comp("B", "B", [text("Bt", "b"), n("Bi", "INSTANCE", mainComponent="A", w=5, h=5, top=20)])
        env = conv(inst("s", "A"), defs=[a, b])
        self.assertEqual([s["text"] for s in content(env, "s")], ["a", "b"])
        self.assertEqual(env["stats"]["materialization"]["counts"]["expansionCycles"], 1)


class Origin(unittest.TestCase):
    def test_published_is_library_unpublished_is_local_when_the_export_publishes_anything(self):
        lib = comp("L", "Lib", [text("Lt", "x")])
        loc = comp("C", "Loc", [text("Ct", "y")], published=False)
        env = conv(inst("a", "L"), inst("b", "C", top=50), defs=[lib, loc])
        self.assertEqual(by_id(env)["a"]["component"]["origin"], "library")
        c = by_id(env)["b"]["component"]
        self.assertEqual((c["origin"], c["childrenRole"]), ("local", "fallback"))
        self.assertNotIn("content", c, "анатомия local идёт детьми, а не слотами")

    def test_nothing_published_is_undecided_and_stays_a_black_box(self):
        env = conv(inst("a", "C"), defs=[comp("C", "Loc", [text("Ct", "y")], published=False)])
        c = by_id(env)["a"]["component"]
        self.assertEqual((c["origin"], c["childrenRole"]), ("undecided", "content"))
        self.assertEqual([s["text"] for s in c["content"]], ["y"])

    def test_state_group_key_without_the_group_is_a_missing_base(self):
        env = conv(inst("a", "B", mainStateGroup="gone"), inst("b", "B", top=50),
                   defs=[comp("B", "Button", [text("Bt", "x")])])
        d = env["stats"]["materialization"]["diagnostics"]
        self.assertEqual(d["MISSING_COMPONENT_BASE"], {"count": 1, "examples": [{"id": "a", "name": "Comp"}]})
        self.assertEqual(by_id(env)["a"]["component"]["figmaName"], "Button", "определение найдено по mainComponent")

    def test_unresolved_instance_names_the_missing_piece(self):
        env = conv(inst("a", "ghost"), n("b", "INSTANCE", name="Без ссылки", w=5, h=5, top=50))
        d = env["stats"]["materialization"]["diagnostics"]
        self.assertEqual(d["MISSING_COMPONENT_DEFINITION"]["examples"], [{"id": "a", "name": "Comp"}])
        self.assertEqual(d["MISSING_MAIN_COMPONENT"]["examples"], [{"id": "b", "name": "Без ссылки"}])


# ── размеры и поверхность ─────────────────────────────────────────────────────
class SizedAndPaints(unittest.TestCase):
    def sized(self, **kw):
        env = conv(inst("a", "B", **kw), defs=[comp("B", "Box", [text("Bt", "x")], w=100, h=40)])
        return by_id(env)["a"]["component"].get("sized")

    def test_same_box_is_master(self):
        self.assertEqual(self.sized(w=100, h=40), "master")

    def test_changed_box_is_own(self):
        self.assertEqual(self.sized(w=80, h=40), "own")

    def test_root_override_decides_over_the_stale_node_size(self):
        self.assertEqual(self.sized(w=80, h=40, props=[po("B", width=100, height=40)]), "master")
        self.assertEqual(self.sized(w=100, h=40, props=[po("B", width=56, height=40)]), "own")

    def test_unknown_master_size_is_silence(self):
        env = conv(inst("a", "B"), defs=[comp("B", "Box", [text("Bt", "x")], w=None, h=None)])
        self.assertNotIn("sized", by_id(env)["a"]["component"])

    def test_numeric_size_and_box_take_the_real_geometry(self):
        defs = [comp("B", "Box", [text("Bt", "x")], w=100, h=40)]
        env = conv(inst("a", "B", left=10, top=10, w=100, h=40, props=[po("B", width=56, height=30)]), defs=defs)
        a = by_id(env)["a"]
        self.assertEqual(a["size"], {"w": 56, "h": 30})
        self.assertEqual(a["box"], {"x": 10, "y": 10, "w": 56, "h": 30})

    def paints(self, master, props=()):
        env = conv(inst("a", "B", props=props), defs=[comp("B", "Box", [text("Bt", "x")], **master)])
        return by_id(env)["a"]["component"].get("paints")

    def test_visible_fill_is_a_surface(self):
        self.assertTrue(self.paints({"fillPaints": [BLACK]}))

    def test_invisible_or_transparent_fill_is_not(self):
        self.assertIsNone(self.paints({"fillPaints": [dict(BLACK, visible=False)]}))
        self.assertIsNone(self.paints({"fillPaints": [dict(BLACK, color={"r": 0, "g": 0, "b": 0, "a": 0})]}))
        self.assertIsNone(self.paints({"fillPaints": []}))

    def test_stroke_needs_weight_and_paint(self):
        self.assertTrue(self.paints({"strokePaints": [BLACK], "strokeWeight": 1}))
        self.assertIsNone(self.paints({"strokePaints": [BLACK], "strokeWeight": 0}))
        self.assertIsNone(self.paints({"strokePaints": [], "strokeWeight": 2}))

    def test_root_override_can_add_a_surface_and_an_empty_list_is_not_an_override(self):
        self.assertTrue(self.paints({}, props=[po("B", fillPaints=[BLACK])]))
        self.assertTrue(self.paints({"fillPaints": [BLACK]}, props=[po("B", fillPaints=[])]),
                        "пустой список в записи оверрайда — «не переопределено» (у заголовка шапки образца так)")

    def test_vector_paint_of_the_artwork_is_not_a_surface(self):
        env = conv(inst("a", "I"), defs=[comp("I", "Icon", [n("V", "VECTOR", w=10, h=10, fillPaints=[BLACK])],
                                              w=24, h=24)])
        self.assertNotIn("paints", by_id(env)["a"]["component"])


# ── иконки ────────────────────────────────────────────────────────────────────
class Icons(unittest.TestCase):
    ICON = dict(w=24, h=24)

    def icon_env(self, master_kids=None, published=True, **master):
        kids = master_kids if master_kids is not None else [n("V", "VECTOR", w=10, h=10, left=2, top=2)]
        return conv(inst("a", "I", w=24, h=24), defs=[comp("I", "Close", kids, published=published,
                                                            **dict(self.ICON, **master))])

    def test_icon_only_library_instance_collapses_and_keeps_the_component(self):
        a = by_id(self.icon_env())["a"]
        self.assertEqual(a["kind"], "icon")
        self.assertEqual(a["component"]["figmaName"], "Close")
        self.assertNotIn("children", a)

    def test_label_prevents_the_collapse(self):
        env = self.icon_env([n("V", "VECTOR", w=10, h=10), text("Lt", "Закрыть")])
        self.assertEqual(by_id(env)["a"]["kind"], "instance")

    def test_hidden_label_does_not(self):
        env = self.icon_env([n("V", "VECTOR", w=10, h=10), text("Lt", "Закрыть", visible=False)])
        self.assertEqual(by_id(env)["a"]["kind"], "icon")

    def test_surface_prevents_the_collapse(self):
        env = self.icon_env(fillPaints=[BLACK])
        a = by_id(env)["a"]
        self.assertEqual(a["kind"], "instance")
        self.assertTrue(a["component"]["paints"])

    def test_bigger_than_icon_max_stays_an_instance(self):
        env = conv(inst("a", "I", w=80, h=80), defs=[comp("I", "Logo", [n("V", "VECTOR", w=60, h=60)], w=80, h=80)])
        self.assertEqual(by_id(env)["a"]["kind"], "instance")

    def test_no_artwork_means_no_collapse(self):
        env = self.icon_env([n("R", "RECTANGLE", w=10, h=10)])
        self.assertEqual(by_id(env)["a"]["kind"], "instance")
        self.assertEqual(env["stats"]["emptyInstances"], 1, "пустой resolved-инстанс виден в статистике")

    def test_local_composition_is_not_an_icon(self):
        lib = comp("L", "Lib", [text("Lt", "x")])
        env = conv(inst("a", "I", w=24, h=24), defs=[lib, comp("I", "Local", [n("V", "VECTOR", w=10, h=10)],
                                                              published=False, w=24, h=24)])
        self.assertEqual(by_id(env)["a"]["kind"], "instance")

    def test_icon_counter(self):
        self.assertEqual(self.icon_env()["stats"]["iconsCollapsed"], 1)


# ── анатомия local-композиции ─────────────────────────────────────────────────
class LocalAnatomy(unittest.TestCase):
    def defs(self):
        lib = comp("L", "Button", [text("Lt", "Библиотечный", name="Label")])
        loc = comp("C", "Card", [
            text("Ct", "Заголовок", name="Title"),
            text("Ch", "скрыто", name="Hidden", top=20, visible=False),
            n("Ci", "INSTANCE", name="Btn", mainComponent="L", left=0, top=40, w=100, h=40),
        ], published=False, w=200, h=100)
        return [lib, loc]

    def test_children_carry_instance_owned_ids_and_the_screen_text(self):
        env = conv(inst("s", "C", w=200, h=100, left=10, top=10,
                        props=[po("Ct", nodeText="Мой заголовок"), po("Ci/Lt", nodeText="Купить")]), defs=self.defs())
        ids = by_id(env)
        s = ids["s"]
        self.assertEqual((s["component"]["origin"], s["component"]["childrenRole"]), ("local", "fallback"))
        self.assertEqual([c["id"] for c in s["children"]], ["s;Ct", "s;Ci"])
        self.assertEqual(s["component"]["childrenPath"], ["Ct", "Ci"])
        self.assertEqual(ids["s;Ct"]["text"], "Мой заголовок")
        self.assertNotIn("s;Ch", ids, "скрытое в анатомию не попадает")

    def test_nested_library_instance_stays_a_black_box_with_its_own_content(self):
        env = conv(inst("s", "C", props=[po("Ci/Lt", nodeText="Купить")]), defs=self.defs())
        nested = by_id(env)["s;Ci"]
        self.assertEqual(nested["kind"], "instance")
        self.assertEqual(nested["component"]["origin"], "library")
        self.assertEqual(flat(nested["component"]["content"]), [("s;Ci;Lt", "Купить", True)])
        self.assertNotIn("overridesUnmatched", env["stats"]["materialization"]["counts"])

    def test_no_text_is_lost_in_local_plus_library(self):
        env = conv(inst("s", "C", props=[po("Ct", nodeText="Мой заголовок"), po("Ci/Lt", nodeText="Купить")]),
                   defs=self.defs())
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["Мой заголовок", "Купить"])
        self.assertEqual([t["via"] for t in C.collect_texts(env["root"])], ["node", "content"])

    def test_without_overrides_the_master_texts_are_used(self):
        env = conv(inst("s", "C"), defs=self.defs())
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["Заголовок", "Библиотечный"])

    def test_two_cells_get_separate_anatomy_ids(self):
        env = conv(inst("a", "C"), inst("b", "C", top=120), defs=self.defs())
        ids = by_id(env)
        self.assertTrue({"a;Ct", "b;Ct", "a;Ci", "b;Ci"} <= set(ids))

    def test_instance_with_its_own_children_keeps_them(self):
        node = inst("s", "C", childNode=[text("own", "свой ребёнок")])
        env = conv(node, defs=self.defs())
        self.assertEqual([c["id"] for c in by_id(env)["s"]["children"]], ["own"])

    def test_local_composition_nesting_itself_terminates(self):
        lib = comp("L", "Lib", [text("Lt", "x")])
        loc = comp("C", "Loop", [text("Ct", "виток"), n("Cn", "INSTANCE", mainComponent="C", w=50, h=20, top=20)],
                   published=False, w=200, h=100)
        env = conv(inst("s", "C"), defs=[lib, loc])
        m = env["stats"]["materialization"]
        self.assertEqual(m["diagnostics"]["COMPONENT_EXPANSION_CYCLE"]["count"], 1)
        ids = by_id(env)
        self.assertIn("s;Cn", ids)
        self.assertNotIn("children", ids["s;Cn"], "на цикле анатомия не раскрывается")
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["виток"])

    def test_geometry_override_moves_the_virtual_child(self):
        env = conv(inst("s", "C", props=[po("Ct", top=70)]), defs=self.defs())
        self.assertEqual([c["id"] for c in by_id(env)["s"]["children"]], ["s;Ci", "s;Ct"],
                         "позиция из записи оверрайда меняет порядок по оси")

    def test_validates(self):
        env = conv(inst("s", "C", props=[po("Ct", nodeText="x")]), defs=self.defs())
        self.assertEqual(C.validate(env), [])

    def test_generic_named_root_instance_keeps_the_master_texts(self):
        lib = comp("L", "Page", [text("Lt", "Внутри страницы")])
        doc = {"dsl": {"pixTreeDslNodes": [inst("r", "L", name="Frame 12", w=100, h=40)],
                       "pixComponentTreeDslNodes": [lib]}}
        env = PX.normalize_doc(doc)
        self.assertEqual(env["root"]["kind"], "frame")
        self.assertNotIn("component", env["root"])
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["Внутри страницы"])


class TextProps(unittest.TestCase):
    def test_text_property_assignment_is_a_text_prop_when_there_is_no_content(self):
        env = conv(inst("a", "E", propAssignMap={"t1": {"name": "Label", "type": "text", "value": "Привет"}}),
                   defs=[comp("E", "Empty", [n("R", "RECTANGLE", w=5, h=5)])])
        self.assertEqual(by_id(env)["a"]["component"]["textProps"], {"Label": "Привет"})

    def test_content_replaces_text_props(self):
        env = conv(inst("a", "E", propAssignMap={"t1": {"name": "Label", "type": "text", "value": "Привет"}}),
                   defs=[comp("E", "Has", [text("Et", "в мастере")])])
        c = by_id(env)["a"]["component"]
        self.assertNotIn("textProps", c)
        self.assertEqual([s["text"] for s in c["content"]], ["в мастере"])


# ── окклюзия ──────────────────────────────────────────────────────────────────
def rect(guid, left, top, w, h, fill=None, **kw):
    return n(guid, "RECTANGLE", w=w, h=h, left=left, top=top, fillPaints=[fill or BLACK], **kw)


class Occlusion(unittest.TestCase):
    def occluded(self, *kids):
        env = conv(*kids)
        return sorted(g for g, k in by_id(env).items() if k.get("occluded"))

    def test_later_opaque_sibling_covers_an_earlier_one(self):
        # дерево Pixso «topmost first»: первый в массиве рисуется последним
        self.assertEqual(self.occluded(rect("cover", 0, 0, 400, 400), text("under", "скрыто", left=10, top=10)),
                         ["under"])

    def test_the_covering_node_itself_and_later_drawn_nodes_stay(self):
        self.assertEqual(self.occluded(text("over", "виден", left=10, top=10), rect("cover", 0, 0, 400, 400)), [])

    def test_semi_transparent_backdrop_proves_nothing(self):
        fill = {"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0, "a": 0.5}}
        self.assertEqual(self.occluded(rect("dim", 0, 0, 400, 400, fill), text("under", "x", left=10, top=10)), [])

    def test_node_opacity_below_one_proves_nothing(self):
        self.assertEqual(self.occluded(rect("dim", 0, 0, 400, 400, opacity=0.9), text("under", "x", left=10, top=10)), [])

    def test_partial_cover_proves_nothing(self):
        self.assertEqual(self.occluded(rect("part", 0, 0, 100, 100), text("under", "x", left=90, top=90)), [])

    def test_hidden_or_gradient_cover_proves_nothing(self):
        self.assertEqual(self.occluded(rect("h", 0, 0, 400, 400, visible=False), text("under", "x", left=1, top=1)), [])
        grad = {"type": "GRADIENT_LINEAR", "stops": []}
        self.assertEqual(self.occluded(rect("g", 0, 0, 400, 400, grad), text("under", "x", left=1, top=1)), [])

    def test_a_parent_fill_does_not_hide_its_own_children(self):
        kid = text("kid", "ребёнок", left=5, top=5)
        self.assertEqual(self.occluded(n("p", w=100, h=100, fillPaints=[BLACK], kids=[kid])), [])

    def test_frames_cover_too(self):
        self.assertEqual(self.occluded(n("page", w=400, h=400, fillPaints=[BLACK]),
                                       text("under", "x", left=1, top=1)), ["under"])

    def test_occluded_subtree_is_kept_but_excluded_from_texts(self):
        under = n("under", w=100, h=50, left=10, top=10, kids=[text("t", "под фоном")])
        env = conv(text("vis", "виден", left=300, top=300), rect("cover", 0, 0, 400, 400), under)
        ids = by_id(env)
        self.assertTrue(ids["under"]["occluded"])
        self.assertIn("t", ids, "в дереве остаётся — для глаз ревьюера")
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["виден"])
        self.assertEqual(env["stats"]["occluded"], 1, "флаг ставится на верхушку закрытого поддерева")

    def test_counter(self):
        env = conv(rect("cover", 0, 0, 400, 400), text("a", "x", left=1, top=1), text("b", "y", left=2, top=2))
        self.assertEqual(env["stats"]["occluded"], 2)


if __name__ == "__main__":
    unittest.main()
