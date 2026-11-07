"""test_frame_states.py — кадры-состояния: регионы-дельты, порог схожести, вердикт состояния (задача 6.2, спека §10.1, §13.6, §14.4.6, §20.4, INV-C4, QG-U21, P3).

Пинит на синтетических парах «экран / состояние»: регионы shared / added / removed / changed в порядке документа; changes (вариант, текст, цвет) и consequence (размер, позиция — регион не создают);
порог схожести 0.6 и ширина корней ±1 px (`available: false` с причиной и выходом); триггеры-кандидаты; дельта-корень (секции — регионы, у changed дети — стабы, хеш региона = хеш дельты и
двигается только вместе с ним); outline / section / резолв / план для состояния работают на дельте и отказывают, когда состояние недоступно; `plan.state` (переменная, значения, переключатель);
вердикт состояния по эксклюзивному содержимому против захвата — четыре вердикта и жёсткий гейт при not-reached.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import outline as OL  # noqa: E402
from daiver import states as S  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import verify_compare as VC  # noqa: E402
from test_pixso_pull import synth  # noqa: E402
from test_verify_compare import snapshot  # noqa: E402
from test_verify_state import World  # noqa: E402


def leaf(i, text=None, **kw):
    n = {"id": i, "kind": "text", "name": i, "text": text if text is not None else f"Текст {i}"}
    n.update(kw)
    return n


def frame(i, kids=(), **kw):
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def inst(i, figma, variants=None, slots=None, **kw):
    comp = {"figmaName": figma, "origin": "library", "childrenRole": "content"}
    if variants:
        comp["variants"] = dict(variants)
    if slots:
        comp["content"] = [{"id": f"{i};{n}", "name": n, "text": t, "overridden": True} for n, t in slots]
    n = {"id": i, "kind": "instance", "name": kw.pop("name", i), "component": comp}
    n.update(kw)
    return n


def env(root):
    return C.new_envelope(root, "pixso", {})


def screen_root(prefix="b", width=400):
    p = prefix
    return frame(f"{p}root", [
        inst(f"{p}head", "Modal Header", {"Steps": "OFF"}, [("Header", "Оформление заказа")]),
        frame(f"{p}body", [leaf(f"{p}name", "Имя получателя"), leaf(f"{p}phone", "Телефон для связи"), leaf(f"{p}addr", "Адрес доставки")], layout={"direction": "column", "gap": 8}),
        inst(f"{p}save", "Modal Action", {"Variant": "Default"}, [("Label", "Сохранить заказ")]),
        leaf(f"{p}help", "Нужна помощь? Позвоните нам"),
    ], layout={"direction": "column", "gap": 12}, size={"w": width, "h": "hug"})


def error_state(prefix="s"):
    """Состояние «ошибка»: добавлен блок с ошибкой, кнопка в другом варианте, текст помощи убран, у шапки изменился цвет."""
    p = prefix
    return frame(f"{p}root", [
        inst(f"{p}head", "Modal Header", {"Steps": "OFF"}, [("Header", "Оформление заказа")], style={"background": "rgba(255, 0, 0, 1)"}),
        frame(f"{p}body", [leaf(f"{p}name", "Имя получателя"), frame(f"{p}err", [leaf(f"{p}errt", "Укажите телефон получателя")], name="Error block"),
                           leaf(f"{p}phone", "Телефон для связи"), leaf(f"{p}addr", "Адрес доставки")], layout={"direction": "column", "gap": 8}),
        inst(f"{p}save", "Modal Action", {"Variant": "Disabled"}, [("Label", "Сохранить заказ")]),
    ], layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"})


def compare(state=None, screen=None):
    return S.compare(env(screen or screen_root()), env(state or error_state()), "main", "error")


def by_kind(res, kind):
    return [r for r in res["regions"] if r["kind"] == kind]


class Regions(unittest.TestCase):
    def test_a_state_of_the_same_screen_splits_into_added_removed_changed_and_shared(self):
        res = compare()
        self.assertTrue(res["available"], res.get("reason"))
        self.assertGreaterEqual(res["similarity"], S.SAME_SCREEN_SIMILARITY)
        added = by_kind(res, "added")
        self.assertEqual([r["nodeId"] for r in added], ["serr"], "блок ошибки — поддерево, которого на экране нет")
        self.assertEqual(added[0]["parentId"], "sbody")
        removed = by_kind(res, "removed")
        self.assertEqual([(r["baseId"], r["parentId"]) for r in removed], [("bhelp", "sroot")], "текст помощи экран рисует, состояние — нет")
        changed = {r["nodeId"]: r for r in by_kind(res, "changed")}
        self.assertEqual(set(changed), {"shead", "ssave"})
        self.assertEqual(changed["ssave"]["baseId"], "bsave")
        self.assertEqual(changed["ssave"]["changes"], [{"kind": "variant", "prop": "Variant", "from": "Default", "to": "Disabled"}])
        self.assertEqual(changed["shead"]["changes"][0]["kind"], "color")
        shared = {r["nodeId"]: r["baseId"] for r in by_kind(res, "shared")}
        self.assertEqual(shared, {"sname": "bname", "sphone": "bphone", "saddr": "baddr"}, "чистые поддеревья — стабы с id узла экрана")
        self.assertEqual(res["counts"], {"shared": 3, "added": 1, "removed": 1, "changed": 2})

    def test_regions_follow_the_document_order_of_the_state(self):
        order = [r["nodeId"] for r in compare()["regions"] if r["kind"] != "removed"]
        self.assertEqual(order, ["shead", "sname", "serr", "sphone", "saddr", "ssave"])

    def test_text_changes_are_named(self):
        st = error_state()
        st["children"][1]["children"][0]["text"] = "Получатель"
        res = compare(st)
        ch = {r["nodeId"]: r for r in by_kind(res, "changed")}["sname"]
        self.assertEqual(ch["changes"], [{"kind": "text", "prop": "text", "from": "Имя получателя", "to": "Получатель"}])

    def test_slot_texts_of_an_instance_are_content_too(self):
        st = error_state()
        st["children"][2]["component"]["content"][0]["text"] = "Подождите"
        ch = {r["nodeId"]: r for r in by_kind(compare(st), "changed")}["ssave"]
        self.assertIn({"kind": "text", "prop": "slots", "from": ["сохранить заказ"], "to": ["подождите"]}, ch["changes"])

    def test_size_and_position_changes_are_consequences_and_make_no_region(self):
        st = error_state()
        st["children"][1]["size"] = {"w": "fill", "h": 300}
        st["children"][3 - 1]["box"] = {"x": 0, "y": 400, "w": 100, "h": 40}
        res = compare(st)
        self.assertNotIn("sbody", {r["nodeId"] for r in by_kind(res, "changed")}, "тело изменилось только по высоте — это следствие содержимого")
        cons = {(c["nodeId"], c["kind"]) for c in res["consequences"]}
        self.assertIn(("sbody", "size"), cons)
        self.assertTrue(all(c["consequence"] is True for c in res["consequences"]))

    def test_an_identical_state_has_no_changes_at_all(self):
        res = S.compare(env(screen_root()), env(screen_root("s")), "main", "same")
        self.assertTrue(res["available"])
        self.assertEqual(res["counts"], {"shared": 1, "added": 0, "removed": 0, "changed": 0}, "весь кадр — один стаб")
        self.assertEqual(res["_delta"]["children"], [])

    def test_the_result_is_deterministic(self):
        a = json.dumps(S.public(compare()), ensure_ascii=False, sort_keys=True)
        b = json.dumps(S.public(compare()), ensure_ascii=False, sort_keys=True)
        self.assertEqual(a, b)


class Availability(unittest.TestCase):
    def test_another_screen_is_not_a_state(self):
        other = frame("oroot", [leaf("o1", "Каталог товаров"), leaf("o2", "Фильтры слева"), leaf("o3", "Сортировка по цене"), leaf("o4", "Корзина пуста")],
                      layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"})
        res = compare(other)
        self.assertFalse(res["available"])
        self.assertIn("не состояние экрана «main»", res["reason"])
        self.assertIn("схожесть кадров", res["reason"])
        self.assertIn("stateOf", res["reason"], "причина с выходом: чинить stateOf или завести кадр как экран")
        self.assertEqual((res["regions"], res["triggers"]), ([], []))

    def test_roots_of_a_different_width_are_not_one_screen(self):
        res = compare(error_state(), screen_root(width=402))
        self.assertFalse(res["available"])
        self.assertIn("корни разной ширины", res["reason"])
        self.assertTrue(compare(error_state(), screen_root(width=401))["available"], "допуск ±1 px")

    def test_the_similarity_threshold_is_the_spec_one(self):
        self.assertEqual(S.SAME_SCREEN_SIMILARITY, 0.6)
        self.assertEqual(S.WIDTH_TOLERANCE, 1)

    def test_a_state_that_adds_a_lot_is_still_a_state_of_the_screen(self):
        """Схожесть — лучшее из двух направлений: экран целиком найден в состоянии, хотя добавленное в экране не нашлось."""
        st = error_state()
        st["children"].append(frame("sbig", [leaf(f"sx{i}", f"Совершенно новый блок номер {i} для состояния") for i in range(12)], layout={"direction": "column", "gap": 4}))
        res = compare(st)
        self.assertTrue(res["available"], res.get("reason"))
        self.assertLess(res["similarity"], 1.01)

    def test_a_state_that_only_removes_content_is_still_a_state(self):
        st = frame("sroot", [inst("shead", "Modal Header", {"Steps": "OFF"}, [("Header", "Оформление заказа")]),
                             frame("sbody", [leaf("sname", "Имя получателя"), leaf("sphone", "Телефон для связи"), leaf("saddr", "Адрес доставки")], layout={"direction": "column", "gap": 8}),
                             inst("ssave", "Modal Action", {"Variant": "Default"}, [("Label", "Сохранить заказ")])], layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"})
        res = compare(st)
        self.assertTrue(res["available"])
        self.assertEqual((res["counts"]["added"], res["counts"]["removed"]), (0, 1))


class Triggers(unittest.TestCase):
    def test_a_changed_variant_is_the_strongest_trigger_then_controls(self):
        res = compare()
        ids = [t["id"] for t in res["triggers"]]
        self.assertEqual(ids[0], "bsave", "вариант кнопки в состоянии другой")
        self.assertIn("Variant", res["triggers"][0]["why"])
        self.assertEqual(len(ids), len(set(ids)), "без повторов")

    def test_controls_by_component_name_are_candidates_when_nothing_changed_variant(self):
        sc = screen_root()
        sc["children"].append(inst("btab", "Tabs Item", {"Active": "Y"}, [("Label", "Вкладка")]))
        st = error_state()
        res = S.compare(env(sc), env(st), "main", "error")
        self.assertIn("btab", [t["id"] for t in res["triggers"]])
        self.assertNotIn("bhead", [t["id"] for t in res["triggers"]], "инстанс, не похожий на управляющий, переключателем не предлагается")


class DeltaRoot(unittest.TestCase):
    def test_sections_are_the_added_and_changed_regions_and_children_of_changed_are_stubs(self):
        d = compare()["_delta"]
        self.assertTrue(d["deltaRoot"])
        self.assertTrue(d["id"].startswith(S.DELTA_PREFIX))
        self.assertEqual([c["id"] for c in d["children"]], ["shead", "serr", "ssave"])
        head = d["children"][0]
        self.assertEqual(head["delta"]["kind"], "changed")
        self.assertEqual(head["delta"]["baseId"], "bhead")
        added = d["children"][1]
        self.assertEqual(added["delta"], {"kind": "added", "parentId": "sbody", "afterId": "sname"})
        self.assertEqual([k["id"] for k in added["children"]], ["serrt"], "добавленное поддерево целиком")
        self.assertEqual([r["baseId"] for r in d["removed"]], ["bhelp"])
        self.assertEqual(sorted(x["stateId"] for x in d["shared"]), ["saddr", "sname", "sphone"])

    def test_changed_container_keeps_stubs_instead_of_shared_children(self):
        st = error_state()
        st["children"][1]["style"] = {"background": "rgba(1, 2, 3, 1)"}
        d = compare(st)["_delta"]
        body = next(c for c in d["children"] if c["id"] == "sbody")
        self.assertTrue(all(k["id"].startswith(S.STUB_PREFIX) for k in body["children"]), [k["id"] for k in body["children"]])
        self.assertEqual([k["ref"] for k in body["children"]], ["sname", "serr", "sphone", "saddr"])

    def test_the_hash_of_a_region_moves_only_with_its_own_delta(self):
        a = {r["id"]: r["hash"] for r in compare()["regions"]}
        st = error_state()
        st["children"][1]["children"][1]["children"][0]["text"] = "Укажите ДРУГОЙ телефон"
        b = {r["id"]: r["hash"] for r in compare(st)["regions"]}
        self.assertNotEqual(a["added:serr"], b["added:serr"])
        for k in ("changed:shead", "changed:ssave", "removed:bhelp", "shared:sname"):
            self.assertEqual(a[k], b[k], k)

    def test_a_changed_container_without_a_layout_does_not_become_an_unproven_group(self):
        """Дети changed-узла — стабы, а не абсолютные потомки: «absolute-children» по ним не репортится; сам синтетический корень дельты — тоже не рамка с абсолютными детьми."""
        st = error_state()
        body = st["children"][1]
        del body["layout"]
        body["style"] = {"background": "rgba(1, 2, 3, 1)"}
        d = compare(st)["_delta"]
        self.assertEqual(OL.unproven_entries(d), [])
        sc = screen_root()
        del sc["children"][1]["layout"]
        d2 = S.compare(env(sc), env(st), "main", "error")["_delta"]
        self.assertEqual(OL.unproven_entries(d2), [], "стабы детей не считаются детьми")

    def test_the_source_trees_are_not_mutated(self):
        sc, st = screen_root(), error_state()
        before = copy.deepcopy((sc, st))
        S.compare(env(sc), env(st), "main", "error")
        self.assertEqual((sc, st), before)


# ── вердикт состояния ────────────────────────────────────────────────────────

def page(root):
    """Страница как модель `dom`: тот же канон с id `dom:…` (матчеру важна только форма)."""
    return env(root)


def report(state_page, state_tree=None, screen=None):
    res = S.compare(env(screen or screen_root()), env(state_tree or error_state()), "main", "error")
    return VC.build_report(env(state_tree or error_state()), page(state_page), None, None, None, None, [], set(), None, None, res)


class Verdict(unittest.TestCase):
    def test_reached_when_the_page_shows_the_state_content_and_not_the_screens(self):
        rep = report(error_state("p"))
        self.assertEqual(rep["state"]["verdict"], "reached")
        self.assertGreater(rep["state"]["stateFound"], 0)
        self.assertEqual(rep["state"]["screenFound"], 0)
        self.assertNotEqual(rep["status"], "hard-gate-fail")

    def test_not_reached_when_the_page_shows_the_screen_instead_is_a_hard_gate_with_the_recipe_as_the_cause(self):
        rep = report(screen_root("p"))
        self.assertEqual(rep["state"]["verdict"], "not-reached")
        self.assertEqual(rep["status"], "hard-gate-fail")
        self.assertFalse(rep["hardGate"]["pass"])
        self.assertTrue(rep["hardGate"]["stateNotReached"])
        self.assertIn("рецепте открытия", rep["hardGate"]["reason"])
        self.assertEqual(rep["defects"], [], "дефекты не показываются: страница не в том состоянии")
        self.assertIn("состояние НЕ достигнуто", rep["nextStep"])
        self.assertIn("open", rep["nextStep"])

    def test_ambiguous_when_both_sides_exclusive_content_is_on_the_page(self):
        both = error_state("p")
        both["children"].append(leaf("phelp", "Нужна помощь? Позвоните нам"))
        rep = report(both)
        self.assertEqual(rep["state"]["verdict"], "ambiguous")
        self.assertIn("actual.png", rep["state"]["reason"])
        self.assertNotEqual(rep["status"], "hard-gate-fail", "ambiguous жёстким гейтом не становится")
        self.assertIn("ambiguous", rep["nextStep"])

    def test_ambiguous_when_neither_sides_exclusive_content_is_on_the_page(self):
        bare = frame("proot", [inst("phead", "Modal Header", {"Steps": "OFF"}, [("Header", "Оформление заказа")]),
                               frame("pbody", [leaf("pname", "Имя получателя"), leaf("pphone", "Телефон для связи"), leaf("paddr", "Адрес доставки")], layout={"direction": "column", "gap": 8}),
                               inst("psave", "Modal Action", {"Variant": "Default"}, [("Label", "Сохранить заказ")])], layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"})
        self.assertEqual(report(bare)["state"]["verdict"], "ambiguous")

    def test_half_of_the_state_content_is_enough_to_see_the_state(self):
        st = error_state()
        st["children"][1]["children"][1]["children"].append(leaf("serrt2", "Проверьте индекс города"))
        page_tree = error_state("p")
        rep = report(page_tree, st)
        self.assertEqual(rep["state"]["stateExclusive"], 2)
        self.assertEqual(rep["state"]["stateFound"], 1, "на странице нашёлся один из двух эксклюзивных текстов состояния")
        self.assertEqual(rep["state"]["verdict"], "reached")

    def test_unknown_when_the_state_has_no_exclusive_content(self):
        st = screen_root("s")
        st["children"][2]["component"]["variants"] = {"Variant": "Disabled"}
        rep = report(screen_root("p"), st)
        self.assertEqual(rep["state"]["verdict"], "unknown")
        self.assertIn("эксклюзивного содержимого", rep["state"]["reason"])
        self.assertNotEqual(rep["status"], "hard-gate-fail")

    def test_unknown_when_the_state_is_not_available(self):
        other = frame("oroot", [leaf("o1", "Каталог товаров"), leaf("o2", "Фильтры слева"), leaf("o3", "Сортировка по цене"), leaf("o4", "Корзина пуста")],
                      layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"})
        res = S.compare(env(screen_root()), env(other), "main", "error")
        v = S.verdict(res, {"pairs": []}, other)
        self.assertEqual(v["verdict"], "unknown")
        self.assertIn("схожесть", v["reason"])

    def test_a_state_that_only_removes_content_is_reached_when_the_removed_content_is_gone(self):
        st = frame("sroot", [inst("shead", "Modal Header", {"Steps": "OFF"}, [("Header", "Оформление заказа")]),
                             frame("sbody", [leaf("sname", "Имя получателя"), leaf("sphone", "Телефон для связи"), leaf("saddr", "Адрес доставки")], layout={"direction": "column", "gap": 8}),
                             inst("ssave", "Modal Action", {"Variant": "Default"}, [("Label", "Сохранить заказ")])], layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"})
        gone = report(st, st)
        self.assertEqual(gone["state"]["verdict"], "reached")
        shown = report(screen_root("p"), st)
        self.assertEqual(shown["state"]["verdict"], "not-reached", "убранное на месте — страница показывает экран")

    def test_only_confident_pairs_count_as_evidence(self):
        res = S.compare(env(screen_root()), env(error_state()), "main", "error")
        ex = res["_exclusive"]
        low = {"pairs": [{"design": i, "impl": "x", "confidence": "low", "anchor": "pos"} for i in ex["state"]]}
        high = {"pairs": [{"design": i, "impl": "x", "confidence": "high", "anchor": "text"} for i in ex["state"]]}
        bare = frame("proot", [leaf("pn", "Совсем другое содержимое страницы")], size={"w": 400, "h": 10})
        self.assertEqual(S.verdict(res, low, bare)["stateFound"], 0, "неуверенные пары (low) свидетельством не считаются")
        self.assertEqual(S.verdict(res, high, bare)["stateFound"], len(ex["state"]))

    def test_a_changed_text_is_exclusive_to_each_side(self):
        sc = screen_root()
        st = copy.deepcopy(sc)
        st["children"][1]["children"][0]["text"] = "Получатель заказа"
        st = S.compare(env(sc), env(st), "main", "ok")
        ex = st["_exclusive"]
        self.assertEqual((ex["state"], ex["screen"]), (["bname"], ["bname"]), "в состоянии у узла новый текст, на экране — старый")


# ── по файлам трека: инструмент, outline / section, резолв, план ─────────────

class OnDisk(World):
    def setUp(self):
        super().setUp()
        self.TB.set_canon(self.root, screen_root())
        (self.root / "e.json").write_text(json.dumps(synth(1)), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": self.TB.TRACK, "dslPath": "e.json", "frameUrl": "https://pixso.example/app/design/abc?item-id=9:9", "frame": "error"})
        self.set_state_canon(error_state())
        T.call("daiver_track_manifest", self.root, {"track": self.TB.TRACK, "frame": "error", "set": {"role": "state", "stateOf": "main"}})

    def set_state_canon(self, node):
        m = TR.load(self.root, self.TB.TRACK)
        paths = TR.frame_paths(self.root, m, "error")
        paths["requirement"].write_text(json.dumps({"dsl": "error"}), encoding="utf-8")
        f = TR.get_frame(m, "error")
        f["requirement"]["sha256"] = TR.sha256_file(paths["requirement"])
        C.write_canonical(paths["canonical"], C.new_envelope(node, "pixso", {"sourceSha256": f["requirement"]["sha256"]}))
        TR.save(self.root, m)

    def call(self, name, **args):
        return T.call(name, self.root, dict({"track": self.TB.TRACK, "frame": "error"}, **args))

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            self.call(name, **args)
        return str(cm.exception)

    def test_the_tool_reports_regions_triggers_and_a_next_step(self):
        r = self.call("daiver_frame_states")
        self.assertTrue(r["available"], r.get("reason"))
        self.assertEqual((r["screen"], r["state"], r["frame"]), ("main", "error", "error"))
        self.assertEqual(r["counts"], {"shared": 3, "added": 1, "removed": 1, "changed": 2})
        self.assertEqual(r["triggers"][0]["id"], "bsave")
        self.assertIn("plan.state", r["next"])
        self.assertFalse([k for k in r if k.startswith("_")], "внутренние поля наружу не идут")

    def test_the_tool_names_why_a_state_is_unavailable(self):
        self.set_state_canon(frame("oroot", [leaf("o1", "Каталог товаров"), leaf("o2", "Фильтры слева"), leaf("o3", "Сортировка по цене"), leaf("o4", "Корзина пуста")],
                                   layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"}))
        r = self.call("daiver_frame_states")
        self.assertFalse(r["available"])
        self.assertIn("схожесть", r["reason"])
        self.assertIn("состояние недоступно", r["next"])

    def test_not_a_state_and_unknown_screens_are_named(self):
        self.assertIn("не состояние", self.refusal("daiver_frame_states", frame="main"))
        m = TR.load(self.root, self.TB.TRACK)
        TR.get_frame(m, "error")["stateOf"] = "нет-такого"                 # манифест такого не пропустит (проверка при записи) — но разбор файла руками испорченного трека не падает
        r = S.analyze(self.root, m, "error")
        self.assertFalse(r["available"])
        self.assertIn("«нет-такого», которого в треке нет", r["reason"])
        TR.get_frame(m, "error")["stateOf"] = "error"
        self.assertIn("самого себя", S.analyze(self.root, m, "error")["reason"])
        with self.assertRaises(T.ToolError):
            T.call("daiver_frame_states", self.root, {"track": "NOPE-1", "frame": "x"})

    def test_a_screen_without_a_canon_is_named(self):
        m = TR.load(self.root, self.TB.TRACK)
        TR.frame_paths(self.root, m, "main")["canonical"].unlink()
        r = self.call("daiver_frame_states")
        self.assertFalse(r["available"])
        self.assertIn("канона кадра «main»", r["reason"])

    def test_outline_and_section_of_a_state_are_the_delta_regions(self):
        o = self.call("daiver_outline")
        self.assertEqual([s["id"] for s in o["sections"]], ["shead", "serr", "ssave"], "секции — регионы added и changed")
        self.assertEqual(o["state"]["screen"], "main")
        self.assertEqual(o["unproven"], [], "у синтетического корня дельты раскладку решать не нужно")
        self.assertEqual([r["baseId"] for r in o["state"]["removed"]], ["bhelp"])
        sec = self.call("daiver_section", sectionId="ssave")
        regions = {r["nodeId"]: r for r in self.call("daiver_frame_states")["regions"]}
        self.assertEqual(sec["section"]["hash"], regions["ssave"]["hash"], "хеш секции = хеш дельты региона")
        self.assertEqual(sec["state"]["region"]["kind"], "changed")
        self.assertEqual(sec["state"]["region"]["baseId"], "bsave")
        self.assertIn("ВНУТРЬ модуля экрана", sec["next"])
        self.assertEqual(sec["ancestors"], [], "синтетический корень дельты в предки не попадает")
        added = self.call("daiver_section", sectionId="serr")
        self.assertEqual(added["state"]["region"], {"kind": "added", "parentId": "sbody", "afterId": "sname"})
        self.assertEqual([k["id"] for k in added["node"]["children"]], ["serrt"])

    def test_a_heavy_added_region_is_cut_by_the_budget_and_keeps_its_region(self):
        o = self.call("daiver_outline", weightBudget=200)
        ids = [x["id"] for x in o["sections"]]
        self.assertIn("serrt", ids, "поддерево тяжелее бюджета режется по обычному правилу")
        self.assertNotIn("serr", ids)
        sec = self.call("daiver_section", sectionId="serrt", weightBudget=200)
        self.assertEqual(sec["state"]["region"]["kind"], "added")
        self.assertEqual(sec["state"]["region"]["regionRoot"], "serr")

    def test_the_query_of_a_changed_node_carries_the_verdict_the_screen_gave_it(self):
        T.call("daiver_resolve_finalize", self.root, {"track": self.TB.TRACK, "frame": "main", "verdict": {
            "nodeId": "bsave", "decision": "local", "confidence": "high", "rationale": "кнопка экрана — свой компонент"}})
        q = self.call("daiver_resolve_query", nodeId="ssave")
        sv = q["screenVerdict"]
        self.assertEqual((sv["frame"], sv["nodeId"], sv["decision"]), ("main", "bsave", "local"))
        self.assertEqual(sv["changes"], [{"kind": "variant", "prop": "Variant", "from": "Default", "to": "Disabled"}])
        self.assertIn("тот же", sv["hint"])
        self.assertNotIn("screenVerdict", self.call("daiver_resolve_query", nodeId="serr"), "добавленный регион на экране не существовал")
        self.assertNotIn("recorded", q, "подсказка вердикт не пишет")

    def flat(self):
        sc = frame("broot", [leaf("bn", "Имя получателя"), leaf("bp", "Телефон для связи"), leaf("ba", "Адрес доставки"), leaf("bh", "Нужна помощь? Позвоните нам")],
                   layout={"direction": "column", "gap": 8}, size={"w": 400, "h": "hug"})
        st = frame("sroot", [leaf("sn", "Имя получателя"), leaf("se", "Укажите телефон получателя"), leaf("sp", "Телефон для связи"), leaf("sa", "Адрес доставки")],
                   layout={"direction": "column", "gap": 8}, size={"w": 400, "h": "hug"})
        self.TB.set_canon(self.root, sc)
        self.set_state_canon(st)

    def compare(self, texts):
        it = TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "error")["verify"] / "iter-1"
        it.mkdir(parents=True, exist_ok=True)
        (it / "snapshot.json").write_text(json.dumps(snapshot(texts)), encoding="utf-8")
        return self.call("daiver_verify_compare", iterationDir=it.relative_to(self.root).as_posix()), json.loads((it / "compare-summary.json").read_text(encoding="utf-8"))

    def test_the_compare_tool_writes_the_state_verdict_and_a_not_reached_state_is_a_hard_gate(self):
        self.flat()
        out, summary = self.compare(["Имя получателя", "Укажите телефон получателя", "Телефон для связи", "Адрес доставки"])
        self.assertEqual(out["state"]["verdict"], "reached")
        self.assertEqual(summary["state"]["verdict"], "reached")
        self.assertNotEqual(out["status"], "hard-gate-fail")
        it = TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "error")["verify"] / "iter-2"
        it.mkdir(parents=True)
        (it / "snapshot.json").write_text(json.dumps(snapshot(["Имя получателя", "Телефон для связи", "Адрес доставки", "Нужна помощь? Позвоните нам"])), encoding="utf-8")
        out = self.call("daiver_verify_compare", iterationDir=it.relative_to(self.root).as_posix())
        self.assertEqual(out["state"]["verdict"], "not-reached")
        self.assertEqual(out["status"], "hard-gate-fail")
        self.assertEqual(out["defects"], [])
        self.assertIn("рецепт", out["nextStep"])
        written = json.loads((it / "compare-summary.json").read_text(encoding="utf-8"))
        self.assertTrue(written["hardGate"]["stateNotReached"])
        self.assertEqual(json.loads((it / "defects.json").read_text(encoding="utf-8"))["defects"], [])

    def test_an_unavailable_state_does_not_break_the_compare_it_says_unknown(self):
        self.set_state_canon(frame("oroot", [leaf("o1", "Каталог товаров"), leaf("o2", "Фильтры слева"), leaf("o3", "Сортировка по цене"), leaf("o4", "Корзина пуста")],
                                   layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"}))
        out, _ = self.compare(["Каталог товаров", "Фильтры слева", "Сортировка по цене", "Корзина пуста"])
        self.assertEqual(out["state"]["verdict"], "unknown")
        self.assertIn("схожесть", out["state"]["reason"])

    def test_a_screen_frame_has_no_state_verdict(self):
        it = TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "main")["verify"] / "iter-1"
        it.mkdir(parents=True)
        (it / "snapshot.json").write_text(json.dumps(snapshot(["Имя получателя"])), encoding="utf-8")
        out = T.call("daiver_verify_compare", self.root, {"track": self.TB.TRACK, "frame": "main", "iterationDir": it.relative_to(self.root).as_posix()})
        self.assertNotIn("state", out)

    def test_the_build_state_of_a_state_frame_follows_the_regions_and_goes_stale_with_one(self):
        self.finish_resolve()
        self.write_plan(self.good())
        r = self.call("daiver_build_state")
        self.assertEqual(r["next"]["sectionId"], "shead")
        self.assertEqual(r["progress"], "0/3")
        T.call("daiver_build_state_set", self.root, {"track": self.TB.TRACK, "frame": "error", "sectionId": "shead", "status": "in_progress"})
        # правится только блок ошибки — хеш ТОЛЬКО его региона меняется
        st = error_state()
        st["children"][1]["children"][1]["children"][0]["text"] = "Укажите другой телефон"
        self.set_state_canon(st)
        with self.assertRaises(T.ToolError) as cm:
            self.call("daiver_build_state")
        self.assertIn("план не соответствует макету", str(cm.exception))
        self.assertIn("serr", str(cm.exception), "стухает регион, который подвинулся")
        self.assertNotIn("shead", str(cm.exception).split("хеш изменился у секций")[-1].split(")")[0])

    def test_the_section_of_a_shared_part_is_refused(self):
        self.assertIn("sname", self.refusal("daiver_section", sectionId="sname"))

    def test_an_unavailable_state_refuses_outline_section_and_resolve_with_the_reason(self):
        self.set_state_canon(frame("oroot", [leaf("o1", "Каталог товаров"), leaf("o2", "Фильтры слева"), leaf("o3", "Сортировка по цене"), leaf("o4", "Корзина пуста")],
                                   layout={"direction": "column", "gap": 12}, size={"w": 400, "h": "hug"}))
        for tool, extra in (("daiver_outline", {}), ("daiver_section", {"sectionId": "o1"}), ("daiver_resolve_candidates", {})):
            msg = self.refusal(tool, **extra)
            self.assertIn("состояние недоступно", msg, tool)
            self.assertIn("схожесть", msg, tool)

    def test_resolve_candidates_exist_only_inside_the_regions(self):
        r = self.call("daiver_resolve_candidates")
        text = " ".join(r["candidates"])
        self.assertIn("shead", text)
        self.assertIn("ssave", text)
        self.assertEqual(r["stats"]["candidates"], 3, r["candidates"])        # shead, ssave (changed) и блок ошибки (added, осмысленное имя)

    def finish_resolve(self):
        from daiver import resolve as RS
        for frame in ("main", "error"):
            for c in RS.Ctx(self.root, self.TB.TRACK, frame).sc.cands:
                T.call("daiver_resolve_finalize", self.root, {"track": self.TB.TRACK, "frame": frame, "verdict": {
                    "nodeId": c.id, "decision": "local", "confidence": "high", "rationale": "в тесте состояния"}})
        T.call("daiver_resolve_report", self.root, {"track": self.TB.TRACK})

    def write_plan(self, plan=None):
        return self.call("daiver_plan_write", plan=plan or {}, weightBudget=self.TB.BIG_BUDGET)

    def good(self, **kw):
        return {"state": dict({"variable": "orderState", "screenValue": "idle", "value": "error", "toggle": {"trigger": "bsave"}}, **kw)}

    def test_a_state_plan_needs_the_state_decision_and_names_the_triggers(self):
        self.finish_resolve()
        msg = self.refusal("daiver_plan_write", plan={})
        self.assertIn("plan.state обязателен", msg)
        self.assertIn("bsave", msg, "кандидаты-переключатели названы в отказе")

    def test_a_state_plan_records_variable_values_toggle_and_the_delta(self):
        self.finish_resolve()
        self.write_plan(self.good())
        plan = json.loads((TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "error")["plan"]).read_text(encoding="utf-8"))
        self.assertEqual(plan["state"], {"screen": "main", "variable": "orderState", "values": {"screen": "idle", "state": "error"},
                                         "toggle": {"kind": "trigger", "id": "bsave", "name": "bsave"}})
        self.assertEqual([r["baseId"] for r in plan["delta"]["removed"]], ["bhelp"])
        self.assertEqual(sorted(x["stateId"] for x in plan["delta"]["shared"]), ["saddr", "sname", "sphone"])
        self.assertEqual([s["id"] for s in plan["sections"]], ["shead", "serr", "ssave"], "секции плана — регионы")
        self.assertEqual([b["id"] for b in plan["buildOrder"]], ["shead", "serr", "ssave"])
        md = (TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "error")["planMd"]).read_text(encoding="utf-8")
        self.assertIn("## Состояние экрана", md)
        self.assertIn("`orderState`", md)
        self.assertIn("orderState === 'error'", md)
        self.assertIn("`bhelp`", md)
        self.assertNotIn("## Оболочка над секциями", md)
        self.assertEqual(plan["pins"]["rootHash"], C.subtree_hash(S.analyze(self.root, TR.load(self.root, self.TB.TRACK), "error")["_delta"]))

    def test_a_named_toggle_is_accepted_and_a_foreign_trigger_is_not(self):
        self.finish_resolve()
        self.write_plan(self.good(toggle={"named": "  кнопка «Оформить»  в шапке "}))
        plan = json.loads((TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "error")["plan"]).read_text(encoding="utf-8"))
        self.assertEqual(plan["state"]["toggle"], {"kind": "named", "name": "кнопка «Оформить» в шапке"})
        self.assertIn("нет среди переключателей-кандидатов", self.refusal("daiver_plan_write", plan=self.good(toggle={"trigger": "bhelp"})))

    def test_bad_state_decisions_are_refused_with_all_reasons(self):
        self.finish_resolve()
        for bad, needle in (({"variable": "Order State"}, "plan.state.variable"), ({"value": "idle"}, "value и screenValue одинаковы"), ({"value": ""}, "plan.state.value"),
                            ({"toggle": {"trigger": "bsave", "named": "x"}}, "plan.state.toggle"), ({"toggle": {}}, "plan.state.toggle"), ({"extra": 1}, "неизвестное поле")):
            self.assertIn(needle, self.refusal("daiver_plan_write", plan=self.good(**bad)), bad)
        self.assertIn("ожидается объект", self.refusal("daiver_plan_write", plan={"state": "yes"}))

    def test_state_is_refused_for_a_frame_that_is_not_a_state(self):
        self.finish_resolve()
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_plan_write", self.root, {"track": self.TB.TRACK, "frame": "main", "plan": self.good(), "weightBudget": self.TB.BIG_BUDGET})
        self.assertIn("поле только у кадра-состояния", str(cm.exception))

    def test_a_hand_edit_of_the_state_decision_makes_the_plan_stale(self):
        self.finish_resolve()
        self.write_plan(self.good())
        m = TR.load(self.root, self.TB.TRACK)
        p = TR.frame_paths(self.root, m, "error")["plan"]
        data = json.loads(p.read_text(encoding="utf-8"))
        data["state"]["variable"] = "other"
        p.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        st = T.call("daiver_plan_contract", self.root, {"track": self.TB.TRACK})
        self.assertTrue([o for o in st["open"] if o["id"] == "plan" and o.get("frame") == "error"], st["open"])

    def test_the_state_of_a_primary_screen_plan_is_unchanged_by_the_second_frame(self):
        self.assertNotIn("state", json.loads(TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "main")["plan"].read_text(encoding="utf-8")))

    def test_compare_of_a_state_frame_reports_the_verdict_in_the_summary(self):
        it = TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), "error")["verify"] / "iter-1"
        it.mkdir(parents=True)
        snap = {"v": 1, "url": "http://x", "title": "", "elements": 40, "root": None}
        self.assertEqual(snap["v"], 1)           # снимок страницы строит браузер (4.2): здесь проверяется только сводка через build_report
        sc = S.analyze(self.root, TR.load(self.root, self.TB.TRACK), "error")
        self.assertTrue(sc["available"])
        self.assertEqual(S.verdict(sc, {"pairs": []}, error_state("p"))["verdict"], "ambiguous", "без уверенных пар (матчер ничего не спарил) вердикт не reached")


if __name__ == "__main__":
    unittest.main()
