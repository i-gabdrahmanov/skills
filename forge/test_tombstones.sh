#!/usr/bin/env bash
#
# test_tombstones.sh — снятое из репо не переживает апгрейд, а операторское переживает.
#
# Прецедент. Ветка lite снята из репо в 60ae8cc, но в установках пережила и мягкий деплой,
# и `update.sh --force`: uninstall определял владение как «запись есть в исходном репо», а у
# УДАЛЁННОГО скилла её там по определению нет → он классифицировался как самописный скилл
# оператора и берёгся. Осиротевший /forge-lite остался достижимым, его SKILL.md предписывал
# `set inputs.mode lite`, и актуальный резолвер отвечал `unknown inputs.mode='lite'` → rc 3
# на резолве каждой фазы. Пользователь на свежей версии получал «баг resolve_phases».
#
# Тест гоняет НАСТОЯЩИЕ deploy.sh/uninstall.sh на копии репо, а не библиотеку в вакууме:
# дыра была именно в проводке предиката, а не в его арифметике.
#
# Запускается через skills/run_all_tests.py --skill root.
# Usage: bash test_tombstones.sh
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass=0
fail=0
ok()  { echo "  ok   $1"; pass=$((pass + 1)); }
bad() { echo "  FAIL $1: $2"; fail=$((fail + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Копия репо как исходник (сам репо править нельзя) + пустой таргет.
SRC="$TMP/forge"
TGT="$TMP/project"
mkdir -p "$TGT"
cp -a "$DIR" "$SRC"
rm -rf "$SRC/.git"
GIG="$TGT/.gigacode"

# Временный «форж-скилл» и «форж-команда», которые мы потом удалим из исходника,
# плюс операторское, которого в исходнике нет никогда.
mkdir -p "$SRC/skills/zzz-temp"
echo "temp skill" > "$SRC/skills/zzz-temp/SKILL.md"
echo "temp command" > "$SRC/commands/zzz-temp.md"

run_deploy() { bash "$SRC/deploy.sh" "$TGT" >/dev/null 2>&1; }

# ── 1. первый деплой: временное встало, реестр записан ────────────────────────
run_deploy
mkdir -p "$GIG/skills/my-own"                       # операторское — ПОСЛЕ деплоя
echo "operator skill" > "$GIG/skills/my-own/SKILL.md"
echo "operator command" > "$GIG/commands/my-own.md"

if [ -d "$GIG/skills/zzz-temp" ] && [ -f "$GIG/commands/zzz-temp.md" ]; then
  ok "первый деплой поставил временный скилл и команду"
else
  bad "первый деплой" "zzz-temp не встал"
fi

if [ -f "$GIG/.forge-deployed" ] && grep -Fxq "skills/zzz-temp" "$GIG/.forge-deployed"; then
  ok "реестр установки записан и содержит положенное"
else
  bad "реестр установки" "нет .forge-deployed или в нём нет skills/zzz-temp"
fi

if grep -Fxq "skills/my-own" "$GIG/.forge-deployed" 2>/dev/null; then
  bad "реестр установки" "операторский my-own попал в реестр форжа"
else
  ok "операторское в реестр не попало"
fi

# ── 2. удаляем из исходника → повторный деплой обязан снять сироту ────────────
rm -rf "$SRC/skills/zzz-temp" "$SRC/commands/zzz-temp.md"
run_deploy

if [ ! -e "$GIG/skills/zzz-temp" ]; then
  ok "скилл, удалённый из репо, снят при апгрейде"
else
  bad "надгробие skills/" "zzz-temp пережил повторный деплой"
fi

if [ ! -e "$GIG/commands/zzz-temp.md" ]; then
  ok "команда, удалённая из репо, снята при апгрейде"
else
  bad "надгробие commands/" "zzz-temp.md пережила повторный деплой"
fi

if [ -f "$GIG/skills/my-own/SKILL.md" ] && [ -f "$GIG/commands/my-own.md" ]; then
  ok "операторское пережило апгрейд (снимаем своё, не всё лишнее)"
else
  bad "операторское" "my-own снесён апгрейдом — регресс инцидента с rm -rf"
fi

if ! grep -Fxq "skills/zzz-temp" "$GIG/.forge-deployed"; then
  ok "реестр переписан под актуальный исходник"
else
  bad "реестр" "zzz-temp остался в реестре после удаления из исходника"
fi

# ── 3. bootstrap-список: работает БЕЗ реестра (установки старше механизма) ────
rm -f "$GIG/.forge-deployed"
mkdir -p "$GIG/skills/forgelite"
echo "снятая ветка" > "$GIG/skills/forgelite/SKILL.md"
echo "снятая команда" > "$GIG/commands/forge-lite.md"
run_deploy

if [ ! -e "$GIG/skills/forgelite" ] && [ ! -e "$GIG/commands/forge-lite.md" ]; then
  ok "tombstones.txt снимает снятую ветку в установке без реестра"
else
  bad "tombstones.txt" "forgelite пережил деплой при отсутствии реестра"
fi

# ── 4. надгробие не трогает то, что в исходнике ЖИВО ──────────────────────────
# Страховка от опечатки в tombstones.txt: живая запись игнорируется.
echo "commands/forge.md" >> "$SRC/tombstones.txt"
run_deploy
if [ -f "$GIG/commands/forge.md" ]; then
  ok "живая в исходнике запись из tombstones.txt проигнорирована"
else
  bad "страховка" "forge.md снесён, хотя есть в исходнике"
fi
sed -i.bak '$d' "$SRC/tombstones.txt" && rm -f "$SRC/tombstones.txt.bak"

# ── 5. uninstall: снимает форж-своё по реестру, операторское оставляет ────────
# Возвращаем временный скилл в исходник и деплоим, чтобы реестр его содержал; потом убираем
# из исходника — на момент uninstall он ЕСТЬ только в реестре. Ровно тот случай, который
# прежний предикат не снимал.
mkdir -p "$SRC/skills/zzz-temp"
echo "temp skill" > "$SRC/skills/zzz-temp/SKILL.md"
run_deploy
rm -rf "$SRC/skills/zzz-temp"

bash "$SRC/uninstall.sh" "$TGT" >/dev/null 2>&1

if [ ! -e "$GIG/skills/zzz-temp" ]; then
  ok "uninstall снял скилл, известный только по реестру"
else
  bad "uninstall по реестру" "zzz-temp пережил uninstall"
fi

if [ -f "$GIG/skills/my-own/SKILL.md" ] && [ -f "$GIG/commands/my-own.md" ]; then
  ok "uninstall оставил операторское"
else
  bad "uninstall" "снесено операторское — регресс инцидента"
fi

if [ ! -e "$GIG/.forge-deployed" ]; then
  ok "uninstall снял и сам реестр"
else
  bad "uninstall" ".forge-deployed остался в таргете"
fi

echo
echo "=== tombstones: PASS=$pass FAIL=$fail ==="
[ "$fail" -eq 0 ]
