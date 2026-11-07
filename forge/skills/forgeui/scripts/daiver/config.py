"""config.py — `.daiver/config.yaml` forge-ui: дефолты и СТРОГОЕ чтение (спека §5.1, INV-B16).

Правила чтения — поведенческие контракты, не удобства:
  • битый YAML не роняет вызывающего — он получает дефолты и предупреждение;
  • значение читается строго: `weightBudget` — только положительное число (опечатка не даёт
    плохой бюджет), `workflow.mode` — только точными словами и только в блоке workflow;
    распознанное, но недопустимое значение → дефолт + предупреждение;
  • списанный ключ (`workflow.maxVerifyIterations` верхнего уровня) игнорируется — из другого
    места он работать не должен;
  • `layout.infer` включён по умолчанию, выключают ровно `off` и `false`;
  • viewport — только целые {width, height} > 0; допуски — каждый ключ падает в свой дефолт сам.
Источник сигнатур библиотеки (`library.source`) не угадывается: без него проект не
инициализирован (INV-A5).

Дополнения forge-ui к схеме спеки: `project.buildCommand` (гейт сборки запускает его
детерминированно) и `verify.vision: on|off` (зрение модели; при off визуальный обзор
записывается skipped кодом). `verify.browserPath` (путь, `~/…` или от корня репо) и `verify.browserChannel` (канал Chrome/
Edge/Chromium) — из шаблона спеки, нужны, только когда проекту нужен не машинный браузер; путь выигрывает у канала, обе
переменные окружения и конфиг читает `browser/find.py`. Телеметрии (§17) в forge-ui нет — ключ `telemetry` игнорируется.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import yamlite

CONFIG_REL = Path(".daiver") / "config.yaml"
MAP_REL = Path(".daiver") / "component-map.yaml"

FRAMEWORKS = ("react", "vue")
LANGUAGES = ("ts", "js")
STYLING = ("less", "css-modules", "tailwind", "styled")
LIBRARY_SOURCES = ("storybook-mcp", "stories", "sources")
SVG_RENDER = ("component", "url", "inline")
WORKFLOW_MODES = ("interactive", "autonomous")
AUTO_ROUTE = ("signal", "ask", "off")
CONFIDENCE = ("high", "medium", "low")
BROWSER_CHANNELS = ("chrome", "chrome-beta", "chrome-dev", "chrome-canary", "chromium",
                    "msedge", "msedge-beta", "msedge-dev", "msedge-canary")

DEFAULT_TOLERANCES = {"px": 2, "color": 2, "fontSize": 0.5, "lineHeight": 1,
                      "sizeRatio": 0.01, "sizeRatioMaxPx": 8}

DEFAULTS: Dict[str, Any] = {
    "project": {"framework": "react", "language": "ts", "styling": {"mode": "less", "colorsFile": None},
                "outputDir": "src/modules", "conventions": [], "buildCommand": None},
    "library": {"mapFile": "./component-map.yaml", "source": None, "storybookMcp": None,
                "scanPaths": []},
    "svg": {"render": "component", "assetsDir": "assets"},
    "layout": {"infer": True},
    "workflow": {"mode": "interactive", "embeddedMode": "autonomous",
                 "autonomous": {"maxVerifyIterations": 3}, "autoRoute": "signal",
                 "confirmPlan": True, "confirmResolutions": "medium",
                 "warnAfterVerifyIterations": 3, "weightBudget": 24000},
    "verify": {"viewport": {"width": 1280, "height": 900}, "visualDiffThreshold": 0.05,
               "tolerances": dict(DEFAULT_TOLERANCES), "editorUrlTemplate": "vscode://file/{path}:{line}:{column}",
               "vision": True, "browserPath": None, "browserChannel": None},
}


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _enum(raw: Any, allowed: Tuple[str, ...], default: Any, key: str, warnings: List[str]) -> Any:
    if raw is None:
        return default
    if isinstance(raw, str) and raw in allowed:
        return raw
    warnings.append(f"{key}: «{raw}» — ожидается одно из {', '.join(allowed)}; взят дефолт «{default}»")
    return default


def _switch(raw: Any, default: bool, key: str, warnings: List[str], off_words=("off", "false"),
            on_words=("on", "true")) -> bool:
    """on|off-переключатель: выключают ТОЛЬКО точные написания (опечатка не должна молча
    поменять поведение)."""
    if raw is None:
        return default
    if raw is False or (isinstance(raw, str) and raw in off_words):
        return False
    if raw is True or (isinstance(raw, str) and raw in on_words):
        return True
    warnings.append(f"{key}: «{raw}» — не on/off; взят дефолт «{'on' if default else 'off'}»")
    return default


def normalize(raw: Any) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Сырой разбор YAML → (эффективный конфиг, warnings, problems).

    problems — то, без чего проект не инициализирован (источник библиотеки, buildCommand…);
    warnings — значения, замещённые дефолтом."""
    cfg = copy.deepcopy(DEFAULTS)
    warnings: List[str] = []
    problems: List[str] = []
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        warnings.append("config.yaml: корень не словарь — взяты дефолты")
        raw = {}

    p = cfg["project"]
    p["framework"] = _enum(_get(raw, "project", "framework"), FRAMEWORKS, p["framework"],
                           "project.framework", warnings)
    p["language"] = _enum(_get(raw, "project", "language"), LANGUAGES, p["language"],
                          "project.language", warnings)
    p["styling"]["mode"] = _enum(_get(raw, "project", "styling", "mode"), STYLING,
                                 p["styling"]["mode"], "project.styling.mode", warnings)
    colors = _get(raw, "project", "styling", "colorsFile")
    if colors is not None:
        if isinstance(colors, str) and colors.strip():
            p["styling"]["colorsFile"] = colors.strip()
        else:
            warnings.append("project.styling.colorsFile: не путь — цвета пойдут сырыми значениями")
    out_dir = _get(raw, "project", "outputDir")
    if out_dir is not None:
        if isinstance(out_dir, str) and out_dir.strip() and not Path(out_dir).is_absolute() \
                and ".." not in Path(out_dir).parts:
            p["outputDir"] = out_dir.strip().rstrip("/")
        else:
            problems.append("project.outputDir: нужен относительный путь внутри репо")
    conv = _get(raw, "project", "conventions")
    if conv is not None:
        if isinstance(conv, list) and all(isinstance(c, str) for c in conv):
            p["conventions"] = conv
        else:
            warnings.append("project.conventions: ожидается список путей к файлам конвенций")
    build = _get(raw, "project", "buildCommand")
    if isinstance(build, str) and build.strip():
        p["buildCommand"] = build.strip()
    else:
        problems.append("project.buildCommand не задан — гейт сборки нечем запускать "
                        "(спрашивается при первом запуске, не угадывается)")

    lib = cfg["library"]
    for k in ("mapFile",):
        v = _get(raw, "library", k)
        if isinstance(v, str) and v.strip():
            lib[k] = v.strip()
    src = _get(raw, "library", "source")
    if src is None:
        problems.append("library.source не задан — источник сигнатур библиотеки "
                        "(storybook-mcp | stories | sources) спрашивается, не угадывается")
    elif src not in LIBRARY_SOURCES:
        problems.append(f"library.source: «{src}» — ожидается storybook-mcp | stories | sources")
    else:
        lib["source"] = src
        if src == "storybook-mcp":
            name = _get(raw, "library", "storybookMcp")
            if isinstance(name, str) and name.strip() and not name.strip().startswith("<"):
                lib["storybookMcp"] = name.strip()
            else:
                problems.append("library.storybookMcp обязателен при source: storybook-mcp "
                                "(имя MCP-сервера сторибука в настройках GigaCode)")
        else:
            paths = _get(raw, "library", "scanPaths")
            if isinstance(paths, list) and paths and all(isinstance(x, str) and x.strip() for x in paths):
                lib["scanPaths"] = [x.strip() for x in paths]
            else:
                problems.append(f"library.scanPaths обязателен при source: {src} "
                                f"(каталоги/файлы библиотеки со сторями или исходниками)")

    svg = cfg["svg"]
    svg["render"] = _enum(_get(raw, "svg", "render"), SVG_RENDER, svg["render"], "svg.render", warnings)
    adir = _get(raw, "svg", "assetsDir")
    if isinstance(adir, str) and adir.strip():
        svg["assetsDir"] = adir.strip()

    cfg["layout"]["infer"] = _switch(_get(raw, "layout", "infer"), True, "layout.infer", warnings)

    wf = cfg["workflow"]
    wf_raw = _get(raw, "workflow")
    if wf_raw is not None and not isinstance(wf_raw, dict):
        warnings.append("workflow: не словарь — взяты дефолты")
        wf_raw = {}
    wf_raw = wf_raw or {}
    wf["mode"] = _enum(wf_raw.get("mode"), WORKFLOW_MODES, wf["mode"], "workflow.mode", warnings)
    wf["embeddedMode"] = _enum(wf_raw.get("embeddedMode"), WORKFLOW_MODES, wf["embeddedMode"],
                               "workflow.embeddedMode", warnings)
    wf["autoRoute"] = _enum(wf_raw.get("autoRoute"), AUTO_ROUTE, wf["autoRoute"], "workflow.autoRoute",
                            warnings)
    wf["confirmResolutions"] = _enum(wf_raw.get("confirmResolutions"), CONFIDENCE,
                                     wf["confirmResolutions"], "workflow.confirmResolutions", warnings)
    cp = wf_raw.get("confirmPlan")
    if cp is not None:
        if isinstance(cp, bool):
            wf["confirmPlan"] = cp
        else:
            warnings.append(f"workflow.confirmPlan: «{cp}» — ожидается true/false; взят дефолт true")
    for key, path, default in (("warnAfterVerifyIterations", ("warnAfterVerifyIterations",), 3),):
        v = wf_raw.get(key)
        if v is not None:
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                wf[key] = v
            else:
                warnings.append(f"workflow.{key}: «{v}» — ожидается целое > 0; взят дефолт {default}")
    mx = _get(wf_raw, "autonomous", "maxVerifyIterations")
    if mx is not None:
        if isinstance(mx, int) and not isinstance(mx, bool) and mx > 0:
            wf["autonomous"]["maxVerifyIterations"] = mx
        else:
            warnings.append(f"workflow.autonomous.maxVerifyIterations: «{mx}» — ожидается целое > 0")
    if "maxVerifyIterations" in wf_raw:
        warnings.append("workflow.maxVerifyIterations — списанный ключ, игнорируется "
                        "(потолок автономии живёт в workflow.autonomous.maxVerifyIterations)")
    wb = wf_raw.get("weightBudget")
    if wb is not None:
        n = _num(wb)
        if n is not None and n > 0:
            wf["weightBudget"] = int(n) if n == int(n) else n
        else:
            warnings.append(f"workflow.weightBudget: «{wb}» — только положительное число; "
                            f"взят дефолт {DEFAULTS['workflow']['weightBudget']}")

    vf = cfg["verify"]
    vp = _get(raw, "verify", "viewport")
    if vp is not None:
        w, h = _get(vp, "width"), _get(vp, "height")
        if all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in (w, h)):
            vf["viewport"] = {"width": w, "height": h}
        else:
            warnings.append("verify.viewport: нужны целые width/height > 0; взят 1280×900")
    th = _get(raw, "verify", "visualDiffThreshold")
    if th is not None:
        n = _num(th)
        if n is not None and n >= 0:
            vf["visualDiffThreshold"] = n
        else:
            warnings.append("verify.visualDiffThreshold: не число ≥ 0; взят 0.05")
    tol = _get(raw, "verify", "tolerances")
    if tol is not None:
        if not isinstance(tol, dict):
            warnings.append("verify.tolerances: не словарь — все допуски по умолчанию")
        else:
            for key, default in DEFAULT_TOLERANCES.items():
                if key in tol:
                    n = _num(tol[key])
                    if n is not None and n >= 0:
                        vf["tolerances"][key] = n
                    else:
                        warnings.append(f"verify.tolerances.{key}: «{tol[key]}» — взят дефолт {default}")
            for key in tol:
                if key not in DEFAULT_TOLERANCES:
                    warnings.append(f"verify.tolerances.{key}: неизвестный ключ, игнорируется")
    eut = _get(raw, "verify", "editorUrlTemplate")
    if isinstance(eut, str) and eut.strip():
        vf["editorUrlTemplate"] = eut.strip()
    vf["vision"] = _switch(_get(raw, "verify", "vision"), True, "verify.vision", warnings)
    bp = _get(raw, "verify", "browserPath")
    if bp is not None:
        if isinstance(bp, str) and bp.strip():
            vf["browserPath"] = bp.strip()
        else:
            warnings.append("verify.browserPath: не путь — игнорируется, браузер ищется по умолчанию")
    bc = _get(raw, "verify", "browserChannel")
    if bc is not None:
        if isinstance(bc, str) and bc in BROWSER_CHANNELS:
            vf["browserChannel"] = bc
        else:
            warnings.append(f"verify.browserChannel: «{bc}» — ожидается одно из {', '.join(BROWSER_CHANNELS)}; игнорируется")
    return cfg, warnings, problems


def load(root: Path) -> Dict[str, Any]:
    """Эффективный конфиг проекта. Никогда не бросает: битый файл → дефолты + предупреждение.

    {exists, path, config, warnings, problems, initialized}"""
    path = Path(root) / CONFIG_REL
    out: Dict[str, Any] = {"exists": path.is_file(), "path": str(path), "warnings": [], "problems": []}
    raw: Any = None
    if out["exists"]:
        try:
            raw = yamlite.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yamlite.YamlError) as e:
            out["warnings"].append(f"config.yaml не разобран ({e}) — взяты дефолты")
            raw = None
    cfg, warnings, problems = normalize(raw)
    out["config"] = cfg
    out["warnings"].extend(warnings)
    if not out["exists"]:
        problems = ["нет .daiver/config.yaml — проект не инициализирован (первый запуск forge-ui "
                    "спрашивает настройки и пишет его через daiver_init)"]
    out["problems"] = problems
    out["initialized"] = out["exists"] and not problems and raw is not None
    return out
