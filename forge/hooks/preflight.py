#!/usr/bin/env python3
"""
Pre-flight check для feature-pipeline.
Проверяет, что control-plane включён и конфигурация доступна.
Вызывается самым первым при старте пайплайна.

Exit 0 — харнес активен, можно продолжать.
Exit 1 — ENFORCEMENT OFF (essential-хук не подключён / settings / risk-policy).
         Стоп-и-предупреди: сначала deploy, потом заново.
Exit 2 — конфиг не инициализирован (ground/pipeline.json нет/неполон). Нормальный
         первый запуск: инициализируй конфиг и перезапусти preflight до exit 0.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _find_foreign_hook_paths(settings: dict, project_root: str) -> list[str]:
    """Ищет в блоке hooks пути, ведущие за пределы .gigacode/hooks/ текущего проекта."""
    hooks = settings.get("hooks", {})
    found = []
    # ВАЖНО: та же склейка, что в settings.hooks.json/resolve_hook_paths.py —
    # project_root + "/.gigacode/hooks/" прямым слэшем, НЕ os.path.join(). На Windows
    # project_root из Path(...).resolve() — обратные слэши ("C:\Work\..."), а хвост
    # из шаблона — прямые; итоговый путь смешанный. os.path.join() дал бы чисто
    # обратные слэши и никогда бы не совпал с реальным префиксом в command.
    expected_prefix = f"{project_root}/.gigacode/hooks/"

    def _walk(node, path=""):
        if isinstance(node, str) and path.endswith("command"):
            # Хук — последний токен команды (интерпретатор ± "-X utf8" — перед ним).
            m = re.search(r"(\S+\.py)\s*$", node)
            if m:
                p = m.group(1)
                if not p.startswith(expected_prefix):
                    found.append(p)
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(hooks)
    return found


def _referenced_hook_basenames(hooks_block: dict) -> set:
    """Собирает basenames .py-хуков, реально перечисленных в command-полях блока hooks.

    Это и есть «wiring»: рантайм исполняет только то, что здесь. Наличие файла на диске
    НЕ означает, что хук подключён (кейс eval-guard: файл был, в settings — нет)."""
    names: set = set()

    def _walk(node):
        if isinstance(node, str):
            m = re.search(r"([\w.-]+\.py)\b", node)
            if m:
                names.add(m.group(1))
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(hooks_block)
    return names


# Канон-имена инструментов рантайма (TOOL_NAME_ALIASES qwen-code) → цель матчинга хуков.
# Матчер, который их НЕ матчит, молча выключает всю цепочку (BLOCKER-0).
_CANON_SHELL = "run_shell_command"
_CANON_WRITES = ("write_file", "edit", "notebook_edit")


def _group_matcher_for(hooks_block: dict, event: str, hook_py: str) -> str | None:
    """matcher группы события `event`, содержащей хук `hook_py` (или None)."""
    for group in hooks_block.get(event, []):
        for h in group.get("hooks", []):
            if re.search(rf"\b{re.escape(hook_py)}\b", str(h.get("command", ""))):
                return group.get("matcher", "")
    return None


def _check_matchers_canonical(hooks_block: dict, wiring_src: str | None) -> list[str]:
    """Проверяет, что matcher-ы блок-цепочек матчат канон-имена рантайма (мимикрия JS RegExp.test)."""
    errs: list[str] = []
    bash_m = _group_matcher_for(hooks_block, "PreToolUse", "destructive-blocker.py")
    if bash_m is not None and not re.search(bash_m, _CANON_SHELL):
        errs.append(
            f"matcher Bash-цепочки {bash_m!r} в {wiring_src} НЕ матчит канон-имя "
            f"{_CANON_SHELL!r} → destructive/pii/sod/gate на shell не сработают (BLOCKER-0). "
            f"Ожидается напр. ^(run_shell_command|Bash)$."
        )
    write_m = _group_matcher_for(hooks_block, "PreToolUse", "tdd-guard.py")
    if write_m is not None:
        missing = [n for n in _CANON_WRITES if not re.search(write_m, n)]
        if missing:
            errs.append(
                f"matcher Write/Edit-цепочки {write_m!r} в {wiring_src} НЕ матчит канон-имена "
                f"{missing} → tdd/eval/sod/gate/state-write на записи не сработают (BLOCKER-0)."
            )
    return errs


# Минимальная версия Python (копия doctor.MIN_PYTHON; пинится test_doctor). Скрипты/хуки
# используют синтаксис 3.10+ (PEP604 `X | None`, match); на 3.9 phase_sync падал.
MIN_PYTHON = (3, 10)


def preflight(project_root: str) -> dict:
    errors = []
    warnings = []
    # «Ещё не инициализирован» — отдельный класс, НЕ enforcement off. Держим вне errors,
    # чтобы deploy сразу после раскатки не выглядел провальным, но passed остаётся False
    # (гейт арминга: субагентов не поднимать, пока конфиг не создан). Различие errors/init
    # маппится на exit-код: 1 = enforcement off, 2 = инициализируй и перезапусти.
    init_needed = []

    # 0. Версия Python (раньше всего — иначе doctor/скрипты упадут с невнятным импорт-эррором)
    if sys.version_info[:2] < MIN_PYTHON:
        have = f"{sys.version_info.major}.{sys.version_info.minor}"
        warnings.append(
            f"Python {have}: пайплайн требует {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
            f"(PEP604/match). Часть скриптов/хуков может падать. Обнови интерпретатор."
        )

    # 1. pipeline.json — конфиг control-plane. Отсутствие/неполнота — это «нормальный первый
    #    запуск» (файл создаёт init_pipeline_config.py, а preflight бежит ДО него), а не поломка
    #    энфорсмента → init_needed, а не errors. Битый JSON (не пустой, а невалидный) — уже реальная
    #    ошибка: конфиг есть, но рантайм его не прочитает → errors.
    pipeline_json = Path(project_root) / "ground" / "pipeline.json"
    if not pipeline_json.exists():
        init_needed.append("ground/pipeline.json not found — конфигурация не инициализирована")
    else:
        try:
            cfg = json.loads(pipeline_json.read_text(encoding="utf-8"))
            if cfg.get("_incomplete"):
                init_needed.append(f"pipeline.json incomplete: {cfg['_incomplete']}")
        except json.JSONDecodeError as e:
            errors.append(f"pipeline.json parse error: {e}")

    # 2. settings.hooks.json
    hooks_settings = Path(project_root) / ".gigacode" / "hooks" / "settings.hooks.json"
    if not hooks_settings.exists():
        errors.append(
            ".gigacode/hooks/settings.hooks.json not found — хуки не задеплоены"
        )
    else:
        try:
            hs = json.loads(hooks_settings.read_text(encoding="utf-8"))
            hooks_block = hs.get("hooks", {})
            if not hooks_block:
                errors.append("hooks block is empty — enforcement inactive")
        except json.JSONDecodeError as e:
            errors.append(f"settings.hooks.json parse error: {e}")

    # 3. Проверка ключевых хуков: ФАЙЛ на диске + РЕАЛЬНОЕ подключение (wiring).
    #    Раньше проверялось только наличие файла → eval-guard лежал на диске, но не был
    #    в settings.json, и preflight давал зелёный свет при выключенном enforcement.
    essential_hooks = [
        "gate-guard.py",
        "phase-gate.py",
        "state-recorder.py",
        "eval-guard.py",
        "state-write-guard.py",
        "log-agent.py",
    ]
    hooks_dir = Path(project_root) / ".gigacode" / "hooks"
    for hook in essential_hooks:
        if not (hooks_dir / hook).exists():
            errors.append(f"hook not found: .gigacode/hooks/{hook}")

    # Источник истины wiring — задеплоенный settings.json (его читает рантайм); если его ещё
    # нет — эталон settings.hooks.json (он будет развёрнут).
    settings_json_p = Path(project_root) / ".gigacode" / "settings.json"
    hooks_template_p = Path(project_root) / ".gigacode" / "hooks" / "settings.hooks.json"
    wiring_src, wiring_block = None, None
    for cand in (settings_json_p, hooks_template_p):
        if cand.exists():
            try:
                wiring_block = json.loads(cand.read_text(encoding="utf-8")).get("hooks", {})
                wiring_src = cand.name
                break
            except (json.JSONDecodeError, OSError):
                continue
    if wiring_block is not None:
        referenced = _referenced_hook_basenames(wiring_block)
        for hook in essential_hooks:
            if hook not in referenced:
                errors.append(
                    f"essential hook НЕ подключён в {wiring_src}: {hook} "
                    f"(файл есть, но рантайм его не вызывает → enforcement off для этого хука)"
                )
        # 3a. Матчеры PreToolUse-цепочек ДОЛЖНЫ матчить КАНОНИЧЕСКИЕ имена инструментов рантайма
        #     (run_shell_command/write_file/edit), а не Claude-нотацию (^Bash$/Write|Edit). Иначе
        #     блок-хуки не попадают в execution-plan и весь deny-first молчит (BLOCKER-0). Рантайм
        #     матчит как new RegExp(matcher).test(canonicalToolName) — здесь мимикрия через re.search.
        errors.extend(_check_matchers_canonical(wiring_block, wiring_src))

    # 3b. risk-policy.json должен существовать и парситься — иначе risk_ladder тихо
    #     деградирует до R1-auto («allow all»). Fail-closed на уровне готовности.
    risk_policy_p = Path(project_root) / ".gigacode" / "hooks" / "risk-policy.json"
    if not risk_policy_p.exists():
        errors.append(".gigacode/hooks/risk-policy.json not found — risk ladder выключится (fail-open)")
    else:
        try:
            json.loads(risk_policy_p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"risk-policy.json parse error: {e} — risk ladder деградирует до allow-all")

    # 4. Проверка settings.json (потребляется рантаймом)
    settings_json = Path(project_root) / ".gigacode" / "settings.json"
    resolver_script = Path(project_root) / ".gigacode" / "hooks" / "resolve_hook_paths.py"
    deploy_script = Path(project_root) / ".gigacode" / "deploy-local.sh"

    if settings_json.exists():
        try:
            stg = json.loads(settings_json.read_text(encoding="utf-8"))
            hooks_block = stg.get("hooks", {})
            if not hooks_block:
                errors.append(
                    ".gigacode/settings.json: hooks block is empty — enforcement inactive"
                )
            else:
                foreign = _find_foreign_hook_paths(stg, project_root)
                if foreign:
                    errors.append(
                        f".gigacode/settings.json: обнаружены пути к хукам вне проекта: "
                        f"{foreign}. Запусти bash .gigacode/deploy-local.sh для исправления."
                    )
        except json.JSONDecodeError as e:
            errors.append(f".gigacode/settings.json parse error: {e}")
    else:
        # settings.json не существует — предупреждаем, но не блокируем (если есть эталон)
        if resolver_script.exists():
            warnings.append(
                ".gigacode/settings.json не найден. Запусти bash .gigacode/deploy-local.sh "
                "для генерации из .gigacode/hooks/settings.hooks.json"
            )
        else:
            errors.append(
                ".gigacode/settings.json не найден. "
                "Скопируй .gigacode/hooks/settings.hooks.json в .gigacode/settings.json "
                "или создай resolve_hook_paths.py."
            )

    # 5. Проверка resolve_hook_paths.py
    if resolver_script.exists():
        try:
            import subprocess
            res = subprocess.run(
                [sys.executable, "-X", "utf8", str(resolver_script),
                 "--check", "--project", project_root],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            if res.returncode != 0:
                out = res.stdout.strip()
                if out:
                    try:
                        detail = json.loads(out)
                        for iss in detail.get("issues", []):
                            errors.append(f"hook path check: {iss}")
                    except json.JSONDecodeError:
                        errors.append(f"resolve_hook_paths.py --check вернул ошибку: {out}")
                else:
                    errors.append("resolve_hook_paths.py --check exit != 0 (см. stderr)")
        except Exception as e:
            warnings.append(f"resolve_hook_paths.py --check не выполнен: {e}")
    else:
        if deploy_script.exists():
            warnings.append(
                "resolve_hook_paths.py не найден. "
                "Пути в settings.hooks.json могут быть неактуальными."
            )

    # 6. doctor.py — self-check целостности пайплайна (advisory: предупреждение, не блок)
    doctor_script = (Path(project_root) / ".gigacode" / "skills" / "feature-pipeline"
                     / "scripts" / "doctor.py")
    if doctor_script.exists():
        try:
            import subprocess
            res = subprocess.run(
                [sys.executable, "-X", "utf8", str(doctor_script),
                 "--project", project_root, "--json"],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            try:
                detail = json.loads(res.stdout) if res.stdout.strip() else {}
            except json.JSONDecodeError:
                detail = {}
            if res.returncode == 1:
                if detail.get("problems"):
                    for prob in detail["problems"]:
                        # Битые межскилловые пути (skill-paths.json) — ЖЁСТКАЯ ошибка: гейты,
                        # которые скиллы зовут по этим путям, молча отвалятся в рантайме
                        # (например forgelite → minor-defect-fix/scripts/check_coverage.py).
                        if str(prob).startswith("registry-paths-exist"):
                            errors.append(f"doctor: {prob}")
                        else:
                            warnings.append(f"doctor: {prob}")
                else:
                    warnings.append("doctor: обнаружены проблемы целостности (см. doctor.py)")
            elif res.returncode == 2:
                warnings.append(f"doctor: не выполнен ({res.stderr.strip()[:200]})")
            # средовые/конфиг-советы doctor (Python/git/config) — даже при exit 0
            for w in detail.get("warnings", []):
                warnings.append(f"doctor: {w}")
        except Exception as e:
            warnings.append(f"doctor.py не выполнен: {e}")

    # passed=True требует и отсутствия enforcement-ошибок, и инициализированного конфига —
    # гейт арминга остаётся жёстким. Но init_needed отделён от errors для разного exit-кода.
    passed = len(errors) == 0 and len(init_needed) == 0
    result = {"passed": passed, "errors": errors}
    if init_needed:
        result["init_needed"] = init_needed
    if warnings:
        result["warnings"] = warnings
    return result


if __name__ == "__main__":
    project_root = Path.cwd()
    for arg in sys.argv[1:]:
        if arg in ("-h", "--help"):
            print("Usage: python preflight.py [project_root]")
            print("       python preflight.py --project <path>")
            sys.exit(0)
        if arg in ("--project", "-p"):
            continue
        if arg.startswith("-") and not arg.startswith("--project="):
            continue
        project_root = Path(arg).resolve()
    if any(a == "--project" for a in sys.argv[1:]):
        idx = sys.argv.index("--project")
        if idx + 1 < len(sys.argv):
            project_root = Path(sys.argv[idx + 1]).resolve()
    result = preflight(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # exit 0 — армирован; 1 — enforcement off (реальные errors, стоп-и-предупреди);
    # 2 — только «не инициализирован» (init_needed): инициализируй конфиг и перезапусти.
    if result["passed"]:
        sys.exit(0)
    sys.exit(1 if result["errors"] else 2)