#!/usr/bin/env python3
"""check_architecture.py — детерминированный ArchUnit-lite гейт слоёв (P2-9).

Покрытие и компиляция не видят архитектурных нарушений: сущность, лезущая в сервис; контроллер,
дёргающий репозиторий напрямую; класс не в своём пакете; пакет не под `package_root`. Этот гейт
ловит их БЕЗ запуска Java/ArchUnit — статическим разбором изменённых `.java` (package + imports +
имя класса). Консервативен: жёстко (fail) — только универсально-согласованные правила
(package_root, чистота домена), остальное — warning (видно, но не валит; `--strict` ужесточает).

Плюс гейт МЕЖМОДУЛЬНЫХ зависимостей (build-граф): ловит новые межмодульные зависимости в diff
build-файлов — Gradle `project(':...')` и Maven `<dependency>` на внутренний модуль в `pom.xml` —
и проверяет их против «архитектурного граунда» проекта (что проект УЖЕ соединяет). Режим `graph`
(дефолт): цикл или новая group-связка → блок; принятая связка → пропуск. Граунд эмитится на grounding
(`--emit-ground`) в docs/system-analysis/architecture-ground.json; уточняется ground/architecture-policy.json.

Usage:
    check_architecture.py [--root .] [--base HEAD] [--changed "a.java b.java"]
        [--pipeline-config pipeline.json] [--package-root ru.x.y] [--strict] [--json]
        [--module-dep-policy graph|deny_new|policy|off] [--arch-ground PATH]
    check_architecture.py --root . --emit-ground docs/system-analysis/architecture-ground.json
Exit: 0 = pass (или только warnings без --strict), 2 = fail (нарушение error-уровня / --strict).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
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


def _git_show(root: Path, ref: str, path: str) -> str:
    """Содержимое path на ревизии ref ('' если файла там нет / не git)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


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
# в allow-list. Gradle: project(':...') построчно. Maven: межмодульный <dependency> синтаксически
# неотличим от внешней либы — сверяем с множеством модулей проекта (parse&compare изменённых pom.xml).
# Поведение: quality.module_dep_policy = graph (дефолт) | deny_new | policy | off.

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
    """Новые межмодульные зависимости из diff build-файлов (Gradle + Maven).

    Возвращает [{file, from, to, line}]. from — модуль build-файла, to — подключаемый модуль.
    Gradle: added-строки project(':...'). Maven: parse&compare изменённых pom.xml (base vs рабочее
    дерево), т.к. межмодульный <dependency> синтаксически неотличим от внешней либы.
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

    # Maven: какие pom.xml изменились (tracked + staged + untracked), затем diff множеств внутр. deps.
    poms = set()
    poms.update(_git(root, "diff", "--name-only", base, "--", "*pom.xml"))
    poms.update(_git(root, "diff", "--name-only", "--cached", "--", "*pom.xml"))
    poms.update(_git(root, "ls-files", "--others", "--exclude-standard", "--", "*pom.xml"))
    poms = {p for p in poms if Path(p).name == "pom.xml"}
    if poms:
        _, resolve = _maven_modules(root)
        for pom in sorted(poms):
            frm = _module_from_build_path(pom)
            base_edges = _pom_internal_edges(_git_show(root, base, pom), frm, resolve)
            try:
                work_txt = (root / pom).read_text(encoding="utf-8", errors="replace")
            except OSError:
                work_txt = ""
            for to in sorted(_pom_internal_edges(work_txt, frm, resolve) - base_edges):
                out.append({"file": pom, "from": frm or "(root)", "to": to,
                            "line": f"<dependency> … <artifactId>{to.split(':')[-1]}</artifactId>"})
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


# ── Maven (pom.xml) ───────────────────────────────────────────────────────────
# В Gradle project(':a:b') самоидентифицирует внутренний модуль. В Maven межмодульная зависимость —
# обычный <dependency> с <groupId>/<artifactId>, синтаксически неотличимый от внешней либы
# (org.springframework:spring-core). Чтобы отличить внутренний модуль, нужно ЗНАТЬ множество модулей
# проекта: сканируем все pom.xml, строим карту координата→path-id (как у Gradle: service/upz/pom.xml
# → service:upz), ребро существует ⇔ артефакт зависимости есть в карте. Парсим xml.etree, обход по
# локальному имени тега (namespace-агностично); deps берём только из прямого <dependencies> проекта —
# вложенный <dependencyManagement><dependencies> туда не попадает.

def _local(tag: str) -> str:
    """Локальное имя тега без namespace: '{http://…/POM/4.0.0}project' → 'project'."""
    return tag.rsplit("}", 1)[-1]


def _child(elem, name: str):
    """Первый прямой потомок с локальным именем name (или None)."""
    if elem is None:
        return None
    for ch in elem:
        if _local(ch.tag) == name:
            return ch
    return None


def _child_text(elem, name: str) -> "str | None":
    ch = _child(elem, name)
    return ch.text.strip() if ch is not None and ch.text and ch.text.strip() else None


def _parse_pom(text: str) -> "dict | None":
    """pom.xml → {group, artifact, deps:[(groupId|None, artifactId)]}; None при ошибке разбора.

    artifact/group — СОБСТВЕННЫЕ координаты модуля: artifactId из <project> (НЕ из <parent>),
    groupId наследуется от <parent> при отсутствии. deps — только прямой <dependencies>/<dependency>
    (managed-блок <dependencyManagement> вложен отдельно и сюда не входит).
    """
    try:
        root = ET.fromstring(text or "")
    except Exception:
        return None
    if _local(root.tag) != "project":
        return None
    artifact = _child_text(root, "artifactId")
    group = _child_text(root, "groupId") or _child_text(_child(root, "parent"), "groupId")
    deps: list = []
    deps_el = _child(root, "dependencies")
    if deps_el is not None:
        for dep in deps_el:
            if _local(dep.tag) != "dependency":
                continue
            a = _child_text(dep, "artifactId")
            if a:
                deps.append((_child_text(dep, "groupId"), a))
    return {"group": group, "artifact": artifact, "deps": deps}


def _resolve_dep(resolve: dict, group: "str | None", artifact: str) -> "str | None":
    """Координата зависимости → path-id внутреннего модуля (или None для внешней)."""
    if group:
        hit = resolve.get(f"{group}:{artifact}")
        if hit:
            return hit
    return resolve.get(artifact)


def _edges_from_parsed(parsed: dict, frm: "str | None", resolve: dict) -> set:
    out: set = set()
    for g, a in parsed.get("deps", []):
        to = _resolve_dep(resolve, g, a)
        if to and to != frm:
            out.add(to)
    return out


def _pom_internal_edges(text: str, frm: "str | None", resolve: dict) -> set:
    """Множество path-id внутр. модулей, на которые ссылается pom-текст (кроме самого frm)."""
    parsed = _parse_pom(text)
    return _edges_from_parsed(parsed, frm, resolve) if parsed else set()


def _iter_pom_files(root: Path):
    for p in root.rglob("pom.xml"):
        if p.is_dir():
            continue
        if set(p.relative_to(root).parts) & _SKIP_DIRS:
            continue
        yield p


def _maven_modules(root: Path) -> "tuple[dict, dict]":
    """Скан всех pom.xml → (modules, resolve).

    modules: {path_id: parsed}. resolve: и 'groupId:artifactId', и голый 'artifactId' → path_id
    (голый — fallback для sibling-dep без groupId / с ${project.groupId}; универсально для любой
    структуры). При коллизии голого artifactId меж модулями голый ключ убираем — авторитетным
    остаётся 'groupId:artifactId'. Корневой/агрегатор pom (path_id=None) узлом не становится.
    """
    modules: dict = {}
    parsed_list: list = []
    for pf in _iter_pom_files(root):
        try:
            txt = pf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parsed = _parse_pom(txt)
        if parsed is None:
            continue
        path_id = _module_from_build_path(str(pf.relative_to(root)))
        if path_id:
            modules[path_id] = parsed
            parsed_list.append((path_id, parsed))

    resolve: dict = {}
    bare: dict = {}
    for path_id, parsed in parsed_list:
        art, grp = parsed.get("artifact"), parsed.get("group")
        if art and grp:
            resolve[f"{grp}:{art}"] = path_id
        if art:
            bare.setdefault(art, set()).add(path_id)
    for art, ids in bare.items():
        if len(ids) == 1:
            resolve.setdefault(art, next(iter(ids)))
    return modules, resolve


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
    # Maven: те же узлы/рёбра из pom.xml (координата зависимости → path-id через карту модулей).
    mvn_modules, resolve = _maven_modules(root)
    for path_id, parsed in mvn_modules.items():
        modules.add(path_id)
        for to in _edges_from_parsed(parsed, path_id, resolve):
            edges.add((path_id, to))
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
    ap.add_argument("--module-dep-policy", choices=["graph", "deny_new", "policy", "off"], default=None,
                    help="политика новых межмодульных зависимостей (дефолт quality.module_dep_policy / graph)")
    ap.add_argument("--arch-ground", default=None,
                    help="путь к architecture-ground.json (дефолт docs/system-analysis/)")
    ap.add_argument("--emit-ground", default=None,
                    help="построить граф модулей и записать architecture-ground.json по этому пути (без проверки)")
    ap.add_argument("--strict", action="store_true", help="warnings тоже валят (exit 2)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()

    # Режим эмиссии архитектурного граунда (фаза grounding): построить граф и записать.
    if args.emit_ground:
        from datetime import datetime, timezone
        g = build_module_graph(root)
        g = {"$schema": "feature-pipeline/architecture-ground@1",
             "generated_at": datetime.now(timezone.utc).isoformat(), **g}
        out = Path(args.emit_ground)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(g, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅ architecture-ground записан: {out}")
        print(f"   модулей {len(g['modules'])}, рёбер {len(g['edges'])}, "
              f"групп {len(g['groups'])}, group-связок {len(g['allowed_group_couplings'])}")
        return 0

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
        mode = "graph"
        if args.pipeline_config:
            try:
                cfg = json.loads(Path(args.pipeline_config).read_text(encoding="utf-8"))
                mode = (cfg.get("quality") or {}).get("module_dep_policy", "graph")
            except Exception:
                pass
    arch_ground = load_arch_ground(root, args.arch_ground) if mode == "graph" else None
    mdep = check_module_deps(root, args.base, mode, arch_ground)
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
