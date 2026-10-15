"""tools.py — реестр инструментов движка forge-ui (`daiver_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется (спека QG-S10).
Нарушения аргументов собираются в одно сообщение: слабой модели легче починить разом.

Сейчас: daiver_status, daiver_init, daiver_spec, daiver_track_manifest, daiver_source_nodes,
daiver_pixso_pull.
Остальные инструменты спеки появляются по задачам docs/forge-ui/; daiver_plan_sign не появится
никогда — подпись плана в forge-ui это согласие человека `ui-plan-<KEY>` (record_approval), а не
инструмент модели.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from . import init as I
from . import source_nodes as SN
from . import specs as S
from . import status as ST
from . import track as TR
from . import uispec as U
from .pixso import pull as PP

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


def _path_inside(root: Path, rel: str) -> Path:
    """Путь внутри репо: относительный либо абсолютный, но лежащий под корнем (без «..»)."""
    p = Path(rel)
    if p.is_absolute():
        try:
            p.resolve().relative_to(root.resolve())
        except ValueError:
            raise ToolError(f"путь «{rel}» вне репозитория: рантайм не пускает за пределы каталога запуска — "
                            f"сохраните файл внутри репо")
        return p
    return _rel_inside(root, rel)


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
    registered: List[Path] = []
    if feature:
        manifest, _problems = TR.load_safe(root, feature)
        if manifest:
            # спеки, зарегистрированные в track.yaml, лежат где угодно (в том числе вне трека)
            registered = [_rel_inside(root, p) for p in manifest.get("specs") or []]
    if not paths and not registered:
        raise ToolError("нужен feature (ключ стори) или paths (спеки от корня репо)")
    found = [p for p in paths if p.is_file()]
    missing = [p for p in paths if not p.is_file()]
    return S.parse_files(found, registered=registered + missing, root=root)


def _track_summary(root: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    frames = []
    for f in manifest.get("frames") or []:
        paths = TR.frame_paths(root, manifest, f["name"])
        frames.append({
            "name": f["name"], "role": f["role"], "dir": f["dir"], "frameUrl": f.get("frameUrl"),
            "requirement": dict(f["requirement"], status=TR.requirement_status(root, manifest, f["name"]))
            if f.get("requirement") else {"status": "none"},
            "raster": TR.raster_info(root, manifest, f["name"]), "frameBox": f.get("frameBox"),
            "url": f.get("url"), "open": f.get("open"), "stateOf": f.get("stateOf"),
            "canonical": paths["canonical"].is_file(), "plan": paths["plan"].is_file(),
            "buildState": paths["buildState"].is_file(), "verifyState": paths["verifyState"].is_file(),
        })
    return {"track": manifest["track"], "implementation": manifest.get("implementation"),
            "specs": manifest.get("specs") or [], "frames": frames}


def t_track_manifest(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        manifest = TR.load(root, track)
    except TR.TrackError as e:
        raise ToolError(str(e))
    changes, raster = args.get("set"), args.get("rasterPath")
    writing = changes is not None or raster is not None
    created = False
    if manifest is None:
        if not writing:
            return {"track": track, "exists": False,
                    "next": "трек заводит daiver_pixso_pull (первый кадр); поля трека — implementation, "
                            "specs — можно задать и до него"}
        if frame is not None or raster is not None:
            raise ToolError(f"трека «{track}» нет — кадры и растр заводит daiver_pixso_pull; поля трека "
                            f"(implementation, specs) можно задать и до него")
        manifest, created = TR.new_manifest(track), True
    changed: List[str] = []
    try:
        if changes is not None:
            manifest, changed, problems = TR.apply_set(manifest, frame, changes)
            if problems:
                raise ToolError("; ".join(problems))
        raster_res = None
        if raster is not None:
            raster_res = TR.attach_raster(root, manifest, frame, _rel_inside(root, raster))
            changed.append("raster")
        if writing:
            TR.save(root, manifest)
    except TR.TrackError as e:
        raise ToolError(str(e))
    out = {"exists": True, "created": created, "changed": changed, **_track_summary(root, manifest)}
    if raster is not None:
        out["rasterAttached"] = raster_res
    if frame is None and changes is not None and any(k in changes for k in TR.FRAME_SETTABLE):
        out["warnings"] = ["frame не указан — поля кадра не меняются"]
    return out


def t_pixso_pull(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    if "dslJson" in args:
        raise ToolError("dslJson не принимается: сырой экспорт (сотни КБ — МБ) не должен проходить через "
                        "контекст модели. Сохраните DSL кадра в файл внутри репо (Pixso MCP или экспорт "
                        "пользователя) и передайте dslPath")
    if "dslPath" not in args:
        raise ToolError("нужен dslPath — путь к файлу с DSL-экспортом кадра (внутри репо)")
    try:
        return PP.pull(root, args["track"], _path_inside(root, args["dslPath"]), args.get("frameUrl"),
                       args.get("frame"))
    except PP.PullError as e:
        raise ToolError(str(e))


def t_source_nodes(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        manifest = TR.load(root, track)
        if manifest is None:
            raise ToolError(f"трека «{track}» нет")
        raw = TR.frame_paths(root, manifest, args.get("frame"))["raw"]
        return SN.probe(raw, args["nodeIds"])
    except (TR.TrackError, SN.ProbeError) as e:
        raise ToolError(str(e))


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
    "daiver_track_manifest": {
        "fn": t_track_manifest,
        "description": ("Манифест трека (.daiver/tracks/<KEY>/track.yaml) — единственный способ его "
                        "править. Без set/rasterPath — чтение: кадры, статус требования (ok|stale|missing), "
                        "растр, что уже готово. set — поля кадра (role screen|dialog|state|breakpoint, url, "
                        "open-рецепт {actions[], clipSelector}, stateOf, frameBox, notes) при указанном "
                        "frame либо поля трека (implementation{url,devCommand}, specs[]) без него; null "
                        "снимает поле. rasterPath — прикрепить goal image кадра (PNG/JPEG, путь от корня "
                        "репо). Кадры и требования заводит daiver_pixso_pull, не этот инструмент."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека), напр. STOR-100"},
            "frame": {"type": "string", "description": "имя кадра; без него — поля трека"},
            "set": {"type": "object", "description": "что изменить: {поле: значение}; null снимает поле"},
            "rasterPath": {"type": "string", "description": "изображение кадра (PNG/JPEG), путь от корня репо"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_pixso_pull": {
        "fn": t_pixso_pull,
        "description": ("Принять DSL-экспорт кадра Pixso из ФАЙЛА: проверить формат, похудить (библиотека и "
                        "служебное прочь, ~100× на реальном экране), сохранить рядом сырой как свидетельство "
                        "и записать кадр в трек с хешем. Сырой DSL читать нельзя — только этот инструмент. "
                        "frameUrl обязателен (ссылка на кадр; не выдумывай — спроси). Заводит трек и кадр. "
                        "Повторный pull того же экспорта ничего не меняет; собранный кадр не перезаписывается."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека), напр. STOR-100"},
            "dslPath": {"type": "string", "description": "файл с DSL-экспортом кадра, путь внутри репо"},
            "frameUrl": {"type": "string", "description": "ссылка на кадр в Pixso (https://…)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию main"},
            "dslJson": {"type": "string", "description": "НЕ ИСПОЛЬЗОВАТЬ: сырой DSL через контекст запрещён, нужен dslPath"}},
            "required": ["track", "frameUrl"], "additionalProperties": False},
    },
    "daiver_source_nodes": {
        "fn": t_source_nodes,
        "description": ("Что дизайнер ОБЪЯВИЛ про названные узлы в сыром экспорте: ограничения "
                        "(horizontalConstraint/verticalConstraint), auto-layout, геометрия. Сырой "
                        "экспорт читать нельзя — только так. До 40 узлов за вызов; файл целиком не "
                        "возвращается. Нужен, когда у контейнера дети остались абсолютными (unproven): "
                        "декларация сильнее впечатления с картинки."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string"}, "frame": {"type": "string"},
            "nodeIds": {"type": "array", "items": {"type": "string"},
                        "description": "id узлов (guid Pixso), до 40"}},
            "required": ["track", "nodeIds"], "additionalProperties": False},
    },
}


def call(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    _check_args(args or {}, tool["schema"])
    fn: Callable[[Path, Dict[str, Any]], Dict[str, Any]] = tool["fn"]
    return fn(Path(root), args or {})
