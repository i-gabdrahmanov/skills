#!/usr/bin/env python3
"""test_python_floor.py — пол интерпретатора держится кодом, а не обещанием в доке.

Зачем этот тест существует
--------------------------
Прецедент (аудит forge/audit-gates-not-firing): в `hooks/_project.py` и
`skills/feature-pipeline/scripts/init_pipeline_config.py` не было
`from __future__ import annotations`, а в аннотациях стоял PEP 604 (`X | None`).
На Python 3.9 такой модуль падает `TypeError` НА ИМПОРТЕ. `_project` тянут
`risk_ladder` и `forge_events`, то есть ВСЕ блокирующие хуки — а крэш на импорте
даёт exit 1, тогда как блокировка это exit 2. Рантайм трактует exit 1 как
«хук не возражает» и пропускает вызов: весь enforcement выключался молча,
на каждом tool-call, без единой строчки в stderr пользователя.

При этом deploy.sh зашивает в settings.json первый python из PATH, а это на macOS
и на многих корпоративных Linux — ровно 3.9. То есть дефолтная установка молча
получала харнес без гейтов.

Два инварианта ниже ловят этот класс до деплоя:

  1. Всё дерево парсится под полом MIN_PYTHON (ловит `match`, `except*` и прочий
     синтаксис новее пола — то, что упало бы ещё на компиляции).
  2. Любой файл с PEP 604 в аннотациях обязан иметь `from __future__ import
     annotations` (ловит ровно тот TypeError-на-импорте, что был).

Пол намеренно НЕ поднят до 3.10: весь код и так парсится под 3.9, и все тесты
на 3.9 зелёные. Поднимать пол — значит без нужды отрезать корпоративные машины,
где python3 обновить нельзя.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Пол интерпретатора. Держится в синхроне с doctor.MIN_PYTHON / preflight.MIN_PYTHON
# (их равенство пинит test_doctor.py).
MIN_PYTHON = (3, 9)

# Каталоги с исходником харнеса. Всё, что реально едет в <project>/.gigacode/.
SCAN_DIRS = ("hooks", "skills")

# `X | Y` в аннотации: аргументы, возвраты, переменные. Ищем в исходнике построчно,
# потому что нас интересует именно наличие синтаксиса, а не его семантика.
_PEP604_ANNOTATION = re.compile(
    r"""
    (?: ->\s* [\w\[\], .]*? \w \s*\|\s* \w            # -> str | None
      | :\s*   [\w\[\], .]*? \w \s*\|\s* \w \s* [,)=] # x: str | None ,  )  =
    )
    """,
    re.VERBOSE,
)


def _py_files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = REPO / d
        if base.is_dir():
            out.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(out)


class TestPythonFloor(unittest.TestCase):
    def test_whole_tree_parses_at_floor(self):
        """Ни одного синтаксиса новее пола: иначе интерпретатор пола не скомпилирует файл."""
        broken: list[str] = []
        for f in _py_files():
            src = f.read_text(encoding="utf-8")
            try:
                # feature_version заставляет парсер отвергнуть синтаксис новее пола
                # (match/except*), даже когда сам тест бежит на свежем интерпретаторе.
                ast.parse(src, filename=str(f), feature_version=MIN_PYTHON[1])
            except SyntaxError as e:
                broken.append(f"{f.relative_to(REPO)}:{e.lineno}: {e.msg}")
        self.assertEqual(
            broken, [],
            "синтаксис новее Python %d.%d — не скомпилируется на полу:\n  %s"
            % (MIN_PYTHON[0], MIN_PYTHON[1], "\n  ".join(broken)),
        )

    def test_pep604_annotations_have_future_import(self):
        """PEP 604 в аннотациях без future-импорта = TypeError на импорте под 3.9.

        Для хуков это не «падает тест», а молча снятый enforcement: крэш на импорте
        отдаёт exit 1, рантайм читает его как «возражений нет» и выполняет вызов.
        """
        offenders: list[str] = []
        for f in _py_files():
            src = f.read_text(encoding="utf-8")
            if "from __future__ import annotations" in src:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or '"""' in stripped:
                    continue
                if _PEP604_ANNOTATION.search(line):
                    offenders.append(f"{f.relative_to(REPO)}:{i}: {stripped[:90]}")
                    break
        self.assertEqual(
            offenders, [],
            "PEP 604 (`X | None`) в аннотациях без `from __future__ import annotations` — "
            "модуль упадёт TypeError на импорте под Python %d.%d:\n  %s"
            % (MIN_PYTHON[0], MIN_PYTHON[1], "\n  ".join(offenders)),
        )

    def test_floor_matches_doctor_and_preflight(self):
        """Пол здесь — тот же, что у doctor/preflight. Три источника правды разъезжаются."""
        pf = (REPO / "hooks" / "preflight.py").read_text(encoding="utf-8")
        self.assertIn(f"MIN_PYTHON = {MIN_PYTHON}", pf,
                      "preflight.MIN_PYTHON разошёлся с полом test_python_floor")
        doc = (REPO / "skills" / "feature-pipeline" / "scripts" / "doctor.py").read_text(
            encoding="utf-8")
        self.assertIn(f"MIN_PYTHON = {MIN_PYTHON}", doc,
                      "doctor.MIN_PYTHON разошёлся с полом test_python_floor")

    def test_running_interpreter_is_at_or_above_floor(self):
        """Сам прогон идёт на интерпретаторе не ниже пола (иначе результаты не о том)."""
        self.assertGreaterEqual(sys.version_info[:2], MIN_PYTHON)


if __name__ == "__main__":
    unittest.main()
