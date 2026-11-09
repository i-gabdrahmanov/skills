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
import posixpath
import re
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from . import assign as AS
from . import blocks as B
from . import catalog as CT
from . import extract as X
from . import fm as F
from . import fsutil as FS
from . import keys as K
from . import ontology as O
from . import overview as OV
from . import queue as Q

REWRITES_REL = ".spec-graph/rewrites.json"
STRUCTURE_REL = ".spec-graph/structure.json"
DEAD_LINKS_REL = ".spec-graph/dead-links.json"
COVERAGE_REL = ".spec-graph/coverage.json"
RETIRED_REL = ".spec-graph/retired"
_ANCHOR = re.compile(r"(?<!!)\[([^\]\n]+)\]\(#([^)\s]+)\)")
_MDLINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\((?!https?:|mailto:|#)([^)\s#]+?\.md)(#[^)\s]*)?\)", re.I)
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
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
    nodes += [n for n in AS.delta_resolution(root)[0] if n["name"] not in names]
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
    # блоки брошенной единицы раскладки — в unplaced: решение по ним за человеком (graph_queue_retry или
    # разбор unplaced.md), а не дыра покрытия, которую гейт map не пропустит никогда
    for it in Q.abandoned_items(root, "assign-section"):
        for blk in (it.get("refs") or {}).get("blocks") or []:
            if blk not in owner and blk not in skip and blk not in unplaced:
                unplaced[blk] = {"blocks": [blk], "reason": "единица раскладки брошена после трёх отказов"}
    _, remap = AS.delta_resolution(root)          # решения человека по узлам дельты (2.6)
    for b, node in list(owner.items()):
        if node in remap:
            if remap[node] is None:
                del owner[b]
                unplaced[b] = {"blocks": [b], "reason": f"узел дельты «{node}» убран человеком"}
            else:
                owner[b] = remap[node]
    return owner, skip, unplaced


def _follow_renames(nodes: List[Dict[str, Any]], owner: Dict[str, str]) -> Dict[str, set]:
    """План раскладки называет узел по имени; rename в каталоге после раскладки имя меняет, а прежнее
    остаётся в names_seen. Блок уходит узлу, который носил это имя и у которого этот файл — источник;
    раньше такие блоки молча пропадали из vault (боевой прогон 2026-11-07). Неоднозначные — в ответ."""
    names = {n["name"] for n in nodes}
    lost: Dict[str, set] = {}
    for b, name in list(owner.items()):
        if name in names:
            continue
        rel = b.rsplit("#", 1)[0]
        cand = [n["name"] for n in nodes if name in (n.get("names_seen") or [])
                and any(s.get("file") == rel for s in n.get("sources") or [])]
        if len(cand) == 1:
            owner[b] = cand[0]
        else:
            lost.setdefault(name, set()).add(rel)
    return lost


# ─── ссылки ────────────────────────────────────────────────────────────────

_HTML_ANCHOR = re.compile(r"<a\s+(?:name|id)\s*=\s*[\"']([^\"']+)[\"'][^>]*>\s*</a>", re.I)
_EMPTY_ANCHOR = re.compile(r"[ \t]*<a\s+(?:name|id)\s*=\s*[\"'][^\"']*[\"'][^>]*>\s*</a>[ \t]*", re.I)
_LINK_UNSAFE = re.compile(r"[\[\]|#^]")


def loose(anchor: str) -> str:
    """Ключ якоря без пунктуации: слаггеры GitHub, IDEA и GitLab по-разному обходятся с тире, точками,
    «ё» и повторными пробелами (`a — b` у GitHub — `a--b`, у нас было `a-b`), а якорь ещё бывает
    %-кодирован. Несовпадение оставляло ссылку `[текст](#якорь)` как есть — в Obsidian она никуда не
    ведёт, и переход «остаётся на текущей странице» (обратная связь аналитика 2026-11-09)."""
    s = _HTML_TAG.sub("", unquote(anchor)).lower().replace("ё", "е")
    return re.sub(r"[\W_]+", "", s)


def heading_text(title: str) -> str:
    """Текст заголовка, как его видит Obsidian после нарезки: без пустых HTML-якорей и без знаков, на
    которых ссылка `[[#…]]` рвётся."""
    t = _EMPTY_ANCHOR.sub(" ", title)
    return re.sub(r"\s+", " ", _LINK_UNSAFE.sub(" ", t)).strip()


def _heading_index(files_blocks: Dict[str, List[Dict[str, Any]]], owner: Dict[str, str]) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """(файл, ключ якоря) → (имя заметки, текст заголовка): по сырому заголовку, по очищенному от нумерации
    и по явным HTML-якорям (`<a name="x"></a>` в заголовке или отдельной строкой перед ним)."""
    idx = {}
    for rel, blocks in files_blocks.items():
        pending: List[str] = []
        for b in blocks:
            if b["type"] == "html":
                pending += _HTML_ANCHOR.findall(b["text"])
                continue
            if b["type"] == "heading" and b["id"] in owner:
                head = heading_text(b["title"])
                keys = [b["title"], X.clean_title(b["title"])] + _HTML_ANCHOR.findall(b["title"]) + pending
                for t in keys:
                    k = loose(t)
                    if k:
                        idx.setdefault((rel, k), (owner[b["id"]], head))
            if b["type"] != "blank":
                pending = []
    return idx


def _file_notes(files_blocks: Dict[str, List[Dict[str, Any]]], owner: Dict[str, str]) -> Dict[str, str]:
    """Файл исходника → заметка, которой ведёт ссылка на файл целиком: владелец первого заголовка файла."""
    out = {}
    for rel, blocks in files_blocks.items():
        first = next((b for b in blocks if b["type"] == "heading" and b["id"] in owner), None) \
            or next((b for b in blocks if b["id"] in owner), None)
        if first:
            out[rel] = owner[first["id"]]
    return out


class _Links:
    """Переписывание ссылок исходника под Obsidian. Всё переписанное — в rewrites, всё, что не нашло цели, —
    в dead (отчёт нарезки и W-link-dead валидатора)."""

    def __init__(self, root: Path, vault: Path, hidx, fidx, rewrites: List[Dict[str, Any]]):
        self.root, self.vault, self.hidx, self.fidx, self.rewrites = root, vault, hidx, fidx, rewrites
        self.dead: List[Dict[str, str]] = []

    def _wiki(self, tnote: str, heading: Optional[str], label: str, note: str, sep: str) -> str:
        if tnote == note:
            return f"[[#{heading}{sep}{label}]]" if heading else label
        return f"[[{tnote}#{heading}{sep}{label}]]" if heading else f"[[{tnote}{sep}{label}]]"

    def rewrite(self, text: str, block: Dict[str, Any], rel: str, note: str) -> str:
        # в строке таблицы `|` алиаса ссылки делит ячейку: Obsidian ждёт `[[цель\|текст]]`, иначе столбцы
        # съезжают (таблицы ЦАРС и File Adapter, обратная связь 2026-11-09)
        sep = "\\|" if block["type"] == "table" else "|"
        bid = block["id"]

        def record(old: str, new: str) -> str:
            if new != old:
                self.rewrites.append({"block": bid, "from": old, "to": new})
            return new

        def anchor(m):
            target = self.hidx.get((rel, loose(m.group(2))))
            if not target:
                self.dead.append({"note": note, "block": bid, "link": m.group(0)})
                return m.group(0)
            return record(m.group(0), self._wiki(target[0], target[1], m.group(1), note, sep))

        def mdlink(m):
            path = unquote(m.group(2).split("?", 1)[0])
            trel = posixpath.normpath(posixpath.join(posixpath.dirname(rel), path))
            frag = (m.group(3) or "")[1:]
            if frag:
                target = self.hidx.get((trel, loose(frag)))
                if target:
                    return record(m.group(0), self._wiki(target[0], target[1], m.group(1), note, sep))
            elif trel in self.fidx:
                if self.fidx[trel] == note:
                    return m.group(0)               # ссылка на свой же файл: вести некуда, не мёртвая
                return record(m.group(0), self._wiki(self.fidx[trel], None, m.group(1), note, sep))
            self.dead.append({"note": note, "block": bid, "link": m.group(0)})
            return m.group(0)

        def attach(m):
            src = (Path(self.root) / rel).parent / m.group(3)
            if not src.is_file():
                return m.group(0)
            name = f"{hashlib.sha1(str(src.relative_to(self.root)).encode('utf-8')).hexdigest()[:8]}-{src.name}"
            dst = self.vault / "_attachments" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.is_file() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
            new = f"![[{name}{sep}{m.group(2)}]]" if m.group(1) else f"[[{name}{sep}{m.group(2)}]]"
            return record(m.group(0), new)

        if block["type"] == "heading" and _EMPTY_ANCHOR.search(text):
            # пустой HTML-якорь в заголовке — разметка оглавления, а не текст: с ним заголовок в Obsidian
            # не совпадал с текстом ссылки [[#…]], и переход вёл в никуда
            head, _, rest = text.partition("\n")
            clean = re.sub(r"(#+)\s+", r"\1 ", _EMPTY_ANCHOR.sub(" ", head), count=1)
            clean = re.sub(r"[ \t]{2,}", " ", clean).rstrip()
            text = record(text, clean + ("\n" + rest if _ else ""))
        return _ATTACH.sub(attach, _MDLINK.sub(mdlink, _ANCHOR.sub(anchor, text)))


def read_note(path: Path) -> Optional[str]:
    """Заметка как есть, без перевода строк (read_text превратил бы CRLF исходника в LF)."""
    p = Path(path)
    return p.read_bytes().decode("utf-8") if p.is_file() else None


def unrewrite(text: str, rewrites: List[Dict[str, Any]]) -> str:
    for r in rewrites:
        if "insert" not in r:
            text = text.replace(r["to"], r["from"])
    return text


def replay(ids: List[str], texts: Dict[str, str], rewrites: List[Dict[str, Any]]) -> str:
    """Текст маркера, который нарезка должна была записать по блокам исходника: переписывания блока —
    по порядку записи, вставки (пустая строка у таблицы, строка «↳ вынесено») — на своём месте. Сверка
    дословности сравнивает с этим текстом, а не откатывает заметку: откат вставки «пустая строка» по
    тексту неоднозначен, по месту — точен."""
    by_block: Dict[str, List[Dict[str, Any]]] = {}
    for r in rewrites:
        by_block.setdefault(r["block"], []).append(r)
    out = []
    for b in ids:
        mine = by_block.get(b, [])
        t = texts.get(b, "")
        for r in mine:
            if "insert" not in r:
                t = t.replace(r["from"], r["to"])
        out += [r["text"] for r in mine if r.get("insert") == "before"] + [t] + \
               [r["text"] for r in mine if r.get("insert") == "after"]
    return "".join(out)


CARVED = "<!-- graph:carved -->"
CARVED_LINE = re.compile(r"^↳ [^\n]*" + re.escape(CARVED) + r"[ \t]*\r?$", re.M)


def strip_carved(text: str) -> str:
    """Текст заметки без строк «↳ вынесено»: это навигация нарезки, а не текст спеки — ни цитатой, ни
    упоминанием для правил она не служит."""
    return CARVED_LINE.sub("", text)


def _ends_blank(text: str) -> bool:
    return text.endswith("\n\n") or text.endswith("\n\r\n")


def _structure(files_blocks: Dict[str, List[Dict[str, Any]]], owner: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Вложенность разделов, разнесённых по заметкам: узел → {file, order, heading, parent}, где parent —
    узел ближайшего раздела-предка с другим владельцем. Так видно, из чего узел вынесен: алгоритм —
    из обработчика сообщения, шедулер — из сервиса. Без этого после нарезки четыре алгоритма лежали
    отдельно, и откуда они запускаются, было не восстановить (History Service, обратная связь 2026-11-09)."""
    out: Dict[str, Dict[str, Any]] = {}
    for rel in sorted(files_blocks):
        blocks = files_blocks[rel]
        secs = X.sections(blocks)
        for si, sec in enumerate(secs):
            hb = blocks[sec["head"]]
            node = owner.get(hb["id"])
            if node is None or node in out:
                continue
            parent, p = None, sec["parent"]
            while p is not None:
                po = owner.get(blocks[secs[p]["head"]]["id"])
                if po and po != node:
                    parent = po
                    break
                p = secs[p]["parent"]
            out[node] = {"file": rel, "order": sec["head"], "heading": sec["title"], "parent": parent,
                         "section": secs[sec["parent"]]["head"] if sec["parent"] is not None else None}
    return out


def _carved(files_blocks: Dict[str, List[Dict[str, Any]]], owner: Dict[str, str],
            structure: Dict[str, Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[Optional[str], List[Tuple[str, Optional[str]]]]]:
    """(файл, родитель) → {id блока родителя, перед которым строки «↳», или None — в конец: [(ребёнок,
    подпись)]}. Подпись — заголовок вынесенного раздела, если он стоял не под последним видимым заголовком
    родителя (раздел «5. Алгоритм …» после «4. REST API» иначе читался бы как часть REST API)."""
    out: Dict[Tuple[str, str], Dict[Optional[str], List[Tuple[str, Optional[str]]]]] = {}
    for child, st in sorted(structure.items(), key=lambda x: (x[1]["file"], x[1]["order"])):
        parent = st["parent"]
        if parent is None:
            continue
        blocks = files_blocks[st["file"]]
        nxt = next((b["id"] for b in blocks[st["order"] + 1:] if owner.get(b["id"]) == parent), None)
        last = max((i for i, b in enumerate(blocks[:st["order"]]) if b["type"] == "heading" and owner.get(b["id"]) == parent),
                   default=None)
        label = None if st.get("section") is None or st["section"] == last else st["heading"]
        out.setdefault((st["file"], parent), {}).setdefault(nxt, []).append((child, label))
    return out


def _assemble(rel: str, note: str, blks: List[Dict[str, Any]], links: "_Links",
              carved: Dict[Optional[str], List[Tuple[str, Optional[str]]]], rewrites: List[Dict[str, Any]]) -> str:
    """Текст заметки из блоков одного файла: ссылки переписаны, у границы таблицы — пустая строка, там,
    где вынесен раздел, — строка «↳ [[…]]». Obsidian не видит таблицу, вплотную прижатую к абзацу
    (GitHub и IDEA видят), — столбцы «съезжали» (обратная связь 2026-11-09)."""
    nl = "\r\n" if any("\r\n" in b["text"] for b in blks) else "\n"
    out, prev = "", None

    def insert(bid: str, where: str, text: str) -> None:
        nonlocal out
        rewrites.append({"block": bid, "insert": where, "text": text})
        out += text

    def carved_line(kids: List[Tuple[str, Optional[str]]]) -> str:
        sep = "" if not out or _ends_blank(out) else (nl if out.endswith("\n") else nl + nl)
        rows, plain = [], []
        for k, label in kids:
            if label is None:
                plain.append(F.link(k))
                continue
            if plain:
                rows.append(", ".join(plain))
                plain = []
            rows.append(f"«{label}» — {F.link(k)}")
        rows += [", ".join(plain)] if plain else []
        return sep + "".join(f"↳ {r} {CARVED}{nl}{nl}" for r in rows)

    for b in blks:
        t = b["text"] if b["type"] == "fence" else links.rewrite(b["text"], b, rel, note)
        if b["id"] in carved:
            insert(b["id"], "before", carved_line(carved[b["id"]]))
        if out and prev is not None and (b["type"] == "table") != (prev == "table") and not _ends_blank(out):
            insert(b["id"], "before", nl if out.endswith("\n") else nl + nl)
        out += t
        prev = b["type"]
    if carved.get(None) and blks:
        insert(blks[-1]["id"], "after", carved_line(carved[None]))
    return out


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
    for k in list(data):
        # пустые ключи-рёбра и пустые aliases не пишутся: свойство без значения аналитику ничего не говорит
        # («метаданные ради метаданных», обратная связь 2026-11-09); человек добавит ключ в Properties сам
        if data[k] == [] and (k in ont.edges or ont.edge_of_inverse(k) or k == "aliases" or k in ont.legacy_keys):
            del data[k]
    for k in ont.legacy_keys:
        data.pop(k, None)
    if ont.owned_by_service(kind) and node.get("service"):
        data["belongs_to"] = [F.link(node["service"])]
    if data.get("status") in (None, "") or (node.get("stub") and data.get("status") == "draft"):
        data["status"] = "stub" if node.get("stub") else "draft"
    elif data.get("status") == "stub" and not node.get("stub") and not ont.kinds[kind].get("key") == "task":
        data["status"] = "draft"          # таблицу описали разделом — заглушка стала узлом; у операции stub ставит карточка
    if sources:
        data["source"] = sources
    else:
        data.pop("source", None)          # своего текста нет (топик, заглушка) — пустое свойство не пишется
    data.pop("blocks", None)              # блоки — в атрибутах маркера source, во frontmatter не дублируются
    return data, order


def _note_text(node, ont, existing_text: Optional[str], groups: List[Tuple[str, List[Dict[str, Any]], str]],
               overview: Optional[str]) -> str:
    existing_fm, body = (F.read(existing_text) if existing_text else (OrderedDict(), ""))
    sources = []
    for rel, blks, _ in groups:
        heads = [b["title"] for b in blks if b["type"] == "heading"]
        sources.append(f"{rel}#{heads[0]}" if heads else rel)
    data, order = _fm_for(node, ont, existing_fm, sources)
    if not body.strip():
        body = f"# {node['name']}\n"
    # снять прежние маркеры source*, чтобы их число совпало с текущими файлами-источниками;
    # снизу вверх по одному разбору: срез верхнего блока сдвигает индексы нижних, и повторный
    # graph_split по заметке с source-2…source-N оставлял непарный маркер (боевой прогон 2026-11-07)
    lines = body.split("\n")
    for b in reversed(F.blocks(body)):
        if b["name"].startswith("source") and b["name"] != "source":
            del lines[b["start"]:b["end"] + 1]
    body = "\n".join(lines)
    for k, (rel, blks, text) in enumerate(groups):
        name = "source" if k == 0 else f"source-{k + 1}"
        attrs = {"file": rel, "blocks": [b["id"] for b in blks], "sha1": _sha("".join(b["text"] for b in blks))}
        body = F.set_block(body, name, text, attrs)
    if not groups and F.get_block(body, "source"):
        lines = body.split("\n")
        blk = F.get_block(body, "source")
        body = "\n".join(lines[:blk["start"]] + lines[blk["end"] + 1:])
    body = F.drop_block(body, "toc")      # до 2026-11-09 — строка ссылок по видам; теперь обзор сервиса
    if overview is not None and F.get_block(body, "overview") is None:
        body = F.set_block(body, "overview", overview, top=True)   # полный обзор пишет синк по связям
    return F.write(data, body if body.endswith("\n") else body + "\n", order)


def split(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ok, why = CT.approved(root)
    if not ok:
        raise SplitError(why, "E-catalog-unapproved")
    ren = renamed(root, cfg)
    if ren:
        raise SplitError(renamed_hint(ren), "E-split-renamed")
    ont = O.load(root)
    vault = Path(root) / cfg["vault"]
    nodes = _nodes(root)
    owner, skip, unplaced = _assignment(root)
    lost = _follow_renames(nodes, owner)
    if lost:
        raise SplitError("раскладка ссылается на узлы, которых нет в утверждённом каталоге: "
                         + "; ".join(f"«{k}» ({', '.join(sorted(v))})" for k, v in sorted(lost.items()))
                         + " — каталог поменяли после раскладки, а узел по имени и файлу не однозначен; "
                           "переразложи эти файлы или верни имя решением каталога", "E-split-unknown-node")
    files = sorted({b.rsplit("#", 1)[0] for b in list(owner) + list(skip) + list(unplaced)} |
                   {s["file"] for n in nodes for s in n.get("sources", [])} | set(AS.expected_files(root, cfg)))
    files_blocks = {rel: (B.load(root, rel) or B.scan_file(root, rel))["blocks"] for rel in files if (Path(root) / rel).is_file()}
    rewrites: List[Dict[str, Any]] = []
    links = _Links(root, vault, _heading_index(files_blocks, owner), _file_notes(files_blocks, owner), rewrites)
    structure = _structure(files_blocks, owner)
    carved = _carved(files_blocks, owner, structure)
    per_node: Dict[str, List[Tuple[str, List[Dict[str, Any]], str]]] = {}
    for rel, blocks in files_blocks.items():
        run: Dict[str, List[Dict[str, Any]]] = OrderedDict()
        for b in blocks:
            if b["id"] in owner:
                run.setdefault(owner[b["id"]], []).append(b)
        for name, blks in run.items():
            # код-блок не переписывается: пример `[x](#y)` или `![](a.png)` внутри фенса — это код, а не ссылка
            text = _assemble(rel, name, blks, links, carved.get((rel, name), {}), rewrites)
            per_node.setdefault(name, []).append((rel, blks, text))
    # обзор сервиса до связей — только состав по видам; входы, выходы и цепочки допишет синк
    view = OV.View(ont, [{"name": n["name"], "kind": n["kind"], "role": n.get("role"), "service": n.get("service")}
                         for n in nodes],
                   {n["name"]: {"belongs_to": [n["service"]]} for n in nodes
                    if n.get("service") and ont.owned_by_service(n["kind"])})
    paths, written, unchanged = {}, 0, 0
    folded: Dict[str, str] = {}
    for n in nodes:
        path = ont.path_for(n["kind"], n["name"], n.get("service"), n.get("subkind"))
        if O.fold(path) in folded:    # macOS/Windows: регистр (и NFC/NFD на APFS) в имени файла не различается
            raise SplitError(f"два узла в один файл {path}: «{folded[O.fold(path)]}» и «{n['name']}»",
                             "E-split-path")
        folded[O.fold(path)] = n["name"]
        paths[path] = n["name"]
        ov = None
        if ont.kinds[n["kind"]]["owner"] == "self":
            text = "".join(t for _, _, t in per_node.get(n["name"], []))
            ov = OV.service(view, n["name"], OV.purpose(text))
        p = vault / path
        old = read_note(p)
        new = _note_text(n, ont, old, per_node.get(n["name"], []), ov)
        if new != old:
            p.parent.mkdir(parents=True, exist_ok=True)
            FS.write_bytes(p, new.encode("utf-8"))      # байты: дословность CRLF исходника не трогаем
            written += 1
        else:
            unchanged += 1
    _write_unplaced(vault, files_blocks, unplaced)
    _write_index(vault, nodes, paths, ont)
    stray, retired = _retire_orphans(root, vault, {Path(v) for v in paths})
    FS.write_text((Path(root) / REWRITES_REL), json.dumps(rewrites, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                          encoding="utf-8")
    FS.write_text((Path(root) / STRUCTURE_REL), json.dumps(structure, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8")
    FS.write_text((Path(root) / DEAD_LINKS_REL), json.dumps(links.dead, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8")
    cov = coverage(root, cfg, files_blocks, owner, skip, unplaced)
    out = {"notes": len(nodes), "written": written, "unchanged": unchanged, "rewrites": len(rewrites),
           "unplaced": sum(f["unplaced"] for f in cov["files"].values()),
           "unassigned": sum(f["unassigned"] for f in cov["files"].values()), "stray_notes": stray[:20],
           "retired": retired[:20]}
    if links.dead:
        out["dead_links"] = {"count": len(links.dead), "top": [f"{d['note']}: {d['link']}" for d in links.dead[:10]],
                             "hint": "ссылки исходника без цели (якорь или файл не нашёлся) — в Obsidian они никуда "
                                     "не ведут; перечень — .spec-graph/dead-links.json, в отчёте проверки — W-link-dead"}
    return out


def renamed(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Заметки, переименованные в Obsidian: заметка движка вне каталога, чьи блоки принадлежат узлу каталога,
    а её имя не из имён этого узла (names_seen) — не решение каталога. Без этой проверки синк превращал все
    её связи в «внесено человеком» (цитаты пропадали), а нарезка уносила её в retired вместе с прозой
    аналитика и создавала заметку прежнего имени — ссылки vault, обновлённые Obsidian, висли (ревью 2026-11-08)."""
    if not (Path(root) / CT.APPROVED_REL).is_file():
        return []
    ont = O.load(root)
    vault = Path(root) / cfg["vault"]
    nodes = _nodes(root)
    owner, _, _ = _assignment(root)
    _follow_renames(nodes, owner)
    by_name = {n["name"]: n for n in nodes}
    expected = {ont.path_for(n["kind"], n["name"], n.get("service"), n.get("subkind")) for n in nodes}
    out = []
    for q in sorted(vault.rglob("*.md")) if vault.is_dir() else []:
        rel = q.relative_to(vault)
        if rel.parts[0] in ("_graph", "_attachments") or rel.as_posix() in expected:
            continue
        try:
            data, body = F.read(read_note(q))
            found = F.blocks(body)
        except (F.FMError, ValueError, UnicodeDecodeError):
            continue
        if not data.get("kind"):
            continue
        ids = [b for blk in found if blk["name"].startswith("source") for b in blk["attrs"].get("blocks") or []]
        node = next((owner[b] for b in ids if b in owner), None)
        if node in by_name and q.stem != node and q.stem not in (by_name[node].get("names_seen") or []):
            out.append({"note": rel.as_posix(), "name": q.stem, "node": node})
    return out


def renamed_hint(items: List[Dict[str, str]]) -> str:
    return ("заметки переименованы в Obsidian: " + "; ".join(f"«{r['name']}» ({r['note']}) — узел каталога «{r['node']}»"
                                                           for r in items[:5])
            + ". Имя узла меняет решение каталога, не файл: верни имя в Obsidian или прими новое решением "
              "graph_catalog_decide rename (node — прежнее имя, name — новое), затем graph_catalog и утверждение")


def _engine_note(path: Path, vault: Optional[Path] = None, ont: Optional[O.Ontology] = None) -> bool:
    """Заметка движка: вид онтологии и `source`, либо (у заметки без своего текста — топика, заглушки, где
    пустой `source` не пишется) лежит ровно по пути онтологии для своего имени."""
    try:
        data, _ = F.read(path.read_bytes().decode("utf-8"))
    except (F.FMError, UnicodeDecodeError):
        return False
    if not data.get("kind"):
        return False
    if "source" in data:
        return True
    if vault is None or ont is None or data["kind"] not in ont.kinds:
        return False
    svc = next((F.link_target(x) for x in data.get("belongs_to") or [] if F.link_target(x)), None)
    try:
        return ont.path_for(data["kind"], path.stem, svc, data.get("subkind")) == path.relative_to(vault).as_posix()
    except O.OntologyError:
        return False


def _retire_orphans(root: Path, vault: Path, known) -> Tuple[List[str], List[str]]:
    """Заметки vault вне каталога. Записанные движком (узел переименован/удалён решением каталога) —
    в RETIRED_REL: иначе старое имя остаётся в vault рядом с новым, и одноимённые заметки снова роняют
    graph_link_start и ссылки [[…]]. Заметки без frontmatter движка — человека, их только называем."""
    stray, retired = [], []
    for q in sorted(vault.rglob("*.md")):
        rel = q.relative_to(vault)
        if rel.parts[0] in ("_graph", "_attachments") or rel in known:
            continue
        if not _engine_note(q, vault, O.load(root)):
            stray.append(rel.as_posix())
            continue
        dst = Path(root) / RETIRED_REL / rel
        n = 1
        while dst.exists():
            dst = dst.with_name(f"{rel.stem}.{n}{rel.suffix}")
            n += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(q), str(dst))
        retired.append(rel.as_posix())
    return stray, retired


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
        FS.write_bytes(p, text.encode("utf-8"))     # дословно, как и заметки


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
        FS.write_text(p, text, encoding="utf-8")


# ─── полнота и дословность ─────────────────────────────────────────────────

def coverage(root: Path, cfg: Dict[str, Any], files_blocks=None, owner=None, skip=None, unplaced=None) -> Dict[str, Any]:
    if files_blocks is None:
        owner, skip, unplaced = _assignment(root)
        # и файлы, где нет ни одного назначения: файл без узлов иначе выпадал из покрытия целиком
        files = sorted({b.rsplit("#", 1)[0] for b in list(owner) + list(skip) + list(unplaced)}
                       | set(AS.expected_files(root, cfg)))
        files_blocks = {}
        for rel in files:
            data = B.load(root, rel) or ((Path(root) / rel).is_file() and B.scan_file(root, rel)) or None
            if data:
                files_blocks[rel] = data["blocks"]
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
    FS.write_text((Path(root) / COVERAGE_REL), json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                          encoding="utf-8")
    return data


def verify(root: Path, cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Ошибки полноты и дословности: E-coverage (блок нигде), E-verbatim (маркер ≠ исходнику),
    E-source-changed (файл спеки изменился после скана — кэш блоков и vault устарели)."""
    errs: List[Tuple[str, str]] = []
    cov = coverage(root, cfg)
    for rel, f in cov["files"].items():
        if B.changed_since_scan(root, rel):
            # нарезка и сверка ниже — по кэшу блоков; без этой проверки правка спеки после разведки давала
            # старый текст в vault при «дословно» и «перенесён полностью» (ревью 2026-11-08)
            errs.append(("E-source-changed", f"исходник {rel} изменён после разведки: в vault — текст прежней "
                                             "версии; graph_survey заново, затем шаги graph-map по изменённым единицам"))
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
            if not (Path(root) / rel).is_file():
                continue
            want = replay(ids, texts[rel], [r for b in ids for r in by_block.get(b, [])])
            if blk["content"].strip("\n") != want.strip("\n") or _sha(src) != blk["attrs"].get("sha1"):
                errs.append(("E-verbatim", f"{p.relative_to(vault)}: текст маркера {blk['name']} не равен блокам исходника"))
    return errs
