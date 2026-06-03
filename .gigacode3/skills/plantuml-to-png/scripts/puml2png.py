#!/usr/bin/env python3
"""Render a PlantUML file to PNG locally via plantuml.jar + java.

Usage: puml2png.py <input.puml> [-o <output.png>]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import get_jar_path  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent

GRAPHVIZ_HINT = (
    "This diagram type requires Graphviz (the `dot` binary), which is not "
    "bundled with this skill for portability.\n"
    "Install Graphviz system-wide:\n"
    "  macOS:        brew install graphviz\n"
    "  Debian/Ubuntu: sudo apt-get install graphviz\n"
    "  Fedora:        sudo dnf install graphviz\n"
    "  Windows:       choco install graphviz   (or download from graphviz.org)\n"
    "Diagrams that work without Graphviz: sequence, class, usecase, json, "
    "yaml, mindmap, gantt, wbs, salt."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="PlantUML → PNG (local)")
    parser.add_argument("input", help="path to .puml file")
    parser.add_argument(
        "-o", "--output", help="output PNG path (defaults to <input>.png next to input)"
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    jar_path = get_jar_path()
    if not jar_path.is_file():
        print(
            f"ERROR: plantuml.jar not found at {jar_path}\n"
            f"Run setup.sh first:\n"
            f"  bash {SKILL_DIR}/setup.sh",
            file=sys.stderr,
        )
        return 1

    java = shutil.which("java")
    if not java:
        print(
            "ERROR: java not found in PATH. PlantUML requires Java.",
            file=sys.stderr,
        )
        return 1

    cmd = [java, "-jar", str(jar_path), "-tpng", "-charset", "UTF-8", str(input_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    default_output = input_path.with_suffix(".png")
    stderr_lower = (proc.stderr or "").lower()
    stdout_lower = (proc.stdout or "").lower()

    if proc.returncode != 0 or not default_output.is_file():
        print("ERROR: PlantUML failed to render diagram.", file=sys.stderr)
        if proc.stdout:
            print("--- stdout ---", file=sys.stderr)
            print(proc.stdout, file=sys.stderr)
        if proc.stderr:
            print("--- stderr ---", file=sys.stderr)
            print(proc.stderr, file=sys.stderr)
        if "graphviz" in stderr_lower or "graphviz" in stdout_lower or "dot" in stderr_lower:
            print("\n" + GRAPHVIZ_HINT, file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path != default_output:
            shutil.move(str(default_output), str(output_path))
    else:
        output_path = default_output

    print(f"OK: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
