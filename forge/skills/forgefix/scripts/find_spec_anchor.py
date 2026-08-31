#!/usr/bin/env python3
"""find_spec_anchor.py — «к какой стори/требованию относится этот баг»: детерминированный поиск
якоря в требованиях-мастере (шаг fix-diag).

Зачем: без этого связь «баг → требование» устанавливает модель глазами, и ошибка не видна —
`/forge-spec merge` заведёт НОВОЕ требование (`+`) вместо правки существующего (`~`), а мастер
начнёт двоиться. Самый дешёвый источник — **сказать слаг стори**: человек его обычно знает, а какое требование
мастера за ней стоит — нет. Поэтому `--story <KEY>` (повторяемый) — свидетельство наивысшего веса,
и он же включает прямое сопоставление: у стори есть СВОЯ дельта `<docs>/feature-pipeline/<KEY>/
sdd.md`, чьи `###`-заголовки ровно и стали требованиями мастера. Туда же смотрят дельты её
ПРОШЛЫХ фиксов (`<KEY>/fixes/<баг>/sdd.md`) — фикс живёт внутри папки своей стори, и требование,
которое он правил, принадлежит стори. Это работает, даже если провенанс `[from:]` в мастере
кто-то потёр руками.

Остальное выводится машинно из того, что форж уже пишет сам:

  1. **Провенанс мастера.** Каждое требование несёт `[from: <slug> <date>]`, где `<slug>` — ключ
     Jira той фичи/стори, что его завела или правила (при `modify` теги НАКАПЛИВАЮТСЯ). Значит
     требование помнит все стори, которые его трогали.
  2. **Старые task-plan'ы.** `<docs>/feature-pipeline/<slug>/task-plan.json` (и планы её прошлых
     фиксов `<slug>/fixes/<баг>/task-plan.json`) перечисляют `artifacts` (созданные файлы) и
     `reuses` (изменённые классы). Файл, который правит фикс, находит стори, которая этот файл
     создала/трогала, — а через её slug и требования мастера.
  3. **Связи Jira самого бага.** `parent`, `issuelinks`, Epic Link, упоминания ключей в тексте.

Кандидаты ранжируются по сумме весов свидетельств. Один максимум — якорь найден; несколько или
ни одного — это РЕШЕНИЕ ПОЛЬЗОВАТЕЛЯ, а не догадка (exit 3). При заданном `--story` кандидаты
других стори отбрасываются: пользователь уже сузил область, лишний шум в вопросе не нужен.

Порядок для оркестратора: сначала прогнать без `--story` (вдруг и так однозначно), при exit 3 —
спросить «по какой стори баг?» и прогнать повторно с `--story`, и только если всё ещё
неоднозначно — показать короткий список требований ЭТОЙ стори.

Usage:
    find_spec_anchor.py --project-root <root> [--story STOR-100 ...] [--issue-json <file|->]
        [--changed-file <path> ...] [--spec <master spec.md>] [--capability <cap>] [--json]
Exit: 0 — якорь однозначен (в `anchor`);
      3 — неоднозначно/не найдено: спроси пользователя, показав `candidates`;
      2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ENGINE_DIR = Path(__file__).resolve().parents[2] / "system-analyst" / "scripts"
_FP_SCRIPTS = Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"
for _d in (_ENGINE_DIR, _FP_SCRIPTS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
try:
    import merge_delta_to_master as _engine
except Exception:  # noqa: BLE001
    _engine = None

_JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_FROM_SLUG = re.compile(r"\[from:\s*([^\s\]]+)")

# Веса свидетельств. Прямая связь Jira сильнее упоминания в тексте; «файл трогала та же стори»
# — сильное независимое свидетельство, потому что идёт от кода, а не от слов.
W_STORY = 6          # пользователь назвал стори явно — сильнее любого выведенного признака
W_STORY_DELTA = 4    # заголовок требования найден в собственной дельте этой стори
W_PARENT = 4
W_LINK = 3
W_EPIC = 2
W_MENTION = 1
W_FILE = 3
W_SDD_REF = 2


def _kebab(s: str) -> str:
    s = re.sub(r"[*`]", "", s or "").strip().lower()
    return re.sub(r"[^0-9a-zа-яё]+", "-", s).strip("-")


def _text_of(desc) -> str:
    if isinstance(desc, dict):  # ADF
        return " ".join(re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                                   json.dumps(desc, ensure_ascii=False)))
    return str(desc or "")


def stories_from_issue(issue: dict) -> dict[str, dict]:
    """{JIRA-KEY: {"weight": int, "why": [...]}} — стори, с которыми связан баг."""
    if not isinstance(issue, dict):
        return {}
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else issue
    self_key = str(issue.get("key") or fields.get("key") or "")
    out: dict[str, dict] = {}

    def add(key: str, weight: int, why: str):
        key = (key or "").strip().upper()
        if not key or key == self_key.upper() or not _JIRA_KEY.fullmatch(key):
            return
        slot = out.setdefault(key, {"weight": 0, "why": []})
        slot["weight"] += weight
        if why not in slot["why"]:
            slot["why"].append(why)

    parent = fields.get("parent")
    if isinstance(parent, dict):
        add(parent.get("key", ""), W_PARENT, "parent")
    elif isinstance(parent, str):
        add(parent, W_PARENT, "parent")

    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        ltype = (link.get("type") or {}).get("name") if isinstance(link.get("type"), dict) else ""
        for side in ("inwardIssue", "outwardIssue"):
            iss = link.get(side)
            if isinstance(iss, dict):
                add(iss.get("key", ""), W_LINK, f"link:{ltype or side}")

    # Epic Link и прочие кастомные поля, где лежит голый ключ
    for name, val in fields.items():
        if not name.startswith("customfield") or not isinstance(val, str):
            continue
        m = _JIRA_KEY.fullmatch(val.strip())
        if m:
            add(m.group(1), W_EPIC, f"field:{name}")
    for alias in ("epic", "epic_link", "epicLink"):
        v = fields.get(alias)
        if isinstance(v, str):
            add(v, W_EPIC, "epic")
        elif isinstance(v, dict):
            add(v.get("key", ""), W_EPIC, "epic")

    text = f"{fields.get('summary') or ''}\n{_text_of(fields.get('description'))}"
    for m in _JIRA_KEY.finditer(text):
        add(m.group(1), W_MENTION, "mention")
    return out


def _feature_docs_dir(root: Path) -> Path:
    try:
        import skill_paths
        return skill_paths.feature_docs_dir(root)
    except Exception:  # noqa: BLE001 — резолвер недоступен: дефолтная in-repo раскладка
        return root / "docs" / "feature-pipeline"


def _matches_file(entry: str, changed: list[str]) -> str | None:
    """Совпадает ли артефакт/reuse старого плана с изменённым файлом фикса."""
    e = str(entry or "").replace("\\", "/").strip()
    if not e:
        return None
    e_last = e.rsplit("/", 1)[-1]
    e_cls = e_last[:-5] if e_last.endswith(".java") else e_last.rsplit(".", 1)[-1]  # FQN → класс
    for ch in changed:
        c = ch.replace("\\", "/")
        c_last = c.rsplit("/", 1)[-1]
        if c.endswith(e) or e.endswith(c):
            return ch
        if e_last and c_last == e_last:
            return ch
        if e_cls and c_last == f"{e_cls}.java":
            return ch
    return None


def stories_from_changed_files(root: Path, changed: list[str]) -> dict[str, dict]:
    """{slug: {"weight", "why", "sdd_refs"}} — фичи, чьи task-plan'ы трогали эти файлы."""
    out: dict[str, dict] = {}
    if not changed:
        return out
    base = _feature_docs_dir(root)
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        # План стори + планы её прошлых фиксов (<стори>/fixes/<баг>/task-plan.json). Файл,
        # который уже чинили внутри этой стори, — такое же свидетельство её авторства.
        plans = [(d / "task-plan.json", d.name)]
        fixes = d / "fixes"
        if fixes.is_dir():
            plans += [(f / "task-plan.json", f"{d.name}/fixes/{f.name}")
                      for f in sorted(p for p in fixes.iterdir() if p.is_dir())]
        for plan_path, origin in plans:
            if not plan_path.exists():
                continue
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for t in plan.get("tasks") or []:
                for entry in list(t.get("artifacts") or []) + list(t.get("reuses") or []):
                    hit = _matches_file(entry, changed)
                    if not hit:
                        continue
                    # Кандидат — всегда СТОРИ (d.name), даже если файл трогал её фикс:
                    # требование мастера принадлежит стори, а не багу.
                    slot = out.setdefault(d.name, {"weight": 0, "why": [], "sdd_refs": []})
                    slot["weight"] = W_FILE  # один файл-хит достаточен; не суммируем по каждому
                    why = f"task-plan:{origin}/{t.get('id', '?')} ← {hit}"
                    if why not in slot["why"]:
                        slot["why"].append(why)
                    ref = t.get("sdd_ref")
                    if ref and ref not in slot["sdd_refs"]:
                        slot["sdd_refs"].append(ref)
    return out


def titles_from_story_delta(root: Path, slug: str) -> list[str]:
    """Заголовки требований из дельт стори: её собственной (<docs>/feature-pipeline/<slug>/sdd.md)
    и дельт её прошлых фиксов (<slug>/fixes/*/sdd.md).

    Прямая связь «стори → её требования»: именно эти `###`-заголовки merge и апсертил в мастер.
    Работает, даже если провенанс [from:] в мастере кто-то потёр руками.
    """
    if _engine is None:
        return []
    story_dir = _feature_docs_dir(root) / slug
    sdds = [story_dir / "sdd.md"]
    fixes = story_dir / "fixes"
    if fixes.is_dir():
        sdds += [f / "sdd.md" for f in sorted(p for p in fixes.iterdir() if p.is_dir())]
    titles: list[str] = []
    for sdd in sdds:
        if not sdd.exists():
            continue
        try:
            for c in _engine.parse_delta(sdd.read_text(encoding="utf-8")):
                title = (c.get("title") or "").strip()
                if title and title not in titles:
                    titles.append(title)
        except Exception:  # noqa: BLE001
            continue
    return titles


def rank(master_reqs: list[dict], jira_stories: dict, file_stories: dict,
         explicit_stories: dict | None = None) -> list[dict]:
    """Кандидаты-требования со score и человекочитаемым evidence.

    explicit_stories: {slug: {"titles": [...]}} — стори, названные пользователем.
    """
    explicit_stories = explicit_stories or {}
    sdd_ref_kebabs = {_kebab(r.split("#")[-1]) for s in file_stories.values()
                      for r in s.get("sdd_refs", []) if r}
    sdd_ref_kebabs.discard("")
    delta_titles = {slug: {_kebab(x) for x in (info.get("titles") or [])} - {""}
                    for slug, info in explicit_stories.items()}
    out = []
    for req in master_reqs:
        tags_slugs = {m.group(1) for t in req.get("tags", []) for m in [_FROM_SLUG.search(t)] if m}
        title_kebab = _kebab(req.get("title", ""))
        score, evidence, explicit_hit = 0, [], False
        for slug in sorted(set(tags_slugs) | set(explicit_stories)):
            if slug in explicit_stories:
                if slug in tags_slugs:
                    score += W_STORY
                    explicit_hit = True
                    evidence.append(f"стори {slug} названа пользователем → [from: {slug}]")
                if title_kebab and title_kebab in delta_titles.get(slug, set()):
                    score += W_STORY_DELTA
                    explicit_hit = True
                    evidence.append(f"требование есть в дельте стори {slug} (её sdd.md)")
            if slug not in tags_slugs:
                continue
            j = jira_stories.get(slug)
            if j:
                score += j["weight"]
                evidence.append(f"jira {slug} ({', '.join(j['why'])}) → [from: {slug}]")
            f = file_stories.get(slug)
            if f:
                score += f["weight"]
                evidence.append(f"код: {'; '.join(f['why'])} → [from: {slug}]")
        if title_kebab and title_kebab in sdd_ref_kebabs:
            score += W_SDD_REF
            evidence.append(f"sdd_ref старой задачи указывает на «{req.get('title')}»")
        if score:
            out.append({"id": req.get("id"), "title": req.get("title"), "score": score,
                        "evidence": evidence, "explicit": explicit_hit})
    # Пользователь назвал стори — чужие требования из вопроса убираем: область уже сужена им.
    if explicit_stories and any(c["explicit"] for c in out):
        out = [c for c in out if c["explicit"]]
    for c in out:
        c.pop("explicit", None)
    return sorted(out, key=lambda c: (-c["score"], c["id"] or ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--story", action="append", default=[],
                    help="ключ основной стори, названный пользователем (можно несколько) — "
                         "сильнейшее свидетельство; сужает кандидатов до требований этой стори")
    ap.add_argument("--issue-json", help="JSON issue бага из Jira MCP (файл или '-')")
    ap.add_argument("--changed-file", action="append", default=[],
                    help="файл, который правит фикс (можно несколько)")
    ap.add_argument("--spec", help="явный путь к мастеру specs/<cap>/spec.md")
    ap.add_argument("--capability")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    issue = {}
    if args.issue_json:
        try:
            raw = sys.stdin.read() if args.issue_json == "-" else \
                Path(args.issue_json).read_text(encoding="utf-8")
            issue = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            print(f"[find_spec_anchor] ERROR: не читается issue: {e}", file=sys.stderr)
            return 2

    if _engine is None:
        print("[find_spec_anchor] ERROR: движок мастера недоступен (merge_delta_to_master)",
              file=sys.stderr)
        return 2
    try:
        spec_path, _cap = _engine.resolve_spec(root, args.capability, args.spec)
        master_text = Path(spec_path).read_text(encoding="utf-8") if Path(spec_path).exists() else ""
        prefix = _engine.spec_options(root).get("id_prefix") or _engine.DEFAULT_ID_PREFIX
        master_reqs = _engine.parse_master(master_text, prefix) if master_text else []
    except Exception:  # noqa: BLE001
        spec_path, master_reqs = None, []

    jira_stories = stories_from_issue(issue)
    file_stories = stories_from_changed_files(root, args.changed_file)
    explicit = {}
    for s in args.story:
        key = str(s).strip().upper()
        if key:
            explicit[key] = {"titles": titles_from_story_delta(root, key)}
    candidates = rank(master_reqs, jira_stories, file_stories, explicit)

    top = candidates[0]["score"] if candidates else 0
    winners = [c for c in candidates if c["score"] == top] if top else []
    status = "resolved" if len(winners) == 1 else ("ambiguous" if winners else "not_found")

    result = {
        "status": status,
        "master": str(spec_path) if spec_path else None,
        "master_requirements": len(master_reqs),
        "stories": {"explicit": explicit, "jira": jira_stories, "code": file_stories},
        "candidates": candidates[:5],
        "anchor": ({"id": winners[0]["id"], "title": winners[0]["title"]} if status == "resolved"
                   else None),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if status == "resolved":
            a = result["anchor"]
            print(f"[find_spec_anchor] OK: якорь — {a['id']}: {a['title']}")
            for e in candidates[0]["evidence"]:
                print(f"   • {e}")
        else:
            print(f"[find_spec_anchor] {status.upper()}: однозначного требования нет "
                  f"(мастер: {len(master_reqs)} требований)", file=sys.stderr)
            for c in candidates[:5]:
                print(f"   {c['id']}: {c['title']}  (score={c['score']})", file=sys.stderr)
                for e in c["evidence"]:
                    print(f"      • {e}", file=sys.stderr)
            if not candidates:
                print("   Свидетельств нет: баг не связан ни с одной стори из провенанса мастера "
                      "и не трогает файлы известных task-plan'ов.", file=sys.stderr)
            if not args.story:
                print("   СНАЧАЛА спроси у пользователя ПРОСТОЕ: «по какой стори этот баг? "
                      "(ключ, напр. STOR-100; не знаешь — так и скажи)» и перезапусти с "
                      "--story <KEY>: человек помнит стори, а не ID требований мастера.",
                      file=sys.stderr)
            print("   Дальше это РЕШЕНИЕ ПОЛЬЗОВАТЕЛЯ: покажи кандидатов и спроси, какое требование "
                  "правим (или 'none' — поведение в спеке не описано, заведём новое). "
                  "Запиши: config.py set sources.spec_anchor <REQ-ID|none>", file=sys.stderr)
    return 0 if status == "resolved" else 3


if __name__ == "__main__":
    sys.exit(main())
