#!/usr/bin/env bash
#
# uninstall.sh — деинсталлятор Forge: снимает обвязку из указанной папки целевого проекта
#                (зеркало deploy.sh: те же аргументы, тот же порядок проверок).
#
# Запускается из склонированного репо Forge. Целевая папка ОБЯЗАТЕЛЬНА — без неё
# ничего никуда не удаляется (проектов может быть несколько, угадывать нельзя).
#
# Что делает (порядок важен — см. ниже):
#   1. Снимает блок hooks из <target>/.gigacode/settings.json (с бэкапом, как deploy-local.sh).
#   2. Отставляет в сторону локальный конфиг оператора (minor-defect-fix/config.json).
#   3. Удаляет ТОЧЕЧНО то, что положил deploy.sh (перечень из исходного репо), внутри
#      co-located skills/ hooks/ commands/ + deploy-local.sh и доки. Самописные скиллы/хуки
#      оператора рядом — НЕ трогает; опустевший каталог убирает только rmdir'ом.
#   4. --purge-state: дополнительно сносит рабочие данные (ground/ + git-refs чекпойнтов).
#
# Порядок «сначала settings.json, потом файлы» — не косметика: если снести hooks/ первым и
# упасть на середине, рантайм останется с блоком hooks, зовущим удалённые скрипты, и КАЖДЫЙ
# вызов инструмента будет падать. Снятый блок при любом обрыве оставляет проект рабочим.
#
# Что НЕ трогает (по умолчанию):
#   - самописные скиллы/хуки/команды оператора в .gigacode/{skills,hooks,commands}/ —
#     всё, чего нет в исходном репо Forge, остаётся на месте.
#   - ground/            — рабочие данные пайплайна (BRD/SDD/манифесты/логи). Только --purge-state.
#   - settings.json      — остальные секции (permissions, mcpServers, $version) не наши.
#   - *.bak              — бэкапы, в т.ч. первозданный settings.json.bak (до установки Forge).
#   - refs/forge/*       — git-чекпойнты отката. Только --purge-state.
#   - <home>/ai-logs-archive/ — общий архив логов вне проекта, шарится между проектами.
#
# Usage (из корня склонированного Forge):
#   bash uninstall.sh /path/to/target-project
#   bash uninstall.sh /path/to/target-project --dry-run       # показать план, ничего не делать
#   bash uninstall.sh /path/to/target-project --purge-state    # + снести ground/ и refs/forge/*
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # корень репо Forge

# --- поиск python-интерпретатора (Windows/git-bash часто без python3, только python/py) ---
PY=()
if command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v python >/dev/null 2>&1; then PY=(python)
elif command -v py >/dev/null 2>&1; then PY=(py -3)
fi

# --- разбор аргументов (позиционный target + флаги, как у deploy.sh) ---
TARGET=""
DRY_RUN=0
PURGE_STATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)     DRY_RUN=1; shift ;;
    --purge-state) PURGE_STATE=1; shift ;;
    -h|--help)
      sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "uninstall.sh: неизвестный аргумент: $1" >&2
      exit 2
      ;;
    *)
      if [ -n "$TARGET" ]; then
        echo "uninstall.sh: лишний аргумент: $1 (целевая папка уже задана: $TARGET)" >&2
        exit 2
      fi
      TARGET="$1"; shift
      ;;
  esac
done

# --- целевая папка обязательна ---
if [ -z "$TARGET" ]; then
  echo "uninstall.sh: не указана целевая папка проекта." >&2
  echo "Usage: bash uninstall.sh /path/to/target-project [--dry-run] [--purge-state]" >&2
  echo "  Без пути ничего не удаляется." >&2
  exit 2
fi

if [ ! -d "$TARGET" ]; then
  echo "uninstall.sh: целевая папка не существует или не каталог: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"

# запрет «деинсталляции из себя» — симметрично запрету деплоя в себя
if [ "$TARGET" = "$SRC" ]; then
  echo "uninstall.sh: целевая папка совпадает с исходным репо Forge — это сам Forge, а не деплой." >&2
  exit 2
fi

GIG="$TARGET/.gigacode"

# Нечего снимать — не ошибка, а норма (идемпотентность: повторный запуск, прерванный прогон,
# «снеси Forge везде» по списку проектов). Ошибкой были бы только кривые аргументы.
if [ ! -d "$GIG" ]; then
  echo "uninstall.sh: в $TARGET нет .gigacode/ — Forge не развёрнут, снимать нечего."
  exit 0
fi

# Есть ли что удалять из файлов (после успешного снятия остаётся только settings.json + бэкапы)
HAS_ARTIFACTS=0
for p in "$GIG/hooks" "$GIG/skills" "$GIG/deploy-local.sh"; do
  if [ -e "$p" ]; then HAS_ARTIFACTS=1; fi
done

if [ "$DRY_RUN" -eq 1 ]; then
  echo "== DRY-RUN: uninstall Forge ← $GIG (ничего не удаляется) =="
else
  echo "== uninstall Forge ← $GIG =="
fi

# ── 1. settings.json: снять блок hooks ПЕРВЫМ ────────────────────────────────
# Снимает forge-хуки этого проекта; чужие записи и прочие секции (permissions/mcpServers)
# резолвер сохраняет — он же их и ставил, контракт блока hooks у него один.
RESOLVER="$SRC/hooks/resolve_hook_paths.py"
if [ ! -f "$RESOLVER" ]; then
  echo "uninstall.sh: не найден $RESOLVER — репо Forge неполный." >&2
  exit 1
fi
if [ "${#PY[@]}" -eq 0 ]; then
  echo "uninstall.sh: не найден ни python3, ни python, ни py в PATH." >&2
  echo "  Без python нельзя снять блок hooks из settings.json — а удалять файлы, оставив" >&2
  echo "  хуки в конфиге, нельзя: рантайм будет падать на каждом вызове инструмента." >&2
  exit 1
fi

SETTINGS="$GIG/settings.json"
# Хвост пути хука в шаблоне всегда с прямыми слэшами (${PROJECT_ROOT}/.gigacode/hooks/...),
# поэтому грепа достаточно, чтобы не плодить бэкапы на пустых прогонах.
if [ ! -f "$SETTINGS" ]; then
  echo "  (settings.json нет — снимать хуки не нужно)"
elif ! grep -q '\.gigacode/hooks' "$SETTINGS" 2>/dev/null; then
  echo "  (forge-хуков в settings.json нет — уже сняты)"
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "  [dry-run] снятие блока hooks из settings.json:"
  "${PY[@]}" -X utf8 "$RESOLVER" --project "$TARGET" --remove --dry-run | sed 's/^/    /'
else
  # бэкап — та же конвенция, что в deploy-local.sh: вечный .bak (первозданный оригинал)
  # никогда не затирается, текущая версия уходит в .<TS>.bak
  if [ ! -f "$SETTINGS.bak" ]; then
    cp -p "$SETTINGS" "$SETTINGS.bak"
    echo "  [backup] первозданный оригинал → $SETTINGS.bak"
  else
    TS="$(date +%Y%m%d-%H%M%S)"
    cp -p "$SETTINGS" "$SETTINGS.$TS.bak"
    echo "  [backup] вечный .bak сохранён; текущая версия → $SETTINGS.$TS.bak"
  fi
  "${PY[@]}" -X utf8 "$RESOLVER" --project "$TARGET" --remove
  echo "  ✓ блок hooks снят из settings.json (permissions/mcpServers и чужие хуки сохранены)"
fi

# ── 2. локальный конфиг оператора — отставить в сторону ──────────────────────
# deploy.sh его НИКОГДА не перетирает (маппинг проект→спека с реальными путями машины),
# значит и уносить его молча вместе со skills/ нельзя.
MDF_CFG="$GIG/skills/minor-defect-fix/config.json"
if [ -f "$MDF_CFG" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] конфиг оператора → $GIG/minor-defect-fix-config.json.bak"
  else
    cp -p "$MDF_CFG" "$GIG/minor-defect-fix-config.json.bak"
    echo "  ✓ конфиг оператора сохранён → $GIG/minor-defect-fix-config.json.bak"
  fi
fi

# ── 3. удалить то, что положил deploy.sh — ТОЧЕЧНО, не всю папку ──────────────
remove_path() {  # $1=path  $2=человекочитаемое имя (одиночные форж-файлы)
  [ -e "$1" ] || return 0
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] удалить: $2"
  else
    rm -rf "$1"
    echo "  ✓ удалено: $2"
  fi
}

# skills/, hooks/, commands/ — co-located: рантайм читает из .gigacode/{skills,hooks,commands}
# и туда же оператор кладёт СВОИ скиллы/хуки/команды. Снести каталог целиком (rm -rf) уносит
# чужое — реальный инцидент: uninstall стёр все самописные скиллы оператора. Поэтому удаляем
# ровно то, что клал deploy.sh: перечень берём из исходного репо ($SRC); всё, чего в $SRC нет,
# — операторское, не трогаем (симметрично prune в deploy.sh). Родительский каталог убираем
# ТОЛЬКО через rmdir (не rm -rf): опустеет — уйдёт, останется чужое (в т.ч. скрытое) — выживет.
remove_forge_owned() {  # $1=src-эталон  $2=dst-в-проекте  $3=label
  local src="$1" dst="$2" label="$3" entry base removed=0 kept=0
  [ -d "$dst" ] || return 0
  # dotfiles тоже (deploy копирует tar'ом); несматченный glob отсеет [ -e ].
  for entry in "$dst"/* "$dst"/.[!.]*; do
    [ -e "$entry" ] || continue
    base="$(basename "$entry")"
    if [ -e "$src/$base" ]; then                    # forge-owned → снять
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [dry-run] удалить: $label/$base"
      else
        rm -rf "$entry"
      fi
      removed=$((removed + 1))
    else                                            # операторское → оставить
      kept=$((kept + 1))
      [ "$DRY_RUN" -eq 1 ] && echo "  [dry-run] ОСТАВИТЬ (операторское): $label/$base"
    fi
  done
  if [ "$kept" -gt 0 ]; then
    echo "  ✓ снято форж-записей из $label/: $removed; ОСТАВЛЕНО операторского: $kept ($label/ сохранён)"
  elif [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] удалить: $label/ (опустеет после снятия $removed форж-записей)"
  else
    rmdir "$dst" 2>/dev/null \
      && echo "  ✓ удалено: $label/ (форж-записей: $removed)" \
      || echo "  ✓ снято форж-записей из $label/: $removed ($label/ сохранён — остались скрытые файлы)"
  fi
}

remove_forge_owned "$SRC/skills"   "$GIG/skills"   "skills"
remove_forge_owned "$SRC/hooks"    "$GIG/hooks"    "hooks"
[ -d "$SRC/commands" ] && remove_forge_owned "$SRC/commands" "$GIG/commands" "commands"
remove_path "$GIG/deploy-local.sh"  "deploy-local.sh"
remove_path "$GIG/FORGE.md"         "FORGE.md"
remove_path "$GIG/SKILLS-REGISTRY.md" "SKILLS-REGISTRY.md"

# ── 4. рабочие данные — только по явному флагу ───────────────────────────────
if [ "$PURGE_STATE" -eq 1 ]; then
  echo
  echo "== --purge-state: сношу рабочие данные пайплайна =="
  remove_path "$TARGET/ground" "ground/ (BRD/SDD/манифесты/evidence/approvals/логи)"

  # git-чекпойнты отката (refs/forge/*) — служебные ref'ы, ветки/HEAD не затрагивают
  if command -v git >/dev/null 2>&1 && [ -d "$TARGET/.git" ]; then
    REFS="$(git -C "$TARGET" for-each-ref --format='%(refname)' refs/forge/ 2>/dev/null || true)"
    if [ -n "$REFS" ]; then
      COUNT="$(printf '%s\n' "$REFS" | wc -l | tr -d ' ')"
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [dry-run] удалить git-refs чекпойнтов: $COUNT шт. (refs/forge/*)"
      else
        printf '%s\n' "$REFS" | while IFS= read -r ref; do
          [ -n "$ref" ] && git -C "$TARGET" update-ref -d "$ref"
        done
        echo "  ✓ удалены git-refs чекпойнтов: $COUNT шт. (refs/forge/*)"
      fi
    else
      echo "  (git-refs чекпойнтов нет)"
    fi
  fi
else
  if [ -d "$TARGET/ground" ]; then
    echo
    echo "  ℹ ground/ ОСТАВЛЕН (рабочие данные: BRD/SDD/манифесты/логи)."
    echo "    Снести вместе с git-чекпойнтами: bash uninstall.sh $TARGET --purge-state"
  fi
fi

# ── 5. подчистить пустой .gigacode/ ──────────────────────────────────────────
if [ "$DRY_RUN" -eq 0 ] && [ -d "$GIG" ]; then
  rmdir "$GIG" 2>/dev/null && echo "  ✓ удалён пустой $GIG" || true
fi

echo
if [ "$DRY_RUN" -eq 1 ]; then
  echo "== DRY-RUN завершён — ничего не изменено =="
  exit 0
fi

if [ "$HAS_ARTIFACTS" -eq 0 ]; then
  echo "== Forge в $TARGET уже был снят — проверил settings.json =="
else
  echo "== Forge снят с $TARGET =="
fi
if [ -f "$SETTINGS" ]; then
  echo "  осталось: settings.json (без блока hooks) + бэкапы *.bak"
fi
if [ -d "$TARGET/ground" ]; then
  echo "  осталось: ground/ — рабочие данные пайплайна"
fi
if [ -f "$GIG/minor-defect-fix-config.json.bak" ]; then
  echo "  осталось: minor-defect-fix-config.json.bak — конфиг оператора"
fi
echo "  не тронут: <home>/ai-logs-archive/ (общий архив логов вне проекта)"
echo
echo "  Вернуть Forge: bash deploy.sh $TARGET"
