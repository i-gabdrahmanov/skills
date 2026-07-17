#!/usr/bin/env python3
"""
resolve_hook_paths.py — подстановка ${PROJECT_ROOT} в settings.hooks.json.

Читает .gigacode/hooks/settings.hooks.json (эталон с плейсхолдером),
заменяет ${PROJECT_ROOT} на реальный путь к корню проекта,
и обновляет ТОЛЬКО блок "hooks" в существующем .gigacode/settings.json.
Все остальные секции (mcpServers, permissions, $version, ...) НЕ трогает.

ЕДИНЫЙ владелец блока hooks в settings.json: и постановка (--resolve, зовёт deploy-local.sh),
и снятие (--remove, зовёт uninstall.sh). Два владельца одного контракта разъезжаются —
поэтому удаление живёт здесь же, а не отдельным скриптом.

Usage:
    python3 resolve_hook_paths.py                           # авто: git toplevel
    python3 resolve_hook_paths.py --project /path/to/proj   # явный путь
    python3 resolve_hook_paths.py --dry-run                 # только вывод, без записи
    python3 resolve_hook_paths.py --check                   # exit 0 если всё ок, 1 если проблемы
    python3 resolve_hook_paths.py --remove                  # снять forge-хуки (деинсталляция)
"""
from __future__ import annotations  # PEP604 (X | None) под Python 3.9

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLACEHOLDER = "${PROJECT_ROOT}"
PYTHON_PLACEHOLDER = "${PYTHON}"


def find_python_cmd() -> str:
    """Абсолютный путь к интерпретатору, которым запущен сам resolver, + -X utf8.

    Путь — тот же python, что deploy-local.sh уже нашёл в PATH (python3/python/py),
    подставляем абсолютным, чтобы хуки на рантайме не зависели от PATH (на Windows
    часто нет python3, только python.exe/py.exe).

    -X utf8 (= PYTHONUTF8=1, но не зависит от синтаксиса cmd.exe/sh для передачи env)
    обязателен: хуки читают JSON-payload из stdin и печатают JSON с ensure_ascii=False
    в stdout — без него на не-английской Windows (cp1251 и т.п.) любая кириллица/иконка
    в payload валит хук UnicodeDecodeError/UnicodeEncodeError на КАЖДОМ вызове инструмента
    (не только при деплое, как было с risk-policy.json).
    """
    exe = sys.executable
    if not exe:
        return "python3 -X utf8"
    quoted = f'"{exe}"' if " " in exe else exe
    return f"{quoted} -X utf8"


def find_project_root(cwd: str | None = None) -> str:
    """Определяет корень проекта через git, gradle или pipeline.json."""
    start = Path(cwd or os.getcwd()).resolve()
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists():
            return str(parent)
    for parent in [start] + list(start.parents):
        if (parent / "build.gradle").exists() or (parent / "settings.gradle").exists():
            return str(parent)
    for parent in [start] + list(start.parents):
        if (parent / "ground" / "pipeline.json").exists():
            return str(parent)
    return str(start)


def resolve_string(value: str, project_root: str, python_cmd: str) -> str:
    """Заменяет ${PROJECT_ROOT} и ${PYTHON} в строке."""
    return value.replace(PLACEHOLDER, project_root).replace(PYTHON_PLACEHOLDER, python_cmd)


def resolve_hooks_block(hooks: dict, project_root: str, python_cmd: str) -> dict:
    """Рекурсивно заменяет ${PROJECT_ROOT}/${PYTHON} во всех строковых значениях блока hooks."""

    def _walk(node):
        if isinstance(node, str):
            return resolve_string(node, project_root, python_cmd)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(hooks)


def has_placeholder(value) -> bool:
    """Проверяет, есть ли хоть один ${PROJECT_ROOT} в JSON-значении."""
    if isinstance(value, str):
        return PLACEHOLDER in value
    if isinstance(value, dict):
        return any(has_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(has_placeholder(item) for item in value)
    return False


def has_absolute_hook_paths(settings: dict) -> list[str]:
    """Ищет оставшиеся абсолютные пути в command-полях блока hooks.

    Возвращает список путей, которые НЕ принадлежат текущему проекту.
    """
    hooks = settings.get("hooks", {})
    found = []

    def _walk(node, path=""):
        if isinstance(node, str) and path.endswith("command"):
            # Хук — последний токен команды (интерпретатор ± "-X utf8" идут перед ним).
            # НЕ якорим на "/" в начале: project_root на Windows — обратные слэши
            # (Path(...).resolve() → "C:\Work\..."), а хвост из шаблона — прямые
            # ("/.gigacode/hooks/x.py"), путь целиком смешанный: "C:\Work\...
            # /.gigacode/hooks/x.py". Якорь на "/" резал бы только хвост после первого "/".
            m = re.search(r"(\S+\.py)\s*$", node)
            if m:
                found.append(m.group(1))
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(hooks)
    return found


_HOOK_SCRIPT_RE = re.compile(r"(\S+\.py)\s*$")


def _norm(path: str) -> str:
    """Разделители к прямым слэшам: путь в command на Windows смешанный
    ("C:\\Work\\proj" + "/.gigacode/hooks/x.py") — сравнивать префиксы можно только после
    нормализации (та же причина, что у has_absolute_hook_paths)."""
    return path.replace("\\", "/")


def hook_script_path(command) -> str | None:
    """Путь хука из command (последний токен: интерпретатор и -X utf8 идут перед ним)."""
    if not isinstance(command, str):
        return None
    m = _HOOK_SCRIPT_RE.search(command)
    return m.group(1) if m else None


def is_forge_hook_command(command, project_root: str) -> bool:
    """Зовёт ли command хук ЭТОГО проекта (<project_root>/.gigacode/hooks/*.py).

    Хук, указывающий на .gigacode ДРУГОГО проекта, своим не считается — снимать чужое
    мы не вправе (та же семантика "foreign", что в has_absolute_hook_paths)."""
    script = hook_script_path(command)
    if not script:
        return False
    return _norm(script).startswith(_norm(f"{project_root}/.gigacode/hooks/"))


def strip_forge_hooks(settings: dict, project_root: str) -> tuple[dict, list, list]:
    """Снимает из блока hooks ТОЛЬКО forge-owned записи этого проекта.

    Возвращает (новые settings, removed, kept_foreign) — списки имён/команд. Записи,
    добавленные оператором вручную (свой хук, хук другого проекта), остаются: деинсталляция
    Forge не должна молча уносить чужой enforcement. Опустевшие группы матчеров и события
    схлопываются; если forge-хуки были единственными — ключ "hooks" удаляется целиком
    (пустой блок рантайм читает как "0 hook entries" — это тот же результат, но мусором в
    конфиге).
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings, [], []

    removed: list = []
    kept_foreign: list = []
    new_hooks: dict = {}

    for event, groups in hooks.items():
        if not isinstance(groups, list):
            new_hooks[event] = groups
            continue
        new_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                new_groups.append(group)
                continue
            kept_entries = []
            for entry in group["hooks"]:
                cmd = entry.get("command") if isinstance(entry, dict) else None
                if is_forge_hook_command(cmd, project_root):
                    removed.append(entry.get("name") or hook_script_path(cmd))
                    continue
                if hook_script_path(cmd):
                    kept_foreign.append(entry.get("name") or hook_script_path(cmd))
                kept_entries.append(entry)
            if kept_entries:
                new_group = dict(group)
                new_group["hooks"] = kept_entries
                new_groups.append(new_group)
        if new_groups:
            new_hooks[event] = new_groups

    result = dict(settings)
    if new_hooks:
        result["hooks"] = new_hooks
    else:
        result.pop("hooks", None)
    return result, removed, kept_foreign


def run_remove(target_settings_path: Path, project_root: str, dry_run: bool) -> int:
    """--remove: снять forge-хуки из settings.json. Идемпотентно (повторный запуск — no-op)."""
    if not target_settings_path.exists():
        print(json.dumps({"passed": True, "removed_entries": 0,
                          "note": f"settings.json не найден — снимать нечего: {target_settings_path}"},
                         ensure_ascii=False, indent=2))
        return 0
    try:
        existing = json.loads(target_settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # fail-closed: не переписываем то, что не смогли прочитать (иначе снесём конфиг оператора)
        print(json.dumps({"passed": False, "error": f"не читается {target_settings_path}: {e}"},
                         ensure_ascii=False, indent=2))
        return 1

    updated, removed, kept_foreign = strip_forge_hooks(existing, project_root)
    output = json.dumps(updated, ensure_ascii=False, indent=2)

    if dry_run:
        print(output)
        return 0

    target_settings_path.write_text(output, encoding="utf-8")
    print(json.dumps(
        {
            "passed": True,
            "project_root": project_root,
            "target": str(target_settings_path),
            "removed_entries": len(removed),
            "removed": removed,
            "foreign_hooks_kept": kept_foreign,
            "hooks_key_removed": "hooks" not in updated,
            "other_sections_preserved": [k for k in updated if k != "hooks"],
        },
        ensure_ascii=False, indent=2))
    return 0


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    check_mode = "--check" in args
    remove_mode = "--remove" in args

    # Определяем корень проекта
    project_root = None
    if "--project" in args:
        idx = args.index("--project")
        if idx + 1 < len(args):
            project_root = args[idx + 1]
    if not project_root:
        try:
            project_root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            project_root = find_project_root()

    project_root = str(Path(project_root).resolve())
    project_gigacode = Path(project_root) / ".gigacode"
    hooks_template_path = project_gigacode / "hooks" / "settings.hooks.json"
    target_settings_path = project_gigacode / "settings.json"

    # --- remove mode (деинсталляция) ---
    # Идёт ПЕРВЫМ и не требует эталона settings.hooks.json: снимать хуки нужно и тогда,
    # когда .gigacode/hooks уже удалён (повторный/прерванный uninstall).
    if remove_mode:
        sys.exit(run_remove(target_settings_path, project_root, dry_run))

    # --- check mode ---
    if check_mode:
        issues = []

        if not hooks_template_path.exists():
            issues.append(f"MISSING: {hooks_template_path}")
        else:
            try:
                tmpl = json.loads(hooks_template_path.read_text(encoding="utf-8"))
                if not has_placeholder(tmpl.get("hooks", {})):
                    issues.append(
                        f"ETALON HAS NO {PLACEHOLDER} in hooks block: {hooks_template_path}"
                    )
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"PARSE ERROR: {hooks_template_path}: {e}")

        if target_settings_path.exists():
            try:
                stg = json.loads(target_settings_path.read_text(encoding="utf-8"))
                abs_paths = has_absolute_hook_paths(stg)
                expected_prefix = f"{project_root}/.gigacode/hooks/"
                foreign = [p for p in abs_paths if not p.startswith(expected_prefix)]
                if foreign:
                    issues.append(
                        f"FOREIGN ABSOLUTE PATHS in settings.json hooks: {foreign}"
                    )
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"PARSE ERROR: {target_settings_path}: {e}")

        if issues:
            print(json.dumps({"passed": False, "issues": issues}, ensure_ascii=False, indent=2))
            sys.exit(1)
        else:
            print(
                json.dumps(
                    {"passed": True, "project_root": project_root},
                    ensure_ascii=False,
                )
            )
            sys.exit(0)

    # --- resolve mode ---
    if not hooks_template_path.exists():
        print(
            json.dumps(
                {
                    "error": f"settings.hooks.json not found: {hooks_template_path}",
                    "passed": False,
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    template = json.loads(hooks_template_path.read_text(encoding="utf-8"))
    template_hooks = template.get("hooks", {})

    if not has_placeholder(template_hooks):
        print(
            json.dumps(
                {
                    "warning": f"Эталон {hooks_template_path} не содержит {PLACEHOLDER} в hooks. "
                    f"Копируем блок как есть.",
                    "passed": True,
                },
                ensure_ascii=False,
            )
        )
        resolved_hooks = template_hooks
    else:
        resolved_hooks = resolve_hooks_block(template_hooks, project_root, find_python_cmd())

    # Читаем существующий settings.json или создаём новый
    if target_settings_path.exists():
        existing = json.loads(target_settings_path.read_text(encoding="utf-8"))
    else:
        existing = {}

    # Обновляем ТОЛЬКО блок hooks
    existing["hooks"] = resolved_hooks

    # Гарантируем minimal обязательные поля
    existing.setdefault("disableAllHooks", False)
    existing.setdefault("$version", 3)

    output = json.dumps(existing, ensure_ascii=False, indent=2)

    if dry_run:
        print(output)
        return

    target_settings_path.write_text(output, encoding="utf-8")

    # Считаем количество замен
    count = count_occurrences_in_hooks(template_hooks)

    print(
        json.dumps(
            {
                "passed": True,
                "project_root": project_root,
                "source": str(hooks_template_path),
                "target": str(target_settings_path),
                "hooks_updated": True,
                "other_sections_preserved": [
                    k for k in existing if k != "hooks"
                ],
                "replacements_made": count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def count_occurrences_in_hooks(node) -> int:
    """Считает количество ${PROJECT_ROOT} в JSON-значении."""
    count = 0
    if isinstance(node, str):
        count += node.count(PLACEHOLDER)
    elif isinstance(node, dict):
        for v in node.values():
            count += count_occurrences_in_hooks(v)
    elif isinstance(node, list):
        for item in node:
            count += count_occurrences_in_hooks(item)
    return count


if __name__ == "__main__":
    main()