"""tools.py — реестр инструментов движка forge-ui (`daiver_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется (спека QG-S10).
Нарушения аргументов собираются в одно сообщение: слабой модели легче починить разом.

Сейчас: daiver_status, daiver_init, daiver_spec, daiver_track_manifest, daiver_source_nodes,
daiver_pixso_pull, daiver_normalize, daiver_svg_assets, daiver_outline, daiver_section.
Остальные инструменты спеки появляются по задачам docs/forge-ui/; daiver_plan_sign не появится
никогда — подпись плана в forge-ui это согласие человека `ui-plan-<KEY>` (record_approval), а не
инструмент модели.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import init as I
from . import outline as OL
from . import source_nodes as SN
from . import specs as S
from . import status as ST
from . import svg_assets as SA
from . import track as TR
from . import uispec as U
from .ingest import normalize as NZ
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
              "number": isinstance(v, (int, float)) and not isinstance(v, bool),
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


def _story_spec_path(root: Path, feature: str) -> Path:
    """Где лежит ui-spec.md стори: каталог фич проекта (его знает хост, не движок) / <ключ> / ui-spec.md.
    ValueError — ключ небезопасен как имя каталога."""
    P = _project_mod()
    return P.feature_docs_dir(root) / P.safe_slug(feature) / U.SPEC_NAME


def _story_specs(root: Path) -> Callable[[str], Optional[Path]]:
    """Резолвер для статуса: ключ трека → ui-spec.md стори; None — путь определить нельзя (спеки трека
    проверяются без него: статус — первый ход и не должен падать из-за раскладки docs)."""
    def resolve(track: str) -> Optional[Path]:
        try:
            return _story_spec_path(root, track)
        except (ValueError, ImportError):
            return None
    return resolve


def t_status(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return ST.status(root, story_spec=_story_specs(root))


def t_init(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    answers = args.get("answers")
    if answers is None:
        out = I.detect(root)
        out["status"] = ST.status(root, story_spec=_story_specs(root))
        out["next"] = ("задай вопросы пользователю (кандидаты — с доказательствами, решение за ним), "
                       "затем вызови daiver_init с answers")
        return out
    return I.init(root, answers)


def t_spec(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    paths: List[Path] = [_rel_inside(root, p) for p in args.get("paths") or []]
    feature = args.get("feature")
    if feature:
        try:
            ui = _story_spec_path(root, feature)
        except ValueError as e:
            raise ToolError(str(e))
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


def t_normalize(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return NZ.normalize_frame(root, args["track"], args.get("frame"))
    except NZ.NormalizeError as e:
        raise ToolError(str(e))


def t_svg_assets(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        svg = _path_inside(root, args["svgPath"]) if "svgPath" in args else None
        module = _path_inside(root, args["moduleDir"]) if "moduleDir" in args else None
        return SA.run(root, track, frame, args.get("slug"), svg, module)
    except (TR.TrackError, SA.SvgAssetsError) as e:
        raise ToolError(str(e))


def t_outline(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return OL.outline_frame(root, track, frame, args.get("weightBudget"))
    except (TR.TrackError, OL.OutlineError) as e:
        raise ToolError(str(e))


def t_section(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return OL.section_frame(root, track, frame, args["sectionId"], args.get("weightBudget"))
    except (TR.TrackError, OL.OutlineError) as e:
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
                        "(.daiver/config.yaml), проблемы конфига, требования дизайн-системы, треки: ступень "
                        "каждого кадра по лестнице empty → requirement → normalized → planned → building → "
                        "built → verifying → clean, фронтирный кадр, итог (outcome); nextStep — шаг forge-ui "
                        "(ui-design | ui-build | ui-verify) с именем кадра; warnings — пересказывай "
                        "пользователю вслух. Каталог .daiver/ руками не листай и ступень «на глаз» не "
                        "выводи — её решает код."),
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
    "daiver_normalize": {
        "fn": t_normalize,
        "description": ("Требование кадра → каноническая модель (.daiver/tracks/<KEY>/…/visual-requirements."
                        "canonical.json). Формат определяет код по файлу, адаптер не выбирается, outPath не "
                        "передаётся. Канон НИКОГДА не читается целиком: дальше только daiver_outline / "
                        "daiver_section. Файл требования, изменённый мимо pull, отклоняется (хеш)."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_svg_assets": {
        "fn": t_svg_assets,
        "description": ("Векторные иллюстрации кадра (узлы `art` канона) ↔ файлы .svg. Без slug/svgPath/moduleDir — "
                        "арты кадра: что зарегистрировано, missingArt (slug без файла). slug + svgPath — "
                        "зарегистрировать экспорт Pixso (.svg внутри репо) как assets/<slug>.svg кадра; повтор "
                        "ничего не меняет; slug берётся из канона, не выдумывается. moduleDir (шаг ui-build; "
                        "внутри outputDir; slug — арт секции, без slug — все арты кадра) — копия файла в "
                        "<moduleDir>/<svg.assetsDir>/ и ТОЧНЫЕ import + jsx под svg.render из конфига "
                        "(component | url | inline; путь импорта — от файла в корне moduleDir). Нет файла — не "
                        "блокер: вернётся помеченный placeholder правильного размера и названный недостающий "
                        "файл. Иллюстрацию рисовать руками, подменять div'ом или писать инлайн-<svg> нельзя — "
                        "только этот файл."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "slug": {"type": "string", "description": "slug арта (узел `art.slug` канона / missingArt)"},
            "svgPath": {"type": "string", "description": "экспорт узла из Pixso (.svg), путь внутри репо; нужен slug"},
            "moduleDir": {"type": "string", "description": "каталог модуля внутри outputDir, куда копируется файл"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_outline": {
        "fn": t_outline,
        "description": ("Карта макета ВМЕСТО канона (канон целиком читать нельзя). Секции sections[] — границы выбирает код "
                        "по весу (минифицированные байты поддерева, не больше weightBudget; лист тяжелее бюджета — oversize, "
                        "не режется); outline — дерево строками «id kind \"name\" вес …» (одна строка = узел, отступ = "
                        "глубина; свёрнуто, если не влезает в бюджет); unproven — контейнеры с абсолютными детьми (children "
                        "через запятую) и кандидаты layoutHints: решает план, не сборка; goalImage + подсказка; "
                        "nodeUrlTemplate для ссылок [id](url) (нет URL — голый id, адрес не выдумывается). weightBudget: "
                        "аргумент > workflow.weightBudget конфига > 24000. Тело секции — daiver_section."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "weightBudget": {"type": "number", "exclusiveMinimum": 0,
                             "description": "бюджет секции в байтах; меньше — мельче секции (надёжнее на слабой модели)"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_section": {
        "fn": t_section,
        "description": ("Тело ОДНОЙ секции канона: section (мета с hash), ancestors (раскладка и поверхность предков "
                        "root→parent — контекст, куда положить построенное), node (полное поддерево ≤ бюджета, строится "
                        "только оно). Понимает ровно sections[].id из daiver_outline при том же weightBudget; любой другой "
                        "id — отказ с подсказкой, какой брать. Корень, тяжелее бюджета, целиком не отдаётся."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "sectionId": {"type": "string", "description": "id секции из sections[] daiver_outline"},
            "weightBudget": {"type": "number", "exclusiveMinimum": 0,
                             "description": "тот же бюджет, что был в daiver_outline (без него — конфиг/24000)"}},
            "required": ["track", "sectionId"], "additionalProperties": False},
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
