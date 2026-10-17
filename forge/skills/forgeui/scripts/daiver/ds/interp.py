"""interp.py — интерпретация фактов: тип → вид пропа и допустимые значения, argTypes → контракт, примеры.

Чистые функции над JSON-фактами (см. facts.py): не знают, какой движок (Python-лексер или node) факты породил.
Вид пропа (`kind`) — словарь из десяти слов, под него пишутся потребители (2.3 lookup, 4.5 DS-гейт):
  enum (закрытый набор литералов: `options`), boolean, string, number, node (слот: ReactNode/ReactElement),
  function, object, array, other (типизирован, но не классифицирован), unknown (о типе ничего не известно).
Допущения (не проверено данными боевой библиотеки): `PropsWithChildren/Omit/Pick/Partial/Required/Readonly`
разворачиваются как в TS; тип, которого нет среди просканированных (`React.HTMLAttributes` и любой внешний),
открывает набор пропсов; союз «литералы + string» (приём `(string & {})`) — не закрытый набор; именованные
типы `…Handler/…Callback/…Listener` — функции.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import facts as F
from . import lex as L
from . import value as V

KINDS = ("enum", "boolean", "string", "number", "node", "function", "object", "array", "other", "unknown")
ELEMENT_LAST = frozenset(("ReactNode", "ReactElement", "ReactChild", "ReactPortal", "ReactText"))
FUNCTION_SUFFIX = ("Handler", "Callback", "Listener")
PASS = frozenset(("PropsWithoutRef", "Readonly", "ComponentPropsWithoutRef", "NoInfer"))
MAX_RESOLVE_DEPTH = 8


class TypeTable:
    """Объявления типов всех файлов; поиск по имени: тот же файл → тот же каталог → первый по пути."""

    def __init__(self, files: List[Dict[str, Any]]):
        self.by_name: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for f in files:
            for name, decl in f.get("types", {}).items():
                self.by_name.setdefault(name, []).append((f["file"], decl))

    def lookup(self, name: str, file: str) -> Optional[Dict[str, Any]]:
        last = name.split(".")[-1]
        cands = self.by_name.get(last)
        if not cands:
            return None
        for path, decl in cands:
            if path == file:
                return decl
        d = file.rsplit("/", 1)[0] if "/" in file else ""
        for path, decl in cands:
            if (path.rsplit("/", 1)[0] if "/" in path else "") == d:
                return decl
        return cands[0][1]


# ── вид и значения из типа ───────────────────────────────────────────────────

def dedupe(values: List[Any]) -> List[Any]:
    """Без повторов, порядок первого появления; 1 и true — разные значения."""
    seen = set()
    out: List[Any] = []
    for v in values:
        key = (type(v).__name__, repr(v))
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def classify(ast: Optional[Dict[str, Any]], file: str, tt: TypeTable, depth: int = 0
             ) -> Tuple[str, Optional[List[Any]]]:
    """(вид, options|None) по упрощённому типу."""
    if ast is None:
        return "unknown", None
    if depth > MAX_RESOLVE_DEPTH:
        return "other", None
    k = ast["k"]
    if k == "lit":
        v = ast["v"]
        return ("boolean", None) if isinstance(v, bool) else ("enum", [v])
    if k == "kw":
        return {"string": ("string", None), "number": ("number", None), "bigint": ("number", None),
                "boolean": ("boolean", None), "any": ("unknown", None), "unknown": ("unknown", None),
                "object": ("object", None)}.get(ast["v"], ("other", None))
    if k == "tpl":
        return "string", None
    if k == "fn":
        return "function", None
    if k in ("arr", "tuple"):
        return "array", None
    if k == "obj":
        return "object", None
    if k == "union":
        items = [x for x in ast["items"] if not (x["k"] == "kw" and x["v"] in ("null", "undefined", "void"))]
        if not items:
            return "other", None
        if len(items) == 1:
            return classify(items[0], file, tt, depth + 1)
        res = [classify(x, file, tt, depth + 1) for x in items]
        kinds = [r[0] for r in res]
        if all(r[0] == "enum" or (r[0] == "boolean") for r in res):
            opts: List[Any] = []
            for (kd, op), x in zip(res, items):
                if kd == "boolean":
                    opts.extend([True, False] if x["k"] == "kw" else [x.get("v")])
                else:
                    opts.extend(op or [])
            seen = dedupe(opts)
            if set(map(repr, seen)) == {"True", "False"}:
                return "boolean", None
            return "enum", seen
        if "node" in kinds:
            return "node", None
        for broad in ("string", "number"):
            if broad in kinds and all(kd in (broad, "enum", "other") for kd in kinds):
                return broad, None
        if len(set(kinds)) == 1:
            return kinds[0], None
        return "other", None
    if k == "inter":
        res = [classify(x, file, tt, depth + 1)[0] for x in ast["items"]]
        if all(r in ("object", "other", "unknown") for r in res):
            return "object", None
        if "string" in res and all(r in ("string", "object") for r in res):
            return "string", None
        return "other", None
    if k == "ref":
        name = ast["name"]
        last = name.split(".")[-1]
        if name == "JSX.Element" or last in ELEMENT_LAST:
            return "node", None
        if last in ("Array", "ReadonlyArray"):
            return "array", None
        if last in ("Record", "Partial", "Map", "Set", "WeakMap"):
            return "object", None
        if last == "Function" or last.endswith(FUNCTION_SUFFIX):
            return "function", None
        decl = tt.lookup(name, file)
        if decl:
            if decl["kind"] == "alias":
                return classify(decl["type"], file, tt, depth + 1)
            if decl["kind"] == "enum":
                vals = [v for v in (decl["enum"] or {}).values() if F.is_scalar(v)]
                if vals and len(vals) == len(decl["enum"]):
                    return "enum", vals
                return "other", None
            return "object", None
        return "other", None
    return "other", None


# ── набор пропсов из типа ────────────────────────────────────────────────────

def _keys_of(ast: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Ключи из `'a' | 'b'` (аргумент Omit/Pick) или None, если не литералы."""
    if ast is None:
        return None
    if ast["k"] == "lit" and isinstance(ast["v"], str):
        return [ast["v"]]
    if ast["k"] == "union":
        out: List[str] = []
        for x in ast["items"]:
            sub = _keys_of(x)
            if sub is None:
                return None
            out.extend(sub)
        return out
    return None


def resolve_props(ast: Optional[Dict[str, Any]], file: str, tt: TypeTable, depth: int = 0,
                  seen: Optional[Tuple[str, ...]] = None) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    """(пропсы {имя: Member}, закрыт ли набор). Закрыт — все составляющие типа разобраны; внешний тип
    (`React.HTMLAttributes`), неразрешимое имя, индексная сигнатура → набор открыт."""
    props: Dict[str, Dict[str, Any]] = {}
    if ast is None:
        return props, False
    if depth > MAX_RESOLVE_DEPTH:
        return props, False
    seen = seen or ()
    k = ast["k"]
    closed = True
    if k == "obj":
        for m in ast["members"]:
            props.setdefault(m["name"], m)
        return props, not ast["open"]
    if k in ("inter", "union"):
        for it in ast["items"]:
            sub, c = resolve_props(it, file, tt, depth + 1, seen)
            for n, m in sub.items():
                props.setdefault(n, m)
            closed = closed and c
        return props, closed and k == "inter"
    if k == "ref":
        name = ast["name"]
        last = name.split(".")[-1]
        args = ast["args"]
        if last in ("RefAttributes", "ClassAttributes"):
            return props, True
        if last == "PropsWithChildren":
            sub, c = resolve_props(args[0] if args else None, file, tt, depth + 1, seen)
            sub.setdefault("children", {"name": "children", "optional": True, "method": False, "doc": None,
                                        "type": {"k": "ref", "name": "ReactNode", "args": []}})
            return sub, c
        if last in PASS and args:
            return resolve_props(args[0], file, tt, depth + 1, seen)
        if last in ("Partial", "Required") and args:
            sub, c = resolve_props(args[0], file, tt, depth + 1, seen)
            return {n: dict(m, optional=(last == "Partial")) for n, m in sub.items()}, c
        if last in ("Omit", "Pick") and len(args) == 2:
            sub, c = resolve_props(args[0], file, tt, depth + 1, seen)
            keys = _keys_of(args[1])
            if keys is None:
                return sub, False
            if last == "Omit":
                return {n: m for n, m in sub.items() if n not in keys}, c
            return {n: m for n, m in sub.items() if n in keys}, c
        if last in ("Record", "ComponentProps", "ComponentPropsWithRef"):
            return props, False
        if name in seen:
            return props, False
        decl = tt.lookup(name, file)
        if not decl:
            return props, False
        nxt = seen + (name,)
        if decl["kind"] == "alias":
            return resolve_props(decl["type"], file, tt, depth + 1, nxt)
        if decl["kind"] != "interface":
            return props, False
        for m in decl["members"]:
            props.setdefault(m["name"], m)
        closed = not decl["open"]
        for base in decl["extends"]:
            sub, c = resolve_props(base, file, tt, depth + 1, nxt)
            for n, m in sub.items():
                props.setdefault(n, m)
            closed = closed and c
        return props, closed
    return props, False


# ── argTypes ─────────────────────────────────────────────────────────────────

_CONTROL_KIND = {"boolean": "boolean", "text": "string", "number": "number", "range": "number",
                 "color": "string", "object": "object", "array": "array", "file": "other", "date": "other"}
_NAME_KIND = {"string": "string", "number": "number", "boolean": "boolean", "function": "function",
              "object": "object", "array": "array", "symbol": "other", "node": "node", "element": "node"}
_UNION_ITEM = re.compile(r"^(?:'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?)|(true|false))$")


def parse_union_text(text: str) -> Tuple[str, Optional[List[Any]]]:
    """Текст типа из `table.type.summary` (`'a' | 'b'`, `boolean`, `() => void`) → (вид, options|None)."""
    s = text.strip()
    if "=>" in s:
        return "function", None
    parts = [p.strip() for p in s.split("|")]
    opts: List[Any] = []
    for p in parts:
        m = _UNION_ITEM.match(p)
        if not m:
            break
        if m.group(1) is not None:
            opts.append(m.group(1))
        elif m.group(2) is not None:
            opts.append(m.group(2))
        elif m.group(3) is not None:
            n = float(m.group(3))
            opts.append(int(n) if n == int(n) else n)
        else:
            opts.append(m.group(4) == "true")
    else:
        if len(opts) == 1 and isinstance(opts[0], bool):
            return "boolean", None
        if set(map(repr, opts)) == {"True", "False"}:
            return "boolean", None
        return ("enum", opts) if opts else ("unknown", None)
    if len(parts) == 1 and s in _NAME_KIND:
        return _NAME_KIND[s], None
    if s in ("ReactNode", "React.ReactNode", "ReactElement", "JSX.Element"):
        return "node", None
    return "other", None


def default_of(v: Any) -> Any:
    """`table.defaultValue.summary` / `defaultValue` → скаляр (кавычки снимаются) или None."""
    if isinstance(v, str):
        s = v.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"`":
            return s[1:-1]
        if s in ("true", "false"):
            return s == "true"
        if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
            n = float(s)
            return int(n) if n == int(n) else n
        return s if s and len(s) <= 80 else None
    if isinstance(v, (bool, int, float)):
        return v
    return None


def argtype_info(raw: Any) -> Dict[str, Any]:
    """Закодированный `argTypes.<prop>` → {options?, kind?, description?, default?, required?}.

    options: `options` | `control.options` | `type: {name:'enum', value:[…]}` | союз в `table.type.summary`;
    kind: из options (enum), `control`/`control.type`, `type`, `table.type.summary`, `action`."""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    ctl = raw.get("control")
    ctl_type = ctl if isinstance(ctl, str) else (ctl.get("type") if isinstance(ctl, dict) else None)
    ctl_type = ctl_type if isinstance(ctl_type, str) else None
    opts = raw.get("options")
    if opts is None and isinstance(ctl, dict):
        opts = ctl.get("options")
    ty = raw.get("type")
    if opts is None and isinstance(ty, dict) and ty.get("name") == "enum" and isinstance(ty.get("value"), list):
        opts = [x.get("value") if isinstance(x, dict) else x for x in ty["value"]]
        opts = [(o.strip("'\"") if isinstance(o, str) else o) for o in opts]
    if isinstance(opts, list) and opts and not F.has_opaque(opts) and all(F.is_scalar(x) for x in opts):
        out["options"] = opts
        out["kind"] = "enum"
    kind = None
    if ctl_type in _CONTROL_KIND:
        kind = _CONTROL_KIND[ctl_type]
    tname = ty if isinstance(ty, str) else (ty.get("name") if isinstance(ty, dict) else None)
    if kind is None and isinstance(tname, str) and tname in _NAME_KIND:
        kind = _NAME_KIND[tname]
    table = raw.get("table")
    tsum = F.obj_get(table, "type", "summary")
    if isinstance(tsum, str):
        tk, to = parse_union_text(tsum)
        if "options" not in out and tk == "enum" and to:
            out["options"], out["kind"] = to, "enum"
        elif kind is None and tk not in ("unknown", "other"):
            kind = tk
    if isinstance(raw.get("action"), str):
        kind = kind or "function"
    if "kind" not in out and kind:
        out["kind"] = kind
    elif out.get("kind") == "enum" and kind == "boolean":
        out.pop("options", None)
        out["kind"] = "boolean"
    desc = raw.get("description")
    if isinstance(desc, str) and desc.strip():
        out["description"] = L.clean_text(desc)
    dv = default_of(F.obj_get(table, "defaultValue", "summary"))
    if dv is None:
        dv = default_of(raw.get("defaultValue"))
    if dv is not None:
        out["default"] = dv
    req = F.obj_get(ty, "required") if isinstance(ty, dict) else None
    if req is True or raw.get("required") is True:
        out["required"] = True
    return out


# ── deprecations ─────────────────────────────────────────────────────────────

_REPLACEMENT = (
    re.compile(r"\binstead[,:]?\s+use\s+[`'\"]?([^`'\"\s;,]+(?:=\"[^\"]*\")?)", re.I),
    re.compile(r"\buse\s+`([^`]+)`", re.I),
    re.compile(r"\breplaced\s+(?:by|with)\s+`?([^`\s;,]+)`?", re.I),
    re.compile(r"\bprefer\s+`([^`]+)`", re.I),
    re.compile(r"\bsee\s+`([^`]+)`", re.I),
    re.compile(r"\buse\s+([A-Za-z_][\w.]*)\s+instead", re.I),
)


def replacement_of(message: str) -> Optional[str]:
    """Чем заменить, из текста `@deprecated` («Use `X` instead», «replaced by X», «prefer `X`»)."""
    for rx in _REPLACEMENT:
        m = rx.search(message)
        if m:
            return m.group(1).strip().rstrip(".,;:")
    return None


# ── примеры ──────────────────────────────────────────────────────────────────

_ROOT_TAG = re.compile(r"^<\s*([A-Za-z_$][\w$.]*)")


def root_tag(jsx: Optional[str]) -> Optional[str]:
    m = _ROOT_TAG.match(jsx.strip()) if jsx else None
    return m.group(1) if m else None


def _attr_text(name: str, v: Any) -> Optional[str]:
    if isinstance(v, bool):
        return name if v else f"{name}={{false}}"
    if isinstance(v, str):
        return f'{name}="{v}"' if '"' not in v and "\n" not in v else f"{name}={{{_js_str(v)}}}"
    if isinstance(v, (int, float)):
        return f"{name}={{{v}}}"
    if F.is_jsx(v):
        return f"{name}={{{V.normalize_jsx(v['text'])}}}"
    if isinstance(v, list) and not F.has_opaque(v):
        txt = _js_value(v)
        return f"{name}={{{txt}}}" if txt and len(txt) <= 80 else None
    return None


def _js_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _js_value(v: Any) -> Optional[str]:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _js_str(v)
    if isinstance(v, list):
        parts = [_js_value(x) for x in v]
        return "[" + ", ".join(parts) + "]" if all(p is not None for p in parts) else None
    return None


def args_to_attrs(args: Dict[str, Any]) -> Tuple[List[str], Optional[str]]:
    """args истории → ([атрибуты JSX в порядке args], children|None); значения, которых статически нет
    (функции, невычисленное), опускаются."""
    attrs: List[str] = []
    children: Optional[str] = None
    for name, v in args.items():
        if name == "children":
            if isinstance(v, str):
                children = v
            elif F.is_jsx(v):
                children = V.normalize_jsx(v["text"])
            continue
        if F.is_opaque(v) or F.is_ref(v) or v is None:
            continue
        txt = _attr_text(name, v)
        if txt:
            attrs.append(txt)
    return attrs, children


def synth_example(component: str, args: Dict[str, Any]) -> Optional[str]:
    """JSX-пример из args: `<Button variant="primary" disabled>Save</Button>`."""
    attrs, children = args_to_attrs(args)
    head = " ".join([component] + attrs)
    return f"<{head}>{children}</{component}>" if children else f"<{head} />"
