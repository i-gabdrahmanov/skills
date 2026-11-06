"""_testkit.py — общие помощники тестов spec-graph: проект на фикстуре и «ответы модели» через очередь.

Тесты шагов (1.3+) и e2e (3.6) гоняют настоящий цикл: `graph_next` → ответ из `fixtures/tz-mini/expected/`
→ инструмент результата с `item_id`. Так проверяется и движок, и контракт очереди, а не обход вокруг него.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

SCRIPTS = Path(__file__).resolve().parent
FIX = SCRIPTS.parent / "fixtures" / "tz-mini"
SRC_REL = "docs/spec"


def make_project(dst: Path, src: Path = FIX / "sources") -> Path:
    """Репо спеки во временном каталоге: исходники фикстуры в docs/spec, конфиг по ответам первого запуска."""
    from sgraph import tools as T
    shutil.copytree(src, Path(dst) / SRC_REL)
    T.call("graph_init", Path(dst), {"sources": [SRC_REL], "exclude": ["**/*-draft.md"]})
    return Path(dst)


def expected_drafts() -> Dict[str, Dict]:
    """Ответы модели на разведке по файлу (пути — от корня sources/)."""
    out = {}
    for p in sorted((FIX / "expected" / "drafts").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["file"]] = d
    return out


def _prefixed(node: Dict) -> Dict:
    n = dict(node)
    if n.get("sections"):
        n["sections"] = [f"{SRC_REL}/{s}" for s in n["sections"]]
    return n


def run_queue(root: Path, queue: str, answer: Callable[[Dict], Optional[Dict]], limit: int = 1000) -> List[Dict]:
    """Крутить очередь, как оркестратор: graph_next → answer(единица) → инструмент результата. answer
    возвращает аргументы инструмента (без item_id) или None — пропустить (единица останется в аренде)."""
    from sgraph import tools as T
    done = []
    for _ in range(limit):
        unit = T.call("graph_next", root, {"queue": queue})
        if unit["status"] != "единица":
            return done
        args = answer(unit)
        if args is None:
            return done
        done.append(T.call(unit["tool"], root, dict(args, item_id=unit["item_id"])))
    raise RuntimeError("очередь не закрылась за limit шагов")


def draft_answer(root: Path) -> Callable[[Dict], Dict]:
    from sgraph import queue as Q
    answers = expected_drafts()
    data = json.loads((root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}

    def answer(unit):
        rel = by_id[unit["item_id"]]["refs"]["file"][len(SRC_REL) + 1:]
        exp = answers[rel]
        sections = set(by_id[unit["item_id"]]["refs"]["sections"])
        nodes = [_prefixed(n) for n in exp["nodes"]]
        nodes = [n for n in nodes if not n.get("sections") or set(n["sections"]) <= sections]
        return {"nodes": nodes, "not_nodes": exp["not_nodes"]}
    return answer


def approve_catalog(root: Path) -> None:
    """Согласие человека на каталог (как оркестратор после «да»): record_approval --bind-file + снимок."""
    import subprocess
    import sys
    from sgraph import tools as T
    res = T.call("graph_catalog", root, {})
    rec = SCRIPTS.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
    r = subprocess.run([sys.executable, "-X", "utf8", str(rec), "--project", str(root), "--key", res["key"],
                        "--approved-by", "user", "--reason", "каталог утверждён", "--evidence", "да, утверждаю",
                        "--bind-file", ".spec-graph/catalog.json"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    assert T.call("graph_catalog", root, {})["consent"] == "ok"


def expected_plans() -> Dict[str, Dict]:
    out = {}
    for p in sorted((FIX / "expected" / "plans").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["file"]] = d
    return out


def assign_answer(root: Path) -> Callable[[Dict], Dict]:
    """Ответ раскладки на единицу: эталонный план файла, срезанный по блокам единицы."""
    from sgraph import queue as Q
    plans = expected_plans()
    data = json.loads((root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}

    def answer(unit):
        refs = by_id[unit["item_id"]]["refs"]
        rel = refs["file"][len(SRC_REL) + 1:]
        mine = set(refs["blocks"])
        pref = lambda ids: [f"{SRC_REL}/{b}" for b in ids if f"{SRC_REL}/{b}" in mine]  # noqa: E731
        plan = plans[rel]
        out = {"assign": [], "skip": [], "unplaced": []}
        for a in plan["assign"]:
            ids = pref(a["blocks"])
            if ids:
                out["assign"].append({"node": a["node"], "blocks": ids})
        for s in plan["skip"]:
            ids = pref(s["blocks"])
            if ids:
                out["skip"].append(dict(s, blocks=ids))
        return out
    return answer


def through_catalog(root: Path) -> None:
    """Разведка → черновики → каталог → согласие человека (для тестов шагов после каталога)."""
    from sgraph import tools as T
    T.call("graph_survey", root, {})
    run_queue(root, "draft-file", draft_answer(root))
    approve_catalog(root)


def through_split(root: Path) -> Dict:
    """… → каталог → раскладка эталоном → нарезка (для тестов связей и проверки)."""
    from sgraph import tools as T
    through_catalog(root)
    T.call("graph_assign_start", root, {})
    run_queue(root, "assign-section", assign_answer(root))
    return T.call("graph_split", root, {})


# ─── «модель-оракул»: отвечает по кандидатам любого корпуса (стресс-корпус 0.5) ──

def draft_oracle(root: Path) -> Callable[[Dict], Dict]:
    """Черновик единицы = её кандидаты как есть (так отвечает идеальная модель на чистом корпусе)."""
    from sgraph import queue as Q
    data = json.loads((root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}

    def answer(unit):
        refs = by_id[unit["item_id"]]["refs"]
        cands = json.loads((root / ".spec-graph" / "candidates" / (refs["file"] + ".json")).read_text(encoding="utf-8"))
        wanted = set(refs["candidates"])
        nodes = []
        for c in cands["candidates"]:
            if c["name_hint"] in wanted:
                n = {"kind": c["kind"], "name": c["name_hint"], "why": c["rule"]}
                for k in ("role", "subkind", "key", "service"):
                    if c.get(k):
                        n[k] = c[k]
                if c.get("section") and c["section"] in refs["sections"]:
                    n["sections"] = [c["section"]]
                nodes.append(n)
        return {"nodes": nodes, "not_nodes": []}
    return answer


def assign_oracle(root: Path) -> Callable[[Dict], Dict]:
    """Раскладка: самый внутренний раздел-кандидат → экран/сервис файла → заголовок в skip:toc → unplaced."""
    from sgraph import catalog as CT
    from sgraph import queue as Q
    data = json.loads((root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}
    names = {n["name"] for n in CT.load_approved(root)["nodes"]}

    def answer(unit):
        refs = by_id[unit["item_id"]]["refs"]
        cands = json.loads((root / ".spec-graph" / "candidates" / (refs["file"] + ".json")).read_text(encoding="utf-8"))
        cands = [c for c in cands["candidates"] if c["name_hint"] in names]
        inner = {}
        for c in cands:
            if c.get("section"):
                for b in c["blocks"]:
                    if b not in inner or len(c["blocks"]) < inner[b][0]:
                        inner[b] = (len(c["blocks"]), c["name_hint"])
        file_level = [c for c in cands if c["rule"] in ("R-screen-file", "R-service-file")]
        svc = next((c for c in file_level if c["rule"] == "R-service-file"), None)
        blocks = {b["id"]: b for b in json.loads((root / ".spec-graph" / "blocks" / (refs["file"] + ".json"))
                                                 .read_text(encoding="utf-8"))["blocks"]}
        assign, skip, unplaced = {}, [], []
        for b in refs["blocks"]:
            if b in inner:
                assign.setdefault(inner[b][1], []).append(b)
            elif file_level:
                fl = next((c for c in file_level if b in c["blocks"]), svc or file_level[0])
                assign.setdefault(fl["name_hint"], []).append(b)
            elif blocks[b]["type"] == "heading":
                skip.append(b)
            else:
                unplaced.append(b)
        return {"assign": [{"node": n, "blocks": ids} for n, ids in assign.items()],
                "skip": [{"blocks": skip, "reason": "toc"}] if skip else [],
                "unplaced": [{"blocks": unplaced}] if unplaced else []}
    return answer


def oracle_split(root: Path) -> Dict:
    """Весь graph-map на «модели-оракуле»: разведка → черновики → каталог → согласие → раскладка → нарезка."""
    from sgraph import tools as T
    T.call("graph_survey", root, {})
    run_queue(root, "draft-file", draft_oracle(root))
    approve_catalog(root)
    T.call("graph_assign_start", root, {})
    run_queue(root, "assign-section", assign_oracle(root))
    return T.call("graph_split", root, {})


def link_answer(root: Path) -> Callable[[Dict], Dict]:
    """Ответ модели по заметке: рёбра эталона, которые ставит не правило (expected/edges/model-edges.json)."""
    from sgraph import queue as Q
    edges = json.loads((FIX / "expected" / "edges" / "model-edges.json").read_text(encoding="utf-8"))
    data = json.loads((root / Q.QUEUE_REL / "link-note.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}
    return lambda unit: {"proposals": edges.get(by_id[unit["item_id"]]["refs"]["note"], [])}


def through_link(root: Path) -> None:
    """… → нарезка → механика → предложения модели эталоном."""
    from sgraph import tools as T
    through_split(root)
    T.call("graph_edges_mechanical", root, {})
    T.call("graph_link_start", root, {})
    run_queue(root, "link-note", link_answer(root))


def card_answer(root: Path) -> Callable[[Dict], Dict]:
    """Карточка из бизнес-описания фикстуры: строки «**Для чего:** …» → поле со ссылкой на свой блок."""
    import re
    from sgraph import blocks as B
    from sgraph import queue as Q
    data = json.loads((root / Q.QUEUE_REL / "card.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}
    marks = {"Для чего": "purpose", "Когда возникает": "when", "Что выполнить": "what", "Результат": "result"}

    def answer(unit):
        ids = by_id[unit["item_id"]]["refs"]["blocks"]
        fields = {k: "нет в источнике" for k in marks.values()}
        texts = {}
        for b in ids:
            rel = b.rsplit("#", 1)[0]
            texts.update({x["id"]: x["text"] for x in B.load(root, rel)["blocks"]})
        for b in ids:
            m = re.match(r"^- \*\*([^*:]+):\*\* (.+?)\.?\s*$", texts[b].strip())
            if m and m.group(1) in marks:
                fields[marks[m.group(1)]] = [{"text": m.group(2), "blocks": [b]}]
        return {"fields": fields}
    return answer
