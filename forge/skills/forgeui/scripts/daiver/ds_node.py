"""ds_node.py — необязательный node-движок разбора для daiver_ds_requirements (engine: "node").

Зачем он есть при чистом Python-пути. Python-лексер (`daiver/ds/*`) — основной путь и ни от чего не зависит; настоящий
парсер `typescript` фронт-репо надёжнее на нестандартных исходниках, и пользователю нужен запасной ход, когда Python
разобрал библиотеку плохо. Поэтому движок выбирается ЯВНЫМ аргументом и никогда не подменяется молча: документ
записывает, чем построен (`source.scanner`), а нет node или typescript — это названный отказ, не откат.

Что делает node: `assets/ds-scan.mjs` читает файлы видов stories | types | source и отдаёт те же ФАКТЫ, что Python-
разбор (схема — `ds/facts.py`), вместе с sha256 прочитанных байтов; `typescript` берётся из репо
(`createRequire(<repo>/package.json)`). MDX TypeScript не разбирает — его всегда читает Python. Слияние, документы и
валидаторы общие для обоих движков (`ds/merge.py`, `ds_requirements.py`).

Сверка: хеш файла, который прочитал node, обязан совпасть с хешем, который посчитал Python (иначе файл менялся
посреди скана, и контракт строился бы не по тем байтам, чьим хешем он подписан).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import ds_requirements as DR

HELPER = Path(__file__).resolve().parent / "assets" / "ds-scan.mjs"
TIMEOUT_SECONDS = 180
NODE_KINDS = ("stories", "types", "source")


def scan(root: Path, entries: List[DR.Entry]) -> Tuple[List[Dict[str, Any]], int, str]:
    """(факты всех файлов, число неразобранных файлов, версия typescript)."""
    node = shutil.which("node")
    if not node:
        raise DR.DsError("engine: node — на машине нет node в PATH. Это необязательный движок: используйте "
                         "engine: python (по умолчанию) или установите node")
    mine = [e for e in entries if e.kind in NODE_KINDS]
    payload = {"root": str(Path(root).resolve()), "files": [{"path": e.path, "kind": e.kind} for e in mine]}
    try:
        proc = subprocess.run([node, str(HELPER)], input=json.dumps(payload), capture_output=True, text=True,
                              encoding="utf-8", timeout=TIMEOUT_SECONDS, cwd=str(root))
    except subprocess.TimeoutExpired:
        raise DR.DsError(f"engine: node — разбор не уложился в {TIMEOUT_SECONDS} с: сузьте library.scanPaths")
    except OSError as e:
        raise DR.DsError(f"engine: node — не удалось запустить node ({e})")
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise DR.DsError(f"engine: node — хелпер не вернул JSON (код {proc.returncode}): {tail}")
    if proc.returncode != 0 or "error" in out:
        what = out.get("message") or out.get("error") or "сбой хелпера"
        raise DR.DsError(f"engine: node — {what}")
    by_path = {f["path"]: f for f in out["files"]}
    facts: List[Dict[str, Any]] = []
    parse_errors = 0
    for e in entries:
        if e.kind == "mdx":
            f, ok = DR.scan_py(e)
            facts.append(f)
            parse_errors += 0 if ok else 1
            continue
        got = by_path.get(e.path)
        if got is None:
            raise DR.DsError(f"engine: node — хелпер не вернул файл {e.path}")
        if got["sha256"] != e.sha:
            raise DR.DsError(f"файл {e.path} изменился во время скана — повторите вызов")
        facts.append(got["facts"])
        parse_errors += 1 if got.get("parseError") else 0
    return facts, parse_errors, str(out.get("ts", ""))
