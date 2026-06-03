"""Детерминированный обзор структуры проекта: модули, зависимости, Spring Boot apps.

Модуль = каталог с build.gradle / build.gradle.kts / pom.xml. Это надёжнее парсинга
settings.gradle (работает и для Gradle, и для Maven, и для нестандартных раскладок).
"""
from __future__ import annotations

import re
from pathlib import Path

from common import BUILD_FILES, in_skipped_dir, iter_java, read_text


def _module_dirs(root: Path) -> list[Path]:
    root = root.resolve()
    dirs: set[Path] = set()
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if in_skipped_dir(root, path):
            continue
        if path.name in BUILD_FILES:
            dirs.add(path.parent)
    return sorted(dirs, key=lambda p: str(p))


def _project_deps(build_text: str) -> list[str]:
    deps: set[str] = set()
    # implementation project(':foo') / project(":a:b") / project(path: ':foo')
    for m in re.finditer(r"project\(\s*(?:path\s*[:=]\s*)?[\"']:?([A-Za-z0-9_\-:./]+)[\"']", build_text):
        token = m.group(1).strip(":").replace(":", "/").rstrip("/")
        deps.add(token.split("/")[-1])
    return sorted(deps)


def scan(root: Path) -> dict:
    root = root.resolve()
    module_dirs = _module_dirs(root)
    build_system = "maven" if any((d / "pom.xml").exists() for d in module_dirs) else "gradle"

    modules: list[dict] = []
    for md in module_dirs:
        bf = next((md / b for b in BUILD_FILES if (md / b).exists()), None)
        txt = read_text(bf) if bf else ""
        rel = "." if md == root else str(md.relative_to(root))
        has_main = (md / "src/main/java").is_dir() or (md / "src/main/kotlin").is_dir()
        has_test = (md / "src/test/java").is_dir() or (md / "src/test/kotlin").is_dir()
        # Имя по Gradle-конвенции мультимодуля: путь с '/'→'-' (utils/web → utils-web),
        # чтобы совпадать со ссылками в depends_on (project(':utils:web') → utils-web).
        modules.append({
            "name": root.name if md == root else rel.replace("/", "-"),
            "path": rel,
            "has_src_main": has_main,
            "has_src_test": has_test,
            "depends_on": _project_deps(txt),
        })

    apps: list[str] = []
    for p in iter_java(root):
        t = read_text(p)
        if "@SpringBootApplication" in t:
            cm = re.search(r"\bclass\s+([A-Za-z_]\w*)", t)
            if cm:
                apps.append(cm.group(1))

    root_build = next((read_text(root / b) for b in BUILD_FILES if (root / b).exists()), "")
    sb = re.search(r"spring[ -]?boot[^\n]*?(\d+\.\d+\.\d+)", root_build, re.IGNORECASE)
    jv = re.search(r"(?:sourceCompatibility|targetCompatibility|languageVersion|java[._]version)\D*(\d{1,2})", root_build)

    code_modules = [m for m in modules if m["has_src_main"]]
    return {
        "build_system": build_system,
        "is_multi_module": len(code_modules) > 1,
        "spring_boot_version": sb.group(1) if sb else None,
        "java_version": jv.group(1) if jv else None,
        "modules": modules,
        "spring_boot_applications": sorted(set(apps)),
    }


def module_dir_index(root: Path, structure: dict) -> list[tuple[str, Path]]:
    """(name, abspath) для каждого модуля — для attribute_module."""
    root = Path(root).resolve()
    out: list[tuple[str, Path]] = []
    for m in structure["modules"]:
        out.append((m["name"], (root if m["path"] == "." else root / m["path"])))
    return out
