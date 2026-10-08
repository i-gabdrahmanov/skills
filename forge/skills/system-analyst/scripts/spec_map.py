#!/usr/bin/env python3
"""spec_map.py — КАРТА требований-мастера: из чего состоит мастер и где чей файл.

Зачем. Merge исходил из одной раскладки — `specs/{capability}/spec.md`, один файл на проект.
У опытного пользователя мастер устроен как угодно: например, репо из N сервисов, у каждого своя
спека (`inbound-adapter/inbound-adapter.md`, `db-service/dbservice.md`), плюс навигационный
`PROJECT_MAP.md` в корне. Старый ресерч ранжировал ВСЕ `*.md` базы и объявлял мастером того, у
кого больше «требований» — навигационный индекс с нумерованными заголовками модулей выигрывал.

Поэтому порядок такой: сначала `research` снимает СТРУКТУРУ мастера в файл проекта
(`ground/spec-map.json`), человек её подтверждает, и только потом merge — строго по карте.

Карта:
    {
      "schema_version": 1, "status": "draft" | "confirmed",
      "base": "<абсолютный путь базы мастера на момент скана>",
      "scan_root": "<путь сканирования относительно base>",
      "layout": "per-capability" | "single" | "empty",
      "capabilities": {
        "<cap>": {"spec": "<путь относительно base>", "form": "native|project|prose|unknown",
                  "confidence": 0.9, "grammar": {...}, "requirements_heading": "...",
                  "audit_heading": "...", "sections_present": true, "modules": [...],
                  "alternatives": [...]}
      },
      "ignore": [{"path": "...", "why": "..."}],
      "deltas": {"<slug>": "<cap>"}        # привязка дельты к сервису, запоминается merge
    }

Без подтверждённой карты merge идёт по-старому ТОЛЬКО когда мастер однозначен: в базе нет
ни одного файла, кроме настроенного пути. Всё остальное — exit 3 «сначала research».
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
_FP = str(SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts")
if _FP not in sys.path:
    sys.path.insert(0, _FP)

import analyze_spec as AS   # noqa: E402
import spec_grammar as SG   # noqa: E402

SCHEMA_VERSION = 1
# Индексы и навигация: мастером не бывают никогда, даже если в них много нумерованных
# заголовков (PROJECT_MAP.md npf выигрывал старый ресерч именно так).
INDEX_NAMES = AS.SKIP_NAMES | {"project_map.md", "toc.md", "summary.md", "contents.md",
                               "navigation.md", "map.md", "overview.md", "glossary.md"}
# Разделы, которые forge заводит в мастере-прозе под свои требования.
REQ_HEADING = "Требования и сценарии"
AUDIT_HEADING = "Журнал изменений"
# Суффиксы, которые не несут смысла при сопоставлении «модуль кода ↔ каталог сервиса».
_NOISE = ("service", "adapter", "svc", "spec", "module", "app", "api", "impl", "server")
_CAP_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MapError(Exception):
    """Нарушение контракта карты (путь вне базы, неизвестный сервис) — exit 2 у CLI."""


# ── пути ───────────────────────────────────────────────────────────────

def _sp():
    import skill_paths
    return skill_paths


def map_path(root: Path) -> Path:
    try:
        return _sp().spec_map_path(Path(root))
    except Exception:  # noqa: BLE001 — битый бандл: раскладка по умолчанию
        return Path(root) / "ground" / "spec-map.json"


def master_base(root: Path) -> Path:
    """База мастера (docs.master.repo_path у separate-repo, иначе docs/)."""
    return Path(_sp()._master_base(Path(root))).expanduser()


def scan_root(root: Path) -> Path:
    """Где лежат мастера сервисов.

    Статический префикс docs.master.spec_path (`<docs>/specs` у форже-дефолта), если он есть
    и не пуст. Иначе у ОТДЕЛЬНОГО репо мастера — весь репо: он целиком про спеки, и раскладку
    задаёт его владелец, а не шаблон forge. У мастера внутри docs/ проекта за префикс не
    выходим: в docs/ лежит всё подряд, и чужие доки мастером не становятся."""
    base = master_base(root)
    try:
        specs = Path(_sp().master_specs_dir(Path(root)))
    except Exception:  # noqa: BLE001
        specs = base
    if specs != base and specs.is_dir() and any(specs.rglob("*.md")):
        return specs
    if separate_repo(root):
        return base
    return specs


def separate_repo(root: Path) -> bool:
    try:
        docs = Path(_sp().docs_base(Path(root)))
    except Exception:  # noqa: BLE001
        return False
    return master_base(root).resolve() != docs.resolve()


def inside(base: Path, p: Path) -> bool:
    """Лежит ли p внутри base после разрешения симлинков (защита от побега из базы)."""
    try:
        Path(p).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


# ── чтение / запись ────────────────────────────────────────────────────

def load(root: Path) -> Optional[dict]:
    try:
        data = json.loads(map_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    return data


def save(root: Path, m: dict) -> Path:
    path = map_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def confirmed(root: Path) -> Optional[dict]:
    """Подтверждённая карта ЭТОЙ базы мастера; None — карты нет, она черновик или устарела
    (docs.master.repo_path поменяли после скана)."""
    m = load(root)
    if not m or m.get("status") != "confirmed":
        return None
    if str(master_base(root).resolve()) != m.get("base"):
        return None
    return m


# ── построение ─────────────────────────────────────────────────────────

def _md_files(d: Path, depth: int) -> List[Path]:
    out = []
    for p in sorted(d.rglob("*.md")):
        rel = p.relative_to(d)
        if len(rel.parts) > depth:
            continue
        if any(part in AS.SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        out.append(p)
    return out


def _rel(p: Path, base: Path) -> str:
    return p.resolve().relative_to(base.resolve()).as_posix()


def _is_index(p: Path) -> bool:
    return p.name.lower() in INDEX_NAMES


def _pick(cap: str, files: List[Path]) -> Tuple[Path, str]:
    """Файл мастера сервиса и почему именно он."""
    named = {p.name.lower(): p for p in files}
    for nm, why in ((f"{cap.lower()}.md", "имя совпадает с каталогом"),
                    ("spec.md", "форже-раскладка spec.md")):
        if nm in named:
            return named[nm], why
    real = [p for p in files if not _is_index(p)] or files
    if len(real) == 1:
        return real[0], "единственный файл сервиса"
    # Несколько кандидатов: у кого есть требования — тот мастер; при равенстве — крупнейший.
    def key(p: Path):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return (0, 0)
        hyps = AS._hypotheses(text)
        return (hyps[0]["score"] if hyps else 0, len(text))
    best = max(real, key=key)
    return best, "крупнейший из нескольких файлов сервиса — проверь"


def _norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    for n in _NOISE:
        if s.endswith(n) and len(s) > len(n) + 2:
            s = s[: -len(n)]
    return s


def _code_modules(root: Path) -> List[Tuple[str, str]]:
    """[(name, path)] модулей кода из инвентаря grounding (structure.json), best-effort."""
    try:
        sp = Path(_sp().scan_dir(Path(root))) / "structure.json"
        data = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — инвентаря нет: модули не сопоставляем
        return []
    out = []
    for m in data.get("modules") or []:
        if isinstance(m, dict) and m.get("name"):
            out.append((str(m["name"]), str(m.get("path") or m["name"])))
    return out


def _modules_for(cap: str, modules: List[Tuple[str, str]]) -> List[str]:
    key = _norm_name(cap)
    if not key:
        return []
    hit = []
    for name, path in modules:
        cand = {_norm_name(name), _norm_name(Path(path).name)}
        if any(c and (c == key or (len(min(c, key, key=len)) >= 4
                                   and (c.startswith(key) or key.startswith(c))))
               for c in cand):
            hit.append(path)
    return sorted(set(hit))


def _heading(markers: List[str], default: str) -> str:
    return markers[0][:1].upper() + markers[0][1:] if markers else default


def describe_file(p: Path, base: Path) -> dict:
    """Запись карты об одном файле мастера: форма, грамматика, есть ли разделы."""
    text = p.read_text(encoding="utf-8", errors="replace")
    a = AS.analyze_text(text, p)
    rel = _rel(p, base)
    entry: dict = {"spec": rel, "confidence": a["confidence"], "warnings": a["warnings"]}
    if "no_requirements_found" in a["warnings"]:
        # Проза (API-дока, описание сервиса): требований своей формы нет. Требования фич
        # пишутся форже-блоками в ОТДЕЛЬНЫЙ раздел в конце — прозу движок не трогает.
        g = dict(SG.NATIVE)
        g["requirements_section"] = [REQ_HEADING.lower()]
        g["audit_section"] = [AUDIT_HEADING.lower()]
        entry.update(form="prose", grammar=g)
    else:
        g = a["grammar"]
        native = a["matches_native"]
        confident = a["confidence"] >= AS.CONFIDENCE_FLOOR
        entry.update(form="native" if native else ("project" if confident else "unknown"),
                     grammar=g)
    gr = SG.Grammar(entry["grammar"])
    lines = text.splitlines()
    entry["requirements_heading"] = _heading(gr.requirements_section, REQ_HEADING)
    entry["audit_heading"] = _heading(gr.audit_section, AUDIT_HEADING)
    entry["sections_present"] = gr.section_span(lines, "requirements") is not None
    if a.get("exemplars"):
        entry["exemplar"] = a["exemplars"][0]["text"]
    return entry


def build(root: Path, prev: Optional[dict] = None, only: Optional[str] = None) -> dict:
    """Черновик карты по текущему содержимому базы. `prev` — прошлая карта: ручные правки
    (выбранный файл, модули, привязки дельт, игнор) переживают рескан."""
    root = Path(root).resolve()
    base = master_base(root)
    scan = scan_root(root)
    prev = prev or {}
    pcaps = prev.get("capabilities") or {}
    m: dict = {
        "schema_version": SCHEMA_VERSION, "status": "draft",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base": str(base.resolve()) if base.exists() else str(base),
        "scan_root": (scan.resolve().relative_to(base.resolve()).as_posix()
                      if base.exists() and inside(base, scan) else "."),
        "layout": "empty", "capabilities": {}, "ignore": [],
        "deltas": dict(prev.get("deltas") or {}), "warnings": [],
    }
    if not base.is_dir():
        m["warnings"].append(f"базы мастера нет: {base}")
        return m
    manual_ignore = [i for i in prev.get("ignore") or [] if i.get("manual")]
    ignored = {i["path"] for i in manual_ignore}
    m["ignore"] = list(manual_ignore)
    modules = _code_modules(root)

    if not scan.is_dir():
        # Каталог из шаблона docs.master.spec_path (`specs/<cap>/`) не существует — это не
        # «дельты не слиты», а неверный путь мастера; сырой FileNotFoundError уходил трейсбеком
        # (боевой прогон v0.4.6, SA-4).
        raise MapError(f"каталога мастеров нет: {scan} — проверь docs.master.spec_path "
                       f"(config.py get docs.master.spec_path) или заведи каталог")
    dirs = [d for d in sorted(scan.iterdir())
            if d.is_dir() and not d.name.startswith(".") and d.name not in AS.SKIP_DIRS]
    caps: Dict[str, dict] = {}
    for d in dirs:
        files = [p for p in _md_files(d, AS.MAX_DEPTH)
                 if _rel(p, base) not in ignored]
        if not files:
            continue
        cap = d.name
        if only and cap != only and cap in pcaps:
            caps[cap] = pcaps[cap]
            continue
        old = pcaps.get(cap) or {}
        chosen = None
        if old.get("manual") and old.get("spec"):
            cp = base / old["spec"]
            if cp.exists() and inside(base, cp):
                chosen, why = cp, "выбран вручную"
        if chosen is None:
            chosen, why = _pick(cap, files)
        entry = describe_file(chosen, base)
        entry["why"] = why
        entry["manual"] = bool(old.get("manual"))
        entry["modules"] = old.get("modules") if old.get("modules_manual") else \
            _modules_for(cap, modules)
        entry["modules_manual"] = bool(old.get("modules_manual"))
        entry["alternatives"] = [_rel(p, base)
                                 for p in files if p != chosen]
        caps[cap] = entry

    top = [p for p in sorted(scan.glob("*.md"))
           if _rel(p, base) not in ignored]
    if caps:
        m["layout"] = "per-capability"
        for p in top:
            m["ignore"].append({"path": _rel(p, base),
                                "why": "файл в корне при раскладке «каталог = сервис» "
                                       "(индекс/навигация)"})
    else:
        real = [p for p in top if not _is_index(p)]
        chosen = None
        if real:
            m["layout"] = "single"
            try:
                cap = _sp().master_capability(root)
            except Exception:  # noqa: BLE001
                cap = "capability"
            chosen, why = _pick(cap, real)
            entry = describe_file(chosen, base)
            entry.update(why=why, manual=False, modules=[], modules_manual=False,
                         alternatives=[_rel(p, base) for p in real if p != chosen])
            caps[cap] = entry
            if len(real) > 1:
                m["warnings"].append("в корне базы несколько файлов — мастер выбран по "
                                     "числу требований, проверь")
        for p in top:
            if p != chosen:
                m["ignore"].append({"path": _rel(p, base),
                                    "why": "индекс/навигация" if _is_index(p)
                                    else "не выбран мастером"})
    m["capabilities"] = caps
    m["deltas"] = {k: v for k, v in m["deltas"].items() if v in caps}
    return m


# ── правки карты (только скриптом, JSON руками не правится) ───────────

def _need(m: Optional[dict]) -> dict:
    if not m:
        raise MapError("карты мастера нет — сначала /forge-spec research")
    return m


def set_spec(root: Path, cap: str, rel: str) -> dict:
    """Выбрать файл мастера сервиса вручную. Путь — относительно базы и только внутри неё."""
    m = _need(load(root))
    base = master_base(root)
    if not _CAP_SAFE.match(cap):
        raise MapError(f"имя сервиса «{cap}» недопустимо")
    p = (base / rel)
    if Path(rel).is_absolute() or ".." in Path(rel).parts or not inside(base, p):
        raise MapError(f"путь «{rel}» выходит за базу мастера {base}")
    if not p.is_file() or p.suffix.lower() != ".md":
        raise MapError(f"файла нет или это не .md: {p}")
    entry = describe_file(p, base)
    old = (m.get("capabilities") or {}).get(cap) or {}
    entry.update(why="выбран вручную", manual=True,
                 modules=old.get("modules") or [], modules_manual=bool(old.get("modules_manual")),
                 alternatives=old.get("alternatives") or [])
    m.setdefault("capabilities", {})[cap] = entry
    m["status"] = "draft"
    save(root, m)
    return m


def add_ignore(root: Path, rel: str) -> dict:
    m = _need(load(root))
    m.setdefault("ignore", [])
    if not any(i.get("path") == rel for i in m["ignore"]):
        m["ignore"].append({"path": rel, "why": "исключён вручную", "manual": True})
    m = build(root, prev=m)
    save(root, m)
    return m


def map_module(root: Path, module: str, cap: str) -> dict:
    m = _need(load(root))
    caps = m.get("capabilities") or {}
    if cap not in caps:
        raise MapError(f"сервиса «{cap}» в карте нет (есть: {', '.join(sorted(caps)) or '—'})")
    e = caps[cap]
    mods = list(e.get("modules") or [])
    if module not in mods:
        mods.append(module)
    e["modules"], e["modules_manual"] = sorted(mods), True
    save(root, m)
    return m


def confirm(root: Path) -> dict:
    m = _need(load(root))
    if str(master_base(root).resolve()) != m.get("base"):
        raise MapError("карта снята с другой базы мастера (docs.master.repo_path менялся) — "
                       "/forge-spec research --refresh")
    bad = [c for c, e in (m.get("capabilities") or {}).items() if e.get("form") == "unknown"]
    if bad:
        raise MapError("форма не распознана у: " + ", ".join(bad) + " — выбери другой файл "
                       "(research --set <сервис>=<путь>) или исключи сервис")
    if not m.get("capabilities"):
        raise MapError("в карте нет ни одного мастера — подтверждать нечего")
    m["status"] = "confirmed"
    m["confirmed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save(root, m)
    return m


def bind_delta(root: Path, slug: str, cap: str) -> None:
    """Запомнить, в спеку какого сервиса сливается дельта (следующий merge/status не спросит)."""
    m = load(root)
    if not m or cap not in (m.get("capabilities") or {}):
        return
    if (m.get("deltas") or {}).get(slug) == cap:
        return
    m.setdefault("deltas", {})[slug] = cap
    save(root, m)


def mark_sections(root: Path, cap: str) -> None:
    m = load(root)
    if m and cap in (m.get("capabilities") or {}):
        m["capabilities"][cap]["sections_present"] = True
        save(root, m)


# ── резолв ─────────────────────────────────────────────────────────────

def ambiguous(root: Path) -> List[str]:
    """Файлы базы, из-за которых мастер без карты неоднозначен (пусто — можно по-старому).

    Однозначно = в месте сканирования нет НИЧЕГО, кроме настроенного пути мастера (или нет
    ничего вовсе: первый merge заведёт мастер из шаблона)."""
    try:
        configured = Path(_sp().master_spec_path(Path(root))).resolve()
    except Exception:  # noqa: BLE001
        configured = None
    scan = scan_root(root)
    if not scan.is_dir():
        return []
    out = []
    for p in _md_files(scan, AS.MAX_DEPTH):
        if configured is not None and p.resolve() == configured:
            continue
        out.append(str(p))
    return out


def entry_for(m: dict, cap: str) -> Optional[dict]:
    return (m.get("capabilities") or {}).get(cap)


def spec_path(root: Path, m: dict, cap: str) -> Path:
    e = entry_for(m, cap)
    if e is None:
        raise MapError(f"сервиса «{cap}» в карте нет (есть: "
                       f"{', '.join(sorted(m.get('capabilities') or {})) or '—'})")
    base = master_base(root).resolve()
    p = base / e["spec"]
    if not inside(base, p):
        raise MapError(f"мастер «{cap}» выходит за базу: {p}")
    return p


def _feature_text(sdd: Path) -> str:
    d = sdd.parent
    parts = []
    for nm in ("sdd.md", "tech-design.md", "task-plan.json"):
        p = d / nm
        if p.exists():
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)


def delta_capability(m: dict, slug: str, sdd: Path, cli: Optional[str] = None
                     ) -> Tuple[Optional[str], str, List[str]]:
    """(сервис, откуда он взялся, кандидаты). Сервис None — нужен выбор человека.

    Порядок: явный --capability → запомненная привязка дельты (или её стори, для фикса) →
    единственный сервис карты → модули кода, упомянутые в доках фичи. Угадывать по названию
    фичи не будем: ошибка = требования в спеке чужого сервиса."""
    caps = m.get("capabilities") or {}
    if cli:
        return (cli if cli in caps else None), "--capability", sorted(caps)
    deltas = m.get("deltas") or {}
    story = slug.split("/fixes/", 1)[0]
    for key in (slug, story):
        if deltas.get(key) in caps:
            return deltas[key], "привязка дельты в карте", sorted(caps)
    if len(caps) == 1:
        return next(iter(caps)), "единственный сервис карты", sorted(caps)
    text = _feature_text(sdd).lower()
    hits = sorted({c for c, e in caps.items()
                   for mod in (e.get("modules") or [])
                   if mod and (mod.lower() in text or Path(mod).name.lower() in text)})
    if len(hits) == 1:
        return hits[0], "модуль кода из доков фичи", sorted(caps)
    return None, ("модули фичи указывают на несколько сервисов: " + ", ".join(hits)
                  if hits else "сервис не определён"), (hits or sorted(caps))


def entry_by_path(root: Path, path: Path) -> "Optional[Tuple[str, dict]]":
    """(сервис, запись карты) для файла мастера — по ПОДТВЕРЖДЁННОЙ карте; иначе None.

    Нужен тем, кто получает путь, а не сервис (гейт состава, судья): форма и состав чужой
    спеки — из карты, а не из проектного детекта по всей базе."""
    m = confirmed(root)
    if not m:
        return None
    try:
        target = Path(path).resolve()
    except OSError:
        return None
    for cap in (m.get("capabilities") or {}):
        try:
            if spec_path(root, m, cap).resolve() == target:
                return cap, m["capabilities"][cap]
        except MapError:
            continue
    return None


def profile_for(root: Path, path: Path, cfg: Optional[dict] = None) -> "Optional[SG.Grammar]":
    """Грамматика мастера-файла по карте (NATIVE ← форма сервиса ← policy); None — не по карте."""
    hit = entry_by_path(root, path)
    if hit is None:
        return None
    _, e = hit
    return SG.load_profile(root, cfg, detected={"grammar": e.get("grammar") or dict(SG.NATIVE),
                                                "confidence": 1.0, "matches_native": False})


def is_foreign(root: Path, path: Path) -> bool:
    """Мастер по карте — чужой документ (проза/своя форма): форже-состав разделов не требуем."""
    hit = entry_by_path(root, path)
    return bool(hit) and hit[1].get("form") != "native"


def grammar_dict(m: dict, cap: str) -> dict:
    e = entry_for(m, cap) or {}
    return e.get("grammar") or dict(SG.NATIVE)


# ── вывод ──────────────────────────────────────────────────────────────

_FORM = {"native": "форже-родной", "project": "своя форма требований",
         "prose": "проза без требований — forge заведёт раздел", "unknown": "НЕ РАСПОЗНАНА"}


def describe(m: dict, path: Optional[Path] = None) -> str:
    st = "ПОДТВЕРЖДЕНА" if m.get("status") == "confirmed" else "черновик — нужна проверка"
    lines = [f"Карта мастера ({st}): {path or 'ground/spec-map.json'}",
             f"   база:      {m.get('base')}" + (f"  (скан: {m['scan_root']})"
                                                  if m.get("scan_root") not in (None, ".") else ""),
             f"   раскладка: {m.get('layout')}"]
    caps = m.get("capabilities") or {}
    if not caps:
        lines.append("   мастеров не найдено")
    for cap in sorted(caps):
        e = caps[cap]
        sec = "есть" if e.get("sections_present") else f"нет — merge допишет «{e.get('requirements_heading')}» с --ensure-sections"
        lines.append(f"\n   [{cap}] {e.get('spec')}")
        lines.append(f"      форма:   {_FORM.get(e.get('form'), e.get('form'))}"
                     f" (уверенность {e.get('confidence')}; {e.get('why', '')})")
        lines.append(f"      раздел требований: {sec}")
        if e.get("modules"):
            lines.append(f"      модули кода: {', '.join(e['modules'])}")
        if e.get("alternatives"):
            lines.append(f"      другие файлы: {', '.join(e['alternatives'][:4])}"
                         + (" …" if len(e["alternatives"]) > 4 else ""))
    if m.get("ignore"):
        lines.append("\n   исключено: " + "; ".join(f"{i['path']} ({i['why']})"
                                                     for i in m["ignore"][:8]))
    if m.get("deltas"):
        lines.append("   привязки дельт: " + ", ".join(f"{k} → {v}"
                                                     for k, v in sorted(m["deltas"].items())))
    for w in m.get("warnings") or []:
        lines.append(f"   ! {w}")
    if m.get("status") != "confirmed":
        lines += ["",
                  "Проверь карту. Поправить: research --set <сервис>=<путь>, --ignore <путь>, "
                  "--map-module <модуль>=<сервис>.",
                  "Подтвердить: research --confirm  — без этого merge в этот мастер не пишет."]
    return "\n".join(lines)


