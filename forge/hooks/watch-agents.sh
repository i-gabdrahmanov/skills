#!/usr/bin/env bash
# watch-agents.sh — смотреть единый лог агента+субагентов GigaCode в реальном времени.
#
# Usage:
#   watch-agents.sh [project-dir] [--json]
#     project-dir   корень проекта (по умолчанию текущий каталог)
#     --json        следить за agents.jsonl вместо человекочитаемого agents.log
#
# Единый лог прогона (главный агент + все субагенты + ошибки) — agents.log/.jsonl.
# Фильтр по конкретному агенту: watch-agents.sh | grep '\[<label>\]'.
#
# Автоматически находит свежайший каталог прогона под <root>/ground/ai-logs/ и tail -f его лог.
# Если прогон ещё не начался — ждёт его появления.
set -euo pipefail

PROJECT="."
MODE="log"   # log | json
while [ $# -gt 0 ]; do
  case "$1" in
    --json)  MODE="json"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) PROJECT="$1"; shift ;;
  esac
done

ROOT="$(cd "$PROJECT" 2>/dev/null && (git rev-parse --show-toplevel 2>/dev/null || pwd))" || ROOT="$PROJECT"
BASE="$ROOT/ground/ai-logs"

ext="log"; [ "$MODE" = "json" ] && ext="jsonl"
rel="agents.$ext"

echo "watch: $BASE/**/$rel   (Ctrl-C для выхода)"
target=""
while :; do
  # каталоги прогонов всегда на глубине 2: <feature>/iter-NN/ либо _adhoc/<run>/
  newest="$(ls -dt "$BASE"/*/*/ 2>/dev/null | head -1 || true)"
  if [ -n "$newest" ] && [ -f "${newest}${rel}" ]; then target="${newest}${rel}"; break; fi
  sleep 1
done
echo "→ $target"
exec tail -n +1 -f "$target"
