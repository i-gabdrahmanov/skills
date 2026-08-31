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


# ── Роутинг записи между policy.json и manifest.json (v2 модели) ──────────────
# Целевая модель: pipeline.json РАЗДЕЛЁН на два файла с разной семантикой. Реестр
# params-registry.json описывает поля в НОВОЙ модели; route_path() определяет,
# в КАКОЙ файл писать запись (policy.json или manifest.json активной фичи).

# file-key (реестр params-registry.json) → имя файла для резолва в resolve_file().
# "pipeline" остаётся для обратной совместимости с записями реестра (старый file-key),
# но resolve_file подменяет его на policy.json при записи и на pipeline.json только
# в режиме legacy_fallback (см. config.py).
FILE_KEY_TO_FILENAME = {
    "policy": "policy.json",
    "pipeline": "policy.json",       # v2: pipeline.* → policy.json
    "manifest": None,                # не файл; резолвится через manifest_path(...)
    "gates": "feature-gates.json",
    "risk": "risk-policy.json",
}


# Префиксы путей, которые пишутся в manifest.json активной фичи (а не в policy.json).
# Это per-feature входы и решения: они переживают только прогон, и разные скиллы
# (forgefix/forgelite/feature-pipeline) пишут свои — конкуренция снимается тем, что
# у каждой фичи свой manifest.json.
MANIFEST_PATH_PREFIXES = ("inputs.", "decisions.")


# Legacy-фоллбэк для dual-read. Когда config_get() / risk_ladder.config_get() не находит
# значение в новом пути (manifest.inputs.X / manifest.decisions.X), пробует старый
# dot-path в pipeline.json. ТОЛЬКО для backward-совместимости со старыми проектами
# и для авто-миграции v1→v2 (init.py). Новые записи идут уже в новые пути.
#
# Структура: новый_путь → legacy_путь_в_pipeline_json
LEGACY_PATHS = {
    # Per-feature входы (бывшие sources.*)
    "inputs.story": "sources.story",
    "inputs.spec": "sources.spec",
    "inputs.spec_anchor": "sources.spec_anchor",
    "inputs.mode": "pipeline.mode",
    # Per-feature решения (бывшие pipeline.mode_task, autonomy.*)
    "decisions.mode_task": "pipeline.mode_task",
    "decisions.criticality": "autonomy.criticality",
    "decisions.auto_max_risk": "autonomy.auto_max_risk",
}


# Legacy-поля, которые НЕ ДОЛЖНЫ БЫТЬ в новом policy.json. При наличии — config.py
# validate выдаёт WARNING и предлагает миграцию. Эти поля жили в pipeline.json, но
# теперь это per-feature данные и должны быть в manifest.json активной фичи.
DEPRECATED_IN_POLICY = {
    "sources.story",
    "sources.spec",
    "sources.spec_anchor",
    "pipeline.mode",
    "pipeline.mode_task",
    "autonomy.criticality",
    "autonomy.auto_max_risk",
}


def route_path(dotted: str) -> tuple[str, str]:
    """Куда писать dotted-путь: ('policy', остаток_пути) или ('manifest', section.key).

    Returns:
        ('policy', dotted)         — для всех путей кроме inputs.*/decisions.*
        ('manifest', 'inputs.X')   — для inputs.* (секция inputs в манифесте)
        ('manifest', 'decisions.X')— для decisions.* (секция decisions в манифесте)

    Секция manifest не разделяет inputs и decisions на уровне путей: в манифесте
    они лежат под ключами "inputs" и "decisions". Поэтому вторая часть возвращает
    путь ОТНОСИТЕЛЬНО манифеста (без префикса 'inputs.'/'decisions.')."""
    if dotted.startswith("inputs."):
        return ("manifest", dotted[len("inputs."):])
    if dotted.startswith("decisions."):
        return ("manifest", dotted[len("decisions."):])
    return ("policy", dotted)


def is_manifest_path(dotted: str) -> bool:
    """True, если путь уходит в manifest.json активной фичи (а не в policy.json)."""
    return dotted.startswith(MANIFEST_PATH_PREFIXES)


def legacy_path_for(dotted: str) -> Optional[str]:
    """Legacy dot-path в pipeline.json для dual-read fallback, None если нет маппинга."""
    return LEGACY_PATHS.get(dotted)


def is_deprecated_in_policy(dotted: str) -> bool:
    """True, если путь когда-то жил в policy/pipeline.json, но теперь это per-feature данные
    и должен быть в manifest.json активной фичи. validate выдаёт WARNING."""
    return dotted in DEPRECATED_IN_POLICY


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
        # `none_is_value` — параметры, для которых 'none' это ОСОЗНАННЫЙ ОТВЕТ пользователя
        # («стори неизвестна», «в спеке не описано»), а не «значение не задано». Их гейты
        # (required_decisions, required_decisions_on_close) отличают «ответили» от «не
        # ответили» по непустому значению: записанный null читается как «не ответили», и
        # документированный ответ 'none' давал ДЕДЛОК — config.py отвечал "applied", а гейт
        # продолжал требовать то же решение, пока прогон не уходил в R4-override.
        if entry.get("none_is_value"):
            return "none"
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
