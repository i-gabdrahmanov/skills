#!/usr/bin/env bash
#
# test_pick_python.sh — тесты выбора интерпретатора (hooks/_pick_python.sh).
#
# Прецедент, ради которого выбор вообще стал проверяемым: оба установщика брали
# «первый python из PATH» и запекали его в settings.json как ${PYTHON} для КАЖДОГО
# хука. На машине аудита это давало два разных отказа:
#   • python3 = 3.9, а _project.py был без future-импорта → хук падал TypeError на
#     импорте. Крэш = exit 1, блокировка = exit 2; рантайм читал exit 1 как «хук не
#     возражает» → enforcement выключался молча, на каждом tool-call.
#   • homebrew python@3.12 идёт со сломанным pyexpat → всё, что парсит JUnit-XML
#     (check_tests_red, module_tests, check_coverage, gate_result), отваливается.
#     По номеру версии он «новее» и выбор по версии взял бы именно его.
#
# Поэтому проверяем не «нашёлся ли python», а «выбран ли РАБОТОСПОСОБНЫЙ».
# Запускается через skills/run_all_tests.py --skill root.
#
# Usage: bash test_pick_python.sh
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PICKER="$DIR/hooks/_pick_python.sh"

pass=0
fail=0

ok()   { echo "  ok   $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL $1: $2"; fail=$((fail + 1)); }

# Реальные интерпретаторы для проб. Здоровый ищем сами (не хардкодим путь машины).
HEALTHY=""
for c in python3 python python3.13 python3.12 python3.11 python3.10 python3.9; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys,xml.etree.ElementTree as ET; ET.XMLParser(); sys.exit(0 if sys.version_info[:2]>=(3,9) else 1)' >/dev/null 2>&1; then
    HEALTHY="$(command -v "$c")"
    break
  fi
done

if [ -z "$HEALTHY" ]; then
  echo "SKIP: на машине нет ни одного здорового python 3.9+ — тестировать выбор не на чем"
  exit 0
fi

# ── 1. Пробник различает здоровый / старый / битый ───────────────────────────
(
  # shellcheck source=hooks/_pick_python.sh
  . "$PICKER"
  _forge_probe_python "$HEALTHY"
) && ok "проба: здоровый интерпретатор → rc=0" \
  || bad "проба здорового" "ожидался rc=0"

TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

# ── 2. Битый stdlib отвергается (rc=4) ───────────────────────────────────────
# Заглушка, у которой ET.XMLParser() кидает — ровно случай homebrew-3.12.
cat > "$TMPD/brokenxml" <<EOF
#!/usr/bin/env bash
code="\${2:-}"
exec "$HEALTHY" -c "
import sys, xml.etree.ElementTree as ET
def _boom(*a, **k):
    raise ImportError('No module named expat; use SimpleXMLTreeBuilder instead')
ET.XMLParser = _boom
\$code
"
EOF
chmod +x "$TMPD/brokenxml"

(
  . "$PICKER"
  _forge_probe_python "$TMPD/brokenxml"
)
rc=$?
[ "$rc" -eq 4 ] && ok "проба: битый expat → rc=4" \
                || bad "проба битого expat" "ожидался rc=4, получен $rc"

# ── 3. Битый python3 в PATH — выбирается здоровый, а не он ───────────────────
SHIM="$TMPD/shim"
mkdir -p "$SHIM"
cp "$TMPD/brokenxml" "$SHIM/python3"
cp "$TMPD/brokenxml" "$SHIM/python"
ln -sf "$HEALTHY" "$SHIM/python3.9"

out="$(PATH="$SHIM:$PATH" bash -c ". '$PICKER'; forge_pick_python; echo \"PY=\${PY[*]:-}\"" 2>/dev/null)"
picked="$(echo "$out" | sed -n 's/^PY=//p')"
case "$picked" in
  python3|python)
    bad "битый python3 в PATH" "выбран битый '$picked' вместо здорового" ;;
  "")
    bad "битый python3 в PATH" "не выбрано ничего (ожидался здоровый кандидат)" ;;
  *)
    ok "битый python3 в PATH → выбран здоровый '$picked'" ;;
esac

# ── 4. Ни одного годного — фолбэк с громким предупреждением, а не тишина ─────
SHIM2="$TMPD/shim2"
mkdir -p "$SHIM2"
# Забиваем битой заглушкой ВСЕ имена-кандидаты: PATH урезать до одной заглушки нельзя —
# из него пропадёт сам bash. Шим идёт первым, поэтому перекрывает системные python*.
for name in python3 python python3.9 python3.10 python3.11 python3.12 python3.13; do
  cp "$TMPD/brokenxml" "$SHIM2/$name"
done
PATH2="$SHIM2:$PATH"
err="$(PATH="$PATH2" bash -c ". '$PICKER'; forge_pick_python; echo \"PY=\${PY[*]:-}\"" 2>&1 >/dev/null)"
outp="$(PATH="$PATH2" bash -c ". '$PICKER'; forge_pick_python; echo \"PY=\${PY[*]:-}\"" 2>/dev/null)"
picked2="$(echo "$outp" | sed -n 's/^PY=//p')"
if [ -n "$picked2" ] && echo "$err" | grep -q "ВНИМАНИЕ"; then
  ok "нет годного python → фолбэк + предупреждение в stderr"
else
  bad "нет годного python" "фолбэк='$picked2', stderr='$err'"
fi

# ── 5. Пол в пикере совпадает с MIN_PYTHON хуков ─────────────────────────────
floor_major="$(grep -o 'FORGE_PY_MIN_MAJOR=[0-9]*' "$PICKER" | cut -d= -f2)"
floor_minor="$(grep -o 'FORGE_PY_MIN_MINOR=[0-9]*' "$PICKER" | cut -d= -f2)"
if grep -q "MIN_PYTHON = ($floor_major, $floor_minor)" "$DIR/hooks/preflight.py"; then
  ok "пол пикера ($floor_major.$floor_minor) = preflight.MIN_PYTHON"
else
  bad "пол пикера" "FORGE_PY_MIN=$floor_major.$floor_minor разошёлся с preflight.MIN_PYTHON"
fi

echo
echo "=== pick_python: PASS=$pass FAIL=$fail ==="
[ "$fail" -eq 0 ]
