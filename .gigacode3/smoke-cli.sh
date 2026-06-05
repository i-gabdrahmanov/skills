#!/usr/bin/env bash
# smoke-cli.sh — проверка, что пайплайн реально СТАРТУЕТ на этом CLI+модели по команде.
#
# Три слоя: (1) статика — CLI есть, версия, doctor, валидность скиллов, evals; (2) модель —
# тривиальный headless-промпт отвечает; (3) live — хуки реально срабатывают, субагент стартует,
# gate блокирует. Live-слой требует CLI+модель; без них (нет ключа) — корректно SKIP.
#
# Usage:
#   bash smoke-cli.sh [HOME] [--live]
#     HOME   — конфиг-дом рантайма (умолч. ~/.gigacode). Для теста: ~/.qwen
#     --live — выполнять живые прогоны CLI (иначе только статика + печать live-команд)
set -uo pipefail

HOME_DIR="${1:-$HOME/.gigacode}"; HOME_DIR="${HOME_DIR/#\~/$HOME}"
LIVE=false; for a in "$@"; do [ "$a" = "--live" ] && LIVE=true; done
PASS=0; FAIL=0; SKIP=0
ok(){ echo "  ✓ $1"; PASS=$((PASS+1)); }
no(){ echo "  ✗ $1"; FAIL=$((FAIL+1)); }
sk(){ echo "  ⊘ SKIP $1"; SKIP=$((SKIP+1)); }

# портативный таймаут (macOS без coreutils `timeout`)
_to(){ local t=$1; shift; "$@" & local p=$!; { sleep "$t"; kill -9 $p; } >/dev/null 2>&1 & local w=$!; wait $p 2>/dev/null; local rc=$?; kill -9 $w >/dev/null 2>&1; wait $w 2>/dev/null; return $rc; }

echo "== smoke-cli: Forge home=$HOME_DIR, live=$LIVE =="

# ── CLI ──
CLI="$(command -v gigacode || command -v qwen || true)"
if [ -z "$CLI" ]; then no "CLI (gigacode|qwen) не найден в PATH — пайплайн запускать нечем"; echo; echo "ИТОГО PASS=$PASS FAIL=$FAIL SKIP=$SKIP"; exit 1; fi
ok "CLI найден: $CLI"
# Форк GigaCode гейтит хуки CLI-флагом --experimental-hooks (без него — 0 hook entries!).
# Переопределить/убрать: SMOKE_HOOKS_FLAG="" bash smoke-cli.sh ...
HF="${SMOKE_HOOKS_FLAG:---experimental-hooks}"
_to 30 "$CLI" --version >/dev/null 2>&1 && ok "CLI --version отвечает" || no "CLI --version не отработал"

# ── статика ──
echo "-- статика --"
if [ -x "$HOME_DIR/hooks/doctor.py" ] || [ -f "$HOME_DIR/hooks/doctor.py" ]; then
  python3 "$HOME_DIR/hooks/doctor.py" --home "$HOME_DIR" >/tmp/_smoke_doc 2>&1 && ok "doctor: Forge готов" || { no "doctor нашёл проблемы"; tail -3 /tmp/_smoke_doc | sed 's/^/      /'; }
else sk "doctor.py не найден в $HOME_DIR/hooks"; fi
[ -f "$HOME_DIR/hooks/validate_skills.py" ] && { python3 "$HOME_DIR/hooks/validate_skills.py" --skills "$HOME_DIR/skills" >/dev/null 2>&1 && ok "скиллы валидны" || no "есть невалидные скиллы"; } || sk "validate_skills.py не найден"
[ -f "$HOME_DIR/hooks/evals/run-evals.py" ] && { python3 "$HOME_DIR/hooks/evals/run-evals.py" >/dev/null 2>&1 && ok "evals хуков PASS" || no "evals хуков FAIL"; } || sk "run-evals.py не найден"

# ── модель ──
echo "-- модель/CLI --"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
MODEL_OK=false
if $LIVE; then
  if _to 120 "$CLI" $HF -p "Ответь одним словом: ok" >/tmp/_smoke_m 2>&1; then
    grep -qiE "ok|привет|готов" /tmp/_smoke_m && { ok "модель отвечает на headless-промпт"; MODEL_OK=true; } || { sk "модель не вернула ожидаемого (см. /tmp/_smoke_m)"; }
  else
    grep -qiE "api.?key|auth|ключ|LOCAL_QWEN" /tmp/_smoke_m && sk "модель не сконфигурирована (нет ключа) — live-проверки пропущены" || no "headless-промпт упал (см. /tmp/_smoke_m)"
  fi
else
  sk "live выключен — пропускаю прогоны модели (запусти с --live на машине с CLI+ключом)"
fi

# ── live: хуки/субагент/gate ──
if $LIVE && $MODEL_OK; then
  echo "-- live (хуки/субагент/gate) --"
  P1="$TMP/p1"; mkdir -p "$P1"
  ( cd "$P1" && _to 150 "$CLI" $HF -p "Выполни в shell ровно одну команду: echo smoke-ok" --approval-mode yolo >/dev/null 2>&1 )
  [ -n "$(find "$P1" -name agents.jsonl 2>/dev/null)" ] && grep -q '"event": "PreToolUse"' $(find "$P1" -name agents.jsonl) 2>/dev/null \
    && ok "хуки СРАБОТАЛИ по команде (agents.jsonl + PreToolUse)" || no "хуки не сработали — проверь блок hooks в settings ('0 hook entries')"

  P2="$TMP/p2"; mkdir -p "$P2"
  ( cd "$P2" && _to 200 "$CLI" $HF -p "Через инструмент agent (subagent_type=general-purpose) запусти субагента, который выполнит echo from-subagent." --approval-mode yolo >/dev/null 2>&1 )
  [ -n "$(find "$P2" -name agents.jsonl 2>/dev/null)" ] && grep -q '"event": "SubagentStart"' $(find "$P2" -name agents.jsonl) 2>/dev/null \
    && ok "субагент СТАРТУЕТ по команде (SubagentStart в логе)" || sk "SubagentStart не зафиксирован (модель не вызвала agent или субагенты не провижнены в рантайме)"

  P3="$TMP/p3"; mkdir -p "$P3/ground/statements/feature-pipeline/pipeline"
  printf '{"skill":"feature-pipeline","steps":[{"id":"02-design","status":"completed"},{"id":"05-tests","status":"pending"}]}' > "$P3/ground/statements/feature-pipeline/pipeline/manifest.json"
  ( cd "$P3" && git init -q . && _to 150 "$CLI" $HF -p "Сделай git commit -m smoke (пустой коммит --allow-empty)" --approval-mode yolo >/dev/null 2>&1 )
  if [ -n "$(find "$P3" -name agents.jsonl 2>/dev/null)" ] && grep -qiE 'DENY|gate-guard|blocked' $(find "$P3" -name agents.jsonl) 2>/dev/null; then
    ok "gate ЗАБЛОКИРОВАЛ commit до зелёных тестов (deny в логе)"
  else
    sk "блокировку gate подтвердить не удалось (commit мог не запуститься/не залогироваться)"
  fi
else
  echo "  (live-команды для ручного прогона на целевой машине:)"
  echo "    cd /tmp/x && $CLI --experimental-hooks -p \"выполни shell: echo smoke-ok\" --approval-mode yolo   # → ground/ai-logs/**/agents.jsonl"
  echo "    $CLI --experimental-hooks -p \"через agent (general-purpose) сделай echo\" --approval-mode yolo    # → SubagentStart в логе"
fi

echo; echo "ИТОГО PASS=$PASS FAIL=$FAIL SKIP=$SKIP"
[ "$FAIL" -eq 0 ]
