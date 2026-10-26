"""ds_gate.py — DS-гейт: компоненты, пропсы, токены (спека §14.4.5; INV-C13, B17, A2; задача 4.5).

Экран может совпасть с макетом по пикселям и при этом быть собран мимо библиотеки: свой Button вместо библиотечного, значение пропа
вне контракта, захардкоженный цвет вместо токена. Гейт держит страницу и исходники против ТРЁХ источников, ни одного не выдумывая:
решений плана (`plan.json` → `sections[].nodes[]`), требований ДС (`ds_lookup.Library`, пара документов 2.2) и токенов макета
(`node.tokens` + цвета канона). Чистая функция `run(...)`; чтение с диска — `gather(root, track, frame)`.

Коды (каждая находка несёт `rule` — какое требование сработало; дефект без правила есть мнение):
  DS_COMPONENT_WRONG   fiber страницы назвал ДРУГОЙ опубликованный компонент, чем решил план;
  DS_COMPONENT_MISSED  fiber у узла нет, а план велел библиотечный компонент — после сверки «до обвинения» (см. ниже);
  DS_PROP_WRONG        проп вне контракта закрытого компонента (`propsClosed`) / литерал вне `options` enum (или не булево у boolean);
  DS_DEPRECATED        компонент или проп с `@deprecated` (запись details — своего компонента, чужая проза не считается);
  DS_TOKEN_MISSED      в макете привязан токен, а в исходниках лежит ровно тот же литерал цвета;
  DS_OFF_SCALE         литерал цвета рядом с цветом привязанного токена (≤ OFF_SCALE_CHANNEL на канал), но не равен ему.

Честность:
  • идентичность не исполняется без карты резолюций (нет плана / в нём нет решений-компонентов) и без fiber на странице
    (`react.found: false`) — об этом `parts.identity.ran: false` с причиной, а не «догадка, какие узлы — компоненты»; план с id
    ДРУГОГО кадра — `degraded` (известные записи всё равно работают, пустые записи молча пропускаются);
  • fiber вне опубликованных имён — молчание: узел отдан скану исходников;
  • имя отсутствующего fiber сверяется ДО обвинения: разметка согласна с планом (Label поверх `<label>`) → dismissal;
    исходники рендерят этот компонент из библиотеки → MISSED снимается в dismissal с остаточным риском вслух; исходники его не рендерят — MISSED стоит;
  • скан кода: импорт из `import.path` контракта; namespace-импорт (`<Ui.Button>`) принимается, одноимённый самодельный (импорт
    из другого пути или определение в файле) — нет; значение, статически не известное, — не оценивается (dismissal);
  • `ran: false` (с причиной, без находок): нет документа требований; нет JS/TS среди outputs; нечего держать против (ни одного
    компонента в плане, ни одного токена в макете);
  • каждая находка с неуверенной парой (`confidence: low`) снимается в dismissal: личность по неверной паре не судится.

Допущения, не проверенные данными (боевых ДС, планов и модулей нет): обработчики `onX`, `data-*`, `aria-*`, `key`, `ref`,
`className`, `style`, `children` не считаются пропами вне контракта (в stories они не объявляются); значения kind≠enum/boolean не
проверяются (`usedValues` — словарь, а не запрет); «литерал рядом с токеном» — по ВСЕМ исходникам outputs, а не по узлу (скан не
сопоставляет строку кода с узлом макета — это дело код-гейта 4.7); допуск OFF_SCALE_CHANNEL = 8; DS_OFF_SCALE не называет
замену — её даёт `daiver_color_tokens`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .. import canon as C
from .. import color_tokens as CT
from .. import component_map as CM
from .. import config as CFG
from .. import ds_lookup as DL
from .. import build_state as BS
from .. import plan as PL
from .. import track as TR
from . import match as MT
from . import tsx_scan as TS

OFF_SCALE_CHANNEL = 8                   # канал rgb (0..255): литерал ближе этого, но не равный цвету токена, — мимо шкалы
OFF_SCALE_ALPHA = 0.05
MAX_SOURCE_BYTES = 1024 * 1024
IGNORED_PROPS = frozenset(("key", "ref", "children", "className", "style", "id", "slot", "dangerouslySetInnerHTML"))
TOKEN_SLOTS = (("fill", "bg"), ("text", "color"), ("stroke", "border"))
LAYER = "ds"
CODES = ("DS_COMPONENT_WRONG", "DS_COMPONENT_MISSED", "DS_PROP_WRONG", "DS_DEPRECATED", "DS_TOKEN_MISSED", "DS_OFF_SCALE")


class DsGateError(ValueError):
    """Вход не похож на канон / результат матчера."""


def _root_of(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict) and "root" in x and "kind" not in x:
        x = x["root"]
    if not isinstance(x, dict) or "kind" not in x or "id" not in x:
        raise DsGateError("ожидается узел канона (или конверт с root)")
    return x


def _index(root: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        out[n["id"]] = n
        stack.extend(k for k in (n.get("children") or []) if isinstance(k, dict))
    return out


# ---------------------------------------------------------------------------------------------------------------- план

def plan_decisions(plan: Any, known: Set[str]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """plan.json → ({id узла макета: {"decision": component|local|ignore, "component": имя?}}, id чужих кадров).

    Читаются записи `sections[].nodes[]` (форма нашего плана); пустая или неразобранная запись молча пропускается, остальные
    работают. Запись с id, которого нет в макете, — чужой кадр: она не решение, а повод `degraded`."""
    out: Dict[str, Dict[str, Any]] = {}
    foreign: List[str] = []
    secs = plan.get("sections") if isinstance(plan, dict) else None
    for s in secs if isinstance(secs, list) else []:
        for e in (s.get("nodes") if isinstance(s, dict) else None) or []:
            if not isinstance(e, dict) or not isinstance(e.get("id"), str):
                continue
            if isinstance(e.get("component"), str) and e["component"].strip():
                rec = {"decision": "component", "component": e["component"].strip()}
            elif e.get("resolution") in ("local", "ignore"):
                rec = {"decision": e["resolution"], "component": None}
            else:
                continue
            if e["id"] not in known:
                foreign.append(e["id"])
            else:
                out[e["id"]] = rec
    return out, sorted(set(foreign))


# ---------------------------------------------------------------------------------------------------------- ресурсы диска

def gather(root: Any, track: str, frame: Optional[str] = None) -> Dict[str, Any]:
    """Всё, что гейту нужно с диска: требования ДС, план, исходники outputs build-state, карта цветов. Только читает; то, чего нет,
    называется в `notes`, а не выдумывается."""
    root = Path(root)
    notes: List[str] = []
    out: Dict[str, Any] = {"lib": None, "libProblem": None, "plan": None, "sources": {}, "colors": None, "colorsFile": None, "notes": notes}
    try:
        out["lib"] = DL.load(root)
    except DL.DsLookupError as e:
        out["libProblem"] = str(e)
    manifest = TR.load(root, track)
    if manifest is None:
        raise DsGateError(f"трек «{track}» не найден")
    paths = TR.frame_paths(root, manifest, frame)
    out["design"] = C.read_canonical(paths["canonical"])
    plan, _raw, why = PL.read_plan(paths["plan"])
    out["plan"] = plan
    if why:
        notes.append(f"plan.json {why}: идентичность по плану не исполняется")
    state = BS.load_state(root, paths["buildState"], track, TR.get_frame(manifest, frame)["name"])
    if state is None:
        notes.append("нет build-state.json: исходников для скана нет")
    else:
        files = sorted({o for s in state["sections"] for o in (s.get("outputs") or [])})
        for rel in files:
            full = root / rel
            if not (TS.is_js(rel) or TS.is_style(rel)) or not full.is_file():
                continue
            try:
                full.resolve().relative_to(root.resolve())
                data = full.read_bytes()
            except (OSError, ValueError):
                notes.append(f"{rel}: не читается")
                continue
            if len(data) > MAX_SOURCE_BYTES:
                notes.append(f"{rel}: больше {MAX_SOURCE_BYTES} байт — пропущен")
                continue
            try:
                out["sources"][rel] = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                notes.append(f"{rel}: не UTF-8 — пропущен")
    cfg = CFG.load(root)
    rel = cfg["config"]["project"]["styling"]["colorsFile"] if cfg["initialized"] else None
    if rel:
        try:
            text, fmt = CT.read_colors_file(root, rel)
            out["colorsFile"] = rel
            if fmt:
                out["colors"] = CT.parse_colors(text, fmt)["table"]
        except CT.ColorTokensError as e:
            notes.append(str(e))
    return out


# ---------------------------------------------------------------------------------------------------------------- гейт

class _State:
    def __init__(self):
        self.findings: List[Dict[str, Any]] = []
        self.dismissals: List[Dict[str, Any]] = []
        self.degraded: List[Dict[str, str]] = []
        self.seen: Set[Tuple[Any, ...]] = set()

    def find(self, code: str, rule: str, message: str, **kw: Any) -> None:
        key = (code, rule, kw.get("design"), kw.get("file"), kw.get("line"), kw.get("prop"))
        if key in self.seen:
            return
        self.seen.add(key)
        sev = "minor" if code == "DS_DEPRECATED" else "major"           # deprecated — предупреждение: ничего не объясняет и не блокирует
        self.findings.append({"code": code, "layer": LAYER, "severity": sev, "rule": rule, "message": message, **kw})

    def dismiss(self, code: str, reason: str, **kw: Any) -> None:
        key = ("d", code, reason, kw.get("design"), kw.get("file"), kw.get("line"), kw.get("prop"))
        if key in self.seen:
            return
        self.seen.add(key)
        self.dismissals.append({"code": code, "layer": LAYER, "reason": reason, **kw})


def _library_key(lib: Any, tag: str, imports: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Тег использования → ключ компонента контракта, если он пришёл из пакета, названного в `import.path` контракта; иначе None."""
    r = TS.resolve_tag(tag, imports)
    if r is None:
        return None
    for key in lib.names:
        imp = lib.comps[key].get("import")
        if not imp or imp["path"] != r["from"]:
            continue
        root_key, _d, tail = key.partition(".")
        if r["kind"] == "namespace":
            if CM.name_key(r["rest"].lstrip(".")) == CM.name_key(key):
                return key
        elif r["kind"] == "named":
            if imp.get("named") == r["imported"] and (r["rest"] == ("." + tail if tail else "")):
                return key
        elif imp.get("default") and (r["rest"] == ("." + tail if tail else "")) and CM.name_key(tag.split(".")[0]) == CM.name_key(root_key):
            return key
    return None


def _scan_all(sources: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    return {rel: TS.scan_source(rel, text) for rel, text in sorted(sources.items())}


def _libusages(lib: Any, scans: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, Dict[str, Any]]]:
    out = []
    for rel, sc in scans.items():
        for u in sc["usages"]:
            key = _library_key(lib, u["tag"], sc["imports"])
            if key:
                out.append((key, rel, u))
    return out


def _rule_props(st: _State, lib: Any, uses: List[Tuple[str, str, Dict[str, Any]]], decided: Set[str]) -> None:
    for key, rel, u in uses:
        comp, det = lib.comps[key], lib.det.get(key)
        dep = DL.deprecation_of(det)
        if dep:
            msg = f"компонент {key} deprecated: {DL.deprecation_text(dep)}"
            rule = f"details.components.{key}.deprecations[component]"
            if dep.get("replacement"):
                msg += f"; замена {dep['replacement']}"
            if key in decided:
                st.dismiss("DS_DEPRECATED", f"план выбрал именно {key}: макет требует этот компонент — не чинить в коде",
                           file=rel, line=u["line"], prop=key)
            else:
                st.find("DS_DEPRECATED", rule, msg, file=rel, line=u["line"], prop=key)
        for p in u["props"]:
            name = p["name"]
            if name in IGNORED_PROPS or name.startswith(("data-", "aria-")) or (name.startswith("on") and name[2:3].isupper()):
                continue
            where = f"contract.components.{key}.props"
            for d in (det or {}).get("deprecations", []):
                if d["target"] == "prop" and d["name"] == name:
                    st.find("DS_DEPRECATED", f"details.components.{key}.deprecations[prop {name}]",
                            f"проп {name} компонента {key} deprecated: {DL.deprecation_text(d)}"
                            + (f"; замена {d['replacement']}" if d.get("replacement") else ""), file=rel, line=p["line"], prop=name)
            spec = comp["props"].get(name)
            if spec is None:
                if comp["propsClosed"]:
                    st.find("DS_PROP_WRONG", f"{where}: нет «{name}» (propsClosed)",
                            f"проп {name} вне контракта {key}", file=rel, line=p["line"], prop=name)
                else:
                    st.dismiss("DS_PROP_WRONG", f"контракт {key} не закрыт (propsClosed: false): проп «{name}» не запрещён",
                               file=rel, line=p["line"], prop=name)
                continue
            if spec["kind"] not in ("enum", "boolean"):
                continue                                 # usedValues — словарь, а не запрет: оценки нет
            if p["kind"] == "dynamic":
                st.dismiss("DS_PROP_WRONG", f"значение {name} не называется статически — оценки нет", file=rel, line=p["line"], prop=name)
                continue
            ok, _shown = DL.accepted_value(spec, comp["usedValues"].get(name, []), p["value"])
            if not ok:
                opts = spec.get("options") if spec["kind"] == "enum" else [True, False]
                st.find("DS_PROP_WRONG", f"{where}.{name}: kind={spec['kind']}, допустимо {opts}",
                        f"значение {DL.show(p['value'])} пропа {name} компонента {key} вне контракта", file=rel,
                        line=p["line"], prop=name, expected=opts, actual=p["value"])


def _chain(impl_node: Dict[str, Any]) -> List[str]:
    comp = impl_node.get("component") or {}
    names = list(comp.get("implNames") or [])
    if comp.get("implName") and comp["implName"] not in names:
        names.append(comp["implName"])
    return [n for n in names if isinstance(n, str) and n]


def _rule_identity(st: _State, lib: Any, decisions: Dict[str, Dict[str, Any]], pairs: Dict[str, Dict[str, Any]],
                   impl_nodes: Dict[str, Dict[str, Any]], used_keys: Dict[str, Tuple[str, int]]) -> int:
    checked = 0
    for d in sorted(decisions):
        dec = decisions[d]
        if dec["decision"] != "component":
            continue
        pair = pairs.get(d)
        if pair is None:
            continue                                     # узла нет на странице — MISSING уже сказал матчер
        name = dec["component"]
        want = CM.name_key(name)
        node = impl_nodes[pair["impl"]]
        low = pair.get("confidence") != "high"
        chain = _chain(node)
        rule = f"plan.sections[].nodes[{d}].component = {name}"
        checked += 1
        if chain:
            if any(CM.name_key(n) == want for n in chain):
                continue
            published = [n for n in chain if CM.name_key(n) in lib.by_key]
            if not published:
                continue                                 # fiber вне опубликованных: молчание, узел отдан скану исходников
            msg = f"на странице {published[0]} там, где план велел {name}"
            if low:
                st.dismiss("DS_COMPONENT_WRONG", "пара узлов неуверенная (позиция/краска): личность компонента по ней не судится",
                           design=d, impl=pair["impl"], prop=name)
            else:
                st.find("DS_COMPONENT_WRONG", rule, msg, design=d, impl=pair["impl"], expected=name, actual=published[0])
            continue
        tag = node.get("tag") or ""
        if tag and CM.name_key(tag) == want:
            st.dismiss("DS_COMPONENT_MISSED", f"разметка согласна с планом: <{tag}> — это и есть {name}", design=d, impl=pair["impl"], prop=name)
            continue
        key = next((k for k in lib.by_key.get(want, [])), None)
        if key and key in used_keys:
            f, ln = used_keys[key]
            st.dismiss("DS_COMPONENT_MISSED", f"исходники рендерят {key} из библиотеки ({f}:{ln}), fiber на странице не виден; "
                                              f"остаточный риск: в DOM это не подтверждено", design=d, impl=pair["impl"], prop=name)
            continue
        if low:
            st.dismiss("DS_COMPONENT_MISSED", "пара узлов неуверенная (позиция/краска): отсутствие компонента по ней не судится",
                       design=d, impl=pair["impl"], prop=name)
            continue
        st.find("DS_COMPONENT_MISSED", rule, f"план велел {name}, а компонента нет ни в fiber страницы, ни в исходниках outputs",
                design=d, impl=pair["impl"], expected=name)
    return checked


def _near(a: str, b: str) -> bool:
    pa, pb = _rgba(a), _rgba(b)
    if pa is None or pb is None or a == b:
        return False
    return all(abs(x - y) <= OFF_SCALE_CHANNEL for x, y in zip(pa[:3], pb[:3])) and abs(pa[3] - pb[3]) <= OFF_SCALE_ALPHA


def _rgba(s: str) -> Optional[Tuple[float, float, float, float]]:
    import re
    m = re.match(r"^rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)$", s)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))) if m else None


def _bound_colors(design_nodes: Dict[str, Dict[str, Any]], pairs: Dict[str, Dict[str, Any]], ignored: Set[str]) -> List[Tuple[str, str, str, str]]:
    """[(id макета, слот, имя токена, цвет в каноне)] — токены, привязанные к узлам, которые есть и на странице."""
    out = []
    for nid in sorted(design_nodes):
        n = design_nodes[nid]
        toks = n.get("tokens")
        if not isinstance(toks, dict) or nid not in pairs or nid in ignored or n.get("occluded") is True:
            continue
        style = n.get("style") or {}
        values = {"bg": style.get("background"), "color": n.get("color"), "border": (style.get("border") or {}).get("color")}
        for slot, src in TOKEN_SLOTS:
            if isinstance(toks.get(slot), str) and values.get(src):
                col = CT.normalize_color(values[src])
                if col:
                    out.append((nid, slot, toks[slot], col))
    return out


def _rule_tokens(st: _State, bound: List[Tuple[str, str, str, str]], scans: Dict[str, Dict[str, Any]], colors: Optional[Dict[str, str]],
                 colors_file: Optional[str]) -> None:
    lits = []
    for rel, sc in scans.items():
        if colors_file and rel == colors_file:
            continue                                      # объявления переменных — не использование
        for c in sc["colors"]:
            lits.append((rel, c))
    for nid, slot, token, color in bound:
        var = (colors or {}).get(color)
        for rel, c in lits:
            where = f"design token {token} ({slot}) = {color}"
            if c["value"] == color:
                if c["fallback"]:
                    st.dismiss("DS_TOKEN_MISSED", "литерал стоит запасным значением в var(--…, …): ссылка на токен есть",
                               design=nid, file=rel, line=c["line"], prop=token)
                else:
                    tail = f"; переменная проекта {var}" if var else ""
                    st.find("DS_TOKEN_MISSED", where, f"в макете токен {token}, в коде литерал {c['raw']}{tail}", design=nid, file=rel,
                            line=c["line"], prop=token, slot=slot, expected=token, actual=c["raw"])
            elif _near(c["value"], color) and not c["fallback"]:
                st.find("DS_OFF_SCALE", where, f"литерал {c['raw']} рядом с токеном {token}, но не равен ему: мимо шкалы", design=nid,
                        file=rel, line=c["line"], prop=token, slot=slot, expected=color, actual=c["value"])


def run(design: Any, impl: Any, matched: Dict[str, Any], plan: Any, lib: Any, sources: Optional[Dict[str, str]],
        colors: Optional[Dict[str, str]] = None, colors_file: Optional[str] = None, react_found: Optional[bool] = None) -> Dict[str, Any]:
    """Макет, страница, результат `match.match`, план, требования ДС (`ds_lookup.Library` | None), исходники outputs
    ({путь: текст}), карта цветов проекта ({rgba: ссылка}) → {ran, reason?, parts, findings, dismissals, degraded, stats}."""
    d_root, i_root = _root_of(design), _root_of(impl) if impl is not None else None
    if not isinstance(matched, dict) or not isinstance(matched.get("pairs"), list):
        raise DsGateError("ожидается результат match.match (с полем pairs)")
    parts: Dict[str, Dict[str, Any]] = {k: {"ran": False} for k in ("identity", "code", "tokens")}
    base = {"parts": parts, "findings": [], "dismissals": [], "degraded": [],
            "stats": {"findings": 0, "dismissals": 0, "byCode": {}, "usages": 0, "checkedNodes": 0}}

    def not_run(reason: str) -> Dict[str, Any]:
        out = dict(base, ran=False, reason=reason)
        for p in parts.values():
            p["reason"] = reason
        return out

    if lib is None:
        return not_run("нет документа требований ДС (daiver_ds_requirements): держать код не против чего")
    srcs = dict(sources or {})
    if not any(TS.is_js(r) for r in srcs):
        return not_run("среди outputs build-state нет JS/TS-файлов: сканировать нечего")
    dnodes = _index(d_root)
    decisions, foreign = plan_decisions(plan, set(dnodes))
    pairs = {p["design"]: p for p in matched["pairs"] if p.get("design") in dnodes}
    ignored = {k for k, v in decisions.items() if v["decision"] == "ignore"}
    bound = _bound_colors(dnodes, pairs, ignored)
    has_components = any(v["decision"] == "component" for v in decisions.values())
    if not has_components and not bound:
        return not_run("нечего держать против: в плане нет компонентов библиотеки и в макете нет привязанных токенов")

    st = _State()
    inodes = _index(i_root) if i_root else {}
    fibers = react_found if react_found is not None else any(_chain(n) for n in inodes.values())
    scans = _scan_all({r: t for r, t in srcs.items()})
    uses = _libusages(lib, scans)
    used_keys: Dict[str, Tuple[str, int]] = {}
    for key, rel, u in uses:
        used_keys.setdefault(key, (rel, u["line"]))
    decided = {v["component"] for v in decisions.values() if v["decision"] == "component" and v["component"] in lib.comps}
    parts["code"] = {"ran": True, "files": len(scans), "usages": len(uses)}
    _rule_props(st, lib, uses, decided)
    if foreign:
        st.degraded.append({"part": "identity", "reason": f"план называет узлы, которых нет в этом кадре ({len(foreign)}, например {foreign[0]}): "
                                                          f"похоже на план другого кадра"})
    if not has_components:
        parts["identity"] = {"ran": False, "reason": "в плане нет решений-компонентов: карты резолюций нет — идентичность не исполнялась"}
    elif not fibers:
        parts["identity"] = {"ran": False, "reason": "на странице нет имён React-компонентов (react.found: false): идентичность не исполнялась"}
    else:
        n = _rule_identity(st, lib, decisions, pairs, inodes, used_keys)
        parts["identity"] = {"ran": True, "checked": n}
    if bound:
        _rule_tokens(st, bound, scans, colors, colors_file)
        parts["tokens"] = {"ran": True, "bound": len(bound)}
    else:
        parts["tokens"] = {"ran": False, "reason": "в макете нет привязанных токенов у узлов, найденных на странице"}
    by_code: Dict[str, int] = {}
    for f in st.findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    findings = sorted(st.findings, key=lambda f: (CODES.index(f["code"]), f.get("design") or "", f.get("file") or "", f.get("line") or 0))
    dismissals = sorted(st.dismissals, key=lambda f: (CODES.index(f["code"]), f.get("design") or "", f.get("file") or "", f.get("line") or 0))
    return {"ran": True, "parts": parts, "findings": findings, "dismissals": dismissals, "degraded": st.degraded,
            "stats": {"findings": len(findings), "dismissals": len(dismissals), "byCode": dict(sorted(by_code.items())),
                      "usages": len(uses), "checkedNodes": parts["identity"].get("checked", 0)}}
