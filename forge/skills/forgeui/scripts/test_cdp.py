#!/usr/bin/env python3
"""test_cdp.py — браузер для проверки страницы: поиск Chromium, клиент WebSocket, протокол DevTools, запуск (задача 4.1; спека §14.2,
§20.5; INV-F11, A8; QG N8).

Главное — тесты БЕЗ браузера. Фейковый сервер WebSocket на stdlib живёт в этом файле, а его кодировщик кадров написан заново по RFC
6455 и не использует `daiver.browser.ws` (иначе тест доказывал бы только согласие кода с самим собой); кроме того кодек сверен с
байтовыми примерами RFC 6455 §5.7 и примером `Sec-WebSocket-Accept` из §1.3. Пинит:
  • RfcVectors   — кодек: примеры RFC, формы длины 7/16/64, маска (по байту и на мегабайтах), инкрементальный разбор, все виды
                   нарушений (RSV, opcode, маска сервера, управляющий кадр, старший бит длины, размер по заголовку);
  • Handshake    — вид запроса (нет Origin, ключ 16 байт, Host), отказ на любом неверном ответе, ответ по байтам, кадр в одном
                   сегменте с ответом, только loopback и только ws://;
  • Messages     — маска клиента, фрагментация в обе стороны, ping/pong, close с обеих сторон, нарушения (код закрытия 1002/1007/1009
                   уходит серверу), таймаут посреди кадра не теряет данные, EOF, большие нагрузки (3 МБ);
  • CdpClient    — id и ответы, ошибки, события раньше/позже ответа, таймаут и запоздавший ответ, неверные ответы и мусор, обрыв,
                   падение/закрытие вкладки, обработчики, очередь событий;
  • PageApi      — new_page, check_url (схемы, file: только внутри каталогов, символьная ссылка наружу), navigate (loaderId, ошибки),
                   evaluate, screenshot, DOM/Input, диалоги закрываются сами;
  • FindBrowser  — порядок env > config > канал > стандартные места > PATH на фейковых исполняемых, битое не заменяется, раскладки
                   платформ, версии, Edge не по умолчанию;
  • Capabilities — форма ответа `daiver_capabilities`, node/typescript/playwright/vision, останов `no-browser`, инструмент и MCP;
  • NoDownloads  — N8: браузера нет → останов с объяснением, не запущено и не открыто ничего; статически: нет сетевых библиотек и
                   командных строк кроме `taskkill`;
  • LaunchFake   — запуск и уборка на фейковом исполняемом файле (процесс в группе, флаги, профиль, порт из DevToolsActivePort,
                   сбои запуска, kill потомков, обрыв, atexit и SIGTERM хозяина);
  • CheckUrl / BrowserVersion / StopAnswer / ProcGroup / HostExit / RealChromeLifecycle — адреса, версия браузера без запуска, готовый
                   останов, группы процессов (kill_group), уборка при смерти хозяина (atexit, SIGTERM), жизненный цикл настоящего Chrome;
  • RealChrome   — настоящий Chrome, если найден (иначе skip с причиной): запуск, data:-страница, evaluate, viewport, media, снимок,
                   DOM+Input, диалог, ошибка навигации, порт только на loopback, внешний kill, отсутствие хвостов процессов.

Данные. Протокол — RFC 6455 и документация DevTools; настоящий Chrome 155 (macOS) — только в RealChrome. Что НЕ проверено: Windows
и Linux на настоящих машинах (раскладки по документации браузеров), Chrome старше 109, wss://, браузер под root в контейнере
(--no-sandbox), SIGKILL самого хозяина (браузер и профиль остаются — документировано в cdp.py).
Переменные окружения: FORGEUI_TEST_NO_BROWSER=1 — не гонять RealChrome; FORGEUI_TEST_REQUIRE_BROWSER=1 — отсутствие или сбой Chrome
в RealChrome считать провалом, а не skip.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _testkit as K  # noqa: E402
from daiver import capabilities as CA  # noqa: E402
from daiver import config as CFG  # noqa: E402
from daiver import proc as P  # noqa: E402
from daiver.browser import BrowserError  # noqa: E402
from daiver.browser import cdp as C  # noqa: E402
from daiver.browser import find as BF  # noqa: E402
from daiver.browser import ws as W  # noqa: E402

POSIX = os.name == "posix"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 8, 9, 10


# ══ независимая серверная сторона WebSocket (RFC 6455 по тексту, без daiver.browser.ws) ═══════════════════════════════════════════

def xor_mask(data: bytes, key: bytes) -> bytes:
    """RFC 6455 §5.3 буквально: j = i MOD 4, octet_i XOR key_j."""
    return bytes(b ^ key[i % 4] for i, b in enumerate(data))


def srv_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def srv_frame(opcode: int, payload: bytes = b"", *, fin: bool = True, rsv: int = 0, mask_key: Optional[bytes] = None,
              form: Optional[str] = None) -> bytes:
    """Кадр сервера (RFC 6455 §5.2). form: '7' | '16' | '64' — принудительная форма длины (по умолчанию минимальная)."""
    n = len(payload)
    first = (0x80 if fin else 0) | (rsv << 4) | opcode
    flag = 0x80 if mask_key is not None else 0
    form = form or ("7" if n < 126 else "16" if n < 65536 else "64")
    if form == "7":
        head = bytes([first, flag | n])
    elif form == "16":
        head = bytes([first, flag | 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([first, flag | 127]) + n.to_bytes(8, "big")
    return head + (payload if mask_key is None else mask_key + xor_mask(payload, mask_key))


class SrvFrame(NamedTuple):
    fin: bool
    rsv: int
    opcode: int
    masked: bool
    key: Optional[bytes]
    payload: bytes
    form: str


class ServerConn:
    """Серверная сторона одного соединения: читает кадры клиента, шлёт свои."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        sock.settimeout(10)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.buf = bytearray()
        self.request_line = ""
        self.headers: Dict[str, str] = {}
        self.raw_request = b""
        self.pongs: List[bytes] = []
        self.closed_by_client: Optional[int] = None

    # — рукопожатие —
    def read_request(self) -> None:
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("клиент закрыл соединение до запроса")
            self.buf += chunk
        end = self.buf.find(b"\r\n\r\n")
        self.raw_request = bytes(self.buf[:end])
        del self.buf[:end + 4]
        lines = self.raw_request.decode("latin-1").split("\r\n")
        self.request_line = lines[0]
        self.headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            self.headers[name.strip().lower()] = value.strip()

    def handshake(self, *, status: str = "101 Switching Protocols", accept: Optional[str] = None,
                  headers: Tuple[Tuple[str, str], ...] = (), drop: Tuple[str, ...] = (), pieces: int = 0,
                  tail: bytes = b"", raw: Optional[bytes] = None) -> None:
        key = self.headers.get("sec-websocket-key", "")
        base = [("Upgrade", "websocket"), ("Connection", "Upgrade"), ("Sec-WebSocket-Accept", accept or srv_accept(key))]
        lines = [(k, v) for k, v in base if k not in drop] + list(headers)
        blob = raw if raw is not None else (f"HTTP/1.1 {status}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in lines) + "\r\n").encode() + tail
        if pieces:
            for i in range(0, len(blob), pieces):
                self.sock.sendall(blob[i:i + pieces])
                time.sleep(0.002)
        else:
            self.sock.sendall(blob)

    # — кадры —
    def _need(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("клиент закрыл соединение")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_frame(self) -> SrvFrame:
        b0, b1 = self._need(2)
        length, form = b1 & 0x7F, "7"
        if length == 126:
            length, form = int.from_bytes(self._need(2), "big"), "16"
        elif length == 127:
            length, form = int.from_bytes(self._need(8), "big"), "64"
        masked = bool(b1 & 0x80)
        key = self._need(4) if masked else None
        payload = self._need(length)
        return SrvFrame(bool(b0 & 0x80), (b0 >> 4) & 7, b0 & 0xF, masked, key, xor_mask(payload, key) if key else payload, form)

    def next_message(self) -> Optional[Tuple[str, Any]]:
        """('text', str) | ('binary', bytes) | ('close', код) — собранное сообщение клиента; None — клиент закрыл сокет.
        Pong'и записываются, close подтверждается."""
        parts: List[bytes] = []
        first: Optional[int] = None
        while True:
            try:
                fr = self.read_frame()
            except (EOFError, ConnectionError, socket.timeout, OSError):
                return None
            if fr.opcode == OP_PONG:
                self.pongs.append(fr.payload)
                continue
            if fr.opcode == OP_PING:
                self.send(srv_frame(OP_PONG, fr.payload))
                continue
            if fr.opcode == OP_CLOSE:
                code = struct.unpack("!H", fr.payload[:2])[0] if len(fr.payload) >= 2 else None
                self.closed_by_client = code
                try:
                    self.send(srv_frame(OP_CLOSE, fr.payload))
                except OSError:
                    pass
                return ("close", code)
            if fr.opcode in (OP_TEXT, OP_BIN):
                first = fr.opcode
            parts.append(fr.payload)
            if fr.fin:
                data = b"".join(parts)
                return ("text", data.decode("utf-8")) if first == OP_TEXT else ("binary", data)

    # — отправка —
    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def send_text(self, text: str, **kw: Any) -> None:
        self.send(srv_frame(OP_TEXT, text.encode("utf-8"), **kw))

    def send_json(self, obj: Any) -> None:
        self.send_text(json.dumps(obj))

    def close_socket(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class FakeServer:
    """Сервер на 127.0.0.1:порт ОС; на каждое соединение — `handler(ServerConn)` в своём потоке. Исключения обработчика копятся в
    `errors` (тест проверяет `check()`: серверные assert не теряются в потоке)."""

    def __init__(self, handler: Callable[[ServerConn], None]):
        self.handler = handler
        self.errors: List[BaseException] = []
        self.conns: List[ServerConn] = []
        self._stop = False
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn = ServerConn(sock)
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: ServerConn) -> None:
        try:
            self.handler(conn)
        except (EOFError, ConnectionError, OSError):
            pass                                # клиент ушёл — для сервера это нормальный конец
        except BaseException as e:  # noqa: BLE001
            self.errors.append(e)
        finally:
            conn.close_socket()

    def url(self, path: str = "/devtools/browser/test") -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    def check(self) -> None:
        if self.errors:
            raise AssertionError(f"ошибка в фейковом сервере: {self.errors[0]!r}") from self.errors[0]

    def close(self) -> None:
        self._stop = True
        try:
            self.listener.close()
        except OSError:
            pass
        for c in self.conns:
            c.close_socket()


class Fail(Exception):
    """Ответить на команду ошибкой DevTools."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Hangup(Exception):
    """Закрыть соединение без ответа."""


NO_REPLY = object()


class FakeCdp:
    """Фальшивый браузер: по таблице `handlers` (метод → функция(req, cdp)) отвечает на команды DevTools и ведёт журнал `log`."""

    def __init__(self, conn: ServerConn, handlers: Optional[Dict[str, Callable[[Dict[str, Any], "FakeCdp"], Any]]] = None):
        self.conn = conn
        self.log: List[Dict[str, Any]] = []
        self.raw: List[str] = []                     # тексты запросов как пришли по проводу
        self.handlers = dict(DEFAULT_HANDLERS)
        self.handlers.update(handlers or {})
        self.state: Dict[str, Any] = {"targets": 0, "loaders": 0}
        self.after: List[Callable[[], None]] = []
        self.expect_path: Optional[str] = None          # если задан — на любой другой путь рукопожатия ответ 404, как у Chrome

    def emit(self, method: str, params: Optional[Dict[str, Any]] = None, session: Optional[str] = None) -> None:
        msg: Dict[str, Any] = {"method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.conn.send_json(msg)

    def reply(self, req: Dict[str, Any], result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"id": req["id"]}
        msg["error" if error else "result"] = error if error else (result if result is not None else {})
        if "sessionId" in req:
            msg["sessionId"] = req["sessionId"]
        self.conn.send_json(msg)

    def next_request(self) -> Optional[Dict[str, Any]]:
        got = self.conn.next_message()
        if got is None or got[0] != "text":
            return None
        self.raw.append(got[1])
        req = json.loads(got[1])
        self.log.append(req)
        return req

    def methods(self) -> List[str]:
        return [r["method"] for r in self.log]

    def run(self, handshake: bool = True) -> None:
        if handshake:
            self.conn.read_request()
            if self.expect_path and self.conn.request_line.split(" ")[1] != self.expect_path:
                self.conn.handshake(status="404 Not Found")
                return
            self.conn.handshake()
        while True:
            req = self.next_request()
            if req is None:
                return
            fn = self.handlers.get(req["method"])
            try:
                result = fn(req, self) if fn else {}
            except Fail as f:
                err = {"code": f.code, "message": f.message}
                if f.data is not None:
                    err["data"] = f.data
                self.reply(req, error=err)
                continue
            except Hangup:
                return
            if result is not NO_REPLY:
                self.reply(req, result)
            todo, self.after = self.after, []
            for fn_after in todo:
                fn_after()


def _h_create_target(req: Dict[str, Any], cdp: FakeCdp) -> Dict[str, Any]:
    cdp.state["targets"] += 1
    return {"targetId": f"T{cdp.state['targets']}"}


def _h_attach(req: Dict[str, Any], cdp: FakeCdp) -> Dict[str, Any]:
    return {"sessionId": f"S{req['params']['targetId'][1:]}"}


def _h_navigate(req: Dict[str, Any], cdp: FakeCdp) -> Dict[str, Any]:
    cdp.state["loaders"] += 1
    loader, sid = f"L{cdp.state['loaders']}", req.get("sessionId")
    for name in ("DOMContentLoaded", "load"):
        cdp.after.append(lambda n=name: cdp.emit("Page.lifecycleEvent", {"frameId": "F1", "loaderId": loader, "name": n}, sid))
    return {"frameId": "F1", "loaderId": loader}


def _h_evaluate(req: Dict[str, Any], cdp: FakeCdp) -> Dict[str, Any]:
    expr = req["params"]["expression"]
    value = {"1+1": 2, "document.title": "fake", "'x'": "x"}.get(expr)
    return {"result": {"type": type(value).__name__, "value": value}}


def _h_screenshot(req: Dict[str, Any], cdp: FakeCdp) -> Dict[str, Any]:
    return {"data": base64.b64encode(K.png_bytes(4, 3)).decode("ascii")}


DEFAULT_HANDLERS: Dict[str, Callable[[Dict[str, Any], FakeCdp], Any]] = {
    "Browser.getVersion": lambda r, c: {"protocolVersion": "1.3", "product": "Chrome/120.0.0.0", "userAgent": "Fake/1", "jsVersion": "1"},
    "Target.createTarget": _h_create_target,
    "Target.attachToTarget": _h_attach,
    "Target.closeTarget": lambda r, c: {"success": True},
    "Page.navigate": _h_navigate,
    "Runtime.evaluate": _h_evaluate,
    "Page.captureScreenshot": _h_screenshot,
    "DOM.getDocument": lambda r, c: {"root": {"nodeId": 1}},
    "DOM.querySelector": lambda r, c: {"nodeId": 5},
    "DOM.getBoxModel": lambda r, c: {"model": {"content": [12, 22, 108, 22, 108, 58, 12, 58], "border": [10, 20, 110, 20, 110, 60, 10, 60]}},
}


def cdp_server(handlers: Optional[Dict[str, Any]] = None, hold: Optional[List[FakeCdp]] = None) -> FakeServer:
    """Сервер DevTools с таблицей команд; созданные FakeCdp складываются в `hold` (журнал для проверок)."""
    def handler(conn: ServerConn) -> None:
        cdp = FakeCdp(conn, handlers)
        if hold is not None:
            hold.append(cdp)
        cdp.run()
    return FakeServer(handler)


def raw_server(script: Callable[[ServerConn], None], *, handshake: bool = True) -> FakeServer:
    """Сервер со сценарием на уровне кадров: рукопожатие (если не отключено), затем `script(conn)`."""
    def handler(conn: ServerConn) -> None:
        conn.read_request()
        if handshake:
            conn.handshake()
        script(conn)
    return FakeServer(handler)


class Net(unittest.TestCase):
    """Общее для тестов с фейковыми серверами: уборка серверов и сокетов, проверка ошибок серверной стороны."""

    def serve(self, server: FakeServer) -> FakeServer:
        self.addCleanup(server.close)
        self.addCleanup(server.check)
        return server

    def connect(self, server: FakeServer, **kw: Any) -> W.WebSocket:
        kw.setdefault("timeout", 5)
        ws = W.WebSocket.connect(server.url(), **kw)
        self.addCleanup(ws.close, timeout=0.1)
        return ws


# ══ кодек ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

class RfcVectors(unittest.TestCase):
    """Байтовые примеры RFC 6455 §5.7 и §1.3, формы длины, маска, инкрементальный разбор, нарушения."""

    h = staticmethod(bytes.fromhex)

    def test_rfc_5_7_examples_encode_and_decode(self):
        key = self.h("37fa213d")
        cases = [
            (W.encode_frame(W.OP_TEXT, b"Hello", mask=False), "810548656c6c6f"),                  # немаскированный «Hello»
            (W.encode_frame(W.OP_TEXT, b"Hello", mask=True, key=key), "818537fa213d7f9f4d5158"),    # маскированный «Hello»
            (W.encode_frame(W.OP_TEXT, b"Hel", fin=False, mask=False), "010348656c"),               # первый фрагмент
            (W.encode_frame(W.OP_CONT, b"lo", mask=False), "80026c6f"),                             # последний фрагмент
            (W.encode_frame(W.OP_PING, b"Hello", mask=False), "890548656c6c6f"),                    # ping
            (W.encode_frame(W.OP_PONG, b"Hello", mask=True, key=key), "8a8537fa213d7f9f4d5158"),    # маскированный pong
        ]
        for got, want in cases:
            self.assertEqual(got.hex(), want)
        dec = W.FrameDecoder()
        dec.feed(self.h("810548656c6c6f") + self.h("010348656c") + self.h("80026c6f") + self.h("890548656c6c6f"))
        self.assertEqual([dec.next_frame() for _ in range(4)],
                         [W.Frame(True, 1, b"Hello"), W.Frame(False, 1, b"Hel"), W.Frame(True, 0, b"lo"), W.Frame(True, 9, b"Hello")])
        self.assertIsNone(dec.next_frame())
        server_side = W.FrameDecoder(expect_masked=True)
        server_side.feed(self.h("818537fa213d7f9f4d5158"))
        self.assertEqual(server_side.next_frame(), W.Frame(True, 1, b"Hello"))

    def test_rfc_length_headers_256_and_64k(self):
        self.assertEqual(W.encode_frame(W.OP_BINARY, b"\0" * 256, mask=False)[:4].hex(), "827e0100")
        self.assertEqual(W.encode_frame(W.OP_BINARY, b"\0" * 65536, mask=False)[:10].hex(), "827f0000000000010000")

    def test_accept_key_example_from_rfc_1_3(self):
        self.assertEqual(W.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_mask_is_the_per_byte_definition_and_its_own_inverse(self):
        for n in (0, 1, 3, 4, 5, 63, 64, 65, 1000):
            data, key = os.urandom(n), os.urandom(4)
            self.assertEqual(W.apply_mask(data, key), xor_mask(data, key), n)
            self.assertEqual(W.apply_mask(W.apply_mask(data, key), key), data)
        big, key = os.urandom(3_000_001), os.urandom(4)
        self.assertEqual(W.apply_mask(W.apply_mask(big, key), key), big)
        with self.assertRaises(ValueError):
            W.apply_mask(b"x", b"123")

    def test_length_forms_switch_exactly_at_the_documented_boundaries(self):
        for n, form in ((0, "7"), (1, "7"), (125, "7"), (126, "16"), (127, "16"), (65535, "16"), (65536, "64")):
            frame = W.encode_frame(W.OP_BINARY, b"\x5a" * n, mask=False)
            self.assertEqual(frame, srv_frame(OP_BIN, b"\x5a" * n, form=form), n)       # независимый кодировщик согласен
            dec = W.FrameDecoder()
            dec.feed(frame)
            self.assertEqual(dec.next_frame(), W.Frame(True, 2, b"\x5a" * n), n)

    def test_non_minimal_length_forms_from_a_server_are_accepted(self):
        dec = W.FrameDecoder()
        dec.feed(srv_frame(OP_TEXT, b"hi", form="64"))
        self.assertEqual(dec.next_frame().payload, b"hi")

    def test_client_frames_are_masked_with_a_fresh_key_each_time(self):
        a, b = W.encode_frame(W.OP_TEXT, b"same"), W.encode_frame(W.OP_TEXT, b"same")
        self.assertTrue(a[1] & 0x80 and b[1] & 0x80, "бит маски установлен")
        self.assertNotEqual(a[2:6], b[2:6], "ключи разные (os.urandom)")
        with mock.patch.object(W.os, "urandom", return_value=b"\x01\x02\x03\x04") as rnd:
            frame = W.encode_frame(W.OP_TEXT, b"x")
        rnd.assert_called_once_with(4)
        self.assertEqual(frame, srv_frame(OP_TEXT, b"x", mask_key=b"\x01\x02\x03\x04"))

    def test_decoder_is_incremental_and_loses_nothing(self):
        frame = srv_frame(OP_TEXT, b"x" * 300)
        dec = W.FrameDecoder()
        for i, byte in enumerate(frame):
            dec.feed(bytes([byte]))
            got = dec.next_frame()
            self.assertEqual(got is not None, i == len(frame) - 1, i)
        two = srv_frame(OP_TEXT, b"one") + srv_frame(OP_TEXT, b"two")
        dec.feed(two[:4])                                            # заголовок и 2 байта из 3: кадр ещё не целый
        self.assertIsNone(dec.next_frame())
        dec.feed(two[4:])
        self.assertEqual([dec.next_frame().payload, dec.next_frame().payload, dec.next_frame()], [b"one", b"two", None])

    def test_decoder_rejects_every_violation(self):
        def one(raw: bytes, **kw: Any) -> W.WsProtocolError:
            dec = W.FrameDecoder(**kw)
            dec.feed(raw)
            with self.assertRaises(W.WsProtocolError) as cm:
                dec.next_frame()
            return cm.exception
        for rsv in (1, 2, 4, 7):
            self.assertIn("RSV", str(one(srv_frame(OP_TEXT, b"x", rsv=rsv))))
        for op in (3, 4, 5, 6, 7, 11, 12, 13, 14, 15):
            self.assertIn("opcode", str(one(bytes([0x80 | op, 0]))))
        self.assertIn("замаскирован", str(one(srv_frame(OP_TEXT, b"x", mask_key=b"abcd"))))
        self.assertIn("не замаскирован", str(one(srv_frame(OP_TEXT, b"x"), expect_masked=True)))
        self.assertIn("фрагментирован", str(one(srv_frame(OP_PING, b"x", fin=False))))
        self.assertIn("125", str(one(srv_frame(OP_PING, b"x" * 126))))
        self.assertIn("старший бит", str(one(bytes([0x81, 127]) + (1 << 63).to_bytes(8, "big"))))
        err = one(srv_frame(OP_TEXT, b"x" * 10), max_payload=9)
        self.assertEqual(err.code, W.CLOSE_TOO_BIG)

    def test_oversize_is_refused_by_the_header_before_the_payload_arrives(self):
        dec = W.FrameDecoder(max_payload=1000)
        dec.feed(bytes([0x82, 127]) + (10 ** 12).to_bytes(8, "big"))           # заголовок бредового кадра, тела нет
        with self.assertRaises(W.WsProtocolError) as cm:
            dec.next_frame()
        self.assertEqual(cm.exception.code, W.CLOSE_TOO_BIG)

    def test_encoder_refuses_what_the_rfc_forbids(self):
        with self.assertRaises(ValueError):
            W.encode_frame(W.OP_PING, b"x" * 126)
        with self.assertRaises(ValueError):
            W.encode_frame(W.OP_CLOSE, b"", fin=False)
        with self.assertRaises(ValueError):
            W.encode_frame(3, b"")

# ══ рукопожатие ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

class Handshake(Net):
    def test_the_request_is_exactly_what_rfc_6455_asks_a_client_for(self):
        seen: Dict[str, Any] = {}

        def script(conn: ServerConn) -> None:
            seen["line"], seen["headers"], seen["raw"] = conn.request_line, dict(conn.headers), conn.raw_request

        server = self.serve(raw_server(script))
        ws = self.connect(server)
        deadline = time.time() + 5
        while "line" not in seen and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(seen["line"], "GET /devtools/browser/test HTTP/1.1")
        h = seen["headers"]
        self.assertEqual(h["host"], f"127.0.0.1:{server.port}")
        self.assertEqual((h["upgrade"].lower(), h["connection"].lower(), h["sec-websocket-version"]), ("websocket", "upgrade", "13"))
        self.assertEqual(len(base64.b64decode(h["sec-websocket-key"])), 16, "ключ — 16 случайных байт в base64")
        self.assertNotIn("origin", h, "Chrome отвергает WebSocket с чужим Origin; клиенту без браузера он не нужен")
        self.assertNotIn("sec-websocket-extensions", h)
        self.assertNotIn("sec-websocket-protocol", h)
        self.assertTrue(seen["raw"].isascii())
        self.assertFalse(ws.closed)

    def test_every_new_connection_uses_a_new_key(self):
        keys: List[str] = []
        server = self.serve(raw_server(lambda c: keys.append(c.headers["sec-websocket-key"])))
        for _ in range(3):
            self.connect(server)
        deadline = time.time() + 5
        while len(keys) < 3 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(set(keys)), 3)

    def test_a_wrong_answer_is_a_refusal_not_a_connection(self):
        cases = {
            "статус 200": dict(status="200 OK"),
            "статус 403": dict(status="403 Forbidden"),
            "статус 404": dict(status="404 Not Found"),
            "нет Upgrade": dict(drop=("Upgrade",)),
            "Upgrade не websocket": dict(headers=(("Upgrade", "h2c"),), drop=("Upgrade",)),
            "нет Connection": dict(drop=("Connection",)),
            "Connection: close": dict(headers=(("Connection", "close"),), drop=("Connection",)),
            "чужой Accept": dict(accept="AAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            "нет Accept": dict(drop=("Sec-WebSocket-Accept",)),
            "расширение, которого не просили": dict(headers=(("Sec-WebSocket-Extensions", "permessage-deflate"),)),
            "подпротокол, которого не просили": dict(headers=(("Sec-WebSocket-Protocol", "chat"),)),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                server = self.serve(raw_server(lambda c, kw=kw: c.handshake(**kw), handshake=False))
                with self.assertRaises(W.WsHandshakeError):
                    W.WebSocket.connect(server.url(), timeout=3)

    def test_garbage_instead_of_http_is_a_refusal(self):
        for name, raw in (("не HTTP", b"SSH-2.0-OpenSSH\r\n\r\n"), ("строка без двоеточия", b"HTTP/1.1 101 X\r\nUpgrade websocket\r\n\r\n"),
                          ("пустой ответ", b"\r\n\r\n")):
            with self.subTest(name):
                server = self.serve(raw_server(lambda c, raw=raw: c.handshake(raw=raw), handshake=False))
                with self.assertRaises(W.WsHandshakeError):
                    W.WebSocket.connect(server.url(), timeout=3)

    def test_an_endless_header_is_cut_off(self):
        def script(conn: ServerConn) -> None:
            conn.sock.sendall(b"HTTP/1.1 101 Switching Protocols\r\n" + b"X-Pad: " + b"a" * 70000)
            time.sleep(0.5)
        server = self.serve(raw_server(script, handshake=False))
        with self.assertRaises(W.WsHandshakeError) as cm:
            W.WebSocket.connect(server.url(), timeout=3)
        self.assertIn("64", str(cm.exception))

    def test_a_server_that_hangs_up_or_stays_silent_is_a_refusal(self):
        server = self.serve(raw_server(lambda c: None, handshake=False))                         # принял и закрыл
        with self.assertRaises(W.WsHandshakeError):
            W.WebSocket.connect(server.url(), timeout=3)
        quiet = self.serve(raw_server(lambda c: time.sleep(1.5), handshake=False))               # принял и молчит
        started = time.monotonic()
        with self.assertRaises(W.WsHandshakeError) as cm:
            W.WebSocket.connect(quiet.url(), timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.2)
        self.assertIn("вовремя", str(cm.exception))

    def test_connection_refused_is_a_handshake_error_with_the_address(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(W.WsHandshakeError) as cm:
            W.WebSocket.connect(f"ws://127.0.0.1:{port}/x", timeout=2)
        self.assertIn(str(port), str(cm.exception))

    def test_the_answer_may_arrive_byte_by_byte(self):
        server = self.serve(raw_server(lambda c: (c.handshake(pieces=1), c.send_text("после рукопожатия")), handshake=False))
        ws = self.connect(server)
        self.assertEqual(ws.recv(timeout=3), "после рукопожатия")

    def test_a_frame_in_the_same_segment_as_the_answer_is_not_lost(self):
        glued = srv_frame(OP_TEXT, "первый".encode("utf-8")) + srv_frame(OP_TEXT, b"second")
        server = self.serve(raw_server(lambda c: c.handshake(tail=glued), handshake=False))
        ws = self.connect(server)
        self.assertEqual([ws.recv(timeout=3), ws.recv(timeout=3)], ["первый", "second"])

    def test_connection_header_may_list_several_tokens_and_any_case(self):
        server = self.serve(raw_server(lambda c: c.handshake(headers=(("Connection", "keep-alive, UPGRADE"),), drop=("Connection",)),
                                       handshake=False))
        self.connect(server)

    def test_only_local_ws_addresses_are_ever_dialled(self):
        with mock.patch.object(W.socket, "create_connection", side_effect=AssertionError("сеть")) as dial:
            for url in ("wss://127.0.0.1:9/x", "http://127.0.0.1:9/x", "ws://example.com:9222/x", "ws://192.168.1.5:9222/x",
                        "ws://0.0.0.0:9222/x", "ws://[2001:db8::1]:9222/x", "ws://127.0.0.1.evil.com:9222/x", "ws://127.0.0.1:99999/x",
                        "ws://[::1:9222/x"):
                with self.subTest(url):
                    with self.assertRaises(W.WsHandshakeError):
                        W.WebSocket.connect(url, timeout=1)
        dial.assert_not_called()

    def test_a_name_that_resolves_off_machine_is_dropped_after_connect(self):
        server = self.serve(raw_server(lambda c: time.sleep(0.5), handshake=False))

        class FakeSock:
            def __init__(self) -> None:
                self.closed = False

            def getpeername(self) -> Tuple[str, int]:
                return ("93.184.216.34", 80)

            def close(self) -> None:
                self.closed = True
        fake = FakeSock()
        with mock.patch.object(W.socket, "create_connection", return_value=fake):
            with self.assertRaises(W.WsHandshakeError) as cm:
                W.WebSocket.connect(server.url().replace("127.0.0.1", "localhost"), timeout=1)
        self.assertIn("нелокальный", str(cm.exception))
        self.assertTrue(fake.closed)


# ══ сообщения ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

class Messages(Net):
    def pair(self, script: Callable[[ServerConn], None], **kw: Any) -> Tuple[FakeServer, W.WebSocket]:
        server = self.serve(raw_server(script))
        return server, self.connect(server, **kw)

    def test_client_frames_are_masked_final_text_frames_the_server_can_read(self):
        got: List[SrvFrame] = []

        def script(conn: ServerConn) -> None:
            got.append(conn.read_frame())
            conn.send_text("ok")
        _, ws = self.pair(script)
        ws.send_text("привет, мир")
        self.assertEqual(ws.recv(timeout=3), "ok")
        fr = got[0]
        self.assertEqual((fr.fin, fr.rsv, fr.opcode, fr.masked, fr.form), (True, 0, OP_TEXT, True, "7"))
        self.assertEqual(fr.payload.decode("utf-8"), "привет, мир")
        self.assertEqual(len(fr.key), 4)

    def test_a_large_message_goes_out_in_one_64_bit_frame_and_arrives_intact(self):
        payload = "ж" * 120_000                                                              # 240 КБ в UTF-8
        got: List[SrvFrame] = []

        def script(conn: ServerConn) -> None:
            got.append(conn.read_frame())
            conn.send_text("получил")
        _, ws = self.pair(script)
        ws.send_text(payload)
        self.assertEqual(ws.recv(timeout=10), "получил")
        self.assertEqual((got[0].form, got[0].payload.decode("utf-8")), ("64", payload))

    def test_the_client_can_cut_a_message_into_frames(self):
        frames: List[SrvFrame] = []

        def script(conn: ServerConn) -> None:
            while True:
                fr = conn.read_frame()
                frames.append(fr)
                if fr.fin:
                    break
            conn.send_text("ok")
        _, ws = self.pair(script)
        ws.send_text("abcdefgh", fragment_size=3)
        ws.recv(timeout=3)
        self.assertEqual([(f.fin, f.opcode, f.payload) for f in frames],
                         [(False, OP_TEXT, b"abc"), (False, OP_CONT, b"def"), (True, OP_CONT, b"gh")])
        self.assertTrue(all(f.masked for f in frames))
        with self.assertRaises(ValueError):
            ws.send_text("x", fragment_size=0)

    def test_messages_of_every_length_form_and_3_megabytes_arrive(self):
        sizes = (0, 5, 125, 126, 1000, 65535, 65536, 70_000, 3_000_000)

        def script(conn: ServerConn) -> None:
            for n in sizes:
                conn.send(srv_frame(OP_TEXT, b"q" * n))
        _, ws = self.pair(script)
        for n in sizes:
            self.assertEqual(len(ws.recv(timeout=10)), n)

    def test_binary_messages_come_back_as_bytes_and_text_as_str(self):
        _, ws = self.pair(lambda c: (c.send(srv_frame(OP_BIN, b"\x00\xff")), c.send_text("т")))
        self.assertEqual((ws.recv(timeout=3), ws.recv(timeout=3)), (b"\x00\xff", "т"))

    def test_a_fragmented_message_is_assembled_and_a_ping_inside_it_is_answered_at_once(self):
        pongs: List[bytes] = []

        def script(conn: ServerConn) -> None:
            conn.send(srv_frame(OP_TEXT, "Hel".encode(), fin=False))
            conn.send(srv_frame(OP_PING, b"are-you-there"))
            time.sleep(0.2)                                         # pong должен прийти, пока сообщение ещё не закончено
            fr = conn.read_frame()
            pongs.append(fr.payload if fr.opcode == OP_PONG and fr.masked else b"NOT A PONG")
            conn.send(srv_frame(OP_CONT, "lo ".encode(), fin=False))
            conn.send(srv_frame(OP_CONT, "мир".encode("utf-8")))
        _, ws = self.pair(script)
        self.assertEqual(ws.recv(timeout=5), "Hello мир")
        self.assertEqual(pongs, [b"are-you-there"])

    def test_a_character_split_across_fragments_is_valid_utf8_when_whole(self):
        raw = "ж".encode("utf-8")
        _, ws = self.pair(lambda c: (c.send(srv_frame(OP_TEXT, raw[:1], fin=False)), c.send(srv_frame(OP_CONT, raw[1:]))))
        self.assertEqual(ws.recv(timeout=3), "ж")

    def test_an_unsolicited_pong_is_swallowed_and_remembered(self):
        _, ws = self.pair(lambda c: (c.send(srv_frame(OP_PONG, b"hb")), c.send_text("после")))
        self.assertEqual(ws.recv(timeout=3), "после")
        self.assertEqual(ws.pongs, [b"hb"])

    def test_the_client_can_ping_and_read_the_pong_through_recv(self):
        def script(conn: ServerConn) -> None:
            fr = conn.read_frame()
            conn.send(srv_frame(OP_PONG, fr.payload))
            conn.send_text("done")
        _, ws = self.pair(script)
        ws.ping(b"p1")
        self.assertEqual(ws.recv(timeout=3), "done")
        self.assertEqual(ws.pongs, [b"p1"])

    def test_a_close_from_the_server_is_echoed_and_surfaced_with_code_and_reason(self):
        echoed: List[SrvFrame] = []

        def script(conn: ServerConn) -> None:
            conn.send(srv_frame(OP_CLOSE, struct.pack("!H", 1000) + "пока".encode("utf-8")))
            echoed.append(conn.read_frame())
        _, ws = self.pair(script)
        with self.assertRaises(W.WsClosed) as cm:
            ws.recv(timeout=3)
        self.assertEqual((cm.exception.code, cm.exception.reason), (1000, "пока"))
        self.assertTrue(ws.closed)
        deadline = time.time() + 3
        while not echoed and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual((echoed[0].opcode, struct.unpack("!H", echoed[0].payload[:2])[0], echoed[0].masked), (OP_CLOSE, 1000, True))
        with self.assertRaises(W.WsClosed):
            ws.recv(timeout=1)
        with self.assertRaises(W.WsClosed):
            ws.send_text("после закрытия")

    def test_a_close_without_a_body_is_a_normal_close(self):
        _, ws = self.pair(lambda c: c.send(srv_frame(OP_CLOSE)))
        with self.assertRaises(W.WsClosed) as cm:
            ws.recv(timeout=3)
        self.assertIsNone(cm.exception.code)

    def test_the_client_closes_by_the_book_and_it_is_idempotent(self):
        seen: List[Any] = []

        def script(conn: ServerConn) -> None:
            fr = conn.read_frame()
            seen.append((fr.opcode, struct.unpack("!H", fr.payload[:2])[0], fr.payload[2:], fr.masked))
            conn.send(srv_frame(OP_CLOSE, fr.payload))
            seen.append(conn.sock.recv(10))                                    # после close клиент должен закрыть сокет: b""
        _, ws = self.pair(script)
        ws.close(1000, "конец")
        ws.close()
        self.assertTrue(ws.closed)
        deadline = time.time() + 3
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(seen[0], (OP_CLOSE, 1000, "конец".encode("utf-8"), True))
        self.assertEqual(seen[1], b"")
        self.assertEqual(ws.close_code, 1000)

    def test_a_server_that_never_answers_close_does_not_hold_the_client(self):
        _, ws = self.pair(lambda c: time.sleep(2))
        started = time.monotonic()
        ws.close(timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(ws.closed)

    def test_the_context_manager_closes(self):
        server = self.serve(raw_server(lambda c: time.sleep(0.3)))
        with W.WebSocket.connect(server.url(), timeout=3) as ws:
            self.assertFalse(ws.closed)
        self.assertTrue(ws.closed)

    def test_every_violation_is_answered_with_a_close_code_and_raised(self):
        text_bad = srv_frame(OP_TEXT, b"\xff\xfe\xfa")
        cases = [
            ("RSV-бит", srv_frame(OP_TEXT, b"x", rsv=4), 1002),
            ("неизвестный opcode", bytes([0x83, 0]), 1002),
            ("маска у кадра сервера", srv_frame(OP_TEXT, b"x", mask_key=b"abcd"), 1002),
            ("продолжение без начала", srv_frame(OP_CONT, b"x"), 1002),
            ("новое сообщение посреди фрагментов", srv_frame(OP_TEXT, b"a", fin=False) + srv_frame(OP_TEXT, b"b"), 1002),
            ("фрагментированный ping", srv_frame(OP_PING, b"x", fin=False), 1002),
            ("close из 1 байта", srv_frame(OP_CLOSE, b"\x03"), 1002),
            ("close с кодом 999", srv_frame(OP_CLOSE, struct.pack("!H", 999)), 1002),
            ("close с кодом 1005 на проводе", srv_frame(OP_CLOSE, struct.pack("!H", 1005)), 1002),
            ("close с причиной не UTF-8", srv_frame(OP_CLOSE, struct.pack("!H", 1000) + b"\xff\xfe"), 1007),
            ("текст не UTF-8", text_bad, 1007),
            ("текст не UTF-8, собранный из фрагментов", srv_frame(OP_TEXT, b"\xc3", fin=False) + srv_frame(OP_CONT, b"("), 1007),
        ]
        for name, raw, code in cases:
            with self.subTest(name):
                got: List[SrvFrame] = []

                def script(conn: ServerConn, raw=raw, got=got) -> None:
                    conn.send(raw)
                    try:
                        got.append(conn.read_frame())
                    except (EOFError, OSError):
                        pass
                _, ws = self.pair(script)
                with self.assertRaises(W.WsProtocolError) as cm:
                    ws.recv(timeout=3)
                self.assertEqual(cm.exception.code, code)
                self.assertTrue(ws.closed)
                deadline = time.time() + 3
                while not got and time.time() < deadline:
                    time.sleep(0.01)
                self.assertTrue(got and got[0].opcode == OP_CLOSE, "серверу ушёл кадр close")
                self.assertEqual(struct.unpack("!H", got[0].payload[:2])[0], code)
                with self.assertRaises(W.WsClosed):
                    ws.recv(timeout=1)

    def test_oversize_message_is_closed_with_1009_by_header_and_by_sum_of_fragments(self):
        for name, raw in (("по заголовку", srv_frame(OP_BIN, b"x" * 5000)),
                          ("по сумме фрагментов", srv_frame(OP_BIN, b"x" * 600, fin=False) + srv_frame(OP_CONT, b"x" * 600))):
            with self.subTest(name):
                _, ws = self.pair(lambda c, raw=raw: c.send(raw), max_message=1000)
                with self.assertRaises(W.WsProtocolError) as cm:
                    ws.recv(timeout=3)
                self.assertEqual(cm.exception.code, 1009)

    def test_a_timeout_in_the_middle_of_a_frame_loses_nothing(self):
        frame = srv_frame(OP_TEXT, "полусообщение".encode("utf-8"))
        gate = threading.Event()

        def script(conn: ServerConn) -> None:
            conn.send(frame[:7])
            gate.wait(5)
            conn.send(frame[7:])
        _, ws = self.pair(script)
        started = time.monotonic()
        with self.assertRaises(W.WsTimeout):
            ws.recv(timeout=0.2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(ws.closed, "таймаут не закрывает соединение")
        with self.assertRaises(W.WsTimeout):
            ws.recv(timeout=0)                                     # нулевой таймаут — проверка без ожидания
        gate.set()
        self.assertEqual(ws.recv(timeout=3), "полусообщение")

    def test_silence_is_a_timeout_not_a_close(self):
        gate = threading.Event()
        _, ws = self.pair(lambda c: (gate.wait(5), c.send_text("позже")))
        with self.assertRaises(W.WsTimeout):
            ws.recv(timeout=0.1)
        gate.set()
        self.assertEqual(ws.recv(timeout=3), "позже")

    def test_eof_in_the_middle_of_a_frame_or_between_frames_is_a_close(self):
        frame = srv_frame(OP_TEXT, b"abcdefghij")
        _, ws = self.pair(lambda c: c.send(frame[:6]))
        with self.assertRaises(W.WsClosed) as cm:
            ws.recv(timeout=3)
        self.assertIn("посреди", str(cm.exception))
        _, ws = self.pair(lambda c: None)
        with self.assertRaises(W.WsClosed) as cm:
            ws.recv(timeout=3)
        self.assertNotIn("посреди", str(cm.exception))

    def test_eof_in_the_middle_of_a_fragmented_message_is_a_close(self):
        _, ws = self.pair(lambda c: c.send(srv_frame(OP_TEXT, b"abc", fin=False)))
        with self.assertRaises(W.WsClosed) as cm:
            ws.recv(timeout=3)
        self.assertIn("посреди", str(cm.exception))

    def test_a_send_that_cannot_complete_closes_the_connection(self):
        gate = threading.Event()
        _, ws = self.pair(lambda c: gate.wait(10))
        self.addCleanup(gate.set)
        started = time.monotonic()
        with self.assertRaises(W.WsTimeout):
            ws.send_text("x" * 40_000_000, timeout=0.3)          # сервер не читает: буфер сокета переполнится
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(ws.closed, "частично ушедший кадр портит поток — соединение закрыто")

# ══ протокол DevTools ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

class CdpBase(Net):
    """Соединение с фейковым браузером; `self.hold[0]` — его FakeCdp (журнал запросов)."""

    def open(self, handlers: Optional[Dict[str, Any]] = None, **kw: Any) -> C.Connection:
        self.hold: List[FakeCdp] = []
        server = self.serve(cdp_server(handlers, self.hold))
        ws = W.WebSocket.connect(server.url(), timeout=5)
        conn = C.Connection(ws, **kw)
        self.addCleanup(conn.close)
        return conn

    @property
    def cdp(self) -> FakeCdp:
        deadline = time.time() + 3
        while not self.hold and time.time() < deadline:
            time.sleep(0.01)
        return self.hold[0]


class CdpClient(CdpBase):
    def test_request_on_the_wire_is_compact_ascii_json_with_rising_ids(self):
        conn = self.open({"Test.echo": lambda r, c: {"got": r.get("params")}})
        self.assertEqual(conn.send("Test.echo", {"a": 1, "текст": "Ж😀"}), {"got": {"a": 1, "текст": "Ж😀"}})
        conn.send("Test.echo")
        conn.send("Test.echo", {}, session_id="S1")
        raw = self.cdp.raw
        self.assertEqual(raw[0], '{"id":1,"method":"Test.echo","params":{"a":1,"\\u0442\\u0435\\u043a\\u0441\\u0442":"\\u0416\\ud83d\\ude00"}}')
        self.assertEqual(raw[1], '{"id":2,"method":"Test.echo"}', "без params ключ params не шлётся")
        self.assertEqual(raw[2], '{"id":3,"method":"Test.echo","params":{},"sessionId":"S1"}')
        self.assertTrue(all(r.isascii() for r in raw))

    def test_an_empty_result_is_a_result(self):
        conn = self.open({"Test.empty": lambda r, c: {}})
        self.assertEqual(conn.send("Test.empty"), {})

    def test_a_browser_error_is_an_exception_with_every_field(self):
        def boom(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            raise Fail(-32000, "Cannot find context", {"detail": 1})
        conn = self.open({"Test.boom": boom})
        with self.assertRaises(C.CdpCommandError) as cm:
            conn.send("Test.boom")
        e = cm.exception
        self.assertEqual((e.method, e.code, e.message, e.data), ("Test.boom", -32000, "Cannot find context", {"detail": 1}))
        self.assertIn("Test.boom", str(e))
        self.assertIn("Cannot find context", str(e))
        self.assertTrue(conn.alive, "ошибка команды не рвёт соединение")
        self.assertEqual(conn.send("Browser.getVersion")["product"], "Chrome/120.0.0.0")

    def test_session_not_found_means_the_target_is_gone(self):
        def gone(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            raise Fail(-32001, "Session with given id not found.")
        conn = self.open({"Test.gone": gone})
        with self.assertRaises(C.TargetClosed):
            conn.send("Test.gone", session_id="S9")

    def test_events_that_arrive_before_the_reply_are_queued_for_wait_event(self):
        def noisy(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.emit("Foo.first", {"n": 1})
            cdp.emit("Foo.second", {"n": 2})
            return {"ok": True}
        conn = self.open({"Test.noisy": noisy})
        self.assertEqual(conn.send("Test.noisy"), {"ok": True})
        self.assertEqual(conn.wait_event("Foo.second", timeout=0), {"n": 2}, "уже в очереди — находится без обращения к сокету")
        self.assertEqual(conn.wait_event("Foo.first", timeout=0), {"n": 1})
        with self.assertRaises(C.CdpTimeout):
            conn.wait_event("Foo.first", timeout=0.05)

    def test_events_after_the_reply_are_read_from_the_stream(self):
        def later(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.after.append(lambda: cdp.emit("Foo.late", {"n": 7}))
            return {}
        conn = self.open({"Test.later": later})
        conn.send("Test.later")
        self.assertEqual(conn.wait_event("Foo.late", timeout=3), {"n": 7})

    def test_wait_event_filters_by_session_and_predicate(self):
        def burst(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            for sid, n in (("S1", 1), ("S2", 2), ("S1", 3), (None, 4)):
                cdp.emit("Foo.evt", {"n": n}, session=sid)
            return {}
        conn = self.open({"Test.burst": burst})
        conn.send("Test.burst")
        self.assertEqual(conn.wait_event("Foo.evt", session_id="S2", timeout=0), {"n": 2})
        self.assertEqual(conn.wait_event("Foo.evt", session_id="S1", predicate=lambda p: p["n"] > 1, timeout=0), {"n": 3})
        self.assertEqual([e["params"]["n"] for e in conn.events("Foo.evt")], [1, 4], "остальные на месте, в порядке прихода")
        self.assertEqual(conn.discard_events("Foo.evt", session_id="S1"), 1)
        self.assertEqual([e["params"]["n"] for e in conn.events()], [4])

    def test_a_call_that_times_out_does_not_take_the_next_ones_answer(self):
        def slow(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.state["slow"] = req
            return NO_REPLY

        def fast(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.reply(cdp.state["slow"], {"late": True})        # запоздавший ответ на первый вызов приходит ПЕРЕД ответом на второй
            return {"fast": True}
        conn = self.open({"Test.slow": slow, "Test.fast": fast})
        started = time.monotonic()
        with self.assertRaises(C.CdpTimeout) as cm:
            conn.send("Test.slow", timeout=0.15)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("Test.slow", str(cm.exception))
        self.assertEqual(conn.send("Test.fast"), {"fast": True})
        self.assertTrue(conn.alive)

    def test_an_answer_with_an_unknown_id_ends_the_conversation(self):
        def liar(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.conn.send_json({"id": 999, "result": {}})
            return NO_REPLY
        conn = self.open({"Test.liar": liar})
        with self.assertRaises(C.CdpProtocolError) as cm:
            conn.send("Test.liar", timeout=5)
        self.assertIn("999", str(cm.exception))
        self.assertFalse(conn.alive)
        before = len(self.cdp.log)
        with self.assertRaises(C.CdpProtocolError):
            conn.send("Browser.getVersion")
        self.assertEqual(len(self.cdp.log), before, "после нарушения в сеть больше не пишем")

    def test_a_stray_answer_while_waiting_for_an_event_is_a_violation_too(self):
        def liar(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.after.append(lambda: cdp.conn.send_json({"id": 42, "result": {}}))
            return {}
        conn = self.open({"Test.liar": liar})
        conn.send("Test.liar")
        with self.assertRaises(C.CdpProtocolError):
            conn.wait_event("Never.comes", timeout=3)

    def test_garbage_is_a_protocol_error_not_a_hang(self):
        def reply_with(raw: Any, binary: bool = False):
            def handler(req: Dict[str, Any], cdp: FakeCdp) -> Any:
                if binary:
                    cdp.conn.send(srv_frame(OP_BIN, b"\x01\x02"))
                else:
                    text = raw(req) if callable(raw) else raw
                    cdp.conn.send_text(text if isinstance(text, str) else json.dumps(text))
                return NO_REPLY
            return handler
        cases = {
            "не JSON": reply_with("not json {"),
            "массив": reply_with([1, 2]),
            "строка": reply_with("\"str\""),
            "объект без id и method": reply_with({"foo": 1}),
            "id строкой": reply_with({"id": "1", "result": {}}),
            "id логический": reply_with({"id": True, "result": {}}),
            "id дробный": reply_with({"id": 1.5, "result": {}}),
            "method не строка": reply_with({"method": 5}),
            "params события — массив": reply_with({"method": "Foo.bar", "params": [1]}),
            "result — не объект": reply_with(lambda r: {"id": r["id"], "result": 5}),
            "error — не объект": reply_with(lambda r: {"id": r["id"], "error": "boom"}),
            "двоичное сообщение": reply_with(None, binary=True),
        }
        for name, handler in cases.items():
            with self.subTest(name):
                conn = self.open({"Test.garbage": handler})
                started = time.monotonic()
                with self.assertRaises(C.CdpProtocolError):
                    conn.send("Test.garbage", timeout=5)
                self.assertLess(time.monotonic() - started, 3, "нарушение видно сразу, а не после таймаута")
                self.assertFalse(conn.alive)

    def test_a_browser_complaint_without_an_id_carries_its_text(self):
        conn = self.open({"Test.bad": lambda r, c: (c.conn.send_json({"error": {"code": -32600, "message": "Invalid request"}}), NO_REPLY)[1]})
        with self.assertRaises(C.CdpProtocolError) as cm:
            conn.send("Test.bad", timeout=5)
        self.assertIn("Invalid request", str(cm.exception))

    def test_a_dropped_socket_is_a_disconnect_and_stays_one(self):
        conn = self.open({"Test.die": lambda r, c: (_ for _ in ()).throw(Hangup())})
        with self.assertRaises(C.CdpDisconnected):
            conn.send("Test.die", timeout=5)
        self.assertFalse(conn.alive)
        started = time.monotonic()
        for _ in range(2):
            with self.assertRaises(C.CdpDisconnected):
                conn.send("Browser.getVersion")
        self.assertLess(time.monotonic() - started, 0.5, "последующие вызовы падают сразу")
        with self.assertRaises(C.CdpDisconnected):
            conn.wait_event("Foo.bar", timeout=3)

    def test_a_websocket_close_frame_is_a_disconnect_with_its_code(self):
        def bye(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.conn.send(srv_frame(OP_CLOSE, struct.pack("!H", 1001) + b"going away"))
            return NO_REPLY
        conn = self.open({"Test.bye": bye})
        with self.assertRaises(C.CdpDisconnected) as cm:
            conn.send("Test.bye", timeout=5)
        self.assertIn("1001", str(cm.exception))

    def test_a_disconnect_while_waiting_for_an_event(self):
        conn = self.open({"Test.die": lambda r, c: (_ for _ in ()).throw(Hangup())})
        conn.ws.send_text(json.dumps({"id": 1, "method": "Test.die"}))
        with self.assertRaises(C.CdpDisconnected):
            conn.wait_event("Foo.bar", timeout=5)

    def test_a_crashed_tab_fails_pending_and_later_calls_at_once(self):
        def hang(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.emit("Inspector.targetCrashed", {}, session=req["sessionId"])
            return NO_REPLY
        conn = self.open({"Page.hang": hang})
        conn.register_session("S1", "T1")
        conn.register_session("S2", "T2")
        started = time.monotonic()
        with self.assertRaises(C.TargetCrashed):
            conn.send("Page.hang", session_id="S1", timeout=10)
        self.assertLess(time.monotonic() - started, 2, "падение вкладки видно сразу, а не через таймаут")
        sent = len(self.cdp.log)
        with self.assertRaises(C.TargetCrashed):
            conn.send("Runtime.evaluate", session_id="S1")
        self.assertEqual(len(self.cdp.log), sent, "к упавшей вкладке команды не уходят")
        self.assertEqual(conn.send("Browser.getVersion", session_id="S2")["userAgent"], "Fake/1", "соседняя вкладка жива")
        with self.assertRaises(C.TargetCrashed):
            conn.wait_event("Foo.bar", session_id="S1", timeout=3)
        self.assertTrue(conn.alive)
        conn.forget_session("S1")
        self.assertEqual(conn.send("Browser.getVersion", session_id="S1")["userAgent"], "Fake/1", "забытая сессия снова допустима")

    def test_the_other_ways_a_tab_dies(self):
        cases = [
            ("отсоединена Inspector.detached", "Inspector.detached", {"reason": "target_closed"}, "S1", C.TargetClosed),
            ("Target.detachedFromTarget", "Target.detachedFromTarget", {"sessionId": "S1", "targetId": "T1"}, None, C.TargetClosed),
            ("Target.targetCrashed", "Target.targetCrashed", {"targetId": "T1", "status": "crashed", "errorCode": 1}, None, C.TargetCrashed),
            ("Target.targetDestroyed", "Target.targetDestroyed", {"targetId": "T1"}, None, C.TargetClosed),
        ]
        for name, method, params, session, expected in cases:
            with self.subTest(name):
                def kill(req: Dict[str, Any], cdp: FakeCdp, method=method, params=params, session=session) -> Any:
                    cdp.emit(method, params, session=session)
                    return NO_REPLY
                conn = self.open({"Page.kill": kill})
                conn.register_session("S1", "T1")
                with self.assertRaises(expected):
                    conn.send("Page.kill", session_id="S1", timeout=5)

    def test_handlers_run_in_the_read_loop_and_a_failing_one_breaks_nothing(self):
        seen: List[str] = []
        conn = self.open({"Test.emit": lambda r, c: (c.emit("Foo.evt", {"n": 1}, session="S1"), {})[1]})

        def bad(event: Dict[str, Any]) -> None:
            raise RuntimeError("обработчик сломался")
        conn.on("Foo.evt", bad, session_id="S1")
        conn.on("Foo.evt", lambda e: seen.append(f"S1:{e['params']['n']}"), session_id="S1")
        conn.on("Foo.evt", lambda e: seen.append("other-session"), session_id="S2")
        conn.on("Foo.evt", lambda e: seen.append(f"any:{e['params']['n']}"))
        conn.send("Test.emit")
        self.assertEqual(seen, ["S1:1", "any:1"])
        self.assertEqual(len(conn.handler_errors), 1)
        self.assertIn("обработчик сломался", conn.handler_errors[0])
        self.assertEqual(conn.wait_event("Foo.evt", timeout=0), {"n": 1}, "событие всё равно в очереди")
        conn.off("S1")
        conn.send("Test.emit")
        self.assertEqual(seen, ["S1:1", "any:1", "any:1"], "off(session) снимает обработчики сессии")

    def test_a_reply_nobody_waits_for_is_read_and_thrown_away(self):
        conn = self.open()
        first = conn.send_nowait("Browser.getVersion")
        self.assertEqual(first, 1)
        self.assertEqual(conn.send("Target.createTarget")["targetId"], "T1")
        self.assertTrue(conn.alive)

    def test_the_event_queue_is_bounded_and_counts_what_it_dropped(self):
        def flood(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            for n in range(5):
                cdp.emit("Foo.evt", {"n": n})
            return {}
        with mock.patch.object(C, "EVENT_QUEUE_MAX", 3):
            conn = self.open({"Test.flood": flood})
        conn.send("Test.flood")
        self.assertEqual([e["params"]["n"] for e in conn.events()], [2, 3, 4])
        self.assertEqual(conn.dropped_events, 2)

    def test_pump_collects_events_for_a_while_and_returns(self):
        def trickle(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.after.append(lambda: [(cdp.emit("Foo.evt", {"n": n}), time.sleep(0.03)) for n in range(4)])
            return {}
        conn = self.open({"Test.trickle": trickle})
        conn.send("Test.trickle")
        started = time.monotonic()
        count = conn.pump(0.5)
        elapsed = time.monotonic() - started
        self.assertEqual(count, 4)
        self.assertTrue(0.4 < elapsed < 1.5, elapsed)
        self.assertEqual(len(conn.events("Foo.evt")), 4)
        self.assertEqual(conn.pump(0), 0)

    def test_a_three_megabyte_answer_is_parsed(self):
        conn = self.open({"Test.big": lambda r, c: {"data": "я" * 1_500_000}})
        self.assertEqual(len(conn.send("Test.big", timeout=20)["data"]), 1_500_000)

    def test_a_closed_connection_refuses_calls(self):
        conn = self.open()
        conn.close()
        with self.assertRaises(C.CdpDisconnected):
            conn.send("Browser.getVersion")

# ══ вкладка ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

class PageBase(CdpBase):
    def browser(self, handlers: Optional[Dict[str, Any]] = None, *, file_roots: Tuple[Any, ...] = (), timeout: float = 5.0) -> Any:
        """Стенд `Chrome` без процесса: только то, что читает `Chrome.new_page` (соединение, viewport, каталоги file:)."""
        conn = self.open(handlers, default_timeout=timeout)
        shim = types.SimpleNamespace(conn=conn, viewport=(1280, 900), file_roots=tuple(file_roots))
        shim.new_page = types.MethodType(C.Chrome.new_page, shim)
        return shim

    def page(self, handlers: Optional[Dict[str, Any]] = None, **kw: Any) -> C.Page:
        return self.browser(handlers, **kw).new_page()


class CheckUrl(unittest.TestCase):
    def test_what_may_be_opened(self):
        for url in ("http://localhost:3000/orders", "https://example.com/a?b=1#c", "HTTP://X.test/", "  http://x.test/  ",
                    "data:text/html,<h1>x</h1>", "about:blank", "ABOUT:BLANK"):
            with self.subTest(url):
                self.assertEqual(C.check_url(url), url.strip())

    def test_what_may_not(self):
        bad = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "\x01javascript:alert(1)", "chrome://settings",
               "chrome-extension://abc/x.html", "view-source:http://x.test/", "blob:http://x.test/uuid", "ftp://x.test/", "ws://x.test/",
               "filesystem:http://x.test/temporary/a", "about:config", "about:blank#x", "devtools://devtools/bundled/inspector.html",
               "//example.com/x", "example.com", "localhost:3000", "", "   ", None, 123, "http://x.test/\nSet-Cookie: a=b",
               "http://[::1/", "file:///etc/passwd"]
        for url in bad:
            with self.subTest(url=url):
                with self.assertRaises(C.UrlRefused):
                    C.check_url(url)

    def test_file_urls_only_inside_the_named_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "site"
            (root / "sub").mkdir(parents=True)
            (root / "sub" / "index.html").write_text("<p>x</p>", encoding="utf-8")
            outside = Path(tmp).resolve() / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            ok = [f"file://{root}/sub/index.html", f"file://localhost{root}/sub/index.html", f"file://{root}", f"file://{root}/nope.html"]
            for url in ok:
                with self.subTest(url=url):
                    self.assertEqual(C.check_url(url, file_roots=[root]), url)
            bad = [f"file://{outside}", f"file://{root}/../secret.txt", f"file://{root}/%2e%2e/secret.txt", f"file://{root}/sub/..%2f..%2fsecret.txt",
                   f"file://otherhost{root}/sub/index.html", "file:///etc/passwd", "file:///"]
            for url in bad:
                with self.subTest(url=url):
                    with self.assertRaises(C.UrlRefused):
                        C.check_url(url, file_roots=[root])
            with self.assertRaises(C.UrlRefused):
                C.check_url(f"file://{root}/sub/index.html")                          # каталоги не названы — file: закрыт целиком
            if POSIX:
                (root / "link").symlink_to(outside)
                with self.assertRaises(C.UrlRefused):
                    C.check_url(f"file://{root}/link", file_roots=[root])               # ссылка внутри каталога на файл снаружи


class PageApi(PageBase):
    def test_a_new_page_is_created_attached_flat_enabled_and_pinned_to_the_viewport(self):
        browser = self.browser()
        page = browser.new_page()
        log = self.cdp.log
        self.assertEqual([r["method"] for r in log], ["Target.createTarget", "Target.attachToTarget", "Page.enable",
                                                      "Emulation.setDeviceMetricsOverride", "Emulation.setEmulatedMedia"])
        self.assertEqual(log[0]["params"], {"url": "about:blank"})
        self.assertEqual(log[1]["params"], {"targetId": "T1", "flatten": True})
        self.assertTrue(all(r.get("sessionId") == "S1" for r in log[2:]) and "sessionId" not in log[0] and "sessionId" not in log[1])
        self.assertEqual(log[3]["params"], {"width": 1280, "height": 900, "deviceScaleFactor": 1.0, "mobile": False})
        features = {f["name"]: f["value"] for f in log[4]["params"]["features"]}
        self.assertEqual(features, {"prefers-color-scheme": "light", "prefers-reduced-motion": "reduce"})
        self.assertEqual((page.target_id, page.session_id), ("T1", "S1"))

    def test_viewport_and_media_come_from_the_caller(self):
        browser = self.browser()
        browser.new_page(viewport=(800, 600), color_scheme="dark", reduced_motion=False)
        log = self.cdp.log
        self.assertEqual((log[3]["params"]["width"], log[3]["params"]["height"]), (800, 600))
        self.assertEqual({f["name"]: f["value"] for f in log[4]["params"]["features"]},
                         {"prefers-color-scheme": "dark", "prefers-reduced-motion": "no-preference"})
        for bad in ((0, 600), (800, -1), (1.5, 2), (True, 5), (800,), "800x600", (99999, 5)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    browser.new_page(viewport=bad)

    def test_emulation_helpers_have_the_pinned_defaults(self):
        page = self.page()
        n = len(self.cdp.log)
        page.emulate_media()
        page.set_viewport(300, 200)
        page.emulate_media(color_scheme="dark", reduced_motion=False)
        media, viewport, media2 = self.cdp.log[n:n + 3]
        self.assertEqual({f["name"]: f["value"] for f in media["params"]["features"]},
                         {"prefers-color-scheme": "light", "prefers-reduced-motion": "reduce"})
        self.assertEqual(viewport["params"], {"width": 300, "height": 200, "deviceScaleFactor": 1.0, "mobile": False})
        self.assertEqual({f["name"]: f["value"] for f in media2["params"]["features"]},
                         {"prefers-color-scheme": "dark", "prefers-reduced-motion": "no-preference"})

    def test_a_page_with_an_url_navigates_to_it_and_waits_for_load(self):
        browser = self.browser()
        browser.new_page("data:text/html,<p>hi</p>")
        methods = self.cdp.methods()
        self.assertEqual(methods[-3:], ["Page.enable", "Page.setLifecycleEventsEnabled", "Page.navigate"])
        self.assertEqual(self.cdp.log[-1]["params"], {"url": "data:text/html,<p>hi</p>"})

    def test_a_refused_url_never_reaches_the_browser(self):
        browser = self.browser()
        for url in ("javascript:alert(1)", "chrome://settings", "file:///etc/passwd", ""):
            with self.assertRaises(C.UrlRefused):
                browser.new_page(url)
        self.assertEqual(self.cdp.log, [], "ни одной команды: вкладка не создана")

    def test_navigate_checks_the_url_again_and_confines_file_urls_to_the_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "a.html").write_text("x", encoding="utf-8")
            page = self.browser(file_roots=(root,)).new_page(f"file://{root}/a.html")
            sent = len(self.cdp.log)
            for url in ("javascript:1", "file:///etc/hosts"):
                with self.assertRaises(C.UrlRefused):
                    page.navigate(url)
            self.assertEqual(len(self.cdp.log), sent)

    def test_navigate_returns_only_for_the_load_of_its_own_loader(self):
        def nav(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            sid = req["sessionId"]
            cdp.emit("Page.lifecycleEvent", {"loaderId": "OLD", "name": "load"}, session=sid)          # чужая загрузка
            cdp.after.append(lambda: cdp.emit("Page.lifecycleEvent", {"loaderId": "NEW", "name": "DOMContentLoaded"}, session=sid))

            def real() -> None:
                time.sleep(0.3)
                cdp.emit("Page.lifecycleEvent", {"loaderId": "NEW", "name": "load"}, session=sid)
            cdp.after.append(real)
            return {"frameId": "F", "loaderId": "NEW"}
        page = self.page({"Page.navigate": nav})
        started = time.monotonic()
        res = page.navigate("http://localhost/")
        self.assertGreaterEqual(time.monotonic() - started, 0.25, "событие load чужого loaderId не засчитано")
        self.assertEqual(res["loaderId"], "NEW")

    def test_navigate_wait_modes(self):
        def nav(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.after.append(lambda: cdp.emit("Page.lifecycleEvent", {"loaderId": "L", "name": "DOMContentLoaded"}, session=req["sessionId"]))
            return {"frameId": "F", "loaderId": "L"}                       # load не придёт никогда
        page = self.page({"Page.navigate": nav})
        started = time.monotonic()
        page.navigate("http://localhost/a", wait="commit", timeout=5)
        page.navigate("http://localhost/b", wait="domcontentloaded", timeout=5)
        self.assertLess(time.monotonic() - started, 2)
        with self.assertRaises(C.CdpTimeout) as cm:
            page.navigate("http://localhost/c", wait="load", timeout=0.3)
        self.assertIn("load", str(cm.exception))
        with self.assertRaises(ValueError):
            page.navigate("http://localhost/", wait="idle")

    def test_navigation_failure_is_an_error_with_the_browsers_words(self):
        page = self.page({"Page.navigate": lambda r, c: {"frameId": "F", "errorText": "net::ERR_CONNECTION_REFUSED"}})
        with self.assertRaises(C.NavigationError) as cm:
            page.navigate("http://localhost:1/")
        self.assertEqual((cm.exception.url, cm.exception.text), ("http://localhost:1/", "net::ERR_CONNECTION_REFUSED"))
        self.assertIn("ERR_CONNECTION_REFUSED", str(cm.exception))

    def test_a_navigation_inside_the_document_has_no_load_to_wait_for(self):
        page = self.page({"Page.navigate": lambda r, c: {"frameId": "F"}})
        started = time.monotonic()
        page.navigate("http://localhost/#anchor", timeout=5)
        self.assertLess(time.monotonic() - started, 1)

    def test_events_of_the_previous_page_do_not_pile_up(self):
        page = self.page()
        page.navigate("http://localhost/a")
        self.assertEqual([e["params"]["name"] for e in page.events("Page.lifecycleEvent")], ["DOMContentLoaded"])
        page.navigate("http://localhost/b")
        self.assertEqual([(e["params"]["loaderId"], e["params"]["name"]) for e in page.events("Page.lifecycleEvent")],
                         [("L2", "DOMContentLoaded")], "залежавшиеся события прошлой навигации выброшены")

    def test_evaluate_returns_values_and_reports_what_the_page_threw(self):
        results = {"num": {"result": {"type": "number", "value": 7}}, "str": {"result": {"type": "string", "value": "ж"}},
                   "obj": {"result": {"type": "object", "value": {"a": [1, {"b": None}]}}},
                   "undef": {"result": {"type": "undefined"}}, "nan": {"result": {"type": "number", "unserializableValue": "NaN"}},
                   "big": {"result": {"type": "bigint", "unserializableValue": "10n"}}}
        page = self.page({"Runtime.evaluate": lambda r, c: results[r["params"]["expression"]]})
        self.assertEqual(page.evaluate("num"), 7)
        self.assertEqual(page.evaluate("str"), "ж")
        self.assertEqual(page.evaluate("obj"), {"a": [1, {"b": None}]})
        self.assertIsNone(page.evaluate("undef"))
        self.assertEqual((page.evaluate("nan"), page.evaluate("big")), ("NaN", "10n"))

    def test_evaluate_exceptions_and_request_shape(self):
        def thrown(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            return {"result": {"type": "object", "subtype": "error"},
                    "exceptionDetails": {"text": "Uncaught", "lineNumber": 3, "columnNumber": 4,
                                         "exception": {"type": "object", "description": "ReferenceError: nope is not defined\n    at <anonymous>:4:5"}}}
        page = self.page({"Runtime.evaluate": thrown}, timeout=10)
        with self.assertRaises(C.JsException) as cm:
            page.evaluate("nope", await_promise=True, timeout=2)
        self.assertEqual(str(cm.exception), "ReferenceError: nope is not defined (строка 3)")
        params = self.cdp.log[-1]["params"]
        self.assertEqual((params["expression"], params["returnByValue"], params["awaitPromise"]), ("nope", True, True))
        self.assertEqual(params["timeout"], 1500, "скрипт страницы обрывает сам браузер раньше таймаута вызова")
        with self.assertRaises(C.JsException):
            self.page({"Runtime.evaluate": lambda r, c: {"result": {}, "exceptionDetails": "странно"}}).evaluate("x")
        with self.assertRaises(C.CdpProtocolError):
            self.page({"Runtime.evaluate": lambda r, c: {}}).evaluate("x")

    def test_screenshot_decodes_the_png_and_passes_the_clip(self):
        page = self.page()
        png = page.screenshot()
        self.assertEqual(png, K.png_bytes(4, 3))
        self.assertEqual(self.cdp.log[-1]["params"], {"format": "png"})
        page.screenshot(clip={"x": 1, "y": 2, "width": 30, "height": 40})
        self.assertEqual(self.cdp.log[-1]["params"]["clip"], {"x": 1, "y": 2, "width": 30, "height": 40, "scale": 1})
        for name, reply in (("нет data", {}), ("не base64", {"data": "@@@"}), ("не строка", {"data": 5})):
            with self.subTest(name):
                with self.assertRaises(C.CdpProtocolError):
                    self.page({"Page.captureScreenshot": lambda r, c, reply=reply: reply}).screenshot()

    def test_dom_and_input(self):
        page = self.page()
        self.assertEqual(page.element_box("#b"), {"x": 10, "y": 20, "width": 100, "height": 40})
        self.assertEqual([r["method"] for r in self.cdp.log[-3:]], ["DOM.getDocument", "DOM.querySelector", "DOM.getBoxModel"])
        self.assertEqual(self.cdp.log[-2]["params"], {"nodeId": 1, "selector": "#b"})
        self.assertEqual(self.cdp.log[-1]["params"], {"nodeId": 5})
        page.click(15, 25)
        page.insert_text("текст")
        events = [(r["params"]["type"], r["params"].get("button"), r["params"].get("clickCount")) for r in self.cdp.log
                  if r["method"] == "Input.dispatchMouseEvent"]
        self.assertEqual(events, [("mouseMoved", None, None), ("mousePressed", "left", 1), ("mouseReleased", "left", 1)])
        self.assertTrue(all((r["params"]["x"], r["params"]["y"]) == (15, 25) for r in self.cdp.log if r["method"] == "Input.dispatchMouseEvent"))
        self.assertEqual(self.cdp.log[-1], dict(self.cdp.log[-1], method="Input.insertText", params={"text": "текст"}))

    def test_a_selector_that_matches_nothing_is_none_and_a_broken_box_is_a_violation(self):
        page = self.page({"DOM.querySelector": lambda r, c: {"nodeId": 0}})
        self.assertIsNone(page.query_selector(".nope"))
        self.assertIsNone(page.element_box(".nope"))
        with self.assertRaises(C.CdpProtocolError):
            self.page({"DOM.getBoxModel": lambda r, c: {"model": {"border": [1, 2, 3]}}}).element_box("#b")
        with self.assertRaises(C.CdpProtocolError):
            self.page({"DOM.getDocument": lambda r, c: {}}).query_selector("#b")
        with self.assertRaises(C.CdpCommandError):
            self.page({"DOM.querySelector": lambda r, c: (_ for _ in ()).throw(Fail(-32000, "not a valid selector"))}).query_selector("##")

    def test_dialogs_are_closed_while_a_call_waits_so_nothing_hangs(self):
        for kind, accept in (("alert", False), ("confirm", False), ("prompt", False), ("beforeunload", True)):
            with self.subTest(kind):
                def evaluate(req: Dict[str, Any], cdp: FakeCdp, kind=kind) -> Any:
                    cdp.emit("Page.javascriptDialogOpening", {"url": "http://x/", "message": "ахтунг", "type": kind}, session=req["sessionId"])
                    handled = cdp.next_request()                      # страница стоит, пока диалог не закрыт
                    cdp.state["handled"] = handled
                    cdp.reply(handled, {})
                    return {"result": {"type": "number", "value": 1}}
                page = self.page({"Runtime.evaluate": evaluate})
                self.assertEqual(page.evaluate("alert('x')", timeout=5), 1)
                handled = self.cdp.state["handled"]
                self.assertEqual((handled["method"], handled["params"], handled["sessionId"]), ("Page.handleJavaScriptDialog", {"accept": accept}, "S1"))
                self.assertEqual(page.dialogs, [{"type": kind, "message": "ахтунг", "url": "http://x/"}])

    def test_closing_a_page_closes_the_target_and_is_safe_to_repeat(self):
        page = self.page()
        page.close()
        page.close()
        self.assertEqual(self.cdp.methods().count("Target.closeTarget"), 1)
        self.assertEqual(self.cdp.log[-1]["params"], {"targetId": "T1"})
        with self.assertRaises(C.TargetClosed):
            page.evaluate("1+1")

    def test_closing_a_page_of_a_dead_browser_does_not_raise(self):
        page = self.page({"Target.closeTarget": lambda r, c: (_ for _ in ()).throw(Hangup())})
        page.close()
        self.assertTrue(page.closed)

    def test_page_calls_surface_a_crash_of_their_own_tab(self):
        def crash(req: Dict[str, Any], cdp: FakeCdp) -> Any:
            cdp.emit("Inspector.targetCrashed", {}, session=req["sessionId"])
            return NO_REPLY
        page = self.page({"Runtime.evaluate": crash})
        with self.assertRaises(C.TargetCrashed):
            page.evaluate("1")
        with self.assertRaises(C.TargetCrashed):
            page.screenshot()

    def test_a_failed_setup_closes_the_tab_it_created(self):
        browser = self.browser({"Emulation.setEmulatedMedia": lambda r, c: (_ for _ in ()).throw(Fail(-32000, "no media"))})
        with self.assertRaises(C.CdpCommandError):
            browser.new_page()
        self.assertEqual(self.cdp.methods()[-1], "Target.closeTarget")

    def test_broken_answers_to_target_creation_are_violations(self):
        with self.assertRaises(C.CdpProtocolError):
            self.browser({"Target.createTarget": lambda r, c: {}}).new_page()
        with self.assertRaises(C.CdpProtocolError):
            self.browser({"Target.attachToTarget": lambda r, c: {"sessionId": 5}}).new_page()

# ══ поиск браузера ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

class FindBase(unittest.TestCase):
    """Мир из фейковых исполняемых во временном каталоге: поиск не видит то, что стоит на машине (env, PATH, /Applications, /usr/bin)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name).resolve()

    def exe(self, *parts: str, text: str = "#!/bin/sh\n", mode: int = 0o755) -> Path:
        p = self.t.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        p.chmod(mode)
        return p

    def find(self, config: Optional[Dict[str, Any]] = None, *, platform: str = "linux", env: Optional[Dict[str, str]] = None,
             **kw: Any) -> Dict[str, Any]:
        kw.setdefault("app_dirs", [self.t / "Applications", self.t / "home" / "Applications"])
        kw.setdefault("home", self.t / "home")
        kw.setdefault("fs_root", self.t / "fs")
        return BF.find_browser(config, platform=platform, env={"PATH": ""} if env is None else env, **kw)

    def assertFound(self, res: Dict[str, Any], path: Path, source: str) -> None:
        self.assertTrue(res["found"], res)
        self.assertEqual((res["path"], res["source"]), (str(path), source))


class FindBrowser(FindBase):
    def test_priority_env_then_config_path_then_channel_then_standard_then_path(self):
        env_exe = self.exe("env", "my-chrome")
        cfg_exe = self.exe("cfg", "project-chrome")
        chan_exe = self.exe("fs", "usr", "bin", "chromium")
        std_exe = self.exe("fs", "opt", "google", "chrome", "chrome")
        path_exe = self.exe("bin", "google-chrome-stable")
        env = {"PATH": str(self.t / "bin"), BF.BROWSER_ENV: str(env_exe)}
        cfg = {"browserPath": str(cfg_exe), "browserChannel": "chromium"}
        self.assertFound(self.find(cfg, env=env), env_exe, "env")
        del env[BF.BROWSER_ENV]
        self.assertFound(self.find(cfg, env=env), cfg_exe, "config.browserPath")
        cfg.pop("browserPath")
        self.assertFound(self.find(cfg, env=env), chan_exe, "config.browserChannel")
        cfg.pop("browserChannel")
        self.assertFound(self.find(cfg, env=env), chan_exe, "standard")       # на Linux стандартный порядок: chromium, затем chrome
        chan_exe.unlink()
        self.assertFound(self.find(cfg, env=env), std_exe, "standard")
        std_exe.unlink()
        self.assertFound(self.find(cfg, env=env), path_exe, "PATH")
        path_exe.unlink()
        res = self.find(cfg, env=env)
        self.assertFalse(res["found"])
        self.assertIsNone(res["path"])

    def test_what_a_human_named_but_is_broken_is_never_replaced_by_another_browser(self):
        other = self.exe("fs", "usr", "bin", "chromium")                      # нормальный браузер лежит рядом
        env = {"PATH": str(other.parent)}
        directory = self.t / "just-a-dir"
        directory.mkdir()
        plain = self.exe("data", "chrome.txt", mode=0o644)
        cases = [
            ("env: файла нет", dict(env=dict(env, **{BF.BROWSER_ENV: str(self.t / "nope")})), None, "env", "файла нет"),
            ("env: каталог", dict(env=dict(env, **{BF.BROWSER_ENV: str(directory)})), None, "env", "каталог"),
            ("env: не исполняемый", dict(env=dict(env, **{BF.BROWSER_ENV: str(plain)})), None, "env", "не исполняемый"),
            ("env: имени нет в PATH", dict(env=dict(env, **{BF.BROWSER_ENV: "no-such-command"})), None, "env", "нет в PATH"),
            ("config: файла нет", dict(env=env), {"browserPath": str(self.t / "nope")}, "config.browserPath", "файла нет"),
            ("config: каталог", dict(env=env), {"browserPath": str(directory)}, "config.browserPath", "каталог"),
            ("канал не установлен", dict(env=env), {"browserChannel": "chrome-canary"}, "config.browserChannel", "не найден"),
            ("канал с опечаткой", dict(env=env), {"browserChannel": "chrom"}, "config.browserChannel", "ожидается одно из"),
        ]
        for name, kw, cfg, source, word in cases:
            with self.subTest(name):
                res = self.find(cfg, **kw)
                self.assertFalse(res["found"], res)
                self.assertEqual(res["source"], source)
                self.assertIn(word, res["reason"])
        self.assertTrue(self.find(None, env=env)["found"], "без заданного выбора браузер находится")

    def test_the_failure_names_the_variable_and_the_value(self):
        res = self.find(None, env={"PATH": "", BF.BROWSER_ENV: "/no/such/chrome"})
        self.assertEqual(res["reason"], f"{BF.BROWSER_ENV}=/no/such/chrome: файла нет")

    def test_an_empty_or_blank_variable_is_unset(self):
        std = self.exe("fs", "usr", "bin", "chromium")
        for value in ("", "   "):
            with self.subTest(repr(value)):
                self.assertFound(self.find(None, env={"PATH": "", BF.BROWSER_ENV: value}), std, "standard")

    def test_a_bare_command_name_is_looked_up_in_path(self):
        exe = self.exe("bin", "my-browser")
        env = {"PATH": str(exe.parent)}
        self.assertFound(self.find(None, env=dict(env, **{BF.BROWSER_ENV: "my-browser"})), exe, "env")
        self.assertFound(self.find({"browserPath": "my-browser"}, env=env), exe, "config.browserPath")

    def test_config_paths_may_be_relative_to_the_repo_home_relative_or_quoted(self):
        repo = self.t / "repo"
        exe = self.exe("repo", "tools", "chrome")
        self.assertFound(self.find({"browserPath": "tools/chrome"}, root=repo), exe, "config.browserPath")
        home_exe = self.exe("home", "bin", "chrome")
        self.assertFound(self.find({"browserPath": "~/bin/chrome"}), home_exe, "config.browserPath")
        self.assertFound(self.find({"browserPath": f'"{exe}"'}), exe, "config.browserPath")
        self.assertFound(self.find({"browserPath": f"'{exe}'"}), exe, "config.browserPath")

    def test_a_macos_app_bundle_may_be_named_instead_of_the_binary(self):
        import plistlib
        app = self.t / "Apps" / "Brave Browser.app"
        binary = self.exe("Apps", "Brave Browser.app", "Contents", "MacOS", "Brave Browser Executable")
        with open(str(app / "Contents" / "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleExecutable": "Brave Browser Executable", "CFBundleShortVersionString": "1.2.3.4"}, fh)
        self.assertFound(self.find(None, platform="darwin", env={"PATH": "", BF.BROWSER_ENV: str(app)}), binary, "env")
        empty = self.t / "Apps" / "Empty.app"
        empty.mkdir()
        res = self.find(None, platform="darwin", env={"PATH": "", BF.BROWSER_ENV: str(empty)})
        self.assertFalse(res["found"])
        self.assertIn(".app", res["reason"])

    def test_a_file_without_the_run_bit_does_not_count_as_found(self):
        self.exe("fs", "usr", "bin", "chromium", mode=0o644)
        good = self.exe("fs", "opt", "google", "chrome", "chrome")
        self.assertFound(self.find(), good, "standard")

    def test_macos_layout_and_order(self):
        chrome = self.exe("Applications", "Google Chrome.app", "Contents", "MacOS", "Google Chrome")
        chromium = self.exe("Applications", "Chromium.app", "Contents", "MacOS", "Chromium")
        user = self.exe("home", "Applications", "Google Chrome.app", "Contents", "MacOS", "Google Chrome")
        self.assertFound(self.find(platform="darwin"), chrome, "standard")
        chrome.unlink()
        self.assertFound(self.find(platform="darwin"), user, "standard")               # ~/Applications — тоже стандартное место, и порядок
        user.unlink()                                                                  # задаёт браузер (Chrome раньше Chromium), потом каталог
        self.assertFound(self.find(platform="darwin"), chromium, "standard")
        chromium.unlink()
        self.assertFalse(self.find(platform="darwin")["found"])

    def test_windows_layout_order_and_no_run_bit_needed(self):
        pf, pf86, local = self.t / "pf", self.t / "pf86", self.t / "local"
        env = {"PATH": "", "PROGRAMFILES": str(pf), "PROGRAMFILES(X86)": str(pf86), "LOCALAPPDATA": str(local)}
        in_local = self.exe("local", "Google", "Chrome", "Application", "chrome.exe", mode=0o644)
        in_pf86 = self.exe("pf86", "Google", "Chrome", "Application", "chrome.exe", mode=0o644)
        in_pf = self.exe("pf", "Google", "Chrome", "Application", "chrome.exe", mode=0o644)
        self.assertFound(self.find(platform="win32", env=env), in_pf, "standard")
        in_pf.unlink()
        self.assertFound(self.find(platform="win32", env=env), in_pf86, "standard")
        in_pf86.unlink()
        self.assertFound(self.find(platform="win32", env=env), in_local, "standard")
        in_local.unlink()
        chromium = self.exe("local", "Chromium", "Application", "chrome.exe", mode=0o644)
        self.assertFound(self.find(platform="win32", env=env), chromium, "standard")
        self.assertFalse(self.find(platform="win32", env={"PATH": ""})["found"], "без переменных Windows искать негде")

    def test_linux_prefers_chromium_and_the_path_prefers_chrome_on_the_other_platforms(self):
        chromium = self.exe("fs", "usr", "bin", "chromium")
        chrome = self.exe("fs", "opt", "google", "chrome", "chrome")
        self.assertFound(self.find(), chromium, "standard")
        chromium.unlink()
        self.assertFound(self.find(), chrome, "standard")
        chrome.unlink()
        bindir = self.t / "bin"
        a = self.exe("bin", "chromium")
        b = self.exe("bin", "google-chrome")
        self.assertFound(self.find(env={"PATH": str(bindir)}, platform="linux"), a, "PATH")
        self.assertFound(self.find(env={"PATH": str(bindir)}, platform="darwin"), b, "PATH")

    def test_every_channel_resolves_on_every_platform(self):
        for channel in BF.CHANNELS:
            self.assertTrue(BF._channel_fixed(channel, "darwin", {}, [Path("/A")], None)[0].parts[2].endswith(".app"), channel)
            self.assertTrue(BF._channel_fixed(channel, "win", {"PROGRAMFILES": "C:/pf"}, [], None), channel)
        self.assertEqual(set(BF.CHANNELS), set(CFG.BROWSER_CHANNELS), "каналы конфига и поиска — один список")
        self.assertEqual(set(BF.CHANNELS), set(BF._MAC_APPS) | set(), "в таблицах macOS есть каждый канал")
        self.assertEqual({*BF._WIN_PATHS, *BF._LINUX}, set(BF.CHANNELS))

    def test_channels_pick_their_own_browser_and_edge_is_never_a_default(self):
        edge = self.exe("Applications", "Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge")
        self.assertFalse(self.find(platform="darwin")["found"], "Edge в поиск по умолчанию не входит")
        self.assertFound(self.find({"browserChannel": "msedge"}, platform="darwin"), edge, "config.browserChannel")
        beta = self.exe("Applications", "Google Chrome Beta.app", "Contents", "MacOS", "Google Chrome Beta")
        self.assertFound(self.find({"browserChannel": "chrome-beta"}, platform="darwin"), beta, "config.browserChannel")
        win_edge = self.exe("pf86", "Microsoft", "Edge", "Application", "msedge.exe", mode=0o644)
        self.assertFound(self.find({"browserChannel": "msedge"}, platform="win32", env={"PATH": "", "PROGRAMFILES(X86)": str(self.t / "pf86")}),
                         win_edge, "config.browserChannel")
        via_path = self.exe("bin", "microsoft-edge")
        self.assertTrue(self.find({"browserChannel": "msedge"}, env={"PATH": str(via_path.parent)})["found"])
        self.assertFalse(self.find(None, env={"PATH": str(via_path.parent)})["found"], "и в PATH Edge по умолчанию не ищется")
        self.assertFalse(self.find({"browserChannel": "chrome-canary"}, platform="linux")["found"], "у Linux нет Canary — отказ, не догадка")

    def test_not_found_explains_where_it_looked(self):
        res = self.find()
        self.assertFalse(res["found"])
        self.assertIsNone(res["source"])
        for word in (BF.BROWSER_ENV, "verify.browserPath", "verify.browserChannel", "стандартные места", "PATH"):
            self.assertIn(word, res["reason"])
        self.assertEqual(len(res["tried"]), 5)

    def test_find_never_raises_on_odd_input(self):
        for cfg in (None, {}, {"browserPath": None, "browserChannel": None}, {"browserPath": 5}, {"browserChannel": 7}, {"browserPath": "  "}):
            with self.subTest(cfg=cfg):
                self.assertIn("found", self.find(cfg))

    def test_the_real_machine_answer_has_the_documented_shape(self):
        res = BF.find_browser()
        self.assertIn(res["found"], (True, False))
        self.assertEqual(sorted(res), sorted(["found", "path", "source", "detail"]) if res["found"] else sorted(["found", "path", "source", "reason", "tried"]))


class BrowserVersion(FindBase):
    def test_macos_reads_the_bundle_without_running_anything(self):
        import plistlib
        binary = self.exe("Applications", "Chromium.app", "Contents", "MacOS", "Chromium", text="#!/bin/sh\nexit 99\n")
        with open(str(binary.parents[1] / "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": "120.0.6099.109"}, fh)
        with mock.patch.object(BF.subprocess, "run", side_effect=AssertionError("процесс не запускается")):
            self.assertEqual(BF.browser_version(str(binary), platform="darwin"), {"product": "Chromium", "version": "120.0.6099.109"})

    @unittest.skipUnless(POSIX, "фейковый исполняемый — shell-скрипт")
    def test_other_platforms_ask_the_binary_for_one_line(self):
        cases = [("Chromium 120.0.6099.109 Arch Linux", "Chromium", "120.0.6099.109"), ("Google Chrome 155.0.8059.85 ", "Google Chrome", "155.0.8059.85"),
                 ("Microsoft Edge 120.0.2210.91", "Microsoft Edge", "120.0.2210.91"), ("120.0.6099.109", None, "120.0.6099.109")]
        for text, product, version in cases:
            with self.subTest(text):
                exe = self.exe("v", "browser", text=f"#!/bin/sh\necho '{text}'\n")
                self.assertEqual(BF.browser_version(str(exe), platform="linux"), {"product": product, "version": version})

    @unittest.skipUnless(POSIX, "фейковый исполняемый — shell-скрипт")
    def test_a_binary_that_cannot_say_gives_none_and_never_raises_or_hangs(self):
        silent = self.exe("v", "silent", text="#!/bin/sh\nexit 0\n")
        noise = self.exe("v", "noise", text="#!/bin/sh\necho 'usage: nothing here'\n")
        failing = self.exe("v", "failing", text="#!/bin/sh\necho 'boom' >&2\nexit 3\n")
        slow = self.exe("v", "slow", text="#!/bin/sh\nsleep 5\necho 'Chrome 1.2.3.4'\n")
        for exe in (silent, noise, failing, self.t / "missing"):
            with self.subTest(exe=exe.name):
                self.assertEqual(BF.browser_version(str(exe), platform="linux"), {"product": None, "version": None})
        started = time.monotonic()
        with mock.patch.object(BF, "VERSION_TIMEOUT", 0.3):
            self.assertEqual(BF.browser_version(str(slow), platform="linux"), {"product": None, "version": None})
        self.assertLess(time.monotonic() - started, 3)

    def test_windows_reads_the_version_directory_next_to_the_exe(self):
        exe = self.exe("Application", "chrome.exe", mode=0o644)
        for name in ("119.0.6045.159", "120.0.6099.109", "SetupMetrics", "Dictionaries", "99.1"):
            (exe.parent / name).mkdir()
        (exe.parent / "121.0.0.1").write_text("файл, не каталог", encoding="utf-8")
        self.assertEqual(BF.browser_version(str(exe), platform="win32"), {"product": None, "version": "120.0.6099.109"})
        lone = self.exe("Lone", "chrome.exe", mode=0o644)
        self.assertEqual(BF.browser_version(str(lone), platform="win32")["version"], None)

    def test_major(self):
        self.assertEqual([BF.major(v) for v in ("155.0.8059.85", "9.1", "x.1", "", None)], [155, 9, None, None, None])


class StopAnswer(unittest.TestCase):
    def test_the_stop_is_named_explained_and_forbids_installing(self):
        ans = BF.blocked_answer("нет Chrome")
        self.assertEqual((ans["skipped"], ans["blocked"], ans["reason"]), (True, "no-browser", "нет Chrome"))
        self.assertIn(BF.BROWSER_ENV, ans["supply"])
        self.assertIn("verify.browserPath", ans["supply"])
        self.assertIn("не скачивай и не ставь", ans["nextStep"])
        json.dumps(ans)

# ══ daiver_capabilities ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class Capabilities(FindBase):
    KEYS = ["ready", "browser", "node", "typescript", "playwright", "vision", "warnings"]

    def setUp(self):
        super().setUp()
        self.repo = self.t / "repo"
        self.repo.mkdir()

    def write(self, rel: str, text: str) -> Path:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def hermetic_find(self, cfg: Any, **kw: Any) -> Dict[str, Any]:
        """Поиск браузера, не видящий машину: пустые PATH, /Applications и /usr/bin."""
        kw.pop("env", None)
        kw.pop("platform", None)
        kw.pop("which", None)
        return BF.find_browser(cfg, env={"PATH": ""}, platform="linux", fs_root=self.t / "fs", app_dirs=[], home=self.t / "home", **kw)

    def caps(self, **kw: Any) -> Dict[str, Any]:
        kw.setdefault("find", self.hermetic_find)
        kw.setdefault("which", lambda name, path=None: None)
        return CA.capabilities(self.repo, **kw)

    def browser(self, version_line: str = "Chromium 120.0.6099.109 Arch Linux") -> Path:
        return self.exe("fs", "usr", "bin", "chromium", text=f"#!/bin/sh\necho '{version_line}'\n")

    @unittest.skipUnless(POSIX, "фейковый браузер — shell-скрипт")
    def test_found_browser_shape(self):
        exe = self.browser()
        out = self.caps(platform="linux")
        self.assertEqual(list(out), self.KEYS)
        self.assertTrue(out["ready"])
        self.assertEqual(out["browser"], {"found": True, "path": str(exe), "version": "120.0.6099.109", "product": "Chromium",
                                          "source": "standard", "detail": "стандартный путь"})
        self.assertEqual((out["node"], out["typescript"]), ({"found": False}, {"found": False}))
        self.assertEqual(out["playwright"], {"found": False, "installed": False, "version": None})
        self.assertEqual(out["warnings"], [])
        self.assertNotIn("blocked", out)
        json.dumps(out)

    def test_no_browser_is_a_named_stop_right_after_ready(self):
        out = self.caps()
        self.assertFalse(out["ready"])
        self.assertEqual(list(out)[:4], ["ready", "blocked", "supply", "nextStep"], "останов — первое, что читает слабая модель")
        self.assertEqual(out["blocked"], "no-browser")
        self.assertEqual(out["browser"]["found"], False)
        self.assertIn(BF.BROWSER_ENV, out["browser"]["reason"])
        self.assertIn(BF.BROWSER_ENV, out["supply"])
        self.assertIn("не скачивай и не ставь", out["nextStep"])
        self.assertEqual(out["browser"]["source"], None)

    def test_a_broken_choice_of_the_human_is_the_reason_and_the_source(self):
        out = self.caps(env={"PATH": "", BF.BROWSER_ENV: "/no/such/chrome"}, find=BF.find_browser, platform="linux")
        self.assertFalse(out["ready"])
        self.assertEqual((out["browser"]["source"], out["browser"]["reason"]), ("env", f"{BF.BROWSER_ENV}=/no/such/chrome: файла нет"))

    @unittest.skipUnless(POSIX, "фейковый браузер — shell-скрипт")
    def test_the_config_of_the_repo_reaches_the_search(self):
        exe = self.exe("tools", "chrome", text="#!/bin/sh\necho 'Google Chrome 130.1.2.3'\n")
        self.write(".daiver/config.yaml", f"verify:\n  browserPath: {exe}\n")
        out = CA.capabilities(self.repo, env={"PATH": ""}, platform="linux", which=lambda n, path=None: None)
        self.assertEqual((out["browser"]["found"], out["browser"]["source"], out["browser"]["version"]), (True, "config.browserPath", "130.1.2.3"))

    def test_node_is_found_by_path_and_asked_for_its_version(self):
        calls: List[List[str]] = []

        def run(cmd: List[str], **kw: Any) -> Any:
            calls.append(cmd)
            return types.SimpleNamespace(stdout="v20.11.1\n", stderr="", returncode=0)
        out = self.caps(which=lambda name, path=None: "/opt/node/bin/node" if name == "node" else None, run=run)
        self.assertEqual(out["node"], {"found": True, "path": "/opt/node/bin/node", "version": "v20.11.1"})
        self.assertEqual(calls, [["/opt/node/bin/node", "--version"]])
        for name, run_fn in (("мусор", lambda c, **k: types.SimpleNamespace(stdout="garbage")),
                             ("OSError", mock.Mock(side_effect=OSError("нет прав"))),
                             ("таймаут", mock.Mock(side_effect=subprocess.TimeoutExpired("node", 1)))):
            with self.subTest(name):
                got = self.caps(which=lambda n, path=None: "/n" if n == "node" else None, run=run_fn)
                self.assertEqual(got["node"], {"found": True, "path": "/n", "version": None})

    def test_typescript_is_found_where_node_would_find_it(self):
        self.assertEqual(self.caps()["typescript"], {"found": False})
        self.write("node_modules/typescript/package.json", json.dumps({"name": "typescript", "version": "5.4.2"}))
        self.assertEqual(self.caps()["typescript"], {"found": True, "version": "5.4.2"})
        (self.repo / "node_modules" / "typescript" / "package.json").unlink()
        self.write("../node_modules/typescript/package.json", json.dumps({"version": "5.3.3"}))              # монорепо: пакет у родителя
        self.assertEqual(self.caps()["typescript"], {"found": True, "version": "5.3.3"})
        self.write("../node_modules/typescript/package.json", "{битый json")
        self.assertEqual(self.caps()["typescript"], {"found": False})

    def test_playwright_test_is_declared_and_or_installed(self):
        self.assertEqual(self.caps()["playwright"], {"found": False, "installed": False, "version": None})
        for section in ("devDependencies", "dependencies", "optionalDependencies", "peerDependencies"):
            with self.subTest(section):
                self.write("package.json", json.dumps({section: {"@playwright/test": "^1.48.0"}}))
                self.assertEqual(self.caps()["playwright"], {"found": True, "installed": False, "version": "^1.48.0"})
        self.write("node_modules/@playwright/test/package.json", json.dumps({"version": "1.48.2"}))
        self.assertEqual(self.caps()["playwright"], {"found": True, "installed": True, "version": "1.48.2"})
        self.write("package.json", json.dumps({"devDependencies": {"playwright": "1.0.0", "playwright-core": "1.0.0"}}))
        self.assertTrue(self.caps()["playwright"]["installed"])
        self.assertFalse(self.caps()["playwright"]["found"], "playwright и playwright-core — не @playwright/test: e2e-гейт ищет именно его")
        self.write("package.json", "[1, 2")
        self.assertFalse(self.caps()["playwright"]["found"])

    def test_vision_follows_the_config_and_off_says_what_will_happen(self):
        on = self.caps()["vision"]
        self.assertEqual(on, {"enabled": True, "note": CA.VISION_ON})
        self.assertIn("не загружается наружу", on["note"])
        self.write(".daiver/config.yaml", "verify:\n  vision: off\n")
        off = self.caps()["vision"]
        self.assertEqual(off, {"enabled": False, "note": CA.VISION_OFF})
        self.assertIn("skipped", off["note"])

    @unittest.skipUnless(POSIX, "фейковый браузер — shell-скрипт")
    def test_warnings_are_the_ones_a_human_must_hear(self):
        self.browser("Chromium 100.0.4896.60")
        self.write(".daiver/config.yaml", "verify:\n  browserChannel: nonsense\n  viewport: {width: 1.5, height: 2}\n")
        out = self.caps(platform="linux")
        self.assertEqual(len(out["warnings"]), 2, out["warnings"])
        self.assertTrue(any("109" in w and "100.0.4896.60" in w for w in out["warnings"]))
        self.assertTrue(any(w.startswith("verify.browserChannel") for w in out["warnings"]))
        self.assertFalse(any("viewport" in w for w in out["warnings"]), "чужие предупреждения конфига сюда не относятся")

    def test_the_tool_is_in_the_registry_takes_no_arguments_and_answers_like_the_function(self):
        from daiver import tools as T
        tool = T.TOOLS["daiver_capabilities"]
        self.assertEqual(tool["schema"], {"type": "object", "properties": {}, "additionalProperties": False})
        for word in ("CHROMIUM_EXECUTABLE_PATH", "no-browser", "не скачивай и не ставь", "playwright", "vision", "Только читает"):
            self.assertIn(word, tool["description"])
        out = T.call("daiver_capabilities", self.repo, {})
        self.assertEqual(list(out)[-6:], self.KEYS[1:])
        with self.assertRaises(T.ToolError):
            T.call("daiver_capabilities", self.repo, {"browser": "x"})

    def test_the_tool_works_over_json_rpc_in_a_separate_process(self):
        server = HERE.parents[2] / "hooks" / "daiver_mcp.py"
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "daiver_capabilities", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "daiver_capabilities", "arguments": {"x": 1}}}]
        env = dict(os.environ, **{BF.BROWSER_ENV: str(self.t / "no-such-browser")})
        r = subprocess.run([sys.executable, "-X", "utf8", str(server), "--project-root", str(self.repo)], input="".join(json.dumps(m) + "\n" for m in msgs),
                           capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
        out = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
        self.assertIn("daiver_capabilities", {t["name"] for t in out[0]["result"]["tools"]})
        body = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual((body["ready"], body["blocked"]), (False, "no-browser"))
        self.assertIn("файла нет", body["browser"]["reason"])
        self.assertTrue(out[2]["result"]["isError"])


# ══ N8: нет браузера — остановка с объяснением, никаких загрузок ═══════════════════════════════════════════════════════════════════

class NoDownloads(FindBase):
    MISS = {"found": False, "path": None, "source": None, "reason": "Chrome/Chromium на машине не найден: (тест)", "tried": []}

    def test_no_browser_stops_with_an_explanation_and_starts_or_opens_nothing(self):
        boom = AssertionError("браузера нет — ни процессов, ни сети быть не должно")
        with mock.patch.object(subprocess, "Popen", side_effect=boom), mock.patch.object(subprocess, "run", side_effect=boom), \
                mock.patch.object(socket.socket, "connect", side_effect=boom), mock.patch.object(socket, "create_connection", side_effect=boom):
            caps = CA.capabilities(self.t, find=lambda cfg, **kw: dict(self.MISS), which=lambda n, path=None: None)
            chrome, stop = None, None
            with mock.patch.object(C.BF, "find_browser", return_value=dict(self.MISS)):
                chrome, stop = C.launch_or_skip({}, root=self.t)
        self.assertFalse(caps["ready"])
        self.assertEqual(caps["blocked"], "no-browser")
        self.assertIsNone(chrome)
        self.assertEqual((stop["skipped"], stop["blocked"]), (True, "no-browser"))
        self.assertEqual(stop["reason"], self.MISS["reason"])
        self.assertIn("человек", stop["supply"])

    def test_the_browser_package_has_no_network_libraries_and_runs_no_command_but_taskkill(self):
        banned = {"urllib.request", "urllib.error", "http", "http.client", "http.server", "ftplib", "smtplib", "xmlrpc", "xmlrpc.client",
                  "socketserver", "ssl", "webbrowser", "ensurepip", "pip", "requests", "urllib3", "asyncio", "telnetlib", "poplib", "imaplib"}
        base = HERE / "daiver"
        files = sorted((base / "browser").glob("*.py")) + [base / "capabilities.py", base / "proc.py"]
        runners = {"run", "Popen", "call", "check_call", "check_output", "popen_group", "system"}
        literal_commands: List[str] = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names if node.module]
                else:
                    names = []
                for name in names:
                    self.assertNotIn(name, banned, f"{path.name}: импорт {name}")
                if isinstance(node, ast.Call):
                    fname = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if fname in runners and node.args:
                        first = node.args[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            literal_commands.append(first.value)
                        if isinstance(first, (ast.List, ast.Tuple)) and first.elts and isinstance(first.elts[0], ast.Constant):
                            literal_commands.append(str(first.elts[0].value))
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            self.fail(f"{path.name}: shell= в вызове {fname}")
        self.assertEqual(sorted(set(literal_commands)), ["taskkill"], "кроме taskkill на Windows команды берутся только из найденных путей")

    def test_the_launch_flags_are_pinned_whole(self):
        self.assertEqual(C.launch_args("/p", 1280, 900, ("--x=1",)), [
            "--headless=new", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars", "--mute-audio", "--force-device-scale-factor=1",
            "--force-color-profile=srgb", "--font-render-hinting=none", "--disable-lcd-text", "--disable-gpu",
            "--disable-background-networking", "--disable-component-update", "--disable-default-apps", "--disable-extensions",
            "--disable-sync", "--disable-breakpad", "--disable-client-side-phishing-detection", "--disable-hang-monitor",
            "--disable-prompt-on-repost", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
            "--disable-background-timer-throttling", "--disable-ipc-flooding-protection", "--disable-dev-shm-usage",
            "--disable-features=Translate,MediaRouter", "--metrics-recording-only", "--password-store=basic", "--use-mock-keychain",
            "--user-data-dir=/p", "--window-size=1280,900", "--x=1", "about:blank"])
        self.assertFalse([a for a in C.BASE_ARGS if "remote-debugging" in a and not a.endswith(("=127.0.0.1", "=0"))])

# ══ фейковый браузер как настоящий процесс ════════════════════════════════════════════════════════════════════════════════════════

class _LoggingCdp(FakeCdp):
    """FakeCdp, который пишет каждый запрос в журнал процесса (его читает тест из другого процесса)."""

    def __init__(self, conn: ServerConn, handlers: Dict[str, Any], log_fn: Callable[[Dict[str, Any]], None]):
        super().__init__(conn, handlers)
        self._log_fn = log_fn
        self.expect_path = "/devtools/browser/fake-uuid"

    def next_request(self) -> Optional[Dict[str, Any]]:
        req = super().next_request()
        if req is not None:
            self._log_fn({"event": "req", "method": req["method"], "params": req.get("params")})
        return req


def fake_chrome_main(argv: List[str], mode: str = "ok", note: Optional[str] = None) -> None:
    """Тело фейкового браузера (отдельный процесс, запускается скриптом `make_fake_browser`): пишет DevToolsActivePort в профиль,
    говорит по DevTools на 127.0.0.1. mode: ok | crash | hang | children | ignore-close | slow-port | bad-ws | no-download-support."""
    if "--version" in argv:
        print("Fake Chrome 120.0.0.0")
        return
    profile = Path(next(a.split("=", 1)[1] for a in argv if a.startswith("--user-data-dir=")))
    note = note or os.environ.get("FORGEUI_FAKE_NOTE")

    def log(obj: Dict[str, Any]) -> None:
        if note:
            with open(note, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj) + "\n")

    log({"event": "start", "pid": os.getpid(), "pgid": os.getpgrp(), "argv": argv, "mark": os.environ.get("FORGEUI_FAKE_MARK")})
    if mode == "crash":
        sys.stderr.write("fake chrome: FATAL: boom\n")
        sys.stderr.flush()
        sys.exit(7)
    if mode == "hang":
        time.sleep(300)
        return
    if mode == "children":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        log({"event": "child", "pid": child.pid})

    def close(req: Dict[str, Any], cdp: FakeCdp) -> Any:
        if mode != "ignore-close":
            cdp.after.append(lambda: threading.Timer(0.05, os._exit, [0]).start())
        return {}

    def no_download(req: Dict[str, Any], cdp: FakeCdp) -> Any:
        raise Fail(-32601, "'Browser.setDownloadBehavior' wasn't found")
    handlers: Dict[str, Any] = {"Browser.close": close}
    if mode == "no-download-support":
        handlers["Browser.setDownloadBehavior"] = no_download
    if mode == "bad-ws":
        server = raw_server(lambda c: c.handshake(status="200 OK"), handshake=False)
    else:
        server = FakeServer(lambda conn: _LoggingCdp(conn, handlers, log).run())
    sys.stderr.write(f"\nDevTools listening on ws://127.0.0.1:{server.port}/devtools/browser/fake-uuid\n")
    sys.stderr.flush()
    marker = profile / "DevToolsActivePort"
    if mode == "slow-port":
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(f"{server.port}\n")
            fh.flush()
            time.sleep(0.4)
            fh.write("/devtools/browser/fake-uuid")
    else:
        marker.write_text(f"{server.port}\n/devtools/browser/fake-uuid", encoding="utf-8")
    while True:
        time.sleep(0.2)


def make_fake_browser(directory: Path, mode: str = "ok", note: Optional[Path] = None, name: str = "fake-chrome") -> Path:
    """Исполняемый скрипт, ведущий себя как браузер (`exec` — процесс скрипта и есть лидер группы). Только POSIX."""
    code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import test_cdp; "
            f"test_cdp.fake_chrome_main(sys.argv[1:], {mode!r}, {str(note) if note else None!r})")
    script = directory / name
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -X utf8 -c "{code}" "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    return script


def process_lines() -> List[str]:
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return out.stdout.splitlines()


class LaunchBase(unittest.TestCase):
    """Фейковые браузеры: по одному скрипту на режим и на класс (свежий исполняемый файл macOS проверяет при первом запуске ~0.2 с,
    а тестов десятки); журнал каждого теста пишется туда, куда указывает FORGEUI_FAKE_NOTE."""

    scripts: Dict[str, Path]

    @classmethod
    def setUpClass(cls):
        cls.scripts = {}
        cls.scripts_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scripts_dir.cleanup)

    def setUp(self):
        if not POSIX:
            self.skipTest("фейковый браузер — shell-скрипт: только POSIX")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.temp_root = self.dir / "tmp"
        self.temp_root.mkdir()
        self.note = self.dir / "note.jsonl"
        patcher = mock.patch.dict(os.environ, {"FORGEUI_FAKE_NOTE": str(self.note)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.reap)

    def notes(self) -> List[Dict[str, Any]]:
        if not self.note.exists():
            return []
        return [json.loads(line) for line in self.note.read_text(encoding="utf-8").splitlines() if line.strip()]

    def pids(self) -> List[int]:
        return [n["pid"] for n in self.notes() if n["event"] in ("start", "child")]

    @staticmethod
    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def wait_dead(self, pids: List[int], timeout: float = 5.0) -> List[int]:
        """Подождать (осиротевшие потомки подбирает init не мгновенно); → кто ещё жив."""
        end = time.monotonic() + timeout
        left = [p for p in pids if self.alive(p)]
        while left and time.monotonic() < end:
            time.sleep(0.05)
            left = [p for p in left if self.alive(p)]
        return left

    def reap(self) -> None:
        for pid in self.pids():
            if self.alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        for leftover in self.temp_root.glob(f"{C.PROFILE_PREFIX}*"):
            shutil.rmtree(str(leftover), ignore_errors=True)

    def browser(self, mode: str = "ok") -> Path:
        if mode not in self.scripts:
            self.scripts[mode] = make_fake_browser(Path(self.scripts_dir.name), mode, None, name=f"fake-chrome-{mode}")
        return self.scripts[mode]

    def launch(self, mode: str = "ok", **kw: Any) -> C.Chrome:
        kw.setdefault("viewport", (800, 600))
        kw.setdefault("launch_timeout", 20)
        chrome = C.Chrome.launch(self.browser(mode), temp_root=self.temp_root, **kw)
        self.addCleanup(chrome.close)
        return chrome

    def profiles(self) -> List[Path]:
        return sorted(self.temp_root.glob(f"{C.PROFILE_PREFIX}*"))

    def no_process_mentions(self, needle: str) -> bool:
        return not [line for line in process_lines() if needle in line]

class LaunchFake(LaunchBase):
    def test_the_browser_gets_the_fixed_flags_a_temporary_profile_and_its_own_process_group(self):
        chrome = self.launch()
        start = self.notes()[0]
        self.assertEqual(start["argv"], C.launch_args(chrome.profile, 800, 600), "аргументы — ровно launch_args, окно по viewport")
        for flag in ("--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1", "--no-first-run",
                     "--no-default-browser-check", "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=800,600"):
            self.assertIn(flag, start["argv"])
        self.assertEqual(start["argv"][-1], "about:blank")
        self.assertEqual(chrome.profile.parent, self.temp_root)
        self.assertTrue(chrome.profile.name.startswith(C.PROFILE_PREFIX))
        self.assertEqual(chrome.profile.stat().st_mode & 0o777, 0o700, "профиль закрыт для других пользователей")
        self.assertEqual(start["pgid"], start["pid"], "браузер — лидер своей группы процессов")
        self.assertNotEqual(start["pgid"], os.getpgrp())
        self.assertEqual(chrome.proc.pid, start["pid"])

    def test_the_port_comes_from_devtools_active_port_and_the_version_from_the_browser(self):
        chrome = self.launch()
        written = (chrome.profile / "DevToolsActivePort").read_text(encoding="utf-8").split("\n")
        self.assertEqual(chrome.port, int(written[0]))
        self.assertEqual((chrome.product, chrome.version, chrome.user_agent), ("Chrome/120.0.0.0", "120.0.0.0", "Fake/1"))
        self.assertTrue(chrome.alive)
        self.assertEqual(chrome.warnings, [])

    def test_a_port_file_that_is_still_being_written_is_waited_out(self):
        started = time.monotonic()
        chrome = self.launch("slow-port")
        self.assertGreaterEqual(time.monotonic() - started, 0.35, "файл дописывался 0.4 с: клиент дождался второй строки")
        self.assertEqual(chrome.version, "120.0.0.0")
        self.assertTrue(chrome.new_page().evaluate("1+1") == 2, "путь взят целым: сервер отвечает 404 на любой другой")

    def test_downloads_are_switched_off_at_launch(self):
        self.launch()
        reqs = [n for n in self.notes() if n["event"] == "req"]
        self.assertEqual([r["method"] for r in reqs][:2], ["Browser.getVersion", "Browser.setDownloadBehavior"])
        self.assertEqual(reqs[1]["params"], {"behavior": "deny"})

    def test_a_browser_that_cannot_switch_downloads_off_is_used_with_a_warning(self):
        chrome = self.launch("no-download-support")
        self.assertEqual(len(chrome.warnings), 1)
        self.assertIn("скачивания", chrome.warnings[0])

    def test_pages_work_over_the_launched_browser(self):
        chrome = self.launch()
        page = chrome.new_page("data:text/html,<p>x</p>")
        self.assertEqual(page.evaluate("1+1"), 2)
        reqs = [n["method"] for n in self.notes() if n["event"] == "req"]
        self.assertIn("Emulation.setDeviceMetricsOverride", reqs)
        self.assertEqual(chrome.new_page(viewport=(320, 480)).evaluate("1+1"), 2)

    def test_graceful_close_leaves_no_process_no_profile_and_is_repeatable(self):
        chrome = self.launch()
        profile, pids = str(chrome.profile), self.pids()
        report = chrome.close()
        self.assertTrue(report["graceful"], report)
        self.assertTrue(report["profileRemoved"])
        self.assertEqual(self.wait_dead(pids), [])
        self.assertEqual(self.profiles(), [])
        self.assertTrue(self.no_process_mentions(profile), "в списке процессов нет упоминаний профиля")
        boom = AssertionError("после закрытия группу не трогаем: её номер мог достаться чужому")
        with mock.patch.object(P.os, "killpg", side_effect=boom), mock.patch.object(C.P, "kill_group", side_effect=boom), \
                mock.patch.object(C, "_remove_tree", side_effect=boom):
            self.assertIs(chrome.close(), report, "повторный close ничего не делает")
        self.assertFalse(chrome.alive)

    def test_a_browser_that_ignores_the_request_to_quit_is_killed(self):
        chrome = self.launch("ignore-close")
        pids = self.pids()
        with mock.patch.object(C, "CLOSE_GRACE", 0.3):
            report = chrome.close()
        self.assertEqual((report["graceful"], report["killed"], report["profileRemoved"]), (False, True, True))
        self.assertEqual(self.wait_dead(pids), [])
        self.assertEqual(self.profiles(), [])

    def test_the_whole_group_dies_with_the_browser_children_included(self):
        chrome = self.launch("children")
        pids = self.pids()
        self.assertEqual(len(pids), 2, self.notes())
        self.assertTrue(all(self.alive(p) for p in pids))
        chrome.close()
        self.assertEqual(self.wait_dead(pids), [], "и потомок, пережившего выход лидера, убит группой")

    def test_close_without_a_graceful_step_kills_at_once(self):
        chrome = self.launch("ignore-close")
        pids = self.pids()
        started = time.monotonic()
        report = chrome.close(graceful=False)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(report["killed"] and report["profileRemoved"])
        self.assertEqual(self.wait_dead(pids), [])

    def test_a_browser_that_dies_at_start_is_an_error_with_its_last_words_and_leaves_nothing(self):
        with self.assertRaises(C.BrowserLaunchError) as cm:
            C.Chrome.launch(self.browser("crash"), temp_root=self.temp_root, launch_timeout=10)
        self.assertIn("код 7", str(cm.exception))
        self.assertIn("FATAL: boom", str(cm.exception))
        self.assertEqual(self.profiles(), [])
        self.assertEqual(self.wait_dead(self.pids()), [])

    def test_a_browser_that_never_opens_the_port_is_an_error_after_the_deadline_and_is_killed(self):
        started = time.monotonic()
        with self.assertRaises(C.BrowserLaunchError) as cm:
            C.Chrome.launch(self.browser("hang"), temp_root=self.temp_root, launch_timeout=1.0)
        self.assertTrue(1.0 <= time.monotonic() - started < 8)
        self.assertIn("не открыл порт", str(cm.exception))
        self.assertEqual(self.profiles(), [])
        self.assertEqual(self.wait_dead(self.pids()), [])

    def test_a_server_on_the_port_that_is_not_devtools_is_an_error_and_cleaned_up(self):
        with self.assertRaises(C.BrowserLaunchError) as cm:
            C.Chrome.launch(self.browser("bad-ws"), temp_root=self.temp_root, launch_timeout=10)
        self.assertIn("не отвечает по DevTools", str(cm.exception))
        self.assertEqual(self.profiles(), [])
        self.assertEqual(self.wait_dead(self.pids()), [])

    def test_a_missing_or_not_runnable_file_creates_nothing(self):
        plain = self.dir / "plain"
        plain.write_text("x", encoding="utf-8")
        plain.chmod(0o644)
        for path in (self.dir / "nope", plain, self.dir):
            with self.subTest(path=path.name):
                with self.assertRaises(C.BrowserLaunchError):
                    C.Chrome.launch(path, temp_root=self.temp_root)
        self.assertEqual(list(self.temp_root.iterdir()), [])

    def test_a_file_that_is_not_a_program_is_a_launch_error(self):
        junk = self.dir / "junk"
        junk.write_bytes(b"\x00\x01 not a program")
        junk.chmod(0o755)
        with self.assertRaises(C.BrowserLaunchError):
            C.Chrome.launch(junk, temp_root=self.temp_root, launch_timeout=5)
        self.assertEqual(list(self.temp_root.iterdir()), [])

    def test_bad_arguments_are_refused_before_anything_starts(self):
        for bad in ((0, 600), (800,), "800x600"):
            with self.assertRaises(ValueError):
                C.Chrome.launch(self.browser(), viewport=bad, temp_root=self.temp_root)
        self.assertEqual(list(self.temp_root.iterdir()), [])
        self.assertEqual(self.pids(), [])

    def test_a_browser_killed_from_outside_is_a_disconnect_and_close_still_cleans(self):
        chrome = self.launch()
        os.killpg(chrome.proc.pid, signal.SIGKILL)
        started = time.monotonic()
        with self.assertRaises(C.CdpDisconnected):
            chrome.conn.send("Browser.getVersion", timeout=10)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(chrome.alive)
        report = chrome.close()
        self.assertTrue(report["profileRemoved"])
        self.assertEqual(self.profiles(), [])

    def test_the_context_manager_closes(self):
        with C.Chrome.launch(self.browser(), temp_root=self.temp_root, launch_timeout=20) as chrome:
            pids = self.pids()
            self.assertTrue(chrome.alive)
        self.assertEqual(self.wait_dead(pids), [])
        self.assertEqual(self.profiles(), [])

    def test_extra_flags_come_after_the_fixed_ones_and_before_the_blank_page(self):
        chrome = self.launch(extra_args=("--no-sandbox",))
        argv = self.notes()[0]["argv"]
        self.assertEqual(argv[-2:], ["--no-sandbox", "about:blank"])
        self.assertEqual(argv[:len(C.BASE_ARGS)], list(C.BASE_ARGS))
        self.assertTrue(chrome.alive)

    def test_two_browsers_at_once_are_independent(self):
        first = self.launch()
        second = self.launch()
        self.assertNotEqual((first.port, first.profile, first.proc.pid), (second.port, second.profile, second.proc.pid))
        first.close()
        self.assertFalse(first.alive)
        self.assertTrue(second.alive, "закрытие одного не задевает группу другого")
        self.assertEqual(second.new_page().evaluate("1+1"), 2)
        self.assertTrue(second.profile.exists() and not first.profile.exists())

    def test_the_environment_is_passed_through_when_given_and_inherited_otherwise(self):
        self.launch(env=dict(os.environ, FORGEUI_FAKE_MARK="given"))
        self.assertEqual(self.notes()[0]["mark"], "given")
        with mock.patch.dict(os.environ, {"FORGEUI_FAKE_MARK": "inherited"}):
            self.launch()
        self.assertEqual([n["mark"] for n in self.notes() if n["event"] == "start"], ["given", "inherited"])

    def test_the_default_viewport_is_the_one_of_the_spec_and_stderr_is_readable(self):
        chrome = C.Chrome.launch(self.browser(), temp_root=self.temp_root, launch_timeout=20)
        self.addCleanup(chrome.close)
        self.assertEqual(chrome.viewport, (1280, 900))
        self.assertIn("--window-size=1280,900", self.notes()[0]["argv"])
        chrome.new_page()
        sizes = [n["params"] for n in self.notes() if n.get("method") == "Emulation.setDeviceMetricsOverride"]
        self.assertEqual((sizes[0]["width"], sizes[0]["height"]), (1280, 900))
        self.assertIn("DevTools listening on ws://127.0.0.1", chrome.stderr_tail())

    def test_launch_or_skip_gives_a_browser_or_a_named_stop_never_an_exception(self):
        exe = self.browser()
        chrome, stop = C.launch_or_skip({"browserPath": str(exe)}, root=self.dir, temp_root=self.temp_root, launch_timeout=20)
        self.assertIsNone(stop)
        self.addCleanup(chrome.close)
        self.assertTrue(chrome.alive)
        chrome.close()
        gone, stop = C.launch_or_skip({"browserPath": str(self.dir / "nope")}, root=self.dir)
        self.assertIsNone(gone)
        self.assertEqual((stop["skipped"], stop["blocked"]), (True, "no-browser"))
        self.assertIn("файла нет", stop["reason"])
        broken = self.browser("crash")
        gone, stop = C.launch_or_skip({"browserPath": str(broken)}, root=self.dir, temp_root=self.temp_root, launch_timeout=10)
        self.assertIsNone(gone)
        self.assertEqual(stop["blocked"], "no-browser")
        self.assertIn("найден, но не запустился", stop["reason"])
        self.assertIn("FATAL: boom", stop["reason"])
        self.assertEqual(self.profiles(), [])


HOST = '''
import signal, sys, time
sys.path.insert(0, {scripts!r})
from daiver.browser import cdp as C
{before}
chrome = C.Chrome.launch({exe!r}, temp_root={tmp!r}, launch_timeout=20)
print("READY", chrome.proc.pid, chrome.profile, flush=True)
{after}
'''


class HostExit(LaunchBase):
    """Хозяин браузера умирает, не закрыв его: атексит и SIGTERM убирают за ним (SIGKILL — нет, и это записано в cdp.py)."""

    def host(self, before: str = "", after: str = "") -> Tuple[subprocess.Popen, int, str]:
        code = HOST.format(scripts=str(HERE), exe=str(self.browser()), tmp=str(self.temp_root), before=before, after=after)
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stderr.close)
        line = proc.stdout.readline().split()
        self.assertEqual(line[:1], ["READY"], proc.stderr.read() if proc.poll() is not None else "хозяин не сказал READY")
        return proc, int(line[1]), " ".join(line[2:])

    def test_a_normal_exit_without_close_cleans_up_through_atexit(self):
        proc, pid, profile = self.host()
        self.assertEqual(proc.wait(timeout=30), 0)
        self.assertEqual(self.wait_dead([pid] + self.pids()), [])
        self.assertFalse(Path(profile).exists())

    def test_a_browser_nobody_holds_any_more_is_still_cleaned_at_exit(self):
        proc, pid, profile = self.host(after="import gc; chrome = None; gc.collect()")
        self.assertEqual(proc.wait(timeout=30), 0)
        self.assertEqual(self.wait_dead([pid] + self.pids()), [], "реестр живых браузеров держит их сильными ссылками")
        self.assertFalse(Path(profile).exists())

    def test_sigterm_to_the_host_cleans_up_and_the_host_still_dies_of_sigterm(self):
        proc, pid, profile = self.host(after="time.sleep(120)")
        self.assertTrue(self.alive(pid))
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=30), -signal.SIGTERM, "после уборки хозяин умирает так, как умер бы без нас")
        self.assertEqual(self.wait_dead([pid] + self.pids()), [])
        self.assertFalse(Path(profile).exists())

    def test_a_handler_the_host_installed_itself_is_not_taken_over(self):
        before = "signal.signal(signal.SIGTERM, lambda s, f: (print('CUSTOM', flush=True), sys.exit(0)))"
        proc, pid, profile = self.host(before=before, after="time.sleep(120)")
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
        self.assertIn("CUSTOM", out)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.wait_dead([pid] + self.pids()), [], "обычный выход хозяина всё равно убирает браузер через atexit")
        self.assertFalse(Path(profile).exists())

# ══ настоящий Chrome ══════════════════════════════════════════════════════════════════════════════════════════════════════════════

REQUIRE_BROWSER = os.environ.get("FORGEUI_TEST_REQUIRE_BROWSER") == "1"


def lan_address() -> Optional[str]:
    """Нелокальный адрес этой машины (без единого пакета наружу: connect у UDP только выбирает маршрут)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 9))
        addr = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if addr.startswith("127.") or addr == "0.0.0.0" else addr


class RealChrome(unittest.TestCase):
    """Настоящий Chrome/Chromium с машины. Нет его (или FORGEUI_TEST_NO_BROWSER=1) — skip с причиной; найден, но не запустился —
    тоже skip (прогон не должен зависеть от окружения), если не задано FORGEUI_TEST_REQUIRE_BROWSER=1. Один браузер на класс."""

    chrome: C.Chrome

    @classmethod
    def setUpClass(cls):
        if os.environ.get("FORGEUI_TEST_NO_BROWSER") == "1":
            raise unittest.SkipTest("FORGEUI_TEST_NO_BROWSER=1")
        found = BF.find_browser()
        if not found["found"]:
            if REQUIRE_BROWSER:
                raise AssertionError(f"FORGEUI_TEST_REQUIRE_BROWSER=1, а браузера нет: {found['reason']}")
            raise unittest.SkipTest(f"Chrome/Chromium не найден: {found['reason']}")
        cls.found = found
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.files = Path(cls.tmp.name).resolve()
        try:
            cls.chrome = C.Chrome.launch(found["path"], viewport=(800, 600), file_roots=[cls.files], launch_timeout=45)
        except C.BrowserLaunchError as e:
            if REQUIRE_BROWSER:
                raise
            raise unittest.SkipTest(f"Chrome найден ({found['path']}), но не запустился: {e}")
        cls.addClassCleanup(cls.chrome.close)

    def new_page(self, url: str = "about:blank", **kw: Any) -> C.Page:
        page = self.chrome.new_page(url, nav_timeout=20, **kw)
        self.addCleanup(page.close)
        return page

    def test_the_browser_is_a_real_modern_chrome_with_downloads_off(self):
        self.assertRegex(self.chrome.version or "", r"^\d+\.\d+\.\d+\.\d+$")
        self.assertGreaterEqual(BF.major(self.chrome.version), BF.MIN_HEADLESS_NEW)
        self.assertIn("Chrome", self.chrome.product)
        self.assertEqual(self.chrome.warnings, [], "Browser.setDownloadBehavior принят браузером")
        self.assertEqual(BF.browser_version(self.found["path"])["version"], self.chrome.version, "версия из поиска и из браузера совпали")

    def test_a_data_page_evaluates_javascript(self):
        page = self.new_page("data:text/html;charset=utf-8,<title>привет</title><p id=x>мир</p>")
        self.assertEqual(page.evaluate("1+1"), 2)
        self.assertEqual(page.evaluate("document.title"), "привет")
        self.assertEqual(page.evaluate("document.getElementById('x').textContent"), "мир")
        self.assertEqual(page.evaluate("({a: [1, 'b', null]})"), {"a": [1, "b", None]})
        self.assertEqual(page.evaluate("Promise.resolve(41).then(v => v + 1)", await_promise=True), 42)
        self.assertIsNone(page.evaluate("undefined"))
        self.assertEqual(page.evaluate("NaN"), "NaN")

    def test_viewport_scale_and_media_are_pinned_whatever_the_system_says(self):
        page = self.new_page("data:text/html,<p>x</p>")
        self.assertEqual(page.evaluate("[innerWidth, innerHeight, devicePixelRatio]"), [800, 600, 1])
        self.assertEqual(page.evaluate("[matchMedia('(prefers-color-scheme: light)').matches, matchMedia('(prefers-color-scheme: dark)').matches, "
                                       "matchMedia('(prefers-reduced-motion: reduce)').matches]"), [True, False, True])
        page.set_viewport(500, 400)
        self.assertEqual(page.evaluate("[innerWidth, innerHeight, devicePixelRatio]"), [500, 400, 1])
        self.assertEqual(self.new_page("about:blank", viewport=(320, 240)).evaluate("innerWidth"), 320)
        dark = self.new_page("about:blank", color_scheme="dark", reduced_motion=False)
        self.assertEqual(dark.evaluate("[matchMedia('(prefers-color-scheme: dark)').matches, matchMedia('(prefers-reduced-motion: reduce)').matches]"),
                         [True, False])

    def test_scrollbars_are_hidden_and_the_page_has_no_default_margin_surprises(self):
        page = self.new_page("data:text/html,<body style='margin:0'><div style='height:3000px;width:100%'>tall</div>")
        self.assertEqual(page.evaluate("document.documentElement.clientWidth"), 800, "полос прокрутки нет: ширина содержимого = viewport")

    def test_a_screenshot_is_a_png_of_the_viewport_size(self):
        page = self.new_page("data:text/html,<body style='margin:0;background:#f00'>x")
        png = page.screenshot()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (800, 600))
        clip = page.screenshot(clip={"x": 0, "y": 0, "width": 100, "height": 50})
        self.assertEqual(struct.unpack(">II", clip[16:24]), (100, 50))

    def test_dom_and_input_drive_a_real_page(self):
        html = ("<body style='margin:0'><button id=btn style='position:absolute;left:100px;top:50px;width:120px;height:40px' "
                "onclick=\"document.title='clicked'\">go</button><input id=in style='position:absolute;left:100px;top:120px'>")
        page = self.new_page("data:text/html," + html)
        box = page.element_box("#btn")
        self.assertEqual({k: round(v) for k, v in box.items()}, {"x": 100, "y": 50, "width": 120, "height": 40})
        self.assertIsNone(page.element_box("#nope"))
        page.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        self.assertEqual(page.evaluate("document.title"), "clicked")
        page.evaluate("document.getElementById('in').focus()")
        page.insert_text("привет")
        self.assertEqual(page.evaluate("document.getElementById('in').value"), "привет")

    def test_dialogs_do_not_freeze_the_page(self):
        page = self.new_page("data:text/html,<script>alert('hello')</script><p id=x>after</p>")
        self.assertEqual([d["message"] for d in page.dialogs], ["hello"])
        self.assertEqual(page.evaluate("document.getElementById('x').textContent"), "after")
        self.assertIs(page.evaluate("confirm('sure?')"), False)
        self.assertIsNone(page.evaluate("prompt('name?')"))
        self.assertEqual([d["type"] for d in page.dialogs], ["alert", "confirm", "prompt"])

    def test_a_refused_connection_is_a_navigation_error(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        page = self.new_page()
        with self.assertRaises(C.NavigationError) as cm:
            page.navigate(f"http://127.0.0.1:{port}/", timeout=20)
        self.assertIn("ERR_CONNECTION_REFUSED", cm.exception.text)
        self.assertEqual(page.evaluate("1+1"), 2, "вкладка после отказа жива")

    def test_pages_come_from_http_and_from_files_inside_the_named_directory(self):
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                                                                       # noqa: N802 — имя задано stdlib
                body = "<title>по http</title><p id=x>ok</p>".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        page = self.new_page(f"http://127.0.0.1:{httpd.server_address[1]}/orders")
        self.assertEqual(page.evaluate("document.title"), "по http")
        (self.files / "index.html").write_text("<meta charset=utf-8><title>из файла</title>", encoding="utf-8")
        self.assertEqual(self.new_page(f"file://{self.files}/index.html").evaluate("document.title"), "из файла")
        with self.assertRaises(C.UrlRefused):
            page.navigate("file:///etc/hosts")

    def test_a_page_exception_and_a_runaway_script_do_not_hang_the_client(self):
        page = self.new_page("data:text/html,<p>x</p>")
        with self.assertRaises(C.JsException) as cm:
            page.evaluate("nope.x")
        self.assertIn("nope is not defined", str(cm.exception))
        started = time.monotonic()
        with self.assertRaises((C.JsException, C.CdpCommandError)):
            page.evaluate("while (true) {}", timeout=2.5)
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(page.evaluate("1+1"), 2, "вкладка после оборванного скрипта жива")

    def test_the_debug_port_is_open_on_loopback_only(self):
        probe = socket.socket()
        probe.settimeout(3)
        self.assertEqual(probe.connect_ex(("127.0.0.1", self.chrome.port)), 0)
        probe.close()
        lan = lan_address()
        if lan is None:
            self.skipTest("у машины нет нелокального адреса — проверять нечем")
        outside = socket.socket()
        outside.settimeout(3)
        self.assertNotEqual(outside.connect_ex((lan, self.chrome.port)), 0, f"порт отладки открыт на {lan}!")
        outside.close()

    def test_the_default_profile_of_the_user_is_not_touched(self):
        self.assertTrue(str(self.chrome.profile).startswith(tempfile.gettempdir()) or str(self.chrome.profile).startswith("/private"))
        self.assertIn(f"--user-data-dir={self.chrome.profile}", " ".join(self.chrome.proc.args))
        self.assertEqual(self.chrome.profile.stat().st_mode & 0o777, 0o700 if POSIX else self.chrome.profile.stat().st_mode & 0o777)

    def test_launch_or_skip_finds_and_starts_the_real_browser(self):
        chrome, stop = C.launch_or_skip(launch_timeout=45)
        self.assertIsNone(stop)
        with chrome:
            self.assertEqual(chrome.new_page("data:text/html,<p>x</p>").evaluate("1+1"), 2)


class RealChromeLifecycle(unittest.TestCase):
    """Каждый тест — свой браузер: уборка и обрывы. Правила пропуска те же, что у RealChrome."""

    def browser_path(self) -> str:
        if os.environ.get("FORGEUI_TEST_NO_BROWSER") == "1":
            self.skipTest("FORGEUI_TEST_NO_BROWSER=1")
        found = BF.find_browser()
        if not found["found"]:
            if REQUIRE_BROWSER:
                self.fail(found["reason"])
            self.skipTest(f"Chrome/Chromium не найден: {found['reason']}")
        return found["path"]

    def launch(self) -> C.Chrome:
        try:
            chrome = C.Chrome.launch(self.browser_path(), launch_timeout=45)
        except C.BrowserLaunchError as e:
            if REQUIRE_BROWSER:
                raise
            self.skipTest(f"Chrome найден, но не запустился: {e}")
        self.addCleanup(chrome.close)
        return chrome

    def test_close_leaves_no_process_no_profile_and_no_listening_port(self):
        chrome = self.launch()
        profile, pid, port = str(chrome.profile), chrome.proc.pid, chrome.port
        page = chrome.new_page("data:text/html,<p>x</p>")
        self.assertEqual(page.evaluate("1+1"), 2)
        report = chrome.close()
        self.assertTrue(report["profileRemoved"], report)
        self.assertFalse(Path(profile).exists())
        self.assertIsNotNone(chrome.proc.poll(), "лидер завершён и забран")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and [l for l in process_lines() if profile in l]:
            time.sleep(0.1)
        self.assertEqual([l[:120] for l in process_lines() if profile in l], [], "хвостов процессов нет (в том числе рендереров)")
        probe = socket.socket()
        probe.settimeout(3)
        self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0, "порт отладки закрыт")
        probe.close()

    @unittest.skipUnless(POSIX, "SIGKILL группы — POSIX")
    def test_a_browser_killed_from_outside_is_a_disconnect_not_a_hang(self):
        chrome = self.launch()
        page = chrome.new_page("data:text/html,<p>x</p>")
        os.killpg(chrome.proc.pid, signal.SIGKILL)
        started = time.monotonic()
        with self.assertRaises(C.CdpDisconnected):
            page.evaluate("1+1", timeout=15)
        self.assertLess(time.monotonic() - started, 8)
        self.assertFalse(chrome.alive)
        profile = chrome.profile
        chrome.close()
        self.assertFalse(profile.exists())

    def test_nothing_is_left_when_the_launch_is_abandoned_by_an_exception(self):
        path = self.browser_path()
        before = set(Path(tempfile.gettempdir()).glob(f"{C.PROFILE_PREFIX}*"))
        with mock.patch.object(C, "Connection", side_effect=RuntimeError("сбой после запуска")):
            with self.assertRaises(RuntimeError):
                C.Chrome.launch(path, launch_timeout=45)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and set(Path(tempfile.gettempdir()).glob(f"{C.PROFILE_PREFIX}*")) - before:
            time.sleep(0.1)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob(f"{C.PROFILE_PREFIX}*")) - before, set(), "профиль удалён")

# ══ процессы в группе ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(POSIX, "группы процессов и сигналы — POSIX (Windows: taskkill, не проверено)")
class ProcGroup(unittest.TestCase):
    SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]

    def spawn(self, cmd: Optional[List[str]] = None) -> Any:
        proc = P.popen_group(cmd or self.SLEEPER, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: P.kill_group(proc, term_grace=0.1, kill_wait=1))
        return proc

    @staticmethod
    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_the_process_leads_a_group_of_its_own(self):
        proc = self.spawn()
        self.assertEqual(os.getpgid(proc.pid), proc.pid)
        self.assertNotEqual(proc.pid, os.getpgrp())
        self.assertTrue(P.group_alive(proc))

    def test_the_whole_group_dies_children_included_and_the_leader_is_reaped(self):
        marker = Path(tempfile.mkdtemp()) / "child.pid"
        self.addCleanup(shutil.rmtree, str(marker.parent), True)
        code = ("import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
                f"open({str(marker)!r}, 'w').write(str(c.pid)); time.sleep(120)")
        proc = self.spawn([sys.executable, "-c", code])
        deadline = time.time() + 10
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.05)
        child = int(marker.read_text(encoding="utf-8"))
        self.assertTrue(self.alive(child))
        self.assertTrue(P.kill_group(proc, term_grace=2))
        self.assertIsNotNone(proc.returncode, "лидер забран (нет зомби)")
        end = time.time() + 5
        while self.alive(child) and time.time() < end:
            time.sleep(0.05)
        self.assertFalse(self.alive(child), "потомок убит вместе с группой")
        self.assertFalse(P.group_alive(proc))

    def test_a_process_that_ignores_sigterm_gets_sigkill(self):
        code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
        proc = self.spawn([sys.executable, "-c", code])
        time.sleep(0.4)                                         # обработчик должен успеть встать
        started = time.monotonic()
        self.assertTrue(P.kill_group(proc, term_grace=0.3, kill_wait=5))
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_a_second_call_after_a_successful_cleanup_touches_nothing(self):
        proc = self.spawn()
        self.assertTrue(P.kill_group(proc, term_grace=1))
        with mock.patch.object(P.os, "killpg", side_effect=AssertionError("номер группы мог достаться чужому процессу")):
            self.assertFalse(P.kill_group(proc))
            self.assertFalse(P.kill_group(proc))

    def test_an_exited_process_with_no_group_left_is_a_quiet_no_op(self):
        proc = self.spawn([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        self.assertFalse(P.kill_group(proc))

    def test_the_group_of_the_caller_is_never_signalled(self):
        stranger = types.SimpleNamespace(pid=os.getpgrp(), poll=lambda: None, wait=lambda timeout=None: 0)      # «лидер» нашей же группы
        with mock.patch.object(P.os, "killpg", side_effect=AssertionError("сигнал своей группе")):
            with self.assertRaises(RuntimeError):
                P.kill_group(stranger)


if __name__ == "__main__":
    unittest.main()
