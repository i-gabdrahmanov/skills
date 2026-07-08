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

# --- поиск python-интерпретатора (Windows/git-bash часто без python3, только python/py) ---
PY=()
if command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v python >/dev/null 2>&1; then PY=(python)
elif command -v py >/dev/null 2>&1; then PY=(py -3)
fi

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

# 1. co-location: hooks И skills в один .gigacode (overwrite — source-managed).
# tar-pipe вместо cp -a: копия работает от рабочего дерева, поэтому руками отсекаем
# runtime-мусор (__pycache__/.pyc/.DS_Store) и ЛОКАЛЬНЫЙ конфиг оператора
# (minor-defect-fix/config.json с реальными путями машины — в таргет едет только
# config.json.example; сам конфиг оператор заводит на первом запуске).
copy_tree() {  # $1=src-dir  $2=dst-dir
  (cd "$1" && tar -cf - \
      --exclude '*__pycache__*' --exclude '*.pyc' --exclude '*.DS_Store' \
      --exclude '*.pytest_cache*' --exclude '*minor-defect-fix/config.json' \
      .) | (cd "$2" && tar -xf -)
}
copy_tree "$SRC/hooks" "$GIG/hooks"
copy_tree "$SRC/skills" "$GIG/skills"
echo "  ✓ скопированы hooks/ и skills/ (co-located, без __pycache__/.DS_Store/локального config.json)"

# Пустой config.json на месте (skill-paths.json/doctor требуют файл; маппинг проект→спека
# оператор заполняет на первом запуске). Существующий конфиг таргета НЕ перетираем.
MDF_CFG="$GIG/skills/minor-defect-fix/config.json"
if [ ! -f "$MDF_CFG" ]; then
  printf '{\n  "projects": {}\n}\n' > "$MDF_CFG"
  echo "  ✓ заведён пустой minor-defect-fix/config.json (маппинг заполняется на первом запуске)"
fi

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
if [ "${#PY[@]}" -eq 0 ]; then
  echo "  (python не найден в PATH — preflight пропущен; поставь Python и добавь его в PATH)"
else
  "${PY[@]}" -X utf8 "$GIG/hooks/preflight.py" --project "$TARGET" || \
    echo "  (preflight сообщил о проблемах — см. вывод выше)"
fi
