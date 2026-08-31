#!/usr/bin/env bash
#
# cleanup-legacy.sh — снять остатки СТАРОЙ раскладки forge (эпохи deploy.sh), которые
#                     перекрывают установленный extension.
#
# Это НЕ деинсталлятор extension'а: снять сам extension — одна команда рантайма
# (`gigacode extensions uninstall forge`, INSTALL.md §5). И это НЕ `forge/uninstall.sh`: тот —
# зеркало `deploy.sh`, он снимает раскладку внутри одного проекта и о extension'е не знает.
#
# Зачем отдельный скрипт. Старая раскладка не удаляется сама и МОЛЧА ПОБЕЖДАЕТ extension:
#   • скилл резолвится project > user > extension — каталог `~/.gigacode/skills/feature-pipeline`
#     от прошлого деплоя подменяет сегодняшний бриф фазы версией многомесячной давности;
#   • одноимённую команду extension'а рантайм переименовывает — `/forge` уезжает к старой копии;
#   • хуки из `settings.json` и хуки extension'а не заменяют друг друга, а СКЛАДЫВАЮТСЯ:
#     цепочка задваивается, а снятые из форжа хуки (log-agent, evidence-enforcer) продолжают
#     висеть и звать код, которого в комплекте больше нет.
# Снаружи это выглядит необъяснимо: preflight видит хуки зелёными, а прогон идёт по мёртвым
# путям старого SKILL.md. Ровно это и ловит `preflight.py` («старые копии перекрывают extension»).
#
# Безопасность — иначе такой чисткой легко снести чужое:
#   • ПО УМОЛЧАНИЮ ТОЛЬКО ПЛАН. Ничего не меняется, пока не передан --apply.
#   • Ничего не удаляется безвозвратно: форж-своё ПЕРЕНОСИТСЯ в forge-legacy-backup-<TS>/.
#   • «Форж-своё» = состав ЭТОГО extension'а (skills/, commands/, hooks/) ПЛЮС список снятых
#     артефактов (log-agent.py, evidence-enforcer.py, forge.toml, …): в комплекте их больше нет,
#     и без явного списка они остались бы в чужих каталогах навсегда.
#   • Всё остальное — операторское, не трогаем: в тех же каталогах лежат самописные скиллы
#     (pptx, pdf, skill-creator…). Был инцидент, когда чистка снесла их вместе с форжевыми.
#   • Родительский каталог убираем только `rmdir` — опустел, значит уйдёт; осталось чужое, выживет.
#   • ground/ (BRD/SDD/манифесты/evidence/approvals) — только по --purge-state, и тоже в бэкап.
#
# Usage (путь к проекту — позиционно ИЛИ через --project; и то и другое повторяемо):
#   bash cleanup-legacy.sh                                   # план по $HOME (ничего не меняет)
#   bash cleanup-legacy.sh --apply                           # выполнить
#   bash cleanup-legacy.sh /path/repo                        # + legacy-раскладка проекта (план)
#   bash cleanup-legacy.sh /path/repo --apply --purge-state  # + ground/ и refs/forge/*
#   bash cleanup-legacy.sh --home /tmp/fakehome --apply      # другая база (тест/другой пользователь)
#   bash cleanup-legacy.sh --apply --backup-dir /tmp/forge-bak  # бэкап в другое место — для
#       корп-контура, где писать в $HOME или в каталог проекта не дают
#   bash cleanup-legacy.sh --ext ~/.gigacode/extensions/forge --apply  # скрипт лежит отдельно
#       (скопирован в ~/bin и т.п.) — эталон состава надо показать явно
#
set -euo pipefail

EXT_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # каталог самого скрипта
EXT=""                                                      # корень extension'а (резолвится ниже)

# ── аргументы ────────────────────────────────────────────────────────────────
APPLY=0
PURGE_STATE=0
BACKUP_DIR=""      # куда складывать снятое; пусто → рядом с самой базой
HOME_BASE="${HOME:-}"
PROJECTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)       APPLY=1; shift ;;
    --dry-run)     APPLY=0; shift ;;                 # план и так по умолчанию — принимаем молча
    --purge-state) PURGE_STATE=1; shift ;;
    --project)     [ $# -ge 2 ] || { echo "cleanup-legacy.sh: --project без пути" >&2; exit 2; }
                   PROJECTS+=("$2"); shift 2 ;;
    --home)        [ $# -ge 2 ] || { echo "cleanup-legacy.sh: --home без пути" >&2; exit 2; }
                   HOME_BASE="$2"; shift 2 ;;
    --backup-dir)  [ $# -ge 2 ] || { echo "cleanup-legacy.sh: --backup-dir без пути" >&2; exit 2; }
                   BACKUP_DIR="$2"; shift 2 ;;
    --ext)         [ $# -ge 2 ] || { echo "cleanup-legacy.sh: --ext без пути" >&2; exit 2; }
                   EXT="$2"; shift 2 ;;
    -h|--help)     sed -n '2,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)            echo "cleanup-legacy.sh: неизвестный аргумент: $1" >&2
                   echo "  Usage: bash cleanup-legacy.sh [<проект>…] [--apply] [--purge-state] [--backup-dir <путь>]" >&2
                   echo "  Путь к проекту можно передать позиционно или через --project (флаг повторяем)." >&2
                   exit 2 ;;
    *)             PROJECTS+=("$1"); shift ;;   # позиционный путь = проект (как target у forge/uninstall.sh)
  esac
done

[ -n "$HOME_BASE" ] || { echo "cleanup-legacy.sh: не определён \$HOME (укажи --home)" >&2; exit 2; }
[ -d "$HOME_BASE" ] || { echo "cleanup-legacy.sh: нет каталога: $HOME_BASE" >&2; exit 2; }
HOME_BASE="$(cd "$HOME_BASE" && pwd)"

PY=""
HOME_BASE_RO=0
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
fi

# ── где лежит extension (эталон «что форжевое») ───────────────────────────────
# Скрипт можно скопировать куда угодно (~/bin, /usr/local/bin) — но списки скиллов/команд/хуков
# он берёт из КАТАЛОГА EXTENSION'А. Без него чистка сняла бы только «снятые» артефакты и
# оставила раскладку полуразобранной (хуже, чем не запускать вовсе), поэтому корень
# либо резолвится, либо скрипт останавливается.
is_ext_root() {  # $1=каталог → 0, если это похоже на forgeExt
  [ -d "$1/hooks" ] || return 1
  ls "$1"/hooks/*.py >/dev/null 2>&1 || return 1
  ls "$1"/skills/*/SKILL.md >/dev/null 2>&1 || return 1
  return 0
}

link_source_of() {  # $1=каталог установленного extension'а → путь-источник из install-маркера
  local marker
  for marker in "$1"/.*-extension-install.json; do
    [ -f "$marker" ] || continue
    sed -n 's/.*"source"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$marker" | head -1
    return 0
  done
}

resolve_ext() {
  local cand src b
  if [ -n "$EXT" ]; then                       # явный --ext
    EXT="$(cd "$EXT" 2>/dev/null && pwd)" || { echo "cleanup-legacy.sh: нет каталога --ext" >&2; exit 2; }
    is_ext_root "$EXT" && return 0
    echo "cleanup-legacy.sh: --ext $EXT не похож на forge-extension (нет skills/*/SKILL.md и hooks/*.py)" >&2
    exit 2
  fi
  is_ext_root "$EXT_SELF" && { EXT="$EXT_SELF"; return 0; }
  for b in $BASES; do                          # установленный extension рядом с рантаймом
    cand="$HOME_BASE/$b/extensions/forge"
    [ -d "$cand" ] || continue
    is_ext_root "$cand" && { EXT="$cand"; return 0; }
    src="$(link_source_of "$cand")"            # link → источник указан в install-маркере
    if [ -n "${src:-}" ] && is_ext_root "$src"; then EXT="$(cd "$src" && pwd)"; return 0; fi
  done
  echo "cleanup-legacy.sh: не нашёл каталог extension'а — а без него не понять, что форжевое," >&2
  echo "  и чистка сняла бы лишь часть (это хуже, чем ничего). Скрипт лежит в $EXT_SELF." >&2
  echo "  Укажи корень явно: --ext <путь>/forgeExt (или ~/.gigacode/extensions/forge)," >&2
  echo "  либо запускай скрипт из самого каталога extension'а." >&2
  exit 2
}
TS="$(date +%Y%m%d-%H%M%S)"
# Каталоги-провайдеры скиллов/команд — те же, что проверяет preflight._SKILL_PROVIDER_DIRS.
# Боевой рантайм — GigaCode (.gigacode); .qwen — дев-машина, .agents — общий каталог агентов.
BASES=".gigacode .qwen .agents"

resolve_ext

# ── что считается форж-своим ─────────────────────────────────────────────────
# Живое — по составу extension'а (не хардкод: добавили скилл → чистка узнает о нём сама).
FORGE_SKILLS=""
for d in "$EXT"/skills/*/; do
  [ -f "$d/SKILL.md" ] && FORGE_SKILLS="$FORGE_SKILLS $(basename "$d")"
done
FORGE_COMMANDS=""
for f in "$EXT"/commands/*.md; do
  [ -f "$f" ] && FORGE_COMMANDS="$FORGE_COMMANDS $(basename "$f" .md)"
done
FORGE_HOOKS=""
for f in "$EXT"/hooks/*.py "$EXT"/hooks/*.json; do
  [ -f "$f" ] && FORGE_HOOKS="$FORGE_HOOKS $(basename "$f")"
done

# Снятое — только явным списком: этих файлов в комплекте уже нет, значит по составу extension'а
# они не опознаются и висели бы вечно (реальный случай: log-agent.py в settings.json звал
# несуществующий скрипт, а evidence-enforcer.py форсил доставку, которой в пайплайне больше нет).
RETIRED_HOOKS="agentops.py budget-meter.py cost-breaker.py evidence-enforcer.py gate-resolver.py
log-agent.py subagent-enforcer.py watch-agents.sh watch-agents.py run-hook-tests.sh doctor.py
validate_skills.py resolve_hook_paths.py settings.hooks.json DEPLOY.md evals __pycache__"
RETIRED_SKILLS="__pycache__"
# Команды: старый формат TOML депрекейтнут (рантайм показывает окно миграции на каждом старте).
LEGACY_CMD_EXT="toml"
# Одиночные файлы, которые deploy.sh клал в <project>/.gigacode/.
PROJECT_EXTRAS="deploy-local.sh FORGE.md SKILLS-REGISTRY.md"

owned() {  # $1=список  $2=имя → 0, если имя в списке
  case " $(echo $1 | tr '\n' ' ') " in *" $2 "*) return 0 ;; *) return 1 ;; esac
}

# ── план/выполнение ──────────────────────────────────────────────────────────
MOVED=0; KEPT=0; STRIPPED=0; FAILED=0
BACKUP_ROOT=""   # создаётся лениво: в режиме плана каталогов не появляется

backup_root_for() {  # $1=база (HOME или проект) → корень бэкапа для неё
  if [ -n "$BACKUP_DIR" ]; then
    # Явное место (корп-контур: в $HOME/проект писать нельзя). Базы не смешиваем —
    # раскладываем по подпапкам, иначе одноимённые skills/ из разных баз наложатся.
    local label
    if [ "$1" = "$HOME_BASE" ]; then label="home"; else label="$(basename "$1")"; fi
    echo "$BACKUP_DIR/forge-legacy-backup-$TS/$label"
  else
    echo "$1/forge-legacy-backup-$TS"
  fi
}

# macOS без Full Disk Access отдаёт EPERM на ~/Documents|Desktop|Downloads и внешних томах —
# ошибка приходит от ОС, а не от прав каталога, поэтому подсказываем адресно.
tcc_hint() {  # $1=путь
  case "$1" in
    "$HOME_BASE"/Documents/*|"$HOME_BASE"/Desktop/*|"$HOME_BASE"/Downloads/*|/Volumes/*)
      echo "      подсказка: macOS ограничивает доступ к этому каталогу — выдай терминалу"
      echo "      Full Disk Access (System Settings → Privacy & Security) и повтори" ;;
  esac
}

move_item() {  # $1=что  $2=база-бэкапа  $3=относительная метка  $4=человекочитаемое имя
  local src="$1" base="$2" rel="$3" label="$4" dest err
  dest="$(backup_root_for "$base")/$rel"
  if [ "$APPLY" -eq 0 ]; then
    echo "    [план] перенести: $label"
    MOVED=$((MOVED + 1))
    return 0
  fi
  # Ошибку одного элемента не превращаем в обрыв всей чистки (set -e убил бы прогон на середине,
  # оставив половину раскладки снятой): сообщаем причину, считаем и идём дальше.
  # mv может не пройти политикой корп-контура или через границу ФС — тогда copy+remove.
  if ! err="$( { mkdir -p "$(dirname "$dest")" && rm -rf "$dest" \
                 && { mv "$src" "$dest" 2>/dev/null || { cp -a "$src" "$dest" && rm -rf "$src"; } }
               } 2>&1 )"; then
    echo "    ✗ НЕ перенесено: $label"
    echo "      причина: ${err:-неизвестна}"
    tcc_hint "$src"
    FAILED=$((FAILED + 1))
    return 0
  fi
  echo "    ✓ перенесено: $label"
  MOVED=$((MOVED + 1))
}

# Каталог skills/commands/hooks: снять форж-своё, чужое оставить, пустой каталог убрать rmdir'ом.
clean_dir() {  # $1=каталог  $2=вид(skills|commands|hooks)  $3=база-бэкапа  $4=метка-для-бэкапа
  local dir="$1" kind="$2" base="$3" rel="$4" entry name stem removed=0 kept=0
  [ -d "$dir" ] || return 0
  for entry in "$dir"/* "$dir"/.[!.]*; do
    [ -e "$entry" ] || continue
    name="$(basename "$entry")"
    case "$kind" in
      skills)   owned "$FORGE_SKILLS $RETIRED_SKILLS" "$name" && hit=1 || hit=0 ;;
      hooks)    owned "$FORGE_HOOKS $RETIRED_HOOKS" "$name" && hit=1 || hit=0 ;;
      commands) stem="${name%.*}"
                if [ "${name##*.}" = "md" ] || [ "${name##*.}" = "$LEGACY_CMD_EXT" ]; then
                  owned "$FORGE_COMMANDS" "$stem" && hit=1 || hit=0
                else hit=0; fi ;;
      *)        hit=0 ;;
    esac
    if [ "$hit" -eq 1 ]; then
      move_item "$entry" "$base" "$rel/$name" "$rel/$name"
      removed=$((removed + 1))
    else
      kept=$((kept + 1)); KEPT=$((KEPT + 1))
    fi
  done
  if [ "$removed" -gt 0 ] && [ "$kept" -gt 0 ]; then
    echo "    ($rel: операторского оставлено — $kept, каталог сохранён)"
  elif [ "$removed" -gt 0 ] && [ "$APPLY" -eq 1 ]; then
    rmdir "$dir" 2>/dev/null && echo "    ✓ пустой каталог убран: $rel" || true
  fi
}

# settings.json: снять ТОЛЬКО форж-записи блока hooks, чужие и прочие секции сохранить.
clean_settings() {  # $1=путь к settings.json
  local settings="$1"
  [ -f "$settings" ] || return 0
  if [ -z "$PY" ]; then
    echo "    ! нет python3 — блок hooks в $settings не проверен (сделай вручную, INSTALL.md §1.1)"
    return 0
  fi
  local out
  out="$("$PY" -X utf8 - "$settings" "$TS" "$APPLY" $FORGE_HOOKS $RETIRED_HOOKS <<'PY'
import json, os, re, sys

path, ts, apply_flag = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
names = {n for n in sys.argv[4:] if n.endswith((".py", ".sh"))}
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:                                  # noqa: BLE001
    print(f"NOTE не JSON ({e}) — пропускаю")
    raise SystemExit(0)

hooks = data.get("hooks")
if not isinstance(hooks, dict) or not hooks:
    print("NOTE блока hooks нет")
    raise SystemExit(0)

removed, kept, new_hooks = [], [], {}
for event, groups in hooks.items():
    new_groups = []
    for group in (groups or []):
        entries = group.get("hooks") or []
        keep = []
        for h in entries:
            cmd = str(h.get("command", ""))
            hit = [x for x in re.findall(r"([\w.\-]+\.(?:py|sh))", cmd) if x in names]
            label = h.get("name") or (hit[0] if hit else cmd[:40])
            if hit:
                removed.append(f"{event}:{label}")
            else:
                keep.append(h)
                kept.append(f"{event}:{label}")
        if keep:
            g = dict(group)
            g["hooks"] = keep
            new_groups.append(g)
    if new_groups:
        new_hooks[event] = new_groups

if not removed:
    print("NOTE forge-хуков в блоке hooks нет")
    raise SystemExit(0)

print(f"HIT снять записей: {len(removed)} ({', '.join(sorted(set(removed)))})")
if kept:
    print(f"KEEP сохранить чужих: {len(kept)} ({', '.join(sorted(set(kept)))})")
if not apply_flag:
    raise SystemExit(0)

# Бэкап: вечный .bak — первозданный оригинал, он не затирается; текущая версия уходит в .<TS>.bak
pristine = path + ".bak"
backup = pristine if not os.path.exists(pristine) else f"{path}.{ts}.bak"
with open(path, encoding="utf-8") as f:
    raw = f.read()
with open(backup, "w", encoding="utf-8") as f:
    f.write(raw)

if new_hooks:
    data["hooks"] = new_hooks
else:
    data.pop("hooks", None)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
print(f"DONE бэкап → {backup}")
PY
)" || { echo "    ! не удалось разобрать $settings"; return 0; }
  local mark; mark="$([ "$APPLY" -eq 0 ] && echo '[план] ' || echo '✓ ')"
  printf '%s\n' "$out" | sed -e "s/^HIT /$mark/" -e 's/^KEEP /… /' -e 's/^DONE /✓ /' \
                             -e 's/^NOTE //' -e '/^$/d' -e 's/^/    /'
  case "$out" in *"HIT "*) STRIPPED=$((STRIPPED + 1)) ;; esac
}

# Предполётно: в apply-режиме база бэкапа обязана быть записываемой — иначе первый же mv
# упрётся в EPERM уже после того, как часть работы сделана.
check_writable() {  # $1=база (проверяем каталог, куда ляжет бэкап этой базы)
  [ "$APPLY" -eq 1 ] || return 0
  local target="${BACKUP_DIR:-$1}"
  [ -d "$target" ] || mkdir -p "$target" 2>/dev/null || true
  [ -w "$target" ] && return 0
  echo "!! нет прав на запись в $target — бэкап туда не положить, пропускаю эту базу" >&2
  echo "   в корп-контуре укажи доступное место: --backup-dir /tmp/forge-bak" >&2
  tcc_hint "$target/x"
  return 1
}

# ── заголовок ────────────────────────────────────────────────────────────────
if [ "$APPLY" -eq 0 ]; then
  echo "== ПЛАН: чистка старой раскладки forge (ничего не меняется) =="
  echo "   выполнить: bash cleanup-legacy.sh --apply${PROJECTS[*]+ --project ...}"
else
  echo "== Чистка старой раскладки forge =="
fi
echo "   эталон состава: $EXT"
echo "   бэкап:          <база>/forge-legacy-backup-$TS/"
echo

# ── 1. user-уровень ($HOME/.gigacode|.qwen|.agents) ──────────────────────────
echo "-- user-уровень: $HOME_BASE"
check_writable "$HOME_BASE" || HOME_BASE_RO=1
if [ "$HOME_BASE_RO" -eq 1 ]; then
  echo "   пропущен (бэкап положить некуда)"
else
  for b in $BASES; do
    base_dir="$HOME_BASE/$b"
    [ -d "$base_dir" ] || continue
    echo "  $b/"
    clean_dir "$base_dir/skills"   skills   "$HOME_BASE" "$b/skills"
    clean_dir "$base_dir/commands" commands "$HOME_BASE" "$b/commands"
    clean_dir "$base_dir/hooks"    hooks    "$HOME_BASE" "$b/hooks"
    clean_settings "$base_dir/settings.json"
  done
fi

# ── 2. проекты (--project, повторяемо) ───────────────────────────────────────
for proj in ${PROJECTS[@]+"${PROJECTS[@]}"}; do
  if [ ! -d "$proj" ]; then
    echo "-- проект пропущен (нет каталога): $proj"
    continue
  fi
  proj="$(cd "$proj" && pwd)"
  echo
  echo "-- проект: $proj"
  check_writable "$proj" || { echo "   пропущен"; continue; }
  for b in $BASES; do
    base_dir="$proj/$b"
    [ -d "$base_dir" ] || continue
    echo "  $b/"
    clean_dir "$base_dir/skills"   skills   "$proj" "$b/skills"
    clean_dir "$base_dir/commands" commands "$proj" "$b/commands"
    clean_dir "$base_dir/hooks"    hooks    "$proj" "$b/hooks"
    clean_settings "$base_dir/settings.json"
    for extra in $PROJECT_EXTRAS; do
      [ -e "$base_dir/$extra" ] && move_item "$base_dir/$extra" "$proj" "$b/$extra" "$b/$extra"
    done
    [ "$APPLY" -eq 1 ] && rmdir "$base_dir" 2>/dev/null && echo "    ✓ пустой каталог убран: $b/" || true
  done

  if [ "$PURGE_STATE" -eq 1 ]; then
    [ -d "$proj/ground" ] && move_item "$proj/ground" "$proj" "ground" \
      "ground/ (BRD/SDD/манифесты/evidence/approvals)"
    if command -v git >/dev/null 2>&1 && [ -d "$proj/.git" ]; then
      refs="$(git -C "$proj" for-each-ref --format='%(refname)' refs/forge/ 2>/dev/null || true)"
      if [ -n "$refs" ]; then
        count="$(printf '%s\n' "$refs" | wc -l | tr -d ' ')"
        if [ "$APPLY" -eq 0 ]; then
          echo "    [план] удалить git-refs чекпойнтов: $count (refs/forge/*)"
        else
          printf '%s\n' "$refs" | while IFS= read -r ref; do
            [ -n "$ref" ] && git -C "$proj" update-ref -d "$ref"
          done
          echo "    ✓ удалены git-refs чекпойнтов: $count (refs/forge/*)"
        fi
      fi
    fi
  elif [ -d "$proj/ground" ]; then
    echo "    ℹ ground/ оставлен (рабочие данные). Снести вместе с чекпойнтами: --purge-state"
  fi
done

# ── итог ─────────────────────────────────────────────────────────────────────
echo
if [ "$MOVED" -eq 0 ] && [ "$STRIPPED" -eq 0 ]; then
  echo "== Чисто: остатков старой раскладки не найдено =="
  exit 0
fi
if [ "$APPLY" -eq 0 ]; then
  echo "== ПЛАН: к переносу $MOVED, settings.json к правке $STRIPPED (операторского не тронуто: $KEPT) =="
  echo "   Ничего не изменено. Выполнить: те же аргументы + --apply"
  exit 0
fi
if [ "$FAILED" -gt 0 ]; then
  echo "== Частично: перенесено $MOVED, НЕ перенесено $FAILED, settings.json поправлено $STRIPPED =="
  echo "   Причины — выше по каждому элементу. Оставшееся продолжает перекрывать extension:"
  echo "   почини доступ и повтори (скрипт идемпотентен, уже перенесённое не тронет)."
  exit 1
fi
echo "== Готово: перенесено $MOVED, settings.json поправлено $STRIPPED (операторского не тронуто: $KEPT) =="
echo "   Бэкап: $(backup_root_for "$HOME_BASE") (и <проект>/forge-legacy-backup-$TS/ для проектов)"
echo "   Дальше: перезапусти сессию рантайма (список скиллов кэшируется на старте) и проверь:"
echo "     python3 $EXT/hooks/preflight.py --project <репо>   # ошибок «перекрывают extension» быть не должно"
