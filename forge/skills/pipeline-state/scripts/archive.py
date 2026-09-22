#!/usr/bin/env python3
"""archive.py — архив доков ЗАВЕРШЁННЫХ строек: <docs_base>/archive/<слаг>.

Пайплайн заканчивается верифицированным артефактом и ничего за собой не убирает: доки
завершённых фич копятся в <docs_base>/feature-pipeline/ плоским списком. Судья спеки при этом
ТРЕБУЕТ, чтобы там лежала только текущая фича, — требование без механизма. Здесь механизм.

Архив — СИБЛИНГ feature-pipeline/, а не подпапка в ней. Дельты (`sdd.md`) ищутся обходом
<docs_base>/feature-pipeline/ (spec_cli._features), и архив внутри этого каталога продолжал бы
попадать в `/forge-spec status`: заархивированное требование предлагалось бы слить повторно.

Стейт прогона (ground/statements/<skill>/<feature>/) НЕ трогается: единственные легальные
писатели control-plane — update.py/record_*. Провенанс переноса едет внутри самой перенесённой
папки (archive-meta.json), поэтому list/restore работают, не читая манифест.

Usage:
    python3 archive.py [--project <root>] status [--json]
    python3 archive.py [--project <root>] put <slug> [--skill S] [--dry-run]
                                                     [--force --reason R] [--json]
    python3 archive.py [--project <root>] list [--json]
    python3 archive.py [--project <root>] restore <slug> [--dry-run] [--json]

Слаг — как его знает пользователь: '<фича>' либо '<стори>/fixes/<баг>'. Короткого ключа бага
достаточно, пока он однозначен (иначе exit 3 с перечнем).

Exit: 0 — ок; 2 — отказ/ошибка; 3 — нужно решение пользователя; 4 — битый JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# `_util` живёт в двух scripts-каталогах с разным содержимым (см. шапку update.py): чужая копия,
# предзагруженная в sys.modules хуками, дала бы ImportError на половине имён.
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

from _util import (archive_docs_dir, feature_docs_dir, manifest_path,  # noqa: E402
                   repo_root, safe_load_json, task_docs_dir)
import read as _read  # summarize()  # noqa: E402

META_NAME = "archive-meta.json"

# Фолбэк финального шага плоских веток, если реестр шагов не прочитался. Живой источник —
# skills/<skill>/references/manifest-steps.json (последний элемент).
_FLAT_FINAL_FALLBACK = {"forgefix": "fix-spec", "forgelite": "lite-verify"}


class Fail(RuntimeError):
    """Отказ архивации. code — код выхода по канону репо (2 отказ, 3 нужно решение)."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _rel(path: Path, base: Path) -> str:
    return str(path.relative_to(base)).replace("\\", "/")


# ── Слаг ─────────────────────────────────────────────────────────────────────────────
def norm_slug(slug) -> str:
    """'<фича>' либо '<стори>/fixes/<баг>'. Traversal/абсолют/пустое — ValueError через Fail."""
    s = str(slug or "").strip().strip("/")
    if not s or s.startswith(("~", "/")) or "\\" in s:
        raise Fail(f"небезопасный слаг: {slug!r}")
    parts = s.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise Fail(f"небезопасный слаг: {slug!r}")
    if len(parts) == 3 and parts[1] == "fixes":
        return "/".join(parts)
    if len(parts) == 1:
        return parts[0]
    raise Fail(f"слаг должен быть '<фича>' либо '<стори>/fixes/<баг>', получено: {slug!r}")


def resolve_slug(project: Path, slug) -> str:
    """Короткий ключ бага → полный слаг. Неоднозначность — exit 3, как у /forge-merge."""
    base = feature_docs_dir(project)
    s = norm_slug(slug)
    if (base / s).is_dir():
        return s
    if "/" in s:
        raise Fail(f"нет каталога доков: {base / s}")
    hits = sorted(p for p in base.glob("*/fixes/" + s) if p.is_dir())
    if len(hits) == 1:
        return _rel(hits[0], base)
    if len(hits) > 1:
        raise Fail("слаг '{}' неоднозначен — есть в нескольких стори: {}.\n"
                   "   Назови полный слаг ('<стори>/fixes/<баг>')."
                   .format(s, ", ".join(_rel(h, base) for h in hits)), code=3)
    raise Fail(f"нет каталога доков: {base / s}")


# ── Прогоны ──────────────────────────────────────────────────────────────────────────
def find_runs(project: Path) -> list:
    """[(skill, feature)] по всем namespace, кроме archived/ (как read.list_features)."""
    base = Path(project) / "ground" / "statements"
    out = []
    if not base.is_dir():
        return out
    for sd in sorted(p for p in base.iterdir() if p.is_dir()):
        for d in sorted(p for p in sd.iterdir() if p.is_dir()):
            if d.name == "archived" or not (d / "manifest.json").exists():
                continue
            out.append((sd.name, d.name))
    return out


def _flat_final_step(skill: str) -> str:
    """Финальный шаг плоской ветки — последний элемент её реестра шагов, не литерал."""
    reg = _HERE.parents[1] / skill / "references" / "manifest-steps.json"
    try:
        steps = json.loads(reg.read_text(encoding="utf-8"))
        if isinstance(steps, dict):
            steps = steps.get("steps", [])
        ids = [s.get("id") for s in steps if isinstance(s, dict) and s.get("id")]
        if ids:
            return ids[-1]
    except Exception:  # noqa: BLE001 — реестр не прочитался: фолбэк ниже, не падаем
        pass
    return _FLAT_FINAL_FALLBACK.get(skill, "")


def final_step_ids(skill: str, manifest: dict) -> list:
    """Шаги, закрытие которых означает «стройка готова».

    Плоские ветки (forgefix/forgelite) — последний шаг их реестра. feature-pipeline — шаги
    ПОСЛЕДНЕЙ ФАЗЫ в каноническом порядке (container-шаг фазы не считается: его статус не
    отражает завершённость динамических шагов). Брать «последний шаг манифеста» нельзя:
    add_steps.py дописывает per-task 04-* в конец, уже ПОСЛЕ 06-spec.
    """
    steps = manifest.get("steps", []) if isinstance(manifest, dict) else []
    ids = [s.get("id") for s in steps if isinstance(s, dict) and s.get("id")]
    if skill in _FLAT_FINAL_FALLBACK:
        fin = _flat_final_step(skill)
        return [fin] if fin in ids else ([ids[-1]] if ids else [])
    try:
        sys.path.insert(0, str(_HERE.parents[1] / "feature-pipeline" / "scripts"))
        import pipeline_phases as PP
        phases = PP._ordered_unique_phases(steps)
        if not phases:
            return []
        last = phases[-1]
        out = [i for i in ids if PP.guess_phase(i) == last and not PP.is_container_step(i)]
        return out or [i for i in ids if PP.guess_phase(i) == last]
    except Exception:  # noqa: BLE001 — реестр фаз не поднялся: не выдаём «готово» на угад
        return []


def run_state(project: Path, skill: str, feature: str) -> dict:
    """Вычисляемое состояние прогона: persisted-поля «завершён» в манифесте нет."""
    man = safe_load_json(manifest_path(Path(project), skill, feature), what="manifest.json")
    summary = _read.summarize(man)
    by = {}
    for s in man.get("steps", []):
        if isinstance(s, dict) and s.get("id"):
            by[s["id"]] = s.get("status")
    finals = final_step_ids(skill, man)
    final_ok = bool(finals) and all(by.get(f) == "completed" for f in finals)
    return {
        "manifest": man,
        "summary": summary,
        "final_steps": finals,
        "final_ok": final_ok,
        "completed": summary.get("status") == "completed" and final_ok,
        "open": [i for i, st in by.items() if st not in ("completed", "skipped")],
        "steps": by,
    }


def _skill_for(project: Path, feature: str, explicit=None) -> str:
    if explicit:
        return str(explicit)
    hits = [s for s, f in find_runs(Path(project)) if f == feature]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise Fail("фича '{}' есть в нескольких namespace ({}) — уточни --skill."
                   .format(feature, ", ".join(hits)), code=3)
    raise Fail(f"нет прогона для фичи '{feature}' в ground/statements/*/ "
               f"(архивируется только то, что пайплайн действительно вёл)")


def live_runs_inside(project: Path, target: Path, exclude) -> list:
    """Незавершённые прогоны, чьи доки лежат ВНУТРИ архивируемого каталога.

    Настоящий случай: архивируем стори STOR-100, а в её fixes/ идёт живой баг."""
    out = []
    for skill, feature in find_runs(Path(project)):
        if (skill, feature) == exclude:
            continue
        try:
            d = task_docs_dir(project, skill, feature)
        except Exception:  # noqa: BLE001 — docs-конфиг не резолвится: не наш гейт
            continue
        if d != target and target not in d.parents:
            continue
        try:
            if not run_state(project, skill, feature)["completed"]:
                out.append(f"{skill}/{feature} → {d}")
        except SystemExit:  # битый манифест соседа — считаем живым, молчать опаснее
            out.append(f"{skill}/{feature} → {d} (манифест нечитаем)")
    return out


def delta_state(project: Path, slug: str):
    """Состояние дельты относительно мастера: merged|new|drifted|no-master (или None)."""
    try:
        sys.path.insert(0, str(_HERE.parents[1] / "system-analyst" / "scripts"))
        import spec_cli
        return spec_cli.delta_state(Path(project), slug)
    except Exception:  # noqa: BLE001 — мастер не настроен/движок не поднялся: гейт не давим
        return None


# ── Перенос ──────────────────────────────────────────────────────────────────────────
def _prune_empty(start: Path, stop: Path) -> None:
    """Убрать опустевшие husk-каталоги ('<стори>/fixes/') до границы docs-базы."""
    cur = start
    while cur != stop and stop in cur.parents:
        try:
            if any(cur.iterdir()):
                return
            cur.rmdir()
        except OSError:
            return
        cur = cur.parent


def archive_feature(project, slug, skill=None, force: bool = False, reason=None,
                    dry_run: bool = False, delta_checked: bool = False) -> dict:
    """Перенести доки завершённой стройки в <docs_base>/archive/<слаг>. Отказ — Fail.

    delta_checked=True ставит вызывающий, который САМ только что свёл или сверил дельту
    (/forge-merge). Иначе состояние дельты пересчитывается здесь — и на dry-run слияния оно
    ещё до-мерджевое, то есть отказ был бы про уже решённую проблему."""
    project = Path(project)
    slug = resolve_slug(project, slug)
    feature = slug.split("/")[-1]
    skill = _skill_for(project, feature, skill)

    base = feature_docs_dir(project)
    src = base / slug
    if not src.is_dir():
        raise Fail(f"нет каталога доков: {src}")

    st = run_state(project, skill, feature)
    if not st["completed"] and not force:
        why = ("незакрытые шаги: " + ", ".join(sorted(st["open"]))) if st["open"] else \
              ("финальный шаг не закрыт (" + ", ".join(st["final_steps"] or ["?"]) + ")")
        raise Fail(f"стройка '{slug}' не завершена — {why}.\n"
                   f"   Архив снимает доки с рабочего стола, поэтому едет только готовое. "
                   f"Осознанно и всё равно надо — --force --reason '<почему>'.")

    if not force:
        live = live_runs_inside(project, src, exclude=(skill, feature))
        if live:
            raise Fail("внутри '{}' идут незавершённые прогоны: {}.\n"
                       "   Архивация утащила бы их доки вместе с папкой."
                       .format(slug, "; ".join(live)))

    ds = "checked-by-caller" if delta_checked else delta_state(project, slug)
    if ds in ("new", "drifted") and not force:
        raise Fail("дельта '{}' не сведена с мастером (состояние: {}).\n"
                   "   Сначала /forge-merge {} — иначе требования уедут в архив, минуя мастер."
                   .format(slug, ds, feature))

    dest_base = archive_docs_dir(project)
    target = dest_base / slug
    if target.exists():
        target = target.parent / f"{target.name}-{_ts()}"

    plan = {
        "ok": True, "slug": slug, "skill": skill, "feature": feature,
        "source": _rel(src, base), "target": str(target), "dry_run": bool(dry_run),
        "delta_state": ds, "forced": bool(force),
    }
    if dry_run:
        plan["moved"] = False
        return plan

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    _prune_empty(src.parent, base)

    man = st["manifest"]
    meta = {
        "version": 1,
        "slug": slug,
        "skill": skill,
        "feature": feature,
        "pipeline_id": man.get("pipeline_id"),
        "started_at": man.get("started_at"),
        "last_update": man.get("last_update"),
        "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": _rel(src, base),
        "steps": st["steps"],
        "delta_state": ds,
        "master_source": _master_source(project),
    }
    if force:
        meta["forced"] = True
        meta["reason"] = reason or "(причина не указана)"
    (target / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    plan["moved"] = True
    return plan


def restore_feature(project, slug, dry_run: bool = False) -> dict:
    """Вернуть заархивированные доки обратно в feature-pipeline/."""
    project = Path(project)
    arc = archive_docs_dir(project)
    base = feature_docs_dir(project)
    s = norm_slug(slug)
    src = arc / s
    if not src.is_dir():
        hits = sorted(p.parent for p in arc.glob("**/" + META_NAME)
                      if p.parent.name == s.split("/")[-1])
        if len(hits) == 1:
            src = hits[0]
        elif len(hits) > 1:
            raise Fail("в архиве несколько '{}': {}. Назови полный слаг."
                       .format(s, ", ".join(_rel(h, arc) for h in hits)), code=3)
        else:
            raise Fail(f"нет в архиве: {arc / s}")

    meta_file = src / META_NAME
    rel = _rel(src, arc)
    if meta_file.is_file():
        meta = safe_load_json(meta_file, what=META_NAME)
        rel = meta.get("source") or rel
    target = base / rel
    if target.exists():
        raise Fail(f"место занято: {target} — уберите каталог или переименуйте архив")

    plan = {"ok": True, "slug": rel, "source": str(src), "target": str(target),
            "dry_run": bool(dry_run)}
    if dry_run:
        plan["moved"] = False
        return plan
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    try:
        (target / META_NAME).unlink()
    except OSError:
        pass
    _prune_empty(src.parent, arc)
    plan["moved"] = True
    return plan


def _master_source(project) -> str:
    """spec.master_source: delta-first (слияние) | master-first (сверка)."""
    try:
        sys.path.insert(0, str(_HERE.parents[1] / "system-analyst" / "scripts"))
        import spec_cli
        return spec_cli.master_source(Path(project))
    except Exception:  # noqa: BLE001
        return "delta-first"


def list_archived(project) -> list:
    arc = archive_docs_dir(Path(project))
    out = []
    if not arc.is_dir():
        return out
    for meta_file in sorted(arc.glob("**/" + META_NAME)):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        meta.setdefault("slug", _rel(meta_file.parent, arc))
        meta["path"] = str(meta_file.parent)
        out.append(meta)
    return out


def status(project) -> dict:
    """Что готово к архивации, что держит отказ, что уже в архиве."""
    project = Path(project)
    base = feature_docs_dir(project)
    rows = []
    for skill, feature in find_runs(project):
        try:
            docs = task_docs_dir(project, skill, feature)
        except Exception:  # noqa: BLE001
            continue
        if not docs.is_dir():
            continue
        try:
            st = run_state(project, skill, feature)
        except SystemExit:
            continue
        try:
            slug = _rel(docs, base)
        except ValueError:
            continue
        ds = delta_state(project, slug)
        blockers = []
        if not st["completed"]:
            blockers.append("прогон не завершён")
        if ds in ("new", "drifted"):
            blockers.append(f"дельта {ds}")
        live = live_runs_inside(project, docs, exclude=(skill, feature))
        if live:
            blockers.append("внутри живые прогоны: " + "; ".join(live))
        rows.append({"slug": slug, "skill": skill, "feature": feature,
                     "status": st["summary"].get("status"), "delta_state": ds,
                     "archivable": not blockers, "blockers": blockers})
    return {"docs_base": str(base), "archive": str(archive_docs_dir(project)),
            "master_source": _master_source(project),
            "candidates": rows, "archived": list_archived(project)}


# ── CLI ──────────────────────────────────────────────────────────────────────────────
def _print_status(data: dict) -> None:
    ready = [r for r in data["candidates"] if r["archivable"]]
    held = [r for r in data["candidates"] if not r["archivable"]]
    print(f"Архив: {data['archive']}  [режим мастера: {data['master_source']}]")
    if ready:
        print(f"\nГотово к архивации ({len(ready)}):")
        for r in ready:
            ds = r["delta_state"] if r["delta_state"] in ("merged", "new", "drifted") else None
            print(f"   ✓ {r['slug']}  [{r['skill']}]{'  дельта: ' + ds if ds else ''}")
        print("   Перенести: /forge-archive put <слаг>  (или само на /forge-merge)")
    if held:
        print(f"\nПока не архивируется ({len(held)}):")
        for r in held:
            print(f"   · {r['slug']}  [{r['skill']}] — {'; '.join(r['blockers'])}")
    if data["archived"]:
        print(f"\nВ архиве ({len(data['archived'])}):")
        for a in data["archived"]:
            print(f"   {a.get('slug')}  ({a.get('archived_at', '?')})")
    if not ready and not held and not data["archived"]:
        print("\nНи одного прогона с доками не найдено.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Архив доков завершённых строек.")
    ap.add_argument("--project", default=None, help="корень проекта (по умолчанию git toplevel)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="что готово к архивации и что уже в архиве")
    s.add_argument("--json", action="store_true")

    p = sub.add_parser("put", help="перенести доки завершённой стройки в архив")
    p.add_argument("slug")
    p.add_argument("--skill", default=None, help="namespace прогона (если фича в нескольких)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="снять гейты готовности (нужен --reason)")
    p.add_argument("--reason", default=None)
    p.add_argument("--json", action="store_true")

    ls = sub.add_parser("list", help="что лежит в архиве")
    ls.add_argument("--json", action="store_true")

    r = sub.add_parser("restore", help="вернуть доки из архива")
    r.add_argument("slug")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    project = Path(args.project).resolve() if args.project else Path(repo_root())
    cmd = args.cmd or "status"
    as_json = getattr(args, "json", False)   # без подкоманды флагов подкоманды в Namespace нет

    try:
        if cmd == "status":
            data = status(project)
            print(json.dumps(data, ensure_ascii=False, indent=2)) if as_json \
                else _print_status(data)
            return 0

        if cmd == "list":
            rows = list_archived(project)
            if as_json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif not rows:
                print("Архив пуст.")
            else:
                for a in rows:
                    print(f"{a.get('slug')}  [{a.get('skill', '?')}]  "
                          f"{a.get('archived_at', '?')}  → {a.get('path')}")
            return 0

        if cmd == "put":
            if args.force and not args.reason:
                raise Fail("--force без --reason: причина обхода гейта обязана быть записана")
            res = archive_feature(project, args.slug, skill=args.skill, force=args.force,
                                  reason=args.reason, dry_run=args.dry_run)
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif args.dry_run:
                print(f"dry-run: {res['source']} → {res['target']} (ничего не записано)")
            else:
                print(f"✅ {res['slug']} → {res['target']}")
                print("   Коммит архива — на тебе, forge не коммитит.")
            return 0

        if cmd == "restore":
            res = restore_feature(project, args.slug, dry_run=args.dry_run)
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif args.dry_run:
                print(f"dry-run: {res['source']} → {res['target']} (ничего не записано)")
            else:
                print(f"✅ возвращено: {res['target']}")
            return 0
    except Fail as e:
        print(f"[archive] DENY: {e}", file=sys.stderr)
        return e.code

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
