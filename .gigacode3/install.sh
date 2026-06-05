#!/usr/bin/env bash
# install.sh — пользовательская установка обвязки GigaCode (ставит ВСЁ сразу).
#
# Канал-агностично: получи эту папку любым способом (git clone / распакованный архив / общий каталог)
# и запусти install.sh из неё. Источник берётся из расположения самого скрипта, путь захардкоден не нужен.
#
# Usage:
#   bash install.sh [TARGET_HOME]      # по умолчанию ~/.gigacode (прод). Для теста: bash install.sh ~/.qwen
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${1:-$HOME/.gigacode}"; HOME_DIR="${HOME_DIR/#\~/$HOME}"

echo "==================================================================="
echo "  Установка GigaCode Forge  →  $HOME_DIR"
echo "==================================================================="

# ── пред-условия ──
ok=1
if command -v python3 >/dev/null 2>&1; then echo "  ✓ python3: $(command -v python3)"; else echo "  ✗ нужен python3 в PATH"; ok=0; fi
CLI="$(command -v gigacode || command -v qwen || true)"
if [ -n "$CLI" ]; then echo "  ✓ CLI рантайма: $CLI"; else echo "  ⚠ CLI gigacode/qwen не найден в PATH — поставь рантайм (без него обвязке негде работать)"; fi
[ "$ok" = 1 ] || { echo "Установка прервана: нет обязательных пред-условий."; exit 1; }

# ── полный деплой (вся обвязка сразу: hooks+skills+доки, мерж блока hooks, доктор) ──
echo; bash "$SRC/deploy.sh" "$HOME_DIR"

# ── что дальше ──
cat <<EOF

==================================================================
  ✅ Установлено. Дальше:
==================================================================
  1) Запускай рантайм ВСЕГДА с флагом хуков (иначе контроль качества выключен):
        gigacode --experimental-hooks            # или: gigacode --experimental-hooks -p "<задача>"

  2) Перед прогоном проверь, что контроль реально включён:
        python3 $HOME_DIR/hooks/preflight.py --project .

  3) Запусти фичу из корня репозитория кода:
        /skills feature-pipeline <JIRA-KEY | идея фичи>

  Руководство пользователя:   $HOME_DIR/GUIDE.md
  Обновить обвязку позже:      повтори  bash install.sh $HOME_DIR
EOF
