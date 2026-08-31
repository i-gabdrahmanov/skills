"""Детерминированный сканер каталога переиспользования (для reuse-judge).

Две группы:
  • dependencies  — внешние зависимости из build.gradle(.kts)/pom.xml (что доступно на classpath);
  • project_utils — внутрипроектные util/helper-классы с публичными сигнатурами.

ADVISORY-категория: неполный каталог не должен ронять gate. Цель — дать судье качества и
разработчику знать, что уже доступно, чтобы не писать велосипеды.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import BUILD_FILES, iter_files, iter_java, read_text, strip_comments

# Gradle: implementation("g:a:v") | implementation 'g:a:v' | api("g:a") и т.п.
_GRADLE_CONF = (r"(?:implementation|api|compileOnly|compileOnlyApi|runtimeOnly|"
                r"testImplementation|testRuntimeOnly|testCompileOnly|annotationProcessor|"
                r"testAnnotationProcessor|developmentOnly|kapt)")
_GRADLE_GAV = re.compile(
    _GRADLE_CONF + r"\s*\(?\s*['\"]([\w.\-]+):([\w.\-]+)(?::([\w.\-${}]+))?['\"]")
# Maven <dependency>…</dependency>
_MVN_DEP = re.compile(r"<dependency>(.*?)</dependency>", re.DOTALL)
_MVN_G = re.compile(r"<groupId>([^<]+)</groupId>")
_MVN_A = re.compile(r"<artifactId>([^<]+)</artifactId>")
_MVN_V = re.compile(r"<version>([^<]+)</version>")


def scan_dependencies(root: Path) -> list[dict]:
    items: list[dict] = []
    for p in iter_files(Path(root), BUILD_FILES):
        raw = read_text(p)
        if p.name == "pom.xml":
            for m in _MVN_DEP.finditer(raw):
                blk = m.group(1)
                g = _MVN_G.search(blk)
                a = _MVN_A.search(blk)
                v = _MVN_V.search(blk)
                if a:
                    items.append({"group": g.group(1).strip() if g else "",
                                  "artifact": a.group(1).strip(),
                                  "version": v.group(1).strip() if v else "",
                                  "file": str(p)})
        else:
            text = strip_comments(raw)
            for m in _GRADLE_GAV.finditer(text):
                items.append({"group": m.group(1), "artifact": m.group(2),
                              "version": m.group(3) or "", "file": str(p)})
    # dedup по (group, artifact)
    seen: set = set()
    uniq: list[dict] = []
    for it in items:
        k = (it["group"], it["artifact"])
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    uniq.sort(key=lambda d: (d["group"], d["artifact"]))
    return uniq


_CLASS_RE = re.compile(r"\b(?:public\s+)?(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)")
_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_PUBLIC_METHOD_RE = re.compile(
    r"public\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.?\s]+?\s+([a-z]\w*)\s*\(([^)]*)\)")
_STATIC_PUBLIC_RE = re.compile(r"public\s+static\s+[\w<>\[\],.?\s]+?\s+[a-z]\w*\s*\(")
_UTIL_NAME_RE = re.compile(r".*(Util|Utils|Helper|Helpers|Support)$")


def scan_project_utils(root: Path) -> list[dict]:
    """Классы-утилиты: по имени (*Util/*Utils/*Helper/*Support) ИЛИ преимущественно
    из публичных static-методов. Возвращает класс, пакет, файл и публичные сигнатуры."""
    items: list[dict] = []
    for p in iter_java(Path(root)):
        raw = read_text(p)
        if "class" not in raw:
            continue
        text = strip_comments(raw)
        cm = _CLASS_RE.search(text)
        if not cm:
            continue
        cls = cm.group(1)
        methods = [(m.group(1), m.group(2).strip()) for m in _PUBLIC_METHOD_RE.finditer(text)]
        static_pub = _STATIC_PUBLIC_RE.findall(text)
        is_util_name = bool(_UTIL_NAME_RE.match(cls))
        is_static_util = len(static_pub) >= 2 and len(static_pub) >= max(1, len(methods)) * 0.6
        if not (is_util_name or is_static_util):
            continue
        pkg = _PKG_RE.search(text)
        sigs = [f"{n}({a})" for n, a in methods][:20]
        items.append({"class": cls, "package": pkg.group(1) if pkg else "",
                      "file": str(p), "methods": sigs})
    items.sort(key=lambda d: d["class"])
    return items
