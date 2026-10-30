#!/usr/bin/env bash
#
# install.sh — ставит forge ИЛИ forge-ui в проект extension'ом GigaCode.
#
#   bash install.sh forge    /path/to/back-repo     # бэк-репо: полный forge (профиль back)
#   bash install.sh forge-ui /path/to/front-repo    # фронт-репо: forge-ui (профиль ui)
#
# Установки раздельные: одна поставка на репо (хуки control-plane у них общие — две поставки в
# одном репо гоняли бы каждый хук дважды), вторая — отказ с подсказкой, как снять первую.
#
# Раскладка: <repo>/.gigacode/extensions/<forge|forge-ui>/ — каталог ЦЕЛИКОМ форжевый
# (манифест, hooks/ + hooks/hooks.json, skills/, commands/, deploy-local.sh). Файлы лежат в
# проекте: GigaCode режет shell к скриптам вне каталога проекта, а гейты зовут свои скрипты
# шеллом. В ~/.gigacode/extensions кладётся только ССЫЛКА (`gigacode extensions link`), и
# extension включается только для этого workspace. Подробности — hooks/forge_extension.py.
#
# Что делает:
#   0. Снимает старую раскладку (≤ 0.5: код в .gigacode/{skills,hooks,commands}, хуки в
#      .gigacode/settings.json) — uninstall.sh --legacy-only: иначе хуки задвоятся, а
#      проектные скиллы перекроют скиллы extension'а (порядок резолва project > extension).
#   1. Собирает поставку по профилю во временный каталог рядом и подменяет ею прежнюю целиком
#      (снятое из репо не переживает обновление; локальный config.json minor-defect-fix —
#      переживает).
#   2. Манифест + hooks/hooks.json с зашитым интерпретатором и абсолютными путями.
#   3. Ссылка в рантайме + включение только для этого workspace (--no-link — пропустить).
#   4. .gitignore: ground/* (кроме policy.json) и .gigacode/.
#   5. preflight (advisory).
#
# Флаги:
#   --runtime gigacode|qwen   чей CLI ставит ссылку (по умолчанию gigacode, нет его — qwen;
#                             qwen — только дев-машина). Переменная FORGE_RUNTIME — то же.
#   --no-link                 не трогать рантайм (CI, тесты, ручная установка ссылки).
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # корень репо Forge

# shellcheck source=hooks/_pick_python.sh
. "$SRC/hooks/_pick_python.sh"
# shellcheck source=_deployed_manifest.sh
. "$SRC/_deployed_manifest.sh"

usage() { echo "Usage: bash install.sh <forge|forge-ui> /path/to/project [--runtime gigacode|qwen] [--no-link]" >&2; }

KIND=""
TARGET=""
RUNTIME="${FORGE_RUNTIME:-}"
NO_LINK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --runtime) RUNTIME="${2:-}"; shift 2 || { echo "install.sh: --runtime без значения" >&2; exit 2; } ;;
    --runtime=*) RUNTIME="${1#--runtime=}"; shift ;;
    --no-link) NO_LINK=1; shift ;;
    -h|--help) sed -n '2,36p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "install.sh: неизвестный флаг: $1" >&2; usage; exit 2 ;;
    *)
      if [ -z "$KIND" ]; then KIND="$1"
      elif [ -z "$TARGET" ]; then TARGET="$1"
      else echo "install.sh: лишний аргумент: $1" >&2; usage; exit 2
      fi
      shift ;;
  esac
done

case "$KIND" in
  forge) PROFILE=back; OTHER=forge-ui ;;
  forge-ui) PROFILE=ui; OTHER=forge ;;
  "") echo "install.sh: не указано, что ставить: forge (бэк-репо) или forge-ui (фронт-репо)." >&2; usage; exit 2 ;;
  *) echo "install.sh: неизвестная поставка '$KIND' — есть forge и forge-ui." >&2; usage; exit 2 ;;
esac
PROFILE_FILE="$SRC/profiles/$PROFILE.profile"

if [ -z "$TARGET" ]; then
  echo "install.sh: не указана папка проекта — без пути ничего не ставится (проектов может быть несколько)." >&2
  usage; exit 2
fi
if [ ! -d "$TARGET" ]; then
  echo "install.sh: папка проекта не существует или не каталог: $TARGET" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"
if [ "$TARGET" = "$SRC" ]; then
  echo "install.sh: папка проекта совпадает с исходным репо Forge — установка в себя запрещена." >&2
  exit 2
fi

GIG="$TARGET/.gigacode"
EXT_ROOT="$GIG/extensions"
EXT="$EXT_ROOT/$KIND"
if [ -d "$EXT_ROOT/$OTHER" ]; then
  echo "install.sh: в проекте уже стоит $OTHER ($EXT_ROOT/$OTHER) — одна поставка на репо." >&2
  echo "  Снять её: bash $SRC/uninstall.sh $OTHER $TARGET" >&2
  exit 2
fi

forge_pick_python || true
if [ "${#PY[@]}" -eq 0 ]; then
  echo "install.sh: не найден здоровый Python 3.9+ (python3/python/py) — хуки forge без него не работают." >&2
  exit 1
fi

profile_values() { forge_strip_comments "$PROFILE_FILE" | awk -v k="$1" '$1 == k { print $2 }'; }
profile_has() { forge_strip_comments "$PROFILE_FILE" | awk '{ print $1 }' | grep -Fxq "$1"; }

echo "== install $KIND (профиль $PROFILE) → $EXT =="

# 0. старая раскладка (project-модель ≤ 0.5)
if [ -f "$GIG/.forge-deployed" ] || [ -f "$GIG/hooks/gate-guard.py" ] || [ -f "$GIG/hooks/settings.hooks.json" ] \
   || { [ -f "$GIG/settings.json" ] && grep -q '\.gigacode/hooks/' "$GIG/settings.json" 2>/dev/null; }; then
  echo "  старая раскладка (.gigacode/{skills,hooks,commands} + settings.json) — снимаю перед установкой:"
  bash "$SRC/uninstall.sh" --legacy-only "$TARGET" | sed 's/^/    /'
fi

# 1. сборка во временный каталог рядом и подмена целиком
mkdir -p "$EXT_ROOT"
STAGE="$(mktemp -d "$EXT_ROOT/.$KIND.new.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
copy_tree() {  # $1=src-dir  $2=dst-dir (cp -a, не tar: см. память deploy-copy-tar-eperm)
  mkdir -p "$2"
  cp -a "$1/." "$2/"
  find "$2" -type d \( -name '__pycache__' -o -name '.pytest_cache' \) -exec rm -rf {} + 2>/dev/null || true
  find "$2" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete 2>/dev/null || true
}
copy_tree "$SRC/hooks" "$STAGE/hooks"
rm -f "$STAGE/hooks/hooks.json"
mkdir -p "$STAGE/skills"
DEPLOYED_SKILLS=""
if profile_has skills-all; then
  for d in "$SRC/skills"/*/; do
    x="$(basename "$d")"
    case "$x" in __pycache__|.*) continue ;; esac
    if profile_values exclude-skill | grep -Fxq "$x"; then continue; fi
    copy_tree "$SRC/skills/$x" "$STAGE/skills/$x"
    DEPLOYED_SKILLS="$DEPLOYED_SKILLS $x"
  done
else
  for x in $(profile_values skill) $(profile_values skill-scripts); do
    [ -d "$SRC/skills/$x" ] || { echo "install.sh: профиль $PROFILE: нет скилла skills/$x" >&2; exit 2; }
    copy_tree "$SRC/skills/$x" "$STAGE/skills/$x"
    DEPLOYED_SKILLS="$DEPLOYED_SKILLS $x"
  done
  # «skill-scripts X» — скрипты нужны хукам, а сам конвейер в этом репо скиллом не светится
  for x in $(profile_values skill-scripts); do rm -f "$STAGE/skills/$x/SKILL.md"; done
fi
for f in "$SRC/skills"/*; do [ -f "$f" ] && cp -p "$f" "$STAGE/skills/"; done
# локальный конфиг машины, откуда ставят, в проект не едет; конфиг проекта переживает обновление
rm -f "$STAGE/skills/minor-defect-fix/config.json"
if [ -d "$STAGE/skills/minor-defect-fix" ]; then
  if [ -f "$EXT/skills/minor-defect-fix/config.json" ]; then
    cp -p "$EXT/skills/minor-defect-fix/config.json" "$STAGE/skills/minor-defect-fix/config.json"
  elif [ -f "$GIG/minor-defect-fix-config.json.bak" ]; then   # снят вместе со старой раскладкой (шаг 0)
    cp -p "$GIG/minor-defect-fix-config.json.bak" "$STAGE/skills/minor-defect-fix/config.json"
    echo "  ✓ конфиг minor-defect-fix перенесён из старой установки"
  else
    printf '{\n  "projects": {}\n}\n' > "$STAGE/skills/minor-defect-fix/config.json"
  fi
fi
mkdir -p "$STAGE/commands"
DEPLOYED_COMMANDS=""
for cdir in $(profile_values commands); do
  for c in "$SRC/$cdir/"*.md; do
    [ -e "$c" ] || continue
    cp "$c" "$STAGE/commands/"
    DEPLOYED_COMMANDS="$DEPLOYED_COMMANDS $(basename "$c")"
  done
done
for d in FORGE.md SKILLS-REGISTRY.md; do [ -f "$SRC/$d" ] && cp "$SRC/$d" "$STAGE/$d"; done
cp "$SRC/deploy-local.sh" "$STAGE/deploy-local.sh"
cp "$PROFILE_FILE" "$STAGE/.forge-profile"
FORGE_REV="$(git -C "$SRC" describe --tags --match 'forge/v*' --always --dirty 2>/dev/null || echo unknown)"
{
  printf '# forge: реестр установки (kind=%s rev=%s)\n' "$KIND" "$FORGE_REV"
  for x in $DEPLOYED_SKILLS; do printf 'skills/%s\n' "$x"; done
  for c in $DEPLOYED_COMMANDS; do printf 'commands/%s\n' "$c"; done
} > "$STAGE/.forge-deployed"
chmod +x "$STAGE/hooks/"*.py "$STAGE/hooks/"*.sh "$STAGE/deploy-local.sh" 2>/dev/null || true

# 2. манифест + hooks.json — пути уже конечные (каталог $EXT), поэтому собираем ПОСЛЕ подмены
OLD=""
if [ -d "$EXT" ]; then
  OLD="$EXT_ROOT/.$KIND.old.$$"
  mv "$EXT" "$OLD"
fi
mv "$STAGE" "$EXT"
trap - EXIT
[ -n "$OLD" ] && rm -rf "$OLD"
if ! BUILT="$("${PY[@]}" -X utf8 "$EXT/hooks/forge_extension.py" build --ext "$EXT" --project "$TARGET" \
               --kind "$KIND" --version "$FORGE_REV")"; then
  echo "install.sh: манифест/hooks.json не собрались — поставка лежит в $EXT, но рантайму не подключена." >&2
  exit 1
fi
NAME="$("${PY[@]}" -X utf8 -c 'import json,sys; print(json.loads(sys.argv[1])["name"])' "$BUILT")"
echo "  ✓ поставка на месте: skills [$(echo $DEPLOYED_SKILLS)], commands [$(echo $DEPLOYED_COMMANDS)]"
echo "  ✓ манифест и hooks/hooks.json собраны: extension $NAME (rev $FORGE_REV)"

# 3. ссылка в рантайме + включение только для этого workspace (+ взаимное выключение с другими
#    forge-проектами машины) — hooks/forge_extension.py link
if [ "$NO_LINK" -eq 1 ]; then
  echo "  (--no-link: рантайм не тронут; подключить: bash $EXT/deploy-local.sh)"
else
  "${PY[@]}" -X utf8 "$EXT/hooks/forge_extension.py" link --ext "$EXT" --project "$TARGET" --runtime "$RUNTIME" \
    || echo "  (ссылка не поставлена целиком — см. строки выше; повторить: bash $EXT/deploy-local.sh)"
fi

# 4. .gitignore проекта (блок с маркерами — его снимает uninstall.sh)
GI_BEGIN="# >>> forge: рабочие данные пайплайна >>>"
GI_END="# <<< forge <<<"
if git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GI="$TARGET/.gitignore"
  if [ -f "$GI" ] && grep -qF "$GI_BEGIN" "$GI"; then
    echo "  ✓ .gitignore: блок forge уже на месте"
  else
    [ -f "$GI" ] && [ -n "$(tail -c 1 "$GI" 2>/dev/null)" ] && printf '\n' >> "$GI"
    {
      printf '%s\n' "$GI_BEGIN"
      printf '%s\n' "# Стейт прогонов, evidence, журналы, архив — производное, в git не нужно."
      printf '%s\n' "# policy.json оставлен под версионированием: это конфигурация проекта."
      printf '%s\n' "ground/*"
      printf '%s\n' "!ground/policy.json"
      printf '%s\n' "# Установленный forge (.gigacode/extensions/…): ставится install.sh, манифест и hooks.json"
      printf '%s\n' "# внутри — с абсолютными путями этой машины. Своё: git add -f .gigacode/skills/<своё>"
      printf '%s\n' ".gigacode/"
      printf '%s\n' "$GI_END"
    } >> "$GI"
    echo "  ✓ .gitignore: ground/ и .gigacode/ исключены из git (policy.json оставлен)"
  fi
else
  echo "  (не git-репозиторий — .gitignore не трогаю)"
fi

# 4b. устаревшие per-feature поля policy.json (идемпотентно, с бэкапом; сбой установку не валит)
if [ -f "$TARGET/ground/policy.json" ] && [ -f "$EXT/skills/config-helper/scripts/config.py" ]; then
  "${PY[@]}" -X utf8 "$EXT/skills/config-helper/scripts/config.py" --project "$TARGET" migrate-deprecated >/dev/null 2>&1 \
    && echo "  ✓ policy.json: устаревшие per-feature поля сняты (если были)" \
    || echo "  (migrate-deprecated не выполнился — проверь: config.py validate)"
fi

# 5. диагностика (advisory)
echo
echo "== preflight =="
if "${PY[@]}" -X utf8 "$EXT/hooks/preflight.py" --project "$TARGET"; then
  :
else
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "  (проект ещё не инициализирован — ground/policy.json заведёт init_command из вывода выше)"
  else
    echo "  (preflight сообщил о проблемах — см. вывод выше)"
  fi
fi
