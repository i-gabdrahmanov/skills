"""ds_requirements.py — daiver_ds_requirements: требования дизайн-системы из stories/исходников (спека §5.3, §20.4;
INV-A5, A6; QG U8, S4).

Строит ДВА коммитимых документа в `.daiver/` (имена зафиксированы, outPath инструмент не принимает):
  design-system-requirements.json          КОНТРАКТ: компонент → title/taxonomy → пропы (kind, options, required) →
                                           usedValues → import (bare specifier) → число историй; без прозы.
  design-system-requirements.details.json  КАК ИСПОЛЬЗОВАТЬ: описание, проза MDX, описания и дефолты пропов,
                                           deprecations (+замена), истории как JSX-примеры.
Только при `library.source` ∈ stories | sources: при storybook-mcp документы не строятся НИКОГДА (сервер и есть
требования, производная копия протухла бы) — отказ, а не пустая запись (S4). Ноль компонентов — ошибка с названной
причиной («сканпути неверны»), а не пустой контракт.

Контракт для потребителей (2.3 ds_inventory/ds_lookup/ds_details, 4.5 DS-гейт) — самопроверяемый:
  • формат версионирован (`daiver.schema`), строго валидируется (`validate_contract/details/pair`: неизвестное
    поле — ошибка, `options` ровно у kind=enum, import — bare specifier);
  • `source.sha256` — хеш файлов, из которых построено (путь + sha256 содержимого, по порядку путей); у пары
    документов он одинаков. `check()` пересчитывает его и отвечает ok | stale | invalid | missing — «устарело»
    определяется, а не угадывается (сверх спеки: там пересборка только вручную);
  • детерминизм: один вход → побайтно один выход (ключи отсортированы, порядок файлов — по пути, ни времени, ни путей
    машины в документе).
Скан ограничен: не более MAX_FILES подходящих файлов (больше — отказ «сузьте scanPaths», не молчаливая обрезка),
файлы крупнее MAX_FILE_BYTES пропускаются с названной причиной; сверх спеки — MAX_TOTAL_BYTES на весь скан (метод
синхронный, зависать он не должен). Пути — только внутри репо; симлинк наружу не раскрывается.

Движки разбора: `python` (по умолчанию, ничего кроме stdlib: `daiver/ds/*`) и необязательный `node` (TypeScript репо,
`assets/ds-scan.mjs`) — явным аргументом, без молчаливого отката: документ записывает, чем построен (`source.scanner`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import VERSION
from . import canon as C
from . import config as CFG
from .ds import csf, decl, facts as F, interp, mdx, merge

CONTRACT_REL = Path(".daiver") / "design-system-requirements.json"
DETAILS_REL = Path(".daiver") / "design-system-requirements.details.json"
SCHEMA = 1
ROLE = "ds-requirements"
SCANNER_PY = "py/1"
SCANNER_NODE = "node/1"
SCANNERS = (SCANNER_PY, SCANNER_NODE)

MAX_FILES = 5000                        # спека §20.4
MAX_FILE_BYTES = 512 * 1024             # спека §20.4
MAX_TOTAL_BYTES = 32 * 1024 * 1024      # сверх спеки
MAX_VISITED = 200000
MAX_SKIPPED_SHOWN = 50

SKIP_DIRS = frozenset(("node_modules", ".git", ".daiver", ".gigacode", "storybook-static", "__snapshots__",
                       "coverage"))
_STORY = re.compile(r"\.stories\.(?:js|jsx|ts|tsx|mjs|cjs|mts|cts)$")
_NOT_SOURCE = re.compile(r"\.(?:test|spec|e2e|stories)\.[a-z]+$|\.d\.ts$|\.config\.[a-z]+$")


class DsError(ValueError):
    """Вызов или скан не годятся — сообщение называет причину и что делать."""


def kind_of(name: str, mode: str) -> Optional[str]:
    """Вид файла для сканера или None. stories | mdx | types (.d.ts) | source (.ts/.tsx — только sources)."""
    if name.endswith(".mdx"):
        return "mdx"
    if _STORY.search(name):
        return "stories"
    if name.endswith(".d.ts"):
        return "types"
    if mode == "sources" and name.endswith((".ts", ".tsx")) and not _NOT_SOURCE.search(name):
        return "source"
    return None


def scan_root(root: Path, rel: str) -> Path:
    """Скан-путь конфига → абсолютный внутри репо (относительный без `..`, либо абсолютный под корнем)."""
    p = Path(rel)
    if ".." in p.parts or rel.startswith("~"):
        raise DsError(f"scanPaths «{rel}»: только внутри репо, без «..»")
    full = (p if p.is_absolute() else Path(root) / p)
    try:
        real = full.resolve()
        real.relative_to(Path(root).resolve())
    except (ValueError, OSError):
        raise DsError(f"scanPaths «{rel}»: путь выходит за корень репо")
    if not real.exists():
        raise DsError(f"scanPaths «{rel}»: пути нет на диске — поправьте library.scanPaths в .daiver/config.yaml")
    return real


def discover(root: Path, scan_paths: List[str], mode: str
             ) -> Tuple[List[Tuple[str, str]], List[Dict[str, str]]]:
    """([(путь от корня, вид)] по возрастанию пути, пропущенное [{path, reason}]). Симлинк, ведущий за пределы репо,
    пропускается (`outside-repo`); вложенный node_modules/.git и точечные каталоги не раскрываются, если они не
    названы в scanPaths явно."""
    base = Path(root).resolve()
    found: Dict[str, str] = {}
    skipped: List[Dict[str, str]] = []
    seen_dirs = set()
    visited = 0

    def rel(p: Path) -> str:
        return p.relative_to(base).as_posix()

    for sp in scan_paths:
        start = scan_root(root, sp)
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur.is_file():
                k = kind_of(cur.name, mode)
                if k:
                    found[rel(cur)] = k
                elif cur is start:
                    skipped.append({"path": rel(cur), "reason": "unsupported"})
                continue
            if cur in seen_dirs:
                continue
            seen_dirs.add(cur)
            try:
                entries = sorted(os.scandir(str(cur)), key=lambda e: e.name)
            except OSError:
                skipped.append({"path": rel(cur), "reason": "unreadable"})
                continue
            for e in entries:
                visited += 1
                if visited > MAX_VISITED:
                    raise DsError(f"просмотрено больше {MAX_VISITED} записей каталога: scanPaths слишком широки — "
                                  f"укажите каталоги библиотеки, а не корень репо")
                real = Path(e.path).resolve()
                try:
                    real.relative_to(base)
                except ValueError:
                    skipped.append({"path": rel(Path(e.path)), "reason": "outside-repo"})
                    continue
                if e.is_dir():
                    if e.name in SKIP_DIRS or e.name.startswith("."):
                        continue
                    stack.append(real)
                elif real.is_file():
                    k = kind_of(e.name, mode)
                    if k:
                        found[rel(real)] = k
    if len(found) > MAX_FILES:
        raise DsError(f"подходящих файлов {len(found)} > {MAX_FILES}: сузьте library.scanPaths до каталогов "
                      f"библиотеки (скан не обрезается молча — неполный контракт хуже остановки)")
    return sorted(found.items()), sorted(skipped, key=lambda s: (s["path"], s["reason"]))


class Entry:
    __slots__ = ("path", "kind", "text", "sha")

    def __init__(self, path: str, kind: str, text: str, sha: str):
        self.path, self.kind, self.text, self.sha = path, kind, text, sha


def read_entries(root: Path, cands: List[Tuple[str, str]], skipped: List[Dict[str, str]]) -> List[Entry]:
    """Читает кандидатов: крупнее MAX_FILE_BYTES и не-UTF-8 пропускаются с причиной; общий объём ограничен."""
    out: List[Entry] = []
    total = 0
    for path, kind in cands:
        full = Path(root).resolve() / path
        try:
            size = full.stat().st_size
        except OSError:
            skipped.append({"path": path, "reason": "unreadable"})
            continue
        if size > MAX_FILE_BYTES:
            skipped.append({"path": path, "reason": "too-large"})
            continue
        total += size
        if total > MAX_TOTAL_BYTES:
            raise DsError(f"объём файлов скана превысил {MAX_TOTAL_BYTES // (1024 * 1024)} МБ: сузьте "
                          f"library.scanPaths до каталогов библиотеки")
        try:
            # CRLF → LF до хеша и разбора: один и тот же репо на Windows (autocrlf) и Linux даёт те же документы и хеш
            text = full.read_bytes().decode("utf-8").replace("\r\n", "\n")
            data = text.encode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"path": path, "reason": "not-utf8"})
            continue
        except OSError:
            skipped.append({"path": path, "reason": "unreadable"})
            continue
        out.append(Entry(path, kind, text, hashlib.sha256(data).hexdigest()))
    skipped.sort(key=lambda s: (s["path"], s["reason"]))
    return out


def sources_hash(entries: List[Entry]) -> str:
    """Хеш источников: путь + sha256 содержимого по возрастанию пути. Меняется при правке, добавлении, удалении
    и переименовании любого просканированного файла."""
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda x: x.path):
        h.update(f"{e.path}\t{e.sha}\n".encode("utf-8"))
    return h.hexdigest()


# ── сериализация ─────────────────────────────────────────────────────────────

def dumps(doc: Any) -> str:
    """JSON для ревью в git: ключи по алфавиту, объект — строка на поле, массив скаляров — в строку. Один вход —
    одни байты."""
    return _fmt(doc, 0) + "\n"


def _fmt(v: Any, level: int) -> str:
    pad = "  " * level
    if isinstance(v, dict):
        if not v:
            return "{}"
        rows = [f"{pad}  {json.dumps(str(k), ensure_ascii=False)}: {_fmt(v[k], level + 1)}" for k in sorted(v)]
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in v):
            return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
        return "[\n" + ",\n".join(f"{pad}  {_fmt(x, level + 1)}" for x in v) + "\n" + pad + "]"
    return json.dumps(v, ensure_ascii=False)


# ── схема и валидаторы ───────────────────────────────────────────────────────

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _null() -> C.Check:
    def check(v, path, errs):
        if v is not None:
            errs.add(path, f"ожидается null, получено {C._typename(v)}")
    return check


def _const(value: Any) -> C.Check:
    def check(v, path, errs):
        if v != value or type(v) is not type(value):
            errs.add(path, f"ожидается {value!r}, получено {v!r}")
    return check


def _sha() -> C.Check:
    def check(v, path, errs):
        if not (isinstance(v, str) and _HEX64.match(v)):
            errs.add(path, "ожидается sha256 (64 символа hex в нижнем регистре)")
    return check


def _scalar() -> C.Check:
    return C.c_any_of("строка, число или булево", C.c_str(), C.c_num(), C.c_bool())


def _str_or_null() -> C.Check:
    return C.c_any_of("строка или null", C.c_str(), _null())


def _envelope(doc_kind: str) -> C.Check:
    return C.c_obj({"version": (C.c_str(True), True), "role": (_const(ROLE), True),
                    "doc": (_const(doc_kind), True), "schema": (_const(SCHEMA), True)})


_C_PROP = C.c_obj({"kind": (C.c_enum(*interp.KINDS), True),
                   "from": (C.c_enum("types", "argTypes", "usage"), True),
                   "options": (C.c_list(_scalar()), False), "required": (_const(True), False)})
_C_IMPORT = C.c_any_of("null или {path, named | default}", _null(), C.c_obj({
    "path": (C.c_str(True), True), "named": (C.c_str(True), False), "default": (_const(True), False)}))
_C_COMP = C.c_obj({
    "title": (_str_or_null(), True), "taxonomy": (C.c_list(C.c_str()), True),
    "props": (C.c_map(_C_PROP), True), "propsClosed": (C.c_bool(), True),
    "usedValues": (C.c_map(C.c_list(_scalar())), True), "import": (_C_IMPORT, True),
    "stories": (C.c_int(0), True), "usedCapped": (C.c_list(C.c_str()), False)})
_C_SOURCE = C.c_obj({
    "librarySource": (C.c_enum("stories", "sources"), True), "scanPaths": (C.c_list(C.c_str(True)), True),
    "scanner": (C.c_enum(*SCANNERS), True), "sha256": (_sha(), True), "files": (C.c_int(0), True),
    "skipped": (C.c_list(C.c_obj({"path": (C.c_str(True), True), "reason": (C.c_str(True), True)})), True)})
_CONTRACT = C.c_obj({"daiver": (_envelope("contract"), True), "source": (_C_SOURCE, True),
                     "components": (C.c_map(_C_COMP), True)})

_D_COMP = C.c_obj({
    "description": (_str_or_null(), True), "docs": (_str_or_null(), True),
    "props": (C.c_map(C.c_obj({"description": (C.c_str(True), False), "default": (_scalar(), False)})), True),
    "deprecations": (C.c_list(C.c_obj({
        "target": (C.c_enum("component", "prop"), True), "name": (C.c_str(True), True),
        "message": (C.c_str(), True), "replacement": (_str_or_null(), True)})), True),
    "examples": (C.c_list(C.c_obj({"name": (C.c_str(True), True), "jsx": (C.c_str(True), True)})), True)})
_DETAILS = C.c_obj({"daiver": (_envelope("details"), True),
                    "source": (C.c_obj({"sha256": (_sha(), True)}), True),
                    "components": (C.c_map(_D_COMP), True)})


def _run(check: C.Check, doc: Any) -> List[str]:
    errs = C._Errs()
    check(doc, "$", errs)
    return errs.result()


def validate_contract(doc: Any) -> List[str]:
    """Нарушения контракта (пусто — валиден): схема + смысл (options ровно у enum, import — bare specifier,
    хотя бы один компонент)."""
    problems = _run(_CONTRACT, doc)
    if problems:
        return problems
    out: List[str] = []
    if not doc["components"]:
        out.append("$.components: пусто — ноль компонентов не контракт (сканпути неверны)")
    for name, comp in doc["components"].items():
        if not name.strip():
            out.append("$.components: пустое имя компонента")
        for pn, prop in comp["props"].items():
            where = f"$.components.{name}.props.{pn}"
            opts = prop.get("options")
            if (prop["kind"] == "enum") != (opts is not None):
                out.append(f"{where}: options обязательны у kind=enum и запрещены у остальных")
            elif opts is not None and (not opts or len(opts) != len(interp.dedupe(opts))):
                out.append(f"{where}: options пусты или с повторами")
        imp = comp["import"]
        if imp is not None:
            where = f"$.components.{name}.import"
            if ("named" in imp) == ("default" in imp):
                out.append(f"{where}: нужно ровно одно из named | default")
            if not merge.is_bare(imp["path"]):
                out.append(f"{where}: path «{imp['path']}» не bare specifier (относительный путь/алиас)")
        for pn, vals in comp["usedValues"].items():
            if not vals:
                out.append(f"$.components.{name}.usedValues.{pn}: пустой список")
    return out


def validate_details(doc: Any) -> List[str]:
    return _run(_DETAILS, doc)


def validate_pair(contract: Any, details: Any) -> List[str]:
    """Пара документов согласована: один хеш источников, подробности — только о компонентах контракта, deprecation
    проп — проп контракта."""
    if validate_contract(contract) or validate_details(details):
        return ["пара не проверяется: документы невалидны"]
    out: List[str] = []
    if contract["source"]["sha256"] != details["source"]["sha256"]:
        out.append("хеши источников контракта и подробностей различаются — пара собрана не за один прогон")
    for name, d in details["components"].items():
        if name not in contract["components"]:
            out.append(f"details: компонента «{name}» нет в контракте")
            continue
        props = contract["components"][name]["props"]
        for dep in d["deprecations"]:
            if dep["target"] == "prop" and dep["name"] not in props:
                out.append(f"details.{name}: deprecated-проп «{dep['name']}» вне контракта")
        for pn in d["props"]:
            if pn not in props:
                out.append(f"details.{name}.props.{pn}: пропа нет в контракте")
    return out


# ── сбор ─────────────────────────────────────────────────────────────────────

PARSE_ERRORS = (RecursionError, IndexError, KeyError, TypeError, AttributeError, ValueError)


def scan_py(entry: Entry) -> Tuple[Dict[str, Any], bool]:
    """Факты файла основным путём; сбой разбора одного файла не валит скан (NameError и прочие баги кода — валят)."""
    try:
        if entry.kind == "stories":
            return csf.scan(entry.text, entry.path), True
        if entry.kind == "mdx":
            return mdx.scan(entry.text, entry.path), True
        return decl.scan(entry.text, entry.path, entry.kind), True
    except PARSE_ERRORS as e:
        facts = F.empty_facts(entry.path, entry.kind)
        facts["warnings"].append(f"файл не разобран ({type(e).__name__}) — пропущен")
        return facts, False


def _library(root: Path) -> Tuple[str, List[str]]:
    """(library.source, scanPaths) или отказ: проект не инициализирован / источник не файловый."""
    cfg = CFG.load(Path(root))
    if not cfg["initialized"]:
        why = "; ".join(cfg["problems"]) or "конфиг не разобран"
        raise DsError(f"проект не инициализирован ({why}) — сначала daiver_init")
    lib = cfg["config"]["library"]
    if lib["source"] == "storybook-mcp":
        raise DsError(f"не строится: library.source = storybook-mcp — сервер сторибука «{lib['storybookMcp']}» и есть "
                      f"источник требований, производная копия протухла бы (INV-A6). Файловые документы нужны только "
                      f"при library.source: stories | sources")
    return lib["source"], list(lib["scanPaths"])


def _zero_message(entries: List[Entry], scan_paths: List[str], skipped: List[Dict[str, str]]) -> str:
    n: Dict[str, int] = {}
    for e in entries:
        n[e.kind] = n.get(e.kind, 0) + 1
    big = sum(1 for s in skipped if s["reason"] == "too-large")
    return (f"в scanPaths ({', '.join(scan_paths)}) не найдено ни одного компонента: просмотрено файлов "
            f"{len(entries)} (stories {n.get('stories', 0)}, mdx {n.get('mdx', 0)}, .d.ts {n.get('types', 0)}, "
            f"исходников {n.get('source', 0)}), пропущено слишком крупных {big}. Сканпути неверны: bundled dist/*.js "
            f"без .d.ts и сторей сигнатур не несёт — укажите в library.scanPaths (.daiver/config.yaml) каталоги со "
            f"stories или с .d.ts / исходниками библиотеки")


def _write(root: Path, rel: Path, text: str) -> bool:
    """Атомарная запись; False — на диске уже те же байты."""
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return True


def documents(source: str, scan_paths: List[str], scanner: str, sha: str, n_files: int,
              skipped: List[Dict[str, str]], comps: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    env = lambda doc: {"version": VERSION, "role": ROLE, "doc": doc, "schema": SCHEMA}      # noqa: E731
    contract = {"daiver": env("contract"),
                "source": {"librarySource": source, "scanPaths": scan_paths, "scanner": scanner, "sha256": sha,
                           "files": n_files, "skipped": skipped[:MAX_SKIPPED_SHOWN]},
                "components": {n: v["contract"] for n, v in comps.items()}}
    details = {"daiver": env("details"), "source": {"sha256": sha},
               "components": {n: v["details"] for n, v in comps.items() if v["details"]}}
    return contract, details


def build(root: Path, engine: str = "python") -> Dict[str, Any]:
    """Скан → слияние → два документа в `.daiver/`. Ответ короткий: счётчики, предупреждения, что записано."""
    root = Path(root)
    if engine not in ("python", "node"):
        raise DsError(f"engine: «{engine}» — ожидается python | node")
    source, scan_paths = _library(root)
    cands, skipped = discover(root, scan_paths, source)
    entries = read_entries(root, cands, skipped)
    ts_version = None
    if engine == "node":
        from . import ds_node
        facts_list, parse_errors, ts_version = ds_node.scan(root, entries)
        scanner = SCANNER_NODE
    else:
        pairs = [scan_py(e) for e in entries]
        facts_list, parse_errors = [f for f, _ in pairs], sum(1 for _, ok in pairs if not ok)
        scanner = SCANNER_PY
    merged = merge.merge(facts_list)
    comps = merged["components"]
    if not comps:
        raise DsError(_zero_message(entries, scan_paths, skipped))
    sha = sources_hash(entries)
    contract, details = documents(source, scan_paths, scanner, sha, len(entries), skipped, comps)
    bad = validate_contract(contract) + validate_details(details) + validate_pair(contract, details)
    if bad:
        raise DsError("внутренняя ошибка: собранные документы не прошли собственную проверку — " + "; ".join(bad[:5]))
    changed = [_write(root, CONTRACT_REL, dumps(contract)), _write(root, DETAILS_REL, dumps(details))]
    comp_docs = contract["components"]
    warnings = merged["warnings"]
    extra = {"typescript": ts_version} if ts_version else {}
    return {
        **extra, "ok": True, "changed": any(changed),
        "written": [CONTRACT_REL.as_posix(), DETAILS_REL.as_posix()],
        "scanner": scanner, "librarySource": source, "sha256": sha[:12],
        "files": len(entries), "skipped": skipped[:10], "parseErrors": parse_errors,
        "components": len(comp_docs), "stories": sum(c["stories"] for c in comp_docs.values()),
        "withDetails": len(details["components"]),
        "withDeprecations": sum(1 for d in details["components"].values() if d["deprecations"]),
        "propsClosed": sum(1 for c in comp_docs.values() if c["propsClosed"]),
        "names": sorted(comp_docs)[:20],
        "warnings": warnings[:20] + ([f"… и ещё {len(warnings) - 20}"] if len(warnings) > 20 else []),
        "next": "документы коммитятся; после обновления библиотеки перезапусти daiver_ds_requirements вручную "
                "(проверка устаревания — daiver_ds_requirements с check:true)"}


# ── проверка ─────────────────────────────────────────────────────────────────

def _load_doc(path: Path) -> Tuple[Any, Optional[str]]:
    if not path.is_file():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, f"не читается как JSON ({type(e).__name__})"


def load_pair(root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[str]]:
    """Прочитать и проверить пару документов (без скана): (контракт, подробности, проблемы). Проблемы пусты — оба
    документа валидны и согласованы (схема, смысл, один хеш источников). Нет файла / не JSON / не прошёл проверку —
    проблемы названы, документы не отдаются (None): потребитель не работает по «почти контракту». Только читает."""
    root = Path(root)
    contract, e1 = _load_doc(root / CONTRACT_REL)
    details, e2 = _load_doc(root / DETAILS_REL)
    problems = []
    for rel, e in ((CONTRACT_REL, e1), (DETAILS_REL, e2)):
        if e == "missing":
            problems.append(f"нет {rel.as_posix()}")
        elif e:
            problems.append(f"{rel.as_posix()}: {e}")
    if not problems:
        problems = ([f"контракт: {p}" for p in validate_contract(contract)]
                    + [f"details: {p}" for p in validate_details(details)])
    if not problems:
        problems = validate_pair(contract, details)
    if problems:
        return None, None, problems
    return contract, details, []


def check(root: Path, recompute: bool = True) -> Dict[str, Any]:
    """Состояние пары документов: not-needed | unconfigured | missing | invalid | stale | ok. Только читает.
    stale — хеш источников, scanPaths или library.source изменились с момента сборки (пересчёт читает файлы
    библиотеки; recompute=False пропускает его)."""
    root = Path(root)
    cfg = CFG.load(root)
    if not cfg["initialized"]:
        return {"state": "unconfigured", "problems": cfg["problems"], "reasons": []}
    lib = cfg["config"]["library"]
    if lib["source"] == "storybook-mcp":
        return {"state": "not-needed", "problems": [], "reasons": ["library.source = storybook-mcp"]}
    contract, details, problems = load_pair(root)
    if problems:
        missing = [p for p in problems if p.startswith("нет ")]
        if missing:
            return {"state": "missing", "problems": [], "reasons": missing}
        return {"state": "invalid", "problems": problems[:20], "reasons": []}
    out = {"state": "ok", "problems": [], "reasons": [], "components": len(contract["components"]),
           "scanner": contract["source"]["scanner"], "sha256": contract["source"]["sha256"][:12]}
    src = contract["source"]
    if src["librarySource"] != lib["source"]:
        out["reasons"].append(f"library.source изменился: {src['librarySource']} → {lib['source']}")
    if src["scanPaths"] != list(lib["scanPaths"]):
        out["reasons"].append("library.scanPaths изменились")
    if recompute and not out["reasons"]:
        try:
            cands, skipped = discover(root, list(lib["scanPaths"]), lib["source"])
            sha = sources_hash(read_entries(root, cands, skipped))
        except DsError as e:
            out["reasons"].append(str(e))
        else:
            if sha != src["sha256"]:
                out["reasons"].append("файлы библиотеки изменились с момента сборки (хеш источников не совпал)")
    if out["reasons"]:
        out["state"] = "stale"
    return out
