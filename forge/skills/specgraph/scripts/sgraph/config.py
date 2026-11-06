"""config.py — конфиг проекта spec-graph `.spec-graph/config.json`: схема, строгая проверка, первый запуск.

Конфиг — единственное место, где лежит специфика репо спеки: где исходники, что исключить, где vault,
как каталоги соотносятся с сервисами. В коде движка доменных путей нет (урок прототипа: `/Users/…`,
`docs/system-requirements` и `KidTopic` были зашиты в скрипты).

Первый запуск не угадывает: `detect` предлагает кандидатов в корни исходников с доказательствами
(сколько MD-файлов и какого объёма), выбирает пользователь, `init` записывает его ответ. Существующий
конфиг `init` не перезаписывает — правка конфига идёт руками или будущими инструментами с согласием.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIG_REL = ".spec-graph/config.json"
CONFIG_VERSION = 1
DEFAULT_VAULT = "docs/graph"
ADAPTERS = ("md",)

# Каталоги, которые никогда не исходники спеки: служебные forge/рантайма, зависимости, VCS.
_SKIP_DIRS = {".git", ".gigacode", ".spec-graph", "ground", "node_modules", ".obsidian", ".idea",
              ".vscode", "__pycache__", "build", "target", "dist"}
_TOP_LEVEL_KEYS = {"version", "vault", "sources", "exclude", "services", "ontology_version", "queue"}
_QUEUE_KEYS = {"parallel", "max_attempts", "budget_chars", "lease_s"}
_SOURCE_KEYS = {"root", "adapter", "kind_hint"}


def config_path(root: Path) -> Path:
    return Path(root) / CONFIG_REL


def _rel_ok(value: Any) -> bool:
    """Относительный путь внутри репо, прямые слэши, без `..` и `~`."""
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith(("/", "~")) or "\\" in value or (len(value) > 1 and value[1] == ":"):
        return False
    return ".." not in Path(value).parts


def validate(cfg: Any) -> List[str]:
    """Нарушения схемы одним списком (слабой модели легче починить разом)."""
    if not isinstance(cfg, dict):
        return ["конфиг: ожидается JSON-объект"]
    errors: List[str] = []
    for k in sorted(set(cfg) - _TOP_LEVEL_KEYS):
        errors.append(f"неизвестный ключ «{k}» (допустимы: {', '.join(sorted(_TOP_LEVEL_KEYS))})")
    if cfg.get("version") != CONFIG_VERSION:
        errors.append(f"version: ожидается {CONFIG_VERSION}")
    if not _rel_ok(cfg.get("vault")):
        errors.append("vault: относительный путь внутри репо (по умолчанию docs/graph)")
    sources = cfg.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources: непустой список корней исходников спеки")
        sources = []
    seen = set()
    for i, s in enumerate(sources):
        where = f"sources[{i}]"
        if not isinstance(s, dict):
            errors.append(f"{where}: ожидается объект {{root, adapter}}")
            continue
        for k in sorted(set(s) - _SOURCE_KEYS):
            errors.append(f"{where}: неизвестный ключ «{k}»")
        if not _rel_ok(s.get("root")):
            errors.append(f"{where}.root: относительный путь внутри репо")
        elif s["root"] in seen:
            errors.append(f"{where}.root: «{s['root']}» указан дважды")
        else:
            seen.add(s["root"])
        if s.get("adapter", "md") not in ADAPTERS:
            errors.append(f"{where}.adapter: допустимы {', '.join(ADAPTERS)}")
        if "kind_hint" in s and not isinstance(s["kind_hint"], str):
            errors.append(f"{where}.kind_hint: строка")
    vault = cfg.get("vault")
    if isinstance(vault, str):
        for r in seen:
            if r == vault or r.startswith(vault.rstrip("/") + "/") or vault.startswith(r.rstrip("/") + "/"):
                errors.append(f"vault «{vault}» и исходник «{r}» вложены друг в друга: граф пишется рядом с "
                              f"исходником, а не внутрь него")
    exclude = cfg.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(x, str) and x for x in exclude):
        errors.append("exclude: список glob-шаблонов (строки)")
    services = cfg.get("services", {})
    if not isinstance(services, dict) or not all(isinstance(k, str) and isinstance(v, str) and v
                                                 for k, v in services.items()):
        errors.append("services: объект «путь каталога или файла → имя сервиса»")
    if not isinstance(cfg.get("ontology_version", 1), int):
        errors.append("ontology_version: целое")
    queue = cfg.get("queue", {})
    if not isinstance(queue, dict):
        errors.append("queue: объект {parallel, max_attempts, budget_chars, lease_s}")
    else:
        for k in sorted(set(queue) - _QUEUE_KEYS):
            errors.append(f"queue: неизвестный ключ «{k}»")
        for k in sorted(set(queue) & _QUEUE_KEYS):
            v = queue[k]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                errors.append(f"queue.{k}: целое > 0")
    return errors


def load(root: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """(конфиг, ошибки). Нет файла — (None, []); битый — (None, [причина])."""
    p = config_path(root)
    if not p.is_file():
        return None, []
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"{CONFIG_REL} не читается: {e}"]
    errors = validate(cfg)
    return (cfg if not errors else None), errors


def detect(root: Path, limit: int = 8) -> List[Dict[str, Any]]:
    """Кандидаты в корни исходников: каталоги с MD-файлами, с доказательствами.

    Кандидат — каталог верхнего уровня или первого уровня внутри `docs/` (так обычно раскладывают
    спеку по сервисам). Ранжирование — по числу и объёму MD; решает пользователь, не движок."""
    root = Path(root)
    counts: Dict[str, List[int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        rel = Path(dirpath).relative_to(root)
        md = [f for f in filenames if f.lower().endswith(".md")]
        if not md or not rel.parts:
            continue
        parts = rel.parts
        key = "/".join(parts[:2]) if parts[0] == "docs" and len(parts) >= 2 else parts[0]
        acc = counts.setdefault(key, [0, 0])
        acc[0] += len(md)
        acc[1] += sum((Path(dirpath) / f).stat().st_size for f in md)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    return [{"root": k, "md_files": n, "bytes": b,
             "evidence": f"{n} MD-файлов, {b // 1024} КБ"} for k, (n, b) in ranked[:limit]]


def build(answers: Dict[str, Any]) -> Dict[str, Any]:
    """Конфиг из ответов первого запуска: sources (список корней), vault, exclude, services."""
    roots = answers.get("sources")
    if isinstance(roots, list):
        sources = [r if isinstance(r, dict) else {"root": r, "adapter": "md"} for r in roots]
    else:
        sources = roots
    return {"version": CONFIG_VERSION,
            "vault": answers.get("vault") or DEFAULT_VAULT,
            "sources": sources,
            "exclude": answers.get("exclude", []),
            "services": answers.get("services", {}),
            "ontology_version": 1}


def init(root: Path, answers: Dict[str, Any]) -> Dict[str, Any]:
    """Записать конфиг по ответам. Существующий не перезаписывается; ошибки схемы — без записи."""
    p = config_path(root)
    if p.exists():
        return {"status": "exists", "config": CONFIG_REL,
                "hint": "конфиг уже есть и не перезаписывается; правка — руками в файле"}
    cfg = build(answers)
    errors = validate(cfg)
    for i, s in enumerate(cfg.get("sources") or []):
        if isinstance(s, dict) and _rel_ok(s.get("root")) and not (Path(root) / s["root"]).exists():
            errors.append(f"sources[{i}].root: «{s['root']}» не существует в репо")
    if errors:
        return {"status": "invalid", "errors": errors}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "written", "config": CONFIG_REL, "vault": cfg["vault"],
            "sources": [s["root"] for s in cfg["sources"]]}
