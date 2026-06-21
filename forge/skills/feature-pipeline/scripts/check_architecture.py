#!/usr/bin/env python3
"""check_architecture.py — детерминированный ArchUnit-lite гейт слоёв (P2-9).

Покрытие и компиляция не видят архитектурных нарушений: сущность, лезущая в сервис; контроллер,
дёргающий репозиторий напрямую; класс не в своём пакете; пакет не под `package_root`. Этот гейт
ловит их БЕЗ запуска Java/ArchUnit — статическим разбором изменённых `.java` (package + imports +
имя класса). Консервативен: жёстко (fail) — только универсально-согласованные правила
(package_root, чистота домена), остальное — warning (видно, но не валит; `--strict` ужесточает).

Usage:
    check_architecture.py [--root .] [--base HEAD] [--changed "a.java b.java"]
        [--pipeline-config pipeline.json] [--package-root ru.x.y] [--strict] [--json]
Exit: 0 = pass (или только warnings без --strict), 2 = fail (нарушение error-уровня / --strict).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Словарь слоёв (сегмент пакета/пути). Совпадает с task-plan layers + типовые доп. пакеты.
LAYERS = {"entity", "domain", "model", "repository", "repo", "dto", "mapper",
          "service", "controller", "config", "exception", "client"}

# Суффикс имени класса → ожидаемый слой (для проверки «класс в своём пакете»).
SUFFIX_LAYER = {
    "Controller": "controller", "Repository": "repository", "ServiceImpl": "service",
    "Service": "service", "Mapper": "mapper", "Dto": "dto", "Request": "dto", "Response": "dto",
}

# Запрещённые зависимости слоёв (error): чистота нижних слоёв. layer → {запрещённые импорты}.
FORBIDDEN_IMPORTS = {
    "entity":     {"service", "controller", "repository", "mapper", "dto"},
    "domain":     {"service", "controller", "repository", "mapper", "dto"},
    "model":      {"service", "controller", "repository", "mapper"},
    "repository": {"service", "controller"},
    "repo":       {"service", "controller"},
}
# Подозрительные, но иногда намеренные зависимости (warning).
WARN_IMPORTS = {"controller": {"repository", "repo"}}

_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.M)
_CLASS_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Z]\w*)")


def _layer_from_pkg(pkg: str) -> str | None:
    for seg in reversed(pkg.split(".")):
        if seg in LAYERS:
            return "repository" if seg == "repo" else seg
    return None


def _layer_from_path(path: str) -> str | None:
    for seg in reversed(Path(path.replace("\\", "/")).parts):
        s = seg[:-5] if seg.endswith(".java") else seg
        if s in LAYERS:
            return "repository" if s == "repo" else s
    return None


def _import_layer(imp: str, package_root: str | None) -> str | None:
    """Слой ВНУТРЕННЕГО импорта (под package_root). Внешние (spring и т.п.) → None."""
    if package_root and not imp.startswith(package_root + "."):
        return None
    if not package_root and "." not in imp:
        return None
    return _layer_from_pkg(imp.rsplit(".", 1)[0])


def analyze_file(path: str, content: str, package_root: str | None) -> list[dict]:
    """Нарушения архитектуры в одном файле. Каждое: {file, rule, severity, detail}."""
    v: list[dict] = []
    norm = path.replace("\\", "/")
    # анализируем только продакшн-исходники (.java под src/main, без тестов)
    if not norm.endswith(".java") or "src/main/" not in norm or "/test/" in norm:
        return v

    pkg_m = _PKG_RE.search(content or "")
    pkg = pkg_m.group(1) if pkg_m else ""
    layer = _layer_from_pkg(pkg) or _layer_from_path(norm)
    cls_m = _CLASS_RE.search(content or "")
    classname = cls_m.group(1) if cls_m else ""

    # 1. package_root — пакет обязан быть под корнем (error)
    if package_root and pkg and not (pkg == package_root or pkg.startswith(package_root + ".")):
        v.append({"file": norm, "rule": "package-root", "severity": "error",
                  "detail": f"пакет '{pkg}' не под package_root '{package_root}'"})

    # 2. размещение класса по суффиксу имени (warning)
    for suf, want in SUFFIX_LAYER.items():
        if classname.endswith(suf):
            if layer and layer != want:
                v.append({"file": norm, "rule": "class-placement", "severity": "warning",
                          "detail": f"класс '{classname}' (суффикс {suf}) ожидается в пакете "
                                    f"'.{want}', а лежит в слое '{layer}'"})
            break

    # 3. зависимости слоёв (error/warning) — только если слой файла определён
    if layer:
        forbidden = FORBIDDEN_IMPORTS.get(layer, set())
        warn = WARN_IMPORTS.get(layer, set())
        for imp in _IMPORT_RE.findall(content or ""):
            il = _import_layer(imp, package_root)
            if not il or il == layer:
                continue
            if il in forbidden:
                v.append({"file": norm, "rule": "layer-dependency", "severity": "error",
                          "detail": f"слой '{layer}' импортирует '{il}' ({imp}) — нарушение слоёв"})
            elif il in warn:
                v.append({"file": norm, "rule": "layer-dependency", "severity": "warning",
                          "detail": f"слой '{layer}' импортирует '{il}' напрямую ({imp})"})
    return v


def analyze(files: dict, package_root: str | None) -> dict:
    """files: {path: content}. Вердикт со списком нарушений."""
    violations: list[dict] = []
    for path, content in files.items():
        violations.extend(analyze_file(path, content, package_root))
    errors = [x for x in violations if x["severity"] == "error"]
    warnings = [x for x in violations if x["severity"] == "warning"]
    return {"status": "fail" if errors else "pass",
            "checked": len(files), "violations": violations,
            "counts": {"error": len(errors), "warning": len(warnings)}}


def _git(root: Path, *args: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines() if out.returncode == 0 else []
    except Exception:
        return []


def _changed_java(root: Path, base: str) -> list[str]:
    files = set()
    files.update(_git(root, "diff", "--name-only", base))
    files.update(_git(root, "diff", "--name-only", "--cached"))
    files.update(_git(root, "ls-files", "--others", "--exclude-standard"))
    return [f for f in sorted(files)
            if f.endswith(".java") and "/test/" not in f and "src/main/" in f]


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic ArchUnit-lite layering gate.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--changed", help="явный список .java (через ,/пробел) — минует git")
    ap.add_argument("--pipeline-config")
    ap.add_argument("--package-root", help="переопределить conventions.package_root")
    ap.add_argument("--strict", action="store_true", help="warnings тоже валят (exit 2)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    package_root = args.package_root
    if package_root is None and args.pipeline_config:
        try:
            cfg = json.loads(Path(args.pipeline_config).read_text(encoding="utf-8"))
            package_root = (cfg.get("conventions") or {}).get("package_root")
        except Exception:
            pass

    if args.changed is not None:
        paths = [c for c in re.split(r"[,\s]+", args.changed.strip())
                 if c.endswith(".java") and "/test/" not in c]
    else:
        paths = _changed_java(root, args.base)

    files = {}
    for p in paths:
        fp = (root / p) if not Path(p).is_absolute() else Path(p)
        try:
            files[p] = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            files[p] = ""

    verdict = analyze(files, package_root)
    failed = verdict["counts"]["error"] > 0 or (args.strict and verdict["counts"]["warning"] > 0)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✗ FAIL" if failed else ("✓ PASS" if not verdict["violations"] else "✓ PASS (есть warnings)")
        c = verdict["counts"]
        print(f"Architecture gate: {mark}  (файлов {verdict['checked']}, "
              f"ошибок {c['error']}, предупр. {c['warning']})")
        for x in verdict["violations"]:
            flag = "✗" if x["severity"] == "error" else "⚠"
            print(f"  {flag} [{x['rule']}] {x['file']}: {x['detail']}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
