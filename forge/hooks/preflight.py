#!/usr/bin/env python3
"""
Pre-flight check для feature-pipeline.
Проверяет, что control-plane включён и конфигурация доступна.
Вызывается самым первым при старте пайплайна.

Код форжа живёт в ДВУХ раскладках, и проверять надо ту, из которой реально запущен этот
файл (база выводится из его расположения — тот же приём, что в hooks/_project.py):
  • extension — нативный extension рантайма (qwen/gigacode): код в <ext>/{hooks,skills},
    подключение хуков — <ext>/hooks/hooks.json через ${CLAUDE_PLUGIN_ROOT};
  • project   — legacy deploy.sh: код в <project>/.gigacode/{hooks,skills},
    подключение — <project>/.gigacode/settings.json (генерится resolve_hook_paths.py).
Раньше проверялась ТОЛЬКО project-раскладка → в extension'е preflight валил старт
(«хуки не задеплоены»), хотя харнес был поднят.

Exit 0 — харнес активен, можно продолжать.
Exit 1 — ENFORCEMENT OFF (essential-хук не подключён / settings / risk-policy) либо рантайм
         грузит НЕ этот код (старые копии скиллов/команд перекрывают extension).
         Стоп-и-предупреди: сначала deploy/установка extension'а, потом заново.
Exit 2 — конфиг не инициализирован (ground/pipeline.json нет/неполон). Нормальный
         первый запуск: инициализируй конфиг и перезапусти preflight до exit 0.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

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

# Единственная переменная, которую рантайм подставляет в file-based хуках extension'а
# (= корень extension'а). От неё строятся пути в hooks/hooks.json.
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"

# Каталоги рантаймов: .gigacode (форк GigaCode, прод) и .qwen (dev/тест).
_RUNTIME_DIRS = (".gigacode", ".qwen")
_EXT_MANIFESTS = ("gigacode-extension.json", "qwen-extension.json")

# Каталоги-провайдеры скиллов и слэш-команд рантайма (в qwen-code —
# SKILL_PROVIDER_CONFIG_DIRS = [".qwen", ".agents"], на форке GigaCode — ".gigacode").
# Скиллы резолвятся project > user > extension, команды при конфликте имён рантайм
# переименовывает — extension проигрывает обоим уровням.
_SKILL_PROVIDER_DIRS = (".gigacode", ".qwen", ".agents")


def _home() -> Path:
    """Домашний каталог (точка подмены в тестах)."""
    return Path.home()


def _self_base() -> Path:
    """База кода, из которой запущен ЭТОТ preflight.

    Файл лежит в <base>/hooks/preflight.py → база = parents[1]:
    корень extension'а, либо <project>/.gigacode (legacy deploy), либо корень source-репо.
    """
    return Path(__file__).resolve().parents[1]


def resolve_layout(project_root, self_base=None) -> dict:
    """Где физически лежит код форжа, который грузит рантайм.

    Проверяем ТУ раскладку, из которой запущен этот файл: запуск из extension'а (рядом есть
    hooks/hooks.json) → extension, иначе развёрнутый в проекте `.gigacode` → project.
    Обратный приоритет («сначала проект») давал ложный блок: устаревший/битый `.gigacode`
    в репо валил старт, хотя энфорсмент реально шёл через установленный extension.

    Исключение — extension лежит рядом, но рантайм его НЕ грузит (не установлен), а деплой
    в проекте есть: тогда проверяем рабочий деплой, а не мёртвый каталог.

    `shadow` — вторая раскладка, найденная одновременно с первой: цепочки хуков сложатся и
    задвоятся (INSTALL.md §1), об этом предупреждаем.
    """
    proj_base = Path(project_root) / ".gigacode"
    base = Path(self_base).resolve() if self_base is not None else _self_base()
    deployed = ((proj_base / "hooks" / "settings.hooks.json").exists()
                or (proj_base / "hooks" / "gate-guard.py").exists())
    ext_wired = base != proj_base and (base / "hooks" / "hooks.json").exists()
    if ext_wired and not (deployed
                          and _extension_registry_status(base, project_root)[0] == "absent"):
        return {"mode": "extension", "base": base,
                "shadow": proj_base if deployed else None}
    if deployed:
        return {"mode": "project", "base": proj_base,
                "shadow": base if ext_wired else None}
    # Ни развёрнутого проекта, ни extension'а — сообщения ниже подскажут оба пути установки.
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

    `prefixes` — явный список допустимых префиксов (extension: "${CLAUDE_PLUGIN_ROOT}/hooks/"
    и абсолютный путь каталога хуков extension'а).
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


def _ext_name(base: Path) -> str | None:
    """Имя extension'а из его манифеста (gigacode-/qwen-extension.json)."""
    for mf in _EXT_MANIFESTS:
        p = base / mf
        try:
            if p.exists():
                name = json.loads(p.read_text(encoding="utf-8")).get("name")
                if isinstance(name, str) and name:
                    return name
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _registry_dirs(project_root) -> list[Path]:
    """Каталоги установленных extension'ов: ~/.{gigacode,qwen}/extensions и проектные."""
    dirs = [_home() / rt / "extensions" for rt in _RUNTIME_DIRS]
    dirs += [Path(project_root) / rt / "extensions" for rt in _RUNTIME_DIRS]
    return [d for d in dirs if d.is_dir()]


def _extension_registry_status(base: Path, project_root) -> tuple[str, str]:
    """Видит ли рантайм ИМЕННО ЭТОТ extension.

    В extension-раскладке «файлы на месте» проверяет сам себя (хуки лежат рядом с preflight),
    поэтому единственный содержательный вопрос — зарегистрирован ли каталог в рантайме.
    Возвращает ("ok"|"other-copy"|"absent"|"unknown", деталь).
    """
    name = _ext_name(base)
    entries: list[Path] = []
    other: Path | None = None
    for d in _registry_dirs(project_root):
        try:
            children = sorted(d.iterdir())
        except OSError:
            continue
        for entry in children:
            if not entry.is_dir():
                continue
            entries.append(entry)
            try:
                if entry.resolve() == base:      # install-копия: рантайм грузит нас же
                    return "ok", str(entry)
            except OSError:
                pass
            # link-установка: в каталоге лежит указатель на источник
            for f in entry.glob("*-extension-install.json"):
                try:
                    src = json.loads(f.read_text(encoding="utf-8")).get("source")
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(src, str) and src:
                    try:
                        if Path(src).expanduser().resolve() == base:
                            return "ok", str(entry)
                    except OSError:
                        pass
            if name and entry.name == name and other is None:
                other = entry
    if other is not None:
        return "other-copy", str(other)
    if not entries:
        return "unknown", ""
    return "absent", ", ".join(str(e) for e in entries[:5])


def _legacy_settings_drift(base: Path, project_root) -> list[str]:
    """forge-хуки, прописанные в settings.json рантайма/проекта (старый deploy.sh).

    Extension грузится ПОВЕРХ settings.json — цепочки складываются, хуки задваиваются,
    а устаревшие пути из settings.json ещё и падают на каждом вызове (INSTALL.md §1).
    """
    try:
        ours = {p.name for p in (base / "hooks").glob("*.py")}
    except OSError:
        ours = set(ESSENTIAL_HOOKS)
    hits = []
    candidates = [_home() / rt / "settings.json" for rt in _RUNTIME_DIRS]
    candidates += [Path(project_root) / rt / "settings.json" for rt in _RUNTIME_DIRS]
    for p in candidates:
        try:
            if not p.exists():
                continue
            block = json.loads(p.read_text(encoding="utf-8")).get("hooks", {})
        except (json.JSONDecodeError, OSError):
            continue
        dup = sorted(_referenced_hook_basenames(block) & ours)
        if dup:
            hits.append(
                f"{p}: forge-хуки прописаны и в settings.json, и в extension'е "
                f"({dup[:6]}{'…' if len(dup) > 6 else ''}) — цепочка задвоится "
                f"(INSTALL.md §1: убрать forge-записи из settings.json)"
            )
    return hits


def _named_entries(d: Path, marker: str | None) -> dict:
    """Имя → путь для каталога скиллов (`marker="SKILL.md"`) либо команд (`marker=None`, *.md)."""
    out: dict = {}
    try:
        children = sorted(d.iterdir())
    except OSError:
        return out
    for entry in children:
        if marker is None:
            if entry.is_file() and entry.suffix == ".md":
                out[entry.stem] = entry
        elif entry.is_dir() and (entry / marker).exists():
            out[entry.name] = entry
    return out


def _shadowing_copies(base: Path, project_root) -> list[str]:
    """Скиллы/команды extension'а, перекрытые одноимёнными копиями user/project-уровня.

    Рантайм резолвит скилл в порядке project > user > extension, а одноимённую слэш-команду
    extension'а переименовывает — в обоих случаях выигрывает НЕ extension. Каталог, оставшийся
    от старого deploy.sh (`~/.qwen/skills/feature-pipeline`, `<project>/.gigacode/skills/…`),
    поэтому молча подменяет брифы фаз старой версией: хуки-то грузятся из extension'а и preflight
    их видит зелёными, а оркестратор идёт по мёртвым путям вида `~/.gigacode/hooks/preflight.py`
    и валится на первом же шаге. Снаружи это выглядит как «preflight ждёт старую раскладку».

    Пути сравниваем через resolve(): симлинк каталога рантайма на сам extension — не подмена.
    Отчёт группируем ПО КАТАЛОГАМ, а не по именам: перекрытых сущностей бывает полтора десятка,
    и плоский список пришлось бы обрезать — ровно на том каталоге, который и грузит рантайм.
    """
    hits: list[str] = []
    roots = [Path(project_root) / p for p in _SKILL_PROVIDER_DIRS]
    roots += [_home() / p for p in _SKILL_PROVIDER_DIRS]
    for kind, subdir, marker in (("скиллы", "skills", "SKILL.md"), ("команды", "commands", None)):
        own = _named_entries(base / subdir, marker)
        if not own:
            continue
        for root in roots:
            names = []
            for name, path in _named_entries(root / subdir, marker).items():
                if name not in own:
                    continue
                try:
                    if path.resolve() == own[name].resolve():
                        continue
                except OSError:
                    pass
                names.append(name)
            if names:
                shown = ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
                hits.append(f"{root / subdir} — {kind} ({len(names)}): {shown}")
    return hits


def _check_extension_layout(base: Path, project_root, errors: list[str],
                            warnings: list[str]) -> None:
    """Раскладка extension: код рядом с этим файлом, wiring — hooks/hooks.json."""
    hooks_dir = base / "hooks"
    for hook in ESSENTIAL_HOOKS:
        if not (hooks_dir / hook).exists():
            errors.append(f"hook not found: {hooks_dir / hook} (extension повреждён)")

    wiring_p = hooks_dir / "hooks.json"
    try:
        hooks_block = json.loads(wiring_p.read_text(encoding="utf-8")).get("hooks", {})
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"hooks/hooks.json parse error: {e} — рантайм не подключит ни одного хука")
        hooks_block = None
    if hooks_block is not None:
        if not hooks_block:
            errors.append("hooks/hooks.json: hooks block is empty — enforcement inactive")
        else:
            _check_wiring(
                hooks_block, "hooks/hooks.json",
                [f"{PLUGIN_ROOT_VAR}/hooks/", f"{hooks_dir}/".replace("\\", "/")],
                errors,
            )

    _check_risk_policy(hooks_dir, "hooks", errors)

    status, detail = _extension_registry_status(base, project_root)
    if status == "absent":
        errors.append(
            f"extension не установлен в рантайме: {base} нет среди установленных "
            f"({detail}) → хуки не грузятся (ENFORCEMENT OFF). Поставь "
            f"`qwen extensions link {base}` (для форка — `gigacode extensions link …`) "
            f"и перезапусти сессию."
        )
    elif status == "other-copy":
        warnings.append(
            f"рантайм грузит другую копию extension'а: {detail} (preflight запущен из {base}). "
            f"Проверено содержимое {base} — обнови установленную копию "
            f"(`extensions update` либо uninstall + link/install)."
        )
    elif status == "unknown":
        warnings.append(
            "не удалось проверить установку extension'а: каталогов "
            f"{'/'.join(_RUNTIME_DIRS)}/extensions нет. Убедись сам, что "
            "`extensions list` показывает forge."
        )

    for msg in _legacy_settings_drift(base, project_root):
        warnings.append(msg)

    shadowed = _shadowing_copies(base, project_root)
    if shadowed:
        errors.append(
            "старые копии перекрывают extension — рантайм грузит НЕ его код: "
            + "; ".join(shadowed)
            + ". Это остатки прежнего deploy.sh: скилл резолвится project > user > extension, "
              "одноимённую команду рантайм переименовывает. Убери форж-своё из этих каталогов "
              "(чужие скиллы оператора рядом не трогай) и перезапусти сессию — INSTALL.md §1."
        )


def _check_project_layout(base: Path, project_root, errors: list[str],
                          warnings: list[str]) -> None:
    """Legacy-раскладка deploy.sh: код в <project>/.gigacode, wiring — settings.json."""
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
                "или создай resolve_hook_paths.py. Либо поставь форж как extension "
                "(см. INSTALL.md) — тогда settings.json не нужен."
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


# Минимальная версия Python (копия doctor.MIN_PYTHON; пинится test_doctor). Скрипты/хуки
# используют синтаксис 3.10+ (PEP604 `X | None`, match).
MIN_PYTHON = (3, 10)


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
            f"Python {have}: пайплайн требует {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
            f"(PEP604/match). Часть скриптов/хуков может падать. Обнови интерпретатор."
        )

    # 1. pipeline.json — конфиг control-plane. ДАННЫЕ всегда в проекте, раскладка кода на это
    #    не влияет. Отсутствие/неполнота — это «нормальный первый запуск» (файл создаёт
    #    init_pipeline_config.py, а preflight бежит ДО него), а не поломка энфорсмента →
    #    init_needed, а не errors. Битый JSON (не пустой, а невалидный) — уже реальная ошибка:
    #    конфиг есть, но рантайм его не прочитает → errors.
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

    # 2. Проверяем ТУ раскладку кода, которую реально грузит рантайм.
    layout = resolve_layout(project_root, self_base)
    base = layout["base"]
    if layout["mode"] == "extension":
        _check_extension_layout(base, project_root, errors, warnings)
        if layout["shadow"] is not None:
            warnings.append(
                f"форж поставлен extension'ом И развёрнут в проекте ({layout['shadow']}) — "
                f"цепочки хуков сложатся и задвоятся. Оставь один способ (INSTALL.md §1)."
            )
    else:
        _check_project_layout(base, project_root, errors, warnings)
        if layout["shadow"] is not None:
            warnings.append(
                f"рядом лежит extension ({layout['shadow']}), но рантайм его не грузит — "
                f"проверена раскладка проекта (.gigacode). Если хотел extension: "
                f"`qwen|gigacode extensions link {layout['shadow']}` + перезапуск сессии."
            )

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
