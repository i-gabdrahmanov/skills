#!/usr/bin/env python3
"""_techlib.py — общее ядро tech-ветки (forgetech): план, классы снимка, пробы, diff.

Здесь ровно то, что нужно детерминированным гейтам техзадачи (check_tech_sdd,
check_invariants, tech_plan, tech_report) и хуку tech-guard — без модели:

  • план — `change-plan.json` (targets / expected_changes / probes / invariants);
  • классы снимка:
      config — ИТОГОВЫЕ значения `application*.yml|yaml|properties` по модулю и профилю
               (база + профиль + `${VAR:default}`), а не строки файла: правка не в том файле
               или перекрытая профилем не должна проходить гейт;
      build  — объявленные версии в билд-файлах (parent/properties/зависимости/плагины pom.xml,
               libs.versions.toml, gradle.properties, строковые координаты build.gradle);
      deps   — РЕЗОЛВНУТЫЕ версии (mvn dependency:list / gradle dependencies): транзитивы и BOM
               видны только так. Репозиторий артефактов недоступен — это отказ, а не тихий
               откат на слабую проверку (tasks/018, Q4);
      api / kafka / db — контракты из сканеров system-analyst (эндпойнты, топики, таблицы);
  • пробы — типизированные (pattern-*, file-*, dep-version), без произвольного shell;
  • diff — снимок vs текущее, за вычетом заявленного.

PyYAML на целевой машине не гарантирован (tasks/018, Q5), поэтому YAML разбирается своим
минимальным парсером блочного стиля: вложенные map, скаляры, кавычки, комментарии, списки,
block scalar `|`/`>`, мультидокументы `---` с `spring.config.activate.on-profile`. Якоря/
алиасы и flow-коллекции сохраняются сырой строкой — сравнивается то, что написано.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parents[1]
_HOOKS = _SKILLS.parent / "hooks"
for _p in (str(_HOOKS),):
    if _p not in sys.path:
        sys.path.append(_p)

SKILL = "forgetech"
PLAN_FILE = "change-plan.json"
SDD_FILE = "tech-sdd.md"
REPORT_FILE = "report.md"
SNAPSHOT_DIR = "snapshots"
SNAPSHOT_FILE = "snapshot.json"
SCHEMA = "forgetech/snapshot@1"

ARCHETYPES = ("dependency-upgrade", "config-change")
CHANGE_KINDS = ("config", "build", "deps")
PROBE_KINDS = ("pattern-absent", "pattern-present", "file-absent", "file-present", "dep-version")
# Имя инварианта в плане → класс снимка. tests судит module_tests.py, не снимок.
INVARIANT_CLASSES = {"tests": None, "api": "api", "kafka": "kafka", "db-schema": "db",
                     "config-keys": "config", "build": "build", "deps": "deps"}
REQUIRED_INVARIANTS = {
    "dependency-upgrade": ("tests", "api", "kafka", "db-schema"),
    "config-change": ("tests", "api", "config-keys"),
}

_BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts", "gradle.properties", "libs.versions.toml")
_SKIP_DIRS = {".git", "build", "target", "node_modules", ".gradle", ".idea", "ground",
              ".gigacode", ".qwen", ".claude", "out", "bin", "docs"}
_CONFIG_RE = re.compile(r"^application(?:-([A-Za-z0-9_.-]+))?\.(ya?ml|properties)$")


class TechError(RuntimeError):
    """Отказ гейта с понятной причиной (не крах скрипта)."""


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Пути ─────────────────────────────────────────────────────────────────────

def project_root(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).resolve()
    from _project import find_project_root
    return Path(find_project_root()).resolve()


def task_dir(root: Path, feature: str) -> Path:
    from _project import tech_docs_dir
    return tech_docs_dir(root, feature)


def plan_path(root: Path, feature: str) -> Path:
    return task_dir(root, feature) / PLAN_FILE


def state_dir(root: Path, feature: str) -> Path:
    from _project import state_dir as _sd
    return _sd(root, SKILL, feature)


def snapshot_dir(root: Path, feature: str) -> Path:
    return state_dir(root, feature) / SNAPSHOT_DIR


def rel(root: Path, p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(p).as_posix()


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            yield Path(dirpath) / fn


# ── План ─────────────────────────────────────────────────────────────────────

def load_plan(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TechError(f"нет плана {path} — его пишет фаза tech-diag")
    except (OSError, ValueError) as e:
        raise TechError(f"план {path} нечитаем: {e}")
    if not isinstance(data, dict):
        raise TechError(f"план {path}: ожидается JSON-объект")
    return data


def is_target_change(ch: dict) -> bool:
    """Цель (точное изменение from→to), а не разрешённая область (`match`)."""
    return isinstance(ch, dict) and "match" not in ch


def change_id(ch: dict) -> str:
    kind = ch.get("kind")
    if kind == "config":
        return f"config {ch.get('module') or '*'}|{ch.get('profile') or 'default'}|{ch.get('key')}"
    if kind == "deps":
        return f"deps {ch.get('coord')}"
    if kind == "build":
        return f"build {ch.get('key')}"
    return f"{kind} ?"


def validate_plan(plan: dict) -> list:
    """Структурные ошибки плана (пусто — схема в порядке). Семантику архетипа и связь с
    tech-sdd.md проверяет check_tech_sdd.py."""
    errs: list = []
    arch = plan.get("archetype")
    if arch not in ARCHETYPES:
        errs.append(f"archetype '{arch}' не из {list(ARCHETYPES)}")
    targets = plan.get("targets")
    if not isinstance(targets, list) or not targets:
        errs.append("targets пуст — скоуп правки не задан")
        targets = []
    for t in targets:
        if not isinstance(t, str) or not t.strip():
            errs.append(f"target {t!r}: ожидается непустой путь")
            continue
        if t.startswith("/") or ".." in Path(t).parts:
            errs.append(f"target '{t}': путь обязан быть относительным, без '..'")
        if any(c in t for c in "*?[]"):
            errs.append(f"target '{t}': глобы запрещены — скоуп утверждается поимённо")
    changes = plan.get("expected_changes")
    if not isinstance(changes, list):
        errs.append("expected_changes: ожидается список")
        changes = []
    for i, ch in enumerate(changes):
        where = f"expected_changes[{i}]"
        if not isinstance(ch, dict):
            errs.append(f"{where}: ожидается объект")
            continue
        kind = ch.get("kind")
        if kind not in CHANGE_KINDS:
            errs.append(f"{where}: kind '{kind}' не из {list(CHANGE_KINDS)}")
            continue
        if "match" in ch:
            if not isinstance(ch["match"], str) or not ch["match"].strip():
                errs.append(f"{where}: match — непустая маска")
            continue
        if "to" not in ch:
            errs.append(f"{where}: у цели нет 'to' (null — удаление)")
        if "from" not in ch:
            errs.append(f"{where}: у цели нет 'from' (null — значения не было)")
        if kind == "config" and not ch.get("key"):
            errs.append(f"{where}: config-цели нужен key")
        if kind == "deps" and not re.fullmatch(r"[\w.\-]+:[\w.\-]+", str(ch.get("coord") or "")):
            errs.append(f"{where}: deps-цели нужен coord вида group:artifact")
        if kind == "build" and not ch.get("key"):
            errs.append(f"{where}: build-цели нужен key (как его печатает `check_invariants.py show`)")
    probes = plan.get("probes") or []
    if not isinstance(probes, list):
        errs.append("probes: ожидается список")
        probes = []
    ids = set()
    for i, pr in enumerate(probes):
        where = f"probes[{i}]"
        if not isinstance(pr, dict):
            errs.append(f"{where}: ожидается объект")
            continue
        pid = pr.get("id") or f"P{i + 1}"
        if pid in ids:
            errs.append(f"{where}: повтор id '{pid}'")
        ids.add(pid)
        kind = pr.get("kind")
        if kind not in PROBE_KINDS:
            errs.append(f"{where}: kind '{kind}' не из {list(PROBE_KINDS)}")
        elif kind.startswith("pattern-"):
            if not pr.get("glob") or not pr.get("regex"):
                errs.append(f"{where}: pattern-пробе нужны glob и regex")
            else:
                try:
                    re.compile(pr["regex"])
                except re.error as e:
                    errs.append(f"{where}: regex не компилируется: {e}")
        elif kind.startswith("file-") and not pr.get("path"):
            errs.append(f"{where}: file-пробе нужен path")
        elif kind == "dep-version" and not (pr.get("coord") and pr.get("expect")):
            errs.append(f"{where}: dep-version нужны coord и expect")
    inv = plan.get("invariants")
    if not isinstance(inv, list):
        errs.append("invariants: ожидается список")
        inv = []
    for name in inv:
        if name not in INVARIANT_CLASSES:
            errs.append(f"invariants: '{name}' не из {sorted(INVARIANT_CLASSES)}")
    for name in REQUIRED_INVARIANTS.get(arch, ()):
        if name not in inv:
            errs.append(f"invariants: для архетипа {arch} обязателен '{name}'")
    if not str(plan.get("rollback") or "").strip():
        errs.append("rollback пуст — как откатить, решается до правки, а не после")
    if "outside_repo" not in plan:
        errs.append("outside_repo отсутствует — напиши, что меняется вне репозитория (или «нет»)")
    if not [c for c in changes if is_target_change(c)] and not probes:
        errs.append("нет ни одной цели (expected_changes без match) и ни одной пробы — "
                    "цель задачи не задана")
    return errs


def classes_of_plan(plan: dict) -> set:
    """Классы снимка, которые нужны плану: инварианты + классы целей/областей."""
    out = {INVARIANT_CLASSES[n] for n in plan.get("invariants") or [] if INVARIANT_CLASSES.get(n)}
    for ch in plan.get("expected_changes") or []:
        if isinstance(ch, dict) and ch.get("kind") in CHANGE_KINDS:
            out.add(ch["kind"])
    if any(isinstance(p, dict) and p.get("kind") == "dep-version" for p in plan.get("probes") or []):
        out.add("deps")
    if plan.get("archetype") == "dependency-upgrade":
        out.update({"deps", "build"})
    return out


# ── YAML (минимальный парсер блочного стиля) ─────────────────────────────────

def _strip_comment(line: str) -> str:
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(raw: str):
    v = raw.strip()
    if not v or v == "~" or v.lower() == "null":
        return None if v.lower() in ("~", "null") else ""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace("''", "'") if v[0] == "'" else inner.replace('\\"', '"')
    return v


def _split_key(text: str):
    """'key: value' → (key, value) с учётом кавычек в ключе; None — не пара."""
    m = re.match(r"""^(?:"([^"]*)"|'([^']*)'|([^:#][^:]*?))\s*:(?:\s+(.*)|\s*)$""", text)
    if not m:
        return None
    key = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
    return key.strip(), (m.group(4) or "")


def parse_yaml_docs(text: str) -> list:
    """Список документов; каждый — плоский dict «a.b.c» → строка (списки — a.b[0])."""
    docs: list = []
    cur: dict = {}
    stack: list = []          # [(indent, prefix, is_item)]
    lines = text.splitlines()
    i = 0
    seq_index: dict = {}      # prefix → следующий индекс элемента списка

    def put(key: str, val):
        cur[key] = "" if val is None else str(val)

    while i < len(lines):
        raw = lines[i]
        i += 1
        if raw.strip() in ("---", "...") and not raw.startswith(" "):
            if cur or docs:
                docs.append(cur)
            cur, stack, seq_index = {}, [], {}
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        is_item = body.startswith("- ") or body == "-"
        if is_item:
            # Элемент списка может стоять на отступе своего ключа (`key:\n- a`): ключ на том же
            # отступе — родитель, а не сосед; соседний элемент того же отступа — снимаем.
            while stack and (stack[-1][0] > indent or (stack[-1][0] == indent and stack[-1][2])):
                stack.pop()
        else:
            while stack and stack[-1][0] >= indent:
                stack.pop()
        prefix = stack[-1][1] if stack else ""
        if is_item:
            idx = seq_index.get(prefix, 0)
            seq_index[prefix] = idx + 1
            item_key = f"{prefix}[{idx}]"
            rest = body[1:].strip()
            pair = _split_key(rest) if rest else None
            if pair and not (rest.startswith(("'", '"')) and rest.endswith(("'", '"'))):
                k, v = pair
                stack.append((indent, item_key, True))
                full = f"{item_key}.{k}"
                if v.strip():
                    put(full, _scalar(v))
                else:
                    stack.append((indent + 1, full, False))
            elif rest:
                put(item_key, _scalar(rest))
            else:
                stack.append((indent, item_key, True))
            continue
        pair = _split_key(body)
        if not pair:
            continue  # не распознали строку — пропускаем, а не падаем
        k, v = pair
        full = f"{prefix}.{k}" if prefix else k
        vs = v.strip()
        if vs in ("|", ">", "|-", ">-", "|+", ">+"):
            block: list = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= indent:
                    break
                block.append(nxt.strip())
                i += 1
            joiner = "\n" if vs.startswith("|") else " "
            put(full, joiner.join(block).strip())
        elif vs:
            put(full, _scalar(vs))
        else:
            stack.append((indent, full, False))
    if cur or not docs:
        docs.append(cur)
    return docs


def parse_properties(text: str) -> dict:
    out: dict = {}
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith(("#", "!"))):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        line = buf + line
        buf = ""
        m = re.match(r"^((?:\\.|[^=:\s])+)\s*[=:\s]\s*(.*)$", line)
        if m:
            out[m.group(1).replace("\\", "")] = m.group(2)
        elif line:
            out[line] = ""
    return out


def _doc_profiles(doc: dict) -> Optional[list]:
    """Профили, для которых активен документ (None — всегда)."""
    for key in ("spring.config.activate.on-profile", "spring.profiles"):
        v = doc.get(key)
        if v:
            return [p.strip().lstrip("!") for p in re.split(r"[,|&]", v) if p.strip()]
    return None


def config_modules(root: Path) -> dict:
    """модуль (каталог над src/main/resources, '.' — корень) → {профиль-файла|'': [пути]}."""
    mods: dict = {}
    for p in _walk(root):
        m = _CONFIG_RE.match(p.name)
        if not m:
            continue
        parts = p.relative_to(root).parts
        if "src" in parts:
            i = parts.index("src")
            if parts[i + 1:i + 3] != ("main", "resources"):
                continue  # src/test/resources — тестовые конфиги не итоговый конфиг
            module = "/".join(parts[:i]) or "."
        else:
            module = "/".join(parts[:-1]) or "."
        mods.setdefault(module, {}).setdefault(m.group(1) or "", []).append(p)
    return mods


def _read_config_file(p: Path) -> list:
    """[(profiles|None, {key: value})] — документы файла."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if p.suffix == ".properties":
        props = parse_properties(text)
        return [(_doc_profiles(props), props)]
    return [(_doc_profiles(d), d) for d in parse_yaml_docs(text)]


_PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _resolve(values: dict) -> dict:
    """`${a.b}` → значение ключа a.b; `${ENV:def}` → def, если такого ключа нет."""
    out = dict(values)
    for _ in range(5):
        changed = False
        for k, v in out.items():
            if "${" not in v:
                continue

            def rep(m):
                name, default = m.group(1).strip(), m.group(2)
                if name in out and "${" not in out[name]:
                    return out[name]
                return default if default is not None else m.group(0)
            nv = _PLACEHOLDER.sub(rep, v)
            if nv != v:
                out[k], changed = nv, True
        if not changed:
            break
    return out


def effective_config(root: Path) -> dict:
    """{'<module>|<profile>|<key>': value} — итоговый конфиг по каждому профилю модуля.

    Профиль `default` — только база (`application.*` + документы без on-profile). Профиль P —
    база, затем `application-P.*` и документы `on-profile: P`, позднее перекрывает раннее."""
    out: dict = {}
    for module, by_file_profile in sorted(config_modules(root).items()):
        base: list = []
        profiled: dict = {}
        for fprof, paths in by_file_profile.items():
            # .properties перекрывает .yml того же уровня (порядок Spring Boot)
            for p in sorted(paths, key=lambda x: (x.suffix == ".properties", str(x))):
                for doc_profiles, values in _read_config_file(p):
                    if fprof:
                        for prof in doc_profiles or [fprof]:
                            profiled.setdefault(prof, []).append(values)
                    elif doc_profiles:
                        for prof in doc_profiles:
                            profiled.setdefault(prof, []).append(values)
                    else:
                        base.append(values)
        merged_base: dict = {}
        for vals in base:
            merged_base.update(vals)
        layers = {"default": merged_base}
        for prof, docs in profiled.items():
            m = dict(merged_base)
            for vals in docs:
                m.update(vals)
            layers[prof] = m
        for prof, vals in layers.items():
            for k, v in _resolve(vals).items():
                out[f"{module}|{prof}|{k}"] = v
    return out


# ── build: объявленные версии ────────────────────────────────────────────────

def _xml_strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _pom_versions(path: Path, relpath: str) -> dict:
    import xml.etree.ElementTree as ET
    out: dict = {}
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError):
        return out
    rootel = tree.getroot()
    for el in rootel.iter():
        el.tag = _xml_strip_ns(el.tag)

    def txt(node, name):
        c = node.find(name)
        return (c.text or "").strip() if c is not None and c.text else ""

    parent = rootel.find("parent")
    if parent is not None and txt(parent, "version"):
        out[f"{relpath}:parent:{txt(parent, 'groupId')}:{txt(parent, 'artifactId')}"] = txt(parent, "version")
    props = rootel.find("properties")
    if props is not None:
        for c in props:
            if c.text and c.text.strip():
                out[f"{relpath}:properties.{c.tag}"] = c.text.strip()
    for section, label in (("dependencies", "dependency"),
                           ("dependencyManagement/dependencies", "managed"),
                           ("build/plugins", "plugin"),
                           ("build/pluginManagement/plugins", "plugin")):
        node = rootel.find(section)
        if node is None:
            continue
        for d in node:
            v = txt(d, "version")
            if v:
                g = txt(d, "groupId") or ("org.apache.maven.plugins" if label == "plugin" else "")
                out[f"{relpath}:{label}.{g}:{txt(d, 'artifactId')}"] = v
    return out


def _toml_versions(path: Path, relpath: str) -> dict:
    out: dict = {}
    section = ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            section = m.group(1).strip()
            continue
        m = re.match(r'^([\w.\-]+)\s*=\s*(.+)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if section == "versions":
            out[f"{relpath}:versions.{key}"] = val.strip('"\'')
        elif section in ("libraries", "plugins"):
            vm = re.search(r'version\s*=\s*"([^"]+)"', val) or re.search(r'"[^":]+:[^":]+:([^"]+)"', val)
            if vm and "version.ref" not in val:
                out[f"{relpath}:{section}.{key}"] = vm.group(1)
    return out


_GRADLE_COORD = re.compile(r"""['"]([\w.\-]+):([\w.\-]+):([\w.\-+\[\]]+)['"]""")
_GRADLE_PLUGIN = re.compile(r"""id\s*\(?\s*['"]([\w.\-]+)['"]\s*\)?\s+version\s+['"]([^'"]+)['"]""")


def _gradle_versions(path: Path, relpath: str) -> dict:
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for m in _GRADLE_COORD.finditer(text):
        out[f"{relpath}:dependency.{m.group(1)}:{m.group(2)}"] = m.group(3)
    for m in _GRADLE_PLUGIN.finditer(text):
        out[f"{relpath}:plugin.{m.group(1)}"] = m.group(2)
    return out


def build_versions(root: Path) -> dict:
    out: dict = {}
    for p in _walk(root):
        if p.name not in _BUILD_FILES:
            continue
        r = rel(root, p)
        if p.name == "pom.xml":
            out.update(_pom_versions(p, r))
        elif p.name == "libs.versions.toml":
            out.update(_toml_versions(p, r))
        elif p.name == "gradle.properties":
            try:
                props = parse_properties(p.read_text(encoding="utf-8"))
            except OSError:
                props = {}
            out.update({f"{r}:{k}": v for k, v in props.items()})
        elif p.name.startswith("build.gradle"):
            out.update(_gradle_versions(p, r))
    return out


# ── deps: резолвнутые версии ─────────────────────────────────────────────────

def build_system(root: Path) -> Optional[str]:
    if (root / "pom.xml").exists():
        return "maven"
    if any((root / n).exists() for n in ("settings.gradle", "settings.gradle.kts",
                                         "build.gradle", "build.gradle.kts")):
        return "gradle"
    return None


def _tool(root: Path, wrapper: str, plain: str) -> list:
    w = root / wrapper
    if w.exists() and os.access(str(w), os.X_OK):
        return [str(w)]
    found = shutil.which(plain)
    if not found:
        raise TechError(f"нет ни ./{wrapper}, ни {plain} в PATH — резолвить зависимости нечем")
    return [found]


_MVN_LINE = re.compile(
    r"^\s*([\w.\-]+):([\w.\-]+):[\w.\-]+(?::[\w.\-]+)?:([\w.\-+]+):"
    r"(?:compile|runtime|provided|test|system|import)\b")
_GRADLE_LINE = re.compile(r"[+\\]---\s+([\w.\-]+):([\w.\-]+)(?::([\w.\-+]+))?(?:\s+->\s+([\w.\-+]+))?")


def parse_maven_list(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        m = _MVN_LINE.match(line)
        if m:
            out.setdefault(f"{m.group(1)}:{m.group(2)}", set()).add(m.group(3))
    return out


def parse_gradle_tree(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        m = _GRADLE_LINE.search(line)
        if not m:
            continue
        ver = m.group(4) or m.group(3)
        if ver:
            out.setdefault(f"{m.group(1)}:{m.group(2)}", set()).add(ver)
    return out


def _gradle_projects(root: Path) -> list:
    projects = [""]
    for name in ("settings.gradle", "settings.gradle.kts"):
        p = root / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"include\s*\(?([^)\n]+)\)?", text):
                for q in re.findall(r"""['"]([^'"]+)['"]""", m.group(1)):
                    projects.append(q if q.startswith(":") else f":{q}")
    return projects


def resolve_deps(root: Path, timeout: int = 1800) -> dict:
    """{'group:artifact': 'v1[,v2]'} резолвнутого дерева. Отказ резолва — TechError."""
    system = build_system(root)
    if system is None:
        raise TechError("не найден ни pom.xml, ни settings.gradle/build.gradle — система сборки неизвестна")
    found: dict = {}
    if system == "maven":
        cmd = _tool(root, "mvnw", "mvn")
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "deps.txt"
            argv = cmd + ["-q", "-B", "dependency:list", f"-DoutputFile={out_file}",
                          "-DappendOutput=true"]
            r = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
            if r.returncode != 0:
                raise TechError(f"`{' '.join(argv[1:4])}` упал (rc={r.returncode}): "
                                f"{(r.stderr or r.stdout).strip()[-800:]}")
            text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
            found = parse_maven_list(text + "\n" + r.stdout)
    else:
        cmd = _tool(root, "gradlew", "gradle")
        for proj in _gradle_projects(root):
            task = f"{proj}:dependencies" if proj else "dependencies"
            argv = cmd + ["-q", task, "--configuration", "runtimeClasspath"]
            r = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
            if r.returncode != 0:
                raise TechError(f"`gradle {task}` упал (rc={r.returncode}): "
                                f"{(r.stderr or r.stdout).strip()[-800:]}")
            for k, v in parse_gradle_tree(r.stdout).items():
                found.setdefault(k, set()).update(v)
    if not found:
        raise TechError("резолв зависимостей вернул пустой список — проверь вывод сборки")
    return {k: ",".join(sorted(v)) for k, v in sorted(found.items())}


# ── контракты (сканеры system-analyst) ───────────────────────────────────────

def _scanners():
    sa = str(_SKILLS / "system-analyst" / "scripts")
    if sa not in sys.path:
        sys.path.insert(0, sa)
    import db  # noqa: E402
    import endpoints  # noqa: E402
    import kafka  # noqa: E402
    return endpoints, kafka, db


def api_contracts(root: Path) -> list:
    endpoints, _, _ = _scanners()
    out = set()
    for ctrl, ep in endpoints.iter_endpoints(endpoints.scan(root)):
        params = ",".join(f"{p.kind}:{p.name}:{p.type}" for p in ep.params)
        out.add(f"{ep.http_method} {ep.path} ({params}) -> {ep.return_type}")
    return sorted(out)


def kafka_contracts(root: Path) -> list:
    _, kafka, _ = _scanners()
    model = kafka.scan(root)
    out = {f"in {t}" for c in model.consumers for t in c.topics}
    out |= {f"out {t}" for p in model.producers for t in p.topics}
    return sorted(out)


def db_contracts(root: Path) -> list:
    _, _, db = _scanners()
    return sorted(db.scan(root).get("tables") or [])


def capture(root: Path, classes: set) -> dict:
    """Снять классы. deps — только по запросу: это запуск сборки."""
    out: dict = {}
    if "config" in classes:
        out["config"] = effective_config(root)
    if "build" in classes:
        out["build"] = build_versions(root)
    if "deps" in classes:
        out["deps"] = resolve_deps(root)
    if "api" in classes:
        out["api"] = api_contracts(root)
    if "kafka" in classes:
        out["kafka"] = kafka_contracts(root)
    if "db" in classes:
        out["db"] = db_contracts(root)
    return out


# ── Пробы ────────────────────────────────────────────────────────────────────

def run_probe(root: Path, probe: dict, deps: Optional[dict] = None) -> tuple:
    """(ok, detail). ok — проба выполнена (целевое состояние достигнуто)."""
    kind = probe.get("kind")
    if kind in ("pattern-absent", "pattern-present"):
        rx = re.compile(probe["regex"])
        hits = []
        for p in _walk(root):
            r = rel(root, p)
            if not fnmatch.fnmatch(r, probe["glob"]):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{r}:{n}")
        if kind == "pattern-absent":
            return (not hits, f"найдено {len(hits)}: {', '.join(hits[:5])}" if hits else "совпадений нет")
        return (bool(hits), f"найдено {len(hits)}" if hits else "совпадений нет")
    if kind in ("file-absent", "file-present"):
        exists = (root / probe["path"]).exists()
        return ((not exists) if kind == "file-absent" else exists,
                f"{probe['path']} {'есть' if exists else 'нет'}")
    if kind == "dep-version":
        if deps is None:
            return False, "deps не сняты"
        have = deps.get(probe["coord"])
        want = str(probe["expect"])
        ok = have is not None and all(fnmatch.fnmatch(v, want) for v in have.split(","))
        return ok, f"{probe['coord']} = {have or 'нет в дереве'} (ожидается {want})"
    return False, f"неизвестный kind {kind}"


# ── Цели и diff ──────────────────────────────────────────────────────────────

def _config_matches(ch: dict, classes: dict) -> list:
    """Ключи config-класса, к которым относится цель (module опционален)."""
    cfg = classes.get("config", {})
    prof = ch.get("profile") or "default"
    key = ch.get("key")
    if ch.get("module"):
        return [f"{ch['module']}|{prof}|{key}"]
    mods = sorted({k.split("|", 1)[0] for k in cfg if k.endswith(f"|{prof}|{key}")})
    if not mods:
        mods = sorted({k.split("|", 1)[0] for k in cfg}) or ["."]
        if len(mods) > 1:
            return []  # ключа нет нигде, а модулей несколько — module обязан быть указан
    return [f"{m}|{prof}|{key}" for m in mods]


def target_keys(ch: dict, classes: dict) -> list:
    kind = ch.get("kind")
    if kind == "config":
        return _config_matches(ch, classes)
    if kind == "deps":
        return [ch.get("coord")]
    if kind == "build":
        return [ch.get("key")]
    return []


def _norm(v):
    return None if v is None else str(v)


def check_from(plan: dict, snap: dict) -> list:
    """Для каждой цели «было» в плане == значению снимка. Ошибки — список строк."""
    errs = []
    for ch in plan.get("expected_changes") or []:
        if not is_target_change(ch):
            continue
        keys = target_keys(ch, snap)
        if not keys:
            errs.append(f"{change_id(ch)}: ключ не найден ни в одном модуле, а модулей несколько — "
                        f"укажи module")
            continue
        for k in keys:
            have = snap.get(ch["kind"], {}).get(k)
            if _norm(have) != _norm(ch.get("from")):
                errs.append(f"{change_id(ch)} [{k}]: в плане «было» = {ch.get('from')!r}, а сейчас "
                            f"{have!r} — план не сходится с текущим состоянием")
            elif _norm(ch.get("from")) == _norm(ch.get("to")):
                errs.append(f"{change_id(ch)} [{k}]: «было» уже равно «стало» ({have!r}) — задача "
                            f"выполнена или выбран не тот ключ/профиль")
    return errs


def check_to(plan: dict, now: dict) -> list:
    errs = []
    for ch in plan.get("expected_changes") or []:
        if not is_target_change(ch):
            continue
        for k in target_keys(ch, now) or ["?"]:
            have = now.get(ch["kind"], {}).get(k)
            if _norm(have) != _norm(ch.get("to")):
                errs.append(f"{change_id(ch)} [{k}]: ожидается {ch.get('to')!r}, сейчас {have!r}")
    return errs


def diff_class(before, after) -> list:
    """[(ключ, было, стало)] для dict-классов; [(элемент, есть|нет, есть|нет)] — для списков."""
    if isinstance(before, list) or isinstance(after, list):
        b, a = set(before or []), set(after or [])
        return [(x, "есть", "нет") for x in sorted(b - a)] + [(x, "нет", "есть") for x in sorted(a - b)]
    before, after = before or {}, after or {}
    out = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            out.append((k, before.get(k), after.get(k)))
    return out


def declared(plan: dict, kind: str, key: str, classes: dict) -> str:
    """'target' | 'match' | '' — заявлено ли изменение ключа класса в плане."""
    for ch in plan.get("expected_changes") or []:
        if not isinstance(ch, dict) or ch.get("kind") != kind:
            continue
        if "match" in ch:
            pat = ch["match"]
            subject = key.split("|", 2)[-1] if kind == "config" else key
            if fnmatch.fnmatch(subject, pat) or fnmatch.fnmatch(key, pat):
                return "match"
        elif key in target_keys(ch, classes):
            return "target"
    return ""


def undeclared(plan: dict, before: dict, after: dict, classes: set) -> dict:
    """{класс: [(ключ, было, стало)]} — изменения вне целей и match-областей."""
    out: dict = {}
    for cls in sorted(classes):
        rows = []
        for k, b, a in diff_class(before.get(cls), after.get(cls)):
            if cls in CHANGE_KINDS and declared(plan, cls, k, after if a is not None else before):
                continue
            rows.append((k, b, a))
        if rows:
            out[cls] = rows
    return out


# ── Снимок и evidence ────────────────────────────────────────────────────────

def git_dirty(root: Path, paths: list) -> list:
    """Пути из списка, изменённые относительно HEAD (или неотслеживаемые). Не git — TechError."""
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", *paths],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise TechError(f"git status не выполнился: {(r.stderr or '').strip()[:300]} — снимок "
                        f"«до» требует git-репозитория")
    dirty = []
    for line in r.stdout.splitlines():
        if len(line) > 3:
            dirty.append(line[3:].strip().strip('"'))
    return dirty


def git_head(root: Path) -> str:
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def last_snapshot(root: Path, feature: str) -> Optional[dict]:
    """Последняя аутентичная запись tech-snapshot прогона, чей файл цел (sha совпадает)."""
    import forge_events as FE
    found = None
    for rec in FE.read_events(root, SKILL, feature):
        if FE._authentic(rec, "tech-snapshot"):
            found = rec
    if found is None:
        return None
    p = root / str(found.get("path") or "")
    try:
        if sha256_file(p) != found.get("sha256"):
            return None
    except OSError:
        return None
    return found


def load_snapshot(root: Path, feature: str) -> dict:
    rec = last_snapshot(root, feature)
    if rec is None:
        raise TechError("нет снимка «до» (или файл снимка подменён) — первым действием tech-apply "
                        "должен быть `check_invariants.py snapshot`")
    return json.loads((root / rec["path"]).read_text(encoding="utf-8"))


def write_state_json(root: Path, feature: str, name: str, data: dict) -> Path:
    d = snapshot_dir(root, feature)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    tmp = p.with_name(f"{name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    return p
