"""card.py — карточка бизнес-операции (задача 2.5, ТЗ §4, D8).

ТЗ хочет у бизнес-операции краткое описание: для чего, когда и при каких условиях, что выполнить,
ожидаемый результат. Это ЕДИНСТВЕННОЕ место, где граф пересказывает, а не переносит дословно. Поэтому
пересказ привязан к основаниям: каждое утверждение карточки ссылается на блок исходника из текста самой
операции, а полный текст лежит рядом дословно (маркер graph:source).

Очередь `card`: единица на заметку вида с ключом `task:` (бизнес-операция — по онтологии). Вход — блоки
операции с id. Ответ (`put`): четыре поля, каждое — до 3 утверждений `{text, blocks}` либо строка
«нет в источнике». Проверки: все поля есть; ссылки — только на блоки этой операции; текст без обратных
слэшей, не длиннее 300 знаков. Операция без бизнес-текста (заведена по коду типа задачи) — все поля
«нет в источнике», статус `stub`, вопрос аналитику в `.spec-graph/questions.json`.
Карточка пишется в маркер `graph:card` заметки; frontmatter меняется только статусом stub.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import blocks as B
from . import catalog as CT
from . import fm as F
from . import queue as Q
from . import resolve as RS

FIELDS = (("purpose", "Для чего"), ("when", "Когда и при каких условиях"), ("what", "Что необходимо выполнить"),
          ("result", "Ожидаемый результат"))
NONE = "нет в источнике"
BRIEF = "operation-card.md"
TOOL = "graph_card_put"
_MAX_STATEMENTS = 3
_MAX_LEN = 300
_BAD = re.compile(r"[\\\x00-\x08\x0b\x0c\x0e-\x1f]")


class CardError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def unit_id(name: str) -> str:
    return "card-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def _note_blocks(root: Path, cfg: Dict[str, Any], node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Блоки исходника, перенесённые в заметку (по атрибутам маркеров graph:source*)."""
    p = Path(root) / cfg["vault"] / node["path"]
    _, body = F.read(p.read_bytes().decode("utf-8"))
    out = []
    for blk in F.blocks(body):
        if not blk["name"].startswith("source"):
            continue
        rel = blk["attrs"].get("file")
        data = B.load(root, rel) if rel else None
        texts = {b["id"]: b for b in (data or {}).get("blocks", [])}
        out += [texts[b] for b in blk["attrs"].get("blocks") or [] if b in texts]
    return out


def _ops(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    from . import ontology as O
    ont = O.load(root)
    return [n for n in RS.vault_index(root, cfg) if ont.kinds[n["kind"]].get("key") == "task"]


def start(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ok, why = CT.approved(root)
    if not ok:
        raise CardError(why, "E-catalog-unapproved")
    specs = []
    for n in _ops(root, cfg):
        blocks = _note_blocks(root, cfg, n)
        lines = [f"# Карточка бизнес-операции «{n['name']}»", "", f"Ключ: {n.get('key') or 'нет'}", "",
                 "## Блоки операции (id — текст дословно)", ""]
        for b in blocks:
            lines += [f"### `{b['id']}`", "", b["text"].rstrip("\n"), ""]
        if not blocks:
            lines += ["_бизнес-текста нет: все поля — «нет в источнике»_", ""]
        lines.append(f"Сдай карточку инструментом `{TOOL}` с item_id этой единицы.")
        specs.append({"id": unit_id(n["name"]), "brief": BRIEF, "tool": TOOL, "input": "\n".join(lines) + "\n",
                      "refs": {"note": n["name"], "blocks": [b["id"] for b in blocks]}})
    counts = Q.build(root, "card", specs, cfg)
    return {"units": len(specs), "queue": counts, "next": "цикл очереди card: graph_next queue=card"}


def _unit(root: Path, item_id: str) -> Dict[str, Any]:
    data = json.loads((Path(root) / Q.QUEUE_REL / "card.json").read_text(encoding="utf-8"))
    return next(it for it in data["items"] if it["id"] == item_id)


def put(root: Path, cfg: Dict[str, Any], item_id: str, fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    unit = _unit(root, item_id)
    note, mine = unit["refs"]["note"], set(unit["refs"]["blocks"])
    errs: List[Tuple[str, str]] = []
    if not isinstance(fields, dict):
        return {}, [("E-card-shape", "fields: объект {purpose, when, what, result}")]
    for key, title in FIELDS:
        v = fields.get(key)
        if v is None:
            errs.append(("E-card-missing", f"нет поля {key} («{title}»): заполни или «{NONE}»"))
            continue
        if v == NONE:
            continue
        if not mine:
            errs.append(("E-card-stub", f"{key}: у операции нет бизнес-текста — только «{NONE}»"))
            continue
        if not isinstance(v, list) or not v or len(v) > _MAX_STATEMENTS:
            errs.append(("E-card-shape", f"{key}: 1–{_MAX_STATEMENTS} утверждения {{text, blocks}} или «{NONE}»"))
            continue
        for i, s in enumerate(v):
            if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip():
                errs.append(("E-card-shape", f"{key}[{i}]: {{text, blocks}}"))
                continue
            if _BAD.search(s["text"]) or len(s["text"]) > _MAX_LEN:
                errs.append(("E-card-text", f"{key}[{i}]: без обратных слэшей, до {_MAX_LEN} знаков"))
            refs = s.get("blocks") or []
            if not refs or any(b not in mine for b in refs):
                errs.append(("E-card-ref", f"{key}[{i}]: ссылки только на блоки этой операции (есть: {len(mine)})"))
    for k in sorted(set(fields) - {k for k, _ in FIELDS}):
        errs.append(("E-card-shape", f"лишнее поле «{k}»"))
    if errs:
        return {}, errs[:10]
    index = {n["name"]: n for n in RS.vault_index(root, cfg)}
    p = Path(root) / cfg["vault"] / index[note]["path"]
    data, body = F.read(p.read_bytes().decode("utf-8"))
    lines = []
    for key, title in FIELDS:
        v = fields[key]
        if v == NONE:
            lines.append(f"**{title}:** _{NONE}_")
        else:
            parts = [f"{s['text'].strip()} ↗ " + ", ".join(f"`{b}`" for b in s["blocks"]) for s in v]
            lines.append(f"**{title}:** " + " ".join(parts))
    body = F.set_block(body, "card", "\n\n".join(lines))
    stub = all(fields[k] == NONE for k, _ in FIELDS)
    if stub:
        data["status"] = "stub"
        qs_p = Path(root) / ".spec-graph" / "questions.json"
        qs = json.loads(qs_p.read_text(encoding="utf-8")) if qs_p.is_file() else []
        q = {"note": note, "edge": "card", "target": "", "reason": "у бизнес-операции нет описания в спеке (ТЗ §4)", "quote": ""}
        if q not in qs:
            qs.append(q)
            qs_p.write_text(json.dumps(qs, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    from . import ontology as O
    ont = O.load(root)
    p.write_bytes(F.write(data, body, ont.fm_keys(index[note]["kind"])).encode("utf-8"))
    return {"note": note, "stub": stub}, []
