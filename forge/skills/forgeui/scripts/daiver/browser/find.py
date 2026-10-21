"""find.py — поиск Chrome/Chromium на машине (спека §14.2, §5.1 `verify.browserPath` / `verify.browserChannel`, INV-F11, QG N8; задача 4.1).

Правило одно: браузер ИЩЕТСЯ, а не ставится. Ни загрузок, ни установки, ни сети: модуль только смотрит файлы и PATH. Сессия браузера
не открывается и при определении версии: macOS — `Info.plist` пакета `.app`, Windows — каталог версии рядом с `chrome.exe`, иначе
короткий `--version` с таймаутом (процесс без окна, вывод одной строкой).

Порядок. Первое ЗАДАННОЕ решает; заданное, но битое НЕ заменяется молча другим браузером — человек должен узнать, что его путь не
работает (так было у частного поиска `plan_contract`, и это сохранено):
  1. переменная окружения `CHROMIUM_EXECUTABLE_PATH` — файл, пакет `.app` (macOS) или имя команды из PATH;
  2. `verify.browserPath` конфига — абсолютный путь, `~/…` либо от корня репо;
  3. `verify.browserChannel` конфига — chrome | chrome-beta | chrome-dev | chrome-canary | chromium | msedge | msedge-beta |
     msedge-dev | msedge-canary: стандартные места этого канала;
  4. стандартные места платформы (macOS: /Applications и ~/Applications; Windows: Program Files, Program Files (x86), LocalAppData;
     Linux: /usr/bin, /opt, /snap) — Chrome и Chromium, порядок по платформе (macOS/Windows — Chrome, затем Chromium; Linux
     наоборот, как в задаче 4.1);
  5. PATH: google-chrome, google-chrome-stable, chromium, chromium-browser, chrome (на Linux chromium впереди).
Edge в поиск по умолчанию НЕ входит: он ставится в Windows всегда, и молчаливая подмена Chrome им дала бы другой рендер; Edge —
явный выбор человека (`verify.browserChannel: msedge`).

«Найден» значит «файл есть и исполняем» (на Windows — есть). Пустая переменная окружения считается незаданной.

Все входы подменяются параметрами (`env`, `platform`, `home`, `app_dirs`, `which`, `run`), поэтому тесты на фейковых исполняемых не
зависят от того, что стоит на машине разработчика. Не проверено: Windows и Linux на настоящих машинах — только раскладки по
документации браузеров (проверено на macOS с Chrome 155).
"""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

BROWSER_ENV = "CHROMIUM_EXECUTABLE_PATH"
CHANNELS = ("chrome", "chrome-beta", "chrome-dev", "chrome-canary", "chromium",
            "msedge", "msedge-beta", "msedge-dev", "msedge-canary")
MIN_HEADLESS_NEW = 109                       # `--headless=new` появился в Chrome 109
VERSION_TIMEOUT = 10.0
DEFAULT_ORDER = {"darwin": ("chrome", "chromium"), "win": ("chrome", "chromium"), "linux": ("chromium", "chrome")}

# macOS: канал → имя пакета (`<имя>.app/Contents/MacOS/<имя>`)
_MAC_APPS = {"chrome": "Google Chrome", "chrome-beta": "Google Chrome Beta", "chrome-dev": "Google Chrome Dev",
             "chrome-canary": "Google Chrome Canary", "chromium": "Chromium", "msedge": "Microsoft Edge",
             "msedge-beta": "Microsoft Edge Beta", "msedge-dev": "Microsoft Edge Dev", "msedge-canary": "Microsoft Edge Canary"}
# Windows: канал → путь внутри Program Files / LocalAppData
_WIN_PATHS = {"chrome": ("Google", "Chrome", "Application", "chrome.exe"),
              "chrome-beta": ("Google", "Chrome Beta", "Application", "chrome.exe"),
              "chrome-dev": ("Google", "Chrome Dev", "Application", "chrome.exe"),
              "chrome-canary": ("Google", "Chrome SxS", "Application", "chrome.exe"),
              "chromium": ("Chromium", "Application", "chrome.exe"),
              "msedge": ("Microsoft", "Edge", "Application", "msedge.exe"),
              "msedge-beta": ("Microsoft", "Edge Beta", "Application", "msedge.exe"),
              "msedge-dev": ("Microsoft", "Edge Dev", "Application", "msedge.exe"),
              "msedge-canary": ("Microsoft", "Edge SxS", "Application", "msedge.exe")}
_WIN_BASES = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
# Linux: канал → (абсолютные пути, имена команд из PATH)
_LINUX = {"chrome": (("/opt/google/chrome/chrome", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"),
                     ("google-chrome", "google-chrome-stable")),
          "chrome-beta": (("/opt/google/chrome-beta/chrome",), ("google-chrome-beta",)),
          "chrome-dev": (("/opt/google/chrome-unstable/chrome",), ("google-chrome-unstable",)),
          "chrome-canary": ((), ()),
          "chromium": (("/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"), ("chromium", "chromium-browser")),
          "msedge": (("/opt/microsoft/msedge/msedge",), ("microsoft-edge", "microsoft-edge-stable")),
          "msedge-beta": (("/opt/microsoft/msedge-beta/msedge",), ("microsoft-edge-beta",)),
          "msedge-dev": (("/opt/microsoft/msedge-dev/msedge",), ("microsoft-edge-dev",)),
          "msedge-canary": ((), ())}

Which = Callable[..., Optional[str]]


def _family(platform: str) -> str:
    return "darwin" if platform == "darwin" else "win" if platform.startswith(("win", "cygwin")) else "linux"


def _is_runnable(p: Path, family: str) -> bool:
    try:
        return p.is_file() and (family == "win" or os.access(str(p), os.X_OK))
    except OSError:
        return False


def _app_executable(app: Path) -> Optional[Path]:
    """Исполняемый файл пакета macOS: `CFBundleExecutable` из Info.plist, иначе файл с именем пакета."""
    try:
        with open(str(app / "Contents" / "Info.plist"), "rb") as fh:
            name = plistlib.load(fh).get("CFBundleExecutable")
        if isinstance(name, str) and name and "/" not in name:
            return app / "Contents" / "MacOS" / name
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    guess = app / "Contents" / "MacOS" / app.stem
    return guess if guess.exists() else None


def _check_given(raw: str, *, base: Optional[Path], home: Path, family: str, which: Which,
                 env: Mapping[str, str]) -> Tuple[Optional[str], str]:
    """Проверка пути, названного человеком: (путь | None, причина). Причина — для человека, по-русски, без пути (его назовёт
    вызывающий вместе с источником)."""
    text = raw.strip().strip('"').strip("'")
    if not text:
        return None, "пусто"
    seps = ["/", os.sep] + ([os.altsep] if os.altsep else [])
    if not any(s in text for s in seps) and not text.startswith("~"):
        found = which(text, path=env.get("PATH", ""))
        return (found, "") if found else (None, f"команды «{text}» нет в PATH")
    if text == "~" or text.startswith(("~/", "~" + os.sep)):
        p = home / text[2:]
    else:
        p = Path(text)
    if not p.is_absolute():
        p = (base or Path.cwd()) / p
    if p.is_dir():
        if p.suffix.lower() == ".app":
            exe = _app_executable(p)
            if exe is not None and _is_runnable(exe, family):
                return str(exe), ""
            return None, "в пакете .app нет исполняемого файла"
        return None, "это каталог, нужен исполняемый файл браузера"
    if not p.exists():
        return None, "файла нет"
    if not _is_runnable(p, family):
        return None, "файл не исполняемый (нет права на запуск)"
    return str(p), ""


def _channel_fixed(channel: str, family: str, env: Mapping[str, str], app_dirs: Sequence[Path], fs_root: Optional[Path]) -> List[Path]:
    """Стандартные места канала (фиксированные пути платформы)."""
    if family == "darwin":
        app = _MAC_APPS[channel]
        return [d / f"{app}.app" / "Contents" / "MacOS" / app for d in app_dirs]
    if family == "win":
        out: List[Path] = []
        for var in _WIN_BASES:
            base = env.get(var)
            if base:
                out.append(Path(base).joinpath(*_WIN_PATHS[channel]))
        return out
    return [fs_root / p.lstrip("/") if fs_root is not None else Path(p) for p in _LINUX[channel][0]]


def _channel_names(channel: str) -> Tuple[str, ...]:
    return _LINUX[channel][1]


def path_names(family: str) -> List[str]:
    """Имена команд для поиска в PATH — в порядке предпочтения платформы; последнее — голое `chrome`."""
    return [n for ch in DEFAULT_ORDER[family] for n in _channel_names(ch)] + ["chrome"]


def _dedupe(items: Sequence[Path]) -> List[Path]:
    seen, out = set(), []
    for it in items:
        if str(it) not in seen:
            seen.add(str(it))
            out.append(it)
    return out


def find_browser(config: Optional[Mapping[str, Any]] = None, *, root: Any = None, env: Optional[Mapping[str, str]] = None,
                 platform: Optional[str] = None, home: Any = None, app_dirs: Optional[Sequence[Any]] = None,
                 fs_root: Any = None, which: Optional[Which] = None) -> Dict[str, Any]:
    """Где браузер. `config` — блок `verify` эффективного конфига (`browserPath`, `browserChannel`); `root` — корень репо
    (относительный `browserPath` считается от него). `home`, `app_dirs` (каталоги пакетов macOS), `fs_root` (корень файловой системы
    для абсолютных путей Linux) и `env`/`platform`/`which` подменяются — тесты не видят то, что стоит на машине.

    → {found: True, path, source, detail} | {found: False, path: None, source, reason, tried}. `source`: env | config.browserPath |
    config.browserChannel | standard | PATH (у «не найден» — источник, на котором остановились, либо None). Не бросает."""
    env = os.environ if env is None else env
    which = which or shutil.which
    family = _family(platform or sys.platform)
    home_dir = Path(home) if home is not None else Path.home()
    dirs = [Path(d) for d in app_dirs] if app_dirs is not None else [Path("/Applications"), home_dir / "Applications"]
    root_fs = Path(fs_root) if fs_root is not None else None
    cfg = config or {}
    base = Path(root) if root is not None else None
    tried: List[str] = []

    def hit(path: str, source: str, detail: str) -> Dict[str, Any]:
        return {"found": True, "path": path, "source": source, "detail": detail}

    def miss(source: Optional[str], reason: str) -> Dict[str, Any]:
        return {"found": False, "path": None, "source": source, "reason": reason, "tried": tried}

    given = env.get(BROWSER_ENV)
    if given is not None and given.strip():
        path, why = _check_given(given, base=None, home=home_dir, family=family, which=which, env=env)
        if path:
            return hit(path, "env", f"переменная {BROWSER_ENV}")
        return miss("env", f"{BROWSER_ENV}={given.strip()}: {why}")
    tried.append(f"переменная {BROWSER_ENV} не задана")

    cfg_path = cfg.get("browserPath")
    if isinstance(cfg_path, str) and cfg_path.strip():
        path, why = _check_given(cfg_path, base=base, home=home_dir, family=family, which=which, env=env)
        if path:
            return hit(path, "config.browserPath", "verify.browserPath из .daiver/config.yaml")
        return miss("config.browserPath", f"verify.browserPath={cfg_path.strip()}: {why}")
    tried.append("verify.browserPath не задан")

    channel = cfg.get("browserChannel")
    if isinstance(channel, str) and channel.strip():
        if channel not in CHANNELS:
            return miss("config.browserChannel", f"verify.browserChannel: «{channel}» — ожидается одно из {', '.join(CHANNELS)}")
        for cand in _dedupe(_channel_fixed(channel, family, env, dirs, root_fs)):
            if _is_runnable(cand, family):
                return hit(str(cand), "config.browserChannel", f"verify.browserChannel: {channel}")
        for name in _channel_names(channel):
            found = which(name, path=env.get("PATH", ""))
            if found:
                return hit(found, "config.browserChannel", f"verify.browserChannel: {channel} (PATH: {name})")
        where = ", ".join(str(c) for c in _channel_fixed(channel, family, env, dirs, root_fs)[:3]) or "стандартных мест у канала на этой платформе нет"
        return miss("config.browserChannel", f"verify.browserChannel: {channel} — браузер этого канала не найден ({where})")
    tried.append("verify.browserChannel не задан")

    order = DEFAULT_ORDER[family]
    places: List[Path] = []
    for ch in order:
        places += _channel_fixed(ch, family, env, dirs, root_fs)
    places = _dedupe(places)
    for cand in places:
        if _is_runnable(cand, family):
            return hit(str(cand), "standard", "стандартный путь")
    sample = ", ".join(str(c) for c in places[:2])
    tried.append(f"стандартные места платформы пусты ({sample}{' …' if len(places) > 2 else ''})")
    names = path_names(family)
    for name in names:
        found = which(name, path=env.get("PATH", ""))
        if found:
            return hit(found, "PATH", f"PATH ({name})")
    tried.append(f"в PATH нет {', '.join(names[:4])}")
    return miss(None, "Chrome/Chromium на машине не найден: " + "; ".join(tried))


def blocked_answer(reason: str) -> Dict[str, Any]:
    """Стандартный ответ «браузера нет, цикл проверки не стартует» (N8): названный останов с тем, кто и что должен дать. Ничего не
    скачивается и не ставится — ни движком, ни моделью."""
    return {"skipped": True, "blocked": "no-browser", "reason": reason,
            "supply": ("человек: установить Chrome или Chromium либо указать путь переменной окружения "
                       f"{BROWSER_ENV} (или verify.browserPath / verify.browserChannel в .daiver/config.yaml)"),
            "nextStep": ("остановись и скажи пользователю причину дословно. Браузер не скачивай и не ставь (ни npx playwright "
                         "install, ни brew/apt/winget): это решение человека")}


_VERSION_RE = re.compile(r"^\s*(?P<product>.*?)\s*(?P<version>\d+\.\d+\.\d+\.\d+)")
_DIR_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _version_from_app(path: Path) -> Optional[Tuple[str, str]]:
    """(продукт, версия) из Info.plist пакета macOS, внутри которого лежит исполняемый файл."""
    for parent in path.parents:
        if parent.suffix.lower() == ".app":
            try:
                with open(str(parent / "Contents" / "Info.plist"), "rb") as fh:
                    plist = plistlib.load(fh)
            except (OSError, ValueError, plistlib.InvalidFileException):
                return None
            version = plist.get("CFBundleShortVersionString")
            return (parent.stem, version) if isinstance(version, str) and version else None
    return None


def _version_from_dirs(path: Path) -> Optional[str]:
    """Windows: рядом с chrome.exe лежит каталог с именем версии (120.0.6099.109) — без запуска процесса."""
    try:
        names = [d.name for d in path.parent.iterdir() if d.is_dir() and _DIR_VERSION_RE.match(d.name)]
    except OSError:
        return None
    return max(names, key=lambda v: tuple(int(x) for x in v.split("."))) if names else None


def browser_version(path: str, *, platform: Optional[str] = None,
                    run: Optional[Callable[..., Any]] = None) -> Dict[str, Optional[str]]:
    """{version, product} браузера по пути; None, где узнать нельзя. Не бросает и не висит дольше `VERSION_TIMEOUT`."""
    family = _family(platform or sys.platform)
    p = Path(path)
    if family == "darwin":
        got = _version_from_app(p)
        if got:
            return {"product": got[0], "version": got[1]}
    if family == "win":
        ver = _version_from_dirs(p)
        return {"product": None, "version": ver}
    try:
        proc = (run or subprocess.run)([str(p), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       timeout=VERSION_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {"product": None, "version": None}
    m = _VERSION_RE.match((proc.stdout or "").strip())
    if not m:
        return {"product": None, "version": None}
    return {"product": m.group("product") or None, "version": m.group("version")}


def major(version: Optional[str]) -> Optional[int]:
    """Главная версия («155.0.8059.85» → 155); None — версии нет."""
    if not version:
        return None
    head = version.split(".", 1)[0]
    return int(head) if head.isdigit() else None
