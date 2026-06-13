"""Детерминированный сканер конфигурации: application*.yml / *.properties по модулям.

Возвращает профили (из имён файлов application-<profile>.* и из spring.config.activate)
и верхнеуровневые ключи. Категория ADVISORY в gate.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import iter_files, read_text

_CONFIG_GLOB = (".yml", ".yaml", ".properties")
_PROFILE_FROM_NAME = re.compile(r"application-([A-Za-z0-9_]+)\.(?:ya?ml|properties)$")


def _is_app_config(path: Path) -> bool:
    return path.name.startswith("application") and path.suffix in _CONFIG_GLOB


def _top_keys(text: str, suffix: str) -> list[str]:
    keys: set[str] = set()
    if suffix == ".properties":
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                keys.add(line.split("=", 1)[0].split(".")[0].strip())
    else:  # yaml — верхнеуровневые ключи (без отступа), грубо
        for line in text.splitlines():
            if line and not line[0].isspace() and not line.lstrip().startswith("#") and ":" in line:
                keys.add(line.split(":", 1)[0].strip())
    return sorted(k for k in keys if k)


def scan(root: Path) -> dict:
    profiles: set[str] = set()
    files: list[dict] = []
    for p in iter_files(Path(root), _CONFIG_GLOB):
        if not _is_app_config(p):
            continue
        text = read_text(p)
        pm = _PROFILE_FROM_NAME.search(p.name)
        if pm:
            profiles.add(pm.group(1))
        for am in re.finditer(r"spring\.config\.activate\.on-profile\s*[:=]\s*([A-Za-z0-9_]+)", text):
            profiles.add(am.group(1))
        for am in re.finditer(r"(?:^|\n)\s*(?:active|on-profile)\s*:\s*([A-Za-z0-9_,\s]+)", text):
            for tok in re.split(r"[,\s]+", am.group(1).strip()):
                if tok:
                    profiles.add(tok)
        files.append({"file": str(p), "top_keys": _top_keys(text, p.suffix)})
    return {"profiles": sorted(profiles), "files": files}


def scan_items(root: Path) -> list[dict]:
    """Плоский список конфиг-файлов (для атрибуции к модулям и счётчиков)."""
    return scan(root)["files"]
