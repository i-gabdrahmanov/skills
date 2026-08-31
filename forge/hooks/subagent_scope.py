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

Модуль stdlib-only и безопасен к любым ошибкам ФС: не смог прочитать/записать — считаем, что
субагента нет (гейт остаётся включённым, как раньше).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

_TTL_SEC = 3 * 60 * 60   # 3 часа: дольше живого субагента, короче суток


def _path(session_id: str) -> str:
    tag = hashlib.sha256(f"subagent|{session_id}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"forge-subagent-{tag}.json")


def _load(session_id: str) -> dict:
    try:
        with open(_path(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(session_id: str, data: dict) -> None:
    try:
        with open(_path(session_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def enter(session_id: str, agent_id: str, agent_type: str = "") -> None:
    """SubagentStart: пометить, что субагент начал работу."""
    if not session_id:
        return
    data = _load(session_id)
    active = data.get("active") if isinstance(data.get("active"), dict) else {}
    active[str(agent_id or agent_type or "subagent")] = time.time()
    _save(session_id, {"active": active})


def leave(session_id: str, agent_id: str, agent_type: str = "") -> None:
    """SubagentStop: снять пометку. Неизвестный id — снимаем самую старую запись, иначе
    рассинхрон id между Start и Stop навсегда оставил бы гейт выключенным."""
    if not session_id:
        return
    data = _load(session_id)
    active = data.get("active") if isinstance(data.get("active"), dict) else {}
    key = str(agent_id or agent_type or "subagent")
    if key in active:
        active.pop(key, None)
    elif active:
        active.pop(min(active, key=lambda k: active[k]), None)
    _save(session_id, {"active": active})


def active(session_id: str) -> bool:
    """Идёт ли сейчас работа субагента в этой сессии (с учётом TTL)."""
    if not session_id:
        return False
    data = _load(session_id)
    entries = data.get("active") if isinstance(data.get("active"), dict) else {}
    now = time.time()
    for started in entries.values():
        try:
            if now - float(started) <= _TTL_SEC:
                return True
        except (TypeError, ValueError):
            continue
    return False
