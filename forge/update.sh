#!/usr/bin/env bash
#
# update.sh — обновляет развёрнутый Forge в целевом проекте до последней версии репо.
#
# Запускается из склонированного репо Forge (как deploy.sh/uninstall.sh). Целевая папка
# ОБЯЗАТЕЛЬНА. Тянет свежий Forge (git pull в этом клоне) и переустанавливает в
# <target>/.gigacode/. Два режима:
#
#   мягко (по умолчанию):   git pull  →  bash deploy.sh <target>
#       overwrite source-managed файлов (hooks/skills/commands/доки), блок hooks в
#       settings.json пере-резолвится. Данные оператора не трогаются: ground/, свои
#       скиллы/хуки/команды, permissions/mcpServers, бэкапы. НО: скилл, УДАЛЁННЫЙ из
#       репо, останется сиротой в таргете (deploy прунит только хуки, не скиллы).
#
#   жёстко (--force):       git pull  →  bash uninstall.sh <target>  →  bash deploy.sh <target>
#       чистая переустановка форж-файлов: uninstall точечно снимает ВСЁ форж-своё (в т.ч.
#       скиллы, которых больше нет в репо), deploy ставит актуальный набор. ground/ и бэкапы
#       сохраняются (uninstall без --purge-state), самописные скиллы/хуки оператора — тоже.
#
# Порядок в --force (uninstall ПЕРЕД deploy) безопасен: uninstall сначала снимает блок hooks
# из settings.json, поэтому при любом обрыве проект остаётся рабочим (не зовёт удалённые хуки);
# следующий deploy возвращает всё на место.
#
# Usage (из корня склонированного Forge):
#   bash update.sh /path/to/target-project              # мягкое обновление (git pull + deploy)
#   bash update.sh /path/to/target-project --force       # чистая переустановка (git pull + uninstall + deploy)
#   bash update.sh /path/to/target-project --no-pull     # без git pull — переустановить из текущего клона
#   bash update.sh /path/to/target-project --dry-run     # показать план, ничего не делать
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # корень репо Forge

DEPLOY="$SRC/deploy.sh"
UNINSTALL="$SRC/uninstall.sh"

# --- разбор аргументов (позиционный target + флаги, как у deploy.sh/uninstall.sh) ---
TARGET=""
FORCE=0
NO_PULL=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --force)    FORCE=1; shift ;;
    --no-pull)  NO_PULL=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "update.sh: неизвестный аргумент: $1" >&2
      exit 2
      ;;
    *)
      if [ -n "$TARGET" ]; then
        echo "update.sh: лишний аргумент: $1 (целевая папка уже задана: $TARGET)" >&2
        exit 2
      fi
      TARGET="$1"; shift
      ;;
  esac
done

# --- целевая папка обязательна ---
if [ -z "$TARGET" ]; then
  echo "update.sh: не указана целевая папка проекта." >&2
  echo "Usage: bash update.sh /path/to/target-project [--force] [--no-pull] [--dry-run]" >&2
  exit 2
fi
if [ ! -d "$TARGET" ]; then
  echo "update.sh: целевая папка не существует или не каталог: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"

if [ "$TARGET" = "$SRC" ]; then
  echo "update.sh: целевая папка совпадает с исходным репо Forge — обновлять нечего." >&2
  exit 2
fi

# --- fail-fast: скрипты установки на месте ---
for s in "$DEPLOY" "$UNINSTALL"; do
  if [ ! -f "$s" ]; then
    echo "update.sh: не найден $s — репо Forge неполный." >&2
    exit 1
  fi
done

MODE="мягкое (git pull + deploy)"
[ "$FORCE" -eq 1 ] && MODE="ЖЁСТКОЕ --force (git pull + uninstall + deploy)"

echo "== update Forge → $TARGET/.gigacode =="
echo "   режим: $MODE"

# --- план для --dry-run: deploy.sh не умеет --dry-run, поэтому просто печатаем шаги ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "== DRY-RUN: ничего не выполняется, только план =="
  if [ "$NO_PULL" -eq 1 ]; then
    echo "  [skip] git pull (--no-pull)"
  else
    echo "  [plan] git -C $SRC pull --ff-only"
  fi
  if [ "$FORCE" -eq 1 ]; then
    echo "  [plan] bash $UNINSTALL $TARGET        # снять форж-своё (ground/ и бэкапы сохранятся)"
  fi
  echo "  [plan] bash $DEPLOY $TARGET            # поставить актуальный Forge"
  echo "== DRY-RUN завершён =="
  exit 0
fi

# --- 1. git pull (свежий Forge) ---------------------------------------------
if [ "$NO_PULL" -eq 1 ]; then
  echo "  (git pull пропущен: --no-pull — обновляю из текущего состояния клона)"
elif ! command -v git >/dev/null 2>&1; then
  echo "  ⚠ git не найден в PATH — не могу подтянуть свежую версию. Обновляю из текущего клона." >&2
elif ! git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "  ⚠ $SRC не git-репозиторий — нечего pull'ить. Обновляю из текущего состояния файлов." >&2
else
  echo "== git pull =="
  # --ff-only: тянем ровно до origin без merge-коммитов; при расхождении веток падаем
  # с понятной ошибкой, чтобы оператор разрулил вручную, а не получил тихий merge.
  if git -C "$SRC" pull --ff-only; then
    echo "  ✓ Forge подтянут (git pull --ff-only)"
  else
    echo "update.sh: git pull --ff-only не прошёл (расхождение веток или локальные изменения)." >&2
    echo "  Разреши состояние в $SRC вручную, либо запусти с --no-pull, чтобы обновить из текущего клона." >&2
    exit 1
  fi
fi

# --- 2. переустановка --------------------------------------------------------
if [ "$FORCE" -eq 1 ]; then
  echo
  echo "== uninstall (снимаю форж-своё; ground/ и бэкапы сохраняются) =="
  bash "$UNINSTALL" "$TARGET"
fi

echo
echo "== deploy (ставлю актуальный Forge) =="
bash "$DEPLOY" "$TARGET"

echo
echo "== Forge в $TARGET обновлён ($MODE) =="
