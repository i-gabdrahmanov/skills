#!/usr/bin/env bash
# run-hook-tests.sh — единый вход тестов control-plane: юнит-тесты хуков + eval-набор.
#
# Раньше был deprecated-шимом только на evals/run-evals.py, который поведенчески покрывает
# 8 из 15 хуков — 20 hooks/test_*.py не запускались НИ ОДНИМ shell-entrypoint'ом (только
# через skills/run_all_tests.py). Теперь: сперва юнит-тесты (все test_*.py через раннер
# с гардом изоляции), затем eval-харнесс. Любой красный — exit 1.
set -u
HOOKS="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HOOKS")"

echo "── 1/2 юнит-тесты хуков (test_*.py) ──"
python3 "$REPO/skills/run_all_tests.py" --skill hooks || exit 1

echo ""
echo "── 2/2 eval-набор (evals/run-evals.py) ──"
exec python3 "$HOOKS/evals/run-evals.py" "$@"
