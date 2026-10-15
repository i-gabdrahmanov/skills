"""tools.py — реестр инструментов движка forge-ui (`daiver_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется (спека QG-S10).
Нарушения аргументов собираются в одно сообщение: слабой модели легче починить разом.

Этап 0: daiver_status, daiver_init, daiver_spec. Остальные инструменты спеки появляются по
этапам tasks/019-forge-ui.md; daiver_plan_sign не появится никогда — подпись плана в forge-ui
это согласие человека `ui-plan-<KEY>` (record_approval), а не инструмент модели.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from . import init as I
from . import specs as S
from . import status as ST
from . import uispec as U

HOOKS = Path(__file__).resolve().parents[4] / "hooks"


class ToolError(Exception):
    """Неверные аргументы инструмента — ответ isError, сервер продолжает работу."""


def _project_mod():
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    import _project  # noqa: WPS433 — общий резолвер путей forge (hooks/ рядом со skills/)
    return _project


def _check_args(args: Dict[str, Any], schema: Dict[str, Any]) -> None:
    errors: List[str] = []
    props = schema.get("properties", {})
    for k in args:
        if k not in props:
            errors.append(f"неизвестный аргумент «{k}» (допустимы: {', '.join(sorted(props)) or 'нет'})")
    for k in schema.get("required", []):
        if k not in args:
            errors.append(f"нет обязательного аргумента «{k}»")
    for k, v in args.items():
        spec = props.get(k)
        if not spec:
            continue
        t = spec.get("type")
        ok = {"string": isinstance(v, str), "object": isinstance(v, dict),
              "boolean": isinstance(v, bool),
              "array": isinstance(v, list) and all(isinstance(x, str) for x in v)}.get(t, True)
        if not ok:
            errors.append(f"«{k}»: ожидается {t}")
    if errors:
        raise ToolError("; ".join(errors))


def _rel_inside(root: Path, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or rel.startswith("~"):
        raise ToolError(f"путь «{rel}»: только относительный, внутри репо, без '..'")
    full = (root / p).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        raise ToolError(f"путь «{rel}» выходит за корень репо")
    return full


def t_status(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return ST.status(root)


def t_init(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    answers = args.get("answers")
    if answers is None:
        out = I.detect(root)
        out["status"] = ST.status(root)
        out["next"] = ("задай вопросы пользователю (кандидаты — с доказательствами, решение за ним), "
                       "затем вызови daiver_init с answers")
        return out
    return I.init(root, answers)


def t_spec(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    paths: List[Path] = [_rel_inside(root, p) for p in args.get("paths") or []]
    feature = args.get("feature")
    if feature:
        P = _project_mod()
        try:
            story_dir = P.feature_docs_dir(root) / P.safe_slug(feature)
        except ValueError as e:
            raise ToolError(str(e))
        ui = story_dir / U.SPEC_NAME
        if ui not in paths:
            paths.insert(0, ui)
    if not paths:
        raise ToolError("нужен feature (ключ стори) или paths (спеки от корня репо)")
    return S.parse_files([p for p in paths if p.is_file()], registered=[p for p in paths if not p.is_file()],
                         root=root)


TOOLS: Dict[str, Dict[str, Any]] = {
    "daiver_status": {
        "fn": t_status,
        "description": ("ПЕРВЫЙ ход forge-ui: состояние с диска — инициализирован ли проект "
                        "(.daiver/config.yaml), проблемы конфига, треки, следующий шаг. "
                        "Каталог .daiver/ руками не листай — ступень трека решает код."),
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "daiver_init": {
        "fn": t_init,
        "description": ("Первый запуск forge-ui в репо. Без answers — только кандидаты с "
                        "доказательствами и список вопросов (ничего не пишет). С answers — пишет "
                        ".daiver/config.yaml (+ component-map.yaml, если его нет) по ОТВЕТАМ "
                        "ПОЛЬЗОВАТЕЛЯ; существующий конфиг не перезаписывает. Значения не угадывай: "
                        "источник библиотеки, outputDir и buildCommand называет человек."),
        "schema": {"type": "object", "properties": {
            "answers": {"type": "object", "description": (
                "framework, language, styling, outputDir, buildCommand, librarySource, "
                "storybookMcp | scanPaths, libraryImport, iconsImport, conventions[], colorsFile, "
                "svgRender, vision (on|off)")}},
            "additionalProperties": False},
    },
    "daiver_spec": {
        "fn": t_spec,
        "description": ("Все спеки бизнес-логики стори одним ответом, разобранные структурно: "
                        "разделы Данные/Состояния/События/Валидации/Сценарии (с атрибуцией), "
                        "сценарии e2e с id <stem>#<n>, ссылки types:/client:, предупреждения. "
                        "feature — ключ стори (берётся её ui-spec.md); paths — доп. спеки от корня."),
        "schema": {"type": "object", "properties": {
            "feature": {"type": "string", "description": "ключ стори, напр. STOR-100"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "доп. файлы спек, пути от корня репо"}},
            "additionalProperties": False},
    },
}


def call(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    _check_args(args or {}, tool["schema"])
    fn: Callable[[Path, Dict[str, Any]], Dict[str, Any]] = tool["fn"]
    return fn(Path(root), args or {})
