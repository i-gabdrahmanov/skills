"""capture.py — `daiver_screenshot`: один вызов снимает реализацию (спека §14.3, §14.6, константы §20.4).

Пишет в папку итерации `actual.png` + `actual.html` + `snapshot.json` (для эталонного рендера, `target: reference` —
`reference.png` + `reference-snapshot.json`: HTML эталона уже лежит рядом, его написал рендер). Снимок страницы
(`assets/dom-snapshot.js`) — ЕДИНСТВЕННЫЙ источник измеренных значений пайплайна, снимается КАЖДУЮ итерацию.

Порядок: открыть адрес → дождаться готовности (`readiness.py`) → проиграть рецепт открытия (клик, ввод … —
глаголы `recipe.ACTION_VERBS`, «выполнить JS» среди них нет) → снова дождаться → `waitForSelector` /
`waitForGone` → снимок и PNG. Страница НЕ готова — файлы всё равно пишутся (агенту нужно видеть, что там), а в
ответе `ready: false`: дефекты такого захвата не сравнивают и не репортят.

Честность против «подожди ещё»: второй ИДЕНТИЧНЫЙ вызов в ту же папку отказ — одна папка итерации не должна
получить два противоречивых вердикта; правильный ход — поднять `waitMs` или назвать загрузчик. Отпечаток вызова
лежит в самом `snapshot.json` (`capture.fingerprint`), отдельного файла состояния нет.
"""
from __future__ import annotations

import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import canon as CN
from .. import config as CFG
from .. import recipe as RC
from .. import track as TR
from ..browser import cdp as CD
from . import implementation as IMPL
from . import readiness as RD

TARGETS = ("implementation", "reference")
FILES = {"implementation": {"png": "actual.png", "html": "actual.html", "snapshot": "snapshot.json"},
         "reference": {"png": "reference.png", "html": None, "snapshot": "reference-snapshot.json"}}
MIN_WAIT_MS = 2000                      # меньше тишины DOM (1500) + паузы не хватит даже страннице без единой задержки
MAX_WAIT_MS = 300000
UNDER_RENDER_RATIO = 0.5                # элементов меньше половины узлов макета — захват недорисован (спека §14.3)
MAX_FONTS_CHECKED = 20
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "assets" / "dom-snapshot.js"
_script_cache: List[str] = []


class CaptureError(Exception):
    """Захват невозможен или отказан: сообщение адресовано агенту и говорит, что делать."""


def script() -> str:
    if not _script_cache:
        _script_cache.append(SCRIPT_PATH.read_text(encoding="utf-8"))
    return _script_cache[0]


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


# ── клавиши ──────────────────────────────────────────────────────────────────

_KEYS: Dict[str, Tuple[str, int]] = {                  # имя → (code, windowsVirtualKeyCode)
    "Enter": ("Enter", 13), "Escape": ("Escape", 27), "Tab": ("Tab", 9), "Backspace": ("Backspace", 8), "Delete": ("Delete", 46),
    "ArrowUp": ("ArrowUp", 38), "ArrowDown": ("ArrowDown", 40), "ArrowLeft": ("ArrowLeft", 37), "ArrowRight": ("ArrowRight", 39),
    "Home": ("Home", 36), "End": ("End", 35), "PageUp": ("PageUp", 33), "PageDown": ("PageDown", 34), "Space": ("Space", 32),
}
_MODS = {"alt": 1, "control": 2, "ctrl": 2, "meta": 4, "cmd": 4, "shift": 8}


def key_event_params(spec: str) -> List[Dict[str, Any]]:
    """`"Enter"`, `"Control+A"`, `"Shift+Tab"` → пара событий Input.dispatchKeyEvent (нажатие, отпускание). Неизвестная клавиша — ValueError."""
    parts = [p for p in str(spec).split("+") if p != ""] if str(spec) != "+" else ["+"]
    if not parts:
        raise ValueError(f"клавиша «{spec}» пуста")
    mods = 0
    for p in parts[:-1]:
        bit = _MODS.get(p.lower())
        if bit is None:
            raise ValueError(f"модификатор «{p}» неизвестен (допустимы Alt, Control, Meta, Shift)")
        mods |= bit
    key = parts[-1]
    text = ""
    if key in _KEYS:
        code, vk = _KEYS[key]
        if key == "Enter":
            text = "\r"
        elif key == "Space":
            key, text = " ", " "
    elif len(key) == 1:
        up = key.upper()
        code = ("Key" + up) if up.isalpha() else ("Digit" + key if key.isdigit() else "")
        vk = ord(up)
        text = key
    else:
        raise ValueError(f"клавиша «{key}» неизвестна (одиночный символ или {', '.join(sorted(_KEYS))})")
    if mods & (1 | 2 | 4):
        text = ""                                        # сочетание — не ввод символа
    down: Dict[str, Any] = {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": mods}
    if text:
        down["text"] = text
    up_ev = {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": mods}
    return [down, up_ev]


# ── PNG ──────────────────────────────────────────────────────────────────────

def png_size(data: bytes) -> Optional[Dict[str, int]]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return {"w": w, "h": h}


def fingerprint(parts: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ── страница ─────────────────────────────────────────────────────────────────

class _Driver:
    """Тонкая обвязка вкладки: вызовы dom-snapshot.js, ожидание с прокачкой событий (диалоги закрываются сами)."""

    def __init__(self, page: Any):
        self.page = page

    def sleep(self, seconds: float) -> None:
        self.page.conn.pump(seconds)

    def ensure(self) -> None:
        """Скрипт ставится ДО документа (Page.addScriptToEvaluateOnNewDocument); если страница его не получила — доставляем вручную."""
        self.page.evaluate("window.__daiver ? 1 : (window.__daiver = (" + script() + ")(), window.__daiver.install(), 2)")

    def call(self, expr: str, *, timeout: Optional[float] = None, await_promise: bool = False) -> Any:
        return self.page.evaluate("window.__daiver." + expr, timeout=timeout, await_promise=await_promise)

    def poll(self) -> Dict[str, Any]:
        """Один замер готовности. Сбой вызова (страница уходит на другую, скрипт оборван) — это «ещё не готово», а не исключение."""
        try:
            self.ensure()
            return self.call("poll()", timeout=CD.ACTION_TIMEOUT)
        except CD.CdpError as e:
            if isinstance(e, (CD.TargetClosed, CD.CdpDisconnected)):
                raise
            return {"sig": None, "loading": [{"reason": "page", "element": str(e)[:120]}], "sinceMutationMs": 0}

    def wait_ready(self, wait_ms: float) -> Dict[str, Any]:
        return RD.wait_ready(self.poll, wait_ms, sleep=self.sleep, clock=time.monotonic)

    # — рецепт —
    def _located(self, selector: str, scroll: bool, timeout: float, need_visible: bool = True) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            r = self.call(f"locate({_js(selector)}, {_js(scroll)})", timeout=CD.ACTION_TIMEOUT)
            if r.get("bad"):
                raise CaptureError(f"селектор «{selector}» некорректен: {r.get('message')}")
            if r.get("found") and (r.get("visible") or not need_visible):
                return r
            if time.monotonic() >= deadline:
                why = "на странице нет" if not r.get("found") else "на странице есть, но не виден (нулевой размер, display:none или visibility:hidden)"
                raise CaptureError(f"элемент «{selector}» {why} за {int(timeout * 1000)} мс")
            self.sleep(0.1)

    def _mouse(self, kind: str, x: float, y: float, **extra: Any) -> None:
        self.page.send("Input.dispatchMouseEvent", dict({"type": kind, "x": x, "y": y}, **extra))

    def play(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Проигрывает шаги рецепта. Первый же сбой — CaptureError с номером шага: рецепт чинят, а не доигрывают наполовину."""
        done: List[Dict[str, Any]] = []
        for i, a in enumerate(actions):
            verb = a["verb"]
            try:
                self._do(verb, a)
            except CaptureError as e:
                raise CaptureError(f"рецепт, шаг {i + 1} ({verb}{' ' + a['selector'] if a.get('selector') else ''}): {e}")
            except CD.JsException as e:
                raise CaptureError(f"рецепт, шаг {i + 1} ({verb}): страница ответила ошибкой — {e}")
            done.append({"verb": verb})
            self.sleep(0.05)
        return done

    def _do(self, verb: str, a: Dict[str, Any]) -> None:
        t = CD.ACTION_TIMEOUT
        if verb in ("click", "dblclick", "hover"):
            r = self._located(a["selector"], True, t)
            x, y = r["x"], r["y"]
            self._mouse("mouseMoved", x, y)
            if verb == "hover":
                return
            for n in ((1,) if verb == "click" else (1, 2)):
                self._mouse("mousePressed", x, y, button="left", clickCount=n)
                self._mouse("mouseReleased", x, y, button="left", clickCount=n)
        elif verb == "fill":
            self._located(a["selector"], True, t)
            f = self.call(f"focus({_js(a['selector'])}, true)", timeout=t)
            if not f.get("editable"):
                raise CaptureError("элемент — не поле ввода (input, textarea, contenteditable)")
            if not f.get("focused"):
                raise CaptureError("поле не приняло фокус")
            self.page.insert_text(a["text"])
        elif verb == "press":
            if a.get("selector"):
                self._located(a["selector"], True, t)
                self.call(f"focus({_js(a['selector'])}, false)", timeout=t)
            try:
                events = key_event_params(a["key"])
            except ValueError as e:
                raise CaptureError(str(e))
            for ev in events:
                self.page.send("Input.dispatchKeyEvent", ev)
        elif verb == "select":
            self._located(a["selector"], True, t)
            r = self.call(f"selectOption({_js(a['selector'])}, {_js(a['value'])})", timeout=t)
            if not r.get("done"):
                raise CaptureError(str(r.get("reason") or "пункт не выбран"))
        elif verb == "waitFor":
            self._located(a["selector"], False, (a.get("ms") or t * 1000) / 1000.0)
        elif verb == "waitForGone":
            self._until_gone(a["selector"], (a.get("ms") or t * 1000) / 1000.0)
        elif verb == "wait":
            self.sleep(a["ms"] / 1000.0)
        elif verb == "scrollTo":
            r = self.call(f"scrollTo({_js(a['selector'])})", timeout=t)
            if r.get("bad"):
                raise CaptureError(f"селектор некорректен: {r.get('message')}")
            if not r.get("found"):
                raise CaptureError("на странице нет такого элемента")
        else:                                              # недостижимо: глагол проверен recipe.validate_actions
            raise CaptureError(f"глагол «{verb}» не поддержан")

    def _until_gone(self, selector: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            r = self.call(f"gone({_js(selector)})", timeout=CD.ACTION_TIMEOUT)
            if r.get("bad"):
                raise CaptureError(f"селектор «{selector}» некорректен: {r.get('message')}")
            if r.get("gone"):
                return
            if time.monotonic() >= deadline:
                raise CaptureError(f"элемент «{selector}» не исчез за {int(timeout * 1000)} мс")
            self.sleep(0.1)

    def try_wait(self, kind: str, selector: str, wait_ms: float) -> Optional[str]:
        """waitForSelector / waitForGone верхнего уровня: None — дождались, иначе причина (это ready:false, не исключение)."""
        try:
            if kind == "present":
                self._located(selector, False, wait_ms / 1000.0)
            else:
                self._until_gone(selector, wait_ms / 1000.0)
        except CaptureError as e:
            return str(e)
        return None


def _fonts_of(design: Optional[Dict[str, Any]]) -> List[str]:
    """Семейства шрифтов, которыми в макете набран текст (первое из стека), по порядку обхода, без повторов."""
    if not design:
        return []
    seen: List[str] = []
    for n in CN.iter_nodes(design["root"]):
        fam = (n.get("font") or {}).get("family")
        if n.get("text") and isinstance(fam, str) and fam and fam not in seen and n.get("occluded") is not True:
            seen.append(fam)
    return seen[:MAX_FONTS_CHECKED]


def _design_count(design: Optional[Dict[str, Any]]) -> Optional[int]:
    if not design:
        return None
    n, stack = 0, [design["root"]]
    while stack:
        node = stack.pop()
        if node.get("occluded") is True:
            continue
        n += 1
        stack.extend(k for k in (node.get("children") or []) if isinstance(k, dict))
    return n


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent


def iteration_dir_ok(paths: Dict[str, Path], iteration_dir: Path) -> bool:
    """Папка итерации — подпапка `verify/` или `baseline/` кадра (их охраняет state-write-guard: пишет только движок)."""
    real = iteration_dir.resolve()
    return any(_inside(real, paths[k].resolve()) for k in ("verify", "baseline"))


def _previous_fingerprint(path: Path) -> Optional[str]:
    try:
        cap = json.loads(path.read_text(encoding="utf-8")).get("capture")
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        return None
    fp = cap.get("fingerprint") if isinstance(cap, dict) else None
    return fp if isinstance(fp, str) else None


def run(root: Path, track: str, frame: Optional[str], url: str, iteration_dir: Path, wait_ms: Any,
        actions: Optional[List[Dict[str, Any]]] = None, clip_selector: Optional[str] = None,
        wait_for_selector: Optional[str] = None, wait_for_gone: Optional[str] = None,
        target: str = "implementation") -> Dict[str, Any]:
    root = Path(root).resolve()
    if target not in TARGETS:
        raise CaptureError(f"target: «{target}» — допустимы {', '.join(TARGETS)}")
    if isinstance(wait_ms, bool) or not isinstance(wait_ms, (int, float)) or not (MIN_WAIT_MS <= wait_ms <= MAX_WAIT_MS):
        raise CaptureError(f"waitMs: число миллисекунд {MIN_WAIT_MS}…{MAX_WAIT_MS} (холодный dev-сервер 60000+, обычно 25000, "
                           f"перепроверка известной страницы 5000)")
    wait_ms = int(wait_ms)
    recipe: Dict[str, Any] = {}
    for key, val in (("actions", actions), ("clipSelector", clip_selector), ("waitForSelector", wait_for_selector),
                     ("waitForGone", wait_for_gone)):
        if val is not None:
            recipe[key] = val
    problems = RC.validate_recipe(recipe, "рецепт")
    if problems:
        raise CaptureError("; ".join(problems[:6]))

    manifest = TR.load(root, track)
    if manifest is None:
        raise CaptureError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    try:
        paths = TR.frame_paths(root, manifest, frame)
    except TR.TrackError as e:
        raise CaptureError(str(e))
    if not iteration_dir_ok(paths, iteration_dir):
        raise CaptureError(f"iterationDir: папка итерации лежит под «{_rel(root, paths['verify'])}» (или «baseline») кадра, "
                           f"а не «{_rel(root, iteration_dir)}» — адрес берётся из daiver_verify_state")

    names = FILES[target]
    snap_path = iteration_dir / names["snapshot"]
    loaded = CFG.load(root)
    verify = loaded["config"]["verify"]
    vp = verify.get("viewport") or {}
    viewport = (int(vp.get("width", 1280)), int(vp.get("height", 900)))
    fp = fingerprint({"target": target, "url": url, "recipe": recipe, "waitMs": wait_ms, "viewport": list(viewport)})
    if _previous_fingerprint(snap_path) == fp:
        raise CaptureError(f"отказ: этот вызов уже выполнен в «{_rel(root, iteration_dir)}» с тем же waitMs={wait_ms}. «Подождать ещё» "
                           f"повтором не лечится — одна папка итерации не получает два вердикта. Подними waitMs либо назови "
                           f"загрузчик через waitForGone / waitForSelector")

    if target == "implementation" and _inside(iteration_dir.resolve(), paths["verify"].resolve()):
        drift = IMPL.pin_problems(IMPL.read_pin(paths["verifyState"]), recipe, {"width": viewport[0], "height": viewport[1]})
        if drift:
            raise CaptureError("захват отличается от закреплённого прошлыми итерациями (рецепт, clip и viewport одинаковы во ВСЕХ итерациях, INV-C23): "
                               + "; ".join(drift) + ". Верни прежние значения — иначе дефекты разных итераций несравнимы")
    design: Optional[Dict[str, Any]] = None
    notes: List[str] = []
    if paths["canonical"].is_file():
        try:
            design = CN.read_canonical(paths["canonical"])
        except CN.CanonError as e:
            notes.append(f"каноническая модель кадра не прочитана ({e}) — число элементов и шрифты макета не сверены")

    chrome, stop = CD.launch_or_skip(verify, root=root, viewport=viewport,
                                     file_roots=[iteration_dir] if target == "reference" else [])
    if stop is not None:
        return stop
    iteration_dir.mkdir(parents=True, exist_ok=True)
    with chrome:
        try:
            got = _capture(chrome, url, viewport, wait_ms, recipe, _fonts_of(design))
        except CD.UrlRefused as e:
            raise CaptureError(f"адрес не пропущен: {e}")
        except CD.BrowserError as e:
            raise CaptureError(f"браузер оборвал захват: {e}")
    snap = got["snapshot"]

    design_nodes = _design_count(design) if target == "implementation" else None
    elements = snap.get("elements", 0)
    sanity: Optional[Dict[str, Any]] = None
    warnings: List[str] = list(notes)
    if design_nodes:
        under = elements < design_nodes * UNDER_RENDER_RATIO
        sanity = {"elements": elements, "designNodes": design_nodes, "underRendered": under}
        if under:
            warnings.append(f"страница отрисовала {elements} элементов при {design_nodes} узлах макета — захват недорисован "
                            f"(что бы ни говорил ready): не сравнивай, найди причину")
    missing = [f for f, ok in got["fonts"].items() if not ok]
    for fam in missing:
        warnings.append(f"шрифт макета «{fam}» не найден в браузере и не загружен страницей — метрики текста будут расходиться")
    if snap.get("truncated", {}).get("nodes") or snap.get("truncated", {}).get("depth"):
        warnings.append("снимок усечён по числу узлов или глубине — сравнение неполное")
    if not snap.get("react", {}).get("found"):
        warnings.append("имён React-компонентов нет (прод-сборка, не React или только DOM-элементы): проверка идентичности "
                        "компонентов НЕ исполнялась — это не «исполнилась и чиста»")
    ready = got["ready"]
    capture_meta = {"target": target, "url": url, "viewport": {"width": viewport[0], "height": viewport[1]}, "waitMs": wait_ms,
                    "fingerprint": fp, "ready": ready, "readiness": got["readiness"], "recipe": recipe,
                    "clip": got["clip"], "png": got["pngSize"], "dialogs": got["dialogs"], "fonts": got["fonts"],
                    "warnings": warnings}
    snap["capture"] = capture_meta

    _atomic_write(iteration_dir / names["png"], got["png"])
    if names["html"]:
        _atomic_write(iteration_dir / names["html"], got["html"].encode("utf-8"))
    _atomic_write(snap_path, json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))    # последним: он же — метка «захват целиком»

    files = {k: _rel(root, iteration_dir / v) for k, v in names.items() if v}
    out: Dict[str, Any] = {
        "track": track, "frame": frame, "target": target, "url": url, "iterationDir": _rel(root, iteration_dir), "files": files,
        "ready": ready, "readiness": got["readiness"], "viewport": capture_meta["viewport"], "clip": got["clip"],
        "png": got["pngSize"], "elements": elements, "domElements": snap.get("domElements"),
        "react": snap.get("react"), "truncated": snap.get("truncated"), "fonts": got["fonts"],
        "dialogs": got["dialogs"], "warnings": warnings}
    if sanity:
        out["sanity"] = sanity
    if ready:
        out["nextStep"] = ("снимок готов: посмотри actual.png глазами и передай снимок на сравнение. Не делай второй такой же вызов в эту папку")
    else:
        out["nextStep"] = ("страница НЕ готова: НЕ сравнивай и НЕ репортуй дефекты этого снимка. Подними waitMs (повтор без изменений "
                           "будет отказан) либо назови загрузчик через waitForGone / ожидаемое через waitForSelector; "
                           "так и не поднялась — «страница никогда не догрузилась» и есть находка, остановись")
    return out


def _rel(root: Path, p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(p)


def _capture(chrome: Any, url: str, viewport: Tuple[int, int], wait_ms: int, recipe: Dict[str, Any],
             font_names: List[str]) -> Dict[str, Any]:
    page = chrome.new_page("about:blank", viewport=viewport)
    try:
        page.send("Page.addScriptToEvaluateOnNewDocument",
                  {"source": "window.__daiver = (" + script() + ")(); window.__daiver.install();"})
        try:
            page.navigate(url, wait="domcontentloaded", timeout=CD.NAV_TIMEOUT)     # не load: повисшая картинка не должна ронять захват —
            #                                                                         недогруженное ловит готовность (readyState, image-loading)
        except (CD.NavigationError, CD.CdpTimeout) as e:
            raise CaptureError(f"страница {url} не открылась: {e}. Запущен ли dev-сервер и верен ли адрес?")
        d = _Driver(page)
        d.ensure()
        readiness = d.wait_ready(wait_ms)
        ready = readiness["ready"]
        actions = recipe.get("actions") or []
        if actions and ready:                                   # на недогруженной странице рецепт не играем: он стал бы гаданием
            d.play(actions)
            second = d.wait_ready(max(RD.POLL_MS * 12, wait_ms - readiness["waitedMs"]))
            readiness = {"ready": second["ready"], "waitedMs": readiness["waitedMs"] + second["waitedMs"],
                         "polls": readiness["polls"] + second["polls"], "reasons": second["reasons"], "actionsPlayed": len(actions)}
            ready = readiness["ready"]
        elif actions:
            readiness["actionsPlayed"] = 0
            readiness["reasons"] = readiness["reasons"] + ["рецепт не проигран: страница не дождалась готовности"]
        if ready:
            for kind, key in (("present", "waitForSelector"), ("gone", "waitForGone")):
                if recipe.get(key):
                    why = d.try_wait(kind, recipe[key], wait_ms)
                    if why:
                        ready = False
                        readiness["ready"] = False
                        readiness["reasons"] = readiness["reasons"] + [f"{key}: {why}"]
        clip_sel = recipe.get("clipSelector")
        clip: Optional[Dict[str, Any]] = None
        if clip_sel:
            r = d.call(f"locate({_js(clip_sel)}, false)", timeout=CD.ACTION_TIMEOUT)
            if r.get("bad"):
                raise CaptureError(f"clipSelector «{clip_sel}» некорректен: {r.get('message')}")
            if not r.get("found"):
                raise CaptureError(f"clipSelector «{clip_sel}»: на странице такого элемента нет — область снимка не определить; "
                                   f"рецепт открытия выработан под другое состояние страницы")
            b = r["box"]
            x0, y0 = int(b["x"] // 1), int(b["y"] // 1)
            x1, y1 = int(-(-(b["x"] + b["w"]) // 1)), int(-(-(b["y"] + b["h"]) // 1))
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                raise CaptureError(f"clipSelector «{clip_sel}»: у элемента нулевой размер — область снимка пуста")
            clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
        snap_text = d.call("snapshot(" + _js({"rootSelector": clip_sel} if clip_sel else {}) + ")", timeout=CD.NAV_TIMEOUT)
        snap = json.loads(snap_text)
        if snap.get("rootMissing"):
            raise CaptureError(f"clipSelector «{clip_sel}» исчез между замерами")
        html = d.call("html()", timeout=CD.NAV_TIMEOUT)
        fonts = d.call(f"fonts({_js(font_names)})", timeout=6, await_promise=True) if font_names else {}
        png = _shot(page, clip)
        return {"snapshot": snap, "html": html, "png": png, "pngSize": png_size(png), "ready": ready, "readiness": readiness,
                "clip": clip, "fonts": fonts, "dialogs": list(page.dialogs)}
    finally:
        page.close()


def _shot(page: Any, clip: Optional[Dict[str, Any]]) -> bytes:
    if clip is None:
        return page.screenshot(timeout=CD.NAV_TIMEOUT)
    import base64
    res = page.send("Page.captureScreenshot",
                    {"format": "png", "captureBeyondViewport": True,
                     "clip": {"x": clip["x"], "y": clip["y"], "width": clip["width"], "height": clip["height"], "scale": 1}},
                    timeout=CD.NAV_TIMEOUT)
    return base64.b64decode(res["data"], validate=True)
