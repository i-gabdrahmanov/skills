"""validate.py — валидатор графа (задача 3.1, ТЗ §8.1–8.5, §9.7–9.8).

Один валидатор с кодами — гейт шага check; его же зовут остальные шаги после записи. «Граф без ошибок»
значит одно и то же для человека (`_graph/report.md`), гейта и модели (`.spec-graph/validate.json`,
счётчики по кодам и первые строки с путями).

Проверки прототипа (validate_vault, deep_audit, edge_walk, connectivity_audit), но по онтологии: виды,
рёбра, обязательные ключи, пути, грамматика имён, допустимые концы, основания — из `ontology.json`.
В этом модуле нет ни одного имени вида или ребра (пинит test_validator.py).

Ошибки (E-*): fm-parse, fm-key, name, path, key-dup, ghost, edge-kind, inverse, evidence, verbatim,
coverage, index, marker. Предупреждения (W-*): orphan, manual-inverse, unplaced, source-missing.
Плюс дыры полноты по правилам `completeness.json` (3.2): раздел «Дыры графа» по бизнес-операциям и вопросы
в `_graph/open-questions.md`. Раздел «Перенос исходников» (3.5): по каждому файлу спеки — перенесён полностью
(каждый блок назначен, дословность цела, 0 E-* в его заметках) или что мешает. `write=False` — ничего не
пишет и отдаёт тексты отчёта и вопросов (так гейт check проверяет их свежесть).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import completeness as CM
from . import edges as E
from . import fm as F
from . import fsutil as FS
from . import ontology as O
from . import split as SP

REPORT_NAME = "report.md"
RESULT_REL = ".spec-graph/validate.json"
TOP = 10
_SKIP_DIRS = ("_graph", "_attachments")
_CELL = re.compile(r"(?<!\\)\|")


def _targets(v: Any) -> List[str]:
    vals = v if isinstance(v, list) else [v]
    return [t for t in (F.link_target(x) for x in vals) if t]


def _table(body: str, by_title: Dict[str, str]) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """Строки таблицы оснований graph:edges: (ребро, цель) → (цитата, источник)."""
    try:
        blk = F.get_block(body, "edges")
    except ValueError:
        return {}
    out = {}
    for line in (blk["content"].split("\n") if blk else []):
        cells = [c.strip() for c in _CELL.split(line)]
        if len(cells) < 6 or cells[1] not in by_title:
            continue
        target = F.link_target(cells[2])
        if target:
            out[(by_title[cells[1]], target)] = (cells[3].replace("\\|", "|"), cells[4])
    return out


class _Run:
    def __init__(self, root: Path, cfg: Dict[str, Any]):
        self.root, self.cfg = Path(root), cfg
        self.ont = O.load(root)
        self.vault = self.root / cfg["vault"]
        self.issues: List[Dict[str, str]] = []

    def add(self, code: str, path: str, msg: str) -> None:
        self.issues.append({"code": code, "path": path, "msg": msg})

    # ── чтение vault ──
    def read(self) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        seen: Dict[str, str] = {}
        for p in sorted(self.vault.rglob("*.md")):
            rel = p.relative_to(self.vault).as_posix()
            if rel.split("/")[0] in _SKIP_DIRS:
                continue
            try:
                data, body = F.read(p.read_bytes().decode("utf-8"))
            except (F.FMError, UnicodeDecodeError) as e:
                self.add("E-fm-parse", rel, f"frontmatter не читается: {str(e)[:160]}")
                continue
            kind = data.get("kind")
            if kind not in self.ont.kinds:
                self.add("E-fm-key", rel, f"kind «{kind}» — не вид онтологии ({', '.join(self.ont.kinds)})"
                         if kind else "нет ключа kind")
                continue
            name = p.stem
            if name in seen:
                self.add("E-name", rel, f"дубль имени «{name}»: уже есть {seen[name]}")
                continue
            seen[name] = rel
            owner = _targets(data.get(self._structural_edge(kind) or "", []))
            nodes[name] = {"name": name, "kind": kind, "role": data.get("role"), "subkind": data.get("subkind"),
                           "key": data.get("key"), "service": owner[0] if owner else None, "path": rel,
                           "data": data, "body": body}
        return nodes

    def _structural_edge(self, kind: str) -> Optional[str]:
        """Ребро принадлежности сервису (evidence optional, ведёт к виду-владельцу) — для вида с владельцем."""
        if not self.ont.owned_by_service(kind):
            return None
        for name, e in self.ont.edges.items():
            if e.get("evidence") == "optional" and self.ont.can_start(name, {"kind": kind}):
                return name
        return None

    # ── проверки узла ──
    def node(self, n: Dict[str, Any], nodes: Dict[str, Dict[str, Any]], keys: Dict[str, str]) -> None:
        ont, rel, data, kind = self.ont, n["path"], n["data"], n["kind"]
        spec = ont.kinds[kind]
        for msg in ont.check_name(kind, n["name"], n.get("service")):
            self.add("E-name", rel, msg)
        for f in spec.get("required") or []:
            if data.get(f) in (None, "", []):
                self.add("E-fm-key", rel, f"нет обязательного ключа «{f}» вида {kind}")
        role = data.get("role")
        if role is not None and role not in (spec.get("roles") or []):
            self.add("E-fm-key", rel, f"роль «{role}» — не из {spec.get('roles') or 'ролей вида нет'}")
            role = None
        allowed = set(ont.fm_keys(kind, role))
        for k in data:
            if k in ont.edge_keys() and k not in allowed and data.get(k):
                self.add("E-fm-key", rel, f"ключ-ребро «{k}» не для вида {kind}" + (f":{role}" if role else ""))
        try:
            want = ont.path_for(kind, n["name"], n.get("service"), data.get("subkind"))
            if want != rel:
                self.add("E-path", rel, f"путь по онтологии — {want}")
        except O.OntologyError as e:
            self.add("E-path", rel, str(e))
        if n.get("key"):
            k = str(n["key"])
            if k in keys:
                self.add("E-key-dup", rel, f"ключ «{k}» уже у {keys[k]}")
            else:
                keys[k] = rel
        for msg in F.marker_errors(n["body"]):
            self.add("E-marker", rel, msg)
        for k, v in data.items():
            for t in _targets(v):
                if t not in nodes:
                    self.add("E-ghost", rel, f"{k}: [[{t}]] — нет такой заметки")

    def aliases(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        """Алиас однозначен во всём vault: не совпадает с чужим именем и с алиасом другой заметки."""
        owner: Dict[str, str] = {}
        for name in sorted(nodes):
            vals = nodes[name]["data"].get("aliases") or []
            for a in (vals if isinstance(vals, list) else [vals]):
                if not isinstance(a, str) or not a.strip() or a == name:
                    continue
                if a in nodes:
                    self.add("E-name", nodes[name]["path"], f"алиас «{a}» — имя другой заметки")
                elif a in owner and owner[a] != name:
                    self.add("E-name", nodes[name]["path"], f"алиас «{a}» уже у [[{owner[a]}]]")
                else:
                    owner[a] = name

    def edges(self, nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Set[str]]]:
        """Прямые рёбра: допустимость концов и основания; возвращает ожидаемые обратные ключи."""
        ont = self.ont
        by_title = {e["title_ru"]: name for name, e in ont.edges.items()}
        tables = {name: _table(n["body"], by_title) for name, n in nodes.items()}
        texts: Dict[str, str] = {}

        def text(name: str) -> str:
            if name not in texts:
                texts[name] = E._ws(E.source_text(self.root, self.cfg, nodes[name]))
            return texts[name]
        expected: Dict[str, Dict[str, Set[str]]] = {}
        for name, n in nodes.items():
            for edge, spec in ont.edges.items():
                if edge not in n["data"] or not ont.can_start(edge, n):
                    continue
                for t in _targets(n["data"][edge]):
                    tn = nodes.get(t)
                    if tn is None:
                        continue                                  # E-ghost уже есть
                    ok, why = ont.edge_allowed(edge, n, tn)
                    if not ok:
                        self.add("E-edge-kind", n["path"], f"{edge} → [[{t}]]: {why}")
                    expected.setdefault(t, {}).setdefault(spec["inverse"], set()).add(name)
                    if spec.get("evidence") != "required":
                        continue
                    row = tables[name].get((edge, t))
                    if row is None and ont.is_symmetric(edge):
                        row = tables[t].get((edge, name))
                    if row is None:
                        self.add("E-evidence", n["path"], f"{edge} → [[{t}]]: нет основания в таблице graph:edges")
                    elif row[1] != "manual":
                        q = E._ws(row[0])
                        if len(q) < E.QUOTE_MIN or (q not in text(name) and q not in text(t)):
                            self.add("E-evidence", n["path"], f"{edge} → [[{t}]]: цитаты основания нет в тексте заметок")
        return expected

    def inverses(self, nodes: Dict[str, Dict[str, Any]], expected: Dict[str, Dict[str, Set[str]]]) -> None:
        ont = self.ont
        for name, n in nodes.items():
            for key in ont.fm_keys(n["kind"], n["data"].get("role") if n["data"].get("role") in
                                   (ont.kinds[n["kind"]].get("roles") or []) else None):
                edge = ont.edge_of_inverse(key)
                if edge is None:
                    continue
                want = expected.get(name, {}).get(key, set())
                have = set(_targets(n["data"].get(key, [])))
                if ont.is_symmetric(edge):
                    for t in sorted(want - have):
                        self.add("E-inverse", n["path"], f"{key}: нет зеркала [[{t}]] (у той стороны ссылка есть)")
                    continue
                for t in sorted(want - have):
                    self.add("E-inverse", n["path"], f"{key}: нет [[{t}]] (выводится из прямого ребра)")
                for t in sorted(have - want):
                    if t in nodes:
                        self.add("W-manual-inverse", n["path"], f"{key}: [[{t}]] без прямого ребра — синк перезапишет")

    def orphans(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        ont = self.ont
        structural = {k for name, e in ont.edges.items() if e.get("evidence") == "optional" for k in (name, e["inverse"])}
        for name, n in nodes.items():
            if ont.kinds[n["kind"]].get("owner") == "self":
                continue                                          # контейнер: связан принадлежностью
            if not any(_targets(v) for k, v in n["data"].items() if k in ont.edge_keys() and k not in structural):
                self.add("W-orphan", n["path"], "узел без рёбер (кроме принадлежности сервису)")

    def index(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        p = self.vault / "_graph" / "index.md"
        if not p.is_file():
            self.add("E-index", "_graph/index.md", "нет реестра узлов — graph_split")
            return
        rows = set()
        for line in p.read_text(encoding="utf-8").split("\n"):
            cells = [c.strip() for c in _CELL.split(line)]
            if len(cells) >= 7 and F.link_target(cells[1]):
                rows.add((F.link_target(cells[1]), cells[5]))
        files = {(name, n["path"]) for name, n in nodes.items()}
        for name, path in sorted(files - rows):
            self.add("E-index", "_graph/index.md", f"нет строки [[{name}]] ({path})")
        for name, path in sorted(rows - files):
            self.add("E-index", "_graph/index.md", f"строка [[{name}]] ({path}) — такой заметки нет")

    def sources(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        for code, msg in SP.verify(self.root, self.cfg):
            path = msg.split(":", 1)[0] if code == "E-verbatim" else ""
            self.add(code, path, msg)
        missing: Dict[str, List[str]] = {}
        for name, n in nodes.items():
            try:
                found = F.blocks(n["body"])
            except ValueError:
                continue
            for blk in found:
                rel = blk["attrs"].get("file")
                if blk["name"].startswith("source") and rel and not (self.root / rel).is_file():
                    missing.setdefault(rel, []).append(n["path"])
        for rel, paths in sorted(missing.items()):
            self.add("W-source-missing", paths[0], f"исходник {rel} удалён (заметок: {len(paths)}) — после приёмки норма")
        cov = self.cov = SP.coverage(self.root, self.cfg)
        for rel, f in sorted(cov["files"].items()):
            if f["unplaced"]:
                self.add("W-unplaced", rel, f"неразмещённых блоков: {f['unplaced']} (_graph/unplaced.md)")


def _transfer(run: "_Run", nodes: Dict[str, Dict[str, Any]], issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Перенос каждого файла спеки: блоки, неразмещённые, и что мешает считать его перенесённым полностью."""
    notes_of: Dict[str, set] = {}
    for n in nodes.values():
        try:
            found = F.blocks(n["body"])
        except ValueError:
            continue
        for blk in found:
            if blk["name"].startswith("source") and blk["attrs"].get("file"):
                notes_of.setdefault(blk["attrs"]["file"], set()).add(n["path"])
    out = []
    for rel, f in sorted(run.cov["files"].items()):
        why = []
        if f["unassigned"]:
            why.append(f"блоков без места: {f['unassigned']}")
        mine = notes_of.get(rel, set())
        errs = sorted({i["code"] for i in issues if i["code"].startswith("E-") and i["path"] in mine})
        if errs:
            why.append("ошибки в его заметках: " + ", ".join(errs))
        out.append({"file": rel, "blocks": f["blocks"], "unplaced": f["unplaced"], "notes": len(mine),
                    "present": (run.root / rel).is_file(), "full": not why, "why": why})
    return out


def validate(root: Path, cfg: Dict[str, Any], scope: Optional[str] = None, write: bool = True) -> Dict[str, Any]:
    run = _Run(root, cfg)
    nodes = run.read()
    keys: Dict[str, str] = {}
    for name in sorted(nodes):
        run.node(nodes[name], nodes, keys)
    run.aliases(nodes)
    expected = run.edges(nodes)
    run.inverses(nodes, expected)
    run.orphans(nodes)
    run.index(nodes)
    run.sources(nodes)
    holes = CM.check(root, nodes)
    questions = CM.write_questions(root, cfg, holes, write=write)
    issues = run.issues + [dict(h, hole=True) for h in holes]
    transfer = _transfer(run, nodes, issues)
    if scope:
        issues = [i for i in issues if not i["path"] or i["path"].startswith(scope.rstrip("/"))]
    issues.sort(key=lambda i: (not i["code"].startswith("E-"), i["code"], i["path"], i["msg"]))
    counts: Dict[str, int] = {}
    for i in issues:
        counts[i["code"]] = counts.get(i["code"], 0) + 1
    errors = sum(v for k, v in counts.items() if k.startswith("E-"))
    warnings = sum(v for k, v in counts.items() if k.startswith("W-"))
    result = {"notes": len(nodes), "errors": errors, "warnings": warnings, "counts": dict(sorted(counts.items())),
              "scope": scope or "", "issues": issues, "transfer": transfer}
    report = _render_report(result)
    if write:
        rp = Path(root) / RESULT_REL
        rp.parent.mkdir(parents=True, exist_ok=True)
        FS.write_text(rp, json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        p = report_path(root, cfg)
        if not p.is_file() or p.read_text(encoding="utf-8") != report:
            p.parent.mkdir(parents=True, exist_ok=True)
            FS.write_text(p, report, encoding="utf-8")
    top = [f"{i['code']} {i['path']}: {i['msg']}" if i["path"] else f"{i['code']}: {i['msg']}" for i in issues[:TOP]]
    out = {"notes": len(nodes), "errors": errors, "warnings": warnings, "counts": result["counts"], "top": top,
           "report": f"{cfg['vault']}/_graph/{REPORT_NAME}", "full": RESULT_REL,
           "transfer": [{"file": t["file"], "full": t["full"], "why": t["why"]} for t in transfer]}
    if not write:
        out.update(report_text=report, questions_text=questions)
    return out


def report_path(root: Path, cfg: Dict[str, Any]) -> Path:
    return Path(root) / cfg["vault"] / "_graph" / REPORT_NAME


def _render_report(result: Dict[str, Any]) -> str:
    lines = ["# Проверка графа", "",
             f"Заметок: {result['notes']}. Ошибок: {result['errors']}, предупреждений: {result['warnings']}."
             + (f" Область: {result['scope']}." if result["scope"] else ""), ""]
    if result["counts"]:
        lines += ["| код | сколько |", "|---|---|"] + [f"| {k} | {v} |" for k, v in result["counts"].items()] + [""]
    holes = [i for i in result["issues"] if i.get("hole")]
    if holes:
        lines += ["", "## Дыры графа", "", "Чего в графе нет, хотя по ТЗ должно быть. Вопросы — _graph/open-questions.md.", ""]
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for h in holes:
            for op in h["ops"] or [""]:
                groups.setdefault(op, []).append(h)
        for op in sorted(groups, key=lambda g: (not g, g)):
            lines += [f"### {F.link(op)}" if op else "### Без связи с бизнес-операцией", ""]
            lines += [f"- {h['code']} {F.link(h['name'])}: {h['msg']}" for h in groups[op]] + [""]
    code = None
    for i in result["issues"]:
        if i.get("hole"):
            continue
        if i["code"] != code:
            code = i["code"]
            lines += ["", f"## {code}", ""]
        where = f"`{i['path']}`: " if i["path"] else ""
        lines.append(f"- {where}{i['msg']}")
    if not result["issues"]:
        lines.append("Нарушений нет.")
    if result["transfer"]:
        lines += ["", "## Перенос исходников", "", "Перенесён полностью — каждый блок файла в заметке, в skip с причиной или в "
                  "_graph/unplaced.md, дословность цела, ошибок в его заметках нет. Удалять исходники — решение "
                  "пользователя: пайплайн их не трогает; после удаления их блоки дают W-source-missing, текст уже в vault.",
                  "", "| файл | блоков | заметок | неразмещённых | итог |", "|---|---|---|---|---|"]
        for t in result["transfer"]:
            verdict = "перенесён полностью" if t["full"] else "не полностью: " + "; ".join(t["why"])
            if not t["present"]:
                verdict += " (исходник удалён)"
            lines.append(f"| `{t['file']}` | {t['blocks']} | {t['notes']} | {t['unplaced']} | {verdict} |")
    return "\n".join(lines) + "\n"
