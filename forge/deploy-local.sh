#!/usr/bin/env bash
#
# deploy-local.sh — перепекает подключение extension'а forge/forge-ui ПРЯМО В ПРОЕКТЕ.
#
# Живёт в <project>/.gigacode/extensions/<forge|forge-ui>/ (кладётся туда install.sh). Его зовут,
# когда подключение устарело: проект переехал или переклонирован в другую папку (пути в
# манифесте и hooks/hooks.json абсолютные), сменился интерпретатор, хуки не грузятся.
#
# Что делает:
#   1. Пересобирает манифест (gigacode-extension.json + qwen-extension.json) и hooks/hooks.json
#      под текущее место проекта и найденный здоровый Python (hooks/forge_extension.py build).
#   2. Ссылка в рантайме: имя extension'а зависит от пути проекта — после переезда снимает
#      старую ссылку и ставит новую; включает extension только для этого workspace.
#
# Usage (из корня проекта):
#   bash .gigacode/extensions/forge/deploy-local.sh             # forge (или forge-ui — свой каталог)
#   bash .gigacode/extensions/forge/deploy-local.sh --check     # только проверка подключения
#   bash .gigacode/extensions/forge/deploy-local.sh --no-link   # без рантайма (только файлы)
#   FORGE_RUNTIME=qwen …                                        # дев-машина: CLI qwen вместо gigacode
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # = <project>/.gigacode/extensions/<kind>
KIND="$(basename "$DIR")"
case "$KIND" in
  forge|forge-ui) ;;
  *) echo "deploy-local.sh: лежит не в .gigacode/extensions/<forge|forge-ui>/ ($DIR) — переустанови: bash install.sh" >&2
     exit 2 ;;
esac
PROJECT_ROOT="$(cd "$DIR/../../.." && pwd)"
TOOL="$DIR/hooks/forge_extension.py"

# shellcheck source=hooks/_pick_python.sh
. "$DIR/hooks/_pick_python.sh"
if ! forge_pick_python && [ "${#PY[@]}" -eq 0 ]; then
  echo "deploy-local.sh: не найден ни python3, ни python, ни py в PATH — поставь Python 3.9+." >&2
  exit 1
fi

CHECK=0
NO_LINK=0
RUNTIME="${FORGE_RUNTIME:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --no-link) NO_LINK=1; shift ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    *) echo "deploy-local.sh: неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

if [ "$CHECK" -eq 1 ]; then
  exec "${PY[@]}" -X utf8 "$TOOL" check --ext "$DIR" --project "$PROJECT_ROOT"
fi

OLD_NAME=""
if [ -f "$DIR/gigacode-extension.json" ]; then
  OLD_NAME="$("${PY[@]}" -X utf8 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("name",""))' \
               "$DIR/gigacode-extension.json" 2>/dev/null || true)"
fi
REV="$("${PY[@]}" -X utf8 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("forgeRevision",""))' \
         "$DIR/gigacode-extension.json" 2>/dev/null || true)"
BUILT="$("${PY[@]}" -X utf8 "$TOOL" build --ext "$DIR" --project "$PROJECT_ROOT" --kind "$KIND" --version "$REV")"
NAME="$("${PY[@]}" -X utf8 -c 'import json,sys; print(json.loads(sys.argv[1])["name"])' "$BUILT")"
echo "  ✓ манифест и hooks/hooks.json пересобраны: $NAME (проект $PROJECT_ROOT)"

if [ "$NO_LINK" -eq 1 ]; then
  echo "  (--no-link: рантайм не тронут)"
  exit 0
fi
# Имя extension'а зависит от пути проекта: после переезда старая ссылка указывает в никуда — снять.
if [ -n "$OLD_NAME" ] && [ "$OLD_NAME" != "$NAME" ]; then
  CLI="${RUNTIME:-$(command -v gigacode || command -v qwen || true)}"
  if [ -n "$CLI" ]; then
    "$CLI" extensions uninstall "$OLD_NAME" >/dev/null 2>&1 && echo "  ✓ снята старая ссылка $OLD_NAME" || true
  fi
fi
exec "${PY[@]}" -X utf8 "$TOOL" link --ext "$DIR" --project "$PROJECT_ROOT" --runtime "$RUNTIME"
