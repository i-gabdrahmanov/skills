"""status.py — `graph_status`: где проект, что делать дальше. Первый ход команды /forge-graph.

Ответ короткий и однозначный (модель — GLM 5.3 Flash): инициализирован ли проект, а если нет —
вопросы первого запуска с кандидатами и доказательствами. Дальше по мере задач здесь появятся шаг
конвейера, очереди и здоровье графа (0.6, 4.1).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Union

from . import VERSION
from . import config as C
from . import fsutil as FS


def status(root: Path) -> Dict[str, Any]:
    root = Path(root)
    cfg, errors = C.load(root)
    if errors:
        return {"engine": VERSION, "initialized": False, "config": C.CONFIG_REL, "errors": errors,
                # «почини» вело модель в Edit, а запись в .spec-graph/ хук graph-guard закрывает — цикл отказов
                "next": f"покажи пользователю errors: {C.CONFIG_REL} правит он сам (запись туда моделью закрыта, "
                        f"graph_init существующий файл не перезапишет), затем снова graph_status"}
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
    if run.get("paused") and run.get("step") is None:
        out["next"] = (f"прогон {run['paused'][0]} на паузе. Спроси пользователя: вернуться к нему "
                       f"(run.commands.resume) или начать новый ({run['next_key']}, команда "
                       f"run.commands.{'init' if run.get('key') is None else 'init_next'})")
    elif run.get("key") is None:
        out["next"] = (f"заведи прогон {run['next_key']}: выполни команду run.commands.init, затем снова graph_status")
    elif run.get("step") is None:
        out["next"] = (f"прогон {run['key']} завершён; новый прогон по спеке — команда run.commands.init_next "
                       f"({run['next_key']})")
    else:
        step = run["step"]
        first = {"graph-map": _map_next(root),
                 "graph-link": "graph_edges_mechanical, дальше по брифу шага",
                 "graph-check": "graph_check, дальше по брифу шага"}[step]
        out["next"] = (f"прогон {run['key']}, шаг {step}: {first}. Гейт шага прошёл — команда "
                       f"run.commands.gate, затем run.commands.close (шаг закрывает только гейт)")
    return out


def _map_next(root: Path) -> str:
    """Где шаг graph-map сейчас — по очередям, а не всегда с начала: после раскладки и нарезки статус
    советовал «graph_survey (разведка)», и модель послабее начала бы шаг заново."""
    from . import queue as Q
    draft, assign = Q.status(root, "draft-file"), Q.status(root, "assign-section")
    if not draft["total"]:
        return "graph_survey (разведка), дальше по брифу шага"
    if draft["pending"] or draft["leased"]:
        return "цикл очереди draft-file (graph_next batch)"
    if not assign["total"]:
        return "graph_catalog — показ каталога и согласие пользователя, затем graph_assign_start"
    if assign["pending"] or assign["leased"]:
        return "цикл очереди assign-section (graph_next batch)"
    return "graph_split (нарезка vault), затем гейт шага"


ENGINE_SKILLS = Path(__file__).resolve().parents[3]          # …/skills (поставка)

STEPS = ("graph-map", "graph-link", "graph-check")
_GATE_ARG = {"graph-map": "map", "graph-link": "link", "graph-check": "check"}


_SHELL_SPECIAL = re.compile(r"[\s'\"$`&|;<>()*?!#~{}\[\]]")


def q(path: Union[str, Path], inner: bool = False) -> str:
    """Путь для готовой команды shell: прямые слэши (рантайм режет строку по POSIX, backslash съедается);
    с пробелом или спецсимволом — в кавычках, внутри --cmd — в одинарных. Без кавычек каталог
    «My Spec Repo» превращал `--project` в `…/My` и лишний аргумент `Spec` (ревью 2026-11-08)."""
    s = str(path).replace("\\", "/")
    if _SHELL_SPECIAL.search(s):
        return f"'{s}'" if inner else f'"{s}"'
    return s


def _py(inner: bool = False) -> str:
    """Интерпретатор движка (здоровый — им же запущен MCP), прямые слэши, в кавычках при пробеле."""
    import sys
    return f"{q(sys.executable, inner)} -X utf8"


def script(rel: str) -> str:
    """Запуск скрипта поставки: интерпретатор движка + абсолютный путь. Подсказки вида
    «python3 <поставка>/…» модель достраивала сама — нашла копию поставки в ~/Downloads, голый
    python3 по чужому пути политика shell форка отклоняла, согласие не записывалось (боевой
    прогон 2026-11-08); тем же префиксом, что у гейта, вызов проходит."""
    return f"{_py()} {q(ENGINE_SKILLS / rel)}"


def run_info(root: Path) -> Dict[str, Any]:
    """Прогон ветки specgraph в машине состояний forge: ключ, текущий шаг и ТОЧНЫЕ команды.

    Без прогона (манифеста) шаги никуда не записывались: гейт «закрывал» шаг только на словах,
    actor-гейты (продуктивные инструменты единиц — только субагентам) молчали, согласия шли под
    ключом `-adhoc` (аудит 2026-11-07). Команды — готовые к запуску, пути поставки абсолютные."""
    import json
    import re
    base = Path(root) / "ground" / "statements" / "specgraph"
    nums, live, paused = [], [], []
    for d in list(base.glob("GRAPH-*")) + list((Path(root) / "ground" / "archive").glob("**/GRAPH-*")):
        m = re.fullmatch(r"GRAPH-(\d+)", d.name)
        if m:
            nums.append(int(m.group(1)))
    for mp in sorted(base.glob("GRAPH-*/manifest.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if FS.run_paused(mp.parent):
            # на паузе (pause.py): хуки его не видят, update.py шаги не двигает — активным не считаем
            paused.append(mp.parent.name)
            continue
        try:
            steps = json.loads(mp.read_text(encoding="utf-8")).get("steps") or []
        except (OSError, ValueError):
            continue
        pending = [s.get("id") for s in steps if isinstance(s, dict) and s.get("status") != "completed"
                   and s.get("id") in STEPS]
        live.append((mp.parent.name, pending[0] if pending else None))
    next_key = f"GRAPH-{max(nums) + 1 if nums else 1}"
    ps = ENGINE_SKILLS / "pipeline-state" / "scripts"
    steps = q(f"@{ENGINE_SKILLS / 'specgraph' / 'references' / 'manifest-steps.json'}")
    init = (f"{_py()} {q(ps / 'init.py')} --project {q(root)} --skill specgraph --feature {{key}} "
            f"--steps {steps}")
    active = next((r for r in live if r[1] is not None), None)
    if active is None:
        info: Dict[str, Any] = {"key": live[0][0] if live else None, "step": None, "next_key": next_key,
                                "commands": {"init" if not live else "init_next": init.format(key=next_key)}}
        if paused:
            info["paused"] = paused
            info["commands"]["resume"] = (f"{_py()} {q(ps / 'pause.py')} --project {q(root)} resume "
                                          f"{paused[0]}")
        return info
    key, step = active
    gate = (f"{_py()} {q(ps / 'record_gate.py')} --project {q(root)} --skill specgraph --feature {key} "
            f"--step-id {step} --cmd \"{_py(inner=True)} {q(ENGINE_SKILLS / 'specgraph' / 'scripts' / 'graph_gate.py', True)} "
            f"{_GATE_ARG[step]} --project {q(root, True)}\"")
    close = (f"{_py()} {q(ps / 'update.py')} --project {q(root)} --skill specgraph --feature {key} "
             f"--step-id {step} --status completed")
    return {"key": key, "step": step, "next_key": next_key, "commands": {"gate": gate, "close": close}}
