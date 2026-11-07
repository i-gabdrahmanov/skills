#!/usr/bin/env python3
"""ui-guard.py — PreToolUse: запреты daiver (слой F спеки, §18.6) кодом, а не текстом брифа (forgeui).

В daiver эти запреты — абзац GIGACODE.md, который слабая модель может пропустить. В forge-ui они —
exit 2. Четыре запрета (задача 3.3; INV F1, F2, F5, F12, C8, C9; QG N1, N3, N6, N9, N12):

  1. ЧТЕНИЕ канона и сырых экспортов макета (INV-F1/F2; N3, N12). `read_file`, `grep_search`,
     `read_many_files` и shell-читатели (`cat`, `head`, `tail`, `less`, `jq`, `grep`, `sed`, `awk`,
     `python -c …open` — любая команда, кроме перечисления и измерения: ls, stat, wc, file, du,
     контрольные суммы) не пускаются к `*.canonical.json`, `visual-requirements.pixso*.json`,
     `visual-requirements.json|html` и к ЛЮБОМУ файлу, чья голова — сигнатура Pixso DSL
     (`{"dsl":{"converterVersion"`), где бы он ни лежал: экспорт до `daiver_pixso_pull` называется как
     угодно. Поиск по каталогу ВНУТРИ `.daiver/` отказывает, если в поддереве (с учётом glob-фильтра)
     есть такой файл. Отказ называет замену: daiver_outline / daiver_section / daiver_source_nodes.
  2. ЗАПИСЬ КОДА МОДУЛЯ — любая запись в `project.outputDir` (`.daiver/config.yaml`; удаление и перенос
     `rm`/`mv`/`touch` — тоже правка) — только на шаге `ui-build` (C8). На `ui-verify` правка кода = переоткрытие `ui-build` (INV-F12, N9: между открытием
     итерации и решением человека код не правится); подсказка — как у sod-enforcer. Файлы СУЩЕСТВУЮЩЕЙ
     страницы, которую правит кадр трека (`sources` в track.yaml, задача 6.4), — тот же код: правила 2–4
     к ним применяются так же, как к outputDir (манифест не читается — список пуст, как без страниц).
  3. ПОДПИСЬ ПЛАНА (C9): запись на `ui-build` — только при действительной подписи. Её проверяет
     `daiver.plan_contract.signature_state`, а НЕ хеш файла `plan.lock.json`: lock — производный
     артефакт, правка plan.json руками его не пересобирает; верна проверка по lock'у, СОБРАННОМУ из
     текущих артефактов (согласие `ui-plan-<KEY>` с `bound_sha256` = sha256 этого lock'а). Резолвер
     `ui-spec.md` стори обязан быть тем же, что у гейта и инструмента (`tools._story_specs`): без него
     дайджест другой и ЗАКОННАЯ подпись читается как устаревшая (проверено на «Modal»). Вызов недёшев
     (перечитывает канон и вердикты), поэтому вердикт кешируется (см. «Кеш»).
  4. ИНЛАЙН-SVG (INV-F5; N1, N6): запись `.tsx/.jsx/.vue/.svelte/.ts/.js/.html` в outputDir, где есть
     `<svg` (или data-URI svg), — отказ. Исключение — ПОМЕЧЕННЫЙ плейсхолдер арта: `<svg>` с атрибутом
     `data-art-placeholder="<slug>"`, подписью `ART MISSING: <slug>.svg` и без нарисованного
     содержимого (допустимы лишь title/text/tspan/desc). Маркер — из `svg_assets.placeholder()`; тест
     сверяет его с живой функцией. Сам `daiver_svg_assets(moduleDir)` выдаёт плейсхолдер как `<div
     data-art-placeholder=…>`, то есть в штатном потоке `<svg` в коде не бывает вовсе.

Вне прогона forgeui хук — noop (как tech-guard вне forgetech). Внутри прогона — fail-CLOSED: крэш
хука отдал бы exit 1, а рантайм читает его как «возражений нет». Непредвиденная ошибка проверки и
ядро forge-ui, которое не грузится, — отказ с причиной.

Кеш подписи. Каждый хук — отдельный процесс, поэтому кеш — файл `ui-guard-cache.json` в каталоге
прогона (`ground/statements/forgeui/<KEY>/`; его пишет только этот хук, state-write-guard закрывает
запись моделью). Ключ — sha256 снимка stat'ов (размер, mtime, ctime, inode) всех входов подписи: файлов
`.daiver/**`, журнала согласий, ui-spec.md стори, зарегистрированных спек и файлов страницы, кода движка
и самого хука. ctime в ключе — чтобы подгонка mtime (`touch -r`) не выдавала старый вердикт за новый.

Данные. Сигнатура экспорта — голова образца «Modal» (`docs/daiver/dsl.json`, минифицирован, ОДНА строка:
`{"dsl":{"converterVersion":"2.2.13","dslVersion":"2.1.15",…`); имена файлов трека — `track.CANONICAL_FILE` /
`REQUIREMENT_FILE` / `RAW_FILE`; подпись, резолвер спеки и кеш проверены на подписанном плане «Modal»
(`test_plan_contract.ModalWorld`: сырой экспорт лежит в `.daiver/inbox/main.dsl.json`, как велит бриф
ui-design); `signature_state` на «Modal» занимает 4 мс (на большом экране не измерено — для него и кеш).
Не проверено данными: боевой прогон на GigaCode (как слабая модель читает отказы, настоящие имена параметров
читающих инструментов форка), Windows.

Не покрыто (best-effort по природе shell): поиск по каталогу, который СОДЕРЖИТ `.daiver/` (репозиторий
целиком) не отказывает — иначе недоступен любой grep по репо; файл с сигнатурой DSL внутри такого
поиска не ловится; `cd dir && …` не отслеживается; цель записи, собранная из переменных, не видна;
рукописный `.svg`-файл и CSS data-URI не проверяются (правило — про кодовые файлы).

Матчеры: Write/Edit-инструменты, shell и читающие инструменты. Блок: exit 2 + stderr.
"""
from __future__ import annotations

import fnmatch
import glob as _glob
import hashlib
import itertools
import json
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Импорт форж-модулей — fail-CLOSED (как у соседних хуков): exit 1 рантайм читает как «хук не
# возражает» и выполняет вызов. Всё, что на уровне модуля может упасть (в том числе реестр веток),
# стоит ВНУТРИ try: иначе падение молча снимет весь enforcement. Пол интерпретатора держит
# test_python_floor.py.
try:
    import risk_ladder as R
    import _shell_targets as ST
    import _branches as BR
    import forge_events as FE
    from _project import docs_base
    SKILL = "forgeui"
    _BRANCH = BR.BRANCHES[SKILL]
    # Шаг, на котором пишется код, и «шаг проверки → шаг, который надо переоткрыть» — из реестра веток
    # (единый источник; роль dev у forgeui — ui-build).
    BUILD_STEP = next(s for s, role in _BRANCH["roles"].items() if role == "dev")
    VERIFY_TO_IMPL = dict(_BRANCH["verify_to_impl"])
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[ui-guard] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f"hooks/hooks.json установки forge (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/extensions/<forge|forge-ui>/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("ui-guard", _e))

_WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
_BASH_TOOLS = ("Bash", "run_shell_command")
_READ_TOOLS = ("Read", "ReadFile", "read_file")
_SEARCH_TOOLS = ("Grep", "GrepSearch", "grep_search", "search_file_content")
_MANY_TOOLS = ("read_many_files", "ReadManyFiles")
_PATH_KEYS = ("file_path", "path", "absolute_path", "notebook_path", "filePath")
_TEXT_KEYS = ("content", "new_string", "new_source", "text", "file_text")

_ENGINE_DIR = Path(__file__).resolve().parents[1] / "skills" / "forgeui" / "scripts"
CACHE_NAME = "ui-guard-cache.json"
CACHE_SCHEMA = 1

# ── что считается каноном и сырым экспортом ───────────────────────────────────
# Имена из задачи (спека §4.2, track.CANONICAL_FILE / REQUIREMENT_FILE / RAW_FILE): `*.canonical.json`,
# `visual-requirements.pixso*.json` (slim и .raw.json), `visual-requirements.json|html` (Figma).
_PROTECTED_NAME = re.compile(
    r"(?i)^(?:[^/\\]*\.canonical\.json|visual-requirements\.pixso[^/\\]*\.json"
    r"|visual-requirements\.(?:json|html))$")
# В тексте команды имя ищем без ведущего `[\w.-]*`: с ним поиск по длинному токену квадратичен (хук повис бы).
_PROTECTED_IN_TEXT = re.compile(
    r"(?i)(?:\.canonical\.json|visual-requirements\.pixso[\w.-]*\.json"
    r"|visual-requirements\.(?:json|html))(?![\w-])")
# Голова сырого экспорта Pixso DSL (pixso/dsl.py: обёртка `{"dsl": {…}}` у экспорта Pixso MCP; тело без
# обёртки движок тоже принимает — оно начинается с первого ключа `converterVersion`). Сверка без учёта
# пробельных символов: экспорт может быть и отформатирован.
_DSL_PREFIXES = (b'{"dsl":{"converterVersion"', b'{"converterVersion"')
_SNIFF_BYTES = 512
_SNIFF_SUFFIXES = (".json", ".dsl", ".txt", "")      # при обходе каталога; прямой путь — любой файл
_DIR_SCAN_CAP = 3000                                 # файлов на обход каталога внутри .daiver (скриншоты verify/)
_SNIFF_CAP = 200                                     # голов на обход каталога
_CAND_CAP = 80                                       # кандидатов-путей на сегмент shell-команды

# Команды shell, которые файл НЕ читают: перечисляют и мерят. Всё остальное, что называет канон или
# сырой экспорт, — отказ (список открытый «снаружи», закрытый «внутри»: новый читатель не проскочит).
_META_CMDS = frozenset({"ls", "stat", "wc", "file", "du", "test", "[", "[[", "sha256sum", "sha1sum",
                        "sha512sum", "shasum", "md5sum", "md5", "cksum", "b2sum", "realpath",
                        "readlink", "basename", "dirname", "find", "pwd"})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_QUOTED = re.compile(r"""['"]([^'"\n]{1,300})['"]""")
_GLOB_MAGIC = re.compile(r"[*?\[]")

READ_DENY = ("канон экрана и сырые экспорты макета читать нельзя (INV-F1, F2): это сотни КБ, а значения "
             "берутся только у движка. Чем заменить: daiver_outline(track, frame) — карта экрана; "
             "daiver_section(track, sectionId, weightBudget) — поддерево секции с измеренными значениями; "
             "daiver_source_nodes(track, frame, nodeIds) — layout-декларации названных узлов сырого "
             "экспорта. Нужного нет в ответах инструментов — это вопрос пользователю, а не повод обходить.")
READ_DENY_SHELL = (READ_DENY + " В shell такие файлы можно только перечислять и мерить (ls, stat, wc, "
                   "file, shasum): читать, копировать и разбирать их нельзя.")
READ_DENY_SEARCH = (READ_DENY + " Нужен поиск по документам трека (plan.md, ui-plan.md) — укажи сам файл или "
                    "фильтр вроде glob=*.md.")

# ── код модуля и инлайн-svg ───────────────────────────────────────────────────
_CODE_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte", ".ts", ".js", ".mjs", ".cjs", ".html", ".htm")
_SVG_OPEN = re.compile(r"<(?:svg|SVG)(?=[\s/>])")
_SVG_DATA = re.compile(r"(?i)data:image/svg\+xml")
# Маркер плейсхолдера — из svg_assets.placeholder(): атрибут data-art-placeholder="<slug>" и подпись
# «ART MISSING: <slug>.svg»; SLUG_RE — тот же, что у svg_assets (строчные латинские слова и цифры через дефис).
_ART_MARK = re.compile(r"""\bdata-art-placeholder\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SVG_CLOSE = re.compile(r"</(?:svg|SVG)\s*>")
_PLACEHOLDER_CHILDREN = frozenset({"title", "text", "tspan", "desc"})
_TAG_SCAN = 4000                                     # дальше такого тег открытия не ищем

_EXEMPT_TOP = ("ground", ".daiver", ".gigacode", ".qwen", ".git")


def _block(reason: str) -> int:
    print(f"[ui-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _active_ui_run(root: Path) -> Optional[Tuple[str, Path]]:
    """(feature, каталог прогона) активного прогона forgeui; None — активна другая ветка или прогона нет."""
    mp = R.active_manifest(root)
    if not mp or mp.parent.parent.name != SKILL:
        return None
    return mp.parent.name, mp.parent


# ── безопасные файловые предикаты ─────────────────────────────────────────────
# Path.is_file() пробрасывает ENAMETOOLONG и подобное, а кандидатом-путём бывает кусок кода из
# `python -c`: сбой предиката не должен быть ни ложным отказом, ни крэшем хука.

def _isfile(p: Path) -> bool:
    try:
        return p.is_file()
    except (OSError, ValueError):
        return False


def _isdir(p: Path) -> bool:
    try:
        return p.is_dir()
    except (OSError, ValueError):
        return False


def _real(p: Path) -> Path:
    try:
        return p.resolve()
    except (OSError, ValueError, RuntimeError):
        return p


def _abs(cwd: str, root: Path, raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(cwd or root) / p
    return p


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _tool_path(tool_input: Dict[str, Any]) -> str:
    for k in _PATH_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


# ── 1. чтение: канон и сырые экспорты ─────────────────────────────────────────

def _dsl_head(head: bytes) -> bool:
    """Голова файла — сигнатура Pixso DSL (с обёрткой или без; пробелы и BOM не мешают)."""
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return b"".join(head.split()).startswith(_DSL_PREFIXES)


def _sniff(path: Path) -> bool:
    """Обычный файл, чья голова — сигнатура DSL. FIFO и устройства не открываются (open завис бы)."""
    if not _isfile(path):
        return False
    try:
        with open(str(path), "rb") as fh:
            return _dsl_head(fh.read(_SNIFF_BYTES))
    except (OSError, ValueError):
        return False


def _is_protected_file(path: Path, any_suffix: bool = True) -> bool:
    """Канон или сырой экспорт: по имени (в том числе имени, на которое указывает ссылка) либо по голове."""
    real = _real(path)
    if _PROTECTED_NAME.match(path.name) or _PROTECTED_NAME.match(real.name):
        return True
    if not any_suffix and real.suffix.lower() not in _SNIFF_SUFFIXES:
        return False
    return _sniff(real)


def _glob_alternatives(flt: Any, strip_globstar: bool = True) -> List[str]:
    """Шаблоны glob-фильтра (строка или список; один уровень `{a,b}` раскрыт, ведущие `./` и — для фильтра
    поиска — `**/` сняты)."""
    out: List[str] = []
    for it in (flt if isinstance(flt, (list, tuple)) else [flt]):
        if not isinstance(it, str) or not it.strip():
            continue
        it = it.strip()
        while it.startswith("./") or (strip_globstar and it.startswith("**/")):
            it = it[2:] if it.startswith("./") else it[3:]
        m = re.search(r"\{([^{}]*)\}", it)
        if m:
            out += [it[:m.start()] + alt + it[m.end():] for alt in m.group(1).split(",")]
        else:
            out.append(it)
    return out


def _protected_in_dir(d: Path, flt: Any = None) -> bool:
    """Есть ли в поддереве d защищённый файл, подходящий под glob-фильтр (None — любой).
    Слишком большой каталог — «да»: проверить нельзя, а защищаем сотни КБ, не удобство."""
    pats = _glob_alternatives(flt)
    seen = sniffed = 0
    for dirpath, dirnames, filenames in os.walk(str(d)):
        dirnames.sort()
        for fn in sorted(filenames):
            seen += 1
            if seen > _DIR_SCAN_CAP:
                return True
            path = Path(dirpath) / fn
            if pats:
                rel = path.relative_to(d).as_posix()
                if not any(fnmatch.fnmatch(fn, p) or fnmatch.fnmatch(rel, p) for p in pats):
                    continue
            if _PROTECTED_NAME.match(fn):
                return True
            if path.suffix.lower() in _SNIFF_SUFFIXES and sniffed < _SNIFF_CAP:
                sniffed += 1
                if _sniff(path):
                    return True
    return False


def _daiver_real(root: Path) -> Path:
    return _real(root / ".daiver")


def _judge_file_read(root: Path, cwd: str, tool_input: Dict[str, Any]) -> Optional[str]:
    raw = _tool_path(tool_input)
    if raw and _is_protected_file(_abs(cwd, root, raw)):
        return READ_DENY
    return None


def _judge_search(root: Path, cwd: str, tool_input: Dict[str, Any]) -> Optional[str]:
    """grep_search: файл-цель; каталог ВНУТРИ .daiver/ — если в его поддереве (под glob-фильтром) есть
    защищённый файл. Поиск от корня репо (каталог, содержащий .daiver) не отказывает — см. «Не покрыто»."""
    raw = _tool_path(tool_input) or cwd or str(root)
    target = _abs(cwd, root, raw)
    if _is_protected_file(target):
        return READ_DENY
    real = _real(target)
    if _isdir(real) and _inside(real, _daiver_real(root)):
        flt = tool_input.get("glob") or tool_input.get("include")
        if _protected_in_dir(real, flt):
            return READ_DENY_SEARCH
    return None


def _glob_to_re(pat: str):
    """Шаблон glob рантайма (`*` не пересекает `/`, `**` — пересекает, `**/` необязателен) → регулярное
    выражение; без учёта регистра (файловые системы macOS/Windows его не различают)."""
    i, n, out = 0, len(pat), []
    while i < n:
        c = pat[i]
        if c == "*":
            if pat.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pat.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[" and pat.find("]", i + 2) != -1:
            j = pat.find("]", i + 2)
            body = pat[i + 1:j].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = j
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.compile("".join(out) + r"\Z", re.IGNORECASE)
    except re.error:                                   # кривой класс символов: шаблон — просто имя
        return re.compile(re.escape(pat) + r"\Z", re.IGNORECASE)


def _real_prefix(pat: str) -> str:
    """Абсолютный шаблон с раскрытыми ссылками в статической части (`/var/…` → `/private/var/…` на macOS):
    файлы `.daiver/` обходятся по реальным путям, шаблон должен быть записан так же."""
    if not os.path.isabs(pat):
        return pat
    m = _GLOB_MAGIC.search(pat)
    head = pat[:m.start()] if m else pat
    cut = head.rfind("/") + 1
    if cut <= 0:
        return pat
    return _real(Path(pat[:cut])).as_posix().rstrip("/") + "/" + pat[cut:]


def _globs_reach_protected(root: Path, base: Path, globs: List[str]) -> bool:
    """Шаблоны read_many_files против файлов `.daiver/`. Рантайм раскрывает шаблоны с точкой в именах
    (`**/*.json` заходит и в `.daiver/`), а Python glob и оболочка — нет, поэтому раскрывать шаблон по диску
    нельзя: сверяем его с файлами `.daiver/`, остальной репозиторий (node_modules) не обходим."""
    regs = [_glob_to_re(_real_prefix(a)) for g in globs for a in _glob_alternatives(g, strip_globstar=False)]
    d = _daiver_real(root)
    if not regs or not _isdir(d):
        return False
    base = _real(base)
    seen = 0
    for dirpath, dirnames, filenames in os.walk(str(d)):
        dirnames.sort()
        for fn in sorted(filenames):
            seen += 1
            if seen > _DIR_SCAN_CAP:
                return True
            path = Path(dirpath) / fn
            try:
                rel = Path(os.path.relpath(str(path), str(base))).as_posix()
            except ValueError:
                rel = path.as_posix()
            if any(r.match(rel) or r.match(path.as_posix()) for r in regs) and _is_protected_file(path, any_suffix=False):
                return True
    return False


def _judge_many(root: Path, cwd: str, tool_input: Dict[str, Any]) -> Optional[str]:
    """read_many_files(paths|include): путь — файл или каталог внутри `.daiver/`; шаблон — против файлов `.daiver/`."""
    pats: List[str] = []
    for key in ("paths", "include", "path", "pattern"):
        v = tool_input.get(key)
        pats += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x.strip()]
    globs: List[str] = []
    for pat in pats[:_CAND_CAP]:
        if _PROTECTED_IN_TEXT.search(pat):
            return READ_DENY
        if _GLOB_MAGIC.search(pat):
            globs.append(pat)
            continue
        p = _abs(cwd, root, pat)
        if _is_protected_file(p, any_suffix=False):
            return READ_DENY
        rp = _real(p)
        if _isdir(rp) and _inside(rp, _daiver_real(root)) and _protected_in_dir(rp):
            return READ_DENY
    if globs and _globs_reach_protected(root, Path(cwd or root), globs):
        return READ_DENY
    return None


def _expand(root: Path, cwd: str, raw: str) -> List[Path]:
    """Путь или раскрытый glob (до 200 совпадений): оболочка раскроет шаблон сама, а хук видит строку."""
    p = _abs(cwd, root, raw)
    if not _GLOB_MAGIC.search(raw):
        return [p]
    try:
        hits = list(itertools.islice(_glob.iglob(str(p), recursive=True), 3000))
    except (OSError, ValueError):
        return [p]
    return [Path(h) for h in sorted(hits)[:200]] or [p]


def _shell_candidates(toks: List[str]) -> List[str]:
    out: List[str] = []
    for t in toks[1:]:
        if not t or len(t) > 4096:
            continue
        out.append(t)
        if "=" in t:                                  # --file=path
            out.append(t.split("=", 1)[1])
        out += _QUOTED.findall(t)                     # python -c "open('x')"
    return out


def _segment_touches_protected(root: Path, cwd: str, toks: List[str]) -> bool:
    for t in toks[1:]:
        if _PROTECTED_IN_TEXT.search(t):
            return True
    seen = 0
    for cand in _shell_candidates(toks):
        c = cand.lstrip("<>").strip()                 # `<file` — вход, `>file` — цель редиректа
        if not c or c.startswith("-") or len(c) > 400 or "\n" in c or "\0" in c:
            continue
        seen += 1
        if seen > _CAND_CAP:
            break
        for p in _expand(root, cwd, c):
            if _is_protected_file(p):
                return True
            rp = _real(p)
            if _isdir(rp) and _inside(rp, _daiver_real(root)) and _protected_in_dir(rp):
                return True
    return False


def _judge_shell_reads(root: Path, cwd: str, command: str) -> Optional[str]:
    """Сегмент команды, называющий канон/сырой экспорт (по имени, шаблону или голове файла), — отказ,
    если команда не из «перечисляют и мерят». Обёртки сняты, `sh -c` раскрыт — общим разбором хуков."""
    if not command.strip():
        return None
    for toks in ST._segments(command):
        name = posixpath.basename(toks[0])
        if name in _META_CMDS and not (name == "find" and _FIND_EXEC & set(toks)):
            continue
        if _segment_touches_protected(root, cwd, toks):
            return READ_DENY_SHELL
    return None


# ── 2–4. запись кода модуля: шаг, подпись плана, инлайн-svg ───────────────────

class EngineError(Exception):
    """Движок forge-ui (skills/forgeui/scripts/daiver) не грузится."""


def _engine(*names: str) -> List[Any]:
    """Модули движка по именам (`daiver.<имя>`). Не грузится — EngineError: в прогоне forgeui запись
    без проверки не пропускается."""
    if str(_ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(_ENGINE_DIR))
    try:
        import importlib
        return [importlib.import_module(f"daiver.{n}") for n in names]
    except Exception as e:  # noqa: BLE001 — любой сбой загрузки = ядра нет
        raise EngineError(f"{type(e).__name__}: {str(e)[:160]}")


def _relative(root: Path, target: str, cwd: str) -> Optional[str]:
    """Путь относительно корня проекта (posix) или None — цель вне проекта."""
    p = _abs(cwd, root, target)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError, RuntimeError):
        return None


def _exempt(root: Path, rel: str) -> bool:
    """Control-plane, каталог харнеса и документы задачи — не код модуля, даже если outputDir задан широко."""
    if rel.split("/")[0] in _EXEMPT_TOP:
        return True
    try:
        docs = docs_base(root).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return False
    return rel == docs or rel.startswith(docs.rstrip("/") + "/")


def _output_dirs(root: Path, out_dir: str) -> List[str]:
    """outputDir из конфига и его раскрытый вид (если каталог — ссылка): posix от корня репо, без регистра
    (файловые системы macOS/Windows не различают регистр, а regexp — да)."""
    names = {out_dir.replace("\\", "/").strip("/")}
    try:
        names.add((root / out_dir).resolve().relative_to(root.resolve()).as_posix())
    except (ValueError, OSError, RuntimeError):
        pass
    return sorted(n.casefold() for n in names)


def _in_output(rel: str, outs: List[str]) -> bool:
    low = rel.casefold()
    return any(o in ("", ".") or low == o or low.startswith(o + "/") for o in outs)


def _written_text(tool_input: Dict[str, Any]) -> str:
    """Текст, который вызов кладёт в файл: content / new_string / new_source и new_string правок списком."""
    parts: List[str] = [v for k in _TEXT_KEYS for v in [tool_input.get(k)] if isinstance(v, str)]
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        parts += [e["new_string"] for e in edits if isinstance(e, dict) and isinstance(e.get("new_string"), str)]
    return "\n".join(parts)


def _step_denial(feature: str, step: Optional[str], rel: str) -> str:
    reopen = (f"python3 <harness>/skills/pipeline-state/scripts/update.py --skill {SKILL} --feature {feature} "
              f"--step-id {BUILD_STEP} --status pending")
    impl = VERIFY_TO_IMPL.get(step or "")
    if impl or step is None:
        where = (f"на шаге '{step}'" if step else "в завершённом прогоне")
        return (f"код модуля ({rel}) {where} не правится: между открытием итерации проверки и решением человека "
                f"правок нет (INV-F12), а «поправить на ходу» — это подгонка под зелёное в обход счётчика "
                f"итераций. Легальный путь — после решения пользователя «чинить» переоткрыть шаг сборки "
                f"'{impl or BUILD_STEP}': {reopen} — и чинить его субагентом (переоткрытие считается: "
                f"quality.max_step_reopens).")
    return (f"код модуля ({rel}) пишется ТОЛЬКО на шаге {BUILD_STEP} по подписанному плану, а активный шаг — "
            f"'{step}'. Закрой текущий шаг его гейтом; правка модуля до {BUILD_STEP} не пускается.")


def _signature_denial(feature: str, state: str, detail: str) -> str:
    key = f"ui-plan-{feature}"
    what = (f"план не подписан (нет действительного согласия {key})" if state == "open"
            else "подпись плана устарела: макет, резолв, спека или сам план изменились после подписи")
    return (f"{what}. Сборка идёт только по подписанному плану (C9), и правкой кода это не чинится: верни статус "
            f"failed с этой причиной — план пересобирает и подписывает человек на ui-design. Подпись не "
            f"инструмент и не твоё решение: согласие не выписывай. [{state}: {detail[:200]}]")


SVG_DENY = ("инлайн-SVG в коде модуля запрещён (INV-F5): иконка — только компонент библиотеки (нет уверенного "
            "маппинга — icons.fallback по плану), иллюстрация — файл через daiver_svg_assets(track, moduleDir): "
            "он даёт import .svg и JSX, а без файла — ПОМЕЧЕННЫЙ плейсхолдер (data-art-placeholder, подпись "
            "«ART MISSING: <slug>.svg»); вставь его дословно. Рисовать иллюстрацию кодом нельзя.")


# — инлайн-svg и помеченный плейсхолдер —

def _tag_end(text: str, start: int) -> int:
    """Индекс `>`, закрывающего тег открытия, что начался в `start`; -1 — не нашли в пределах _TAG_SCAN.
    `>` внутри кавычек атрибута и внутри JSX-выражения `{…}` (стрелки, сравнения) концом тега не считается."""
    i, n, depth = start, min(len(text), start + _TAG_SCAN), 0
    while i < n:
        c = text[i]
        if c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if (depth and text[j] == "\\") else 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == ">" and depth == 0:
            return i
        i += 1
    return -1


def _is_art_placeholder(text: str, start: int) -> bool:
    """`<svg …>` в позиции start — помеченный плейсхолдер арта: атрибут data-art-placeholder="<slug>",
    подпись `ART MISSING: <slug>.svg` с тем же slug и ничего нарисованного внутри (title/text/tspan/desc)."""
    end = _tag_end(text, start)
    if end < 0:
        return False
    head = text[start:end + 1]
    m = _ART_MARK.search(head)
    slug = (m.group(1) or m.group(2) or "") if m else ""
    if not _SLUG_RE.match(slug):
        return False
    label = f"ART MISSING: {slug}.svg"
    if head.rstrip().endswith("/>"):
        return label in head
    close = _SVG_CLOSE.search(text, end + 1)
    if close is None:
        return False
    body = text[end + 1:close.start()]
    if label not in head and label not in body:
        return False
    return all(t.lower() in _PLACEHOLDER_CHILDREN for t in re.findall(r"<\s*([A-Za-z][\w:.-]*)", body))


def _svg_problem(text: str) -> bool:
    """В тексте есть инлайн-svg (элемент `<svg` или data-URI), кроме помеченных плейсхолдеров арта."""
    if _SVG_DATA.search(text):
        return True
    return any(not _is_art_placeholder(text, m.start()) for m in _SVG_OPEN.finditer(text))


# — подпись плана: вердикт движка и его кеш —

def _stat_line(p: Path) -> str:
    try:
        st = p.stat()
    except (OSError, ValueError):
        return "-"
    return f"{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_ino}"


def _fingerprint(root: Path, extras: Iterable[str]) -> str:
    """Снимок stat'ов ВСЕХ входов подписи (см. «Кеш» в докстринге модуля)."""
    h = hashlib.sha256()

    def feed(label: str, p: Path) -> None:
        h.update(f"{label}\0{_stat_line(p)}\n".encode("utf-8", "surrogateescape"))

    base = root / ".daiver"
    if _isdir(base):
        for dirpath, dirnames, filenames in os.walk(str(base)):
            dirnames.sort()
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                feed(p.relative_to(root).as_posix(), p)
    feed("approvals", FE.approvals_path(root))
    feed("approvals-dir", FE.approvals_dir(root))
    for e in extras:
        feed("x:" + e, root / e)
    code = _ENGINE_DIR / "daiver"
    if _isdir(code):
        for p in sorted(code.rglob("*.py")):
            feed("c:" + p.relative_to(_ENGINE_DIR).as_posix(), p)
    feed("hook", Path(__file__))
    return h.hexdigest()


def _cache_read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == CACHE_SCHEMA else None


def _cache_write(path: Path, entry: Dict[str, Any]) -> None:
    """Атомарно (tmp + replace): два хука подряд не должны увидеть половину файла. Кеш — удобство, не
    обязанность: не записался — следующий вызов посчитает заново."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _extras(root: Path, feature: str, resolver: Any, TR: Any) -> List[str]:
    """Входы подписи вне `.daiver/`: ui-spec.md стори, зарегистрированные спеки и файлы страницы."""
    out: List[str] = []
    story = resolver(feature)
    if story is not None:
        p = Path(story)
        try:
            out.append(p.resolve().relative_to(root.resolve()).as_posix())
        except (ValueError, OSError):
            out.append(str(p))
    try:
        m = TR.load(root, feature)
    except Exception:  # noqa: BLE001 — манифест не читается: подпись скажет сама
        m = None
    if isinstance(m, dict):
        out += [s for s in (m.get("specs") or []) if isinstance(s, str)]
        for f in m.get("frames") or []:
            if isinstance(f, dict):
                out += [s for s in (f.get("sources") or []) if isinstance(s, str)]
    return out


def _signature(root: Path, feature: str, run_dir: Path) -> Tuple[str, str]:
    """(state, detail) подписи плана: ok | open | stale. Движок — `plan_contract.signature_state`, резолвер
    ui-spec.md стори — тот же, что у гейта (`tools._story_specs`): с другим дайджест иной, и законная подпись
    читается как устаревшая. Вердикт кешируется по снимку входов; без движка — EngineError."""
    cache_path = run_dir / CACHE_NAME
    cached = _cache_read(cache_path)
    if cached is not None:
        extras = [e for e in (cached.get("extras") or []) if isinstance(e, str)]
        if cached.get("fp") == _fingerprint(root, extras):
            st, detail = cached.get("state"), cached.get("detail")
            if st in ("ok", "open", "stale") and isinstance(detail, str):
                return st, detail
    PC, T, TR = _engine("plan_contract", "tools", "track")
    resolver = T._story_specs(root)
    extras = _extras(root, feature, resolver, TR)
    fp = _fingerprint(root, extras)          # ДО расчёта: правка во время расчёта обновит снимок, кеш не совпадёт
    try:
        sig = PC.signature_state(root, feature, resolver)
        state, detail = str(sig.get("state")), str(sig.get("detail") or "")
    except PC.ContractError as e:
        state, detail = "open", str(e)       # трека или кадров нет: подписывать нечего
    if state not in ("ok", "open", "stale"):
        raise RuntimeError(f"signature_state вернул состояние {state!r}")
    _cache_write(cache_path, {"schema": CACHE_SCHEMA, "fp": fp, "extras": extras, "state": state,
                              "detail": detail[:300]})
    return state, detail


def _page_files(root: Path, feature: str) -> set:
    """Файлы существующих страниц, которые правят кадры трека (`sources`, 6.4), — posix от корня репо, без регистра."""
    (TR,) = _engine("track")
    try:
        m = TR.load(root, feature)
    except Exception:  # noqa: BLE001 — манифест не читается: страниц не знаем (подпись его всё равно не пустит)
        m = None
    out: set = set()
    for f in (m or {}).get("frames") or [] if isinstance(m, dict) else []:
        if isinstance(f, dict):
            out |= {s.replace("\\", "/").strip("/").casefold() for s in (f.get("sources") or []) if isinstance(s, str)}
    return out


def _judge_write(root: Path, cwd: str, feature: str, run_dir: Path, tool_name: str,
                 tool_input: Dict[str, Any]) -> Optional[str]:
    if tool_name in _BASH_TOOLS:
        command = str(tool_input.get("command") or "")
        # Удаление и перенос тоже правят код модуля: `rm src/modules/X.tsx` на ui-verify — та же правка на ходу.
        targets, text = ST.write_targets(command) + ST.unlink_targets(command), command
    else:
        targets, text = [_tool_path(tool_input)], _written_text(tool_input)
    targets = [t for t in targets if t]
    if not targets:
        return None
    (CFG,) = _engine("config")
    outs = _output_dirs(root, str(CFG.load(root)["config"]["project"]["outputDir"]))
    pages = _page_files(root, feature)
    hits: List[str] = []
    for t in targets:
        rel = _relative(root, t, cwd)
        if rel is not None and not _exempt(root, rel) and (_in_output(rel, outs) or rel.casefold() in pages):
            hits.append(rel)
    if not hits:
        return None
    step = R.current_step_id(root)
    if step != BUILD_STEP:
        return _step_denial(feature, step, hits[0])
    state, detail = _signature(root, feature, run_dir)
    if state != "ok":
        return _signature_denial(feature, state, detail)
    if any(h.lower().endswith(_CODE_SUFFIXES) for h in hits) and _svg_problem(text):
        return SVG_DENY
    return None


def _judge(root: Path, cwd: str, feature: str, run_dir: Path, tool_name: str,
           tool_input: Dict[str, Any]) -> Optional[str]:
    """Причина отказа или None."""
    if tool_name in _READ_TOOLS:
        return _judge_file_read(root, cwd, tool_input)
    if tool_name in _SEARCH_TOOLS:
        return _judge_search(root, cwd, tool_input)
    if tool_name in _MANY_TOOLS:
        return _judge_many(root, cwd, tool_input)
    if tool_name in _BASH_TOOLS:
        reason = _judge_shell_reads(root, cwd, str(tool_input.get("command") or ""))
        return reason or _judge_write(root, cwd, feature, run_dir, tool_name, tool_input)
    return _judge_write(root, cwd, feature, run_dir, tool_name, tool_input)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    if tool_name not in _WRITE_TOOLS + _BASH_TOOLS + _READ_TOOLS + _SEARCH_TOOLS + _MANY_TOOLS:
        return 0
    cwd = str(data.get("cwd") or "")
    try:
        root = Path(R.project_root(cwd))
        run = _active_ui_run(root)
    except Exception:  # noqa: BLE001 — «есть ли прогон forgeui» не определить: хук вне прогона — noop
        return 0
    if run is None:
        return 0
    feature, run_dir = run
    # Внутри прогона forgeui — fail-CLOSED: крэш хука отдал бы exit 1, а его рантайм читает как
    # «возражений нет». Непредвиденная ошибка проверки = отказ с причиной.
    try:
        reason = _judge(root, cwd, feature, run_dir, tool_name, tool_input)
    except EngineError as e:
        return _block(f"ядро forge-ui не грузится ({e}) — в прогоне forgeui действие без проверки не пропускается")
    except Exception as e:  # noqa: BLE001
        return _block(f"ошибка проверки ({type(e).__name__}: {str(e)[:160]}) — в прогоне forgeui действие без "
                      f"проверки не пропускается")
    return _block(reason) if reason else 0


if __name__ == "__main__":
    sys.exit(main())
