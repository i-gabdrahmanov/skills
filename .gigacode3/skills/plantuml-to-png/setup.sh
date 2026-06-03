#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SKILL_DIR"

if ! command -v java >/dev/null 2>&1; then
    echo "ERROR: java not found in PATH. PlantUML requires Java to render diagrams." >&2
    echo "Install Java (e.g. https://adoptium.net/) and re-run setup." >&2
    exit 1
fi

# Find the project root by walking up from the skill directory looking for
# a marker (.git or an existing .venv). The skill reuses the project venv
# rather than creating its own.
find_project_root() {
    local dir="$SKILL_DIR"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.git" ] || [ -d "$dir/.venv" ]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}

PROJECT_ROOT="$(find_project_root)" || {
    echo "ERROR: cannot locate project root (no .git or .venv found above $SKILL_DIR)." >&2
    echo "Create a venv at <project-root>/.venv first: python3 -m venv .venv" >&2
    exit 1
}

VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: python3 not found in PATH" >&2
        exit 1
    fi
    echo "Creating project venv at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
fi

echo "Using venv: $VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r requirements.txt --quiet

"$VENV_DIR/bin/python" scripts/download_jar.py

echo "OK: skill installed at $SKILL_DIR"
