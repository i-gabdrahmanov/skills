#!/usr/bin/env python3
"""test_import_failclosed.py — сломанный бандл обязан БЛОКИРОВАТЬ, а не молчать.

Класс бага, ради которого тест написан
--------------------------------------
Протокол хука: `exit 2` — блокировка, всё остальное — «хук не возражает», вызов
выполняется. Значит любое падение хука ДО его собственной логики (ошибка импорта)
даёт exit 1 и рантайм спокойно выполняет инструмент. Пользователь не видит ничего:
stderr упавшего хука в норме не показывается, а тул отрабатывает как обычно.

Ровно это и случилось: в `hooks/_project.py` не было `from __future__ import
annotations` при PEP 604 в аннотациях, и на Python 3.9 (дефолт macOS и многих
корпоративных Linux — а установщик брал первый python из PATH) модуль падал
TypeError на импорте. `_project` тянут `risk_ladder` и `forge_events`, то есть ВСЕ
блокирующие хуки. Весь enforcement был выключен, на каждом tool-call, молча.

Корневые причины устранены (future-импорт + выбор здорового интерпретатора в
hooks/_pick_python.sh), но САМ КЛАСС остаётся: любая будущая ошибка импорта снова
тихо снимет гейты. Поэтому блокирующие хуки ловят ошибку импорта и выходят с 2.

Тест проверяет это на реальном запуске: копирует бандл во временный каталог, ломает
в копии `risk_ladder.py` и требует от каждого хука rc=2 + внятную причину в stderr.
(Подмена через PYTHONPATH тут не работает: при запуске скрипта sys.path[0] — каталог
самого скрипта, и настоящий модуль из hooks/ всегда выигрывает у PYTHONPATH. Поэтому
ломаем именно копию бандла — заодно это и есть реалистичный «битый деплой».)

Stop-хук `phase-gate.py` намеренно НЕ в списке: он блокирует не кодом возврата, а
JSON `{"decision":"block"}`, и fail-closed там запер бы пользователя в петле
«не могу завершить ход».
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent

# Блокирующие PreToolUse-хуки, которые импортируют форж-модули.
# state-write-guard/fork-syntax-guard тут нет: они stdlib-only и сломаться так не могут.
BLOCKING_HOOKS = [
    "destructive-blocker.py",
    "pii-boundary.py",
    "gate-guard.py",
    "tdd-guard.py",
    "eval-guard.py",
    "sod-enforcer.py",
    "inline-phase-guard.py",
]

PAYLOAD = '{"tool_name":"run_shell_command","tool_input":{"command":"echo hi"},"cwd":"%s"}'


class TestImportFailClosed(unittest.TestCase):
    def test_broken_bundle_denies_instead_of_passing(self):
        """Битый risk_ladder в бандле → rc=2 (deny), а не тихий пропуск."""
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "hooks"
            shutil.copytree(HOOKS, bundle,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            # Ровно то, что видит рантайм при битом деплое: модуль на месте, но не грузится.
            (bundle / "risk_ladder.py").write_text(
                "raise ImportError('simulated broken bundle')\n", encoding="utf-8")

            offenders = []
            for hook in BLOCKING_HOOKS:
                r = subprocess.run(
                    [sys.executable, "-X", "utf8", str(bundle / hook)],
                    input=PAYLOAD % td, capture_output=True, text=True,
                )
                if r.returncode != 2:
                    offenders.append(
                        f"{hook}: rc={r.returncode} (нужен 2), stderr={r.stderr.strip()[:160]}")
                elif "DENY" not in r.stderr:
                    offenders.append(f"{hook}: rc=2, но без причины в stderr")

            self.assertEqual(
                offenders, [],
                "хук со сломанным импортом обязан блокировать (exit 2), иначе рантайм "
                "выполнит вызов и enforcement выключится молча:\n  " + "\n  ".join(offenders),
            )

    def test_healthy_bundle_still_passes_benign_command(self):
        """Контроль: без подмены те же хуки пропускают безобидную команду (rc=0).

        Без этой половины тест был бы удовлетворён хуком, который блокирует ВСЁ.
        """
        with tempfile.TemporaryDirectory() as td:
            offenders = []
            for hook in BLOCKING_HOOKS:
                r = subprocess.run(
                    [sys.executable, "-X", "utf8", str(HOOKS / hook)],
                    input=PAYLOAD % td, capture_output=True, text=True,
                )
                if r.returncode != 0:
                    offenders.append(
                        f"{hook}: rc={r.returncode}, stderr={r.stderr.strip()[:160]}")
            self.assertEqual(offenders, [],
                             "здоровый бандл не должен блокировать `echo hi`:\n  "
                             + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
