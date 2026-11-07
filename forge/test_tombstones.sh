#!/usr/bin/env bash
#
# test_tombstones.sh — снятое из репо не переживает обновление, а операторское переживает.
#
# Прецедент. Ветка lite снята из репо в 60ae8cc, но в установках старой раскладки пережила и
# мягкий деплой, и `update.sh --force`: владение определялось как «запись есть в исходном репо»,
# а у УДАЛЁННОГО скилла её там по определению нет → он считался самописным и берёгся. Осиротевший
# /forge-lite оставался достижимым, и резолвер отвечал `unknown inputs.mode='lite'` на каждой фазе.
#
# Сейчас два режима:
#   • extension (.gigacode/extensions/<kind>/) — каталог целиком форжевый, install подменяет его
#     целиком: удалённое из репо уходит само, без реестра;
#   • миграция старой раскладки (co-located .gigacode/{skills,hooks,commands}) — снимается по
#     исходнику + реестру .forge-deployed + tombstones.txt, операторское рядом остаётся.
#
# Тест гоняет НАСТОЯЩИЕ install.sh/uninstall.sh на копии репо: дыры были в проводке, а не в
# арифметике предиката. Запускается через skills/run_all_tests.py --skill root.
# Usage: bash test_tombstones.sh
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass=0
fail=0
ok()  { echo "  ok   $1"; pass=$((pass + 1)); }
bad() { echo "  FAIL $1: $2"; fail=$((fail + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SRC="$TMP/forge"
TGT="$TMP/project"
mkdir -p "$TGT"
cp -a "$DIR" "$SRC"
rm -rf "$SRC/.git"
GIG="$TGT/.gigacode"
EXT="$GIG/extensions/forge"

mkdir -p "$SRC/skills/zzz-temp"
echo "temp skill" > "$SRC/skills/zzz-temp/SKILL.md"
echo "temp command" > "$SRC/commands/zzz-temp.md"
echo "# temp hook" > "$SRC/hooks/zzz-temp-hook.py"

run_install() { bash "$SRC/install.sh" forge "$TGT" --no-link >/dev/null 2>&1; }

# ── 1. extension: временное встало ─────────────────────────────────────────────
run_install
mkdir -p "$GIG/skills/my-own" "$GIG/commands"           # своё оператора — в co-located каталогах
echo "operator skill" > "$GIG/skills/my-own/SKILL.md"
echo "operator command" > "$GIG/commands/my-own.md"
if [ -d "$EXT/skills/zzz-temp" ] && [ -f "$EXT/commands/zzz-temp.md" ] && [ -f "$EXT/hooks/zzz-temp-hook.py" ]; then
  ok "установка положила временные скилл, команду и хук в extension"
else
  bad "установка" "zzz-temp не встал в $EXT"
fi

# ── 2. удалили из исходника → переустановка снимает, конфиг и операторское целы ─
rm -rf "$SRC/skills/zzz-temp" "$SRC/commands/zzz-temp.md" "$SRC/hooks/zzz-temp-hook.py"
printf '{\n  "projects": {"/work/proj": "docs/specs/orders.md"}\n}\n' > "$EXT/skills/minor-defect-fix/config.json"
run_install
if [ ! -e "$EXT/skills/zzz-temp" ] && [ ! -e "$EXT/commands/zzz-temp.md" ] && [ ! -e "$EXT/hooks/zzz-temp-hook.py" ]; then
  ok "удалённое из репо снято переустановкой (каталог extension'а подменён целиком)"
else
  bad "переустановка" "zzz-temp пережил переустановку"
fi
if grep -q '/work/proj' "$EXT/skills/minor-defect-fix/config.json" 2>/dev/null; then
  ok "переустановка сохранила конфиг оператора minor-defect-fix"
else
  bad "конфиг оператора" "install обнулил minor-defect-fix/config.json (боевой прогон v0.4.6, A-F1)"
fi
if [ -f "$GIG/skills/my-own/SKILL.md" ] && [ -f "$GIG/commands/my-own.md" ]; then
  ok "операторское в .gigacode/{skills,commands} пережило переустановку"
else
  bad "операторское" "my-own снесён установкой"
fi

# ── 3. миграция без реестра: tombstones.txt снимает снятую ветку ───────────────
bash "$SRC/uninstall.sh" forge "$TGT" --no-link >/dev/null 2>&1
mkdir -p "$GIG/hooks" "$GIG/skills/forgelite"
cp "$SRC/hooks/gate-guard.py" "$GIG/hooks/gate-guard.py"     # признак старой раскладки
echo "# operator hook" > "$GIG/hooks/my-hook.py"
echo "снятая ветка" > "$GIG/skills/forgelite/SKILL.md"
echo "снятая команда" > "$GIG/commands/forge-lite.md"
run_install
if [ ! -e "$GIG/skills/forgelite" ] && [ ! -e "$GIG/commands/forge-lite.md" ]; then
  ok "tombstones.txt снимает снятую ветку старой раскладки без реестра"
else
  bad "tombstones.txt" "forgelite пережил миграцию"
fi
if [ ! -e "$GIG/hooks/gate-guard.py" ] && [ -f "$GIG/hooks/my-hook.py" ]; then
  ok "форж-хук старой раскладки снят, самописный хук оператора остался"
else
  bad "миграция hooks/" "gate-guard.py остался или my-hook.py снесён"
fi

# ── 4. миграция по реестру: снятое, известное только реестру, уходит ───────────
bash "$SRC/uninstall.sh" forge "$TGT" --no-link >/dev/null 2>&1
mkdir -p "$GIG/skills/zzz-old"
echo "старый форж-скилл" > "$GIG/skills/zzz-old/SKILL.md"
printf '# forge deploy manifest v1\nskills/zzz-old\nskills/feature-pipeline\n' > "$GIG/.forge-deployed"
run_install
if [ ! -e "$GIG/skills/zzz-old" ] && [ ! -e "$GIG/.forge-deployed" ]; then
  ok "скилл, известный только реестру старой установки, снят вместе с реестром"
else
  bad "миграция по реестру" "zzz-old или .forge-deployed пережили миграцию"
fi
if [ -f "$GIG/skills/my-own/SKILL.md" ] && [ -f "$GIG/commands/my-own.md" ]; then
  ok "миграция оставила операторское"
else
  bad "миграция" "снесено операторское — регресс инцидента с rm -rf"
fi

# ── 5. uninstall: extension целиком, операторское — на месте ───────────────────
bash "$SRC/uninstall.sh" forge "$TGT" --no-link >/dev/null 2>&1
if [ ! -e "$EXT" ] && [ -f "$GIG/skills/my-own/SKILL.md" ]; then
  ok "uninstall снял extension и оставил операторское"
else
  bad "uninstall" "extension остался или операторское снесено"
fi

echo
echo "=== tombstones: PASS=$pass FAIL=$fail ==="
[ "$fail" -eq 0 ]
