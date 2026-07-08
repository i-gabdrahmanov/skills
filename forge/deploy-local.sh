#!/usr/bin/env bash
#
# deploy-local.sh — чинит блок hooks в settings.json ПРЯМО В ПРОЕКТЕ, без копирования.
#
# Живёт внутри <project>/.gigacode/ (кладётся туда установщиком deploy.sh).
# Его зовёт preflight.py, когда в settings.json устарели/чужие пути к хукам
# (например, проект переехал или переклонирован в другую папку).
#
# Что делает:
#   1. Бэкапит существующий .gigacode/settings.json ПЕРЕД любой записью:
#        - нет settings.json.bak       → settings.json.bak       (первозданный оригинал, вечный)
#        - settings.json.bak уже есть  → settings.json.<TS>.bak   (текущая версия)
#      Вечный .bak никогда не затирается, ничего не теряется.
#   2. Делегирует merge в hooks/resolve_hook_paths.py — обновляется ТОЛЬКО блок hooks,
#      permissions / mcpServers / $version и прочие секции сохраняются.
#
# Usage (из корня проекта):
#   bash .gigacode/deploy-local.sh                  # проект = родитель .gigacode/
#   bash .gigacode/deploy-local.sh --project /path  # явный корень
#   bash .gigacode/deploy-local.sh --dry-run        # показать результат, без записи и бэкапа
#   bash .gigacode/deploy-local.sh --check          # только валидация (через resolver)
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # = <project>/.gigacode
RESOLVER="$DIR/hooks/resolve_hook_paths.py"

# --- поиск python-интерпретатора (Windows/git-bash часто без python3, только python/py) ---
PY=()
if command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v python >/dev/null 2>&1; then PY=(python)
elif command -v py >/dev/null 2>&1; then PY=(py -3)
else
  echo "deploy-local.sh: не найден ни python3, ни python, ни py в PATH." >&2
  echo "  Поставь Python 3 и убедись, что он добавлен в PATH." >&2
  exit 1
fi
TEMPLATE="$DIR/hooks/settings.hooks.json"
TARGET="$DIR/settings.json"

# --- разбор аргументов ---
PROJECT_ROOT=""
DRY_RUN=0
CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --project)
      PROJECT_ROOT="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --check)
      CHECK=1
      shift
      ;;
    *)
      echo "deploy-local.sh: неизвестный аргумент: $1" >&2
      exit 2
      ;;
  esac
done

# --- корень проекта: по умолчанию родитель .gigacode/ ---
if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT="$(cd "$DIR/.." && pwd)"
fi
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

# --- fail-fast: эталон должен быть на месте ---
if [ ! -f "$TEMPLATE" ]; then
  echo "deploy-local.sh: не найден эталон $TEMPLATE" >&2
  echo "  Хуки не разложены в .gigacode/hooks/ — сначала запусти установщик deploy.sh." >&2
  exit 1
fi

# --- бэкап (пропускаем в dry-run / check и если целевого файла ещё нет) ---
if [ "$DRY_RUN" -eq 0 ] && [ "$CHECK" -eq 0 ]; then
  if [ -f "$TARGET" ]; then
    if [ ! -f "$TARGET.bak" ]; then
      cp -p "$TARGET" "$TARGET.bak"
      echo "[backup] первозданный оригинал → $TARGET.bak"
    else
      TS="$(date +%Y%m%d-%H%M%S)"
      cp -p "$TARGET" "$TARGET.$TS.bak"
      echo "[backup] вечный .bak сохранён; текущая версия → $TARGET.$TS.bak"
    fi
  else
    echo "[backup] не требуется — settings.json создаётся с нуля"
  fi
fi

# --- merge через resolver (корень передаём явно, чтобы он совпал с бэкапом) ---
RESOLVER_ARGS=("--project" "$PROJECT_ROOT")
[ "$DRY_RUN" -eq 1 ] && RESOLVER_ARGS+=("--dry-run")
[ "$CHECK" -eq 1 ] && RESOLVER_ARGS+=("--check")

exec "${PY[@]}" "$RESOLVER" "${RESOLVER_ARGS[@]}"
