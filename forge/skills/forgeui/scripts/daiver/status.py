"""status.py — daiver_status: первый ход агента — состояние forge-ui с диска (спека §3.2, §15.7; INV-A13, C1).

Что отвечает: инициализирован ли проект, проблемы конфига, требования дизайн-системы, треки (ступень
каждого кадра, фронтирный кадр, итог), `nextStep` — шаг forge-ui с именем кадра, и `warnings`, которые
модель обязана пересказать вслух. Каталог `.daiver/` руками не листается: какой артефакт какую ступень
даёт, решено здесь, а не в голове модели (INV-A13). Модуль только читает — на диск не пишет ничего.

Лестница кадра (§3.2): `empty → requirement → normalized → planned → building → built → verifying → clean`.
Ступень — САМАЯ ВЫСОКАЯ, до которой кадр дотянулся подряд: каждая определяется артефактом предыдущей
фазы, поэтому артефакт выше пропавшего нижнего (план без канона после перетяжки макета) не учитывается —
кадр возвращается на ступень ниже и «залечивается» сам, когда нижний артефакт появляется снова.

  empty        требования нет (записи нет в манифесте либо файла требования нет на диске)
  requirement  требование на месте, канон не читается или его нет
  normalized   канон читается, `plan.json` нет
  planned      `plan.json` читается, `build-state.json` нет
  building     в `build-state.json` есть секции не `done` (pending | in_progress | stale)
  built        все секции `done`, записанных итераций verify нет
  verifying    итерации записаны, последняя НЕ чистая (открыты блокирующие дефекты)
  clean        итерации записаны, последняя чистая

`building` проверяется РАНЬШЕ ступеней verify: секция, стухшая после проверки (макет сдвинулся, сборка
пометила её stale), возвращает проверенный кадр обратно в сборку — зелёная история verify относится к
прежней сборке. Ступень трека — ступень ФРОНТИРНОГО кадра (первого не-clean в порядке манифеста); трек
clean ⇔ все кадры clean. `outcome` трека — агрегат: clean (все кадры clean) | defects-remaining (есть
кадр в verifying) | never-run (иначе: часть кадров не проверялась); `blocked:<причина>` — задача 7.1.

Минимальный контракт артефактов, которых ещё нет как кода (их пишут 2.6, 3.1, 4.8 — лестница читает
ровно эти поля и ничего шире; имена файлов — из `track.frame_paths`):
  plan.json          JSON-объект; содержимое не читается — важны наличие и разбираемость;
  build-state.json   {"sections": [{"status": pending|in_progress|done|stale, …}, …]}, список не пуст
                     (пустой «все готово» ничего не собрал — читается как повреждённый);
  verify-state.json  {"history": [{"clean": bool, "blocking": число | список (необязательно), …}, …]};
                     пустая history допустима (итерация открыта, но не записана) — это ступень built.
Файл, не вписавшийся в контракт (не UTF-8, не JSON, не тот тип, статус вне перечня…), читается как
ОТСУТСТВУЮЩИЙ и называется в warnings (`artifact-unreadable`): повреждённый state-файл не слепит
роутер и не роняет статус. Рассинхрон `plan.json` ↔ `build-state.json` (план перегенерирован, секции не
сверены) лестница не видит: сверка хешей секций — дело `daiver_build_state` (3.1).

Предупреждения вслух (§15.7), каждое — код + строка: requirement-stale-built (громче всего: собранный
кадр без versions/), requirement-stale, requirement-missing, frame-no-requirement, frame-url-missing,
goal-image-missing, art-svg-missing, implementation-url-missing, specs-missing, ds-requirements-missing;
служебные — artifact-unreadable, canonical-stale, manifest-unreadable. Данные берутся из готовых модулей:
хеш требования — `track.requirement_status`, арты — `svg_assets.load_arts`/`missing_slugs`, goal image —
`track.raster_info`, адрес страницы — `track.implementation_url`; логика здесь не дублируется.

Допущение, не проверенное данными: «записаны sources — страница уже в коде» (§3.2) ступень НЕ повышает.
Поля `sources` в track.yaml ещё нет (его заведёт 6.4), а `sources` пишутся на ui-design, до плана и сборки
(§12.2): повышение «по факту записи» отправило бы кадр правки существующей страницы сразу в ui-verify,
минуя ui-build. Как кадр-страница без build-state попадает в built — решает 6.4, когда появится поток.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import VERSION
from . import config as C
from . import svg_assets as SA
from . import track as TR

LADDER = ("empty", "requirement", "normalized", "planned", "building", "built", "verifying", "clean")
BUILD_STATUSES = ("pending", "in_progress", "done", "stale")
OUTCOMES = ("clean", "defects-remaining", "never-run")

# Шаги ветки forgeui (hooks/_branches.py → BRANCHES["forgeui"]["steps"]); совпадение пинит test_status_ladder.
STEP_SPEC, STEP_DESIGN, STEP_BUILD, STEP_VERIFY = "ui-spec", "ui-design", "ui-build", "ui-verify"
STEP_OF_STAGE: Dict[str, Optional[str]] = {
    "empty": STEP_DESIGN, "requirement": STEP_DESIGN, "normalized": STEP_DESIGN,
    "planned": STEP_BUILD, "building": STEP_BUILD,
    "built": STEP_VERIFY, "verifying": STEP_VERIFY, "clean": None,
}

NEXT_INIT = ("init: спроси настройки проекта (daiver_init без ответов — кандидаты с "
             "доказательствами), затем daiver_init с ответами пользователя")
NEXT_SPEC = "ui-spec: фронт-спека стори (ui-spec.md)"

WARNING_CODES = ("requirement-stale-built", "requirement-stale", "requirement-missing", "frame-no-requirement",
                 "frame-url-missing", "goal-image-missing", "art-svg-missing", "implementation-url-missing",
                 "specs-missing", "ds-requirements-missing", "artifact-unreadable", "canonical-stale",
                 "manifest-unreadable")
LOUD = ("requirement-stale-built",)          # идут в списке первыми

DS_DOCS = ("design-system-requirements.json", "design-system-requirements.details.json")
NAMES_SHOWN = 3                              # сколько имён (арты, спеки, кадры) перечислять в строке

Warn = Tuple[str, str]                       # (код, человекочитаемая строка)
StoryResolver = Callable[[str], Optional[Path]]


def _short(msg: Any, limit: int = 160) -> str:
    s = " ".join(str(msg).split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _names(items: List[str]) -> str:
    shown = ", ".join(items[:NAMES_SHOWN])
    return shown + (f" и ещё {len(items) - NAMES_SHOWN}" if len(items) > NAMES_SHOWN else "")


# ── чтение state-файлов: всё, что не вписалось в контракт, — «отсутствует» ───────────────────────────

_ABSENT = object()                              # «файла нет» ≠ JSON null


def _read_json(path: Path) -> Tuple[Any, Optional[str]]:
    """(данные, причина). Файла нет — (_ABSENT, None); есть, но не читается — (_ABSENT, причина)."""
    if not path.is_file():
        return _ABSENT, None
    try:
        return json.loads(path.read_bytes().decode("utf-8-sig")), None
    except OSError as e:
        return _ABSENT, f"не читается: {e.strerror or e}"
    except UnicodeDecodeError:                  # раньше ValueError: это его подкласс
        return _ABSENT, "не UTF-8"
    except RecursionError:
        return _ABSENT, "вложенность JSON слишком глубока"
    except ValueError:
        return _ABSENT, "не JSON"


def _plan_problem(data: Any) -> Optional[str]:
    return None if isinstance(data, dict) else "ожидается JSON-объект"


def _build_fact(data: Any) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """((готово, всего), причина): секции со статусами из перечня, список не пуст."""
    if not isinstance(data, dict):
        return None, "ожидается JSON-объект"
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        return None, "нет sections[] (пусто или не список): нечего считать собранным"
    done = 0
    for i, s in enumerate(sections):
        st = s.get("status") if isinstance(s, dict) else None
        if st not in BUILD_STATUSES:
            return None, f"sections[{i}].status: {_short(st, 40)!r} — ожидается {'|'.join(BUILD_STATUSES)}"
        done += 1 if st == "done" else 0
    return (done, len(sections)), None


def _verify_fact(data: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """({iterations, clean, blocking}, причина) по history[]; clean последней записи — bool."""
    if not isinstance(data, dict):
        return None, "ожидается JSON-объект"
    history = data.get("history")
    if not isinstance(history, list):
        return None, "нет history[] (ожидается список записей итераций)"
    if not history:
        return {"iterations": 0, "clean": None, "blocking": None}, None
    last = history[-1]
    if not isinstance(last, dict) or not isinstance(last.get("clean"), bool):
        return None, "history[-1].clean: ожидается true/false"
    blocking = last.get("blocking")
    if isinstance(blocking, (list, tuple)):
        blocking = len(blocking)
    elif isinstance(blocking, bool) or not isinstance(blocking, int) or blocking < 0:
        blocking = None
    return {"iterations": len(history), "clean": last["clean"], "blocking": blocking}, None


# ── ступень кадра ────────────────────────────────────────────────────────────────────────────────────

def stage_of(facts: Dict[str, Any]) -> str:
    """Ступень по фактам об артефактах — чистая функция, на ней стоит таблица S2.

    facts: requirement none|missing|stale|ok; canonical, plan — читаются ли; build — None | (готово, всего);
    verify — None | {iterations, clean, blocking}."""
    if facts["requirement"] in ("none", "missing"):
        return "empty"
    if not facts["canonical"]:
        return "requirement"
    if not facts["plan"]:
        return "normalized"
    build = facts["build"]
    if build is None:
        return "planned"
    if build[0] < build[1]:           # building РАНЬШЕ verify: стухшая секция отменяет зелёную историю
        return "building"
    verify = facts["verify"]
    if verify is None or verify["iterations"] == 0:
        return "built"
    return "clean" if verify["clean"] else "verifying"


def _has_versions(d: Path) -> bool:
    try:
        return d.is_dir() and any(p.is_dir() for p in d.iterdir())
    except OSError:
        return False


def _gather(root: Path, manifest: dict, f: dict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(факты для stage_of, прочее: пути, арты, нечитаемые файлы). Только чтение."""
    name = f["name"]
    paths = TR.frame_paths(root, manifest, name)
    extra: Dict[str, Any] = {"paths": paths, "unreadable": [], "arts": [], "canonWarnings": [], "reqError": None}
    facts: Dict[str, Any] = {"canonical": False, "plan": False, "build": None, "verify": None}
    try:
        facts["requirement"] = TR.requirement_status(root, manifest, name)
    except OSError as e:               # файл есть, прочесть нельзя: для лестницы это «требования нет»
        facts["requirement"], extra["reqError"] = "missing", (e.strerror or str(e))
    if paths["canonical"].is_file():
        try:
            extra["arts"], extra["canonWarnings"] = SA.load_arts(root, manifest, f)
            facts["canonical"] = True
        except (SA.SvgAssetsError, RecursionError) as e:
            extra["unreadable"].append((paths["canonical"].name, _short(e)))
    data, why = _read_json(paths["plan"])
    if why is None and data is not _ABSENT:
        why = _plan_problem(data)
        facts["plan"] = why is None
    if why:
        extra["unreadable"].append((paths["plan"].name, why))
    data, why = _read_json(paths["buildState"])
    if why is None and data is not _ABSENT:
        facts["build"], why = _build_fact(data)
    if why:
        extra["unreadable"].append((paths["buildState"].name, why))
    data, why = _read_json(paths["verifyState"])
    if why is None and data is not _ABSENT:
        facts["verify"], why = _verify_fact(data)
    if why:
        extra["unreadable"].append((paths["verifyState"].name, why))
    return facts, extra


def frame_status(root: Path, manifest: dict, f: dict) -> Tuple[Dict[str, Any], List[Warn], Dict[str, Any]]:
    """(публичный вид кадра, предупреждения, факты). Кадр climbs отдельно от соседей."""
    root, track, name = Path(root), manifest["track"], f["name"]
    who = f"кадр «{name}» трека {track}"
    facts, extra = _gather(root, manifest, f)
    paths, req = extra["paths"], facts["requirement"]
    stage = stage_of(facts)
    warns: List[Warn] = []

    # требование: устарело (громче всего у собранного кадра без versions/), нет файла, нет записи
    reqfile = paths["requirement"].name
    if req == "none":
        warns.append(("frame-no-requirement", f"{who}: требования нет — кадр не может быть clean; "
                      f"заведи его: daiver_pixso_pull"))
    elif req == "missing":
        why = f"не читается ({extra['reqError']})" if extra["reqError"] else "нет на диске"
        warns.append(("requirement-missing", f"{who}: файл требования {reqfile} {why} — "
                      f"тяни кадр заново: daiver_pixso_pull"))
    elif req == "stale":
        if paths["buildState"].is_file() and not _has_versions(paths["versions"]):
            warns.append(("requirement-stale-built", f"{who}: СОБРАН, а файл требования {reqfile} изменён мимо pull "
                          f"(sha256 не совпал с манифестом) и сохранённой версии (versions/) нет — собранное "
                          f"не соответствует требованию, прежний экспорт потерян. Перетянуть собранный кадр "
                          f"нельзя: верни файл из git или реши с пользователем"))
        else:
            warns.append(("requirement-stale", f"{who}: файл требования {reqfile} изменён мимо pull (sha256 не "
                          f"совпал с манифестом) — всё построенное поверх него устарело; тяни кадр заново: "
                          f"daiver_pixso_pull"))
    reqinfo = f.get("requirement") or {}
    if reqinfo.get("kind") == "pixso" and not f.get("frameUrl"):
        warns.append(("frame-url-missing", f"{who}: pull записан без frameUrl — ссылки на узлы в отчётах будут "
                      f"голыми id; ссылку на кадр в Pixso даёт пользователь (frameUrl задаёт daiver_pixso_pull)"))
    raster = TR.raster_info(root, manifest, name)
    if reqinfo and (raster is None or not raster["exists"] or "format" not in raster):
        if raster is None:
            what = "нет goal image (картинки макета)"
        elif not raster["exists"]:
            what = f"goal image {raster['path']} записана в манифесте, но файла нет"
        else:
            what = f"goal image {raster['path']} не читается как PNG/JPEG"
        warns.append(("goal-image-missing", f"{who}: {what} — сверка пойдёт по эталонному рендеру; приложи PNG/JPEG: "
                      f"daiver_track_manifest(track, frame, rasterPath)"))
    for fname, why in extra["unreadable"]:
        warns.append(("artifact-unreadable", f"{who}: {fname} не читается ({why}) — читаю как отсутствующий"
                      + ("" if fname.endswith(TR.CANONICAL_FILE) else "; файл пишет только движок, руками не чини")))
    for w in extra["canonWarnings"]:
        warns.append(("canonical-stale", f"{who}: {w}"))
    if LADDER.index(stage) >= LADDER.index("normalized"):
        missing = SA.missing_slugs(extra["arts"], paths["assets"])
        if missing:
            warns.append(("art-svg-missing", f"{who}: арты без .svg: {_names(missing)} — получи экспорт слоёв из Pixso "
                          f"и зарегистрируй: daiver_svg_assets(track, slug, svgPath); без файла сборка поставит "
                          f"помеченный placeholder"))
    url = TR.implementation_url(manifest, name)
    if stage == "built" and not url:
        warns.append(("implementation-url-missing", f"{who}: собран, а адрес страницы для проверки не записан "
                      f"(диалог адрес не наследует) — спроси его у пользователя и запиши: "
                      f"daiver_track_manifest(track, frame, set={{url}})"))

    view: Dict[str, Any] = {"name": name, "role": f["role"], "stage": stage, "dir": f["dir"]}
    if f.get("stateOf"):
        view["stateOf"] = f["stateOf"]
    if url:
        view["url"] = url
    if reqinfo:
        view["requirement"] = reqinfo.get("kind")
        if req != "ok":
            view["requirementStatus"] = req
    view["goalImage"] = bool(raster and raster["exists"] and "format" in raster)
    if facts["build"] is not None:
        view["build"] = f"{facts['build'][0]}/{facts['build'][1]}"
    if facts["verify"] is not None and facts["verify"]["iterations"]:
        view["iterations"] = facts["verify"]["iterations"]
        if stage == "verifying" and facts["verify"]["blocking"] is not None:
            view["blocking"] = facts["verify"]["blocking"]
    return view, warns, facts


def _frame_next(track: str, view: Dict[str, Any], facts: Dict[str, Any]) -> str:
    """Что делать с кадром: `<шаг forge-ui>: кадр «имя» трека T — …`; clean — «готово»."""
    stage, who = view["stage"], f"кадр «{view['name']}» трека {track}"
    step = STEP_OF_STAGE[stage]
    if stage == "clean":
        return f"готово: {who} чист (clean)"
    if stage == "empty":
        text = (f"{who} — нет требования: daiver_pixso_pull (DSL файлом; имя кадра и ссылка на макет — из таблицы "
                f"«Кадры» ui-spec.md)")
    elif stage == "requirement":
        text = f"{who} — требование есть, канона нет: daiver_normalize"
    elif stage == "normalized":
        text = (f"{who} — канон есть, плана нет: карта секций (daiver_outline), резолв компонентов, план секций; "
                f"подпись плана — решение человека")
    elif stage == "planned":
        text = f"{who} — план есть, сборки нет: после подписи плана собрать секции по buildOrder"
    elif stage == "building":
        done, total = facts["build"]
        text = (f"{who} — секций готово {done} из {total}: продолжить с первой pending/stale "
                f"(daiver_build_state → next)")
    elif stage == "built":
        text = f"{who} — собран, проверок не было: начать первую итерацию проверки"
    else:
        v = facts["verify"]
        blocking = f", блокирующих дефектов {v['blocking']}" if v["blocking"] else ""
        text = (f"{who} — итераций {v['iterations']}, последняя не чистая{blocking}: показать итог человеку и "
                f"спросить «чинить / стоп» (чинить — переоткрыть ui-build)")
    if view.get("requirementStatus") in ("stale", "missing"):
        text += f". Сначала warnings: requirement-{view['requirementStatus']}"
    return f"{step}: {text}"


# ── трек ─────────────────────────────────────────────────────────────────────────────────────────────

def _specs_warning(root: Path, track: str, manifest: dict, story_spec: Optional[StoryResolver]) -> Optional[Warn]:
    """Спеки трека — те же, что собирает daiver_spec: ui-spec.md стори + зарегистрированные в track.yaml."""
    paths: List[Path] = []
    if story_spec is not None:
        story = story_spec(track)
        if story is not None:
            paths.append(Path(story))
    paths += [root / rel for rel in manifest.get("specs") or []]
    missing: List[str] = []
    for p in paths:
        if not p.is_file():
            try:
                missing.append(p.resolve().relative_to(root.resolve()).as_posix())
            except ValueError:
                missing.append(str(p))
    if not missing:
        return None
    return ("specs-missing", f"трек {track}: спеки не на месте — нет {_names(missing)}; бизнес-логика (данные, "
                             f"состояния, сценарии e2e) в сборку не попадёт. Верни файл либо перерегистрируй: "
                             f"daiver_track_manifest(track, set={{specs}})")


def _outcome(stages: List[str]) -> str:
    if stages and all(s == "clean" for s in stages):
        return "clean"
    return "defects-remaining" if "verifying" in stages else "never-run"


def track_status(root: Path, track: str, story_spec: Optional[StoryResolver] = None
                 ) -> Tuple[Dict[str, Any], List[Warn]]:
    """(вид трека, предупреждения). Повреждённый манифест — трек пуст, причина названа в warnings."""
    root = Path(root)
    warns: List[Warn] = []
    manifest, problems = TR.load_safe(root, track)
    for p in problems:
        warns.append(("manifest-unreadable", f"трек {track}: {_short(p, 300)} — читаю как пустой; восстанови "
                      f"track.yaml из git либо удали трек и вытяни кадры заново (daiver_pixso_pull)"))
    frames_in = (manifest or {}).get("frames") or []
    if not frames_in:
        if problems:
            nxt = (f"{STEP_DESIGN}: track.yaml трека {track} не читается (см. warnings: manifest-unreadable) — "
                   f"восстанови его из git либо удали трек и вытяни кадры заново (daiver_pixso_pull)")
        else:
            nxt = (f"{STEP_DESIGN}: в треке {track} нет кадров — их заводит daiver_pixso_pull "
                   f"по таблице «Кадры» ui-spec.md")
        return {"name": track, "stage": "empty", "outcome": "never-run", "nextStep": nxt, "frames": []}, warns
    views: List[Dict[str, Any]] = []
    facts_of: Dict[str, Dict[str, Any]] = {}
    for f in frames_in:
        view, w, facts = frame_status(root, manifest, f)
        views.append(view)
        facts_of[f["name"]] = facts
        warns += w
    spec_warn = _specs_warning(root, track, manifest, story_spec)
    if spec_warn:
        warns.append(spec_warn)
    stages = [v["stage"] for v in views]
    frontier = next((v for v in views if v["stage"] != "clean"), None)
    out: Dict[str, Any] = {"name": track, "stage": frontier["stage"] if frontier else "clean",
                           "outcome": _outcome(stages)}
    if frontier is None:
        out["nextStep"] = f"готово: трек {track} чист (clean), все кадры чистые"
    else:
        out["frame"] = frontier["name"]
        text = _frame_next(track, frontier, facts_of[frontier["name"]])
        rest = [f"{v['name']} ({v['stage']})" for v in views[views.index(frontier) + 1:] if v["stage"] != "clean"]
        if rest:
            text += f". Далее по треку: {_names(rest)}"
        out["nextStep"] = text
    out["frames"] = views
    return out, warns


# ── проект ───────────────────────────────────────────────────────────────────────────────────────────

def _design_system(root: Path, cfg: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Warn]]:
    """Источник библиотеки и построены ли требования ДС (только для stories|sources: при storybook-mcp
    производная копия не строится никогда — INV-A6)."""
    if not cfg["initialized"]:
        return None, []
    source = cfg["config"]["library"]["source"]
    if source == "storybook-mcp":
        return {"source": source, "requirements": "not-needed"}, []
    base = Path(root) / C.CONFIG_REL.parent
    missing = [d for d in DS_DOCS if not (base / d).is_file()]
    if not missing:
        return {"source": source, "requirements": "built"}, []
    return ({"source": source, "requirements": "missing"},
            [("ds-requirements-missing", f"требования дизайн-системы не строились (library.source: {source}): нет "
                                         f"{_names([C.CONFIG_REL.parent.as_posix() + '/' + d for d in missing])} — "
                                         f"без них контракт компонентов библиотеки нечем проверять, а читать "
                                         f"библиотеку напрямую нельзя; построй их инструментом "
                                         f"daiver_ds_requirements")])


def status(root: Path, story_spec: Optional[StoryResolver] = None) -> Dict[str, Any]:
    """Ответ daiver_status. story_spec(track) → путь ui-spec.md стори (его знает хост, не движок);
    без него проверяются только спеки, зарегистрированные в track.yaml."""
    root = Path(root)
    cfg = C.load(root)
    c = cfg["config"]
    summary = {
        "framework": c["project"]["framework"], "language": c["project"]["language"],
        "styling": c["project"]["styling"]["mode"], "outputDir": c["project"]["outputDir"],
        "buildCommand": c["project"]["buildCommand"], "librarySource": c["library"]["source"],
        "vision": "on" if c["verify"]["vision"] else "off",
    } if cfg["exists"] else None
    design_system, warns = _design_system(root, cfg)
    tracks: List[Dict[str, Any]] = []
    for name in TR.list_tracks(root):
        view, w = track_status(root, name, story_spec)
        tracks.append(view)
        warns += w
    open_tracks = [t for t in tracks if t["stage"] != "clean"]
    if not cfg["initialized"]:
        next_step = NEXT_INIT
    elif not tracks:
        next_step = NEXT_SPEC
    elif not open_tracks:
        next_step = f"все треки чистые (clean). Новая стори — {NEXT_SPEC}"
    else:
        next_step = (f"{open_tracks[0]['nextStep']}. Если твоя стори — другая (её ключа нет среди tracks[]), "
                     f"начни с {STEP_SPEC}")
    warns.sort(key=lambda w: w[0] not in LOUD)          # стабильно: громкие первыми, остальные в порядке обхода
    return {
        "engine": VERSION,
        "initialized": cfg["initialized"],
        "config": summary,
        "designSystem": design_system,
        "problems": cfg["problems"],
        "warnings": list(cfg["warnings"]) + [f"{code}: {text}" for code, text in warns],
        "tracks": tracks,
        "nextStep": next_step,
    }
