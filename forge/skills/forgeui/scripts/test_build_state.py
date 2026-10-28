#!/usr/bin/env python3
"""test_build_state.py — build-state.json: возобновляемая сборка по секциям (задача 3.1; спека §13.1, §13.3–13.4; INV C10, G1, G2, A11;
QG U13).

Пинит как свойства КОДА (не текста брифа):
  • `daiver_build_state`: состояние создаётся из `buildOrder` настоящего плана (`daiver_plan_write`), все секции pending, порядок и
    имена — по плану; повторный вызов без изменений файл не пишет и замок не берёт; `next` — первая секция не done (в том числе
    оставленная in_progress: прерванный прогон возвращается к ней); прогон, остановленный посередине, продолжается с `next`, а не с нуля;
  • стухание считает код: хеш секции в плане изменился → done становится stale, остальные секции не тронуты (pending / in_progress
    keep), outputs прежней сборки остаются; новые id — pending, удалённые выпадают, смена бюджета меняет id; план, не равный нынешнему
    канону, — отказ (макет изменился после плана), состояние при этом не меняется;
  • `daiver_build_state_set`: нельзя перескочить (done только из in_progress, in_progress только для next, done не воскресает);
    статус вне {in_progress, done} — отказ схемы; неизвестный id — ошибка с перечнем настоящих; done требует outputs и hash, чужой hash —
    отказ, и настоящий hash в отказе не называется; все нарушения одним сообщением;
  • outputs: пути ОТ КОРНЯ ПРОЕКТА (абсолютный внутри репо приводится к такому), голое имя файла, `..`, `~`, обратные слэши, каталог,
    путь вне репо — отказ; несуществующий путь — предупреждение, которое повторяется, пока файла нет; отсортированы, без повторов;
  • запись: атомарная (tmp + replace, сбой не оставляет мусора и не портит файл), под замком со свежим чтением внутри него, без
    времени и случайности — побайтно один файл на один вход; повреждённый файл — отказ с причиной, а не молчаливый сброс;
  • контракт лестницы 1.10: файл движка читается `daiver_status` (sections[].status ∈ перечня), stale возвращает проверенный кадр в сборку;
  • реестр: оба инструмента на месте, схемы закрыты, произвольных путей (statePath, outPath) нет;
  • секция без кода (3.2): корень секции в плане решён ignore → её закрывает сама сверка (`done`, `noCode: true`, без outputs), `next` её не
    называет, set её не двигает, гейт сборки доволен, ступень лестницы считает её готовой; перемена вердикта в любую сторону видна
    (ignore → строить: pending; собранная → ignore: файлы остаются, предупреждение один раз); все секции без кода — отказ; ручная правка
    файла проверяется по новому ключу.

Данные. «Modal» — настоящий образец Pixso (одна секция); экраны на несколько секций — СИНТЕТИКА из маленьких построителей ниже
(панели-секции под корнем-оболочкой, бюджет подобран так, чтобы панель была секцией, а корень — оболочкой). Что НЕ проверено данными:
боевых экранов и прогонов сборки нет — поведение на большом числе секций, встречается ли секция с корнем ignore на боевом экране (признак
«нет кода» выведен из текста спеки), то, как слабая модель читает отказы и `nextStep` (X.4).
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

from daiver import build_state as BS  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import ds_requirements as DR  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import plan as PL  # noqa: E402
from daiver import status as ST  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
TRACK = "STOR-100"
URL = "https://pixso.example/a?item-id=2:153702"
SHA = "a" * 64
TRACK_DIR = f".daiver/tracks/{TRACK}"
STATE_REL = f"{TRACK_DIR}/build-state.json"
PLAN_REL = f"{TRACK_DIR}/plan.json"
BUDGET = 200                    # панель экрана весит 168 (с правкой имени листа — 175), корень из трёх панелей — 614
BIG_BUDGET = 100000             # весь экран — одна секция (корень)


# ── построители ───────────────────────────────────────────────────────────────

def leaf(i, **kw):
    n = {"id": i, "kind": "text", "name": i}
    n.update(kw)
    return n


def frame(i, kids=(), **kw):
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def autoname(i: str) -> str:
    """Автогенерированное имя слоя («Frame 11249»): такой фрейм — не кандидат резолва, резолвить нечего."""
    return "Frame " + "".join(str(ord(c)) for c in i)


def panel(i, kids=()):
    return frame(i, kids, name=autoname(i), layout={"direction": "column", "gap": 8}, size={"w": "fill", "h": "hug"})


def screen(ids=("p1", "p2", "p3"), edited=()):
    """Корень-оболочка с панелями-секциями; `edited` — панели, у которых изменён лист (другой хеш секции, то же имя и id)."""
    return frame("Page", [panel(i, [leaf("t" + i[1:], name="t" + i[1:] + ("-edited" if i in edited else ""))]) for i in ids],
                 name=autoname("Page"), layout={"direction": "column", "gap": 8})


def decor(i, edited=False):
    """Панель с осмысленным именем — кандидат резолва (обычные панели автоимённые и кандидатами не бывают). Её корень решат ignore."""
    return frame(i, [leaf("t" + i[1:], name="t" + i[1:] + ("-edited" if edited else ""))], name="Decoration " + i,
                 layout={"direction": "column", "gap": 8}, size={"w": "fill", "h": "hug"})


def decor_screen(named=("p1", "p2", "p3"), ids=("p1", "p2", "p3"), edited=()):
    """Экран из панелей-секций: `named` — панели-кандидаты (Decoration …), остальные — обычные автоимённые."""
    return frame("Page", [decor(i, i in edited) if i in named else panel(i, [leaf("t" + i[1:])]) for i in ids],
                 name=autoname("Page"), layout={"direction": "column", "gap": 8})


def decide(root: Path, verdicts: Dict[str, str]) -> None:
    for sid, decision in verdicts.items():
        T.call("daiver_resolve_finalize", root, {"track": TRACK, "verdict": {"nodeId": sid, "decision": decision, "confidence": "high",
                                                                              "rationale": "в тесте"}})


def replan_now(root: Path) -> Dict[str, Any]:
    T.call("daiver_resolve_report", root, {"track": TRACK})
    return T.call("daiver_plan_write", root, {"track": TRACK, "weightBudget": BUDGET})


def make_decor_world(root: Path, verdicts: Dict[str, str], ids) -> None:
    """Панели из `verdicts` (id → решение) — кандидаты резолва, остальные — обычные; план строится по настоящему резолву."""
    init_repo(root)
    build_repo(root, decor_screen(tuple(verdicts), tuple(ids)))
    decide(root, verdicts)
    replan_now(root)


def init_repo(root: Path) -> None:
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}", encoding="utf-8")
    res = I.init(root, {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
                        "buildCommand": "npm run build", "librarySource": "stories", "scanPaths": ["lib"]})
    assert res["ok"], res


def set_canon(root: Path, node: dict) -> None:
    """Канон кадра main = node; sha256 требования в манифесте равен тому, по которому «построен» канон (как после pull)."""
    m = TR.load(root, TRACK)
    paths = TR.frame_paths(root, m, "main")
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["requirement"].write_text(json.dumps({"dsl": "main"}), encoding="utf-8")
    TR.get_frame(m, "main")["requirement"]["sha256"] = TR.sha256_file(paths["requirement"])
    C.write_canonical(paths["canonical"], C.new_envelope(node, "pixso", {"sourceSha256": TR.get_frame(m, "main")["requirement"]["sha256"]}))
    TR.save(root, m)


def build_repo(root: Path, node: dict) -> None:
    m = TR.new_manifest(TRACK, created="2026-10-15T12:00:00Z")
    TR.upsert_frame(m, "main", frameUrl=URL, requirement={"kind": "pixso", "path": "visual-requirements.pixso.json", "sha256": SHA,
                                                         "pulledAt": "2026-10-15T12:00:00Z"})
    TR.save(root, m)
    set_canon(root, node)


def make_world(root: Path, node: Optional[dict] = None, budget: Any = BUDGET) -> None:
    """Репо с треком и НАСТОЯЩИМ plan.json от daiver_plan_write (кандидатов резолва в экране нет: план строится сразу)."""
    init_repo(root)
    build_repo(root, node if node is not None else screen())
    T.call("daiver_resolve_report", root, {"track": TRACK})
    T.call("daiver_plan_write", root, {"track": TRACK, "weightBudget": budget})


def write_ds(root: Path, names: List[str]) -> None:
    """Пара требований ДС с названными компонентами (формат 2.2, проходит настоящие валидаторы)."""
    comps = {n: {"title": None, "taxonomy": [], "props": {}, "propsClosed": False, "usedValues": {},
                 "import": {"path": "@t/ui", "named": n}, "stories": 1} for n in names}
    env = lambda d: {"version": "0.1.0", "role": "ds-requirements", "doc": d, "schema": 1}      # noqa: E731
    contract = {"daiver": env("contract"),
                "source": {"librarySource": "stories", "scanPaths": ["lib"], "scanner": "py/1", "sha256": SHA, "files": 1, "skipped": []},
                "components": comps}
    details = {"daiver": env("details"), "source": {"sha256": SHA}, "components": {}}
    bad = DR.validate_contract(contract) + DR.validate_details(details) + DR.validate_pair(contract, details)
    assert not bad, bad
    for rel, doc in ((DR.CONTRACT_REL, contract), (DR.DETAILS_REL, details)):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DR.dumps(doc), encoding="utf-8")


def snapshot(root: Path) -> Dict[str, Any]:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def walk(root: Path) -> bytes:
    """Одинаковый путь по состоянию в любом репо: p1 и p2 собраны, p3 начата. → байты build-state.json."""
    def call(name, **args):
        return T.call(name, root, dict(args, track=TRACK))

    call("daiver_build_state")
    for sid in ("p1", "p2"):
        call("daiver_build_state_set", sectionId=sid, status="in_progress")
        h = call("daiver_section", sectionId=sid, weightBudget=BUDGET)["section"]["hash"]
        path = root / "src" / "modules" / f"{sid}.tsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export {};\n", encoding="utf-8")
        call("daiver_build_state_set", sectionId=sid, status="done", outputs=[f"src/modules/{sid}.tsx"], hash=h)
    call("daiver_build_state_set", sectionId="p3", status="in_progress")
    return (root / STATE_REL).read_bytes()


class World(unittest.TestCase):
    """Репо с треком STOR-100, кадром main (экран из трёх панелей-секций p1, p2, p3) и настоящим планом."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()

    def world(self, node=None, budget=BUDGET):
        make_world(self.root, node, budget)

    def replan(self, node=None, budget=BUDGET):
        """Макет изменился (node) или бюджет сменился: канон перезаписан, отчёт резолва и план пересобраны."""
        if node is not None:
            set_canon(self.root, node)
        self.call("daiver_resolve_report", track=TRACK)
        return self.call("daiver_plan_write", track=TRACK, weightBudget=budget)

    def call(self, name, **args):
        return T.call(name, self.root, args)

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            T.call(name, self.root, args)
        return str(cm.exception)

    def bs(self, **kw):
        return self.call("daiver_build_state", track=TRACK, **kw)

    def bs_refused(self, **kw):
        return self.refusal("daiver_build_state", track=TRACK, **kw)

    def set(self, sid, status, **kw):
        return self.call("daiver_build_state_set", track=TRACK, sectionId=sid, status=status, **kw)

    def set_refused(self, sid, status, **kw):
        return self.refusal("daiver_build_state_set", track=TRACK, sectionId=sid, status=status, **kw)

    def state_path(self) -> Path:
        return self.root / STATE_REL

    def raw(self) -> Optional[bytes]:
        p = self.state_path()
        return p.read_bytes() if p.is_file() else None

    def state(self) -> dict:
        return json.loads(self.state_path().read_text(encoding="utf-8"))

    def statuses(self) -> Dict[str, str]:
        return {s["id"]: s["status"] for s in self.state()["sections"]}

    def section(self, sid) -> dict:
        return next(s for s in self.state()["sections"] if s["id"] == sid)

    def write_state(self, data) -> None:
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        self.state_path().write_text(text, encoding="utf-8")

    def hash_of(self, sid, budget=BUDGET) -> str:
        return self.call("daiver_section", track=TRACK, sectionId=sid, weightBudget=budget)["section"]["hash"]

    def touch(self, rel: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("export {};\n", encoding="utf-8")

    def files(self, sid: str) -> List[str]:
        return [f"src/modules/orders/{sid.upper()}.tsx"]

    def build(self, sid, budget=BUDGET, files=None):
        """Один цикл сборки, как его проведёт модель: in_progress → daiver_section → файлы → done с outputs и hash."""
        files = files or self.files(sid)
        self.set(sid, "in_progress")
        h = self.hash_of(sid, budget)
        for f in files:
            self.touch(f)
        return self.set(sid, "done", outputs=files, hash=h)

    def build_all(self, ids=("p1", "p2", "p3")):
        for sid in ids:
            self.build(sid)

    def stage(self) -> str:
        t = next(t for t in self.call("daiver_status")["tracks"] if t["name"] == TRACK)
        return t["stage"]

    def plan_json(self) -> dict:
        return json.loads((self.root / PLAN_REL).read_text(encoding="utf-8"))

    def write_plan_json(self, plan) -> None:
        (self.root / PLAN_REL).write_text(plan if isinstance(plan, str) else json.dumps(plan, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")


# ── инициализация ────────────────────────────────────────────────────────────

class Init(World):
    def test_first_call_creates_the_state_from_the_build_order_of_the_real_plan(self):
        self.world()
        out = self.bs()
        want = [{"hash": b["hash"], "id": b["id"], "name": b["name"], "status": "pending"} for b in self.plan_json()["buildOrder"]]
        self.assertEqual([w["id"] for w in want], ["p1", "p2", "p3"])
        self.assertEqual(self.state(), {"schema": 1, "track": TRACK, "frame": "main", "weightBudget": BUDGET, "sections": want},
                         "версия state = 1; все секции pending, в порядке buildOrder, с именем и хешем плана; outputs у pending нет")
        self.assertTrue(out["created"] and out["written"])
        self.assertEqual((out["progress"], out["weightBudget"]), ("0/3", BUDGET))
        self.assertEqual(out["next"], {"sectionId": "p1", "name": want[0]["name"], "status": "pending"})
        for part in ("p1", "status=in_progress", "daiver_section", f"weightBudget={BUDGET}", "status=done", "section.hash"):
            self.assertIn(part, out["nextStep"])

    def test_the_answer_names_the_readable_plan_of_the_frame_so_the_model_does_not_derive_the_path(self):
        self.world()
        out = self.bs()
        self.assertEqual(out["planMd"], f"{TRACK_DIR}/plan.md")
        self.assertTrue((self.root / out["planMd"]).is_file(), "план.md пишет daiver_plan_write: на месте")
        self.assertEqual(self.bs()["planMd"], out["planMd"], "путь называется и при повторном (читающем) вызове")

    def test_the_state_file_is_the_engines_alone_and_ends_with_a_newline_without_time(self):
        self.world()
        self.bs()
        raw = self.raw()
        self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw)
        text = raw.decode("utf-8")
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}|T\d{2}:\d{2}", text), "времени в файле нет")
        self.assertNotIn(str(self.root), text, "путей машины в файле нет")
        keys = [k for k in json.loads(text)]
        self.assertEqual(keys, sorted(keys), "ключи отсортированы")

    def test_second_call_changes_nothing_and_is_a_pure_read(self):
        self.world()
        self.bs()
        before = snapshot(self.root)
        out = self.bs()
        self.assertEqual(snapshot(self.root), before, "ни байта, ни mtime: читающий вызов следов не оставляет")
        self.assertFalse(out["created"] or out["written"])
        self.assertNotIn("stale", out)

    def test_the_state_follows_the_order_of_the_plan_not_the_document(self):
        """Порядок сборки — дело плана (создатели общих local-компонентов раньше потребителей): состояние берёт его как есть."""
        self.world()
        plan = self.plan_json()
        plan["buildOrder"].reverse()
        self.write_plan_json(plan)
        out = self.bs()
        self.assertEqual([s["id"] for s in self.state()["sections"]], ["p3", "p2", "p1"])
        self.assertEqual(out["next"]["sectionId"], "p3")

    def test_the_state_lives_in_the_frame_folder_of_the_track(self):
        self.world()
        self.bs(frame="main")
        self.assertTrue(self.state_path().is_file())
        self.assertEqual(self.state_path(), TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["buildState"])

    def test_no_plan_means_no_state(self):
        init_repo(self.root)
        build_repo(self.root, screen())
        msg = self.bs_refused()
        self.assertIn("плана кадра нет", msg)
        self.assertIn("ui-design", msg)
        self.assertIsNone(self.raw())

    def test_unknown_track_frame_and_missing_canon(self):
        self.world()
        self.assertIn("нет", self.refusal("daiver_build_state", track="STOR-999"))
        self.assertIn("кадра «ghost» в треке нет", self.bs_refused(frame="ghost"))
        self.assertIn("имя трека", self.refusal("daiver_build_state", track="../x"))
        TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["canonical"].unlink()
        self.assertIn("канона кадра", self.bs_refused())
        self.assertIsNone(self.raw())

    def test_the_canon_warning_of_the_outline_loader_reaches_the_answer(self):
        """Канон построен по другому экспорту, чем записан в манифесте: хеши секций те же, сборка идёт, но об этом сказано вслух."""
        self.world()
        m = TR.load(self.root, TRACK)
        env = C.read_canonical(TR.frame_paths(self.root, m, "main")["canonical"])
        env["stats"]["sourceSha256"] = "b" * 64
        C.write_canonical(TR.frame_paths(self.root, m, "main")["canonical"], env)
        out = self.bs()
        self.assertTrue(any("канон построен по другому экспорту" in w for w in out["warnings"]), out.get("warnings"))
        self.assertEqual(out["progress"], "0/3")

    def test_a_plan_the_engine_did_not_write_is_refused_with_the_fix(self):
        self.world()
        good = self.plan_json()
        bad_entry = dict(good, buildOrder=[{"id": "p1", "name": "x"}])
        twice = dict(good, buildOrder=good["buildOrder"] + good["buildOrder"][:1])
        cases = [("nope", "не JSON"), ("[]", "ожидается JSON-объект"),
                 (dict(good, weightBudget=-5), "weightBudget"), (dict(good, weightBudget="200"), "weightBudget"),
                 (dict(good, weightBudget=True), "weightBudget"), ({k: v for k, v in good.items() if k != "weightBudget"}, "weightBudget"),
                 ({k: v for k, v in good.items() if k != "buildOrder"}, "нет buildOrder"), (dict(good, buildOrder={}), "нет buildOrder"),
                 (dict(good, buildOrder=[]), "buildOrder пуст"), (bad_entry, "нужны строки id и hash"),
                 (dict(good, buildOrder=["p1"]), "нужны строки id и hash"), (twice, "названа дважды")]
        for plan, phrase in cases:
            with self.subTest(phrase=phrase, plan=str(plan)[:50]):
                self.write_plan_json(plan)
                msg = self.bs_refused()
                self.assertIn(phrase, msg)
                if phrase != "buildOrder пуст":
                    self.assertIn("daiver_plan_write", msg, "отказ называет, чем чинить")
                self.assertIsNone(self.raw(), "по плану, который не годится, состояние не создаётся")

    def test_the_answer_stays_short_however_many_sections_there_are(self):
        ids = tuple(f"p{i}" for i in range(1, 31))
        self.world(screen(ids))
        out = self.bs()
        self.assertEqual(out["progress"], "0/30")
        self.assertLess(len(json.dumps(out, ensure_ascii=False, indent=1).splitlines()), 30,
                        "ответ не растёт с числом секций: next и прогресс, а не список")


# ── продолжение с next ───────────────────────────────────────────────────────

class Resume(World):
    def test_the_run_stopped_in_the_middle_continues_from_next_not_from_scratch(self):
        self.world()
        self.bs()
        self.build("p1")
        self.set("p2", "in_progress")                            # прогон оборван на секции 2: p2 начата, не закончена
        out = self.bs()                                          # «новая сессия» — тот же вызов
        self.assertEqual(out["progress"], "1/3")
        self.assertEqual(out["next"], {"sectionId": "p2", "name": out["next"]["name"], "status": "in_progress"})
        self.assertIn("начата раньше", out["nextStep"])
        self.assertEqual(self.statuses(), {"p1": "done", "p2": "in_progress", "p3": "pending"})
        self.assertFalse(out["written"], "возобновление ничего не пересоздаёт и не откатывает")
        self.assertEqual(self.section("p1")["outputs"], self.files("p1"))

    def test_next_walks_the_plan_in_order_until_it_is_empty(self):
        self.world()
        seen = []
        out = self.bs()
        while out["next"] is not None:
            sid = out["next"]["sectionId"]
            seen.append(sid)
            out = self.build(sid)
        self.assertEqual(seen, ["p1", "p2", "p3"])
        self.assertEqual((out["progress"], out["next"]), ("3/3", None))
        self.assertIn("все секции готовы", out["nextStep"])
        self.assertEqual(self.bs()["next"], None)

    def test_a_fresh_call_after_a_finished_build_does_not_restart_it(self):
        self.world()
        self.bs()
        self.build_all()
        before = self.raw()
        out = self.bs()
        self.assertEqual((out["progress"], out["next"], out["written"]), ("3/3", None, False))
        self.assertEqual(self.raw(), before)

    def test_the_ladder_follows_the_state_planned_building_built(self):
        self.world()
        self.assertEqual(self.stage(), "planned")
        self.assertIn("daiver_build_state", self.call("daiver_status")["tracks"][0]["nextStep"],
                      "ступень planned называет инструмент, который теперь есть")
        self.bs()
        self.assertEqual(self.stage(), "building")
        self.build("p1")
        self.set("p2", "in_progress")
        self.assertEqual(self.stage(), "building")
        frame = self.call("daiver_status")["tracks"][0]["frames"][0]
        self.assertEqual(frame["build"], "1/3")
        self.build("p2")
        self.build("p3")
        self.assertEqual(self.stage(), "built")
        self.assertEqual(self.call("daiver_status")["tracks"][0]["frames"][0]["build"], "3/3")


# ── стухание: хеш секции в плане изменился ───────────────────────────────────

class StaleDetection(World):
    def built_world(self):
        self.world()
        self.bs()
        self.build_all()

    def test_only_the_changed_section_goes_stale_and_the_others_stay_done(self):
        self.built_world()
        self.assertEqual(self.stage(), "built")
        old_hash = self.hash_of("p2")
        self.replan(screen(edited=("p2",)))
        out = self.bs()
        self.assertEqual(self.statuses(), {"p1": "done", "p2": "stale", "p3": "done"})
        self.assertEqual(out["stale"], ["p2"])
        self.assertEqual(out["progress"], "2/3")
        self.assertEqual(out["next"], {"sectionId": "p2", "name": out["next"]["name"], "status": "stale",
                                       "outputs": self.files("p2")}, "файлы прежней сборки названы: секция перестраивается на месте")
        self.assertIn("перестрой ТОЛЬКО её", out["nextStep"])
        self.assertNotEqual(self.section("p2")["hash"], old_hash, "в состоянии новый хеш секции")
        self.assertEqual(self.section("p2")["outputs"], self.files("p2"), "outputs стухшей секции остаются до новой сборки")
        for sid in ("p1", "p3"):
            self.assertEqual(self.section(sid)["outputs"], self.files(sid))
        self.assertEqual(self.bs()["written"], False, "повторная сверка стабильна: stale не «разстухает» и не пишется заново")

    def test_a_stale_section_returns_a_built_frame_to_building_and_is_rebuilt_alone(self):
        self.built_world()
        self.replan(screen(edited=("p2",)))
        self.assertEqual(self.stage(), "built", "пока сверки не было, лестница видит старый файл — сверка дело daiver_build_state")
        self.bs()
        self.assertEqual(self.stage(), "building", "stale ⇒ building: контракт лестницы 1.10")
        self.build("p2")
        self.assertEqual(self.statuses(), {"p1": "done", "p2": "done", "p3": "done"})
        self.assertEqual(self.stage(), "built")
        self.assertEqual(self.section("p2")["hash"], self.hash_of("p2"))

    def test_a_stale_section_after_a_clean_verification_returns_the_frame_to_building(self):
        self.built_world()
        (self.root / TRACK_DIR / "verify-state.json").write_text(json.dumps({"history": [{"clean": True, "blocking": 0}]}), encoding="utf-8")
        self.assertEqual(self.stage(), "clean")
        self.replan(screen(edited=("p1",)))
        self.bs()
        self.assertEqual(self.stage(), "building", "зелёная история verify относится к прежней сборке")

    def test_done_with_the_hash_of_the_old_section_is_refused_and_the_new_hash_is_not_in_the_refusal(self):
        self.built_world()
        old_hash = self.hash_of("p2")
        self.replan(screen(edited=("p2",)))
        self.bs()
        self.set("p2", "in_progress")
        msg = self.set_refused("p2", "done", outputs=self.files("p2"), hash=old_hash)
        self.assertIn("hash не совпал", msg)
        new_hash = self.hash_of("p2")
        self.assertNotIn(new_hash, msg, "настоящий хеш из отказа не списать: его даёт только daiver_section")
        self.assertNotIn(old_hash, msg)
        self.assertEqual(self.statuses()["p2"], "in_progress")

    def test_pending_and_in_progress_sections_keep_their_status_when_their_hash_changes(self):
        self.world()
        self.bs()
        self.build("p1")
        self.set("p2", "in_progress")
        before = {s["id"]: s["hash"] for s in self.state()["sections"]}
        self.replan(screen(edited=("p2", "p3")))
        out = self.bs()
        self.assertEqual(self.statuses(), {"p1": "done", "p2": "in_progress", "p3": "pending"})
        self.assertNotIn("stale", out, "stale — только done, которая устарела")
        after = {s["id"]: s["hash"] for s in self.state()["sections"]}
        self.assertEqual(after["p1"], before["p1"])
        self.assertNotEqual(after["p2"], before["p2"])
        self.assertNotEqual(after["p3"], before["p3"])
        self.build("p2")                                          # доделывается по НОВЫМ данным
        self.assertEqual(self.section("p2")["hash"], self.hash_of("p2"))

    def test_an_already_stale_section_stays_stale_when_it_changes_again(self):
        self.built_world()
        self.replan(screen(edited=("p2",)))
        self.bs()
        self.replan(screen(edited=("p2", "p3")))
        out = self.bs()
        self.assertEqual(self.statuses(), {"p1": "done", "p2": "stale", "p3": "stale"})
        self.assertEqual(out["stale"], ["p3"], "p2 стухла раньше; в этой сверке стухла p3")
        self.assertEqual(out["next"]["sectionId"], "p2")

    def test_replanning_without_a_change_in_the_mockup_touches_nothing(self):
        self.built_world()
        before = self.raw()
        self.replan()
        out = self.bs()
        self.assertEqual((out["written"], out["progress"]), (False, "3/3"))
        self.assertEqual(self.raw(), before)

    def test_a_plan_that_no_longer_matches_the_mockup_is_refused_and_the_state_is_not_touched(self):
        self.built_world()
        before = self.raw()
        set_canon(self.root, screen(edited=("p2",)))              # макет изменился, плана ещё не пересобирали
        msg = self.bs_refused()
        for part in ("план не соответствует макету", "хеш изменился у секций p2", "daiver_plan_write", "подпись плана слетит"):
            self.assertIn(part, msg)
        named = msg.split("хеш изменился у секций")[1].split(")")[0]
        self.assertNotIn("p1", named)
        self.assertNotIn("p3", named)
        self.assertEqual(self.raw(), before, "состояние сборки не тронуто")

    def test_a_section_missing_from_the_current_mockup_is_named_in_the_refusal(self):
        self.built_world()
        set_canon(self.root, screen(ids=("p1", "p3")))            # p2 из макета ушла
        msg = self.bs_refused()
        self.assertIn("p2", msg)
        self.assertIn("нет в каноне", msg)
        self.assertIn(f"weightBudget={BUDGET}", msg)

    def test_a_canon_that_a_new_engine_rebuilt_is_caught_the_same_way(self):
        """Хеши секций меняются и без правки макета (обновлённый движок пересобрал канон) — защита одна."""
        self.built_world()
        env = C.read_canonical(TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["canonical"])
        env["root"]["children"][0]["layout"]["gap"] = 9
        C.write_canonical(TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["canonical"], env)
        self.assertIn("хеш изменился у секций p1", self.bs_refused())


# ── новые и удалённые секции ─────────────────────────────────────────────────

class AddedRemoved(World):
    def test_new_sections_are_pending_removed_ones_drop_and_the_order_is_the_plans(self):
        self.world()
        self.bs()
        self.build("p1")
        self.build("p2")
        self.replan(screen(ids=("p2", "p3", "p4")))               # p1 ушла из макета, появилась p4
        out = self.bs()
        self.assertEqual([s["id"] for s in self.state()["sections"]], ["p2", "p3", "p4"])
        self.assertEqual(self.statuses(), {"p2": "done", "p3": "pending", "p4": "pending"})
        self.assertEqual((out["added"], out["removed"]), (["p4"], ["p1"]))
        self.assertNotIn("stale", out)
        self.assertEqual(self.section("p2")["outputs"], self.files("p2"), "прежняя done-секция сохранила свои файлы")
        self.assertNotIn("p1", json.dumps(self.state()), "удалённая секция выпала вместе с outputs")
        self.assertEqual((out["progress"], out["next"]["sectionId"]), ("1/3", "p3"))

    def test_a_new_budget_changes_the_section_ids_so_the_old_ones_drop_and_the_new_ones_start_pending(self):
        self.world()
        self.bs()
        self.build("p1")
        self.replan(budget=BIG_BUDGET)                            # весь экран — одна секция (корень)
        out = self.bs()
        self.assertEqual([s["id"] for s in self.state()["sections"]], ["Page"])
        self.assertEqual(self.statuses(), {"Page": "pending"})
        self.assertEqual(sorted(out["removed"]), ["p1", "p2", "p3"])
        self.assertEqual(out["added"], ["Page"])
        self.assertEqual((out["weightBudget"], self.state()["weightBudget"]), (BIG_BUDGET, BIG_BUDGET),
                        "бюджет плана записан: с ним ответ set называет weightBudget для daiver_section")
        self.assertIn(f"weightBudget={BIG_BUDGET}", out["nextStep"])

    def test_a_long_list_of_changes_is_capped_in_the_answer(self):
        ids = tuple(f"p{i}" for i in range(1, 31))
        self.world(screen(ids))
        self.bs()
        self.build_all(ids)
        self.replan(screen(ids, edited=ids))
        out = self.bs()
        self.assertEqual(len(out["stale"]), BS.SHOWN)
        self.assertEqual(out["staleMore"], 30 - BS.SHOWN)
        self.assertEqual(set(self.statuses().values()), {"stale"})
        self.assertLess(len(json.dumps(out, ensure_ascii=False, indent=1).splitlines()), 40)


# ── переходы статуса ─────────────────────────────────────────────────────────

class Transitions(World):
    def setUp(self):
        super().setUp()
        self.world()
        self.bs()

    def test_the_normal_cycle_pending_in_progress_done(self):
        out = self.set("p1", "in_progress")
        self.assertEqual((out["status"], out["changed"], out["progress"]), ("in_progress", True, "0/3"))
        self.assertEqual(out["next"]["sectionId"], "p1", "начатая секция остаётся next, пока не done")
        self.assertIn(f"weightBudget={BUDGET}", out["nextStep"], "с бюджетом плана вызывается daiver_section")
        self.touch(self.files("p1")[0])
        out = self.set("p1", "done", outputs=self.files("p1"), hash=self.hash_of("p1"))
        self.assertEqual((out["status"], out["progress"], out["outputs"]), ("done", "1/3", self.files("p1")))
        self.assertEqual(out["next"]["sectionId"], "p2")
        self.assertNotIn("warnings", out)
        self.assertEqual(self.section("p1")["outputs"], self.files("p1"))

    def test_done_straight_from_pending_is_a_jump_and_is_refused(self):
        msg = self.set_refused("p1", "done", outputs=self.files("p1"), hash=self.hash_of("p1"))
        self.assertIn("перескочить нельзя", msg)
        self.assertIn("status=in_progress", msg, "отказ называет, что делать")
        self.assertEqual(self.statuses()["p1"], "pending")

    def test_only_next_can_be_started_so_the_run_never_jumps_ahead_or_restarts(self):
        msg = self.set_refused("p2", "in_progress")
        self.assertIn("next — «p1»", msg)
        self.assertEqual(set(self.statuses().values()), {"pending"})
        self.build("p1")
        msg = self.set_refused("p1", "in_progress")                # «начать заново» уже собранную
        self.assertIn("уже done", msg)
        msg = self.set_refused("p3", "in_progress")
        self.assertIn("next — «p2»", msg)

    def test_starting_the_section_that_is_already_in_progress_is_a_no_op(self):
        self.set("p1", "in_progress")
        before = self.raw()
        out = self.set("p1", "in_progress")
        self.assertFalse(out["changed"])
        self.assertEqual(self.raw(), before)

    def test_status_outside_the_two_settable_ones_is_a_schema_refusal(self):
        for status in ("pending", "stale", "finished", "DONE", "", "in-progress"):
            with self.subTest(status=status):
                msg = self.set_refused("p1", status)
                self.assertIn("in_progress | done", msg)
                self.assertIn("pending и stale выставляет только движок", msg)
        self.assertEqual(set(self.statuses().values()), {"pending"})

    def test_an_unknown_section_id_is_an_error_naming_the_real_ones(self):
        for sid in ("nope", "P1", "p1 ", "Page"):
            with self.subTest(sid=sid):
                msg = self.set_refused(sid, "in_progress")
                self.assertIn("в build-state нет", msg)
                self.assertIn("p1, p2, p3", msg)
                self.assertIn("не выдумывается", msg)
        self.assertEqual(set(self.statuses().values()), {"pending"})

    def test_an_empty_section_id_is_refused_as_an_argument_not_as_an_unknown_section(self):
        for sid in ("", "  "):
            msg = self.set_refused(sid, "in_progress")
            self.assertIn("вызов не принят", msg)
            self.assertNotIn("в build-state нет", msg)

    def test_done_needs_outputs_and_hash_and_says_so_in_one_message(self):
        self.set("p1", "in_progress")
        msg = self.set_refused("p1", "done")
        self.assertIn("outputs", msg)
        self.assertIn("hash", msg)
        self.assertIn("(2)", msg, "все нарушения одним сообщением")
        self.assertIn("исправь всё перечисленное сразу", msg)
        self.assertIn("outputs", self.set_refused("p1", "done", outputs=[], hash=self.hash_of("p1")))
        self.assertIn("hash", self.set_refused("p1", "done", outputs=self.files("p1"), hash=" "))
        self.assertEqual(self.statuses()["p1"], "in_progress")

    def test_outputs_and_hash_do_not_belong_to_in_progress(self):
        msg = self.set_refused("p1", "in_progress", outputs=self.files("p1"), hash="x")
        self.assertIn("outputs и hash: только при status=done", msg)
        self.assertEqual(self.statuses()["p1"], "pending")

    def test_a_foreign_hash_is_refused_and_the_real_one_is_not_named(self):
        self.set("p1", "in_progress")
        real = self.hash_of("p1")
        for wrong in (self.hash_of("p2"), "deadbeef0000", real.upper(), " " + real):
            with self.subTest(wrong=wrong):
                msg = self.set_refused("p1", "done", outputs=self.files("p1"), hash=wrong)
                self.assertIn("hash не совпал", msg)
                self.assertIn("daiver_section", msg)
                self.assertNotIn(real, msg)
        self.assertEqual(self.statuses()["p1"], "in_progress")

    def test_every_violation_of_one_call_is_reported_together(self):
        msg = self.set_refused("p1", "done", outputs=["Bare.tsx", "../x/y.tsx"])
        for part in ("(3)", "голое имя файла", "«..»", "hash"):
            self.assertIn(part, msg)
        # нарушения состояния (перескок, чужой hash) — вторым заходом, когда аргументы в порядке, но тоже все сразу
        msg = self.set_refused("p1", "done", outputs=self.files("p1"), hash="zzz")
        self.assertIn("(2)", msg)
        self.assertIn("перескочить нельзя", msg)
        self.assertIn("hash не совпал", msg)

    def test_done_is_repeatable_it_replaces_the_outputs_and_the_same_call_changes_nothing(self):
        self.build("p1")
        before = self.raw()
        again = self.set("p1", "done", outputs=self.files("p1"), hash=self.hash_of("p1"))
        self.assertFalse(again["changed"])
        self.assertEqual(self.raw(), before)
        other = ["src/modules/orders/P1.tsx", "src/modules/orders/P1.less"]
        self.touch(other[1])
        out = self.set("p1", "done", outputs=other, hash=self.hash_of("p1"))
        self.assertTrue(out["changed"])
        self.assertEqual(self.section("p1")["outputs"], sorted(other))
        self.assertEqual(self.statuses()["p1"], "done")

    def test_a_stale_section_can_be_started_only_when_it_is_next(self):
        self.build_all()
        self.replan(screen(edited=("p1", "p3")))
        self.bs()
        self.assertEqual(self.statuses(), {"p1": "stale", "p2": "done", "p3": "stale"})
        self.assertIn("next — «p1»", self.set_refused("p3", "in_progress"))
        out = self.set("p1", "in_progress")
        self.assertEqual(out["next"]["outputs"], self.files("p1"), "outputs прежней сборки остаются при начале перестройки")
        self.assertEqual(self.section("p1")["outputs"], self.files("p1"))
        self.build("p1")                                          # повторное in_progress допустимо, пока секция next
        self.assertEqual(self.bs()["next"]["sectionId"], "p3")

    def test_stale_cannot_jump_to_done_either(self):
        self.build_all()
        self.replan(screen(edited=("p1",)))
        self.bs()
        msg = self.set_refused("p1", "done", outputs=self.files("p1"), hash=self.hash_of("p1"))
        self.assertIn("в статусе stale", msg)

    def test_a_section_left_in_progress_ahead_of_a_stale_one_waits_for_its_turn(self):
        self.build("p1")
        self.set("p2", "in_progress")
        self.replan(screen(edited=("p1",)))
        out = self.bs()
        self.assertEqual(self.statuses(), {"p1": "stale", "p2": "in_progress", "p3": "pending"})
        self.assertEqual(out["next"]["sectionId"], "p1")
        self.assertIn("next — «p1»", self.set_refused("p2", "in_progress"))
        self.build("p1")
        self.assertEqual(self.bs()["next"]["sectionId"], "p2")
        self.build("p2")
        self.assertEqual(self.bs()["next"]["sectionId"], "p3")

    def test_the_state_must_exist_before_a_section_can_move(self):
        self.state_path().unlink()
        msg = self.set_refused("p1", "in_progress")
        self.assertIn("ещё нет", msg)
        self.assertIn("daiver_build_state", msg)
        self.assertIsNone(self.raw(), "set состояния не создаёт: его создаёт сверка с планом")

    def test_unknown_frame_and_unknown_track_for_set(self):
        self.assertIn("кадра «ghost»", self.refusal("daiver_build_state_set", track=TRACK, frame="ghost", sectionId="p1",
                                                      status="in_progress"))
        self.assertIn("нет", self.refusal("daiver_build_state_set", track="STOR-999", sectionId="p1", status="in_progress"))

    def test_the_frame_argument_selects_the_same_file(self):
        out = self.call("daiver_build_state_set", track=TRACK, frame="main", sectionId="p1", status="in_progress")
        self.assertEqual(out["frame"], "main")
        self.assertEqual(self.statuses()["p1"], "in_progress")


# ── outputs: пути ОТ КОРНЯ ПРОЕКТА ───────────────────────────────────────────

class Outputs(World):
    def setUp(self):
        super().setUp()
        self.world()
        self.bs()
        self.set("p1", "in_progress")
        self.h = self.hash_of("p1")

    def done(self, outputs, **kw):
        return self.set("p1", "done", outputs=outputs, hash=self.h, **kw)

    def refused(self, outputs):
        return self.set_refused("p1", "done", outputs=outputs, hash=self.h)

    def test_a_bare_file_name_is_refused_because_the_code_gate_would_open_exactly_that_string(self):
        for bare in ("Screen.tsx", "./Screen.tsx", "Screen.tsx/", ".//Screen.tsx", "index.html"):
            with self.subTest(bare=bare):
                msg = self.refused([bare])
                self.assertIn("голое имя файла", msg)
                self.assertIn("от корня проекта", msg)
                self.assertIn(bare.strip("./").rstrip("/") or bare, msg)
        self.assertEqual(self.statuses()["p1"], "in_progress", "ничего не записано")

    def test_paths_that_cannot_be_opened_from_the_project_root_are_refused(self):
        outside = str(Path(self.tmp.name) / "elsewhere" / "x.tsx")
        cases = [("src\\modules\\x\\A.tsx", "обратные слэши"), ("../x/A.tsx", "«..»"), ("src/../../x/A.tsx", "«..»"),
                 ("~/x/A.tsx", "«~»"), (outside, "вне репозитория"), ("/etc/passwd", "вне репозитория"),
                 ("C:/proj/src/A.tsx", "вне репозитория"), ("", "пустой путь"), ("   ", "пустой путь"),
                 ("src/mod\nules/A.tsx", "управляющие знаки"), ("src/" + "x" * 300 + "/A.tsx", "длиннее")]
        for path, phrase in cases:
            with self.subTest(path=path[:40]):
                self.assertIn(phrase, self.refused([path]))
        self.assertEqual(self.statuses()["p1"], "in_progress")

    def test_the_root_itself_and_an_existing_folder_are_not_files(self):
        self.touch("src/modules/orders/A.tsx")
        self.assertIn("корень проекта", self.refused([str(self.root.resolve())]))
        msg = self.refused(["src/modules/orders"])
        self.assertIn("каталог, а не файл", msg)
        self.assertIn("«src/modules/orders»", msg)
        self.assertIn("каталог, а не файл", self.refused(["src/modules/orders/"]), "слэш на конце — тот же каталог")
        self.assertIn("каталог, а не файл", self.refused(["src/modules/orders/A.tsx", "src/modules"]))

    def test_an_absolute_path_inside_the_repo_is_recorded_from_the_project_root(self):
        self.touch("src/modules/orders/A.tsx")
        for root in (self.root, self.root.resolve()):
            with self.subTest(root=str(root)[:30]):
                out = self.done([str(root / "src" / "modules" / "orders" / "A.tsx")])
                self.assertEqual(out["outputs"], ["src/modules/orders/A.tsx"])
                self.assertEqual(self.section("p1")["outputs"], ["src/modules/orders/A.tsx"])
                self.assertNotIn(str(self.root), self.raw().decode("utf-8"), "путей машины в файле нет")

    def test_a_link_leading_out_of_the_repo_is_not_a_path_inside_it(self):
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        (outside / "x.tsx").write_text("x", encoding="utf-8")
        try:
            os.symlink(str(outside), str(self.root / "src" / "link"))
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны")
        self.assertIn("выходит за корень репо", self.refused(["src/link/x.tsx"]))

    def test_paths_are_normalized_sorted_and_deduplicated(self):
        for f in ("src/modules/orders/B.tsx", "src/modules/orders/A.tsx", "src/modules/orders/A.less"):
            self.touch(f)
        out = self.done(["./src//modules/orders/B.tsx", "src/modules/orders/A.tsx", "src/modules/orders/A.less",
                         "src/modules/orders/B.tsx", "src/modules/./orders/A.tsx"])
        want = ["src/modules/orders/A.less", "src/modules/orders/A.tsx", "src/modules/orders/B.tsx"]
        self.assertEqual(out["outputs"], want)
        self.assertEqual(self.section("p1")["outputs"], want)

    def test_the_order_the_model_names_the_files_in_does_not_change_the_file(self):
        for f in ("src/a/A.tsx", "src/a/B.tsx"):
            self.touch(f)
        self.done(["src/a/A.tsx", "src/a/B.tsx"])
        first = self.raw()
        out = self.done(["src/a/B.tsx", "src/a/A.tsx"])
        self.assertFalse(out["changed"])
        self.assertEqual(self.raw(), first)

    def test_outputs_must_be_a_non_empty_bounded_list(self):
        self.assertIn("outputs", self.refused([]))
        for f in range(BS.MAX_OUTPUTS + 1):
            self.touch(f"src/many/F{f:03d}.tsx")
        msg = self.refused([f"src/many/F{f:03d}.tsx" for f in range(BS.MAX_OUTPUTS + 1)])
        self.assertIn(str(BS.MAX_OUTPUTS), msg)
        self.assertIn("файлы секции, а не каталоги", msg)
        self.done([f"src/many/F{f:03d}.tsx" for f in range(BS.MAX_OUTPUTS)])
        self.assertEqual(len(self.section("p1")["outputs"]), BS.MAX_OUTPUTS)

    def test_the_tool_schema_refuses_a_string_or_a_non_string_item_before_the_engine_sees_it(self):
        self.assertIn("outputs", self.refusal("daiver_build_state_set", track=TRACK, sectionId="p1", status="done",
                                              outputs="src/a/A.tsx", hash=self.h))
        self.assertIn("outputs", self.refusal("daiver_build_state_set", track=TRACK, sectionId="p1", status="done",
                                              outputs=["src/a/A.tsx", 5], hash=self.h))

    def test_a_missing_file_is_a_warning_not_a_refusal_and_it_is_repeated_until_the_file_exists(self):
        out = self.done(["src/modules/orders/Ghost.tsx"])
        self.assertEqual(self.statuses()["p1"], "done", "спека: несуществующий output — предупреждение, не отказ")
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("src/modules/orders/Ghost.tsx", out["warnings"][0])
        self.assertIn("Разбери сразу", out["warnings"][0])
        again = self.bs()
        self.assertIn("p1: src/modules/orders/Ghost.tsx", again["warnings"][0], "каждая сверка повторяет предупреждение")
        self.assertEqual(self.bs()["warnings"], again["warnings"])
        self.touch("src/modules/orders/Ghost.tsx")
        self.assertNotIn("warnings", self.bs(), "файл появился — предупреждение ушло")

    def test_a_folder_that_replaced_a_recorded_file_is_missing_too(self):
        self.touch("src/modules/orders/P1.tsx")
        self.done(["src/modules/orders/P1.tsx"])
        (self.root / "src/modules/orders/P1.tsx").unlink()
        (self.root / "src/modules/orders/P1.tsx").mkdir()
        self.assertIn("p1: src/modules/orders/P1.tsx", self.bs()["warnings"][0])

    def test_fixing_the_path_of_a_done_section_is_one_more_done_call(self):
        self.done(["src/modules/orders/Typo.tsx"])
        self.touch("src/modules/orders/P1.tsx")
        out = self.done(["src/modules/orders/P1.tsx"])
        self.assertNotIn("warnings", out)
        self.assertEqual(self.section("p1")["outputs"], ["src/modules/orders/P1.tsx"])
        self.assertNotIn("warnings", self.bs())

    def test_warnings_are_listed_but_capped(self):
        paths = [f"src/gone/G{i:02d}.tsx" for i in range(15)]
        self.done(paths)
        warn = self.bs()["warnings"][0]
        self.assertIn("(15)", warn)
        self.assertIn("… +5", warn)

    def test_only_done_sections_are_checked_a_stale_one_may_have_lost_its_files(self):
        self.touch("src/modules/orders/P1.tsx")
        self.done(["src/modules/orders/P1.tsx"])
        self.replan(screen(edited=("p1",)))
        (self.root / "src/modules/orders/P1.tsx").unlink()
        out = self.bs()
        self.assertEqual(self.statuses()["p1"], "stale")
        self.assertNotIn("warnings", out)


class NormalizeUnits(unittest.TestCase):
    """normalize_output / output_problem без репо: чистые правила."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_non_strings_are_refused(self):
        for bad in (None, 5, ["a/b"], {"a": 1}, b"a/b"):
            rel, why = BS.normalize_output(self.root, bad)
            self.assertIsNone(rel)
            self.assertIn("путь — строка", why)

    def test_the_normal_form_is_a_stored_form(self):
        for raw, rel in (("src/a/B.tsx", "src/a/B.tsx"), ("./src/a/B.tsx", "src/a/B.tsx"), ("src//a/./B.tsx", "src/a/B.tsx")):
            got, why = BS.normalize_output(self.root, raw)
            self.assertEqual((got, why), (rel, None))
            self.assertIsNone(BS.output_problem(got), "то, что движок пишет, он сам читает без нареканий")

    def test_the_reader_rejects_what_the_writer_never_writes(self):
        for bad, phrase in (("Screen.tsx", "голое имя файла"), ("/abs/a/B.tsx", "от корня проекта"), ("src\\a.tsx", "от корня проекта"),
                            ("src/../a.tsx", "«..»"), ("src//a.tsx", "пустые звенья"), ("./src/a.tsx", "пустые звенья"),
                            ("~/a/b.tsx", "от корня проекта"), ("C:/a/b.tsx", "от корня проекта"), ("", "непустая строка"),
                            (5, "непустая строка"), ("src/" + "x" * 300, "длиннее")):
            self.assertIn(phrase, BS.output_problem(bad) or "", repr(bad))


# ── повреждённый и чужой файл ────────────────────────────────────────────────

class Integrity(World):
    def setUp(self):
        super().setUp()
        self.world()
        self.bs()
        self.build("p1")
        self.good = self.state()

    def mutated(self, fn):
        data = json.loads(json.dumps(self.good))
        fn(data)
        return data

    def test_a_damaged_file_is_a_refusal_with_the_cause_for_both_tools_and_it_is_never_rewritten(self):
        cases = [("{не json", "не JSON"), ("[]", "ожидается JSON-объект"), ("", "не JSON"), ('"done"', "ожидается JSON-объект")]
        for text, cause in cases:
            with self.subTest(text=text):
                self.write_state(text)
                before = self.raw()
                for msg in (self.bs_refused(), self.set_refused("p2", "in_progress")):
                    self.assertIn("build-state.json", msg)
                    self.assertIn(cause, msg)
                    self.assertIn("руками не правится", msg)
                    self.assertIn("удаляется", msg)
                self.assertEqual(self.raw(), before, "молчаливого сброса нет: прогресс не теряется")

    def test_a_file_that_does_not_fit_the_format_is_refused_with_every_cause(self):
        def drop(key):
            return lambda d: d.pop(key)

        cases = [
            ("статус вне перечня", lambda d: d["sections"][0].update(status="finished"), "sections[0].status"),
            ("done без outputs", lambda d: d["sections"][0].pop("outputs"), "статус done без outputs"),
            ("done с пустыми outputs", lambda d: d["sections"][0].update(outputs=[]), "статус done без outputs"),
            ("голое имя в outputs", lambda d: d["sections"][0].update(outputs=["A.tsx"]), "голое имя файла"),
            ("абсолютный путь в outputs", lambda d: d["sections"][0].update(outputs=["/tmp/a/b.tsx"]), "от корня проекта"),
            ("outputs не список", lambda d: d["sections"][0].update(outputs="a/b.tsx"), "список путей"),
            ("чужой кадр", lambda d: d.update(frame="dialog"), "файл другого кадра"),
            ("чужой трек", lambda d: d.update(track="STOR-7"), "файл другого трека"),
            ("другая схема", lambda d: d.update(schema=2), "schema"),
            ("неизвестное поле", lambda d: d.update(extra=1), "неизвестное поле"),
            ("неизвестное поле секции", lambda d: d["sections"][0].update(note="x"), "неизвестное поле"),
            ("секций нет", lambda d: d.update(sections=[]), "непустой список секций"),
            ("секции не список", lambda d: d.update(sections="p1"), "непустой список секций"),
            ("секция не объект", lambda d: d["sections"].append("p9"), "ожидается объект"),
            ("повтор id", lambda d: d["sections"].append(dict(d["sections"][1])), "уже есть в sections"),
            ("нет бюджета", drop("weightBudget"), "weightBudget"),
            ("бюджет не число", lambda d: d.update(weightBudget="200"), "weightBudget"),
            ("нет хеша", lambda d: d["sections"][1].pop("hash"), "hash"),
            ("нет id", lambda d: d["sections"][1].pop("id"), "id"),
        ]
        for title, fn, phrase in cases:
            with self.subTest(title):
                self.write_state(self.mutated(fn))
                before = self.raw()
                for msg in (self.bs_refused(), self.set_refused("p2", "in_progress")):
                    self.assertIn("не проходит проверку", msg)
                    self.assertIn(phrase, msg)
                self.assertEqual(self.raw(), before)

    def test_the_ladder_reads_the_same_damage_as_an_absent_file_and_names_it(self):
        self.write_state("{не json")
        st = self.call("daiver_status")
        self.assertEqual(self.stage(), "planned")
        self.assertTrue(any(w.startswith("artifact-unreadable") and "build-state.json" in w for w in st["warnings"]))

    def test_deleting_the_damaged_file_starts_the_build_over_from_the_first_section(self):
        self.write_state("{не json")
        self.state_path().unlink()
        out = self.bs()
        self.assertTrue(out["created"])
        self.assertEqual((out["progress"], out["next"]["sectionId"]), ("0/3", "p1"))

    def test_a_valid_file_in_another_formatting_is_read_and_rewritten_canonically(self):
        """Файл читается по содержанию, а не по байтам: форматирование не причина отказа (пишет-то всегда движок)."""
        self.write_state(json.dumps(self.good, ensure_ascii=False))
        out = self.bs()
        self.assertEqual(out["progress"], "1/3")
        self.assertTrue(out["written"], "сверка вернула файлу канонический вид")
        self.assertEqual(self.raw(), (json.dumps(self.good, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))


# ── запись: атомарность, замок, детерминизм ──────────────────────────────────

class Recorder:
    """Подмена BS._lock: считает входы и в момент входа может «вклиниться соседом» (запись, сделанная между чтением и замком)."""

    def __init__(self):
        self.entered = 0
        self.hook = None

    def __call__(self, root):
        outer = self

        class Ctx:
            def __enter__(self):
                outer.entered += 1
                if outer.hook:
                    outer.hook()
                return self

            def __exit__(self, *exc):
                return False
        return Ctx()


class Writes(World):
    def test_a_failed_write_leaves_the_old_file_and_no_scraps(self):
        self.world()
        with mock.patch("daiver.plan.os.replace", side_effect=OSError(13, "нет прав")):
            self.assertIn("запись", self.bs_refused())
        self.assertIsNone(self.raw(), "создания не вышло — файла нет")
        self.assertEqual(list(self.state_path().parent.glob("*.tmp")), [])
        self.bs()
        before = self.raw()
        self.set("p1", "in_progress")
        after_ok = self.raw()
        with mock.patch("daiver.plan.os.replace", side_effect=OSError(13, "нет прав")):
            self.assertIn("не обновлён", self.set_refused("p1", "done", outputs=self.files("p1"), hash=self.hash_of("p1")))
        self.assertEqual(self.raw(), after_ok)
        self.assertNotEqual(before, after_ok)
        self.assertEqual(list(self.state_path().parent.glob("*.tmp")), [])

    def test_the_lock_is_taken_to_write_and_not_taken_to_read(self):
        self.world()
        rec = Recorder()
        with mock.patch.object(BS, "_lock", rec):
            self.bs()
            self.assertEqual(rec.entered, 1, "создание — под замком")
            self.bs()
            self.assertEqual(rec.entered, 1, "ничего не изменилось — замок не берётся")
            self.set("p1", "in_progress")
            self.assertEqual(rec.entered, 2)
            self.set("p1", "in_progress")
            self.assertEqual(rec.entered, 3, "переход читает и пишет состояние — всегда под замком")

    def test_the_real_lock_file_lives_with_the_other_engine_locks(self):
        self.world()
        self.bs()
        self.assertTrue((self.root / "ground" / BS.LOCK_NAME).is_file())

    def test_reconcile_works_from_the_state_read_under_the_lock_not_from_the_one_read_before_it(self):
        self.world()
        self.bs()

        def neighbor():                                           # между чтением и замком сосед отметил p1 начатой
            data = self.state()
            data["sections"][0]["status"] = "in_progress"
            self.write_state(data)

        self.replan(screen(ids=("p1", "p2", "p3", "p4")))         # у плана новая секция — сверке есть что писать
        rec = Recorder()
        rec.hook = neighbor
        with mock.patch.object(BS, "_lock", rec):
            out = self.bs()
        self.assertEqual(rec.entered, 1)
        self.assertEqual(out["added"], ["p4"])
        self.assertEqual(self.statuses(), {"p1": "in_progress", "p2": "pending", "p3": "pending", "p4": "pending"},
                         "запись соседа не потеряна")

    def test_a_transition_is_judged_by_the_state_read_under_the_lock(self):
        self.world()
        self.bs()
        self.build("p1")

        def neighbor():                                           # пока ждали замок, p1 вернули в работу
            data = self.state()
            data["sections"][0]["status"] = "in_progress"
            self.write_state(data)

        rec = Recorder()
        rec.hook = neighbor
        with mock.patch.object(BS, "_lock", rec):
            msg = self.set_refused("p2", "in_progress")
        self.assertIn("next — «p1»", msg)
        self.assertEqual(self.statuses()["p2"], "pending")

    def test_one_input_one_file_byte_for_byte_in_two_separate_repos(self):
        raws = []
        for n in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / f"repo{n}"
                root.mkdir()
                make_world(root)
                raws.append(walk(root))
        self.assertEqual(raws[0], raws[1])
        self.assertNotIn(b"repo", raws[0], "в файле нет ни одного имени из пути репо")

    def test_the_file_of_a_state_that_walked_the_same_way_is_the_same_in_any_order_of_equal_calls(self):
        self.world()
        self.bs()
        self.build("p1")
        a = self.raw()
        self.bs()
        self.set("p1", "done", outputs=self.files("p1"), hash=self.hash_of("p1"))
        self.assertEqual(self.raw(), a)


# ── сверка с планом: чистые правила ──────────────────────────────────────────

def sec(i, status="pending", h=None, outputs=None, name=None):
    s = {"id": i, "name": name or i.upper(), "hash": h or "h-" + i, "status": status}
    if outputs:
        s["outputs"] = list(outputs)
    return s


def seed(i, h=None, name=None):
    return {"id": i, "name": name or i.upper(), "hash": h or "h-" + i}


class ReconcileRules(unittest.TestCase):
    def test_creation_makes_every_section_pending_in_the_order_of_the_seeds_and_reports_nothing(self):
        out, rep = BS.reconcile(None, [seed("b"), seed("a")])
        self.assertEqual(out, [sec("b"), sec("a")])
        self.assertEqual(rep, {"stale": [], "added": [], "removed": []}, "при создании «новыми» были бы все")

    def test_an_unchanged_hash_keeps_status_and_outputs_whatever_the_status(self):
        old = [sec("a", "done", outputs=["src/a/A.tsx"]), sec("b", "in_progress"), sec("c", "stale", outputs=["src/a/C.tsx"]), sec("d")]
        out, rep = BS.reconcile(old, [seed(s["id"]) for s in old])
        self.assertEqual(out, old)
        self.assertEqual(rep, {"stale": [], "added": [], "removed": []})

    def test_a_changed_hash_turns_only_done_into_stale_and_keeps_the_outputs(self):
        old = [sec("a", "done", outputs=["src/a/A.tsx"]), sec("b", "in_progress"), sec("c", "stale", outputs=["src/a/C.tsx"]), sec("d")]
        out, rep = BS.reconcile(old, [seed(s["id"], h="NEW-" + s["id"]) for s in old])
        self.assertEqual([(s["id"], s["status"], s["hash"]) for s in out],
                         [("a", "stale", "NEW-a"), ("b", "in_progress", "NEW-b"), ("c", "stale", "NEW-c"), ("d", "pending", "NEW-d")])
        self.assertEqual(out[0]["outputs"], ["src/a/A.tsx"])
        self.assertEqual(out[2]["outputs"], ["src/a/C.tsx"])
        self.assertEqual(rep["stale"], ["a"])

    def test_new_ids_are_pending_missing_ids_drop_and_the_order_and_names_are_the_seeds(self):
        old = [sec("a", "done", outputs=["src/a/A.tsx"]), sec("b", "done", outputs=["src/a/B.tsx"]), sec("c")]
        seeds = [seed("c", name="Новое имя"), seed("x"), seed("a")]
        out, rep = BS.reconcile(old, seeds)
        self.assertEqual([s["id"] for s in out], ["c", "x", "a"])
        self.assertEqual(out[0]["name"], "Новое имя", "name может обновиться")
        self.assertEqual(out[1], sec("x"))
        self.assertEqual(out[2], old[0])
        self.assertEqual((rep["added"], rep["removed"], rep["stale"]), (["x"], ["b"], []))

    def test_reconcile_never_touches_its_input_and_is_idempotent(self):
        import copy
        old = [sec("a", "done", outputs=["src/a/A.tsx"]), sec("b")]
        frozen = copy.deepcopy(old)
        seeds = [seed("a", h="new"), seed("b")]
        once, rep1 = BS.reconcile(old, seeds)
        self.assertEqual(old, frozen)
        twice, rep2 = BS.reconcile(once, seeds)
        self.assertEqual(twice, once)
        self.assertEqual(rep2, {"stale": [], "added": [], "removed": []})
        self.assertEqual(rep1["stale"], ["a"])

    def test_next_is_the_first_section_that_is_not_done_including_one_left_in_progress(self):
        sections = [sec("a", "done", outputs=["src/a/A.tsx"]), sec("b", "in_progress"), sec("c", "stale"), sec("d")]
        self.assertEqual(BS.next_section(sections)["id"], "b")
        sections[1]["status"] = "done"
        sections[1]["outputs"] = ["src/a/B.tsx"]
        self.assertEqual(BS.next_section(sections)["id"], "c")
        self.assertIsNone(BS.next_section([sec("a", "done", outputs=["src/a/A.tsx"])]))
        self.assertEqual(BS.progress(sections), (2, 4))

    def test_the_statuses_are_exactly_the_ones_the_ladder_reads(self):
        self.assertEqual(BS.STATUSES, ST.BUILD_STATUSES)
        self.assertEqual(BS.STATUSES, ("pending", "in_progress", "done", "stale"), "перечень спеки и контракта лестницы 1.10")
        self.assertEqual(BS.SETTABLE, ("in_progress", "done"))


# ── читатель для гейта сборки ────────────────────────────────────────────────

class GateReader(World):
    def test_no_state_not_done_missing_outputs_and_not_reconciled_each_blocks(self):
        self.world()
        self.assertIn("build-state нет", BS.gate_problems(self.root, TRACK)[0])
        self.bs()
        msg = BS.gate_problems(self.root, TRACK)
        self.assertEqual(len(msg), 1)
        self.assertIn("секций не готово 3 из 3: p1 (pending), p2 (pending), p3 (pending)", msg[0])
        self.build_all()
        self.assertEqual(BS.gate_problems(self.root, TRACK), [], "всё done, файлы на месте — гейту нечего возразить")
        (self.root / self.files("p2")[0]).unlink()
        msg = BS.gate_problems(self.root, TRACK)
        self.assertEqual(len(msg), 1)
        self.assertIn("outputs, которых нет на диске (1): p2: src/modules/orders/P2.tsx", msg[0])

    def test_a_replanned_but_not_reconciled_state_is_not_ready_even_if_it_looks_all_done(self):
        self.world()
        self.bs()
        self.build_all()
        self.replan(screen(ids=("p1", "p2", "p3", "p4")))
        msg = BS.gate_problems(self.root, TRACK)
        self.assertTrue(any("не сверен с планом" in m for m in msg), msg)
        self.assertTrue(any("p4 (pending)" in m for m in msg), "недостающее видно по сверенному виду")
        self.bs()
        self.assertEqual(len(BS.gate_problems(self.root, TRACK)), 1)
        self.build("p4")
        self.assertEqual(BS.gate_problems(self.root, TRACK), [])

    def test_stale_sections_block_the_gate_and_a_plan_that_does_not_match_the_mockup_is_named(self):
        self.world()
        self.bs()
        self.build_all()
        self.replan(screen(edited=("p2",)))
        self.bs()
        msg = BS.gate_problems(self.root, TRACK)
        self.assertEqual(len(msg), 1)
        self.assertIn("p2 (stale)", msg[0])
        set_canon(self.root, screen(edited=("p2", "p3")))
        self.assertIn("план не соответствует макету", BS.gate_problems(self.root, TRACK)[0])

    def test_the_reader_only_reads_the_disk(self):
        self.world()
        self.bs()
        self.build("p1")
        before = snapshot(self.root)
        BS.gate_problems(self.root, TRACK)
        self.assertEqual(snapshot(self.root), before)

    def test_no_track_and_no_plan_are_reported_not_raised(self):
        self.assertIn("нет", BS.gate_problems(self.root, TRACK)[0])
        init_repo(self.root)
        build_repo(self.root, screen())
        self.assertIn("плана кадра нет", BS.gate_problems(self.root, TRACK)[0])


# ── секция без кода: корень решён ignore ─────────────────────────────────────

class NoCodeSections(World):
    """Секция, чей корень в плане решён ignore, ничего не строит, а `outputs` у `done` обязателен — без особого случая тупик (3.1 →
    README). Решение 3.2: такую секцию закрывает сама сверка; модели нечего пропускать, а гейту — требовать."""

    def decide(self, verdicts):
        decide(self.root, verdicts)

    def replan_now(self):
        return replan_now(self.root)

    def decor_world(self, verdicts=None, ids=("p1", "p2", "p3")):
        make_decor_world(self.root, {"p2": "ignore"} if verdicts is None else verdicts, ids)

    def test_the_plan_names_the_section_whose_root_is_ignored_and_no_other(self):
        self.decor_world({"p1": "local", "p2": "ignore", "p3": "local"})
        self.assertEqual(PL.no_code_sections(self.plan_json()), ["p2"])

    def test_the_engine_closes_such_a_section_at_once_and_the_run_walks_around_it(self):
        self.decor_world()
        out = self.bs()
        self.assertEqual((out["progress"], out["noCode"], out["next"]["sectionId"]), ("1/3", ["p2"], "p1"))
        self.assertEqual(self.section("p2"), {"id": "p2", "name": "Decoration p2", "hash": self.hash_of("p2"), "status": "done",
                                              "noCode": True})
        self.assertEqual(self.stage(), "building")
        self.assertEqual(self.build("p1")["next"]["sectionId"], "p3", "после p1 — p3: закрытая секция в очередь не попадает")
        out = self.build("p3")
        self.assertEqual((out["progress"], out["next"]), ("3/3", None))
        self.assertEqual(self.stage(), "built")
        self.assertEqual(BS.gate_problems(self.root, TRACK), [], "гейту нечего возразить: у секции без кода файлов нет и не требуется")
        self.assertNotIn("outputs", self.section("p2"))
        self.assertEqual(self.bs()["next"], None)
        self.assertEqual(self.stage(), "built", "повторный вызов ничего не перезапускает")

    def test_the_gate_reader_does_not_list_the_closed_section_among_the_unfinished_ones(self):
        self.decor_world()
        self.bs()
        msg = BS.gate_problems(self.root, TRACK)
        self.assertEqual(len(msg), 1)
        self.assertIn("секций не готово 2 из 3: p1 (pending), p3 (pending)", msg[0])

    def test_the_model_cannot_move_a_closed_section_and_the_state_stays(self):
        self.decor_world()
        self.bs()
        before = self.raw()
        for status, extra in (("in_progress", {}), ("done", {"outputs": ["src/modules/orders/P2.tsx"], "hash": self.hash_of("p2")})):
            msg = self.set_refused("p2", status, **extra)
            self.assertIn("без кода", msg)
            self.assertIn("ignore", msg)
            self.assertIn("next из daiver_build_state", msg)
        self.assertEqual(self.raw(), before)

    def test_every_section_without_code_is_a_refusal_like_an_empty_build_order(self):
        self.decor_world({"p1": "ignore", "p2": "ignore", "p3": "ignore"})
        msg = self.bs_refused()
        self.assertIn("корни всех секций кадра решены ignore", msg)
        self.assertIn("daiver_plan_write", msg)
        self.assertIsNone(self.raw(), "состояние не создаётся")
        self.assertIn("корни всех секций", BS.gate_problems(self.root, TRACK)[0])

    def test_the_file_of_a_closed_section_is_deterministic_and_the_ladder_reads_it(self):
        self.decor_world()
        self.bs()
        self.assertIn(b'"noCode": true', self.raw())
        self.assertEqual(self.statuses(), {"p1": "pending", "p2": "done", "p3": "pending"})
        t = next(t for t in self.call("daiver_status")["tracks"] if t["name"] == TRACK)
        self.assertEqual((t["stage"], t["frames"][0]["build"]), ("building", "1/3"), "лестница считает секцию без кода готовой")
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        make_decor_world(other, {"p2": "ignore"}, ("p1", "p2", "p3"))
        T.call("daiver_build_state", other, {"track": TRACK})
        self.assertEqual((other / STATE_REL).read_bytes(), self.raw(), "один вход — побайтно один файл в любом репо")

    def test_a_changed_hash_updates_a_closed_section_but_never_makes_it_stale(self):
        self.decor_world()
        self.bs()
        old = self.section("p2")["hash"]
        set_canon(self.root, decor_screen(named=("p2",), edited=("p2",)))
        self.replan_now()
        out = self.bs()
        self.assertNotIn("stale", out)
        self.assertNotEqual(self.section("p2")["hash"], old)
        self.assertEqual((self.section("p2")["status"], self.section("p2")["noCode"]), ("done", True))

    def test_a_verdict_that_stops_being_ignore_reopens_the_section_from_scratch(self):
        self.decor_world()
        self.bs()
        self.build("p1")
        self.decide({"p2": "local"})
        self.replan_now()
        out = self.bs()
        self.assertEqual((out["progress"], out["added"], out["next"]["sectionId"], "noCode" in out), ("1/3", ["p2"], "p2", False))
        self.assertEqual(self.section("p2")["status"], "pending")
        self.assertNotIn("noCode", self.section("p2"))
        self.build("p2")
        self.build("p3")
        self.assertEqual(BS.gate_problems(self.root, TRACK), [])

    def test_a_built_section_that_becomes_ignore_keeps_its_files_and_the_warning_is_given_once(self):
        self.decor_world({"p2": "local"})
        self.bs()
        self.build_all()
        files = self.files("p2")
        self.decide({"p2": "ignore"})
        self.replan_now()
        out = self.bs()
        self.assertEqual((out["progress"], out["noCode"], out["next"]), ("3/3", ["p2"], None))
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn(files[0], out["warnings"][0])
        self.assertIn("остаются на диске", out["warnings"][0])
        self.assertTrue((self.root / files[0]).is_file(), "движок код не удаляет")
        self.assertNotIn("outputs", self.section("p2"))
        self.assertNotIn("warnings", self.bs(), "предупреждение — в вызове, который записал перемену")

    def test_the_list_of_closed_sections_is_capped(self):
        ids = tuple(f"p{i}" for i in range(1, 13))
        self.decor_world({sid: "ignore" for sid in ids[:11]}, ids=ids)
        out = self.bs()
        self.assertEqual((len(out["noCode"]), out["noCodeMore"], out["progress"]), (BS.SHOWN, 1, "11/12"))
        self.assertEqual(out["next"]["sectionId"], "p12")

    def test_a_hand_edited_file_is_checked_against_the_new_key(self):
        self.decor_world()
        self.bs()
        good = self.state()
        cases = {
            "noCode, отличный от true": lambda s: s["sections"][1].update(noCode=False),
            "секция без кода не done": lambda s: s["sections"][1].update(status="pending"),
            "секция без кода с outputs": lambda s: s["sections"][1].update(outputs=["src/modules/orders/P2.tsx"]),
            "done без outputs у обычной секции": lambda s: s["sections"][0].update(status="done"),
        }
        for label, edit in cases.items():
            with self.subTest(label):
                data = json.loads(json.dumps(good))
                edit(data)
                self.write_state(data)
                msg = self.bs_refused()
                self.assertIn("не проходит проверку", msg)
                self.assertTrue("noCode" in msg or "без кода" in msg or "done без outputs" in msg, msg)
                self.assertIn("sections[", msg)
        self.write_state(good)
        self.bs()


class NoCodeUnits(unittest.TestCase):
    def test_only_the_root_of_a_section_decided_ignore_counts(self):
        plan = {"sections": [
            {"id": "a", "nodes": [{"id": "a", "resolution": "ignore"}]},
            {"id": "b", "nodes": [{"id": "b", "resolution": "local"}]},
            {"id": "c", "nodes": [{"id": "inner", "resolution": "ignore"}]},
            {"id": "d", "nodes": [{"id": "d", "component": "Button"}]},
            {"id": "e", "nodes": []},
            {"id": "f", "nodes": [{"id": "x", "resolution": "ignore"}, {"id": "f", "resolution": "ignore"}]},
        ]}
        self.assertEqual(PL.no_code_sections(plan), ["a", "f"], "корень секции, а не любой ignore внутри; порядок — плана")

    def test_unreadable_shapes_are_not_codeless_and_do_not_raise(self):
        for bad in (None, [], "x", {}, {"sections": None}, {"sections": "a"}, {"sections": [None, 5, "s", {}]},
                    {"sections": [{"id": 5, "nodes": [{"id": 5, "resolution": "ignore"}]}]},
                    {"sections": [{"id": "a", "nodes": "ignore"}]}, {"sections": [{"id": "a", "nodes": [None, "ignore", 3]}]}):
            self.assertEqual(PL.no_code_sections(bad), [], repr(bad))

    def test_reconcile_closes_reopens_and_drops_by_the_seeds_alone(self):
        s = lambda i, **kw: dict({"id": i, "name": i.upper(), "hash": "h-" + i}, **kw)               # noqa: E731
        out, rep = BS.reconcile(None, [s("a"), s("b", noCode=True)])
        self.assertEqual(out, [{"id": "a", "name": "A", "hash": "h-a", "status": "pending"},
                               {"id": "b", "name": "B", "hash": "h-b", "status": "done", "noCode": True}])
        self.assertEqual(rep, {"stale": [], "added": [], "removed": []}, "закрытая секция «новой» не называется")
        again, rep = BS.reconcile(out, [s("a"), s("b", noCode=True)])
        self.assertEqual((again, rep), (out, {"stale": [], "added": [], "removed": []}), "идемпотентно")
        moved, rep = BS.reconcile(out, [s("a"), dict(s("b", noCode=True), hash="h2")])
        self.assertEqual((moved[1]["status"], moved[1]["hash"], rep["stale"]), ("done", "h2", []), "хеш не делает её stale")
        reopened, rep = BS.reconcile(out, [s("a"), s("b")])
        self.assertEqual(reopened[1], {"id": "b", "name": "B", "hash": "h-b", "status": "pending"})
        self.assertEqual(rep["added"], ["b"])
        built = [{"id": "b", "name": "B", "hash": "h-b", "status": "done", "outputs": ["src/m/B.tsx"]}]
        closed, rep = BS.reconcile(built, [s("b", noCode=True)])
        self.assertEqual(closed, [{"id": "b", "name": "B", "hash": "h-b", "status": "done", "noCode": True}], "outputs прежней сборки сняты")

    def test_the_state_check_knows_the_new_key(self):
        sec = {"id": "a", "name": "A", "hash": "h", "status": "done", "noCode": True}
        plain = {"id": "b", "name": "B", "hash": "h2", "status": "pending"}
        data = {"schema": 1, "track": TRACK, "frame": "main", "weightBudget": 200, "sections": [sec, plain]}
        self.assertEqual(BS.check_state(data, TRACK, "main"), [])
        self.assertIn("noCode", BS.SECTION_KEYS)
        for edit in ({"noCode": "yes"}, {"noCode": False}, {"status": "stale"}, {"status": "pending"}, {"outputs": ["src/m/A.tsx"]}):
            bad = dict(data, sections=[dict(sec, **edit), plain])
            self.assertTrue(BS.check_state(bad, TRACK, "main"), edit)


# ── настоящий образец ────────────────────────────────────────────────────────

class ModalSample(World):
    """«Modal» из Pixso: pull → normalize → резолв четырёх кандидатов → план → сборка единственной секции."""

    def modal(self):
        init_repo(self.root)
        write_ds(self.root, ["ModalHeader", "ModalAction"])
        inbox = self.root / ".daiver" / "inbox"
        inbox.mkdir(parents=True)
        shutil.copyfile(str(FIXTURE), str(inbox / "main.dsl.json"))
        self.call("daiver_pixso_pull", track=TRACK, dslPath=".daiver/inbox/main.dsl.json", frameUrl=URL, frame="main")
        self.call("daiver_normalize", track=TRACK)
        fin = lambda v: self.call("daiver_resolve_finalize", track=TRACK, verdict=v)          # noqa: E731
        fin({"nodeId": "2:153702", "decision": "local", "confidence": "high", "rationale": "оболочка экрана"})
        fin({"nodeId": "2:153703", "decision": "component", "component": "ModalHeader", "confidence": "high",
             "rationale": "шапка по составу слотов"})
        fin({"nodeId": "2:153704", "decision": "component", "component": "ModalAction", "confidence": "high",
             "rationale": "блок действий по составу слотов"})
        self.call("daiver_resolve_report", track=TRACK)
        self.call("daiver_plan_write", track=TRACK)

    def test_the_sample_is_one_section_and_it_is_built_in_one_cycle(self):
        self.modal()
        out = self.bs()
        self.assertEqual(out["progress"], "0/1")
        self.assertEqual(out["next"], {"sectionId": "2:153702", "name": "Modal", "status": "pending"})
        self.assertEqual(self.state()["sections"], [{"hash": "0ccf768ee9d7", "id": "2:153702", "name": "Modal", "status": "pending"}])
        self.assertEqual(self.state()["weightBudget"], 24000, "бюджет по умолчанию — конфиг/24000")
        self.assertEqual(self.stage(), "building")
        self.set("2:153702", "in_progress")
        section = self.call("daiver_section", track=TRACK, sectionId="2:153702", weightBudget=24000)["section"]
        self.assertEqual(section["hash"], "0ccf768ee9d7", "хеш секции из daiver_section — тот, что в состоянии")
        files = ["src/modules/modal/Modal.tsx", "src/modules/modal/mocks.ts"]
        for f in files:
            self.touch(f)
        out = self.set("2:153702", "done", outputs=files, hash=section["hash"])
        self.assertEqual((out["progress"], out["next"]), ("1/1", None))
        self.assertEqual(self.stage(), "built")
        self.assertEqual(BS.gate_problems(self.root, TRACK), [])

    def test_a_started_build_state_does_not_lock_the_frame_and_the_same_export_changes_nothing(self):
        """Правило 6.3: «собран» — все секции done (`status.frame_built`), а не «есть build-state.json». Начатая сборка кадр не запирает:
        тот же экспорт ничего не меняет (mode unchanged), состояние сборки на месте; ревизию заводит только pull поверх собранного."""
        self.modal()
        self.bs()
        before = self.state()
        out = self.call("daiver_pixso_pull", track=TRACK, dslPath=".daiver/inbox/main.dsl.json", frameUrl=URL, frame="main")
        self.assertEqual((out["mode"], out["unchanged"]), ("unchanged", True))
        self.assertEqual(self.state(), before)
        self.assertNotIn("baseline", TR.get_frame(TR.load(self.root, TRACK), "main"))


# ── реестр инструментов ──────────────────────────────────────────────────────

class Registry(unittest.TestCase):
    def test_both_tools_are_registered_with_closed_schemas_and_no_arbitrary_paths(self):
        a, b = T.TOOLS["daiver_build_state"]["schema"], T.TOOLS["daiver_build_state_set"]["schema"]
        self.assertEqual(sorted(a["properties"]), ["frame", "track"])
        self.assertEqual(a["required"], ["track"])
        self.assertEqual(sorted(b["properties"]), ["frame", "hash", "outputs", "sectionId", "status", "track"])
        self.assertEqual(sorted(b["required"]), ["sectionId", "status", "track"])
        for schema in (a, b):
            self.assertIs(schema["additionalProperties"], False)
            for forbidden in ("statePath", "outPath", "path", "sections", "buildOrder"):
                self.assertNotIn(forbidden, schema["properties"], "путь к состоянию и секции модель не передаёт: их знает код")
        self.assertEqual(b["properties"]["status"]["enum"], ["in_progress", "done"])
        self.assertEqual(b["properties"]["outputs"]["maxItems"], BS.MAX_OUTPUTS)

    def test_the_hook_inventory_lists_both(self):
        text = (HERE.parents[2] / "hooks" / "test_daiver_mcp.py").read_text(encoding="utf-8")
        for name in ("daiver_build_state", "daiver_build_state_set"):
            self.assertIn(f'"{name}"', text)

    def test_descriptions_carry_the_rules_the_model_must_follow(self):
        a, b = T.TOOLS["daiver_build_state"]["description"], T.TOOLS["daiver_build_state_set"]["description"]
        for part in ("next", "done → stale", "weightBudget", "сначала пересобери план", "noCode", "ignore"):
            self.assertIn(part, a)
        for part in ("ОТ КОРНЯ ПРОЕКТА", "голое имя файла", "section.hash", "Перескочить нельзя", "warnings"):
            self.assertIn(part, b)

    def test_argument_errors_are_tool_errors_not_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, args in (("daiver_build_state", {}), ("daiver_build_state", {"track": TRACK, "bogus": 1}),
                               ("daiver_build_state", {"track": 5}), ("daiver_build_state_set", {"track": TRACK}),
                               ("daiver_build_state_set", {"track": TRACK, "sectionId": "p1", "status": "done", "outputs": "x/y.ts"}),
                               ("daiver_build_state_set", {"track": TRACK, "sectionId": "p1", "status": 1})):
                with self.subTest(name=name, args=str(args)[:50]):
                    with self.assertRaises(T.ToolError):
                        T.call(name, root, args)


# ── код модуля: детерминизм и гигиена ────────────────────────────────────────

class Hygiene(unittest.TestCase):
    PATH = HERE / "daiver" / "build_state.py"

    def test_no_time_no_randomness_in_what_goes_into_the_file(self):
        tree = ast.parse(self.PATH.read_text(encoding="utf-8"), filename=str(self.PATH))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
        self.assertFalse(imported & {"time", "datetime", "random", "uuid", "secrets"}, sorted(imported))
        self.assertNotIn("os.environ", self.PATH.read_text(encoding="utf-8"))

    def test_the_module_keeps_the_python_floor_and_the_encoding_rule(self):
        text = self.PATH.read_text(encoding="utf-8")
        self.assertIn("from __future__ import annotations", text)
        tree = ast.parse(text)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("read_text", "write_text"):
                self.assertTrue(any(k.arg == "encoding" for k in n.keywords), f"{n.lineno}: {n.func.attr} без encoding=")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
                self.fail(f"{n.lineno}: open() — файлы читает pathlib с явной кодировкой")
            self.assertNotIsInstance(n, ast.NamedExpr, f"{getattr(n, 'lineno', '?')}: walrus")

    def test_the_writer_is_the_atomic_one_of_the_plan_not_a_second_invention(self):
        text = self.PATH.read_text(encoding="utf-8")
        self.assertIn("PL.write_atomic", text)
        self.assertNotIn("os.replace", text)
        self.assertNotIn(".write_text(", text, "состояние пишется байтами через write_atomic, а не write_text")
        self.assertNotIn(".write_bytes(", text)


if __name__ == "__main__":
    unittest.main()
