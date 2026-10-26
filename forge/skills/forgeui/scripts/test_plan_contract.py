#!/usr/bin/env python3
"""test_plan_contract.py — план секций, контракт готовности, подпись и гейт ui-design (задача 2.6; спека §11.5–11.6, §12.2–12.4, §20.4;
INV A1, A11, B12, C8, C9, D1, F6; QG U12).

Пинит как свойства КОДА (не текста брифа):
  • `daiver_plan_write`: план пишет валидация и арифметика, а не файл модели — секции и их хеши из канона (при том же weightBudget,
    что у outline), решения по узлам из ПОДТВЕРЖДЁННОГО резолва, buildOrder из реальных id секций с хешами (создатели общих
    local-компонентов → потребители → остальное в порядке документа; occluded не строится; пролог не секция), решения по раскладке
    `unproven` (§11.4: apply и group без свидетельства не принимаются, группа считает gap и бокс по боксам канона); неверный
    buildOrder (чужой id, не тот хеш, пропущенная секция, не тот порядок) — отказ одним списком, ничего не записано; детерминизм
    (те же данные — те же байты); `plan.json` — JSON-объект, как требует лестница статуса 1.10;
  • `daiver_plan_contract`: ровно 16 проверок `initialized, library-source, browser, requirement, canonical, plan, resolutions, assets,
    goal-image, specs, state, baseline, sources, implementation, dev-command, signature` — каждая нарушается по отдельности и называется
    (id, кадр, состояние, blocking, blockedReason, owner, detail, ask, record, degradesTo); `ready` / `complete` / `signed`;
    неразбираемый или устаревший план не проходит; упавшая проверка — блокирующий пункт, а не «прошла»; planUncertainty считает узел один раз;
  • подпись — согласие `ui-plan-<KEY>`, привязанное к `plan.lock.json` (настоящий `record_approval.py --bind-file`): перетяжка
    требования, пересборка плана, правка вердикта/спеки/файла плана руками делают её stale, устаревшее согласие снимает писатель
    (иначе `record_approval` не записал бы новое), гейт и `evaluate(write=False)` только читают;
  • гейт `ui_gate.py design`: exit 0 ТОЛЬКО при ready + signed.

Данные. «Modal» — настоящий образец Pixso (корень-фрейм и три инстанса); остальное — СИНТЕТИКА из маленьких построителей ниже: экраны на
несколько секций, общие local-компоненты, контейнеры с абсолютными детьми. Что НЕ проверено данными: боевых экранов и планов нет —
формулировки `ask` и то, как их читает слабая модель; правило общих local-компонентов и их имён; состав дайджеста на большом экране
(боевой прогон — X.4).
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

import _testkit as K  # noqa: E402
import forge_events as FE  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import config as CFG  # noqa: E402
from daiver import ds_requirements as DR  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import outline as OL  # noqa: E402
from daiver import plan as PL  # noqa: E402
from daiver import plan_contract as PC  # noqa: E402
from daiver import plan_digest as PD  # noqa: E402
from daiver import status as ST  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
RECORD_APPROVAL = HERE.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
UI_GATE = HERE / "ui_gate.py"
TRACK = "STOR-100"
URL = "https://pixso.example/a?item-id=2:153702"
SHA = "a" * 64
TRACK_DIR = f".daiver/tracks/{TRACK}"
LOCK_REL = f"{TRACK_DIR}/plan.lock.json"
UI_PLAN_REL = f"{TRACK_DIR}/ui-plan.md"
PAGE = "http://localhost:3000/orders"


# ── построители ───────────────────────────────────────────────────────────────

def leaf(i, kind="text", **kw):
    n = {"id": i, "kind": kind, "name": i}
    n.update(kw)
    return n


def frame(i, kids=(), **kw):
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def inst(i, figma, variants=None, slots=None, origin="library", **kw):
    """Инстанс как у адаптера Pixso: слоты — в `component.content`, детей нет."""
    comp: Dict[str, Any] = {"figmaName": figma, "origin": origin, "childrenRole": "content"}
    if variants:
        comp["variants"] = dict(variants)
    if slots:
        comp["content"] = [{"id": f"{i};{n}", "name": n, "text": t, "overridden": True,
                            "font": {"family": "Fixture Sans", "size": 14, "weight": 400}} for n, t in slots]
    n = {"id": i, "kind": "instance", "name": kw.pop("name", i), "component": comp}
    n.update(kw)
    return n


def autoname(i: str) -> str:
    """Автогенерированное имя слоя («Frame 11249»): такой фрейм — простой контейнер, а не кандидат резолва."""
    return "Frame " + "".join(str(ord(c)) for c in i)


def panel(i, kids=()):
    """Панель-секция: фрейм с объявленной раскладкой и автоименем (кандидатом резолва не бывает)."""
    return frame(i, kids, name=autoname(i), layout={"direction": "column", "gap": 8}, size={"w": "fill", "h": "hug"})


def screen():
    """Базовый экран: оболочка «Page» (раскладка объявлена), шапка и действие библиотеки, текст. Кандидатов три."""
    return frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"}, [("Header", "x")]),
                          inst("a", "Modal Action", {"Variant": "Default"}, [("Label", "Ok"), ("Label", "No")]),
                          leaf("t")], layout={"direction": "column", "gap": 8})


def art_screen():
    """Базовый экран + векторная иллюстрация `hero` (узел с `art`: это файл .svg, а не компонент)."""
    root = screen()
    root["children"].append(frame("hero", [], name="hero", art={"slug": "hero", "nodes": 5},
                                  box={"x": 0, "y": 0, "w": 120, "h": 80}))
    return root


def abox(i, x, y, w=40, h=20):
    return leaf(i, "rect", box={"x": x, "y": y, "w": w, "h": h})


def bar(i, kids):
    """Контейнер БЕЗ раскладки с абсолютными детьми (автоимя — не кандидат резолва): запись unproven `absolute-children`."""
    return frame(i, kids, name=autoname(i))


def abs_screen():
    """Экран с тремя записями unproven: ряд из трёх блоков с зазором 10, колонка из двух, фрейм с подсказкой layoutHints."""
    return frame("Page", [bar("bar", [abox("b1", 0, 0), abox("b2", 50, 0), abox("b3", 100, 0)]),
                          bar("col", [abox("c1", 0, 0, 20, 30), abox("c2", 0, 40, 20, 30)]),
                          inst("h1", "Modal Header", {"Steps": "OFF"}, [("Header", "x")]),
                          frame("hinted", [leaf("t")], name=autoname("hinted"), layoutHints={"w": "fill", "main": "space-between"})],
                 layout={"direction": "column", "gap": 8})


GOOD_LAYOUT = [
    {"id": "bar", "reason": "absolute-children", "decision": "group", "evidence": "goal-image", "direction": "row",
     "groups": [["b1", "b2", "b3"]]},
    {"id": "col", "reason": "absolute-children", "decision": "keep-px", "evidence": "none"},
    {"id": "hinted", "reason": "layout-hints", "decision": "apply", "evidence": "declaration"},
]


def tall_screen():
    """Пять панелей-секций: [текст] [Info Card] [Page Header] [Info Card] [Page Header] — для порядка сборки (создатели → потребители)."""
    return frame("Page", [
        panel("p1", [leaf("t1")]),
        panel("p2", [inst("c1", "Info Card", {"Kind": "A"}, [("Title", "x")])]),
        panel("p3", [inst("h1", "Page Header", {"Size": "L"}, [("Title", "y")])]),
        panel("p4", [inst("c2", "Info Card", {"Kind": "A"}, [("Title", "z")])]),
        panel("p5", [inst("h2", "Page Header", {"Size": "L"}, [("Title", "w")])]),
    ], layout={"direction": "column", "gap": 8})


def tall_budget(root_node: dict) -> int:
    """Бюджет, при котором панели — секции, а корень — оболочка над ними."""
    m = OL._Map(root_node)
    return max(m.W[k["id"]] for k in root_node["children"])


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


def build_repo(root: Path, root_node, frames: Optional[Dict[str, dict]] = None, roles: Optional[Dict[str, str]] = None,
               frame_url: str = URL):
    """Трек с кадром main (канон = root_node) и, если нужно, ещё кадрами {имя: корень}. Файл требования лежит на диске, его
    sha256 записан в манифест — как после настоящего pull."""
    m = TR.new_manifest(TRACK, created="2026-10-15T12:00:00Z")
    for name in ["main"] + list(frames or {}):
        TR.upsert_frame(m, name, frameUrl=frame_url, requirement={
            "kind": "pixso", "path": "visual-requirements.pixso.json", "sha256": SHA, "pulledAt": "2026-10-15T12:00:00Z"})
    for name, role in (roles or {}).items():
        TR.get_frame(m, name)["role"] = role
    TR.save(root, m)
    for name, node in [("main", root_node)] + list((frames or {}).items()):
        paths = TR.frame_paths(root, m, name)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        paths["requirement"].write_text(json.dumps({"dsl": name}), encoding="utf-8")
        TR.get_frame(m, name)["requirement"]["sha256"] = TR.sha256_file(paths["requirement"])
        env = C.new_envelope(node, "pixso", {"sourceSha256": TR.get_frame(m, name)["requirement"]["sha256"]})
        C.write_canonical(paths["canonical"], env)
    TR.save(root, m)
    return m


def verdict(node_id, decision="local", confidence="high", rationale="потому что так видно по составу", **kw):
    v = {"nodeId": node_id, "decision": decision, "confidence": confidence, "rationale": rationale}
    v.update(kw)
    return v


def comp_verdict(node_id, component, confidence="high", **kw):
    return verdict(node_id, "component", confidence, component=component, **kw)


def snapshot(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


UI_SPEC = """# STOR-100 — заказы

**Jira:** STOR-100
**Статус:** approved
**Бэк-часть:** нет

## Назначение и границы
Экран заказов.

## Кадры
| Имя | Роль | Макет | Маршрут | Как открыть |
|---|---|---|---|---|
| main | screen | https://pixso.example/a?item-id=2:153702 | /orders | — |

## Данные
types: `src/shared/api/orders.ts`

## События
- onSubmit: отправка формы

## Сценарии
Scenario: Открыть экран заказов
  Given пользователь авторизован
  When он открывает /orders
  Then он видит список заказов
"""


class Base(unittest.TestCase):
    """Репо с треком: ДС «ModalHeader/ModalAction», конфиг, браузер найден (фейковый файл), ui-spec.md стори на месте."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        chrome = Path(self.tmp.name) / "chrome"
        chrome.write_text("#!/bin/sh\n", encoding="utf-8")
        chrome.chmod(0o755)                              # поиск браузера (4.1) требует исполняемый файл, а не просто файл
        patcher = mock.patch.dict(os.environ, {PC.BROWSER_ENV: str(chrome)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.chrome = chrome

    # — мир —
    def repo(self, root_node=None, frames=None, roles=None, ds=("ModalHeader", "ModalAction"), spec=True):
        init_repo(self.root)
        write_ds(self.root, list(ds))
        m = build_repo(self.root, root_node if root_node is not None else screen(), frames, roles)
        if spec:
            self.write_spec(UI_SPEC)
        return m

    def write_spec(self, text: str) -> Path:
        path = T._story_spec_path(self.root, TRACK)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def call(self, name, **args):
        return T.call(name, self.root, args)

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            T.call(name, self.root, args)
        return str(cm.exception)

    def fin(self, v, **kw):
        return self.call("daiver_resolve_finalize", track=TRACK, verdict=v, **kw)

    def resolve_screen(self):
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.fin(comp_verdict("a", "ModalAction"))
        self.fin(verdict("Page", "local", rationale="оболочка экрана"))

    def report(self):
        return self.call("daiver_resolve_report", track=TRACK)

    def write_plan(self, plan=None, frame=None, **kw):
        args: Dict[str, Any] = {"track": TRACK}
        if plan is not None:
            args["plan"] = plan
        if frame is not None:
            args["frame"] = frame
        args.update(kw)
        return self.call("daiver_plan_write", **args)

    def set_url(self, url=PAGE, frame="main"):
        return self.call("daiver_track_manifest", track=TRACK, frame=frame, set={"url": url})

    def contract(self):
        return self.call("daiver_plan_contract", track=TRACK)

    def ready_world(self, root_node=None, **kw):
        """Резолв закончен и подтверждён, план записан, адрес страницы назван: ready без подписи."""
        self.repo(root_node, **kw)
        self.resolve_screen()
        self.report()
        self.write_plan()
        self.set_url()
        out = self.contract()
        self.assertTrue(out["ready"], out)
        return out

    def sign(self, quote: Optional[str] = "да, подписываю план", bind: Optional[str] = LOCK_REL, key: Optional[str] = None
             ) -> "subprocess.CompletedProcess[str]":
        cmd = [sys.executable, "-X", "utf8", str(RECORD_APPROVAL), "--project", str(self.root),
               "--key", key or f"ui-plan-{TRACK}", "--approved-by", "user", "--reason", "план секций подписан"]
        if quote is not None:
            cmd += ["--evidence", quote]
        if bind:
            cmd += ["--bind-file", bind]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)

    def signed_world(self, root_node=None, **kw):
        self.ready_world(root_node, **kw)
        self.assertEqual(self.sign().returncode, 0)
        out = self.contract()
        self.assertTrue(out["signed"], out)
        return out

    def gate(self, step="design", feature=TRACK) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([sys.executable, "-X", "utf8", str(UI_GATE), step, "--project", str(self.root),
                               "--feature", feature], capture_output=True, text=True, encoding="utf-8", timeout=60,
                              env=dict(os.environ))

    def evaluate(self, write=False) -> "PC.Contract":
        return PC.evaluate(self.root, TRACK, T._story_specs(self.root), write=write)

    # — разбор ответа контракта —
    @staticmethod
    def find(out, cid, frame=None):
        return [i for i in out["open"] if i["id"] == cid and (frame is None or i.get("frame") == frame)]

    def one(self, out, cid, frame=None):
        got = self.find(out, cid, frame)
        self.assertEqual(len(got), 1, f"{cid}: {got}\n{json.dumps(out['open'], ensure_ascii=False, indent=1)}")
        return got[0]

    @staticmethod
    def check_line(out, cid):
        return next(c for c in out["checks"] if c.startswith(cid + ":"))

    def paths(self, name="main"):
        return TR.frame_paths(self.root, TR.load(self.root, TRACK), name)

    def edit_canon(self, mutate, name="main"):
        path = self.paths(name)["canonical"]
        env = C.read_canonical(path)
        mutate(env)
        C.write_canonical(path, env)


# ── путь от резолва до подписи ───────────────────────────────────────────────

class Flow(Base):
    def test_sixteen_checks_in_the_order_of_the_spec(self):
        self.assertEqual(PC.CHECK_IDS, ("initialized", "library-source", "browser", "requirement", "canonical", "plan", "resolutions",
                                        "assets", "goal-image", "specs", "state", "baseline", "sources", "implementation",
                                        "dev-command", "signature"))
        self.repo()
        out = self.contract()
        self.assertEqual([c.split(":")[0] for c in out["checks"]], list(PC.CHECK_IDS))
        for i in out["open"]:
            self.assertIn(i["state"], PC.STATES)
            self.assertIn(i["owner"], PC.OWNERS)
            self.assertIsInstance(i["blocking"], bool)
            self.assertTrue(i["detail"])

    def test_the_whole_road_resolve_plan_questions_signature_gate(self):
        self.repo()
        self.resolve_screen()
        self.report()
        out = self.contract()
        self.assertFalse(out["ready"])
        self.assertEqual(self.one(out, "plan")["state"], "open")
        self.assertIn("plan-incomplete", out["blocked"])
        self.assertNotIn("toSign", out)
        self.write_plan()
        out = self.contract()
        self.assertEqual(out["blocks"], ["implementation"], "план есть, нет адреса страницы: это единственное блокирующее")
        ask = self.one(out, "implementation")
        self.assertEqual((ask["owner"], ask["blockedReason"]), ("human", "no-implementation"))
        self.assertIn("/orders", ask["ask"], "маршрут из таблицы «Кадры» ui-spec.md подсказан в вопросе")
        self.assertIn("daiver_track_manifest", ask["record"])
        self.assertNotIn("written", out, "пока не готов — ui-plan.md и lock не пишутся")
        self.assertFalse((self.root / UI_PLAN_REL).exists())
        self.set_url()
        out = self.contract()
        self.assertEqual((out["ready"], out["complete"], out["signed"]), (True, False, False))
        self.assertEqual(out["blocked"], "plan-unsigned")
        self.assertEqual(sorted(out["written"]), [LOCK_REL, UI_PLAN_REL])
        self.assertTrue(out["toSign"])
        self.assertIn(UI_PLAN_REL, out["toSign"][0])
        self.assertEqual(self.gate().returncode, 1)
        r = self.sign()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.contract()
        self.assertEqual((out["ready"], out["signed"]), (True, True))
        self.assertNotIn("blocked", out)
        self.assertNotIn("written", out, "ничего не изменилось — файлы не переписываются")
        g = self.gate()
        self.assertEqual(g.returncode, 0, g.stderr)
        self.assertIn("план подписан", g.stdout)

    def test_complete_means_not_even_a_degradation_left(self):
        self.ready_world()
        out = self.contract()
        self.assertFalse(out["complete"])
        soft = {i["id"] for i in out["open"] if not i["blocking"]}
        self.assertEqual(soft, {"goal-image", "dev-command"})
        # закрываем все деградации: картинка макета и команда запуска
        png = self.root / "goal.png"
        png.write_bytes(K.png_bytes(8, 8))
        self.call("daiver_track_manifest", track=TRACK, frame="main", rasterPath="goal.png")
        self.call("daiver_track_manifest", track=TRACK, set={"implementation": {"url": PAGE, "devCommand": "npm run dev"}})
        out = self.contract()
        self.assertTrue(out["complete"], out["open"])
        self.assertEqual([i["id"] for i in out["open"]], ["signature"])

    def test_the_contract_is_ready_without_the_signature_and_the_gate_still_demands_it(self):
        self.ready_world()
        c = self.evaluate()
        self.assertTrue(c.ready)
        self.assertFalse(c.signed)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("не подписан", r.stderr)
        self.assertIn("record_approval.py", r.stderr)
        self.assertIn(PC.LOCK_NAME, r.stderr)


# ── 16 проверок: каждая нарушается по отдельности и называется ──────────────

class Matrix(Base):
    # initialized
    def test_initialized_open_without_a_config(self):
        self.repo()
        (self.root / ".daiver" / "config.yaml").unlink()
        out = self.contract()
        it = self.one(out, "initialized")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]), ("open", True, "plan", "not-initialized"))
        self.assertIn("buildCommand", it["ask"])
        self.assertIn("daiver_init", it["record"])
        self.assertIn("initialized", out["blocks"])
        self.assertFalse(out["ready"])
        self.assertTrue(self.check_line(out, "initialized").endswith("open"))

    def test_initialized_ok_names_nothing(self):
        self.repo()
        self.assertEqual(self.check_line(self.contract(), "initialized"), "initialized: ok")

    # library-source
    def test_library_source_open_when_the_requirements_of_the_library_were_never_built(self):
        self.repo()
        (self.root / DR.CONTRACT_REL).unlink()
        it = self.one(self.contract(), "library-source")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]),
                         ("open", True, "human", "library-source-unreachable"))
        self.assertIn("scanPaths", it["ask"])
        self.assertIn("daiver_ds_requirements", it["record"])

    def test_library_source_stale_when_the_requirements_belong_to_another_library(self):
        self.repo()
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('- "lib"', '- "other-lib"'), encoding="utf-8")
        it = self.one(self.contract(), "library-source")
        self.assertEqual((it["state"], it["blockedReason"]), ("stale", "library-source-unreachable"))
        self.assertIn("scanPaths", it["detail"])

    def test_library_source_ok_for_storybook_mcp_without_any_document(self):
        init_repo(self.root, source="storybook-mcp")
        build_repo(self.root, screen())
        out = self.contract()
        self.assertEqual(self.check_line(out, "library-source"), "library-source: ok")
        self.assertFalse((self.root / DR.CONTRACT_REL).exists(), "при storybook-mcp производная копия не строится никогда")

    # browser
    def test_browser_open_when_the_variable_points_to_nothing_and_does_not_fall_back_silently(self):
        self.repo()
        with mock.patch.dict(os.environ, {PC.BROWSER_ENV: str(self.root / "no-such-chrome")}):
            it = self.one(self.contract(), "browser")
            self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]),
                             ("open", True, "human", "no-browser"))
            self.assertIn(PC.BROWSER_ENV, it["ask"])
            path, why = PC.find_browser()
            self.assertIsNone(path)
            self.assertIn("файла нет", why)

    def test_browser_found_through_the_variable_before_any_standard_place(self):
        self.repo()
        path, how = PC.find_browser()
        self.assertEqual(path, str(self.chrome))
        self.assertIn(PC.BROWSER_ENV, how)
        self.assertEqual(self.check_line(self.contract(), "browser"), "browser: ok")

    def test_browser_missing_everywhere_is_a_blocking_item(self):
        self.repo()
        with mock.patch.object(PC, "find_browser", return_value=(None, "нет ни переменной, ни Chrome")):
            out = self.contract()
        self.assertIn("browser", out["blocks"])
        self.assertIn("plan-incomplete", out["blocked"])
        self.assertIn("browser", out["blocked"])

    # requirement
    def test_requirement_stale_after_the_file_was_touched_beside_pull_is_a_track_conflict(self):
        self.repo()
        self.paths()["requirement"].write_text('{"dsl": "changed"}', encoding="utf-8")
        it = self.one(self.contract(), "requirement", "main")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]), ("stale", True, "plan", "track-conflict"))
        self.assertIn("daiver_pixso_pull", it["record"])

    def test_requirement_open_when_the_frame_has_none_or_the_file_is_gone(self):
        self.repo()
        self.paths()["requirement"].unlink()
        it = self.one(self.contract(), "requirement", "main")
        self.assertEqual((it["state"], it["blockedReason"]), ("open", "no-requirement"))
        self.assertIn(".daiver/inbox/main.dsl.json", it["ask"], "просят выгрузку файлом, а не вставку DSL в контекст")
        m = TR.load(self.root, TRACK)
        del TR.get_frame(m, "main")["requirement"]
        TR.save(self.root, m)
        it = self.one(self.contract(), "requirement", "main")
        self.assertIn("нет в манифесте", it["detail"])

    def test_requirement_completeness_against_the_frames_table_matches_by_link_then_by_name(self):
        self.repo()
        self.assertEqual(self.check_line(self.contract(), "requirement"), "requirement: ok, ok")
        # строка таблицы с другим именем, но той же ссылкой макета — тот же кадр
        self.write_spec(UI_SPEC.replace("| main | screen |", "| Экран заказов | screen |"))
        self.assertEqual(self.check_line(self.contract(), "requirement"), "requirement: ok, ok")
        # строка, которой нет в треке ни по ссылке, ни по имени, — блокирует и названа
        self.write_spec(UI_SPEC + "\n")
        extra = UI_SPEC.replace("| main | screen | https://pixso.example/a?item-id=2:153702 | /orders | — |",
                                "| main | screen | https://pixso.example/a?item-id=2:153702 | /orders | — |\n"
                                "| Подробности | dialog | https://pixso.example/a?item-id=9:9 | — | клик |")
        self.write_spec(extra)
        out = self.contract()
        lost = [i for i in self.find(out, "requirement") if "frame" not in i]
        self.assertEqual(len(lost), 1, out["open"])
        self.assertEqual((lost[0]["blocking"], lost[0]["blockedReason"]), (True, "no-requirement"))
        self.assertIn("Подробности", lost[0]["detail"])
        self.assertIn("daiver_pixso_pull", lost[0]["record"])

    # canonical
    def test_canonical_open_when_missing_or_unreadable_and_stale_when_built_from_another_export(self):
        self.repo()
        self.edit_canon(lambda env: env["stats"].update(sourceSha256="b" * 64))
        it = self.one(self.contract(), "canonical", "main")
        self.assertEqual((it["state"], it["blocking"], it["blockedReason"]), ("stale", True, "plan-incomplete"))
        self.assertIn("daiver_normalize", it["record"])
        self.paths()["canonical"].write_text("{не json", encoding="utf-8")
        it = self.one(self.contract(), "canonical", "main")
        self.assertEqual(it["state"], "open")
        self.assertIn("не читается", it["detail"])
        self.paths()["canonical"].unlink()
        it = self.one(self.contract(), "canonical", "main")
        self.assertIn("канона нет", it["detail"])

    # plan
    def test_plan_open_without_a_file_and_an_unreadable_plan_is_not_a_pass(self):
        self.ready_world()
        p = self.paths()["plan"]
        p.write_text("{не json", encoding="utf-8")
        out = self.contract()
        it = self.one(out, "plan", "main")
        self.assertEqual((it["state"], it["blocking"], it["blockedReason"], it["owner"]), ("open", True, "plan-incomplete", "plan"))
        self.assertIn("не читается", it["detail"])
        self.assertIn("daiver_plan_write", it["record"])
        self.assertFalse(out["ready"])
        for body in ("[]", "null", '"строка"', "42", ""):               # парсится или пусто, но это не JSON-объект плана
            p.write_text(body, encoding="utf-8")
            out = self.contract()
            self.assertEqual(self.one(out, "plan", "main")["state"], "open", body)
            self.assertFalse(out["ready"], body)
        p.write_bytes(b"\xff\xfe\x00\x01")
        self.assertIn("не UTF-8", self.one(self.contract(), "plan", "main")["detail"])
        p.unlink()
        it = self.one(self.contract(), "plan", "main")
        self.assertIn("плана кадра нет", it["detail"])

    def test_the_status_ladder_still_reads_the_plan_as_an_object_and_names_a_broken_one(self):
        """Контракт 1.10: лестница читает у plan.json лишь наличие и разбираемость (JSON-объект) — писатель 2.6 его не ломает."""
        self.ready_world()
        st = ST.status(self.root, story_spec=T._story_specs(self.root))
        track = next(t for t in st["tracks"] if t["name"] == TRACK)
        self.assertEqual(track["stage"], "planned")
        data = json.loads(self.paths()["plan"].read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.paths()["plan"].write_text("[1, 2]", encoding="utf-8")
        st = ST.status(self.root, story_spec=T._story_specs(self.root))
        track = next(t for t in st["tracks"] if t["name"] == TRACK)
        self.assertEqual(track["stage"], "normalized", "неразбираемый план — как отсутствующий")
        self.assertTrue(any(w.startswith("artifact-unreadable") and "plan.json" in w for w in st["warnings"]), st["warnings"])

    def test_plan_stale_when_the_json_is_edited_by_hand_or_the_markdown_twin_differs(self):
        self.ready_world()
        p = self.paths()["plan"]
        good = p.read_text(encoding="utf-8")
        p.write_text(good.replace('"component": "ModalHeader"', '"component": "Header"'), encoding="utf-8")
        it = self.one(self.contract(), "plan", "main")
        self.assertEqual((it["state"], it["blocking"]), ("stale", True))
        self.assertIn("вердикты резолва или карта", it["detail"])
        p.write_text(good, encoding="utf-8")
        self.assertEqual(self.check_line(self.contract(), "plan"), "plan: ok")
        md = self.paths()["planMd"]
        md.write_text(md.read_text(encoding="utf-8") + "\nприписка руками\n", encoding="utf-8")
        it = self.one(self.contract(), "plan", "main")
        self.assertEqual(it["state"], "stale")
        self.assertIn("plan.md", it["detail"])

    def test_plan_stale_after_a_verdict_the_canon_the_requirement_or_the_specs_change(self):
        self.ready_world()
        self.fin(comp_verdict("a", "ModalAction", "medium", rationale="пересмотрено"))
        self.assertIn("вердикты резолва или карта", self.one(self.contract(), "plan", "main")["detail"])
        self.fin(comp_verdict("a", "ModalAction", "high", rationale="как было"))
        self.report()
        self.assertEqual(self.check_line(self.contract(), "plan"), "plan: ok", "тот же вердикт — тот же план")
        self.write_spec(UI_SPEC + "\n## Валидации\n- сумма больше нуля\n")
        self.assertIn("спеки стори изменились", self.one(self.contract(), "plan", "main")["detail"])
        self.write_spec(UI_SPEC)
        self.assertEqual(self.check_line(self.contract(), "plan"), "plan: ok")
        self.edit_canon(lambda env: env["root"]["children"].append(leaf("t2")))
        d = self.one(self.contract(), "plan", "main")["detail"]
        self.assertIn("секции макета изменились", d)
        self.assertIn("rootHash", d)
        m = TR.load(self.root, TRACK)
        TR.get_frame(m, "main")["requirement"]["sha256"] = "c" * 64
        TR.save(self.root, m)
        self.assertIn("требование кадра перетянуто", self.one(self.contract(), "plan", "main")["detail"])

    def test_plan_unknown_when_the_data_it_is_checked_against_cannot_be_read_and_it_still_blocks(self):
        self.ready_world()
        (self.root / ".daiver" / "component-map.yaml").write_text("rules: [oops\n", encoding="utf-8")
        out = self.contract()
        it = self.one(out, "plan", "main")
        self.assertEqual((it["state"], it["blocking"]), ("unknown", True))
        self.assertIn("карт", it["detail"])
        self.assertFalse(out["ready"], "блокирующий пункт в состоянии unknown блокирует так же, как open")
        self.assertTrue(self.check_line(out, "plan").endswith("unknown"))

    # resolutions
    def test_resolutions_open_per_frame_for_unresolved_candidates(self):
        self.repo(screen(), {"detail": frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"})])})
        self.resolve_screen()
        self.fin(verdict("Detail", "local"), frame="detail")
        out = self.contract()
        self.assertEqual(self.check_line(out, "resolutions"), "resolutions: main ok, detail open")
        self.assertEqual(self.one(out, "resolutions", "detail")["detail"].split(" — ")[0], "кадр «detail»: не решено кандидатов 1")
        self.report()
        out = self.contract()
        self.assertEqual(self.check_line(out, "resolutions"), "resolutions: main ok, detail ok")
        self.fin(comp_verdict("a", "ModalAction", "medium", rationale="пересмотрено"))
        out = self.contract()
        stale = [i for i in self.find(out, "resolutions") if "устарел" in i["detail"]]
        self.assertTrue(stale, out["open"])
        self.assertEqual((stale[0]["blocking"], stale[0]["blockedReason"], stale[0]["owner"]), (True, "plan-incomplete", "plan"))
        self.assertIn("daiver_resolve_report", stale[0]["record"])

    def test_resolutions_open_until_the_human_confirmed_the_low_verdicts(self):
        self.repo()
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.fin(comp_verdict("a", "ModalAction", "low", rationale="догадка по слотам"))
        self.fin(verdict("Page", "local", rationale="оболочка экрана"))
        self.report()
        out = self.contract()
        it = self.one(out, "resolutions")
        self.assertIn("не подтверждены", it["detail"])
        self.assertIn("ui-resolve-STOR-100", it["detail"])
        cmd = [sys.executable, "-X", "utf8", str(RECORD_APPROVAL), "--project", str(self.root), "--key", "ui-resolve-STOR-100",
               "--approved-by", "user", "--reason", "вердикты подтверждены", "--evidence", "да, все вердикты верны",
               "--bind-file", f"{TRACK_DIR}/resolution-report.md"]
        self.assertEqual(subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").returncode, 0)
        self.assertEqual(self.check_line(self.contract(), "resolutions"), "resolutions: ok")

    # assets
    def test_assets_open_without_the_svg_of_an_illustration_and_it_only_degrades(self):
        self.repo(art_screen())
        out = self.contract()
        it = self.one(out, "assets", "main")
        self.assertEqual((it["state"], it["blocking"], it["owner"]), ("open", False, "human"))
        self.assertIn("hero", it["detail"])
        self.assertIn("hero", it["ask"])
        self.assertIn("daiver_svg_assets", it["record"])
        self.assertIn("placeholder", it["degradesTo"])
        self.assertNotIn("assets", out["blocks"])
        assets = self.paths()["assets"]
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "hero.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg' width='120' height='80'/>", encoding="utf-8")
        self.assertEqual(self.check_line(self.contract(), "assets"), "assets: ok")

    def test_assets_not_applicable_without_illustrations_and_unknown_when_the_canon_is_unreadable(self):
        self.repo()
        self.assertEqual(self.check_line(self.contract(), "assets"), "assets: н/п")
        self.paths()["canonical"].write_text("{не json", encoding="utf-8")
        it = self.one(self.contract(), "assets", "main")
        self.assertEqual((it["state"], it["blocking"]), ("unknown", False))

    # goal-image
    def test_goal_image_open_without_a_picture_degrades_to_the_reference_render(self):
        self.repo()
        it = self.one(self.contract(), "goal-image", "main")
        self.assertEqual((it["state"], it["blocking"], it["owner"]), ("open", False, "human"))
        self.assertIn("rasterPath", it["record"])
        self.assertIn("эталонному рендеру", it["degradesTo"])
        png = self.root / "goal.png"
        png.write_bytes(K.png_bytes(8, 8))
        self.call("daiver_track_manifest", track=TRACK, frame="main", rasterPath="goal.png")
        self.assertEqual(self.check_line(self.contract(), "goal-image"), "goal-image: ok")
        self.paths()["raster"].write_bytes(b"not an image")
        self.assertIn("PNG/JPEG", self.one(self.contract(), "goal-image", "main")["detail"])

    # specs
    def test_specs_degrade_to_a_presentational_module_when_there_is_no_logic_or_no_file(self):
        self.repo(spec=False)
        it = self.one(self.contract(), "specs")
        self.assertEqual((it["state"], it["blocking"], it["owner"]), ("open", False, "host"))
        self.assertIn("файлов нет", it["detail"])
        self.assertIn("презентационным", it["degradesTo"])
        self.write_spec("# STOR-100\n\n## Кадры\n| Имя | Роль | Макет |\n|---|---|---|\n| main | screen | https://pixso.example/a?item-id=2:153702 |\n")
        it = self.one(self.contract(), "specs")
        self.assertIn("нет разделов", it["detail"])
        self.assertIn("презентационный", it["ask"])
        self.write_spec(UI_SPEC)
        self.assertEqual(self.check_line(self.contract(), "specs"), "specs: ok")

    def test_specs_open_for_a_registered_file_that_is_gone(self):
        self.repo()
        self.call("daiver_track_manifest", track=TRACK, set={"specs": ["docs/specs/orders.spec.md"]})
        it = self.one(self.contract(), "specs")
        self.assertIn("orders.spec.md", it["detail"])
        self.assertIn("daiver_track_manifest", it["record"])

    # state
    def test_state_frame_without_its_screen_degrades_to_a_second_screen(self):
        self.repo(screen(), {"err": frame("Err", [inst("e1", "Modal Header", {"Steps": "OFF"})])}, roles={"err": "state"})
        it = self.one(self.contract(), "state", "err")
        self.assertEqual((it["state"], it["blocking"], it["owner"]), ("open", False, "human"))
        self.assertIn("второй экран", it["degradesTo"])
        self.assertIn("stateOf", it["record"])
        self.assertEqual(self.check_line(self.contract(), "state"), "state: main н/п, err open")
        self.call("daiver_track_manifest", track=TRACK, frame="err", set={"stateOf": "main"})
        self.assertEqual(self.check_line(self.contract(), "state"), "state: main н/п, err ok")

    # baseline и sources — кадр, который правит существующую страницу
    def test_sources_and_baseline_are_not_applicable_to_a_frame_built_from_scratch(self):
        self.repo()
        out = self.contract()
        self.assertEqual(self.check_line(out, "sources"), "sources: н/п")
        self.assertEqual(self.check_line(out, "baseline"), "baseline: н/п")

    def test_sources_need_existing_files_and_a_scope(self):
        self.repo()
        self.call("daiver_track_manifest", track=TRACK, frame="main", set={"sources": ["src/pages/Orders.tsx"], "scope": "change"})
        it = self.one(self.contract(), "sources", "main")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]), ("open", True, "plan", "no-sources"))
        self.assertIn("src/pages/Orders.tsx", it["detail"])
        self.assertIn("не угаданные", it["ask"], "файлы страницы подтверждаются чтением, не угадываются")
        page = self.root / "src" / "pages" / "Orders.tsx"
        page.parent.mkdir(parents=True)
        page.write_text("export default function Orders() { return null }\n", encoding="utf-8")
        self.assertEqual(self.check_line(self.contract(), "sources"), "sources: ok")
        self.call("daiver_track_manifest", track=TRACK, frame="main", set={"scope": None})
        it = self.one(self.contract(), "sources", "main")
        self.assertIn("не задан scope", it["detail"])
        self.assertIn("change", it["ask"])

    def test_baseline_of_a_page_base_frame_blocks_until_the_capture_is_in_the_baseline_folder(self):
        self.repo()
        self.call("daiver_track_manifest", track=TRACK, frame="main", set={"sources": ["src/pages/Orders.tsx"], "scope": "page"})
        it = self.one(self.contract(), "baseline", "main")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]), ("open", True, "human", "plan-incomplete"))
        self.assertIn("ДО правок", it["ask"])
        self.assertIn("iterationDir", it["record"])
        base = self.paths()["baseline"]
        base.mkdir(parents=True)
        self.assertEqual(self.one(self.contract(), "baseline", "main")["state"], "open", "пустая папка — не база")
        (base / "actual.png").write_bytes(K.png_bytes(4, 4))
        self.assertEqual(self.check_line(self.contract(), "baseline"), "baseline: ok")

    # implementation
    def test_implementation_url_blocks_and_a_dialog_does_not_inherit_the_page_address(self):
        self.repo(screen(), {"dlg": frame("Dlg", [leaf("x")])}, roles={"dlg": "dialog"})
        self.call("daiver_track_manifest", track=TRACK, set={"implementation": {"url": PAGE}})
        out = self.contract()
        self.assertEqual(self.check_line(out, "implementation"), "implementation: main ok, dlg open, dlg open")
        url = [i for i in self.find(out, "implementation", "dlg") if i["blocking"]]
        recipe = [i for i in self.find(out, "implementation", "dlg") if not i["blocking"]]
        self.assertEqual((len(url), len(recipe)), (1, 1))
        self.assertEqual((url[0]["blockedReason"], url[0]["owner"]), ("no-implementation", "human"))
        self.assertIn("диалог адрес не наследует", url[0]["detail"])
        self.assertEqual((recipe[0]["blockedReason"], recipe[0]["owner"]), ("no-open-recipe", "human"))
        self.assertIn("no-open-recipe", recipe[0]["degradesTo"])
        self.assertIn("{open}", recipe[0]["record"])
        self.call("daiver_track_manifest", track=TRACK, frame="dlg", set={
            "url": PAGE + "/details", "open": {"actions": [{"verb": "click", "selector": "#open"}], "clipSelector": ".modal"}})
        self.assertEqual(self.check_line(self.contract(), "implementation"), "implementation: main ok, dlg ok")

    def test_a_state_frame_takes_the_address_of_its_screen_but_still_wants_an_open_recipe(self):
        self.repo(screen(), {"err": frame("Err", [leaf("x")])}, roles={"err": "state"})
        self.call("daiver_track_manifest", track=TRACK, frame="err", set={"stateOf": "main"})
        self.call("daiver_track_manifest", track=TRACK, set={"implementation": {"url": PAGE}})
        out = self.contract()
        self.assertEqual(self.check_line(out, "implementation"), "implementation: main ok, err ok, err open")
        self.assertEqual([i["blocking"] for i in self.find(out, "implementation", "err")], [False])

    # dev-command
    def test_dev_command_only_degrades_and_the_hint_warns_that_set_replaces_the_whole_object(self):
        self.ready_world()
        out = self.contract()
        it = self.one(out, "dev-command")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]), ("open", False, "human", "no-dev-command"))
        self.assertIn("целиком", it["record"])
        self.assertIn("no-dev-command", it["degradesTo"])
        self.assertNotIn("dev-command", out["blocks"])
        self.call("daiver_track_manifest", track=TRACK, set={"implementation": {"url": PAGE, "devCommand": "npm run dev"}})
        self.assertEqual(self.check_line(self.contract(), "dev-command"), "dev-command: ok")

    # упавшая проверка не «проходит»
    def test_a_check_that_crashes_is_a_blocking_item_and_not_a_pass(self):
        self.ready_world()
        boom = lambda w: 1 / 0                                                      # noqa: E731
        checks = tuple((cid, boom if cid == "specs" else fn) for cid, fn in PC.CHECKS)
        with mock.patch.object(PC, "CHECKS", checks):
            out = self.contract()
        it = self.one(out, "specs")
        self.assertEqual((it["state"], it["blocking"]), ("open", True), "даже у неблокирующей проверки сбой — блокирующий пункт")
        self.assertIn("ZeroDivisionError", it["detail"])
        self.assertFalse(out["ready"])
        self.assertEqual(self.gate().returncode, 1)

    def test_a_digest_that_cannot_be_built_blocks_the_plan_and_writes_nothing(self):
        self.repo()
        self.resolve_screen()
        self.report()
        self.write_plan()
        self.set_url()
        with mock.patch.object(PD, "render", side_effect=RuntimeError("сломался")):
            out = self.contract()
        it = [i for i in self.find(out, "plan") if "ui-plan.md" in i["detail"]]
        self.assertEqual(len(it), 1, out["open"])
        self.assertIn("RuntimeError", it[0]["detail"])
        self.assertFalse(out["ready"])
        self.assertFalse((self.root / UI_PLAN_REL).exists())
        self.assertFalse((self.root / LOCK_REL).exists())

    def test_every_open_item_carries_what_the_weak_reader_needs(self):
        """Человеку — вопрос (ask), модели — чем записать ответ (record), а цена неответа (degradesTo) у неблокирующих."""
        self.repo(art_screen(), spec=False)
        out = self.contract()
        for i in out["open"]:
            self.assertTrue(i["detail"], i)
            if i["owner"] == "human" and i["id"] != "signature" and i["state"] != "unknown":
                self.assertTrue(i.get("ask"), f"человек-владелец без вопроса: {i}")
            if not i["blocking"]:
                self.assertTrue(i.get("degradesTo"), f"неблокирующий пункт без цены: {i}")
            if i["blocking"] and i["id"] != "signature":
                self.assertTrue(i.get("blockedReason"), f"блокирующий пункт без причины останова: {i}")
        self.assertTrue(out["nextStep"])


# ── подпись: согласие человека, привязанное к lock ──────────────────────────

class Signature(Base):
    def test_there_is_no_tool_that_signs_and_the_consent_needs_the_quote_of_the_user(self):
        self.assertNotIn("daiver_plan_sign", T.TOOLS, "подпись плана — решение человека, не инструмент модели")
        self.assertEqual(PC.consent_key("STOR-100"), "ui-plan-STOR-100", "ключ подписи — класс + ключ стори, как в risk-policy")
        self.assertIn(PC.CONSENT_PREFIX, FE.CONSENT_PREFIXES)
        self.assertTrue(FE.consent_required(PC.consent_key(TRACK)))
        self.ready_world()
        self.assertEqual(self.sign(quote=None).returncode, 2, "без цитаты пользователя согласие не пишется")
        self.assertEqual(self.sign(quote="верни как было, я передумал").returncode, 2, "цитата без слова согласия — не согласие")
        self.assertFalse(self.contract()["signed"])

    def test_the_consent_is_bound_to_the_lock_the_engine_built_and_the_lock_names_what_was_read(self):
        self.signed_world()
        rec = FE.approval(self.root, PC.consent_key(TRACK))
        lock_bytes = (self.root / LOCK_REL).read_bytes()
        self.assertEqual(rec["bound_sha256"], PL.sha256_bytes(lock_bytes))
        self.assertEqual(rec["bound_file"], LOCK_REL)
        lock = json.loads(lock_bytes.decode("utf-8"))
        m = TR.load(self.root, TRACK)
        self.assertEqual(sorted(lock), ["frames", "resolutionReportSha256", "schema", "track", "uiPlanSha256"])
        self.assertEqual(lock["frames"], [{"name": "main", "requirementSha256": TR.get_frame(m, "main")["requirement"]["sha256"],
                                           "planSha256": PL.sha256_bytes(self.paths()["plan"].read_bytes())}])
        self.assertEqual(lock["uiPlanSha256"], PL.sha256_bytes((self.root / UI_PLAN_REL).read_bytes()))
        self.assertEqual(lock["resolutionReportSha256"], PL.sha256_bytes((self.root / TRACK_DIR / "resolution-report.md").read_bytes()))

    def test_a_consent_written_without_bind_file_or_bound_to_another_file_does_not_sign_and_is_revoked_by_the_writer(self):
        self.ready_world()
        self.assertEqual(self.sign(bind=None).returncode, 0)
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertEqual(c.signature["state"], "stale")
        self.assertIn("без --bind-file", c.signature["detail"])
        self.assertIsNotNone(FE.approval(self.root, PC.consent_key(TRACK)), "читатель согласие не трогает")
        out = self.contract()
        self.assertIn("signatureRevoked", out)
        self.assertIsNone(FE.approval(self.root, PC.consent_key(TRACK)), "писатель снял согласие: иначе новое не записать")
        self.assertEqual(self.sign(bind=".daiver/config.yaml").returncode, 0)
        c = self.evaluate(write=False)
        self.assertFalse(c.signed, "привязка к другому файлу — не подпись плана даже для читателя")
        self.assertIn("изменились после подписи", c.signature["detail"])
        self.assertEqual(self.gate().returncode, 1)
        out = self.contract()
        self.assertFalse(out["signed"], "привязка к другому файлу — не подпись плана")
        self.assertIn("signatureRevoked", out)
        self.assertEqual(self.sign().returncode, 0)
        self.assertTrue(self.contract()["signed"])

    def test_another_story_and_another_key_do_not_sign_this_plan(self):
        self.ready_world()
        self.assertEqual(self.sign(key="ui-plan-OTHER-7").returncode, 0)
        self.assertEqual(self.sign(key="ui-resolve-STOR-100").returncode, 0)
        self.assertFalse(self.contract()["signed"])

    def test_the_signature_goes_stale_when_the_inputs_change_and_the_writer_revokes_it_so_the_human_can_sign_again(self):
        self.signed_world()
        self.write_spec(UI_SPEC + "\n## Валидации\n- сумма больше нуля\n")             # человек поправил спеку после подписи
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertEqual(c.signature["state"], "stale", "план стал stale, подписывать нечего, а подпись относилась к прежнему")
        self.assertIn("прежнему плану", c.signature["detail"])
        self.assertEqual(self.gate().returncode, 1)
        self.write_plan()                                                           # план пересобран по новой спеке
        out = self.contract()                                                       # ready → новый дайджест/lock → старая подпись слетает
        self.assertTrue(out["ready"])
        self.assertFalse(out["signed"])
        self.assertIn("signatureRevoked", out)
        self.assertEqual(sorted(out["written"]), [LOCK_REL, UI_PLAN_REL])
        self.assertIn("прежняя подпись снята", self.one(out, "signature")["detail"])
        self.assertEqual(self.sign().returncode, 0)
        self.assertTrue(self.contract()["signed"])
        self.assertEqual(self.gate().returncode, 0)

    def test_a_changed_fact_of_the_digest_after_the_signature_is_named_by_the_hash_and_not_only_by_the_files(self):
        """Адрес страницы — факт дайджеста: он сменился → собранный lock другой → подпись относилась к прежнему содержимому."""
        self.signed_world()
        self.set_url(PAGE + "/v2")
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertEqual(c.signature["state"], "stale")
        self.assertIn("изменились после подписи", c.signature["detail"])
        self.assertIn("sha256 lock не совпал", c.signature["detail"])
        self.assertTrue(c.ready, "план остался готов: сменился только факт, который человек подписывал")
        out = self.contract()
        self.assertIn("signatureRevoked", out, "писатель пересобрал дайджест и снял прежнее согласие")
        self.assertEqual(self.sign().returncode, 0)
        self.assertTrue(self.contract()["signed"])

    def test_hand_edited_plan_after_the_signature_breaks_it_though_nobody_regenerated_the_lock(self):
        """Lock — производный файл: подпись сверяется с lock, СОБРАННЫМ из артефактов, а не с тем, что лежит на диске."""
        self.signed_world()
        lock_before = (self.root / LOCK_REL).read_bytes()
        p = self.paths()["plan"]
        p.write_text(p.read_text(encoding="utf-8").replace('"confidence": "high"', '"confidence": "medium"', 1), encoding="utf-8")
        self.assertEqual((self.root / LOCK_REL).read_bytes(), lock_before, "файл lock не тронут — сверка по нему ничего бы не заметила")
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertEqual(self.one({"open": c.open}, "plan", "main")["state"], "stale")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("plan:", r.stderr)

    def test_the_digest_and_the_lock_are_rebuilt_byte_for_byte_and_the_same_signature_returns(self):
        self.signed_world()
        ui_plan, lock = (self.root / UI_PLAN_REL).read_bytes(), (self.root / LOCK_REL).read_bytes()
        (self.root / UI_PLAN_REL).unlink()
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertIn("на диске не соответствуют", c.signature["detail"])
        self.assertEqual(self.gate().returncode, 1)
        out = self.contract()
        self.assertEqual(out["written"], [UI_PLAN_REL])
        self.assertEqual((self.root / UI_PLAN_REL).read_bytes(), ui_plan, "дайджест детерминирован")
        self.assertEqual((self.root / LOCK_REL).read_bytes(), lock)
        self.assertTrue(out["signed"], "содержимое то же — прежняя подпись снова верна")
        (self.root / LOCK_REL).write_text("{}\n", encoding="utf-8")                     # lock испорчен руками
        self.assertFalse(self.evaluate(write=False).signed)
        self.assertTrue(self.contract()["signed"])
        self.assertEqual((self.root / LOCK_REL).read_bytes(), lock)

    def test_when_the_machine_loses_its_browser_the_plan_is_not_ready_but_the_signature_stays_valid(self):
        """Готовность и подпись — разные вещи: пропавший Chrome не делает план «неподписанным» (ui-guard пишет по подписи)."""
        self.signed_world()
        journal = FE.approval(self.root, PC.consent_key(TRACK))
        digest = (self.root / UI_PLAN_REL).read_bytes()
        with mock.patch.dict(os.environ, {PC.BROWSER_ENV: str(self.root / "no-such-chrome")}):
            out = self.contract()
            self.assertFalse(out["ready"])
            self.assertTrue(out["signed"], "дайджест не зависит от блокирующих пунктов окружения")
            self.assertEqual(out["blocked"], "plan-incomplete (browser)")
            self.assertNotIn("signatureRevoked", out)
            self.assertNotIn("written", out, "не готов — файлы не переписываются")
            self.assertTrue(PC.signature_state(self.root, TRACK, T._story_specs(self.root))["state"] == "ok")
            r = self.gate()
            self.assertEqual(r.returncode, 1, "гейт требует и готовность")
            self.assertIn("browser", r.stderr)
        self.assertEqual(FE.approval(self.root, PC.consent_key(TRACK)), journal, "согласие в журнале не тронуто")
        self.assertEqual((self.root / UI_PLAN_REL).read_bytes(), digest)
        self.assertEqual(self.gate().returncode, 0)

    def test_a_transient_change_while_the_plan_is_not_ready_does_not_burn_the_signature(self):
        """Согласие снимается только при пересборке готового плана: правка, откатившаяся до этого, подпись не сжигает."""
        self.signed_world()
        with mock.patch.dict(os.environ, {PC.BROWSER_ENV: str(self.root / "no-such-chrome")}):
            self.set_url(PAGE + "/v2")
            out = self.contract()
            self.assertEqual((out["ready"], out["signed"]), (False, False))
            self.assertNotIn("signatureRevoked", out)
            self.assertIsNotNone(FE.approval(self.root, PC.consent_key(TRACK)), "план не готов — согласие в журнале цело")
            self.set_url(PAGE)                                                  # правка откатилась
        out = self.contract()
        self.assertEqual((out["ready"], out["signed"]), (True, True))

    def test_the_reader_never_writes_the_files_nor_the_journal(self):
        self.ready_world()
        self.sign()
        self.write_spec(UI_SPEC + "\n## Валидации\n- x\n")
        before = snapshot(self.root)
        for _ in range(2):
            self.evaluate(write=False)
            self.gate()
        self.assertEqual(snapshot(self.root), before)

    def test_signature_blocks_always_even_when_the_config_says_confirm_plan_false(self):
        self.ready_world()
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("confirmPlan: true", "confirmPlan: false"), encoding="utf-8")
        out = self.contract()
        it = self.one(out, "signature")
        self.assertTrue(it["blocking"])
        self.assertIn("confirmPlan: false", it["detail"])
        self.assertEqual(self.gate().returncode, 1)

    def test_ask_and_record_of_the_signature_item_tell_who_writes_the_consent(self):
        self.ready_world()
        it = self.one(self.contract(), "signature")
        self.assertEqual((it["state"], it["blocking"], it["owner"], it["blockedReason"]), ("open", True, "human", "plan-unsigned"))
        self.assertIn("Подписываете план", it["ask"])
        self.assertIn(f"--key {PC.consent_key(TRACK)}", it["record"])
        self.assertIn(f"--bind-file {LOCK_REL}", it["record"])
        self.assertIn("оркестратор", it["record"])
        self.assertIn("не инструмент модели", it["record"])
        self.assertIn("sign-plan", self.contract()["nextStep"])


# ── настоящий образец: перетяжка макета слетает подпись ─────────────────────

class ModalWorld(Base):
    """«Modal» из Pixso: pull → normalize → резолв четырёх кандидатов → план → адрес → подпись (мир без тестов, для двух классов)."""

    def modal(self):
        init_repo(self.root)
        write_ds(self.root, ["ModalHeader", "ModalAction"])
        self.write_spec(UI_SPEC)
        inbox = self.root / ".daiver" / "inbox"
        inbox.mkdir(parents=True)
        shutil.copyfile(str(FIXTURE), str(inbox / "main.dsl.json"))
        self.call("daiver_pixso_pull", track=TRACK, dslPath=".daiver/inbox/main.dsl.json", frameUrl=URL, frame="main")
        self.call("daiver_normalize", track=TRACK)
        self.fin(verdict("2:153702", "local", rationale="оболочка экрана"))
        self.fin(comp_verdict("2:153703", "ModalHeader"))
        self.fin(comp_verdict("2:153704", "ModalAction"))
        self.report()
        self.write_plan()
        self.set_url()
        self.assertTrue(self.contract()["ready"])
        self.assertEqual(self.sign().returncode, 0)
        self.assertTrue(self.contract()["signed"])

    def repull(self):
        raw = FIXTURE.read_text(encoding="utf-8")
        marker = "Текст9 слово - 20"
        self.assertIn(marker, raw)
        (self.root / ".daiver" / "inbox" / "main2.dsl.json").write_text(raw.replace(marker, marker + " (правка)", 1), encoding="utf-8")
        return self.call("daiver_pixso_pull", track=TRACK, dslPath=".daiver/inbox/main2.dsl.json", frameUrl=URL, frame="main")



class RealModal(ModalWorld):
    def test_the_plan_of_the_sample_is_one_section_and_its_hash_is_the_root_hash(self):
        self.modal()
        plan = json.loads(self.paths()["plan"].read_text(encoding="utf-8"))
        self.assertEqual(plan["tree"], {"section": "2:153702"}, "образец целиком влезает в бюджет: корень — единственная секция")
        self.assertEqual(plan["buildOrder"], [{"hash": "0ccf768ee9d7", "id": "2:153702", "name": "Modal"}])
        self.assertEqual(plan["pins"]["rootHash"], "0ccf768ee9d7")
        nodes = {n["id"]: n for n in plan["sections"][0]["nodes"]}
        self.assertEqual(set(nodes), {"2:153702", "2:153703", "2:153704", "22:63141"})
        self.assertEqual((nodes["2:153703"]["component"], nodes["22:63141"]["source"]), ("ModalHeader", "reuse"))
        self.assertEqual(plan["stats"]["candidates"], 4)

    def test_repulling_a_changed_export_makes_the_signature_stale_then_replanning_revokes_it_and_a_new_one_is_needed(self):
        self.modal()
        out = self.repull()
        self.assertFalse(out["unchanged"])
        self.assertIn("plan.json", out["stale"], "pull сам называет артефакты, которые устарели")
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertEqual(c.signature["state"], "stale")
        self.assertEqual(self.gate().returncode, 1)
        out = self.contract()
        self.assertFalse(out["ready"], "канон удалён перетяжкой, план устарел")
        self.assertIn("canonical", out["blocks"])
        self.assertIn("plan", out["blocks"])
        self.call("daiver_normalize", track=TRACK)
        out = self.contract()
        plan_item = self.one(out, "plan", "main")
        self.assertEqual(plan_item["state"], "stale")
        self.assertIn("требование кадра перетянуто", plan_item["detail"])
        self.write_plan()
        out = self.contract()
        self.assertTrue(out["ready"])
        self.assertFalse(out["signed"])
        self.assertIn("signatureRevoked", out)
        self.assertEqual(self.sign().returncode, 0)
        self.assertTrue(self.contract()["signed"])
        self.assertEqual(self.gate().returncode, 0)

    def test_repulling_the_same_export_changes_nothing(self):
        self.modal()
        before = snapshot(self.root)
        out = self.call("daiver_pixso_pull", track=TRACK, dslPath=".daiver/inbox/main.dsl.json", frameUrl=URL, frame="main")
        self.assertTrue(out["unchanged"])
        self.assertEqual(snapshot(self.root), before)
        self.assertTrue(self.contract()["signed"])


# ── гейт ui_gate.py design ───────────────────────────────────────────────────

class Gate(Base):
    def test_no_track_is_not_ready(self):
        init_repo(self.root)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("трека «STOR-100» нет", r.stderr)

    def test_exit_0_only_when_ready_and_signed(self):
        self.repo()
        self.resolve_screen()
        self.assertEqual(self.gate().returncode, 1, "резолв не подтверждён: нет отчёта")
        self.report()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("plan: плана кадра нет", r.stderr)
        self.write_plan()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("implementation: адрес страницы реализации не записан", r.stderr)
        self.assertNotIn("plan:", r.stderr)
        self.set_url()
        r = self.gate()
        self.assertEqual(r.returncode, 1, "план готов, но ui-plan.md и lock ещё не собраны, подписи нет")
        self.assertIn("не подписан", r.stderr)
        self.contract()
        self.assertEqual(self.gate().returncode, 1, "готов, но не подписан")
        self.assertEqual(self.sign().returncode, 0)
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_the_signature_alone_is_not_enough_when_the_plan_stopped_being_ready(self):
        self.signed_world()
        self.call("daiver_track_manifest", track=TRACK, frame="main", set={"url": None})
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("implementation", r.stderr)
        self.set_url()
        self.assertEqual(self.gate().returncode, 0, "адрес вернулся тот же — дайджест тот же — подпись снова верна")

    def test_blocking_items_are_listed_with_their_frame_in_a_multi_frame_track(self):
        self.repo(screen(), {"detail": frame("Detail", [leaf("x")])})
        self.resolve_screen()
        self.fin(verdict("Detail", "local"), frame="detail")
        self.report()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        for line in ("plan[main]", "plan[detail]", "implementation[main]", "implementation[detail]"):
            self.assertIn(line, r.stderr)
        self.assertIn("daiver_plan_contract", r.stderr)

    def test_the_resolution_problems_of_2_5_stay_in_the_output(self):
        self.repo()
        self.fin(verdict("Page", "local"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("не решено кандидатов 2", r.stderr)
        self.assertIn("daiver_resolve_finalize", r.stderr)

    def test_other_steps_are_untouched_and_arguments_are_checked(self):
        r = self.gate("verify")                         # ui-verify реализован (4.8): цикла проверки здесь не было — «не готово», а не «не реализовано»
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("не реализована", r.stderr)
        r = self.gate("build")                          # ui-build реализован (3.2): сборки здесь не было — «не готово», а не «не реализовано»
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("не реализована", r.stderr)
        r = subprocess.run([sys.executable, "-X", "utf8", str(UI_GATE), "design", "--project", str(self.root)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_the_gate_only_reads_and_the_runner_records_a_pass_only_for_a_signed_plan(self):
        self.ready_world()
        before = snapshot(self.root)
        for _ in range(2):
            self.gate()
        self.assertEqual(snapshot(self.root), before)
        self.sign()
        cmd = [sys.executable, "-X", "utf8", str(HERE.parents[1] / "pipeline-state" / "scripts" / "record_gate.py"),
               "--project", str(self.root), "--skill", "forgeui", "--feature", TRACK, "--step-id", "ui-design", "--cmd",
               f"{sys.executable} -X utf8 {UI_GATE} design --project {self.root} --feature {TRACK}"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ui-design: PASSED", r.stdout)


# ── planUncertainty: каждый узел один раз ────────────────────────────────────

def uncertain_screen():
    """«Toolbar» — кандидат (осмысленное имя) И контейнер с абсолютными детьми: узел называют оба носителя. Ещё один узел —
    только вердиктом (инстанс), ещё один — только раскладкой (автоимённый контейнер)."""
    return frame("Page", [frame("Toolbar", [abox("t1", 0, 0), abox("t2", 50, 0)]),
                          inst("h1", "Modal Header", {"Steps": "OFF"}, [("Header", "x")]),
                          bar("plain", [abox("p1", 0, 0), abox("p2", 50, 0)])], layout={"direction": "column", "gap": 8})


class Uncertainty(Base):
    def world(self):
        self.repo(uncertain_screen())
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("confirmResolutions: medium", "confirmResolutions: low"), encoding="utf-8")
        self.fin(verdict("Page", "local"))
        self.fin(verdict("Toolbar", "local", "medium", rationale="панель инструментов без аналога"))
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало"))
        self.report()

    def test_a_node_named_by_two_carriers_is_counted_once(self):
        self.world()
        self.write_plan({"layoutRest": "keep-px"})
        unc = self.contract()["planUncertainty"]
        self.assertEqual(sorted(unc["nodes"]), sorted(["h1", "plain", "Toolbar"]))
        self.assertEqual(unc["count"], 3, "Toolbar назван вердиктом medium и раскладкой без свидетельства — считается один раз")
        self.assertEqual(unc["by"], {"resolutions": 2, "layout": 1})

    def test_evidence_makes_a_layout_node_certain_and_a_high_verdict_is_not_uncertain(self):
        self.world()
        self.fin(verdict("Toolbar", "local", "high", rationale="панель инструментов без аналога"))
        self.report()
        self.write_plan({"layout": [
            {"id": "Toolbar", "reason": "absolute-children", "decision": "keep-px", "evidence": "declaration"},
            {"id": "plain", "reason": "absolute-children", "decision": "keep-px", "evidence": "user"}]})
        unc = self.contract()["planUncertainty"]
        self.assertEqual(unc["nodes"], ["h1"])
        self.assertEqual(unc["count"], 1)

    def test_it_survives_a_plan_that_cannot_be_read_and_then_counts_every_unproven_entry(self):
        self.world()
        self.write_plan({"layoutRest": "keep-px"})
        self.paths()["plan"].write_text("{не json", encoding="utf-8")
        out = self.contract()
        self.assertEqual(self.one(out, "plan", "main")["state"], "open")
        self.assertEqual(out["planUncertainty"]["count"], 3)
        self.paths()["plan"].unlink()
        self.assertEqual(self.contract()["planUncertainty"]["count"], 3)

    def test_no_uncertainty_in_a_certain_plan(self):
        self.ready_world()
        self.assertEqual(self.contract()["planUncertainty"], {"count": 0, "by": {}, "nodes": []})

    def test_multi_frame_nodes_carry_the_frame_and_the_same_id_in_two_frames_is_two_nodes(self):
        self.repo(uncertain_screen(), {"detail": uncertain_screen()})
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("confirmResolutions: medium", "confirmResolutions: low"), encoding="utf-8")
        out = self.contract()
        unc = out["planUncertainty"]
        self.assertEqual(unc["count"], 4, "по два узла раскладки в каждом кадре (Toolbar, plain): ключ — кадр и узел")
        self.assertIn("main:plain", unc["nodes"])
        self.assertIn("detail:plain", unc["nodes"])


# ── daiver_plan_write: план пишет код ────────────────────────────────────────

class PlanWrite(Base):
    def prepared(self, root_node=None, **kw):
        """Резолв закончен и подтверждён; плана ещё нет."""
        self.repo(root_node, **kw)
        self.resolve_screen()
        self.report()

    def read(self, name="main"):
        return json.loads(self.paths(name)["plan"].read_text(encoding="utf-8"))

    # — запись —
    def test_json_and_markdown_land_in_the_frame_folder_and_a_repeat_changes_nothing(self):
        self.prepared()
        out = self.write_plan()
        self.assertEqual((out["ok"], out["written"]), (True, True))
        self.assertEqual(out["plan"], f"{TRACK_DIR}/plan.json")
        self.assertEqual(out["planMd"], f"{TRACK_DIR}/plan.md")
        self.assertEqual(out["weightBudget"], 24000)
        self.assertEqual(out["nodes"], {"component": 2, "local": 1, "ignore": 0})
        body, md = self.paths()["plan"].read_bytes(), self.paths()["planMd"].read_bytes()
        self.assertTrue(body.endswith(b"\n") and md.endswith(b"\n"))
        again = self.write_plan()
        self.assertFalse(again["written"], "те же данные — те же байты, файл не переписывается")
        self.assertEqual((self.paths()["plan"].read_bytes(), self.paths()["planMd"].read_bytes()), (body, md))
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
        self.assertEqual(md.decode("utf-8"), PL.render_md(self.read()), "plan.md — чистая отрисовка plan.json")

    def test_plan_json_keys_are_sorted_at_every_level_so_a_diff_shows_only_real_changes(self):
        self.abs_world()
        self.write_plan({"layout": GOOD_LAYOUT})

        def check(pairs):
            keys = [k for k, _v in pairs]
            self.assertEqual(keys, sorted(keys), keys)
            return dict(pairs)

        json.loads(self.paths()["plan"].read_text(encoding="utf-8"), object_pairs_hook=check)

    def test_medium_and_low_verdicts_are_listed_as_uncertain_in_the_plan_and_in_its_header_but_not_high_or_map(self):
        self.repo()
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("confirmResolutions: medium", "confirmResolutions: low"), encoding="utf-8")
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало"))
        self.fin(comp_verdict("a", "ModalAction", "high"))
        self.fin(verdict("Page", "local", "high"))
        self.report()
        self.write_plan()
        plan = self.read()
        self.assertEqual(plan["uncertain"], ["h1"])
        self.assertEqual(plan["stats"]["uncertain"], 1)
        self.assertIn("неуверенных узлов: 1", self.paths()["planMd"].read_text(encoding="utf-8"))
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="теперь уверен"))
        self.report()
        self.write_plan()
        self.assertEqual((self.read()["uncertain"], self.read()["stats"]["uncertain"]), ([], 0))

    def test_two_separately_built_repos_give_byte_identical_plans(self):
        outs = []
        for n in range(2):
            self.tmp2 = tempfile.TemporaryDirectory()
            self.addCleanup(self.tmp2.cleanup)
            self.root = Path(self.tmp2.name) / f"repo{n}"
            self.root.mkdir()
            self.prepared()
            self.write_plan()
            outs.append((self.paths()["plan"].read_bytes(), self.paths()["planMd"].read_bytes()))
        self.assertEqual(outs[0], outs[1], "в плане нет ни путей, ни времени, ни случайности")

    def test_the_plan_names_every_decision_in_the_spec_form(self):
        self.prepared()
        self.write_plan()
        plan = self.read()
        self.assertEqual(plan["schema"], 1)
        self.assertEqual((plan["track"], plan["frame"], plan["role"]), (TRACK, "main", "screen"))
        nodes = {n["id"]: n for n in plan["sections"][0]["nodes"]}
        self.assertEqual(nodes["h1"]["component"], "ModalHeader")
        self.assertNotIn("resolution", nodes["h1"])
        self.assertEqual(nodes["Page"]["resolution"], "local")
        self.assertNotIn("component", nodes["Page"])
        self.assertEqual({n["source"] for n in nodes.values()}, {"resolver"})
        self.assertEqual(plan["stats"], {"sections": 1, "buildable": 1, "candidates": 3, "component": 2, "local": 1, "ignore": 0,
                                         "sharedLocals": 0, "uncertain": 0})
        self.assertEqual(plan["layout"], {"decisions": [], "rest": None})
        self.assertEqual(plan["tree"], {"section": "Page"}, "экран влез в одну секцию — оболочки нет")

    def test_an_ignore_verdict_is_a_node_without_a_component(self):
        self.repo()
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.fin(verdict("a", "ignore", rationale="кнопки в этой версии нет"))
        self.fin(verdict("Page", "local"))
        self.report()
        self.write_plan()
        nodes = {n["id"]: n for n in self.read()["sections"][0]["nodes"]}
        self.assertEqual(nodes["a"]["resolution"], "ignore")
        self.assertNotIn("component", nodes["a"])
        self.assertEqual(self.read()["stats"]["ignore"], 1)

    def test_props_of_a_component_travel_into_the_plan(self):
        self.repo()
        self.fin(comp_verdict("h1", "ModalHeader", props={"closable": True, "size": "large"}))
        self.fin(comp_verdict("a", "ModalAction"))
        self.fin(verdict("Page", "local"))
        self.report()
        self.write_plan()
        nodes = {n["id"]: n for n in self.read()["sections"][0]["nodes"]}
        self.assertEqual(nodes["h1"]["props"], {"closable": True, "size": "large"})
        self.assertNotIn("props", nodes["a"])

    # — секции, хеши, бюджет —
    def test_sections_and_hashes_are_the_outline_ones_and_the_section_tool_accepts_each_id_at_the_planned_budget(self):
        tall = tall_screen()
        budget = tall_budget(tall)
        self.repo(tall, ds=("Unused",))
        for v in (verdict("Page"), verdict("c1"), verdict("h1")):
            self.fin(v)
        self.report()
        out = self.write_plan(weightBudget=budget)
        plan = self.read()
        self.assertEqual(plan["weightBudget"], budget)
        env = C.read_canonical(self.paths()["canonical"])
        want = OL.section_list(env, budget)
        self.assertEqual([(s["id"], s["hash"], s["weight"]) for s in plan["sections"]], [(s["id"], s["hash"], s["weight"]) for s in want])
        self.assertEqual(sorted(b["id"] for b in plan["buildOrder"]), sorted(s["id"] for s in want))
        for b in plan["buildOrder"]:
            sec = self.call("daiver_section", track=TRACK, sectionId=b["id"], weightBudget=plan["weightBudget"])
            self.assertEqual(sec["section"]["hash"], b["hash"], "хеш секции в плане равен хешу, который отдаст сборке daiver_section")
        self.assertEqual(out["sections"], 5)
        self.assertNotIn("Page", [b["id"] for b in plan["buildOrder"]], "оболочка над секциями — не секция сборки")

    def test_the_shell_above_the_sections_is_a_tree_with_a_layout_digest_and_section_references(self):
        tall = tall_screen()
        self.repo(tall, ds=("Unused",))
        for v in (verdict("Page", rationale="оболочка"), verdict("c1"), verdict("h1")):
            self.fin(v)
        self.report()
        self.write_plan(weightBudget=tall_budget(tall))
        tree = self.read()["tree"]
        self.assertEqual((tree["id"], tree["kind"]), ("Page", "frame"))
        self.assertEqual(tree["layout"], {"direction": "column", "gap": 8}, "дайджест раскладки контейнера — из канона")
        self.assertEqual(tree["decision"]["resolution"], "local", "кандидат в оболочке несёт своё решение")
        self.assertEqual([c for c in tree["children"]], [{"section": f"p{n}"} for n in range(1, 6)], "секции — ссылками, в порядке документа")
        sec = self.read()["sections"][1]
        self.assertEqual((sec["layout"], sec["size"]), ({"direction": "column", "gap": 8}, {"w": "fill", "h": "hug"}),
                         "у корня секции — дайджест его раскладки")

    def test_a_smaller_budget_gives_finer_sections_and_the_budget_is_recorded_for_the_build(self):
        tall = tall_screen()
        self.repo(tall, ds=("Unused",))
        for v in (verdict("Page"), verdict("c1"), verdict("h1")):
            self.fin(v)
        self.report()
        self.write_plan()
        self.assertEqual((self.read()["weightBudget"], self.read()["stats"]["sections"]), (24000, 1))
        self.write_plan(weightBudget=tall_budget(tall))
        self.assertEqual(self.read()["stats"]["sections"], 5)
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("weightBudget: 24000", "weightBudget: 100000"), encoding="utf-8")
        self.assertEqual(self.check_line(self.contract(), "plan"), "plan: ok", "бюджет записан в плане: конфиг после плана его не ломает")
        self.assertIn("положительное число", self.refusal("daiver_plan_write", track=TRACK, weightBudget=-5))

    # — порядок сборки —
    def test_build_order_creators_of_shared_locals_first_then_their_users_then_document_order(self):
        tall = tall_screen()
        self.repo(tall, ds=("Unused",))
        for v in (verdict("Page"), verdict("c1"), verdict("h1")):
            self.fin(v)
        self.report()
        self.write_plan(weightBudget=tall_budget(tall))
        plan = self.read()
        self.assertEqual([b["id"] for b in plan["buildOrder"]], ["p2", "p3", "p4", "p5", "p1"],
                         "создатели Info Card (p2) и Page Header (p3), их потребители (p4, p5), остальное (p1)")
        names = {x["name"]: x for x in plan["locals"]}
        self.assertEqual(sorted(names), ["InfoCard", "PageHeader"])
        self.assertEqual((names["InfoCard"]["creator"], names["InfoCard"]["sections"], names["InfoCard"]["nodes"]), ("p2", ["p2", "p4"], 2))
        self.assertEqual((names["PageHeader"]["creator"], names["PageHeader"]["origin"], names["PageHeader"]["shared"]), ("p3", "derived", True))
        by_id = {n["id"]: n for s in plan["sections"] for n in s["nodes"]}
        self.assertEqual((by_id["c1"]["local"], by_id["c2"]["local"], by_id["h2"]["local"]), ("InfoCard", "InfoCard", "PageHeader"))
        self.assertEqual(by_id["c2"]["source"], "reuse", "второй Info Card получил вердикт копией — INV-B23")
        self.assertEqual([b["name"] for b in plan["buildOrder"]], [autoname(n) for n in ("p2", "p3", "p4", "p5", "p1")])

    def test_without_shared_locals_the_order_is_the_document_order_and_the_prologue_is_not_a_step(self):
        tall = tall_screen()
        self.repo(tall, ds=("PageHeader", "InfoCard"))
        self.fin(verdict("Page"))
        self.fin(comp_verdict("c1", "InfoCard"))
        self.fin(comp_verdict("h1", "PageHeader"))
        self.report()
        self.write_plan(weightBudget=tall_budget(tall))
        plan = self.read()
        self.assertEqual([b["id"] for b in plan["buildOrder"]], ["p1", "p2", "p3", "p4", "p5"])
        self.assertEqual(plan["locals"], [])
        for b in plan["buildOrder"]:
            self.assertIn(b["id"], {s["id"] for s in plan["sections"]}, "в buildOrder только настоящие id секций, никаких псевдо-шагов")
        self.assertNotIn("prologue", json.dumps(plan, ensure_ascii=False).lower())

    def test_occluded_sections_are_in_the_plan_but_not_in_the_build_order(self):
        root = frame("Page", [panel("p1", [leaf("t1")]), dict(panel("p2", [leaf("t2")]), occluded=True), panel("p3", [leaf("t3")])],
                     layout={"direction": "column", "gap": 8})
        self.repo(root, ds=("Unused",))
        self.fin(verdict("Page"))
        self.report()
        self.write_plan(weightBudget=tall_budget(root))
        plan = self.read()
        self.assertEqual([s["id"] for s in plan["sections"]], ["p1", "p2", "p3"])
        self.assertTrue(plan["sections"][1]["occluded"])
        self.assertEqual([b["id"] for b in plan["buildOrder"]], ["p1", "p3"])
        self.assertEqual(plan["stats"]["buildable"], 2)

    def test_a_local_name_comes_from_the_map_rule_first_then_from_the_figma_name_and_never_collides(self):
        root = frame("Page", [panel("p1", [inst("a1", "Info Card", {"Kind": "A"}), inst("a2", "Info Card", {"Kind": "A"})]),
                              panel("p2", [inst("b1", "info-card", {"Kind": "B"}), inst("b2", "info-card", {"Kind": "B"})]),
                              panel("p3", [inst("c1", "Карточка", {"Kind": "A"}), inst("c2", "Карточка", {"Kind": "A"})]),
                              panel("p4", [inst("d1", "Promo Box"), inst("d2", "Promo Box"), inst("d3", "Promo Box")])],
                     layout={"direction": "column", "gap": 8})
        self.repo(root, ds=("Unused",))
        (self.root / ".daiver" / "component-map.yaml").write_text(
            "rules:\n  - match: Promo Box\n    outcome: local\n    component: PromoTile\n", encoding="utf-8")
        for v in (verdict("Page"), verdict("a1"), verdict("b1"), verdict("c1"), verdict("d1")):
            self.fin(v)
        self.report()
        self.write_plan(weightBudget=tall_budget(root))
        plan = self.read()
        got = {x["name"]: x for x in plan["locals"]}
        self.assertEqual(sorted(got), ["InfoCard", "InfoCard2", "Local1", "PromoTile"],
                         "карта называет PromoTile; «Info Card» и «info-card» с разными вариантами — одно PascalCase-имя, второму суффикс; "
                         "кириллица — Local1 (транслитерировать = выдумывать)")
        self.assertEqual((got["PromoTile"]["origin"], got["InfoCard"]["origin"]), ("map", "derived"))
        self.assertEqual(got["PromoTile"]["nodes"], 3)
        valid = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        self.assertTrue(all(valid.match(n) for n in got), "имя — допустимый идентификатор JSX")

    def test_a_map_named_local_gets_its_name_even_when_it_occurs_once(self):
        self.repo(screen(), ds=("ModalHeader",))
        (self.root / ".daiver" / "component-map.yaml").write_text(
            "rules:\n  - match: Modal Action\n    outcome: local\n    component: ActionBar\n", encoding="utf-8")
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.fin(verdict("a"))
        self.fin(verdict("Page"))
        self.report()
        self.write_plan()
        plan = self.read()
        by_id = {n["id"]: n for n in plan["sections"][0]["nodes"]}
        self.assertEqual(by_id["a"]["local"], "ActionBar")
        self.assertEqual([x["shared"] for x in plan["locals"]], [False])
        self.assertNotIn("local", by_id["Page"], "фрейм — не инстанс: имени нет")
        self.assertEqual(plan["stats"]["sharedLocals"], 0)

    # — свой buildOrder модели проверяется кодом —
    def tall_world(self):
        tall = tall_screen()
        self.repo(tall, ds=("Unused",))
        for v in (verdict("Page"), verdict("c1"), verdict("h1")):
            self.fin(v)
        self.report()
        return tall_budget(tall)

    def real_order(self, budget):
        self.write_plan(weightBudget=budget)
        order = [{"id": b["id"], "hash": b["hash"]} for b in self.read()["buildOrder"]]
        self.paths()["plan"].unlink()
        self.paths()["planMd"].unlink()
        return order

    def test_the_models_own_build_order_is_accepted_only_when_it_is_the_real_one(self):
        budget = self.tall_world()
        order = self.real_order(budget)
        out = self.write_plan({"buildOrder": order}, weightBudget=budget)
        self.assertTrue(out["ok"])
        self.assertEqual([b["id"] for b in self.read()["buildOrder"]], [o["id"] for o in order])
        named = [dict(o, name="любое имя") for o in order]
        self.assertTrue(self.write_plan({"buildOrder": named}, weightBudget=budget)["ok"], "name необязателен и не сверяется")

    def test_a_wrong_build_order_is_refused_with_every_violation_named_and_nothing_is_written(self):
        budget = self.tall_world()
        order = self.real_order(budget)
        doc_order = sorted(order, key=lambda o: o["id"])                         # p1..p5 — порядок документа, но не порядок сборки
        cases = {
            "чужой id": ([{"id": "zzz", "hash": "abc"}] + order, "не секция кадра"),
            "пролог как шаг сборки": (order + [{"id": "prologue", "hash": "abc"}], "пролог"),
            "не тот хеш": ([dict(order[0], hash="000000000000")] + order[1:], "хеш секции"),
            "пропущена секция": (order[:-1], "нет секций"),
            "не тот порядок": (doc_order, "порядок сборки считает код"),
            "секция дважды": (order + [order[0]], "уже названа"),
            "лишнее поле": ([dict(order[0], status="done")] + order[1:], "неизвестные поля"),
            "без хеша": ([{"id": order[0]["id"]}] + order[1:], "нужны строки id и hash"),
        }
        for title, (bad, phrase) in cases.items():
            before = snapshot(self.root)
            msg = self.refusal("daiver_plan_write", track=TRACK, plan={"buildOrder": bad}, weightBudget=budget)
            self.assertIn(phrase, msg, title)
            self.assertIn("Запись не изменена", msg, title)
            self.assertEqual(snapshot(self.root), before, f"{title}: отказ ничего не пишет")
            self.assertFalse(self.paths()["plan"].exists(), title)
        msg = self.refusal("daiver_plan_write", track=TRACK, weightBudget=budget, plan={"buildOrder": [
            {"id": "zzz", "hash": "abc"}, dict(order[1], hash="000000000000")] + order[2:]})
        for phrase in ("не секция кадра", "хеш секции", "нет секций"):
            self.assertIn(phrase, msg)
        self.assertRegex(msg, r"\(\d+\): 1\) ")

    def test_a_build_order_for_another_budget_names_foreign_ids(self):
        """Секции зависят от бюджета: порядок, снятый при одном бюджете, при другом — чужие id."""
        budget = self.tall_world()
        order = self.real_order(budget)
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"buildOrder": order})
        self.assertIn("не секция кадра при weightBudget=24000", msg)

    # — форма входа —
    def test_the_input_shape_is_strict_and_says_what_the_code_builds_itself(self):
        self.prepared()
        before = snapshot(self.root)
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"sections": [], "tree": {}, "layoutRest": "drop"})
        self.assertIn("plan.sections", msg)
        self.assertIn("plan.tree", msg)
        self.assertIn("код строит сам", msg)
        self.assertIn("plan.layoutRest", msg)
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": "bar", "buildOrder": ["p1"]})
        self.assertIn("plan.layout: ожидается список", msg)
        self.assertIn("plan.buildOrder: ожидается список", msg)
        self.assertIn("ожидается object", self.refusal("daiver_plan_write", track=TRACK, plan="x"))
        self.assertEqual(snapshot(self.root), before)

    # — предусловия: резолв закончен и подтверждён —
    def test_no_plan_until_the_resolution_is_finished_and_confirmed(self):
        self.repo()
        self.fin(verdict("Page", "local"))
        msg = self.refusal("daiver_plan_write", track=TRACK)
        self.assertIn("план строится по подтверждённому резолву", msg)
        self.assertIn("не решено кандидатов 2", msg)
        self.assertFalse(self.paths()["plan"].exists())
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.fin(comp_verdict("a", "ModalAction", "low", rationale="догадка по слотам"))
        self.report()
        msg = self.refusal("daiver_plan_write", track=TRACK)
        self.assertIn("не подтверждены", msg, "low увидит человек, и без его «да» план не строится")
        self.assertFalse(self.paths()["plan"].exists())

    def test_unknown_things_are_named_and_the_tool_checks_its_arguments(self):
        self.prepared()
        self.assertIn("кадра «nope» в треке нет", self.refusal("daiver_plan_write", track=TRACK, frame="nope"))
        self.assertIn("ключ стори", self.refusal("daiver_plan_write", track="../x"))
        self.assertIn("трека «STOR-9» нет", self.refusal("daiver_plan_write", track="STOR-9"))
        self.assertIn("нет обязательного аргумента «track»", self.refusal("daiver_plan_write"))
        self.assertIn("неизвестный аргумент «outPath»", self.refusal("daiver_plan_write", track=TRACK, outPath="x.json"))
        self.assertIn("ожидается number", self.refusal("daiver_plan_write", track=TRACK, weightBudget="много"))
        self.assertIn("положительное число", self.refusal("daiver_plan_write", track=TRACK, weightBudget=-5))
        self.assertIn("нет обязательного аргумента «track»", self.refusal("daiver_plan_contract"))
        self.assertIn("неизвестный аргумент «frame»", self.refusal("daiver_plan_contract", track=TRACK, frame="main"))
        self.assertIn("трека «STOR-9» нет", self.refusal("daiver_plan_contract", track="STOR-9"))

    def test_the_tool_schemas_come_with_required_arguments_and_no_free_output_path(self):
        write, contract = T.TOOLS["daiver_plan_write"]["schema"], T.TOOLS["daiver_plan_contract"]["schema"]
        self.assertEqual(write["required"], ["track"])
        self.assertEqual(sorted(write["properties"]), ["frame", "plan", "track", "weightBudget"])
        self.assertEqual(contract["required"], ["track"])
        self.assertEqual(sorted(contract["properties"]), ["track"])
        for sch in (write, contract):
            self.assertFalse(sch["additionalProperties"])
            self.assertNotIn("outPath", sch["properties"], "данные кадра — в папку кадра трека, произвольных путей нет")

    # — решения по раскладке (§11.4) —
    def abs_world(self):
        self.repo(abs_screen(), ds=("ModalHeader",))
        self.fin(verdict("Page"))
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.report()

    def test_every_unproven_entry_needs_a_decision_or_the_explicit_rest(self):
        self.abs_world()
        msg = self.refusal("daiver_plan_write", track=TRACK)
        self.assertIn("не решено записей unproven: 3", msg)
        for entry in ("bar (absolute-children)", "col (absolute-children)", "hinted (layout-hints)"):
            self.assertIn(entry, msg)
        self.assertIn("daiver_source_nodes", msg)
        self.assertIn("layoutRest", msg)
        out = self.write_plan({"layoutRest": "keep-px"})
        self.assertEqual(out["layout"], {"keep-px": 3, "notDecided": 3})
        decisions = self.read()["layout"]["decisions"]
        self.assertEqual([(d["id"], d["decision"], d["evidence"], d["rest"]) for d in decisions],
                         [("bar", "keep-px", "none", True), ("col", "keep-px", "none", True), ("hinted", "keep-px", "none", True)])
        self.assertEqual(self.read()["layout"]["rest"], "keep-px")
        self.assertEqual(self.read()["stats"]["uncertain"], 3, "не решённое — неопределённость плана")

    def test_decisions_are_stored_with_geometry_computed_by_the_code(self):
        self.abs_world()
        out = self.write_plan({"layout": GOOD_LAYOUT})
        self.assertEqual(out["layout"], {"apply": 1, "group": 1, "keep-px": 1})
        by_id = {d["id"]: d for d in self.read()["layout"]["decisions"]}
        self.assertEqual(by_id["bar"]["groups"], [{"ids": ["b1", "b2", "b3"], "gap": 10, "box": {"x": 0, "y": 0, "w": 140, "h": 20}}])
        self.assertEqual(by_id["bar"]["direction"], "row")
        self.assertEqual(by_id["hinted"]["hints"], {"main": "space-between", "w": "fill"},
                         "принятый hint копируется из канона: сборка берёт его из плана, а не из канона")
        self.assertEqual(by_id["col"], {"id": "col", "reason": "absolute-children", "decision": "keep-px", "evidence": "none"})
        self.assertEqual(self.read()["uncertain"], ["col"], "keep-px без свидетельства — неопределённость; остальные подтверждены")
        self.assertEqual(self.check_line(self.contract(), "plan"), "plan: ok", "план с решениями пересобирается из самого себя побайтно")

    def test_a_group_is_ordered_by_position_and_uneven_gaps_are_listed_not_averaged(self):
        self.abs_world()
        layout = [dict(GOOD_LAYOUT[0], groups=[["b3", "b1", "b2"]]), GOOD_LAYOUT[1], GOOD_LAYOUT[2]]
        self.write_plan({"layout": layout})
        self.assertEqual(self.read()["layout"]["decisions"][0]["groups"][0]["ids"], ["b1", "b2", "b3"], "порядок задаёт позиция")
        self.edit_canon(lambda env: next(k for k in env["root"]["children"] if k["id"] == "bar")["children"][2]["box"].update(x=120))
        self.write_plan({"layout": layout})
        g = self.read()["layout"]["decisions"][0]["groups"][0]
        self.assertEqual((g.get("gaps"), "gap" in g), ([10, 30], False))
        self.assertEqual(g["box"], {"x": 0, "y": 0, "w": 160, "h": 20})
        self.edit_canon(lambda env: next(k for k in env["root"]["children"] if k["id"] == "bar")["children"][2]["box"].update(x=101))
        self.write_plan({"layout": layout})
        self.assertEqual(self.read()["layout"]["decisions"][0]["groups"][0]["gap"], 10.5, "10 и 11 px — один ритм (допуск 1 px)")

    def test_a_column_group_measures_along_y(self):
        self.abs_world()
        layout = [GOOD_LAYOUT[0], {"id": "col", "reason": "absolute-children", "decision": "group", "evidence": "user",
                                   "direction": "column", "groups": [["c2", "c1"]]}, GOOD_LAYOUT[2]]
        self.write_plan({"layout": layout})
        col = self.read()["layout"]["decisions"][1]
        self.assertEqual(col["groups"], [{"ids": ["c1", "c2"], "gap": 10, "box": {"x": 0, "y": 0, "w": 20, "h": 70}}])

    def test_bad_layout_decisions_are_all_named_in_one_refusal(self):
        self.abs_world()
        before = snapshot(self.root)
        bad = [
            {"id": "nope", "reason": "absolute-children", "decision": "keep-px", "evidence": "none"},
            {"id": "bar", "reason": "absolute-children", "decision": "apply", "evidence": "declaration"},
            {"id": "col", "reason": "absolute-children", "decision": "group", "evidence": "none", "direction": "row",
             "groups": [["c1", "c2"]]},
            {"id": "hinted", "reason": "layout-hints", "decision": "apply", "evidence": "magic"},
            {"id": "bar", "reason": "layout-hints", "decision": "keep-px", "evidence": "none"},
            {"id": "col", "reason": "absolute-children", "decision": "keep-px", "evidence": "none", "color": "red"},
        ]
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": bad, "layoutRest": "keep-px"})
        for phrase in ("в unproven кадра нет", "для причины «absolute-children» допустимо keep-px | group",
                       "без свидетельства не принимается", "layout[3].evidence", "неизвестное поле"):
            self.assertIn(phrase, msg)
        self.assertEqual(snapshot(self.root), before)

    def test_group_violations_are_named(self):
        self.abs_world()
        cases = [
            ({"decision": "group", "evidence": "user", "direction": "diagonal", "groups": [["b1", "b2"]]}, "row | column"),
            ({"decision": "group", "evidence": "user", "direction": "row"}, "непустой список групп"),
            ({"decision": "group", "evidence": "user", "direction": "row", "groups": [["b1"]]}, "в группе от двух узлов"),
            ({"decision": "group", "evidence": "user", "direction": "row", "groups": [["b1", "zz"]]}, "не дети узла"),
            ({"decision": "group", "evidence": "user", "direction": "row", "groups": [["b1", "b2"], ["b2", "b3"]]}, "в двух группах"),
            ({"decision": "keep-px", "evidence": "none", "direction": "row"}, "только при decision=group"),
            ({"decision": "group", "evidence": "user", "direction": "row", "groups": [["b1", "b2"]], "note": "x" * 300}, "note"),
        ]
        for extra, phrase in cases:
            d = dict({"id": "bar", "reason": "absolute-children"}, **extra)
            msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": [d], "layoutRest": "keep-px"})
            self.assertIn(phrase, msg, extra)
        self.edit_canon(lambda env: next(k for k in env["root"]["children"] if k["id"] == "bar")["children"][1]["box"].update(x=20))
        d = dict(GOOD_LAYOUT[0])
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": [d], "layoutRest": "keep-px"})
        self.assertIn("перекрываются", msg, "блоки наложились — это не ряд")

    def test_a_decision_that_the_canon_no_longer_supports_makes_the_plan_stale(self):
        self.abs_world()
        self.write_plan({"layout": GOOD_LAYOUT})
        self.edit_canon(lambda env: next(k for k in env["root"]["children"] if k["id"] == "bar").update(layout={"direction": "row"}))
        it = self.one(self.contract(), "plan", "main")
        self.assertEqual(it["state"], "stale")
        self.assertTrue("раскладке" in it["detail"] or "секции макета" in it["detail"], it["detail"])


# ── золотой plan.md «Modal»: единственное место, где формат зафиксирован целиком ─────────────────────────────

GOLDEN_MODAL_PLAN_MD = """# План кадра «main» — STOR-100

Файл строит движок (`daiver_plan_write`), руками не правится. Подписывается не он, а lock всего трека (`plan.lock.json`, согласие `ui-plan-STOR-100`).

Роль: screen · макет: https://pixso.example/a?item-id=2:153702 · бюджет секции (weightBudget): 24000 Б
Требование: sha256 `167e6205ea75` · канон: rootHash `0ccf768ee9d7`
Кандидатов: 4 — component 3, local 1, ignore 0 · секций: 1 (строится 1) · общих local-компонентов: 0 · неуверенных узлов: 0

## Порядок сборки

Пролог (токены, моки) — не секция и в порядок не входит; секции под непрозрачным слоем (occluded) не строятся.

1. [2:153702](https://pixso.example/a?item-id=2:153702) «Modal» · hash `0ccf768ee9d7` · 3101 Б

## Секции

### [2:153702](https://pixso.example/a?item-id=2:153702) «Modal»

frame · hash `0ccf768ee9d7` · 3101 Б · раскладка корня: column pad=20/20/20/20 main=start cross=center w=600 h=hug box=0,0,600,245

| Узлы | Макет | Решение | Уверенность | Откуда |
|---|---|---|---|---|
| [2:153702](https://pixso.example/a?item-id=2:153702) | фрейм «Modal» | local | high | резолвер |
| [2:153703](https://pixso.example/a?item-id=2:153703), [22:63141](https://pixso.example/a?item-id=22:63141) | Modal Header {Steps=OFF} ×2 | component `ModalHeader` | high | резолвер |
| [2:153704](https://pixso.example/a?item-id=2:153704) | Modal Action {Variant=Default} | component `ModalAction` | high | резолвер |

## Раскладка (unproven)

Контейнеров с абсолютными детьми и непринятых подсказок нет — раскладка макета объявлена или доказана.

## Local-компоненты

Общих и названных local-компонентов нет.

## Спеки

Файлов: docs/feature-pipeline/STOR-100/ui-spec.md
- Данные: docs/feature-pipeline/STOR-100/ui-spec.md:15 «Данные»
- События: docs/feature-pipeline/STOR-100/ui-spec.md:18 «События»
- Сценарии e2e: `ui-spec#1` Given пользователь авторизован

## Дизайн-система

Токены макета (0): —
Цвета без привязанного токена (2): rgba(255, 255, 255, 1), rgba(31, 20, 51, 0.12)
"""


class GoldenAndRpc(ModalWorld):
    def test_the_plan_markdown_of_the_sample_is_pinned_whole(self):
        self.modal()
        self.assertEqual(self.paths()["planMd"].read_text(encoding="utf-8"), GOLDEN_MODAL_PLAN_MD)

    def test_the_tools_work_over_json_rpc_and_the_answers_are_plain_json(self):
        self.modal()
        server = HERE.parents[2] / "hooks" / "daiver_mcp.py"
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "daiver_plan_contract", "arguments": {"track": TRACK}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "daiver_plan_write", "arguments": {"track": TRACK, "plan": {"tree": {}}}}}]
        r = subprocess.run([sys.executable, "-X", "utf8", str(server), "--project-root", str(self.root)],
                           input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        out = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
        names = {t["name"] for t in out[0]["result"]["tools"]}
        self.assertTrue({"daiver_plan_write", "daiver_plan_contract"} <= names)
        self.assertNotIn("daiver_plan_sign", names)
        body = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual((body["ready"], body["signed"]), (True, True))
        self.assertEqual(len(body["checks"]), 16)
        self.assertTrue(out[2]["result"]["isError"], "отказ плана — isError, а не падение сервера")
        self.assertIn("plan.tree", out[2]["result"]["content"][0]["text"])


# ── несколько кадров ─────────────────────────────────────────────────────────

class MultiFrame(Base):
    def world(self, state=False):
        detail = frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"}, [("Header", "q")])])
        self.repo(screen(), {"detail": detail}, roles={"detail": "state" if state else "dialog"})
        if state:
            self.call("daiver_track_manifest", track=TRACK, frame="detail", set={"stateOf": "main"})
        self.resolve_screen()
        self.fin(verdict("Detail", "local"), frame="detail")
        self.report()

    def test_each_frame_has_its_plan_in_its_folder_and_the_track_has_one_digest_and_one_lock(self):
        self.world()
        self.write_plan(frame="main")
        self.write_plan(frame="detail")
        for name in ("main", "detail"):
            self.assertTrue((self.root / TRACK_DIR / "frames" / name / "plan.json").is_file())
            self.assertTrue((self.root / TRACK_DIR / "frames" / name / "plan.md").is_file())
        self.assertFalse((self.root / TRACK_DIR / "plan.json").exists())
        self.set_url()
        self.set_url(PAGE + "/details", "detail")
        self.call("daiver_track_manifest", track=TRACK, frame="detail", set={
            "open": {"actions": [{"verb": "click", "selector": "#open"}], "clipSelector": ".modal"}})
        out = self.contract()
        self.assertTrue(out["ready"], out["open"])
        self.assertEqual(sorted(out["written"]), [LOCK_REL, UI_PLAN_REL], "ui-plan.md и lock лежат в корне трека, не в папке кадра")
        lock = json.loads((self.root / LOCK_REL).read_text(encoding="utf-8"))
        self.assertEqual([f["name"] for f in lock["frames"]], ["main", "detail"], "порядок манифеста")
        text = (self.root / UI_PLAN_REL).read_text(encoding="utf-8")
        self.assertIn("Кадр «main»", text)
        self.assertIn("Кадр «detail»", text)
        self.assertIn(PAGE + "/details", text)
        self.assertEqual(self.check_line(out, "plan"), "plan: main ok, detail ok")
        self.assertEqual(self.sign().returncode, 0)
        self.assertEqual(self.gate().returncode, 0)

    def test_one_frame_without_a_plan_keeps_the_whole_track_not_ready(self):
        self.world()
        self.write_plan(frame="main")
        self.set_url()
        self.set_url(PAGE + "/details", "detail")
        out = self.contract()
        self.assertFalse(out["ready"])
        self.assertEqual(self.check_line(out, "plan"), "plan: main ok, detail open")
        self.assertEqual(self.one(out, "plan")["frame"], "detail")
        self.assertIn("plan[detail]", out["blocks"])
        self.assertNotIn("plan[main]", out["blocks"])
        self.assertFalse((self.root / UI_PLAN_REL).exists())

    def test_the_second_frame_getting_a_stale_plan_unsigns_the_track(self):
        self.world()
        for n in ("main", "detail"):
            self.write_plan(frame=n)
        self.set_url()
        self.set_url(PAGE + "/details", "detail")
        self.call("daiver_track_manifest", track=TRACK, frame="detail", set={
            "open": {"actions": [{"verb": "click", "selector": "#open"}], "clipSelector": ".modal"}})
        self.contract()
        self.sign()
        self.assertTrue(self.contract()["signed"])
        self.fin(verdict("Detail", "ignore", rationale="кадр больше не нужен в этой версии"), frame="detail")
        c = self.evaluate(write=False)
        self.assertFalse(c.signed)
        self.assertEqual(self.gate().returncode, 1)

    def test_a_state_frame_is_planned_as_a_state_of_its_screen(self):
        self.world(state=True)
        self.write_plan(frame="detail")
        plan = json.loads((self.root / TRACK_DIR / "frames" / "detail" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual((plan["role"], plan["stateOf"]), ("state", "main"))
        self.assertIn("состояние экрана «main»", (self.root / TRACK_DIR / "frames" / "detail" / "plan.md").read_text(encoding="utf-8"))


# ── ui-plan.md ───────────────────────────────────────────────────────────────

class Digest(Base):
    SECTIONS = ("## Кадры", "## Секции", "## Компоненты", "### Компоненты библиотеки", "### Свои (local) — и почему",
                "## Дизайн-система", "## Ассеты", "## Бизнес-логика", "## Принятые деградации", "## Неопределённость плана",
                "## Открытые вопросы", "## Строка задачи")

    def text(self):
        return (self.root / UI_PLAN_REL).read_text(encoding="utf-8")

    def test_the_digest_has_every_section_of_the_spec_and_its_facts_come_from_artifacts(self):
        self.ready_world()
        text = self.text()
        for h in self.SECTIONS:
            self.assertIn(h, text)
        order = [text.index(h) for h in self.SECTIONS]
        self.assertEqual(order, sorted(order), "разделы идут в порядке спеки §12.4")
        self.assertIn(PAGE, text, "адрес страницы — из track.yaml")
        self.assertIn("`ModalHeader`", text)
        self.assertIn("ui-spec#1", text, "сценарий e2e — из разбора спеки")
        self.assertIn("src/shared/api/orders.ts", text, "путь типа — из «Данных»")
        self.assertIn("`forge-ui: track=STOR-100 from=plan`", text)
        self.assertIn("Блокирующих вопросов нет", text)
        self.assertIn("оболочка экрана", text, "почему local — основание из вердикта")

    def test_degradations_are_listed_with_their_price_and_the_assets_section_names_placeholders(self):
        self.repo(art_screen())
        self.resolve_screen()
        self.report()
        self.write_plan()
        self.set_url()
        out = self.contract()
        self.assertTrue(out["ready"], out["open"])
        text = self.text()
        self.assertIn("**assets**", text)
        self.assertIn("**goal-image**", text)
        self.assertIn("**dev-command**", text)
        self.assertIn("→ цена: помеченный placeholder правильного размера вместо иллюстрации", text)
        self.assertIn("БЕЗ файла (будет помеченный placeholder): hero", text)
        self.assertEqual(text.count("**assets**"), 1)

    def test_the_digest_is_not_written_while_there_are_blocking_questions_and_names_none_when_written(self):
        self.repo()
        self.resolve_screen()
        self.report()
        self.write_plan()
        self.contract()
        self.assertFalse((self.root / UI_PLAN_REL).exists(), "нет адреса страницы — блокирующий вопрос — файлов нет")
        self.assertFalse((self.root / LOCK_REL).exists())
        self.set_url()
        self.contract()
        self.assertIn("Блокирующих вопросов нет: файл собирается, только когда контракт плана готов", self.text())

    def test_the_digest_is_a_pure_function_of_the_artifacts_and_carries_no_time(self):
        self.ready_world()
        first = self.text()
        (self.root / UI_PLAN_REL).unlink()
        (self.root / LOCK_REL).unlink()
        self.contract()
        self.assertEqual(self.text(), first)
        self.assertNotRegex(first, r"\b20\d\d-\d\d-\d\d\b", "в дайджесте нет дат и времени")
        self.assertNotIn(str(self.root), first, "и путей машины")
        self.assertNotIn(str(self.chrome), first)

    def test_uncertainty_and_layout_without_evidence_reach_the_human_in_the_digest(self):
        self.repo(abs_screen(), ds=("ModalHeader",))
        self.fin(verdict("Page"))
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало"))
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("confirmResolutions: medium", "confirmResolutions: low"), encoding="utf-8")
        self.report()
        self.write_plan({"layoutRest": "keep-px"})
        self.set_url()
        self.contract()
        text = self.text()
        self.assertIn("Узлов, в которых план не уверен: 4", text)
        self.assertIn("по раскладке без свидетельства (px оставлен): 3", text)
        self.assertIn("h1", text.split("## Неопределённость плана")[1].split("## Открытые вопросы")[0])
        plan_md = self.paths()["planMd"].read_text(encoding="utf-8")
        self.assertIn("не решено (layoutRest)", plan_md)


# ── код модулей: детерминизм и гигиена ───────────────────────────────────────

class Hygiene(unittest.TestCase):
    MODULES = ("plan.py", "plan_contract.py", "plan_digest.py")

    def trees(self):
        for name in self.MODULES:
            path = HERE / "daiver" / name
            yield name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_no_time_no_randomness_no_environment_in_what_goes_into_the_signed_files(self):
        for name, tree in self.trees():
            imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
            self.assertFalse(imported & {"time", "datetime", "random", "uuid", "secrets"}, f"{name}: {sorted(imported)}")
        text = (HERE / "daiver" / "plan.py").read_text(encoding="utf-8") + (HERE / "daiver" / "plan_digest.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ", text, "план и дайджест не зависят от окружения машины: браузер проверяет только контракт")

    def test_every_module_keeps_the_python_floor_and_the_encoding_rule(self):
        for name in self.MODULES:
            text = (HERE / "daiver" / name).read_text(encoding="utf-8")
            self.assertIn("from __future__ import annotations", text, name)
        for name, tree in self.trees():
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("read_text", "write_text"):
                    self.assertTrue(any(k.arg == "encoding" for k in n.keywords) or (n.func.attr == "read_text" and n.args),
                                    f"{name}:{n.lineno}: {n.func.attr} без encoding=")
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
                    self.fail(f"{name}:{n.lineno}: open() — файлы читает pathlib с явной кодировкой")

    def test_the_closed_lists_of_the_contract_match_the_spec(self):
        self.assertEqual(PC.STATES, ("ok", "open", "stale", "unknown"))
        self.assertEqual(PC.OWNERS, ("human", "plan", "host"))
        self.assertEqual(sorted(PC.REASONS), sorted(["initialized", "library-source", "browser", "requirement", "canonical", "plan",
                                                    "resolutions", "implementation", "baseline", "sources", "signature"]))
        # причины останова — из §20.3 (no-browser — наше дополнение, оно названо в README)
        spec_reasons = {"not-initialized", "library-source-unreachable", "no-requirement", "track-conflict", "plan-incomplete",
                        "plan-unsigned", "no-implementation", "no-sources", "no-open-recipe", "no-dev-command"}
        used = set(PC.REASONS.values()) | {PC.OPEN_RECIPE_REASON, PC.DEV_COMMAND_REASON, PC.TRACK_CONFLICT}
        self.assertEqual(used - spec_reasons, {"no-browser"})


# ── угловые случаи плана: запись, имена, оболочка, дайджест токенов, нечитаемое ──────────────────────────────

class PlanCorners(Base):
    def read(self, name="main"):
        return json.loads(self.paths(name)["plan"].read_text(encoding="utf-8"))

    def prepared(self, root_node=None, resolve=None, **kw):
        self.repo(root_node, **kw)
        for v in (resolve or [verdict("Page")]):
            self.fin(v)
        self.report()

    # — запись —
    def test_a_failed_write_is_a_named_error_and_leaves_no_half_written_file(self):
        self.prepared(screen(), [comp_verdict("h1", "ModalHeader"), comp_verdict("a", "ModalAction"), verdict("Page")])
        with mock.patch("os.replace", side_effect=OSError("диск полон")):
            msg = self.refusal("daiver_plan_write", track=TRACK)
        self.assertIn("не удалась", msg)
        self.assertIn("plan.json не обновлён", msg)
        leftovers = [p.name for p in (self.root / TRACK_DIR).rglob("*") if p.name.startswith("plan")]
        self.assertEqual(leftovers, [], "ни plan.json, ни хвостов .tmp")

    def test_the_input_helpers_work_without_the_tool_layer(self):
        self.assertEqual(PL.check_input(None), ({"layout": [], "layoutRest": None, "buildOrder": None, "placement": None}, []))
        self.assertEqual(PL.check_input([])[1], ["plan: ожидается объект {layout?, layoutRest?, buildOrder?, placement?}"])
        self.assertEqual(PL._rel(Path("/a"), Path("/b/c")), "/b/c", "путь вне корня называется как есть")

    def test_more_bad_layout_entries_each_with_its_own_reason(self):
        root = frame("Page", [bar("nobox", [leaf("n1"), leaf("n2")]), bar("bar", [abox("b1", 0, 0), abox("b2", 50, 0)])],
                     layout={"direction": "column"})
        self.prepared(root)
        base = {"id": "bar", "reason": "absolute-children", "decision": "keep-px", "evidence": "none"}
        cases = [
            ({"reason": "absolute-children", "decision": "keep-px", "evidence": "none"}, "id узла из unproven"),
            (dict(base, reason="whatever"), "как в записи unproven"),
            (dict(base, id="nobox", decision="group", evidence="user", direction="row", groups=[["n1", "n2"]]), "нет box"),
            (dict(base, note=7), "строка до 200 знаков"),
        ]
        for entry, phrase in cases:
            msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": [entry], "layoutRest": "keep-px"})
            self.assertIn(phrase, msg, entry)
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": [base, dict(base, evidence="user")], "layoutRest": "keep-px"})
        self.assertIn("решён дважды", msg)

    def test_apply_does_not_take_group_fields_and_a_note_travels_into_both_files(self):
        self.prepared(abs_screen(), [verdict("Page"), comp_verdict("h1", "ModalHeader")], ds=("ModalHeader",))
        msg = self.refusal("daiver_plan_write", track=TRACK, plan={"layout": [
            dict(GOOD_LAYOUT[2], direction="row")], "layoutRest": "keep-px"})
        self.assertIn("только при decision=group", msg)
        layout = [GOOD_LAYOUT[0], dict(GOOD_LAYOUT[1], note="блоки поплыли — оставляем px"), GOOD_LAYOUT[2]]
        self.write_plan({"layout": layout})
        self.assertEqual(self.read()["layout"]["decisions"][1]["note"], "блоки поплыли — оставляем px")
        self.assertIn("блоки поплыли — оставляем px", self.paths()["planMd"].read_text(encoding="utf-8"))

    def test_plan_markdown_shows_the_decisions_and_the_gaps_of_a_layout(self):
        self.prepared(abs_screen(), [verdict("Page"), comp_verdict("h1", "ModalHeader")], ds=("ModalHeader",))
        self.write_plan({"layout": GOOD_LAYOUT})
        md = self.paths()["planMd"].read_text(encoding="utf-8")
        self.assertIn("row: [b1, b2, b3] gap=10", md)
        box = next(d for d in self.read()["layout"]["decisions"] if d["decision"] == "group")["groups"][0]["box"]
        self.assertIn(f"gap=10 box={box['x']},{box['y']},{box['w']},{box['h']}", md, "сборка берёт бокс группы из plan.md (3.2)")
        self.assertIn("main=space-between w=fill", md)
        self.assertIn("| absolute-children | group | goal-image |", md)

    # — имена local-компонентов —
    def test_a_lonely_local_has_no_name_and_a_name_starting_with_a_digit_is_prefixed(self):
        self.repo(screen())
        for v in (comp_verdict("h1", "ModalHeader"), verdict("a"), verdict("Page")):
            self.fin(v)
        self.report()
        self.write_plan()
        by_id = {n["id"]: n for n in self.read()["sections"][0]["nodes"]}
        self.assertNotIn("local", by_id["a"], "одиночный local без правила карты — просто разметка, имя ему не нужно")
        root = frame("Page", [panel("p1", [inst("a1", "3D Card", {"K": "1"}), inst("a2", "3D Card", {"K": "1"})])], layout={"direction": "column"})
        self.root = Path(self.tmp.name) / "repo2"
        self.root.mkdir()
        self.prepared(root, [verdict("Page"), verdict("a1")], ds=("Unused",))
        self.write_plan(weightBudget=tall_budget(root))
        self.assertEqual([x["name"] for x in self.read()["locals"]], ["Local3DCard"])

    # — оболочка и вложенные контейнеры —
    def test_nested_containers_above_the_sections_make_a_nested_tree(self):
        mid = frame("mid", [panel("p1", [leaf("a")]), panel("p2", [leaf("b")]), panel("p3", [leaf("c")])], name=autoname("mid"),
                    layout={"direction": "row", "gap": 4, "wrap": True})
        root = frame("Page", [mid, panel("p4", [leaf("d")])], layout={"direction": "column", "gap": 8})
        budget = max(OL._Map(root).W[k] for k in ("p1", "p2", "p3", "p4"))
        self.prepared(root, ds=("Unused",))
        self.write_plan(weightBudget=budget)
        tree = self.read()["tree"]
        self.assertEqual([c.get("id") or c.get("section") for c in tree["children"]], ["mid", "p4"])
        self.assertEqual([c["section"] for c in tree["children"][0]["children"]], ["p1", "p2", "p3"])
        self.assertEqual(tree["children"][0]["layout"], {"direction": "row", "gap": 4, "wrap": True})
        md = self.paths()["planMd"].read_text(encoding="utf-8")
        self.assertIn("## Оболочка над секциями", md)
        self.assertIn("row gap=4 wrap", md)
        self.assertEqual(self.check_line(self.contract(), "plan"), "plan: ok")

    def test_without_a_node_url_template_the_files_carry_bare_ids_never_an_invented_link(self):
        m = self.repo(screen())
        TR.upsert_frame(m, "main", frameUrl="не ссылка")
        TR.save(self.root, m)
        for v in (comp_verdict("h1", "ModalHeader"), comp_verdict("a", "ModalAction"), verdict("Page")):
            self.fin(v)
        self.report()
        self.write_plan()
        self.assertIsNone(self.read()["nodeUrlTemplate"])
        md = self.paths()["planMd"].read_text(encoding="utf-8")
        self.assertNotIn("](", md)
        self.assertIn("`h1`", md)

    def test_a_group_shows_the_resolver_record_as_its_origin_even_when_the_copy_comes_first_in_the_document(self):
        root = frame("Page", [inst("m1", "Modal Header", {"Steps": "OFF"}), inst("m2", "Modal Header", {"Steps": "OFF"})],
                     layout={"direction": "column"})
        self.repo(root)
        self.fin(comp_verdict("m2", "ModalHeader"))                           # эталон — второй по документу узел
        self.fin(verdict("Page"))
        self.report()
        self.write_plan()
        nodes = {n["id"]: n for n in self.read()["sections"][0]["nodes"]}
        self.assertEqual((nodes["m1"]["source"], nodes["m2"]["source"]), ("reuse", "resolver"))
        row = next(l for l in self.paths()["planMd"].read_text(encoding="utf-8").splitlines() if "Modal Header" in l)
        self.assertIn("| резолвер |", row)
        self.assertNotIn("копия вердикта", row)

    def test_a_screen_fully_covered_by_an_opaque_layer_has_nothing_to_build(self):
        self.repo(frame("Page", [leaf("t")], occluded=True), ds=("Unused",))
        self.report()
        out = self.write_plan()
        self.assertEqual(out["buildOrder"], [])
        self.assertEqual(self.read()["buildOrder"], [])
        self.assertIn("Строить нечего", self.paths()["planMd"].read_text(encoding="utf-8"))

    # — дайджест токенов и цветов —
    def test_tokens_and_unbound_colors_come_from_the_canon_and_a_library_instance_inside_is_not_the_projects(self):
        inner = frame("inner", [], name=autoname("inner"), style={"background": "#999999"})
        card = inst("i1", "Info Card", None, None, style={"background": "#abcdef"})
        card["children"] = [inner]
        root = frame("Page", [
            frame("bg", [], name=autoname("bg"), style={"background": "#ffffff"}),
            frame("dark", [], name=autoname("dark"), style={"background": "#000000", "border": {"width": 1, "color": "#eeeeee"}},
                  tokens={"fill": "Bg/Dark"}),
            leaf("t1", "text", color="#111111", font={"size": 14}),
            leaf("t2", "text", color="#222222", tokens={"text": "Text/B1"}, font={"token": "Text/B1"}),
            leaf("t3", "text", font={"token": "Text/Caption"}),
            card], layout={"direction": "column"})
        self.prepared(root, [verdict("Page"), verdict("i1")], ds=("Unused",))
        self.write_plan()
        ds = self.read()["design"]
        self.assertEqual(ds["tokens"], ["Bg/Dark", "Text/B1", "Text/Caption"])
        self.assertEqual(ds["rawColors"], ["#111111", "#abcdef", "#eeeeee", "#ffffff"],
                         "#000000 привязан к токену заливки, #222222 — к токену текста, #999999 лежит внутри инстанса библиотеки")
        self.assertEqual((ds["tokensCount"], ds["rawColorsCount"]), (3, 4))

    # — нечитаемое —
    def test_a_json_nested_beyond_the_recursion_limit_is_unreadable_not_a_crash(self):
        self.prepared(screen(), [comp_verdict("h1", "ModalHeader"), comp_verdict("a", "ModalAction"), verdict("Page")])
        self.write_plan()
        self.paths()["plan"].write_text("[" * 200000, encoding="utf-8")
        data, raw, why = PL.read_plan(self.paths()["plan"])
        self.assertEqual((data, raw), (None, None))
        self.assertIn("вложенность", why)
        self.assertEqual(self.one(self.contract(), "plan", "main")["state"], "open")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root читает любой файл")
    def test_a_plan_that_cannot_be_opened_is_named_not_passed(self):
        self.prepared(screen(), [comp_verdict("h1", "ModalHeader"), comp_verdict("a", "ModalAction"), verdict("Page")])
        self.write_plan()
        p = self.paths()["plan"]
        p.chmod(0)
        self.addCleanup(lambda: p.chmod(0o644))
        it = self.one(self.contract(), "plan", "main")
        self.assertEqual(it["state"], "open")
        self.assertIn("не читается", it["detail"])

    def test_a_hand_edited_layout_or_budget_in_the_plan_is_stale_or_open_never_a_crash(self):
        self.prepared(screen(), [comp_verdict("h1", "ModalHeader"), comp_verdict("a", "ModalAction"), verdict("Page")])
        self.write_plan()
        good = self.read()
        for bad in ({"layout": []}, {"layout": {"decisions": "x", "rest": 5}}):
            self.paths()["plan"].write_text(json.dumps(dict(good, **bad), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(self.one(self.contract(), "plan", "main")["state"], "stale", bad)
        for bad in (-1, 0, "много", None, True):
            self.paths()["plan"].write_text(json.dumps(dict(good, weightBudget=bad), ensure_ascii=False), encoding="utf-8")
            it = self.one(self.contract(), "plan", "main")
            self.assertEqual(it["state"], "open", bad)
            self.assertIn("weightBudget", it["detail"])

    def test_the_plan_cannot_be_checked_without_the_verdict_store_and_that_blocks(self):
        self.prepared(screen(), [comp_verdict("h1", "ModalHeader"), comp_verdict("a", "ModalAction"), verdict("Page")])
        self.write_plan()
        self.paths()["resolutions"].unlink()
        out = self.contract()
        it = self.one(out, "plan", "main")
        self.assertEqual((it["state"], it["blocking"]), ("unknown", True))
        self.assertIn("не решено кандидатов", it["detail"])
        self.assertFalse(out["ready"])

    # — много секций: усечённые списки —
    def test_many_sections_truncate_the_lists_in_the_answer_and_the_digest_but_not_the_plan(self):
        root = frame("Page", [panel(f"p{n:02d}", [leaf(f"t{n}")]) for n in range(26)], layout={"direction": "column"})
        self.prepared(root, ds=("Unused",))
        out = self.write_plan(weightBudget=tall_budget(root))
        self.assertEqual(out["sections"], 26)
        self.assertEqual((len(out["buildOrder"]), out["buildOrderMore"]), (10, 16))
        self.assertEqual(len(self.read()["buildOrder"]), 26, "в плане порядок полный")
        self.set_url()
        self.contract()
        self.assertIn("… ещё 2 (полный порядок — plan.md кадра)", (self.root / UI_PLAN_REL).read_text(encoding="utf-8"))


# ── угловые случаи контракта ─────────────────────────────────────────────────

class ContractCorners(Base):
    # — поиск браузера: делегирован daiver.browser.find (4.1); платформы, порядок и приоритеты — test_cdp.FindBrowser —
    def test_the_browser_search_is_delegated_to_browser_find_with_the_verify_block_of_the_config(self):
        self.repo()
        seen = {}

        def fake(config=None, **kw):
            seen["config"], seen["root"] = config, kw.get("root")
            return {"found": True, "path": "/x/chrome", "source": "config.browserPath", "detail": "verify.browserPath из .daiver/config.yaml"}

        with mock.patch.object(PC.BF, "find_browser", side_effect=fake):
            out = self.contract()
        self.assertEqual(self.check_line(out, "browser"), "browser: ok")
        self.assertIn("browserPath", seen["config"], "в поиск уходит блок verify эффективного конфига")
        self.assertEqual(Path(seen["root"]), self.root)

    def test_the_browser_named_by_the_config_is_found_when_the_variable_is_unset(self):
        self.repo()
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8").replace("verify:\n", f"verify:\n  browserPath: {self.chrome}\n", 1)
        cfg.write_text(text, encoding="utf-8")
        with mock.patch.dict(os.environ):
            os.environ.pop(PC.BROWSER_ENV, None)
            path, how = PC.find_browser(CFG.load(self.root)["config"]["verify"], self.root)
            self.assertEqual((path, how), (str(self.chrome), "verify.browserPath из .daiver/config.yaml"))
            self.assertEqual(self.check_line(self.contract(), "browser"), "browser: ok")

    # — сбои данных —
    def test_a_requirement_file_that_cannot_be_read_counts_as_missing(self):
        self.repo()
        with mock.patch.object(PC.TR, "requirement_status", side_effect=OSError("нет доступа")):
            it = self.one(self.contract(), "requirement", "main")
        self.assertEqual(it["state"], "open")
        self.assertIn("файла требования нет на диске", it["detail"])

    def test_a_crash_inside_the_plan_check_is_unknown_and_blocks(self):
        self.ready_world()
        with mock.patch.object(PL, "check_frame", side_effect=RuntimeError("сломалась")):
            out = self.contract()
        it = self.one(out, "plan", "main")
        self.assertEqual((it["state"], it["blocking"]), ("unknown", True))
        self.assertIn("RuntimeError", it["detail"])
        self.assertFalse(out["ready"])

    def test_a_track_that_cannot_be_read_or_has_no_frames_is_refused_by_name(self):
        init_repo(self.root)
        d = self.root / TRACK_DIR
        d.mkdir(parents=True)
        (d / "track.yaml").write_text("track: [oops\n", encoding="utf-8")
        self.assertIn("повреждён", self.refusal("daiver_plan_contract", track=TRACK))
        TR.save(self.root, TR.new_manifest(TRACK, created="2026-10-15T12:00:00Z"))
        self.assertIn("нет ни одного кадра", self.refusal("daiver_plan_contract", track=TRACK))
        with self.assertRaises(PC.ContractError):
            PC.evaluate(self.root, TRACK)

    def test_a_table_row_without_a_link_matches_the_frame_by_name(self):
        self.repo()
        self.write_spec(UI_SPEC.replace("| main | screen | https://pixso.example/a?item-id=2:153702 |", "| main | screen | export.json |"))
        self.assertEqual(self.check_line(self.contract(), "requirement"), "requirement: ok, ok")
        self.write_spec(UI_SPEC.replace("| main | screen | https://pixso.example/a?item-id=2:153702 |", "| orders | screen | export.json |"))
        self.assertTrue(any("orders" in i["detail"] for i in self.find(self.contract(), "requirement") if "frame" not in i))

    def test_storybook_mcp_without_a_server_name_is_an_open_item(self):
        init_repo(self.root, source="storybook-mcp")
        build_repo(self.root, screen())
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('storybookMcp: "sb"', "storybookMcp: null"), encoding="utf-8")
        out = self.contract()
        it = self.one(out, "library-source")
        self.assertEqual((it["state"], it["blocking"]), ("open", True))
        self.assertIn("library.storybookMcp", it["detail"])

    # — списки в ответе обрезаются, счётчик честный —
    def test_many_uncertain_nodes_are_counted_all_and_listed_by_twenty(self):
        root = frame("Page", [bar(f"b{n:02d}", [abox("x", 0, 0), abox("y", 50, 0)]) for n in range(22)], layout={"direction": "column"})
        for n, k in enumerate(root["children"]):                                   # id детей уникальны в пределах кадра
            for c in k["children"]:
                c["id"] = f"{k['id']}-{c['id']}"
        self.repo(root, ds=("Unused",))
        self.fin(verdict("Page"))
        self.report()
        self.write_plan({"layoutRest": "keep-px"})
        unc = self.contract()["planUncertainty"]
        self.assertEqual((unc["count"], len(unc["nodes"]), unc["nodesMore"]), (22, 20, 2))

    def test_a_planned_state_frame_shows_its_screen_in_the_digest_and_shared_locals_reach_the_question(self):
        detail = frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"}, [("Header", "q")])])
        self.repo(screen(), {"detail": detail}, roles={"detail": "state"})
        self.call("daiver_track_manifest", track=TRACK, frame="detail", set={"stateOf": "main"})
        self.resolve_screen()
        self.fin(verdict("Detail", "local"), frame="detail")
        self.report()
        for n in ("main", "detail"):
            self.write_plan(frame=n)
        self.set_url()
        self.call("daiver_track_manifest", track=TRACK, frame="detail", set={
            "open": {"actions": [{"verb": "click", "selector": "#more"}], "clipSelector": ".state"}})
        out = self.contract()
        self.assertTrue(out["ready"], out["open"])
        self.assertIn("state ← «main»", (self.root / UI_PLAN_REL).read_text(encoding="utf-8"))

    def test_a_second_frame_with_a_placement_puts_the_one_module_into_the_digest_and_the_question(self):
        """6.1: у не-первичного кадра решение о месте корня видно человеку (дайджест и вопрос подписи); у одного кадра раздела «Модуль» нет."""
        detail = frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"}, [("Header", "q")])])
        self.repo(screen(), {"detail": detail}, roles={"detail": "dialog"})
        self.resolve_screen()
        self.fin(verdict("Detail", "local"), frame="detail")
        self.report()
        self.write_plan(frame="main")
        self.write_plan({"placement": {"mode": "own-file", "component": "DetailDialog", "reason": "модалка поверх экрана"}}, frame="detail")
        self.set_url()
        self.set_url(frame="detail")
        self.call("daiver_track_manifest", track=TRACK, frame="detail", set={"open": {"actions": [{"verb": "click", "selector": "#more"}], "clipSelector": ".dlg"}})
        out = self.contract()
        self.assertTrue(out["ready"], [(i["id"], i.get("frame"), i["detail"][:80]) for i in out["open"] if i["blocking"]] or out["blocks"])
        text = (self.root / UI_PLAN_REL).read_text(encoding="utf-8")
        self.assertIn("## Модуль", text)
        self.assertIn("| detail | отдельный компонент в каталоге модуля | DetailDialog | модалка поверх экрана |", text)
        self.assertTrue(any(l.startswith("Один модуль трека; корни кадров: detail — отдельный компонент DetailDialog") for l in out["toSign"]), out["toSign"])

    def test_a_single_frame_digest_has_no_module_section(self):
        self.ready_world()
        self.assertNotIn("## Модуль", (self.root / UI_PLAN_REL).read_text(encoding="utf-8"))

    def test_shared_local_components_are_named_in_the_question_to_sign(self):
        tall = tall_screen()
        self.repo(tall, ds=("Unused",))
        for v in (verdict("Page"), verdict("c1"), verdict("h1")):
            self.fin(v)
        self.report()
        self.write_plan(weightBudget=tall_budget(tall))
        self.set_url()
        out = self.contract()
        self.assertIn("Общие local-компоненты: InfoCard, PageHeader", out["toSign"])
        self.assertTrue(any(l.startswith("Компоненты библиотеки: нет") for l in out["toSign"]))

    # — digest: ignore, нет local, спеки без путей и вовсе без спек —
    def test_digest_names_ignored_nodes_and_says_when_there_is_no_local_or_no_logic(self):
        self.repo(screen(), ds=("ModalHeader", "Page"))
        self.fin(comp_verdict("h1", "ModalHeader"))
        self.fin(verdict("a", "ignore", rationale="кнопки в этой версии нет"))
        self.fin(comp_verdict("Page", "Page"))
        self.report()
        self.write_plan()
        self.set_url()
        self.write_spec(UI_SPEC.replace("types: `src/shared/api/orders.ts`", "моки: клиента пока нет"))
        self.write_plan()
        self.contract()
        text = (self.root / UI_PLAN_REL).read_text(encoding="utf-8")
        self.assertIn("### Не строятся (ignore): 1", text)
        self.assertIn("Узлов с решением local нет.", text)
        self.assertIn("«Данные» не называют путей типов и клиента — моки и пропсы onX (в спеке упомянуты моки)", text)
        self.write_spec("# STOR-100\n\n## Кадры\n| Имя | Роль | Макет |\n|---|---|---|\n| main | screen | https://pixso.example/a?item-id=2:153702 |\n")
        self.write_plan()
        self.contract()
        self.assertIn("Разделов логики в спеках нет — модуль презентационный", (self.root / UI_PLAN_REL).read_text(encoding="utf-8"))
        self.assertIn("Разделов логики в спеках нет", self.paths()["planMd"].read_text(encoding="utf-8"))

    def test_the_digest_names_the_sections_that_are_not_built(self):
        root = frame("Page", [panel("p1", [leaf("t1")]), dict(panel("p2", [leaf("t2")]), occluded=True)], layout={"direction": "column"})
        self.repo(root, ds=("Unused",))
        self.fin(verdict("Page"))
        self.report()
        self.write_plan(weightBudget=tall_budget(root))
        self.set_url()
        self.contract()
        self.assertIn("Не строятся (закрыты непрозрачным слоем): p2", (self.root / UI_PLAN_REL).read_text(encoding="utf-8"))

    def test_a_buildorder_naming_a_covered_section_is_refused(self):
        root = frame("Page", [panel("p1", [leaf("t1")]), dict(panel("p2", [leaf("t2")]), occluded=True)], layout={"direction": "column"})
        self.repo(root, ds=("Unused",))
        self.fin(verdict("Page"))
        self.report()
        budget = tall_budget(root)
        self.write_plan(weightBudget=budget)
        secs = json.loads(self.paths()["plan"].read_text(encoding="utf-8"))["sections"]
        h = {sec["id"]: sec["hash"] for sec in secs}
        msg = self.refusal("daiver_plan_write", track=TRACK, weightBudget=budget,
                           plan={"buildOrder": [{"id": "p1", "hash": h["p1"]}, {"id": "p2", "hash": h["p2"]}]})
        self.assertIn("закрыта непрозрачным слоем", msg)

    # — гейт как функция —
    def test_gate_problems_lists_blocking_items_then_the_signature_and_nothing_when_signed(self):
        self.assertEqual(PC.gate_problems(self.root, "STOR-9")[0][:12], "трека «STOR-")
        self.repo()
        self.resolve_screen()
        self.report()
        resolver = T._story_specs(self.root)
        problems = PC.gate_problems(self.root, TRACK, resolver)
        self.assertTrue(any(p.startswith("plan: плана кадра нет") for p in problems), problems)
        self.write_plan()
        self.set_url()
        self.contract()
        problems = PC.gate_problems(self.root, TRACK, resolver)
        self.assertEqual(len(problems), 1)
        self.assertIn("не подписан", problems[0])
        self.assertIn("record_approval.py", problems[0])
        self.sign()
        self.assertEqual(PC.gate_problems(self.root, TRACK, resolver), [])


class DirectCalls(Base):
    """Публичные функции модулей работают и без слоя инструментов (их зовут гейт и следующие задачи)."""

    def test_check_frame_names_a_missing_and_an_unreadable_plan_by_itself(self):
        self.ready_world()
        ctx = PL.RS.Ctx(self.root, TRACK, "main")
        st = ctx.state(PL.RS.read_store(ctx.root, ctx.spath))
        self.assertEqual(PL.check_frame(self.root, ctx, st, T._story_specs(self.root))["state"], "ok")
        self.paths()["plan"].write_text("{не json", encoding="utf-8")
        got = PL.check_frame(self.root, ctx, st, T._story_specs(self.root))
        self.assertEqual(got["state"], "open")
        self.assertIn("неразбираемый план не проходит", got["detail"])
        self.paths()["plan"].unlink()
        self.assertIn("плана кадра нет", PL.check_frame(self.root, ctx, st, T._story_specs(self.root))["detail"])

    def test_signature_state_is_the_reader_for_the_guard_and_does_not_depend_on_readiness(self):
        self.ready_world()
        resolver = T._story_specs(self.root)
        self.assertEqual(PC.signature_state(self.root, TRACK, resolver)["state"], "open")
        self.sign()
        self.assertEqual(PC.signature_state(self.root, TRACK, resolver)["state"], "ok")
        self.call("daiver_track_manifest", track=TRACK, frame="main", set={"url": None})
        got = PC.signature_state(self.root, TRACK, resolver)
        self.assertEqual(got["state"], "stale", "адрес — факт дайджеста: подписанное содержимое изменилось")
        with self.assertRaises(PC.ContractError):
            PC.signature_state(self.root, "STOR-9", resolver)

    def test_a_bad_track_name_is_a_contract_error_for_the_python_caller_too(self):
        with self.assertRaises(PC.ContractError):
            PC.evaluate(self.root, "../x")


if __name__ == "__main__":
    unittest.main()
