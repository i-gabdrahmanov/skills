#!/usr/bin/env python3
"""check_tech_sdd.py — гейт фазы tech-diag: tech-sdd.md + change-plan.json техзадачи.

Техзадача описывается не бизнес-сценариями (Given-When-Then), а целевым состоянием и
инвариантами (tasks/018 §3): что было → что стало, в каких файлах, что обязано остаться, как
откатить. Документ — для человека (`tech-sdd.md`), план — для гейтов (`change-plan.json`).
Этот гейт проверяет, что оба собраны так, что по ним можно утверждать и сторожить правку.

Что проверяем (детерминированно, без модели):
  1. tech-sdd.md: обязательные разделы есть и не пусты; строка «Архетип» совпадает с планом
     (и с решением inputs.archetype, если передан --archetype).
  2. change-plan.json: схема (_techlib.validate_plan) — архетип, явные targets без глобов,
     цели с from/to, пробы, инварианты (обязательный минимум архетипа), откат, «вне репо».
  3. Скоуп: каждый target существует (новый файл — только если перечислен в new_files), не
     R5-путь; цели лежат в файлах своего вида (config → application*, deps/build → билд-файл).
  4. Архетип: config-change — только config-цели; dependency-upgrade — есть deps/build-цель и
     раздел рисков с таблицей breaking changes, где у КАЖДОЙ строки есть решение, и ссылкой на
     источник (release notes / migration guide). Пустой раздел = «не читали release notes».

Usage:
    check_tech_sdd.py <task-dir> [--project <root>] [--archetype <a>] [--json]
Exit: 0 — pass; 1 — есть ошибки; 2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _techlib as T  # noqa: E402

# Раздел → подстроки заголовка (любая подходит). Порядок — как в шаблоне.
REQUIRED_SECTIONS = (
    ("Цель и мотивация", ("цель",)),
    ("Текущее → целевое", ("текущее", "целевое")),
    ("Скоуп (targets)", ("скоуп", "targets")),
    ("Инварианты", ("инвариант",)),
    ("Ожидаемые изменения", ("ожидаемые изменения",)),
    ("Вне репозитория", ("вне репозитория",)),
    ("Риски / breaking changes", ("риск", "breaking")),
    ("Откат", ("откат",)),
)
_CONFIG_FILE = re.compile(r"(^|/)application[^/]*\.(ya?ml|properties)$")
_BUILD_FILE = re.compile(r"(^|/)(pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|"
                         r"gradle\.properties|libs\.versions\.toml)$")


def split_sections(md: str) -> dict:
    """{заголовок ## (lower): тело}."""
    out: dict = {}
    cur = None
    for line in md.splitlines():
        m = re.match(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", line)
        if m and not line.startswith("###"):
            cur = m.group(1).strip().lower()
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _find(sections: dict, needles) -> tuple:
    for title, body in sections.items():
        if any(n in title for n in needles):
            return title, body
    return None, None


def _meaningful(body: str) -> bool:
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    text = re.sub(r"[\s|\-:*_>`#]+", " ", text).strip()
    return len(text) >= 3


def _table_rows(body: str) -> list:
    rows = []
    for line in (body or "").splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # разделитель шапки
        rows.append(cells)
    return rows[1:] if rows else []   # без строки заголовков


def check(task_dir: Path, project: Path, archetype=None) -> list:
    errs: list = []
    sdd = task_dir / T.SDD_FILE
    if not sdd.exists():
        return [f"нет {sdd} — документ техзадачи пишет фаза tech-diag под этим именем"]
    md = sdd.read_text(encoding="utf-8")
    sections = split_sections(md)
    for label, needles in REQUIRED_SECTIONS:
        title, body = _find(sections, needles)
        if title is None:
            errs.append(f"tech-sdd.md: нет раздела «{label}»")
        elif not _meaningful(body):
            errs.append(f"tech-sdd.md: раздел «{label}» пуст")

    try:
        plan = T.load_plan(task_dir / T.PLAN_FILE)
    except T.TechError as e:
        return errs + [str(e)]
    errs += [f"change-plan.json: {e}" for e in T.validate_plan(plan)]
    arch = plan.get("archetype")
    m = re.search(r"\*\*Архетип:?\*\*:?\s*`?([\w-]+)", md)
    if not m:
        errs.append("tech-sdd.md: нет строки «**Архетип:** <архетип>» в шапке")
    elif m.group(1) != arch:
        errs.append(f"tech-sdd.md: архетип «{m.group(1)}» не совпадает с планом «{arch}»")
    if archetype and archetype != arch:
        errs.append(f"архетип плана «{arch}» не совпадает с решением inputs.archetype «{archetype}» "
                    f"— сменить архетип можно только новым ответом пользователя")

    targets = [t for t in plan.get("targets") or [] if isinstance(t, str)]
    new_files = set(plan.get("new_files") or [])
    for t in new_files - set(targets):
        errs.append(f"new_files: '{t}' нет в targets")
    try:
        import risk_ladder as R
    except Exception:  # noqa: BLE001
        R = None
    for t in targets:
        if not (project / t).exists() and t not in new_files:
            errs.append(f"target '{t}' не существует (новый файл перечисли в new_files)")
        if R is not None:
            try:
                lvl = R.classify("Write", {"file_path": t}, str(project))["level"]
            except Exception:  # noqa: BLE001
                lvl = None
            if lvl == "R5":
                errs.append(f"target '{t}' — R5-путь (change-advisory): техзадачей не правится")
    changes = [c for c in plan.get("expected_changes") or [] if isinstance(c, dict)]
    kinds = {c.get("kind") for c in changes if T.is_target_change(c)}
    if "config" in kinds and not any(_CONFIG_FILE.search(t) for t in targets):
        errs.append("есть config-цели, но ни одного application*.yml|properties в targets")
    if kinds & {"deps", "build"} and not any(_BUILD_FILE.search(t) for t in targets):
        errs.append("есть deps/build-цели, но ни одного билд-файла (pom.xml/build.gradle/"
                    "libs.versions.toml/gradle.properties) в targets")

    if arch == "config-change":
        if kinds - {"config"}:
            errs.append(f"config-change: цели только вида config, а есть {sorted(kinds - {'config'})}"
                        f" — апгрейд версий это dependency-upgrade")
        if "config" not in kinds:
            errs.append("config-change: нет ни одной config-цели (key/profile/from/to)")
    elif arch == "dependency-upgrade":
        if not kinds & {"deps", "build"}:
            errs.append("dependency-upgrade: нет ни одной deps/build-цели с from/to")
        title, body = _find(sections, ("риск", "breaking"))
        if title is not None:
            rows = _table_rows(body)
            if not rows:
                errs.append("dependency-upgrade: в разделе рисков нет таблицы breaking changes "
                            "(| изменение | касается нас? | решение |) — пустой раздел значит, что "
                            "release notes не читали")
            for i, cells in enumerate(rows, 1):
                if any(not c for c in cells):
                    errs.append(f"dependency-upgrade: строка {i} таблицы рисков с пустой ячейкой — "
                                f"решение обязательно по КАЖДОМУ пункту")
            if not re.search(r"https?://", body or ""):
                errs.append("dependency-upgrade: в разделе рисков нет ссылки на источник "
                            "(release notes / migration guide)")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task_dir", help="каталог техзадачи (skill_paths.py tech-docs --feature <KEY>)")
    ap.add_argument("--project", default=None, help="корень репо кода")
    ap.add_argument("--archetype", help="решение inputs.archetype — архетип плана обязан совпасть")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    task_dir = Path(args.task_dir)
    if not task_dir.is_dir():
        print(f"[check_tech_sdd] ERROR: нет каталога {task_dir}", file=sys.stderr)
        return 2
    errs = check(task_dir, T.project_root(args.project), args.archetype)
    if args.json:
        print(json.dumps({"passed": not errs, "errors": errs}, ensure_ascii=False, indent=2))
    if errs:
        print("[check_tech_sdd] FAIL:\n  - " + "\n  - ".join(errs), file=sys.stderr)
        return 1
    if not args.json:
        print(json.dumps({"passed": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
