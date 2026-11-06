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
