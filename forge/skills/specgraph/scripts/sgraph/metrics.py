"""metrics.py — счётчики прогона и история здоровья графа (задача 4.1).

Зачем. Реальной спеки разработка не увидит никогда: из окружения вернутся только цифры и коды
(пакет диагностики, 4.3). Поэтому движок сам считает, что происходило: какой инструмент и почему
отказывал модели, где она переделывала единицу работы, что сломалось внутри. Хуки-логгеры forge
уже снимали из-за оверхеда, так что считает диспетчер инструментов (`tools.call`) — бесплатно для
модели, и каждый новый инструмент считается сам.

Правило безопасности: в счётчиках НЕТ текста. Пишутся имя инструмента, исход, коды отказа (только
по формату `E-…`/`W-…`/`I-…`), id единиц работы (`ref`), длительность и размер ответа. Аргументы
вызова не пишутся никогда (в них цитаты спеки), сообщение исключения — тоже (только тип).

Раскладка:
    .spec-graph/runs/<KEY>/calls.jsonl     — по строке на вызов инструмента
    .spec-graph/runs/<KEY>/counters.json   — именованные счётчики шага (попытки, вопросы, решения)
    .spec-graph/health.json                — последние 30 снимков здоровья графа + метка стабилизации
KEY — ключ живого прогона ветки specgraph (`ground/statements/specgraph/<KEY>/`, свежайший), вне
прогона — `adhoc`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import fsutil as FS

RUNS_REL = ".spec-graph/runs"
HEALTH_REL = ".spec-graph/health.json"
HEALTH_KEEP = 30
STABLE_RUNS = 3
ADHOC = "adhoc"
_CODE_RE = re.compile(r"^[EWI]-[a-z0-9][a-z0-9-]*$")
_REF_RE = re.compile(r"^[^\s\"'\\]{1,200}$")
_COUNTER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _now_ms() -> int:
    return int(time.time() * 1000)


def current_key(root: Path) -> str:
    """Ключ прогона, к которому относятся вызовы: свежайший манифест ветки specgraph, иначе adhoc."""
    base = Path(root) / "ground" / "statements" / "specgraph"
    best, best_m = None, -1.0
    if base.is_dir():
        for m in base.glob("*/manifest.json"):
            if FS.run_paused(m.parent):
                continue                 # прогон на паузе — вызовы идут не к нему
            try:
                mt = m.stat().st_mtime
            except OSError:
                continue
            if mt > best_m:
                best, best_m = m.parent.name, mt
    return best or ADHOC


def run_dir(root: Path, key: Optional[str] = None) -> Path:
    return Path(root) / RUNS_REL / (key or current_key(root))


def clean_codes(codes: Iterable[Any]) -> List[str]:
    """Только коды по формату: свободный текст в счётчик не попадает, даже если его туда передали."""
    out = []
    for c in codes or ():
        if isinstance(c, str) and _CODE_RE.match(c) and c not in out:
            out.append(c)
    return out


def _clean_refs(refs: Iterable[Any]) -> List[str]:
    return [r for r in (refs or ()) if isinstance(r, str) and _REF_RE.match(r)][:20]


def _append(path: Path, rec: Dict[str, Any]) -> None:
    """Запись счётчика не роняет инструмент: сбой диска — строка в stderr, а не отказ работы."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as e:
        print(f"[spec-graph] счётчик не записан: {type(e).__name__}", file=sys.stderr)


def record_call(root: Path, tool: str, outcome: str, *, codes: Iterable[Any] = (), refs: Iterable[Any] = (),
                duration_ms: int = 0, size: int = 0, error_type: str = "", key: Optional[str] = None) -> None:
    rec: Dict[str, Any] = {"t": _now_ms(), "tool": tool, "outcome": outcome, "ms": int(duration_ms),
                           "size": int(size)}
    cs = clean_codes(codes)
    if cs:
        rec["codes"] = cs
    rs = _clean_refs(refs)
    if rs:
        rec["refs"] = rs
    if error_type:
        rec["error"] = re.sub(r"[^A-Za-z0-9_.]", "", error_type)[:80]
    _append(run_dir(root, key) / "calls.jsonl", rec)


def bump(root: Path, counter: str, n: int = 1, key: Optional[str] = None) -> int:
    """Именованный счётчик шага (`assign.attempts`, `human.questions`, …); имя — по формату."""
    if not _COUNTER_RE.match(counter or ""):
        raise ValueError(f"имя счётчика «{counter}»: латиница, цифры, _.-")
    p = run_dir(root, key) / "counters.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        data = {}
    data[counter] = int(data.get(counter, 0)) + int(n)
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return data[counter]


def read_calls(root: Path, key: Optional[str] = None) -> List[Dict[str, Any]]:
    p = run_dir(root, key) / "calls.jsonl"
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def summarize(root: Path, key: Optional[str] = None, top: int = 10) -> Dict[str, Any]:
    """Сводка прогона: исходы по инструментам, топ кодов отказа, единицы с повторами, ошибки движка."""
    calls = read_calls(root, key)
    by_tool: Dict[str, Dict[str, int]] = {}
    codes: Dict[str, int] = {}
    retries: Dict[str, int] = {}
    errors: Dict[str, int] = {}
    for c in calls:
        t = by_tool.setdefault(c.get("tool", "?"), {})
        t[c.get("outcome", "?")] = t.get(c.get("outcome", "?"), 0) + 1
        for code in c.get("codes", ()):
            codes[code] = codes.get(code, 0) + 1
        if c.get("outcome") == "rejected":
            for r in c.get("refs", ()):
                retries[r] = retries.get(r, 0) + 1
        if c.get("error"):
            errors[c["error"]] = errors.get(c["error"], 0) + 1
    rank = lambda d: [[k, v] for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]  # noqa: E731
    counters_p = run_dir(root, key) / "counters.json"
    try:
        counters = json.loads(counters_p.read_text(encoding="utf-8")) if counters_p.is_file() else {}
    except (OSError, ValueError):
        counters = {}
    return {"key": key or current_key(root), "calls": len(calls), "by_tool": by_tool, "top_codes": rank(codes),
            "most_rejected_refs": rank(retries), "engine_errors": rank(errors), "counters": counters}


def _signature(finding: str) -> str:
    return hashlib.sha1(finding.encode("utf-8")).hexdigest()[:12]


def record_health(root: Path, snapshot: Dict[str, Any], findings: Iterable[str] = (),
                  key: Optional[str] = None, stable_runs: int = STABLE_RUNS) -> Dict[str, Any]:
    """Снимок здоровья графа в историю: числа из `snapshot` (только числа и словари чисел) и
    подписи находок (хеши — текст находок в историю не попадает). Новая находка — подпись, которой
    не было в прошлом снимке. Стабилизация — `stable_runs` снимков подряд без новых находок."""
    nums = _numbers_only(snapshot)
    sigs = sorted({_signature(f) for f in findings if isinstance(f, str)})
    p = Path(root) / HEALTH_REL
    try:
        hist = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        hist = {}
    runs = hist.get("runs") if isinstance(hist.get("runs"), list) else []
    prev = set(runs[-1].get("findings", [])) if runs else set()
    new = [s for s in sigs if s not in prev] if runs else sigs
    entry = {"t": _now_ms(), "key": key or current_key(root), "metrics": nums, "findings": sigs,
             "new_findings": len(new)}
    runs = (runs + [entry])[-HEALTH_KEEP:]
    streak = 0
    for r in reversed(runs):
        if r.get("new_findings", 1):
            break
        streak += 1
    out = {"runs": runs, "stable_streak": streak, "stable": streak >= stable_runs}
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"new_findings": len(new), "stable_streak": streak, "stable": out["stable"], "kept": len(runs)}


def _numbers_only(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, dict):
        return {k: _numbers_only(x) for k, x in v.items()
                if isinstance(k, str) and _COUNTER_RE.match(k) and _is_numeric_tree(x)}
    return None


def _is_numeric_tree(v: Any) -> bool:
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, dict):
        return all(isinstance(k, str) and _is_numeric_tree(x) for k, x in v.items())
    return False
