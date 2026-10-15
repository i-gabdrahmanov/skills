"""recipe.py — рецепт открытия кадра: шаги действий в браузере и область снимка (спека §14.2, §20.4).

Рецепт выработан ОДИН раз тем, кто видел страницу, записан в track.yaml и проигрывается без
человека — поэтому глаголы ограничены списком и среди них нет «выполнить JS» (рецепт коммитится,
и произвольный код в нём был бы дырой). Валидация тут общая: манифест трека проверяет рецепт при
записи, daiver_screenshot — перед проигрыванием.
"""
from __future__ import annotations

from typing import Any, Dict, List

ACTION_VERBS = ("click", "dblclick", "hover", "fill", "press", "select", "waitFor", "waitForGone",
                "wait", "scrollTo")
MAX_ACTIONS = 30
MAX_WAIT_MS = 60000
# глагол → (обязательные аргументы, допустимые необязательные)
_ARGS: Dict[str, tuple] = {
    "click": (("selector",), ()),
    "dblclick": (("selector",), ()),
    "hover": (("selector",), ()),
    "fill": (("selector", "text"), ()),
    "press": (("key",), ("selector",)),
    "select": (("selector", "value"), ()),
    "waitFor": (("selector",), ("ms",)),
    "waitForGone": (("selector",), ("ms",)),
    "wait": (("ms",), ()),
    "scrollTo": (("selector",), ()),
}
RECIPE_KEYS = ("actions", "clipSelector", "waitForSelector", "waitForGone")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_actions(actions: Any, path: str = "open.actions") -> List[str]:
    if not isinstance(actions, list):
        return [f"{path}: ожидается список шагов"]
    problems: List[str] = []
    if len(actions) > MAX_ACTIONS:
        problems.append(f"{path}: не больше {MAX_ACTIONS} шагов, получено {len(actions)}")
    for i, a in enumerate(actions[:MAX_ACTIONS]):
        where = f"{path}[{i}]"
        if not isinstance(a, dict):
            problems.append(f"{where}: шаг — объект {{verb, …}}")
            continue
        verb = a.get("verb")
        if verb not in ACTION_VERBS:
            hint = " («выполнить JS» среди глаголов нет намеренно — рецепт коммитится и " \
                   "проигрывается без человека)" if str(verb).lower() in ("evaluate", "eval", "js", "script") else ""
            problems.append(f"{where}.verb: «{verb}» — допустимы {', '.join(ACTION_VERBS)}{hint}")
            continue
        required, optional = _ARGS[verb]
        allowed = {"verb"} | set(required) | set(optional)
        for k in a:
            if k not in allowed:
                problems.append(f"{where}.{k}: лишний аргумент для «{verb}» (допустимы {', '.join(sorted(allowed - {'verb'}))})")
        for k in required:
            if k not in a:
                problems.append(f"{where}: для «{verb}» нужен аргумент «{k}»")
        for k in ("selector", "text", "key", "value"):
            if k in a and not (isinstance(a[k], str) and a[k] != ""):
                problems.append(f"{where}.{k}: непустая строка")
        if "ms" in a and not (_is_num(a["ms"]) and 0 <= a["ms"] <= MAX_WAIT_MS):
            problems.append(f"{where}.ms: число миллисекунд 0…{MAX_WAIT_MS}")
    return problems


def validate_recipe(recipe: Any, path: str = "open") -> List[str]:
    """Рецепт: {actions[], clipSelector?, waitForSelector?, waitForGone?} — пустой тоже допустим."""
    if not isinstance(recipe, dict):
        return [f"{path}: ожидается объект {{actions, clipSelector}}"]
    problems = [f"{path}.{k}: неизвестное поле (допустимы {', '.join(RECIPE_KEYS)})"
                for k in recipe if k not in RECIPE_KEYS]
    if "actions" in recipe:
        problems += validate_actions(recipe["actions"], f"{path}.actions")
    for k in ("clipSelector", "waitForSelector", "waitForGone"):
        if k in recipe and not (isinstance(recipe[k], str) and recipe[k]):
            problems.append(f"{path}.{k}: непустой CSS-селектор")
    return problems
