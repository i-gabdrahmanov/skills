"""sources.py — `daiver_propose_sources`: где в репо лежит существующая страница кадра (спека §12.2 «page exists?», §20.4 proposeSources;
задача 6.4; INV-C18).

Файлы страницы находятся, а не угадываются: по конвенциям проекта (их читает модель — `project.conventions`), по роутеру и по текстам
страницы. Инструмент делает две детерминированные части:
  • тексты — видимые тексты канона кадра длиной ≥ MIN_TEXT_LENGTH (без повторов), каждый ищется как подстрока в файлах кода репо; кандидат —
    файл с найденными текстами, ранг — сколько РАЗНЫХ текстов нашлось (затем путь); до LIMIT кандидатов, у каждого — какие тексты и где;
  • роутер — адрес страницы кадра (`url` манифеста или трека): путь адреса с подставными сегментами на месте чисел и id ищется как строковый
    литерал (`'/orders/:id'` для `/orders/42`); файлы с ним — `routes` (там видно, какой компонент рендерит страницу).
Тексты, вынесенные в словари (i18n `.json`/`.yaml`), ищутся там же и отдаются отдельно — `textFiles`: страница их импортирует, сама она не там.
Обход — не больше MAX_FILES файлов (каталоги зависимостей, сборки и служебные — мимо), отсортированный: один вход — один ответ; обрезан —
`truncated: true`. Читает, ничего не пишет; что из кандидатов — страница, решает ЧТЕНИЕ файлов, потом `daiver_track_manifest set={sources, scope}`.

Не проверено данными (боевого репо фронта нет): доля страниц, чьи тексты лежат не в коде, а приходят с сервера (тогда текстами не найти —
остаются роутер и конвенции); подставные сегменты адреса для роутеров с другим синтаксисом параметров (`[id]`, `{id}`, `$id` — учтены по форме).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import canon as C
from . import track as TR

MIN_TEXT_LENGTH = 3                  # §20.4
LIMIT = 8                            # §20.4
MAX_FILES = 3000                     # §20.4
MAX_BYTES = 512 * 1024
SHOWN_TEXTS = 5
CODE_SUFFIXES = (".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs", ".vue", ".svelte", ".html")
TEXT_SUFFIXES = (".json", ".yaml", ".yml")
SKIP_DIRS = {"node_modules", ".git", ".daiver", "dist", "build", "out", "coverage", ".next", ".nuxt", ".cache", ".turbo", ".svelte-kit",
             "ground", ".gigacode", ".qwen", ".claude", "vendor", "storybook-static", "__pycache__"}
_DYNAMIC = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-f-]{20,})$", re.IGNORECASE)


class SourcesError(ValueError):
    """Кадра или канона нет — с причиной."""


def _walk(root: Path) -> Tuple[List[str], bool]:
    """Файлы кода и словарей репо (пути от корня, отсортированы), не больше MAX_FILES; второй элемент — обход обрезан."""
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            if name.lower().endswith(CODE_SUFFIXES + TEXT_SUFFIXES) and not name.lower().endswith((".d.ts", ".min.js", ".map")):
                out.append((rel_dir / name).as_posix())
                if len(out) >= MAX_FILES:
                    return out, True
    return out, False


def page_texts(root: dict) -> List[str]:
    seen: Dict[str, None] = {}
    for t in C.collect_texts(root):
        s = " ".join(str(t["text"]).split())
        if len(s) >= MIN_TEXT_LENGTH:
            seen.setdefault(s, None)
    return list(seen)


def route_pattern(url: Optional[str]) -> Optional[re.Pattern]:
    """Путь адреса → регулярка строкового литерала маршрута; корень и пустой путь — None (по «/» искать нечего)."""
    if not url:
        return None
    path = urlparse(url).path if "://" in url else url.split("?")[0].split("#")[0]
    segs = [s for s in path.split("/") if s]
    if not segs:
        return None
    parts = []
    for s in segs:
        parts.append(r"[:\[{$]?[\w.\-\]}]+" if _DYNAMIC.match(s) else re.escape(s))
    return re.compile(r"""['"`]/""" + "/".join(parts) + r"""/?['"`]""")


def _line(text: str, off: int) -> int:
    return text.count("\n", 0, off) + 1


def propose(root: Any, manifest: dict, frame_name: Optional[str]) -> Dict[str, Any]:
    root = Path(root)
    try:
        f = TR.get_frame(manifest, frame_name)
    except TR.TrackError as e:
        raise SourcesError(str(e))
    paths = TR.frame_paths(root, manifest, f["name"])
    if not paths["canonical"].is_file():
        raise SourcesError(f"канона кадра «{f['name']}» нет — сначала daiver_normalize: файлы страницы ищутся по её текстам")
    try:
        env = C.read_canonical(paths["canonical"])
    except C.CanonError as e:
        raise SourcesError(f"канон кадра не читается ({e}) — daiver_normalize заново")
    texts = page_texts(env["root"])
    url = TR.implementation_url(manifest, f["name"])
    pat = route_pattern(url)
    files, truncated = _walk(root)
    code: List[Dict[str, Any]] = []
    dicts: List[Dict[str, Any]] = []
    routes: List[Dict[str, Any]] = []
    for rel in files:
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if len(data) > MAX_BYTES:
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        hits: List[Dict[str, Any]] = []
        for t in texts:
            off = text.find(t)
            if off >= 0:
                hits.append({"text": t, "line": _line(text, off)})
        is_code = rel.lower().endswith(CODE_SUFFIXES)
        if hits:
            rec = {"file": rel, "texts": len(hits), "found": hits[:SHOWN_TEXTS]}
            (code if is_code else dicts).append(rec)
        if pat is not None and is_code:
            m = pat.search(text)
            if m:
                routes.append({"file": rel, "line": _line(text, m.start()), "literal": m.group(0)[:80]})
    rank = lambda r: (-r["texts"], r["file"])            # noqa: E731
    code.sort(key=rank)
    dicts.sort(key=rank)
    out: Dict[str, Any] = {"track": manifest.get("track"), "frame": f["name"], "texts": len(texts), "scanned": len(files), "truncated": truncated,
                           "candidates": code[:LIMIT], "routes": routes[:LIMIT], "textFiles": dicts[:LIMIT]}
    if len(code) > LIMIT:
        out["candidatesMore"] = len(code) - LIMIT
    out["url"] = url
    if f.get("sources"):
        out["recorded"] = list(f["sources"])
    nxt = []
    if code:
        nxt.append(f"кандидаты по текстам страницы — прочитай их (и импорты), прежде чем записывать: страница — файлы, которые РЕНДЕРЯТ этот экран")
    if routes:
        nxt.append("routes — где адрес страницы объявлен в роутере: там видно, какой компонент её рендерит")
    if dicts and not code:
        nxt.append("тексты нашлись только в словарях (textFiles): ищи, какой компонент импортирует эти ключи")
    if not code and not routes and not dicts:
        nxt.append("ни текстов, ни маршрута в коде не нашлось: похоже, страницы ещё нет — кадр строится новым модулем (sources не нужны); "
                   "если она есть, спроси пользователя, где её файлы")
    else:
        nxt.append("подтверждённые чтением файлы — daiver_track_manifest(track, frame, set={sources: [...], scope}); scope спрашивает человек: "
                   "change — точечное изменение страницы по макету, page — привести к макету всю страницу; потом база ДО правок кода")
    if truncated:
        nxt.append(f"обход обрезан на {MAX_FILES} файлах — кандидаты могут быть неполными")
    out["next"] = "; ".join(nxt)
    return out
