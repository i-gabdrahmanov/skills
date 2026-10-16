#!/usr/bin/env python3
"""test_outline.py — daiver_outline и daiver_section: карта макета и тело секции (задача 1.8; спека §9.1–9.3, §16 nodeUrl,
§20.4; QG U4, F7, N3; INV B1, B10, B11, B12, F2).

Данные. «Modal» — настоящий образец (4 узла, 3101 байт): на нём пинятся форма строк дайджеста, хеш корня (равен
`rootHash` daiver_normalize), пустой unproven («экран полностью объявлен»), шаблон ссылок из frameUrl кадра и
разрез корня на три инстанса при бюджете 1500. Большого экрана в образце нет, поэтому границы секций, лёгкость
дайджеста и детерминизм доказаны на СИНТЕТИКЕ, собранной из настоящих узлов образца (копии его инстансов со слотами
content — реальная форма и вес) и маленьких построителей ниже; допущения названы в docstring `daiver/outline.py`.

Пинит: бюджет (аргумент > конфиг > 24000, ≤ бюджета подходит, лист тяжелее — oversize целиком, большой бюджет — корень
целиком); разрез — разбиение дерева, в порядке документа, мельчающее с бюджетом; hash/weight секции равны хешу и весу
канона; разрез и хеши не зависят от порядка ключей и процесса (F7); дайджест строками (поля, усечение текста до 80 без
эллипсиса, box только у абсолютных, раскладка как в каноне) и его потолок «весь ответ ≤ ¼ канона» с лестницей свёртки;
unproven по правилам (оверлей молчит, объявленный контейнер молчит, двойная причина — дважды, кап 60 с честным
счётчиком, до 20 id детей) и в согласии с `ingest/layout.py`; goalImage с подсказкой; nodeUrlTemplate (Pixso, Figma,
добавление параметра, путь внутри инстанса, нет URL); daiver_section понимает ровно id outline и не отдаёт канон
целиком в обход (N3); отказы инструментов.
"""
from __future__ import annotations

import copy
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _testkit as K  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import outline as OL  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.ingest import layout as LY  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
URL = "https://pixso.example/a?item-id=2:153702"
TRACK = "STOR-100"
SHA = "a" * 64


# ── построители канона ────────────────────────────────────────────────────────
def leaf(i, kind="text", **kw):
    n = {"id": i, "kind": kind, "name": i}
    n.update(kw)
    return n


def frame(i, kids, **kw):
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def stack(gap=0, pad=0, direction="column", **kw):
    lay = {"direction": direction, "stated": True,
           "padding": {"top": pad, "right": pad, "bottom": pad, "left": pad}}
    if gap:
        lay["gap"] = gap
    lay.update(kw)
    return lay


def env_of(root, fmt="pixso", stats=None):
    return C.new_envelope(root, fmt, stats)


def panel(i, texts=3):
    return frame(f"p{i}", [leaf(f"p{i}t{j}", text=f"Строка {i}-{j}") for j in range(texts)],
                 layout=stack(gap=4), size={"w": "fill", "h": "hug"})


def page(n=4, texts=3):
    return frame("root", [panel(i, texts) for i in range(n)], layout=stack(gap=8),
                 box={"x": 0, "y": 0, "w": 400, "h": 400}, size={"w": 400, "h": "hug"})


def ids(sections):
    return [s["id"] for s in sections]


def line_ids(lines):
    return [ln.strip().split(" ", 1)[0] for ln in lines]


# ── синтетика из настоящих узлов образца ──────────────────────────────────────
_CACHE = {}


def real_nodes():
    """Три настоящих инстанса «Modal» (канон после 1.3–1.5): шапка 659 Б, шапка с описанием 1063 Б, блок действий 886 Б."""
    if "inst" not in _CACHE:
        env = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
        _CACHE["inst"] = {k["id"]: k for k in env["root"]["children"]}
    return _CACHE["inst"]


def clone(node, suffix):
    n = copy.deepcopy(node)

    def rename(x):
        x["id"] = f"{x['id']}~{suffix}"
        for k in x.get("children") or []:
            rename(k)
        for slot in (x.get("component") or {}).get("content") or []:
            slot["id"] = f"{slot['id']}~{suffix}"
    rename(n)
    return n


def text_node(i, text):
    return leaf(i, text=text, font={"family": "Fixture Sans", "size": 14, "weight": 400, "lineHeight": 20,
                                    "letterSpacing": -0.2, "token": "Text/B1"},
                color="rgba(31, 20, 51, 1)", size={"h": "hug", "w": "fill"})


def row_with_icon(p):
    return frame(f"row{p}", [leaf(f"ic{p}", "icon", size={"w": 24, "h": 24}), text_node(f"rt{p}", "Подпись")],
                 layout={"direction": "row", "gap": 8, "stated": True, "align": {"main": "start", "cross": "center"}},
                 size={"w": "fill", "h": "hug"})


def ds_panel(p):
    """Панель в духе экрана на дизайн-системе: два инстанса образца, два текста, ряд «иконка + подпись»."""
    inst = real_nodes()
    return frame(f"panel{p}", [clone(inst["2:153703"], f"{p}a"), text_node(f"{p}x", "Подпись"),
                               text_node(f"{p}y", "Короткое описание блока"), row_with_icon(p),
                               clone(inst["2:153704"], f"{p}b")],
                 layout=stack(gap=12, pad=16), size={"w": "fill", "h": "hug"},
                 style={"background": "rgba(255, 255, 255, 1)", "radius": 12})


def ru_panel(p):
    """Панель с длинным русским именем: кириллица в строках дайджеста весит вдвое больше символов — вес считается в байтах."""
    n = ds_panel(p)
    n["name"] = f"Панель управления настройками клиента и параметрами уведомлений, раздел номер {p}"
    return n


def text_panel(p):
    inst = real_nodes()
    texts = [text_node(f"{p}-{i}", f"Описание строки с достаточно длинным текстом номер {i} для проверки усечения")
             for i in range(6)]
    return frame(f"panel{p}", [clone(inst["2:153703"], f"{p}a")] + texts + [row_with_icon(p), clone(inst["2:153704"], f"{p}b")],
                 layout=stack(gap=12, pad=16), size={"w": "fill", "h": "hug"})


def wall_group(p, per, template):
    return frame(f"g{p}", [clone(real_nodes()[template], f"g{p}i{i}") for i in range(per)],
                 layout=stack(gap=8, pad=8), size={"w": "fill", "h": "hug"})


def screen(n, make_panel):
    return frame("dash", [make_panel(p) for p in range(n)], layout=stack(gap=24, pad=24),
                 box={"x": 0, "y": 0, "w": 1280, "h": 4000}, size={"w": 1280, "h": "hug"},
                 style={"background": "rgba(245, 245, 250, 1)"})


def wall(groups, per, template):
    return frame("wall", [wall_group(p, per, template) for p in range(groups)], layout=stack(gap=24, pad=24),
                 box={"x": 0, "y": 0, "w": 1280, "h": 4000}, size={"w": 1280, "h": "hug"})


def cells(groups, per):
    """Скелет таблицы: много маленьких пустых рамок. Это не «простые листья» (kind=frame), поэтому лист-свёртка не помогает."""
    return frame("table", [frame(f"r{g}", [frame(f"c{g}x{i}", [], size={"w": 80, "h": 24}) for i in range(per)],
                                 layout=stack(direction="row", gap=4), size={"w": "fill", "h": 24}) for g in range(groups)],
                 layout=stack(gap=4), box={"x": 0, "y": 0, "w": 1280, "h": 4000}, size={"w": 1280, "h": "hug"})


def shuffled(o, rng):
    """Копия дерева с другим порядком ключей в каждом объекте (содержимое то же)."""
    if isinstance(o, dict):
        keys = list(o)
        rng.shuffle(keys)
        return {k: shuffled(o[k], rng) for k in keys}
    if isinstance(o, list):
        return [shuffled(x, rng) for x in o]
    return o


# ── репо с треком и каноном ───────────────────────────────────────────────────
class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, root_node, fmt="pixso", frame_url=URL, stats=None, config=None):
        m = TR.new_manifest(TRACK, created="2026-10-15T12:00:00Z")
        fields = {"requirement": {"kind": "pixso", "path": "visual-requirements.pixso.json", "sha256": SHA,
                                  "pulledAt": "2026-10-15T12:00:00Z"}}
        if frame_url is not None:
            fields["frameUrl"] = frame_url
        TR.upsert_frame(m, "main", **fields)
        TR.save(self.root, m)
        paths = TR.frame_paths(self.root, m, "main")
        C.write_canonical(paths["canonical"], env_of(root_node, fmt, stats))
        if config is not None:
            self.config(config)
        return paths

    def config(self, text):
        p = self.root / ".daiver" / "config.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def outline(self, **kw):
        return T.call("daiver_outline", self.root, dict({"track": TRACK}, **kw))

    def section(self, section_id, **kw):
        return T.call("daiver_section", self.root, dict({"track": TRACK, "sectionId": section_id}, **kw))

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            T.call(name, self.root, args)
        return str(cm.exception)


# ── бюджет ────────────────────────────────────────────────────────────────────
class Budget(RepoCase):
    def test_default_is_24000(self):
        self.assertEqual(OL.effective_budget(self.root), (24000, []))
        self.assertEqual(OL.DEFAULT_BUDGET, 24000)

    def test_config_beats_default(self):
        self.config("workflow:\n  weightBudget: 5000\n")
        self.assertEqual(OL.effective_budget(self.root)[0], 5000)

    def test_argument_beats_config_beats_default(self):
        self.config("workflow:\n  weightBudget: 5000\n")
        self.assertEqual(OL.effective_budget(self.root, 3000), (3000, []))
        self.assertEqual(OL.effective_budget(self.root, None)[0], 5000)

    def test_priority_reaches_the_tool_answer(self):
        self.repo(page())
        self.assertEqual(self.outline()["weightBudget"], 24000)
        self.config("workflow:\n  weightBudget: 5000\n")
        self.assertEqual(self.outline()["weightBudget"], 5000)
        self.assertEqual(self.outline(weightBudget=3000)["weightBudget"], 3000)

    def test_whole_float_is_a_plain_integer(self):
        b = OL.check_budget(1500.0)
        self.assertEqual((b, type(b)), (1500, int))
        self.assertEqual(OL.check_budget(1500.5), 1500.5)

    def test_bad_arguments_are_refused_with_the_rule(self):
        for bad in (0, -1, True, False, "5000", float("nan"), float("inf"), [], {}):
            with self.assertRaises(OL.OutlineError, msg=repr(bad)) as cm:
                OL.check_budget(bad)
            self.assertIn("положительное число", str(cm.exception))

    def test_tool_refuses_wrong_type_and_non_positive(self):
        self.repo(page())
        self.assertIn("ожидается number", self.refusal("daiver_outline", track=TRACK, weightBudget="много"))
        self.assertIn("положительное число", self.refusal("daiver_outline", track=TRACK, weightBudget=0))
        self.assertIn("ожидается number", self.refusal("daiver_section", track=TRACK, sectionId="root", weightBudget=True))

    def test_broken_config_value_falls_back_to_default_and_says_so(self):
        self.repo(page())
        self.config("workflow:\n  weightBudget: много\n")
        out = self.outline()
        self.assertEqual(out["weightBudget"], 24000)
        self.assertTrue(any(w.startswith("workflow.weightBudget") for w in out["warnings"]), out.get("warnings"))


# ── границы секций ────────────────────────────────────────────────────────────
class Sections(unittest.TestCase):
    def setUp(self):
        self.root = page()
        self.env = env_of(self.root)
        self.w_root = C.weight(self.root)
        self.w_panel = C.weight(self.root["children"][0])

    def sections(self, budget, env=None):
        return OL.section_list(env or self.env, budget)

    def test_node_that_fits_is_a_whole_section_and_the_root_is_whole_at_a_big_budget(self):
        for budget in (self.w_root, self.w_root * 10, 10 ** 9):
            secs = self.sections(budget)
            self.assertEqual(ids(secs), ["root"], budget)
            self.assertEqual((secs[0]["depth"], secs[0]["nDescendants"]), (0, 16))

    def test_one_byte_less_and_the_root_is_cut_into_its_children(self):
        secs = self.sections(self.w_root - 1)
        self.assertEqual(ids(secs), ["p0", "p1", "p2", "p3"])
        self.assertEqual({s["depth"] for s in secs}, {1})

    def test_weight_equal_to_the_budget_fits_one_byte_less_does_not(self):
        self.assertEqual(ids(self.sections(self.w_panel)), ["p0", "p1", "p2", "p3"])
        finer = self.sections(self.w_panel - 1)
        self.assertEqual(ids(finer), [f"p{i}t{j}" for i in range(4) for j in range(3)])

    def test_document_order_is_kept_even_when_the_cut_is_uneven(self):
        root = copy.deepcopy(self.root)
        root["children"][1]["children"][1]["text"] = "очень длинный текст " * 20          # p1t1 стал тяжёлым
        budget = C.weight(root["children"][0]) + 5
        secs = ids(self.sections(budget, env_of(root)))
        self.assertEqual(secs, ["p0", "p1t0", "p1t1", "p1t2", "p2", "p3"],
                         "p0 влезает; p1 из-за тяжёлого ребёнка разрезан на детей, остальные панели целы — и всё по порядку")

    def test_leaf_heavier_than_the_budget_is_oversize_and_never_cut(self):
        root = copy.deepcopy(self.root)
        root["children"][2]["children"][0]["text"] = "x" * 400
        big = root["children"][2]["children"][0]
        budget = C.weight(root["children"][0]) - 1
        secs = self.sections(budget, env_of(root))
        by = {s["id"]: s for s in secs}
        self.assertTrue(by["p2t0"]["oversize"])
        self.assertEqual((by["p2t0"]["weight"], by["p2t0"]["nDescendants"]), (C.weight(big), 0))
        self.assertGreater(by["p2t0"]["weight"], budget)
        self.assertEqual([s["id"] for s in secs if s.get("oversize")], ["p2t0"])
        self.assertTrue(all("oversize" not in s for s in secs if s["id"] != "p2t0"))

    def test_single_node_canon_heavier_than_the_budget(self):
        secs = self.sections(5, env_of(leaf("only", text="что-то длинное")))
        self.assertEqual((ids(secs), secs[0].get("oversize")), (["only"], True))

    def test_sections_partition_the_tree_for_every_budget(self):
        for budget in (10 ** 9, self.w_root, self.w_root - 1, self.w_panel, self.w_panel - 1, 60, 1):
            secs = self.sections(budget)
            owner = {}
            for s in secs:
                for n in C.iter_nodes(C.find_node(self.root, s["id"])):
                    self.assertNotIn(n["id"], owner, f"секции пересекаются: {n['id']} (бюджет {budget})")
                    owner[n["id"]] = s["id"]
                if not s.get("oversize"):
                    self.assertLessEqual(s["weight"], budget)
            spine = [n for n in C.iter_nodes(self.root) if n["id"] not in owner]
            for n in spine:                                             # оболочка: тяжелее бюджета и с детьми
                self.assertGreater(C.weight(n), budget, n["id"])
                self.assertTrue(n.get("children"), n["id"])
            self.assertEqual(len(owner) + len(spine), len(list(C.iter_nodes(self.root))))

    def test_smaller_budget_refines_the_larger_one(self):
        budgets = [10 ** 9, self.w_root, self.w_panel, self.w_panel - 1, 1]
        for coarse_b, fine_b in zip(budgets, budgets[1:]):
            coarse = self.sections(coarse_b)
            inside = {}
            for s in coarse:
                for n in C.iter_nodes(C.find_node(self.root, s["id"])):
                    inside[n["id"]] = s["id"]
            for fine in self.sections(fine_b):
                self.assertIn(fine["id"], inside, f"{fine['id']} при {fine_b} не лежит ни в одной секции при {coarse_b}")

    def test_meta_equals_what_the_canon_computes(self):
        for s in self.sections(self.w_panel - 1):
            node = C.find_node(self.root, s["id"])
            self.assertEqual(s["hash"], C.subtree_hash(node))
            self.assertEqual(s["weight"], C.weight(node))
            self.assertEqual(s["nDescendants"], C.descendants_count(node))
            chain = C.ancestors_of(self.root, s["id"])
            self.assertEqual(s["depth"], len(chain))
            self.assertEqual(s["path"], " > ".join(a["name"] for a in chain + [node]))
            self.assertEqual((s["name"], s["kind"]), (node["name"], node["kind"]))
        self.assertEqual(list(self.sections(self.w_panel)[0]),
                         ["id", "name", "kind", "weight", "nDescendants", "depth", "path", "hash"])

    def test_hash_does_not_depend_on_the_budget(self):
        a = {s["id"]: s["hash"] for s in self.sections(self.w_panel)}
        b = {s["id"]: s["hash"] for s in self.sections(self.w_root)}
        self.assertEqual(a["p0"], C.subtree_hash(self.root["children"][0]))
        self.assertNotEqual(b["root"], a["p0"])

    def test_instance_sections_name_their_component(self):
        inst = real_nodes()
        root = frame("r", [clone(inst["2:153703"], "a"), clone(inst["2:153704"], "b")], layout=stack())
        secs = self.sections(C.weight(root) - 1, env_of(root))
        self.assertEqual([(s["kind"], s["figmaName"]) for s in secs],
                         [("instance", "Modal Header"), ("instance", "Modal Action")])
        self.assertTrue(all("figmaName" not in s for s in self.sections(self.w_panel)))

    def test_occluded_is_inherited_by_sections_under_a_hidden_layer(self):
        back = frame("back", [panel(7), panel(8)], occluded=True, layout=stack())
        root = frame("r", [back, panel(1)], layout=stack())
        secs = {s["id"]: s for s in self.sections(C.weight(panel(1)), env_of(root))}
        self.assertTrue(secs["p7"]["occluded"] and secs["p8"]["occluded"], "флаг на предке — секции под ним тоже")
        self.assertNotIn("occluded", secs["p1"])


# ── F7: детерминизм ───────────────────────────────────────────────────────────
class Determinism(RepoCase):
    def setUp(self):
        super().setUp()
        self.root_node = screen(8, ds_panel)

    def test_same_input_same_bytes(self):
        a = OL.build_outline(env_of(self.root_node), 24000, frame_url=URL)
        b = OL.build_outline(env_of(copy.deepcopy(self.root_node)), 24000, frame_url=URL)
        self.assertEqual(C.dumps(a), C.dumps(b))
        self.assertEqual(json.dumps(a, ensure_ascii=False), json.dumps(b, ensure_ascii=False), "и порядок ключей ответа")

    def test_order_of_keys_in_the_canon_changes_nothing(self):
        base = OL.build_outline(env_of(self.root_node), 6000)
        other = OL.build_outline(env_of(shuffled(self.root_node, random.Random(7))), 6000)
        self.assertEqual(C.dumps(base), C.dumps(other))
        self.assertEqual(ids(base["sections"]), ids(other["sections"]))
        self.assertEqual([s["hash"] for s in base["sections"]], [s["hash"] for s in other["sections"]])

    def test_section_ids_hashes_and_lines_are_the_same_across_processes(self):
        path = self.repo(self.root_node)["canonical"]
        code = ("import sys, json; sys.path.insert(0, %r); from pathlib import Path\n"
                "from daiver import canon as C, outline as OL\n"
                "env = C.read_canonical(Path(sys.argv[1]))\n"
                "print(json.dumps(OL.build_outline(env, 6000, frame_url=%r), ensure_ascii=False))") % (str(HERE), URL)
        outs = []
        for seed in ("1", "424242"):
            r = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(path)], capture_output=True, text=True,
                               encoding="utf-8", timeout=120, env=dict(os.environ, PYTHONHASHSEED=seed))
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertGreater(len(json.loads(outs[0])["sections"]), 1)

    def test_a_change_moves_only_the_hashes_that_contain_it(self):
        before = {s["id"]: s["hash"] for s in OL.section_list(env_of(self.root_node), 6000)}
        edited = copy.deepcopy(self.root_node)
        C.find_node(edited, "panel3")["children"][1]["text"] = "Другая подпись"
        after = {s["id"]: s["hash"] for s in OL.section_list(env_of(edited), 6000)}
        self.assertEqual(list(before), list(after), "набор и порядок секций те же")
        changed = [i for i in before if before[i] != after[i]]
        self.assertEqual(changed, ["panel3"], "хеш меняется только у секции, в которой правка (B10)")
        whole = [OL.section_list(env_of(r), 10 ** 9)[0]["hash"] for r in (self.root_node, edited)]
        self.assertNotEqual(whole[0], whole[1], "корень целиком содержит правку — его хеш другой")


# ── дайджест: строки ──────────────────────────────────────────────────────────
class Digest(unittest.TestCase):
    def lines(self, root, budget=10 ** 9, **kw):
        return OL.build_outline(env_of(root), budget, **kw)["outline"]

    def test_one_line_per_node_indent_is_depth_ids_in_document_order(self):
        root = page()
        lines = self.lines(root)
        self.assertEqual(line_ids(lines), [n["id"] for n in C.iter_nodes(root)])
        self.assertEqual([len(ln) - len(ln.lstrip(" ")) for ln in lines][:5], [0, 2, 4, 4, 4])
        self.assertTrue(lines[0].startswith('root frame "root" '), lines[0])

    def test_light_fields_kind_name_weight_children(self):
        root = page(1, 2)
        lines = self.lines(root)
        self.assertEqual(lines[0].split(" ")[:4], ["root", "frame", '"root"', f"{C.weight(root)}B"])
        self.assertIn(" n=1 ", lines[0])
        self.assertIn(" n=2 ", lines[1])
        self.assertNotIn(" n=", lines[2], "у листа n нет")
        self.assertEqual(lines[2].strip().split(" ")[:4], ["p0t0", "text", '"p0t0"', f"{C.weight(root['children'][0]['children'][0])}B"])

    def test_text_is_cut_to_80_chars_without_an_ellipsis(self):
        for length, expected in ((100, 80), (81, 80), (80, 80), (79, 79)):
            t = "щ" * length
            line = self.lines(frame("r", [leaf("t", text=t)], layout=stack()))[1]
            self.assertIn('text="' + "щ" * expected + '"', line, length)
            self.assertNotIn("…", line)
            self.assertNotIn("щ" * (expected + 1), line)
        self.assertNotIn("text=", self.lines(frame("r", [leaf("t", text="")], layout=stack()))[1], "пустой текст не печатается")

    def test_box_only_where_the_canon_has_it(self):
        root = frame("r", [leaf("a", "rect", box={"x": 10, "y": 20, "w": 30, "h": 40}), leaf("b", "rect")],
                     box={"x": 0, "y": 0, "w": 100, "h": 100})
        lines = self.lines(root)
        self.assertIn("box=0,0,100,100", lines[0])
        self.assertIn("box=10,20,30,40", lines[1])
        self.assertNotIn("box=", lines[2])

    def test_layout_and_size_are_printed_as_the_canon_says(self):
        lay = {"direction": "row", "gap": 8, "crossGap": 4, "wrap": True, "inferred": True,
               "padding": {"top": 1, "right": 2, "bottom": 3, "left": 4}, "align": {"main": "space-between", "cross": "center"}}
        line = self.lines(frame("r", [leaf("a", "rect")], layout=lay, size={"w": 600, "h": "hug"}))[0]
        self.assertTrue(line.endswith("row gap=8 crossGap=4 pad=1/2/3/4 main=space-between cross=center wrap inferred w=600 h=hug"), line)

    def test_zero_values_and_absent_alignment_are_not_invented(self):
        lay = {"direction": "column", "gap": 0, "padding": {"top": 0, "right": 0, "bottom": 0, "left": 0},
               "align": {"main": "start"}, "stated": True}
        line = self.lines(frame("r", [leaf("a", "rect")], layout=lay))[0]
        self.assertTrue(line.endswith("column main=start stated"), line)
        self.assertNotIn("cross=", line, "нет align.cross в каноне — значит и в строке (это не start)")
        self.assertNotIn("gap=", line)
        self.assertNotIn("pad=", line)

    def test_fractions_keep_their_digits_and_uniform_padding_is_one_number(self):
        lay = {"direction": "column", "gap": 12.5, "padding": {"top": 16, "right": 16, "bottom": 16, "left": 16}}
        line = self.lines(frame("r", [leaf("a", "rect")], layout=lay, size={"w": 33.33, "h": "fill"}))[0]
        self.assertTrue(line.endswith("column gap=12.5 pad=16 w=33.33 h=fill"), line)

    def test_a_single_axis_of_size_is_printed_alone(self):
        self.assertTrue(self.lines(frame("r", [leaf("a", "rect")], size={"h": 40}))[0].endswith(" h=40"))

    def test_instance_line_names_the_component_not_the_variant_string(self):
        root = frame("r", [clone(real_nodes()["2:153703"], "x")], layout=stack())
        line = self.lines(root)[1]
        self.assertIn(f'instance "Steps=OFF" <Modal Header> {C.weight(root["children"][0])}B', line)

    def test_art_and_occluded_are_marked(self):
        root = frame("r", [frame("hero", [], art={"slug": "hero-banner", "nodes": 4}), frame("back", [leaf("x", "rect")], occluded=True)])
        lines = self.lines(root)
        self.assertIn(" art=hero-banner", lines[1])
        self.assertIn(" occluded", lines[2])
        self.assertNotIn(" occluded", lines[3], "флаг стоит на верхнем узле, как в каноне")

    def test_names_are_quoted_and_escaped_so_spaces_and_quotes_do_not_break_the_line(self):
        root = frame("r", [leaf("a", "rect", name='He said "hi" <b> x')])
        line = self.lines(root)[1]
        self.assertIn('"He said \\"hi\\" <b> x"', line)

    def test_nameless_node_has_an_empty_name(self):
        n = {"id": "z", "kind": "rect"}
        self.assertEqual(self.lines(frame("r", [n]))[1].strip().split(" ")[:3], ["z", "rect", '""'])

    def test_structured_tree_is_the_same_map(self):
        root = screen(3, ds_panel)
        tree = OL.light_tree(env_of(root))
        flat, stack_ = [], [tree]
        while stack_:
            n = stack_.pop()
            flat.append(n)
            stack_.extend(reversed(n.get("children", [])))
            self.assertEqual(n.get("nChildren", 0), len(n.get("children", [])))
        self.assertEqual([n["id"] for n in flat], line_ids(self.lines(root)))
        self.assertEqual({n["id"]: n["weight"] for n in flat}, C.weights(root))
        self.assertNotIn("children", flat[-1])


# ── потолок дайджеста ─────────────────────────────────────────────────────────
class Lightness(unittest.TestCase):
    def check_quarter(self, root, budget=24000):
        env = env_of(root)
        out = OL.build_outline(env, budget, frame_url=URL)
        canon = C.weight(root)
        self.assertGreater(canon, budget, "экран большой: корень не влезает в одну секцию")
        self.assertLessEqual(len(C.dumps(out).encode("utf-8")) * OL.DIGEST_RATIO, canon,
                             f"ответ {len(C.dumps(out))} Б против канона {canon} Б")
        return out

    def test_big_screen_on_a_design_system_answer_is_at_least_4x_lighter(self):
        root = screen(60, ds_panel)                       # ≈ 176 КБ: размер «крупного экрана» из §6.1
        self.assertGreater(C.weight(root), 130 * 1024)
        out = self.check_quarter(root)
        self.assertEqual(out["stats"]["instances"], 120)
        self.assertEqual(out["folded"]["leaves"], True)
        self.assertNotIn("depth", out["folded"], "хватило свернуть простые листья")
        instances = [ln for ln in out["outline"] if " instance " in ln]
        self.assertEqual(len(instances), 120, "кандидаты резолва (инстансы) остались в дереве все до одного")
        self.assertEqual(out["folded"]["hiddenNodes"], out["stats"]["nodes"] - len(out["outline"]))
        self.assertTrue(all(" folded" in ln for ln in out["outline"] if ln.strip().startswith("panel")),
                        "панели с непоказанными листьями помечены")

    def test_text_heavy_screen_is_cut_the_same_way_and_stays_light(self):
        out = self.check_quarter(screen(35, text_panel))
        self.assertEqual(out["folded"]["leaves"], True)
        self.assertTrue(all("text=" not in ln for ln in out["outline"]), "текстовые листья ушли первыми")

    def test_weight_of_the_digest_is_counted_in_bytes_so_cyrillic_names_do_not_break_the_quarter(self):
        out = self.check_quarter(screen(60, ru_panel))
        self.assertTrue("folded" in out)
        self.assertEqual(OL._size(["щ"]), len('["щ"]'.encode("utf-8")))

    def test_when_leaves_are_not_enough_whole_levels_fold(self):
        root = cells(40, 60)                              # 2400 пустых рамок, ≈ 170 КБ: листьев text/rect/… нет
        out = self.check_quarter(root)
        self.assertNotIn("leaves", out["folded"], "простых листьев нет — свёртывать нечего, кроме глубины")
        self.assertEqual(out["folded"]["depth"], 1)
        self.assertEqual(len(out["outline"]), 1 + 40)
        self.assertEqual(out["folded"]["hiddenNodes"], 2400)
        self.assertTrue(all(" folded" in ln for ln in out["outline"][1:]), "граничные узлы помечены")
        self.assertIn("свёрнуто", out["next"])

    def test_digest_that_already_fits_a_quarter_is_not_touched(self):
        root = wall(20, 6, "22:63141")
        out = OL.build_outline(env_of(root), 24000, frame_url=URL)
        self.assertGreater(C.weight(root), 100 * 1024)
        self.assertNotIn("folded", out)
        self.assertEqual(len(out["outline"]), out["stats"]["nodes"])
        self.assertLessEqual(len(C.dumps(out).encode("utf-8")) * 4, C.weight(root))

    def test_canon_that_fits_one_section_is_never_folded(self):
        root = screen(3, text_panel)
        out = OL.build_outline(env_of(root), C.weight(root))
        self.assertEqual((ids(out["sections"]), "folded" in out), (["dash"], False))
        self.assertEqual(len(out["outline"]), out["stats"]["nodes"])

    def test_deep_nesting_cannot_blow_the_digest_up(self):
        node = leaf("n299", "frame")
        for i in range(298, -1, -1):
            node = frame(f"n{i}", [node])                          # цепочка из 300 вложенных рамок: канон ≈ 14 КБ
        canon = C.weight(node)
        self.assertLess(canon, 24000)
        out = OL.build_outline(env_of(node), 24000)
        self.assertEqual(len(out["sections"]), 1, "канон влезает в одну секцию")
        self.assertIn("depth", out["folded"], "а строки с отступом глубины тяжелее самого канона — дерево свёрнуто")
        self.assertLessEqual(len(C.dumps(out["outline"]).encode("utf-8")), 24000)
        self.assertEqual(out["folded"]["hiddenNodes"], 300 - len(out["outline"]))

    def test_the_map_is_never_empty_even_when_nothing_fits(self):
        out = OL.build_outline(env_of(screen(6, ds_panel)), 100)
        self.assertEqual(out["folded"]["depth"], 0)
        self.assertEqual(len(out["outline"]), 1)
        self.assertEqual(line_ids(out["outline"]), ["dash"])
        self.assertGreater(len(out["sections"]), 1, "секции при этом полные: oversize-листья названы")

    def test_fold_does_not_touch_sections_unproven_or_hashes(self):
        root = screen(60, ds_panel)
        folded = OL.build_outline(env_of(root), 24000)
        self.assertTrue("folded" in folded, "экран большой: дерево свёрнуто")
        self.assertTrue(folded["sections"] == OL.section_list(env_of(root), 24000), "секции и хеши свёртка не трогает")
        self.assertEqual(folded["stats"]["nodes"], len(list(C.iter_nodes(root))), "счётчики — по всему канону")
        self.assertEqual(folded["unproven"], [])


# ── unproven ──────────────────────────────────────────────────────────────────
def abs_box(i, x, y, w=10, h=10, kind="rect"):
    return leaf(i, kind, box={"x": x, "y": y, "w": w, "h": h})


class Unproven(unittest.TestCase):
    def entries(self, root, budget=10 ** 9):
        out = OL.build_outline(env_of(root), budget)
        return out["unproven"], out

    def test_container_without_layout_names_all_its_children(self):
        root = frame("c", [abs_box("a", 0, 0), abs_box("b", 20, 0), abs_box("c2", 40, 0)], box={"x": 0, "y": 0, "w": 100, "h": 100})
        items, out = self.entries(root)
        self.assertEqual(items, [{"id": "c", "name": "c", "reason": "absolute-children", "count": 3, "children": "a,b,c2"}])
        self.assertEqual(out["stats"]["unproven"], 1)
        self.assertNotIn("unprovenDropped", out)

    def test_one_absolute_child_is_an_overlay_not_a_layout_and_stays_silent(self):
        items, out = self.entries(frame("c", [abs_box("a", 0, 0)], box={"x": 0, "y": 0, "w": 100, "h": 100}))
        self.assertEqual((items, out["stats"]["unproven"]), ([], 0))

    def test_declared_or_proven_container_is_silent_even_with_pinned_children(self):
        pinned = frame("c", [leaf("flow", "rect"), abs_box("pin1", 5, 5), abs_box("pin2", 9, 9)], layout=stack())
        inferred = frame("d", [leaf("x", "rect"), leaf("y", "rect")], layout={"direction": "row", "inferred": True})
        items, _ = self.entries(frame("r", [pinned, inferred], layout=stack()))
        self.assertEqual(items, [])

    def test_instances_art_and_leaves_are_not_containers(self):
        inst = real_nodes()["2:153704"]
        art = frame("art", [], art={"slug": "x", "nodes": 3})
        root = frame("r", [clone(inst, "i"), art, leaf("t", text="x")], layout=stack())
        self.assertEqual(self.entries(root)[0], [])
        two_kids_instance = {"id": "k", "kind": "instance", "component": {"figmaName": "Local", "origin": "local"},
                             "children": [abs_box("k1", 0, 0), abs_box("k2", 20, 0)]}
        self.assertEqual(self.entries(frame("r", [two_kids_instance], layout=stack()))[0], [],
                         "рамка — контейнер раскладки, инстанс — нет (как в ingest/layout.py)")

    def test_layout_hints_are_reported_in_a_compact_form(self):
        a = abs_box("bg", 0, 0, 100, 100)
        a["layoutHints"] = {"w": "fill", "h": "fill"}
        b = frame("row", [leaf("i", "rect")], layout=stack(direction="row"),
                  layoutHints={"main": "space-between", "grid": {"confidence": "high", "columns": 4}})
        c = frame("g", [leaf("j", "rect")], layout=stack(direction="row"), layoutHints={"grid": {"confidence": "medium"}})
        items, _ = self.entries(frame("r", [a, b, c], layout=stack()))
        self.assertEqual([(i["id"], i["reason"], i["hints"]) for i in items],
                         [("bg", "layout-hints", "w=fill h=fill"),
                          ("row", "layout-hints", "main=space-between grid=high columns=4"),
                          ("g", "layout-hints", "grid=medium")])

    def test_two_reasons_on_one_node_are_two_entries_children_first(self):
        c = frame("c", [abs_box("a", 0, 0), abs_box("b", 20, 0)], layoutHints={"w": "fill"})
        items, out = self.entries(frame("r", [c], layout=stack()))
        self.assertEqual([(i["id"], i["reason"]) for i in items], [("c", "absolute-children"), ("c", "layout-hints")])
        self.assertEqual(out["stats"]["unproven"], 2)

    def test_list_is_capped_at_60_with_an_honest_count_of_the_rest(self):
        boxes = [frame(f"c{i:02d}", [abs_box(f"c{i:02d}a", 0, 0), abs_box(f"c{i:02d}b", 20, 0)]) for i in range(75)]
        items, out = self.entries(frame("r", boxes, layout=stack()))
        self.assertEqual(len(items), OL.MAX_UNPROVEN)
        self.assertEqual(OL.MAX_UNPROVEN, 60)
        self.assertEqual([i["id"] for i in items], [f"c{i:02d}" for i in range(60)], "первые 60 в порядке документа")
        self.assertEqual(out["unprovenDropped"], 15)
        self.assertEqual(out["stats"]["unproven"], 75)

    def test_at_most_20_child_ids_and_the_true_count(self):
        kids = [abs_box(f"k{i:02d}", i * 12, 0) for i in range(25)]
        items, _ = self.entries(frame("c", kids))
        self.assertEqual(items[0]["count"], 25)
        self.assertEqual(items[0]["children"].split(","), [f"k{i:02d}" for i in range(20)])
        self.assertEqual(OL.MAX_ABSOLUTE_CHILD_IDS, 20)

    def test_hidden_content_and_library_anatomy_are_not_screen_layout(self):
        hidden = frame("back", [frame("c", [abs_box("a", 0, 0), abs_box("b", 20, 0)], layoutHints={"w": "fill"})], occluded=True)
        anatomy = {"id": "lib", "kind": "instance", "component": {"figmaName": "Btn", "childrenRole": "content", "origin": "library"},
                   "layoutHints": {"h": "fill"},
                   "children": [frame("inner", [abs_box("i1", 0, 0), abs_box("i2", 20, 0)])]}
        items, _ = self.entries(frame("r", [hidden, anatomy], layout=stack()))
        self.assertEqual([(i["id"], i["reason"]) for i in items], [("lib", "layout-hints")],
                         "под occluded молчим; анатомия чёрного ящика молчит, но подсказка на самом инстансе остаётся")

    def test_a_root_without_layout_counts_too(self):
        items, _ = self.entries(frame("root", [abs_box("a", 0, 0), abs_box("b", 20, 0)], box={"x": 0, "y": 0, "w": 50, "h": 50}))
        self.assertEqual([i["id"] for i in items], ["root"])

    def test_agrees_with_the_layout_inference_of_task_1_7(self):
        def box(x, y, w, h):
            return {"x": x, "y": y, "w": w, "h": h}

        def build():
            overlap = frame("over", [abs_box("o1", 10, 10, 100, 100), abs_box("o2", 60, 60, 100, 100)],
                            box=box(0, 300, 200, 200))
            stackable = frame("stk", [abs_box("s1", 0, 0, 50, 20), abs_box("s2", 0, 30, 50, 20), abs_box("s3", 0, 60, 50, 20)],
                              box=box(0, 0, 50, 80))
            hero = frame("hero", [abs_box("bg", 0, 600, 400, 100), abs_box("btn", 20, 620, 120, 40)], box=box(0, 600, 400, 100))
            lone = frame("lone", [abs_box("only", 5, 905, 20, 20)], box=box(0, 900, 100, 50))
            return frame("root", [stackable, overlap, hero, lone], box=box(0, 0, 400, 1000))

        before = OL.unproven_entries(build())
        self.assertEqual({e["id"] for e in before if e["reason"] == "absolute-children"} - {"root"}, {"stk", "over", "hero"},
                         "без вывода раскладки все контейнеры с ≥ 2 абсолютными детьми — кандидаты")
        root = build()
        report = LY.infer_layout(root)
        refused = {i["id"] for i in report["unproven"]["items"]}
        mine = {e["id"] for e in OL.unproven_entries(root) if e["reason"] == "absolute-children"}
        self.assertEqual(refused, mine, "то, что отказал вывод раскладки, и есть unproven; доказанное и оверлей молчат")
        self.assertIn("over", mine)
        self.assertNotIn("stk", mine)
        self.assertNotIn("lone", mine)
        hints = [e for e in OL.unproven_entries(root) if e["reason"] == "layout-hints"]
        self.assertIn("bg", {e["id"] for e in hints}, "подсказки вывода раскладки (оверлей-фон) попадают в unproven")


# ── goalImage ─────────────────────────────────────────────────────────────────
class GoalImage(RepoCase):
    def grouped(self):
        return frame("c", [abs_box("a", 0, 0), abs_box("b", 20, 0)], box={"x": 0, "y": 0, "w": 100, "h": 100})

    def attach(self):
        (self.root / "shots").mkdir(exist_ok=True)
        (self.root / "shots" / "goal.png").write_bytes(K.png_bytes(8, 6))
        return T.call("daiver_track_manifest", self.root, {"track": TRACK, "frame": "main", "rasterPath": "shots/goal.png"})

    def test_no_picture_is_null_with_an_instruction(self):
        self.repo(self.grouped())
        out = self.outline()
        self.assertIsNone(out["goalImage"])
        self.assertEqual(out["goalImageNext"], OL.GOAL_MISSING)
        self.assertIn("rasterPath", out["goalImageNext"])

    def test_attached_picture_is_a_path_from_the_repo_root_and_says_to_open_it(self):
        self.repo(self.grouped())
        self.attach()
        out = self.outline()
        self.assertEqual(out["goalImage"], ".daiver/tracks/STOR-100/visual-requirements.png")
        self.assertTrue((self.root / out["goalImage"]).is_file())
        self.assertEqual(out["goalImageNext"], OL.GOAL_OPEN)
        self.assertIn("daiver_source_nodes", out["goalImageNext"], "сначала декларации: они сильнее впечатления")

    def test_vision_off_says_not_to_open_it(self):
        self.repo(self.grouped(), config="verify:\n  vision: off\n")
        self.attach()
        out = self.outline()
        self.assertEqual(out["goalImageNext"], OL.GOAL_VISION_OFF)
        self.assertEqual(out["goalImage"], ".daiver/tracks/STOR-100/visual-requirements.png", "путь отдаётся, открывать не велено")

    def test_picture_is_not_needed_when_there_is_nothing_to_group(self):
        self.repo(page())
        self.assertEqual(self.outline()["goalImageNext"], OL.GOAL_NOT_NEEDED)
        self.attach()
        out = self.outline()
        self.assertEqual(out["goalImageNext"], OL.GOAL_NOT_NEEDED)
        self.assertIsNotNone(out["goalImage"])

    def test_registered_but_missing_file_counts_as_no_picture(self):
        self.repo(self.grouped())
        self.attach()
        (self.root / ".daiver/tracks/STOR-100/visual-requirements.png").unlink()
        out = self.outline()
        self.assertIsNone(out["goalImage"])
        self.assertEqual(out["goalImageNext"], OL.GOAL_MISSING)


# ── ссылки на узлы ────────────────────────────────────────────────────────────
class NodeUrl(RepoCase):
    def test_pixso_item_id_is_replaced(self):
        self.assertEqual(OL.node_url_template("https://pixso.example/a?item-id=592:50458", "pixso"),
                         "https://pixso.example/a?item-id={id}")
        self.assertEqual(OL.node_url_template("https://pixso.net/app/design/X?page-id=1:2&item-id=5:6&mode=dev#top", "pixso"),
                         "https://pixso.net/app/design/X?page-id=1:2&item-id={id}&mode=dev#top")

    def test_missing_parameter_is_added(self):
        self.assertEqual(OL.node_url_template("https://pixso.example/a", "pixso"), "https://pixso.example/a?item-id={id}")
        self.assertEqual(OL.node_url_template("https://pixso.example/a?page-id=1:2", "pixso"),
                         "https://pixso.example/a?page-id=1:2&item-id={id}")
        self.assertEqual(OL.node_url_template("https://pixso.example/a#frag", "pixso"), "https://pixso.example/a?item-id={id}#frag")

    def test_figma_uses_node_id_with_dashes(self):
        tpl = OL.node_url_template("https://www.figma.com/design/KEY/Name?node-id=1-2&t=abc", "figma-json-a")
        self.assertEqual(tpl, "https://www.figma.com/design/KEY/Name?node-id={id}&t=abc")
        self.assertEqual(OL.node_url(tpl, "12:34"), "https://www.figma.com/design/KEY/Name?node-id=12-34&t=abc")
        self.assertEqual(OL.node_url_template("https://www.figma.com/file/K", "figma-html"), "https://www.figma.com/file/K?node-id={id}")

    def test_pixso_link_keeps_the_colon(self):
        tpl = OL.node_url_template(URL, "pixso")
        self.assertEqual(OL.node_url(tpl, "592:50458"), "https://pixso.example/a?item-id=592:50458")

    def test_a_path_inside_an_instance_links_to_the_instance(self):
        tpl = OL.node_url_template(URL, "pixso")
        self.assertEqual(OL.node_url(tpl, "2:153704;2:170352/2:170446"), "https://pixso.example/a?item-id=2:153704")

    def test_synthetic_and_measured_ids_have_no_design_node_and_no_link(self):
        tpl = OL.node_url_template(URL, "pixso")
        for nid in ("inferred:2:1+2:2", "dom:0/1/2", "code:s1/a", "", None):
            self.assertIsNone(OL.node_url(tpl, nid), nid)

    def test_no_url_no_template_and_nothing_is_invented(self):
        for url in (None, "", "   ", 5, "ftp://x/y", "not a url", "pixso://frame/1"):
            self.assertIsNone(OL.node_url_template(url, "pixso"), url)
        for fmt in ("dom", "code", None, "weird"):
            self.assertIsNone(OL.node_url_template(URL, fmt), fmt)
        self.assertIsNone(OL.node_url(None, "2:1"))

    def test_outline_carries_the_template_of_the_frame_url_from_the_manifest(self):
        self.repo(page(), frame_url="https://pixso.example/a?item-id=2:1")
        out = self.outline()
        self.assertEqual(out["nodeUrlTemplate"], "https://pixso.example/a?item-id={id}")
        self.assertIn("A;B/C", out["nodeUrlNote"])
        self.assertNotIn("дефис", out["nodeUrlNote"])

    def test_outline_without_a_frame_url_says_null(self):
        self.repo(page(), frame_url=None)
        out = self.outline()
        self.assertIsNone(out["nodeUrlTemplate"])
        self.assertNotIn("nodeUrlNote", out)

    def test_figma_note_tells_about_the_dash(self):
        out = OL.build_outline(env_of(page(), "figma-json-a"), 24000, frame_url="https://www.figma.com/design/K/N?node-id=1-2")
        self.assertIn("дефис", out["nodeUrlNote"])


# ── daiver_section (чистая часть) ─────────────────────────────────────────────
class SectionBody(unittest.TestCase):
    def setUp(self):
        self.root = screen(6, ds_panel)
        self.env = env_of(self.root)
        self.budget = C.weight(self.root["children"][0]) + 1

    def sections(self, budget=None):
        return OL.section_list(self.env, budget or self.budget)

    def test_every_outline_id_opens_and_nothing_else_does(self):
        secs = self.sections()
        self.assertGreater(len(secs), 3)
        good = set(ids(secs))
        for s in secs:
            out = OL.build_section(self.env, s["id"], self.budget)
            self.assertEqual(out["section"], s)
            self.assertLessEqual(C.weight(out["node"]), self.budget, "тело секции ≤ бюджета")
            self.assertEqual(C.subtree_hash(out["node"]), s["hash"])
        for n in C.iter_nodes(self.root):
            if n["id"] in good:
                continue
            with self.assertRaises(OL.OutlineError, msg=n["id"]):
                OL.build_section(self.env, n["id"], self.budget)

    def test_ancestors_are_the_chain_root_to_parent_with_layout_and_surface(self):
        target = self.sections()[0]
        out = OL.build_section(self.env, target["id"], self.budget)
        chain = C.ancestors_of(self.root, target["id"])
        self.assertEqual([a["id"] for a in out["ancestors"]], [a["id"] for a in chain])
        first = out["ancestors"][0]
        self.assertEqual(first["id"], "dash")
        self.assertEqual((first["layout"], first["size"], first["box"], first["style"]),
                         (self.root["layout"], self.root["size"], self.root["box"], self.root["style"]))
        self.assertNotIn("children", first)
        self.assertNotIn("text", first)

    def test_instance_ancestor_keeps_its_component_name_only(self):
        inst = real_nodes()["2:153703"]
        local = copy.deepcopy(clone(inst, "w"))
        local["children"] = [text_node("w-a", "один"), text_node("w-b", "два")]
        local["component"]["childrenRole"] = "fallback"
        local["component"]["origin"] = "local"
        root = frame("r", [local], layout=stack())
        out = OL.build_section(env_of(root), "w-a", 20)
        anc = out["ancestors"][-1]
        self.assertEqual((anc["id"], anc["figmaName"]), (local["id"], "Modal Header"))
        self.assertNotIn("component", anc)

    def test_root_that_is_heavier_than_the_budget_is_not_given_out_whole(self):
        with self.assertRaises(OL.OutlineError) as cm:
            OL.build_section(self.env, "dash", self.budget)
        msg = str(cm.exception)
        self.assertIn("тяжелее бюджета", msg)
        self.assertIn("канон целиком не отдаётся", msg)
        self.assertIn("panel0", msg, "и сказано, какие id брать")

    def test_node_inside_a_section_points_to_its_section(self):
        with self.assertRaises(OL.OutlineError) as cm:
            OL.build_section(self.env, "0x", self.budget)           # текст внутри panel0
        self.assertIn("внутри секции «panel0»", str(cm.exception))

    def test_unknown_id_is_a_clear_error(self):
        with self.assertRaises(OL.OutlineError) as cm:
            OL.build_section(self.env, "no-such-node", self.budget)
        self.assertIn("в каноне кадра нет", str(cm.exception))
        self.assertIn("sections[].id", str(cm.exception))

    def test_spine_error_lists_at_most_ten_ids_and_the_true_count(self):
        root = frame("r", [panel(i, 1) for i in range(15)], layout=stack())
        with self.assertRaises(OL.OutlineError) as cm:
            OL.build_section(env_of(root), "r", C.weight(root["children"][0]))
        msg = str(cm.exception)
        self.assertIn("всего 15", msg)
        self.assertIn("p9", msg)
        self.assertNotIn("p10", msg)
        self.assertTrue(msg.rstrip().endswith("…"))

    def test_a_section_id_is_valid_only_for_the_budget_that_produced_it(self):
        fine = self.sections(C.weight(self.root["children"][0]["children"][0]) + 1)
        small = next(s for s in fine if s["depth"] == 2)             # ребёнок панели: при бюджете панели он внутри неё
        OL.build_section(self.env, small["id"], C.weight(self.root["children"][0]["children"][0]) + 1)
        with self.assertRaises(OL.OutlineError):
            OL.build_section(self.env, small["id"], self.budget)

    def test_oversize_leaf_comes_whole_and_is_flagged(self):
        root = copy.deepcopy(self.root)
        big = C.find_node(root, "panel2")["children"][1]
        big["text"] = "x" * 3000
        env = env_of(root)
        budget = 1500
        out = OL.build_section(env, big["id"], budget)
        self.assertTrue(out["section"]["oversize"])
        self.assertGreater(C.weight(out["node"]), budget)
        self.assertEqual(out["node"], big)
        self.assertIn("не режется", out["next"])

    def test_occluded_section_says_it_is_not_built(self):
        back = frame("back", [panel(7), panel(8)], occluded=True, layout=stack())
        root = frame("r", [back, panel(1)], layout=stack())
        out = OL.build_section(env_of(root), "p7", C.weight(panel(1)))
        self.assertTrue(out["section"]["occluded"])
        self.assertIn("не строится", out["next"])


# ── инструменты на репо ───────────────────────────────────────────────────────
class ToolsOnRepo(RepoCase):
    def test_outline_and_section_tools_work_on_a_stored_canon(self):
        root = screen(4, ds_panel)
        self.repo(root)
        out = self.outline(weightBudget=C.weight(root["children"][0]) + 1)
        self.assertEqual((out["track"], out["frame"]), (TRACK, "main"))
        first = out["sections"][0]
        body = self.section(first["id"], weightBudget=C.weight(root["children"][0]) + 1)
        self.assertEqual(body["section"], first)
        self.assertEqual(body["node"], C.find_node(root, first["id"]))

    def test_arguments_are_checked_by_hand_with_one_message(self):
        self.repo(page())
        self.assertIn("неизвестный аргумент «foo»", self.refusal("daiver_outline", track=TRACK, foo=1))
        msg = self.refusal("daiver_section", foo=1)
        self.assertIn("неизвестный аргумент «foo»", msg)
        self.assertIn("нет обязательного аргумента «track»", msg)
        self.assertIn("нет обязательного аргумента «sectionId»", msg)
        self.assertIn("ожидается string", self.refusal("daiver_section", track=TRACK, sectionId=5))
        self.assertIn("непустая строка", self.refusal("daiver_section", track=TRACK, sectionId=""))

    def test_no_track_no_canon_broken_canon_unknown_frame_are_named(self):
        self.assertIn("daiver_pixso_pull", self.refusal("daiver_outline", track=TRACK))
        paths = self.repo(page())
        self.assertIn("кадра «nope» в треке нет", self.refusal("daiver_outline", track=TRACK, frame="nope"))
        paths["canonical"].write_text("{", encoding="utf-8")
        self.assertIn("не читается", self.refusal("daiver_outline", track=TRACK))
        paths["canonical"].unlink()
        self.assertIn("daiver_normalize", self.refusal("daiver_outline", track=TRACK))
        self.assertIn("daiver_normalize", self.refusal("daiver_section", track=TRACK, sectionId="root"))

    def test_track_name_is_validated_like_everywhere(self):
        self.assertIn("имя трека", self.refusal("daiver_outline", track="../x"))
        self.assertIn("имя кадра", self.refusal("daiver_outline", track=TRACK, frame="a/b"))

    def test_canon_built_from_another_export_is_warned_about(self):
        self.repo(page(), stats={"sourceSha256": "0" * 64})
        self.assertIn("другому экспорту", self.outline()["warnings"][0])
        self.assertIn("другому экспорту", self.section("root")["warnings"][0])

    def test_oversize_leaves_are_called_out_for_the_operator(self):
        root = copy.deepcopy(page())
        root["children"][0]["children"][0]["text"] = "y" * 500
        self.repo(root)
        out = self.outline(weightBudget=300)
        self.assertEqual(out["stats"]["oversize"], 1)
        self.assertTrue(any("p0t0" in w and "workflow.weightBudget" in w for w in out["warnings"]), out["warnings"])

    def test_frame_argument_selects_the_frame(self):
        self.repo(page())
        self.assertEqual(self.outline(frame="main")["frame"], "main")

    def test_every_answer_is_plain_json(self):
        self.repo(screen(5, ds_panel))
        for out in (self.outline(weightBudget=3000), self.section(self.outline(weightBudget=3000)["sections"][0]["id"], weightBudget=3000)):
            self.assertEqual(json.loads(json.dumps(out, ensure_ascii=False)), out)


# ── образец «Modal» ───────────────────────────────────────────────────────────
class OnModalFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        shutil.copyfile(str(FIXTURE), str(cls.root / "dsl.json"))
        T.call("daiver_pixso_pull", cls.root, {"track": TRACK, "dslPath": "dsl.json", "frameUrl": URL})
        cls.norm = T.call("daiver_normalize", cls.root, {"track": TRACK})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def outline(self, **kw):
        return T.call("daiver_outline", self.root, dict({"track": TRACK}, **kw))

    def test_the_whole_screen_is_one_section_and_its_hash_is_the_roots(self):
        out = self.outline()
        self.assertEqual(out["weightBudget"], 24000)
        self.assertEqual(len(out["sections"]), 1)
        s = out["sections"][0]
        self.assertEqual((s["id"], s["name"], s["kind"], s["depth"], s["nDescendants"], s["path"]),
                         ("2:153702", "Modal", "frame", 0, 3, "Modal"))
        self.assertEqual((s["weight"], s["hash"]), (self.norm["weight"], self.norm["rootHash"]), "хеш и вес — те же, что у normalize")
        self.assertEqual(out["stats"], {"nodes": 4, "instances": 3, "weight": 3101, "sections": 1, "unproven": 0})

    def test_digest_lines_of_the_real_screen(self):
        self.assertEqual(self.outline()["outline"], [
            '2:153702 frame "Modal" 3101B n=3 box=0,0,600,245 column pad=20 main=start cross=center stated w=600 h=hug',
            '  2:153703 instance "Steps=OFF" <Modal Header> 659B column gap=4 pad=0/0/20/0 main=start cross=start stated w=fill h=hug',
            '  22:63141 instance "Steps=OFF" <Modal Header> 1063B column gap=4 pad=0/0/20/0 main=start cross=start stated w=fill h=hug',
            '  2:153704 instance "Variant=Default" <Modal Action> 886B column gap=16 pad=20/0/0/0 main=start cross=start stated w=560 h=hug',
        ])

    def test_fully_declared_layout_has_nothing_unproven(self):
        out = self.outline()
        self.assertEqual(out["unproven"], [])
        self.assertNotIn("unprovenDropped", out)
        self.assertEqual(out["goalImageNext"], OL.GOAL_NOT_NEEDED)
        self.assertIsNone(out["goalImage"])

    def test_link_template_comes_from_the_frame_url_pull_recorded(self):
        self.assertEqual(self.outline()["nodeUrlTemplate"], "https://pixso.example/a?item-id={id}")

    def test_small_budget_cuts_the_root_into_the_three_instances_in_document_order(self):
        out = self.outline(weightBudget=1500)
        self.assertEqual([(s["id"], s["weight"], s["figmaName"]) for s in out["sections"]],
                         [("2:153703", 659, "Modal Header"), ("22:63141", 1063, "Modal Header"), ("2:153704", 886, "Modal Action")])
        self.assertEqual({s["path"] for s in out["sections"]}, {"Modal > Steps=OFF", "Modal > Variant=Default"})
        self.assertEqual(len(out["outline"]), 4, "дайджест маленького экрана полный: он укладывается в свой потолок")
        self.assertNotIn("folded", out)

    def test_below_the_smallest_instance_each_one_is_an_oversize_leaf(self):
        out = self.outline(weightBudget=500)
        self.assertEqual([(s["id"], s.get("oversize")) for s in out["sections"]],
                         [("2:153703", True), ("22:63141", True), ("2:153704", True)])
        self.assertEqual(out["stats"]["oversize"], 3)

    def test_section_of_a_real_instance_carries_slots_and_the_ancestor_context(self):
        body = T.call("daiver_section", self.root, {"track": TRACK, "sectionId": "2:153704", "weightBudget": 1500})
        self.assertEqual(body["section"]["hash"], "7a7a580f471f")
        self.assertEqual([a["id"] for a in body["ancestors"]], ["2:153702"])
        self.assertEqual(body["ancestors"][0]["layout"]["direction"], "column")
        self.assertEqual(body["ancestors"][0]["style"]["radius"], 16)
        self.assertEqual([s["text"] for s in body["node"]["component"]["content"]], ["Текст3", "Текст11"])
        self.assertLessEqual(C.weight(body["node"]), 1500)

    def test_the_whole_screen_cannot_be_fetched_around_the_budget(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_section", self.root, {"track": TRACK, "sectionId": "2:153702", "weightBudget": 1500})
        self.assertIn("канон целиком не отдаётся", str(cm.exception))
        whole = T.call("daiver_section", self.root, {"track": TRACK, "sectionId": "2:153702"})
        self.assertEqual(whole["section"]["weight"], 3101, "а при бюджете, в который корень влезает, он и есть секция")

    def test_state_of_the_screen_is_untouched_by_reading(self):
        before = (self.root / ".daiver/tracks/STOR-100/visual-requirements.canonical.json").read_bytes()
        self.outline()
        T.call("daiver_section", self.root, {"track": TRACK, "sectionId": "2:153702"})
        self.assertEqual((self.root / ".daiver/tracks/STOR-100/visual-requirements.canonical.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
