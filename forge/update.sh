#!/usr/bin/env bash
#
# update.sh — обновляет установленный forge / forge-ui в проекте до последней версии репо.
#
# Запускается из склонированного репо Forge. Папка проекта ОБЯЗАТЕЛЬНА. Тянет свежий Forge
# (git pull --ff-only в этом клоне) и переставляет поставку: install.sh подменяет каталог
# extension'а целиком, поэтому снятое из репо в проекте не остаётся, а ground/, самописное
# оператора и конфиг minor-defect-fix переживают обновление.
#
# Какую поставку обновлять, скрипт понимает сам: стоит .gigacode/extensions/forge-ui — forge-ui,
# стоит forge — forge; старая раскладка (≤ 0.5) — по её профилю (.gigacode/.forge-profile: ui →
# forge-ui, иначе forge), и install.sh мигрирует её в extension. Можно назвать явно.
#
#   bash update.sh /path/to/project                 # git pull + install.sh <как стоит>
#   bash update.sh forge-ui /path/to/project        # то же, поставка названа явно
#   bash update.sh /path/to/project --force         # + сначала uninstall.sh (ground/ сохраняется)
#   bash update.sh /path/to/project --no-pull       # без git pull — из текущего клона
#   bash update.sh /path/to/project --dry-run       # показать план, ничего не делать
#   (--runtime / --no-link — передаются install.sh)
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # корень репо Forge
INSTALL="$SRC/install.sh"
UNINSTALL="$SRC/uninstall.sh"

KIND=""
TARGET=""
FORCE=0
NO_PULL=0
DRY_RUN=0
PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --force)    FORCE=1; shift ;;
    --no-pull)  NO_PULL=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    --no-link)  PASS+=("--no-link"); shift ;;
    --runtime)  PASS+=("--runtime" "${2:-}"); shift 2 || { echo "update.sh: --runtime без значения" >&2; exit 2; } ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "update.sh: неизвестный аргумент: $1" >&2; exit 2 ;;
    forge|forge-ui)
      if [ -z "$KIND" ] && [ -z "$TARGET" ]; then KIND="$1"; else TARGET="$1"; fi
      shift ;;
    *)
      if [ -n "$TARGET" ]; then
        echo "update.sh: лишний аргумент: $1 (папка проекта уже задана: $TARGET)" >&2
        exit 2
      fi
      TARGET="$1"; shift ;;
  esac
done

if [ -z "$TARGET" ]; then
  echo "update.sh: не указана папка проекта." >&2
  echo "Usage: bash update.sh [forge|forge-ui] /path/to/project [--force] [--no-pull] [--dry-run]" >&2
  exit 2
fi
if [ ! -d "$TARGET" ]; then
  echo "update.sh: папка проекта не существует или не каталог: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"
if [ "$TARGET" = "$SRC" ]; then
  echo "update.sh: папка проекта совпадает с исходным репо Forge — обновлять нечего." >&2
  exit 2
fi
for s in "$INSTALL" "$UNINSTALL"; do
  [ -f "$s" ] || { echo "update.sh: не найден $s — репо Forge неполный." >&2; exit 1; }
done

GIG="$TARGET/.gigacode"
if [ -z "$KIND" ]; then
  if [ -d "$GIG/extensions/forge-ui" ]; then KIND=forge-ui
  elif [ -d "$GIG/extensions/forge" ]; then KIND=forge
  elif [ -f "$GIG/.forge-profile" ] && grep -q '^profile ui' "$GIG/.forge-profile"; then KIND=forge-ui
  else KIND=forge
  fi
fi

MODE="git pull + install.sh $KIND"
[ "$FORCE" -eq 1 ] && MODE="git pull + uninstall.sh + install.sh $KIND (--force)"
[ "$NO_PULL" -eq 1 ] && MODE="${MODE/git pull + /}"
echo "== update $KIND → $TARGET =="
echo "   режим: $MODE"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "== DRY-RUN: ничего не выполняется, только план =="
  [ "$NO_PULL" -eq 1 ] && echo "  [skip] git pull (--no-pull)" || echo "  [plan] git -C $SRC pull --ff-only"
  [ "$FORCE" -eq 1 ] && echo "  [plan] bash $UNINSTALL $KIND $TARGET     # ground/ и бэкапы сохранятся"
  echo "  [plan] bash $INSTALL $KIND $TARGET"
  echo "== DRY-RUN завершён =="
  exit 0
fi

if [ "$NO_PULL" -eq 1 ]; then
  echo "  (git pull пропущен: --no-pull — обновляю из текущего состояния клона)"
elif ! command -v git >/dev/null 2>&1; then
  echo "  ⚠ git не найден в PATH — не могу подтянуть свежую версию. Обновляю из текущего клона." >&2
elif ! git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "  ⚠ $SRC не git-репозиторий — нечего pull'ить. Обновляю из текущего состояния файлов." >&2
else
  echo "== git pull =="
  if git -C "$SRC" pull --ff-only; then
    echo "  ✓ Forge подтянут (git pull --ff-only)"
  else
    echo "update.sh: git pull --ff-only не прошёл (расхождение веток или локальные изменения)." >&2
    echo "  Разреши состояние в $SRC вручную, либо запусти с --no-pull." >&2
    exit 1
  fi
fi

if [ "$FORCE" -eq 1 ]; then
  echo
  echo "== uninstall (ground/ и бэкапы сохраняются) =="
  bash "$UNINSTALL" "$KIND" "$TARGET" ${PASS[@]+"${PASS[@]}"}
fi
echo
bash "$INSTALL" "$KIND" "$TARGET" ${PASS[@]+"${PASS[@]}"}
echo
echo "== $KIND в $TARGET обновлён ($MODE) =="
