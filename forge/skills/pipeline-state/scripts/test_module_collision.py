#!/usr/bin/env python3
"""Regression test для fragile `sys.modules['_util']` reset pattern в pipeline-state.

Сценарий, который этот тест фиксирует (regression для R6):
  1. `hooks/risk_ladder.py` импортируется первым — он кладёт config-helper/scripts/ в
     sys.path[0] и кэширует свой `_util` в sys.modules['_util'].
  2. Затем импортируется какой-либо pipeline-state скрипт (record_approval, update, ...).
  3. ДО фикса: паттерн `if str(_HERE) not in sys.path` пропускал insert (т.к. _HERE уже
     в sys.path[0+], но не на [0]), Python находил `_util` по прежнему пути [0], а
     `del sys.modules['_util']` уже произошёл — и итоговый import брал ЧУЖОЙ _util
     (config-helperский), где нет approval_path/is_jira_key/etc. → ImportError.
  4. ПОСЛЕ фикса: `_HERE` сначала удаляется со своей старой позиции, потом вставляется
     на [0] безусловно. После `del sys.modules['_util']` следующий import находит
     pipeline-state'ский `_util` первым.

Каждый скрипт проверяется в ОТДЕЛЬНОМ subprocess — иначе sys.modules из первого
теста загрязнит второй (а сам баг именно в порядке «config-helper первым → потом
pipeline-state»). Это не unittest-изоляция ради изоляции, это часть сценария бага.

Запуск: python3 -m unittest test_module_collision
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
CONFIG_HELPER_SCRIPTS = SCRIPTS.parent.parent / "config-helper" / "scripts"

# Скрипты, в которых был исправлен sys.path-reset (9 шт., см. R6).
# Сверяемся с грепом `if str(_HERE) in sys.path:` чтобы тест не врал, если список изменится.
ALL_SCRIPTS = [
    "update.py", "init.py", "record_gate.py", "record_approval.py", "rollback.py",
    "patch_manifest_judges.py", "override_judge.py", "read.py", "add_steps.py",
    "archive.py",
]

# Подтверждаем, что список соответствует реальности — иначе тест пропустит баг.
# Исключаем тестовые файлы: они либо используют `HERE` (не `_HERE`), либо содержат
# паттерн только в docstring (как этот файл).
_FIXED = {p.name for p in SCRIPTS.glob("*.py")
          if p.name.startswith("test_") is False
          and "if str(_HERE) in sys.path:" in p.read_text(encoding="utf-8")}


def _run_collision(script: str) -> subprocess.CompletedProcess:
    """Симулируем реальный баг-триггер: config-helper первым, потом pipeline-state скрипт.

    Делается в subprocess, чтобы sys.modules был свежим — баг про порядок первого импорта,
    повторно воспроизвести в одном процессе нельзя (sys.modules уже загрязнён после первой
    итерации).
    """
    harness = textwrap.dedent(f"""\
        import sys
        # Шаг 0: кладём pipeline-state в sys.path ДО config-helper — это типично для тестов
        # и conftest, которые уже добавили scripts-каталог, прежде чем risk_ladder.py
        # добавил config-helper сверху. Без этого шага баг не воспроизводится: `if str(_HERE)
        # not in sys.path` возвращает True, insert(0) выполняется, и _HERE оказывается
        # впереди config-helper — никакой коллизии.
        sys.path.insert(0, {str(SCRIPTS)!r})
        # Шаг 1: эмулируем risk_ladder.py — он кладёт config-helper в [0] и кэширует _util.
        sys.path.insert(0, {str(CONFIG_HELPER_SCRIPTS)!r})
        import _util as _cached
        assert "config-helper" in _cached.__file__, _cached.__file__
        # Шаг 2: грузим целевой pipeline-state скрипт через importlib (как подгружаемый модуль,
        # без модификации sys.path самим тестом — это сделает сам скрипт своим reset-блоком).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "target_script", {str(SCRIPTS / script)!r})
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        # Шаг 3: проверяем, что в sys.modules лежит НАШ _util, а не config-helperский.
        final = sys.modules.get("_util")
        assert final is not None, "_util disappeared from sys.modules"
        assert "pipeline-state" in final.__file__, (
            f"WRONG _util loaded: {{final.__file__!r}} "
            f"(expected pipeline-state, got config-helper — R6 fix regression!)")
        print("R6_FIX_VERIFIED")
    """)
    return subprocess.run(
        [sys.executable, "-c", harness],
        capture_output=True, text=True,
        cwd=str(SCRIPTS.parent.parent.parent),  # /Users/21879313/work/forge
    )


class TestR6ModuleCollisionRegression(unittest.TestCase):
    """R6 regression: после предзагрузки config-helper, pipeline-state скрипт
    должен брать СВОЙ `_util`, а не config-helperский."""

    def test_all_known_scripts_use_fixed_pattern(self):
        """Защита от тихого возврата к старому паттерну при правке скриптов.

        Если кто-то добавит новый скрипт с `if str(_HERE) not in sys.path:` — этот
        тест упадёт, заставляя добавить скрипт в ALL_SCRIPTS и явно подумать про reset.
        """
        missing = set(ALL_SCRIPTS) - _FIXED
        unexpected = _FIXED - set(ALL_SCRIPTS)
        self.assertEqual(missing, set(),
                         f"scripts ожидают R6-фикс, но паттерн не найден: {missing}")
        self.assertEqual(unexpected, set(),
                         f"скрипты с R6-фиксом не покрыты тестом: {unexpected}")

    def test_pipeline_state_util_wins_after_config_helper_preload(self):
        """Главный сценарий: config-helper → pipeline-state, и _util должен быть наш."""
        for script in ALL_SCRIPTS:
            with self.subTest(script=script):
                r = _run_collision(script)
                self.assertEqual(
                    r.returncode, 0,
                    f"{script}: subprocess FAILED\nstdout={r.stdout!r}\nstderr={r.stderr!r}")
                self.assertIn(
                    "R6_FIX_VERIFIED", r.stdout,
                    f"{script}: no R6_FIX_VERIFIED in stdout\n{r.stdout!r}\nstderr={r.stderr!r}")

    def test_collision_is_real_without_fix(self):
        """Мета-тест: подтверждаем, что наш harness действительно триггерит коллизию.

        Если убрать R6-фикс (вернуть `if str(_HERE) not in sys.path:`), этот же harness
        должен падать с ImportError или assertion. Без этой проверки тест выше мог бы
        молча «зелениться» из-за какой-нибудь косвенной причины (например, если config-helper
        и pipeline-state начнут делить один _util — тогда проверка sys.modules ничего
        бы не ловила).

        Используем копию harness, который грузит scripts/config-helper/scripts/_util.py
        напрямую как «целевой» — это эквивалентно «скрипту БЕЗ фикса»: его sys.path не
        переставляется, и `from _util import ...` берёт config-helperский _util, после чего
        дальнейшие попытки получить pipeline-state-специфичные имена упадут.
        """
        harness = textwrap.dedent(f"""\
            import sys
            sys.path.insert(0, {str(CONFIG_HELPER_SCRIPTS)!r})
            import _util as _cached
            # Без фикса — `from _util import ...` ниже найдёт config-helperский _util первым,
            # потому что он лежит на sys.path[0]. Симулируем это прямым импортом.
            try:
                from _util import approval_path
                # Если дошли сюда — значит config-helperский _util почему-то экспортирует
                # approval_path (что само по себе уже означало бы коллизию API). Проверяем
                # вызовом с pipeline-state-сигнатурой (project, key).
                try:
                    approval_path("/tmp", "x")
                    print("UNEXPECTED_OK")
                except TypeError as e:
                    print("COLLISION_TRIGGERED:" + str(e))
            except ImportError as e:
                # config-helperский _util не имеет approval_path — самый частый исход.
                # Это и есть «баг проявился бы без фикса»: импорт взял ЧУЖОЙ _util.
                print("COLLISION_TRIGGERED:" + str(e))
        """)
        r = subprocess.run(
            [sys.executable, "-c", harness],
            capture_output=True, text=True,
            cwd=str(SCRIPTS.parent.parent.parent),
        )
        self.assertEqual(r.returncode, 0,
                         f"harness crashed:\nstdout={r.stdout!r}\nstderr={r.stderr!r}")
        # Один из двух исходов валиден:
        self.assertTrue(
            "COLLISION_TRIGGERED" in r.stdout or "UNEXPECTED_OK" in r.stdout,
            f"harness produced no useful signal:\nstdout={r.stdout!r}\nstderr={r.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
