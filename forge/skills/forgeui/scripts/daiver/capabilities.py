"""capabilities.py — что есть на машине для проверки страницы (спека §14.2 `daiver_capabilities`, INV-F11, INV-A8, QG N8; задача 4.1).

Первый ход итерации проверки: какой браузер будет запущен и чем ещё располагает репо. Сессию браузера не открывает: версию берёт из
пакета (macOS) или каталога (Windows), иначе коротким `<браузер> --version` (без окна, с таймаутом); так же `node --version`. Ничего
не пишет, ничего не скачивает и не ставит.

  • `browser` — {found, path, version, product, source, detail} либо {found: false, source, reason}; поиск — `browser/find.py`
    (переменная CHROMIUM_EXECUTABLE_PATH → verify.browserPath / verify.browserChannel → стандартные места → PATH). Нет браузера —
    `ready: false` и названный останов `blocked: no-browser` с тем, кто и что должен дать (`supply`), и с прямым запретом ставить его
    самому: цикл проверки не стартует (N8).
  • `node` — {found, path, version}: нужен только запасному node-движку DS-скана и прогону команд репо.
  • `typescript` — {found, version}: пакет `typescript` в `node_modules` репо или его родителей (как его ищет сам node).
  • `playwright` — {found, installed, version}: `@playwright/test` репо. Страницу снимает встроенный клиент DevTools (playwright не нужен);
    пакет нужен только e2e-гейту (5.5): есть в package.json — гейт гоняет тесты, нет — `skipped`. Так отличается от спеки, где
    playwright-core — драйвер браузера (README, «Отклонения»).
  • `vision` — {enabled, note}: `verify.vision` конфига; картинки смотрит сам агент и ничего не уходит наружу (INV-A8); выключено —
    визуальный обзор записывается `skipped` кодом.
`warnings` — то, что надо пересказать человеку (браузер старше Chrome 109, битые ключи `verify.browser*` конфига).

Все внешние входы подменяются параметрами (`env`, `platform`, `which`, `run`, `find`) — тесты не зависят от машины.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import config as CFG
from .browser import find as BF

NODE_TIMEOUT = 10.0
VISION_ON = "картинки смотрит сам агент; ничего не загружается наружу"
VISION_OFF = "verify.vision: off — визуальный обзор записывается как skipped; картинки не описывать"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _package_version(path: Path) -> Optional[str]:
    version = (_read_json(path) or {}).get("version")
    return version if isinstance(version, str) and version else None


def _node(which: Callable[..., Optional[str]], env: Mapping[str, str], run: Callable[..., Any]) -> Dict[str, Any]:
    path = which("node", path=env.get("PATH", ""))
    if not path:
        return {"found": False}
    version = None
    try:
        proc = run([path, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=NODE_TIMEOUT,
                   stdin=subprocess.DEVNULL)
        out = (proc.stdout or "").strip()
        version = out if out.startswith("v") else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"found": True, "path": path, "version": version}


def _typescript(root: Path) -> Dict[str, Any]:
    """`typescript` так, как его найдёт `require` из корня репо: node_modules корня и всех родителей."""
    for base in [root] + list(root.parents):
        version = _package_version(base / "node_modules" / "typescript" / "package.json")
        if version:
            return {"found": True, "version": version}
    return {"found": False}


def _playwright(root: Path) -> Dict[str, Any]:
    """`@playwright/test` репо: объявлен в package.json (его читает e2e-гейт) и/или стоит в node_modules."""
    declared = None
    pkg = _read_json(root / "package.json") or {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps = pkg.get(section)
        if isinstance(deps, dict) and isinstance(deps.get("@playwright/test"), str):
            declared = deps["@playwright/test"]
            break
    installed = None
    for base in [root] + list(root.parents):
        installed = _package_version(base / "node_modules" / "@playwright" / "test" / "package.json")
        if installed:
            break
    return {"found": declared is not None, "installed": installed is not None, "version": installed or declared}


def capabilities(root: Any, *, env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None,
                 which: Optional[Callable[..., Optional[str]]] = None, run: Optional[Callable[..., Any]] = None,
                 find: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Возможности машины для проверки страницы репо `root`. Не бросает; браузера нет — `ready: false` и останов `no-browser`.
    `find` подменяет `browser.find.find_browser` (та же сигнатура) — для тестов «браузера нет», не зависящих от машины."""
    root = Path(root).resolve()
    env_map: Mapping[str, str] = os.environ if env is None else env
    which_fn = which or shutil.which
    run_fn = run or subprocess.run
    loaded = CFG.load(root)
    verify = loaded["config"]["verify"]
    warnings: List[str] = [w for w in loaded["warnings"] if w.startswith("verify.browser")]

    found = (find or BF.find_browser)(verify, root=root, env=env_map, platform=platform, which=which_fn)
    browser: Dict[str, Any]
    if found["found"]:
        ver = BF.browser_version(found["path"], platform=platform or sys.platform, run=run_fn)
        browser = {"found": True, "path": found["path"], "version": ver["version"], "product": ver["product"],
                   "source": found["source"], "detail": found["detail"]}
        major = BF.major(ver["version"])
        if major is not None and major < BF.MIN_HEADLESS_NEW:
            warnings.append(f"браузер {ver['version']}: --headless=new есть с Chrome {BF.MIN_HEADLESS_NEW}, запуск может не получиться — "
                            f"обновите браузер")
    else:
        browser = {"found": False, "source": found.get("source"), "reason": found["reason"]}

    out: Dict[str, Any] = {"ready": bool(found["found"])}
    if not found["found"]:                                   # останов — сразу за ready: первое, что должна прочесть слабая модель
        stop = BF.blocked_answer(found["reason"])
        for key in ("blocked", "supply", "nextStep"):
            out[key] = stop[key]
    out.update({"browser": browser, "node": _node(which_fn, env_map, run_fn), "typescript": _typescript(root),
                "playwright": _playwright(root),
                "vision": {"enabled": bool(verify["vision"]), "note": VISION_ON if verify["vision"] else VISION_OFF},
                "warnings": warnings})
    return out
