#!/usr/bin/env python3
"""spec_grammar.py — форма требований-мастера: заголовок блока, сценарии, разделы, провенанс.

Зачем отдельный модуль. Форма мастера жила в ТРЁХ независимых копиях: `merge_delta_to_master`
(`_SEC_*`, `_req_pat`, `_GWT`), `check_master_spec` (свои списки заголовков + `_req_heading`) и
`_project` (`specs/<cap>/spec.md`). Правка формата требовала синхронной правки трёх мест, а
проект со СВОЕЙ спекой не описывался ни одним: `parse_master` не находил ни одного требования,
`plan_ops` считал все требования дельты новыми, и merge дописывал в чужой документ блоки в
форже-грамматике. Архивация гейтится тем же парсером, поэтому дельта навсегда оставалась
`new`/`drifted`.

Профиль собирается тремя слоями, приоритет сверху вниз:

  policy.json   `spec.grammar.*` + `spec.id_prefix` — подтверждено человеком (`config.py set`)
  детект        `ground/inventory/spec-conventions.json` — снял `analyze_spec.py`
  NATIVE        сегодняшний форже-формат (`### REQ-0007: …` + строки Given-When-Then)

Дефолт — NATIVE, поэтому проект, который ничего не настраивал, существования профиля не
замечает: регулярки и маркеры получаются ровно те же, что были литералами.

Формат, который профилем НЕ выражается (`kind`/`style` = `unknown`/`mixed`), — это отказ, а не
«попробуем как обычно»: `supported()` возвращает False, а вызывающий обязан остановиться.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
# Ниже этого порога детект не применяется: неуверенная догадка, молча подменившая грамматику,
# хуже отсутствия детекта — легаси-мастер (плоские списки) как раз даёт слабые гипотезы.
DETECT_FLOOR = 0.6

# Форже-родная форма. Значения — те же литералы, что жили в merge_delta_to_master и
# check_master_spec; менять их = менять поведение всех проектов без профиля.
NATIVE = {
    "requirement": {"level": 3, "kind": "id-colon", "lead": "", "id_prefix": "REQ", "id_width": 4,
                    "scope": "document"},
    "requirements_section": ["требования и сценарии", "requirements", "требования (require"],
    "audit_section": ["журнал изменений", "audit trail"],
    "scenario": {"style": "gwt-inline", "level": 4},
    "provenance": "from-bracket",
}

REQUIREMENT_KINDS = ("id-colon", "title-only", "numbered", "bullet-id")
SCENARIO_STYLES = ("gwt-inline", "gwt-block", "bullet", "none")

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_GWT = re.compile(r"(?i)given.*when.*then")
_FROM_TAG = re.compile(r"\[from:[^\]]*\]")
_SCEN_HEAD = re.compile(r"(?i)^\s{0,3}(#{2,6})\s+(?:scenario|сценарий)\s*:?\s*(.*)$")
_DOTTED = re.compile(r"^\d+(?:\.\d+)*$")


def _norm(s: str) -> str:
    """Нормализация для сравнения: без провенанса, разметки и лишних пробелов."""
    s = _FROM_TAG.sub("", s)
    s = re.sub(r"[*`\-]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _norm_block(lines: Iterable[str]) -> str:
    return "\n".join(_norm(l) for l in lines if _norm(l))


def _as_markers(val, default: Sequence[str]) -> List[str]:
    """Маркер раздела: строка, список строк или пусто («раздела нет»)."""
    if isinstance(val, str):
        v = val.strip().lower()
        return [v] if v else []
    if isinstance(val, (list, tuple)):
        out = [str(x).strip().lower() for x in val if str(x).strip()]
        return out
    return list(default)


class Grammar:
    """Скомпилированный профиль: всё, что merge/check/judge знают о форме мастера."""

    def __init__(self, profile: dict, layers: Optional[dict] = None) -> None:
        self.profile = profile
        self.layers = layers or {}
        req = profile.get("requirement") or {}
        self.level = int(req.get("level") or 3)
        self.kind = str(req.get("kind") or "id-colon")
        self.lead = str(req.get("lead") or "")
        self.id_prefix = str(req.get("id_prefix") or "REQ")
        self.id_width = int(req.get("id_width") or 4)
        scn = profile.get("scenario") or {}
        self.scenario_style = str(scn.get("style") or "gwt-inline")
        self.scenario_level = int(scn.get("level") or (self.level + 1))
        # Где искать требования: по всему документу (как форже-родной ### REQ-…, который ни с
        # чем не спутаешь) или только внутри раздела требований — иначе у безыдентификаторной
        # формы требованием станет каждый заголовок файла, включая «## Назначение».
        self.scope = str(req.get("scope") or ("section" if (self.kind == "title-only"
                                                            and not self.lead) else "document"))
        self.provenance = str(profile.get("provenance") or "from-bracket")
        self.requirements_section = _as_markers(profile.get("requirements_section"),
                                                NATIVE["requirements_section"])
        self.audit_section = _as_markers(profile.get("audit_section"), NATIVE["audit_section"])

    # ── пригодность ────────────────────────────────────────────────────
    def supported(self) -> Tuple[bool, List[str]]:
        """(можно ли работать, причины отказа). Fail-closed: неизвестная форма = отказ."""
        why: List[str] = []
        if self.kind not in REQUIREMENT_KINDS:
            why.append(f"вид заголовка требования «{self.kind}» не поддержан "
                       f"(известны: {', '.join(REQUIREMENT_KINDS)})")
        if not 1 <= self.level <= 6:
            why.append(f"уровень заголовка требования {self.level} вне 1..6")
        if self.scenario_style not in SCENARIO_STYLES:
            why.append(f"стиль сценариев «{self.scenario_style}» не поддержан "
                       f"(известны: {', '.join(SCENARIO_STYLES)})")
        return (not why), why

    def is_native(self) -> bool:
        """Совпадает ли форма с форже-родной.

        Якоря разделов сравниваются на СОВМЕСТИМОСТЬ, а не побайтово: детект возвращает один
        реальный заголовок («требования и сценарии»), а NATIVE держит список синонимов вместе
        с англоязычными — требовать равенства значило бы объявлять чужим свой же мастер.
        """
        n = Grammar(NATIVE)
        if self.signature()[:9] != n.signature()[:9]:
            return False
        return (markers_compatible(self.requirements_section, n.requirements_section)
                and markers_compatible(self.audit_section, n.audit_section))

    def signature(self) -> tuple:
        return (self.level, self.kind, self.lead, self.id_prefix, self.id_width, self.scope,
                self.scenario_style, self.scenario_level, self.provenance,
                tuple(self.requirements_section), tuple(self.audit_section))

    def describe(self) -> str:
        """Человекочитаемое «как устроена спека этого проекта»."""
        head = {
            "id-colon": f"{'#' * self.level} {self.id_prefix}-"
                        f"{'N' * self.id_width}: <название>",
            "title-only": f"{'#' * self.level} {self.lead + ' ' if self.lead else ''}<название>",
            "numbered": f"{'#' * self.level} <N.N> <название>",
            "bullet-id": f"- **{self.id_prefix}-{'N' * self.id_width}** — <название>",
        }.get(self.kind, f"<{self.kind}>")
        scen = {
            "gwt-inline": "строкой Given-When-Then внутри блока",
            "gwt-block": f"подзаголовком «{'#' * self.scenario_level} Scenario: …»",
            "bullet": "списком строк под требованием",
            "none": "не ведутся",
        }.get(self.scenario_style, self.scenario_style)
        sec = self.requirements_section[0] if self.requirements_section else "нет (требования на верхнем уровне)"
        audit = self.audit_section[0] if self.audit_section else "нет"
        src = ", ".join(f"{k}←{v}" for k, v in sorted(self.layers.items())) \
            or ("форже-родной" if self.is_native() else "детект")
        return ("Формат требований-мастера:\n"
                f"   требование     {head}\n"
                f"   сценарии       {scen}\n"
                f"   раздел требований  {sec}\n"
                f"   журнал изменений   {audit}\n"
                f"   провенанс      {'[from: <фича> <дата>]' if self.provenance == 'from-bracket' else 'не проставляется'}\n"
                f"   источник полей {src}")

    # ── заголовок требования ───────────────────────────────────────────
    _NEVER = re.compile(r"(?!x)x")

    def req_pattern(self) -> "re.Pattern[str]":
        # Неизвестный вид заголовка не должен ронять разбор: отказ даёт supported(), а парсер
        # обязан вернуть «требований нет», а не IndexError на несуществующей группе.
        if self.kind not in REQUIREMENT_KINDS:
            return self._NEVER
        h = "#" * self.level
        if self.kind == "id-colon":
            return re.compile(r"^\s{0,3}" + h + r"\s+(" + re.escape(self.id_prefix)
                              + r"-(\d+))\s*:\s*(.+?)\s*$")
        if self.kind == "numbered":
            return re.compile(r"^\s{0,3}" + h + r"\s+((\d+(?:\.\d+)*))[.)]?\s+(.+?)\s*$")
        if self.kind == "bullet-id":
            return re.compile(r"^\s{0,3}[-*]\s+\**(" + re.escape(self.id_prefix)
                              + r"-(\d+))\**\s*[:—–-]\s*(.+?)\s*$")
        # Регистр лида не значим: в документе «## Requirement:», в профиле может лежать
        # «requirement» — точное совпадение по регистру давало ноль требований молча.
        lead = "(?i:" + re.escape(self.lead.rstrip(":")) + r")\s*:\s*" if self.lead else ""
        return re.compile(r"^\s{0,3}" + h + r"\s+" + lead + r"()(.+?)\s*$")

    def match_requirement(self, line: str):
        """→ (id, num, title) либо None. У безыдентификаторной формы id = название."""
        if self.kind not in REQUIREMENT_KINDS:
            return None
        m = self.req_pattern().match(line)
        if not m:
            return None
        if self.kind == "title-only":
            title = m.group(2).strip()
            return (title, None, title)
        rid, num, title = m.group(1), m.group(2), m.group(3)
        if self.kind == "numbered":
            return (rid, num, title.strip())
        return (rid, int(num), title.strip())

    def _scenario_head(self, line: str):
        if self.scenario_style != "gwt-block":
            return None
        m = _SCEN_HEAD.match(line)
        if m and len(m.group(1)) > self.level:
            return m.group(2).strip()
        return None

    def _scope_span(self, lines: Sequence[str]):
        """Окно поиска требований: весь документ либо раздел требований."""
        if self.scope != "section":
            return (0, len(lines))
        span = self.section_span(lines, "requirements")
        return (span[0] + 1, span[1]) if span else (0, len(lines))

    def _closes_block(self, line: str) -> bool:
        """Заголовок закрывает блок требования — кроме подзаголовка сценария."""
        if not _HEADING.match(line):
            return False
        return self._scenario_head(line) is None

    # ── разбор мастера ─────────────────────────────────────────────────
    def parse(self, text: str) -> List[dict]:
        """Требования мастера: [{id, num, title, statement, scenarios, tags, start, end}]."""
        lines = text.splitlines()
        lo, hi = self._scope_span(lines)
        out: List[dict] = []
        cur: Optional[dict] = None
        for i in range(lo, hi):
            line = lines[i]
            hit = self.match_requirement(line)
            if hit:
                if cur is not None:
                    cur["end"] = i
                    out.append(cur)
                cur = {"id": hit[0], "num": hit[1], "title": hit[2], "body": [], "start": i}
                continue
            if cur is None:
                continue
            if self.kind == "bullet-id" and re.match(r"^\s{0,3}[-*]\s+", line):
                cur["end"] = i
                out.append(cur)
                cur = None
                continue
            if self._closes_block(line):
                cur["end"] = i
                out.append(cur)
                cur = None
            else:
                cur["body"].append(line)
        if cur is not None:
            cur["end"] = hi
            out.append(cur)

        for r in out:
            body = r.pop("body")
            r["scenarios"] = self.scenarios_of(body)
            r["statement"] = self.statement_of(body)
            r["tags"] = self.find_provenance("\n".join(body))
            while r["end"] - 1 > r["start"] and not lines[r["end"] - 1].strip():
                r["end"] -= 1
        return out

    def scenarios_of(self, body: Sequence[str]) -> List[str]:
        """Сценарии блока. У gwt-block элемент многострочный — заголовок сценария плюс тело."""
        if self.scenario_style == "none":
            return []
        if self.scenario_style == "gwt-block":
            out: List[str] = []
            cur: Optional[List[str]] = None
            for line in body:
                if self._scenario_head(line) is not None:
                    if cur:
                        out.append("\n".join(cur).rstrip())
                    cur = [line.strip()]
                elif cur is not None:
                    if _HEADING.match(line):
                        out.append("\n".join(cur).rstrip())
                        cur = None
                    else:
                        cur.append(line.rstrip())
            if cur:
                out.append("\n".join(cur).rstrip())
            return [s for s in out if s.strip()]
        if self.scenario_style == "bullet":
            return [l.strip() for l in body
                    if l.strip().startswith(("-", "*")) and not l.lstrip().startswith("#")]
        return [l.strip() for l in body if _GWT.search(l) and not l.lstrip().startswith("#")]

    def statement_of(self, body: Sequence[str]) -> str:
        scen = {_norm(s) for s in self.scenarios_of(body)}
        keep = []
        for l in body:
            if not l.strip() or l.lstrip().startswith("#"):
                continue
            n = _norm(l)
            if not n or n in scen or any(n in s for s in scen):
                continue
            keep.append(l.strip())
        return self.strip_provenance(" ".join(keep)).strip()

    # ── рендер ─────────────────────────────────────────────────────────
    def heading(self, rid: Optional[str], title: str) -> str:
        h = "#" * self.level
        title = title.strip()
        if self.kind == "bullet-id":
            return f"- **{rid}** — {title}" if rid else f"- {title}"
        if self.kind == "title-only":
            lead = f"{self.lead.rstrip(':')}: " if self.lead else ""
            return f"{h} {lead}{title}"
        if not rid:
            return f"{h} {title}"
        sep = ": " if self.kind == "id-colon" else " "
        return f"{h} {rid}{sep}{title}"

    def render(self, rid: Optional[str], title: str, statement: str,
               scenarios: Sequence[str], tags: Sequence[str]) -> List[str]:
        out = [self.heading(rid, title)]
        stmt = (statement or "").strip() or title.strip()
        out.append(f"{stmt}  {' '.join(tags)}".rstrip())
        for s in scenarios:
            s = s.strip()
            if not s:
                continue
            if self.scenario_style == "none":
                break
            if self.scenario_style == "gwt-block":
                if self._scenario_head(s.splitlines()[0]) is not None:
                    out += [l.rstrip() for l in s.splitlines()]
                else:
                    out.append("#" * self.scenario_level + f" Scenario: {_scenario_name(s)}")
                    out.append(s if s.startswith("-") else f"- {s}")
                continue
            out.append(s if s.startswith(("-", "*")) else f"- {s}")
        return out

    def next_id(self, existing: Sequence[dict]) -> Optional[str]:
        """Следующий свободный ID. У безыдентификаторной формы — None (тождество по названию)."""
        if self.kind == "title-only":
            return None
        if self.kind == "numbered":
            ids = [str(r.get("num") or "") for r in existing]
            ids = [i for i in ids if _DOTTED.match(i)]
            if not ids:
                return "1"
            # Нумерация иерархическая: продолжаем самый частый префикс (3.1, 3.2 → 3.3),
            # а не заводим новый верхний раздел.
            from collections import Counter
            pref = Counter(".".join(i.split(".")[:-1]) for i in ids).most_common(1)[0][0]
            tail = max(int(i.split(".")[-1]) for i in ids
                       if ".".join(i.split(".")[:-1]) == pref)
            return f"{pref}.{tail + 1}" if pref else str(tail + 1)
        nums = [r["num"] for r in existing if isinstance(r.get("num"), int)]
        return f"{self.id_prefix}-{max(nums, default=0) + 1:0{self.id_width}d}"

    # ── разделы ────────────────────────────────────────────────────────
    def section_span(self, lines: Sequence[str], which: str):
        """Границы раздела (idx заголовка, idx конца) вместе с подзаголовками; None — нет.

        Отличие от модульного `section_span`: заголовок, который сам является требованием, раздел
        НЕ закрывает. У форже-родного профиля (требования — `###`, разделы — `##`) это ничего не
        меняет; у грамматики, где требование тоже `##`, без этого раздел требований схлопывался
        бы в одну строку — и первое же требование оказывалось бы «за разделом».
        """
        markers = self.requirements_section if which == "requirements" else self.audit_section
        if not markers:
            return None
        other = self.audit_section if which == "requirements" else self.requirements_section
        start = None
        for i, ln in enumerate(lines):
            m = _HEADING.match(ln)
            if not m:
                continue
            level, head = len(m.group(1)), m.group(2).lower()
            if start is None:
                if level == 2 and any(mk in head for mk in markers):
                    start = i
                continue
            if level > 2:
                continue
            if any(mk in head for mk in other) or not self.match_requirement(ln):
                return (start, i)
        return (start, len(lines)) if start is not None else None

    # ── провенанс ──────────────────────────────────────────────────────
    def provenance_tag(self, feature: str, today: str) -> str:
        return f"[from: {feature} {today}]" if self.provenance == "from-bracket" else ""

    def provenance_query(self, slug: str) -> str:
        """Подстрока, по которой судья ищет след дельты в мастере."""
        return f"from: {slug}" if self.provenance == "from-bracket" else slug

    def find_provenance(self, text: str) -> List[str]:
        return _FROM_TAG.findall(text) if self.provenance == "from-bracket" else []

    def strip_provenance(self, s: str) -> str:
        return _FROM_TAG.sub("", s) if self.provenance == "from-bracket" else s

    def scenario_key(self, scenario: str) -> str:
        """Содержание сценария без оформления: заголовок «#### Scenario: …» в тождество не входит.

        Дельта несёт сценарий одной строкой, мастер в стиле gwt-block — заголовком плюс шаги.
        Сравнивать «как записано» значит объявлять расхождением повторный merge собственной же
        записи: `=` превращалось в `~`, и идемпотентность ломалась.
        """
        lines = [l for l in scenario.splitlines() if l.strip()]
        if lines and self._scenario_head(lines[0]) is not None:
            lines = lines[1:]
        return _norm_block(lines)

    def is_scenario(self, line: str) -> bool:
        if self.scenario_style == "none":
            return False
        if self.scenario_style == "gwt-block":
            return self._scenario_head(line) is not None or bool(_GWT.search(line))
        if self.scenario_style == "bullet":
            return line.strip().startswith(("-", "*"))
        return bool(_GWT.search(line))


def markers_compatible(got: Sequence[str], native: Sequence[str]) -> bool:
    """Каждый найденный якорь узнаётся списком родных (или их набор совпадает)."""
    if list(got) == list(native):
        return True
    if not got:
        return not native
    return all(any(mk in g or g in mk for mk in native) for g in got)


def _scenario_name(scenario: str) -> str:
    """Короткое имя сценария из строки Given-When-Then (для рендера в gwt-block).

    Разметку выделения снимаем: в дельте шаги помечены `**When**`, и без очистки имя сценария
    получалось вида «** оператор запросил возврат **».
    """
    s = re.sub(r"^[-*]\s*", "", scenario.strip().splitlines()[0])
    m = re.split(r"(?i)\*{0,2}\bwhen\b\*{0,2}", s, maxsplit=1)
    name = (m[1] if len(m) > 1 else s)
    name = re.sub(r"(?i)\*{0,2}\bthen\b\*{0,2}.*$", "", name)
    name = re.sub(r"[*_`]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" ,.;:—-")
    return (name[:70] or "случай").strip()


def section_span(lines: Sequence[str], markers: Sequence[str]):
    """Границы раздела уровня `##` ВМЕСТЕ с его подзаголовками: (idx заголовка, idx конца)."""
    start = None
    for i, ln in enumerate(lines):
        m = _HEADING.match(ln)
        if not m:
            continue
        level, head = len(m.group(1)), m.group(2).lower()
        if start is None:
            if level == 2 and any(mk in head for mk in markers):
                start = i
        elif level <= 2:
            return (start, i)
    return (start, len(lines)) if start is not None else None


# ── сборка профиля ─────────────────────────────────────────────────────

def _deep_merge(base: dict, over) -> dict:
    out = dict(base)
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if v is None or v == "":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def detected_profile(root: Path) -> Optional[dict]:
    """Грамматика из ground/inventory/spec-conventions.json (или None, если детекта нет)."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from common import find_project_root  # noqa: F401  (путь к hooks/ уже проложен)
        from _project import spec_conventions_path
        path = spec_conventions_path(Path(root))
    except Exception:  # noqa: BLE001 — резолвер недоступен: та же раскладка по умолчанию
        path = Path(root) / "ground" / "inventory" / "spec-conventions.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def policy_grammar(cfg: dict) -> dict:
    """Ручки spec.grammar.* + spec.id_prefix из policy.json → форма профиля."""
    spec = (cfg.get("spec") if isinstance(cfg, dict) else None) or {}
    g = spec.get("grammar") or {}
    req = {}
    if g.get("requirement_level") is not None:
        req["level"] = g.get("requirement_level")
    if g.get("requirement_kind"):
        req["kind"] = g.get("requirement_kind")
    if g.get("requirement_lead") is not None:
        req["lead"] = g.get("requirement_lead")
    if spec.get("id_prefix"):
        req["id_prefix"] = spec.get("id_prefix")
    out: dict = {}
    if req:
        out["requirement"] = req
    scn = {}
    if g.get("scenario_style"):
        scn["style"] = g.get("scenario_style")
    if g.get("scenario_level") is not None:
        scn["level"] = g.get("scenario_level")
    if scn:
        out["scenario"] = scn
    if g.get("requirement_scope"):
        req["scope"] = g.get("requirement_scope")
        out["requirement"] = req
    if g.get("requirements_section") is not None:
        out["requirements_section"] = g.get("requirements_section")
    if g.get("audit_section") is not None:
        out["audit_section"] = g.get("audit_section")
    if g.get("provenance"):
        out["provenance"] = g.get("provenance")
    return out


def load_profile(root, cfg: Optional[dict] = None, detected: Optional[dict] = None) -> Grammar:
    """NATIVE ← детект ← policy.json. Слой каждой оси записывается в Grammar.layers."""
    root = Path(root)
    if cfg is None:
        try:
            from _config_loader import load_project_config
            cfg = load_project_config(root) or {}
        except Exception:  # noqa: BLE001 — битый бандл: работаем на NATIVE + детекте
            cfg = {}
    if detected is None:
        detected = detected_profile(root)

    layers = {}
    profile = dict(NATIVE)
    det_g = (detected or {}).get("grammar")
    confident = (float((detected or {}).get("confidence") or 0.0) >= DETECT_FLOOR
                 or isinstance((detected or {}).get("research"), dict))
    if isinstance(det_g, dict) and det_g and confident \
            and not (detected or {}).get("matches_native"):
        profile = _deep_merge(profile, det_g)
        for k in det_g:
            layers[k] = "detected"
    pol_g = policy_grammar(cfg or {})
    if pol_g:
        profile = _deep_merge(profile, pol_g)
        for k in pol_g:
            layers[k] = "policy"
    return Grammar(profile, layers)


def native() -> Grammar:
    return Grammar(dict(NATIVE))


if __name__ == "__main__":  # разбор формата текущего проекта одной командой
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Показать профиль грамматики мастер-спеки.")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    g = load_profile(Path(a.project_root).resolve())
    ok, why = g.supported()
    if a.json:
        print(json.dumps({"profile": g.profile, "layers": g.layers, "supported": ok,
                          "reasons": why, "native": g.is_native()}, ensure_ascii=False, indent=2))
    else:
        print(g.describe())
        if not ok:
            print("\n✗ профилем не описывается:")
            for r in why:
                print(f"   - {r}")
    sys.exit(0 if ok else 2)
