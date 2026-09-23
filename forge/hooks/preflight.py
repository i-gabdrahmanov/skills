#!/usr/bin/env python3
"""
Pre-flight check для feature-pipeline.
Проверяет, что control-plane включён и конфигурация доступна.
Вызывается самым первым при старте пайплайна.

Единственная поддерживаемая раскладка — project: код в `<project>/.gigacode/{hooks,skills}`,
подключение хуков — `<project>/.gigacode/settings.json` (генерится resolve_hook_paths.py).
Ставится через `bash deploy.sh <project>` (см. INSTALL.md).

Exit 0 — харнес активен, можно продолжать.
Exit 1 — ENFORCEMENT OFF (essential-хук не подключён / settings / risk-policy).
         Стоп-и-предупреди: deploy ещё раз, потом заново.
Exit 2 — конфиг не инициализирован (ground/pipeline.json нет/неполон). Нормальный
         первый запуск: инициализируй конфиг и перезапусти preflight до exit 0.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """Корень проекта для ДАННЫХ (ground/, docs/): вверх от cwd по live-маркерам.

    Backwards-compat wrapper: delegates the algorithm to
    :func:`_config_loader.find_project_root` (Phase 0 v2 consolidation). The
    inline implementation lived here before to keep preflight self-contained
    when it was copied standalone into ``<project>/.gigacode/hooks/``; now that
    ``_config_loader`` ships next to preflight on every deploy, the duplication
    is removed and behaviour stays identical (.git → build.gradle/… → ground/<marker>,
    fallback to cwd on miss).
    """
    start = Path.cwd()
    if not start.is_absolute():
        start = start.resolve()
    from _config_loader import find_project_root as _canonical_find_project_root
    return _canonical_find_project_root(start) or start


# Ключевые хуки: файл на диске + РЕАЛЬНОЕ подключение (wiring). Раньше проверялось только
# наличие файла → eval-guard лежал на диске, но не был в settings.json, и preflight давал
# зелёный свет при выключенном enforcement.
ESSENTIAL_HOOKS = [
    "gate-guard.py",
    "phase-gate.py",
    "state-recorder.py",
    "eval-guard.py",
    "state-write-guard.py",
    "grounding-evidence.py",
]


def _home() -> Path:
    """Домашний каталог (точка подмены в тестах)."""
    return Path.home()


def _self_base() -> Path:
    """База кода, из которой запущен ЭТОТ preflight.

    Файл лежит в <base>/hooks/preflight.py → база = parents[1]: <project>/.gigacode
    (deploy) либо корень source-репо (запуск из репозитория разработки).
    """
    return Path(__file__).resolve().parents[1]


def resolve_layout(project_root, self_base=None) -> dict:
    """Где физически лежит код форжа, который грузит рантайм.

    Project-раскладка: код в `<project>/.gigacode`, проверка по наличию
    `settings.hooks.json` или хотя бы одного essential-хука. Если деплой есть —
    база = `<project>/.gigacode`; если нет — `mode == "none"` и сообщения ниже
    подскажут путь установки.
    """
    proj_base = Path(project_root) / ".gigacode"
    base = Path(self_base).resolve() if self_base is not None else _self_base()
    deployed = ((proj_base / "hooks" / "settings.hooks.json").exists()
                or (proj_base / "hooks" / "gate-guard.py").exists())
    if deployed:
        return {"mode": "project", "base": proj_base, "shadow": None}
    # Деплой не найден — сообщения ниже подскажут путь установки.
    return {"mode": "none", "base": proj_base, "shadow": None}


def _find_foreign_hook_paths(settings: dict, project_root=None, prefixes=None) -> list[str]:
    """Ищет в блоке hooks пути к хукам вне ожидаемого каталога.

    По умолчанию (project-раскладка) ожидается project_root + "/.gigacode/hooks/" — та же
    склейка, что в settings.hooks.json/resolve_hook_paths.py. Сравниваем через прямые слэши
    с обеих сторон: resolve_hook_paths.py подставляет в command прямой слэш (backslash рантайм
    съедал при POSIX-разборе), а project_root тут из Path(...).resolve() — на Windows обратные
    слэши. Без нормализации свои же хуки ложно попали бы в foreign, и preflight зациклил бы
    совет «запусти deploy-local.sh». os.path.join не годится: дал бы чисто обратные слэши,
    никогда не совпал бы с реальным префиксом в command.

    `prefixes` — явный список допустимых префиксов (на случай, если проверяется чужой
    блок wiring; в обычном пути используется project-prefix по умолчанию).
    """
    hooks = settings.get("hooks", {})
    found = []
    if prefixes is None:
        prefixes = [f"{project_root}/.gigacode/hooks/"]
    prefixes = [p.replace("\\", "/") for p in prefixes]

    def _walk(node, path=""):
        if isinstance(node, str) and path.endswith("command"):
            # Хук — последний токен команды (интерпретатор ± "-X utf8" — перед ним).
            m = re.search(r"(\S+\.py)\s*$", node)
            if m:
                p = m.group(1)
                if not any(p.replace("\\", "/").startswith(pref) for pref in prefixes):
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


def _iter_hook_commands(hooks_block: dict) -> list:
    """Все строки `command` из блока hooks — то, что рантайм реально запустит."""
    out = []

    def _walk(node, path=""):
        if isinstance(node, str) and path.endswith("command"):
            out.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(hooks_block)
    return out


def _split_command(command: str):
    """(интерпретатор, путь_скрипта) из строки command. Любой элемент может быть None."""
    script = None
    m = re.search(r"(\S+\.py)\s*$", command or "")
    if m:
        script = m.group(1)
    interp = None
    mi = re.match(r'\s*(?:"([^"]+)"|(\S+))', command or "")
    if mi:
        interp = mi.group(1) or mi.group(2)
    return interp, script


def _check_commands_runnable(hooks_block: dict, wiring_src: str, hooks_dir, errors: list) -> None:
    """Файл хука по пути из command существует, и интерпретатор грузит бандл.

    Зачем отдельная проверка. `python3` на НЕСУЩЕСТВУЮЩИЙ файл возвращает **exit 2**, а в
    протоколе хуков это «блокировать». То есть один устаревший путь в settings.json кирпичит
    сессию целиком: каждый Bash/Write отклоняется сообщением `can't open file`, без единого
    упоминания forge, и починка тоже идёт через Bash. Прежний `_find_foreign_hook_paths`
    ловил только пути ВНЕ проекта — устаревший путь ВНУТРИ проекта (переименованный хук,
    недокопированный деплой) проходил как свой.

    Второй слой — интерпретатор: битый python (3.8, сломанный expat) валит импорт бандла, и
    все блокирующие хуки уходят в fail-CLOSED deny на каждом вызове."""
    import subprocess

    missing, checked_interps = [], set()
    for command in _iter_hook_commands(hooks_block):
        interp, script = _split_command(command)
        if script and not Path(script).exists():
            missing.append(script)
        if not interp or interp in checked_interps:
            continue
        checked_interps.add(interp)
        # Относительное имя (python3) резолвит PATH рантайма — проверить нечего.
        if "/" not in interp and "\\" not in interp:
            continue
        if not Path(interp).exists():
            errors.append(
                f"{wiring_src}: интерпретатор хуков не найден: {interp}. Каждый вызов "
                f"инструмента будет отклонён (rc=2). Запусти bash .gigacode/deploy-local.sh"
            )
            continue
        try:
            r = subprocess.run(
                [interp, "-X", "utf8", "-c", "import risk_ladder, _project, forge_events"],
                cwd=str(hooks_dir), capture_output=True, text=True, timeout=30,
            )
        except Exception as e:  # noqa: BLE001 — диагностика, не enforcement
            errors.append(f"{wiring_src}: не удалось проверить интерпретатор {interp}: {e}")
            continue
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
            errors.append(
                f"{wiring_src}: интерпретатор {interp} не грузит бандл forge ({tail[0]}). "
                f"Все блокирующие хуки уйдут в fail-closed deny. "
                f"Смени python в settings.json и перезапусти bash .gigacode/deploy-local.sh"
            )
    if missing:
        errors.append(
            f"{wiring_src}: в командах хуков указаны несуществующие файлы: {sorted(set(missing))}. "
            f"python вернёт rc=2 на КАЖДЫЙ вызов инструмента — сессия будет заблокирована "
            f"целиком. Запусти bash .gigacode/deploy-local.sh"
        )


# Базовые каталоги настроек, из которых РАНТАЙМ читает свой settings.json. `.gigacode` —
# форк GigaCode (наша цель деплоя), `.qwen` — стоковый qwen-code (SETTINGS_DIRECTORY_NAME).
_RUNTIME_SETTINGS_DIRS = (".gigacode", ".qwen")


def _carries_forge_hooks(settings_path) -> bool:
    """Есть ли в settings.json хоть один essential-хук forge."""
    try:
        block = json.loads(Path(settings_path).read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return False
    return bool(_referenced_hook_basenames(block) & set(ESSENTIAL_HOOKS))


def _check_policy_drift(project_root, raw_cfg: dict, warnings: list) -> None:
    """policy.json правили ПОД живым прогоном? Прогон идёт по своему снимку — скажи об этом.

    Прогон фиксирует политику на старте (init.py кладёт policy_snapshot/policy_digest), и
    гейты/судьи читают именно снимок. Это защита от «шаги 1-5 закрылись под coverage 80%,
    шаги 6-10 — под 50%». Но молчаливая защита путает: оператор правит policy.json, ничего
    не меняется, и причина ниоткуда не видна. Здесь она становится видна.

    Только WARNING: расхождение легитимно (готовим настройку для следующего прогона), а
    применить сейчас — `config.py repin`.
    """
    try:
        from _config_loader import policy_digest, run_is_live
        from _project import load_active_manifest
        _mp, manifest = load_active_manifest(Path(project_root))
    except Exception:  # noqa: BLE001 — диагностика не должна ронять preflight
        return
    if not manifest or not run_is_live(manifest):
        return
    saved = manifest.get("policy_digest")
    if not saved or saved == policy_digest(raw_cfg):
        return
    warnings.append(
        f"policy.json изменён после старта прогона "
        f"{manifest.get('skill', '?')}/{manifest.get('feature', '?')}: прогон идёт по снимку, "
        f"сделанному на init.py, и правка к нему НЕ применится. Применить сейчас: "
        f"config.py repin --skill {manifest.get('skill', '<S>')} "
        f"--feature {manifest.get('feature', '<F>')}"
    )


def _check_runtime_settings_dirs(project_root, base, warnings: list) -> None:
    """Рантайм читает settings.json ИЗ СВОЕГО базового каталога — и он может быть не тем,
    куда мы задеплоились.

    Слепое пятно, найденное e2e-прогоном: `deploy.sh` кладёт всё в `<project>/.gigacode/` и
    туда же пишет `settings.json`, а стоковый qwen-code читает `<project>/.qwen/settings.json`
    (`SETTINGS_DIRECTORY_NAME = ".qwen"`; строки `.gigacode` в его бандле нет вовсе). Все
    прежние проверки preflight смотрели только в каталог деплоя — то есть на стоковом qwen
    выдавали `errors: []` при том, что рантайм не загружал НИ ОДНОГО хука. Зелёный preflight
    при полностью снятом enforcement — ровно тот класс тишины, против которого построен
    остальной харнес.

    На форке GigaCode расхождения нет по построению (базовый каталог и есть `.gigacode`),
    поэтому предупреждаем только когда рядом реально существует ВТОРОЙ каталог настроек и
    хуков forge в нём нет. Уровень — warning, а не error: какой рантайм запускает оператор,
    отсюда не видно, и ронять деплой из-за постороннего `.qwen/` неправильно."""
    root = Path(project_root)
    base_name = Path(base).name
    for name in _RUNTIME_SETTINGS_DIRS:
        if name == base_name:
            continue
        d = root / name
        if not d.is_dir():
            continue                       # такого рантайма рядом нет — молчим
        settings = d / "settings.json"
        if settings.exists() and _carries_forge_hooks(settings):
            continue                       # второй рантайм тоже армлен — всё в порядке
        warnings.append(
            f"рядом есть каталог настроек другого рантайма `{name}/`, и хуков forge в "
            f"{name}/settings.json нет. Если проект запускается ИМ, а не рантаймом с базой "
            f"`{base_name}`, то enforcement для него ВЫКЛЮЧЕН целиком (рантайм читает только "
            f"свой settings.json). Из своего каталога он читает и `commands/`, и `skills/` — "
            f"значит слэш-команды (/forge, /forge-fix) и SKILL.md брифы фаз для него тоже не "
            f"существуют, то есть выключен не только enforcement, но и весь guidance-слой. "
            f"Проверь, каким CLI ты запускаешь проект; если тем — перенеси в {name}/ блок "
            f"`hooks` из {base_name}/settings.json (пути к хукам абсолютные, править не нужно) "
            f"плюс `commands/` и `skills/`."
        )


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


def _check_wiring(hooks_block: dict, wiring_src: str, prefixes: list[str] | None,
                  errors: list[str]) -> None:
    """Общая для обеих раскладок проверка подключения: essential-хуки перечислены,
    матчеры матчат канон-имена, пути ведут в ожидаемый каталог.

    `prefixes=None` — проверку путей не делать (project-раскладка проверяет их отдельно,
    на живом settings.json, и даёт свой совет про deploy-local.sh)."""
    referenced = _referenced_hook_basenames(hooks_block)
    for hook in ESSENTIAL_HOOKS:
        if hook not in referenced:
            errors.append(
                f"essential hook НЕ подключён в {wiring_src}: {hook} "
                f"(файл есть, но рантайм его не вызывает → enforcement off для этого хука)"
            )
    # Матчеры PreToolUse-цепочек ДОЛЖНЫ матчить КАНОНИЧЕСКИЕ имена инструментов рантайма
    # (run_shell_command/write_file/edit), а не Claude-нотацию (^Bash$/Write|Edit). Иначе
    # блок-хуки не попадают в execution-plan и весь deny-first молчит (BLOCKER-0). Рантайм
    # матчит как new RegExp(matcher).test(canonicalToolName) — здесь мимикрия через re.search.
    errors.extend(_check_matchers_canonical(hooks_block, wiring_src))
    if prefixes is None:
        return
    foreign = _find_foreign_hook_paths({"hooks": hooks_block}, prefixes=prefixes)
    if foreign:
        errors.append(
            f"{wiring_src}: обнаружены пути к хукам вне ожидаемого каталога: {foreign}. "
            f"Ожидается префикс {prefixes[0]!r}."
        )


def _check_risk_policy(hooks_dir: Path, label: str, errors: list[str]) -> None:
    """risk-policy.json должен существовать и парситься — иначе risk_ladder тихо
    деградирует до R1-auto («allow all»). Fail-closed на уровне готовности."""
    risk_policy_p = hooks_dir / "risk-policy.json"
    if not risk_policy_p.exists():
        errors.append(f"{label}/risk-policy.json not found — risk ladder выключится (fail-open)")
        return
    try:
        json.loads(risk_policy_p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"risk-policy.json parse error: {e} — risk ladder деградирует до allow-all")


def _check_project_layout(base: Path, project_root, errors: list[str],
                          warnings: list[str]) -> None:
    """Project-раскладка deploy.sh: код в <project>/.gigacode, wiring — settings.json."""
    hooks_dir = base / "hooks"
    hooks_settings = hooks_dir / "settings.hooks.json"
    if not hooks_settings.exists():
        errors.append(".gigacode/hooks/settings.hooks.json not found — хуки не задеплоены")
    else:
        try:
            hs = json.loads(hooks_settings.read_text(encoding="utf-8"))
            if not hs.get("hooks", {}):
                errors.append("hooks block is empty — enforcement inactive")
        except json.JSONDecodeError as e:
            errors.append(f"settings.hooks.json parse error: {e}")

    for hook in ESSENTIAL_HOOKS:
        if not (hooks_dir / hook).exists():
            errors.append(f"hook not found: .gigacode/hooks/{hook}")

    # Источник истины wiring — задеплоенный settings.json (его читает рантайм); если его ещё
    # нет — эталон settings.hooks.json (он будет развёрнут).
    settings_json = base / "settings.json"
    wiring_src, wiring_block = None, None
    for cand in (settings_json, hooks_settings):
        if cand.exists():
            try:
                wiring_block = json.loads(cand.read_text(encoding="utf-8")).get("hooks", {})
                wiring_src = cand.name
                break
            except (json.JSONDecodeError, OSError):
                continue
    if wiring_block is not None:
        # prefixes=None: пути проверяются ниже, на живом settings.json (в эталоне
        # settings.hooks.json они ещё в виде плейсхолдеров ${PROJECT_ROOT}).
        _check_wiring(wiring_block, wiring_src, None, errors)

    _check_risk_policy(hooks_dir, ".gigacode/hooks", errors)

    resolver_script = hooks_dir / "resolve_hook_paths.py"
    deploy_script = base / "deploy-local.sh"

    if settings_json.exists():
        try:
            stg = json.loads(settings_json.read_text(encoding="utf-8"))
            if not stg.get("hooks", {}):
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
                # Пути в живом settings.json уже резолвнуты — здесь и только здесь можно
                # проверить, что файлы существуют и интерпретатор грузит бандл.
                _check_commands_runnable(stg.get("hooks", {}), ".gigacode/settings.json",
                                         hooks_dir, errors)
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

    # Проверка resolve_hook_paths.py
    if resolver_script.exists():
        try:
            import subprocess
            res = subprocess.run(
                [sys.executable, "-X", "utf8", str(resolver_script),
                 "--check", "--project", str(project_root)],
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


def _run_doctor(base: Path, project_root, errors: list[str], warnings: list[str]) -> None:
    """doctor.py — self-check целостности пайплайна (advisory: предупреждение, не блок)."""
    doctor_script = base / "skills" / "feature-pipeline" / "scripts" / "doctor.py"
    if not doctor_script.exists():
        return
    try:
        import subprocess
        res = subprocess.run(
            [sys.executable, "-X", "utf8", str(doctor_script),
             "--project", str(project_root), "--json"],
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


# Минимальная версия Python (копия doctor.MIN_PYTHON; пинится test_doctor).
# Пол = 3.9: весь код парсится под 3.9 и все тесты на нём зелёные. Раньше здесь стояло 3.10
# «из-за PEP604/match», но match нигде нет, а PEP604 живёт только в аннотациях и лениво
# вычисляется через `from __future__ import annotations`. Инвариант держит test_python_floor.py
# (парсинг всего дерева под полом + обязательный future-импорт там, где есть `X | None`).
MIN_PYTHON = (3, 9)


def preflight(project_root: str, self_base=None) -> dict:
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
            f"Python {have}: пайплайн требует {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+. "
            f"Часть скриптов/хуков может падать. Обнови интерпретатор."
        )

    # 1. Конфиг control-plane (v2: ground/policy.json, legacy: ground/pipeline.json).
    #    ДАННЫЕ всегда в проекте, раскладка кода на это не влияет. Отсутствие — «нормальный
    #    первый запуск» (файл создаёт init_pipeline_config.py, а preflight бежит ДО него),
    #    init_needed, а не errors. Битый JSON — errors. Двойной рид через load_project_config
    #    (policy.json → pipeline.json fallback) — легаси-проекты продолжают работать.
    from _config_loader import load_project_config
    # raw=True — ИМЕННО файл: пустота здесь означает «конфиг не инициализирован». Эффективный
    # конфиг (со снимком политики живого прогона поверх) сделал бы удалённый policy.json
    # похожим на живой, и preflight перестал бы звать init_pipeline_config.py.
    cfg = load_project_config(project_root, raw=True)
    if not cfg:
        init_needed.append("ground/policy.json not found — конфигурация не инициализирована")
    else:
        # JSONDecodeError на невалидном policy.json/pipeline.json — load_project_config
        # глотает и возвращает {} (best-effort). Значит cfg == {} и при НЕвалидном JSON тоже;
        # отдельно отлавливать здесь нечего — corruption-диагностика на стороне config-helper.
        if cfg.get("_incomplete"):
            init_needed.append(f"policy.json incomplete: {cfg['_incomplete']}")
        _check_policy_drift(project_root, cfg, warnings)

    # 2. Проверяем ТУ раскладку кода, которую реально грузит рантайм.
    layout = resolve_layout(project_root, self_base)
    base = layout["base"]
    # Единственная раскладка — project: либо деплой есть (mode == "project"), либо нет
    # (mode == "none"). В обоих случаях прогоняем project-проверки: они сами решают,
    # что ругать (settings.hooks.json/gate-guard.py/resolve_hook_paths.py) и подсказывают
    # путь установки.
    _check_project_layout(base, project_root, errors, warnings)

    # 2b. Каталог настроек РАНТАЙМА vs каталог деплоя (см. _check_runtime_settings_dirs).
    _check_runtime_settings_dirs(project_root, base, warnings)

    # 3. doctor.py — self-check целостности (база кода зависит от раскладки)
    _run_doctor(base, project_root, errors, warnings)

    # passed=True требует и отсутствия enforcement-ошибок, и инициализированного конфига —
    # гейт арминга остаётся жёстким. Но init_needed отделён от errors для разного exit-кода.
    passed = len(errors) == 0 and len(init_needed) == 0
    result = {
        "passed": passed,
        # Раскладка + база кода: этим же префиксом зовутся скрипты скиллов
        # (<base>/skills/<skill>/scripts/…) — оркестратору не надо угадывать путь.
        "layout": {"mode": layout["mode"], "base": str(base)},
        "errors": errors,
    }
    if init_needed:
        result["init_needed"] = init_needed
        # Готовая команда, а не только диагноз. Без неё модель видела exit 2 (в брифах описаны
        # были только 0 и 1), шла дальше — и КАЖДЫЙ последующий `config.py set` падал с exit 3
        # «pipeline.json не найден». Так терялись записанные решения (sources.story,
        # pipeline.mode): вопрос пользователю задан, ответ получен, а артефакта решения нет.
        result["init_command"] = (
            f"python3 {base}/skills/feature-pipeline/scripts/init_pipeline_config.py "
            f"--project {project_root}"
        )
    if warnings:
        result["warnings"] = warnings
    return result


if __name__ == "__main__":
    project_root = _find_project_root()
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
