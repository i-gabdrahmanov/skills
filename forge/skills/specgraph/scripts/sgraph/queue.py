"""queue.py — очередь работ: задачи собирает движок, модель исполняет по одной (задача 0.6, D23).

Зачем. GLM 5.3 не удержит в голове сотни единиц работы (20 сервисов × 40 секций): на длинной работе
она теряет пункты, путает сделанное, а после обрыва сессии начинает заново. Свой план работ слабая
модель составит так же плохо, а полный список единиц известен механически после скана. Поэтому
список держит движок, а оркестратор крутит цикл без планирования: `graph_next` → субагент →
результат инструментом с `item_id` → `graph_next` … → «пусто» → гейт.

Очередь — `.spec-graph/queue/<имя>.json` (имена: draft-file, assign-section, link-note, card,
selfcheck, improve). Единицы строит КОД движка (`build`) из артефактов; модель единиц не добавляет.
Вход единицы готовится заранее в `.spec-graph/queue/inputs/<id>.md` и вместе с брифом обязан
уложиться в бюджет (`queue.budget_chars`) — крупное режет генератор, а не модель.

Жизненный цикл: pending → leased (аренда, `graph_next`) → done (результат принят) | pending (отказ,
попытка + 1) → abandoned (после `max_attempts` отказов, с последними кодами — в отчёт человеку).
Аренда истекает (субагент оборвался) — единица снова pending. Повторный `graph_next` до сдачи результата
возвращает ту же единицу: после обрыва сессии работа продолжается с того же места, без повторов и пропусков.
Параллельные субагенты — `graph_next batch`: пачка до `queue.parallel` (по умолчанию 4) единиц, висящие в
аренде первыми; одновременные вызовы инструментов сериализует блокировка проекта (fsutil).

Результат принимает инструмент, объявивший в схеме `x-queue: <имя очереди>`: `tools.call` сверяет
`item_id` (своя очередь, единица в аренде) ДО работы инструмента и закрывает или возвращает её ПОСЛЕ.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import fsutil as FS

QUEUE_REL = ".spec-graph/queue"
QUEUES = ("draft-file", "assign-section", "link-note", "card", "selfcheck", "improve")
DEFAULTS = {"parallel": 4, "max_attempts": 3, "budget_chars": 24000, "lease_s": 1800}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#@-]{0,159}$")
EMPTY = "пусто"

_clock: Callable[[], float] = time.time


class QueueError(Exception):
    """Нарушение контракта очереди (чужая/закрытая единица, вход сверх бюджета)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def settings(cfg: Optional[Dict[str, Any]]) -> Dict[str, int]:
    out = dict(DEFAULTS)
    q = (cfg or {}).get("queue") or {}
    for k in DEFAULTS:
        if isinstance(q.get(k), int) and not isinstance(q.get(k), bool) and q[k] > 0:
            out[k] = q[k]
    return out


def _path(root: Path, name: str) -> Path:
    if name not in QUEUES:
        raise QueueError(f"неизвестная очередь «{name}» (есть: {', '.join(QUEUES)})", "E-queue-name")
    return Path(root) / QUEUE_REL / f"{name}.json"


def _input_path(root: Path, item_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", item_id)
    digest = hashlib.sha1(item_id.encode("utf-8")).hexdigest()[:8]
    return Path(root) / QUEUE_REL / "inputs" / f"{safe}-{digest}.md"


def _load(root: Path, name: str) -> Dict[str, Any]:
    p = _path(root, name)
    if not p.is_file():
        return {"queue": name, "version": 1, "items": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _save(root: Path, name: str, data: Dict[str, Any]) -> None:
    p = _path(root, name)
    FS.write_text(p, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def build(root: Path, name: str, specs: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
          brief_chars: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """(Пере)строить очередь из спецификаций единиц, которые выдал генератор шага.

    spec: {id, brief, tool, input (текст входа), refs?: {…}}. Порядок выдачи = порядок `specs`.
    Повторная сборка детерминирована и бережна: закрытые (done/abandoned) единицы с тем же id
    сохраняют исход, незакрытые — попытки; единица, которой больше нет в артефактах и которая не
    закрыта, уходит из очереди. Вход сверх бюджета — отказ всей сборки (генератор режет мельче)."""
    st = settings(cfg)
    brief_chars = brief_chars or {}
    seen, errors = set(), []
    for s in specs:
        iid = s.get("id")
        if not isinstance(iid, str) or not _ID_RE.match(iid):
            errors.append(f"id единицы «{iid}»: латиница, цифры, ._:#@-, до 160")
        elif iid in seen:
            errors.append(f"id «{iid}» повторяется")
        seen.add(iid)
        size = len(s.get("input") or "") + brief_chars.get(s.get("brief") or "", 0)
        if size > st["budget_chars"]:
            errors.append(f"вход «{iid}» — {size} знаков с брифом, бюджет {st['budget_chars']}: режь мельче")
    if errors:
        raise QueueError("; ".join(errors), "E-queue-build")
    old = {it["id"]: it for it in _load(root, name).get("items", [])}
    items = []
    for s in specs:
        prev = old.get(s["id"], {})
        item = {"id": s["id"], "brief": s.get("brief", ""), "tool": s.get("tool", ""),
                "refs": s.get("refs") or {}, "input_sha": hashlib.sha1((s.get("input") or "").encode("utf-8")).hexdigest(),
                "status": "pending", "attempts": 0, "lease_until": 0, "last_codes": []}
        if prev.get("status") in ("done", "abandoned") and prev.get("input_sha") == item["input_sha"]:
            item.update({k: prev[k] for k in ("status", "attempts", "last_codes")})
        elif prev:
            item["attempts"] = prev.get("attempts", 0) if prev.get("input_sha") == item["input_sha"] else 0
        p = _input_path(root, s["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.is_file() or p.read_text(encoding="utf-8") != (s.get("input") or ""):
            FS.write_text(p, s.get("input") or "", encoding="utf-8")
        items.append(item)
    data = {"queue": name, "version": 1, "items": items}
    _save(root, name, data)
    return counts(data)


def counts(data: Dict[str, Any]) -> Dict[str, Any]:
    c = {"pending": 0, "leased": 0, "done": 0, "abandoned": 0}
    for it in data.get("items", []):
        c[it["status"]] = c.get(it["status"], 0) + 1
    c["total"] = len(data.get("items", []))
    return c


def _expire(data: Dict[str, Any], now: float) -> None:
    for it in data["items"]:
        if it["status"] == "leased" and it.get("lease_until", 0) <= now:
            it["status"] = "pending"
            it["lease_until"] = 0


def lease(root: Path, name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Следующая единица в аренду — последовательный протокол: есть висящая в аренде — она же (продолжение
    после обрыва без пропусков). Параллельность (`parallel`) — только пачкой: `lease_batch`."""
    st = settings(cfg)
    data = _load(root, name)
    now = _clock()
    _expire(data, now)
    items = data["items"]
    total = len(items)
    done = sum(1 for it in items if it["status"] in ("done", "abandoned"))
    leased = [it for it in items if it["status"] == "leased"]
    if leased:
        it = min(leased, key=lambda x: x.get("lease_until", 0))
        repeat = True
    else:
        it = next((x for x in items if x["status"] == "pending"), None)
        repeat = False
        if it is None:
            _save(root, name, data)
            return {"status": EMPTY if not leased else "ждём сдачи", "queue": name,
                    "progress": f"{done} из {total}", "leased": [x["id"] for x in leased]}
    it["status"] = "leased"
    it["lease_until"] = now + st["lease_s"]
    _save(root, name, data)
    return {"status": "единица", "queue": name, "item_id": it["id"], "brief": it["brief"], "tool": it["tool"],
            "input": str(_input_path(root, it["id"]).relative_to(root)).replace("\\", "/"),
            "attempt": it["attempts"] + 1, "progress": f"{done + 1} из {total}", "repeat": repeat}


def _unit(root: Path, name: str, it: Dict[str, Any], done: int, total: int, k: int, repeat: bool) -> Dict[str, Any]:
    return {"queue": name, "item_id": it["id"], "brief": it["brief"], "tool": it["tool"],
            "input": str(_input_path(root, it["id"]).relative_to(root)).replace("\\", "/"),
            "attempt": it["attempts"] + 1, "progress": f"{done + k} из {total}", "repeat": repeat}


def lease_batch(root: Path, name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Пачка для параллельных субагентов: до `parallel` единиц. Висящие в аренде идут первыми (их субагент
    оборвался — пачка выдаётся, когда прошлая сдана), остальное — новые pending. Одна единица в пачке один раз:
    два субагента на одну единицу не получат её никогда."""
    st = settings(cfg)
    data = _load(root, name)
    now = _clock()
    _expire(data, now)
    items = data["items"]
    total = len(items)
    done = sum(1 for it in items if it["status"] in ("done", "abandoned"))
    picked = sorted((it for it in items if it["status"] == "leased"), key=lambda x: x.get("lease_until", 0))
    resumed = {it["id"] for it in picked}
    picked = picked[:st["parallel"]]
    for it in items:
        if len(picked) >= st["parallel"]:
            break
        if it["status"] == "pending":
            picked.append(it)
    for it in picked:
        it["status"] = "leased"
        it["lease_until"] = now + st["lease_s"]
    _save(root, name, data)
    if not picked:
        return {"status": EMPTY, "queue": name, "progress": f"{done} из {total}", "units": []}
    return {"status": "единицы", "queue": name, "progress": f"{done} из {total}", "parallel": st["parallel"],
            "units": [_unit(root, name, it, done, total, k, it["id"] in resumed) for k, it in enumerate(picked, 1)]}


def check_leased(root: Path, name: str, item_id: Any) -> None:
    """Результат принимается только по единице этой очереди, которая сейчас в аренде."""
    data = _load(root, name)
    it = next((x for x in data["items"] if x["id"] == item_id), None)
    if it is None:
        raise QueueError(f"item_id «{item_id}» нет в очереди {name}: бери единицу через graph_next", "E-queue-item")
    if it["status"] != "leased":
        raise QueueError(f"единица «{item_id}» не в аренде (статус {it['status']}): бери следующую через graph_next",
                         "E-queue-item")


def settle(root: Path, name: str, item_id: str, accepted: bool, codes: Optional[List[str]] = None,
           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Исход сдачи: принято → done; отказ → попытка + 1, pending или abandoned по лимиту."""
    st = settings(cfg)
    data = _load(root, name)
    it = next(x for x in data["items"] if x["id"] == item_id)
    it["lease_until"] = 0
    if accepted:
        it["status"] = "done"
    else:
        it["attempts"] = it.get("attempts", 0) + 1
        it["last_codes"] = list(codes or [])[:5]
        it["status"] = "abandoned" if it["attempts"] >= st["max_attempts"] else "pending"
    _save(root, name, data)
    return {"item_id": item_id, "status": it["status"], "attempts": it["attempts"]}


def status(root: Path, name: str, top: int = 5) -> Dict[str, Any]:
    data = _load(root, name)
    codes: Dict[str, int] = {}
    for it in data["items"]:
        for c in it.get("last_codes", []):
            codes[c] = codes.get(c, 0) + 1
    return {"queue": name, **counts(data),
            "abandoned_items": [{"item_id": it["id"], "codes": it.get("last_codes", [])}
                                for it in data["items"] if it["status"] == "abandoned"][:top],
            "top_codes": [[k, v] for k, v in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]}


def closed(root: Path, name: str) -> Tuple[bool, List[str]]:
    """Гейт: очередь закрыта, если всё done или abandoned. Второе — брошенные единицы для отчёта."""
    data = _load(root, name)
    open_ = [it for it in data["items"] if it["status"] not in ("done", "abandoned")]
    return (not open_, [it["id"] for it in data["items"] if it["status"] == "abandoned"])
