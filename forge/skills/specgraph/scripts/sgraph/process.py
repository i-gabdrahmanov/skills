"""process.py — проверки процесса целиком: цепочка «вход → шаги → результат» (2026-11-09).

Правила полноты из `completeness.json` смотрят на один узел: «у алгоритма нет запуска», «у обработчика нет
топика». Цепочку целиком они не видят: алгоритм, которого запускает другой алгоритм, формально запущен, даже
если ни один эндпойнт, обработчик или шедулер до этой пары не доходит; шаг, не упомянутый в тексте
обработчика, молча встаёт в конец «Хода обработки»; цикл запусков рисуется как «уже выше — цикл». Здесь — те
же блоки, что строит синк (`overview.View` по заметкам vault), и правило с `check` вместо ключей:

  • unordered — у узла два и больше шагов, и часть не упомянута в его тексте: порядок угадан;
  • cycle — шаги запускают друг друга по кругу (один раз на цикл, у наименьшего имени);
  • unreachable — шаг запускают, но ни одна цепочка вверх не доходит до входа (`flow.entries`, input).

Виды и рёбра — из `obsidian.json → flow` и онтологии; литералов видов здесь нет.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import fm as F
from . import ontology as O
from . import overview as OV

CHECKS = ("unordered", "cycle", "unreachable")


def check(ont: O.Ontology, nodes: Dict[str, Dict[str, Any]], rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дыры процесса по правилам с `check`: [{code, name, msg, question}]."""
    mine = [r for r in rules if r.get("check")]
    if not mine:
        return []
    v = OV.from_notes(ont, nodes)
    out = []
    for r in mine:
        for name, msg in _run(v, r["check"]):
            out.append({"code": r["id"], "name": name, "msg": msg, "question": r["question"].format(name=name)})
    return out


def _run(v: OV.View, what: str) -> List[tuple]:
    if what == "unordered":
        return [(n, "шаги без места в тексте: " + ", ".join(F.link(t) for t in v.unordered(n)))
                for n in sorted(v.nodes) if v.unordered(n)]
    if what == "cycle":
        return [(c[0], "цикл: " + " → ".join(F.link(x) for x in c + [c[0]])) for c in v.cycles()]
    if what == "unreachable":
        return [(n, "запускают " + ", ".join(F.link(c) for c in v.callers_of_step(n)) + ", но ни один вход до них не доходит")
                for n in sorted(v.nodes) if v.is_step(n) and v.callers_of_step(n) and not v.reaches_entry(n)]
    raise ValueError(f"проверки «{what}» нет: {', '.join(CHECKS)}")
