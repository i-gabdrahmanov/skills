"""ws.py — минимальный клиент WebSocket (RFC 6455) на stdlib для протокола DevTools (спека §14.2; задача 4.1).

Что умеет — ровно то, что нужно разговору с локальным браузером, и умеет строго:
  • рукопожатие: HTTP Upgrade с `Sec-WebSocket-Key`, проверка 101, `Upgrade`, `Connection` и `Sec-WebSocket-Accept` (ответ сервера, не
    прошедший проверку, — отказ, а не «ну вроде работает»); заголовок `Origin` НЕ шлётся — Chrome отвергает WebSocket с чужим
    Origin (с версии 111), а клиенту без браузера он не нужен; расширения и подпротоколы не запрашиваются, поэтому ответ с ними —
    нарушение; байты кадров, пришедшие в одном сегменте с ответом, не теряются;
  • кадры: клиент маскирует каждый кадр свежим ключом из `os.urandom`, сервер обязан слать немаскированные (маскированный кадр
    сервера — нарушение §5.1); длины 7/16/64 бита; RSV-биты и неизвестные opcode — нарушение (расширений нет); управляющие кадры не
    длиннее 125 байт и не фрагментируются; старший бит 64-битной длины должен быть 0; предел размера проверяется по ЗАГОЛОВКУ,
    до приёма тела: бредовая длина не съедает память;
  • фрагментация: сообщение из кадров (`fin=0` … `CONT` … `fin=1`) собирается, управляющие кадры между фрагментами обрабатываются
    сразу; текст проверяется как UTF-8 (иначе закрытие 1007); новый data-кадр посреди сообщения и продолжение без начала —
    нарушение; отправка может резать сообщение на кадры (`fragment_size`);
  • ping → немедленный pong с той же нагрузкой, pong глотается; close — эхо кода и закрытие, затем `WsClosed` с кодом и причиной;
    при нарушении клиент шлёт close 1002/1007/1009 и закрывает сокет;
  • таймауты: любой `recv` с дедлайном. Неполный кадр после таймаута НЕ теряется — следующий `recv` продолжит с того же места
    (разбор идёт по буферу, а не по блокирующему чтению). EOF посреди кадра — `WsClosed`, а не таймаут.

Безопасность: подключаться можно ТОЛЬКО к петлевому адресу (127.0.0.1, ::1, localhost) и по `ws://`; после соединения сверяется
адрес пира. TLS не нужен: порт отладки браузера открыт на 127.0.0.1 открытым текстом. Потоки не поддерживаются — клиент
синхронный, один владелец.

Проверено: байтовые примеры RFC 6455 §5.7 (`test_cdp.RfcVectors`), фейковый сервер с независимым кодировщиком (рукопожатие, кадры,
фрагменты, ping/pong, close, нарушения, обрывы), настоящий Chrome 155 (macOS). Не проверено: Windows, wss://, сервера с расширениями.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import socket
import struct
import time
from typing import Dict, List, NamedTuple, Optional, Union
from urllib.parse import urlsplit

from . import BrowserError

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
_KNOWN_OPS = (OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG)
MAX_CONTROL_PAYLOAD = 125
CLOSE_NORMAL, CLOSE_PROTOCOL, CLOSE_INVALID_DATA, CLOSE_TOO_BIG = 1000, 1002, 1007, 1009
DEFAULT_MAX_MESSAGE = 128 * 1024 * 1024      # скриншоты и снапшоты DOM идут мегабайтами; бредовое — отказ по заголовку
MAX_HEAD_BYTES = 64 * 1024
RECV_CHUNK = 256 * 1024
SEND_TIMEOUT = 30.0
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class WsError(BrowserError):
    """Сбой WebSocket."""


class WsHandshakeError(WsError):
    """Соединения нет или сервер не принял/неверно принял Upgrade."""


class WsProtocolError(WsError):
    """Нарушение RFC 6455 пиром; `code` — код закрытия, которым клиент ответил."""

    def __init__(self, message: str, code: int = CLOSE_PROTOCOL):
        super().__init__(message)
        self.code = code


class WsTimeout(WsError):
    """Дедлайн вышел; соединение цело, неполный кадр сохранён."""


class WsClosed(WsError):
    """Соединение закрыто (пиром, обрывом или нами); `code`/`reason` — из кадра close, если он был."""

    def __init__(self, message: str, code: Optional[int] = None, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.reason = reason


class Frame(NamedTuple):
    fin: bool
    opcode: int
    payload: bytes


def accept_key(key: str) -> str:
    """`Sec-WebSocket-Accept` для ключа клиента (RFC 6455 §4.2.2)."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def apply_mask(data: bytes, key: bytes) -> bytes:
    """XOR нагрузки с 4-байтным ключом (RFC 6455 §5.3); операция обратима. Через целые числа: мегабайты за миллисекунды."""
    n = len(data)
    if not n:
        return b""
    if len(key) != 4:
        raise ValueError("ключ маски — ровно 4 байта")
    stream = (key * ((n + 3) // 4))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(n, "big")


def encode_frame(opcode: int, payload: bytes = b"", *, fin: bool = True, mask: bool = True, key: Optional[bytes] = None) -> bytes:
    """Один кадр. Клиентский — `mask=True` (по умолчанию), ключ свежий из `os.urandom`; `key` — для воспроизводимых тестов."""
    if opcode not in _KNOWN_OPS:
        raise ValueError(f"неизвестный opcode {opcode:#x}")
    if opcode >= 0x8 and (not fin or len(payload) > MAX_CONTROL_PAYLOAD):
        raise ValueError("управляющий кадр: fin=1 и нагрузка не длиннее 125 байт")
    n = len(payload)
    first = (0x80 if fin else 0) | opcode
    flag = 0x80 if mask else 0
    if n < 126:
        head = struct.pack("!BB", first, flag | n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", first, flag | 126, n)
    else:
        head = struct.pack("!BBQ", first, flag | 127, n)
    if not mask:
        return head + payload
    key = os.urandom(4) if key is None else key
    return head + key + apply_mask(payload, key)


class FrameDecoder:
    """Инкрементальный разборщик кадров: `feed(байты)`, затем `next_frame()` до None. Ничего не теряет между вызовами."""

    def __init__(self, *, max_payload: int = DEFAULT_MAX_MESSAGE, expect_masked: bool = False):
        self._buf = bytearray()
        self.max_payload = max_payload
        self.expect_masked = expect_masked

    def feed(self, data: bytes) -> None:
        self._buf += data

    @property
    def buffered(self) -> int:
        return len(self._buf)

    def next_frame(self) -> Optional[Frame]:
        buf = self._buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        if b0 & 0x70:
            raise WsProtocolError("RSV-биты установлены, а расширения не согласовывались")
        fin, opcode = bool(b0 & 0x80), b0 & 0x0F
        if opcode not in _KNOWN_OPS:
            raise WsProtocolError(f"неизвестный opcode {opcode:#x}")
        masked = bool(b1 & 0x80)
        if masked != self.expect_masked:
            raise WsProtocolError("кадр сервера замаскирован" if masked else "кадр клиента не замаскирован")
        length, pos = b1 & 0x7F, 2
        if length == 126:
            if len(buf) < 4:
                return None
            length, pos = struct.unpack_from("!H", buf, 2)[0], 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length, pos = struct.unpack_from("!Q", buf, 2)[0], 10
            if length >> 63:
                raise WsProtocolError("старший бит 64-битной длины кадра установлен")
        if opcode >= 0x8:
            if not fin:
                raise WsProtocolError("управляющий кадр фрагментирован")
            if length > MAX_CONTROL_PAYLOAD:
                raise WsProtocolError("управляющий кадр длиннее 125 байт")
        if length > self.max_payload:
            raise WsProtocolError(f"кадр в {length} байт больше предела {self.max_payload}", CLOSE_TOO_BIG)
        key = None
        if masked:
            if len(buf) < pos + 4:
                return None
            key, pos = bytes(buf[pos:pos + 4]), pos + 4
        end = pos + length
        if len(buf) < end:
            return None
        payload = bytes(buf[pos:end])
        del buf[:end]
        return Frame(fin, opcode, apply_mask(payload, key) if key else payload)


def _valid_close_code(code: int) -> bool:
    return 1000 <= code <= 1003 or 1007 <= code <= 1014 or 3000 <= code <= 4999


class WebSocket:
    """Клиентское соединение. Создаётся `WebSocket.connect(url)`; `recv` отдаёт сообщения целиком (str для текста, bytes для
    двоичных), `send_text` шлёт текст. Не потокобезопасно."""

    def __init__(self, sock: socket.socket, *, max_message: int = DEFAULT_MAX_MESSAGE):
        self._sock = sock
        self._dec = FrameDecoder(max_payload=max_message)
        self._max_message = max_message
        self._frag_op: Optional[int] = None
        self._frag = bytearray()
        self._closed = False
        self._close_sent = False
        self.close_code: Optional[int] = None
        self.close_reason = ""
        self.pongs: List[bytes] = []

    # ── соединение ───────────────────────────────────────────────────────────

    @classmethod
    def connect(cls, url: str, *, timeout: float = 10.0, max_message: int = DEFAULT_MAX_MESSAGE) -> "WebSocket":
        """Подключиться к `ws://127.0.0.1:порт/путь` и пройти рукопожатие за `timeout` секунд (всего)."""
        try:
            parts = urlsplit(url)
        except ValueError as e:
            raise WsHandshakeError(f"{url}: адрес не разобрать ({e})")
        if parts.scheme != "ws":
            raise WsHandshakeError(f"{url}: поддерживается только ws:// (порт отладки браузера открыт без TLS на 127.0.0.1)")
        host = parts.hostname or ""
        if host not in LOOPBACK_HOSTS:
            raise WsHandshakeError(f"{url}: подключаться можно только к локальному адресу (127.0.0.1, ::1, localhost)")
        try:
            port = parts.port or 80
        except ValueError as e:
            raise WsHandshakeError(f"{url}: неверный порт ({e})")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        deadline = time.monotonic() + timeout
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise WsHandshakeError(f"нет соединения с {host}:{port}: {e.strerror or e}")
        try:
            peer = sock.getpeername()[0]
            if not ipaddress.ip_address(peer.split("%")[0]).is_loopback:
                raise WsHandshakeError(f"{host} разрешился в нелокальный адрес {peer} — соединение закрыто")
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            ws = cls(sock, max_message=max_message)
            ws._handshake(f"[{host}]" if ":" in host else host, port, path, deadline)
        except BaseException:
            sock.close()
            raise
        return ws

    def _handshake(self, host: str, port: int, path: str, deadline: float) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode("ascii")
        try:
            self._send_raw(request, max(0.001, deadline - time.monotonic()))
            head = bytearray()
            while True:
                end = head.find(b"\r\n\r\n")
                if end >= 0:
                    break
                if len(head) > MAX_HEAD_BYTES:
                    raise WsHandshakeError("ответ на рукопожатие длиннее 64 КБ")
                head += self._recv_some(deadline)
        except WsTimeout:
            self._shutdown()
            raise WsHandshakeError("сервер не ответил на рукопожатие вовремя")
        except WsClosed as e:
            raise WsHandshakeError(f"сервер закрыл соединение при рукопожатии: {e}")
        rest = bytes(head[end + 4:])
        lines = bytes(head[:end]).decode("latin-1").split("\r\n")
        m = re.match(r"HTTP/1\.[01] (\d{3})\b", lines[0])
        headers: Dict[str, str] = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if not sep:
                self._shutdown()
                raise WsHandshakeError(f"строка заголовка без «:»: {line[:60]!r}")
            lower = name.strip().lower()
            headers[lower] = f"{headers[lower]}, {value.strip()}" if lower in headers else value.strip()
        problem = None
        if not m:
            problem = f"ответ не похож на HTTP: {lines[0][:60]!r}"
        elif m.group(1) != "101":
            problem = f"сервер не принял Upgrade: {lines[0][:80]}"
        elif headers.get("upgrade", "").lower() != "websocket":
            problem = "в ответе нет «Upgrade: websocket»"
        elif "upgrade" not in [t.strip().lower() for t in headers.get("connection", "").split(",")]:
            problem = "в ответе нет «Connection: Upgrade»"
        elif headers.get("sec-websocket-accept") != accept_key(key):
            problem = "неверный Sec-WebSocket-Accept: это не тот сервер или ответ подменён"
        elif "sec-websocket-extensions" in headers or "sec-websocket-protocol" in headers:
            problem = "сервер согласовал расширение или подпротокол, которых клиент не просил"
        if problem:
            self._shutdown()
            raise WsHandshakeError(problem)
        self._dec.feed(rest)

    # ── ввод-вывод ───────────────────────────────────────────────────────────

    @property
    def closed(self) -> bool:
        return self._closed

    def _shutdown(self) -> None:
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def _send_raw(self, data: bytes, timeout: Optional[float] = None) -> None:
        if self._closed:
            raise WsClosed("соединение уже закрыто", self.close_code, self.close_reason)
        wait = SEND_TIMEOUT if timeout is None else timeout
        try:
            self._sock.settimeout(wait)
            self._sock.sendall(data)
        except socket.timeout:
            self._shutdown()          # часть данных могла уйти: поток испорчен, продолжать нельзя
            raise WsTimeout(f"отправка не уложилась в {wait:g} с — соединение закрыто")
        except OSError as e:
            self._shutdown()
            raise WsClosed(f"соединение разорвано при отправке: {e.strerror or e}")

    def _recv_some(self, deadline: Optional[float]) -> bytes:
        """Один `recv` до дедлайна: байты | WsTimeout (соединение цело) | WsClosed (EOF, обрыв)."""
        if self._closed:
            raise WsClosed("соединение уже закрыто", self.close_code, self.close_reason)
        wait = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            self._sock.settimeout(wait)
            chunk = self._sock.recv(RECV_CHUNK)
        except (socket.timeout, BlockingIOError):
            raise WsTimeout("данных нет")
        except OSError as e:
            self._shutdown()
            raise WsClosed(f"соединение разорвано: {e.strerror or e}")
        if not chunk:
            self._shutdown()
            midway = self._dec.buffered or self._frag_op is not None
            raise WsClosed("соединение закрыто посреди сообщения" if midway else "соединение закрыто сервером")
        return chunk

    def _fail(self, err: WsProtocolError) -> None:
        """Нарушение пира: сообщить кодом закрытия (если ещё можно), закрыть сокет. Исключение поднимает вызывающий."""
        if not self._closed and not self._close_sent:
            try:
                self._send_close(err.code, "", 1.0)
            except WsError:
                pass
        self._shutdown()

    def _send_close(self, code: int, reason: str, timeout: Optional[float]) -> None:
        text = reason.encode("utf-8")[:123].decode("utf-8", "ignore").encode("utf-8")      # не режем символ посередине
        body = struct.pack("!H", code) + text
        self._close_sent = True
        self._send_raw(encode_frame(OP_CLOSE, body), timeout)

    # ── приём ────────────────────────────────────────────────────────────────

    def recv(self, timeout: Optional[float] = None) -> Union[str, bytes]:
        """Следующее сообщение целиком: str (текст) или bytes (двоичное). Ping отвечается, pong глотается.

        WsTimeout — за `timeout` секунд сообщения нет (неполный кадр сохранён, можно звать снова); WsClosed — закрыто; WsProtocolError —
        пир нарушил RFC (клиент ответил кодом закрытия и закрыл сокет)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._closed:
                raise WsClosed("соединение закрыто", self.close_code, self.close_reason)
            try:
                frame = self._dec.next_frame()
            except WsProtocolError as e:
                self._fail(e)
                raise
            if frame is None:
                self._dec.feed(self._recv_some(deadline))
                continue
            msg = self._on_frame(frame)
            if msg is not None:
                return msg

    def _on_frame(self, frame: Frame) -> Optional[Union[str, bytes]]:
        op = frame.opcode
        if op == OP_PING:
            self._send_raw(encode_frame(OP_PONG, frame.payload))
            return None
        if op == OP_PONG:
            self.pongs = self.pongs[-15:] + [frame.payload]
            return None
        if op == OP_CLOSE:
            self._on_close(frame.payload)          # всегда бросает: WsClosed или WsProtocolError
        if op == OP_CONT:
            if self._frag_op is None:
                self._violation("продолжение сообщения без начала")
            self._append(frame.payload)
            if not frame.fin:
                return None
            kind, data = self._frag_op, bytes(self._frag)
            self._frag_op, self._frag = None, bytearray()
            return self._finish(kind, data)
        if self._frag_op is not None:
            self._violation("новое сообщение посреди фрагментированного")
        if frame.fin:
            return self._finish(op, frame.payload)
        self._frag_op, self._frag = op, bytearray()
        self._append(frame.payload)
        return None

    def _append(self, payload: bytes) -> None:
        if len(self._frag) + len(payload) > self._max_message:
            self._violation(f"сообщение больше предела {self._max_message} байт", CLOSE_TOO_BIG)
        self._frag += payload

    def _finish(self, kind: int, data: bytes) -> Union[str, bytes]:
        if kind == OP_BINARY:
            return data
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            err = WsProtocolError("текстовое сообщение — не UTF-8", CLOSE_INVALID_DATA)
        self._fail(err)
        raise err

    def _violation(self, message: str, code: int = CLOSE_PROTOCOL) -> None:
        err = WsProtocolError(message, code)
        self._fail(err)
        raise err

    def _on_close(self, payload: bytes) -> None:
        code: Optional[int] = None
        reason = ""
        if len(payload) == 1:
            self._violation("тело кадра close — 1 байт")
        if len(payload) >= 2:
            code = struct.unpack("!H", payload[:2])[0]
            if not _valid_close_code(code):
                self._violation(f"недопустимый код закрытия {code}")
            try:
                reason = payload[2:].decode("utf-8")
            except UnicodeDecodeError:
                self._violation("причина закрытия — не UTF-8", CLOSE_INVALID_DATA)
        self.close_code, self.close_reason = code, reason
        if not self._close_sent:
            try:
                self._send_close(code if code is not None else CLOSE_NORMAL, "", 1.0)
            except WsError:
                pass
        self._shutdown()
        raise WsClosed(f"сервер закрыл соединение (код {code}{': ' + reason if reason else ''})", code, reason)

    # ── отправка и закрытие ──────────────────────────────────────────────────

    def send_text(self, text: str, *, timeout: Optional[float] = None, fragment_size: Optional[int] = None) -> None:
        """Отправить текстовое сообщение (маскированными кадрами); `fragment_size` — резать на кадры такого размера."""
        data = text.encode("utf-8")
        if fragment_size is not None and fragment_size < 1:
            raise ValueError("fragment_size >= 1")
        if not fragment_size or len(data) <= fragment_size:
            self._send_raw(encode_frame(OP_TEXT, data), timeout)
            return
        pieces = [data[i:i + fragment_size] for i in range(0, len(data), fragment_size)]
        frames = [encode_frame(OP_TEXT if i == 0 else OP_CONT, piece, fin=i == len(pieces) - 1) for i, piece in enumerate(pieces)]
        self._send_raw(b"".join(frames), timeout)

    def ping(self, payload: bytes = b"", *, timeout: Optional[float] = None) -> None:
        self._send_raw(encode_frame(OP_PING, payload), timeout)

    def close(self, code: int = CLOSE_NORMAL, reason: str = "", timeout: float = 2.0) -> None:
        """Закрыть по RFC: послать close и подождать close пира не дольше `timeout`, затем закрыть сокет. Идемпотентно, не бросает."""
        if self._closed:
            return
        deadline = time.monotonic() + timeout
        try:
            if not self._close_sent:
                self._send_close(code, reason, max(0.001, timeout))
            while True:
                try:
                    frame = self._dec.next_frame()
                except WsProtocolError:
                    break
                if frame is None:
                    try:
                        self._dec.feed(self._recv_some(deadline))
                    except WsError:
                        break
                    continue
                if frame.opcode == OP_CLOSE:
                    if len(frame.payload) >= 2:
                        self.close_code = struct.unpack("!H", frame.payload[:2])[0]
                    break
        except WsError:
            pass
        finally:
            self._shutdown()

    def abort(self) -> None:
        """Закрыть сокет сразу, без рукопожатия закрытия (уборка при смерти хозяина: ждать пира некогда). Идемпотентно."""
        self._shutdown()

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

