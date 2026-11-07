"""devserver.py — dev-сервер автономного прогона: запуск ЗАПИСАННОЙ команды, ожидание адреса, гарантированное завершение (спека §14.2, §14.12; задача 4.9; INV-D4, D6).

Правило одно: interactive НИКОГДА не стартует сервер сам и не угадывает адрес — `start` в режиме не `autonomous` отказывает до запуска любого процесса.
Автономный прогон запускает `implementation.devCommand` манифеста — это коммитнутая инструкция команды, данность, а не догадка. Сервер стартует в СВОЕЙ группе
процессов (`proc.popen_group`: `npm` → `node` → воркеры), адрес опрашивается до ответа с таймаутом, в конце группа убирается целиком (`proc.kill_group`:
SIGTERM → SIGKILL, повтор — пустая операция). Если адрес уже отвечает — это чужой запущенный сервер: он не стартует второй раз и не глушится
(`owned: false`). Не поднялся, умер раньше времени или команды нет — именованный останов `blocked: implementation-unreachable` с тем, кто что даёт; хвост
вывода сервера — в ответе, чтобы человек видел причину.

Команда исполняется оболочкой (`sh -c` / `cmd /c`): запись манифеста — одна строка вроде `npm run dev -- --port 5173` или `PORT=5173 npm start`; оболочка нужна
ради переменных окружения и `&&`. Группа процессов гарантирует, что оболочка и все потомки уйдут вместе.

Не проверено данными (боевого dev-сервера нет, проверено на `python -m http.server`): время старта настоящего `vite`/`webpack-dev-server`, серверы, которые
отвечают на адрес раньше готовности сборки (первая загрузка вернёт заглушку — ответственность `waitMs` захвата), Windows (`cmd /c`, `taskkill /T`).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import proc as P

READY_TIMEOUT = 60.0
POLL_STEP = 0.25
PROBE_TIMEOUT = 2.0
TAIL_BYTES = 2000


class DevServerError(ValueError):
    """Сервер запустить нельзя: режим не автономный, нет команды, адрес не http(s)."""


def reachable(url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """Отвечает ли адрес по HTTP. Любой ответ (и 404, и 500) — сервер жив; отказ соединения, таймаут, не-HTTP — нет. Прокси не используются."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(urllib.parse.quote(url, safe=":/?&=#%+~@!$'()*,;[]"), timeout=timeout).close()      # путь не из ASCII — тоже адрес
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def blocked_unreachable(url: Optional[str], why: str, dev_command: Optional[str], tail: str = "") -> Dict[str, Any]:
    """Стандартный останов «страница реализации недоступна» (INV-D2): кто и что должен дать."""
    out: Dict[str, Any] = {"blocked": "implementation-unreachable", "url": url, "reason": why,
                           "supply": ("человек: запустить приложение по этому адресу либо записать рабочую `implementation.devCommand` "
                                      "(daiver_track_manifest) — автономный прогон запускает только записанную команду"),
                           "nextStep": "остановись и скажи пользователю причину дословно; команду для запуска не придумывай"}
    if dev_command:
        out["devCommand"] = dev_command
    if tail:
        out["serverOutput"] = tail
    return out


class DevServer:
    """Запущенный (или уже работавший) сервер; закрывается `with` / `stop()`."""

    def __init__(self, url: str, proc: Optional["subprocess.Popen[bytes]"], log: Optional[Path], owned: bool):
        self.url, self.proc, self.log, self.owned = url, proc, log, owned
        self.stopped = False

    def tail(self) -> str:
        if self.log is None:
            return ""
        try:
            data = self.log.read_bytes()[-TAIL_BYTES:]
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace").strip()

    def stop(self) -> bool:
        """Убрать группу процессов (только своего сервера); повтор — пустая операция. → True, если пришлось слать сигнал."""
        if self.stopped:
            return False
        self.stopped = True
        sent = False
        if self.owned and self.proc is not None:
            sent = P.kill_group(self.proc)
        if self.log is not None:
            try:
                self.log.unlink()
            except OSError:
                pass
        return sent

    def __enter__(self) -> "DevServer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def _shell(command: str) -> List[str]:
    return ["/bin/sh", "-c", command] if os.name == "posix" else ["cmd", "/c", command]          # pragma: no cover — Windows не проверено


def start(command: Optional[str], cwd: Any, url: str, mode: str, ready_timeout: float = READY_TIMEOUT, poll: float = POLL_STEP) -> DevServer:
    """Запустить записанную команду и дождаться адреса. Исключение — только отказ ДО запуска (режим, команда, адрес): всё, что случилось после,
    возвращает `DevServerFailed` с готовым ответом `blocked`."""
    if mode != "autonomous":
        raise DevServerError("interactive не стартует dev-сервер сам никогда (INV-D4): адрес спрашивают у человека один раз и записывают в манифест")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise DevServerError(f"адрес сервера: полный http(s)-адрес, получено {url!r}")
    if reachable(url):
        return DevServer(url, None, None, owned=False)                  # чужой запущенный сервер: не трогаем ни при старте, ни при уборке
    if not isinstance(command, str) or not command.strip() or "\n" in command:
        raise DevServerFailed(blocked_unreachable(url, "адрес не отвечает, а implementation.devCommand не записан", None))
    fd, name = tempfile.mkstemp(prefix="forge-devserver-", suffix=".log")
    log = Path(name)
    out = os.fdopen(fd, "wb")
    try:
        proc = P.popen_group(_shell(command), cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as e:
        raise DevServerFailed(blocked_unreachable(url, f"команда не запустилась: {e.strerror or e}", command))
    finally:
        out.close()
    server = DevServer(url, proc, log, owned=True)
    deadline = time.monotonic() + ready_timeout
    while True:
        if reachable(url):
            return server
        code = proc.poll()
        if code is not None:
            tail = server.tail()
            server.stop()
            raise DevServerFailed(blocked_unreachable(url, f"сервер завершился с кодом {code}, не дождавшись ответа адреса", command, tail))
        if time.monotonic() >= deadline:
            tail = server.tail()
            server.stop()
            raise DevServerFailed(blocked_unreachable(url, f"адрес не ответил за {ready_timeout:g} с: сервер не поднялся", command, tail))
        time.sleep(poll)


class DevServerFailed(Exception):
    """Сервер не поднялся: `.blocked` — готовый ответ `blocked: implementation-unreachable` (процесс уже убран)."""

    def __init__(self, blocked: Dict[str, Any]):
        super().__init__(blocked["reason"])
        self.blocked = blocked
