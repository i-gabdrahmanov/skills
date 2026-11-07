"""status.py — `graph_status`: где проект, что делать дальше. Первый ход команды /graph.

Ответ короткий и однозначный (модель — GLM 5.3 Flash): инициализирован ли проект, а если нет —
вопросы первого запуска с кандидатами и доказательствами. Дальше по мере задач здесь появятся шаг
конвейера, очереди и здоровье графа (0.6, 4.1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import VERSION
from . import config as C


def status(root: Path) -> Dict[str, Any]:
    root = Path(root)
    cfg, errors = C.load(root)
    if errors:
        return {"engine": VERSION, "initialized": False, "config": C.CONFIG_REL, "errors": errors,
                "next": f"почини {C.CONFIG_REL} по списку errors (файл существует — graph_init его не перезапишет)"}
    if cfg is None:
        return {"engine": VERSION, "initialized": False, "config": C.CONFIG_REL,
                "questions": [
                    {"id": "sources", "ask": "Где лежит плоская спека? Один или несколько корней (каталог или "
                                             "файл), относительно корня репо.",
                     "candidates": C.detect(root)},
                    {"id": "exclude", "ask": "Что исключить из разбора (черновые копии, архивы)? glob-шаблоны; "
                                             "можно пусто.", "candidates": []},
                    {"id": "vault", "ask": f"Куда писать граф (vault Obsidian)? По умолчанию {C.DEFAULT_VAULT}.",
                     "candidates": [C.DEFAULT_VAULT]},
                ],
                "next": "задай вопросы пользователю (кандидаты — первыми, решает он), затем graph_init с ответами"}
    out = {"engine": VERSION, "initialized": True, "config": C.CONFIG_REL, "vault": cfg["vault"],
           "sources": [s["root"] for s in cfg["sources"]]}
    run = run_info(root)
    out["run"] = run
    if run.get("key") is None:
        out["next"] = (f"заведи прогон {run['next_key']}: выполни команду run.commands.init, затем снова graph_status")
    elif run.get("step") is None:
        out["next"] = (f"прогон {run['key']} завершён; новый прогон по спеке — команда run.commands.init_next "
                       f"({run['next_key']})")
    else:
        step = run["step"]
        first = {"graph-map": "graph_survey (разведка), дальше по брифу шага",
                 "graph-link": "graph_edges_mechanical, дальше по брифу шага",
                 "graph-check": "graph_check, дальше по брифу шага"}[step]
        out["next"] = (f"прогон {run['key']}, шаг {step}: {first}. Гейт шага прошёл — команда "
                       f"run.commands.gate, затем run.commands.close (шаг закрывает только гейт)")
    return out


ENGINE_SKILLS = Path(__file__).resolve().parents[3]          # …/skills (поставка)

STEPS = ("graph-map", "graph-link", "graph-check")
_GATE_ARG = {"graph-map": "map", "graph-link": "link", "graph-check": "check"}


def _py(inner: bool = False) -> str:
    """Интерпретатор движка (здоровый — им же запущен MCP), прямые слэши; внутри --cmd — в
    одинарных кавычках, если в пути пробел."""
    import sys
    exe = Path(sys.executable).as_posix()
    if " " in exe:
        exe = f"'{exe}'" if inner else f'"{exe}"'
    return f"{exe} -X utf8"


def run_info(root: Path) -> Dict[str, Any]:
    """Прогон ветки specgraph в машине состояний forge: ключ, текущий шаг и ТОЧНЫЕ команды.

    Без прогона (манифеста) шаги никуда не записывались: гейт «закрывал» шаг только на словах,
    actor-гейты (продуктивные инструменты единиц — только субагентам) молчали, согласия шли под
    ключом `-adhoc` (аудит 2026-11-07). Команды — готовые к запуску, пути поставки абсолютные."""
    import json
    import re
    base = Path(root) / "ground" / "statements" / "specgraph"
    nums, live = [], []
    for d in list(base.glob("GRAPH-*")) + list((Path(root) / "ground" / "archive").glob("**/GRAPH-*")):
        m = re.fullmatch(r"GRAPH-(\d+)", d.name)
        if m:
            nums.append(int(m.group(1)))
    for mp in sorted(base.glob("GRAPH-*/manifest.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            steps = json.loads(mp.read_text(encoding="utf-8")).get("steps") or []
        except (OSError, ValueError):
            continue
        pending = [s.get("id") for s in steps if isinstance(s, dict) and s.get("status") != "completed"
                   and s.get("id") in STEPS]
        live.append((mp.parent.name, pending[0] if pending else None))
    next_key = f"GRAPH-{max(nums) + 1 if nums else 1}"
    ps = ENGINE_SKILLS / "pipeline-state" / "scripts"
    init = (f"{_py()} {ps / 'init.py'} --project {root} --skill specgraph --feature {{key}} "
            f"--steps @{ENGINE_SKILLS / 'specgraph' / 'references' / 'manifest-steps.json'}")
    active = next((r for r in live if r[1] is not None), None)
    if active is None:
        info: Dict[str, Any] = {"key": live[0][0] if live else None, "step": None, "next_key": next_key,
                                "commands": {"init" if not live else "init_next": init.format(key=next_key)}}
        return info
    key, step = active
    gate = (f"{_py()} {ps / 'record_gate.py'} --project {root} --skill specgraph --feature {key} "
            f"--step-id {step} --cmd \"{_py(inner=True)} {ENGINE_SKILLS / 'specgraph' / 'scripts' / 'graph_gate.py'} "
            f"{_GATE_ARG[step]} --project {root}\"")
    close = (f"{_py()} {ps / 'update.py'} --project {root} --skill specgraph --feature {key} "
             f"--step-id {step} --status completed")
    return {"key": key, "step": step, "next_key": next_key, "commands": {"gate": gate, "close": close}}
