"""split.py — нарезка в vault и проверка полноты (задача 1.6, рычаг 1, D6, D8, D11).

По утверждённому каталогу и плану раскладки (1.5) заметки пишет скрипт: текст ДОСЛОВНО, frontmatter по
онтологии, ребро `belongs_to` — из владельца. Полнота переноса (ТЗ §9.8) проверяется здесь механически:
каждый блок исходника — в заметке, в skip с причиной или в `_graph/unplaced.md`; рой сверщиков не нужен.

Тело заметки: заголовок-имя, затем блоки исходника между маркерами
`<!-- graph:source:start {"file", "blocks", "sha1"} -->` … `<!-- graph:source:end -->` (по маркеру на
файл-источник: source, source-2, …). Ссылки переписываются под Obsidian, и каждое переписывание пишется
в `.spec-graph/rewrites.json`: якорь `](#x)` → `[[#Заголовок|текст]]` (в той же заметке) или
`[[Заметка#Заголовок|текст]]`; вложение `](resources/…)` → копия в `_attachments/` и `![[файл|alt]]`.
Дословность = sha1 блоков исходника после ОБРАТНОГО применения переписываний.

Повторная нарезка бережная: проза человека вне маркеров, его ключи frontmatter и рёбра (их пишет синк
2.4) сохраняются; меняются только скаляры каталога и содержимое маркеров. Повтор без изменений входа —
0 изменённых файлов. Заметка узла, которого больше нет в каталоге, не удаляется — она в отчёте.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import assign as AS
from . import blocks as B
from . import catalog as CT
from . import extract as X
from . import fm as F
from . import keys as K
from . import ontology as O

REWRITES_REL = ".spec-graph/rewrites.json"
COVERAGE_REL = ".spec-graph/coverage.json"
_ANCHOR = re.compile(r"(?<!!)\[([^\]\n]+)\]\(#([^)\s]+)\)")
_ATTACH = re.compile(r"(!?)\[([^\]\n]*)\]\((?!https?:|#|mailto:)([^)\s]+\.(?:png|jpe?g|gif|svg|webp|pdf|puml|json|txt))\)", re.I)


class SplitError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U)
    return re.sub(r"\s+", "-", s)


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ─── что куда ──────────────────────────────────────────────────────────────

def _nodes(root: Path) -> List[Dict[str, Any]]:
    cat = CT.load_approved(root)
    nodes = [n for n in cat["nodes"] if n.get("in_scope", True)]
    names = {n["name"] for n in nodes}
    nodes += [n for n in AS.load_delta(root) if n["name"] not in names]
    return nodes


def _assignment(root: Path) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, Dict[str, Any]]]:
    """(блок → имя узла, блок → причина skip, блок → запись unplaced)."""
    owner, skip, unplaced = {}, {}, {}
    for p in AS.load_plans(root):
        for a in p["assign"]:
            for b in a["blocks"]:
                owner[b] = a["node"]
        for s in p["skip"]:
            for b in s["blocks"]:
                skip[b] = s["reason"]
        for u in p["unplaced"]:
            for b in u["blocks"]:
                unplaced[b] = u
    return owner, skip, unplaced


# ─── ссылки ────────────────────────────────────────────────────────────────

def _heading_index(files_blocks: Dict[str, List[Dict[str, Any]]], owner: Dict[str, str]) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """(файл, slug) → (имя заметки, текст заголовка): по сырому заголовку и по очищенному от нумерации."""
    idx = {}
    for rel, blocks in files_blocks.items():
        for b in blocks:
            if b["type"] == "heading" and b["id"] in owner:
                for t in (b["title"], X.clean_title(b["title"])):
                    idx.setdefault((rel, slug(t)), (owner[b["id"]], b["title"]))
    return idx


def _rewrite(text: str, block: Dict[str, Any], rel: str, note: str, hidx, root: Path, vault: Path,
             rewrites: List[Dict[str, Any]]) -> str:
    def anchor(m):
        target = hidx.get((rel, m.group(2).lower()))
        if not target:
            return m.group(0)
        tnote, heading = target
        new = f"[[#{heading}|{m.group(1)}]]" if tnote == note else f"[[{tnote}#{heading}|{m.group(1)}]]"
        rewrites.append({"block": block["id"], "from": m.group(0), "to": new})
        return new

    def attach(m):
        src = (Path(root) / rel).parent / m.group(3)
        if not src.is_file():
            return m.group(0)
        name = f"{hashlib.sha1(str(src.relative_to(root)).encode('utf-8')).hexdigest()[:8]}-{src.name}"
        dst = vault / "_attachments" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.is_file() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
        new = f"![[{name}|{m.group(2)}]]" if m.group(1) else f"[[{name}|{m.group(2)}]]"
        rewrites.append({"block": block["id"], "from": m.group(0), "to": new})
        return new

    return _ATTACH.sub(attach, _ANCHOR.sub(anchor, text))


def read_note(path: Path) -> Optional[str]:
    """Заметка как есть, без перевода строк (read_text превратил бы CRLF исходника в LF)."""
    p = Path(path)
    return p.read_bytes().decode("utf-8") if p.is_file() else None


def unrewrite(text: str, rewrites: List[Dict[str, Any]]) -> str:
    for r in rewrites:
        text = text.replace(r["to"], r["from"])
    return text


# ─── запись заметок ────────────────────────────────────────────────────────

def _fm_for(node: Dict[str, Any], ont: O.Ontology, existing: "OrderedDict[str, Any]", sources: List[str]) -> "OrderedDict[str, Any]":
    kind = node["kind"]
    order = ont.fm_keys(kind, node.get("role"))
    data: "OrderedDict[str, Any]" = OrderedDict(existing)
    data["kind"] = kind
    if node.get("subkind"):
        data["subkind"] = node["subkind"]
    if node.get("role"):
        data["role"] = node["role"]
    if node.get("key"):
        data["key"] = node["key"]
        scheme, rest = K.split_key(node["key"])
        if scheme == "http" and rest:
            method, _, path = rest.partition(" ")
            data["method"], data["path"] = method, path
            if not data.get("aliases"):
                data["aliases"] = [f"{method} {path}"]
    for k in order:
        if k not in data:
            data[k] = [] if (k in ont.edges or ont.edge_of_inverse(k) or k == "aliases") else None
    if ont.owned_by_service(kind) and node.get("service"):
        data["belongs_to"] = [F.link(node["service"])]
    if data.get("status") in (None, ""):
        data["status"] = "draft"
    data["source"] = sources
    data.pop("blocks", None)              # блоки — в атрибутах маркера source, во frontmatter не дублируются
    return data, order


def _note_text(node, ont, existing_text: Optional[str], groups: List[Tuple[str, List[Dict[str, Any]], str]],
               toc: Optional[str]) -> str:
    existing_fm, body = (F.read(existing_text) if existing_text else (OrderedDict(), ""))
    sources = []
    for rel, blks, _ in groups:
        heads = [b["title"] for b in blks if b["type"] == "heading"]
        sources.append(f"{rel}#{heads[0]}" if heads else rel)
    data, order = _fm_for(node, ont, existing_fm, sources)
    if not body.strip():
        body = f"# {node['name']}\n"
    # снять прежние маркеры source*, чтобы их число совпало с текущими файлами-источниками
    for b in F.blocks(body):
        if b["name"].startswith("source") and b["name"] != "source":
            lines = body.split("\n")
            body = "\n".join(lines[:b["start"]] + lines[b["end"] + 1:])
    for k, (rel, blks, text) in enumerate(groups):
        name = "source" if k == 0 else f"source-{k + 1}"
        attrs = {"file": rel, "blocks": [b["id"] for b in blks], "sha1": _sha("".join(b["text"] for b in blks))}
        body = F.set_block(body, name, text, attrs)
    if not groups and F.get_block(body, "source"):
        lines = body.split("\n")
        blk = F.get_block(body, "source")
        body = "\n".join(lines[:blk["start"]] + lines[blk["end"] + 1:])
    if toc is not None:
        body = F.set_block(body, "toc", toc)
    return F.write(data, body if body.endswith("\n") else body + "\n", order)


def split(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ok, why = CT.approved(root)
    if not ok:
        raise SplitError(why, "E-catalog-unapproved")
    ont = O.load(root)
    vault = Path(root) / cfg["vault"]
    nodes = _nodes(root)
    owner, skip, unplaced = _assignment(root)
    files = sorted({b.rsplit("#", 1)[0] for b in list(owner) + list(skip) + list(unplaced)} |
                   {s["file"] for n in nodes for s in n.get("sources", [])})
    files_blocks = {rel: (B.load(root, rel) or B.scan_file(root, rel))["blocks"] for rel in files if (Path(root) / rel).is_file()}
    hidx = _heading_index(files_blocks, owner)
    rewrites: List[Dict[str, Any]] = []
    per_node: Dict[str, List[Tuple[str, List[Dict[str, Any]], str]]] = {}
    for rel, blocks in files_blocks.items():
        run: Dict[str, List[Dict[str, Any]]] = OrderedDict()
        for b in blocks:
            if b["id"] in owner:
                run.setdefault(owner[b["id"]], []).append(b)
        for name, blks in run.items():
            text = "".join(_rewrite(b["text"], b, rel, name, hidx, root, vault, rewrites) for b in blks)
            per_node.setdefault(name, []).append((rel, blks, text))
    by_service: Dict[str, Dict[str, List[str]]] = {}
    for n in nodes:
        if n.get("service") and ont.owned_by_service(n["kind"]):
            by_service.setdefault(n["service"], {}).setdefault(n["kind"], []).append(n["name"])
    paths, written, unchanged = {}, 0, 0
    for n in nodes:
        path = ont.path_for(n["kind"], n["name"], n.get("service"), n.get("subkind"))
        if path in paths:
            raise SplitError(f"два узла в один файл {path}: «{paths[path]}» и «{n['name']}»", "E-split-path")
        paths[path] = n["name"]
        toc = None
        if ont.kinds[n["kind"]]["owner"] == "self":
            groups = by_service.get(n["name"], {})
            toc = "\n".join(f"**{ont.kinds[k]['title_ru']}:** " + ", ".join(F.link(x) for x in sorted(v))
                            for k, v in groups.items()) or "_операций нет_"
        p = vault / path
        old = read_note(p)
        new = _note_text(n, ont, old, per_node.get(n["name"], []), toc)
        if new != old:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(new.encode("utf-8"))      # байты: дословность CRLF исходника не трогаем
            written += 1
        else:
            unchanged += 1
    _write_unplaced(vault, files_blocks, unplaced)
    _write_index(vault, nodes, paths, ont)
    known = {Path(v) for v in paths}
    stray = sorted(str(q.relative_to(vault)) for q in vault.rglob("*.md")
                   if not str(q.relative_to(vault)).startswith("_graph/") and q.relative_to(vault) not in known)
    (Path(root) / REWRITES_REL).write_text(json.dumps(rewrites, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                          encoding="utf-8")
    cov = coverage(root, cfg, files_blocks, owner, skip, unplaced)
    return {"notes": len(nodes), "written": written, "unchanged": unchanged, "rewrites": len(rewrites),
            "unplaced": sum(f["unplaced"] for f in cov["files"].values()),
            "unassigned": sum(f["unassigned"] for f in cov["files"].values()), "stray_notes": stray[:20]}


def _write_unplaced(vault: Path, files_blocks, unplaced) -> None:
    lines = ["# Неразмещённое", "", "Блоки исходника, которым раскладка не нашла узла. Текст дословно; решает человек.", ""]
    for rel, blocks in files_blocks.items():
        mine = [b for b in blocks if b["id"] in unplaced]
        for b in mine:
            u = unplaced[b["id"]]
            hint = f" — предложен вид «{u['suggested_kind']}»" if u.get("suggested_kind") else ""
            lines += [f"## {b['id']}{hint}", "", b["text"].rstrip("\n"), ""]
    if len(lines) == 4:
        lines.append("_нет_")
    p = vault / "_graph" / "unplaced.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    if read_note(p) != text:
        p.write_bytes(text.encode("utf-8"))     # дословно, как и заметки


def _write_index(vault: Path, nodes, paths, ont) -> None:
    by_name = {v: k for k, v in paths.items()}
    lines = ["# Реестр узлов", "", "| узел | вид | сервис | key | путь |", "|---|---|---|---|---|"]
    for n in sorted(nodes, key=lambda x: (list(ont.kinds).index(x["kind"]), x["name"])):
        lines.append(f"| {F.link(n['name'])} | {n['kind']} | {n.get('service') or ''} | {n.get('key') or ''} | "
                     f"{by_name[n['name']]} |")
    text = "\n".join(lines) + "\n"
    p = vault / "_graph" / "index.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.is_file() or p.read_text(encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8")


# ─── полнота и дословность ─────────────────────────────────────────────────

def coverage(root: Path, cfg: Dict[str, Any], files_blocks=None, owner=None, skip=None, unplaced=None) -> Dict[str, Any]:
    if files_blocks is None:
        owner, skip, unplaced = _assignment(root)
        files = sorted({b.rsplit("#", 1)[0] for b in list(owner) + list(skip) + list(unplaced)})
        files_blocks = {rel: (B.load(root, rel) or B.scan_file(root, rel))["blocks"] for rel in files}
    out = {}
    for rel, blocks in files_blocks.items():
        ids = [b["id"] for b in blocks]
        reasons: Dict[str, int] = {}
        for b in ids:
            if b in skip:
                reasons[skip[b]] = reasons.get(skip[b], 0) + 1
        out[rel] = {"blocks": len(ids), "assigned": sum(1 for b in ids if b in owner), "skip": reasons,
                    "unplaced": sum(1 for b in ids if b in unplaced),
                    "unassigned": sum(1 for b in ids if b not in owner and b not in skip and b not in unplaced),
                    "unassigned_ids": [b for b in ids if b not in owner and b not in skip and b not in unplaced][:20]}
    data = {"files": out}
    (Path(root) / COVERAGE_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                          encoding="utf-8")
    return data


def verify(root: Path, cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Ошибки полноты и дословности: E-coverage (блок нигде), E-verbatim (маркер ≠ исходнику)."""
    errs: List[Tuple[str, str]] = []
    cov = coverage(root, cfg)
    for rel, f in cov["files"].items():
        for b in f["unassigned_ids"]:
            errs.append(("E-coverage", f"блок {b} не назначен ни узлу, ни skip, ни unplaced"))
    rw_p = Path(root) / REWRITES_REL
    rewrites = json.loads(rw_p.read_text(encoding="utf-8")) if rw_p.is_file() else []
    by_block: Dict[str, List[Dict[str, Any]]] = {}
    for r in rewrites:
        by_block.setdefault(r["block"], []).append(r)
    vault = Path(root) / cfg["vault"]
    texts = {}
    for p in sorted(vault.rglob("*.md")):
        if "/_graph/" in p.as_posix():
            continue
        try:
            _, body = F.read(read_note(p))
            found = F.blocks(body)
        except (F.FMError, ValueError) as e:
            errs.append(("E-verbatim", f"{p.relative_to(vault)}: не читается ({e})"))
            continue
        for blk in found:
            if not blk["name"].startswith("source"):
                continue
            ids = blk["attrs"].get("blocks") or []
            rel = blk["attrs"].get("file")
            if rel not in texts:
                data = B.load(root, rel) if rel else None
                texts[rel] = {b["id"]: b["text"] for b in data["blocks"]} if data else {}
            src = "".join(texts[rel].get(b, "") for b in ids)
            back = blk["content"]
            for b in ids:
                back = unrewrite(back, by_block.get(b, []))
            if not (Path(root) / rel).is_file():
                continue
            if back.strip("\n") != src.strip("\n") or _sha(src) != blk["attrs"].get("sha1"):
                errs.append(("E-verbatim", f"{p.relative_to(vault)}: текст маркера {blk['name']} не равен блокам исходника"))
    return errs
