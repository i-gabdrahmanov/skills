#!/usr/bin/env bash
#
# sync-from-forge.sh — регенерирует содержимое extension'а из source-of-truth ../forge.
#
# Пока forge/ остаётся источником правды, этот скрипт переносит из него в forgeExt/ ровно то,
# что нужно нативному qwen/gigacode-extension'у, с двумя трансформациями путей хуков:
#   ${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/X.py  →  python3 ${CLAUDE_PLUGIN_ROOT}/hooks/X.py
# ${CLAUDE_PLUGIN_ROOT} — единственная var, которую рантайм подставляет в file-based хуках
# (= корень extension'а). resolve_hook_paths.py/${PYTHON}-shim при этом не нужны.
#
# Что НЕ едет: юнит-тесты (test_*.py), evals/, tests/, resolve_hook_paths.py (деплой-only),
# settings.hooks.json (заменён на hooks/hooks.json), локальный minor-defect-fix/config.json,
# __pycache__/*.pyc. FORGE.md кладётся как справочная дока, но НЕ как авто-контекст
# (contextFileName не задан — 84KB в каждую сессию не инжектим).
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/../forge" && pwd)"

echo "== sync forge ($SRC) → extension ($HERE) =="

# чистим генерируемое (манифест/README/этот скрипт не трогаем)
rm -rf "$HERE/hooks" "$HERE/commands" "$HERE/skills"
mkdir -p "$HERE/hooks" "$HERE/commands" "$HERE/skills"

# 1) hooks: рантайм-скрипты (.py) + зависимости (_project.py, risk_ladder.py) + данные политики.
for f in "$SRC/hooks/"*.py; do
  b="$(basename "$f")"
  case "$b" in
    test_*.py)             continue;;  # юнит-тесты
    resolve_hook_paths.py) continue;;  # деплой-only shim, в extension не нужен
  esac
  cp "$f" "$HERE/hooks/$b"
done
cp "$SRC/hooks/risk-policy.json" "$HERE/hooks/"
echo "  ✓ hooks/*.py (без test_*/resolve_hook_paths) + risk-policy.json"

# 2) settings.hooks.json → hooks/hooks.json с переписанными путями
python3 - "$SRC/hooks/settings.hooks.json" "$HERE/hooks/hooks.json" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding="utf-8"))
d.pop("_comment", None)
old = "${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/"
new = "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/"
for arr in d.get("hooks", {}).values():
    for entry in arr:
        for h in entry.get("hooks", []):
            if "command" in h:
                h["command"] = h["command"].replace(old, new)
json.dump(d, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(dst, "a", encoding="utf-8").write("\n")
print("  ✓ hooks/hooks.json (${CLAUDE_PLUGIN_ROOT} + python3)")
PY

# 3) слэш-команды
cp "$SRC/commands/"*.md "$HERE/commands/"
echo "  ✓ commands/*.md → /$(cd "$HERE/commands" && ls *.md | sed 's/\.md$//' | paste -sd, -)"

# 4) скиллы (каждый каталог со SKILL.md). Мусор и локальный конфиг оператора не тащим.
cp -a "$SRC/skills/." "$HERE/skills/"
rm -f "$HERE/skills/run_all_tests.py"
rm -f "$HERE/skills/minor-defect-fix/config.json"
find "$HERE/skills" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$HERE/skills" -type f -name '*.pyc' -delete 2>/dev/null || true
echo "  ✓ skills/*/SKILL.md ($(find "$HERE/skills" -maxdepth 1 -type d | tail -n +2 | wc -l | tr -d ' ') скиллов)"

# 5) справочная дока (НЕ авто-контекст)
cp "$SRC/FORGE.md" "$HERE/FORGE.md"
echo "  ✓ FORGE.md (справка, не contextFileName)"

# финальная уборка — по всему extension'у (не только hooks): pycache/pyc/.DS_Store
find "$HERE" -name '.DS_Store' -delete 2>/dev/null || true
find "$HERE" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$HERE" -type f -name '*.pyc' -delete 2>/dev/null || true

echo "== готово. Проверка: node -e JSON.parse(qwen-extension.json), затем: qwen extensions link \"$HERE\" =="
