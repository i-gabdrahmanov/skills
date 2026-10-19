#!/usr/bin/env python3
"""test_ui_gate_build.py — гейт шага ui-build: `ui_gate.py build` (задача 3.2; спека §13.3 п.4, §13.8; INV-C20).

Пинит как свойства КОДА (не текста брифа; buildCommand в тестах — скрипт-заглушка, настоящего npm/tsc нет):
  • порядок: сначала секции (build-state есть, сверен с планом, ничего не pending / in_progress / stale, outputs на диске), потом
    команда; пока секции не готовы или команда не задана, `buildCommand` НЕ запускается (след-маркер заглушки не появляется);
  • stale-секция — «не готово»: макет изменился после сборки, пересобрать нужно ту секцию, и гейт зелёным не бывает; пропавший output и
    отсутствие состояния — тоже; кадр трека без канона/состояния называется по имени; секция без кода (корень решён ignore) гейт не держит;
  • команда: запускается из корня репо, как строка оболочки (`a && b`, кавычки), stdin закрыт (вопрос не повесит гейт), зелёный
    (exit 0) → exit гейта 0, красный → 1 с кодом и ХВОСТОМ вывода в stderr последним (раннер record_gate берёт конец вывода);
    таймаут → 1, команда убита вместе с потомками; гигантский вывод не раздувает ответ; ANSI и «\\r» хвоста вычищены;
  • гейт ничего не пишет (дерево до и после побайтно равно), `verify` по-прежнему exit 3, неверные аргументы — exit 2;
  • «Modal» из Pixso: полный цикл (pull → резолв → план → build-state → секция → done) и зелёный гейт.

Данные. «Modal» — настоящий образец Pixso (одна секция); экраны на несколько секций — СИНТЕТИКА из построителей test_build_state.py.
Что НЕ проверено данными: настоящая сборка фронт-репо (npm run build / tsc / vite), поведение на Windows (taskkill), время сборки на
боевом проекте — таймаут 900 с подобран, не измерен (X.4).
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
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

import test_build_state as TB  # noqa: E402 — построители синтетического экрана (panel, screen, build_repo, set_canon)
from daiver import build_gate as BG  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver import tools as T  # noqa: E402

GATE = HERE / "ui_gate.py"
TRACK = TB.TRACK
STUB = r'''
import os, subprocess, sys, time
from pathlib import Path
mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "ok":
    print("build ok")
elif mode == "ran":                       # доказательство запуска и места запуска
    Path("build-ran.txt").write_text(Path.cwd().name, encoding="utf-8")
elif mode == "fail":
    for i in range(100):
        print("LINE", i)
    sys.exit(3)
elif mode == "ansi":
    sys.stdout.write("\x1b[31mred\x1b[0m\rprogress 50%\rprogress 100%\n")
    print("error TS2304: Cannot find name 'Foo'.")
    sys.exit(2)
elif mode == "slow":
    Path(sys.argv[2]).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(60)
elif mode == "tree":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(sys.argv[2]).write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
    time.sleep(60)
elif mode == "stdin":
    sys.exit(7 if sys.stdin.read() == "" else 0)
elif mode == "big":
    for _ in range(20000):
        sys.stdout.write("x" * 1023 + "\n")
    sys.stdout.write("THE END\n")
    sys.exit(1)
elif mode == "stderr":
    sys.stderr.write("from stderr\n")
    sys.exit(1)
'''


def snapshot(root: Path) -> Dict[str, Any]:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def gone_soon(pids: List[int], seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not any(alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


class Gate(unittest.TestCase):
    """Репо с треком STOR-100, кадром main (три панели-секции p1, p2, p3), настоящим планом и заглушкой вместо buildCommand."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stub = self.base / "stub_build.py"
        self.stub.write_text(STUB, encoding="utf-8")
        self.root = self.base / "repo"
        self.root.mkdir()

    def cmd(self, mode: str = "ok", *args: str) -> str:
        return " ".join([f'"{sys.executable}"', "-X", "utf8", f'"{self.stub.as_posix()}"', mode, *args])

    def world(self, command: Optional[str] = "UNSET", node: Optional[dict] = None, root: Optional[Path] = None,
              verdicts: Optional[Dict[str, str]] = None) -> Path:
        root = root or self.root
        command = self.cmd("ran") if command == "UNSET" else command
        (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text("{}", encoding="utf-8")
        answers = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules", "librarySource": "stories",
                   "scanPaths": ["lib"], "buildCommand": command or "npm run build"}
        res = I.init(root, answers)
        self.assertTrue(res["ok"], res)
        if command is None:                              # daiver_init команду требует; без неё конфиг бывает только после правки руками
            cfg = root / ".daiver" / "config.yaml"
            cfg.write_text(re.sub(r"(?m)^  buildCommand:.*$", "  buildCommand: null", cfg.read_text(encoding="utf-8")), encoding="utf-8")
        TB.build_repo(root, node if node is not None else TB.screen())
        if verdicts:
            TB.decide(root, verdicts)
        TB.replan_now(root)
        return root

    def call(self, name: str, root: Optional[Path] = None, **args):
        return T.call(name, root or self.root, dict(args, track=TRACK))

    def build_section(self, sid: str, root: Optional[Path] = None) -> None:
        root = root or self.root
        self.call("daiver_build_state_set", root, sectionId=sid, status="in_progress")
        h = self.call("daiver_section", root, sectionId=sid, weightBudget=TB.BUDGET)["section"]["hash"]
        rel = f"src/modules/orders/{sid.upper()}.tsx"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("export {};\n", encoding="utf-8")
        self.call("daiver_build_state_set", root, sectionId=sid, status="done", outputs=[rel], hash=h)

    def built(self, command: Optional[str] = "UNSET", ids=("p1", "p2", "p3")) -> Path:
        self.world(command)
        self.call("daiver_build_state")
        for sid in ids:
            self.build_section(sid)
        return self.root

    def gate(self, *extra: str, root: Optional[Path] = None, feature: str = TRACK) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([sys.executable, "-X", "utf8", str(GATE), "build", "--project", str(root or self.root), "--feature", feature,
                               *extra], input="непрочитанный ввод\n", capture_output=True, text=True, encoding="utf-8", timeout=120)

    def marker(self) -> Path:
        return self.root / "build-ran.txt"


# ── порядок: секции, потом команда ───────────────────────────────────────────

class Order(Gate):
    def test_all_sections_built_and_a_green_command_is_a_green_gate(self):
        self.built()
        before = snapshot(self.root)
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("✓ ui-build", r.stdout)
        self.assertIn("main", r.stdout)
        self.assertIn("exit 0", r.stdout)
        self.assertEqual(self.marker().read_text(encoding="utf-8"), "repo", "команда запущена из корня репо")
        after = snapshot(self.root)
        after.pop("build-ran.txt")
        self.assertEqual(after, before, "гейт сам ничего не пишет: всё, что появилось, — след команды")

    def test_a_pending_section_means_not_ready_and_the_command_is_not_even_started(self):
        self.world()
        self.call("daiver_build_state")
        self.build_section("p1")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("шаг не готов к закрытию (1)", r.stderr)
        self.assertIn("секций не готово 2 из 3: p2 (pending), p3 (pending)", r.stderr)
        self.assertFalse(self.marker().exists(), "незавершённую сборку не компилируют")

    def test_a_stale_section_is_not_ready_even_though_everything_was_built(self):
        self.built()
        self.assertEqual(self.gate().returncode, 0)
        self.marker().unlink()
        TB.set_canon(self.root, TB.screen(edited=("p2",)))
        TB.replan_now(self.root)
        self.call("daiver_build_state")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("p2 (stale)", r.stderr)
        self.assertFalse(self.marker().exists())
        self.build_section("p2")                       # перестроена на месте — гейт снова зелёный
        self.assertEqual(self.gate().returncode, 0)

    def test_a_replanned_but_not_reconciled_state_is_not_ready(self):
        self.built()
        TB.set_canon(self.root, TB.screen(ids=("p1", "p2", "p3", "p4")))
        TB.replan_now(self.root)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("не сверен с планом", r.stderr)
        self.assertFalse(self.marker().exists())

    def test_a_plan_that_no_longer_matches_the_mockup_is_named(self):
        self.built()
        TB.set_canon(self.root, TB.screen(edited=("p1", "p2")))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("план не соответствует макету", r.stderr)
        self.assertFalse(self.marker().exists())

    def test_a_vanished_output_blocks_the_gate(self):
        self.built()
        (self.root / "src/modules/orders/P3.tsx").unlink()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("outputs, которых нет на диске (1): p3: src/modules/orders/P3.tsx", r.stderr)
        self.assertFalse(self.marker().exists())

    def test_no_state_at_all_is_reported_with_the_way_to_create_it(self):
        self.world()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("build-state нет", r.stderr)
        self.assertIn("daiver_build_state", r.stderr)

    def test_a_missing_build_command_is_a_problem_of_its_own_and_nothing_runs(self):
        self.built(command=None)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("project.buildCommand не задан", r.stderr)
        self.assertIn("не угадывай", r.stderr)
        self.assertFalse(self.marker().exists())

    def test_sections_and_command_problems_are_listed_together(self):
        self.world(command=None)
        r = self.gate()
        self.assertIn("шаг не готов к закрытию (2)", r.stderr)

    def test_no_config_and_no_track_are_named_not_raised(self):
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("трека «STOR-100» нет", r.stderr)
        self.assertIn("нет .daiver/config.yaml", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_every_frame_of_the_track_must_be_built_and_the_unbuilt_one_is_named(self):
        self.built()
        m = TR.load(self.root, TRACK)
        TR.upsert_frame(m, "second", frameUrl=TB.URL, requirement={"kind": "pixso", "path": "visual-requirements.pixso.json",
                                                                    "sha256": TB.SHA, "pulledAt": "2026-10-15T12:00:00Z"})
        TR.save(self.root, m)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("second", r.stderr)
        self.assertFalse(self.marker().exists())

    def test_a_section_without_code_does_not_hold_the_gate(self):
        self.world(node=TB.decor_screen(named=("p2",)), verdicts={"p2": "ignore"})
        out = self.call("daiver_build_state")
        self.assertEqual(out["noCode"], ["p2"])
        self.build_section("p1")
        self.build_section("p3")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marker().read_text(encoding="utf-8"), "repo")


# ── команда: зелёная, красная, таймаут ───────────────────────────────────────

class Command(Gate):
    def test_a_red_command_is_a_red_gate_with_the_exit_code_and_the_tail_last(self):
        self.built(self.cmd("fail"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("завершилась с кодом 3", r.stderr)
        self.assertIn("проект не собирается", r.stderr)
        self.assertIn("не спрашивая человека", r.stderr, "§13.8: красный билд чинит сам субагент")
        self.assertTrue(r.stderr.rstrip().endswith("LINE 99"), "хвост вывода — последнее, что видит раннер")
        self.assertIn("LINE 60", r.stderr)
        self.assertNotIn("LINE 10\n", r.stderr, "хвост ограничен последними строками")
        self.assertNotIn("✓", r.stdout)

    def test_the_fix_cycle_a_red_gate_turns_green_when_the_command_does(self):
        self.built(self.cmd("fail"))
        self.assertEqual(self.gate().returncode, 1)
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace(" fail", " ok"), encoding="utf-8")
        self.assertEqual(self.gate().returncode, 0)

    def test_stderr_of_the_command_is_in_the_tail_too(self):
        self.built(self.cmd("stderr"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("from stderr", r.stderr)

    def test_ansi_colors_and_carriage_returns_are_cleaned_from_the_tail(self):
        self.built(self.cmd("ansi"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("\x1b", r.stderr)
        self.assertNotIn("\r", r.stderr)
        self.assertIn("progress 100%\nerror TS2304", r.stderr)

    def test_the_shell_line_is_run_as_a_shell_line(self):
        self.built(self.cmd("ok") + " && " + self.cmd("fail"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("LINE 99", r.stderr)
        self.assertIn("завершилась с кодом 3", r.stderr)

    def test_stdin_is_closed_so_a_question_cannot_hang_the_gate(self):
        self.built(self.cmd("stdin"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("завершилась с кодом 7", r.stderr, "команда увидела пустой stdin, хотя гейту на вход подали текст")

    def test_a_huge_output_does_not_swell_the_answer(self):
        self.built(self.cmd("big"))
        started = time.monotonic()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.rstrip().endswith("THE END"))
        self.assertLess(len(r.stderr), 6000)
        self.assertLess(time.monotonic() - started, 60)

    @unittest.skipUnless(os.name == "posix", "убийство дерева процессов проверено на POSIX")
    def test_a_timeout_is_a_red_gate_and_the_command_is_killed_with_its_children(self):
        pidfile = self.base / "pids.txt"
        self.built(self.cmd("tree", f'"{pidfile.as_posix()}"'))
        started = time.monotonic()
        r = self.gate("--timeout", "4")                    # с запасом на холодный старт интерпретатора под нагрузкой
        self.assertEqual(r.returncode, 1)
        self.assertIn("не уложилась в 4 с и остановлена", r.stderr)
        self.assertLess(time.monotonic() - started, 40)
        pids = [int(x) for x in pidfile.read_text(encoding="utf-8").split()]
        self.assertEqual(len(pids), 2)
        self.assertTrue(gone_soon(pids), f"процессы команды живы после таймаута: {pids}")

    @unittest.skipUnless(os.name == "posix", "убийство дерева процессов проверено на POSIX")
    def test_run_command_reports_the_timeout_without_an_exit_code(self):
        pidfile = self.base / "pid.txt"
        res = BG.run_command(self.cmd("slow", f'"{pidfile.as_posix()}"'), self.base, 3)
        self.assertEqual((res["exit"], res["timedOut"]), (None, True))
        self.assertTrue(gone_soon([int(pidfile.read_text(encoding="utf-8"))]))

    def test_run_command_reports_exit_codes_and_a_start_failure(self):
        self.assertEqual(BG.run_command(self.cmd("ok"), self.base, 30)["exit"], 0)
        self.assertEqual(BG.run_command(self.cmd("fail"), self.base, 30)["exit"], 3)
        res = BG.run_command("true", self.base / "no-such-dir", 30)
        self.assertEqual((res["exit"], res["timedOut"]), (None, False))
        self.assertIn("не запустилась", res["tail"])


# ── хвост ────────────────────────────────────────────────────────────────────

class Tail(unittest.TestCase):
    def test_ansi_carriage_returns_and_trailing_blank_lines(self):
        raw = "\x1b[31mred\x1b[0m\rprogress 1%\rprogress 100%\r\nerror\n\n\n".encode("utf-8")
        self.assertEqual(BG.clean_tail(raw), "red\nprogress 1%\nprogress 100%\nerror")

    def test_only_the_last_lines_are_kept_and_the_cut_is_marked(self):
        raw = "".join(f"line {i}\n" for i in range(BG.TAIL_LINES + 5)).encode("utf-8")
        tail = BG.clean_tail(raw)
        self.assertTrue(tail.startswith("…\nline 5\n"))
        self.assertTrue(tail.endswith(f"line {BG.TAIL_LINES + 4}"))
        self.assertEqual(len(tail.splitlines()), BG.TAIL_LINES + 1)
        self.assertFalse(BG.clean_tail(b"a\nb\n").startswith("…"))

    def test_a_long_tail_is_cut_by_characters_at_a_line_boundary(self):
        raw = "".join(f"{i:04d} " + "y" * 98 + "\n" for i in range(40)).encode("utf-8")        # 4000 знаков
        tail = BG.clean_tail(raw)
        self.assertLessEqual(len(tail), BG.TAIL_CHARS + 3)
        self.assertTrue(tail.startswith("…\n"))
        self.assertTrue(tail.splitlines()[1].startswith(("00", "0")), tail[:40])
        self.assertTrue(all(len(ln) == 103 for ln in tail.splitlines()[1:]), "строки не рвутся пополам")

    def test_one_giant_line_and_bytes_that_are_not_utf8(self):
        self.assertLessEqual(len(BG.clean_tail(b"z" * 100000)), BG.TAIL_CHARS + 3)
        self.assertIn("�", BG.clean_tail(b"ok \xff\xfe bad"))
        self.assertEqual(BG.clean_tail(b""), "")


# ── аргументы, прочие шаги, гигиена ──────────────────────────────────────────

class Cli(Gate):
    def test_bad_arguments_are_exit_2_and_verify_still_stops_honestly(self):
        r = subprocess.run([sys.executable, "-X", "utf8", str(GATE), "build", "--project", str(self.root)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.gate("--timeout", "0").returncode, 2)
        self.assertEqual(self.gate("--timeout", "-5").returncode, 2)
        v = subprocess.run([sys.executable, "-X", "utf8", str(GATE), "verify", "--project", str(self.root), "--feature", TRACK],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(v.returncode, 3)
        self.assertIn("ui-verify", v.stderr)

    def test_the_gate_command_of_the_pipeline_matches_what_record_gate_expects(self):
        policy = json.loads((HERE.parents[2] / "hooks" / "risk-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["gate_cmd_expect"]["ui-build"], ["ui_gate.py build"])

    def test_the_runner_records_a_red_and_a_green_gate_as_it_happened(self):
        runner = HERE.parents[2] / "skills" / "pipeline-state" / "scripts" / "record_gate.py"
        self.built(self.cmd("fail"))
        cmd = f"{sys.executable} -X utf8 {GATE} build --project {self.root} --feature {TRACK}"          # как в брифе: без кавычек
        args = [sys.executable, "-X", "utf8", str(runner), "--project", str(self.root), "--skill", "forgeui", "--feature", TRACK,
                "--step-id", "ui-build", "--cmd", cmd]
        red = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(red.returncode, 1, red.stdout + red.stderr)
        self.assertIn("ui-build: FAILED", red.stdout)
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace(" fail", " ok"), encoding="utf-8")
        green = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertIn("ui-build: PASSED", green.stdout)


class BuildCommand(Gate):
    def test_the_command_is_taken_from_the_config_as_written(self):
        self.world("npm run build && npx tsc --noEmit")
        self.assertEqual(BG.build_command(self.root), ("npm run build && npx tsc --noEmit", None))

    def test_no_config_and_no_command_have_their_own_words(self):
        self.assertIn("не инициализирован", BG.build_command(self.root)[1])
        self.world(command=None)
        cmd, why = BG.build_command(self.root)
        self.assertIsNone(cmd)
        self.assertIn("project.buildCommand не задан", why)


class ModalSample(Gate):
    """«Modal» из Pixso: pull → normalize → резолв четырёх кандидатов → план → сборка единственной секции → зелёный гейт."""

    def test_the_sample_builds_in_one_cycle_and_the_gate_goes_green(self):
        root = self.root
        (root / "src" / "modules").mkdir(parents=True)
        (root / "package.json").write_text("{}", encoding="utf-8")
        res = I.init(root, {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules", "librarySource": "stories",
                            "scanPaths": ["lib"], "buildCommand": self.cmd("ran")})
        self.assertTrue(res["ok"], res)
        TB.write_ds(root, ["ModalHeader", "ModalAction"])
        inbox = root / ".daiver" / "inbox"
        inbox.mkdir(parents=True)
        shutil.copyfile(str(TB.FIXTURE), str(inbox / "main.dsl.json"))
        self.call("daiver_pixso_pull", dslPath=".daiver/inbox/main.dsl.json", frameUrl=TB.URL, frame="main")
        self.call("daiver_normalize")
        fin = lambda v: self.call("daiver_resolve_finalize", verdict=v)                      # noqa: E731
        fin({"nodeId": "2:153702", "decision": "local", "confidence": "high", "rationale": "оболочка экрана"})
        fin({"nodeId": "2:153703", "decision": "component", "component": "ModalHeader", "confidence": "high", "rationale": "шапка"})
        fin({"nodeId": "2:153704", "decision": "component", "component": "ModalAction", "confidence": "high", "rationale": "действия"})
        self.call("daiver_resolve_report")
        self.call("daiver_plan_write")
        self.assertEqual(self.gate().returncode, 1, "до сборки гейт красный")
        self.call("daiver_build_state")
        self.call("daiver_build_state_set", sectionId="2:153702", status="in_progress")
        h = self.call("daiver_section", sectionId="2:153702", weightBudget=24000)["section"]["hash"]
        files = ["src/modules/modal/Modal.tsx", "src/modules/modal/mocks.ts"]
        for f in files:
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_text("export {};\n", encoding="utf-8")
        self.assertEqual(self.gate().returncode, 1, "секция начата, но не закрыта")
        self.call("daiver_build_state_set", sectionId="2:153702", status="done", outputs=files, hash=h)
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.marker().is_file())


class Hygiene(unittest.TestCase):
    PATHS = (HERE / "daiver" / "build_gate.py", HERE / "ui_gate.py")

    def test_modules_keep_the_python_floor_and_the_encoding_rule_and_use_no_time_or_randomness(self):
        for path in self.PATHS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("from __future__ import annotations", text, path.name)
            tree = ast.parse(text, filename=str(path))
            imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
            self.assertFalse(imported & {"time", "datetime", "random", "uuid", "secrets"}, (path.name, sorted(imported)))
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("read_text", "write_text"):
                    self.assertTrue(any(k.arg == "encoding" for k in n.keywords), f"{path.name}:{n.lineno}: без encoding=")
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
                    self.fail(f"{path.name}:{n.lineno}: open()")
                self.assertNotIsInstance(n, ast.NamedExpr, f"{path.name}:{getattr(n, 'lineno', '?')}: walrus")

    def test_the_gate_module_writes_no_project_files(self):
        text = (HERE / "daiver" / "build_gate.py").read_text(encoding="utf-8")
        for needle in (".write_text(", ".write_bytes(", "os.environ", "shutil", "unlink", "rmtree"):
            self.assertNotIn(needle, text, f"гейт только читает: {needle}")


if __name__ == "__main__":
    unittest.main()
