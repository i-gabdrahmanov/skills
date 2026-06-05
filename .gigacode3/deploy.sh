#!/usr/bin/env bash
# deploy.sh — развернуть Forge (hooks + skills, co-located) в дом рантайма и влить блок hooks.
# Чинит корневые причины провального прогона: хуки не зарегистрированы (0 entries) и skills не рядом.
#
# Usage:
#   bash deploy.sh [TARGET_HOME]      # по умолчанию ~/.gigacode (прод). Для теста: bash deploy.sh ~/.qwen
#
# Идемпотентно. После копирования мержит блок hooks из hooks/settings.hooks.json в
# <home>/settings.json (ретаргетит пути на <home>), затем прогоняет doctor.py.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${1:-$HOME/.gigacode}"
HOME_DIR="${HOME_DIR/#\~/$HOME}"
mkdir -p "$HOME_DIR/hooks" "$HOME_DIR/skills"

echo "== deploy Forge → $HOME_DIR =="

# 1. co-location: hooks И skills в ОДИН дом (иначе гейты не найдут ../skills)
cp -a "$SRC/hooks/." "$HOME_DIR/hooks/"
cp -a "$SRC/skills/." "$HOME_DIR/skills/"
for d in FORGE.md GUIDE.md AGENT-RUNBOOK.md smoke-cli.sh; do
  [ -f "$SRC/$d" ] && cp "$SRC/$d" "$HOME_DIR/$d"   # доки и runtime-смоук рядом с обвязкой
done
chmod +x "$HOME_DIR/hooks/"*.py "$HOME_DIR/hooks/"*.sh "$HOME_DIR/hooks/evals/"*.py 2>/dev/null || true
echo "  ✓ скопированы hooks/ и skills/ (co-located)"

# 2. влить блок hooks в settings.json (мерж, ретаргет путей на этот дом)
python3 - "$SRC" "$HOME_DIR" <<'PY'
import json, os, sys, pathlib, shutil
src, home = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
ref = json.load(open(src / "hooks" / "settings.hooks.json"))["hooks"]
home_token = f"$HOME/{os.path.relpath(home, os.path.expanduser('~'))}/hooks/" if str(home).startswith(os.path.expanduser('~')) else f"{home}/hooks/"
def rt(o):
    if isinstance(o, dict): return {k: rt(v) for k, v in o.items()}
    if isinstance(o, list): return [rt(x) for x in o]
    if isinstance(o, str): return o.replace("$HOME/.gigacode/hooks/", home_token)
    return o
ref = rt(ref)
sp = home / "settings.json"
settings = {}
if sp.exists():
    shutil.copy(sp, str(sp) + ".bak")
    try: settings = json.load(open(sp))
    except Exception: settings = {}
settings["hooks"] = ref
settings.pop("disableAllHooks", None)
json.dump(settings, open(sp, "w"), ensure_ascii=False, indent=2)
json.load(open(sp))  # validate
print(f"  ✓ блок hooks влит в {sp} ({len(ref)} событий, пути → {home_token})")
PY

# 3. диагностика
echo
bash -c "python3 '$HOME_DIR/hooks/doctor.py' --home '$HOME_DIR'"
