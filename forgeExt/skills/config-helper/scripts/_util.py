"""Общие хелперы config-helper: резолв корня, атомарная запись, бэкап, навигация по
dotted-path, валидация значения по записи реестра. Всю запись в конфиги делает скрипт —
модель не правит JSON руками."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple


def repo_root() -> str:
    """Корень репо: git toplevel или cwd. Без $() — рантайм Qwen/GigaCode режет
    command substitution, и вызов с подстановкой блокируется до запуска python."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def atomic_write(path, data: Any) -> None:
    """Пишет JSON в .tmp, затем os.replace — конфиг не бьётся при обрыве."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, p)


def backup(path, project_root) -> Optional[str]:
    """Копия текущего файла в ground/config-helper/backups/<name>.<ts>.bak."""
    p = Path(path)
    if not p.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bdir = Path(project_root) / "ground" / "config-helper" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"{p.name}.{ts}.bak"
    dest.write_bytes(p.read_bytes())
    return str(dest)


def dig(obj: dict, dotted: str) -> Tuple[bool, Any]:
    """(found, value) по dotted-пути. found=False если путь отсутствует."""
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return (False, None)
        cur = cur[part]
    return (True, cur)


def assign(obj: dict, dotted: str, value: Any) -> None:
    """Кладёт value по dotted-пути, создавая промежуточные dict при необходимости."""
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


_TRUE = {"true", "1", "yes", "on", "да", "вкл"}
_FALSE = {"false", "0", "no", "off", "нет", "выкл"}
_NULL = {"null", "none", "~", "nil"}


def _check_range(entry: dict, v) -> None:
    lo, hi = entry.get("min"), entry.get("max")
    if lo is not None and v < lo:
        raise ValueError(f"значение {v} меньше минимума {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"значение {v} больше максимума {hi}")


def coerce_and_validate(entry: dict, raw) -> Any:
    """Приводит строковый ввод к типу параметра и валидирует (fail-closed).
    Бросает ValueError с понятным сообщением при несоответствии."""
    t = entry.get("type")
    s = str(raw).strip()

    # Явный null разрешён для string/bool-параметров с допустимым null-дефолтом
    if s.lower() in _NULL:
        if t in ("string", "bool") or entry.get("default") is None:
            return None
        raise ValueError(f"null недопустим для параметра типа {t}")

    if t == "bool":
        if isinstance(raw, bool):
            return raw
        low = s.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"ожидался bool (true/false), получено {raw!r}")

    if t == "int":
        try:
            v = int(s)
        except ValueError:
            raise ValueError(f"ожидалось целое, получено {raw!r}")
        _check_range(entry, v)
        return v

    if t == "float":
        try:
            v = float(s)
        except ValueError:
            raise ValueError(f"ожидалось число, получено {raw!r}")
        _check_range(entry, v)
        return v

    if t == "enum":
        allowed = entry.get("enum", [])
        if s not in allowed:
            raise ValueError(f"значение {s!r} не входит в допустимые: {allowed}")
        return s

    if t == "string":
        return str(raw)

    if t == "list":
        # Принимаем JSON-массив строк ('["a","b"]') или CSV ("a,b"); пустая строка → []
        if isinstance(raw, list):
            vals = raw
        elif s.startswith("["):
            try:
                vals = json.loads(s)
            except json.JSONDecodeError as ex:
                raise ValueError(f"невалидный JSON-массив: {ex}")
            if not isinstance(vals, list):
                raise ValueError(f"ожидался JSON-массив, получено {type(vals).__name__}")
        else:
            vals = [p.strip() for p in s.split(",") if p.strip()]
        if not all(isinstance(v, str) for v in vals):
            raise ValueError("список должен содержать только строки")
        return vals

    raise ValueError(f"неизвестный тип параметра: {t!r}")


def validate_typed(entry: dict, value: Any) -> None:
    """Проверяет УЖЕ типизированное значение (как оно лежит в JSON) против записи реестра.

    В отличие от coerce_and_validate, НЕ приводит строки к числам — наоборот, ловит
    рассинхрон типа: `"0.8"` строкой там, где ждём float, должен упасть, а не «починиться».
    Это валидация на ЧТЕНИЕ конфига (а не на запись). Бросает ValueError при несоответствии.
    """
    t = entry.get("type")

    if value is None:
        if t in ("string", "bool") or entry.get("default") is None:
            return
        raise ValueError(f"null недопустим для параметра типа {t}")

    if t == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"ожидался bool, в файле {type(value).__name__}: {value!r}")
        return

    if t == "int":
        # bool — подкласс int, но это другой тип; считаем рассинхроном
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"ожидалось целое, в файле {type(value).__name__}: {value!r}")
        _check_range(entry, value)
        return

    if t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"ожидалось число, в файле {type(value).__name__}: {value!r}")
        _check_range(entry, value)
        return

    if t == "enum":
        allowed = entry.get("enum", [])
        if value not in allowed:
            raise ValueError(f"значение {value!r} не входит в допустимые: {allowed}")
        return

    if t == "string":
        if not isinstance(value, str):
            raise ValueError(f"ожидалась строка, в файле {type(value).__name__}: {value!r}")
        return

    if t == "list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"ожидался список строк, в файле {type(value).__name__}: {value!r}")
        return

    raise ValueError(f"неизвестный тип параметра: {t!r}")
