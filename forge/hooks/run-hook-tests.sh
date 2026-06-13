#!/usr/bin/env bash
# run-hook-tests.sh — УСТАРЕЛ, заменён на evals/run-evals.py (PDLC v3.5).
# Старые кейсы не учитывали risk ladder + evidence. Канонический набор — eval-харнесс ниже.
HOOKS="$(cd "$(dirname "$0")" && pwd)"
echo "run-hook-tests.sh заменён на eval-набор. Запускаю evals/run-evals.py …"
exec python3 "$HOOKS/evals/run-evals.py" "$@"
