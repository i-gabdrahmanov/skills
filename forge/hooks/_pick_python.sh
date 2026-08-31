#!/usr/bin/env bash
#
# _pick_python.sh — выбор интерпретатора для хуков. Sourced из deploy.sh и deploy-local.sh.
#
# Зачем отдельный файл: оба установщика раньше несли ОДИН И ТОТ ЖЕ блок
#
#     if command -v python3 >/dev/null 2>&1; then PY=(python3)
#     elif command -v python  >/dev/null 2>&1; then PY=(python)
#     elif command -v py      >/dev/null 2>&1; then PY=(py -3)
#     fi
#
# — «первый python из PATH, без единой проверки». Именно он и запекается в
# settings.json как ${PYTHON} для КАЖДОГО хука. Два наблюдавшихся следствия:
#
#   1. Слишком старый интерпретатор. На macOS и на многих корпоративных Linux
#      `python3` — это 3.9. Пока в _project.py не было future-импорта, любой хук
#      падал TypeError на импорте. Крэш — exit 1, а блокировка — exit 2: рантайм
#      читает exit 1 как «хук не возражает» и выполняет вызов. Харнес молча
#      разворачивался без гейтов, на каждом tool-call.
#
#   2. Формально свежий, но битый интерпретатор. На машине аудита homebrew
#      python@3.12 идёт со сломанным pyexpat (Symbol not found:
#      _XML_SetAllocTrackerActivationThreshold) — `import xml.etree` есть, а
#      XMLParser() кидает ImportError. Всё, что читает JUnit-XML (check_tests_red,
#      module_tests, check_coverage, gate_result), на нём отваливается. Версия при
#      этом «новее», так что выбор по номеру версии выбрал бы именно его.
#
# Поэтому кандидат не просто ищется в PATH, а ПРОБУЕТСЯ: версия >= пола И живой
# stdlib (expat). Здоровый кандидат побеждает более новый, но битый.
#
# Экспортирует массив PY=(...) — argv интерпретатора (может быть многословным: `py -3`).
# Возврат: 0 — выбран здоровый; 1 — выбран с оговорками (предупреждение уже в stderr);
#          2 — годного нет вовсе (PY пуст).

# Пол — тот же, что MIN_PYTHON в hooks/preflight.py и doctor.py
# (равенство пинит hooks/test_python_floor.py).
FORGE_PY_MIN_MAJOR=3
FORGE_PY_MIN_MINOR=9

# Проба кандидата. Коды: 0 здоров; 3 старый; 4 битый stdlib; иначе — не запускается.
_forge_probe_python() {
  "$@" -c "
import sys
if sys.version_info[:2] < ($FORGE_PY_MIN_MAJOR, $FORGE_PY_MIN_MINOR):
    sys.exit(3)
try:
    import xml.etree.ElementTree as ET
    ET.XMLParser()          # expat: без него молча ломаются все JUnit-гейты
except Exception:
    sys.exit(4)
sys.exit(0)
" >/dev/null 2>&1
}

# Порядок кандидатов. Сначала то, что даёт PATH (предсказуемо для оператора и совпадает
# с тем, что он сам наберёт в терминале), потом явные версии — как запасной путь, когда
# дефолт из PATH оказался старым или битым.
_forge_python_candidates() {
  cat <<'CANDS'
python3
python
py -3
python3.13
python3.12
python3.11
python3.10
python3.9
CANDS
}

# Заполняет PY. Печатает диагностику в stderr, ничего в stdout (вызывающие парсят свой вывод).
forge_pick_python() {
  PY=()
  local fallback=() fallback_why=""
  local line cand_bin rc

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    # shellcheck disable=SC2206
    local cand=($line)
    cand_bin="${cand[0]}"
    command -v "$cand_bin" >/dev/null 2>&1 || continue

    _forge_probe_python "${cand[@]}"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      PY=("${cand[@]}")
      # Здоровый кандидат найден не с первой попытки — оператору важно знать, что
      # выбран НЕ тот python, что отдаёт PATH: хуки поедут именно на этом.
      if [ -n "$fallback_why" ]; then
        echo "  ⚠ python из PATH не годится ($fallback_why); хуки поедут на: ${PY[*]}" >&2
      fi
      return 0
    fi

    # Первый негодный кандидат запоминаем как запасной — лучше кривой харнес с явным
    # предупреждением, чем отказ от деплоя, если ничего лучше на машине нет.
    if [ "${#fallback[@]}" -eq 0 ] && { [ "$rc" -eq 3 ] || [ "$rc" -eq 4 ]; }; then
      fallback=("${cand[@]}")
      case "$rc" in
        3) fallback_why="$cand_bin старее ${FORGE_PY_MIN_MAJOR}.${FORGE_PY_MIN_MINOR}" ;;
        4) fallback_why="$cand_bin со сломанным stdlib (expat)" ;;
      esac
    fi
  done < <(_forge_python_candidates)

  if [ "${#fallback[@]}" -gt 0 ]; then
    PY=("${fallback[@]}")
    echo "  ⚠ ВНИМАНИЕ: годного python на машине нет — выбран ${PY[*]} ($fallback_why)." >&2
    echo "    Харнес будет работать частично. Поставь python ${FORGE_PY_MIN_MAJOR}.${FORGE_PY_MIN_MINOR}+" >&2
    echo "    с рабочим expat и перезапусти установщик." >&2
    return 1
  fi

  return 2
}
