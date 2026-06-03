"""Shared path helpers for the plantuml-to-png skill.

The plantuml jar is cached per-user in an OS-appropriate directory, not in
the skill directory, so it can be shared between projects and is not
committed to git.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PLANTUML_VERSION = "1.2026.3"
JAR_URL = (
    f"https://github.com/plantuml/plantuml/releases/download/"
    f"v{PLANTUML_VERSION}/plantuml-{PLANTUML_VERSION}.jar"
)


def get_cache_dir() -> Path:
    """Return per-user cache directory for this skill, by platform."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        appdata = os.environ.get("LOCALAPPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "plantuml-skill"


def get_jar_path() -> Path:
    return get_cache_dir() / f"plantuml-{PLANTUML_VERSION}.jar"
