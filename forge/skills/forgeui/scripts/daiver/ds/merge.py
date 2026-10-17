"""merge.py — факты файлов → компоненты: слияние CSF + MDX + деклараций типов (спека §5.3, QG U8).

Одна точка, где решается, что видят потребители (2.3 lookup, 4.5 DS-гейт), поэтому правила здесь явные:

Что такое компонент и откуда его имя
  • группа stories (CSF/storiesOf/<Meta>) → `meta.component`; нет — JSX-корень историй, совпавший с последним
    сегментом `title`; нет — единственный JSX-корень всех историй; иначе группа не привязана (предупреждение);
  • экспортированная привязка из деклараций/исходников (`const X: FC<P>`, `function X(props: P)`, класс);
  • имя через пространство имён (`Ui.Button` при `import * as Ui`) — имя без префикса, import — из `Ui`.
Один компонент = одно имя: повторы сливаются (предупреждение, если у групп разные title).

Прописание контракта пропа (kind/options) — по убыванию доверия:
  types (TS-тип: закрытый набор литералов, примитив, слот, функция) → argTypes (options, control, type, сводка
  table.type) → usage (вид по значениям args/JSX). Узкие options из argTypes применяются, когда TS-тип «просто
  string/number». `from` в записи пропа говорит, что победило. `propsClosed` — набор пропсов выведен из
  полностью разобранного TS-типа (без внешних `*Attributes`, без индексной сигнатуры): только тогда проп
  «вне контракта» — нарушение; для историй-без-типов набор неполон по построению.
usedValues — скалярные значения пропсов из args историй и литеральных атрибутов JSX (в том числе вложенные
элементы чужих историй, если тег — известный компонент); `children` исключён (текст, не словарь значений),
строки длиннее 40 символов не берутся, не более 24 значений на проп (`usedCapped` называет урезанные).
Документация MDX привязывается к компоненту: `Meta of={X}` → CSF-файл → его компоненты; `component`; совпавший
`title`; соседний файл «тот же каталог, то же имя до первой точки»; иначе — предупреждение.
"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Dict, List, Optional, Tuple

from . import facts as F
from . import interp as I
from . import value as V

MAX_USED_STR = 40
MAX_USED_VALUES = 24
MAX_DESCRIPTION = 600
MAX_DOCS = 4000
MAX_PROP_DESCRIPTION = 300
MAX_MESSAGE = 300
MAX_EXAMPLE = 600
MAX_EXAMPLES = 8
SOURCE_EXTS = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".mts", ".cts", ".cjs")
_ARGS_SPREAD = re.compile(r"\{\s*\.\.\.\s*args\s*\}")


def is_bare(spec: str) -> bool:
    """Bare specifier (`@scope/pkg`, `pkg/sub`), а не относительный путь, алиас (`@/`, `~/`, `#x`) или URL."""
    return bool(spec) and not spec.startswith((".", "/", "~", "#", "@/")) and ":" not in spec.split("/")[0]


def norm_key(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.casefold())


def clip(s: str, limit: int) -> str:
    s = s.strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


class Comp:
    def __init__(self, name: str):
        self.name = name
        self.groups: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []     # (факты файла, группа)
        self.bindings: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        self.prose: List[str] = []
        self.titles: List[str] = []
        self.file_set: List[str] = []


def _strip_ns(name: str, imports: Dict[str, Dict[str, Any]]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """`Ui.Button` при `import * as Ui from 'pkg'` → ("Button", запись импорта Ui). Иначе имя как есть."""
    if "." in name:
        root, rest = name.split(".", 1)
        imp = imports.get(root)
        if imp and imp.get("ns"):
            return rest, imp
    return name, None


def infer_component(group: Dict[str, Any]) -> Optional[str]:
    """Имя компонента группы: явное; иначе по JSX историй (см. докстринг модуля)."""
    meta = group["meta"]
    if meta.get("component"):
        return meta["component"]
    roots = []
    for s in group["stories"]:
        r = I.root_tag(s.get("jsx"))
        if r and r[0].isupper():
            roots.append(r)
    if not roots:
        return None
    last = norm_key((meta.get("title") or "").split("/")[-1])
    for r in roots:
        if last and norm_key(r) == last:
            return r
    return roots[0] if len(set(roots)) == 1 else None


def import_of(name: str, imports: Dict[str, Dict[str, Any]], module: Optional[str] = None
              ) -> Optional[Tuple[str, str, str]]:
    """Как импортируется компонент в этом файле: (путь, named|"default", имя) для bare specifier, иначе None."""
    base, ns = _strip_ns(name, imports)
    root = base.split(".")[0]
    if ns is not None:
        return (ns["from"], root, root) if is_bare(ns["from"]) else None
    imp = imports.get(root)
    if imp and is_bare(imp["from"]):
        return imp["from"], ("default" if imp.get("default") else (imp.get("named") or root)), root
    if module and is_bare(module):
        return module, root, root
    return None


def _resolve_import_file(mdx_file: str, spec: str, known: Dict[str, Any]) -> List[str]:
    """Относительный импорт из MDX → файлы среди просканированных (расширение не обязательно)."""
    base = posixpath.normpath(posixpath.join(posixpath.dirname(mdx_file), spec))
    out = [base] if base in known else []
    out += [base + e for e in SOURCE_EXTS if base + e in known]
    out += [base + "/index" + e for e in SOURCE_EXTS if base + "/index" + e in known]
    return out


def build_registry(files: List[Dict[str, Any]], warnings: List[str]) -> Dict[str, Comp]:
    """Компоненты из групп stories и привязок деклараций + привязка MDX-документации."""
    comps: Dict[str, Comp] = {}
    by_file: Dict[str, List[str]] = {}

    def get(name: str) -> Comp:
        if name not in comps:
            comps[name] = Comp(name)
        return comps[name]

    for f in files:
        for g in f["groups"]:
            raw = infer_component(g)
            if raw is None:
                title = g["meta"].get("title") or "без title"
                warnings.append(f"{f['file']}: группа «{title}» без компонента (нет `component`, JSX историй "
                                f"неоднозначен) — не учтена")
                continue
            name, _ = _strip_ns(raw, f["imports"])
            c = get(name)
            c.groups.append((f, g))
            title = g["meta"].get("title")
            if title and title not in c.titles:
                c.titles.append(title)
            by_file.setdefault(f["file"], []).append(name)
        for b in f["components"]:
            get(b["name"]).bindings.append((f, b))
            by_file.setdefault(f["file"], []).append(b["name"])
    known = {f["file"]: f for f in files}

    for c in comps.values():
        if len(c.titles) > 1:
            warnings.append(f"компонент {c.name}: группы с разными title ({', '.join(c.titles)}) — слиты в одну "
                            f"запись")
    for f in files:
        if not f.get("prose"):
            continue
        d = f.get("docOf") or {}
        targets: List[str] = []
        if d.get("of") and d["of"] in f["imports"]:
            spec = f["imports"][d["of"]]["from"]
            if spec.startswith("."):
                for cand in _resolve_import_file(f["file"], spec, known):
                    targets += by_file.get(cand, [])
        if not targets and d.get("component"):
            name, _ = _strip_ns(d["component"], f["imports"])
            if name in comps:
                targets = [name]
        if not targets and d.get("title"):
            targets = [c.name for c in comps.values() if d["title"] in c.titles]
        if not targets:
            stem = posixpath.basename(f["file"]).split(".")[0]
            folder = posixpath.dirname(f["file"])
            for other, names in sorted(by_file.items()):
                if other != f["file"] and posixpath.dirname(other) == folder and \
                        posixpath.basename(other).split(".")[0] == stem:
                    targets += names
        targets = sorted(set(targets))
        if not targets:
            warnings.append(f"{f['file']}: MDX-документация не привязана к компоненту (нет совпадения по "
                            f"`of`, `component`, `title` или соседнему файлу)")
        for name in targets:
            comps[name].prose.append(f["prose"])
    return comps


# ── использование: значения пропсов и import ─────────────────────────────────

def _usage_pass(files: List[Dict[str, Any]], comps: Dict[str, Comp]) -> Dict[str, Dict[str, List[Any]]]:
    """Скалярные значения пропсов по компонентам: args историй (мета + история) и литеральные атрибуты JSX."""
    used: Dict[str, Dict[str, List[Any]]] = {n: {} for n in comps}

    def add(name: str, prop: str, v: Any) -> None:
        if name in used and F.is_scalar(v):
            used[name].setdefault(prop, []).append(v)

    for f in files:
        for g in f["groups"]:
            raw = infer_component(g)
            owner = _strip_ns(raw, f["imports"])[0] if raw else None
            base = g["meta"].get("args") or {}
            for s in g["stories"]:
                if owner:
                    merged = dict(base)
                    merged.update(s.get("args") or {})
                    for prop, v in merged.items():
                        add(owner, prop, v)
                for tag, props in s.get("usage") or []:
                    name = _strip_ns(tag, f["imports"])[0]
                    for prop, vals in props.items():
                        for v in vals:
                            add(name, prop, v)
    return used


def _import_candidates(files: List[Dict[str, Any]], comps: Dict[str, Comp]) -> Dict[str, Dict[Tuple[str, str], int]]:
    """Свидетельства import-пути: bare-импорты имени компонента в просканированных файлах и имя ambient-модуля."""
    out: Dict[str, Dict[Tuple[str, str], int]] = {}
    for f in files:
        for local, imp in f["imports"].items():
            if imp.get("ns") or not is_bare(imp["from"]):
                continue
            name = local if imp.get("default") else (imp.get("named") or local)
            if name in comps:
                key = (imp["from"], "default" if imp.get("default") else name)
                out.setdefault(name, {}).setdefault(key, 0)
                out[name][key] += 1
        for g in f["groups"]:                                     # `component: Ui.Btn` при `import * as Ui`
            raw = infer_component(g)
            if raw and "." in raw:
                name, ns = _strip_ns(raw, f["imports"])
                if ns is not None and name in comps and is_bare(ns["from"]):
                    key = (ns["from"], name.split(".")[0])
                    out.setdefault(name, {}).setdefault(key, 0)
                    out[name][key] += 1
        for b in f["components"]:
            if b.get("module") and is_bare(b["module"]):
                key = (b["module"], b["name"])
                out.setdefault(b["name"], {}).setdefault(key, 0)
                out[b["name"]][key] += 1
    return out


def _sort_key(v: Any) -> Tuple[int, Any]:
    return (0, v) if isinstance(v, bool) else (1, v) if isinstance(v, (int, float)) else (2, v)


# ── контракт ─────────────────────────────────────────────────────────────────

class Info:
    """Промежуточное по компоненту: нужное и контракту, и details."""
    def __init__(self):
        self.ts_props: Dict[str, Dict[str, Any]] = {}
        self.closed = False
        self.binding: Optional[Dict[str, Any]] = None
        self.bfile = ""
        self.arg_infos: Dict[str, Dict[str, Any]] = {}


def _decide(ts: Optional[Dict[str, Any]], ai: Dict[str, Any], values: List[Any], file: str,
            tt: I.TypeTable) -> Tuple[str, Optional[List[Any]], str]:
    """(kind, options, from) для пропа — по убыванию доверия: types → argTypes → usage."""
    ts_kind, ts_opts = "unknown", None
    if ts is not None:
        ts_kind, ts_opts = ("function", None) if ts.get("method") else I.classify(ts.get("type"), file, tt)
    ai_kind, ai_opts = ai.get("kind"), ai.get("options")
    if ts_kind == "enum":
        return "enum", ts_opts, "types"
    if ts_kind in ("boolean", "node", "function", "object", "array"):
        return ts_kind, None, "types"
    if ts_kind in ("string", "number"):
        return ("enum", ai_opts, "argTypes") if ai_opts else (ts_kind, None, "types")
    if ts_kind == "other":
        if ai_kind:
            return ai_kind, (ai_opts if ai_kind == "enum" else None), "argTypes"
        return "other", None, "types"
    if ai_kind:
        return ai_kind, (ai_opts if ai_kind == "enum" else None), "argTypes"
    frm = "types" if ts is not None else ("argTypes" if ai else "usage")
    if values:
        if all(isinstance(v, bool) for v in values):
            return "boolean", None, "usage"
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            return "number", None, "usage"
        if all(isinstance(v, str) for v in values):
            return "string", None, "usage"
    return "unknown", None, frm


def _contract_entry(c: Comp, tt: I.TypeTable, used: Dict[str, List[Any]],
                    imports: Dict[Tuple[str, str], int]) -> Tuple[Dict[str, Any], Info]:
    info = Info()
    for f, b in c.bindings:
        if b.get("props"):
            info.binding, info.bfile = b, f["file"]
            break
    if info.binding is None and c.bindings:
        info.bfile, info.binding = c.bindings[0][0]["file"], c.bindings[0][1]
    if info.binding is not None and info.binding.get("props"):
        info.ts_props, info.closed = I.resolve_props(info.binding["props"], info.bfile, tt)
    for f, g in c.groups:
        sources = [g["meta"].get("argTypes")] + [s.get("argTypes") for s in g["stories"]]
        for src in sources:
            for prop, raw in (src or {}).items():
                cur = info.arg_infos.setdefault(prop, {})
                for k, v in I.argtype_info(raw).items():
                    cur.setdefault(k, v)
    names = set(info.ts_props) | set(info.arg_infos)
    if not info.closed:
        names |= {n for n in used if n != "children"}
    file0 = info.bfile or (c.groups[0][0]["file"] if c.groups else "")
    props: Dict[str, Any] = {}
    for name in sorted(names):
        ts = info.ts_props.get(name)
        ai = info.arg_infos.get(name, {})
        kind, options, frm = _decide(ts, ai, used.get(name, []), file0, tt)
        entry: Dict[str, Any] = {"kind": kind, "from": frm}
        if options:
            entry["options"] = options
        if (ts is not None and not ts.get("optional") and not ts.get("method")) or ai.get("required"):
            entry["required"] = True
        props[name] = entry
    used_out: Dict[str, List[Any]] = {}
    capped: List[str] = []
    for prop, vals in used.items():
        if prop == "children":
            continue
        ok = [v for v in I.dedupe(vals) if not isinstance(v, str) or 0 < len(v) <= MAX_USED_STR]
        ok.sort(key=_sort_key)
        if len(ok) > MAX_USED_VALUES:
            capped.append(prop)
            ok = ok[:MAX_USED_VALUES]
        if ok:
            used_out[prop] = ok
    imp: Optional[Dict[str, Any]] = None
    if imports:
        path, named = sorted(imports, key=lambda k: (-imports[k], k))[0]
        imp = {"path": path, "default": True} if named == "default" else {"path": path, "named": named}
    title = c.titles[0] if c.titles else None
    entry_c: Dict[str, Any] = {
        "title": title,
        "taxonomy": [p.strip() for p in title.split("/")[:-1]] if title else [],
        "props": props,
        "propsClosed": bool(info.closed and info.binding is not None),
        "usedValues": used_out,
        "import": imp,
        "stories": sum(len(g["stories"]) for _, g in c.groups),
    }
    if capped:
        entry_c["usedCapped"] = sorted(capped)
    return entry_c, info


# ── подробности (КАК ИСПОЛЬЗОВАТЬ) ───────────────────────────────────────────

def first_paragraph(prose: Optional[str]) -> Optional[str]:
    """Первый абзац прозы, который не заголовок, не код, не таблица и не JSX."""
    for block in re.split(r"\n\s*\n", prose or ""):
        b = block.strip()
        if b and not b.startswith(("#", "```", "~~~", "|", "<", "- ", "* ")):
            return " ".join(b.split())
    return None


def _dep(target: str, name: str, message: str) -> Dict[str, Any]:
    return {"target": target, "name": name, "message": clip(message, MAX_MESSAGE),
            "replacement": I.replacement_of(message)}


def _inline_args(text: str, attrs: List[str], children: Optional[str]) -> str:
    """`{...args}` в JSX истории → атрибуты args; `children` из args — внутрь самозакрытого корня: пример становится
    самодостаточным."""
    out = re.sub(r" {2,}", " ", _ARGS_SPREAD.sub(" ".join(attrs), text))
    out = out.replace(" >", ">").replace(" />", "/>").replace("/>", " />")
    tag = I.root_tag(out)
    if children and tag and out.endswith("/>") and out.count("<") == 1:
        out = out[:-3].rstrip() + f">{children}</{tag}>"
    return out


def _examples(c: Comp) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for f, g in c.groups:
        base = g["meta"].get("args") or {}
        for s in g["stories"]:
            name = s.get("name") or (F.story_name_from_export(s["export"]) if s.get("export") else None)
            if not name:
                continue
            merged = dict(base)
            merged.update(s.get("args") or {})
            jsx = None
            if s.get("jsx"):
                text = V.normalize_jsx(s["jsx"])
                if _ARGS_SPREAD.search(text):                 # `{...args}` → атрибуты args (children даёт JSX)
                    text = _inline_args(text, *I.args_to_attrs(merged))
                    jsx = text if I.root_tag(text) == c.name and len(text) <= MAX_EXAMPLE else None
                elif I.root_tag(text) == c.name and len(text) <= MAX_EXAMPLE:
                    jsx = text
            if jsx is None and merged:
                jsx = I.synth_example(c.name, merged)
            if jsx is None and s.get("jsx"):
                text = V.normalize_jsx(s["jsx"])
                jsx = text if len(text) <= MAX_EXAMPLE and not _ARGS_SPREAD.search(text) else None
            if jsx is None or len(jsx) > MAX_EXAMPLE or (name, jsx) in seen:
                continue
            seen.add((name, jsx))
            out.append({"name": name, "jsx": jsx})
            if len(out) >= MAX_EXAMPLES:
                return out
    return out


def _details_entry(c: Comp, tt: I.TypeTable, info: Info, prop_names: List[str]) -> Optional[Dict[str, Any]]:
    meta_desc = next((g["meta"]["description"] for _, g in c.groups if g["meta"].get("description")), None)
    prose = "\n\n".join(dict.fromkeys(c.prose)) if c.prose else None
    bdoc = info.binding["doc"] if info.binding else None
    type_doc = None
    pt = info.binding.get("props") if info.binding else None
    if pt and pt["k"] == "ref":
        decl = tt.lookup(pt["name"], info.bfile)
        type_doc = decl["doc"] if decl else None
    description = meta_desc or first_paragraph(prose) or (bdoc and bdoc.get("text")) or \
        (type_doc and type_doc.get("text")) or None
    props: Dict[str, Any] = {}
    for name in prop_names:
        ts = info.ts_props.get(name)
        ai = info.arg_infos.get(name, {})
        d: Dict[str, Any] = {}
        text = ai.get("description") or (ts["doc"]["text"] if ts and ts.get("doc") else "")
        if text:
            d["description"] = clip(text, MAX_PROP_DESCRIPTION)
        default = ai.get("default")
        if default is None and ts and ts.get("doc") and ts["doc"].get("default") is not None:
            default = I.default_of(ts["doc"]["default"])
        if default is not None:
            d["default"] = default
        if d:
            props[name] = d
    deps: List[Dict[str, Any]] = []
    if bdoc and bdoc.get("deprecated") is not None:
        deps.append(_dep("component", c.name, bdoc["deprecated"]))
    for name in sorted(info.ts_props):
        doc = info.ts_props[name].get("doc")
        if doc and doc.get("deprecated") is not None:
            deps.append(_dep("prop", name, doc["deprecated"]))
    examples = _examples(c)
    if not (description or prose or props or deps or examples):
        return None
    return {"description": clip(description, MAX_DESCRIPTION) if description else None,
            "docs": clip(prose, MAX_DOCS) if prose else None,
            "props": props, "deprecations": deps, "examples": examples}


# ── вход ─────────────────────────────────────────────────────────────────────

def merge(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Факты файлов → {"components": {имя: {"contract": …, "details": … | None}}, "warnings": […]}."""
    files = sorted(files, key=lambda f: f["file"])
    warnings: List[str] = []
    for f in files:
        warnings.extend(f"{f['file']}: {w}" for w in f.get("warnings", []))
    tt = I.TypeTable(files)
    comps = build_registry(files, warnings)
    used = _usage_pass(files, comps)
    imports = _import_candidates(files, comps)
    out: Dict[str, Any] = {}
    for name in sorted(comps):
        c = comps[name]
        contract, info = _contract_entry(c, tt, used.get(name, {}), imports.get(name, {}))
        out[name] = {"contract": contract, "details": _details_entry(c, tt, info, list(contract["props"]))}
    return {"components": out, "warnings": warnings}
