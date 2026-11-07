"""cdp.py — протокол Chrome DevTools поверх клиента WebSocket и запуск headless-браузера (спека §14.2, §20.4, INV-F11, INV-A8; задача 4.1).

Три слоя, снизу вверх:
  • `Connection` — JSON-RPC протокола: id/method/params, ответы и события, таймауты, сессии. Синхронный и однопоточный: `send` пишет
    запрос и читает сообщения, пока не придёт ответ с его id; события, пришедшие по дороге, ложатся в очередь и достаются
    `wait_event` (поэтому «событие пришло раньше ответа» не гонка). Ответ после таймаута отбрасывается, а не подсовывается
    следующему вызову; ответ с НЕИЗВЕСТНЫМ id, не-JSON, двоичное сообщение, сообщение, не похожее ни на ответ, ни на событие, —
    `CdpProtocolError` и соединение закрыто (это не Chrome, или поток испорчен: ждать полный таймаут незачем);
    обрыв — `CdpDisconnected`; авария или закрытие вкладки (`Inspector.targetCrashed`, `Target.detachedFromTarget`, …) — вызовы к
    этой сессии падают сразу `TargetCrashed`/`TargetClosed`, а не висят до таймаута.
  • `Page` — вкладка (target + сессия `flatten`) и тонкие обёртки доменов Page (navigate, captureScreenshot, диалоги), Runtime
    (evaluate), Emulation (viewport, media), Input (мышь, ввод текста), DOM (поиск и бокс). Всё остальное — `send(метод, параметры)`.
    Диалоги страницы (alert/confirm/prompt/beforeunload) закрываются сами в цикле чтения: иначе страница замирает, и ни один вызов не
    возвращается. Адрес страницы называет ТОЛЬКО вызывающий (`navigate(url)`; умолчаний нет) и проходит `check_url`: схемы http, https,
    data, about:blank и file — последняя лишь внутри названных каталогов (`file_roots`): страница `file:///etc/…` иначе станет
    скриншотом любого файла на диске.
  • `Chrome` — процесс: `Chrome.launch(путь)` запускает headless в новой группе процессов с временным профилем, ждёт
    `DevToolsActivePort`, подключается к 127.0.0.1 и закрывает скачивания (`Browser.setDownloadBehavior: deny` — страница не пишет
    на диск). `close()` просит браузер выйти, затем убивает ВСЮ группу и удаляет профиль; то же — при исключении во время запуска,
    на выходе из интерпретатора (`atexit`) и по SIGTERM процесса-хозяина (если обработчик не был чужим). Не покрыто: SIGKILL самого
    хозяина оставит браузер и профиль в `$TMPDIR/forgeui-chrome-*`.

Детерминизм запуска. Фиксированный список флагов (`launch_args`): `--headless=new`, порт 0 на 127.0.0.1, временный профиль,
`--force-device-scale-factor=1`, `--hide-scrollbars`, `--force-color-profile=srgb`, `--font-render-hinting=none`,
`--disable-lcd-text`, `--disable-gpu`, `--window-size` — размер задаёт вызывающий; без фоновой сети, обновлений, синхронизации,
связки ключей. Вкладке задаются viewport (`Emulation.setDeviceMetricsOverride`, масштаб 1) и media (`prefers-color-scheme: light`,
`prefers-reduced-motion: reduce`): тёмная тема системы и анимации не должны менять снимок. Чего нет: локаль и часовой пояс не
фиксируются (спека их не называет).

Не проверено данными: Windows и Linux (раскладка и `taskkill` — по документации), Chrome старше 109 (нет `--headless=new`). Проверено:
фейковым сервером (`test_cdp.py`) и настоящим Chrome 155 на macOS.
"""
from __future__ import annotations

import atexit
import base64
import collections
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urlsplit

from . import BrowserError
from . import find as BF
from .ws import WebSocket, WsClosed, WsError, WsProtocolError, WsTimeout
from .. import proc as P

# Константы времени: навигация и действие — из §20.4 спеки (nav timeout 30000, action timeout 5000); остальное — наши.
DEFAULT_TIMEOUT = 30.0
NAV_TIMEOUT = 30.0
ACTION_TIMEOUT = 5.0
LAUNCH_TIMEOUT = 30.0
CLOSE_GRACE = 3.0
DEFAULT_VIEWPORT = (1280, 900)
MAX_VIEWPORT = 16384
EVENT_QUEUE_MAX = 5000
PROFILE_PREFIX = "forgeui-chrome-"
LOG_NAME = "forgeui-stderr.log"
ALLOWED_SCHEMES = ("http", "https", "data", "about", "file")
WAIT_STATES = ("commit", "domcontentloaded", "load")
_LIFECYCLE = {"domcontentloaded": "DOMContentLoaded", "load": "load"}


class CdpError(BrowserError):
    """Сбой протокола DevTools."""


class CdpTimeout(CdpError):
    """Браузер не ответил за срок; соединение цело, запоздавший ответ будет отброшен."""


class CdpDisconnected(CdpError):
    """Соединение с браузером оборвано (браузер завершился, сокет закрыт)."""


class CdpProtocolError(CdpError):
    """Пир говорит не по протоколу DevTools (не JSON, двоичное, неизвестный id, не ответ и не событие)."""


class CdpCommandError(CdpError):
    """Браузер вернул ошибку на команду: `method`, `code`, `message`, `data`."""

    def __init__(self, method: str, code: Any, message: str, data: Any = None):
        super().__init__(f"{method}: {message} (код {code})")
        self.method, self.code, self.message, self.data = method, code, message, data


class TargetClosed(CdpError):
    """Вкладка закрыта или отсоединена."""


class TargetCrashed(TargetClosed):
    """Вкладка упала."""


class JsException(CdpError):
    """Исключение в странице при `Runtime.evaluate`."""


class NavigationError(CdpError):
    """Навигация не состоялась (`errorText` браузера: net::ERR_CONNECTION_REFUSED и т. п.)."""

    def __init__(self, url: str, text: str):
        super().__init__(f"{url}: {text}")
        self.url, self.text = url, text


class UrlRefused(BrowserError):
    """Адрес не из разрешённых (схема, каталог file:, управляющие символы)."""


class BrowserLaunchError(BrowserError):
    """Браузер не запустился или не отвечает после запуска."""


# ── адреса ────────────────────────────────────────────────────────────────────

def _file_path(parts: Any) -> Path:
    path = unquote(parts.path)
    if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":      # pragma: no cover — Windows
        path = path[1:]
    return Path(path)


def check_url(url: Any, *, file_roots: Sequence[Any] = ()) -> str:
    """Адрес, который можно открыть: http(s), data:, about:blank и file: ВНУТРИ `file_roots`. Остальное (javascript:, chrome:,
    blob:, view-source:, ftp: …), пустое и с управляющими символами — `UrlRefused`. Хост не ограничивается: страницы называет
    вызывающий (dev-сервер, стенд); откуда они, решает не клиент."""
    if not isinstance(url, str) or not url.strip():
        raise UrlRefused("адрес пуст")
    text = url.strip()
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        raise UrlRefused("в адресе управляющие символы")
    try:
        parts = urlsplit(text)
    except ValueError as e:
        raise UrlRefused(f"адрес не разобрать ({e})")
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlRefused(f"схема «{scheme or text[:12]}» не разрешена (можно http, https, data, about:blank и file: внутри "
                         f"названных каталогов)")
    if scheme == "about" and text.lower() != "about:blank":
        raise UrlRefused("из about: разрешён только about:blank")
    if scheme == "file":
        if parts.netloc not in ("", "localhost"):
            raise UrlRefused("file: с хостом не разрешён")
        roots = [Path(r).resolve() for r in file_roots]
        if not roots:
            raise UrlRefused("file: не разрешён: каталоги для file: не названы")
        target = _file_path(parts).resolve()
        if not any(target == r or r in target.parents for r in roots):
            raise UrlRefused(f"file: вне разрешённых каталогов ({', '.join(str(r) for r in roots)})")
    return text


# ── соединение ────────────────────────────────────────────────────────────────

class Connection:
    """JSON-RPC протокола DevTools поверх `WebSocket`. Синхронное, один владелец, без потоков."""

    def __init__(self, ws: WebSocket, *, default_timeout: float = DEFAULT_TIMEOUT):
        self.ws = ws
        self.default_timeout = default_timeout
        self.dropped_events = 0
        self.handler_errors: List[str] = []
        self._next_id = 0
        self._events: Deque[Dict[str, Any]] = collections.deque(maxlen=EVENT_QUEUE_MAX)
        self._ignored: Set[int] = set()                 # id, чей ответ никому не нужен: вышли по таймауту или без ожидания
        self._handlers: List[Tuple[str, Optional[str], Callable[[Dict[str, Any]], None]]] = []
        self._targets: Dict[str, str] = {}              # sessionId → targetId
        self._doomed: Dict[str, Tuple[type, str]] = {}  # sessionId → (исключение, текст): сессия больше не ответит
        self._failure: Optional[Tuple[type, str]] = None

    # — состояние —
    def _fail(self, cls: type, message: str) -> CdpError:
        self._failure = (cls, message)
        self.ws.close(timeout=0.2)
        return cls(message)

    def _check_alive(self, session_id: Optional[str] = None) -> None:
        if self._failure:
            raise self._failure[0](self._failure[1])
        if session_id and session_id in self._doomed:
            cls, message = self._doomed[session_id]
            raise cls(message)

    @property
    def alive(self) -> bool:
        return self._failure is None and not self.ws.closed

    # — отправка —
    def _post(self, method: str, params: Optional[Dict[str, Any]], session_id: Optional[str], timeout: float) -> int:
        self._check_alive(session_id)
        self._next_id += 1
        mid = self._next_id
        msg: Dict[str, Any] = {"id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        text = json.dumps(msg, ensure_ascii=True, separators=(",", ":"))
        try:
            self.ws.send_text(text, timeout=timeout)
        except (WsClosed, WsTimeout) as e:
            raise self._fail(CdpDisconnected, f"{method}: соединение с браузером оборвано при отправке ({e})")
        return mid

    def send(self, method: str, params: Optional[Dict[str, Any]] = None, *, session_id: Optional[str] = None,
             timeout: Optional[float] = None) -> Dict[str, Any]:
        """Команда и её `result`. Ошибка браузера — `CdpCommandError`; нет ответа за `timeout` секунд (на всё: отправку и ожидание) —
        `CdpTimeout`; обрыв — `CdpDisconnected`."""
        limit = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        mid = self._post(method, params, session_id, limit)
        reply: Optional[Dict[str, Any]] = None
        try:
            while reply is None:
                msg = self._read(deadline, f"{method}: ответа нет за {limit:g} с")
                reply = self._route(msg, mid)
                if reply is None:
                    self._check_alive(session_id)
        finally:
            if reply is None:                                # вышли без ответа: запоздавший ответ отбросим, а не примем за чужой
                self._ignored.add(mid)
        if "error" in reply:
            err = reply["error"]
            if not isinstance(err, dict):
                raise self._fail(CdpProtocolError, f"{method}: поле error — не объект")
            code, message = err.get("code"), str(err.get("message", ""))
            if code == -32001 or message.startswith("Session with given id not found"):
                raise TargetClosed(f"{method}: вкладка закрыта ({message})")
            raise CdpCommandError(method, code, message, err.get("data"))
        result = reply.get("result", {})
        if not isinstance(result, dict):
            raise self._fail(CdpProtocolError, f"{method}: result — не объект")
        return result

    def send_nowait(self, method: str, params: Optional[Dict[str, Any]] = None, *, session_id: Optional[str] = None) -> int:
        """Отправить, не дожидаясь ответа (его прочитают и выбросят). Для реакций внутри цикла чтения — закрытие диалога."""
        mid = self._post(method, params, session_id, self.default_timeout)
        self._ignored.add(mid)
        return mid

    # — чтение —
    def _read(self, deadline: float, what: str) -> Dict[str, Any]:
        try:
            raw = self.ws.recv(timeout=max(0.0, deadline - time.monotonic()))
        except WsTimeout:
            raise CdpTimeout(what)
        except WsClosed as e:
            raise self._fail(CdpDisconnected, f"соединение с браузером оборвано: {e}")
        except WsProtocolError as e:
            raise self._fail(CdpProtocolError, f"нарушение WebSocket: {e}")
        except WsError as e:
            raise self._fail(CdpDisconnected, f"сбой WebSocket: {e}")
        if isinstance(raw, bytes):
            raise self._fail(CdpProtocolError, "двоичное сообщение: протокол DevTools шлёт только текст")
        try:
            msg = json.loads(raw)
        except ValueError:
            raise self._fail(CdpProtocolError, f"сообщение — не JSON: {raw[:60]!r}")
        if not isinstance(msg, dict):
            raise self._fail(CdpProtocolError, "сообщение — не объект JSON")
        return msg

    def _route(self, msg: Dict[str, Any], awaiting: Optional[int]) -> Optional[Dict[str, Any]]:
        """Ответ, которого ждём, → он сам; событие → в обработчики и очередь; чужой ответ → отброшен или нарушение."""
        if "id" in msg:
            rid = msg["id"]
            if isinstance(rid, bool) or not isinstance(rid, int):
                raise self._fail(CdpProtocolError, f"id ответа — не целое: {rid!r}")
            if rid == awaiting:
                return msg
            if rid in self._ignored:
                self._ignored.discard(rid)
                return None
            raise self._fail(CdpProtocolError, f"ответ с неизвестным id {rid}" + (f" (ждали {awaiting})" if awaiting else ""))
        method = msg.get("method")
        if isinstance(method, str):
            params = msg.get("params", {})
            if not isinstance(params, dict):
                raise self._fail(CdpProtocolError, f"событие {method}: params — не объект")
            event: Dict[str, Any] = {"method": method, "params": params}
            if isinstance(msg.get("sessionId"), str):
                event["sessionId"] = msg["sessionId"]
            self._on_event(event)
            return None
        if "error" in msg:
            err = msg["error"]
            text = err.get("message") if isinstance(err, dict) else err
            raise self._fail(CdpProtocolError, f"браузер отклонил сообщение клиента: {text}")
        raise self._fail(CdpProtocolError, "сообщение не похоже ни на ответ, ни на событие")

    def _on_event(self, event: Dict[str, Any]) -> None:
        method, params, sid = event["method"], event["params"], event.get("sessionId")
        if method == "Inspector.targetCrashed" and sid:
            self._doomed[sid] = (TargetCrashed, "вкладка упала (Inspector.targetCrashed)")
        elif method == "Inspector.detached" and sid:
            self._doomed[sid] = (TargetClosed, f"вкладка отсоединена ({params.get('reason', '?')})")
        elif method == "Target.detachedFromTarget" and isinstance(params.get("sessionId"), str):
            self._doomed[params["sessionId"]] = (TargetClosed, "вкладка отсоединена (Target.detachedFromTarget)")
        elif method in ("Target.targetCrashed", "Target.targetDestroyed") and isinstance(params.get("targetId"), str):
            cls, why = (TargetCrashed, "вкладка упала") if method == "Target.targetCrashed" else (TargetClosed, "вкладка закрыта")
            for session, target in self._targets.items():
                if target == params["targetId"]:
                    self._doomed[session] = (cls, why)
        for name, want_sid, fn in list(self._handlers):
            if name == method and (want_sid is None or want_sid == sid):
                try:
                    fn(event)
                except Exception as e:  # noqa: BLE001 — сбой обработчика не должен ломать чтение
                    self.handler_errors = (self.handler_errors + [f"{method}: {type(e).__name__}: {e}"])[-20:]
        if len(self._events) == self._events.maxlen:
            self.dropped_events += 1
        self._events.append(event)

    # — события —
    def on(self, method: str, handler: Callable[[Dict[str, Any]], None], *, session_id: Optional[str] = None) -> None:
        """Обработчик события: вызывается внутри цикла чтения, до постановки в очередь. Из него можно `send_nowait`."""
        self._handlers.append((method, session_id, handler))

    def off(self, session_id: str) -> None:
        self._handlers = [h for h in self._handlers if h[1] != session_id]

    @staticmethod
    def _match(event: Dict[str, Any], method: Optional[str], session_id: Optional[str]) -> bool:
        return (method is None or event["method"] == method) and (session_id is None or event.get("sessionId") == session_id)

    def events(self, method: Optional[str] = None, *, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Копия очереди событий (старые первыми), отфильтрованная по методу и сессии. Очередь не меняется."""
        return [e for e in self._events if self._match(e, method, session_id)]

    def discard_events(self, method: Optional[str] = None, *, session_id: Optional[str] = None) -> int:
        """Выбросить накопленные события (устаревшие lifecycle перед новой навигацией). → сколько выброшено."""
        keep = [e for e in self._events if not self._match(e, method, session_id)]
        dropped = len(self._events) - len(keep)
        self._events.clear()
        self._events.extend(keep)
        return dropped

    def _take(self, method: str, session_id: Optional[str], predicate: Optional[Callable[[Dict[str, Any]], bool]]) -> Optional[Dict[str, Any]]:
        for event in self._events:
            if self._match(event, method, session_id) and (predicate is None or predicate(event["params"])):
                self._events.remove(event)
                return event
        return None

    def wait_event(self, method: str, *, session_id: Optional[str] = None,
                   predicate: Optional[Callable[[Dict[str, Any]], bool]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        """`params` первого события `method` (с подходящим `predicate(params)`) — из очереди или из потока. Нет за `timeout` —
        `CdpTimeout`. Событие, которое уже пришло, находится сразу."""
        limit = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            found = self._take(method, session_id, predicate)
            if found is not None:
                return found["params"]
            self._check_alive(session_id)
            msg = self._read(deadline, f"событие {method}: нет за {limit:g} с")
            self._route(msg, None)

    def pump(self, seconds: float) -> int:
        """Читать и раскладывать сообщения `seconds` секунд (ожидание «пока страница дорисуется»). → сколько прочитано."""
        deadline = time.monotonic() + max(0.0, seconds)
        count = 0
        while time.monotonic() < deadline:
            try:
                msg = self._read(deadline, "пауза")
            except CdpTimeout:
                break
            self._route(msg, None)
            count += 1
        return count

    # — сессии —
    def register_session(self, session_id: str, target_id: str) -> None:
        self._targets[session_id] = target_id

    def forget_session(self, session_id: str) -> None:
        self._targets.pop(session_id, None)
        self._doomed.pop(session_id, None)
        self.off(session_id)

    def close(self) -> None:
        self.ws.close(timeout=0.5)

    def abort(self) -> None:
        """Оборвать сокет без рукопожатия (уборка при смерти хозяина)."""
        self.ws.abort()


# ── вкладка ───────────────────────────────────────────────────────────────────

def _viewport(viewport: Any) -> Tuple[int, int]:
    try:
        w, h = viewport
    except (TypeError, ValueError):
        raise ValueError("viewport: нужна пара (ширина, высота)")
    for v in (w, h):
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_VIEWPORT:
            raise ValueError(f"viewport: целые от 1 до {MAX_VIEWPORT}, получено {viewport!r}")
    return w, h


class Page:
    """Вкладка (target + плоская сессия). Не создаётся напрямую — `Chrome.new_page`."""

    def __init__(self, conn: Connection, target_id: str, session_id: str, *, file_roots: Sequence[Any] = ()):
        self.conn, self.target_id, self.session_id = conn, target_id, session_id
        self.file_roots = tuple(file_roots)
        self.dialogs: List[Dict[str, str]] = []
        self.closed = False
        self._lifecycle = False
        conn.register_session(session_id, target_id)
        conn.on("Page.javascriptDialogOpening", self._on_dialog, session_id=session_id)

    def _on_dialog(self, event: Dict[str, Any]) -> None:
        """Диалог страницы блокирует её скрипты, и ни один вызов не вернётся: закрываем сразу. alert/confirm/prompt — отменой,
        beforeunload — согласием (иначе уйти со страницы нельзя). Что показывали — в `dialogs`."""
        params = event["params"]
        kind = str(params.get("type", ""))
        note = {"type": kind, "message": str(params.get("message", ""))[:200], "url": str(params.get("url", ""))[:200]}
        self.dialogs = (self.dialogs + [note])[-50:]
        self.conn.send_nowait("Page.handleJavaScriptDialog", {"accept": kind == "beforeunload"}, session_id=self.session_id)

    # — общее —
    def send(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        if self.closed:
            raise TargetClosed("вкладка закрыта")
        return self.conn.send(method, params, session_id=self.session_id, timeout=timeout)

    def wait_event(self, method: str, *, predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
                   timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.conn.wait_event(method, session_id=self.session_id, predicate=predicate, timeout=timeout)

    def events(self, method: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.conn.events(method, session_id=self.session_id)

    def clear_events(self, method: Optional[str] = None) -> int:
        return self.conn.discard_events(method, session_id=self.session_id)

    def enable(self, *domains: str) -> None:
        """`<Домен>.enable` для каждого (Page, Runtime, DOM, Network …): события домена начнут приходить в очередь."""
        for domain in domains:
            self.send(f"{domain}.enable")

    # — Emulation —
    def set_viewport(self, width: int, height: int, *, scale: float = 1.0) -> None:
        w, h = _viewport((width, height))
        self.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": scale, "mobile": False})

    def emulate_media(self, *, color_scheme: str = "light", reduced_motion: bool = True) -> None:
        """Тема и анимации не зависят от системы: светлая схема, `prefers-reduced-motion: reduce`."""
        features = [{"name": "prefers-color-scheme", "value": color_scheme},
                    {"name": "prefers-reduced-motion", "value": "reduce" if reduced_motion else "no-preference"}]
        self.send("Emulation.setEmulatedMedia", {"media": "", "features": features})

    # — Page —
    def navigate(self, url: str, *, wait: str = "load", timeout: float = NAV_TIMEOUT) -> Dict[str, Any]:
        """Открыть адрес (`check_url`) и дождаться `wait`: commit | domcontentloaded | load. Нет сети, DNS, отказ — `NavigationError`;
        события загрузки нет за `timeout` — `CdpTimeout`."""
        if wait not in WAIT_STATES:
            raise ValueError(f"wait: одно из {', '.join(WAIT_STATES)}")
        check_url(url, file_roots=self.file_roots)
        deadline = time.monotonic() + timeout
        if not self._lifecycle:
            self.send("Page.enable")
            self.send("Page.setLifecycleEventsEnabled", {"enabled": True})
            self._lifecycle = True
        self.clear_events("Page.lifecycleEvent")             # события прошлой страницы не должны сойти за события этой
        res = self.send("Page.navigate", {"url": url}, timeout=max(0.001, deadline - time.monotonic()))
        if res.get("errorText"):
            raise NavigationError(url, str(res["errorText"]))
        loader = res.get("loaderId")
        if wait == "commit" or not loader:                   # без loaderId навигация внутри документа: события load не будет
            return res
        want = _LIFECYCLE[wait]
        try:
            self.wait_event("Page.lifecycleEvent", timeout=max(0.001, deadline - time.monotonic()),
                            predicate=lambda p: p.get("loaderId") == loader and p.get("name") == want)
        except CdpTimeout:
            raise CdpTimeout(f"{url}: событие {want} не пришло за {timeout:g} с")
        return res

    def screenshot(self, *, clip: Optional[Dict[str, float]] = None, timeout: Optional[float] = None) -> bytes:
        """PNG видимой области (viewport) или `clip` {x, y, width, height} в CSS-пикселях."""
        params: Dict[str, Any] = {"format": "png"}
        if clip is not None:
            params["clip"] = {"x": clip["x"], "y": clip["y"], "width": clip["width"], "height": clip["height"], "scale": 1}
        res = self.send("Page.captureScreenshot", params, timeout=timeout)
        data = res.get("data")
        if not isinstance(data, str):
            raise CdpProtocolError("Page.captureScreenshot: в ответе нет data")
        try:
            return base64.b64decode(data, validate=True)
        except ValueError:
            raise CdpProtocolError("Page.captureScreenshot: data — не base64")

    # — Runtime —
    def evaluate(self, expression: str, *, await_promise: bool = False, timeout: Optional[float] = None) -> Any:
        """Значение выражения в странице (по значению). Исключение страницы — `JsException`. Скрипт, застрявший в цикле, обрывает
        сам браузер чуть раньше таймаута вызова."""
        limit = self.conn.default_timeout if timeout is None else timeout
        res = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": await_promise,
                                            "timeout": max(1, int((limit - 0.5) * 1000))}, timeout=limit)
        if "exceptionDetails" in res:
            det = res["exceptionDetails"] if isinstance(res["exceptionDetails"], dict) else {}
            exc = det.get("exception") if isinstance(det.get("exception"), dict) else {}
            text = str(exc.get("description") or det.get("text") or "исключение в странице").split("\n")[0]
            raise JsException(f"{text} (строка {det.get('lineNumber', '?')})")
        out = res.get("result")
        if not isinstance(out, dict):
            raise CdpProtocolError("Runtime.evaluate: в ответе нет result")
        if "value" in out:
            return out["value"]
        if "unserializableValue" in out:
            return out["unserializableValue"]                  # NaN, Infinity, -0, 10n: строкой, как их называет браузер
        return None

    # — DOM и Input —
    def query_selector(self, selector: str) -> Optional[int]:
        """nodeId первого элемента по CSS-селектору; None — такого нет."""
        root = self.send("DOM.getDocument", {"depth": 0}).get("root")
        if not isinstance(root, dict) or not isinstance(root.get("nodeId"), int):
            raise CdpProtocolError("DOM.getDocument: в ответе нет root.nodeId")
        node = self.send("DOM.querySelector", {"nodeId": root["nodeId"], "selector": selector}).get("nodeId", 0)
        return node if isinstance(node, int) and node > 0 else None

    def element_box(self, selector: str) -> Optional[Dict[str, float]]:
        """Бокс элемента по границе (border box: с рамкой и отступами) {x, y, width, height} в CSS-пикселях viewport; None — элемента нет."""
        node = self.query_selector(selector)
        if node is None:
            return None
        quad = (self.send("DOM.getBoxModel", {"nodeId": node}).get("model") or {}).get("border")
        if not isinstance(quad, list) or len(quad) != 8:
            raise CdpProtocolError("DOM.getBoxModel: в ответе нет border из 8 чисел")
        xs, ys = quad[0::2], quad[1::2]
        return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}

    def click(self, x: float, y: float) -> None:
        """Клик левой кнопкой в точке viewport (наведение, нажатие, отпускание)."""
        base = {"x": x, "y": y}
        self.send("Input.dispatchMouseEvent", dict(base, type="mouseMoved"))
        self.send("Input.dispatchMouseEvent", dict(base, type="mousePressed", button="left", clickCount=1))
        self.send("Input.dispatchMouseEvent", dict(base, type="mouseReleased", button="left", clickCount=1))

    def insert_text(self, text: str) -> None:
        """Вставить текст в сфокусированное поле (как ввод, без клавиш)."""
        self.send("Input.insertText", {"text": text})

    def close(self) -> None:
        """Закрыть вкладку; сбои закрытия не мешают (браузер мог уже уйти)."""
        if self.closed:
            return
        self.closed = True
        try:
            self.conn.send("Target.closeTarget", {"targetId": self.target_id}, timeout=ACTION_TIMEOUT)
        except BrowserError:
            pass
        self.conn.forget_session(self.session_id)


# ── процесс ───────────────────────────────────────────────────────────────────

# Фиксированный порядок и состав: один и тот же набор флагов на каждом запуске (детерминизм), а не «что придёт в голову сегодня».
BASE_ARGS = (
    "--headless=new",
    "--remote-debugging-address=127.0.0.1",          # Chrome может его игнорировать — тогда порт и так на loopback; проверяется тестом
    "--remote-debugging-port=0",
    "--no-first-run",
    "--no-default-browser-check",
    "--hide-scrollbars",
    "--mute-audio",
    "--force-device-scale-factor=1",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-hang-monitor",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling",
    "--disable-ipc-flooding-protection",
    "--disable-dev-shm-usage",
    "--disable-features=Translate,MediaRouter",
    "--metrics-recording-only",
    "--password-store=basic",
    "--use-mock-keychain",
)


def launch_args(profile: Any, width: int, height: int, extra: Sequence[str] = ()) -> List[str]:
    """Аргументы браузера (без пути к нему): флаги, временный профиль, размер окна, `extra`, пустая страница."""
    return [*BASE_ARGS, f"--user-data-dir={profile}", f"--window-size={width},{height}", *extra, "about:blank"]


def _tail(path: Path, limit: int = 1500) -> str:
    try:
        with open(str(path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit))
            return fh.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _remove_tree(path: Path, attempts: int = 10, pause: float = 0.1) -> bool:
    """Удалить каталог с повторами: только что убитый браузер ещё может дописывать файлы (и держать их на Windows)."""
    for _ in range(attempts):
        shutil.rmtree(str(path), ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(pause)
    return not path.exists()


def _wait_devtools_port(profile: Path, proc: Any, log: Path, timeout: float) -> Tuple[int, str]:
    """(порт, путь браузерного WebSocket) из `DevToolsActivePort` профиля; браузер умер или файла нет за срок — `BrowserLaunchError`."""
    marker = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while True:
        code = proc.poll()
        if code is not None:
            hint = " (запуск от root: Chrome без песочницы не стартует — запускайте не от root)" if (
                os.name == "posix" and os.geteuid() == 0) else ""
            raise BrowserLaunchError(f"браузер завершился сразу (код {code}){hint}: {_tail(log) or 'вывода нет'}")
        try:
            lines = marker.read_text(encoding="utf-8").split("\n")
        except (OSError, UnicodeDecodeError):
            lines = []
        if len(lines) >= 2 and lines[0].strip().isdigit() and lines[1].strip().startswith("/devtools/browser/"):
            port = int(lines[0])
            if 1 <= port <= 65535:
                return port, lines[1].strip()
        if time.monotonic() >= deadline:
            raise BrowserLaunchError(f"браузер не открыл порт отладки за {timeout:g} с (файла DevToolsActivePort нет): "
                                     f"{_tail(log) or 'вывода нет'}")
        time.sleep(0.05)


_LIVE: "Set[Chrome]" = set()                 # сильные ссылки: забытый без close браузер всё равно будет убран при выходе
_hooks = {"atexit": False, "sigterm": False}


def _close_all(graceful: bool = True) -> None:
    for chrome in list(_LIVE):
        try:
            chrome.close(graceful=graceful)
        except Exception:  # noqa: BLE001 — уборка не должна падать на одной вкладке
            pass


def _on_sigterm(signum: int, frame: Any) -> None:
    _close_all(graceful=False)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def _install_exit_hooks() -> None:
    """Уборка при выходе интерпретатора (`atexit`) и по SIGTERM хозяина — если там стоит обработчик по умолчанию (чужой не трогаем)."""
    if not _hooks["atexit"]:
        _hooks["atexit"] = True
        atexit.register(_close_all)
    if not _hooks["sigterm"] and os.name == "posix" and threading.current_thread() is threading.main_thread():
        try:
            if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
                signal.signal(signal.SIGTERM, _on_sigterm)
                _hooks["sigterm"] = True
        except (ValueError, OSError):
            pass


class Chrome:
    """Запущенный headless-браузер: процесс в своей группе, временный профиль, соединение DevTools. Создаётся `Chrome.launch`."""

    def __init__(self, proc: Any, profile: Path, log: Path, conn: Connection, *, port: int, info: Dict[str, Any],
                 viewport: Tuple[int, int], file_roots: Sequence[Any], warnings: List[str]):
        self.proc, self.profile, self.log, self.conn, self.port = proc, profile, log, conn, port
        self.product = str(info.get("product", ""))
        self.version = self.product.split("/", 1)[1] if "/" in self.product else None
        self.user_agent = str(info.get("userAgent", ""))
        self.viewport = viewport
        self.file_roots = tuple(file_roots)
        self.warnings = warnings
        self.report: Dict[str, Any] = {}
        self._closing = False
        self._closed = False

    @classmethod
    def launch(cls, executable: Any, *, viewport: Sequence[int] = DEFAULT_VIEWPORT, file_roots: Sequence[Any] = (),
               extra_args: Sequence[str] = (), launch_timeout: float = LAUNCH_TIMEOUT, call_timeout: float = DEFAULT_TIMEOUT,
               temp_root: Any = None, env: Optional[Dict[str, str]] = None) -> "Chrome":
        """Запустить браузер `executable` (путь — от `find.find_browser`; свой выбор вызывающий не делает). Профиль — новый
        временный каталог (0700, удаляется в `close`), порт отладки — на 127.0.0.1, выбирает ОС. `extra_args` — после стандартных
        флагов (например `--no-sandbox` в контейнере под root — решение вызывающего). Любой сбой запуска убирает за собой."""
        exe = Path(str(executable))
        width, height = _viewport(viewport)
        if not exe.is_file() or not os.access(str(exe), os.X_OK):
            raise BrowserLaunchError(f"{exe}: исполняемого файла браузера нет")
        profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX, dir=None if temp_root is None else str(temp_root)))
        log = profile / LOG_NAME
        proc: Any = None
        ws: Optional[WebSocket] = None
        conn: Optional[Connection] = None
        try:
            _install_exit_hooks()
            try:
                with open(str(log), "wb") as out:
                    proc = P.popen_group([str(exe)] + launch_args(profile, width, height, extra_args), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=out, env=env)
            except OSError as e:
                raise BrowserLaunchError(f"{exe}: не запустился ({e.strerror or e})")
            port, path = _wait_devtools_port(profile, proc, log, launch_timeout)
            warnings: List[str] = []
            try:
                ws = WebSocket.connect(f"ws://127.0.0.1:{port}{path}", timeout=min(10.0, launch_timeout))
                conn = Connection(ws, default_timeout=call_timeout)
                info = conn.send("Browser.getVersion", timeout=10.0)
                try:
                    conn.send("Browser.setDownloadBehavior", {"behavior": "deny"}, timeout=10.0)
                except CdpCommandError as e:
                    warnings.append(f"скачивания страницей не отключены: {e.message}")
            except (WsError, CdpError) as e:
                raise BrowserLaunchError(f"браузер запущен, но не отвечает по DevTools: {e}; вывод: {_tail(log) or 'нет'}")
            chrome = cls(proc, profile, log, conn, port=port, info=info, viewport=(width, height), file_roots=file_roots,
                         warnings=warnings)
        except BaseException:
            if ws is not None:
                ws.close(timeout=0.2)
            if proc is not None:
                P.kill_group(proc)
            _remove_tree(profile)
            raise
        _LIVE.add(chrome)
        return chrome

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None and self.conn.alive

    def stderr_tail(self, limit: int = 1500) -> str:
        """Хвост stderr браузера (пока профиль не удалён): чем объяснить внезапную смерть."""
        return _tail(self.log, limit)

    def new_page(self, url: str = "about:blank", *, viewport: Optional[Sequence[int]] = None, color_scheme: str = "light",
                 reduced_motion: bool = True, nav_timeout: float = NAV_TIMEOUT) -> Page:
        """Новая вкладка с заданным viewport и media; `url` (если не about:blank) открывается и ждётся до `load`. Адрес проходит
        `check_url` ДО создания вкладки."""
        check_url(url, file_roots=self.file_roots)
        width, height = _viewport(viewport if viewport is not None else self.viewport)
        created = self.conn.send("Target.createTarget", {"url": "about:blank"})
        target_id = created.get("targetId")
        if not isinstance(target_id, str):
            raise CdpProtocolError("Target.createTarget: в ответе нет targetId")
        attached = self.conn.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attached.get("sessionId")
        if not isinstance(session_id, str):
            raise CdpProtocolError("Target.attachToTarget: в ответе нет sessionId")
        page = Page(self.conn, target_id, session_id, file_roots=self.file_roots)
        try:
            page.enable("Page")
            page.set_viewport(width, height)
            page.emulate_media(color_scheme=color_scheme, reduced_motion=reduced_motion)
            if url.strip().lower() != "about:blank":
                page.navigate(url, timeout=nav_timeout)
        except BaseException:
            page.close()
            raise
        return page

    def close(self, *, graceful: bool = True) -> Dict[str, Any]:
        """Закрыть браузер: попросить выйти (`Browser.close`), затем убить ВСЮ группу процессов и удалить профиль. Идемпотентно, не
        бросает. → {graceful: вышел сам, killed: пришлось слать сигнал, profileRemoved}."""
        if self._closed:                                     # уже закрыт: номер группы мог достаться чужому процессу — не трогаем
            return self.report
        if self._closing:                                    # повторный вход (сигнал посреди close): добить, что можно
            P.kill_group(self.proc, term_grace=0.2, kill_wait=1.0)
            _remove_tree(self.profile, attempts=3)
            return self.report
        self._closing = True
        self.report = {"graceful": False, "killed": False, "profileRemoved": False}
        try:
            if graceful and self.conn.alive and self.proc.poll() is None:
                try:
                    self.conn.send("Browser.close", timeout=CLOSE_GRACE)
                except BrowserError:
                    pass                                     # браузер вправе закрыть сокет, не ответив
            if graceful:
                self.conn.close()
                end = time.monotonic() + CLOSE_GRACE
                while self.proc.poll() is None and time.monotonic() < end:
                    time.sleep(0.02)
            else:
                self.conn.abort()
            self.report["graceful"] = self.proc.poll() is not None
        finally:
            self.report["killed"] = P.kill_group(self.proc, term_grace=0.5 if not graceful else 2.0)
            self.report["profileRemoved"] = _remove_tree(self.profile)
            _LIVE.discard(self)
            self._closed = True
        return self.report

    def __enter__(self) -> "Chrome":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def launch_or_skip(config: Optional[Dict[str, Any]] = None, *, root: Any = None,
                   **launch_kw: Any) -> Tuple[Optional[Chrome], Optional[Dict[str, Any]]]:
    """Найти и запустить браузер: (Chrome, None) | (None, ответ «skipped»). `config` — блок `verify` эффективного конфига (там
    browserPath / browserChannel), `root` — корень репо; остальное (viewport, file_roots, …) уходит в `Chrome.launch`. Нет браузера или он не запустился — ответ
    `find.blocked_answer` (blocked: no-browser, reason, supply), а не исключение: цикл проверки не стартует, человеку объяснено. Ничего
    не скачивается и не ставится."""
    found = BF.find_browser(config, root=root)
    if not found["found"]:
        return None, BF.blocked_answer(found["reason"])
    try:
        return Chrome.launch(found["path"], **launch_kw), None
    except BrowserLaunchError as e:
        return None, BF.blocked_answer(f"{found['path']} найден, но не запустился: {e}")
