"""proc.py — процессы в собственной группе и их гарантированное завершение (задача 4.1; общий помощник для браузера и dev-сервера 4.9).

Зачем группа. Chrome — десяток процессов (рендереры, GPU, утилиты), dev-сервер — `npm` → `node` → воркеры. Убитый лидер оставляет
потомков, и они держат порты и временные профили. Поэтому процесс стартует в новой сессии/группе (POSIX: `start_new_session`,
группа = pid лидера), а завершение шлёт сигнал ВСЕЙ группе: сначала SIGTERM, после короткой паузы SIGKILL. На Windows —
`taskkill /F /T` (не проверено: машина разработки — macOS).

Предохранитель: группа вызывающего процесса не трогается никогда (сигнал себе вместе с потомками был бы хуже утечки).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Sequence

POLL_STEP = 0.02
DONE_ATTR = "_forge_group_done"                              # отметка на Popen: группа убрана, больше не трогать


def popen_group(cmd: Sequence[str], **kwargs: Any) -> "subprocess.Popen[bytes]":
    """`subprocess.Popen` в новой группе процессов (аргументы — как у Popen; оболочки нет, `cmd` — список)."""
    if os.name == "posix":
        kwargs.setdefault("start_new_session", True)
    else:                                                        # pragma: no cover — на Windows не проверено
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return subprocess.Popen(list(cmd), **kwargs)


def group_alive(proc: "subprocess.Popen[bytes]") -> bool:
    """Жив ли хоть один процесс группы лидера `proc` (POSIX); на Windows — жив ли сам процесс."""
    if os.name != "posix":                                       # pragma: no cover
        return proc.poll() is None
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                                      # группа есть, чужая — для нас «жива»
        return True
    return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def kill_group(proc: "subprocess.Popen[bytes]", *, term_grace: float = 2.0, kill_wait: float = 5.0) -> bool:
    """Завершить процесс и ВСЮ его группу и забрать лидера (без зомби). → True, если пришлось слать сигнал.

    Идемпотентна: после успешной уборки повторный вызов не шлёт и не проверяет ничего — номер группы освободился и мог достаться
    чужому процессу, а сигнал ему хуже утечки. Ждёт не дольше `term_grace` + `kill_wait` секунд."""
    if getattr(proc, DONE_ATTR, False):
        return False
    if os.name != "posix":                                       # pragma: no cover — на Windows не проверено
        sent = proc.poll() is None
        if sent:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            proc.wait(timeout=kill_wait)
        except subprocess.TimeoutExpired:
            return sent
        setattr(proc, DONE_ATTR, True)
        return sent
    pgid = proc.pid
    if pgid == os.getpgrp():
        raise RuntimeError("kill_group: процесс не в своей группе — группу вызывающего убивать нельзя")

    def alive() -> bool:
        proc.poll()                                              # забрать лидера: зомби держит группу «живой»
        return group_alive(proc)

    sent = False
    if alive():
        sent = True
        _signal_group(pgid, signal.SIGTERM)
        end = time.monotonic() + term_grace
        while time.monotonic() < end and alive():
            time.sleep(POLL_STEP)
        if alive():
            _signal_group(pgid, signal.SIGKILL)
            end = time.monotonic() + kill_wait
            while time.monotonic() < end and alive():
                time.sleep(POLL_STEP)
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:                            # pragma: no cover — SIGKILL не игнорируется
        pass
    if not group_alive(proc):
        setattr(proc, DONE_ATTR, True)
    return sent
