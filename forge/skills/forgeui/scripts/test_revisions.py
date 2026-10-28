"""test_revisions.py — ревизии макета: pull поверх собранного кадра, регионы против собранной версии, сборка в существующий код, вердикт базы
(задача 6.3, спека §7.3, §10.1, §13.7, §14.4.6; INV-B15, G3, C18; QG-U22 (часть), P2).

Пинит: собранный кадр (все секции done) pull НЕ перезаписывает — семья целиком уходит в `versions/<n>/` (байт в байт), манифест получает версию и
`baseline`, растр уходит с версией, подпись плана слетает; недостроенный кадр перезаписывается, кадр-состояние не версионируется, тот же экспорт
ничего не меняет, занятый каталог версии — отказ без следов. Регионы против собранной версии с файлами, где они собраны (по цепочке версий тоже);
outline / section / резолв / план / build-state ревизии идут по регионам: секции — только изменённое, повторный pull недостроенной ревизии
переоткрывает лишь подвинутый регион; вердикты shared-частей план берёт у версии, их читают матчер и DS-гейт; код-гейт видит файлы каждой версии.
Вердикт базы: current / previous (не жёсткий гейт) / ambiguous / unknown. Образец «Modal» из Pixso проходит ревизию целиком.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
import test_build_state as TB  # noqa: E402
import test_plan_contract as TPC  # noqa: E402
from daiver import baseline as BL  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import resolve as RS  # noqa: E402
from daiver import states as STS  # noqa: E402
from daiver import status as ST  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import ds_gate as DS  # noqa: E402
from daiver.verify import verify_compare as VC  # noqa: E402
from test_frame_states import env, frame, inst, leaf, screen_root  # noqa: E402
from test_pixso_pull import synth  # noqa: E402
from test_verify_compare import snapshot  # noqa: E402

TRACK, URL = TB.TRACK, TB.URL
BUDGET = 400                                    # секции синтетического экрана — дети корня: head, body, save, help


def v1(p="b"):
    return screen_root(p)


def v2(p="b"):
    """Новая версия: в шапке другой текст (changed), в теле — новый блок (added), текст помощи убран (removed), кнопка как была (shared)."""
    n = copy.deepcopy(v1(p))
    head, body, save, _help = n["children"]
    head["component"]["content"][0]["text"] = "Новый заказ"
    body["children"].insert(1, frame(f"{p}new", [leaf(f"{p}newt", "Укажите удобное время доставки")], name="Delivery slot"))
    n["children"] = [head, body, save]
    return n


def variant_only(p="b"):
    """Ревизия, которая меняет только вариант кнопки: эксклюзивного содержимого у версий нет."""
    n = copy.deepcopy(v1(p))
    n["children"][2]["component"]["variants"] = {"Variant": "Disabled"}
    return n


DECIDE = {"Modal Header": ("component", "ModalHeader"), "Modal Action": ("component", "ModalAction")}


# ── чистые части ──────────────────────────────────────────────────────────────

def report(page_tree, new=None, base=None):
    res = STS.compare(env(base or v1()), env(new or v2()), "main v1", "main", gate=False)
    res.update(version=1)
    return VC.build_report(env(new or v2()), env(page_tree), None, None, None, None, [], set(), None, None, None, res)


class Verdict(unittest.TestCase):
    def test_current_when_the_page_shows_the_new_version(self):
        rep = report(v2("p"))
        self.assertEqual(rep["baseline"]["verdict"], "current")
        self.assertGreater(rep["baseline"]["newFound"], 0)
        self.assertEqual(rep["baseline"]["baseFound"], 0)
        self.assertNotIn("ПРЕЖНЯЯ", rep["nextStep"])

    def test_previous_when_the_page_shows_the_built_version_is_not_a_hard_gate(self):
        rep = report(v1("p"))
        self.assertEqual(rep["baseline"]["verdict"], "previous")
        self.assertNotEqual(rep["status"], "hard-gate-fail", "previous — не жёсткий гейт: MISSING и EXTRA и есть само изменение")
        self.assertTrue(rep["hardGate"]["pass"])
        self.assertTrue(rep["defects"], "дефекты изменения остаются в отчёте")
        self.assertTrue(rep["nextStep"].startswith("на странице ПРЕЖНЯЯ версия"), rep["nextStep"])
        self.assertIn("dev-сервер", rep["nextStep"])

    def test_ambiguous_when_both_versions_show(self):
        both = v2("p")
        both["children"].append(leaf("phelp", "Нужна помощь? Позвоните нам"))
        rep = report(both)
        self.assertEqual(rep["baseline"]["verdict"], "ambiguous")
        self.assertIn("actual.png", rep["nextStep"])

    def test_unknown_without_exclusive_content_and_when_the_base_is_unavailable(self):
        rep = report(variant_only("p"), new=variant_only())
        self.assertEqual(rep["baseline"]["verdict"], "unknown")
        self.assertIn("не различить", rep["baseline"]["reason"])
        self.assertEqual(BL.verdict({"available": False, "version": 2, "reason": "канона версии 2 нет"}, {"pairs": []}, {})["verdict"], "unknown")

    def test_a_screen_frame_report_has_no_baseline_verdict(self):
        rep = VC.build_report(env(v2()), env(v2("p")), None, None, None, None, [], set(), None, None)
        self.assertIsNone(rep["baseline"])


class CodeSections(unittest.TestCase):
    def test_one_layer_is_kept_as_is(self):
        layer = [{"id": "a", "outputs": ["x.tsx"]}, {"id": "b", "outputs": ["x.tsx"]}]
        self.assertEqual(BL.code_sections([layer]), layer, "без версий раскладка код-гейта прежняя")

    def test_revision_sections_merge_into_the_built_ones_by_id_and_by_shared_files(self):
        v1_secs = [{"id": "head", "outputs": ["m/Head.tsx"]}, {"id": "body", "outputs": ["m/Body.tsx"]}]
        cur = [{"id": "head", "outputs": ["m/Head.tsx", "m/HeadBadge.tsx"]}, {"id": "new", "outputs": ["m/Body.tsx", "m/Slot.tsx"]},
               {"id": "own", "outputs": ["m/Own.tsx"]}]
        self.assertEqual(BL.code_sections([v1_secs, cur]), [
            {"id": "head", "outputs": ["m/Head.tsx", "m/HeadBadge.tsx"]},
            {"id": "body", "outputs": ["m/Body.tsx", "m/Slot.tsx"]},
            {"id": "own", "outputs": ["m/Own.tsx"]}], "правка в файлах прежней сборки — второй вход того же компонента дал бы дубль дерева")


class PlanDecisions(unittest.TestCase):
    def test_the_ds_gate_reads_inherited_decisions_of_a_revision_plan(self):
        plan = {"sections": [{"id": "s", "nodes": [{"id": "a", "component": "ModalHeader"}]}],
                "baseline": {"inherited": [{"id": "b", "baseId": "b", "component": "ModalAction"}, {"id": "c", "baseId": "c", "resolution": "local"}]}}
        got, foreign = DS.plan_decisions(plan, {"a", "b", "c"})
        self.assertEqual(got, {"a": {"decision": "component", "component": "ModalHeader"}, "b": {"decision": "component", "component": "ModalAction"},
                               "c": {"decision": "local", "component": None}})
        self.assertEqual(foreign, [])


def open_item(case, out, cid):
    got = [i for i in out["open"] if i["id"] == cid]
    case.assertEqual(len(got), 1, out["open"])
    return got[0]


# ── мир на диске ──────────────────────────────────────────────────────────────

class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        TB.init_repo(self.root)
        TB.write_ds(self.root, ["ModalHeader", "ModalAction"])
        TB.build_repo(self.root, v1())
        self.n_pull = 0

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, **args):
        return T.call(name, self.root, dict({"track": TRACK}, **args))

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            self.call(name, **args)
        return str(cm.exception)

    @property
    def manifest(self):
        return TR.load(self.root, TRACK)

    def paths(self, frame_name="main"):
        return TR.frame_paths(self.root, self.manifest, frame_name)

    def resolve(self, frame_name=None):
        ctx = RS.Ctx(self.root, TRACK, frame_name)
        for c in ctx.state(RS.read_store(ctx.root, ctx.spath)).todo:
            decision, comp = DECIDE.get(c.lookup, ("local", None))
            v = {"nodeId": c.id, "decision": decision, "confidence": "high", "rationale": "в тесте ревизии"}
            if comp:
                v["component"] = comp
            self.call("daiver_resolve_finalize", verdict=v, **({"frame": frame_name} if frame_name else {}))
        self.call("daiver_resolve_report")

    def plan(self, frame_name=None, **kw):
        return self.call("daiver_plan_write", weightBudget=BUDGET, **({"frame": frame_name} if frame_name else {}), **kw)

    def build(self, frame_name=None, only=None):
        """Собрать секции по порядку (`only` — сколько): каждая пишет свой файл. → {id секции: файл}."""
        fa = {"frame": frame_name} if frame_name else {}
        files = {}
        bs = self.call("daiver_build_state", **fa)
        while bs["next"] is not None and (only is None or len(files) < only):
            sid = bs["next"]["sectionId"]
            self.call("daiver_build_state_set", sectionId=sid, status="in_progress", **fa)
            h = self.call("daiver_section", sectionId=sid, weightBudget=BUDGET, **fa)["section"]["hash"]
            rel = (bs["next"].get("outputs") or [f"src/modules/main/{sid.replace(':', '_')}.tsx"])[0]
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(f"// {sid}\n", encoding="utf-8")
            bs = self.call("daiver_build_state_set", sectionId=sid, status="done", outputs=[rel], hash=h, **fa)
            files[sid] = rel
        return files

    def built_v1(self):
        self.resolve()
        self.plan()
        return self.build()

    def set_canon(self, node, tag, frame_name="main"):
        """Канон и требование кадра (как после pull + normalize) — синтетический узел; тег делает хеш требования своим."""
        m = self.manifest
        paths = TR.frame_paths(self.root, m, frame_name)
        paths["requirement"].write_text(json.dumps({"dsl": tag}), encoding="utf-8")
        f = TR.get_frame(m, frame_name)
        f["requirement"]["sha256"] = TR.sha256_file(paths["requirement"])
        C.write_canonical(paths["canonical"], C.new_envelope(node, "pixso", {"sourceSha256": f["requirement"]["sha256"]}))
        TR.save(self.root, m)

    def pull(self, frame_name="main", dsl=None):
        self.n_pull += 1
        doc = dsl or synth(self.n_pull)
        rel = f".daiver/inbox/{frame_name}-{self.n_pull}.json"
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(json.dumps(doc), encoding="utf-8")
        return self.call("daiver_pixso_pull", dslPath=rel, frameUrl=URL if frame_name == "main" else f"{URL}-{frame_name}", frame=frame_name)

    def revise(self, node=None, tag="v2"):
        out = self.pull()
        self.set_canon(node or v2(), tag)
        return out


class Retire(World):
    def family(self):
        d = self.paths()["dir"]
        return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()
                and p.name not in ("track.yaml", "resolution-report.md") and not p.relative_to(d).as_posix().startswith("versions/")}

    def test_a_pull_over_a_built_frame_retires_the_whole_family_into_versions_1(self):
        self.built_v1()
        png = self.root / "goal.png"
        png.write_bytes(K.png_bytes(4, 4))
        self.call("daiver_track_manifest", frame="main", rasterPath="goal.png")
        self.call("daiver_track_manifest", frame="main", set={"frameBox": {"x": 0, "y": 0, "w": 4, "h": 4}})
        it = self.paths()["verify"] / "iter-1"
        it.mkdir(parents=True)
        (it / "snapshot.json").write_text("{}", encoding="utf-8")
        before, old_req = self.family(), dict(TR.get_frame(self.manifest, "main")["requirement"])
        self.assertIn("build-state.json", before)
        self.assertIn("verify/iter-1/snapshot.json", before)
        out = self.pull()
        self.assertEqual((out["mode"], out["version"]), ("revision", 1))
        self.assertTrue(any(n.startswith("REVISION — ") and "versions/1/" in n for n in out["notes"]), out["notes"])
        v = self.paths()["versions"] / "1"
        after = {p.relative_to(v).as_posix(): p.read_bytes() for p in sorted(v.rglob("*")) if p.is_file()}
        self.assertEqual(after, before, "вся семья ушла в versions/1/ байт в байт")
        for name in ("plan.json", "build-state.json", "resolutions.json", "visual-requirements.canonical.json", "visual-requirements.png", "verify"):
            self.assertFalse((self.paths()["dir"] / name).exists(), name)
        self.assertTrue((self.paths()["dir"] / "track.yaml").is_file(), "собственные файлы трека остаются на месте")
        self.assertTrue((self.paths()["dir"] / "resolution-report.md").is_file())
        f = TR.get_frame(self.manifest, "main")
        self.assertEqual(f["baseline"], {"kind": "version", "version": 1})
        rec = f["versions"][0]
        self.assertEqual((rec["n"], rec["requirement"], rec["raster"], rec["frameBox"]), (1, old_req, "visual-requirements.png", {"x": 0, "y": 0, "w": 4, "h": 4}))
        self.assertNotIn("raster", f, "растр ушёл с версией")
        self.assertNotIn("frameBox", f)
        self.assertNotEqual(f["requirement"]["sha256"], old_req["sha256"])
        self.assertEqual(TR.version_paths(self.root, self.manifest, "main", 1)["raster"].read_bytes(), K.png_bytes(4, 4))
        st = T.call("daiver_status", self.root, {})["tracks"][0]["frames"][0]
        self.assertEqual(st["stage"], "requirement", "ревизия начинается с нормализации нового экспорта")

    def test_the_versions_never_get_overwritten_a_built_revision_retires_into_versions_2(self):
        self.built_v1()
        self.revise()
        self.resolve()
        self.plan()
        self.build()
        v1_plan = (self.paths()["versions"] / "1" / "plan.json").read_bytes()
        out = self.pull()
        self.assertEqual((out["mode"], out["version"]), ("revision", 2))
        self.assertEqual(TR.get_frame(self.manifest, "main")["baseline"], {"kind": "version", "version": 2})
        self.assertEqual([r["n"] for r in TR.get_frame(self.manifest, "main")["versions"]], [1, 2])
        self.assertEqual((self.paths()["versions"] / "1" / "plan.json").read_bytes(), v1_plan, "versions/1/ не тронут")
        self.assertTrue((self.paths()["versions"] / "2" / "build-state.json").is_file())

    def test_an_identical_export_over_a_built_frame_changes_nothing(self):
        """INV-G3 идемпотентен по содержанию: тот же экспорт (тот же хеш требования) поверх собранного кадра — ни версии, ни следов."""
        self.pull(dsl=synth(3))
        self.call("daiver_normalize")
        self.resolve()
        self.plan()
        self.build()
        self.assertTrue(ST.frame_built(self.paths()["buildState"])[0])
        before = self.family()
        self.n_pull = 2
        out = self.pull(dsl=synth(3))
        self.assertEqual((out["mode"], out["unchanged"]), ("unchanged", True))
        self.assertEqual(self.family(), before)
        self.assertFalse(self.paths()["versions"].exists())

    def test_a_partially_built_frame_is_overwritten_and_keeps_its_build_state(self):
        self.resolve()
        self.plan()
        self.build(only=1)
        before = self.paths()["buildState"].read_bytes()
        out = self.pull()
        self.assertEqual(out["mode"], "overwrite")
        self.assertFalse(self.paths()["versions"].exists(), "недостроенный кадр ревизией не становится")
        self.assertNotIn("baseline", TR.get_frame(self.manifest, "main"))
        self.assertEqual(self.paths()["buildState"].read_bytes(), before, "сверка сборки пометит stale сдвинутые секции сама")
        self.assertIn("plan.json", out["stale"])

    def test_a_state_frame_is_never_versioned(self):
        self.built_v1()
        self.pull("err")
        self.call("daiver_track_manifest", frame="err", set={"role": "state", "stateOf": "main"})
        st = copy.deepcopy(v1("s"))
        st["children"][1]["children"].insert(1, frame("serr", [leaf("serrt", "Укажите телефон получателя")], name="Error block"))
        self.set_canon(st, "err1", "err")
        self.resolve("err")
        self.call("daiver_plan_write", frame="err", weightBudget=BUDGET,
                  plan={"state": {"variable": "orderState", "screenValue": "idle", "value": "error", "toggle": {"named": "ошибка отправки"}}})
        self.build("err")
        self.assertTrue(ST.frame_built(self.paths("err")["buildState"])[0])
        out = self.pull("err")
        self.assertEqual(out["mode"], "overwrite")
        f = TR.get_frame(self.manifest, "err")
        self.assertNotIn("baseline", f)
        self.assertNotIn("versions", f)
        self.assertFalse(self.paths("err")["versions"].exists())

    def test_a_stray_version_directory_is_never_overwritten(self):
        """Каталог versions/1/, которого манифест не знает (руками, из git), не затирается: версия получает следующий свободный номер."""
        self.built_v1()
        stray = self.paths()["versions"] / "1"
        stray.mkdir(parents=True)
        (stray / "junk.txt").write_text("x", encoding="utf-8")
        out = self.pull()
        self.assertEqual((out["mode"], out["version"]), ("revision", 2))
        self.assertEqual(sorted(p.name for p in stray.iterdir()), ["junk.txt"])
        self.assertTrue((self.paths()["versions"] / "2" / "plan.json").is_file())

    def test_a_version_directory_that_cannot_be_created_refuses_and_changes_nothing(self):
        self.built_v1()
        self.paths()["versions"].write_text("не каталог", encoding="utf-8")
        yaml_before = (self.paths()["dir"] / "track.yaml").read_bytes()
        family = self.family()
        self.n_pull += 1
        rel = ".daiver/inbox/blocked.json"
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(json.dumps(synth(5)), encoding="utf-8")
        msg = self.refusal("daiver_pixso_pull", dslPath=rel, frameUrl=URL, frame="main")
        self.assertIn("versions/1/ не создаётся", msg)
        self.assertIn("ничего не изменено", msg)
        self.assertEqual((self.paths()["dir"] / "track.yaml").read_bytes(), yaml_before)
        self.assertEqual(self.family(), family)


class Regions(World):
    def setUp(self):
        super().setUp()
        self.v1_files = self.built_v1()
        self.revise()

    def regions(self):
        return {r["nodeId"]: r for r in self.call("daiver_frame_baseline")["regions"]}

    def test_the_tool_reports_the_change_against_the_built_version_with_the_files_of_each_region(self):
        r = self.call("daiver_frame_baseline")
        self.assertTrue(r["available"], r.get("reason"))
        self.assertEqual((r["frame"], r["version"]), ("main", 1))
        self.assertEqual(r["counts"], {"shared": 4, "added": 1, "removed": 1, "changed": 1})
        self.assertFalse([k for k in r if k.startswith("_")], "внутренние поля наружу не идут")
        reg = self.regions()
        self.assertEqual(reg["bhead"]["kind"], "changed")
        self.assertEqual(reg["bhead"]["files"], [self.v1_files["bhead"]], "changed — файл, где узел собран")
        self.assertEqual((reg["bnew"]["kind"], reg["bnew"]["parentId"]), ("added", "bbody"))
        self.assertEqual(reg["bnew"]["files"], [self.v1_files["bbody"]], "added — файлы родителя: туда вписать")
        self.assertEqual((reg["bhelp"]["kind"], reg["bhelp"]["files"]), ("removed", [self.v1_files["bhelp"]]))
        self.assertEqual(reg["bsave"]["files"], [self.v1_files["bsave"]])
        self.assertEqual(r["inherited"], 1, "решение кнопки (shared) взято у версии")
        self.assertIn("files", r["next"])

    def test_a_revision_that_changes_almost_everything_is_still_a_revision_with_a_warning(self):
        other = frame("broot", [leaf("o1", "Каталог товаров"), leaf("o2", "Фильтры слева"), leaf("o3", "Сортировка по цене")],
                      layout={"direction": "column", "gap": 12}, size={"w": 1280, "h": "hug"})
        self.set_canon(other, "v2-other")
        r = self.call("daiver_frame_baseline")
        self.assertTrue(r["available"], "порог схожести — признак «не состояние», а не «не ревизия»")
        self.assertLess(r["similarity"], STS.SAME_SCREEN_SIMILARITY)
        self.assertTrue([w for w in r["warnings"] if "почти целиком другая" in w and "отдельным кадром" in w], r["warnings"])
        self.assertGreater(r["counts"]["removed"], 0)

    def test_a_heavy_added_region_cut_by_the_budget_keeps_the_files_of_its_region(self):
        n = v2()
        block = n["children"][1]["children"][1]
        block["children"] = [frame(f"bslot{i}", [leaf(f"bslot{i}t", f"Интервал доставки номер {i} с девяти до восемнадцати")], name=f"Slot {i}") for i in range(4)]
        self.set_canon(n, "v2-heavy")
        ids = [x["id"] for x in self.call("daiver_outline", weightBudget=BUDGET)["sections"]]
        self.assertNotIn("bnew", ids, "добавленное поддерево тяжелее бюджета режется")
        sub = next(i for i in ids if i.startswith("bslot"))
        sec = self.call("daiver_section", sectionId=sub, weightBudget=BUDGET)
        self.assertEqual(sec["baseline"]["region"]["regionRoot"], "bnew")
        self.assertEqual(sec["baseline"]["region"]["files"], [self.v1_files["bbody"]], "часть региона — файлы его родителя")

    def test_a_block_added_to_the_root_gets_the_files_of_every_section_inside_it(self):
        """Корень кадра — оболочка над секциями: своей секции у него нет, его код — в файлах секций внутри (оболочку пишет последняя)."""
        n = v2()
        n["children"].append(frame("bfoot", [leaf("bfoott", "Оформляя заказ, вы соглашаетесь с условиями")], name="Footer note"))
        self.set_canon(n, "v2-foot")
        foot = self.regions()["bfoot"]
        self.assertEqual((foot["kind"], foot["parentId"]), ("added", "broot"))
        self.assertEqual(foot["files"], sorted(self.v1_files.values()))

    def test_a_frame_that_is_not_a_revision_is_named(self):
        self.call("daiver_track_manifest", frame="main", set={"notes": "x"})
        m = self.manifest
        TR.get_frame(m, "main").pop("baseline")
        with self.assertRaises(TR.TrackError) as cm:
            BL.analyze(self.root, m, "main")
        self.assertIn("не ревизия", str(cm.exception))

    def test_a_missing_base_is_named_and_refuses_outline_section_resolve_and_plan(self):
        (self.paths()["versions"] / "1" / TR.CANONICAL_FILE).unlink()
        r = self.call("daiver_frame_baseline")
        self.assertFalse(r["available"])
        self.assertIn("канона версии 1 нет", r["reason"])
        self.assertIn("база ревизии недоступна", r["next"])
        for tool, extra in (("daiver_outline", {}), ("daiver_section", {"sectionId": "bhead"}), ("daiver_resolve_candidates", {})):
            msg = self.refusal(tool, **extra)
            self.assertIn("база недоступна", msg, tool)
            self.assertIn("versions/1/", msg, tool)

    def test_files_of_a_region_inside_a_shared_part_of_a_revision_are_found_through_the_chain_of_versions(self):
        """v2 построила только регионы; v3 меняет кнопку, которая в v2 была shared: её файл — из сборки v1."""
        self.resolve()
        self.plan()
        v2_files = self.build()
        self.assertNotIn("bsave", v2_files, "кнопку v2 не строила")
        self.pull()
        v3 = v2()
        v3["children"][2]["component"]["variants"] = {"Variant": "Disabled"}
        self.set_canon(v3, "v3")
        reg = {r["nodeId"]: r for r in self.call("daiver_frame_baseline")["regions"]}
        self.assertEqual((reg["bsave"]["kind"], reg["bsave"]["files"]), ("changed", [self.v1_files["bsave"]]))
        self.assertEqual(reg["bhead"]["files"], [v2_files["bhead"]], "регион, построенный ревизией, — её файл")

    def test_outline_and_section_of_a_revision_are_the_delta_regions(self):
        o = self.call("daiver_outline", weightBudget=BUDGET)
        self.assertEqual([x["id"] for x in o["sections"]], ["bhead", "bnew"], "секции — только изменённое")
        self.assertEqual(o["baseline"]["version"], 1)
        self.assertEqual([r["baseId"] for r in o["baseline"]["removed"]], ["bhelp"])
        self.assertEqual(o["baseline"]["removed"][0]["files"], [self.v1_files["bhelp"]])
        self.assertEqual(o["unproven"], [])
        sec = self.call("daiver_section", sectionId="bhead", weightBudget=BUDGET)
        self.assertEqual(sec["baseline"]["region"]["kind"], "changed")
        self.assertEqual(sec["baseline"]["region"]["files"], [self.v1_files["bhead"]])
        self.assertIn("СУЩЕСТВУЮЩИЕ файлы", sec["next"])
        self.assertIn("ни пролога", sec["next"])
        self.assertEqual(sec["section"]["hash"], self.regions()["bhead"]["hash"], "хеш секции = хеш дельты региона")
        self.assertEqual(sec["ancestors"], [])
        added = self.call("daiver_section", sectionId="bnew", weightBudget=BUDGET)
        self.assertEqual(added["baseline"]["region"], {"kind": "added", "parentId": "bbody", "afterId": "bname", "files": [self.v1_files["bbody"]]})
        self.assertIn("bsave", self.refusal("daiver_section", sectionId="bsave", weightBudget=BUDGET), "shared-часть — не секция")

    def test_candidates_exist_only_in_the_changed_regions_and_the_built_verdicts_are_reused(self):
        r = self.call("daiver_resolve_candidates")
        text = " ".join(r["candidates"])
        self.assertIn("bnew", text, "добавленный блок с осмысленным именем — кандидат")
        self.assertNotIn("bsave", text, "shared-кнопка не кандидат")
        self.assertNotIn("bhead", text, "шапка с тем же figmaName и вариантами получила вердикт версии копией")
        q = self.call("daiver_resolve_query", nodeId="bhead")
        self.assertEqual((q["recorded"]["source"], q["recorded"]["fromFrame"]), ("reuse", "main v1"))
        self.assertEqual(q["baseVerdict"]["component"], "ModalHeader")
        self.assertEqual(q["baseVerdict"]["changes"][0]["prop"], "slots")

    def test_a_changed_variant_is_a_candidate_with_the_verdict_of_the_built_version_as_a_hint(self):
        n = v2()
        n["children"][0]["component"]["variants"] = {"Steps": "ON"}
        self.set_canon(n, "v2b")
        self.assertIn("bhead", " ".join(self.call("daiver_resolve_candidates")["candidates"]))
        q = self.call("daiver_resolve_query", nodeId="bhead")
        self.assertNotIn("recorded", q)
        bv = q["baseVerdict"]
        self.assertEqual((bv["version"], bv["nodeId"], bv["decision"], bv["component"]), (1, "bhead", "component", "ModalHeader"))
        self.assertIn({"kind": "variant", "prop": "Steps", "from": "OFF", "to": "ON"}, bv["changes"])
        self.assertIn("тот же", bv["hint"])
        self.assertNotIn("baseVerdict", self.call("daiver_resolve_query", nodeId="bnew"), "добавленного узла в версии не было")


class PlanAndBuild(World):
    def setUp(self):
        super().setUp()
        self.v1_files = self.built_v1()
        self.revise()
        self.resolve()
        self.plan()

    def plan_json(self):
        return json.loads(self.paths()["plan"].read_text(encoding="utf-8"))

    def test_the_plan_of_a_revision_names_the_base_what_to_delete_and_the_inherited_decisions(self):
        plan = self.plan_json()
        b = plan["baseline"]
        self.assertEqual((b["kind"], b["version"]), ("version", 1))
        self.assertEqual(b["removed"], [{"baseId": "bhelp", "name": "bhelp", "parentId": "broot", "files": [self.v1_files["bhelp"]]}])
        self.assertEqual(b["inherited"], [{"id": "bsave", "baseId": "bsave", "component": "ModalAction"}])
        self.assertEqual([(x["id"], x["region"]) for x in plan["sections"]], [("bhead", "changed"), ("bnew", "added")])
        md = self.paths()["planMd"].read_text(encoding="utf-8")
        self.assertIn("## Изменение версии", md)
        self.assertIn("РЕВИЗИЯ собранной версии 1", md)
        self.assertIn(f"`bhelp` «bhelp» (внутри `broot`) — `{self.v1_files['bhelp']}`", md)
        self.assertIn("Пролога у ревизии нет", md)
        self.assertNotIn("## Оболочка над секциями", md)

    def test_the_revision_plan_is_not_stale_on_reread_and_a_hand_edit_makes_it_stale(self):
        st = self.call("daiver_plan_contract")
        self.assertFalse([o for o in st["open"] if o["id"] == "plan"], st["open"])
        self.assertIn("baseline: ok", st["checks"], "база ревизии записана pull'ом")
        data = self.plan_json()
        data["baseline"]["inherited"] = []
        self.paths()["plan"].write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        st = self.call("daiver_plan_contract")
        self.assertTrue([o for o in st["open"] if o["id"] == "plan"], st["open"])

    def test_the_contract_blocks_a_revision_whose_base_is_gone(self):
        (self.paths()["versions"] / "1" / TR.CANONICAL_FILE).unlink()
        st = open_item(self, self.call("daiver_plan_contract"), "baseline")
        self.assertTrue(st["blocking"])
        self.assertIn("versions/1/", st["detail"])

    def test_the_build_state_holds_only_the_regions_and_names_what_to_delete(self):
        bs = self.call("daiver_build_state")
        self.assertEqual(bs["progress"], "0/2")
        self.assertEqual(bs["revision"]["version"], 1)
        self.assertEqual([r["baseId"] for r in bs["revision"]["removed"]], ["bhelp"])
        self.assertIn("без пролога", bs["revision"]["note"])
        files = self.build()
        self.assertEqual(sorted(files), ["bhead", "bnew"])
        self.assertTrue(ST.frame_built(self.paths()["buildState"])[0])
        done = self.call("daiver_build_state")
        self.assertIn("revision.removed", done["nextStep"])

    def test_a_repeated_pull_of_an_unfinished_revision_reopens_only_the_moved_region(self):
        self.build(only=1)                                   # шапка собрана, блок доставки — нет
        out = self.pull()
        self.assertEqual(out["mode"], "overwrite", "недостроенная ревизия перезаписывается, база остаётся")
        n = v2()
        n["children"][1]["children"][1]["children"][0]["text"] = "Укажите удобный день доставки"
        self.set_canon(n, "v3")
        self.assertEqual(TR.get_frame(self.manifest, "main")["baseline"], {"kind": "version", "version": 1})
        self.resolve()
        self.plan()
        bs = self.call("daiver_build_state")
        states = {x["id"]: x["status"] for x in json.loads(self.paths()["buildState"].read_text(encoding="utf-8"))["sections"]}
        self.assertEqual(states, {"bhead": "done", "bnew": "pending"}, "регион шапки не сдвинулся — остаётся собранным")
        self.assertEqual(bs["next"]["sectionId"], "bnew")

    def test_a_non_primary_frame_revision_keeps_the_placement_of_its_built_version(self):
        self.pull("dlg")
        d1 = frame("droot", [leaf("dtitle", "Подтвердите заказ"), leaf("dtext", "Заказ будет отправлен сегодня")], layout={"direction": "column", "gap": 8},
                   size={"w": 300, "h": "hug"})
        self.set_canon(d1, "d1", "dlg")
        self.resolve("dlg")
        self.call("daiver_plan_write", frame="dlg", weightBudget=BUDGET, plan={"placement": {"mode": "own-file", "component": "ConfirmDialog", "reason": "отдельный диалог"}})
        self.build("dlg")
        self.pull("dlg")
        d2 = copy.deepcopy(d1)
        d2["children"][1]["text"] = "Заказ будет отправлен завтра"
        self.set_canon(d2, "d2", "dlg")
        self.resolve("dlg")
        self.call("daiver_plan_write", frame="dlg", weightBudget=BUDGET)
        plan = json.loads(self.paths("dlg")["plan"].read_text(encoding="utf-8"))
        self.assertEqual(plan["module"]["root"], {"mode": "own-file", "component": "ConfirmDialog", "reason": "отдельный диалог"})
        self.assertEqual(plan["baseline"]["version"], 1)


class Verify(World):
    def setUp(self):
        super().setUp()
        self.v1_files = self.built_v1()
        self.revise()
        self.resolve()
        self.plan()
        self.files = self.build()

    def compare(self, texts, name="iter-1"):
        it = self.paths()["verify"] / name
        it.mkdir(parents=True, exist_ok=True)
        (it / "snapshot.json").write_text(json.dumps(snapshot(texts)), encoding="utf-8")
        out = self.call("daiver_verify_compare", iterationDir=it.relative_to(self.root).as_posix())
        return out, json.loads((it / "compare-summary.json").read_text(encoding="utf-8"))

    def test_the_compare_tool_writes_the_baseline_verdict_and_previous_is_not_a_hard_gate(self):
        new = ["Новый заказ", "Имя получателя", "Укажите удобное время доставки", "Телефон для связи", "Адрес доставки", "Сохранить заказ"]
        out, summary = self.compare(new)
        self.assertEqual((out["baseline"]["verdict"], summary["baseline"]["verdict"]), ("current", "current"))
        old = ["Оформление заказа", "Имя получателя", "Телефон для связи", "Адрес доставки", "Сохранить заказ", "Нужна помощь? Позвоните нам"]
        out, summary = self.compare(old, "iter-2")
        self.assertEqual(out["baseline"]["verdict"], "previous")
        self.assertNotEqual(out["status"], "hard-gate-fail")
        self.assertIn("dev-сервер", out["nextStep"])

    def test_the_code_gate_reads_the_files_of_every_version(self):
        it = self.paths()["verify"] / "iter-1"
        it.mkdir(parents=True, exist_ok=True)
        for rel in set(self.v1_files.values()) | set(self.files.values()):
            (self.root / rel).write_text("export default function X() { return <div>x</div>; }\n", encoding="utf-8")
        (self.root / self.v1_files["bhelp"]).unlink()            # removed-регион удалён ревизией — не находка
        out = self.call("daiver_code_compare", iterationDir=it.relative_to(self.root).as_posix())
        model = json.loads((it / "code-summary.json").read_text(encoding="utf-8"))
        ids = sorted(model.get("sections") or {})
        for sid in ("bbody", "bsave", "bhead"):
            self.assertIn(sid, ids, f"{sid}: файл версии 1 в скане ({ids})")
        self.assertFalse([w for w in out["warnings"] if self.v1_files["bhelp"] in w], "удалённый файл версии — не предупреждение")

    def test_the_ds_gate_gathers_the_sources_of_every_version(self):
        got = DS.gather(self.root, TRACK)
        for rel in self.v1_files.values():
            if (self.root / rel).is_file():
                self.assertIn(rel, got["sources"])


class Manifest(World):
    def test_versions_and_baseline_are_validated_and_not_settable_by_the_manifest_tool(self):
        self.built_v1()
        self.revise()
        m = self.manifest
        f = TR.get_frame(m, "main")
        for mutate, needle in ((lambda x: x["versions"][0].update(n=0), "номер версии"),
                               (lambda x: x["versions"].append(dict(x["versions"][0])), "номер версии"),
                               (lambda x: x.update(baseline={"kind": "version", "version": 3}), "версии 3 в versions нет"),
                               (lambda x: x.update(baseline={"kind": "page"}), "baseline"),
                               (lambda x: x["versions"][0].update(extra=1), "неизвестное поле версии"),
                               (lambda x: x["versions"][0]["requirement"].update(sha256="x"), "sha256")):
            bad = copy.deepcopy(m)
            mutate(TR.get_frame(bad, "main"))
            self.assertTrue([p for p in TR.validate(bad) if needle in p], (needle, TR.validate(bad)))
        self.assertEqual(TR.validate(m), [])
        for key in ("baseline", "versions"):
            msg = self.refusal("daiver_track_manifest", frame="main", set={key: f[key]})
            self.assertIn("daiver_pixso_pull", msg)

    def test_a_state_frame_cannot_carry_a_baseline(self):
        self.built_v1()
        self.revise()
        m = copy.deepcopy(self.manifest)
        TR.upsert_frame(m, "err", role="state", stateOf="main", baseline={"kind": "version", "version": 1},
                        versions=copy.deepcopy(TR.get_frame(m, "main")["versions"]))
        self.assertTrue([p for p in TR.validate(m) if "кадр-состояние не версионируется" in p], TR.validate(m))


class Ladder(World):
    def test_a_stale_requirement_of_a_built_revision_is_loud_and_names_the_pull(self):
        self.built_v1()
        self.revise()
        self.resolve()
        self.plan()
        self.build()
        self.paths()["requirement"].write_text("{}", encoding="utf-8")
        st = T.call("daiver_status", self.root, {})
        codes = [w.split(":")[0] for w in st["warnings"]]
        self.assertIn("requirement-stale-built", codes)
        self.assertIn("daiver_pixso_pull", next(w for w in st["warnings"] if w.startswith("requirement-stale-built")))


class Signature(TPC.Base):
    def test_a_revision_pull_takes_the_signature_off(self):
        out = self.signed_world()
        self.assertTrue(out["signed"])
        bs = self.call("daiver_build_state", track=TRACK)
        sid = bs["next"]["sectionId"]
        self.call("daiver_build_state_set", track=TRACK, sectionId=sid, status="in_progress")
        h = self.call("daiver_section", track=TRACK, sectionId=sid)["section"]["hash"]
        (self.root / "src/modules/main").mkdir(parents=True, exist_ok=True)
        (self.root / "src/modules/main/Page.tsx").write_text("//\n", encoding="utf-8")
        self.call("daiver_build_state_set", track=TRACK, sectionId=sid, status="done", outputs=["src/modules/main/Page.tsx"], hash=h)
        (self.root / "next.json").write_text(json.dumps(synth(2)), encoding="utf-8")
        pulled = self.call("daiver_pixso_pull", track=TRACK, dslPath="next.json", frameUrl=TPC.URL, frame="main")
        self.assertEqual(pulled["mode"], "revision")
        st = self.contract()
        self.assertFalse(st["signed"])
        sig = self.one(st, "signature")
        self.assertEqual(sig["state"], "stale", "подпись прежнего плана снята: плана кадра больше нет, требование другое")


class RealModal(unittest.TestCase):
    """Образец «Modal» из Pixso: собранный кадр, изменённый текст шапки — ревизия целиком."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        TB.init_repo(self.root)
        TB.write_ds(self.root, ["ModalHeader", "ModalAction"])
        self.inbox = self.root / ".daiver" / "inbox"
        self.inbox.mkdir(parents=True)
        shutil.copyfile(str(TB.FIXTURE), str(self.inbox / "v1.dsl.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, **args):
        return T.call(name, self.root, dict({"track": TRACK}, **args))

    def test_the_sample_goes_through_a_revision(self):
        self.call("daiver_pixso_pull", dslPath=".daiver/inbox/v1.dsl.json", frameUrl=URL, frame="main")
        self.call("daiver_normalize")
        fin = lambda v: self.call("daiver_resolve_finalize", verdict=dict(v, confidence="high", rationale="образец"))  # noqa: E731
        fin({"nodeId": "2:153702", "decision": "local"})
        fin({"nodeId": "2:153703", "decision": "component", "component": "ModalHeader"})
        fin({"nodeId": "2:153704", "decision": "component", "component": "ModalAction"})
        self.call("daiver_resolve_report")
        self.call("daiver_plan_write")
        self.call("daiver_build_state")
        self.call("daiver_build_state_set", sectionId="2:153702", status="in_progress")
        h = self.call("daiver_section", sectionId="2:153702", weightBudget=24000)["section"]["hash"]
        (self.root / "src/modules/modal").mkdir(parents=True, exist_ok=True)
        (self.root / "src/modules/modal/Modal.tsx").write_text("//\n", encoding="utf-8")
        self.call("daiver_build_state_set", sectionId="2:153702", status="done", outputs=["src/modules/modal/Modal.tsx"], hash=h)
        doc = json.loads((self.inbox / "v1.dsl.json").read_text(encoding="utf-8"))
        doc["dsl"]["pixTreeDslNodes"][0]["childNode"][2]["props"][2]["nodeText"] = "Оформление заказа"
        (self.inbox / "v2.dsl.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        out = self.call("daiver_pixso_pull", dslPath=".daiver/inbox/v2.dsl.json", frameUrl=URL, frame="main")
        self.assertEqual((out["mode"], out["version"]), ("revision", 1))
        self.assertEqual(out["revision"]["regions"], {"added": 0, "removed": 0, "changed": 1})
        self.assertEqual(out["revision"]["similarity"], 1.0)
        self.call("daiver_normalize")
        r = self.call("daiver_frame_baseline")
        changed = [x for x in r["regions"] if x["kind"] == "changed"]
        self.assertEqual([(x["nodeId"], x["files"]) for x in changed], [("2:153703", ["src/modules/modal/Modal.tsx"])])
        self.assertEqual(self.call("daiver_resolve_candidates")["stats"]["todo"], 0, "шапка с новым текстом — тот же компонент, вердикт версии копией")
        self.call("daiver_resolve_report")
        self.call("daiver_plan_write")
        bs = self.call("daiver_build_state")
        self.assertEqual((bs["progress"], bs["next"]["sectionId"]), ("0/1", "2:153703"))


if __name__ == "__main__":
    unittest.main()
