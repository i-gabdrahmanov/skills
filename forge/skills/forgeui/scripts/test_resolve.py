#!/usr/bin/env python3
"""test_resolve.py — firewall резолва: кандидаты, daiver_resolve_query, daiver_resolve_finalize (задача 2.4; спека §10.1,
§10.2, §10.4, §20.4; QG S11; INV A7, B23, A2, A3).

Пинит именно то, что резолв не должен видеть или делать, — как свойства КОДА, а не текста брифа:
  • проекция кандидата собрана из белого списка: в «ядовитом» каноне (в каждом небелом поле своя метка) ни одной метки нет в
    ответе — ни геометрии, ни стилей, ни шрифтов, ни цветов, ни токенов, ни ЗНАЧЕНИЙ слотов, ни предков и соседей, ни id
    потомков; текст не длиннее 40, дерево — глубина 2 и не больше 48 узлов с кандидатом, обрезка названа (`truncated`,
    `hidden`, «… +N»), отпечаток поддерева по kind считает ВСЁ поддерево, ответ остаётся малой долей поддерева и укладывается в
    потолок размера при любом входе; неизвестный id и не-кандидат — отказ с причиной;
  • карта встроена в ответ и не глотается: hit даёт вердикт high, битая карта останавливает резолв (а не «как без карты»);
  • вердикт проходит схему (матрица нарушений, все — одним сообщением), узел обязан быть кандидатом кадра, вердикт при hit
    в карте обязан совпасть с правилом, для того же figmaName+variants принимается только записанный (INV-B23), компонент
    без правила карты обязан существовать в библиотеке, mapRuleProposal проходит проверку daiver_map_append;
  • хранилище `resolutions.json`: атомарно, под замком (параллельные записи не теряются), побайтно детерминированно,
    повреждённый файл — отказ, а не молчаливый сброс; запись годится, только пока узел тот же, а у hit — равна правилу;
  • переиспользование: тот же figmaName+variants решается один раз — в кадре и между кадрами трека.

Данные. «Modal» — настоящий образец (корень-фрейм «Modal» и три инстанса: «Modal Header» ×2 с теми же вариантами — на нём
пинится INV-B23 — и «Modal Action»); большие деревья, «ядовитый» канон и многокадровые треки — СИНТЕТИКА из маленьких
построителей ниже. Что НЕ проверено данными: боевых экранов и боевой ДС нет — правило кандидатов для `kind=icon`, сигналы
`origin`/`slots=`, размеры строк проекции (кроме 40/2/48 из спеки) и то, как слабая модель читает отказы, выведены из текста
спеки; формулировки отказов проверяются по смыслу (ключевые слова), боевой прогон — X.4.
"""
from __future__ import annotations

import copy
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

from daiver import canon as C  # noqa: E402
from daiver import component_map as CM  # noqa: E402
from daiver import ds_requirements as DR  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import names as NM  # noqa: E402
from daiver import outline as OL  # noqa: E402
from daiver import resolve as RS  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
FAKE_DS = HERE.parent / "fixtures" / "fake-ds"
MCP_SERVER = HERE.parents[2] / "hooks" / "daiver_mcp.py"
URL = "https://pixso.example/a?item-id=2:153702"
TRACK = "STOR-100"
SHA = "a" * 64


# ── построители канона ────────────────────────────────────────────────────────

def leaf(i, kind="text", **kw):
    n = {"id": i, "kind": kind, "name": i}
    n.update(kw)
    return n


def frame(i, kids=(), **kw):
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def slot(i, name, text, overridden=True):
    return {"id": i, "name": name, "text": text, "overridden": overridden,
            "font": {"family": "Fixture Sans", "size": 14, "weight": 400}, "color": "rgba(1, 2, 3, 1)"}


def inst(i, figma, variants=None, slots=None, origin="library", **kw):
    """Инстанс как у адаптера Pixso: слот-контент в `component.content`, детей нет."""
    comp: Dict[str, Any] = {"figmaName": figma, "origin": origin, "childrenRole": "content"}
    if variants:
        comp["variants"] = dict(variants)
    if slots:
        comp["content"] = [slot(f"{i};{n}", n, t) for n, t in slots]
    n = {"id": i, "kind": "instance", "name": kw.pop("name", i), "component": comp}
    n.update(kw)
    return n


def env_of(root, fmt="pixso", stats=None):
    return C.new_envelope(root, fmt, stats)


def real_nodes():
    """Три настоящих инстанса «Modal» (канон после 1.3–1.5) и сам экран."""
    if "env" not in _CACHE:
        _CACHE["env"] = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
    return _CACHE["env"]


_CACHE: Dict[str, Any] = {}


def dumps(x):
    """Ответ так, как его печатает MCP-сервер."""
    return json.dumps(x, ensure_ascii=False, indent=1)


# ── метка в каждом поле, которое резолву видеть нельзя ────────────────────────

POISON = {
    "style": "STYLEPOISON", "font_family": "FAMILYPOISON", "token": "TOKENPOISON", "color": "COLORPOISON",
    "slot_text": "SLOTVALUEPOISON", "textprop": "TEXTPROPVALUEPOISON", "tag": "TAGPOISON", "unknown": "UNKNOWNPOISON",
    "sibling": "SIBLINGPOISON", "ancestor": "ANCESTORPOISON", "slot_color": "SLOTCOLORPOISON", "art_slug": "ARTSLUGPOISON",
}
POISON_NUMBERS = (71117, 72227, 73337, 74447, 75557, 76667, 77777, 78887)
ID_MARK = "QZ"          # метка в id: ни имя, ни текст её не содержат — id потомков в ответе искать по ней


def poisoned(i, kind, name=None, **kw):
    """Узел с «ядом» во всех полях, которые резолву видеть нельзя; id несёт метку QZ, имя — нет."""
    n = {"id": ID_MARK + i, "kind": kind, "name": name or f"Layer {kind}",
         "box": {"x": POISON_NUMBERS[0], "y": POISON_NUMBERS[1], "w": POISON_NUMBERS[2], "h": POISON_NUMBERS[3]},
         "layout": {"direction": "row", "gap": POISON_NUMBERS[4], "stated": True,
                    "padding": {"top": POISON_NUMBERS[5], "right": 0, "bottom": 0, "left": 0}},
         "size": {"w": POISON_NUMBERS[6], "h": "hug"},
         "style": {"background": "rgba(1, 2, 3, 1)", "shadow": POISON["style"], "radius": POISON_NUMBERS[7]},
         "font": {"family": POISON["font_family"], "size": 13, "weight": 700, "token": POISON["token"]},
         "color": f"rgba(9, 9, 9, 1) {POISON['color']}", "tokens": {"fill": POISON["token"]},
         "tag": POISON["tag"], "layoutHints": {"w": "fill"}, "secret": POISON["unknown"]}
    n.update(kw)
    return n


def poisoned_screen():
    """Предок и сосед кандидата ядовиты. Кандидат — фрейм «header_bf_01»: ядовитые text/rect/vector/image/арт и инстанс, у
    которого в слотах лежат метки значений (content) и в textProps — тоже."""
    hdr = poisoned("hdr", "instance", "Steps=OFF", component={
        "figmaName": "Modal Header", "variants": {"Steps": "OFF"}, "origin": "library", "childrenRole": "content",
        "content": [dict(slot("hdr;1", "Title", POISON["slot_text"]), color=POISON["slot_color"]),
                    slot("hdr;2", "Subtitle", POISON["slot_text"] + "2")],
        "textProps": {"Ignored": POISON["textprop"]}, "sized": "own", "paints": True})
    hdr["textProps"] = {"Caption": POISON["textprop"] + "n"}
    bare = poisoned("bare", "instance", "Card", component={
        "figmaName": "Card", "origin": "library", "textProps": {"Heading": POISON["textprop"], "Body": POISON["textprop"]}})
    art = poisoned("art1", "vector", "Illustration", art={"slug": POISON["art_slug"], "nodes": 12})
    inner = poisoned("inner", "frame", "Wrapper", children=[
        poisoned("t1", "text", "Title", text="Заголовок раздела"), poisoned("r1", "rect"), poisoned("v1", "vector"),
        poisoned("img", "image"), art, hdr, bare])
    cand = poisoned("cand", "frame", "header_bf_01", children=[inner])
    sibling = poisoned("sib", "frame", f"sibling {POISON['sibling']}", children=[
        poisoned("sib1", "text", "Sibling text", text=POISON["sibling"])])
    return poisoned("top", "frame", f"Page {POISON['ancestor']}", children=[sibling, cand])


# ── репо с треком и каноном ───────────────────────────────────────────────────

def init_repo(root: Path, source: str = "stories", scan_paths=("lib",)) -> None:
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}", encoding="utf-8")
    answers: Dict[str, Any] = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
                               "buildCommand": "npm run build", "librarySource": source}
    if source == "storybook-mcp":
        answers["storybookMcp"] = "sb"
    else:
        answers["scanPaths"] = list(scan_paths)
    res = I.init(root, answers)
    assert res["ok"], res


def write_ds(root: Path, names: List[str], source: str = "stories", scan_paths=("lib",)) -> None:
    """Пара требований ДС с названными компонентами (формат 2.2, проходит настоящие валидаторы)."""
    comps = {n: {"title": None, "taxonomy": [], "props": {}, "propsClosed": False, "usedValues": {},
                 "import": {"path": "@t/ui", "named": n.split(".")[0]}, "stories": 1} for n in names}
    env = lambda d: {"version": "0.1.0", "role": "ds-requirements", "doc": d, "schema": 1}      # noqa: E731
    contract = {"daiver": env("contract"),
                "source": {"librarySource": source, "scanPaths": list(scan_paths), "scanner": "py/1", "sha256": SHA,
                           "files": 1, "skipped": []},
                "components": comps}
    details = {"daiver": env("details"), "source": {"sha256": SHA}, "components": {}}
    bad = DR.validate_contract(contract) + DR.validate_details(details) + DR.validate_pair(contract, details)
    assert not bad, bad
    for rel, doc in ((DR.CONTRACT_REL, contract), (DR.DETAILS_REL, details)):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DR.dumps(doc), encoding="utf-8")


def build_repo(root: Path, root_node, frames: Optional[Dict[str, dict]] = None, track: str = TRACK):
    """Трек с кадром main (канон = root_node); frames — ещё кадры {имя: корень} (раскладка frames/<имя>/ — как у 6.1)."""
    m = TR.new_manifest(track, created="2026-10-15T12:00:00Z")
    req = {"kind": "pixso", "path": "visual-requirements.pixso.json", "sha256": SHA, "pulledAt": "2026-10-15T12:00:00Z"}
    TR.upsert_frame(m, "main", frameUrl=URL, requirement=dict(req))
    for name in (frames or {}):
        TR.upsert_frame(m, name, frameUrl=URL, requirement=dict(req))
    TR.save(root, m)
    C.write_canonical(TR.frame_paths(root, m, "main")["canonical"], env_of(root_node))
    for name, node in (frames or {}).items():
        C.write_canonical(TR.frame_paths(root, m, name)["canonical"], env_of(node))
    return m


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, root_node, frames: Optional[Dict[str, dict]] = None, track: str = TRACK):
        return build_repo(self.root, root_node, frames, track)

    def set_canon(self, root_node, name: str = "main", track: str = TRACK):
        m = TR.load(self.root, track)
        C.write_canonical(TR.frame_paths(self.root, m, name)["canonical"], env_of(root_node))

    def library(self, *names: str, source: str = "stories") -> None:
        """Проект с конфигом и требованиями ДС из названных компонентов."""
        init_repo(self.root, source)
        write_ds(self.root, list(names), source=source)

    def write_map(self, text: str) -> None:
        p = self.root / ".daiver" / "component-map.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def store_file(self, name: str = "main", track: str = TRACK) -> Path:
        m = TR.load(self.root, track)
        return TR.frame_paths(self.root, m, name)["resolutions"]

    def store(self, name: str = "main") -> dict:
        return json.loads(self.store_file(name).read_text(encoding="utf-8"))

    def call(self, name, **args):
        return T.call(name, self.root, args)

    def candidates(self, **kw):
        return self.call("daiver_resolve_candidates", track=TRACK, **kw)

    def query(self, node_id, **kw):
        return self.call("daiver_resolve_query", track=TRACK, nodeId=node_id, **kw)

    def finalize(self, verdict, **kw):
        return self.call("daiver_resolve_finalize", track=TRACK, verdict=verdict, **kw)

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            T.call(name, self.root, args)
        return str(cm.exception)

    def refused(self, verdict, **kw):
        return self.refusal("daiver_resolve_finalize", track=TRACK, verdict=verdict, **kw)


def verdict(node_id, decision="local", confidence="medium", rationale="потому что так видно по составу", **kw):
    v = {"nodeId": node_id, "decision": decision, "confidence": confidence, "rationale": rationale}
    v.update(kw)
    return v


def comp_verdict(node_id, component, confidence="medium", **kw):
    return verdict(node_id, "component", confidence, component=component, **kw)


# ── имена ─────────────────────────────────────────────────────────────────────
class Names(unittest.TestCase):
    MEANINGFUL = ("header_bf_01", "Modal", "Card / Filters", "Кнопка", "Icon24", "A1", "table-row", "Modal Header")
    MEANINGLESS = ("Frame 123", "Group 5", "TEXT", "Rectangle", "Rectangle 12", "Фрейм 7", "Группа", "", "   ", "24",
                   "—", "123 456", "/ - /", None, 5, ["Frame"])

    def test_the_spec_examples(self):
        for name in self.MEANINGFUL:
            self.assertTrue(RS.meaningful_name(name), name)
        for name in self.MEANINGLESS:
            self.assertFalse(RS.meaningful_name(name), name)

    def test_the_autoname_predicate_is_the_one_ingest_uses(self):
        for name in ("Frame 123", "Group 5", "TEXT", "Rectangle", "Ellipse 2", "Vector", "Фрейм 7"):
            self.assertTrue(NM.is_auto_name(name))
            self.assertFalse(RS.meaningful_name(name), "автоимя ingest — не осмысленное")
        self.assertFalse(NM.is_auto_name("header_bf_01"))

    def test_the_layout_inference_group_name_is_not_meaningful(self):
        from daiver.ingest import layout as LY
        self.assertFalse(RS.meaningful_name(LY.GROUP_NAME), "выведенная обёртка — не кандидат (1.7 это оговорил)")


# ── кандидаты (§10.1) ─────────────────────────────────────────────────────────
def ids_of(root_node):
    return [c.id for c in RS.scan(env_of(root_node)).cands]


class Candidates(unittest.TestCase):
    def test_instances_and_named_frames_in_document_order(self):
        root = frame("Page", [
            frame("header_bf_01", [inst("i1", "Logo")]),
            frame("Frame 123", [inst("i2", "Button"), leaf("t1", "text", text="Привет")]),
            inst("i3", "Badge"),
            frame("Group 5", [frame("card_user", [inst("i4", "Avatar")])])])
        self.assertEqual(ids_of(root), ["Page", "header_bf_01", "i1", "i2", "i3", "card_user", "i4"],
                         "порядок документа; «Frame 123» и «Group 5» — div, но их дети остаются кандидатами")

    def test_text_rect_vector_image_are_never_candidates(self):
        root = frame("Page", [leaf("t", "text", text="a"), leaf("r", "rect"), leaf("v", "vector"), leaf("im", "image")])
        self.assertEqual(ids_of(root), ["Page"])
        sc = RS.scan(env_of(root))
        for nid, kind in (("t", "text"), ("r", "rect"), ("v", "vector"), ("im", "image")):
            self.assertIn(kind, RS.why_not(sc, nid))

    def test_art_is_not_a_candidate_even_with_a_meaningful_name(self):
        root = frame("Page", [leaf("ill", "frame", name="hero_illustration", art={"slug": "hero", "nodes": 9}),
                              inst("i1", "Button")])
        self.assertEqual(ids_of(root), ["Page", "i1"])
        why = RS.why_not(RS.scan(env_of(root)), "ill")
        self.assertIn(".svg", why)
        self.assertIn("daiver_svg_assets", why)

    def test_occluded_content_is_not_a_candidate_with_its_whole_subtree(self):
        root = frame("Modal", [frame("backdrop_page", [inst("under", "Table")], occluded=True), inst("i1", "Button"),
                               inst("hidden", "Badge", occluded=True)])
        self.assertEqual(ids_of(root), ["Modal", "i1"])
        sc = RS.scan(env_of(root))
        self.assertIn("occluded", RS.why_not(sc, "backdrop_page"))
        self.assertIn("backdrop_page", RS.why_not(sc, "under"), "причина названа: внутри закрытого слоя")
        self.assertIn("occluded", RS.why_not(sc, "hidden"))

    def test_inside_a_library_instance_nothing_is_a_candidate(self):
        box = inst("box", "Card", slots=[("Title", "x")])
        box["children"] = [frame("inner_named", [inst("deep", "Button")]), inst("nested", "Icon")]
        root = frame("Page", [box, inst("after", "Badge")])
        self.assertEqual(ids_of(root), ["Page", "box", "after"], "анатомию рисует сам компонент")
        why = RS.why_not(RS.scan(env_of(root)), "deep")
        self.assertIn("box", why)
        self.assertIn("Card", why)
        self.assertIn("анатомию", why)

    def test_local_composition_keeps_its_library_instances_as_candidates(self):
        loc = inst("loc", "Filters", origin="local")
        loc["component"]["childrenRole"] = "fallback"
        loc["children"] = [inst("lib1", "Button"), frame("Frame 3", [inst("lib2", "Checkbox")])]
        self.assertEqual(ids_of(frame("Page", [loc])), ["Page", "loc", "lib1", "lib2"])

    def test_an_icon_instance_is_a_candidate_a_bare_icon_is_not(self):
        root = frame("Page", [leaf("ic1", "icon", component={"figmaName": "Icon24", "variants": {"Size": "24"}}),
                              leaf("ic2", "icon")])
        self.assertEqual(ids_of(root), ["Page", "ic1"])
        self.assertIn("component", RS.why_not(RS.scan(env_of(root)), "ic2"))

    def test_synthetic_engine_nodes_are_not_candidates(self):
        root = frame("Page", [frame("inferred:g1", [inst("i1", "Button")], name="row_wrapper"),
                              frame("inferred:roots:7", [], name="roots")])
        self.assertEqual(ids_of(root), ["Page", "i1"])
        self.assertIn("inferred", RS.why_not(RS.scan(env_of(root)), "inferred:g1"))

    def test_a_frame_without_a_name_is_a_div_with_a_named_reason(self):
        root = frame("Page", [frame("anon", [], name=""), {"id": "noname", "kind": "frame", "children": []}])
        sc = RS.scan(env_of(root))
        self.assertEqual([c.id for c in sc.cands], ["Page"])
        self.assertIn("div", RS.why_not(sc, "anon"))
        self.assertIn("div", RS.why_not(sc, "noname"))

    def test_unknown_id_is_an_error_that_sends_to_the_list(self):
        sc = RS.scan(env_of(frame("Page", [])))
        why = RS.why_not(sc, "nope", "main")
        self.assertIn("нет", why)
        self.assertIn("«main»", why)
        self.assertIn("daiver_resolve_candidates", why)

    def test_an_instance_without_component_is_still_a_candidate_by_its_name(self):
        n = {"id": "bare", "kind": "instance", "name": "Legacy Widget"}
        c = RS.scan(env_of(frame("Page", [n]))).by_id["bare"]
        self.assertEqual((c.lookup, c.reusable), ("Legacy Widget", False), "без figmaName переиспользовать нечего")

    def test_very_deep_trees_are_walked_without_recursion(self):
        node = inst("deepest", "Button")
        for i in range(3000):
            node = frame(f"wrap{i}", [node], name=f"Frame {i}")
        root = frame("Page", [node])
        self.assertEqual(ids_of(root), ["Page", "deepest"])
        sc = RS.scan(env_of(root))
        self.assertEqual(len(sc.nodes), 3002)

    def test_the_black_box_rule_is_the_one_outline_uses(self):
        battery = [inst("a", "X"), inst("b", "X", origin="local"), frame("c"), leaf("d", "icon"),
                   {"id": "e", "kind": "instance", "component": {"figmaName": "X", "childrenRole": "fallback"}},
                   {"id": "f", "kind": "instance"}, {"id": "g", "kind": "instance", "component": "bad"}]
        for n in battery:
            self.assertEqual(RS._black_box(n), OL._black_box(n), n["id"])

    def test_the_modal_sample_screen_and_three_instances(self):
        sc = RS.scan(real_nodes())
        self.assertEqual([(c.id, c.kind, c.lookup) for c in sc.cands],
                         [("2:153702", "frame", "Modal"), ("2:153703", "instance", "Modal Header"),
                          ("22:63141", "instance", "Modal Header"), ("2:153704", "instance", "Modal Action")])
        self.assertEqual(sc.by_id["2:153703"].variants, {"Steps": "OFF"})
        self.assertEqual(sc.skip, {}, "в образце все четыре узла — кандидаты")


class Keys(unittest.TestCase):
    """Ключ переиспользования (INV-B23): figmaName — как ключ имени карты, варианты — как есть."""

    def key(self, name, variants=None):
        return RS.scan(env_of(frame("Page", [inst("i", name, variants)]))).by_id["i"].key

    def test_name_is_case_and_separator_insensitive_like_the_map(self):
        base = self.key("Modal Header", {"Steps": "OFF"})
        for alias in ("modal-header", "ModalHeader", "MODAL_HEADER", "Modal / Header"):
            self.assertEqual(self.key(alias, {"Steps": "OFF"}), base, alias)

    def test_variants_are_exact_and_order_does_not_matter(self):
        self.assertEqual(self.key("A", {"x": "1", "y": "2"}), self.key("A", {"y": "2", "x": "1"}))
        self.assertNotEqual(self.key("A", {"Steps": "OFF"}), self.key("A", {"Steps": "off"}), "значения — как есть")
        self.assertNotEqual(self.key("A", {"Steps": "OFF"}), self.key("A", {"Steps": "OFF", "Size": "L"}))
        self.assertNotEqual(self.key("A", {"Steps": "OFF"}), self.key("A"))

    def test_the_key_is_unambiguous(self):
        self.assertNotEqual(self.key("A", {"x": "1&y=2"}), self.key("A", {"x": "1", "y": "2"}))
        self.assertNotEqual(self.key("A", {"x|y": "1"}), self.key("A", {"x": "y=1"}))
        self.assertEqual(self.key("Modal Header", {"Steps": "OFF"}), "modalheader|Steps=OFF", "читаемый вид")

    def test_frames_and_nameless_instances_do_not_reuse(self):
        sc = RS.scan(env_of(frame("Page", [frame("card_a"), {"id": "bare", "kind": "instance", "name": "Widget"}])))
        self.assertFalse(sc.by_id["card_a"].reusable)
        self.assertFalse(sc.by_id["bare"].reusable)
        self.assertNotIn("|", sc.by_id["card_a"].key, "ключи без переиспользования не похожи на `имя|варианты`")


# ── проекция кандидата (§10.2): белый список ──────────────────────────────────
def project_of(root_node, node_id):
    sc = RS.scan(env_of(root_node))
    return RS.project(sc.by_id[node_id])


def shuffled(o, rng):
    """Копия дерева с другим порядком ключей в каждом объекте (содержимое то же)."""
    if isinstance(o, dict):
        keys = list(o)
        rng.shuffle(keys)
        return {k: shuffled(o[k], rng) for k in keys}
    if isinstance(o, list):
        return [shuffled(x, rng) for x in o]
    return o


class Firewall(unittest.TestCase):
    """Резолв не видит канон: проекция собрана из белого списка, в «ядовитом» каноне яд не доезжает до ответа."""

    def setUp(self):
        self.screen = poisoned_screen()
        self.proj = project_of(self.screen, ID_MARK + "cand")
        self.text = dumps(self.proj)

    def test_the_poison_is_really_in_the_canon(self):
        # без этого проверки ниже были бы пустыми: каждая метка и число обязаны лежать в канонических байтах экрана
        raw = C.dumps(self.screen)
        for key, mark in POISON.items():
            self.assertIn(mark, raw, key)
        for num in POISON_NUMBERS:
            self.assertIn(str(num), raw, num)

    def test_no_poison_reaches_the_answer(self):
        for key, mark in POISON.items():
            self.assertNotIn(mark, self.text, f"{key}: «{mark}» просочилась в проекцию")
        for num in POISON_NUMBERS:
            self.assertNotIn(str(num), self.text, f"число {num} из box/layout/size/style/font просочилось")

    def test_slot_values_are_withheld_only_keys_are_shown(self):
        self.assertNotIn(POISON["slot_text"], self.text)
        self.assertNotIn(POISON["textprop"], self.text)
        tree = "\n".join(self.proj["tree"])
        self.assertIn("slots=Title,Subtitle", tree, "ключи слотов content — без значений")
        self.assertNotIn("Ignored", self.text, "content ЗАМЕНЯЕТ textProps (§6.4): их ключи не слоты")
        self.assertNotIn("Caption", self.text)
        self.assertIn("slots=Body,Heading", tree, "без content слоты — ключи textProps (по алфавиту), тоже без значений")

    def test_other_nodes_are_not_in_the_answer_ancestors_siblings_and_descendant_ids(self):
        self.assertNotIn(POISON["ancestor"], self.text)
        self.assertNotIn(POISON["sibling"], self.text)
        self.assertNotIn("sib1", self.text)
        self.assertEqual(self.text.count(ID_MARK), 1, "id — только у самого кандидата; у потомков id в ответе нет")
        self.assertEqual(self.proj["candidate"]["id"], ID_MARK + "cand")

    def test_the_answer_has_exactly_the_whitelisted_top_level_keys(self):
        self.assertEqual(set(self.proj), {"candidate", "descendants", "fingerprint", "tree", "truncated"})
        self.assertEqual(set(self.proj["candidate"]), {"id", "kind", "name", "figmaName"})

    def test_what_the_resolver_does_get(self):
        tree = "\n".join(self.proj["tree"])
        self.assertEqual(self.proj["candidate"], {"id": ID_MARK + "cand", "kind": "frame", "name": "header_bf_01",
                                                  "figmaName": "header_bf_01"})
        self.assertIn('text "Title" text="Заголовок раздела"', tree)
        self.assertIn('instance "Steps=OFF" <Modal Header> {Steps=OFF} origin=library slots=Title,Subtitle', tree)
        self.assertIn('instance "Card" <Card> origin=library slots=Body,Heading', tree)
        self.assertIn('vector "Illustration" art', tree, "арт помечен — это файл, а не компонент")
        self.assertIn('frame "Wrapper"', tree)

    def test_an_unknown_future_field_is_invisible_by_construction(self):
        n = inst("i", "Button", {"Kind": "primary"}, [("Label", "v")])
        n["brandNewField"] = "NEWFIELDPOISON"
        n["component"]["brandNewComponentField"] = "NEWFIELDPOISON2"
        n["children"] = [dict(leaf("c", "rect"), anotherNew="NEWFIELDPOISON3")]
        text = dumps(RS.project(RS.scan(env_of(frame("Page", [n]))).by_id["i"]))
        self.assertNotIn("NEWFIELDPOISON", text, "белый список, а не чёрный: чего не назвали — того нет")

    def test_slot_values_of_a_candidate_instance_are_withheld_too(self):
        n = inst("i", "Modal Header", {"Steps": "OFF"}, [("Header", "SECRETVALUE"), ("Header", "SECRETVALUE2"), ("Hint", "z")])
        proj = RS.project(RS.scan(env_of(frame("Page", [n]))).by_id["i"])
        self.assertNotIn("SECRETVALUE", dumps(proj))
        self.assertEqual(proj["candidate"]["textSlotKeys"], ["Header", "Hint"], "ключи без повторов — готовый аргумент ds_lookup")
        self.assertEqual(proj["candidate"]["variants"], {"Steps": "OFF"})
        self.assertEqual(proj["candidate"]["figmaName"], "Modal Header")

    def test_repeated_slot_keys_are_counted_in_the_tree_line(self):
        n = frame("card", [inst("a", "Modal Action", {"Variant": "Default"}, [("Label", "x"), ("Label", "y")])])
        proj = project_of(frame("Page", [n]), "card")
        self.assertIn("slots=Label×2", proj["tree"][0], "два одноимённых слота — два ярлыка (две кнопки), и это сигнал")

    def test_the_projection_does_not_touch_the_canon(self):
        before = copy.deepcopy(self.screen)
        project_of(self.screen, ID_MARK + "cand")
        self.assertEqual(self.screen, before)

    def test_key_order_of_the_canon_does_not_change_a_byte(self):
        want = dumps(self.proj)
        for seed in range(5):
            mixed = shuffled(copy.deepcopy(self.screen), random.Random(seed))
            self.assertEqual(dumps(project_of(mixed, ID_MARK + "cand")), want, seed)


class Limits(unittest.TestCase):
    """§20.4: depth 2, maxNodes 48, maxTextLen 40 — и честная обрезка."""

    def test_the_spec_constants(self):
        self.assertEqual((RS.MAX_DEPTH, RS.MAX_NODES, RS.MAX_TEXT_LEN), (2, 48, 40))

    def test_text_is_cut_at_forty_and_flattened_into_one_line(self):
        long = "Очень длинный текст кнопки, который не должен целиком уезжать в контекст слабой модели " * 3
        root = frame("card", [leaf("t1", "text", name="Title", text=long), leaf("t2", "text", name=long, text=long),
                              leaf("t3", "text", name="Sub", text="a\nb\t c  d")])
        tree = project_of(frame("Page", [root]), "card")["tree"]
        self.assertEqual(tree[0], 'text "Title" text="' + " ".join(long.split())[:40] + '"')
        self.assertEqual(tree[1], 'text "' + " ".join(long.split())[:40] + '"', "имя = текст: один раз и тоже до 40")
        self.assertEqual(tree[2], 'text "Sub" text="a b c d"')
        for line in tree:
            for lit in json.loads("[" + ",".join(part for part in _literals(line)) + "]"):
                self.assertLessEqual(len(lit), max(40, RS.MAX_NAME), line)

    def test_a_text_node_longer_than_forty_never_shows_more_than_forty_of_its_text(self):
        root = frame("card", [leaf("t1", "text", name="Заголовок", text="x" * 500)])
        line = project_of(frame("Page", [root]), "card")["tree"][0]
        self.assertEqual(line, 'text "Заголовок" text="' + "x" * 40 + '"')

    def test_long_names_are_clipped_with_a_marker(self):
        root = frame("card_" + "n" * 300, [inst("i", "F" * 300, {"k" * 100: "v" * 100})])
        proj = project_of(frame("Page", [root]), "card_" + "n" * 300)
        self.assertLessEqual(len(proj["candidate"]["name"]), RS.MAX_NAME)
        self.assertTrue(proj["candidate"]["name"].endswith("…"))
        line = proj["tree"][0]
        self.assertLess(len(line), 4 * RS.MAX_NAME + 100)

    def test_depth_two_and_the_cut_is_named(self):
        chain = frame("l4", [], name="level_4")
        for i, name in ((3, "level_3"), (2, "level_2"), (1, "level_1")):
            chain = frame(f"l{i}", [chain], name=name)
        proj = project_of(frame("Page", [frame("cand", [chain], name="card_main")]), "cand")
        self.assertEqual(proj["tree"], ['frame "level_1"', '  frame "level_2"', '    … +2'])
        self.assertEqual((proj["truncated"], proj["hidden"], proj["descendants"]), (True, 2, 4))
        self.assertEqual(proj["fingerprint"], "frame=4", "отпечаток считает ВСЁ поддерево, а не показанное")

    def test_node_cap_counts_the_candidate_and_shows_what_was_cut(self):
        kids = [inst(f"i{k:03d}", "Button", {"n": str(k)}) for k in range(100)]
        proj = project_of(frame("Page", [frame("cand", kids, name="toolbar")]), "cand")
        shown = [ln for ln in proj["tree"] if not ln.lstrip().startswith("…")]
        self.assertEqual(len(shown), RS.MAX_NODES - 1, "48 узлов вместе с самим кандидатом")
        self.assertEqual(proj["tree"][-1], "… +53")
        self.assertEqual((proj["truncated"], proj["hidden"], proj["descendants"]), (True, 53, 100))
        self.assertEqual(proj["fingerprint"], "instance=100")

    def test_not_truncated_when_everything_fits_and_no_hidden_key(self):
        proj = project_of(frame("Page", [frame("cand", [inst("a", "A"), inst("b", "B")], name="pair")]), "cand")
        self.assertIs(proj["truncated"], False)
        self.assertNotIn("hidden", proj)
        self.assertEqual(len(proj["tree"]), 2)

    def test_exactly_at_the_cap_is_not_truncated(self):
        kids = [inst(f"i{k}", "Button") for k in range(RS.MAX_NODES - 1)]
        proj = project_of(frame("Page", [frame("cand", kids, name="toolbar")]), "cand")
        self.assertEqual((len(proj["tree"]), proj["truncated"]), (RS.MAX_NODES - 1, False))
        proj = project_of(frame("Page", [frame("cand", kids + [inst("extra", "Button")], name="toolbar")]), "cand")
        self.assertEqual((len(proj["tree"]), proj["truncated"], proj["hidden"]), (RS.MAX_NODES, True, 1))

    def test_shallow_levels_are_taken_before_deep_ones(self):
        deep = frame("A", [inst(f"a{k}", "Deep") for k in range(60)], name="first_group")
        root = frame("cand", [deep, inst("b", "Late")], name="screen_part")
        tree = "\n".join(project_of(frame("Page", [root]), "cand")["tree"])
        self.assertIn('<Late>', tree, "ребёнок второго уровня не вытеснен внуками первого")
        self.assertIn('frame "first_group"', tree)

    def test_the_cut_marker_sits_after_the_shown_children_in_document_order(self):
        a = frame("A", [inst(f"a{k}", "Deep") for k in range(2)] + [frame("A3", [inst("x", "Hidden")], name="deeper")],
                  name="group_a")
        tree = project_of(frame("Page", [frame("cand", [a, inst("b", "Next")], name="screen_part")]), "cand")["tree"]
        self.assertEqual(tree, ['frame "group_a"', '  instance "a0" <Deep> origin=library',
                                '  instance "a1" <Deep> origin=library', '  frame "deeper"', '    … +1',
                                'instance "b" <Next> origin=library'])

    def test_fingerprint_counts_every_kind_in_fixed_order(self):
        root = frame("cand", [frame("f", [leaf("t", "text", text="a"), leaf("r", "rect"), inst("i", "X"),
                                          leaf("ic", "icon", component={"figmaName": "I"})]),
                              leaf("v", "vector"), leaf("im", "image"), leaf("t2", "text", text="b")], name="mixed_block")
        proj = project_of(frame("Page", [root]), "cand")
        self.assertEqual(proj["fingerprint"], "frame=1 text=2 instance=1 icon=1 image=1 vector=1 rect=1")
        self.assertEqual(proj["descendants"], 8)

    def test_occluded_subtrees_are_not_screen_content(self):
        hidden = frame("bg", [inst("under1", "Table"), inst("under2", "Table")], occluded=True)
        root = frame("cand", [hidden, inst("i", "Button")], name="dialog_body")
        proj = project_of(frame("Page", [root]), "cand")
        self.assertEqual(proj["fingerprint"], "instance=1")
        self.assertEqual(proj["occluded"], 3, "исключённое названо числом узлов, молча не дропается")
        self.assertNotIn("Table", dumps(proj))

    def test_a_leaf_candidate_has_an_empty_tree(self):
        proj = project_of(frame("Page", [inst("i", "Badge")]), "i")
        self.assertEqual((proj["tree"], proj["descendants"], proj["fingerprint"], proj["truncated"]), ([], 0, "", False))

    def test_response_size_ceiling_holds_for_hostile_input(self):
        fat = []
        for k in range(200):
            fat.append(inst(f"i{k}", "N" * 80, {f"key{j}" + "k" * 40: "v" * 60 for j in range(20)},
                            [(f"slot{j}" + "s" * 40, "v") for j in range(20)], name="L" * 90))
        proj = project_of(frame("Page", [frame("cand", fat, name="fat_block")]), "cand")
        chars = sum(len(ln) + 1 for ln in proj["tree"] if not ln.lstrip().startswith("…"))
        self.assertLessEqual(chars, RS.TREE_CHARS, "потолок строк дерева в знаках держит код")
        self.assertTrue(proj["truncated"])
        self.assertLess(len(proj["tree"]), RS.MAX_NODES, "узлы отброшены потолком размера, а не только числом")
        self.assertLess(len(dumps(proj)), 3 * RS.TREE_CHARS)

    def test_the_projection_is_a_small_share_of_a_big_subtree(self):
        env = real_nodes()
        inst_nodes = {k["id"]: k for k in env["root"]["children"]}
        panels = []
        for p in range(30):
            kids = []
            for j, src in enumerate(inst_nodes.values()):
                n = copy.deepcopy(src)
                n["id"] = f"{src['id']}~{p}~{j}"
                kids.append(n)
            panels.append(frame(f"panel{p}", kids, name=f"settings_panel_{p}"))
        big = frame("cand", panels, name="settings_page")
        full = C.dumps(big)
        proj = project_of(frame("Page", [big]), "cand")
        self.assertGreater(len(full), 60000)
        self.assertLess(len(dumps(proj)) / len(full), 0.1, "проекция — малая доля полного поддерева (§10.2)")
        self.assertEqual(proj["descendants"], 30 * 3 + 30)


def _literals(line):
    """JSON-литералы строк в строке дерева (для проверки длины): всё, что в двойных кавычках."""
    out, i = [], 0
    while i < len(line):
        if line[i] == '"':
            j = i + 1
            while j < len(line) and line[j] != '"':
                j += 2 if line[j] == "\\" else 1
            out.append(line[i:j + 1])
            i = j + 1
        else:
            i += 1
    return out


# ── форма вердикта (INV-A7) ───────────────────────────────────────────────────
class VerdictSchema(unittest.TestCase):
    def ok(self, raw):
        clean, problems = RS.check_verdict(raw)
        self.assertEqual(problems, [], raw)
        self.assertIsNotNone(clean)
        return clean

    def bad(self, raw):
        clean, problems = RS.check_verdict(raw)
        self.assertIsNone(clean, raw)
        self.assertTrue(problems, raw)
        return problems

    def one(self, raw, needle):
        problems = self.bad(raw)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(needle, problems[0])

    def test_the_contract_fields_are_the_inv_a7_list(self):
        self.assertEqual(RS.VERDICT_FIELDS, ("nodeId", "decision", "component", "props", "confidence", "rationale",
                                             "mapRuleProposal"))
        self.assertEqual(RS.VERDICT_REQUIRED, ("nodeId", "decision", "confidence", "rationale"))
        self.assertEqual(RS.DECISIONS, ("component", "local", "ignore"))
        self.assertEqual(RS.CONFIDENCE, ("high", "medium", "low"))

    def test_minimal_valid_verdicts(self):
        self.assertEqual(self.ok(verdict("n1")), {"nodeId": "n1", "decision": "local", "confidence": "medium",
                                                  "rationale": "потому что так видно по составу"})
        self.assertEqual(self.ok(verdict("n1", "ignore", "high"))["decision"], "ignore")
        full = self.ok(comp_verdict("n1", "ModalHeader", "high", props={"steps": False, "size": "lg", "n": 2, "r": 1.5}))
        self.assertEqual(full["component"], "ModalHeader")
        self.assertEqual(full["props"], {"steps": False, "size": "lg", "n": 2, "r": 1.5})

    def test_component_without_a_name_is_refused(self):
        self.one(verdict("n1", "component"), "decision=component требует имя")
        self.one(verdict("n1", "component", component=""), "decision=component требует имя")
        self.one(verdict("n1", "component", component="   "), "decision=component требует имя")
        self.one(verdict("n1", "component", component=5), "decision=component требует имя")

    def test_a_name_with_a_non_component_decision_is_refused(self):
        self.one(verdict("n1", "local", component="Button"), "при decision=local")
        self.one(verdict("n1", "ignore", component="Button"), "при decision=ignore")

    def test_null_optional_fields_read_as_absent(self):
        clean = self.ok(verdict("n1", "local", component=None, props=None, mapRuleProposal=None))
        self.assertEqual(set(clean), {"nodeId", "decision", "confidence", "rationale"})

    def test_component_names_follow_the_jsx_rules_of_the_map(self):
        self.one(comp_verdict("n1", "button"), "тег HTML")
        self.one(comp_verdict("n1", "Foo Bar"), "не имя компонента")
        self.one(comp_verdict("n1", "Form..Item"), "не имя компонента")
        self.one(comp_verdict("n1", "B" * 201), "длиннее")
        self.assertEqual(self.ok(comp_verdict("n1", "Form.Item"))["component"], "Form.Item")
        self.assertEqual(self.ok(comp_verdict("n1", "  Button  "))["component"], "Button", "пробелы по краям снимаются")

    def test_decision_and_confidence_are_closed_enums_without_defaults(self):
        for bad in ("Component", "", None, 5, ["local"], "remove"):
            self.one(verdict("n1", bad), "decision")
        for bad in ("HIGH", "", None, 1, "very-high"):
            self.one(verdict("n1", confidence=bad), "confidence")
        raw = verdict("n1")
        del raw["confidence"]
        self.one(raw, "confidence")

    def test_rationale_is_required_one_line_and_bounded(self):
        raw = verdict("n1")
        del raw["rationale"]
        self.one(raw, "rationale: обязателен")
        for blank in ("", "   ", "\n\t"):
            self.one(verdict("n1", rationale=blank), "rationale: пустой")
        self.one(verdict("n1", rationale=5), "rationale: обязателен")
        self.one(verdict("n1", rationale="я" * (RS.MAX_RATIONALE + 1)), f"не больше {RS.MAX_RATIONALE}")
        self.one(verdict("n1", rationale="a\x00b"), "управляющие")
        self.assertEqual(len(self.ok(verdict("n1", rationale="я" * RS.MAX_RATIONALE))["rationale"]), RS.MAX_RATIONALE)
        self.assertEqual(self.ok(verdict("n1", rationale="  две\n строки\t и   пробелы "))["rationale"], "две строки и пробелы",
                         "в таблицу отчёта идёт одна строка")

    def test_the_limit_counts_after_whitespace_is_collapsed(self):
        self.assertEqual(len(self.ok(verdict("n1", rationale="я" * 400 + "\n\n  "))["rationale"]), 400)

    def test_props_rules(self):
        self.one(verdict("n1", "local", props={"a": 1}), "только у decision=component")
        self.one(verdict("n1", "ignore", props={"a": 1}), "только у decision=component")
        self.assertNotIn("props", self.ok(verdict("n1", "local", props={})), "пустой объект — как отсутствие")
        self.one(comp_verdict("n1", "B", props=["a"]), "объект")
        self.one(comp_verdict("n1", "B", props={"1bad": 1}), "не имя пропа")
        self.one(comp_verdict("n1", "B", props={"a": None}), "значение — строка, число или true/false")
        self.one(comp_verdict("n1", "B", props={"a": {"b": 1}}), "значение — строка, число или true/false")
        self.one(comp_verdict("n1", "B", props={"a": [1]}), "значение — строка, число или true/false")
        self.one(comp_verdict("n1", "B", props={"a": float("nan")}), "конечным")
        self.one(comp_verdict("n1", "B", props={"a": float("inf")}), "конечным")
        self.one(comp_verdict("n1", "B", props={"a": "x" * (RS.MAX_PROP_VALUE + 1)}), "длиннее")
        self.one(comp_verdict("n1", "B", props={"a": "line\nbreak"}), "перевод")
        self.one(comp_verdict("n1", "B", props={f"p{i}": i for i in range(RS.MAX_PROPS + 1)}), "не больше")

    def test_unknown_fields_are_a_violation_not_ignored(self):
        problems = self.bad(dict(verdict("n1"), confidense="low", extra=1))
        self.assertEqual(len(problems), 2)
        self.assertIn("«confidense»", problems[0])
        self.assertIn("nodeId, decision, component, props, confidence, rationale, mapRuleProposal", problems[0])

    def test_not_an_object_is_one_clear_violation(self):
        for raw in (None, [], "local", 5, [verdict("n1")]):
            self.one(raw, "ожидается объект")

    def test_node_id_is_a_non_empty_string(self):
        for bad in (None, "", 5, ["a"]):
            self.one(verdict(bad), "nodeId")

    def test_every_violation_is_named_in_one_pass(self):
        raw = {"decision": "teleport", "confidence": "sure", "component": "x y", "props": [], "rationale": "",
               "mapRuleProposal": "rule", "oops": 1}
        problems = self.bad(raw)
        text = " | ".join(problems)
        for needle in ("oops", "nodeId", "decision", "confidence", "rationale", "mapRuleProposal"):
            self.assertIn(needle, text)
        self.assertGreaterEqual(len(problems), 6, "слабый субагент чинит всё разом, а не по одному за вызов")

    def test_map_rule_proposal_must_be_an_object(self):
        self.one(verdict("n1", mapRuleProposal="match: A"), "правило карты")
        self.one(verdict("n1", mapRuleProposal=["a"]), "правило карты")
        self.assertEqual(self.ok(verdict("n1", mapRuleProposal={"match": "A"}))["mapRuleProposal"], {"match": "A"})

    def test_props_equality_is_strict(self):
        self.assertTrue(RS.same_props(None, {}))
        self.assertTrue(RS.same_props({"a": 1}, {"a": 1.0}))
        self.assertFalse(RS.same_props({"a": False}, {"a": 0}), "false — не 0")
        self.assertFalse(RS.same_props({"a": "1"}, {"a": 1}))
        self.assertFalse(RS.same_props({"a": 1}, {"a": 1, "b": 2}))


# ── daiver_resolve_query ──────────────────────────────────────────────────────
MAP = """\
library:
  import: "@t/ui"
rules:
  - match: Modal Header
    outcome: component
    component: ModalHeader
    props: { steps: false }
  - match: Modal Action
    outcome: local
  - match: [Divider, Spacer]
    outcome: ignore
  - match: header_bf_01
    outcome: component
    component: AppHeader
"""


def screen():
    return frame("Page", [
        frame("header_bf_01", [inst("h1", "Logo")], name="header_bf_01"),
        frame("Frame 7", [inst("a", "Modal Header", {"Steps": "OFF"}, [("Header", "Один")]),
                          inst("b", "Modal Header", {"Steps": "OFF"}, [("Header", "Два")]),
                          inst("c", "Modal Action", {"Variant": "Default"}, [("Label", "Ок")])]),
        inst("d", "Divider"), inst("e", "Badge", {"Tone": "info"}, [("Text", "Новое")])])


class QueryTool(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())

    def test_a_projection_with_the_map_result_embedded(self):
        self.write_map(MAP)
        out = self.query("a")
        self.assertEqual((out["track"], out["frame"]), (TRACK, "main"))
        self.assertEqual(out["candidate"], {"id": "a", "kind": "instance", "name": "a", "figmaName": "Modal Header",
                                            "variants": {"Steps": "OFF"}, "textSlotKeys": ["Header"], "origin": "library"})
        self.assertEqual(out["map"]["hit"], True)
        self.assertEqual((out["map"]["outcome"], out["map"]["component"], out["map"]["props"], out["map"]["confidence"]),
                         ("component", "ModalHeader", {"steps": False}, "high"))
        self.assertEqual(out["map"]["import"]["statement"], 'import { ModalHeader } from "@t/ui";')
        self.assertIn("правило карты сработало", out["next"])
        self.assertNotIn("Один", dumps(out), "значение слота не выходит")

    def test_the_embedded_result_is_exactly_what_daiver_map_lookup_answers(self):
        self.write_map(MAP)
        for node, name, variants in (("a", "Modal Header", {"Steps": "OFF"}), ("e", "Badge", {"Tone": "info"}),
                                     ("d", "Divider", None), ("header_bf_01", "header_bf_01", None)):
            self.assertEqual(self.query(node)["map"], self.call("daiver_map_lookup", figmaName=name,
                                                                **({"variants": variants} if variants else {})), node)

    def test_a_miss_says_so_and_points_to_the_library(self):
        self.write_map(MAP)
        out = self.query("e")
        self.assertEqual(out["map"]["hit"], False)
        self.assertIn("daiver_ds_lookup", out["next"])
        self.assertIn("textSlotKeys", out["next"])

    def test_named_frames_are_looked_up_by_their_layer_name(self):
        self.write_map(MAP)
        out = self.query("header_bf_01")
        self.assertEqual((out["candidate"]["kind"], out["candidate"]["figmaName"]), ("frame", "header_bf_01"))
        self.assertEqual((out["map"]["hit"], out["map"]["component"]), (True, "AppHeader"))

    def test_without_a_map_file_it_is_a_miss_with_a_note_not_a_failure(self):
        out = self.query("e")
        self.assertEqual(out["map"]["hit"], False)
        self.assertIn("карты нет", out["map"]["note"])

    def test_a_broken_map_stops_the_resolve_it_is_not_swallowed(self):
        self.write_map("rules:\n  - match: Modal Header\n    outcome: teleport\n")
        for name, args in (("daiver_resolve_query", {"nodeId": "a"}), ("daiver_resolve_candidates", {}),
                           ("daiver_resolve_finalize", {"verdict": verdict("a")})):
            msg = self.refusal(name, track=TRACK, **args)
            self.assertIn("некорректна", msg, name)
            self.assertIn("не подставляй правило «по догадке»", msg, name)
        self.assertFalse(self.store_file().exists())

    def test_storybook_mcp_sends_the_resolver_to_the_server(self):
        init_repo(self.root, "storybook-mcp")
        self.assertIn("MCP-сервер", self.query("e")["next"])
        self.assertIn("«sb»", self.query("e")["next"])

    def test_not_a_candidate_is_refused_with_the_reason(self):
        c = frame("Page", [leaf("t", "text", text="a"), frame("Frame 3", [inst("i", "Badge")]),
                           leaf("ill", "frame", name="hero", art={"slug": "h", "nodes": 3}),
                           frame("bg", [inst("under", "Table")], occluded=True),
                           inst("box", "Card", slots=[("Title", "x")], children=[inst("inner", "Icon")])])
        self.repo(c)
        for nid, needle in (("t", "text"), ("Frame 3", "div"), ("ill", ".svg"), ("under", "occluded"),
                            ("inner", "анатомию"), ("nope", "нет")):
            msg = self.refusal("daiver_resolve_query", track=TRACK, nodeId=nid)
            self.assertIn(needle, msg, nid)

    def test_unknown_id_error_is_clear_and_names_the_way_out(self):
        msg = self.refusal("daiver_resolve_query", track=TRACK, nodeId="2:999999")
        self.assertIn("«2:999999»", msg)
        self.assertIn("«main»", msg)
        self.assertIn("daiver_resolve_candidates", msg)

    def test_arguments_are_checked_by_hand(self):
        self.assertIn("неизвестный аргумент «foo»", self.refusal("daiver_resolve_query", track=TRACK, nodeId="a", foo=1))
        msg = self.refusal("daiver_resolve_query", foo=1)
        self.assertIn("нет обязательного аргумента «track»", msg)
        self.assertIn("нет обязательного аргумента «nodeId»", msg)
        self.assertIn("ожидается string", self.refusal("daiver_resolve_query", track=TRACK, nodeId=5))
        self.assertIn("непустая строка", self.refusal("daiver_resolve_query", track=TRACK, nodeId=""))
        self.assertIn("имя трека", self.refusal("daiver_resolve_query", track="../x", nodeId="a"))
        self.assertIn("имя кадра", self.refusal("daiver_resolve_query", track=TRACK, nodeId="a", frame="a/b"))
        self.assertIn("кадра «other» в треке нет", self.refusal("daiver_resolve_query", track=TRACK, nodeId="a", frame="other"))

    def test_no_track_no_canon_broken_canon_are_named(self):
        self.assertIn("daiver_pixso_pull", self.refusal("daiver_resolve_query", track="STOR-200", nodeId="a"))
        m = TR.load(self.root, TRACK)
        paths = TR.frame_paths(self.root, m, "main")
        paths["canonical"].write_text("{", encoding="utf-8")
        self.assertIn("не читается", self.refusal("daiver_resolve_query", track=TRACK, nodeId="a"))
        paths["canonical"].unlink()
        self.assertIn("daiver_normalize", self.refusal("daiver_resolve_query", track=TRACK, nodeId="a"))

    def test_a_stale_record_is_named_in_the_query(self):
        self.finalize(verdict("e", "local"))
        self.repo(frame("Page", [inst("e", "Badge", {"Tone": "danger"})]))           # у узла другой вариант — запись не его
        out = self.query("e")
        self.assertNotIn("recorded", out)
        self.assertIn("изменился", out["recordStale"])

    def test_it_only_reads(self):
        self.write_map(MAP)
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.query("a")
        self.query("e")
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()), before)

    def test_a_canon_built_from_another_export_is_warned_about(self):
        m = TR.load(self.root, TRACK)
        C.write_canonical(TR.frame_paths(self.root, m, "main")["canonical"], env_of(screen(), stats={"sourceSha256": "0" * 64}))
        self.assertIn("другому экспорту", self.query("a")["warnings"][0])

    def test_the_answer_is_plain_json(self):
        out = self.query("a")
        self.assertEqual(json.loads(json.dumps(out, ensure_ascii=False)), out)

    def test_recorded_and_reuse_are_shown_after_a_verdict(self):
        self.finalize(verdict("a", "local", "low", rationale="шапка модалки"))
        rec = self.query("a")["recorded"]
        self.assertEqual((rec["decision"], rec["confidence"], rec["source"]), ("local", "low", "resolver"))
        twin = self.query("b")
        self.assertEqual(twin["recorded"]["source"], "reuse")
        self.assertEqual(twin["recorded"]["from"], "a")
        self.assertIn("«a»", twin["next"])
        self.assertNotIn("reuse", twin, "запись у узла уже есть — подсказка reuse не нужна")


# ── daiver_resolve_finalize: запись и отказы ──────────────────────────────────
ALL = ["Page", "header_bf_01", "h1", "a", "b", "c", "d", "e"]        # кандидаты screen() в порядке документа


class FinalizeTool(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())

    def test_an_accepted_verdict_is_stored_and_answered_thinly(self):
        out = self.finalize(verdict("e", "local", "low", rationale="нет такого в библиотеке"))
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["recorded"], {"decision": "local", "confidence": "low",
                                           "rationale": "нет такого в библиотеке", "source": "resolver"})
        self.assertEqual(out["remaining"], len(ALL) - 1)
        self.assertEqual((out["track"], out["frame"], out["nodeId"]), (TRACK, "main", "e"))
        self.assertLess(len(dumps(out)), 900, "ответ тонкий: ни библиотечных документов, ни канона")
        store = self.store()
        self.assertEqual((store["schema"], store["seq"], list(store["verdicts"])), (1, 1, ["e"]))
        self.assertEqual(store["verdicts"]["e"], {
            "confidence": "low", "decision": "local", "figmaName": "Badge", "key": "badge|Tone=info", "kind": "instance",
            "name": "e", "nodeId": "e", "rationale": "нет такого в библиотеке", "seq": 1, "source": "resolver",
            "variants": {"Tone": "info"}})

    def test_the_store_lives_in_the_frame_folder_the_track_layout_defines(self):
        self.finalize(verdict("e"))
        self.assertEqual(self.store_file(), self.root / ".daiver" / "tracks" / TRACK / "resolutions.json",
                         "у трека из одного кадра — .daiver/tracks/<KEY>/resolutions.json")
        self.assertEqual(self.store_file(), TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["resolutions"])
        self.assertTrue(self.store_file().is_file())

    def test_the_same_verdict_twice_changes_nothing(self):
        self.finalize(verdict("e"))
        first = self.store_file().read_bytes()
        out = self.finalize(verdict("e"))
        self.assertEqual(self.store_file().read_bytes(), first)
        self.assertEqual(out["remaining"], len(ALL) - 1)

    def test_a_new_verdict_for_the_same_node_replaces_it_and_keeps_its_place(self):
        self.finalize(verdict("e", "local"))
        self.finalize(verdict("d", "ignore"))
        self.finalize(verdict("e", "ignore", "high", rationale="решил иначе"))
        store = self.store()
        self.assertEqual((store["verdicts"]["e"]["decision"], store["verdicts"]["e"]["seq"], store["seq"]), ("ignore", 1, 2))
        self.assertEqual(len(store["verdicts"]), 2)

    def test_refusal_names_every_violation_in_one_message_and_writes_nothing(self):
        msg = self.refused({"nodeId": "e", "decision": "component", "confidence": "sure", "rationale": "",
                            "props": {"a": 1}, "extra": 1})
        self.assertIn("вердикт не принят (4)", msg)
        for needle in ("«extra»", "decision=component требует", "confidence", "rationale: пустой"):
            self.assertIn(needle, msg)
        self.assertIn("1)", msg)
        self.assertIn("4)", msg)
        self.assertIn("Запись не изменена", msg)
        self.assertFalse(self.store_file().exists())

    def test_a_form_violation_and_an_unknown_node_arrive_together(self):
        msg = self.refused({"nodeId": "ghost", "decision": "component", "confidence": "high", "rationale": "r"})
        self.assertIn("decision=component требует", msg)
        self.assertIn("«ghost»", msg)
        self.assertIn("(2)", msg)

    def test_only_a_candidate_of_the_frame_gets_a_verdict(self):
        c = frame("Page", [leaf("t", "text", text="a"), frame("Frame 3", [inst("i", "Badge")]),
                           leaf("ill", "frame", name="hero", art={"slug": "h", "nodes": 3}),
                           frame("bg", [inst("under", "Table")], occluded=True),
                           inst("box", "Card", slots=[("Title", "x")], children=[inst("inner", "Icon")])])
        self.repo(c)
        for nid, needle in (("t", "text"), ("Frame 3", "div"), ("ill", ".svg"), ("under", "occluded"), ("inner", "анатомию"),
                            ("nope", "нет"), ("", "nodeId")):
            self.assertIn(needle, self.refused(verdict(nid)), nid)
        self.assertFalse(self.store_file().exists(), "чужой узел не оставляет следа")
        self.finalize(verdict("i"))
        self.finalize(verdict("box"))

    def test_a_verdict_of_another_frame_is_a_foreign_node(self):
        self.repo(screen(), frames={"second": frame("Other", [inst("z", "Zed")])})
        self.assertIn("«second»", self.refused(verdict("z"), frame="main") + "«second»")      # id есть лишь в другом кадре
        self.assertIn("нет", self.refused(verdict("z"), frame="main"))
        self.finalize(verdict("z"), frame="second")
        self.assertEqual(list(self.store("second")["verdicts"]), ["z"])
        self.assertFalse(self.store_file("main").exists())

    def test_arguments_are_checked_by_hand(self):
        self.assertIn("неизвестный аргумент «foo»", self.refusal("daiver_resolve_finalize", track=TRACK, verdict=verdict("e"), foo=1))
        msg = self.refusal("daiver_resolve_finalize", foo=1)
        self.assertIn("нет обязательного аргумента «track»", msg)
        self.assertIn("нет обязательного аргумента «verdict»", msg)
        self.assertIn("ожидается object", self.refusal("daiver_resolve_finalize", track=TRACK, verdict="local"))
        self.assertIn("имя трека", self.refusal("daiver_resolve_finalize", track="../x", verdict=verdict("e")))

    def test_a_missing_field_set_is_all_listed(self):
        msg = self.refused({})
        for needle in ("nodeId", "decision", "confidence", "rationale"):
            self.assertIn(needle, msg)

    def test_no_time_no_randomness_the_same_calls_the_same_bytes(self):
        def run(root):
            build_repo(Path(root), screen())
            for v in (verdict("e", "local", "low"), verdict("c", "ignore", "high"), verdict("a", "local", "medium")):
                T.call("daiver_resolve_finalize", Path(root), {"track": TRACK, "verdict": v})
            return (Path(root) / ".daiver" / "tracks" / TRACK / "resolutions.json").read_bytes()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = run(a)
            self.assertEqual(first, run(b))
        self.assertIsNone(re.search(rb"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", first), "ни дат, ни времени в записи")


# ── вердикт против карты (INV-B17: карта сильнее вывода) ───────────────────────
class FinalizeAgainstMap(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())
        self.write_map(MAP)

    def test_a_verdict_equal_to_the_rule_is_recorded_as_map(self):
        out = self.finalize(comp_verdict("a", "ModalHeader", "high", props={"steps": False}, rationale="правило карты"))
        self.assertEqual(out["recorded"]["source"], "map")
        self.assertEqual(self.store()["verdicts"]["a"]["source"], "map")

    def test_a_different_component_is_refused_with_the_dictated_verdict(self):
        msg = self.refused(comp_verdict("a", "Other", "high", props={"steps": False}))
        self.assertIn("правило карты #1 «Modal Header»", msg)
        self.assertIn('decision=component component=ModalHeader props={"steps": false} confidence=high', msg)
        self.assertIn("decision=component component=Other", msg)
        self.assertIn("карта сильнее вывода", msg)
        self.assertFalse(self.store_file().exists())

    def test_every_field_of_the_verdict_is_held_to_the_rule(self):
        for label, v in (("confidence", comp_verdict("a", "ModalHeader", "medium", props={"steps": False})),
                         ("пропы пропущены", comp_verdict("a", "ModalHeader", "high")),
                         ("лишний проп", comp_verdict("a", "ModalHeader", "high", props={"steps": False, "size": "lg"})),
                         ("false ≠ 0", comp_verdict("a", "ModalHeader", "high", props={"steps": 0})),
                         ("другое решение", verdict("a", "local", "high")),
                         ("игнор", verdict("a", "ignore", "high"))):
            self.assertIn("правило карты #1", self.refused(v), label)
        self.assertFalse(self.store_file().exists())

    def test_local_and_ignore_rules_dictate_their_decision(self):
        self.assertEqual(self.finalize(verdict("c", "local", "high"))["recorded"]["source"], "map")
        self.assertEqual(self.finalize(verdict("d", "ignore", "high"))["recorded"]["source"], "map")
        self.assertIn("правило карты #2", self.refused(comp_verdict("c", "Whatever", "high")))
        self.assertIn("правило карты #2", self.refused(verdict("c", "ignore", "high")))
        self.assertIn("правило карты #2", self.refused(verdict("c", "local", "medium")))
        self.assertIn("правило карты #3", self.refused(verdict("d", "local", "high")))

    def test_a_named_frame_follows_the_rule_of_its_layer_name(self):
        self.assertIn("component=AppHeader", self.refused(verdict("header_bf_01", "local", "high")))
        self.finalize(comp_verdict("header_bf_01", "AppHeader", "high"))

    def test_a_rule_hit_group_gets_the_verdict_once(self):
        out = self.finalize(comp_verdict("a", "ModalHeader", "high", props={"steps": False}))
        self.assertEqual(out["reused"], ["b"], "тот же figmaName, варианты и правило — вторая запись копией")
        rec = self.store()["verdicts"]["b"]
        self.assertEqual((rec["source"], rec["from"], rec["decision"], rec["component"]), ("map", "a", "component", "ModalHeader"))
        self.assertEqual(out["remaining"], len(ALL) - 2)
        lines = self.candidates()["candidates"]
        self.assertFalse(any(ln.startswith(("a ", "b ")) for ln in lines), "обе записаны — в списке их нет")

    def test_a_map_hit_is_never_overridden_by_a_recorded_heuristic(self):
        # b — тот же figmaName+variants, что a; пока в карте правила не было, решили эвристикой
        self.write_map("rules: []\n")
        self.finalize(verdict("a", "local", "medium", rationale="пока без карты"))
        self.assertEqual(self.store()["verdicts"]["b"]["source"], "reuse")
        self.write_map(MAP)                                   # человек добавил правило
        listing = self.candidates()
        self.assertTrue(any(ln.startswith("a ") and "[stale]" in ln for ln in listing["candidates"]),
                        "прежний вердикт расходится с картой — узел снова в списке")
        self.assertEqual(listing["stats"]["resolved"], 0, "эвристические записи a и b обе недействительны")
        self.assertIn("расходится", dumps(listing["stale"]))
        self.assertIn("правило карты", self.refused(verdict("a", "local", "medium")))
        self.finalize(comp_verdict("a", "ModalHeader", "high", props={"steps": False}))
        self.assertEqual(self.store()["verdicts"]["b"]["source"], "map", "копию по эвристике сменила копия по правилу")
        self.assertEqual(self.candidates()["stats"]["todo"], len(ALL) - 2)

    def test_a_map_record_whose_rule_was_removed_is_no_longer_trusted(self):
        self.finalize(verdict("d", "ignore", "high"))
        self.write_map("rules: []\n")
        out = self.candidates()
        self.assertTrue(any(ln.startswith("d ") and "[stale]" in ln for ln in out["candidates"]), out["candidates"])
        self.assertIn("больше нет", out["stale"]["d"])

    def test_icon_and_regex_rules_dictate_a_component_with_their_props(self):
        self.write_map("""\
icons:
  import:
    path: "@t/icons"
    wrapper: { component: Icon }
rules:
  - match: Icon24
    outcome: icon
    icon: ArrowLeft
  - match: "/^Technical (.+)$/"
    outcome: component
    component: "Tech$1"
    import: { path: "@t/ui" }
""")
        c = frame("Page", [leaf("ic", "icon", component={"figmaName": "Icon24"}), inst("t", "Technical Table", {"a": "b"})])
        self.repo(c)
        self.assertIn("component=Icon", self.refused(verdict("ic", "local", "high")))
        self.assertIn('props={"name": "ArrowLeft"}', self.refused(comp_verdict("ic", "Icon", "high")))
        self.finalize(comp_verdict("ic", "Icon", "high", props={"name": "ArrowLeft"}))
        self.assertIn("component=TechTable", self.refused(comp_verdict("t", "Technical", "high")))
        self.finalize(comp_verdict("t", "TechTable", "high"))


# ── вердикт против библиотеки (INV-A2: ничего не выдумывать) ───────────────────
class FinalizeAgainstLibrary(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())
        init_repo(self.root, "stories")
        write_ds(self.root, ["Badge", "PrimaryButton", "Form", "Alert.Box"])

    def test_a_component_of_the_library_is_accepted(self):
        out = self.finalize(comp_verdict("e", "Badge", "high", props={"tone": "info"}))
        self.assertEqual(out["recorded"]["component"], "Badge")
        self.assertEqual(out["recorded"]["props"], {"tone": "info"})

    def test_a_compound_name_is_checked_by_its_root(self):
        self.finalize(comp_verdict("e", "Form.Item"))
        self.finalize(comp_verdict("d", "Alert.Box"))

    def test_an_invented_component_is_refused_and_the_way_out_is_named(self):
        msg = self.refused(comp_verdict("e", "Alert"))
        self.assertIn("«Alert» нет в библиотеке", msg)
        self.assertIn("decision=local", msg)
        self.assertFalse(self.store_file().exists())

    def test_close_names_of_the_library_are_suggested(self):
        msg = self.refused(comp_verdict("e", "Button"))
        self.assertIn("похожие по имени: PrimaryButton", msg)
        self.assertIn("нет в библиотеке", self.refused(comp_verdict("e", "BADGE")), "регистр важен: JSX читает его")

    def test_local_and_ignore_do_not_need_the_library(self):
        for p in (self.root / DR.CONTRACT_REL, self.root / DR.DETAILS_REL):
            p.unlink()
        self.finalize(verdict("e", "local"))
        self.finalize(verdict("d", "ignore"))

    def test_a_component_verdict_without_requirements_is_blocked_not_guessed(self):
        for p in (self.root / DR.CONTRACT_REL, self.root / DR.DETAILS_REL):
            p.unlink()
        msg = self.refused(comp_verdict("e", "Badge"))
        self.assertIn("blocked: library-source-unreachable", msg)
        self.assertIn("daiver_ds_requirements", msg)

    def test_requirements_built_for_another_library_are_blocked(self):
        write_ds(self.root, ["Badge"], scan_paths=("elsewhere",))
        self.assertIn("blocked: library-source-unreachable", self.refused(comp_verdict("e", "Badge")))

    def test_with_storybook_mcp_there_is_no_document_to_check_against(self):
        for p in (self.root / DR.CONTRACT_REL, self.root / DR.DETAILS_REL):
            p.unlink()
        (self.root / ".daiver" / "config.yaml").unlink()
        init_repo(self.root, "storybook-mcp")
        self.assertEqual(self.finalize(comp_verdict("e", "AnythingTheServerKnows"))["recorded"]["component"],
                         "AnythingTheServerKnows")

    def test_an_uninitialized_project_cannot_judge_a_component(self):
        (self.root / ".daiver" / "config.yaml").unlink()
        self.assertIn("не инициализирован", self.refused(comp_verdict("e", "Badge")))
        self.finalize(verdict("e", "local"))

    def test_the_map_outranks_the_library_check(self):
        self.write_map("rules:\n  - match: Modal Header\n    outcome: component\n    component: ModalHeader\n")
        self.finalize(comp_verdict("a", "ModalHeader", "high"))       # в библиотеке такого нет, а правило карты есть

    def test_a_library_problem_arrives_with_the_other_violations(self):
        msg = self.refused(dict(comp_verdict("e", "Alert"), rationale=""))
        self.assertIn("rationale", msg)
        self.assertEqual(self.refused(dict(comp_verdict("e", "Alert"))).count("нет в библиотеке"), 1)


# ── предложение правила карты ─────────────────────────────────────────────────
class FinalizeProposal(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())
        init_repo(self.root, "stories")
        write_ds(self.root, ["Badge", "Chip"])
        self.good = {"match": "Badge", "outcome": "component", "component": "Badge"}

    def test_a_valid_proposal_is_kept_with_the_verdict_and_nothing_is_written_to_the_map(self):
        map_before = (self.root / ".daiver" / "component-map.yaml").read_bytes()
        out = self.finalize(comp_verdict("e", "Badge", mapRuleProposal=self.good))
        self.assertEqual(self.store()["verdicts"]["e"]["mapRuleProposal"], self.good)
        self.assertEqual((self.root / ".daiver" / "component-map.yaml").read_bytes(), map_before,
                         "карту дописывает только daiver_map_append по «да» человека")
        self.assertNotIn("mapRuleProposal", out["recorded"])

    def test_a_proposal_that_append_would_refuse_is_refused_now(self):
        msg = self.refused(comp_verdict("e", "Badge", mapRuleProposal={"match": "Badge", "outcome": "teleport"}))
        self.assertIn("не проходит проверку daiver_map_append", msg)
        self.assertIn("outcome", msg)
        self.assertNotIn("согласие не потрачено", msg, "согласия тут нет, фраза append не нужна")
        self.assertIn("поле", self.refused(comp_verdict("e", "Badge", mapRuleProposal={"match": "Badge", "outcome": "ignore",
                                                                                       "oops": 1})))

    def test_a_proposal_must_agree_with_the_verdict(self):
        bad = dict(self.good, outcome="local", component="Badge")
        self.assertIn("противоречит decision=component", self.refused(comp_verdict("e", "Badge", mapRuleProposal=bad)))
        bad = dict(self.good, component="Chip")
        self.assertIn("≠ component вердикта", self.refused(comp_verdict("e", "Badge", mapRuleProposal=bad)))
        self.assertIn("противоречит decision=local", self.refused(verdict("e", "local", mapRuleProposal=self.good)))

    def test_a_proposal_must_cover_the_node(self):
        msg = self.refused(comp_verdict("e", "Badge", mapRuleProposal=dict(self.good, match="Chip")))
        self.assertIn("не покрывает этот узел", msg)
        self.assertIn("«Badge»", msg)
        self.finalize(comp_verdict("e", "Badge", mapRuleProposal=dict(self.good, match="/^Bad/")))
        self.finalize(comp_verdict("d", "Chip", mapRuleProposal={"match": ["Divider", "Spacer"], "outcome": "component",
                                                                  "component": "Chip"}))

    def test_a_duplicate_of_an_existing_rule_is_refused(self):
        self.write_map("rules:\n  - match: Badge\n    outcome: component\n    component: Badge\n")
        out = self.refused(comp_verdict("e", "Badge", mapRuleProposal=self.good))
        self.assertIn("map_append", out)

    def test_without_a_map_file_a_proposal_has_nowhere_to_go(self):
        (self.root / ".daiver" / "component-map.yaml").unlink()
        self.assertIn("карты нет", self.refused(comp_verdict("e", "Badge", mapRuleProposal=self.good)))
        self.finalize(comp_verdict("e", "Badge"))

    def test_a_proposal_does_not_spend_or_need_consent(self):
        import forge_events as FE
        self.finalize(comp_verdict("e", "Badge", mapRuleProposal=self.good))
        self.assertIsNone(FE.approval(self.root, CM.consent_key(TRACK)))


# ── переиспользование вердиктов (INV-B23) ─────────────────────────────────────
def twins(n, figma="Modal Header", variants=None, prefix="t"):
    return [inst(f"{prefix}{i}", figma, variants or {"Steps": "OFF"}, [("Header", f"v{i}")]) for i in range(n)]


def hand_record(node_id, key, decision, seq, kind="instance"):
    """Запись хранилища, написанная руками (в обход finalize) — для проверки порядка эталонов."""
    return {"nodeId": node_id, "decision": decision, "confidence": "medium", "rationale": f"вручную {node_id}",
            "source": "resolver", "key": key, "kind": kind, "seq": seq}


class ReuseInFrame(RepoCase):
    def setUp(self):
        super().setUp()
        self.library("Badge", "Chip")

    def test_the_same_figma_name_and_variants_are_resolved_once(self):
        self.repo(frame("Page", twins(3)))
        out = self.finalize(comp_verdict("t0", "Badge", "low", rationale="по имени и слотам", props={"tone": "x"}))
        self.assertEqual(out["reused"], ["t1", "t2"])
        self.assertEqual(out["remaining"], 1, "остался лишь корень «Page»")
        for nid in ("t1", "t2"):
            rec = self.store()["verdicts"][nid]
            self.assertEqual((rec["source"], rec["from"], rec["decision"], rec["component"], rec["props"]),
                             ("reuse", "t0", "component", "Badge", {"tone": "x"}))
            self.assertEqual((rec["confidence"], rec["rationale"]), ("low", "по имени и слотам"),
                             "копия несёт уверенность эталона и не повышает её")
        self.assertEqual(self.store()["verdicts"]["t0"]["source"], "resolver")

    def test_repeating_a_call_on_a_copy_changes_nothing_either(self):
        self.repo(frame("Page", twins(3)))
        self.finalize(verdict("t0", "local", "low"))
        first = self.store_file().read_bytes()
        self.finalize(verdict("t1", "local", "high"))            # копия подтверждается тем же решением
        self.assertEqual(self.store_file().read_bytes(), first)

    def test_a_copy_never_becomes_the_reference_for_others(self):
        self.repo(frame("Page", twins(3)))
        self.finalize(verdict("t0", "local"))
        self.assertEqual({r["from"] for r in self.store()["verdicts"].values() if r["source"] == "reuse"}, {"t0"})

    def test_a_new_node_with_a_recorded_key_may_only_take_the_recorded_verdict(self):
        self.repo(frame("Page", twins(1)))
        self.finalize(comp_verdict("t0", "Badge", "high", rationale="эталон"))
        self.repo(frame("Page", twins(2)))                       # в макете появился ещё один узел с тем же ключом
        hint = self.query("t1")
        self.assertEqual(hint["reuse"]["from"], "t0")
        self.assertEqual(hint["reuse"]["verdict"]["component"], "Badge")
        self.assertIn("заново не решай", hint["next"])
        msg = self.refused(verdict("t1", "local", "high"))
        self.assertIn("узел «t0»", msg)
        self.assertIn("INV-B23", msg)
        self.assertIn("decision=component component=Badge", msg)
        self.assertNotIn("t1", self.store()["verdicts"])
        out = self.finalize(comp_verdict("t1", "Badge", "low", rationale="свой текст"))
        self.assertEqual(out["recorded"]["source"], "reuse")
        self.assertIn("t0", out["note"])
        rec = self.store()["verdicts"]["t1"]
        self.assertEqual((rec["confidence"], rec["rationale"]), ("high", "эталон"), "берётся эталон целиком")

    def test_the_figma_name_matches_like_the_map_but_variants_are_exact(self):
        root = frame("Page", [inst("a", "Modal Header", {"Steps": "OFF"}), inst("b", "modal-header", {"Steps": "OFF"}),
                              inst("c", "Modal Header", {"Steps": "off"}), inst("d", "Modal Header", {"Steps": "OFF", "Size": "L"}),
                              inst("e", "Modal Header")])
        self.repo(root)
        out = self.finalize(verdict("a", "local"))
        self.assertEqual(out["reused"], ["b"])
        self.assertEqual(out["remaining"], 4, "корень и три узла с другими вариантами")

    def test_frames_and_nameless_instances_are_not_reused(self):
        root = frame("Page", [frame("card_x", [inst("i1", "Badge")]), frame("card_x2", [inst("i2", "Badge")], name="card_x"),
                              {"id": "bare1", "kind": "instance", "name": "Widget"},
                              {"id": "bare2", "kind": "instance", "name": "Widget"}])
        self.repo(root)
        self.assertNotIn("reused", self.finalize(verdict("card_x")))
        self.assertNotIn("reused", self.finalize(verdict("bare1")))
        self.assertEqual(self.candidates()["stats"], {"candidates": 7, "resolved": 2, "todo": 5, "groups": 4},
                         "i1 и i2 — один figmaName «Badge», и они в одной группе")

    def test_icon_instances_are_reused_too(self):
        icons = [leaf(f"ic{i}", "icon", component={"figmaName": "Icon24", "variants": {"Size": "24"}}) for i in range(3)]
        self.repo(frame("Page", icons))
        self.assertEqual(self.finalize(verdict("ic0", "local"))["reused"], ["ic1", "ic2"])

    def test_revising_the_reference_updates_its_copies_but_a_copy_cannot_be_revised(self):
        self.repo(frame("Page", twins(3)))
        self.finalize(verdict("t0", "local", "low", rationale="первая версия"))
        self.finalize(comp_verdict("t0", "Badge", "high", rationale="вторая версия"))
        for nid in ("t1", "t2"):
            rec = self.store()["verdicts"][nid]
            self.assertEqual((rec["component"], rec["rationale"], rec["from"]), ("Badge", "вторая версия", "t0"), nid)
        self.assertEqual(self.store()["verdicts"]["t0"]["seq"], 1, "эталон остался эталоном")
        self.assertIn("INV-B23", self.refused(verdict("t2", "ignore", "high")))

    def test_a_proposal_belongs_to_the_reference_only(self):
        self.repo(frame("Page", twins(2)))
        self.finalize(comp_verdict("t0", "Badge", mapRuleProposal={"match": "Modal Header", "outcome": "component",
                                                                   "component": "Badge"}))
        self.assertIn("mapRuleProposal", self.store()["verdicts"]["t0"])
        self.assertNotIn("mapRuleProposal", self.store()["verdicts"]["t1"])

    def test_the_answer_names_a_bounded_list_of_recipients(self):
        self.repo(frame("Page", twins(30)))
        out = self.finalize(verdict("t0", "local"))
        self.assertEqual((len(out["reused"]), out["reusedCount"]), (RS.MAX_PROPAGATED, 29))
        self.assertEqual(self.store()["seq"], 30)

    def test_a_node_whose_key_changed_loses_its_record_and_so_do_its_copies(self):
        self.repo(frame("Page", twins(2)))
        self.finalize(verdict("t0", "local"))
        self.set_canon(frame("Page", [inst("t0", "Modal Header", {"Steps": "ON"}, [("Header", "v")]), twins(2)[1]]))
        out = self.candidates()
        self.assertEqual(out["stats"]["resolved"], 0)
        self.assertTrue(any(ln.startswith("t0 ") and "[stale]" in ln for ln in out["candidates"]), out["candidates"])
        self.assertIn("изменился", out["stale"]["t0"])
        self.assertIn("эталон", out["stale"]["t1"])
        self.finalize(verdict("t0", "ignore"))                   # новый ключ — новое решение; старая запись перезаписана
        self.assertEqual(self.store()["verdicts"]["t0"]["decision"], "ignore")

    def test_an_orphaned_copy_is_stale_when_its_reference_disappears(self):
        self.repo(frame("Page", twins(2)))
        self.finalize(verdict("t0", "local"))
        self.set_canon(frame("Page", [twins(2)[1]]))             # узла-эталона в макете больше нет
        out = self.candidates()
        self.assertEqual(out["stats"]["resolved"], 0)
        self.assertIn("t1", out["stale"])
        self.finalize(verdict("t1", "ignore"))


class ReuseAcrossFrames(RepoCase):
    def setUp(self):
        super().setUp()
        self.library("Badge", "Chip")
        self.repo(frame("Page", twins(1) + [inst("m", "Modal Action", {"Variant": "Default"})]),
                  frames={"second": frame("Other", twins(2, prefix="s") + [inst("sm", "Modal Action", {"Variant": "Default"})])})
        self.finalize(comp_verdict("t0", "Badge", "medium", rationale="решено на первом кадре"), frame="main")

    def test_a_later_frame_takes_the_recorded_verdict_without_a_resolver(self):
        listing = self.candidates(frame="second")
        self.assertEqual(listing["stats"]["resolved"], 2, "оба узла «Modal Header» второго кадра получили вердикт")
        lines = " ".join(listing["candidates"])
        self.assertNotIn("<Modal Header>", lines)
        self.assertIn("<Modal Action>", lines, "а новый ключ — обычный кандидат")
        for nid in ("s0", "s1"):
            rec = self.store("second")["verdicts"][nid]
            self.assertEqual((rec["source"], rec["from"], rec["fromFrame"], rec["component"]), ("reuse", "t0", "main", "Badge"))
        self.assertEqual(list(self.store("main")["verdicts"]), ["t0"], "первый кадр не тронут")

    def test_before_the_sync_query_shows_the_hint_and_finalize_enforces_it(self):
        hint = self.call("daiver_resolve_query", track=TRACK, nodeId="s0", frame="second")
        self.assertEqual((hint["reuse"]["from"], hint["reuse"]["fromFrame"]), ("t0", "main"))
        msg = self.refusal("daiver_resolve_finalize", track=TRACK, frame="second", verdict=verdict("s0", "local"))
        self.assertIn("кадр «main»", msg)
        self.assertIn("INV-B23", msg)
        self.assertEqual(self.finalize(comp_verdict("s0", "Badge"), frame="second")["recorded"]["from"], "t0")

    def test_the_sync_is_idempotent(self):
        self.candidates(frame="second")
        first = self.store_file("second").read_bytes()
        self.candidates(frame="second")
        self.assertEqual(self.store_file("second").read_bytes(), first)

    def test_a_reference_may_also_come_from_a_later_frame(self):
        self.finalize(comp_verdict("sm", "Chip"), frame="second")
        hint = self.call("daiver_resolve_query", track=TRACK, nodeId="m", frame="main")
        self.assertEqual((hint["reuse"]["from"], hint["reuse"]["fromFrame"]), ("sm", "second"))

    def test_the_earliest_frame_then_the_lowest_seq_is_the_reference(self):
        key = "modalaction|Variant=Default"
        build_repo(self.root, frame("Page", [inst("m1", "Modal Action", {"Variant": "Default"})]),
                   frames={"second": frame("B", [inst("m2", "Modal Action", {"Variant": "Default"})]),
                           "third": frame("C", [inst("m3", "Modal Action", {"Variant": "Default"})])})
        for name, nid, decision, seq in (("main", "m1", "local", 5), ("second", "m2", "ignore", 1)):
            store = RS.empty_store()
            store["seq"] = seq
            store["verdicts"][nid] = hand_record(nid, key, decision, seq)
            RS.write_store(self.root, self.store_file(name), store)
        hint = self.call("daiver_resolve_query", track=TRACK, nodeId="m3", frame="third")
        self.assertEqual((hint["reuse"]["from"], hint["reuse"]["fromFrame"], hint["reuse"]["verdict"]["decision"]),
                         ("m1", "main", "local"), "кадр раньше в манифесте важнее меньшего seq в позднем")

    def test_map_hits_are_not_taken_from_other_frames(self):
        self.write_map("rules:\n  - match: Modal Header\n    outcome: ignore\n")
        listing = self.candidates(frame="second")
        self.assertTrue(any("<Modal Header>" in ln for ln in listing["candidates"]), "правило карты сверяется с узлом отдельно")
        self.assertFalse(self.store_file("second").exists())

    def test_another_track_shares_nothing(self):
        build_repo(self.root, frame("Page", twins(1)), track="STOR-200")
        out = T.call("daiver_resolve_candidates", self.root, {"track": "STOR-200"})
        self.assertEqual(out["stats"]["resolved"], 0)


# ── хранилище вердиктов ───────────────────────────────────────────────────────
WORKER = """\
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from daiver import tools as T
root, ids = Path(sys.argv[2]), sys.argv[3:]
for nid in ids:
    T.call("daiver_resolve_finalize", root, {"track": "STOR-100", "verdict": {
        "nodeId": nid, "decision": "local", "confidence": "high", "rationale": "процесс " + nid}})
"""


class StoreFile(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())

    def test_a_corrupt_file_is_refused_not_silently_reset(self):
        self.finalize(verdict("e"))
        path = self.store_file()
        for broken, needle in (("{", "не JSON"), ("[]", "{schema, seq, verdicts}"),
                               ('{"schema": 2, "seq": 0, "verdicts": {}}', "schema"),
                               ('{"schema": 1, "seq": 0, "verdicts": {}, "extra": 1}', "extra"),
                               ('{"schema": 1, "seq": 1, "verdicts": {"e": {"nodeId": "e"}}}', "verdicts[e]")):
            path.write_text(broken, encoding="utf-8")
            for call in (lambda: self.refused(verdict("d")), lambda: self.refusal("daiver_resolve_candidates", track=TRACK),
                         lambda: self.refusal("daiver_resolve_query", track=TRACK, nodeId="e")):
                msg = call()
                self.assertIn("resolutions.json", msg)
                self.assertIn(needle, msg, broken)
            self.assertEqual(path.read_text(encoding="utf-8"), broken, "отказ не трогает файл")
        path.write_bytes(b"\xff\xfe\x00")
        self.assertIn("не читается", self.refused(verdict("d")))
        path.unlink()                                             # удалил человек — вердикты принимаются заново
        self.assertEqual(self.finalize(verdict("d"))["remaining"], len(ALL) - 1)

    def test_a_hand_edited_record_that_the_engine_could_not_have_written_is_caught(self):
        self.finalize(verdict("e"))
        path = self.store_file()
        good = json.loads(path.read_text(encoding="utf-8"))
        cases = {"длинный rationale": lambda r: r.update(rationale="я" * 500),
                 "компонент у local": lambda r: r.update(component="Button"),
                 "чужой source": lambda r: r.update(source="human"),
                 "нет ключа": lambda r: r.pop("key"),
                 "seq вне диапазона": lambda r: r.update(seq=99),
                 "nodeId не тот": lambda r: r.update(nodeId="other"),
                 "reuse без from": lambda r: r.update(source="reuse"),
                 "неизвестное поле": lambda r: r.update(approvedBy="user")}
        for label, mutate in cases.items():
            bad = copy.deepcopy(good)
            mutate(bad["verdicts"]["e"])
            path.write_text(json.dumps(bad), encoding="utf-8")
            self.assertIn("не проходит проверку", self.refusal("daiver_resolve_candidates", track=TRACK), label)

    def test_no_temp_file_is_left_behind_and_the_directory_stays_clean(self):
        self.finalize(verdict("e"))
        self.assertEqual(sorted(p.name for p in self.store_file().parent.iterdir() if p.suffix == ".tmp"), [])
        self.assertTrue((self.root / "ground" / "resolve.lock").exists(), "замок лежит в ground/, а не рядом с данными")

    def test_a_failed_write_leaves_the_old_file_and_says_so(self):
        self.finalize(verdict("e"))
        before = self.store_file().read_bytes()
        import os as _os
        real = _os.replace

        def boom(src, dst):
            raise OSError("диск полон")
        _os.replace = boom
        try:
            msg = self.refusal("daiver_resolve_finalize", track=TRACK, verdict=verdict("d"))
        finally:
            _os.replace = real
        self.assertIn("запись", msg)
        self.assertIn("диск полон", msg)
        self.assertEqual(self.store_file().read_bytes(), before)
        self.assertEqual([p for p in self.store_file().parent.iterdir() if p.suffix == ".tmp"], [])

    def test_parallel_resolvers_lose_no_verdicts_threads(self):
        ids = ["a", "c", "d", "e", "h1", "header_bf_01", "Page"]
        errors: List[BaseException] = []

        def work(nid):
            try:
                T.call("daiver_resolve_finalize", self.root, {"track": TRACK, "verdict": verdict(nid, "local", "high")})
            except BaseException as e:      # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=work, args=(i,)) for i in ids]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        got = set(self.store()["verdicts"])
        self.assertEqual(got, set(ids) | {"b"}, "ничья запись не потерялась (b — копия a)")
        self.assertEqual(sorted(r["seq"] for r in self.store()["verdicts"].values()), list(range(1, len(got) + 1)),
                         "seq без дыр и повторов")

    def test_parallel_resolvers_lose_no_verdicts_processes(self):
        groups = [["a", "c"], ["d", "e"], ["h1", "header_bf_01"], ["Page"]]
        procs = [subprocess.Popen([sys.executable, "-X", "utf8", "-c", WORKER, str(HERE), str(self.root)] + g,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
                 for g in groups]
        for p in procs:
            _out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual(set(self.store()["verdicts"]), {"a", "b", "c", "d", "e", "h1", "header_bf_01", "Page"})

    def test_check_store_accepts_what_finalize_writes(self):
        self.finalize(verdict("a"))
        self.assertEqual(RS.check_store(self.store()), [])
        self.assertEqual(RS.check_store(RS.empty_store()), [])

    def test_the_store_is_machine_state_not_a_hand_file(self):
        text = self.store_file().read_text(encoding="utf-8") if self.finalize(verdict("e")) else ""
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(json.loads(text), self.store())
        self.assertEqual(text, json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                         "ключи отсортированы, отступ 1, кириллица как есть")


# ── daiver_resolve_candidates ─────────────────────────────────────────────────
class CandidatesTool(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo(screen())

    def test_the_list_lines_groups_and_stats(self):
        out = self.candidates()
        self.assertEqual((out["track"], out["frame"]), (TRACK, "main"))
        self.assertEqual(out["stats"], {"candidates": 8, "resolved": 0, "todo": 8, "groups": 7})
        self.assertEqual(out["candidates"], [
            'Page frame "Page"',
            'header_bf_01 frame "header_bf_01"',
            'h1 instance "h1" <Logo>',
            'a instance "a" <Modal Header> {Steps=OFF} ×2',
            'c instance "c" <Modal Action> {Variant=Default}',
            'd instance "d" <Divider>',
            'e instance "e" <Badge> {Tone=info}'])
        self.assertNotIn("more", out)

    def test_it_lists_only_what_is_left_and_says_when_it_is_over(self):
        for nid in ("Page", "header_bf_01", "h1", "a", "c", "d", "e"):
            out = self.finalize(verdict(nid))
        self.assertEqual(out["remaining"], 0)
        self.assertIn("все кандидаты кадра решены", out["next"])
        done = self.candidates()
        self.assertEqual((done["candidates"], done["stats"]["todo"], done["stats"]["resolved"]), ([], 0, 8))
        self.assertIn("резолв закончен", done["next"])

    def test_a_screen_without_candidates_says_there_is_nothing_to_resolve(self):
        self.repo(frame("Frame 12", [leaf("t", "text", text="a"), frame("Group 4", [leaf("r", "rect")])]))
        out = self.candidates()
        self.assertEqual((out["candidates"], out["stats"]["candidates"]), ([], 0))
        self.assertIn("резолвить нечего", out["next"])

    def test_the_list_is_bounded_and_the_rest_is_counted(self):
        self.repo(frame("Page", [inst(f"i{k:03d}", f"Component{k}") for k in range(130)]))
        out = self.candidates()
        self.assertEqual((len(out["candidates"]), out["more"], out["stats"]["groups"]), (RS.MAX_LIST, 31, 131))
        self.assertIn("обрезан", out["next"])
        for k in range(RS.MAX_LIST - 1):
            self.finalize(verdict(f"i{k:03d}"))
        out = self.candidates()
        self.assertEqual((len(out["candidates"]), "more" in out), (131 - 99, False), "по мере решения список сжимается до остатка")
        self.assertEqual([ln.split()[0] for ln in out["candidates"][:3]], ["Page", "i099", "i100"],
                         "и остаются именно нерешённые, в порядке документа")

    def test_map_hit_groups_are_one_line_and_one_verdict(self):
        self.repo(frame("Page", [inst(f"r{i}", "Table Row", {"State": "default"}) for i in range(5)]))
        self.write_map("rules:\n  - match: Table Row\n    outcome: local\n")
        out = self.candidates()
        self.assertIn('r0 instance "r0" <Table Row> {State=default} ×5', out["candidates"])
        rec = self.finalize(verdict("r0", "local", "high"))
        self.assertEqual((rec["recorded"]["source"], len(rec["reused"])), ("map", 4))
        self.assertEqual({r["source"] for r in self.store()["verdicts"].values()}, {"map"})
        self.assertEqual(self.candidates()["stats"]["todo"], 1)

    def test_it_does_not_write_when_there_is_nothing_to_hand_out(self):
        self.candidates()
        self.assertFalse(self.store_file().exists())
        self.assertFalse((self.root / "ground").exists(), "читающий вызов не берёт замок и не оставляет следов")

    def test_arguments_and_frame(self):
        self.assertIn("неизвестный аргумент «foo»", self.refusal("daiver_resolve_candidates", track=TRACK, foo=1))
        self.assertIn("нет обязательного аргумента «track»", self.refusal("daiver_resolve_candidates"))
        self.assertIn("имя трека", self.refusal("daiver_resolve_candidates", track="a b"))
        self.assertIn("кадра «x» в треке нет", self.refusal("daiver_resolve_candidates", track=TRACK, frame="x"))
        self.assertIn("daiver_pixso_pull", self.refusal("daiver_resolve_candidates", track="STOR-9"))

    def test_the_answer_is_short_for_a_realistic_screen(self):
        self.repo(frame("Page", [frame(f"section_{s}", [inst(f"s{s}i{k}", f"Comp{k % 6}", {"v": str(k % 3)}) for k in range(40)])
                                  for s in range(5)]))
        out = self.candidates()
        self.assertLess(out["stats"]["groups"], 30, "200 узлов с 18 различными ключами — несколько десятков строк")
        self.assertLess(len(dumps(out)), 6000)


# ── поверхность инструментов (QG S11, INV-A7) ─────────────────────────────────
class Surface(RepoCase):
    NAMES = ("daiver_resolve_candidates", "daiver_resolve_query", "daiver_resolve_finalize")

    def test_the_resolver_contract_tools_exist_with_closed_schemas(self):
        for name in self.NAMES:
            tool = T.TOOLS[name]
            self.assertTrue(callable(tool["fn"]))
            self.assertIs(tool["schema"]["additionalProperties"], False, name)
            self.assertIn("track", tool["schema"]["required"], name)
            self.assertIn("frame", tool["schema"]["properties"], name)
        self.assertEqual(set(T.TOOLS["daiver_resolve_query"]["schema"]["properties"]), {"track", "frame", "nodeId"})
        self.assertEqual(T.TOOLS["daiver_resolve_query"]["schema"]["required"], ["track", "nodeId"])
        self.assertEqual(set(T.TOOLS["daiver_resolve_finalize"]["schema"]["properties"]), {"track", "frame", "verdict"})
        self.assertEqual(T.TOOLS["daiver_resolve_finalize"]["schema"]["required"], ["track", "verdict"])

    def test_the_verdict_schema_is_the_inv_a7_contract(self):
        v = T.TOOLS["daiver_resolve_finalize"]["schema"]["properties"]["verdict"]
        self.assertEqual(set(v["properties"]), {"nodeId", "decision", "component", "props", "confidence", "rationale",
                                                "mapRuleProposal"})
        self.assertEqual(v["required"], ["nodeId", "decision", "confidence", "rationale"])
        self.assertIs(v["additionalProperties"], False)
        self.assertEqual(v["properties"]["decision"]["enum"], ["component", "local", "ignore"])
        self.assertEqual(v["properties"]["confidence"]["enum"], ["high", "medium", "low"])
        self.assertEqual(v["properties"]["rationale"]["maxLength"], 400)
        self.assertEqual(tuple(v["properties"]), RS.VERDICT_FIELDS, "одно место правды — резолв")

    def test_descriptions_state_the_limits_from_the_constants(self):
        d = T.TOOLS["daiver_resolve_query"]["description"]
        for needle in (f"глубина {RS.MAX_DEPTH}", f"{RS.MAX_NODES} узлов", f"{RS.MAX_TEXT_LEN} знаков", "fingerprint", "truncated",
                       "daiver_map_lookup", "Только читает"):
            self.assertIn(needle, d)
        self.assertIn("400 знаков", T.TOOLS["daiver_resolve_finalize"]["description"])
        self.assertIn("×N", T.TOOLS["daiver_resolve_candidates"]["description"])

    def test_the_hook_test_inventory_lists_the_three_tools(self):
        text = (HERE.parents[2] / "hooks" / "test_daiver_mcp.py").read_text(encoding="utf-8")
        for name in self.NAMES:
            self.assertIn(f'"{name}"', text)

    def test_a_json_rpc_session_goes_through_the_whole_chain(self):
        self.repo(screen())
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "daiver_resolve_candidates", "arguments": {"track": TRACK}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "daiver_resolve_query", "arguments": {"track": TRACK, "nodeId": "a"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "daiver_resolve_finalize", "arguments": {"track": TRACK, "verdict": verdict("a")}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "daiver_resolve_finalize", "arguments": {"track": TRACK, "verdict": {"nodeId": "a"}}}},
                {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                 "params": {"name": "daiver_resolve_query", "arguments": {"track": TRACK, "nodeId": "nope"}}}]
        data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs)
        r = subprocess.run([sys.executable, "-X", "utf8", str(MCP_SERVER), "--project-root", str(self.root)], input=data,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = {json.loads(line)["id"]: json.loads(line) for line in r.stdout.splitlines() if line.strip()}
        listed = {t["name"] for t in out[1]["result"]["tools"]}
        self.assertTrue(set(self.NAMES) <= listed)
        cands = json.loads(out[2]["result"]["content"][0]["text"])
        self.assertEqual(cands["stats"]["candidates"], 8)
        self.assertEqual(json.loads(out[3]["result"]["content"][0]["text"])["candidate"]["figmaName"], "Modal Header")
        self.assertEqual(json.loads(out[4]["result"]["content"][0]["text"])["recorded"]["decision"], "local")
        self.assertIs(out[5]["result"]["isError"], True)
        self.assertIn("вердикт не принят (3)", out[5]["result"]["content"][0]["text"])
        self.assertIs(out[6]["result"]["isError"], True)

    def test_verdicts_api_returns_only_valid_records_by_node(self):
        self.repo(screen())
        self.assertEqual(RS.verdicts(self.root, TRACK), {})
        self.finalize(verdict("a", "local", "low"))
        got = RS.verdicts(self.root, TRACK)
        self.assertEqual(set(got), {"a", "b"})
        self.assertEqual(got["b"]["source"], "reuse")
        self.set_canon(frame("Page", [inst("a", "Other Thing")]))
        self.assertEqual(RS.verdicts(self.root, TRACK), {}, "узел изменился — запись не годится")


# ── образец «Modal» ───────────────────────────────────────────────────────────
class OnModalFixture(unittest.TestCase):
    """Настоящий образец: pull + normalize в каталоге класса (читают все), а тесты с записью берут свою копию."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name) / "base"
        cls.base.mkdir()
        shutil.copyfile(str(FIXTURE), str(cls.base / "dsl.json"))
        T.call("daiver_pixso_pull", cls.base, {"track": TRACK, "dslPath": "dsl.json", "frameUrl": URL})
        T.call("daiver_normalize", cls.base, {"track": TRACK})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def fresh(self) -> Path:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name) / "repo"
        shutil.copytree(str(self.base), str(root))
        return root

    def test_candidates_of_the_real_screen(self):
        out = T.call("daiver_resolve_candidates", self.base, {"track": TRACK})
        self.assertEqual(out["stats"], {"candidates": 4, "resolved": 0, "todo": 4, "groups": 3})
        self.assertEqual(out["candidates"], [
            '2:153702 frame "Modal"',
            '2:153703 instance "Steps=OFF" <Modal Header> {Steps=OFF} ×2',
            '2:153704 instance "Variant=Default" <Modal Action> {Variant=Default}'])

    def test_projection_of_the_real_screen_frame_and_of_its_instances(self):
        q = lambda nid: T.call("daiver_resolve_query", self.base, {"track": TRACK, "nodeId": nid})   # noqa: E731
        root = q("2:153702")
        self.assertEqual(root["candidate"], {"id": "2:153702", "kind": "frame", "name": "Modal", "figmaName": "Modal"})
        self.assertEqual(root["tree"], [
            'instance "Steps=OFF" <Modal Header> {Steps=OFF} origin=library slots=Header',
            'instance "Steps=OFF" <Modal Header> {Steps=OFF} origin=library slots=Header,Description',
            'instance "Variant=Default" <Modal Action> {Variant=Default} origin=library slots=Label×2'])
        self.assertEqual((root["fingerprint"], root["descendants"], root["truncated"]), ("instance=3", 3, False))
        action = q("2:153704")
        self.assertEqual(action["candidate"]["textSlotKeys"], ["Label"])
        self.assertEqual(action["candidate"]["variants"], {"Variant": "Default"})
        self.assertEqual(q("22:63141")["candidate"]["textSlotKeys"], ["Header", "Description"])

    def test_nothing_from_the_real_canon_but_the_whitelist_reaches_the_resolver(self):
        env = real_nodes()
        secrets = {text["text"] for text in C.collect_texts(env["root"])}
        secrets |= {"Fixture Sans", "Headline/H2", "Text/B1", "Text/B1 bold", "rgba(", "shadow", "padding", "radius",
                    '"box"', '"layout"', '"style"', '"font"', '"color"', "inherit"}
        self.assertGreater(len(secrets), 8)
        for nid in ("2:153702", "2:153703", "22:63141", "2:153704"):
            text = dumps(T.call("daiver_resolve_query", self.base, {"track": TRACK, "nodeId": nid}))
            for secret in secrets:
                self.assertNotIn(secret, text, f"{nid}: «{secret}» из настоящего канона просочилась в проекцию")

    def test_one_resolver_pass_per_distinct_component_and_the_whole_screen_gets_resolved(self):
        root = self.fresh()
        write_map = lambda text: (root / ".daiver").mkdir(exist_ok=True) or (root / ".daiver" / "component-map.yaml").write_bytes(  # noqa: E731
            text.encode("utf-8"))
        init_repo(root, "stories")
        write_ds(root, ["ModalHeader", "ModalAction", "Modal"])
        write_map("rules:\n  - match: Modal Header\n    outcome: component\n    component: ModalHeader\n")
        call = lambda name, **a: T.call(name, root, a)       # noqa: E731
        self.assertEqual(call("daiver_resolve_candidates", track=TRACK)["stats"]["todo"], 4)
        out = call("daiver_resolve_finalize", track=TRACK,
                   verdict=comp_verdict("2:153703", "ModalHeader", "high", rationale="правило карты: Modal Header"))
        self.assertEqual((out["recorded"]["source"], out["reused"], out["remaining"]), ("map", ["22:63141"], 2),
                         "вторая шапка с теми же вариантами получила вердикт сама (INV-B23)")
        out = call("daiver_resolve_finalize", track=TRACK,
                   verdict=comp_verdict("2:153704", "ModalAction", "medium", rationale="имя совпало, две кнопки"))
        self.assertEqual((out["recorded"]["source"], out["remaining"]), ("resolver", 1))
        out = call("daiver_resolve_finalize", track=TRACK,
                   verdict=verdict("2:153702", "local", "high", rationale="оболочка экрана: свои отступы и тень"))
        self.assertEqual(out["remaining"], 0)
        self.assertEqual(call("daiver_resolve_candidates", track=TRACK)["candidates"], [])
        got = RS.verdicts(root, TRACK)
        self.assertEqual({k: v["decision"] for k, v in got.items()},
                         {"2:153702": "local", "2:153703": "component", "22:63141": "component", "2:153704": "component"})
        self.assertEqual(got["22:63141"]["from"], "2:153703")

    def test_an_invented_component_for_the_real_action_block_is_refused(self):
        root = self.fresh()
        init_repo(root, "stories")
        write_ds(root, ["Modal"])
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_resolve_finalize", root, {"track": TRACK, "verdict": comp_verdict("2:153704", "ActionBar", "high")})
        self.assertIn("«ActionBar» нет в библиотеке", str(cm.exception))
        self.assertFalse((root / ".daiver" / "tracks" / TRACK / "resolutions.json").exists())


# ── добавки по итогам мутационной проверки ────────────────────────────────────
class MutationGaps(RepoCase):
    """Правила, которые мутационная проверка нашла недотестированными."""

    def test_an_occluded_subtree_deep_in_the_candidate_is_counted_too(self):
        deep = frame("w", [frame("bg", [inst("u1", "Table"), inst("u2", "Table")], occluded=True), inst("i", "Button")],
                     name="wrapper_block")
        proj = project_of(frame("Page", [frame("cand", [deep], name="dialog_body")]), "cand")
        self.assertEqual((proj["occluded"], proj["fingerprint"]), (3, "frame=1 instance=1"))

    def test_when_the_cap_hits_the_first_children_in_document_order_are_the_ones_shown(self):
        kids = [inst(f"i{k:03d}", "Button", {"n": str(k)}) for k in range(100)]
        tree = project_of(frame("Page", [frame("cand", kids, name="toolbar")]), "cand")["tree"]
        self.assertIn("{n=0}", tree[0])
        self.assertIn(f"{{n={RS.MAX_NODES - 2}}}", tree[-2])
        self.assertNotIn("{n=99}", "\n".join(tree))

    def test_a_regex_rule_that_hits_one_spelling_keeps_hits_and_heuristics_apart(self):
        self.write_map('rules:\n  - match: "/^Modal Header$/"\n    outcome: ignore\n')
        root = frame("Page", [inst("hit", "Modal Header", {"S": "1"}), inst("miss", "modal header", {"S": "1"})])
        self.repo(root)
        self.assertEqual(RS.scan(env_of(root)).by_id["hit"].key, RS.scan(env_of(root)).by_id["miss"].key)
        self.finalize(verdict("miss", "local"))                      # эвристика не должна лечь поверх узла с правилом
        self.assertNotIn("hit", self.store()["verdicts"])
        self.assertIn("правило карты", self.refused(verdict("hit", "local", "high")))
        out = self.finalize(verdict("hit", "ignore", "high"))        # а правило не разносится на узел без правила
        self.assertNotIn("reused", out)
        self.assertEqual(self.store()["verdicts"]["miss"]["source"], "resolver")

    def test_a_map_record_of_another_frame_is_no_reference_for_a_node_the_rule_does_not_cover(self):
        self.write_map('rules:\n  - match: "/^Modal Header$/"\n    outcome: ignore\n')
        self.repo(frame("Page", [inst("hit", "Modal Header", {"S": "1"})]),
                  frames={"second": frame("Other", [inst("miss", "modal header", {"S": "1"})])})
        self.finalize(verdict("hit", "ignore", "high"), frame="main")
        out = self.candidates(frame="second")
        self.assertEqual(out["stats"]["resolved"], 0, "вердикт по правилу карты не эталон для узла без правила")
        self.assertFalse(self.store_file("second").exists())

    def _three_frames(self):
        key = "modalaction|Variant=Default"
        node = lambda i: inst(i, "Modal Action", {"Variant": "Default"})        # noqa: E731
        build_repo(self.root, frame("Page", [node("a1"), node("a2"), node("a3")]),
                   frames={"second": frame("B", [node("b1"), node("b2")])})
        return key

    def _write(self, name, records):
        store = RS.empty_store()
        for nid, key, decision, seq in records:
            store["verdicts"][nid] = hand_record(nid, key, decision, seq)
            store["seq"] = max(store["seq"], seq)
        RS.write_store(self.root, self.store_file(name), store)

    def test_a_reference_of_an_earlier_frame_beats_the_local_one_even_with_a_larger_seq(self):
        key = self._three_frames()
        self._write("main", [("a1", key, "local", 9)])
        self._write("second", [("b1", key, "ignore", 1)])
        hint = self.call("daiver_resolve_query", track=TRACK, nodeId="b2", frame="second")
        self.assertEqual((hint["reuse"]["from"], hint["reuse"]["fromFrame"], hint["reuse"]["verdict"]["decision"]),
                         ("a1", "main", "local"))

    def test_the_local_reference_beats_a_later_frame(self):
        key = self._three_frames()
        self._write("main", [("a1", key, "local", 9)])
        self._write("second", [("b1", key, "ignore", 1)])
        hint = self.call("daiver_resolve_query", track=TRACK, nodeId="a2", frame="main")
        self.assertEqual((hint["reuse"]["from"], "fromFrame" in hint["reuse"], hint["reuse"]["verdict"]["decision"]),
                         ("a1", False, "local"))

    def test_among_local_references_the_lowest_seq_wins(self):
        key = self._three_frames()
        self._write("main", [("a1", key, "local", 7), ("a2", key, "ignore", 3)])
        hint = self.call("daiver_resolve_query", track=TRACK, nodeId="a3", frame="main")
        self.assertEqual((hint["reuse"]["from"], hint["reuse"]["verdict"]["decision"]), ("a2", "ignore"))

    def test_a_substitution_the_map_cannot_make_stops_the_resolve(self):
        self.write_map('rules:\n  - match: "/^Tech (.+)$/"\n    outcome: component\n    component: "Tech$1"\n')
        self.repo(frame("Page", [inst("t", "Tech Foo Bar"), inst("ok", "Tech Foo")]))
        for name, args in (("daiver_resolve_query", {"nodeId": "t"}), ("daiver_resolve_candidates", {}),
                           ("daiver_resolve_finalize", {"verdict": verdict("ok")})):
            self.assertIn("после подстановки", self.refusal(name, track=TRACK, **args), name)

    def test_a_name_without_letters_cannot_be_looked_up_but_can_be_resolved(self):
        self.write_map("rules: []\n")
        self.repo(frame("Page", [inst("dash", "—")]))
        out = self.query("dash")
        self.assertEqual(out["map"]["hit"], False)
        self.assertIn("нет ни букв, ни цифр", out["map"]["note"])
        self.assertEqual(self.finalize(verdict("dash", "local"))["recorded"]["decision"], "local")

    def test_the_reason_for_a_node_inside_an_instance_names_the_instance(self):
        box = inst("box", "Card", slots=[("Title", "x")], children=[inst("deep", "Icon")])
        why = RS.why_not(RS.scan(env_of(frame("Page", [box]))), "deep")
        self.assertIn("«box» (Card)", why)
        self.assertIn("кандидат инстанс «box»", why)


if __name__ == "__main__":
    unittest.main()
