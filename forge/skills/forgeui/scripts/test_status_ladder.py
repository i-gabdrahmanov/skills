#!/usr/bin/env python3
"""test_status_ladder.py — daiver_status: лестница трека и предупреждения (задача 1.10; спека §3.2, §15.7;
INV C1, A13; QG S2, X4).

Пинит: карту «артефакты на диске → ступень кадра» таблицей по сочетаниям (S2) — от пустого кадра до clean,
включая «стухшая секция после чистого verify → building» (building раньше verify) и самолечение лестницы
(пропавший нижний артефакт опускает кадр, вернувшийся — поднимает); чтение state-файлов по МИНИМАЛЬНОМУ
контракту (plan.json — JSON-объект; build-state.json — sections[].status из перечня, список не пуст;
verify-state.json — history[], clean последней записи — bool): не вписавшийся файл (мусор, не UTF-8, не тот
тип, неизвестный статус, слишком глубокий JSON) читается как отсутствующий, называется в warnings и не роняет
статус; многокадровый трек — ступень фронтирного кадра (первый не-clean в порядке манифеста), clean ⇔ все
кадры clean, итог (outcome) — функция ступеней (X4); каждое предупреждение по отдельному коду (устарело
требование — громче всего у собранного кадра без versions/, нет goal image, арт без .svg, требования ДС не
строились, кадр без требования, URL не записан после built, спеки не на месте, pull без frameUrl); разрешение
адреса страницы по ролям (§14.2); верхний nextStep; статус только читает, детерминирован и сериализуется;
имена шагов в nextStep — из реестра веток forge (hooks/_branches.py).
Синтетика: plan.json / build-state.json / verify-state.json пишутся в минимальном контракте лестницы (их
писателей — 2.6, 3.1, 4.8 — ещё нет); манифест, файл требования (хеш из манифеста) и канон — настоящие; на
образце «Modal» (fixtures/pixso-modal) проверен ход pull → normalize → goal image.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
HOOKS = HERE.parents[2] / "hooks"

import _testkit as K  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import status as ST  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
URL = "https://pixso.example/a?item-id=2:1"
PAGE = "http://localhost:5173/orders"
TRACK = "STOR-100"
NOW = "2026-10-16T00:00:00Z"
DEFAULT_IMPL = {"url": PAGE}
CLEAN = [{"iteration": 1, "clean": True, "blocking": 0}]
DIRTY = [{"iteration": 1, "clean": False, "blocking": 2}]
OUTCOME_OF = {"clean": "clean", "verifying": "defects-remaining"}      # остальные ступени — never-run


# ── построители ───────────────────────────────────────────────────────────────
def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frame(name="main", **kw) -> dict:
    """Что лежит на диске у кадра. Умолчание — кадр на ступени planned без замечаний.

    req: ok | stale | missing | none; canon: True | False | "stale" | "corrupt"; plan: True | False | своё
    содержимое; build — список статусов секций | своё; verify — список записей history | своё;
    raster: True | False | "missing" (записан, файла нет) | "garbage" (не картинка)."""
    spec = dict(name=name, role="screen", req="ok", canon=True, plan=True, build=None, verify=None, raster=True,
                arts=(), svgs=(), url=None, state_of=None, frame_url=True, versions=False)
    unknown = set(kw) - set(spec)
    assert not unknown, unknown
    spec.update(kw)
    return spec


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    elif isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def canon_env(source_sha: str, arts=()) -> dict:
    kids = []
    for i, a in enumerate(arts):
        slug, occluded = (a, False) if isinstance(a, str) else a
        node = {"id": f"art{i}", "kind": "vector", "name": slug, "box": {"x": 0, "y": 10 * i, "w": 40, "h": 20},
                "art": {"slug": slug, "nodes": 3}}
        if occluded:
            node["occluded"] = True
        kids.append(node)
    root = {"id": "root", "kind": "frame", "name": "Screen", "box": {"x": 0, "y": 0, "w": 400, "h": 300}}
    if kids:
        root["children"] = kids
    return C.new_envelope(root, "pixso", {"sourceSha256": source_sha})


def _build_value(v):
    if isinstance(v, list):
        return {"version": 1, "sections": [{"id": f"s{i}", "status": st} for i, st in enumerate(v)]}
    return v


def _verify_value(v):
    return {"history": v} if isinstance(v, list) else v


def make_track(root, frames, key=TRACK, implementation=DEFAULT_IMPL, registered=None, story_spec=True) -> dict:
    """Трек на диске: настоящий манифест (проходит validate), файлы требований, каналы и state-файлы по описанию."""
    root = Path(root)
    m = TR.new_manifest(key, created=NOW)
    text_of = {}
    for spec in frames:
        name = spec["name"]
        text_of[name] = '{"dsl":{"frame":"%s"}}' % name
        fields = {"role": spec["role"]}
        if spec["state_of"]:
            fields["stateOf"] = spec["state_of"]
        if spec["frame_url"]:
            fields["frameUrl"] = URL
        if spec["url"]:
            fields["url"] = spec["url"]
        if spec["raster"]:
            fields["raster"] = "visual-requirements.png"
        if spec["req"] != "none":
            fields["requirement"] = {
                "kind": "pixso", "path": "visual-requirements.pixso.json", "pulledAt": NOW,
                "sha256": sha("записано в манифесте") if spec["req"] == "stale" else sha(text_of[name])}
        TR.upsert_frame(m, name, **fields)
    if implementation is not None:
        m["implementation"] = implementation
    if registered:
        m["specs"] = sorted(registered)
    TR.save(root, m)
    for spec in frames:
        name = spec["name"]
        paths = TR.frame_paths(root, m, name)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        if spec["req"] in ("ok", "stale"):
            _write(paths["requirement"], text_of[name])
        manifest_sha = (TR.get_frame(m, name).get("requirement") or {}).get("sha256") or sha(text_of[name])
        if spec["canon"] is True or spec["canon"] == "stale":
            C.write_canonical(paths["canonical"], canon_env(
                manifest_sha if spec["canon"] is True else sha("другой экспорт"), spec["arts"]))
        elif spec["canon"] == "corrupt":
            _write(paths["canonical"], "{не JSON")
        if spec["plan"] is True:
            _write(paths["plan"], {"buildOrder": []})
        elif spec["plan"] is not False and spec["plan"] is not None:
            _write(paths["plan"], spec["plan"])
        if spec["build"] is not None:
            _write(paths["buildState"], _build_value(spec["build"]))
        if spec["verify"] is not None:
            _write(paths["verifyState"], _verify_value(spec["verify"]))
        if spec["raster"] is True:
            _write(paths["dir"] / "visual-requirements.png", K.png_bytes(8, 8))
        elif spec["raster"] == "garbage":
            _write(paths["dir"] / "visual-requirements.png", b"not an image")
        for slug in spec["svgs"]:
            _write(paths["assets"] / f"{slug}.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
        if spec["versions"]:
            (paths["versions"] / "1").mkdir(parents=True, exist_ok=True)
    if story_spec:
        _write(root / "docs" / "feature-pipeline" / key / "ui-spec.md", "# spec\n")
    for rel, exists in (registered or {}).items():
        if exists:
            _write(root / rel, "# spec\n")
    return m


def init_repo(root, source="storybook-mcp") -> None:
    root = Path(root)
    _write(root / "package.json", "{}")
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "src" / "components").mkdir(parents=True, exist_ok=True)
    answers = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
               "buildCommand": "npm run build", "librarySource": source}
    if source == "storybook-mcp":
        answers["storybookMcp"] = "sb"
    else:
        answers["scanPaths"] = ["src/components"]
    res = I.init(root, answers)
    assert res["ok"], res


def status(root) -> dict:
    st = T.call("daiver_status", Path(root), {})
    json.dumps(st, ensure_ascii=False)          # ответ уходит в MCP как JSON — обязан сериализоваться
    return st


def codes(st) -> list:
    """Коды статусных предупреждений (строки конфига кодов не имеют и отфильтровываются)."""
    out = [w.split(":", 1)[0] for w in st["warnings"]]
    return sorted(c for c in out if c in ST.WARNING_CODES)


def warning(st, code) -> str:
    hits = [w for w in st["warnings"] if w.startswith(code + ":")]
    assert len(hits) == 1, (code, st["warnings"])
    return hits[0]


def track_of(st, key=TRACK) -> dict:
    return next(t for t in st["tracks"] if t["name"] == key)


def branch_steps() -> tuple:
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    import _branches
    return tuple(_branches.BRANCHES["forgeui"]["steps"])


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def track(self, *frames, **kw) -> dict:
        return make_track(self.root, list(frames), **kw)

    def one(self, **kw) -> dict:
        """Статус единственного кадра трека STOR-100: (вид трека, весь ответ)."""
        st = status(self.root)
        return track_of(st), st


# ── карта «артефакты → ступень» (QG S2) ───────────────────────────────────────
# (имя, кадр, ступень, шаг forge-ui или None для clean, нечитаемые state-файлы)
DONE2 = ["done", "done"]
ROWS = [
    ("нет записи требования", dict(req="none", canon=False, plan=False), "empty", "ui-design", ()),
    ("запись есть, файла требования нет", dict(req="missing", canon=False, plan=False), "empty", "ui-design", ()),
    ("только требование", dict(canon=False, plan=False), "requirement", "ui-design", ()),
    ("требование устарело, канона нет", dict(req="stale", canon=False, plan=False), "requirement", "ui-design", ()),
    ("канон", dict(plan=False), "normalized", "ui-design", ()),
    ("план", dict(), "planned", "ui-build", ()),
    ("сборка: все pending", dict(build=["pending", "pending"]), "building", "ui-build", ()),
    ("сборка: часть done", dict(build=["done", "pending"]), "building", "ui-build", ()),
    ("сборка: in_progress", dict(build=["done", "in_progress"]), "building", "ui-build", ()),
    ("сборка: stale", dict(build=["done", "stale"]), "building", "ui-build", ()),
    ("сборка: все done", dict(build=DONE2), "built", "ui-verify", ()),
    ("сборка: одна секция, done", dict(build=["done"]), "built", "ui-verify", ()),
    ("verify: history пуста (итерация открыта)", dict(build=DONE2, verify=[]), "built", "ui-verify", ()),
    ("verify: последняя не чистая", dict(build=DONE2, verify=DIRTY), "verifying", "ui-verify", ()),
    ("verify: последняя чистая", dict(build=DONE2, verify=CLEAN), "clean", None, ()),
    ("verify: чистая, потом грязная", dict(build=DONE2, verify=CLEAN + DIRTY), "verifying", "ui-verify", ()),
    ("verify: грязная, потом чистая", dict(build=DONE2, verify=DIRTY + CLEAN), "clean", None, ()),
    ("verify: clean=true при непустых blocking — верим clean",
     dict(build=DONE2, verify=[{"clean": True, "blocking": ["d1"]}]), "clean", None, ()),
    # building РАНЬШЕ verify: стухшая секция возвращает проверенный кадр в сборку
    ("stale после чистого verify → building", dict(build=["done", "stale"], verify=CLEAN), "building", "ui-build", ()),
    ("pending после чистого verify → building", dict(build=["done", "pending"], verify=CLEAN), "building", "ui-build", ()),
    ("in_progress после чистого verify → building", dict(build=["in_progress"], verify=CLEAN), "building", "ui-build", ()),
    ("stale после грязного verify → building", dict(build=["stale"], verify=DIRTY), "building", "ui-build", ()),
    # лестница: ступень = самая высокая из подряд достигнутых
    ("план пропал, build и verify лежат", dict(plan=False, build=DONE2, verify=CLEAN), "normalized", "ui-design", ()),
    ("канон пропал, остальное лежит", dict(canon=False, build=DONE2, verify=CLEAN), "requirement", "ui-design", ()),
    ("файл требования пропал, остальное лежит", dict(req="missing", build=DONE2, verify=CLEAN), "empty", "ui-design", ()),
    ("записи требования нет, остальное лежит", dict(req="none", build=DONE2, verify=CLEAN), "empty", "ui-design", ()),
    ("verify без build-state", dict(verify=CLEAN), "planned", "ui-build", ()),
    ("устаревшее требование ступень не понижает", dict(req="stale", build=DONE2, verify=CLEAN), "clean", None, ()),
    # битые / неожиданные state-файлы читаются как отсутствующие и называются
    ("канон: не JSON", dict(canon="corrupt"), "requirement", "ui-design", ("visual-requirements.canonical.json",)),
    ("plan.json: мусор", dict(plan="nope"), "normalized", "ui-design", ("plan.json",)),
    ("plan.json: массив", dict(plan="[]"), "normalized", "ui-design", ("plan.json",)),
    ("plan.json: JSON null", dict(plan="null"), "normalized", "ui-design", ("plan.json",)),
    ("plan.json: пустой файл", dict(plan=""), "normalized", "ui-design", ("plan.json",)),
    ("plan.json: не UTF-8", dict(plan=b"\xff\xfe\x00\x00garbage"), "normalized", "ui-design", ("plan.json",)),
    ("plan.json: вложенность за пределом рекурсии", dict(plan="[" * 100000), "normalized", "ui-design", ("plan.json",)),
    ("plan.json: пустой объект — контракт выполнен", dict(plan="{}"), "planned", "ui-build", ()),
    ("build-state: мусор", dict(build="nope"), "planned", "ui-build", ("build-state.json",)),
    ("build-state: массив", dict(build="[]"), "planned", "ui-build", ("build-state.json",)),
    ("build-state: секций нет", dict(build=[]), "planned", "ui-build", ("build-state.json",)),
    ("build-state: без sections", dict(build={"version": 1}), "planned", "ui-build", ("build-state.json",)),
    ("build-state: sections не список", dict(build={"sections": "done"}), "planned", "ui-build", ("build-state.json",)),
    ("build-state: статус вне перечня", dict(build=["done", "finished"]), "planned", "ui-build", ("build-state.json",)),
    ("build-state: секция не объект", dict(build={"sections": ["done"]}), "planned", "ui-build", ("build-state.json",)),
    ("build-state: статус нестрока", dict(build={"sections": [{"status": ["done"]}]}), "planned", "ui-build", ("build-state.json",)),
    ("verify-state: мусор при собранном кадре", dict(build=DONE2, verify="nope"), "built", "ui-verify", ("verify-state.json",)),
    ("verify-state: мусор при открытых секциях", dict(build=["pending"], verify="nope"), "building", "ui-build", ("verify-state.json",)),
    ("verify-state: массив", dict(build=DONE2, verify="[]"), "built", "ui-verify", ("verify-state.json",)),
    ("verify-state: history не список", dict(build=DONE2, verify={"history": {}}), "built", "ui-verify", ("verify-state.json",)),
    ("verify-state: без history", dict(build=DONE2, verify={"iterations": 3}), "built", "ui-verify", ("verify-state.json",)),
    ("verify-state: у записи нет clean", dict(build=DONE2, verify=[{"iteration": 1}]), "built", "ui-verify", ("verify-state.json",)),
    ("verify-state: clean строкой", dict(build=DONE2, verify=[{"clean": "true"}]), "built", "ui-verify", ("verify-state.json",)),
    ("verify-state: запись не объект", dict(build=DONE2, verify=["clean"]), "built", "ui-verify", ("verify-state.json",)),
    ("битые build и verify вместе", dict(build="x", verify="y"), "planned", "ui-build", ("build-state.json", "verify-state.json")),
]


def expected_codes(kw, unreadable) -> list:
    """Какие предупреждения обязаны быть у кадра из таблицы (независимо от status.py): по требованию и битым файлам."""
    spec = frame(**kw)
    out = ["artifact-unreadable"] * len(unreadable)
    if spec["req"] == "none":
        out.append("frame-no-requirement")
    elif spec["req"] == "missing":
        out.append("requirement-missing")
    elif spec["req"] == "stale":
        out.append("requirement-stale-built" if spec["build"] is not None else "requirement-stale")
    return sorted(out)


class Ladder(Env):
    def test_table_artifacts_to_stage_and_next_step(self):
        steps = branch_steps()
        for name, kw, stage, step, unreadable in ROWS:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                make_track(tmp, [frame(**kw)])
                st = status(tmp)
                t = track_of(st)
                self.assertEqual(t["stage"], stage)
                self.assertEqual([f["stage"] for f in t["frames"]], [stage])
                self.assertEqual(t["outcome"], OUTCOME_OF.get(stage, "never-run"))
                if step is None:
                    self.assertTrue(t["nextStep"].startswith("готово"), t["nextStep"])
                    self.assertNotIn("frame", t, "у clean-трека фронтирного кадра нет")
                else:
                    self.assertIn(step, steps, "шаг из nextStep обязан быть шагом ветки forgeui")
                    self.assertTrue(t["nextStep"].startswith(step + ": "), t["nextStep"])
                    self.assertIn("«main»", t["nextStep"], "nextStep называет кадр")
                    self.assertIn(TRACK, t["nextStep"], "nextStep называет трек")
                    self.assertEqual(t["frame"], "main")
                bad = [w for w in st["warnings"] if w.startswith("artifact-unreadable:")]
                self.assertEqual(len(bad), len(unreadable), bad)
                for fname in unreadable:
                    self.assertEqual(sum(fname in w for w in bad), 1, (fname, bad))
                self.assertEqual(codes(st), expected_codes(kw, unreadable))

    def test_stale_after_clean_verify_returns_to_building_and_heals(self):
        """Макет сдвинулся → сборка пометила секцию stale → кадр снова building; пересобрали → clean опять."""
        m = self.track(frame(build=DONE2, verify=CLEAN))
        paths = TR.frame_paths(self.root, m, "main")
        self.assertEqual(self.one()[0]["stage"], "clean")
        _write(paths["buildState"], _build_value(["done", "stale"]))
        t, _ = self.one()
        self.assertEqual((t["stage"], t["outcome"]), ("building", "never-run"))
        self.assertIn("1 из 2", t["nextStep"])
        self.assertEqual(t["frames"][0]["build"], "1/2")
        _write(paths["buildState"], _build_value(DONE2))
        self.assertEqual(self.one()[0]["stage"], "clean", "пересобранная секция возвращает чистую историю в силу")

    def test_missing_lower_artifact_lowers_the_frame_and_heals_when_it_returns(self):
        m = self.track(frame(build=DONE2, verify=CLEAN))
        canon = TR.frame_paths(self.root, m, "main")["canonical"]
        saved = canon.read_bytes()
        canon.unlink()
        t, st = self.one()
        self.assertEqual(t["stage"], "requirement")
        self.assertIn("daiver_normalize", t["nextStep"])
        self.assertEqual(codes(st), [], "пропавший канон — не повреждение: тихо опускаем ступень")
        canon.write_bytes(saved)
        self.assertEqual(self.one()[0]["stage"], "clean")

    def test_garbage_state_file_never_raises_and_is_named(self):
        """Директория на месте файла — отсутствует (не файл); любой мусор — отсутствует + строка в warnings."""
        m = self.track(frame(build=DONE2))
        paths = TR.frame_paths(self.root, m, "main")
        (paths["verifyState"]).mkdir()
        t, st = self.one()
        self.assertEqual(t["stage"], "built")
        self.assertEqual(codes(st), [])
        paths["verifyState"].rmdir()
        _write(paths["verifyState"], b"\x00\x01\x02")
        t, st = self.one()
        self.assertEqual(t["stage"], "built")
        self.assertEqual(codes(st), ["artifact-unreadable"])
        self.assertIn("verify-state.json", warning(st, "artifact-unreadable"))

    def test_blocking_count_shapes(self):
        cases = [([{"clean": False, "blocking": ["d1", "d2", "d3"]}], 3), ([{"clean": False, "blocking": 4}], 4),
                 ([{"clean": False, "blocking": 0}], 0),          # гейт не пройден при пустом списке дефектов
                 ([{"clean": False, "blocking": True}], None), ([{"clean": False, "blocking": -1}], None),
                 ([{"clean": False, "blocking": {"d": 1}}], None), ([{"clean": False}], None)]
        for hist, expect in cases:
            with self.subTest(hist), tempfile.TemporaryDirectory() as tmp:
                make_track(tmp, [frame(build=DONE2, verify=hist)])
                t = track_of(status(tmp))
                view = t["frames"][0]
                self.assertEqual(view.get("blocking"), expect)
                self.assertEqual(view["iterations"], 1)
                self.assertEqual("блокирующих дефектов" in t["nextStep"], bool(expect), t["nextStep"])
                self.assertIn("последняя не чистая", t["nextStep"])

    def test_frame_view_fields(self):
        self.track(frame(build=["done", "pending"], url=PAGE), implementation=None)
        view = self.one()[0]["frames"][0]
        self.assertEqual(view, {"name": "main", "role": "screen", "stage": "building", "dir": ".", "url": PAGE,
                                "requirement": "pixso", "goalImage": True, "build": "1/2"})
        self.track(frame(build=DONE2, verify=DIRTY + DIRTY), key="STOR-2")
        v2 = track_of(status(self.root), "STOR-2")["frames"][0]
        self.assertEqual((v2["stage"], v2["iterations"], v2["blocking"], v2["build"]), ("verifying", 2, 2, "2/2"))
        self.assertEqual(v2["url"], PAGE, "первичный кадр берёт implementation.url трека")


class MultiFrame(Env):
    """Каждый кадр climbs отдельно; ступень трека — фронтирного (первого с работой), clean ⇔ все clean (INV-C1, X4)."""
    CLEAN_F = dict(build=["done"], verify=CLEAN)
    BUILT = dict(build=["done"])
    BUILDING = dict(build=["done", "pending"])
    VERIFYING = dict(build=["done"], verify=DIRTY)
    PLANNED = dict()
    NORM = dict(plan=False)
    REQ = dict(canon=False, plan=False)
    EMPTY = dict(req="none", canon=False, plan=False)

    def run_case(self, frames, stage, frontier, outcome):
        with tempfile.TemporaryDirectory() as tmp:
            make_track(tmp, [frame(n, **kw) for n, kw in frames])
            t = track_of(status(tmp))
            self.assertEqual((t["stage"], t.get("frame"), t["outcome"]), (stage, frontier, outcome))
            self.assertEqual([f["name"] for f in t["frames"]], [n for n, _ in frames], "кадры — в порядке манифеста")
            return t

    def test_frontier_is_first_frame_with_work_in_manifest_order(self):
        C_, B, BG, V, P, N, R, E = (self.CLEAN_F, self.BUILT, self.BUILDING, self.VERIFYING, self.PLANNED,
                                    self.NORM, self.REQ, self.EMPTY)
        cases = [
            ([("a", C_), ("b", C_)], "clean", None, "clean"),
            ([("a", C_), ("b", B)], "built", "b", "never-run"),
            ([("a", B), ("b", C_)], "built", "a", "never-run"),
            ([("a", C_), ("b", E)], "empty", "b", "never-run"),
            ([("a", V), ("b", P)], "verifying", "a", "defects-remaining"),
            ([("a", P), ("b", V)], "planned", "a", "defects-remaining"),
            ([("a", BG), ("b", C_), ("c", R)], "building", "a", "never-run"),
            ([("zeta", B), ("alpha", N)], "built", "zeta", "never-run"),      # не по алфавиту и не по высоте ступени
            ([("zeta", C_), ("alpha", N), ("mid", V)], "normalized", "alpha", "defects-remaining"),
            ([("a", C_), ("b", C_), ("c", C_)], "clean", None, "clean"),
        ]
        for frames, stage, frontier, outcome in cases:
            with self.subTest([f"{n}:{sorted(kw)}" for n, kw in frames]):
                self.run_case(frames, stage, frontier, outcome)

    def test_every_frame_has_its_own_stage(self):
        t = self.run_case([("a", self.CLEAN_F), ("b", self.BUILDING), ("c", self.EMPTY), ("d", self.VERIFYING)],
                          "building", "b", "defects-remaining")
        self.assertEqual([f["stage"] for f in t["frames"]], ["clean", "building", "empty", "verifying"])
        self.assertEqual([f["dir"] for f in t["frames"]], [f"frames/{n}" for n in "abcd"],
                         "со второго кадра все лежат в frames/<имя>")

    def test_next_step_names_the_frontier_and_lists_the_rest(self):
        t = self.run_case([("a", self.VERIFYING), ("b", self.PLANNED), ("c", self.CLEAN_F), ("d", self.EMPTY)],
                          "verifying", "a", "defects-remaining")
        self.assertTrue(t["nextStep"].startswith("ui-verify: кадр «a» трека STOR-100"), t["nextStep"])
        self.assertIn("Далее по треку: b (planned), d (empty)", t["nextStep"])
        self.assertNotIn("c (clean)", t["nextStep"], "чистые кадры в хвосте не перечисляются")

    def test_state_frame_is_a_frame_like_any_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_track(tmp, [frame("main", **self.CLEAN_F), frame("err", role="state", state_of="main", build=["done"])])
            t = track_of(status(tmp))
            self.assertEqual((t["stage"], t["frame"]), ("built", "err"))
            self.assertEqual(t["frames"][1]["stateOf"], "main")
            self.assertEqual(t["frames"][1]["url"], PAGE, "состояние берёт адрес своего экрана")

    def test_outcome_is_a_function_of_stages(self):
        """X4: итог трека выводится из ступеней кадров одним правилом, на любых деревьях артефактов."""
        names = {"clean": self.CLEAN_F, "built": self.BUILT, "building": self.BUILDING, "verifying": self.VERIFYING,
                 "planned": self.PLANNED, "normalized": self.NORM, "requirement": self.REQ, "empty": self.EMPTY}
        for combo in itertools.product(names, repeat=2):
            with self.subTest(combo), tempfile.TemporaryDirectory() as tmp:
                make_track(tmp, [frame(f"f{i}", **names[s]) for i, s in enumerate(combo)])
                t = track_of(status(tmp))
                want_stage = next((s for s in combo if s != "clean"), "clean")
                want = ("clean" if all(s == "clean" for s in combo)
                        else "defects-remaining" if "verifying" in combo else "never-run")
                self.assertEqual((t["stage"], t["outcome"]), (want_stage, want))
                self.assertEqual([f["stage"] for f in t["frames"]], list(combo))


class Warnings(Env):
    """Предупреждения вслух: у каждого свой код и человекочитаемая строка (§15.7)."""

    def test_untroubled_tracks_are_silent(self):
        self.track(frame())
        self.assertEqual(self.one()[1]["warnings"], [])
        self.track(frame(build=DONE2, verify=CLEAN), key="STOR-2")
        self.assertEqual(status(self.root)["warnings"], [])

    def test_stale_requirement(self):
        self.track(frame(req="stale"))
        t, st = self.one()
        self.assertEqual(codes(st), ["requirement-stale"])
        text = warning(st, "requirement-stale")
        for part in ("«main»", TRACK, "изменён мимо pull", "daiver_pixso_pull"):
            self.assertIn(part, text)
        self.assertEqual(t["frames"][0]["requirementStatus"], "stale")
        self.assertIn("Сначала warnings: requirement-stale", t["nextStep"])

    def test_stale_requirement_of_a_built_frame_without_version_is_the_loudest(self):
        self.track(frame("a", raster=False), frame("b", req="stale", build=["done"], url=PAGE))
        st = status(self.root)
        self.assertEqual(codes(st), ["goal-image-missing", "requirement-stale-built"])
        self.assertTrue(st["warnings"][0].startswith("requirement-stale-built:"),
                        "громкое идёт первым, хотя кадр «a» раньше в манифесте")
        text = warning(st, "requirement-stale-built")
        for part in ("СОБРАН", "versions/", "«b»", "верни файл из git"):
            self.assertIn(part, text)

    def test_stale_built_frame_with_a_saved_version_is_an_ordinary_stale(self):
        self.track(frame(req="stale", build=["done"], versions=True))
        self.assertEqual(codes(self.one()[1]), ["requirement-stale"])

    def test_partially_built_frame_is_built_for_the_stale_rule(self):
        """«Собран» — как у pull: есть build-state.json (он и отказывает в перезаписи), а не «все секции done»."""
        self.track(frame(req="stale", build=["pending"]))
        self.assertEqual(codes(self.one()[1]), ["requirement-stale-built"])

    def test_missing_requirement_file_and_frame_without_requirement(self):
        self.track(frame(req="missing"))
        t, st = self.one()
        self.assertEqual(codes(st), ["requirement-missing"])
        self.assertIn("visual-requirements.pixso.json", warning(st, "requirement-missing"))
        self.assertEqual(t["frames"][0]["requirementStatus"], "missing")
        self.track(frame(req="none"), key="STOR-2")
        t2 = track_of(status(self.root), "STOR-2")
        self.assertEqual(t2["stage"], "empty")
        self.assertNotIn("requirement", t2["frames"][0], "записи требования нет — и поля нет")
        text = warning(status(self.root), "frame-no-requirement")
        self.assertIn("не может быть clean", text)

    def test_pull_without_frame_url(self):
        self.track(frame(frame_url=False))
        st = self.one()[1]
        self.assertEqual(codes(st), ["frame-url-missing"])
        self.assertIn("frameUrl", warning(st, "frame-url-missing"))

    def test_goal_image_variants(self):
        cases = [(True, None), (False, "нет goal image"), ("missing", "файла нет"), ("garbage", "не читается как PNG/JPEG")]
        for raster, phrase in cases:
            with self.subTest(raster), tempfile.TemporaryDirectory() as tmp:
                make_track(tmp, [frame(raster=raster)])
                st = status(tmp)
                self.assertEqual(track_of(st)["frames"][0]["goalImage"], phrase is None)
                if phrase is None:
                    self.assertEqual(codes(st), [])
                else:
                    self.assertEqual(codes(st), ["goal-image-missing"])
                    self.assertIn(phrase, warning(st, "goal-image-missing"))
                    self.assertIn("rasterPath", warning(st, "goal-image-missing"))

    def test_art_without_svg(self):
        self.track(frame(arts=["hero-banner", "logo"], svgs=["logo"]))
        st = self.one()[1]
        self.assertEqual(codes(st), ["art-svg-missing"])
        text = warning(st, "art-svg-missing")
        self.assertIn("hero-banner", text)
        self.assertNotIn("logo", text, "зарегистрированный арт не перечисляется")
        self.assertIn("daiver_svg_assets", text)

    def test_art_with_files_or_under_an_opaque_layer_is_silent(self):
        self.track(frame(arts=["hero", ("covered", True)], svgs=["hero"]))
        self.assertEqual(self.one()[1]["warnings"], [])

    def test_many_missing_arts_are_listed_briefly(self):
        self.track(frame(arts=[f"art-{i}" for i in range(6)]))
        text = warning(self.one()[1], "art-svg-missing")
        self.assertIn("art-0, art-1, art-2 и ещё 3", text)
        self.assertNotIn("art-5", text)

    def test_art_warning_survives_to_clean_and_is_silent_below_normalized(self):
        self.track(frame(arts=["hero"], build=DONE2, verify=CLEAN))
        t, st = self.one()
        self.assertEqual((t["stage"], codes(st)), ("clean", ["art-svg-missing"]), "placeholder не уезжает незамеченным")
        self.track(frame(arts=["hero"], req="missing"), key="STOR-2")
        self.assertEqual(codes(status(self.root)), ["art-svg-missing", "requirement-missing"],
                         "STOR-100 — выше; STOR-2 на ступени empty арты не читает")

    def test_canonical_built_from_another_export(self):
        self.track(frame(canon="stale"))
        st = self.one()[1]
        self.assertEqual(codes(st), ["canonical-stale"])
        self.assertIn("daiver_normalize", warning(st, "canonical-stale"))

    def test_url_not_recorded_after_built(self):
        cases = [
            ("built, адреса нет нигде", [frame(build=DONE2)], None, ["implementation-url-missing"]),
            ("built, есть implementation.url", [frame(build=DONE2)], DEFAULT_IMPL, []),
            ("built, адрес кадра", [frame(build=DONE2, url=PAGE)], None, []),
            ("planned без адреса — рано", [frame()], None, []),
            ("building без адреса — рано", [frame(build=["pending"])], None, []),
            ("verifying без адреса — адрес пинит verify-state", [frame(build=["done"], verify=DIRTY)], None, []),
            ("clean без адреса", [frame(build=["done"], verify=CLEAN)], None, []),
            ("диалог не наследует адрес трека",
             [frame(build=["done"], verify=CLEAN), frame("dlg", role="dialog", build=["done"])], DEFAULT_IMPL,
             ["implementation-url-missing"]),
            ("диалог со своим адресом",
             [frame(build=["done"], verify=CLEAN), frame("dlg", role="dialog", build=["done"], url=PAGE)], None, []),
            ("диалог первичным кадром тоже не наследует", [frame(role="dialog", build=DONE2)], DEFAULT_IMPL,
             ["implementation-url-missing"]),
            ("состояние берёт адрес экрана (url трека у первичного)",
             [frame(build=["done"], verify=CLEAN), frame("err", role="state", state_of="main", build=["done"])],
             DEFAULT_IMPL, []),
            ("состояние берёт собственный адрес экрана",
             [frame(build=["done"], verify=CLEAN, url=PAGE), frame("err", role="state", state_of="main", build=["done"])],
             None, []),
            ("второй экран не берёт адрес трека",
             [frame(build=["done"], verify=CLEAN), frame("two", build=["done"])], DEFAULT_IMPL,
             ["implementation-url-missing"]),
        ]
        for name, frames, impl, expect in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                make_track(tmp, frames, implementation=impl)
                st = status(tmp)
                self.assertEqual(codes(st), expect)
                if expect:
                    text = warning(st, "implementation-url-missing")
                    self.assertIn("daiver_track_manifest", text)

    def test_specs_not_in_place(self):
        self.track(frame(), story_spec=False)
        st = status(self.root)
        self.assertEqual(codes(st), ["specs-missing"])
        self.assertIn("docs/feature-pipeline/STOR-100/ui-spec.md", warning(st, "specs-missing"))
        T.call("daiver_spec", self.root, {"feature": TRACK})       # тот же путь, что видит daiver_spec
        files = T.call("daiver_spec", self.root, {"feature": TRACK})["files"]
        self.assertEqual([(f["path"], f["exists"]) for f in files], [("docs/feature-pipeline/STOR-100/ui-spec.md", False)])

    def test_registered_specs(self):
        self.track(frame(), registered={"docs/a.spec.md": False, "docs/b.spec.md": True})
        text = warning(status(self.root), "specs-missing")
        self.assertIn("docs/a.spec.md", text)
        self.assertNotIn("b.spec.md", text)
        self.assertNotIn("ui-spec.md", text, "ui-spec.md стори на месте")
        self.track(frame(), key="STOR-2", registered={"docs/c.spec.md": True})
        self.assertEqual([w for w in status(self.root)["warnings"] if "STOR-2" in w], [])
        self.track(frame(), key="STOR-3", registered={"docs/d.spec.md": True}, story_spec=False)
        text3 = warning_for(status(self.root), "specs-missing", "STOR-3")
        self.assertIn("ui-spec.md", text3)
        self.assertNotIn("d.spec.md", text3)


def warning_for(st, code, key) -> str:
    hits = [w for w in st["warnings"] if w.startswith(code + ":") and key in w]
    assert len(hits) == 1, (code, key, st["warnings"])
    return hits[0]


class DesignSystem(Env):
    DOCS = (".daiver/design-system-requirements.json", ".daiver/design-system-requirements.details.json")

    def test_project_not_initialized_has_no_design_system(self):
        st = status(self.root)
        self.assertIsNone(st["designSystem"])
        self.assertEqual(codes(st), [])

    def test_storybook_mcp_needs_no_documents(self):
        init_repo(self.root, "storybook-mcp")
        st = status(self.root)
        self.assertEqual(st["designSystem"], {"source": "storybook-mcp", "requirements": "not-needed"})
        self.assertEqual(codes(st), [])

    def test_stories_and_sources_without_documents(self):
        for source in ("stories", "sources"):
            with self.subTest(source), tempfile.TemporaryDirectory() as tmp:
                init_repo(tmp, source)
                st = status(tmp)
                self.assertEqual(st["designSystem"], {"source": source, "requirements": "missing"})
                self.assertEqual(codes(st), ["ds-requirements-missing"])
                text = warning(st, "ds-requirements-missing")
                for doc in self.DOCS:
                    self.assertIn(doc, text)
                self.assertIn(f"library.source: {source}", text)

    def test_documents_built_or_half_built(self):
        init_repo(self.root, "stories")
        _write(self.root / self.DOCS[0], "{}")
        st = status(self.root)
        self.assertEqual(st["designSystem"]["requirements"], "missing")
        text = warning(st, "ds-requirements-missing")
        self.assertIn("details", text)
        self.assertNotIn("requirements.json ", text.replace("requirements.details.json", ""),
                         "построенный документ не перечисляется")
        _write(self.root / self.DOCS[1], "{}")
        st = status(self.root)
        self.assertEqual(st["designSystem"], {"source": "stories", "requirements": "built"})
        self.assertEqual(codes(st), [])


class TrackLevel(Env):
    def test_track_without_manifest_and_manifest_without_frames(self):
        (self.root / ".daiver" / "tracks" / "EMPTY-1").mkdir(parents=True)
        TR.save(self.root, TR.new_manifest("EMPTY-2", created=NOW))
        st = status(self.root)
        for key in ("EMPTY-1", "EMPTY-2"):
            t = track_of(st, key)
            self.assertEqual((t["stage"], t["outcome"], t["frames"]), ("empty", "never-run", []))
            self.assertTrue(t["nextStep"].startswith("ui-design: "), t["nextStep"])
            self.assertIn("нет кадров", t["nextStep"])
            self.assertNotIn("frame", t)
        self.assertEqual(codes(st), [], "пустая папка трека — не повреждение")

    def test_corrupt_manifest_is_named_and_does_not_hide_other_tracks(self):
        self.track(frame(), key="GOOD-1")
        bad = {"синтаксис YAML": "frames: [\n  - name: x\n",
               "неизвестное поле": "track: BAD-1\nschema: 1\ncreated: x\nframes: []\ncolour: red\n",
               "чужое имя трека": "track: OTHER-1\nschema: 1\ncreated: x\nframes: []\n"}
        for what, text in bad.items():
            with self.subTest(what):
                _write(self.root / ".daiver" / "tracks" / "BAD-1" / "track.yaml", text)
                st = status(self.root)
                self.assertEqual(track_of(st, "GOOD-1")["stage"], "planned", "соседний трек жив")
                t = track_of(st, "BAD-1")
                self.assertEqual((t["stage"], t["frames"]), ("empty", []))
                self.assertIn("manifest-unreadable", t["nextStep"])
                self.assertTrue(t["nextStep"].startswith("ui-design: "))
                self.assertEqual(codes(st), ["manifest-unreadable"])
                self.assertIn("BAD-1", warning(st, "manifest-unreadable"))

    def test_only_valid_track_names_are_tracks(self):
        for bad in (".hidden", "bad name", "x" * 70):
            (self.root / ".daiver" / "tracks" / bad).mkdir(parents=True)
        _write(self.root / ".daiver" / "tracks" / "stray.txt", "x")
        self.track(frame())
        self.assertEqual([t["name"] for t in status(self.root)["tracks"]], [TRACK])

    def test_next_step_texts_are_tool_accurate(self):
        """Текст шага называет только то, что есть у движка к этой ступени; clean — «готово»."""
        texts = {}
        for stage, kw in (("empty", dict(req="none")), ("requirement", dict(canon=False)), ("normalized", dict(plan=False)),
                          ("planned", {}), ("building", dict(build=["pending"])), ("built", dict(build=["done"])),
                          ("verifying", dict(build=["done"], verify=DIRTY)), ("clean", dict(build=["done"], verify=CLEAN))):
            with tempfile.TemporaryDirectory() as tmp:
                make_track(tmp, [frame(**kw)])
                texts[stage] = track_of(status(tmp))["nextStep"]
        self.assertIn("daiver_pixso_pull", texts["empty"])
        self.assertIn("daiver_normalize", texts["requirement"])
        self.assertIn("плана нет", texts["normalized"])
        self.assertIn("подпись плана — решение человека", texts["normalized"])
        self.assertIn("после подписи плана", texts["planned"])
        self.assertIn("секций готово 0 из 1", texts["building"])
        self.assertIn("начать первую итерацию", texts["built"])
        self.assertNotIn("daiver_verify_state", texts["built"], "инструмента verify к ступени built может ещё не быть")
        self.assertIn("чинить / стоп", texts["verifying"])
        self.assertIn("блокирующих дефектов 2", texts["verifying"])
        self.assertEqual(len(set(texts.values())), len(texts))


class TopLevel(Env):
    def test_skeleton_answer_shape_is_kept(self):
        st = status(self.root)
        self.assertEqual(list(st), ["engine", "initialized", "config", "designSystem", "problems", "warnings",
                                    "tracks", "nextStep"])
        self.assertFalse(st["initialized"])
        self.assertIsNone(st["config"])
        self.assertEqual(st["tracks"], [])
        self.assertTrue(st["nextStep"].startswith("init: "), st["nextStep"])

    def test_initialized_without_tracks_points_at_ui_spec(self):
        init_repo(self.root)
        st = status(self.root)
        self.assertTrue(st["initialized"], st["problems"])
        self.assertEqual(st["nextStep"], ST.NEXT_SPEC)
        self.assertEqual(st["tracks"], [])
        self.assertEqual(st["config"]["librarySource"], "storybook-mcp")

    def test_uninitialized_project_lists_tracks_but_routes_to_init(self):
        self.track(frame())
        st = status(self.root)
        self.assertTrue(st["nextStep"].startswith("init: "))
        self.assertEqual([t["name"] for t in st["tracks"]], [TRACK])

    def test_all_tracks_clean(self):
        init_repo(self.root)
        self.track(frame(build=DONE2, verify=CLEAN), key="AAA-1")
        self.track(frame(build=DONE2, verify=CLEAN), key="BBB-2")
        st = status(self.root)
        self.assertTrue(st["nextStep"].startswith("все треки чистые"), st["nextStep"])
        self.assertIn(ST.NEXT_SPEC, st["nextStep"], "после чистых треков — новая стори")
        self.assertEqual([(t["stage"], t["outcome"]) for t in st["tracks"]], [("clean", "clean")] * 2)

    def test_top_level_next_step_is_the_first_open_track_by_name(self):
        init_repo(self.root)
        self.track(frame(build=DONE2, verify=CLEAN), key="AAA-1")
        self.track(frame(build=["done", "pending"]), key="BBB-2")
        self.track(frame(), key="CCC-3")
        st = status(self.root)
        own = {t["name"]: t["nextStep"] for t in st["tracks"]}
        self.assertTrue(own["AAA-1"].startswith("готово"))
        self.assertTrue(own["BBB-2"].startswith("ui-build: кадр «main» трека BBB-2"), own["BBB-2"])
        self.assertTrue(own["CCC-3"].startswith("ui-build: кадр «main» трека CCC-3"), own["CCC-3"])
        self.assertTrue(st["nextStep"].startswith(own["BBB-2"]), "верхний nextStep — первый незавершённый трек")
        self.assertIn("её ключа нет среди tracks[]", st["nextStep"])
        self.assertTrue(st["nextStep"].endswith("начни с ui-spec"), st["nextStep"])

    def test_tool_schema_still_takes_no_arguments(self):
        self.assertEqual(T.TOOLS["daiver_status"]["schema"]["properties"], {})
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_status", self.root, {"track": TRACK})
        self.assertIn("неизвестный аргумент", str(cm.exception))

    def test_init_embeds_the_same_status(self):
        _write(self.root / "package.json", "{}")
        self.track(frame(build=["pending"]))
        self.assertEqual(T.call("daiver_init", self.root, {})["status"], status(self.root))

    def test_every_warning_code_is_reachable(self):
        """Реестр кодов полон: один запутанный репо поднимает ВСЕ коды, громкое — первым."""
        init_repo(self.root, "stories")
        fs = [frame("f1", req="stale", build=["done"]),
              frame("f2", req="stale"),
              frame("f3", req="missing"),
              frame("f4", req="none"),
              frame("f5", frame_url=False),
              frame("f6", raster=False),
              frame("f7", arts=["x"]),
              frame("f8", build=["done"]),
              frame("f9", plan="nope"),
              frame("f10", canon="stale")]
        self.track(*fs, story_spec=False)
        _write(self.root / ".daiver" / "tracks" / "BAD-1" / "track.yaml", "frames: [\n")
        st = status(self.root)
        self.assertEqual(sorted(set(codes(st))), sorted(ST.WARNING_CODES))
        self.assertEqual(len(ST.WARNING_CODES), len(set(ST.WARNING_CODES)))
        self.assertEqual(st["warnings"][0].split(":")[0], "requirement-stale-built")
        for code in ST.WARNING_CODES:
            self.assertIn(code, ST.__doc__, "каждый код назван в докстринге модуля")


class ImplementationUrl(unittest.TestCase):
    """Адрес страницы реализации по ролям (§14.2): кадр → первичный берёт url трека → состояние берёт адрес
    экрана → диалог не наследует никогда."""
    U1, U2, U3 = "http://localhost/a", "http://localhost/b", "http://localhost/c"

    def test_matrix(self):
        U1, U2, U3 = self.U1, self.U2, self.U3
        st = {"role": "state", "stateOf": "main"}
        cases = [
            ([("main", {})], U1, "main", U1),
            ([("main", {"url": U2})], U1, "main", U2),
            ([("main", {})], None, "main", None),
            ([("main", {}), ("two", {})], U1, "two", None),
            ([("main", {}), ("two", {"url": U2})], U1, "two", U2),
            ([("main", {"role": "dialog"})], U1, "main", None),
            ([("main", {"role": "dialog", "url": U2})], U1, "main", U2),
            ([("main", {}), ("dlg", {"role": "dialog"})], U1, "dlg", None),
            ([("main", {}), ("dlg", {"role": "dialog", "url": U3})], U1, "dlg", U3),
            ([("main", {}), ("s", dict(st))], U1, "s", U1),
            ([("main", {"url": U2}), ("s", dict(st))], U1, "s", U2),
            ([("main", {}), ("s", dict(st, url=U3))], U1, "s", U3),
            ([("main", {}), ("two", {}), ("s", {"role": "state", "stateOf": "two"})], U1, "s", None),
            ([("main", {}), ("two", {"url": U2}), ("s", {"role": "state", "stateOf": "two"})], U1, "s", U2),
            ([("main", {"role": "dialog", "url": U2}), ("s", dict(st))], U1, "s", U2),
            ([("main", {"role": "dialog"}), ("s", dict(st))], U1, "s", None),
        ]
        for frames, impl, name, expect in cases:
            with self.subTest(frames=frames, impl=impl, frame=name):
                m = TR.new_manifest(TRACK, created=NOW)
                for n, fields in frames:
                    TR.upsert_frame(m, n, **fields)
                if impl:
                    m["implementation"] = {"url": impl}
                self.assertEqual(TR.implementation_url(m, name), expect)

    def test_default_frame_is_the_primary_one(self):
        m = TR.new_manifest(TRACK, created=NOW)
        TR.upsert_frame(m, "main")
        m["implementation"] = {"url": self.U1}
        self.assertEqual(TR.implementation_url(m), self.U1)
        with self.assertRaises(TR.TrackError):
            TR.implementation_url(m, "нет-такого")


class Contract(Env):
    def test_steps_come_from_the_forgeui_branch_registry(self):
        steps = branch_steps()
        for s in (ST.STEP_SPEC, ST.STEP_DESIGN, ST.STEP_BUILD, ST.STEP_VERIFY):
            self.assertIn(s, steps)
        self.assertEqual(set(ST.STEP_OF_STAGE), set(ST.LADDER))
        for stage, step in ST.STEP_OF_STAGE.items():
            self.assertTrue(step is None or step in steps, stage)
        self.assertEqual(ST.STEP_OF_STAGE["clean"], None)
        self.assertTrue(ST.NEXT_SPEC.startswith(ST.STEP_SPEC + ": "))

    def test_ladder_is_the_spec_ladder(self):
        self.assertEqual(ST.LADDER, ("empty", "requirement", "normalized", "planned", "building", "built",
                                     "verifying", "clean"))
        self.assertEqual(ST.OUTCOMES, ("clean", "defects-remaining", "never-run"))

    def test_response_is_byte_identical_for_the_same_artifacts(self):
        def build(tmp):
            init_repo(tmp, "stories")
            make_track(tmp, [frame("a", build=["done"], verify=CLEAN), frame("b", build=["pending"], arts=["hero"])])
            make_track(tmp, [frame(req="stale", build=DONE2)], key="OTHER-2", registered={"docs/x.spec.md": False})
            return json.dumps(status(tmp), ensure_ascii=False)
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            first, second = build(t1), build(t2)
            self.assertEqual(first, second, "в ответе нет ни путей temp-каталога, ни времени, ни случайности")
            self.assertEqual(json.dumps(status(t1), ensure_ascii=False), first, "повторный вызов — тот же ответ")

    def test_status_only_reads(self):
        init_repo(self.root, "stories")
        self.track(frame("a", build=["done"], verify=CLEAN, arts=["hero"]),
                   frame("b", req="stale", build=["pending"], plan="nope"))
        _write(self.root / ".daiver" / "tracks" / "BAD-1" / "track.yaml", "frames: [\n")

        def snap():
            return {p.relative_to(self.root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                    for p in sorted(self.root.rglob("*"))}
        before = snap()
        status(self.root)
        status(self.root)
        self.assertEqual(snap(), before, "статус не создаёт, не правит и не удаляет ничего")

    def test_response_stays_short_for_a_weak_reader(self):
        """MCP печатает indent=1: вложенный объект раскрывается построчно — ответ держим плоским и коротким."""
        self.track(frame(build=["done", "pending"]))
        small = json.dumps(status(self.root), ensure_ascii=False, indent=1).count("\n")
        self.assertLess(small, 50, small)
        with tempfile.TemporaryDirectory() as tmp:
            make_track(tmp, [frame(n, build=["done"]) for n in "abcd"])
            big = json.dumps(status(tmp), ensure_ascii=False, indent=1).count("\n")
            self.assertLess(big, 110, big)


class SampleFlow(Env):
    """Образец «Modal» (настоящий экспорт Pixso) проходит лестницу: pull → normalize → goal image → state-файлы."""

    def test_modal_sample_climbs_the_ladder(self):
        key = "STOR-1"
        _write(self.root / "docs" / "feature-pipeline" / key / "ui-spec.md", "# spec\n")
        shutil.copy(str(FIXTURE), str(self.root / "dsl.json"))
        T.call("daiver_pixso_pull", self.root, {"track": key, "dslPath": "dsl.json", "frameUrl": URL})
        st = status(self.root)
        t = track_of(st, key)
        self.assertEqual((t["stage"], t["frame"], codes(st)), ("requirement", "main", ["goal-image-missing"]))
        self.assertEqual(t["frames"][0], {"name": "main", "role": "screen", "stage": "requirement", "dir": ".",
                                         "requirement": "pixso", "goalImage": False})
        T.call("daiver_normalize", self.root, {"track": key})
        t = track_of(status(self.root), key)
        self.assertEqual(t["stage"], "normalized")
        self.assertIn("плана нет", t["nextStep"])
        _write(self.root / "goal.png", K.png_bytes(648, 293))
        T.call("daiver_track_manifest", self.root, {"track": key, "frame": "main", "rasterPath": "goal.png"})
        st = status(self.root)
        self.assertEqual(st["warnings"], [], "канон образца — без артов, goal image и спека на месте")
        self.assertTrue(track_of(st, key)["frames"][0]["goalImage"])
        m = TR.load(self.root, key)
        paths = TR.frame_paths(self.root, m, "main")
        _write(paths["plan"], {"buildOrder": []})
        self.assertEqual(track_of(status(self.root), key)["stage"], "planned")
        _write(paths["buildState"], _build_value(["pending"]))
        self.assertEqual(track_of(status(self.root), key)["stage"], "building")
        _write(paths["buildState"], _build_value(["done"]))
        st = status(self.root)
        self.assertEqual((track_of(st, key)["stage"], codes(st)), ("built", ["implementation-url-missing"]))
        T.call("daiver_track_manifest", self.root, {"track": key, "set": {"implementation": {"url": PAGE}}})
        st = status(self.root)
        self.assertEqual((track_of(st, key)["stage"], codes(st)), ("built", []))
        self.assertEqual(track_of(st, key)["frames"][0]["url"], PAGE)
        _write(paths["verifyState"], _verify_value(DIRTY))
        t = track_of(status(self.root), key)
        self.assertEqual((t["stage"], t["outcome"]), ("verifying", "defects-remaining"))
        _write(paths["verifyState"], _verify_value(DIRTY + CLEAN))
        t = track_of(status(self.root), key)
        self.assertEqual((t["stage"], t["outcome"]), ("clean", "clean"))
        # требование тронули мимо pull: кадр собран, версии нет — громче всего, а ступень не понижается
        paths["requirement"].write_text(paths["requirement"].read_text(encoding="utf-8") + " ", encoding="utf-8")
        st = status(self.root)
        self.assertEqual(track_of(st, key)["stage"], "clean")
        self.assertEqual(codes(st), ["requirement-stale-built"])
        self.assertTrue(st["warnings"][0].startswith("requirement-stale-built:"))


if __name__ == "__main__":
    unittest.main()
