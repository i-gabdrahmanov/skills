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
    return {"engine": VERSION, "initialized": True, "config": C.CONFIG_REL, "vault": cfg["vault"],
            "sources": [s["root"] for s in cfg["sources"]],
            "next": "конвейер ещё не реализован (spec-graph 0.1): шаги graph-map/graph-link/graph-check "
                    "отвечают «не реализовано»"}
