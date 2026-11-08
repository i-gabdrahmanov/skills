#!/usr/bin/env python3
"""subagent_scope.py — «идёт ли сейчас работа ВНУТРИ субагента» для actor-aware гейтов.

Зачем это вообще нужно
----------------------
`inline-phase-guard` обязан отличать оркестратора от субагента, и делал это по полю
`agent_type` в payload'е. Живой прогон на qwen-code 0.21.14 (метапроект, e2e) показал, что
поля НЕТ вовсе — ни `agent_type`, ни `agent_id` — и это одинаково верно для вызовов главного
агента и для вызовов ИЗ субагента:

    [15:50:23] SubagentStart   agent_type=general-purpose  agent_id=general-purpose-986521589
    [15:53:01] PreToolUse edit agent_type=—                agent_id=—        ← это субагент
    [15:53:11] SubagentStop    agent_type=general-purpose  agent_id=general-purpose-986521589

`session_id` и `transcript_path` у субагента те же, что у оркестратора. То есть на уровне
одного payload'а субагент НЕОТЛИЧИМ, и хук блокировал ровно ту работу, которую сам же требует
делать субагентом: субагент получал deny «выполняй это через agent(...)», возвращал текст
отказа наверх, оркестратор предлагал «давайте запустим субагента» — и так по кругу.

Решение
-------
`agent_type`/`agent_id` рантайм отдаёт на SubagentStart и SubagentStop. Значит границу можно
записать: SubagentStart кладёт id в маркер сессии, SubagentStop убирает. Пока множество
непусто — тул-вызовы принадлежат субагенту (оркестратор в это время ЖДЁТ результат
tool-call'а и своих вызовов не делает).

Ограничения, принятые сознательно:
  • фоновые задачи (`run_in_background`) теоретически могут дать вызов оркестратора во время
    работы субагента — гейт в этом окне промолчит. Это ослабление на порядок меньше, чем
    полный дедлок продуктивных фаз;
  • маркер живёт в temp-каталоге ОС и привязан к `session_id` — ground/ не засоряется,
    state-write-guard не задевается, новая сессия всегда стартует с чистого листа;
  • TTL: запись старше `_TTL_SEC` игнорируется. Иначе упавший субагент (процесс убит, до
    SubagentStop не дошло) выключил бы actor-гейт до конца сессии.

Модуль stdlib-only (замок — `_project.exclusive_lock`; нет его — пишем без замка) и безопасен к
любым ошибкам ФС: не смог прочитать/записать — считаем, что субагента нет (гейт остаётся
включённым, как раньше).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager

_TTL_SEC = 3 * 60 * 60   # 3 часа: дольше живого субагента, короче суток


def _path(session_id: str) -> str:
    tag = hashlib.sha256(f"subagent|{session_id}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"forge-subagent-{tag}.json")


@contextmanager
def _locked(session_id: str):
    """Read-modify-write маркера под замком. Оркестратор запускает пачку субагентов ОДНИМ ходом —
    десять SubagentStart приходят разом; без замка запись соседа терялась (замер: до 9 отметок из
    10), а SubagentStop первого снимал отметку у остальных, и их сдачи ловили DENY
    inline-phase-guard (боевой /graph 2026-11-07, ~10% единиц assign-section при parallel=10)."""
    try:
        from _project import exclusive_lock
    except Exception:
        yield
        return
    with exclusive_lock(_path(session_id) + ".lock"):
        yield


def _load(session_id: str) -> dict:
    try:
        with open(_path(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(session_id: str, data: dict) -> None:
    """Атомарно: open("w") сперва обнуляет файл, и читатель в этот миг видел «субагентов нет»."""
    path = _path(session_id)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _active(data: dict) -> dict:
    """{ключ агента: [время старта, …]}. Список — на случай одного ключа у нескольких живых
    субагентов (рантайм не дал agent_id, ключом стал agent_type). Старый формат — одно число."""
    raw = data.get("active") if isinstance(data.get("active"), dict) else {}
    out = {}
    for k, v in raw.items():
        stamps = v if isinstance(v, list) else [v]
        stamps = [x for x in stamps if isinstance(x, (int, float))]
        if stamps:
            out[k] = stamps
    return out


def enter(session_id: str, agent_id: str, agent_type: str = "") -> None:
    """SubagentStart: пометить, что субагент начал работу."""
    if not session_id:
        return
    with _locked(session_id):
        data = _load(session_id)
        active = _active(data)
        active.setdefault(str(agent_id or agent_type or "subagent"), []).append(time.time())
        _save(session_id, {"active": active, "seen": True})


def leave(session_id: str, agent_id: str, agent_type: str = "") -> None:
    """SubagentStop: снять пометку (одну — у ключа может быть несколько живых субагентов).

    Неизвестный id снимает запись, только если она единственная: это рассинхрон id между Start и
    Stop одного субагента, иначе гейт остался бы выключенным до TTL. При нескольких живых чужую не
    трогаем — прежде снималась самая старая, то есть ещё работающего соседа по пачке."""
    if not session_id:
        return
    with _locked(session_id):
        data = _load(session_id)
        active = _active(data)
        key = str(agent_id or agent_type or "subagent")
        if key not in active and len(active) == 1 and len(next(iter(active.values()))) == 1:
            key = next(iter(active))
        if key in active:
            active[key].pop(0)
            if not active[key]:
                del active[key]
        # `seen` — липкий: он про способность РАНТАЙМА слать SubagentStart, а не про то, работает
        # ли субагент прямо сейчас. Снимать его вместе с active нельзя, иначе диагностика ниже
        # («сигнал есть, но ты оркестратор» vs «сигнала нет вовсе») теряет смысл после первого
        # же SubagentStop.
        _save(session_id, {"active": active, "seen": bool(data.get("seen")) or bool(active)})


def start_seen(session_id: str) -> bool:
    """Приходил ли в этой сессии хоть один SubagentStart.

    Отличает два несовпадающих состояния, которые для `active()` выглядят одинаково («False»):
      • сигнал РАБОТАЕТ, но субагент сейчас не активен → блокировка оркестратора корректна;
      • сигнала НЕТ вовсе (рантайм не шлёт SubagentStart, хук не разложен, матчер не сработал,
        в payload'е нет session_id) → блокируется в том числе САМ субагент, и это тупик
        (tasks/008). Различать их надо в тексте отказа, иначе причина не диагностируема.
    """
    if not session_id:
        return False
    return bool(_load(session_id).get("seen"))


def active(session_id: str) -> bool:
    """Идёт ли сейчас работа субагента в этой сессии (с учётом TTL)."""
    if not session_id:
        return False
    now = time.time()
    return any(now - t <= _TTL_SEC for stamps in _active(_load(session_id)).values() for t in stamps)
