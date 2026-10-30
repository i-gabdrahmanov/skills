#!/usr/bin/env bash
#
# uninstall.sh — снимает forge / forge-ui с проекта (зеркало install.sh).
#
#   bash uninstall.sh forge    /path/to/project            # снять extension forge
#   bash uninstall.sh forge-ui /path/to/project            # снять extension forge-ui
#   bash uninstall.sh /path/to/project                     # снять то, что стоит (и старую раскладку)
#   bash uninstall.sh --legacy-only /path/to/project       # только старая раскладка (зовёт install.sh)
#
# Что делает:
#   1. Extension (<repo>/.gigacode/extensions/<forge|forge-ui>/): снимает ссылку в рантайме
#      (`gigacode extensions uninstall <имя>` — у link-установки исходники не трогаются) и
#      удаляет каталог extension'а ЦЕЛИКОМ: он весь форжевый. Локальный конфиг
#      minor-defect-fix/config.json уходит в .gigacode/minor-defect-fix-config.json.bak.
#   2. Старая раскладка (project-модель ≤ 0.5): код форжа в co-located .gigacode/{skills,hooks,
#      commands}, хуки и MCP в .gigacode/settings.json. Сначала блок hooks из settings.json
#      (с бэкапом), потом ТОЧЕЧНО форж-своё: перечень — исходное репо + реестр прошлой установки
#      (.gigacode/.forge-deployed) + tombstones.txt; самописные скиллы/хуки оператора рядом не
#      трогаются, опустевший каталог убирается только rmdir'ом.
#   3. .gitignore: снимается ровно блок forge (между маркерами).
#   4. --purge-state: дополнительно ground/ и git-refs чекпойнтов refs/forge/*.
#
# Порядок «сначала подключение, потом файлы» — не косметика: снесённые файлы при живом
# подключении (ссылка в рантайме, блок hooks в settings.json) = каждый вызов инструмента падает.
#
# Что НЕ трогает: самописное оператора в .gigacode/{skills,hooks,commands}/; ground/ (только
# --purge-state); остальные секции settings.json (permissions, чужие mcpServers); *.bak.
#
# Флаги: --dry-run (план, ничего не делать), --purge-state, --runtime gigacode|qwen, --no-link
# (рантайм не трогать — снять ссылку вручную: gigacode extensions uninstall <имя>).
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # корень репо Forge

# shellcheck source=_deployed_manifest.sh
. "$SRC/_deployed_manifest.sh"

PY=()
if command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v python >/dev/null 2>&1; then PY=(python)
elif command -v py >/dev/null 2>&1; then PY=(py -3)
fi

usage() { echo "Usage: bash uninstall.sh [forge|forge-ui|--legacy-only] /path/to/project [--dry-run] [--purge-state] [--runtime R] [--no-link]" >&2; }

KIND=""
TARGET=""
DRY_RUN=0
PURGE_STATE=0
LEGACY_ONLY=0
NO_LINK=0
RUNTIME="${FORGE_RUNTIME:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)     DRY_RUN=1; shift ;;
    --purge-state) PURGE_STATE=1; shift ;;
    --legacy-only) LEGACY_ONLY=1; shift ;;
    --no-link)     NO_LINK=1; shift ;;
    --runtime) RUNTIME="${2:-}"; shift 2 || { echo "uninstall.sh: --runtime без значения" >&2; exit 2; } ;;
    --runtime=*) RUNTIME="${1#--runtime=}"; shift ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "uninstall.sh: неизвестный аргумент: $1" >&2; usage; exit 2 ;;
    forge|forge-ui)
      if [ -z "$KIND" ] && [ -z "$TARGET" ]; then KIND="$1"
      elif [ -z "$TARGET" ]; then TARGET="$1"
      else echo "uninstall.sh: лишний аргумент: $1" >&2; exit 2
      fi
      shift ;;
    *)
      if [ -n "$TARGET" ]; then
        echo "uninstall.sh: лишний аргумент: $1 (папка проекта уже задана: $TARGET)" >&2
        exit 2
      fi
      TARGET="$1"; shift ;;
  esac
done

if [ -z "$TARGET" ]; then
  echo "uninstall.sh: не указана папка проекта — без пути ничего не удаляется." >&2
  usage; exit 2
fi
if [ ! -d "$TARGET" ]; then
  echo "uninstall.sh: папка проекта не существует или не каталог: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"
if [ "$TARGET" = "$SRC" ]; then
  echo "uninstall.sh: папка проекта совпадает с исходным репо Forge — это сам Forge, а не установка." >&2
  exit 2
fi

GIG="$TARGET/.gigacode"
EXT_ROOT="$GIG/extensions"
if [ ! -d "$GIG" ]; then
  echo "uninstall.sh: в $TARGET нет .gigacode/ — Forge не установлен, снимать нечего."
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "== DRY-RUN: uninstall Forge ← $TARGET (ничего не удаляется) =="
else
  echo "== uninstall Forge ← $TARGET =="
fi

remove_path() {  # $1=path  $2=человекочитаемое имя
  [ -e "$1" ] || return 0
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] удалить: $2"
  else
    rm -rf "$1"
    echo "  ✓ удалено: $2"
  fi
}

save_mdf_config() {  # $1=путь к config.json — конфиг оператора уносить молча нельзя
  [ -f "$1" ] || return 0
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] конфиг оператора → $GIG/minor-defect-fix-config.json.bak"
  else
    cp -p "$1" "$GIG/minor-defect-fix-config.json.bak"
    echo "  ✓ конфиг оператора сохранён → $GIG/minor-defect-fix-config.json.bak"
  fi
}

# ── 1. extension ─────────────────────────────────────────────────────────────
KINDS=""
if [ "$LEGACY_ONLY" -eq 0 ]; then
  if [ -n "$KIND" ]; then KINDS="$KIND"; else
    for k in forge forge-ui; do [ -d "$EXT_ROOT/$k" ] && KINDS="$KINDS $k"; done
  fi
fi
REMOVED_EXT=0
for k in $KINDS; do
  EXT="$EXT_ROOT/$k"
  if [ ! -d "$EXT" ]; then
    echo "  ($k не установлен extension'ом — $EXT нет)"
    continue
  fi
  if [ "$NO_LINK" -eq 1 ]; then
    echo "  (--no-link: ссылку в рантайме снять вручную: gigacode extensions uninstall <имя из $EXT/gigacode-extension.json>)"
  elif [ "${#PY[@]}" -eq 0 ]; then
    echo "  ⚠ python не найден — ссылку снять вручную: gigacode extensions uninstall <имя из $EXT/gigacode-extension.json>"
  elif [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] снять ссылку в рантайме (forge_extension.py unlink)"
  else
    "${PY[@]}" -X utf8 "$SRC/hooks/forge_extension.py" unlink --ext "$EXT" --project "$TARGET" --runtime "$RUNTIME" || true
  fi
  save_mdf_config "$EXT/skills/minor-defect-fix/config.json"
  remove_path "$EXT" ".gigacode/extensions/$k/ (extension $k)"
  REMOVED_EXT=1
done
if [ "$DRY_RUN" -eq 0 ] && [ -d "$EXT_ROOT" ]; then
  rmdir "$EXT_ROOT" 2>/dev/null || true
fi

# ── 2. старая раскладка (project-модель ≤ 0.5) ───────────────────────────────
SETTINGS="$GIG/settings.json"
# Признаки — то, что клал только старый deploy.sh. Свои .gigacode/{skills,commands} оператора
# старой установкой не считаются: в новой раскладке они законны и форжу не принадлежат.
LEGACY=0
for p in "$(forge_manifest_file "$GIG")" "$GIG/.forge-profile" "$GIG/deploy-local.sh" \
         "$GIG/hooks/gate-guard.py" "$GIG/hooks/settings.hooks.json"; do
  if [ -e "$p" ]; then LEGACY=1; fi
done
if [ -f "$SETTINGS" ] && grep -q '\.gigacode/hooks/' "$SETTINGS" 2>/dev/null; then LEGACY=1; fi

if [ "$LEGACY" -eq 1 ]; then
  # 2.1 settings.json: снять блок hooks ПЕРВЫМ (форж-хуки этого проекта и свои MCP-серверы;
  # чужие записи и прочие секции резолвер сохраняет — он же их и ставил, контракт у него один)
  RESOLVER="$SRC/hooks/resolve_hook_paths.py"
  if [ -f "$SETTINGS" ] && grep -q '\.gigacode/hooks' "$SETTINGS" 2>/dev/null; then
    if [ "${#PY[@]}" -eq 0 ]; then
      echo "uninstall.sh: не найден python — без него нельзя снять блок hooks из settings.json, а удалять" >&2
      echo "  файлы, оставив хуки в конфиге, нельзя: рантайм будет падать на каждом вызове инструмента." >&2
      exit 1
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "  [dry-run] снятие блока hooks из settings.json:"
      "${PY[@]}" -X utf8 "$RESOLVER" --project "$TARGET" --remove --dry-run | sed 's/^/    /'
    else
      if [ ! -f "$SETTINGS.bak" ]; then
        cp -p "$SETTINGS" "$SETTINGS.bak"
        echo "  [backup] первозданный оригинал → $SETTINGS.bak"
      else
        TS="$(date +%Y%m%d-%H%M%S)"
        cp -p "$SETTINGS" "$SETTINGS.$TS.bak"
        echo "  [backup] вечный .bak сохранён; текущая версия → $SETTINGS.$TS.bak"
      fi
      "${PY[@]}" -X utf8 "$RESOLVER" --project "$TARGET" --remove
      echo "  ✓ старый блок hooks и MCP forge сняты из settings.json (permissions, чужие MCP и хуки сохранены)"
    fi
  fi

  # 2.2 конфиг оператора — отставить в сторону
  save_mdf_config "$GIG/skills/minor-defect-fix/config.json"

  # 2.3 форж-своё в co-located каталогах — ТОЧЕЧНО (реестр читаем один раз)
  MANIFEST_ENTRIES="$(forge_manifest_entries "$GIG")"
  _forge_in_manifest() {  # $1="<dir>/<name>"
    [ -n "$MANIFEST_ENTRIES" ] || return 1
    printf '%s\n' "$MANIFEST_ENTRIES" | grep -Fxq "$1"
  }
  while IFS= read -r _e; do
    [ -n "$_e" ] || continue
    remove_path "$GIG/$_e" "$_e (удалено из репо)"
  done <<EOF
$(forge_tombstones "$SRC" "$GIG")
EOF
  # Владение: запись есть в исходнике ИЛИ её клал прошлый деплой (реестр .forge-deployed).
  # Всё прочее — операторское (был инцидент: rm -rf skills/ унёс самописные скиллы); родительский
  # каталог — только rmdir'ом.
  remove_forge_owned() {  # $1=src-эталон  $2=dst-в-проекте  $3=label
    local src="$1" dst="$2" label="$3" entry base removed=0 kept=0
    [ -d "$dst" ] || return 0
    for entry in "$dst"/* "$dst"/.[!.]*; do
      [ -e "$entry" ] || continue
      base="$(basename "$entry")"
      if [ -e "$src/$base" ] || _forge_in_manifest "$label/$base"; then
        if [ "$DRY_RUN" -eq 1 ]; then echo "  [dry-run] удалить: $label/$base"; else rm -rf "$entry"; fi
        removed=$((removed + 1))
      else
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
  # /forge фронт-репо (профиль ui) лежал в .gigacode/commands/ — его исходник в forge-ui/commands
  [ -d "$SRC/forge-ui/commands" ] && remove_forge_owned "$SRC/forge-ui/commands" "$GIG/commands" "commands"
  remove_path "$(forge_manifest_file "$GIG")" "$FORGE_MANIFEST_NAME (реестр старой установки)"
  remove_path "$GIG/.forge-profile"     ".forge-profile (профиль старой установки)"
  remove_path "$GIG/deploy-local.sh"    "deploy-local.sh (старая раскладка)"
  remove_path "$GIG/FORGE.md"           "FORGE.md (старая раскладка)"
  remove_path "$GIG/SKILLS-REGISTRY.md" "SKILLS-REGISTRY.md (старая раскладка)"
elif [ "$LEGACY_ONLY" -eq 1 ]; then
  echo "  (старой раскладки нет — снимать нечего)"
fi

if [ "$LEGACY_ONLY" -eq 1 ]; then
  exit 0
fi

# ── 3. .gitignore: снять РОВНО блок forge (между маркерами) ───────────────────
GI="$TARGET/.gitignore"
GI_BEGIN="# >>> forge: рабочие данные пайплайна >>>"
GI_END="# <<< forge <<<"
if [ -f "$GI" ] && grep -qF "$GI_BEGIN" "$GI"; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] снять блок forge из .gitignore"
  else
    awk -v b="$GI_BEGIN" -v e="$GI_END" '
      $0 == b { skip = 1; next }
      $0 == e { skip = 0; next }
      !skip   { print }
    ' "$GI" > "$GI.forge-tmp" && mv "$GI.forge-tmp" "$GI"
    echo "  ✓ .gitignore: блок forge снят (строки оператора не тронуты)"
  fi
fi

# ── 4. рабочие данные — только по явному флагу ───────────────────────────────
if [ "$PURGE_STATE" -eq 1 ]; then
  echo
  echo "== --purge-state: сношу рабочие данные пайплайна =="
  remove_path "$TARGET/ground" "ground/ (BRD/SDD/манифесты/evidence/approvals/логи)"
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
elif [ -d "$TARGET/ground" ]; then
  echo
  echo "  ℹ ground/ ОСТАВЛЕН (рабочие данные: BRD/SDD/манифесты/логи)."
  echo "    Снести вместе с git-чекпойнтами: bash uninstall.sh $TARGET --purge-state"
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
if [ "$REMOVED_EXT" -eq 0 ] && [ "$LEGACY" -eq 0 ]; then
  echo "== Forge в $TARGET не найден — снимать было нечего =="
else
  echo "== Forge снят с $TARGET =="
fi
[ -f "$SETTINGS" ] && echo "  осталось: settings.json (без форжевых хуков) + бэкапы *.bak"
[ -d "$TARGET/ground" ] && echo "  осталось: ground/ — рабочие данные пайплайна"
[ -f "$GIG/minor-defect-fix-config.json.bak" ] && echo "  осталось: minor-defect-fix-config.json.bak — конфиг оператора"
echo
echo "  Вернуть: bash install.sh <forge|forge-ui> $TARGET"
