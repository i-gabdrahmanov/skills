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


# ── Гейт межмодульных зависимостей (build-граф) ──────────────────────────────
# Слоевой анализ выше смотрит .java-импорты; он НЕ видит, что фича дописала в build.gradle
# зависимость на другой модуль (project(':service:upzservice')), которого по правилам проекта
# подключать нельзя (прогон #3: агент молча подключил модуль УПЗ к task-service). Этот блок ловит
# НОВЫЕ межмодульные зависимости в diff build-файлов и блокирует (deny-first), если они не
# в allow-list. Поведение: quality.module_dep_policy = deny_new (дефолт) | policy | off.

_PROJECT_REF_RE = re.compile(
    r"""project\s*\(\s*(?:path\s*[:=]\s*)?['"](:?[\w.\-]+(?::[\w.\-]+)*)['"]""")


def _module_from_build_path(path: str) -> "str | None":
    """'service/taskservice/build.gradle' → 'service:taskservice'. Корневой build.* → None."""
    parts = Path(path.replace("\\", "/")).parts
    base = {"build.gradle", "build.gradle.kts", "pom.xml"}
    p = [x for x in parts if x not in base]
    return ":".join(p) if p else None


def _canon_module(ref: str) -> str:
    """':service:upzservice' / 'service:upzservice' / 'service-upzservice' → 'service:upzservice'."""
    return ref.lstrip(":").replace("-", ":") if ":" not in ref.lstrip(":") and "-" in ref \
        else ref.lstrip(":")


def _added_module_dep_edges(root: Path, base: str) -> list[dict]:
    """Новые межмодульные project(:...) зависимости из diff build-файлов (added-строки).

    Возвращает [{file, from, to, line}]. from — модуль build-файла, to — подключаемый модуль.
    """
    out: list[dict] = []
    diff = _git(root, "diff", "-U0", base, "--",
                "*.gradle", "*.gradle.kts", "**/build.gradle", "**/build.gradle.kts")
    cur = None
    for ln in diff:
        if ln.startswith("+++ b/"):
            cur = ln[6:]
        elif cur and ln.startswith("+") and not ln.startswith("+++"):
            frm = _module_from_build_path(cur)
            for m in _PROJECT_REF_RE.finditer(ln):
                to = _canon_module(m.group(1))
                if to and to != frm:
                    out.append({"file": cur, "from": frm or "(root)", "to": to,
                                "line": ln[1:].strip()[:120]})
    return out


def load_arch_policy(root: Path) -> dict:
    """ground/architecture-policy.json: {module_deps:{forbidden:[[a,b]], allowed_new:[[c,d]]}}."""
    p = root / "ground" / "architecture-policy.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("module_deps", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


# ── Граф модулей (архитектурный граунд) ──────────────────────────────────────
# Модули можно соединять, но по правилам КОНКРЕТНОГО проекта. Правила выводим из фактического
# графа зависимостей модулей (что проект УЖЕ делает) — это «архитектурный граунд». Канонизация
# рёбер тут — единственный источник правды и для эмиттера граунда (--emit-ground), и для гейта.
_BUILD_NAMES = ("build.gradle", "build.gradle.kts")
_SKIP_DIRS = {".git", "build", "out", "target", ".gradle", ".idea", "node_modules", "bin", ".gigacode"}


def _group(mod_id: str) -> str:
    """Группа модуля = первый сегмент id: 'service:taskservice' → 'service'."""
    return mod_id.split(":")[0] if mod_id else mod_id


def _iter_build_files(root: Path):
    for p in root.rglob("*"):
        if p.is_dir() or p.name not in _BUILD_NAMES:
            continue
        if set(p.relative_to(root).parts) & _SKIP_DIRS:
            continue
        yield p


def build_module_graph(root: Path) -> dict:
    """Полный граф модулей из build-файлов: modules, edges (канон.), groups, allowed_group_couplings."""
    modules: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for bf in _iter_build_files(root):
        frm = _module_from_build_path(str(bf.relative_to(root)))
        if frm:
            modules.add(frm)
        try:
            txt = bf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _PROJECT_REF_RE.finditer(txt):
            to = _canon_module(m.group(1))
            if frm and to and frm != to:
                edges.add((frm, to))
                modules.add(to)
    couplings = {(_group(a), _group(b)) for a, b in edges}
    return {"modules": sorted(modules), "edges": sorted(list(e) for e in edges),
            "groups": sorted({_group(m) for m in modules}),
            "allowed_group_couplings": sorted(list(c) for c in couplings)}


def _reaches(edges: set, start: str, target: str) -> bool:
    """Есть ли путь start →…→ target в edges (set рёбер (a,b))."""
    adj: dict = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    seen, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n == target:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, []))
    return False


def _creates_cycle(base_edges: set, frm: str, to: str) -> bool:
    """Добавление frm→to замыкает цикл ⇔ to уже достигает frm."""
    return _reaches(base_edges, to, frm)


def load_arch_ground(root: Path, explicit: "str | None" = None) -> "dict | None":
    """architecture-ground.json (по умолчанию docs/system-analysis/), либо None."""
    p = Path(explicit) if explicit else (root / "docs" / "system-analysis" / "architecture-ground.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _mdep_violation(edge: dict, detail: str) -> dict:
    return {"file": edge["file"], "rule": "module-dependency", "severity": "error", "detail": detail}


def check_module_deps(root: Path, base: str, mode: str, arch_ground: "dict | None" = None) -> list[dict]:
    """Нарушения по новым межмодульным зависимостям. mode: graph | deny_new | policy | off."""
    if mode == "off":
        return []
    policy = load_arch_policy(root)
    forbidden = {tuple(_canon_module(x) for x in e) for e in policy.get("forbidden", []) if len(e) == 2}
    allowed = {tuple(_canon_module(x) for x in e) for e in policy.get("allowed_new", []) if len(e) == 2}

    new_edges = _added_module_dep_edges(root, base)
    if not new_edges:
        return []

    base_edges: set = set()
    couplings: set = set()
    if mode == "graph":
        ground = arch_ground if arch_ground is not None else load_arch_ground(root)
        if ground:
            base_edges = {tuple(e) for e in ground.get("edges", []) if len(e) == 2}
            couplings = {tuple(c) for c in ground.get("allowed_group_couplings", []) if len(c) == 2}
        else:
            # граунда нет → деривация на лету: текущий граф МИНУС новые рёбра = baseline
            cur = build_module_graph(root)
            cur_edges = {tuple(e) for e in cur["edges"]}
            new_set = {(e["from"], e["to"]) for e in new_edges}
            base_edges = cur_edges - new_set
            couplings = {(_group(a), _group(b)) for a, b in base_edges}

    violations: list[dict] = []
    for edge in new_edges:
        frm, to = edge["from"], edge["to"]
        pair = (frm, to)
        if pair in allowed:
            continue
        if pair in forbidden:
            violations.append(_mdep_violation(
                edge, f"межмодульная зависимость {frm} → {to} ЗАПРЕЩЕНА политикой "
                      f"(architecture-policy.json): {edge['line']}"))
            continue
        if mode == "policy":
            continue  # вне graph: блокируем только forbidden
        if mode == "deny_new":
            violations.append(_mdep_violation(
                edge, f"новая межмодульная зависимость {frm} → {to} в {edge['file']}: {edge['line']} — "
                      f"не подключай модуль молча. Используй существующий API-модуль/контракт; если это "
                      f"осознанное архрешение — override (§0.6.1) или ground/architecture-policy.json allowed_new."))
            continue
        # mode == "graph"
        if _creates_cycle(base_edges, frm, to):
            violations.append(_mdep_violation(
                edge, f"ЦИКЛ зависимостей: {frm} → {to} замыкает граф модулей (через {edge['file']}). "
                      f"Циклы между модулями запрещены — разорви связь (вынеси общий код/контракт)."))
        elif (_group(frm), _group(to)) not in couplings:
            violations.append(_mdep_violation(
                edge, f"НОВАЯ group-связка {_group(frm)} → {_group(to)} ({frm} → {to} в {edge['file']}): "
                      f"проект так модули не соединяет (нет в architecture-ground). Не вводи новое арх-связывание "
                      f"молча — используй существующий API-модуль/контракт, либо подтверди: override (§0.6.1) "
                      f"или ground/architecture-policy.json allowed_new."))
        # иначе — связка уже принята в проекте → пропуск
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic ArchUnit-lite layering gate.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--changed", help="явный список .java (через ,/пробел) — минует git")
    ap.add_argument("--pipeline-config")
    ap.add_argument("--package-root", help="переопределить conventions.package_root")
    ap.add_argument("--module-dep-policy", choices=["deny_new", "policy", "off"], default=None,
                    help="политика новых межмодульных зависимостей (дефолт quality.module_dep_policy / deny_new)")
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

    # Гейт межмодульных зависимостей (по git diff build-файлов) — независим от --changed/слоёв.
    mode = args.module_dep_policy
    if mode is None:
        mode = "deny_new"
        if args.pipeline_config:
            try:
                cfg = json.loads(Path(args.pipeline_config).read_text(encoding="utf-8"))
                mode = (cfg.get("quality") or {}).get("module_dep_policy", "deny_new")
            except Exception:
                pass
    mdep = check_module_deps(root, args.base, mode)
    if mdep:
        verdict["violations"].extend(mdep)
        verdict["counts"]["error"] += len(mdep)
        verdict["status"] = "fail"

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
