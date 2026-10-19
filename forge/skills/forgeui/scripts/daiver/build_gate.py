"""build_gate.py — гейт шага ui-build: секции собраны, проект собирается (спека §13.3 п.4, §13.8, INV-C20; задача 3.2).

Гейт читает диск и запускает команду проекта; слова модели «собрал» он не принимает. Порядок строгий, и каждая ступень отвечает за своё:
  1. все кадры трека: `build-state.json` есть, сверен с планом, ни одна секция не `pending` / `in_progress` / `stale`, `outputs` всех
     готовых секций лежат на диске (читатель `build_state.gate_problems` — он же отказывает, если план не соответствует макету);
  2. `project.buildCommand` из конфига задан — его называет человек при первом запуске (`daiver_init`), не угадывает движок;
  3. только когда 1 и 2 в порядке — запуск `buildCommand` из корня репо с таймаутом; красный итог (код ≠ 0, таймаут) — красный гейт
     с хвостом вывода: модуль, который не компилируется, чинит сам субагент (§13.8), а не спрашивает человека.
Незавершённую сборку компилировать незачем: build идёт минуты, а ответ «секции не готовы» уже известен.

Запуск. `shell=True` (команда — строка конфига вроде `npm run build && npx tsc --noEmit`, тот же уровень доверия, что у scripts из
package.json), stdin закрыт (вопросов команда задать не может), вывод пишется во временный файл — в память попадает только хвост:
лог сборки бывает на десятки мегабайт. На POSIX команда стартует в новой группе процессов и при таймауте убивается ВСЯ группа
(иначе `node` внука жил бы дальше и держал порты); на Windows — `taskkill /T /F`. Не проверено на Windows (машина разработки — macOS).
Таймаут по умолчанию — 900 с: раннер `record_gate` ждёт 1800 с, и наш отказ с хвостом информативнее его безликого «TIMEOUT».

Хвост ответа — последние `TAIL_LINES` строк (не больше `TAIL_CHARS` знаков) после вырезания ANSI-цветов и перевода `\\r` в перевод строки
(прогресс-бары). Время и пути машины в ответ не пишутся. Кадры-состояния (`stateOf`) строит 6.2: до неё гейт требует состояние и у них.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import build_state as BS
from . import config as CFG
from . import track as TR

DEFAULT_TIMEOUT = 900
TAIL_LINES = 40
TAIL_CHARS = 3000
READ_BYTES = 64 * 1024                       # сколько байт конца лога читается для хвоста
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def frame_names(root: Path, track: str) -> Tuple[List[str], List[str]]:
    """(имена кадров трека по порядку манифеста, причины, по которым их нет)."""
    try:
        manifest = TR.load(Path(root), track)
    except TR.TrackError as e:
        return [], [str(e)]
    if manifest is None:
        return [], [f"трека «{track}» нет — сначала шаг ui-design (daiver_pixso_pull → … → план, подпись)"]
    names = [f["name"] for f in manifest.get("frames") or []]
    return (names, []) if names else ([], [f"в треке «{track}» нет кадров — сборке нечего строить"])


def section_problems(root: Path, track: str) -> List[str]:
    """Причины, по которым сборка кадров трека не закончена. Пусто — все секции всех кадров готовы, файлы на месте."""
    names, why = frame_names(root, track)
    out = list(why)
    for name in names:
        out += BS.gate_problems(root, track, name)
    return out


def build_command(root: Path) -> Tuple[Optional[str], Optional[str]]:
    """(команда, причина отказа): `project.buildCommand` проекта — как записана в конфиге."""
    loaded = CFG.load(Path(root))
    if not loaded["exists"]:
        return None, ("нет .daiver/config.yaml — проект не инициализирован: настройки спрашивает и пишет daiver_init "
                      "(в том числе команду сборки)")
    command = loaded["config"]["project"]["buildCommand"]
    if not command:
        return None, ("project.buildCommand не задан — гейту нечем собирать проект. Команду называет человек (daiver_init: "
                      "«Команда сборки/проверки типов проекта»), не угадывай её")
    return command, None


def clean_tail(raw: bytes) -> str:
    """Хвост лога для ответа: без ANSI, без возвратов каретки, последние строки, ограниченный размер."""
    text = _ANSI.sub("", raw.decode("utf-8", errors="replace")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    cut = len(lines) > TAIL_LINES
    tail = "\n".join(lines[-TAIL_LINES:])
    if len(tail) > TAIL_CHARS:
        tail, cut = tail[-TAIL_CHARS:].split("\n", 1)[-1], True
    return ("…\n" if cut else "") + tail


def _kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    """Убрать команду вместе с потомками: убитая оболочка иначе оставила бы `node` и занятые порты."""
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:                                                    # pragma: no cover — на Windows не проверено
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_command(command: str, cwd: Path, timeout: int) -> Dict[str, Any]:
    """Запуск `command` в `cwd`. → {exit: код (None — убита по таймауту), timedOut, tail}. Не бросает на не-нуле и не висит дольше таймаута."""
    kwargs: Dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:                                                        # pragma: no cover — на Windows не проверено
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with tempfile.TemporaryFile() as log:
        try:
            proc = subprocess.Popen(command, shell=True, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, **kwargs)
        except OSError as e:
            return {"exit": None, "timedOut": False, "tail": f"команда не запустилась: {e.strerror or e}"}
        timed_out = False
        try:
            code: Optional[int] = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out, code = True, None
            _kill_tree(proc)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:                    # pragma: no cover — SIGKILL не игнорируется
                pass
        size = log.seek(0, os.SEEK_END)
        log.seek(max(0, size - READ_BYTES))
        tail = clean_tail(log.read())
    return {"exit": code, "timedOut": timed_out, "tail": tail}


def check(root: Any, track: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Гейт целиком. → {ok, problems: [...], command, exit, timedOut, tail, frames}. Команда запускается, только если секции готовы
    и она задана; ничего, кроме побочных эффектов самой команды, на диск не пишется."""
    root = Path(root)
    problems = section_problems(root, track)
    command, why = build_command(root)
    if why:
        problems.append(why)
    out: Dict[str, Any] = {"ok": False, "problems": problems, "command": command, "exit": None, "timedOut": False, "tail": "",
                           "frames": frame_names(root, track)[0]}
    if problems:
        return out
    ran = run_command(command or "", root, timeout)
    out.update(ran)
    out["ok"] = ran["exit"] == 0
    return out
