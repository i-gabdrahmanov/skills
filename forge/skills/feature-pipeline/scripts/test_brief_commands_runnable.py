#!/usr/bin/env python3
"""Doc-lint: команды из брифов реально исполнимы.

Класс дефектов, который эти тесты закрывают, статикой ловится дёшево, а прогоном — дорого:
бриф печатает команду, модель её выполняет, получает exit 2/3 и остаётся без объяснения,
потому что в самом брифе такого исхода нет.

Три найденных на аудите случая (все воспроизведены запуском):
  • `config.py set decisions.criticality …` → exit 3: в реестре id был `autonomy.criticality`
    (у него совпадал только `path`), а `find_entry` матчит по `id`. Критичность не писалась,
    и `gate-guard` блокировал любое R2+ действие fix/lite-ветки;
  • `set_criticality.py --criticality …` без `--skill`/`--feature` → argparse error: у скрипта
    они `required=True`;
  • `config.py set quality.eval_enabled false` стоял ПОСЛЕ `init.py` → exit 1 «policy.json
    immutable», при том что соседние `inputs.*`/`decisions.*` наоборот ТРЕБУЮТ манифеста;
    исполнимого порядка у блока не существовало.

Проверяем два инварианта:
  1. каждый `config.py … set <id>` из корпуса резолвится через реестр (`config.find_entry`);
  2. каждый форжевый `*.py` из корпуса вызван со всеми своими `required`-аргументами.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Корпус = брифы всех веток + фазовые брифы + слэш-команды. Именно отсюда модель берёт команды.
CORPUS: list[tuple[str, str]] = []
for _p in sorted(REPO.glob("skills/*/SKILL.md")) + \
        sorted((REPO / "skills/feature-pipeline/references/phases").glob("*.md")) + \
        sorted((REPO / "commands").glob("*.md")):
    CORPUS.append((str(_p.relative_to(REPO)), _p.read_text(encoding="utf-8")))


def _load_config_module():
    """config.py как модуль — берём его собственные load_registry/find_entry, а не копию правил."""
    path = REPO / "skills/config-helper/scripts/config.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("ch_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# `config.py … set <id> <value>`; плейсхолдеры (<ключ>, $VAR) пропускаем — это не литералы.
_SET_RE = re.compile(r"config\.py[^\n`]*?\bset\s+([A-Za-z_][\w.]*)")
_PLACEHOLDER = re.compile(r"^[<$]")


class ConfigSetIdsResolve(unittest.TestCase):
    def test_every_set_id_is_in_registry(self):
        cfg = _load_config_module()
        params = cfg.load_registry()
        bad: list[str] = []
        for rel, text in CORPUS:
            for m in _SET_RE.finditer(text):
                pid = m.group(1)
                if _PLACEHOLDER.match(pid):
                    continue
                if cfg.find_entry(params, pid) is None:
                    line = text[:m.start()].count("\n") + 1
                    bad.append(f"{rel}:{line} → config.py set {pid}")
        self.assertEqual(
            bad, [],
            "В брифах есть `config.py set <id>`, которых нет в params-registry.json "
            "(вернут exit 3 «параметр не найден», решение прогона молча не запишется):\n  "
            + "\n  ".join(bad))


# Вызовы форжевых скриптов в брифах: `... /scripts/<name>.py <хвост до конца строки>`.
# Только РЕАЛЬНЫЕ вызовы: `python3 …/scripts/<name>.py <хвост>`. Без требования `python`
# сюда попадали прозаические упоминания («предложи откат: `rollback.py --to-step <X>`»)
# и таблицы-справочники скриптов — там неполнота аргументов ожидаема и дефектом не является.
# Хвост читаем ДО конца команды, включая переносы `\`+\n: иначе многострочный вызов
# обрывается на первом продолжении и все его флаги выглядят пропущенными.
_SCRIPT_CALL_RE = re.compile(r"\bpython3?\s+\S*scripts/([a-z0-9_-]+\.py)((?:\\\n|[^\n`])*)")

# Скрипты, у которых обязательные аргументы задаются позиционно или подкомандой, а не флагами,
# — для них проверка флагов неприменима.
_SKIP_SCRIPTS = {"spec_cli.py", "archive.py", "config.py", "get_prompt.py", "run_judge.py",
                 "check_sdd_doc.py", "check_taskplan.py", "check_build.py", "check_coverage.py"}


class ScriptCallsHaveRequiredArgs(unittest.TestCase):
    def test_documented_calls_pass_required_flags(self):
        bad: list[str] = []
        for rel, text in CORPUS:
            for m in _SCRIPT_CALL_RE.finditer(text):
                name, tail = m.group(1), m.group(2).replace("\\\n", " ")
                if name in _SKIP_SCRIPTS:
                    continue
                found = list(REPO.glob(f"skills/*/scripts/{name}")) + \
                    list(REPO.glob(f"hooks/{name}"))
                if len(found) != 1:
                    continue  # не наш скрипт либо неоднозначность — не наш гейт
                required = _required_flags(found[0])
                if required is None:
                    continue
                missing = sorted(f for f in required if f not in tail)
                if missing:
                    line = text[:m.start()].count("\n") + 1
                    bad.append(f"{rel}:{line} → {name} без {', '.join(missing)}")
        self.assertEqual(
            bad, [],
            "В брифах есть вызовы скриптов без обязательных аргументов "
            "(argparse оборвёт их с ошибкой использования):\n  " + "\n  ".join(bad))


_REQUIRED_CACHE: dict[Path, "set[str] | None"] = {}


def _required_flags(script: Path) -> "set[str] | None":
    """Обязательные флаги скрипта — из его собственного `--help` (не из копии списка здесь).

    None — help не получен (скрипт требует окружения): пропускаем, это не наш гейт.
    """
    if script in _REQUIRED_CACHE:
        return _REQUIRED_CACHE[script]
    try:
        r = subprocess.run([sys.executable, str(script), "--help"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        _REQUIRED_CACHE[script] = None
        return None
    if r.returncode != 0 or "usage:" not in r.stdout:
        _REQUIRED_CACHE[script] = None
        return None
    # argparse печатает обязательные флаги в usage БЕЗ квадратных скобок: `--skill SKILL`.
    usage = r.stdout.split("usage:", 1)[1].split("\n\n", 1)[0]
    # Снимаем опциональные `[...]` (бывают вложенными) и взаимоисключающие `(--a | --b)`:
    # член такой группы обязателен не сам по себе, а только как «один из», и требовать
    # каждый из них — ложный отказ (напр. rollback.py: `(--to-step | --to-phase | --list)`).
    while True:
        stripped = re.sub(r"\[[^\[\]]*\]|\([^()]*\)", " ", usage)
        if stripped == usage:
            break
        usage = stripped
    out = set(re.findall(r"(--[a-z][\w-]*)", usage))
    _REQUIRED_CACHE[script] = out
    return out


if __name__ == "__main__":
    unittest.main(verbosity=2)
