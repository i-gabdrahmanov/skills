"""plan_contract.py — контракт готовности плана и его подпись (спека §12.3, §11.6, §20.4; задача 2.6; INV-C8, C9, D1, F6, A11).

Идея спеки: вопросы планирования (какой адрес у страницы, чем её запускать, где иллюстрации, есть ли браузер…) задаются на план-этапе,
пока человек рядом, а не посреди автономной сборки, где отвечать некому. `daiver_plan_contract(track)` читает ДИСК (не память) и
возвращает, что автономный прогон ещё спросил бы: 16 проверок — `initialized, library-source, browser, requirement, canonical, plan,
resolutions, assets, goal-image, specs, state, baseline, sources, implementation, dev-command, signature`. У каждого нарушенного
пункта — `{id, frame?, state, blocking, blockedReason, owner, detail, ask, record?, degradesTo?}`: что не так, кто отвечает
(human | plan | host), ДОСЛОВНЫЙ вопрос человеку, чем записать ответ и во что деградирует, если не отвечать. Состояния пункта —
`ok | open | stale | unknown` (§20.4): `unknown` — проверить нельзя, потому что нет данных, от которых она зависит (канон не читается);
блокирующая проверка в любом состоянии, кроме ok, блокирует. Проверка, которая упала, — открытый блокирующий пункт, а не «прошла».

`ready` — нет блокирующих пунктов (подпись в них НЕ входит); `complete` — нет и деградаций; `signed` — подпись валидна. Гейт ui-design
пропускает только `ready` + `signed`.

Подпись (D8) — не инструмент модели, а согласие человека `ui-plan-<KEY>` с цитатой, привязанное `record_approval.py --bind-file` к
`plan.lock.json`. Lock — производный файл трека: sha256 требований кадров, `plan.json` кадров, `resolution-report.md` и `ui-plan.md`.
`signed` = согласие есть, `bound_sha256` равен sha256 lock'а, СОБРАННОГО ИЗ ТЕКУЩИХ артефактов (а не прочитанного с диска), и файлы
`ui-plan.md`/`plan.lock.json` на диске им равны. Поэтому перетянутое требование, правка плана/вердикта/карты/спеки руками или
инструментом делают подпись `stale` с названной причиной, даже если никто не пересобрал lock. Гейт и `evaluate(write=False)` только
читают; `daiver_plan_contract` (write=True) пересобирает `ui-plan.md` и lock, когда план готов, и снимает устаревшее согласие
(`record_approval` не пишет второе согласие по активному ключу — как с `ui-resolve`, 2.5).

Подпись НЕ зависит от `ready`: в дайджест не входят блокирующие пункты (файл пишется, только когда их нет), поэтому пропавший на
машине браузер делает план «не готовым», но не «не подписанным», а правка, откатившаяся до пересборки готового плана, подпись не
сжигает. Читает подпись `signature_state` — для `ui-guard` (3.3): он проверяет согласие по lock'у, собранному из артефактов, а не
хешем файла `plan.lock.json`.

Отклонения от спеки (README): `resolutions` блокирует (в спеке — нет: автономия появится в 7.1/7.2, до неё подтверждение требуется
всегда); `signature` блокирует всегда (в спеке — «только при confirmPlan»: `phase_approvals` ui-build/ui-verify требуют `ui-plan-<KEY>`
безусловно, D9); `browser` и `baseline/sources` проверяются минимально (поиск Chromium — `daiver/browser/find.py`, задача 4.1; база страницы — по полям
`sources`/`scope` манифеста и непустому `baseline/`, захват базы и поток правки страницы — 4.x/6.4); поле `directive` ответа спеки —
в 7.1 (грамматику строки задаёт она).

Не проверено данными (боевых планов и экранов нет): формулировки вопросов `ask` и то, как слабая модель их читает; полнота кадров
против таблицы «Кадры» (сопоставление строки с кадром — по ссылке макета, затем по имени); смысл `unknown`; `planUncertainty` как
число (спека называет его, но не определяет — здесь: узлы с вердиктом ниже high и записи раскладки без свидетельства).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import canon as C
from . import component_map as CM
from . import config as CFG
from . import ds_requirements as DR
from . import outline as OL
from . import plan as PL
from . import plan_digest as PD
from . import resolution_report as RR
from . import resolve as RS
from . import svg_assets as SA
from . import track as TR
from . import uispec as U
from .browser import find as BF

CHECK_IDS = ("initialized", "library-source", "browser", "requirement", "canonical", "plan", "resolutions", "assets",
             "goal-image", "specs", "state", "baseline", "sources", "implementation", "dev-command", "signature")
STATES = ("ok", "open", "stale", "unknown")
OWNERS = ("human", "plan", "host")
CONSENT_PREFIX = "ui-plan"
UI_PLAN_NAME = "ui-plan.md"
LOCK_NAME = "plan.lock.json"
BROWSER_ENV = BF.BROWSER_ENV
SHOWN = 10

# Причина останова автономного прогона, к месту которой стоит пункт (§20.3); None — пункт не останавливает прогон сам.
REASONS = {"initialized": "not-initialized", "library-source": "library-source-unreachable", "browser": "no-browser",
           "requirement": "no-requirement", "canonical": "plan-incomplete", "plan": "plan-incomplete",
           "resolutions": "plan-incomplete", "implementation": "no-implementation", "baseline": "plan-incomplete",
           "sources": "no-sources", "signature": "plan-unsigned"}
OPEN_RECIPE_REASON = "no-open-recipe"
DEV_COMMAND_REASON = "no-dev-command"
TRACK_CONFLICT = "track-conflict"

StoryResolver = Callable[[str], Optional[Path]]


class ContractError(ValueError):
    """Контракт построить нельзя: трека или кадров нет, манифест повреждён."""


def consent_key(track: str) -> str:
    """Ключ подписи плана: `ui-plan-<KEY>` (класс согласий с цитатой пользователя, forge_events.CONSENT_PREFIXES)."""
    return f"{CONSENT_PREFIX}-{track}"


def ui_plan_path(root: Any, track: str) -> Path:
    return TR.track_dir(Path(root), track) / UI_PLAN_NAME


def lock_path(root: Any, track: str) -> Path:
    return TR.track_dir(Path(root), track) / LOCK_NAME


def item(cid: str, state: str, blocking: bool, owner: str, detail: str, *, frame: Optional[str] = None,
         reason: Optional[str] = None, ask: Optional[str] = None, record: Optional[str] = None,
         degrades: Optional[str] = None, na: bool = False) -> Dict[str, Any]:
    """Результат одной проверки. Ключи — только с содержанием (ответ читает слабая модель): ok-записи несут id, state, detail."""
    assert cid in CHECK_IDS and state in STATES and owner in OWNERS, (cid, state, owner)
    out: Dict[str, Any] = {"id": cid}
    if frame is not None:
        out["frame"] = frame
    out.update({"state": state, "blocking": blocking})
    if state != "ok" and reason:
        out["blockedReason"] = reason
    out["owner"] = owner
    out["detail"] = detail
    if state != "ok":
        for key, val in (("ask", ask), ("record", record), ("degradesTo", degrades)):
            if val:
                out[key] = val
    if na:
        out["na"] = True
    return out


def ok(cid: str, owner: str, detail: str, frame: Optional[str] = None, na: bool = False) -> Dict[str, Any]:
    return item(cid, "ok", cid not in ("assets", "goal-image", "specs", "state", "dev-command"), owner, detail, frame=frame, na=na)


# ── поиск браузера (daiver.browser.find, задача 4.1) ─────────────────────────

def find_browser(config: Optional[Dict[str, Any]] = None, root: Any = None) -> Tuple[Optional[str], str]:
    """(путь, источник | причина). Поиск — `daiver.browser.find`: переменная CHROMIUM_EXECUTABLE_PATH → `verify.browserPath` /
    `verify.browserChannel` конфига (`config` — блок verify) → стандартные места платформы → PATH. Ничего не скачивается и не
    запускается; заданная, но битая переменная или путь конфига не заменяются молча другим браузером."""
    found = BF.find_browser(config, root=root)
    return (found["path"], found["detail"]) if found["found"] else (None, found["reason"])


# ── мир: всё, что читают проверки ────────────────────────────────────────────

class Facts:
    """Факты об одном кадре с кешем: проверки разных пунктов не читают канон и вердикты по два раза."""

    def __init__(self, w: "World", f: Dict[str, Any]):
        self.w, self.f, self.name = w, f, f["name"]
        self.paths = TR.frame_paths(w.root, w.manifest, self.name)
        self._memo: Dict[str, Any] = {}

    def _once(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def req_status(self) -> str:
        def calc() -> str:
            try:
                return TR.requirement_status(self.w.root, self.w.manifest, self.name)
            except OSError:
                return "missing"
        return self._once("req", calc)

    def canon(self) -> Tuple[Optional[dict], Optional[str]]:
        """(канон | None, причина). Нет файла и нечитаемый файл — разные причины."""
        def calc() -> Tuple[Optional[dict], Optional[str]]:
            if not self.paths["canonical"].is_file():
                return None, "канона нет — daiver_normalize"
            try:
                return C.read_canonical(self.paths["canonical"]), None
            except (C.CanonError, RecursionError) as e:
                return None, f"канон не читается ({str(e)[:160]}) — daiver_normalize заново"
        return self._once("canon", calc)

    def ctx(self) -> Tuple[Optional["RS.Ctx"], Optional["RS.State"], Optional[str]]:
        def calc() -> Tuple[Optional["RS.Ctx"], Optional["RS.State"], Optional[str]]:
            try:
                ctx = RS.Ctx(self.w.root, self.w.track, self.name)
                return ctx, ctx.state(RS.read_store(ctx.root, ctx.spath)), None
            except RS.ResolveError as e:
                return None, None, str(e)
        return self._once("ctx", calc)

    def plan(self) -> Dict[str, Any]:
        """Состояние плана кадра (plan.check_frame); нет файла/нечитаем — open без пересборки, нет данных для сверки — unknown."""
        def calc() -> Dict[str, Any]:
            data, _raw, why = PL.read_plan(self.paths["plan"])
            if why or data is None:
                return {"state": "open", "detail": (f"plan.json не читается ({why}) — неразбираемый план не проходит; пересобери: "
                                                    f"daiver_plan_write(track, frame, plan)") if why
                        else "плана кадра нет — daiver_plan_write(track, frame, plan)"}
            ctx, st, err = self.ctx()
            if ctx is None:
                return {"state": "unknown", "detail": f"план нельзя сверить с данными: {err}"}
            try:
                return PL.check_frame(self.w.root, ctx, st, self.w.story_spec)
            except Exception as e:                           # noqa: BLE001 — сбой сверки не «проходит»: пункт plan блокирует
                return {"state": "unknown", "detail": f"сверка плана не выполнилась: {type(e).__name__}: {str(e)[:160]}"}
        return self._once("plan", calc)

    def arts(self) -> Tuple[Optional[List[dict]], List[str], Optional[str]]:
        """(арты кадра | None, slug без .svg, причина)."""
        def calc() -> Tuple[Optional[List[dict]], List[str], Optional[str]]:
            try:
                arts, _w = SA.load_arts(self.w.root, self.w.manifest, self.f)
            except (SA.SvgAssetsError, RecursionError) as e:
                return None, [], str(e)[:200]
            return arts, SA.missing_slugs(arts, self.paths["assets"]), None
        return self._once("arts", calc)


class World:
    def __init__(self, root: Any, track: str, story_spec: Optional[StoryResolver]):
        self.root = Path(root)
        self.track = TR.check_track_name(track)
        self.story_spec = story_spec
        try:
            manifest = TR.load(self.root, self.track)
        except TR.TrackError as e:
            raise ContractError(str(e))
        if manifest is None:
            raise ContractError(f"трека «{track}» нет — сначала daiver_pixso_pull")
        if not manifest.get("frames"):
            raise ContractError(f"в треке «{track}» нет ни одного кадра — их заводит daiver_pixso_pull по таблице «Кадры» ui-spec.md")
        self.manifest = manifest
        self.cfg = CFG.load(self.root)
        self.frames: Dict[str, Facts] = {f["name"]: Facts(self, f) for f in manifest["frames"]}
        self._table: Optional[Dict[str, Any]] = None

    @property
    def multi(self) -> bool:
        return len(self.frames) > 1

    def table(self) -> Dict[str, Any]:
        """Таблица «Кадры» ui-spec.md стори; {} — файла нет или он не читается (полнота кадров тогда не проверяется)."""
        if self._table is None:
            self._table = {}
            story = self.story_spec(self.track) if self.story_spec is not None else None
            if story is not None and Path(story).is_file():
                try:
                    res = U.frames_table(Path(story).read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    res = {}
                self._table = res if res.get("found") else {}
        return self._table

    def row_frame(self, row: Dict[str, str]) -> Optional[str]:
        """Кадр трека, соответствующий строке таблицы: сначала по ссылке макета, потом по имени (имена свободные, имя кадра —
        каталог, поэтому ссылка надёжнее)."""
        m = U._URL.search(row.get("mockup") or "")
        url = m.group(0).rstrip(".,;)") if m else None
        if url:
            for name, fx in self.frames.items():
                fu = fx.f.get("frameUrl")
                if isinstance(fu, str) and fu.strip().rstrip(".,;)") == url:
                    return name
        key = CM.name_key(row.get("name") or "")
        if key:
            for name in self.frames:
                if CM.name_key(name) == key:
                    return name
        return None

    def route_of(self, frame: str) -> Optional[str]:
        for row in self.table().get("rows") or []:
            if self.row_frame(row) == frame:
                route = (row.get("route") or "").strip()
                return route if route and route not in ("—", "-") else None
        return None


def _who(w: World, frame: str) -> str:
    return f"кадр «{frame}»"


def _p(w: World, frame: str) -> str:
    """Приставка пункта: у трека из нескольких кадров — «кадр «x»: », у одного кадра имя не нужно."""
    return f"кадр «{frame}»: " if w.multi else ""


# ── проверки ─────────────────────────────────────────────────────────────────

def chk_initialized(w: World) -> List[Dict[str, Any]]:
    cfg = w.cfg
    if cfg["initialized"]:
        return [ok("initialized", "plan", ".daiver/config.yaml на месте и проходит проверку")]
    why = "; ".join(cfg["problems"][:3]) or "config.yaml не разобран"
    return [item("initialized", "open", True, "plan", f"проект не инициализирован: {why}", reason=REASONS["initialized"],
                 ask="Проект не инициализирован. Назовите стек (framework, language, styling), outputDir, команду сборки "
                     "(buildCommand) и источник библиотеки (storybook-mcp | stories | sources) — значения не угадываются.",
                 record="daiver_init без answers покажет кандидатов; затем daiver_init с answers (config.yaml руками не пишется)")]


def chk_library(w: World) -> List[Dict[str, Any]]:
    lib = w.cfg["config"]["library"]
    src = lib["source"]
    reason = REASONS["library-source"]
    if src is None:
        return [item("library-source", "open", True, "human", "library.source не задан — источник сигнатур библиотеки не угадывается",
                     reason=reason, ask="Откуда брать компоненты библиотеки: storybook-mcp, stories (истории) или sources (исходники)?",
                     record="daiver_init (config.yaml руками не пишется)")]
    if src == "storybook-mcp":
        if lib.get("storybookMcp"):
            return [ok("library-source", "human", f"storybook-mcp «{lib['storybookMcp']}»: список компонентов даёт его сервер (доступность "
                                                 f"проверил резолв)")]
        return [item("library-source", "open", True, "human", "library.storybookMcp не задан", reason=reason,
                     ask="Как называется MCP-сервер сторибука в настройках GigaCode?", record="daiver_init")]
    res = DR.check(w.root, recompute=False)
    state = res["state"]
    if state in ("ok", "not-needed"):
        return [ok("library-source", "human", f"требования ДС построены ({res.get('components', '?')} компонентов, источник {src})")]
    why = "; ".join((res.get("reasons") or res.get("problems") or [])[:3]) or state
    ask = ("Требования дизайн-системы не построены или не подходят к библиотеке: проверьте library.scanPaths (каталоги историй/"
           "исходников) — после этого они строятся один раз.")
    record = "daiver_ds_requirements (после правки scanPaths человеком; check:true — проверить, устарели ли)"
    if state == "stale":
        return [item("library-source", "stale", True, "human", f"требования ДС построены для другой библиотеки: {why}", reason=reason,
                     ask=ask, record=record)]
    return [item("library-source", "open", True, "human", f"требований ДС нет или они повреждены ({state}): {why}", reason=reason,
                 ask=ask, record=record)]


def chk_browser(w: World) -> List[Dict[str, Any]]:
    path, how = find_browser(w.cfg["config"]["verify"], w.root)
    if path:
        return [ok("browser", "human", f"браузер для verify найден ({how})")]
    return [item("browser", "open", True, "human", f"Chrome/Chromium для проверки страницы не найден: {how}", reason=REASONS["browser"],
                 ask=(f"Где на этой машине Chrome или Chromium? Установите его либо укажите путь переменной окружения {BROWSER_ENV} "
                      f"(или ключом verify.browserPath в .daiver/config.yaml — файл правит человек)."),
                 record=f"переменная окружения {BROWSER_ENV} либо verify.browserPath (ничего не скачивается и не ставится само)")]


def chk_requirement(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        st = fx.req_status()
        if st == "ok":
            out.append(ok("requirement", "plan", "файл требования на месте, sha256 совпал с манифестом", frame=name))
        elif st == "stale":
            out.append(item("requirement", "stale", True, "plan",
                            f"{_p(w, name)}файл требования изменён мимо pull (sha256 не совпал с манифестом)", frame=name,
                            reason=TRACK_CONFLICT, record="daiver_pixso_pull (тянет кадр заново; план и подпись тогда пересобираются)"))
        else:
            why = "требования нет в манифесте" if st == "none" else "файла требования нет на диске"
            out.append(item("requirement", "open", True, "plan", f"{_p(w, name)}{why}", frame=name, reason=REASONS["requirement"],
                            ask=f"Выгрузите DSL кадра «{name}» из Pixso в файл внутри репо (например .daiver/inbox/{name}.dsl.json) и "
                                f"пришлите ссылку на кадр.", record="daiver_pixso_pull(track, dslPath, frameUrl, frame)"))
    rows = w.table().get("rows") or []
    lost = [r.get("name") or "?" for r in rows if w.row_frame(r) is None]
    if lost:
        out.append(item("requirement", "open", True, "plan",
                        f"кадры из таблицы «Кадры» ui-spec.md не заведены в трек: {', '.join(lost[:SHOWN])}", reason=REASONS["requirement"],
                        record="daiver_pixso_pull для каждого (имя и ссылка на макет — из таблицы)"))
    elif rows:
        out.append(ok("requirement", "plan", f"все кадры таблицы «Кадры» ({len(rows)}) заведены в трек"))
    return out


def chk_canonical(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        env, err = fx.canon()
        if env is None:
            out.append(item("canonical", "open", True, "plan", f"{_p(w, name)}{err}", frame=name, reason=REASONS["canonical"],
                            record="daiver_normalize(track, frame)"))
            continue
        src = (env.get("stats") or {}).get("sourceSha256")
        want = (fx.f.get("requirement") or {}).get("sha256")
        if src and want and src != want:
            out.append(item("canonical", "stale", True, "plan",
                            f"{_p(w, name)}канон построен по другому экспорту, чем записан в манифесте (требование перетянуто)",
                            frame=name, reason=REASONS["canonical"], record="daiver_normalize(track, frame)"))
        else:
            out.append(ok("canonical", "plan", "канон читается и построен по текущему требованию", frame=name))
    return out


def chk_plan(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        res = fx.plan()
        if res["state"] == "ok":
            out.append(ok("plan", "plan", "план равен тому, что строит движок из текущих данных", frame=name))
        else:
            out.append(item("plan", res["state"], True, "plan", f"{_p(w, name)}{res['detail']}", frame=name, reason=REASONS["plan"],
                            record="daiver_plan_write(track, frame, plan)"))
    return out


def chk_resolutions(w: World) -> List[Dict[str, Any]]:
    """Резолв закончен, отчёт актуален, вердикты, которые обязан увидеть человек, подтверждены (`resolution_report.check`). Нерешённое
    называет кадр; устаревший или неподтверждённый отчёт относится к треку целиком — «ок» по кадрам тогда не выдаётся."""
    problems = RR.check(w.root, w.track)
    if not problems:
        return [ok("resolutions", "plan", "все кандидаты решены, отчёт актуален, вердикты подтверждены", frame=name) for name in w.frames]
    record = "daiver_resolve_candidates → finalize → daiver_resolve_report → вопрос человеку → согласие ui-resolve"
    by_frame: Dict[str, List[Dict[str, Any]]] = {}
    track_level: List[Dict[str, Any]] = []
    for p in problems:
        frame = next((n for n in w.frames if p.startswith(f"кадр «{n}»")), None)
        it = item("resolutions", "open", True, "plan", p, frame=frame, reason=REASONS["resolutions"], record=record)
        (by_frame.setdefault(frame, []) if frame else track_level).append(it)
    out: List[Dict[str, Any]] = []
    for name in w.frames:
        if name in by_frame:
            out += by_frame[name]
        elif not track_level:
            out.append(ok("resolutions", "plan", "кандидаты кадра решены", frame=name))
    return out + track_level


def chk_assets(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        arts, missing, err = fx.arts()
        if arts is None:
            out.append(item("assets", "unknown", False, "human", f"{_p(w, name)}иллюстрации не определить — {err}", frame=name,
                            degrades="помеченный placeholder на месте каждой иллюстрации"))
        elif not arts:
            out.append(ok("assets", "human", "векторных иллюстраций в кадре нет", frame=name, na=True))
        elif missing:
            names = ", ".join(missing[:SHOWN]) + (f" … +{len(missing) - SHOWN}" if len(missing) > SHOWN else "")
            out.append(item("assets", "open", False, "human", f"{_p(w, name)}иллюстрации без файла .svg: {names}", frame=name,
                            ask=f"Для иллюстраций ({names}) нет файлов .svg. Экспортируйте слои из Pixso как SVG и дайте пути внутри репо "
                                f"— или оставляем помеченные placeholder'ы?",
                            record="daiver_svg_assets(track, frame, slug, svgPath) на каждую",
                            degrades="помеченный placeholder правильного размера вместо иллюстрации"))
        else:
            out.append(ok("assets", "human", f"иллюстраций {len(arts)}, у всех есть файл .svg", frame=name))
    return out


def chk_goal_image(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        info = TR.raster_info(w.root, w.manifest, name)
        if info and info["exists"] and "format" in info:
            out.append(ok("goal-image", "human", f"картинка макета {info['path']} ({info['format']}) на месте", frame=name))
        else:
            what = "картинка макета не записана" if not info else \
                f"картинка {info['path']} записана, но файла нет" if not info["exists"] else \
                f"картинка {info['path']} не читается как PNG/JPEG"
            out.append(item("goal-image", "open", False, "human", f"{_p(w, name)}{what}", frame=name,
                            ask=f"Приложите картинку кадра «{name}» (PNG или JPEG — экспорт кадра из Pixso).",
                            record="daiver_track_manifest(track, frame, rasterPath)",
                            degrades="сверка пойдёт по эталонному рендеру, а не по картинке макета; группировку контейнеров по картинке "
                                     "сделать нельзя"))
    return out


def chk_specs(w: World) -> List[Dict[str, Any]]:
    d = PL.specs_digest(w.root, w.track, w.manifest, w.story_spec)
    degrade = "модуль считается презентационным: данные, события, валидации не подключаются, сценарии e2e не создаются"
    if d["missing"]:
        return [item("specs", "open", False, "host", f"спеки зарегистрированы, но файлов нет: {', '.join(d['missing'][:SHOWN])}",
                     ask="Файлы спек на месте? Верните их либо перерегистрируйте.", record="daiver_track_manifest(track, set={specs})",
                     degrades=degrade)]
    if not d["hasSpecs"]:
        return [item("specs", "open", False, "host", "в спеках стори нет разделов Данные/Состояния/События/Валидации/Сценарии",
                     ask="В ui-spec.md нет бизнес-логики (данные, события, валидации, сценарии). Модуль презентационный — верно? Если "
                         "логика есть, допишите её в ui-spec.md.", degrades=degrade)]
    return [ok("specs", "host", f"спеки разобраны: файлов {len(d['files'])}, сценариев e2e {len(d['e2e'])}")]


def chk_state(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        if fx.f.get("role") != "state":
            out.append(ok("state", "human", "кадр не состояние экрана", frame=name, na=True))
        elif fx.f.get("stateOf"):
            out.append(ok("state", "human", f"состояние экрана «{fx.f['stateOf']}»", frame=name))
        else:
            out.append(item("state", "open", False, "human", f"{_who(w, name)} помечен состоянием, но экран не назван", frame=name,
                            ask=f"Состоянием какого экрана является кадр «{name}»? Назовите кадр-экран трека.",
                            record="daiver_track_manifest(track, frame, set={stateOf})",
                            degrades="кадр превратится во второй экран, а не в состояние первого"))
    return out


def _page_base(fx: Facts) -> bool:
    return bool(fx.f.get("sources"))


def chk_baseline(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        if not _page_base(fx):
            out.append(ok("baseline", "human", "кадр строится с нуля, базы страницы нет", frame=name, na=True))
            continue
        base = fx.paths["baseline"]
        if base.is_dir() and any(p.is_file() for p in base.rglob("*")):
            out.append(ok("baseline", "human", "база страницы захвачена (baseline/ не пуст)", frame=name))
        else:
            out.append(item("baseline", "open", True, "human", f"{_p(w, name)}правит существующую страницу, а базы (baseline/) нет",
                            frame=name, reason=REASONS["baseline"],
                            ask=f"Страница кадра «{name}» уже работает? Запустите её и дайте адрес — снимем базу ДО правок кода (скриншот и "
                                f"оба сравнения в baseline/). Если работающей страницы нет вовсе — скажите, это будет записано открыто.",
                            record="daiver_screenshot → daiver_verify_compare → daiver_code_compare с iterationDir=<папка кадра>/baseline "
                                   "(инструменты проверки — этап 4)"))
    return out


def chk_sources(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        if not _page_base(fx):
            out.append(ok("sources", "plan", "страница в коде не заявлена: строится новый модуль", frame=name, na=True))
            continue
        gone = [p for p in fx.f["sources"] if not (w.root / p).is_file()]
        if gone:
            out.append(item("sources", "open", True, "plan", f"{_p(w, name)}файлов страницы нет на диске: {', '.join(gone[:SHOWN])}",
                            frame=name, reason=REASONS["sources"],
                            ask=f"Каких файлов страницы кадра «{name}» нет на месте? Назовите настоящие пути (найденные чтением, не угаданные).",
                            record="daiver_track_manifest(track, frame, set={sources, scope})"))
        elif fx.f.get("scope") not in TR.SCOPES:
            out.append(item("sources", "open", True, "plan", f"{_p(w, name)}у страницы не задан scope", frame=name,
                            reason=REASONS["sources"],
                            ask=f"Что делаем со страницей кадра «{name}»: меняем точечно по макету (change) или пересобираем целиком (page)?",
                            record="daiver_track_manifest(track, frame, set={scope})"))
        else:
            out.append(ok("sources", "plan", f"страница: файлов {len(fx.f['sources'])}, scope {fx.f['scope']}", frame=name))
    return out


def chk_implementation(w: World) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        url = TR.implementation_url(w.manifest, name)
        role = fx.f.get("role")
        if url:
            out.append(ok("implementation", "human", f"адрес страницы: {url}", frame=name))
        else:
            route = w.route_of(name)
            hint = f" В ui-spec.md для кадра указан маршрут «{route}» — нужен полный адрес с хостом." if route else ""
            note = " (диалог адрес не наследует)" if role == "dialog" else ""
            out.append(item("implementation", "open", True, "human", f"{_p(w, name)}адрес страницы реализации не записан{note}",
                            frame=name, reason=REASONS["implementation"],
                            ask=f"По какому адресу откроется «{name}»? Полный http-адрес, например http://localhost:3000/orders.{hint}",
                            record="daiver_track_manifest(track, frame, set={url})"))
        if role in ("dialog", "state") and not fx.f.get("open"):
            out.append(item("implementation", "open", False, "human", f"{_who(w, name)} ({role}): рецепта открытия нет", frame=name,
                            reason=OPEN_RECIPE_REASON,
                            ask=f"Как показать «{name}» на странице? Опишите шаги: клик по селектору, ожидание, при попапе — clipSelector.",
                            record="daiver_track_manifest(track, frame, set={open})",
                            degrades="рецепт выработает проверка вместе с человеком; автономный прогон остановится (blocked: no-open-recipe)"))
    return out


def chk_dev_command(w: World) -> List[Dict[str, Any]]:
    impl = w.manifest.get("implementation") or {}
    if impl.get("devCommand"):
        return [ok("dev-command", "human", f"команда запуска страницы записана: {impl['devCommand']}")]
    return [item("dev-command", "open", False, "human", "команда запуска dev-сервера не записана", reason=DEV_COMMAND_REASON,
                 ask="Какой ОДНОЙ командой запускается dev-сервер фронта (например `npm run dev`)? Её запускает прогон без человека.",
                 record="daiver_track_manifest(track, set={implementation:{url:<как сейчас>, devCommand:<команда>}}) — объект целиком",
                 degrades="прогон без человека не сможет поднять страницу (blocked: no-dev-command); с человеком страницу запускает он")]


CHECKS: Tuple[Tuple[str, Callable[[World], List[Dict[str, Any]]]], ...] = (
    ("initialized", chk_initialized), ("library-source", chk_library), ("browser", chk_browser),
    ("requirement", chk_requirement), ("canonical", chk_canonical), ("plan", chk_plan), ("resolutions", chk_resolutions),
    ("assets", chk_assets), ("goal-image", chk_goal_image), ("specs", chk_specs), ("state", chk_state),
    ("baseline", chk_baseline), ("sources", chk_sources), ("implementation", chk_implementation),
    ("dev-command", chk_dev_command))


# ── planUncertainty ──────────────────────────────────────────────────────────

def uncertainty(w: World) -> Dict[str, Any]:
    """Узлы, в которых план не уверен, — КАЖДЫЙ ОДИН РАЗ, какой бы носитель его ни назвал. Носителей два, оба читаются с диска и не
    зависят от того, разбирается ли plan.json: (1) вердикты резолва (`resolutions.json`): эвристический вердикт с уверенностью ниже
    high; (2) записи `unproven` канона без решения со свидетельством (нет решения, свидетельство `none`, плана нет или он нечитаем).
    Узел, названный обоими, считается за первым носителем. Кадр, чей канон или хранилище не читаются, назван в `unavailable`."""
    seen: set = set()
    nodes: List[str] = []
    by = {"resolutions": 0, "layout": 0}
    unavailable: List[str] = []

    def add(frame: str, nid: str, carrier: str) -> None:
        if (frame, nid) in seen:
            return
        seen.add((frame, nid))
        nodes.append(f"{frame}:{nid}" if w.multi else nid)
        by[carrier] += 1

    for name, fx in w.frames.items():
        ctx, st, _err = fx.ctx()
        if ctx is None:
            unavailable.append(name)
            continue
        for c in ctx.sc.cands:
            rec = st.valid.get(c.id)
            if rec is not None and rec["source"] != "map" and rec["confidence"] != "high":
                add(name, c.id, "resolutions")
        data, _raw, _why = PL.read_plan(fx.paths["plan"])
        decided: Dict[Tuple[str, Any], Any] = {}
        lay = data.get("layout") if isinstance(data, dict) else None
        if isinstance(lay, dict):
            for d in lay.get("decisions") or []:
                if isinstance(d, dict) and isinstance(d.get("id"), str):
                    decided[(d["id"], d.get("reason"))] = d.get("evidence")
        for e in OL.unproven_entries(ctx.env["root"]):
            if decided.get((e["id"], e["reason"]), "none") == "none":
                add(name, e["id"], "layout")
    out: Dict[str, Any] = {"count": len(nodes), "by": {k: v for k, v in by.items() if v}, "nodes": nodes[:20]}
    if len(nodes) > 20:
        out["nodesMore"] = len(nodes) - 20
    if unavailable:
        out["unavailable"] = unavailable
    return out


# ── ui-plan.md, lock, подпись ────────────────────────────────────────────────

def build_artifacts(w: World, items: List[Dict[str, Any]], unc: Dict[str, Any]) -> Tuple[Optional[str], Optional[bytes]]:
    """(ui-plan.md, plan.lock.json) из ТЕКУЩИХ артефактов; (None, None) — планы кадров ещё не равны тому, что строит движок.
    Чистая функция диска: ни времени, ни случайности; в дайджест не входят пункт signature (его состояние зависит от хеша дайджеста) и
    блокирующие пункты (файл пишется, только когда их нет): подпись не слетает оттого, что на машине пропал браузер."""
    plans: Dict[str, Dict[str, Any]] = {}
    for name, fx in w.frames.items():
        res = fx.plan()
        if res["state"] != "ok":
            return None, None
        plans[name] = res["plan"]
    frames: List[Dict[str, Any]] = []
    for name, fx in w.frames.items():
        arts, missing, _err = fx.arts()
        info = TR.raster_info(w.root, w.manifest, name)
        row: Dict[str, Any] = {"name": name, "role": fx.f.get("role") or "screen", "frameUrl": fx.f.get("frameUrl"),
                               "url": TR.implementation_url(w.manifest, name), "open": bool(fx.f.get("open")),
                               "goalImage": bool(info and info["exists"] and "format" in info), "plan": plans[name],
                               "arts": [a["slug"] for a in arts or []], "missingArts": list(missing)}
        if fx.f.get("stateOf"):
            row["stateOf"] = fx.f["stateOf"]
        frames.append(row)
    degradations = [i for i in items if i["id"] != "signature" and i["state"] != "ok" and not i["blocking"]]
    digest = PD.render(w.track, frames, str(w.cfg["config"]["library"]["source"]), degradations, unc,
                       f"forge-ui: track={w.track} from=plan")
    report = RR.report_path(w.root, w.track)
    lock = {"schema": 1, "track": w.track,
            "frames": [{"name": n, "requirementSha256": (w.frames[n].f.get("requirement") or {}).get("sha256"),
                        "planSha256": PL.sha256_bytes(PL.dumps(plans[n]).encode("utf-8"))} for n in w.frames],
            "resolutionReportSha256": PL.sha256_bytes(report.read_bytes()) if report.is_file() else None,
            "uiPlanSha256": PL.sha256_bytes(digest.encode("utf-8"))}
    return digest, (json.dumps(lock, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def _disk(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def signature_item(w: World, digest: Optional[str], lock: Optional[bytes], ready: bool, write: bool) -> Tuple[Dict[str, Any], Optional[str]]:
    """Пункт `signature` и, если устаревшее согласие снято, — причина. Снимает ТОЛЬКО писатель (write) и только когда план готов:
    `record_approval` не запишет второе согласие по активному ключу, а на машине без браузера (план «не готов» из-за окружения)
    подпись не должна пропадать насовсем."""
    FE, _P = RR._hooks()
    key = consent_key(w.track)
    rec = FE.approval(w.root, key)
    revoked: Optional[str] = None
    sha = PL.sha256_bytes(lock) if lock is not None else None
    if write and rec is not None and ready and lock is not None:
        bound = rec.get("bound_sha256")
        if not bound:
            revoked = "согласие записано без --bind-file: на содержимое плана оно не опирается"
        elif bound != sha:
            revoked = "план, требование, отчёт резолва или дайджест изменились после подписи"
        if revoked:
            FE.revoke_approval(w.root, key, reason=f"подпись снята: {revoked}")
            rec = None
    sign = (f"Подписываете план (ui-plan.md)? Подпись привязана к тексту плана: любая правка макета, резолва или плана её снимет и "
            f"спросит снова.")
    how = (f"после явного «да» оркестратор: record_approval.py --key {key} --bind-file .daiver/tracks/{w.track}/{LOCK_NAME} "
           f"--evidence \"<ответ дословно>\" (подпись не инструмент модели)")
    extra = ""
    if not w.cfg["config"]["workflow"].get("confirmPlan", True):
        extra = " workflow.confirmPlan: false в конфиге подпись не отменяет — ui-build/ui-verify требуют её безусловно."
    if lock is None or digest is None:
        if rec is not None:
            return (item("signature", "stale", True, "human",
                         "подпись относилась к прежнему плану: планы кадров больше не равны тому, что строит движок (см. пункт plan) — "
                         "после пересборки плана прежнее согласие снимется и подпись спросят заново" + extra, reason=REASONS["signature"],
                         ask=sign, record=how), revoked)
        return (item("signature", "open", True, "human", "подписывать нечего: планы кадров не готовы (см. пункт plan)" + extra,
                     reason=REASONS["signature"]), revoked)
    if rec is None:
        why = f"прежняя подпись снята: {revoked}. " if revoked else ""
        return (item("signature", "open", True, "human", f"{why}план не подписан: нет согласия {key}" + extra,
                     reason=REASONS["signature"], ask=sign, record=how), revoked)
    bound = rec.get("bound_sha256")
    if not bound:
        return (item("signature", "stale", True, "human", f"согласие {key} записано без --bind-file: на содержимое плана оно не опирается",
                     reason=REASONS["signature"], ask=sign, record=how), revoked)
    if bound != sha:
        return (item("signature", "stale", True, "human",
                     "план, требование, отчёт резолва или дайджест изменились после подписи (sha256 lock не совпал с тем, что "
                     "человек подписал)", reason=REASONS["signature"], ask=sign, record=how), revoked)
    if _disk(ui_plan_path(w.root, w.track)) != digest.encode("utf-8") or _disk(lock_path(w.root, w.track)) != lock:
        return (item("signature", "stale", True, "human",
                     f"{UI_PLAN_NAME} или {LOCK_NAME} на диске не соответствуют плану (удалены или правлены) — daiver_plan_contract "
                     f"пересоберёт их; подпись вернётся, если содержимое то же", reason=REASONS["signature"], ask=sign, record=how), revoked)
    return (ok("signature", "human", f"план подписан: согласие {key} привязано к текущему {LOCK_NAME}"), revoked)


# ── контракт ─────────────────────────────────────────────────────────────────

class Contract:
    def __init__(self, w: World, items: List[Dict[str, Any]], unc: Dict[str, Any], written: List[str],
                 revoked: Optional[str], digest: Optional[str]):
        self.w, self.track, self.items, self.unc, self.written, self.revoked, self.digest = w, w.track, items, unc, written, revoked, digest
        body = [i for i in items if i["id"] != "signature"]
        self.ready = not any(i["blocking"] and i["state"] != "ok" for i in body)
        self.complete = self.ready and not any(i["state"] != "ok" for i in body)
        sig = next(i for i in items if i["id"] == "signature")
        self.signature = sig
        self.signed = sig["state"] == "ok"
        order = {cid: n for n, cid in enumerate(CHECK_IDS)}
        self.open = sorted((i for i in items if i["state"] != "ok"), key=lambda i: order[i["id"]])    # sorted устойчива: кадры по порядку
        self.blocking = [i for i in self.open if i["blocking"] and i["id"] != "signature"]

    def label(self, i: Dict[str, Any]) -> str:
        return f"{i['id']}[{i['frame']}]" if i.get("frame") and self.w.multi else i["id"]

    def checks(self) -> List[str]:
        out = []
        for cid in CHECK_IDS:
            parts = []
            for r in (i for i in self.items if i["id"] == cid):
                tag = "н/п" if r.get("na") else r["state"]
                parts.append(f"{r['frame']} {tag}" if r.get("frame") and self.w.multi else tag)
            out.append(f"{cid}: " + ", ".join(parts))
        return out

    def blocked(self) -> Optional[str]:
        if not self.ready:
            ids = []
            for i in self.blocking:
                if i["id"] not in ids:
                    ids.append(i["id"])
            return f"plan-incomplete ({', '.join(ids)})"
        return None if self.signed else "plan-unsigned"

    def next_step(self) -> str:
        key = consent_key(self.track)
        if self.blocking:
            first = self.blocking[0]
            if first.get("ask") and first["owner"] == "human":
                how = f"; ответ запиши: {first['record']}" if first.get("record") else ""
                return (f"{self.label(first)}: спроси пользователя — «{first['ask']}»{how}; затем снова daiver_plan_contract "
                        f"(блокирующих пунктов: {len(self.blocking)})")
            rec = first.get("record")
            return (f"{self.label(first)}: {first['detail']}"
                    + (f" → {rec}" if rec and rec not in first["detail"] else "")
                    + f" (блокирующих пунктов: {len(self.blocking)}); потом снова daiver_plan_contract")
        if not self.signed:
            soft = [i for i in self.open if not i["blocking"]]
            tail = (f" Принятые деградации ({len(soft)}) уже в ui-plan.md: человек видит цену каждой." if soft else "")
            return (f"план готов к подписи. Верни needs_input: вопрос kind=sign-plan со строками toSign дословно и путём "
                    f"{UI_PLAN_NAME}; согласие {key} запишет оркестратор после явного «да» (подпись не инструмент модели).{tail}")
        return "план подписан: ui_gate.py design вернёт 0 — гоняй гейт через record_gate.py и закрывай шаг"

    def to_sign(self) -> List[str]:
        """Строки вопроса «Подписываем план?»: факты из plan.json кадров и пунктов контракта; пересказ ui-plan.md в 6–8 строк."""
        plans = [fx.plan().get("plan") for fx in self.w.frames.values()]
        plans = [p for p in plans if p]
        if not plans:
            return []
        n = lambda key: sum(p["stats"][key] for p in plans)          # noqa: E731
        lib = sorted({e["component"] for p in plans for s in p["sections"] for e in s["nodes"] if "component" in e})
        shared = sorted({x["name"] for p in plans for x in p["locals"] if x["shared"]})
        lines = [f"План: {ui_plan_path(self.w.root, self.track).relative_to(self.w.root).as_posix()} (по кадрам — plan.md рядом с plan.json)",
                 f"Кадров {len(plans)}, секций {n('sections')} (строится {n('buildable')}); узлов: библиотека {n('component')}, "
                 f"local {n('local')}, ignore {n('ignore')}",
                 "Компоненты библиотеки: " + (", ".join(lib[:SHOWN]) + (" …" if len(lib) > SHOWN else "") or "нет")]
        if shared:
            lines.append("Общие local-компоненты: " + ", ".join(shared[:SHOWN]))
        mods = [(fx.f["name"], p["module"]) for fx, p in zip(self.w.frames.values(), plans) if p.get("module")]
        if mods:
            lines.append("Один модуль трека; корни кадров: " + "; ".join(f"{n} — " + (f"внутри первичного, узел {m['root']['host']}" if m["root"]["mode"] == "inside-primary"
                                                                                      else f"отдельный компонент {m['root']['component']}") for n, m in mods[:SHOWN]))
        soft = [i for i in self.open if not i["blocking"]]
        lines.append("Принятые деградации: " + ("; ".join(f"{self.label(i)} — {RS._clip(i.get('degradesTo') or i['detail'], 90)}"
                                                         for i in soft[:6]) if soft else "нет"))
        if self.unc["count"]:
            lines.append(f"Неопределённость, узлов: {self.unc['count']} (вердикты ниже high или раскладка без свидетельства): "
                         + ", ".join(self.unc["nodes"][:8]))
        return lines

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"track": self.track, "ready": self.ready, "complete": self.complete, "signed": self.signed}
        blocked = self.blocked()
        if blocked:
            out["blocked"] = blocked
        out["blocks"] = [self.label(i) for i in self.blocking]
        out["checks"] = self.checks()
        out["open"] = [{k: v for k, v in i.items() if k != "na"} for i in self.open]
        out["planUncertainty"] = self.unc
        if self.ready and not self.signed:
            out["toSign"] = self.to_sign()
        if self.written:
            out["written"] = self.written
        if self.revoked:
            out["signatureRevoked"] = f"прежняя подпись снята: {self.revoked} — подписать заново"
        out["nextStep"] = self.next_step()
        return out


def evaluate(root: Any, track: str, story_spec: Optional[StoryResolver] = None, write: bool = False) -> Contract:
    """Контракт готовности трека из диска. write=False — только чтение (гейт); write=True (`daiver_plan_contract`) — когда план
    готов, пересобирает ui-plan.md и plan.lock.json и снимает устаревшее согласие."""
    try:
        w = World(root, track, story_spec)
    except TR.TrackError as e:
        raise ContractError(str(e))
    items: List[Dict[str, Any]] = []
    for cid, fn in CHECKS:
        try:
            items += fn(w)
        except Exception as e:                                # noqa: BLE001 — упавшая проверка не «проходит»: блокирующий пункт
            items.append(item(cid, "open", True, "plan", f"проверка не выполнилась: {type(e).__name__}: {str(e)[:200]} — это сбой "
                                                         f"проверки, а не состояние трека; сообщи пользователю", reason="plan-incomplete"))
    unc = uncertainty(w)
    try:
        digest, lock = build_artifacts(w, items, unc)
    except Exception as e:                                    # noqa: BLE001 — дайджест не собрался: подписывать нечего, план не «готов»
        digest = lock = None
        items.append(item("plan", "open", True, "plan", f"сборка {UI_PLAN_NAME} не выполнилась: {type(e).__name__}: {str(e)[:200]} — "
                                                         f"это сбой движка, а не состояние трека; сообщи пользователю",
                          reason="plan-incomplete"))
    ready = not any(i["blocking"] and i["state"] != "ok" for i in items)
    written: List[str] = []
    if write and ready and digest is not None and lock is not None:
        for path, data in ((ui_plan_path(w.root, w.track), digest.encode("utf-8")), (lock_path(w.root, w.track), lock)):
            if _disk(path) != data:
                PL.write_atomic(w.root, path, data, path.name)
                written.append(PL._rel(w.root, path))
    sig, revoked = signature_item(w, digest, lock, ready, write)
    items.append(sig)
    return Contract(w, items, unc, written, revoked, digest)


def run(root: Any, track: str, story_spec: Optional[StoryResolver] = None) -> Dict[str, Any]:
    """`daiver_plan_contract`."""
    return evaluate(root, track, story_spec, write=True).to_dict()


def signature_state(root: Any, track: str, story_spec: Optional[StoryResolver] = None) -> Dict[str, Any]:
    """Пункт `signature` контракта (только читает): `ok` | `open` | `stale` и `detail`. Для `ui-guard` (3.3): подпись — согласие
    `ui-plan-<KEY>`, привязанное к lock'у, СОБРАННОМУ из текущих артефактов, а не хеш файла `plan.lock.json`. Не зависит от готовности
    контракта. Недёшево: перечитывает канон и вердикты — кешируй по mtime артефактов. Трека нет — ContractError."""
    return evaluate(root, track, story_spec, write=False).signature


# ── гейт ui-design ───────────────────────────────────────────────────────────

def gate_problems(root: Any, track: str, story_spec: Optional[StoryResolver] = None) -> List[str]:
    """Что мешает закрыть ui-design: блокирующие пункты контракта, затем подпись. Пусто — готово. Только читает диск."""
    try:
        c = evaluate(root, track, story_spec, write=False)
    except ContractError as e:
        return [str(e)]
    out = [f"{c.label(i)}: {i['detail']}" for i in c.blocking]
    if not out and not c.signed:
        sig = c.signature
        out.append(f"{sig['detail']}. {sig.get('record', '')}".rstrip())
    return out
