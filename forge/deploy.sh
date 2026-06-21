#!/usr/bin/env bash
#
# deploy.sh — установщик Forge: разворачивает СКЛОНИРОВАННЫЙ репозиторий в указанную
#             папку целевого проекта по проектной модели (<target>/.gigacode/).
#
# Запускается из склонированного репо Forge. Целевая папка ОБЯЗАТЕЛЬНА — без неё
# ничего никуда не копируется (проектов может быть несколько, угадывать нельзя).
#
# Что делает:
#   1. Копирует hooks/ и skills/ (co-located) + доки в <target>/.gigacode/.
#   2. Кладёт туда deploy-local.sh (in-project фиксер путей, его зовёт preflight.py).
#   3. Доводит <target>/.gigacode/settings.json (merge блока hooks + бэкап) через deploy-local.sh.
#   4. Прогоняет preflight.py (advisory).
#
# Usage (из корня склонированного Forge):
#   bash deploy.sh /path/to/target-project
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # корень репо Forge

# --- целевая папка обязательна ---
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  echo "deploy.sh: не указана целевая папка проекта." >&2
  echo "Usage: bash deploy.sh /path/to/target-project" >&2
  echo "  Без пути ничего не копируется." >&2
  exit 2
fi

if [ ! -d "$TARGET" ]; then
  echo "deploy.sh: целевая папка не существует или не каталог: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"

# запрет деплоя «в себя»
if [ "$TARGET" = "$SRC" ]; then
  echo "deploy.sh: целевая папка совпадает с исходным репо Forge — деплой в себя запрещён." >&2
  exit 2
fi

GIG="$TARGET/.gigacode"
echo "== deploy Forge → $GIG =="
mkdir -p "$GIG/hooks" "$GIG/skills"

# 1. co-location: hooks И skills в один .gigacode (overwrite — source-managed)
cp -a "$SRC/hooks/." "$GIG/hooks/"
cp -a "$SRC/skills/." "$GIG/skills/"
echo "  ✓ скопированы hooks/ и skills/ (co-located)"

# 2. in-project фиксер
cp "$SRC/deploy-local.sh" "$GIG/deploy-local.sh"

# 3. доки рядом для справки
for d in FORGE.md SKILLS-REGISTRY.md; do
  [ -f "$SRC/$d" ] && cp "$SRC/$d" "$GIG/$d"
done
echo "  ✓ deploy-local.sh и доки на месте"

# исполняемость
chmod +x "$GIG/hooks/"*.py "$GIG/hooks/"*.sh "$GIG/deploy-local.sh" 2>/dev/null || true

# 4. доводка settings.json (merge блока hooks + бэкап) — на месте, проект = TARGET
echo
bash "$GIG/deploy-local.sh"

# 5. диагностика (advisory — не валим деплой)
echo
echo "== preflight =="
python3 "$GIG/hooks/preflight.py" --project "$TARGET" || \
  echo "  (preflight сообщил о проблемах — см. вывод выше)"
