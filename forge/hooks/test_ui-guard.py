#!/usr/bin/env python3
"""Тесты hooks/ui-guard.py — запреты daiver (слой F, §18.6) кодом: задача 3.3, INV F1, F2, F5, F12, C8, C9;
QG N1, N3, N6, N9, N12.

Каждый запрет проверяется вместе с РАЗРЕШЁННЫМ двойником (иначе тест удовлетворил бы хук, запрещающий всё):

  • чтение канона и сырых экспортов (read_file / grep_search / read_many_files / shell-читатели) — запрет;
    план, конфиг, спека, исходники, перечисление и измерение (ls, stat, wc, file, shasum) — можно;
  • запись кода модуля (`project.outputDir`) — только на `ui-build`; на `ui-verify` — отказ с подсказкой про
    переоткрытие `ui-build`; запись вне outputDir и в документы задачи — можно;
  • запись на `ui-build` — только при действительной подписи плана: подписан → можно; не подписан, подпись
    устарела (перетяжка макета, правка plan.json руками, правка спеки, отозванное согласие, согласие без
    привязки) → отказ; пересбор плана и новая подпись возвращают запись;
  • инлайн-`<svg` в кодовых файлах outputDir — отказ; компонент библиотеки и помеченный плейсхолдер арта — можно.

Мир — НАСТОЯЩИЙ образец «Modal» (Pixso) с подписанным планом из `test_plan_contract.ModalWorld`: подпись
здесь проверяет не заглушка, а `plan_contract.signature_state`. Сырой экспорт образца лежит в
`.daiver/inbox/main.dsl.json` — именно туда его кладёт бриф ui-design, поэтому по голове файла хук обязан
узнавать его и там, и под любым другим именем. Кеш подписи проверяется поведением: любое изменение входа
(в том числе подгонка mtime при тех же размере и содержимом по длине) снимает старый вердикт.

Что НЕ проверено данными: боевой прогон на GigaCode (как слабая модель читает отказы), настоящие имена
параметров читающих инструментов рантайма сверх `file_path`/`path`/`absolute_path`, Windows.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

HOOKS = Path(__file__).resolve().parent
HOOK = HOOKS / "ui-guard.py"
REPO = HOOKS.parent
SCRIPTS = REPO / "skills" / "forgeui" / "scripts"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(SCRIPTS))

import test_plan_contract as TPC  # noqa: E402  — мир «Modal» с подписанным планом
from daiver import plan_contract as PC  # noqa: E402
from daiver import svg_assets as SA  # noqa: E402
from daiver import tools as T  # noqa: E402

KEY = TPC.TRACK
TRACK_DIR = f".daiver/tracks/{KEY}"
CANON = f"{TRACK_DIR}/visual-requirements.canonical.json"
SLIM = f"{TRACK_DIR}/visual-requirements.pixso.json"
RAW = f"{TRACK_DIR}/visual-requirements.pixso.raw.json"
DSL = ".daiver/inbox/main.dsl.json"
MODULE = "src/modules/Modal"
CODE = f"{MODULE}/Modal.tsx"
STEPS = ["ui-spec", "ui-design", "ui-build", "ui-verify"]


def _load_hook():
    spec = importlib.util.spec_from_file_location("ui_guard_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class UiWorld(TPC.ModalWorld):
    """Подписанный план «Modal» + активный прогон forgeui (шаг ui-build) + корень проекта (.git)."""

    def setUp(self):
        super().setUp()
        self.modal()
        (self.root / ".git").mkdir(exist_ok=True)
        self.step("ui-build")

    # — прогон и шаги —
    def run_dir(self) -> Path:
        return self.root / "ground" / "statements" / "forgeui" / KEY

    def step(self, current: Optional[str], skill: str = "forgeui", steps=None) -> None:
        """Манифест прогона: все шаги до `current` закрыты; None — закрыты все."""
        ids = steps or STEPS
        out, done, prev = [], True, None
        for sid in ids:
            if sid == current:
                done = False
            out.append({"id": sid, "status": "completed" if done else "pending", "depends_on": [prev] if prev else []})
            prev = sid
        d = self.root / "ground" / "statements" / skill / KEY
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"feature": KEY, "steps": out}), encoding="utf-8")

    def other_branch(self, skill: str, steps, current: str) -> None:
        shutil.rmtree(self.root / "ground" / "statements", ignore_errors=True)
        self.step(current, skill=skill, steps=steps)

    # — запуск хука как рантайм —
    def hook(self, tool: str, tool_input: Dict[str, Any], cwd: Optional[Path] = None,
             root: Optional[Path] = None) -> "subprocess.CompletedProcess[str]":
        payload = {"hook_event_name": "PreToolUse", "cwd": str(cwd or root or self.root), "tool_name": tool,
                   "tool_input": tool_input}
        return subprocess.run([sys.executable, "-X", "utf8", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def abs(self, rel: str) -> str:
        return str(self.root / rel)

    def write(self, rel: str = CODE, content: str = "export const Modal = () => null;\n", tool: str = "write_file"):
        return self.hook(tool, {"file_path": self.abs(rel), "content": content})

    def read(self, rel: str, tool: str = "read_file"):
        return self.hook(tool, {"file_path": self.abs(rel)})

    def sh(self, command: str, cwd: Optional[Path] = None):
        return self.hook("run_shell_command", {"command": command}, cwd=cwd)

    def put(self, rel: str, text: Any) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
        return p

    def allowed(self, r: "subprocess.CompletedProcess[str]", msg: str = "") -> None:
        self.assertEqual(r.returncode, 0, f"{msg}\n{r.stderr}")

    def denied(self, r: "subprocess.CompletedProcess[str]", *needles: str) -> None:
        self.assertEqual(r.returncode, 2, f"{r.stdout}\n{r.stderr}")
        self.assertIn("[ui-guard] DENY", r.stderr)
        for n in needles:
            self.assertIn(n, r.stderr)


# ── noop вне прогона forgeui ──────────────────────────────────────────────────

class Noop(UiWorld):
    """Вне прогона forgeui хук ничего не делает — ни чтения, ни записи, ни svg."""

    def probes(self):
        return [("read_file", {"file_path": self.abs(CANON)}),
                ("grep_search", {"pattern": "x", "path": self.abs(TRACK_DIR)}),
                ("run_shell_command", {"command": f"cat {CANON}"}),
                ("write_file", {"file_path": self.abs(CODE), "content": "<svg><path/></svg>"}),
                ("edit", {"file_path": self.abs(CODE), "old_string": "a", "new_string": "<svg>"}),
                ("run_shell_command", {"command": f"echo '<svg>' > {CODE}"})]

    def test_no_run_at_all(self):
        shutil.rmtree(self.root / "ground" / "statements")
        for tool, ti in self.probes():
            with self.subTest(tool=tool, ti=str(ti)[:40]):
                self.allowed(self.hook(tool, ti))

    def test_other_branches_are_noop(self):
        for skill, steps, cur in (("feature-pipeline", ["01-grounding", "04-build-T1"], "04-build-T1"),
                                  ("forgefix", ["fix-intake", "fix-green"], "fix-green"),
                                  ("forgetech", ["tech-intake", "tech-apply"], "tech-apply")):
            self.other_branch(skill, steps, cur)
            for tool, ti in self.probes():
                with self.subTest(skill=skill, tool=tool):
                    self.allowed(self.hook(tool, ti))

    def test_unrelated_tools_and_garbage_input_inside_a_live_run(self):
        for tool, ti in (("glob", {"pattern": "**/*.json"}), ("web_fetch", {"url": "https://example.com"}),
                         ("mcp__forge-ui__daiver_outline", {"track": KEY}), ("list_directory", {"path": TRACK_DIR})):
            with self.subTest(tool=tool):
                self.allowed(self.hook(tool, ti))
        for raw in ("", "not json", "[]", '"x"', '{"tool_name": "read_file", "tool_input": []}'):
            with self.subTest(raw=raw):
                r = subprocess.run([sys.executable, "-X", "utf8", str(HOOK)], input=raw, capture_output=True, text=True,
                                   encoding="utf-8", timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)


# ── 1. чтение канона и сырых экспортов (N3, N12) ──────────────────────────────

class ReadBan(UiWorld):
    def test_canon_and_requirements_are_denied_for_every_read_tool(self):
        self.put(f"{TRACK_DIR}/visual-requirements.json", "{}")
        self.put(f"{TRACK_DIR}/visual-requirements.html", "<html></html>")
        self.put("exports/modal.canonical.json", "{}")
        for tool in ("read_file", "Read", "ReadFile"):
            for rel in (CANON, SLIM, RAW, f"{TRACK_DIR}/visual-requirements.json",
                        f"{TRACK_DIR}/visual-requirements.html", "exports/modal.canonical.json"):
                with self.subTest(tool=tool, rel=rel):
                    self.denied(self.read(rel, tool), "daiver_outline", "daiver_section", "daiver_source_nodes")

    def test_the_names_match_without_case_and_in_a_frame_subdir(self):
        self.put(f"{TRACK_DIR}/frames/main/VISUAL-REQUIREMENTS.CANONICAL.JSON", "{}")
        self.denied(self.read(f"{TRACK_DIR}/frames/main/VISUAL-REQUIREMENTS.CANONICAL.JSON"))
        self.denied(self.hook("read_file", {"absolute_path": self.abs(CANON)}))
        self.denied(self.hook("read_file", {"path": self.abs(CANON)}))

    def test_a_relative_path_is_resolved_from_the_session_cwd(self):
        r = self.hook("read_file", {"file_path": f"../{CANON}"}, cwd=self.root / "src")
        self.denied(r)
        self.allowed(self.hook("read_file", {"file_path": f"../{TRACK_DIR}/plan.md"}, cwd=self.root / "src"))

    def test_the_raw_export_is_recognised_by_its_head_wherever_it_lies(self):
        dsl = (self.root / DSL).read_text(encoding="utf-8")
        variants = {
            "exports/anything.bin": dsl[:3000],
            "notes/pretty.txt": '{\n  "dsl": {\n    "converterVersion": "2.2.13",\n    "dslVersion": "2.1.15"\n  }\n}',
            "notes/bom.json": "﻿" + '{"dsl":{"converterVersion":"2.2.13"}}',
            "notes/unwrapped.json": '{"converterVersion":"2.2.13","dslVersion":"2.1.15","pixTreeDslNodes":[]}',
        }
        for rel, body in variants.items():
            self.put(rel, body)
            with self.subTest(rel=rel):
                self.denied(self.read(rel), "daiver_source_nodes")
        self.denied(self.read(DSL))

    def test_the_denial_names_the_replacement_and_leaks_nothing(self):
        r = self.read(DSL)
        self.denied(r, "daiver_outline", "daiver_section", "daiver_source_nodes")
        for leaked in ("converterVersion", "dslVersion", "Modal", "main.dsl.json", "pixTreeDslNodes"):
            self.assertNotIn(leaked, r.stderr, "отказ не должен пересказывать ни содержимое, ни имя файла")

    def test_a_symlink_to_the_canon_is_the_canon(self):
        link = self.root / "shortcut.json"
        try:
            os.symlink(str(self.root / CANON), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны")
        self.denied(self.read("shortcut.json"))
        self.denied(self.sh("cat shortcut.json"))

    def test_allowed_twins_for_read_file(self):
        self.put("notes/other.json", '{"dsl":{"other":1}}')
        self.put("notes/word.json", '{"name":"converterVersion"}')
        self.put("notes/empty.json", "")
        for rel in (f"{TRACK_DIR}/plan.md", f"{TRACK_DIR}/ui-plan.md", f"{TRACK_DIR}/resolution-report.md",
                    f"{TRACK_DIR}/track.yaml", f"{TRACK_DIR}/plan.json", ".daiver/config.yaml",
                    ".daiver/component-map.yaml", "docs/feature-pipeline/STOR-100/ui-spec.md", "package.json",
                    "src/modules/Modal/Modal.tsx", "notes/other.json", "notes/word.json", "notes/empty.json",
                    f"{TRACK_DIR}/visual-requirements.png", "does/not/exist.json"):
            with self.subTest(rel=rel):
                self.allowed(self.read(rel))

    def test_a_fifo_is_not_opened(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("нет mkfifo")
        os.mkfifo(str(self.root / "pipe.json"))
        r = self.hook("read_file", {"file_path": self.abs("pipe.json")})        # open() на FIFO повесил бы хук
        self.allowed(r)

    def test_grep_search_on_a_file_and_on_directories_inside_daiver(self):
        deny = [{"path": self.abs(CANON), "pattern": "x"}, {"path": self.abs(TRACK_DIR), "pattern": "x"},
                {"path": self.abs(TRACK_DIR), "pattern": "x", "glob": "*.json"},
                {"path": self.abs(".daiver"), "pattern": "x"},
                {"path": self.abs(".daiver/inbox"), "pattern": "x"},
                {"path": self.abs(TRACK_DIR), "pattern": "x", "glob": "*.{md,json}"},
                {"path": self.abs(TRACK_DIR), "pattern": "x", "include": "**/*.canonical.json"}]
        for ti in deny:
            for tool in ("grep_search", "search_file_content", "Grep"):
                with self.subTest(tool=tool, ti=ti):
                    self.denied(self.hook(tool, ti), "daiver_outline")
        self.denied(self.hook("grep_search", {"path": self.abs(TRACK_DIR), "pattern": "x"}), "glob=*.md")
        allow = [{"path": self.abs(f"{TRACK_DIR}/plan.md"), "pattern": "x"},
                 {"path": self.abs(TRACK_DIR), "pattern": "x", "glob": "*.md"},
                 {"path": self.abs(TRACK_DIR), "pattern": "x", "glob": "{plan,ui-plan}.md"},
                 {"path": self.abs(".daiver/inbox"), "pattern": "x", "glob": "*.md"},
                 {"path": self.abs("src"), "pattern": "x"}, {"path": self.abs("src/modules/Modal"), "pattern": "x"},
                 {"path": self.abs("docs"), "pattern": "x"}]
        for ti in allow:
            with self.subTest(allowed=ti):
                self.allowed(self.hook("grep_search", ti))

    def test_known_limit_a_repo_wide_search_is_not_denied(self):
        """Поиск от корня репо (каталог, СОДЕРЖАЩИЙ .daiver) не отказывает: иначе недоступен любой grep по репо.
        Тест пинит это границей, а не достоинством — см. «Не покрыто» в докстринге хука."""
        self.allowed(self.hook("grep_search", {"pattern": "Button"}))
        self.allowed(self.hook("grep_search", {"pattern": "Button", "path": str(self.root)}))

    def test_read_many_files(self):
        """Шаблоны сверяются с файлами `.daiver/` по правилам рантайма (точка в имени не мешает `**`), а не по
        диску: Python glob и оболочка скрытые каталоги пропускают, `read_many_files` — нет."""
        for paths in ([CANON], [f"{TRACK_DIR}/*.json"], [f"{TRACK_DIR}/visual-req*"], [".daiver/**/*.json"],
                      ["**/*.json"], ["**/visual-requirements.*"], [".daiver/*/*/*.canonical.json"], [".daiver/{inbox,tracks}/**"],
                      [str(self.root / ".daiver" / "**" / "*.json")], [".daiver/inbox"], ["src/**/*.tsx", CANON],
                      ["src/*/*.canonical.json"]):
            with self.subTest(paths=paths):
                self.denied(self.hook("read_many_files", {"paths": paths}))
        self.denied(self.hook("read_many_files", {"paths": ["src"], "include": [f"{TRACK_DIR}/*.canonical.json"]}))
        self.denied(self.hook("ReadManyFiles", {"paths": ["**/*.json"]}))
        for paths in (["src/**/*.tsx"], [f"{TRACK_DIR}/*.md"], ["docs/**/*.md", "package.json"], ["*.json"],
                      [".daiver/*.yaml"], [".daiver/**/*.md"], ["[z-a].json"], ["**/*.tsx"]):
            with self.subTest(paths=paths):
                self.allowed(self.hook("read_many_files", {"paths": paths}))


class ShellReaders(UiWorld):
    """Shell-читатели (cat, head, tail, less, jq, python -c …open, sed, awk, grep, cp…) — те же файлы."""

    def test_readers_are_denied(self):
        cases = [f"cat {CANON}", f"head -c 200 {CANON}", f"tail -n 5 {CANON}", f"less {CANON}", f"jq . {CANON}",
                 f"jq . < {CANON}", f"sed -n 1,5p {SLIM}", f"awk '{{print}}' {RAW}", f"grep -n x {CANON}",
                 f"rg x {CANON}", f"cp {CANON} /tmp/copy.json", f"mv {CANON} /tmp/moved.json", f"xxd {CANON}",
                 f"python3 -c \"print(open('{CANON}').read())\"",
                 f"python3 -c \"import json; json.load(open('{TRACK_DIR}/visual-requirements.canonical.json'))\"",
                 f"node -e \"console.log(require('./{CANON}'))\"",
                 f"sh -c 'cat {CANON}'", f"bash -c \"head -c 5 {CANON}\"", f"sudo cat {CANON}", f"env A=1 cat {CANON}",
                 f"timeout 5 cat {CANON}", f"cat {CANON} | head -c 10", f"echo ok && cat {CANON}",
                 f"cat {TRACK_DIR}/*.canonical.json", f"cat {TRACK_DIR}/visual-req*", f"cat {TRACK_DIR}/*",
                 f"cat .daiver/tracks/*/visual-requirements.canonical.json", f"cat {DSL}", f"head -c 100 {DSL}",
                 f"python3 -c \"print(open('{DSL}').read(10))\"", f"jq keys {DSL}", f"echo x > {CANON}",
                 f"tee {CANON}", f"cat <{CANON}", f"cat --file={CANON}"]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                self.denied(self.sh(cmd), "daiver_outline", "daiver_source_nodes")

    def test_a_raw_export_is_recognised_by_head_under_any_name_in_a_command(self):
        self.put("exports/anything.bin", (self.root / DSL).read_text(encoding="utf-8")[:2000])
        for cmd in ("cat exports/anything.bin", "head -c 9 exports/anything.bin",
                    "python3 -c \"print(open('exports/anything.bin').read())\"", "cat exports/*.bin",
                    "node -e \"require('./exports/anything.bin')\""):
            with self.subTest(cmd=cmd):
                self.denied(self.sh(cmd))

    def test_directories_inside_daiver_are_denied_for_readers_but_listed_freely(self):
        for cmd in ("grep -rn Button .daiver", "grep -rn Button .daiver/tracks", f"grep -rn x {TRACK_DIR}",
                    "rg Button .daiver/inbox", f"cat {TRACK_DIR}/../../inbox/main.dsl.json"):
            with self.subTest(cmd=cmd):
                self.denied(self.sh(cmd))
        for cmd in ("ls .daiver", "ls -la .daiver/inbox", f"ls -la {TRACK_DIR}/", "ls -R .daiver",
                    "find .daiver -name '*.canonical.json'", "find .daiver -type f"):
            with self.subTest(cmd=cmd):
                self.allowed(self.sh(cmd))

    def test_find_with_exec_is_a_reader(self):
        self.denied(self.sh("find .daiver -name '*.json' -exec cat {} +"))
        self.denied(self.sh(f"find {TRACK_DIR} -name '*.canonical.json' -execdir head {{}} ;"))

    def test_listing_and_measuring_is_allowed(self):
        for cmd in (f"ls -la {CANON}", f"wc -c {CANON}", f"wc -l {DSL}", f"stat {CANON}", f"stat -f %z {DSL}",
                    f"file {CANON}", f"file {DSL}", f"du -sh {TRACK_DIR}", f"shasum -a 256 {CANON}",
                    f"sha256sum {DSL}", f"md5 {CANON}", f"test -f {CANON}", f"[ -f {CANON} ]", f"[[ -f {CANON} ]]",
                    f"basename {CANON}", f"realpath {CANON}", f"ls {TRACK_DIR}/*.canonical.json"):
            with self.subTest(cmd=cmd):
                self.allowed(self.sh(cmd))

    def test_allowed_twins_for_shell(self):
        self.put("notes/other.json", '{"dsl":{"other":1}}')
        for cmd in (f"cat {TRACK_DIR}/plan.md", f"head -n 20 {TRACK_DIR}/ui-plan.md", f"tail {TRACK_DIR}/resolution-report.md",
                    "cat .daiver/config.yaml", f"jq . {TRACK_DIR}/plan.json", f"grep -n Button {TRACK_DIR}/plan.md",
                    "grep -rn Button src", "grep -rn Button .", "cat package.json", "cat notes/other.json",
                    f"cat {TRACK_DIR}/*.md", "git status", "git diff --stat", "npm run build", "npx tsc --noEmit",
                    "python3 -c \"print(1)\"", "echo hello", "ls -la src/modules", "cat does/not/exist.json",
                    f"python3 <forge>/skills/forgeui/scripts/ui_gate.py build --project {self.root} --feature {KEY}",
                    f"python3 <forge>/skills/pipeline-state/scripts/record_gate.py --project {self.root} --skill forgeui "
                    f"--feature {KEY} --step-id ui-build --cmd \"python3 ui_gate.py build --project . --feature {KEY}\""):
            with self.subTest(cmd=cmd):
                self.allowed(self.sh(cmd))

    def test_a_session_started_in_a_subdirectory_resolves_relative_names(self):
        self.denied(self.sh(f"cat ../../{CANON}", cwd=self.root / "src" / "modules"))
        self.allowed(self.sh(f"cat ../../{TRACK_DIR}/plan.md", cwd=self.root / "src" / "modules"))

    def test_candidate_cap_and_long_tokens_do_not_crash_or_slow_the_hook(self):
        big = " ".join(f"f{i}.txt" for i in range(500))
        self.allowed(self.sh(f"echo {big}"))
        self.allowed(self.sh("cat " + "a" * 5000))
        self.allowed(self.sh("python3 -c \"" + "x = 1; " * 2000 + "\""))
        self.denied(self.sh("cat " + " ".join(f"f{i}.txt" for i in range(30)) + f" {CANON}"))


# ── 2–3. запись кода модуля: шаг и подпись плана (C8, C9, N9) ─────────────────

class WriteStep(UiWorld):
    def test_signed_plan_on_ui_build_allows_every_form_of_write(self):
        self.allowed(self.write(), "Write на ui-build при подписи")
        for tool in ("Write", "WriteFile", "write_file"):
            with self.subTest(tool=tool):
                self.allowed(self.write(tool=tool))
        self.allowed(self.hook("edit", {"file_path": self.abs(CODE), "old_string": "a", "new_string": "b"}))
        self.allowed(self.hook("Edit", {"file_path": self.abs(CODE), "old_string": "a", "new_string": "b"}))
        self.allowed(self.hook("NotebookEdit", {"notebook_path": self.abs(f"{MODULE}/n.ipynb"), "new_source": "x"}))
        for cmd in (f"echo 'export default 1' > {MODULE}/mocks.ts", f"printf x | tee {MODULE}/a.ts",
                    f"cp /tmp/x.ts {MODULE}/b.ts", f"sed -i 's/a/b/' {CODE}", f"cat > {CODE} <<'EOF'\nexport const A = 1;\nEOF",
                    f"python3 -c \"open('{CODE}','w').write('x')\""):
            with self.subTest(cmd=cmd):
                self.allowed(self.sh(cmd))

    def test_code_is_denied_outside_ui_build_with_the_reason(self):
        for step in ("ui-spec", "ui-design"):
            self.step(step)
            with self.subTest(step=step):
                r = self.write()
                self.denied(r, "ui-build", step)
                self.assertNotIn("update.py", r.stderr, "до сборки переоткрывать нечего")
        for step in ("ui-verify", None):
            self.step(step)
            with self.subTest(step=step):
                self.denied(self.write(), "ui-build", "update.py", "--status pending", "--skill forgeui",
                            f"--feature {KEY}", "--step-id ui-build", "INV-F12")

    def test_n9_nothing_is_edited_between_opening_a_verify_iteration_and_the_decision(self):
        self.step("ui-verify")
        self.denied(self.write(), "переоткрыть шаг сборки")
        self.denied(self.hook("edit", {"file_path": self.abs(CODE), "old_string": "a", "new_string": "b"}), "ui-build")
        self.denied(self.sh(f"echo fix > {CODE}"), "ui-build", "update.py")
        self.denied(self.sh(f"sed -i 's/a/b/' {CODE}"), "ui-build")
        self.denied(self.sh(f"python3 -c \"open('{CODE}','w').write('x')\""), "ui-build")
        self.denied(self.sh(f"cp /tmp/x.ts {MODULE}/b.ts"), "ui-build")

    def test_deleting_and_moving_module_files_is_an_edit_too(self):
        cmds = [f"rm {CODE}", f"rm -rf {MODULE}", f"touch {MODULE}/new.ts", f"mv {CODE} /tmp/gone.tsx",
                f"mv /tmp/x.tsx {MODULE}/", f"unlink {CODE}", "rm -rf src/modules",
                f"python3 -c \"import os; os.remove('{CODE}')\""]
        self.step("ui-verify")
        for cmd in cmds:
            with self.subTest(step="ui-verify", cmd=cmd):
                self.denied(self.sh(cmd), "ui-build")
        self.step("ui-build")
        for cmd in cmds:
            with self.subTest(step="ui-build", cmd=cmd):
                self.allowed(self.sh(cmd))
        self.step("ui-verify")
        for cmd in ("rm -f build.log", "rm -rf node_modules/.cache", "touch /tmp/ui-guard-touch", f"mv /tmp/a /tmp/b"):
            with self.subTest(step="ui-verify", free=cmd):
                self.allowed(self.sh(cmd))

    def test_reopening_ui_build_makes_the_write_legal_again(self):
        self.step("ui-verify")
        self.denied(self.write())
        self.step("ui-build")                                       # то, что делает update.py --status pending
        self.allowed(self.write())

    def test_writes_that_are_not_module_code_are_free_on_every_step(self):
        self.step("ui-verify")
        for rel in ("docs/feature-pipeline/STOR-100/ui-spec.md", "README.md", "src/other/Page.tsx", "package.json",
                    f"{TRACK_DIR}/verify/iteration-1/report.md", "ground/notes.txt", "src/modules.txt"):
            with self.subTest(rel=rel):
                self.allowed(self.write(rel))
        self.allowed(self.hook("write_file", {"file_path": "/tmp/ui-guard-x.txt", "content": "x"}))
        self.allowed(self.sh("echo x > /tmp/ui-guard-y.txt"))
        self.allowed(self.sh("echo x > docs/feature-pipeline/STOR-100/note.md"))
        self.allowed(self.sh("npm run build > build.log 2>&1"))

    def test_the_directory_comes_from_the_config_and_matching_ignores_case_and_symlinks(self):
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('outputDir: "src/modules"', 'outputDir: "app/ui"'),
                       encoding="utf-8")
        self.step("ui-verify")
        self.allowed(self.write(CODE))                              # прежний каталог больше не модуль
        self.denied(self.write("app/ui/Modal/Modal.tsx"), "ui-build")
        self.denied(self.write("APP/UI/Modal/Modal.tsx"), "ui-build")
        self.denied(self.sh("echo x > app/ui/Modal/a.ts"), "ui-build")
        self.allowed(self.write("app/uikit/Modal.tsx"))             # соседний каталог с общим префиксом
        real = self.root / "packages" / "ui-src"
        real.mkdir(parents=True)
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('outputDir: "app/ui"', 'outputDir: "linked"'),
                       encoding="utf-8")
        try:
            os.symlink(str(real), str(self.root / "linked"))
        except (OSError, NotImplementedError):
            return
        self.denied(self.write("packages/ui-src/A.tsx"), "ui-build")    # пишут по раскрытому пути
        self.denied(self.write("linked/A.tsx"), "ui-build")

    def test_a_broad_output_dir_does_not_swallow_control_plane_and_task_documents(self):
        """outputDir «.» делает модулем весь репозиторий; control-plane, харнес и документы задачи — не код модуля."""
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('outputDir: "src/modules"', 'outputDir: "."'), encoding="utf-8")
        self.step("ui-verify")
        self.denied(self.write("src/Page.tsx"), "ui-build")
        self.denied(self.write("README.md"), "ui-build")
        for rel in ("ground/notes.txt", f"{TRACK_DIR}/verify/iteration-1/report.md", ".git/info/x", ".gigacode/hooks/x.py",
                    ".qwen/x.json", "docs/feature-pipeline/STOR-100/ui-spec.md"):
            with self.subTest(rel=rel):
                self.allowed(self.write(rel))
        self.allowed(self.sh("echo x > ground/notes.txt"))
        self.denied(self.sh("echo x > src/Page.tsx"), "ui-build")

    def test_default_directory_when_the_project_is_not_initialised(self):
        (self.root / ".daiver" / "config.yaml").unlink()
        self.step("ui-verify")
        self.denied(self.write(f"src/modules/X/X.tsx"), "ui-build")

    def test_an_unreadable_target_does_not_crash_the_hook(self):
        self.step("ui-verify")
        self.allowed(self.hook("write_file", {"content": "x"}))
        self.allowed(self.hook("write_file", {"file_path": "", "content": "x"}))
        self.allowed(self.hook("write_file", {"file_path": "a\0b", "content": "x"}))
        self.allowed(self.hook("edit", {"file_path": self.abs("x" * 400 + ".ts"), "new_string": "x"}))
        self.allowed(self.sh(""))
        self.allowed(self.hook("run_shell_command", {}))


class WriteNeedsASignature(UiWorld):
    def deny_unsigned(self, *needles: str) -> None:
        r = self.write()
        self.denied(r, "failed", "ui-design", "согласие не выписывай", *needles)
        self.assertNotIn("record_approval", r.stderr, "подпись — решение человека: подсказки, как её выписать, нет")
        self.denied(self.sh(f"echo x > {CODE}"), "failed")

    def test_the_story_spec_resolver_is_the_one_the_gate_uses(self):
        """Без резолвера ui-spec.md дайджест другой и ЗАКОННАЯ подпись читается как устаревшая — хук обязан
        звать signature_state с тем же резолвером, что `ui_gate.py design` (иначе запрещал бы всё)."""
        self.assertEqual(PC.signature_state(self.root, KEY, T._story_specs(self.root))["state"], "ok")
        self.assertEqual(PC.signature_state(self.root, KEY, None)["state"], "stale")
        self.allowed(self.write(), "хук позвал signature_state без резолвера")

    def test_no_approval(self):
        shutil.copyfile(str(self.root / "ground" / "approvals.jsonl"), str(self.root / "approvals.bak"))
        (self.root / "ground" / "approvals.jsonl").write_text("", encoding="utf-8")
        self.deny_unsigned("не подписан")

    def test_approval_without_binding(self):
        (self.root / "ground" / "approvals.jsonl").unlink()
        self.assertEqual(self.sign(bind=None).returncode, 0)
        self.deny_unsigned("устарела")

    def test_a_revoked_approval(self):
        import forge_events as FE
        FE.revoke_approval(self.root, f"ui-plan-{KEY}", reason="тест")
        self.deny_unsigned("не подписан")

    def test_approval_for_another_story_is_not_ours(self):
        (self.root / "ground" / "approvals.jsonl").unlink()
        self.assertEqual(self.sign(key="ui-plan-OTHER-1").returncode, 0)
        self.deny_unsigned("не подписан")

    def test_a_repulled_export_makes_the_signature_stale_and_a_new_signature_returns_the_write(self):
        self.allowed(self.write())
        self.repull()
        self.deny_unsigned("устарела")
        self.call("daiver_normalize", track=KEY)
        self.write_plan()
        self.contract()                                              # писатель снимает прежнее согласие
        self.deny_unsigned("не подписан")
        self.assertEqual(self.sign().returncode, 0)
        self.assertTrue(self.contract()["signed"])
        self.allowed(self.write(), "новая подпись должна вернуть запись")

    def test_plan_json_edited_by_hand_makes_the_signature_stale(self):
        """lock-файл правка plan.json не пересобирает — хеш файла lock'а остался бы прежним; верна проверка по
        lock'у, собранному из артефактов (так что вердикт `stale`, а не `ok`)."""
        self.allowed(self.write())
        plan = self.paths()["plan"]
        before = plan.read_text(encoding="utf-8")
        plan.write_text(before.replace("Modal", "Modul", 1), encoding="utf-8")
        self.assertNotEqual(before, plan.read_text(encoding="utf-8"))
        self.deny_unsigned("устарела")

    def test_spec_edited_after_signing_makes_the_signature_stale(self):
        self.allowed(self.write())
        spec = T._story_spec_path(self.root, KEY)
        spec.write_text(spec.read_text(encoding="utf-8") + "\n## События\n- onClose: закрыть\n", encoding="utf-8")
        self.deny_unsigned("устарела")

    def test_missing_track_or_plan_is_unsigned_not_a_crash(self):
        shutil.rmtree(self.root / TRACK_DIR)
        self.deny_unsigned("нет")

    def test_signature_is_not_checked_when_the_write_is_not_module_code(self):
        (self.root / "ground" / "approvals.jsonl").write_text("", encoding="utf-8")
        self.allowed(self.write("docs/feature-pipeline/STOR-100/ui-spec.md"))
        self.allowed(self.write("README.md"))
        self.allowed(self.read(f"{TRACK_DIR}/plan.md"))


class SignatureCache(UiWorld):
    """Вердикт кешируется по снимку stat'ов входов; ЛЮБОЕ изменение входа снимает вердикт."""

    def cache(self) -> Dict[str, Any]:
        return json.loads((self.run_dir() / "ui-guard-cache.json").read_text(encoding="utf-8"))

    def test_the_verdict_is_cached_next_to_the_manifest_and_reused(self):
        mod = _load_hook()
        calls = []
        real = mod._engine
        mod._engine = lambda *names: (calls.append(names), real(*names))[1]
        first = mod._signature(self.root, KEY, self.run_dir())
        second = mod._signature(self.root, KEY, self.run_dir())
        self.assertEqual(first[0], "ok")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, "второй вызов обязан ответить из кеша, не поднимая движок")
        entry = self.cache()
        self.assertEqual((entry["schema"], entry["state"]), (1, "ok"))
        self.assertEqual(entry["fp"], mod._fingerprint(self.root, entry["extras"]))
        self.assertIn("docs/feature-pipeline/STOR-100/ui-spec.md", entry["extras"], "спека стори — вход подписи вне .daiver/")

    def test_a_corrupt_or_foreign_cache_is_recomputed(self):
        self.allowed(self.write())
        for junk in ("not json", "[]", '{"schema": 99}', '{"schema": 1, "fp": "x", "state": "ok"}',
                     '{"schema": 1, "fp": "x", "state": "bogus", "detail": "d", "extras": []}'):
            (self.run_dir() / "ui-guard-cache.json").write_text(junk, encoding="utf-8")
            with self.subTest(junk=junk):
                self.allowed(self.write())
                self.assertEqual(self.cache()["state"], "ok")

    def test_a_planted_ok_verdict_does_not_survive_a_changed_input(self):
        self.allowed(self.write())
        entry = self.cache()
        plan = self.paths()["plan"]
        plan.write_text(plan.read_text(encoding="utf-8").replace("Modal", "Modul", 1), encoding="utf-8")
        (self.run_dir() / "ui-guard-cache.json").write_text(json.dumps(entry), encoding="utf-8")   # кеш «ok» остался
        self.denied(self.write(), "устарела")

    def test_each_kind_of_input_invalidates_the_cache(self):
        mod = _load_hook()
        self.allowed(self.write())
        base = self.cache()
        spec = T._story_spec_path(self.root, KEY)
        probes = {
            "артефакт трека": self.paths()["planMd"],
            "конфиг": self.root / ".daiver" / "config.yaml",
            "журнал согласий": self.root / "ground" / "approvals.jsonl",
            "спека стори": spec,
        }
        for name, path in probes.items():
            before = mod._fingerprint(self.root, base["extras"])
            st = path.stat()
            try:
                os.utime(str(path), ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
                after = mod._fingerprint(self.root, base["extras"])
            finally:
                os.utime(str(path), ns=(st.st_atime_ns, st.st_mtime_ns))
            with self.subTest(input=name):
                self.assertNotEqual(before, after)
        with tempfile.TemporaryDirectory() as td:                    # «код движка» — вход подписи (обновление форжа)
            fake = Path(td) / "daiver"
            fake.mkdir()
            (fake / "x.py").write_text("x = 1\n", encoding="utf-8")
            mod._ENGINE_DIR = Path(td)
            before = mod._fingerprint(self.root, base["extras"])
            (fake / "x.py").write_text("x = 2\n", encoding="utf-8")
            self.assertNotEqual(before, mod._fingerprint(self.root, base["extras"]), "код движка")
        newfile = self.root / ".daiver" / "tracks" / KEY / "assets" / "hero.svg"
        before = mod._fingerprint(self.root, base["extras"])
        newfile.parent.mkdir(parents=True, exist_ok=True)
        newfile.write_text("<svg/>", encoding="utf-8")
        self.assertNotEqual(before, mod._fingerprint(self.root, base["extras"]), "новый файл в .daiver/")
        newfile.unlink()

    def test_restoring_mtime_does_not_hide_a_changed_file(self):
        """Правка с теми же размером и mtime (touch -r) оставляет ctime новым: старый вердикт «ok» не годится."""
        self.allowed(self.write())
        plan = self.paths()["plan"]
        st = plan.stat()
        text = plan.read_text(encoding="utf-8")
        plan.write_text(text.replace("Modal", "Modul", 1), encoding="utf-8")
        os.utime(str(plan), ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(plan.stat().st_size, st.st_size)
        self.assertEqual(plan.stat().st_mtime_ns, st.st_mtime_ns)
        self.denied(self.write(), "устарела")

    def test_a_not_ok_verdict_is_cached_too_and_flips_on_a_new_signature(self):
        (self.root / "ground" / "approvals.jsonl").write_text("", encoding="utf-8")
        self.denied(self.write(), "не подписан")
        self.assertEqual(self.cache()["state"], "open")
        self.assertEqual(self.sign().returncode, 0)
        self.allowed(self.write(), "журнал согласий изменился — вердикт пересчитан")
        self.assertEqual(self.cache()["state"], "ok")

    def test_a_read_only_run_dir_only_loses_the_cache(self):
        mod = _load_hook()
        orig = mod._cache_write
        mod._cache_write = lambda path, entry: orig(self.root / "no" / "such" / "dir" / "c.json", entry)
        self.assertEqual(mod._signature(self.root, KEY, self.run_dir())[0], "ok")


# ── 4. инлайн-svg (N1, N6) ────────────────────────────────────────────────────

class InlineSvg(UiWorld):
    ICON = ('import React from "react";\nexport const Close = () => (\n  <svg width="24" height="24" viewBox="0 0 24 24">'
            '<path d="M6 6l12 12M18 6L6 18" stroke="#000"/></svg>\n);\n')
    PLACEHOLDER_SVG = ('<svg data-art-placeholder="hero" role="img" aria-label="ART MISSING: hero.svg" '
                       'style={{ width: 120, height: 80 }}><title>ART MISSING: hero.svg</title></svg>')

    def test_n1_an_inline_svg_icon_is_denied_in_every_code_file(self):
        for suffix in (".tsx", ".jsx", ".vue", ".svelte", ".ts", ".js", ".mjs", ".html", ".TSX"):
            with self.subTest(suffix=suffix):
                self.denied(self.write(f"{MODULE}/Close{suffix}", self.ICON), "INV-F5", "daiver_svg_assets",
                            "data-art-placeholder")

    def test_every_write_form_is_checked(self):
        self.denied(self.hook("edit", {"file_path": self.abs(CODE), "old_string": "null", "new_string": self.ICON}), "INV-F5")
        self.denied(self.hook("Edit", {"file_path": self.abs(CODE), "old_string": "null", "new_string": "<svg/>"}), "INV-F5")
        self.denied(self.hook("NotebookEdit", {"notebook_path": self.abs(f"{MODULE}/n.ts"), "new_source": "<svg>"}), "INV-F5")
        self.denied(self.sh(f"cat > {CODE} <<'EOF'\n{self.ICON}EOF"), "INV-F5")
        self.denied(self.sh(f"echo '<svg></svg>' > {MODULE}/Icon.tsx"), "INV-F5")
        self.denied(self.sh(f"printf '%s' '<SVG>' | tee {MODULE}/Icon.html"), "INV-F5")
        self.denied(self.sh(f"python3 -c \"open('{CODE}','w').write('<svg/>')\""), "INV-F5")
        self.denied(self.hook("edit", {"file_path": self.abs(CODE), "edits": [
            {"old_string": "a", "new_string": "ok"}, {"old_string": "b", "new_string": "<svg>"}]}), "INV-F5")

    def test_a_data_uri_svg_is_an_inline_svg(self):
        body = 'export const bg = "data:image/svg+xml;utf8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22%3E";\n'
        self.denied(self.write(f"{MODULE}/bg.ts", body), "INV-F5")

    def test_allowed_twins_a_library_icon_and_everything_that_is_not_an_svg_element(self):
        for rel, body in {
            f"{MODULE}/Close.tsx": 'import { Close } from "@corp/ui/icons";\nexport const A = () => <Close size={24} />;\n',
            f"{MODULE}/Wrap.tsx": "export const A = () => <svg-icon name=\"close\" />;\n",
            f"{MODULE}/Comp.tsx": "import Svg from './Svg';\nexport const A = () => <Svg />;\n",
            f"{MODULE}/Gt.tsx": "export const A = (a: number) => (a > 1 ? 'svg' : '<span>');\n",
            f"{MODULE}/mocks.ts": "export const label = 'svg';\n",
            f"{MODULE}/Modal.less": ".a { background: url('data:image/png;base64,AAA'); }\n",
            f"{MODULE}/README.md": "Не пишите <svg> руками.\n",
            f"{MODULE}/notes.json": '{"hint": "<svg>"}\n',
        }.items():
            with self.subTest(rel=rel):
                self.allowed(self.write(rel, body))

    def test_the_text_is_judged_only_when_a_module_code_file_is_the_target(self):
        self.allowed(self.write("docs/feature-pipeline/STOR-100/note.md", self.ICON))
        self.allowed(self.write("src/other/Icon.tsx", self.ICON))             # вне outputDir: правило про модуль
        self.allowed(self.write(f"{MODULE}/notes.txt", self.ICON))
        self.allowed(self.sh(f"grep -c '<svg' {CODE}"))                       # чтение, целей записи нет

    def test_n6_the_marked_placeholder_of_an_art_is_allowed_a_drawn_one_is_not(self):
        live = SA.placeholder("hero", 120, 80)                                # то, что выдаёт daiver_svg_assets
        self.allowed(self.write(f"{MODULE}/Hero.tsx", f"export const A = () => ({live});\n"))
        self.allowed(self.write(f"{MODULE}/Hero2.tsx", f"export const A = () => (\n  {self.PLACEHOLDER_SVG}\n);\n"))
        self.allowed(self.write(f"{MODULE}/Hero3.tsx", '<svg data-art-placeholder="hero-big" aria-label="ART MISSING: hero-big.svg" />'))
        self.allowed(self.sh(f"printf '%s' '{self.PLACEHOLDER_SVG}' > {MODULE}/Hero4.tsx"))
        drawn = ('<svg data-art-placeholder="hero" aria-label="ART MISSING: hero.svg">'
                 '<path d="M0 0L10 10"/></svg>')
        no_label = '<svg data-art-placeholder="hero" style={{ width: 120 }}></svg>'
        other_slug = '<svg data-art-placeholder="hero" aria-label="ART MISSING: other.svg"></svg>'
        bad_slug = '<svg data-art-placeholder="Hero Art" aria-label="ART MISSING: Hero Art.svg"></svg>'
        empty_mark = '<svg data-art-placeholder="" aria-label="ART MISSING: .svg"></svg>'
        unclosed = '<svg data-art-placeholder="hero" aria-label="ART MISSING: hero.svg"><title>x</title>'
        mark_in_body = '<svg width="10"><g data-art-placeholder="hero"/>ART MISSING: hero.svg</svg>'
        for name, body in {"нарисован": drawn, "без подписи": no_label, "чужой slug": other_slug,
                           "кривой slug": bad_slug, "пустая метка": empty_mark, "не закрыт": unclosed,
                           "метка не на svg": mark_in_body, "голый svg": "<svg></svg>"}.items():
            with self.subTest(case=name):
                self.denied(self.write(f"{MODULE}/Bad.tsx", f"export const A = () => ({body});\n"), "INV-F5")
        # плейсхолдер рядом с настоящей иконкой — иконка всё равно запрещена
        self.denied(self.write(f"{MODULE}/Mix.tsx", f"{self.PLACEHOLDER_SVG}\n{self.ICON}"), "INV-F5")

    def test_the_marker_is_pinned_to_the_live_svg_assets_placeholder(self):
        mod = _load_hook()
        live = SA.placeholder("hero-art", 120, 80)
        m = mod._ART_MARK.search(live)
        self.assertIsNotNone(m, "атрибут плейсхолдера svg_assets изменился — обнови _ART_MARK ui-guard")
        self.assertEqual(m.group(1), "hero-art")
        self.assertIn("ART MISSING: hero-art.svg", live)
        self.assertEqual(mod._SLUG_RE.pattern, SA.SLUG_RE.pattern, "slug-ы svg_assets и ui-guard разошлись")
        # формат, который ui-guard принимает за помеченный плейсхолдер, строится из тех же частей
        self.assertFalse(mod._svg_problem(live), "живой плейсхолдер — не инлайн-svg")
        as_svg = live.replace("<div", "<svg").replace("</div>", "</svg>")
        self.assertFalse(mod._svg_problem(as_svg), "svg-вариант живого плейсхолдера допустим как помеченный")

    def test_tag_end_skips_greater_than_in_strings_and_jsx_expressions(self):
        mod = _load_hook()
        text = '<svg a="x > y" b={() => 1 > 0 ? "}" : \'{\'} data-art-placeholder="s" aria-label="ART MISSING: s.svg"/>rest'
        end = mod._tag_end(text, 0)
        self.assertEqual(text[end - 1:end + 1], "/>")
        self.assertEqual(mod._tag_end("<svg " + "a " * 4000, 0), -1, "бесконечный тег не должен вешать хук")
        self.assertEqual(mod._tag_end("<svg a={", 0), -1)
        self.assertFalse(mod._svg_problem(text))


# ── fail-closed ───────────────────────────────────────────────────────────────

class FailClosed(UiWorld):
    """Внутри живого прогона forgeui сбой хука и несобранное ядро — отказ, а не тихий пропуск (exit 1 рантайм
    читает как «возражений нет»)."""

    def bundle(self, *, engine: bool = True) -> Path:
        dst = Path(tempfile.mkdtemp(prefix="bundle-", dir=str(self.root.parent)))
        shutil.copytree(HOOKS, dst / "hooks", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "evals"))
        if engine:
            shutil.copytree(SCRIPTS / "daiver", dst / "skills" / "forgeui" / "scripts" / "daiver",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return dst

    def run_in(self, bundle: Path, tool: str, tool_input: Dict[str, Any]) -> "subprocess.CompletedProcess[str]":
        payload = {"hook_event_name": "PreToolUse", "cwd": str(self.root), "tool_name": tool, "tool_input": tool_input}
        return subprocess.run([sys.executable, "-X", "utf8", str(bundle / "hooks" / "ui-guard.py")],
                              input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_healthy_bundle_copy_behaves_like_the_original(self):
        b = self.bundle()
        self.assertEqual(self.run_in(b, "write_file", {"file_path": self.abs(CODE), "content": "x"}).returncode, 0)
        self.assertEqual(self.run_in(b, "read_file", {"file_path": self.abs(CANON)}).returncode, 2)

    def test_broken_import_of_a_forge_module_denies(self):
        for victim in ("risk_ladder.py", "_shell_targets.py", "forge_events.py", "_project.py"):
            b = self.bundle()
            (b / "hooks" / victim).write_text("raise ImportError('simulated broken bundle')\n", encoding="utf-8")
            r = self.run_in(b, "read_file", {"file_path": self.abs("package.json")})
            with self.subTest(victim=victim):
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("DENY", r.stderr)

    def test_a_registry_without_the_ui_branch_denies_instead_of_crashing_silently(self):
        """Реестр целый, но ветки forgeui в нём нет: остальные модули грузятся, а поиск шагов в хуке падает —
        на уровне модуля, вне функций. Без try вокруг него это был бы крэш с exit 1, то есть тихий пропуск."""
        b = self.bundle()
        reg = b / "hooks" / "_branches.py"
        reg.write_text(reg.read_text(encoding="utf-8") + '\n\ndel BRANCHES["forgeui"]\n', encoding="utf-8")
        r = self.run_in(b, "read_file", {"file_path": self.abs("package.json")})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("DENY", r.stderr)
        self.assertIn("forgeui", r.stderr)

    def test_engine_missing_denies_module_writes_but_reads_still_judged_by_name(self):
        b = self.bundle(engine=False)
        r = self.run_in(b, "write_file", {"file_path": self.abs(CODE), "content": "x"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ядро forge-ui не грузится", r.stderr)
        self.assertEqual(self.run_in(b, "read_file", {"file_path": self.abs(CANON)}).returncode, 2)
        self.assertEqual(self.run_in(b, "read_file", {"file_path": self.abs("package.json")}).returncode, 0)
        self.assertEqual(self.run_in(b, "write_file", {"file_path": self.abs("README.md"), "content": "x"}).returncode, 2,
                         "без ядра неизвестно, где outputDir: запись в проект не пропускается")

    def test_engine_that_does_not_import_denies(self):
        b = self.bundle()
        (b / "skills" / "forgeui" / "scripts" / "daiver" / "plan_contract.py").write_text(
            "raise ImportError('simulated broken engine')\n", encoding="utf-8")
        r = self.run_in(b, "write_file", {"file_path": self.abs(CODE), "content": "x"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ядро forge-ui не грузится", r.stderr)

    def test_an_unexpected_error_in_a_check_denies_with_the_reason(self):
        b = self.bundle()
        cfg = b / "skills" / "forgeui" / "scripts" / "daiver" / "config.py"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n\ndef load(root):\n    raise RuntimeError('boom')\n",
                       encoding="utf-8")
        r = self.run_in(b, "write_file", {"file_path": self.abs(CODE), "content": "x"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ошибка проверки", r.stderr)
        self.assertIn("RuntimeError", r.stderr)

    def test_an_unexpected_error_in_the_read_path_denies_too(self):
        b = self.bundle()
        src = (b / "hooks" / "ui-guard.py").read_text(encoding="utf-8")
        patched = src.replace("def _judge_file_read(root: Path, cwd: str, tool_input: Dict[str, Any]) -> Optional[str]:\n",
                              "def _judge_file_read(root: Path, cwd: str, tool_input: Dict[str, Any]) -> Optional[str]:\n"
                              "    raise ValueError('boom')\n", 1)
        self.assertNotEqual(src, patched)
        (b / "hooks" / "ui-guard.py").write_text(patched, encoding="utf-8")
        r = self.run_in(b, "read_file", {"file_path": self.abs("package.json")})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ошибка проверки", r.stderr)

    def test_outside_a_forgeui_run_a_broken_engine_is_irrelevant(self):
        b = self.bundle(engine=False)
        shutil.rmtree(self.run_dir().parent)
        self.assertEqual(self.run_in(b, "write_file", {"file_path": self.abs(CODE), "content": "<svg>"}).returncode, 0)


# ── устройство: реестр, проводка, чистые функции ──────────────────────────────

class Wiring(unittest.TestCase):
    SETTINGS = json.loads((HOOKS / "settings.hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]

    @staticmethod
    def names(group):
        return [re.search(r"([\w-]+)\.py", h["command"]).group(1) for h in group["hooks"]]

    def groups_with(self, name: str):
        return [g for g in self.SETTINGS if name in self.names(g)]

    def test_the_hook_sits_after_tech_guard_in_both_write_chains(self):
        chains = [g for g in self.groups_with("ui-guard") if "tech-guard" in self.names(g)]
        self.assertEqual(len(chains), 2, "и Bash-цепочка, и Write/Edit-цепочка")
        for g in chains:
            n = self.names(g)
            self.assertEqual(n.index("ui-guard"), n.index("tech-guard") + 1, n)
            self.assertLess(n.index("ui-guard"), n.index("gate-guard"))
            self.assertLess(n.index("ui-guard"), n.index("sod-enforcer"))

    def test_the_matchers_cover_every_tool_the_hook_judges(self):
        mod = _load_hook()
        for g in self.groups_with("ui-guard"):
            m = g["matcher"]
            tools = [t for t in mod._WRITE_TOOLS + mod._BASH_TOOLS + mod._READ_TOOLS + mod._SEARCH_TOOLS + mod._MANY_TOOLS
                     if re.search(m, t)]
            self.assertTrue(tools, f"matcher {m!r} не ловит ни одного инструмента хука")
            self.assertTrue(g.get("sequential"), "блокирующие хуки идут sequential")
        covered = {t for g in self.groups_with("ui-guard") for t in
                   (mod._WRITE_TOOLS + mod._BASH_TOOLS + mod._READ_TOOLS + mod._SEARCH_TOOLS + mod._MANY_TOOLS)
                   if re.search(g["matcher"], t)}
        self.assertEqual(covered, set(mod._WRITE_TOOLS + mod._BASH_TOOLS + mod._READ_TOOLS + mod._SEARCH_TOOLS
                                      + mod._MANY_TOOLS), "инструмент хука, на который не повешен матчер, не форсится")

    def test_the_read_chain_has_the_hook_and_still_the_phase_gate(self):
        read = [g for g in self.groups_with("ui-guard") if "grounding-evidence" in self.names(g)]
        self.assertEqual(len(read), 1)
        self.assertTrue(all(re.search(read[0]["matcher"], t) for t in ("read_file", "Read", "grep_search", "Grep")))
        self.assertIn("gate-guard", self.names(read[0]))

    def test_the_branch_registry_is_the_single_source_for_steps(self):
        mod = _load_hook()
        self.assertEqual(mod.BUILD_STEP, "ui-build")
        self.assertEqual(mod.VERIFY_TO_IMPL, {"ui-verify": "ui-build"})
        self.assertEqual(mod.SKILL, "forgeui")

    def test_the_hook_file_is_stdlib_python39_with_encoding_everywhere(self):
        import ast
        src = HOOK.read_text(encoding="utf-8")
        ast.parse(src, feature_version=9)
        self.assertIn("from __future__ import annotations", src)


class PureFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def test_dsl_head(self):
        m = self.mod
        for head in (b'{"dsl":{"converterVersion":"2.2.13"', b'{ "dsl" : { "converterVersion" : 1', b'\xef\xbb\xbf{"dsl":{"converterVersion"',
                     b'{\r\n  "dsl": {\r\n    "converterVersion"', b'{"converterVersion":"2.2.13","dslVersion"'):
            self.assertTrue(m._dsl_head(head), head)
        for head in (b"", b"{}", b'{"dsl":{"other":1}}', b'{"name":"converterVersion"}', b'[{"dsl":{"converterVersion"',
                     b'{"dslx":{"converterVersion"', b"converterVersion", b'{"schema":1,"tree":{}}'):
            self.assertFalse(m._dsl_head(head), head)

    def test_protected_names(self):
        m = self.mod
        for n in ("visual-requirements.canonical.json", "x.canonical.json", "visual-requirements.pixso.json",
                  "visual-requirements.pixso.raw.json", "visual-requirements.json", "visual-requirements.html",
                  "VISUAL-REQUIREMENTS.JSON"):
            self.assertTrue(m._PROTECTED_NAME.match(n), n)
            self.assertTrue(m._PROTECTED_IN_TEXT.search(f"cat a/{n} | head"), n)
        for n in ("visual-requirements.png", "visual-requirements.jsonl", "canonical.json.md", "plan.json",
                  "requirements.json", "visual-requirements.pixso.png", "my.canonical.yaml", "track.yaml"):
            self.assertFalse(m._PROTECTED_NAME.match(n), n)
            self.assertFalse(m._PROTECTED_IN_TEXT.search(f"cat a/{n}"), n)

    def test_glob_alternatives(self):
        m = self.mod
        self.assertEqual(m._glob_alternatives("*.{md,json}"), ["*.md", "*.json"])
        self.assertEqual(m._glob_alternatives("**/*.md"), ["*.md"])
        self.assertEqual(m._glob_alternatives("./a/*.md"), ["a/*.md"])
        self.assertEqual(m._glob_alternatives(["*.md", " ", None, 5]), ["*.md"])
        self.assertEqual(m._glob_alternatives(None), [])

    def test_glob_to_re_follows_the_runtime_glob(self):
        m = self.mod
        for pat, yes, no in (("*.json", ["a.json"], ["d/a.json"]), ("**/*.json", ["a.json", "d/a.json", ".d/e/a.json"], ["a.js"]),
                             ("src/**/*.tsx", ["src/a.tsx", "src/x/y/a.tsx"], ["lib/a.tsx"]),
                             (".daiver/*/vis*", [".daiver/tracks/visual-x"], [".daiver/tracks/k/visual-x"]),
                             ("a?.md", ["ab.md", "AB.MD"], ["abc.md", "a/b.md"]), ("[ab].md", ["a.md"], ["c.md"]),
                             ("[!ab].md", ["c.md"], ["a.md"]), ("a/**", ["a/b", "a/b/c"], ["b/a"]), ("[z-a]", ["[z-a]"], ["z"])):
            r = m._glob_to_re(pat)
            for y in yes:
                self.assertTrue(r.match(y), (pat, y))
            for n in no:
                self.assertFalse(r.match(n), (pat, n))

    def test_output_dirs(self):
        m = self.mod
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            self.assertEqual(m._output_dirs(root, "src/Modules/"), ["src/modules"])
            self.assertTrue(m._in_output("SRC/modules/a.tsx", ["src/modules"]))
            self.assertTrue(m._in_output("src/modules", ["src/modules"]))
            self.assertFalse(m._in_output("src/modules2/a.tsx", ["src/modules"]))
            self.assertFalse(m._in_output("src/other/a.tsx", ["src/modules"]))
            self.assertTrue(m._in_output("anything/a.tsx", ["."]))

    def test_written_text(self):
        m = self.mod
        self.assertEqual(m._written_text({"content": "a", "new_string": "b", "new_source": "c"}), "a\nb\nc")
        self.assertEqual(m._written_text({"edits": [{"new_string": "x"}, {"old_string": "y"}, "z"]}), "x")
        self.assertEqual(m._written_text({"content": 5}), "")


if __name__ == "__main__":
    unittest.main()
