"""tools.py — реестр инструментов движка forge-ui (`daiver_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется (спека QG-S10).
Нарушения аргументов собираются в одно сообщение: слабой модели легче починить разом.

Сейчас: daiver_status, daiver_init, daiver_spec, daiver_track_manifest, daiver_source_nodes,
daiver_pixso_pull, daiver_normalize, daiver_svg_assets, daiver_outline, daiver_section,
daiver_map_lookup, daiver_map_append, daiver_ds_requirements, daiver_ds_inventory, daiver_ds_lookup,
daiver_ds_details, daiver_resolve_candidates, daiver_resolve_query, daiver_resolve_finalize, daiver_resolve_report,
daiver_plan_write, daiver_plan_contract, daiver_build_state, daiver_build_state_set, daiver_color_tokens,
daiver_capabilities, daiver_screenshot, daiver_code_compare, daiver_verify_compare, daiver_verify_state, daiver_verify_state_record, daiver_verify_decide, daiver_render_reference, daiver_visual_diff, daiver_visual_review, daiver_verify_report, daiver_e2e_plan, daiver_e2e_run, daiver_frame_states, daiver_frame_baseline, daiver_propose_sources.
Остальные инструменты спеки появляются по задачам docs/forge-ui/; daiver_plan_sign не появится
никогда — подпись плана в forge-ui это согласие человека `ui-plan-<KEY>` (record_approval), а не
инструмент модели.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import baseline as BL
from . import build_state as BS
from . import capabilities as CA
from . import color_tokens as CT
from . import component_map as CM
from . import ds_lookup as DL
from . import ds_requirements as DR
from . import init as I
from . import states as STS
from . import layout as LAY
from . import outline as OL
from . import page_base as PB
from . import plan as PL
from . import plan_contract as PC
from . import resolution_report as RR
from . import resolve as RS
from . import source_nodes as SN
from . import sources as SRC
from . import specs as S
from . import status as ST
from . import svg_assets as SA
from . import track as TR
from . import uispec as U
from .ingest import normalize as NZ
from .pixso import pull as PP
from .verify import capture as CP
from .verify import code_compare as CCMP
from .verify import e2e as E2E
from .verify import reference as VREF
from .verify import report_html as VRPT
from .verify import state as VST
from .verify import verify_compare as VCMP
from .verify import visual_diff as VDIFF
from .verify import visual_review as VREV

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
              "array": isinstance(v, list) and all(isinstance(x, dict if (spec.get("items") or {}).get("type") == "object" else str)
                                                   for x in v)}.get(t, True)
        if not ok:
            what = "список объектов" if t == "array" and (spec.get("items") or {}).get("type") == "object" else t
            errors.append(f"«{k}»: ожидается {what}")
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
        return LAY.redirect(root, p)
    return LAY.redirect(root, _rel_inside(root, rel))


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


def t_capabilities(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return CA.capabilities(root)


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
        for key in ("sources", "scope"):                   # только у кадра, правящего существующую страницу
            if f.get(key):
                frames[-1][key] = f[key]
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


def t_ds_requirements(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if args.get("check"):
            return DR.check(root)
        return DR.build(root, args.get("engine") or "python")
    except DR.DsError as e:
        raise ToolError(str(e))


def t_ds_inventory(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return DL.inventory(root)
    except DL.DsLookupError as e:
        raise ToolError(str(e))


def t_ds_lookup(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return DL.lookup(root, args["figmaName"], args.get("variants"), args.get("textSlotKeys"), args.get("limit"))
    except DL.DsLookupError as e:
        raise ToolError(str(e))


def t_ds_details(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return DL.details(root, args["components"])
    except DL.DsLookupError as e:
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


def t_map_lookup(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return CM.lookup(root, args["figmaName"], args.get("variants"))
    except CM.MapError as e:
        raise ToolError(str(e))


def t_map_append(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return CM.append(root, args["track"], args["rule"], bool(args.get("dryRun")))
    except CM.MapError as e:
        raise ToolError(str(e))


def t_resolve_candidates(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return RS.candidates_frame(root, track, frame)
    except (TR.TrackError, RS.ResolveError) as e:
        raise ToolError(str(e))


def t_resolve_query(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return RS.query_frame(root, track, frame, args["nodeId"])
    except (TR.TrackError, RS.ResolveError) as e:
        raise ToolError(str(e))


def t_resolve_finalize(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return RS.finalize_frame(root, track, frame, args["verdict"])
    except (TR.TrackError, RS.ResolveError) as e:
        raise ToolError(str(e))


def t_resolve_report(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return RR.write(root, TR.check_track_name(args["track"]))
    except (TR.TrackError, RR.ReportError, RS.ResolveError) as e:
        raise ToolError(str(e))


def t_plan_write(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return PL.write_frame(root, track, frame, args.get("plan"), args.get("weightBudget"), _story_specs(root),
                              lambda: RR.check(root, track))
    except (TR.TrackError, PL.PlanError) as e:
        raise ToolError(str(e))


def t_plan_contract(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return PC.run(root, TR.check_track_name(args["track"]), _story_specs(root))
    except (TR.TrackError, PC.ContractError, PL.PlanError) as e:
        raise ToolError(str(e))


def t_build_state(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return BS.run(root, track, frame)
    except (TR.TrackError, BS.BuildStateError) as e:
        raise ToolError(str(e))


def t_build_state_set(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return BS.set_section(root, track, frame, args["sectionId"], args["status"], args.get("outputs"), args.get("hash"))
    except (TR.TrackError, BS.BuildStateError) as e:
        raise ToolError(str(e))


def t_screenshot(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return CP.run(root, track, frame, args["url"], _path_inside(root, args["iterationDir"]), args["waitMs"], args.get("actions"),
                      args.get("clipSelector"), args.get("waitForSelector"), args.get("waitForGone"), args.get("target", "implementation"))
    except (TR.TrackError, CP.CaptureError) as e:
        raise ToolError(str(e))


def _frame_arg(args: Dict[str, Any]) -> Optional[str]:
    frame = args.get("frame")
    if frame is not None:
        TR.check_frame_name(frame)
    return frame


def t_render_reference(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VREF.run(root, TR.check_track_name(args["track"]), _frame_arg(args), _path_inside(root, args["iterationDir"]))
    except (TR.TrackError, VREF.ReferenceError_, CP.CaptureError) as e:
        raise ToolError(str(e))


def t_visual_diff(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VDIFF.run(root, TR.check_track_name(args["track"]), _frame_arg(args), _path_inside(root, args["iterationDir"]), args.get("against"))
    except (TR.TrackError, VDIFF.VisualDiffError) as e:
        raise ToolError(str(e))


def t_visual_review(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VREV.run(root, TR.check_track_name(args["track"]), _frame_arg(args), _path_inside(root, args["iterationDir"]), args.get("review"), args.get("prepare"))
    except (TR.TrackError, VREV.ReviewError, VDIFF.VisualDiffError) as e:
        raise ToolError(str(e))


def t_verify_report(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VRPT.run(root, TR.check_track_name(args["track"]), _frame_arg(args), _path_inside(root, args["iterationDir"]), args.get("date"))
    except (TR.TrackError, VRPT.ReportError) as e:
        raise ToolError(str(e))


MAX_REGIONS_SHOWN = 60


def t_frame_baseline(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        manifest = TR.load(root, track)
        if manifest is None:
            raise ToolError(f"трека «{track}» нет — сначала daiver_pixso_pull")
        frame = TR.get_frame(manifest, _frame_arg(args))
        if TR.is_page(frame):
            return _page_baseline(root, track, manifest, frame)
        out = BL.public(BL.analyze(root, manifest, frame["name"]))
    except TR.TrackError as e:
        raise ToolError(str(e))
    out = dict({"track": track}, **out)
    for r in out["regions"]:
        if len(r["files"]) > BL.MAX_FILES:
            r["filesMore"] = len(r["files"]) - BL.MAX_FILES
            r["files"] = r["files"][:BL.MAX_FILES]
    if len(out["regions"]) > MAX_REGIONS_SHOWN:
        out["regionsMore"] = len(out["regions"]) - MAX_REGIONS_SHOWN
        out["regions"] = out["regions"][:MAX_REGIONS_SHOWN]
    out["inherited"] = len(out.get("inherited") or [])
    if out["available"]:
        out["next"] = ("кадр — ревизия собранной версии %d: дальше резолв (кандидаты только в регионах added и changed; у changed — baseVerdict версии), план "
                       "(daiver_plan_write: регионы, что удалить, решения shared-частей берутся у версии) и подпись заново; сборка — В СУЩЕСТВУЮЩИЕ файлы "
                       "регионов (files), removed удаляются, без пролога. consequence-изменения (размер, позиция) не пинятся" % out["version"])
    else:
        out["next"] = "база ревизии недоступна: " + out["reason"]
    return out


def _page_baseline(root: Path, track: str, manifest: dict, frame: dict) -> Dict[str, Any]:
    """Кадр-правка существующей страницы (6.4): регионы изменения из разрыва базы, место каждого в коде, старые расхождения, правки вне регионов."""
    res = PB.analyze(root, manifest, frame["name"])
    out = dict({"track": track}, **PB.public(res))
    if len(out["regions"]) > MAX_REGIONS_SHOWN:
        out["regionsMore"] = len(out["regions"]) - MAX_REGIONS_SHOWN
        out["regions"] = out["regions"][:MAX_REGIONS_SHOWN]
    if not res["available"]:
        out["next"] = res["reason"]
        return out
    got = PB.outside_edits(root, manifest, frame["name"], res)
    out["outsideEdits"] = {"checked": got["checked"], "count": len(got["edits"]), "edits": [PB.edit_words(e) for e in got["edits"][:PB.MAX_EDITS]],
                           **({"unchecked": got["unchecked"]} if got.get("unchecked") else {}), **({"reason": got["reason"]} if got.get("reason") else {})}
    scope = res["scope"]
    out["next"] = ("кадр правит существующую страницу (scope %s): строятся только регионы — added вписать по location (ancestor — внутрь найденного "
                   "предка, между соседями), changed — правка на месте элемента (own/inside: file:line…endLine), removed — удалить элемент; файл "
                   "прочитай ДО правки — его структура, имена и стиль домашние; вне регионов ничего не реформатится и не переименовывается "
                   "(outsideEdits — нарушения, гейт сборки их не пустит)" % scope
                   + ("; preExisting — старые расхождения страницы с макетом: при scope change их не трогают и они не блокируют — если среди них "
                      "и есть изменение (цвет, отступ), это решение человека: scope page" if scope == "change" and res["counts"]["preExisting"] else ""))
    return out


def t_propose_sources(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        manifest = TR.load(root, track)
        if manifest is None:
            raise ToolError(f"трека «{track}» нет — сначала daiver_pixso_pull")
        return SRC.propose(root, manifest, _frame_arg(args))
    except (TR.TrackError, SRC.SourcesError) as e:
        raise ToolError(str(e))


def t_frame_states(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        manifest = TR.load(root, track)
        if manifest is None:
            raise ToolError(f"трека «{track}» нет — сначала daiver_pixso_pull")
        frame = TR.get_frame(manifest, _frame_arg(args))
        out = STS.public(STS.analyze(root, manifest, frame["name"]))
    except TR.TrackError as e:
        raise ToolError(str(e))
    out = dict({"track": track, "frame": frame["name"]}, **out)
    if len(out["regions"]) > MAX_REGIONS_SHOWN:
        out["regionsMore"] = len(out["regions"]) - MAX_REGIONS_SHOWN
        out["regions"] = out["regions"][:MAX_REGIONS_SHOWN]
    if out["available"]:
        out["next"] = ("кадр — состояние экрана «%s»: дальше резолв (daiver_resolve_candidates — кандидаты только в регионах added и changed), план (daiver_plan_write с plan.state: "
                       "переменная, значения и переключатель — из triggers или названный пользователем), сборка регионов ВНУТРИ модуля экрана. consequence-изменения (размер, позиция) не пинятся" % out["screen"])
    else:
        out["next"] = "состояние недоступно: " + out["reason"]
    return out


def t_e2e_plan(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        return E2E.plan(root, track, t_spec(root, {"feature": track}))
    except (TR.TrackError, E2E.E2eError) as e:
        raise ToolError(str(e))


def t_e2e_run(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        return E2E.run(root, track, _frame_arg(args), _path_inside(root, args["iterationDir"]), t_spec(root, {"feature": track}))
    except (TR.TrackError, E2E.E2eError) as e:
        raise ToolError(str(e))


def t_verify_state(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VST.open_iteration(root, TR.check_track_name(args["track"]), _frame_arg(args))
    except (TR.TrackError, VST.VerifyStateError) as e:
        raise ToolError(str(e))


def t_verify_state_record(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VST.record(root, TR.check_track_name(args["track"]), _frame_arg(args), args["iteration"], args["gates"], args.get("diffPercent"),
                          args.get("reviewReport"), args.get("reviewSkipped"), args.get("defects"), args.get("decision"), args.get("fixed"),
                          args.get("declined"))
    except (TR.TrackError, VST.VerifyStateError) as e:
        raise ToolError(str(e))


def t_verify_decide(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return VST.decide(root, TR.check_track_name(args["track"]), _frame_arg(args), args["iteration"], args["decision"], args.get("fixed"),
                          args.get("declined"))
    except (TR.TrackError, VST.VerifyStateError) as e:
        raise ToolError(str(e))


def t_verify_compare(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return VCMP.run(root, track, frame, _path_inside(root, args["iterationDir"]))
    except (TR.TrackError, VCMP.VerifyCompareError, BS.BuildStateError) as e:
        raise ToolError(str(e))


def t_code_compare(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"])
        frame = args.get("frame")
        if frame is not None:
            TR.check_frame_name(frame)
        return CCMP.run(root, track, frame, _path_inside(root, args["iterationDir"]))
    except (TR.TrackError, CCMP.CodeCompareError, BS.BuildStateError) as e:
        raise ToolError(str(e))


def t_color_tokens(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        track = TR.check_track_name(args["track"]) if "track" in args else None
        frame = args.get("frame")
        if frame is not None:
            if track is None:
                raise ToolError("frame называется вместе с track (цвета кадра); без track ответ — карта всего файла цветов")
            TR.check_frame_name(frame)
        return CT.run(root, track, frame)
    except (TR.TrackError, CT.ColorTokensError) as e:
        raise ToolError(str(e))


# Схема вердикта (INV-A7, §10.2): поля берутся из резолва — одно место правды; клиент видит их в tools/list.
VERDICT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "nodeId": {"type": "string", "description": "id кандидата из daiver_resolve_candidates"},
        "decision": {"type": "string", "enum": list(RS.DECISIONS),
                     "description": "component — компонент библиотеки; local — своя разметка; ignore — не строить"},
        "component": {"type": "string", "description": "имя компонента; ТОЛЬКО при decision=component"},
        "props": {"type": "object", "description": "{проп: строка | число | true/false}; ТОЛЬКО при decision=component"},
        "confidence": {"type": "string", "enum": list(RS.CONFIDENCE),
                       "description": "честная уверенность; не повышай её ради прохода"},
        "rationale": {"type": "string", "maxLength": RS.MAX_RATIONALE,
                      "description": f"одна-две фразы, до {RS.MAX_RATIONALE} знаков"},
        "mapRuleProposal": {"type": "object",
                            "description": "необязательно: правило карты {match, outcome, component | icon, props?, import?}"}},
    "required": list(RS.VERDICT_REQUIRED), "additionalProperties": False}


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
    "daiver_capabilities": {
        "fn": t_capabilities,
        "description": ("Первый ход итерации проверки (ui-verify): чем располагает машина. browser — какой Chrome/Chromium будет запущен "
                        "(found, path, version, source: env | config.browserPath | config.browserChannel | standard | PATH); он ищется: "
                        "переменная CHROMIUM_EXECUTABLE_PATH → verify.browserPath / verify.browserChannel конфига → стандартные места → PATH. "
                        "ready: false (blocked: no-browser) — цикл проверки НЕ стартует: скажи пользователю reason и supply дословно и "
                        "остановись. Браузер не скачивай и не ставь (ни npx playwright install, ни brew/apt/winget) и сам не запускай — "
                        "это решение человека. node, typescript (пакет репо), playwright (@playwright/test репо — нужен только e2e-гейту; "
                        "страницу снимает встроенный клиент, playwright для захвата не нужен) — справочно. vision.enabled — смотрит ли модель "
                        "картинки (verify.vision): картинки открывает сам агент, ничего не загружается наружу; off — визуальный обзор "
                        "записывается skipped. warnings пересказывай пользователю. Только читает: ничего не пишет и не открывает браузер (версию берёт из пакета "
                        "или коротким `--version`)."),
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
                        "open-рецепт {actions[], clipSelector}, stateOf, frameBox, notes; sources — файлы СУЩЕСТВУЮЩЕЙ "
                        "страницы, которую правит кадр, пути от корня репо; scope change|page — только вместе с "
                        "sources; baselineSkip — причина одной строкой, если работающей страницы нет вовсе и базу не снять: "
                        "пропуск записан открыто) при указанном frame либо поля трека (implementation{url,devCommand} — целиком, "
                        "specs[]) без него; null снимает поле. rasterPath — прикрепить goal image кадра (PNG/JPEG, путь от корня "
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
                        "Повторный pull того же экспорта ничего не меняет. Поверх СОБРАННОГО кадра (все секции done) — ревизия: прежняя семья уходит в versions/<n>/ "
                        "целиком, ответ mode: revision + version + схожесть и число регионов, строка «REVISION — …» — перескажи человеку; план и подпись заново, "
                        "строится только изменённое. Недостроенный кадр перезаписывается (mode: overwrite). Второй кадр трека — тот же вызов с другим frame: "
                        "семья первого переезжает в frames/<имя>/, пути переписываются, в ответе layout («LAYOUT — …») — перескажи человеку; два кадра "
                        "одного трека на одном frameUrl — ошибка именования (отказ)."),
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
    "daiver_map_lookup": {
        "fn": t_map_lookup,
        "description": ("Карта компонентов проекта (.daiver/component-map.yaml): имя узла макета → компонент библиотеки. "
                        "figmaName — имя компонента из канона (component.figmaName; регистр и разделители не важны). "
                        "hit:true — вердикт high БЕЗ чтения библиотеки: outcome (component | local | ignore | icon), "
                        "component, props, import.statement — вставляй в код ДОСЛОВНО, не сочиняй (null — пути нет ни "
                        "в правиле, ни в library.import: основной пакет не угадывается, спроси пользователя), rule — "
                        "какое правило сработало. Иконки идут из своего пакета (icons.import), не из основного. "
                        "hit:false — правила нет: решай по библиотеке (ds_lookup / storybook-mcp). variants в "
                        "сопоставлении не участвуют (props задаёт правило). Карта некорректна — отказ с перечнем проблем: "
                        "файл правит человек. Файл карты руками не читай и не правь."),
        "schema": {"type": "object", "properties": {
            "figmaName": {"type": "string",
                          "description": "имя компонента из канона (component.figmaName) или имя узла"},
            "variants": {"type": "object",
                         "description": "варианты узла {ключ: значение} из канона; в сопоставлении не участвуют"}},
            "required": ["figmaName"], "additionalProperties": False},
    },
    "daiver_map_append": {
        "fn": t_map_append,
        "description": ("Дописать ОДНО правило в карту компонентов — единственный способ её изменить. Только по явному "
                        "«да» пользователя на ЭТО правило: (1) вызови с dryRun:true — правило проверяется и печатается "
                        "YAML-ом, ничего не пишется; (2) покажи YAML пользователю и спроси прямо; (3) ТОЛЬКО после «да» "
                        "запиши согласие record_approval.py --key ui-map-<track> с его ответом в --evidence; (4) вызови "
                        "без dryRun. Согласие одноразовое: одно «да» — одно правило. rule: {match: имя | [имена] | "
                        "\"/regex/\" (захваты $1…$9; флаг i), outcome: component | local | ignore | icon, component "
                        "(нужен при component), icon (нужен при icon), props?: {проп: значение}, import?: {path?, "
                        "named?, default?}}. Дубликат или конфликт с имеющимся правилом, кривое правило, нет согласия — "
                        "отказ одним сообщением, файл не тронут. Комментарии и порядок файла сохраняются, файл целиком "
                        "не заменяется. В автономном прогоне согласия нет — карту он не пишет."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека): по нему согласие ui-map-<track>"},
            "rule": {"type": "object", "description": "правило карты: match, outcome, component | icon, props, import"},
            "dryRun": {"type": "boolean", "description": "только проверить и показать YAML; согласие не нужно"}},
            "required": ["track", "rule"], "additionalProperties": False},
    },
    "daiver_resolve_candidates": {
        "fn": t_resolve_candidates,
        "description": ("Кандидаты резолва кадра, которые ЕЩЁ надо решить (§10.1): инстансы, иконки-инстансы и фреймы с осмысленными "
                        "именами (header_bf_01 — да; «Frame 12», «Group 5», TEXT, Rectangle — нет). Арт (это файл .svg), закрытое "
                        "слоем (occluded) и внутренности библиотечного инстанса — не кандидаты, их id резолв отклонит. Строка: "
                        "«id kind \"имя\" <figmaName> {варианты}»; «×N» — N узлов с тем же figmaName и вариантами решаются ОДИН "
                        "раз, остальные получат вердикт сами (INV-B23; так же вердикты, уже принятые в раннем кадре трека); "
                        "«[stale]» — прежний вердикт не годится (причина в stale). Пустой список и todo 0 — резолв кадра закончен. "
                        "id придумывать нельзя — только отсюда. Вызывай в начале и после пачки вердиктов."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_resolve_query": {
        "fn": t_resolve_query,
        "description": ("Что резолвер видит об ОДНОМ кандидате — и больше у него нет ничего. candidate: id, kind, name, figmaName, "
                        "variants, textSlotKeys (КЛЮЧИ текстовых слотов, значений нет), origin — готовые аргументы для "
                        "daiver_ds_lookup; tree — потомки строками (глубина "
                        f"{RS.MAX_DEPTH}, не больше {RS.MAX_NODES} узлов с кандидатом, текст до {RS.MAX_TEXT_LEN} знаков; у вложенных "
                        "инстансов <figmaName>, {варианты}, slots= — сигнал композиции); fingerprint — число узлов по kind во "
                        "ВСЁМ поддереве, даже если дерево обрезано (truncated, hidden, «… +N»); map — результат daiver_map_lookup "
                        "(hit:true — вердикт high по правилу, библиотеку не читать). Геометрии, стилей, шрифтов, цветов, значений "
                        "слотов, предков и соседей в ответе нет, и достать их нельзя. recorded — вердикт узла уже записан; reuse — "
                        "для того же figmaName и вариантов вердикт уже записан, заново не решай. Не кандидат или неизвестный id — "
                        "отказ с причиной. Карта некорректна — отказ с перечнем: файл правит человек. Только читает."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "nodeId": {"type": "string", "description": "id кандидата из daiver_resolve_candidates"}},
            "required": ["track", "nodeId"], "additionalProperties": False},
    },
    "daiver_resolve_finalize": {
        "fn": t_resolve_finalize,
        "description": ("Принять вердикт по ОДНОМУ кандидату и записать его — единственный путь вердикта в план. verdict: {nodeId, "
                        "decision: component | local | ignore, component (только при component), props? ({проп: строка | число | "
                        f"true/false}}, только при component), confidence: high | medium | low (честная, не ради прохода), "
                        f"rationale (одна-две фразы, до {RS.MAX_RATIONALE} знаков), mapRuleProposal? (правило карты: проходит ту же "
                        "проверку, что daiver_map_append dryRun, и должно покрывать этот узел)}. Отказ перечисляет ВСЕ нарушения "
                        "разом, ничего не записано — исправь и вызови снова. Сверх формы код сверяет: узел — кандидат кадра; при hit "
                        "в карте вердикт обязан совпасть с правилом (карта сильнее вывода; confidence high); для того же figmaName и "
                        "вариантов вердикт уже записан — принимается только он (INV-B23); компонент без правила карты обязан быть "
                        "в библиотеке (по требованиям ДС), иначе decision=local. Ответ тонкий: recorded, reused (узлы кадра, "
                        "получившие тот же вердикт), remaining — сколько кандидатов осталось."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "verdict": VERDICT_SCHEMA},
            "required": ["track", "verdict"], "additionalProperties": False},
    },
    "daiver_resolve_report": {
        "fn": t_resolve_report,
        "description": ("Конец резолва трека: перегенерирует resolution-report.md (ОДИН файл на трек, секция «## frame: имя» на кадр, "
                        "таблица ЭВРИСТИЧЕСКИХ вердиктов со ссылками [id](url) на узлы; попадания в карту не входят) и говорит, что "
                        "спросить человека. Вызывай, когда daiver_resolve_candidates пуст во всех кадрах; после правки вердикта — "
                        "снова. toConfirm — вердикты, которые обязан увидеть человек: уверенность не выше "
                        "workflow.confirmResolutions, low — ВСЕГДА, иконка без маппинга при icons.fallback: ask; строки готовы к "
                        "вставке в вопрос. proposals — предложенные правила карты (rule + yaml; отдельный вопрос на каждое, "
                        "сначала они). confirmed: true — согласие ui-resolve-<track> записано и привязано к ТЕКУЩЕМУ отчёту; "
                        "отчёт изменился — прежнее согласие снято (consentRevoked). unresolved > 0 — резолв не закончен, "
                        "человека не спрашивай. Пишет только отчёт (и раздаёт готовые вердикты между кадрами): вердикты не "
                        "меняет, согласие не записывает — его выписывает пользователь словами, оркестратор фиксирует."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_plan_write": {
        "fn": t_plan_write,
        "description": ("План кадра — единственный способ его записать (plan.json и plan.md в папке кадра). Дерево секций, решение по "
                        "каждому узлу (из ПОДТВЕРЖДЁННОГО резолва: component | local | ignore), хеши секций и порядок сборки считает КОД "
                        "— в plan названо только то, чего в данных нет. plan.layout — решение по КАЖДОЙ записи unproven из daiver_outline "
                        "(§11.4): {id, reason: absolute-children | layout-hints, decision: keep-px | group (для absolute-children), "
                        "apply | keep-px (для layout-hints), evidence: declaration (daiver_source_nodes) | goal-image | user | none, "
                        "direction: row | column и groups: [[id детей]…] при group, note?}; apply и group без свидетельства не принимаются, "
                        "px → flex меняет только план. plan.layoutRest: \"keep-px\" — остальные записи остаются px без свидетельства "
                        "(они попадут в planUncertainty). plan.buildOrder необязателен: если назван ([{id, hash}] из sections[] "
                        "daiver_outline), код сверяет id, хеши и порядок с настоящими — чужой id, придуманный шаг или неверный хеш отказ. "
                        "plan.placement — ТОЛЬКО у не-первичного кадра без stateOf: решение о месте его корня в ЕДИНОМ модуле трека {mode: own-file | inside-primary, "
                        "component?: PascalCase, host?: id узла канона первичного кадра (только inside-primary, обязателен там), reason?}; по умолчанию own-file с компонентом по имени "
                        "кадра; у первичного кадра и у кадра-состояния — отказ. Резолв должен быть закончен и подтверждён, иначе отказ. weightBudget — тот же, что у daiver_outline (по умолчанию "
                        "конфиг/24000), записывается в план: сборка берёт секции при нём. Отказ перечисляет ВСЕ нарушения, ничего не "
                        "записано. Запись идемпотентна: те же данные — те же байты."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "plan": {"type": "object", "description": "{layout?: [решения по unproven], layoutRest?: \"keep-px\", buildOrder?: [{id, hash}], placement?: {mode, component?, host?, reason?}}; "
                                                        "без unproven — можно {} или не передавать"},
            "weightBudget": {"type": "number", "exclusiveMinimum": 0,
                             "description": "бюджет секции в байтах, как у daiver_outline; без него — конфиг/24000"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_plan_contract": {
        "fn": t_plan_contract,
        "description": ("Контракт готовности плана трека — ИЗ ДИСКА: что автономный прогон ещё спросил бы. 16 проверок: initialized, "
                        "library-source, browser, requirement, canonical, plan, resolutions, assets, goal-image, specs, state, baseline, "
                        "sources, implementation, dev-command, signature. checks — сводка по всем; open[] — каждый нарушенный пункт: "
                        "{id, frame?, state: open | stale | unknown, blocking, blockedReason, owner: human | plan | host, detail, ask — "
                        "ДОСЛОВНЫЙ вопрос человеку, record — чем записать ответ, degradesTo — во что деградирует, если не отвечать}. "
                        "ready — нет блокирующих (подпись в них не входит); complete — нет и деградаций; signed — подпись валидна "
                        "(согласие ui-plan-<track>, привязанное к plan.lock.json: правка плана, требования, резолва или перетяжка макета "
                        "её снимают; от ready она не зависит). Когда ready, инструмент сам пересобирает ui-plan.md (дайджест для человека) и plan.lock.json и "
                        "снимает устаревшее согласие; toSign — строки вопроса «Подписываем план?». planUncertainty — узлы, в которых "
                        "план не уверен, каждый один раз. Подпись — НЕ инструмент: согласие выписывает человек, запись делает "
                        "оркестратор. Вопросы к человеку задавай дословно по ask, ответы пиши по record, затем вызывай снова."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_build_state": {
        "fn": t_build_state,
        "description": ("Состояние сборки кадра (build-state.json) — первый ход шага ui-build и каждого его возобновления. Создаёт "
                        "его из buildOrder плана (все секции pending) или сверяет с планом и возвращает next — точку возобновления "
                        "(первая секция не done по порядку плана) — и progress: прогон, остановленный на секции 3 из 6, "
                        "продолжается с next, а не с нуля. Сверку считает КОД: хеш секции в плане не изменился — done остаётся; "
                        "изменился — done → stale (перестроить ТОЛЬКО её, файлы прежней сборки — next.outputs); новые id — pending; "
                        "удалённые выпадают; порядок и имена — по плану. Секции, чей корень в плане решён ignore (узел не строится вовсе), "
                        "движок закрывает САМ (noCode: готовы, без outputs): next их не называет, строить и закрывать их не нужно. "
                        "План, не соответствующий нынешнему макету (хеши buildOrder "
                        "≠ секциям канона), — отказ: сначала пересобери план (daiver_plan_write). weightBudget в ответе — тот, с "
                        "которым вызывается daiver_section; planMd — путь плана кадра (plan.md): решения по узлам берутся из него. warnings — outputs, "
                        "которых нет на диске: разбери сразу. Файл пишет "
                        "только этот инструмент и daiver_build_state_set; руками его не читай и не правь."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_build_state_set": {
        "fn": t_build_state_set,
        "description": ("Перевод ОДНОЙ секции в in_progress (начал; секция обязана быть next) или done (закончил). На done "
                        "обязательны outputs — файлы секции, пути ОТ КОРНЯ ПРОЕКТА (src/modules/x/Screen.tsx) или абсолютные внутри "
                        "репо; голое имя файла (Screen.tsx), каталог и путь вне репо — отказ: код-гейт открывает именно эти строки, — и "
                        "hash — section.hash из ответа daiver_section для ЭТОЙ секции (чужой hash — отказ, он в ответах не "
                        "называется). Перескочить нельзя: done — только из in_progress (повторный done обновляет outputs), "
                        "in_progress — только для next. pending и stale выставляет сверка (daiver_build_state), не ты. Неизвестный "
                        "sectionId — отказ с перечнем настоящих. Путь из outputs, которого нет на диске, — не отказ, а warnings: "
                        "создай файл или поправь путь повторным done."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "sectionId": {"type": "string", "description": "id секции — next.sectionId из ответа daiver_build_state"},
            "status": {"type": "string", "enum": list(BS.SETTABLE),
                       "description": "in_progress — начал секцию; done — закончил (нужны outputs и hash)"},
            "outputs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": BS.MAX_OUTPUTS,
                        "description": "только при done: файлы, которые записала секция, — пути от корня проекта "
                                       "(src/modules/x/Screen.tsx) или абсолютные внутри репо"},
            "hash": {"type": "string", "description": "только при done: section.hash из ответа daiver_section для этой секции"}},
            "required": ["track", "sectionId", "status"], "additionalProperties": False},
    },
    "daiver_screenshot": {
        "fn": t_screenshot,
        "description": ("Захват реализации — ОДИН вызов за итерацию проверки (ui-verify): открывает url в браузере, ждёт готовности, "
                        "проигрывает рецепт открытия (actions) и пишет в iterationDir actual.png + actual.html + snapshot.json (снимок "
                        "страницы: измеренные боксы, computed-стили, тексты, имена React-компонентов — единственный источник измеренных "
                        "значений). waitMs — потолок ожидания, а не цена: выбирай осознанно (холодный dev-сервер или первый заход 60000+, "
                        "обычно 25000, перепроверка известной страницы 5000; минимум 2000). Готово = нет признаков загрузки (loader, "
                        "spinner, skeleton, aria-busy, progressbar), DOM не менялся 1500 мс и содержимое устойчиво. ПРОЧТИ ready: false — "
                        "страница ещё грузится: такой снимок НЕ сравнивай и его дефекты НЕ репорти; подними waitMs либо назови загрузчик "
                        "через waitForGone (селектор, который должен исчезнуть) / waitForSelector (который должен появиться); так и не "
                        "поднялась — «страница никогда не догрузилась» и есть находка, остановись. Второй ИДЕНТИЧНЫЙ вызов в ту же папку "
                        "отказ («подождать ещё» повтором не лечится): меняй waitMs или рецепт. sanity.underRendered: true — на странице "
                        "куда меньше элементов, чем узлов в макете: захват недорисован, что бы ни говорил ready. fonts / warnings — "
                        "пересказывай (нет шрифта макета = метрики текста разойдутся; react.found: false = имена компонентов недоступны, "
                        "проверка идентичности НЕ исполнялась). Рецепт: actions — шаги click, dblclick, hover, fill, press, select, waitFor, "
                        "waitForGone, wait, scrollTo (CSS-селекторы; «выполнить JS» среди них нет и не будет); clipSelector — область "
                        "снимка: макет попапа или модалки описывает элемент, поэтому для них укажи его, макет целого экрана — весь viewport "
                        "(не указывай). Рецепт и clip, один раз выбранные, используй во ВСЕХ итерациях одинаково. Диалоги страницы "
                        "(alert/confirm/prompt) закрываются сами и попадают в dialogs. target: implementation (по умолчанию) | reference "
                        "(снимок эталонного рендера; url — file: внутри iterationDir; пишет reference.png и reference-snapshot.json). "
                        "iterationDir — только подпапка verify/ (или baseline/) кадра из daiver_verify_state. Браузера нет — ответ "
                        "blocked: no-browser (прочти reason и supply дословно, остановись, браузер не ставь). Страница не открылась или "
                        "рецепт не отработал — отказ с номером шага."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "url": {"type": "string", "description": "адрес страницы реализации (http/https); target=reference — file: внутри iterationDir"},
            "iterationDir": {"type": "string", "description": "папка итерации — подпапка verify/ кадра (путь от корня проекта), из daiver_verify_state"},
            "waitMs": {"type": "number", "description": "потолок ожидания готовности, мс: 2000…300000 (60000+ холодный старт, 25000 обычно, 5000 перепроверка)"},
            "actions": {"type": "array", "items": {"type": "object"},
                        "description": "рецепт открытия: шаги {verb, selector?, text?, key?, value?, ms?}; verb: click | dblclick | hover | fill (selector, text) | "
                                       "press (key, selector?) | select (selector, value) | waitFor / waitForGone (selector, ms?) | wait (ms) | scrollTo (selector)"},
            "clipSelector": {"type": "string", "description": "CSS-селектор элемента, который снимается (попап, модалка); без него — весь viewport"},
            "waitForSelector": {"type": "string", "description": "CSS-селектор, который должен появиться до снимка"},
            "waitForGone": {"type": "string", "description": "CSS-селектор загрузчика, который должен исчезнуть до снимка"},
            "target": {"type": "string", "enum": list(CP.TARGETS), "description": "implementation (по умолчанию) | reference (эталонный рендер)"}},
            "required": ["track", "url", "iterationDir", "waitMs"], "additionalProperties": False},
    },
    "daiver_render_reference": {
        "fn": t_render_reference,
        "description": ("Эталонный рендер кадра: строит HTML из КАНОНА макета (раскладка, стили, тексты, шрифты слотов) и снимает его тем же захватом и в том же viewport — "
                        "reference.png, reference.html и reference-snapshot.json (маркеры макета для отчёта) в iterationDir. Нужен, когда у кадра нет картинки дизайна "
                        "(goal image правдивее и берётся первой) и для визуального обзора. Это РЕКОНСТРУКЦИЯ: арт без .svg нарисован помеченным блоком (файл — "
                        "daiver_svg_assets), слоты-плейсхолдеры библиотеки помечены пунктиром, поверхность библиотечных компонентов не нарисована — поэтому дифф с ним "
                        "сигнал, а не вердикт. Браузера нет — blocked: no-browser (прочти reason и supply дословно, остановись). iterationDir — подпапка verify/ кадра."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации — подпапка verify/ кадра"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_verify_report": {
        "fn": t_verify_report,
        "description": ("ШАГ 7 итерации (interactive): рендерит report.html в iterationDir — самодостаточную страницу без внешних ресурсов: макет и реализация рядом (картинки вшиты), "
                        "пронумерованные пины дефектов, таблица МАКЕТ | РЕАЛИЗАЦИЯ с русскими именами свойств, чипы-фильтры по фактическим кодам, ссылки на узлы макета и файл:строку, ранее принятые "
                        "решения человека, полоса кадров трека. Вызывай ПОСЛЕ daiver_verify_state_record. ПУТЬ ОТЧЁТА (report в ответе) сообщай пользователю ВСЕГДА, до любых слов о результатах: он "
                        "решает глазами. stateRecorded: false — итерация не записана: сначала запись, потом отчёт. date — дата входом (например 2026-10-26; в файл идёт как есть, часы не читаются): "
                        "одинаковый вход — одинаковые байты. Автономный режим — rendered: false (отчёт заменяет триаж). Находки обзора в нём помечены «не подтверждено»."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации с actual.png (подпапка verify/ кадра)"},
            "date": {"type": "string", "description": "дата входом, одна короткая строка; без неё даты в отчёте нет"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_frame_baseline": {
        "fn": t_frame_baseline,
        "description": ("Ревизия кадра против собранной версии (база — versions/<n>/, её завёл pull поверх собранного кадра): регионы дельты shared / added / removed / "
                        "changed (+changes; consequence: true — размер и позиция от контента, не пинятся) в порядке документа, у каждого files — куда регион "
                        "построен в коде (added — файлы родителя, куда вписать); similarity, inherited — сколько решений узлов взято у версии. available: false + "
                        "reason — базы или канона нет. Кадр с sources (правка существующей страницы) — регионы изменения из разрыва базы страницы с макетом: added / "
                        "changed / removed с location (own | inside | ancestor; file:line…endLine в файлах страницы), preExisting — старые расхождения (scope change "
                        "их не трогает), outsideEdits — правки файлов вне регионов. Читающий инструмент: ничего не пишет; outline / section / резолв / план ревизии и "
                        "правки страницы работают на этой дельте сами."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра-ревизии; по умолчанию первичный"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_propose_sources": {
        "fn": t_propose_sources,
        "description": ("Где в репо существующая страница кадра: файлы кода с текстами макета (тексты ≥ 3 символов; до 8 кандидатов, ранг — сколько разных "
                        "текстов нашлось; found — какие и на какой строке), routes — где адрес страницы объявлен в роутере, textFiles — словари с текстами "
                        "(i18n). Обход до 3000 файлов (truncated). Читающий: ничего не пишет. Кандидат — не решение: страница — файлы, которые РЕНДЕРЯТ этот "
                        "экран, это подтверждает чтение; потом daiver_track_manifest set={sources, scope}."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_frame_states": {
        "fn": t_frame_states,
        "description": ("Кадр-состояние против своего экрана (stateOf): регионы дельты shared (стабы с baseId — id узла экрана) / added / removed / changed (+changes: вариант, текст, цвет; "
                        "consequence: true — изменения размера и позиции от контента, они не пинятся никогда) в порядке документа; triggers — кандидаты-переключатели на экране; available: false + reason, "
                        "если схожесть < 0.6 или корни разной ширины (±1 px) — чини причину (stateOf не на тот кадр), а не планируй от целой страницы. Читающий инструмент: ничего не пишет. "
                        "outline / section / резолв / план такого кадра работают на этой дельте автоматически."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра-состояния (у него задан stateOf)"}},
            "required": ["track", "frame"], "additionalProperties": False},
    },
    "daiver_e2e_plan": {
        "fn": t_e2e_plan,
        "description": ("План e2e-гейта: из спек стори (ui-spec.md и зарегистрированные) — сценарии {id, file, title, body, context, line, skip?}. Имя файла детерминировано по id "
                        "(ui-spec#2 → <dir>/ui-spec-2.e2e.spec.ts), каталог — testDir из playwright.config, иначе e2e/, но НЕ внутри outputDir (туда пишет только ui-build). Сценарий с "
                        "контекстом REMOVED Requirements — skip с причиной: тест на удаляемое поведение НЕ пиши, но скажи, что скипнуто. hasE2e: false — гейт skipped (gate в ответе), скажи один раз. "
                        "Тела тестов пишешь ты (по одному файлу на сценарий, ассерты — по текстам mocks.ts, повторный прогон заменяет свои файлы), потом daiver_e2e_run."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_e2e_run": {
        "fn": t_e2e_run,
        "description": ("Гейт e2e итерации: прогоняет НАПИСАННЫЕ тесты плана (`npx --no-install playwright test <файлы> --reporter=junit`) и разбирает JUnit в дефекты итерации (zone e2e, у каждого "
                        "file:line). Пишет в iterationDir e2e-summary.json, e2e-defects.json, e2e-junit.xml. status: pass | fail (падения, прогон не дошёл до тестов, не уложился во время — называется, "
                        "не глушится) | skipped (нет сценариев / файлов / @playwright/test / node — с причиной в reason, clean не блокирует; ничего не устанавливается). Статус гейта в "
                        "daiver_verify_state_record берётся из этих файлов, а не из твоих слов. Вызывай после визуального обзора, до записи итерации."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации — подпапка verify/ кадра"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_visual_review": {
        "fn": t_visual_review,
        "description": ("Визуальный обзор итерации — рукопожатие ДВУХ вызовов; картинки открываешь ТЫ сам (ничего не уходит наружу, vision-бэкенда у движка нет). ПЕРВЫЙ вызов без review "
                        "ничего не пишет: вернёт images (картинка дизайна ПЕРВОЙ, реализация ВТОРОЙ — открой обе по порядку), prompt, reportPath, priorFixes и, для длинного экрана, slices "
                        "(парные полосы). Есть доказанные дефекты compare — вопрос сужается до ОСТАТКА (иконки, перекрытия и обрезка, перенос текста, порядок, сбои рендера): значения "
                        "(px, %, HEX, шрифт) в ответе ЗАПРЕЩЕНЫ, «ничего не нашёл» — законный ответ; доказанных нет — полный обзор по семи осям с числами и без «слегка/примерно». "
                        "ВТОРОЙ вызов с review={opened: [пути из images как есть], prior?: [{n, verdict: FIXED|NOT FIXED|REGRESSED}], findings: [{title, now, shouldBe, fix, check}] | nothingFound: true} "
                        "проверяется по форме и пишет visual-review.md (это priorFixes следующей итерации; руками файл не пишется). Не смог открыть картинки, модель их не принимает или "
                        "verify.vision: off — review={skipped: «причина»}: гейт skipped, ничего не описывай по имени файла. Находки — НЕПОДТВЕРЖДЁННЫЕ (zone vision): показываются человеку, "
                        "чистоту не блокируют, автономно не чинятся. prepare: jpeg — JPEG-копии картинок и полос через canvas, если PNG не прочёлся."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации с actual.png (подпапка verify/ кадра)"},
            "review": {"type": "object", "description": "ответ обзора: {opened, prior?, findings | nothingFound} либо {skipped: «причина»}; без него — первый вызов"},
            "prepare": {"type": "string", "enum": ["jpeg"], "description": "только в первом вызове: JPEG-копии картинок и полос в review-images/"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_visual_diff": {
        "fn": t_visual_diff,
        "description": ("Пиксельный дифф «макет | реализация» (после daiver_screenshot в ту же iterationDir). against: goal — картинка дизайна кадра (обрезка по frameBox, подложка "
                        "на белое), reference — эталонный рендер (daiver_render_reference); без against берётся goal, если он есть: он правдивее, в ответе сказано, что "
                        "использовано. Дифф — СИГНАЛ, а не вердикт: сам дефектов не создаёт и гейт не валит (gate всегда pass, если браузер был). diffPercent — ПРОЦЕНТ "
                        "(3.18 = 3.18 %), передавай его в daiver_verify_state_record как есть, не делением на 100. zones — где расходится; explainedBy — дефекты compare, которые "
                        "зону уже объяснили (повторно не комментируй); зоны без объяснения — материал для визуального обзора (diff-strip.png). Сглаживание шрифтов и "
                        "плейсхолдеры эталона — не баги; aboveHint — подсказка по verify.visualDiffThreshold, не блокировка. Размеры разные — оба кладутся на белый холст. "
                        "Пишет diff.png, diff-strip.png, visual-diff.json. Браузера нет — blocked: no-browser, gate: skipped."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации с actual.png (подпапка verify/ кадра)"},
            "against": {"type": "string", "enum": list(VDIFF.AGAINST), "description": "goal | reference; по умолчанию goal, если картинка дизайна есть"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_verify_state": {
        "fn": t_verify_state,
        "description": ("ПЕРВЫЙ вызов итерации проверки (ui-verify): открывает итерацию кадра и называет всё, что нужно для хода. Читай: iteration и "
                        "iterationDir (каждый артефакт итерации — только в свою папку, она не перезаписывается; повторный вызов до записи номер НЕ двигает); "
                        "unrecordedIterations — итерация исполнена, но не записана: сначала запиши её (daiver_verify_state_record), и только потом новые "
                        "гейты; priorReview — Fix plan прошлой итерации или null; goalImage (нет картинки дизайна на 1-й итерации — спроси пользователя, "
                        "положи visual-requirements.png в папку кадра и вызови снова); missingArt; warnStalling; history; implementation (url, open, "
                        "devCommand; нет url — спроси пользователя, не выдумывай); declinedSoFar — отклонённое человеком НЕ переспрашивай; mode. "
                        "Шаги итерации: daiver_screenshot → daiver_verify_compare → daiver_code_compare → [дифф → обзор → e2e] → "
                        "daiver_verify_state_record. Чинить посреди итерации нельзя: гейты только собирают свидетельства."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"}},
            "required": ["track"], "additionalProperties": False},
    },
    "daiver_verify_state_record": {
        "fn": t_verify_state_record,
        "description": ("ЗАПИСЬ итога итерации — без неё тот же номер итерации, пустой priorReview и сломанные progress/stall/итог. Вызывай ПОСЛЕ гейтов, до "
                        "любых слов человеку. gates — ровно пять имён: compare, code, visualDiff, review, e2e, каждый pass | fail | skipped | degraded "
                        "(строкой или {status, reason}); гейты, которых ещё нет (визуальный дифф, обзор, e2e), — skipped с причиной. Статусы compare и "
                        "code, matchRate, fidelity и ВСЕ дефекты compare/code движок берёт из артефактов итерации сам и переписывает твои слова фактом "
                        "(расхождение вернётся в warnings): defects — только пункты, которых движок не знает (zone: vision | e2e, id, severity). "
                        "diffPercent — проценты 0–100 ровно как вернул дифф (3.18, не 0.0318). Ответ: clean, blocking, progress (stalled: не лучше прошлой; "
                        "регресс — тоже stall), report — путь verify-report.md. Решение человека (clean/fix_all/fix_some/stop, fixed, declined) к "
                        "записанной итерации вносит оркестратор через daiver_verify_decide; здесь decision допустим только для автономного прогона."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iteration": {"type": "number", "description": "номер итерации из daiver_verify_state"},
            "gates": {"type": "object", "description": "ровно пять ключей: compare, code, visualDiff, review, e2e"},
            "diffPercent": {"type": "number", "description": "проценты 0–100, как вернул дифф"},
            "reviewReport": {"type": "object", "description": "отчёт визуального обзора (Fix plan) — станет priorReview следующей итерации"},
            "reviewSkipped": {"type": "string", "description": "почему обзор не проводился"},
            "defects": {"type": "array", "items": {"type": "object"}, "description": "только пункты vision/e2e: {id, zone, severity, message, …}"},
            "decision": {"type": "string", "enum": list(VST.DECISIONS), "description": "только автономный прогон (решение человека — daiver_verify_decide)"},
            "fixed": {"type": "array", "items": {"type": "string"}, "description": "id дефектов, исправленных по решению"},
            "declined": {"type": "array", "items": {"type": "string"}, "description": "id дефектов, отклонённых человеком"}},
            "required": ["track", "iteration", "gates"], "additionalProperties": False},
    },
    "daiver_verify_decide": {
        "fn": t_verify_decide,
        "description": ("РЕШЕНИЕ человека к уже записанной итерации (его вызывает оркестратор после ответа пользователя). decision: clean (только если "
                        "итерация чистая) | fix_all | fix_some | stop; fixed — id дефектов, которые будут чиниться, declined — id, от которых человек "
                        "отказался (их больше не переспрашивают). Решение записывается ОДИН раз и не переписывается. stop — остановка немедленно: без "
                        "«ещё одной правки», без дополнительного гейта; остановка с открытыми дефектами требует согласия ui-accept-<KEY>. Ответ человека "
                        "переноси дословно: свои решения за него не принимай."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iteration": {"type": "number", "description": "номер записанной итерации"},
            "decision": {"type": "string", "enum": list(VST.DECISIONS)},
            "fixed": {"type": "array", "items": {"type": "string"}},
            "declined": {"type": "array", "items": {"type": "string"}}},
            "required": ["track", "iteration", "decision"], "additionalProperties": False},
    },
    "daiver_verify_compare": {
        "fn": t_verify_compare,
        "description": ("Жёсткий гейт итерации проверки (ui-verify): модельное сравнение реализации с макетом ОДНИМ вызовом. Читает snapshot.json, "
                        "записанный daiver_screenshot в ту же iterationDir, строит модель страницы, сопоставляет её с макетом, проверяет правила "
                        "(текст, слоты, размеры, отступы, типографика, цвет, форма), DS-гейт и каскад подавления; пишет impl-model.json, "
                        "findings.json, defects.json, compare-summary.json. ЧИТАЙ status: clean | defects | hard-gate-fail (matchRate < 0.5: "
                        "страница — не этот кадр, чини захват: рецепт, адрес, состояние — а не код; дефектов нет) | recapture (снимок не готов или "
                        "недорисован — перезахвати и повтори). matchRate (сколько макета найдено) и fidelity (доля найденного без блокирующих "
                        "неправильностей) — ОБЕ: unmatched > 0 и lowMatchRate читай вслух, при низком matchRate fidelity выглядит хорошо только "
                        "потому, что считается по найденному. Дефекты: стабильный id (не от значений), coverage (сколько находок свёрнуто), "
                        "lifecycle (new | persisting | regressed; fixed — в ответе списком), rows МАКЕТ|РЕАЛИЗАЦИЯ, designLink — ссылка на узел макета. "
                        "Блокируют critical и major без noise; minor (deprecated) предупреждает. unverified и dismissals пересказывай: «нечего "
                        "было сравнивать» не то же, что «чисто». Каждая итерация — своя подпапка verify/ кадра из daiver_verify_state."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации — та же, куда писал daiver_screenshot (подпапка verify/ кадра)"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_code_compare": {
        "fn": t_code_compare,
        "description": ("Код-гейт итерации проверки (ui-verify) — исходники модуля против макета, БЕЗ браузера: читает файлы из outputs "
                        "build-state (+ mocks.ts рядом), строит по ним дерево, сверяет с макетом и проверяет только текст, содержимое "
                        "слотов и покрытие (никаких чисел). Пишет в iterationDir code-model.json, code-findings.json, code-defects.json, "
                        "code-summary.json. Читай status: pass | fail (есть блокирующие дефекты кода — у каждого file:line и editor-ссылка) | "
                        "skipped (Vue/Svelte, нет build-state, исходники пропали, дереву не доверяют — причина в reason, не блокирует). "
                        "treeRecognized: false — страница-код не похожа на макет, находки только улики. MISSING status: unconfirmed — "
                        "отсутствие подтверждает только DOM-гейт (compare-summary.json рядом); dismissals/unverified читай вслух: динамика, "
                        "неразрешённые импорты и условный рендер — не судятся. DS_* код-гейт не выдаёт — это дело DS-гейта. "
                        "iterationDir — подпапка verify/ (или baseline/) кадра из daiver_verify_state."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека)"},
            "frame": {"type": "string", "description": "имя кадра; по умолчанию первичный"},
            "iterationDir": {"type": "string", "description": "папка итерации — подпапка verify/ кадра (путь от корня проекта)"}},
            "required": ["track", "iterationDir"], "additionalProperties": False},
    },
    "daiver_color_tokens": {
        "fn": t_color_tokens,
        "description": ("Карта цветов проекта (project.styling.colorsFile: .less / .scss / .css) для шага ui-build — ОДИН вызов в "
                        "прологе. С track — цвета ЭТОГО кадра (канон читает код, не ты): matched — «цвет → переменная проекта», raw — "
                        "цвета без переменной. Правило одно: переменная ТОЛЬКО при точном совпадении значения (регистр и короткая "
                        "форма hex не мешают, rgba в виде, как в макете), иначе сырое значение макета; ближний цвет не "
                        "подбирай и переменную не угадывай. Без track — карта всего файла (map, первые строки). conflicts — "
                        "переменные с разными значениями (в карту не входят), skipped — не литералы цвета (функции, карты), "
                        "ambiguous — значения с несколькими переменными (взята первая по файлу). colorsFile не задан или формат "
                        "не разбирается — все цвета сырые (nextStep говорит). Файла нет — отказ с лечением (путь правит человек)."),
        "schema": {"type": "object", "properties": {
            "track": {"type": "string", "description": "ключ стори (имя трека): с ним — цвета кадра; без него — карта всего файла"},
            "frame": {"type": "string", "description": "имя кадра (только вместе с track); по умолчанию первичный"}},
            "additionalProperties": False},
    },
    "daiver_ds_requirements": {
        "fn": t_ds_requirements,
        "description": ("Требования дизайн-системы из stories/исходников библиотеки (library.scanPaths) → два файла в "
                        ".daiver/: design-system-requirements.json (КОНТРАКТ: компонент → пропсы и допустимые значения, "
                        "usedValues, import, число историй) и …details.json (описания, дефолты, deprecations, примеры). "
                        "Только при library.source: stories | sources; при storybook-mcp — отказ (сервер сторибука и есть "
                        "требования). Строится один раз после daiver_init и коммитится; после обновления библиотеки "
                        "перезапускается вручную. Ноль компонентов — ошибка: поправь scanPaths, пустой контракт не "
                        "пишется. check:true — только проверить пару (валидна ли, не устарела ли по хешу источников), "
                        "ничего не пишет. engine: python (по умолчанию, без node) | node (TypeScript репо — если "
                        "разбор python ошибся). Файлы библиотеки и эти документы руками не читай и не правь."),
        "schema": {"type": "object", "properties": {
            "check": {"type": "boolean", "description": "только проверить уже построенную пару, ничего не писать"},
            "engine": {"type": "string", "enum": ["python", "node"],
                       "description": "движок разбора: python (по умолчанию) | node (нужны node и typescript репо)"}},
            "additionalProperties": False},
    },
    "daiver_ds_inventory": {
        "fn": t_ds_inventory,
        "description": ("Список компонентов дизайн-системы из ГОТОВЫХ требований (.daiver/design-system-requirements*.json; "
                        "библиотеку не сканирует и не читает) — для library.source stories | sources; при storybook-mcp "
                        "имена даёт MCP-сервер сторибука. Вызывай ОДИН раз на трек, до резолва кандидатов; субагентам "
                        "список передаёт оркестратор, сами они его не запрашивают. Строка: «Имя [DEPRECATED → замена] "
                        "(группа) — описание»; DEPRECATED — не использовать. В шапке счётчики (count, stories, "
                        "withDetails, withoutImport — у стольких компонентов путь импорта неизвестен). Ответ «blocked: "
                        "library-source-unreachable» — требований нет, они битые или построены для другой библиотеки: "
                        "резолв не начинать, построй их daiver_ds_requirements. Файлы библиотеки и эти документы руками не "
                        "читай."),
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "daiver_ds_lookup": {
        "fn": t_ds_lookup,
        "description": ("Шортлист компонентов ДС для узла макета (когда daiver_map_lookup дал hit:false). Ранжирует КОД, не "
                        "модель, и объясняет баллы. Кандидат — только компонент со связью по имени; уровень match: exact "
                        "(имя совпало; регистр и разделители не важны; score 900–1099) > word (одно имя входит в другое "
                        "целым словом: Button в PrimaryButton, 600–798) > part (внутри слова: Tab в Tabs, 300–498) > "
                        "shared (только общие слова, 100–299). Внутри уровня порядок задают принимаемые варианты узла "
                        "(variants), текстовые слоты (textSlotKeys — только КЛЮЧИ, без значений) и число историй; равные "
                        "баллы — по имени. Синонимов и опечаток нет: «Notification» не найдёт «Alert». candidates:[] — "
                        "настоящий ответ «в библиотеке такого нет» → local, подходящее на глаз не подбирай. limit по "
                        f"умолчанию {DL.DEFAULT_LIMIT}, допустимо {DL.MIN_LIMIT}..{DL.MAX_LIMIT} (вне диапазона приводится к "
                        f"границе, limitNote скажет), total — сколько всего нашлось. import — готовый statement, вставляй "
                        "ДОСЛОВНО; null — путь неизвестен (не угадывай: library.import карты или вопрос пользователю). "
                        "deprecated — не бери без причины. "
                        "Финалистов (1–2) проверь daiver_ds_details. Файлы библиотеки руками не читай."),
        "schema": {"type": "object", "properties": {
            "figmaName": {"type": "string",
                          "description": "имя компонента из канона (component.figmaName) или имя узла"},
            "variants": {"type": "object",
                         "description": "варианты узла {ключ: значение} из канона (component.variants)"},
            "textSlotKeys": {"type": "array", "items": {"type": "string"},
                             "description": "КЛЮЧИ текстовых слотов узла (component.textProps), без значений"},
            "limit": {"type": "number", "minimum": DL.MIN_LIMIT, "maximum": DL.MAX_LIMIT,
                      "description": f"сколько кандидатов вернуть; по умолчанию {DL.DEFAULT_LIMIT}, допустимо "
                                     f"{DL.MIN_LIMIT}..{DL.MAX_LIMIT}"}},
            "required": ["figmaName"], "additionalProperties": False},
    },
    "daiver_ds_details": {
        "fn": t_ds_details,
        "description": (f"Факты о названных компонентах ДС (не больше {DL.MAX_DETAILS} за вызов; обычно — 1–2 финалиста "
                        f"daiver_ds_lookup): "
                        "import (statement, вставляй ДОСЛОВНО), propsClosed, props (тип и допустимые значения, required, "
                        "default, used — значения, которые реально передавали истории, описание), description, deprecations "
                        "(под размер не сокращаются), examples — JSX из историй (имя истории → код). propsClosed:true — набор пропов "
                        "полный, пропа вне списка нет; false — список неполный, отсутствие пропа в нём ничего не доказывает. "
                        "enum: значение вне списка — нарушение; used — подтверждённый словарь, не запрет. Дефолт повторно не "
                        "передавай, набор пропов сам не расширяй. Длинное (docs, примеры, описания, хвост пропов) "
                        "сокращается кодом под размер ответа — в cut сказано, что убрано. Имя берёшь из lookup/inventory; "
                        "неизвестное имя — отказ (регистр и разделители не важны)."),
        "schema": {"type": "object", "properties": {
            "components": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": DL.MAX_DETAILS,
                           "description": f"имена компонентов из daiver_ds_lookup / daiver_ds_inventory, от 1 до "
                                          f"{DL.MAX_DETAILS}"}},
            "required": ["components"], "additionalProperties": False},
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
