#!/usr/bin/env bash
#
# _deployed_manifest.sh — реестр того, что deploy.sh положил в таргет, и вывод надгробий.
# Общая библиотека deploy.sh и uninstall.sh (sourced, не запускается сам).
#
# Зачем. .gigacode/{skills,hooks,commands}/ — co-located: там И форж-обвязка, И самописное
# оператора (см. шапку uninstall.sh). Поэтому снести каталог целиком нельзя, и владение
# определялось предикатом «запись есть в исходном репо $SRC». Для скилла, УДАЛЁННОГО из репо,
# этот предикат отвечает ровно наоборот: в $SRC его нет → «операторское» → беречь. То есть
# единственный класс, ради которого существует `update.sh --force`, он снять не может.
# Реальная цена: ветка forgelite снята из репо в 60ae8cc, а в установках пережила и мягкий
# апгрейд, и --force. Команда /forge-lite осталась живой, её SKILL.md предписывал
# `set inputs.mode lite`, и резолвер актуальной версии отвечал `unknown inputs.mode='lite'`
# → rc 3 на резолве каждой фазы. Пользователь на свежей версии получал «баг resolve_phases».
#
# Механика. deploy пишет в таргет реестр положенного (top-level записи трёх каталогов).
# Следующий запуск считает НАДГРОБИЯ = (реестр прошлой установки ∪ tombstones.txt) ∖ текущий $SRC
# и снимает их. Ключевое свойство — вычитание: удаляется не «всё лишнее в каталоге», а ровно
# то, что форж сам положил в прошлый раз и больше не кладёт. Записи оператора не входят ни в
# реестр, ни в исходник — для алгоритма они невидимы, как и было.
#
# tombstones.txt — bootstrap для установок, заведённых ДО реестра (у них его нет и взяться
# ему неоткуда). Применяется всегда, идемпотентен.
#
# Формат реестра — плоский текст, не JSON: он читается в двух bash-скриптах, а python на
# машине может отсутствовать (deploy обязан отработать и без него — см. _pick_python.sh).
# Проверка вхождения — grep -Fxq, без парсера.

FORGE_MANIFEST_NAME=".forge-deployed"
FORGE_MANIFEST_DIRS="skills hooks commands"

forge_manifest_file() {  # $1=GIG
  printf '%s\n' "$1/$FORGE_MANIFEST_NAME"
}

# Строки файла без комментариев и пустых. `|| true` — grep без совпадений даёт rc 1,
# а вызывающие живут под `set -euo pipefail`.
forge_strip_comments() {  # $1=file
  sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$1" 2>/dev/null | grep -v '^$' || true
}

# Текущий исходник: "<dir>/<name>" по top-level трёх каталогов (dotfiles тоже — cp -a их несёт).
forge_src_entries() {  # $1=SRC
  local src="$1" d entry
  for d in $FORGE_MANIFEST_DIRS; do
    [ -d "$src/$d" ] || continue
    for entry in "$src/$d"/* "$src/$d"/.[!.]*; do
      [ -e "$entry" ] || continue
      printf '%s/%s\n' "$d" "$(basename "$entry")"
    done
  done
}

forge_manifest_entries() {  # $1=GIG
  local f
  f="$(forge_manifest_file "$1")"
  [ -f "$f" ] || return 0
  forge_strip_comments "$f"
}

forge_manifest_write() {  # $1=GIG  $2=SRC  $3=rev (опционально)
  local f
  f="$(forge_manifest_file "$1")"
  {
    printf '# forge deploy manifest v1 — что положил deploy.sh в этот таргет.\n'
    printf '# Не редактировать руками: по нему следующий апгрейд снимает то, что удалено из репо.\n'
    printf '# rev: %s  at: %s\n' "${3:-unknown}" "$(date +%Y-%m-%dT%H:%M:%S%z)"
    forge_src_entries "$2"
  } > "$f"
}

# Надгробия: было форж-своим, в текущем исходнике отсутствует, в таргете лежит.
# Печатает "<dir>/<name>" построчно; пусто — снимать нечего.
forge_tombstones() {  # $1=SRC  $2=GIG
  local src="$1" gig="$2" all e
  all="$(
    forge_manifest_entries "$gig"
    [ -f "$src/tombstones.txt" ] && forge_strip_comments "$src/tombstones.txt"
    true
  )"
  [ -n "$all" ] || return 0
  printf '%s\n' "$all" | sort -u | while IFS= read -r e; do
    [ -n "$e" ] || continue
    case "$e" in */*) ;; *) continue ;; esac                 # только "<dir>/<name>"
    case "$e" in */*/*) continue ;; esac                     # вложенное — не наш скоуп
    case "${e%%/*}" in skills|hooks|commands) ;; *) continue ;; esac
    [ -e "$src/$e" ] && continue                             # живо в исходнике → не трогать
    [ -e "$gig/$e" ] || continue                             # в таргете нет → нечего снимать
    printf '%s\n' "$e"
  done
}
