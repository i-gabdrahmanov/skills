#!/usr/bin/env bash
#
# deploy.sh — совместимость со старой командой установки. Установщик теперь install.sh:
#
#   bash install.sh forge    /path/to/back-repo
#   bash install.sh forge-ui /path/to/front-repo
#
# Старая форма переводится как есть: `deploy.sh <repo>` → `install.sh forge <repo>`,
# `deploy.sh --profile ui <repo>` → `install.sh forge-ui <repo>`. Старая раскладка
# (.gigacode/{skills,hooks,commands} + settings.json) install.sh снимает сам.
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="back"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 || { echo "deploy.sh: --profile без значения" >&2; exit 2; } ;;
    --profile=*) PROFILE="${1#--profile=}"; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
case "$PROFILE" in
  back) KIND=forge ;;
  ui) KIND=forge-ui ;;
  *) echo "deploy.sh: нет профиля поставки '$PROFILE'. Есть: back, ui (→ install.sh forge|forge-ui)." >&2; exit 2 ;;
esac
echo "deploy.sh устарел — ставлю через: bash install.sh $KIND ${ARGS[*]-}" >&2
exec bash "$SRC/install.sh" "$KIND" ${ARGS[@]+"${ARGS[@]}"}
