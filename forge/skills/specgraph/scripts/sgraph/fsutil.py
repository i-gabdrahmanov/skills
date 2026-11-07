"""fsutil.py — блокировка проекта и атомарная запись (параллельные субагенты).

Субагенты работают одновременно: модель думает параллельно, а их вызовы инструментов могут прийти в один
процесс MCP-сервера или в разные (как рантайм поднимает MCP для субагентов, не проверено). Состояние
движка — JSON «прочитать → изменить → записать»: два одновременных `graph_link_put` без блокировки теряли
бы запись друг друга. Поэтому:

  • `project_lock(root)` — межпроцессная блокировка `.spec-graph/.lock` (flock на POSIX, msvcrt.locking на
    Windows), повторно входимая в пределах потока. Диспетчер `tools.call` держит её на время КАЖДОГО вызова
    инструмента: вызовы короткие (миллисекунды–секунды), долгая часть — рассуждение модели — идёт вне её;
  • `write_text` / `write_bytes` — запись через временный файл рядом и `os.replace`: читатель (гейт, хук,
    обход, Obsidian) видит старый файл или новый, но не полузаписанный.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

LOCK_REL = ".spec-graph/.lock"
TIMEOUT_S = float(os.environ.get("SGRAPH_LOCK_TIMEOUT_S") or 600.0)
_POLL_S = 0.02
_held = threading.local()


class LockTimeout(Exception):
    pass


def _try_lock(f) -> bool:
    if os.name == "nt":  # pragma: no cover — Windows
        import msvcrt
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    if os.name == "nt":  # pragma: no cover
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def project_lock(root: Union[str, Path], timeout: Optional[float] = None) -> Iterator[None]:
    timeout = TIMEOUT_S if timeout is None else timeout
    key = str(Path(root).resolve())
    depth = getattr(_held, "depth", None)
    if depth is None:
        depth = _held.depth = {}
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    p = Path(key) / LOCK_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(f):
            if time.monotonic() >= deadline:
                raise LockTimeout(f"проект занят дольше {int(timeout)} с (другой вызов graph_* держит {LOCK_REL})")
            time.sleep(_POLL_S)
        depth[key] = 1
        try:
            yield
        finally:
            depth[key] = 0
            _unlock(f)
    finally:
        f.close()


def _mode_for(p: Path) -> int:
    import stat
    try:
        return stat.S_IMODE(os.stat(p).st_mode)
    except OSError:
        mask = os.umask(0)
        os.umask(mask)
        return 0o666 & ~mask


def write_bytes(path: Union[str, Path], data: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # mkstemp создаёт файл с правами 0600, и os.replace переносил их на заметку: после записи
        # движком vault на общей машине/шаре переставал читаться другими. Режим — как у прежнего
        # файла, у нового — обычный по umask.
        os.chmod(tmp, _mode_for(p))
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text(path: Union[str, Path], text: str, encoding: str = "utf-8") -> None:
    write_bytes(path, text.encode(encoding))
