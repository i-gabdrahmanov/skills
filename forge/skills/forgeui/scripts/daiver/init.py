"""init.py — первый запуск forge-ui в репо: детект с доказательствами + запись конфига по ответам.

daiver_init работает в два хода (спека §1.4, INV-A5, INV-F14):
  1. без ответов — только ДЕТЕКТ: кандидаты с evidence (package.json, lock-файл, каталоги,
     файлы конвенций) и список вопросов. Ничего не пишет и ничего не решает;
  2. с ответами — пишет `.daiver/config.yaml` (и `component-map.yaml`, если его нет).
     Ответы проверяются тем же строгим читателем, что и готовый конфиг: недостающее или
     недопустимое называется списком одним сообщением. Существующий config.yaml не
     перезаписывается никогда — правка дальше руками; карта компонентов — тем более.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config as C
from . import yamlite

LOCKFILES = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm"))
OUTPUT_DIR_CANDIDATES = ("src/modules", "src/features", "src/pages", "src/screens", "src/widgets",
                         "src/components")
CONVENTION_FILES = ("GIGACODE.md", "AGENTS.md", "QWEN.md", "CLAUDE.md", "CONTRIBUTING.md",
                    "docs/architecture.md", "docs/conventions.md", "docs/code-style.md")
COLOR_FILES = ("colors.less", "colors.scss", "colors.css", "variables.less", "variables.scss",
               "tokens.css", "tokens.less", "theme.less")
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".gigacode", ".daiver", "coverage", ".next"}

QUESTIONS = [
    {"id": "framework", "ask": "Фреймворк UI: react или vue?"},
    {"id": "language", "ask": "Язык: ts или js?"},
    {"id": "styling", "ask": "Стили: less | css-modules | tailwind | styled?"},
    {"id": "outputDir", "ask": "Куда строить модули экранов (outputDir, путь от корня репо)?"},
    {"id": "buildCommand", "ask": "Команда сборки/проверки типов проекта (её запускает гейт "
                                  "ui-build), например `npm run build` или `npx tsc --noEmit`?"},
    {"id": "librarySource", "ask": "Откуда читать сигнатуры компонентной библиотеки: "
                                   "storybook-mcp (MCP-сервер сторибука) | stories (файлы сторей) | "
                                   "sources (исходники/типы библиотеки)?"},
    {"id": "storybookMcp", "ask": "Имя MCP-сервера сторибука (если source: storybook-mcp)."},
    {"id": "scanPaths", "ask": "Каталоги/файлы библиотеки для сканирования (если stories|sources)."},
    {"id": "libraryImport", "ask": "Пакет импорта компонентов библиотеки, например @your/ui."},
    {"id": "conventions", "ask": "Файлы конвенций проекта (архитектура, нейминг, стили) — "
                                 "сборка читает их перед первой строкой. Нет — пустой список."},
    {"id": "vision", "ask": "Модель видит картинки (визуальный обзор включать)? on | off"},
]


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _walk_names(root: Path, names: tuple, limit: int = 3000) -> List[str]:
    """Пути (от корня) файлов с именем из names; обход ограничен и не заходит в сборки."""
    found: List[str] = []
    seen = 0
    stack = [root]
    while stack and seen < limit:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            seen += 1
            if e.is_dir():
                if e.name not in _SKIP_DIRS and not e.name.startswith("."):
                    stack.append(e)
            elif e.name in names:
                found.append(str(e.relative_to(root)))
    return found


def detect(root: Path) -> Dict[str, Any]:
    """Кандидаты для конфига с доказательствами. Решений не принимает."""
    root = Path(root)
    pkg = _read_json(root / "package.json")
    deps: Dict[str, str] = {}
    scripts: Dict[str, str] = {}
    if isinstance(pkg, dict):
        for k in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(pkg.get(k), dict):
                deps.update({str(n): str(v) for n, v in pkg[k].items()})
        if isinstance(pkg.get("scripts"), dict):
            scripts = {str(n): str(v) for n, v in pkg["scripts"].items()}
    sug: Dict[str, List[dict]] = {}

    def add(key: str, value: Any, evidence: str):
        sug.setdefault(key, []).append({"value": value, "evidence": evidence})

    if pkg is None:
        add("framework", None, "нет package.json в корне — это не фронтовый репо?")
    for fw in ("react", "vue"):
        if fw in deps:
            add("framework", fw, f"package.json: {fw} {deps[fw]}")
    if "typescript" in deps or (root / "tsconfig.json").is_file():
        add("language", "ts", "tsconfig.json / typescript в зависимостях")
    elif pkg is not None:
        add("language", "js", "нет tsconfig.json и typescript")
    if "tailwindcss" in deps:
        add("styling", "tailwind", f"package.json: tailwindcss {deps['tailwindcss']}")
    if "styled-components" in deps or "@emotion/styled" in deps:
        add("styling", "styled", "package.json: styled-components/@emotion")
    if "less" in deps:
        add("styling", "less", f"package.json: less {deps['less']}")
    pm = next((name for f, name in LOCKFILES if (root / f).is_file()), None)
    if pm:
        add("packageManager", pm, f"lock-файл {next(f for f, n in LOCKFILES if n == pm)}")
    run = {"npm": "npm run", "pnpm": "pnpm run", "yarn": "yarn"}.get(pm or "npm", "npm run")
    for s in ("typecheck", "type-check", "tsc", "build"):
        if s in scripts:
            add("buildCommand", f"{run} {s}", f"package.json scripts.{s}: {scripts[s]}")
    if "typescript" in deps and not any(s in scripts for s in ("typecheck", "type-check", "tsc")):
        add("buildCommand", "npx tsc --noEmit", "typescript в зависимостях, скрипта typecheck нет")
    for d in OUTPUT_DIR_CANDIDATES:
        if (root / d).is_dir():
            add("outputDir", d, f"каталог {d} существует")
    for f in CONVENTION_FILES:
        if (root / f).is_file():
            add("conventions", f, f"файл {f} в репо")
    if (root / ".storybook").is_dir():
        add("librarySource", "stories", "каталог .storybook в репо")
    for name in sorted(deps):
        low = name.lower()
        if any(t in low for t in ("ui-kit", "/ui", "design-system", "components", "-ui")) and \
                not low.startswith(("@types/", "react-dom", "@testing-library")):
            add("libraryImport", name, f"package.json: {name} {deps[name]}")
    for f in _walk_names(root, COLOR_FILES):
        add("colorsFile", f, "файл цветовых переменных")
    return {"suggestions": sug, "questions": QUESTIONS,
            "note": "кандидаты — не решения: каждое значение подтверждает человек"}


def _yaml_str(v: Any) -> str:
    """Скаляр для YAML: строки — в двойных кавычках (пути с # и : не ломают разбор)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _yaml_list(items: List[Any], indent: str) -> str:
    if not items:
        return " []"
    return "".join(f"\n{indent}- {_yaml_str(x)}" for x in items)


def render_config(a: Dict[str, Any]) -> str:
    """config.yaml по ответам — с комментариями, чтобы файл можно было править руками."""
    conv = a.get("conventions") or []
    scan = a.get("scanPaths") or []
    vision = a.get("vision", "on")
    vision = "on" if vision in (True, "on", "true", None) else "off" if vision in (False, "off", "false") else vision
    return f"""# forge-ui: настройки проекта. Записан daiver_init по ответам; дальше правится руками.
# Схема и правила чтения — skills/forgeui (docs/daiver/daiver spec.md §5.1).
project:
  framework: {_yaml_str(a.get("framework"))}        # react | vue
  language: {_yaml_str(a.get("language"))}          # ts | js
  styling:
    mode: {_yaml_str(a.get("styling"))}             # less | css-modules | tailwind | styled
    colorsFile: {_yaml_str(a.get("colorsFile"))}    # цвета макета → переменные ТОЛЬКО по точному значению
  outputDir: {_yaml_str(a.get("outputDir"))}
  buildCommand: {_yaml_str(a.get("buildCommand"))}  # гейт ui-build запускает её как есть
  conventions:{_yaml_list(conv, "    ")}             # читаются сборкой перед первой строкой

library:
  mapFile: ./component-map.yaml
  source: {_yaml_str(a.get("librarySource"))}       # storybook-mcp | stories | sources
  storybookMcp: {_yaml_str(a.get("storybookMcp"))}
  scanPaths:{_yaml_list(scan, "    ")}

svg:
  render: {_yaml_str(a.get("svgRender") or "component")}   # component | url | inline
  assetsDir: assets

layout:
  infer: on                  # off — оставить абсолютную геометрию макета

workflow:
  mode: interactive          # interactive | autonomous — для прогонов, начатых человеком
  embeddedMode: autonomous   # для прогонов из директивы
  autonomous:
    maxVerifyIterations: 3   # жёсткий потолок итераций verify без человека
  autoRoute: signal          # signal | ask | off
  confirmPlan: true
  confirmResolutions: medium # high | medium | low — вердикт с такой уверенностью и ниже спрашивается; low — всегда
  warnAfterVerifyIterations: 3
  weightBudget: 24000        # максимум секции, минифицированные байты канона

verify:
  viewport: {{ width: 1280, height: 900 }}
  visualDiffThreshold: 0.05  # подсказка, не блокирующий гейт
  vision: {vision}               # on | off — видит ли модель картинки
  # browserPath / browserChannel — только когда проекту нужен другой браузер, чем машинный дефолт (путь обычно в переменной
  # окружения CHROMIUM_EXECUTABLE_PATH); browserChannel: chrome | chrome-beta | chrome-dev | chrome-canary | chromium | msedge | …
  # tolerances: {{ px: 2, color: 2, fontSize: 0.5, lineHeight: 1, sizeRatio: 0.01, sizeRatioMaxPx: 8 }}
"""


def render_map(a: Dict[str, Any]) -> str:
    imp = a.get("libraryImport")
    icons = a.get("iconsImport")
    return f"""# Карта компонентов forge-ui: как узел макета становится компонентом библиотеки.
# Правило из карты — вердикт high без чтения библиотеки. Новые правила добавляются ТОЛЬКО
# после явного «да» пользователя (daiver_map_append); автономный прогон карту не пишет.
library:
  name: {_yaml_str(a.get("libraryName") or imp)}
  import: {_yaml_str(imp)}
icons:
  import:
    path: {_yaml_str(icons)}   # иконки часто живут в отдельном пакете
  fallback: ask                # ask | placeholder
rules: []
"""


def init(root: Path, answers: Dict[str, Any]) -> Dict[str, Any]:
    """Запись конфига по ответам. Отказ — {ok: False, problems: [...]}, ничего не записано."""
    root = Path(root)
    from . import run as RUN
    why = RUN.refuse_autonomous(root, "инициализация проекта (daiver_init)")
    if why:
        return {"ok": False, "problems": [why]}
    cfg_path = root / C.CONFIG_REL
    if cfg_path.exists():
        return {"ok": False, "problems": [
            f"{C.CONFIG_REL} уже есть — init его не перезаписывает; правка конфига — руками"]}
    if not isinstance(answers, dict):
        return {"ok": False, "problems": ["answers: ожидается объект с ответами пользователя"]}
    text = render_config(answers)
    try:
        raw = yamlite.loads(text)
    except yamlite.YamlError as e:
        return {"ok": False, "problems": [f"ответы не складываются в YAML: {e}"]}
    cfg, warnings, problems = C.normalize(raw)
    missing = [q["id"] for q in QUESTIONS[:6] if not answers.get(q["id"])]
    problems = [f"нет ответа: {m}" for m in missing] + [p for p in problems
                                                         if not any(m in p for m in missing)]
    if warnings:
        problems.extend(f"недопустимое значение — {w}" for w in warnings)
    if problems:
        return {"ok": False, "problems": problems}
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text, encoding="utf-8")
    written = [str(C.CONFIG_REL)]
    map_path = root / C.MAP_REL
    notes: List[str] = []
    if not map_path.exists():
        map_path.write_text(render_map(answers), encoding="utf-8")
        written.append(str(C.MAP_REL))
    else:
        notes.append("component-map.yaml уже есть — оставлен как был")
    if not answers.get("libraryImport"):
        notes.append("пакет импорта библиотеки не задан — карта без library.import; импорты "
                     "не изобретаются, его спросят при первом резолве")
    return {"ok": True, "written": written, "config": cfg, "notes": notes}
