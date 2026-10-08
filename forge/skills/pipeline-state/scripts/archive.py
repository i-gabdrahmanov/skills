#!/usr/bin/env python3
"""archive.py — архив доков ЗАВЕРШЁННЫХ строек: <docs_base>/archive/<слаг>.

Пайплайн заканчивается верифицированным артефактом и ничего за собой не убирает: доки
завершённых фич копятся в <docs_base>/feature-pipeline/ плоским списком. Судья спеки при этом
ТРЕБУЕТ, чтобы там лежала только текущая фича, — требование без механизма. Здесь механизм.

Архив — СИБЛИНГ feature-pipeline/, а не подпапка в ней. Дельты (`sdd.md`) ищутся обходом
<docs_base>/feature-pipeline/ (spec_cli._features), и архив внутри этого каталога продолжал бы
попадать в `/forge-spec status`: заархивированное требование предлагалось бы слить повторно.

`put` уносит ОБЕ половины и требует каталог доков. Для прогона, умершего ДО первого артефакта,
доков нет по определению — его убирает `abandon`: только стейт, туда же в ground/archive/, с
обязательной причиной. Без него такой прогон был неубираем ничем (put отказывал на доках,
status его не показывал, руками нельзя — state-write-guard) и продолжал числиться активным.

Уносится ДВА каталога: доки (<docs_base>/feature-pipeline/<слаг>) и стейт прогона
(ground/statements/<skill>/<feature>/ → ground/archive/<skill>/<feature>/). Второй — тоже
control-plane, поэтому archive.py встаёт в один ряд с init.py/rollback.py как санкционированный
писатель стейта (BLOCKER-1): произвольный `mv` по этим путям режет state-write-guard.

Зачем уносить стейт. Активная фича резолвится обходом ground/statements/*/*/
(_project.resolve_active_run): свежайший ЖИВОЙ манифест. Завершённый прогон из выборки живых
выпадает сам, но БРОШЕННЫЙ — нет (шаги остались pending, по статусу он живой), и пока он лежит
в statements/, гейты рискуют примениться по чужому стейту. Из ground/archive/ прогон выпадает
у всех резолверов сразу — поэтому и `put`, и `abandon` переносят стейт, а не только доки.

Git-чекпойнты фичи (refs/forge/checkpoints/<feature>/*) при архивации (`put`) УДАЛЯЮТСЯ: это
точки восстановления для rollback.py, а откатывать завершённое некуда. Их restore не вернёт —
число удалённых пишется в archive-meta.json. У `abandon` иначе: прогон не завершён, и его
чекпойнты откладываются в refs/forge/abandoned/<метка>/ — с живого namespace сняты (новый
прогон с тем же слагом не откатится на чужой снапшот), а `restore <feature>` возвращает прогон
целиком, с ними.

Провенанс переноса едет внутри самой перенесённой папки доков (archive-meta.json), поэтому
list/restore работают, не читая манифест.

Согласие (R4). `abandon` и `put --force` снимают с активных прогон, который по статусу шагов
ещё живой (`put --force` к тому же удаляет его чекпойнты). Какой прогон
брошен, знает только пользователь: на боевом прогоне модель сама сняла чужой прогон по
подсказке preflight. Поэтому обе команды требуют approval-маркер `abandon-<feature>` /
`archive-force-<slug>` с цитатой пользователя — его сверяет gate-guard и, вторым слоем, этот
скрипт (exit 3 без маркера). Маркер одноразовый. `--dry-run` не гейтится.

Usage:
    python3 archive.py [--project <root>] status [--json]
    python3 archive.py [--project <root>] put <slug> [--skill S] [--dry-run]
                                                     [--force --reason R] [--json]
    python3 archive.py [--project <root>] abandon <feature> [--skill S] --reason R
                                                            [--dry-run] [--json]
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

from _util import (archive_docs_dir, feature_docs_dir, ground_dir, manifest_path,  # noqa: E402
                   repo_root, safe_component, safe_load_json, state_archive_dir, state_dir,
                   task_docs_dir)
import read as _read  # summarize()  # noqa: E402
import forge_events as FE  # noqa: E402 — согласия пользователя (ground/approvals.jsonl)
# _util при импорте кладёт hooks/ в sys.path — оттуда берём ЕДИНЫЙ предикат живости прогона
# (тот же, которым резолвят активную фичу хуки и config.py), а не вторую его копию здесь.
import _config_loader as _CL  # noqa: E402

META_NAME = "archive-meta.json"


def _ensure_path(p: Path) -> None:
    """sys.path.insert без дублей: резолверы зовутся в цикле по прогонам, и голый insert
    растил бы sys.path на запись за вызов (и замедлял КАЖДЫЙ последующий импорт)."""
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# Фолбэк финального шага плоских веток, если реестр шагов не прочитался. Живой источник —
# skills/<skill>/references/manifest-steps.json (последний элемент).
_FLAT_FINAL_FALLBACK = {"forgefix": "fix-spec"}


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

    Плоская ветка (forgefix) — последний шаг её реестра. feature-pipeline — шаги
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
        _ensure_path(_HERE.parents[1] / "feature-pipeline" / "scripts")
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


def nested_runs(project: Path, target: Path, exclude) -> list:
    """ЗАВЕРШЁННЫЕ прогоны, чьи доки лежат внутри архивируемого каталога: [(skill, feature, docs)].

    Их доки уезжают вместе с папкой стори — значит, и стейт обязан уехать с ними. Иначе
    история фикса оставалась в ground/statements/ сиротой (доков на месте нет, прогон в
    выборках есть), а restore стори возвращал доки без стейта."""
    out = []
    for skill, feature in find_runs(Path(project)):
        if (skill, feature) == exclude:
            continue
        try:
            d = task_docs_dir(project, skill, feature)
        except Exception:  # noqa: BLE001
            continue
        if d != target and target in d.parents:
            out.append((skill, feature, d))
    return out


def unmerged_nested(project: Path, src: Path, base: Path) -> list:
    """Дельты фиксов внутри папки стори, не сведённые с мастером: ['<слаг> (<состояние>)'].

    Архивация стори уносит fixes/ целиком, а дельты ищутся обходом рабочего каталога — неслитый
    фикс выпал бы из /forge-merge молча, и его требования не попали бы в мастер никогда."""
    out = []
    for sdd in sorted(src.glob("fixes/*/sdd.md")):
        slug = _rel(sdd.parent, base)
        ds = delta_state(project, slug)
        if ds in ("new", "drifted", "unknown-format"):
            out.append(f"{slug} ({ds})")
    return out


def _move_into(src: Path, target: Path) -> bool:
    """Влить src в уже существующий target БЕЗ archive-meta (каталог, куда раньше отдельно
    уехали фиксы этой стори). Пересечения имён — False, ничего не тронуто."""
    if (target / META_NAME).exists() or not target.is_dir():
        return False
    names = [c.name for c in src.iterdir()]
    if any((target / n).exists() and not (n == "fixes" and (target / n).is_dir()) for n in names):
        return False
    if (src / "fixes").is_dir() and (target / "fixes").is_dir():
        if any((target / "fixes" / c.name).exists() for c in (src / "fixes").iterdir()):
            return False
    for c in sorted(src.iterdir()):
        if c.name == "fixes" and (target / "fixes").is_dir():
            for f in sorted(c.iterdir()):
                shutil.move(str(f), str(target / "fixes" / f.name))
            c.rmdir()
        else:
            shutil.move(str(c), str(target / c.name))
    src.rmdir()
    return True


def delta_state(project: Path, slug: str):
    """Состояние дельты относительно мастера: merged|new|drifted|unknown-format|no-master."""
    try:
        _ensure_path(_HERE.parents[1] / "system-analyst" / "scripts")
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


def _require_drop_consent(project: Path, prefix: str, slug: str) -> str:
    """Второй слой R4 (первый — gate-guard.check_archive_drop): снять прогон с активных можно
    только с согласием пользователя. Возвращает ключ маркера; нет маркера → Fail(code=3)."""
    key = safe_component(f"{prefix}-{slug}")
    if FE.approval(project, key) is None:
        raise Fail(f"снять прогон '{slug}' с активных — R4: прогон перестанет числиться живым "
                   f"(у put --force удалятся и git-чекпойнты). Нужно согласие пользователя — "
                   f"approval-маркер '{key}': record_approval.py --key {key} --approved-by user "
                   f"--reason \"<почему брошен>\" --evidence \"<дословная цитата пользователя>\".\n"
                   f"   Какой прогон брошен, решает пользователь: покажи `status` и спроси. "
                   f"Ничего не изменилось.", code=3)
    return key


def _park_checkpoints(project: Path, feature: str, tag: str) -> int:
    """Снять чекпойнты брошенного прогона с живого namespace, не теряя. Best-effort."""
    try:
        from checkpoint import park_checkpoints
        return int(park_checkpoints(Path(project), feature, tag))
    except Exception:  # noqa: BLE001 — не git-репо/нет ref'ов: уборка не обязана падать
        return 0


def _drop_checkpoints(project: Path, feature: str) -> int:
    """Снять git-чекпойнты завершённой фичи. Best-effort: нет git — просто 0."""
    try:
        from checkpoint import delete_checkpoints
        return int(delete_checkpoints(Path(project), feature))
    except Exception:  # noqa: BLE001 — не git-репо/нет ref'ов: уборка не обязана падать
        return 0


def archive_feature(project, slug, skill=None, force: bool = False, reason=None,
                    dry_run: bool = False, assume_merged: bool = False) -> dict:
    """Перенести доки завершённой стройки в <docs_base>/archive/<слаг>. Отказ — Fail.

    assume_merged честится ТОЛЬКО на dry_run: там мастер ещё не записан, пересчитанное
    состояние заведомо до-мерджевое, и отказ был бы про уже решённую проблему. На РЕАЛЬНОМ
    переносе состояние пересчитывается всегда, что бы ни передал вызывающий: доверие слову
    вызывающего и было дырой в гейте — ошибись он, и требования уехали бы в архив мимо
    мастера, а заметить это было бы уже нечем."""
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
        pending = unmerged_nested(project, src, base)
        if pending:
            raise Fail("внутри '{}' лежат дельты фиксов, не сведённые с мастером: {}.\n"
                       "   Архивация унесла бы их мимо мастера. Сначала /forge-merge по каждой "
                       "(или /forge-merge --all — фиксы сливаются раньше стори)."
                       .format(slug, "; ".join(pending)))
    nested = nested_runs(project, src, exclude=(skill, feature))

    ds = "assumed-merged" if (assume_merged and dry_run) else delta_state(project, slug)
    if ds in ("new", "drifted") and not force:
        raise Fail("дельта '{}' не сведена с мастером (состояние: {}).\n"
                   "   Сначала /forge-merge {} — иначе требования уедут в архив, минуя мастер."
                   .format(slug, ds, feature))
    if ds == "unknown-format" and not force:
        # Форма мастера не разобрана: сведена дельта или нет — НЕИЗВЕСТНО. Пропустить архивацию
        # значит увезти требование из обхода _features мимо мастера и молча его потерять.
        raise Fail("формат мастера не описан профилем, состояние дельты '{}' неизвестно.\n"
                   "   Сначала /forge-spec research — снять профиль формата спеки проекта."
                   .format(slug))

    dest_base = archive_docs_dir(project)
    target = dest_base / slug
    merge_into = target.exists() and not (target / META_NAME).exists() and target.is_dir()
    if target.exists() and not merge_into:
        target = target.parent / f"{target.name}-{_ts()}"

    plan = {
        "ok": True, "slug": slug, "skill": skill, "feature": feature,
        "source": _rel(src, base), "target": str(target), "dry_run": bool(dry_run),
        "state_target": str(state_archive_dir(project, skill) / feature),
        "delta_state": ds, "forced": bool(force),
        "nested": [f"{s}/{f}" for s, f, _ in nested],
    }
    if dry_run:
        plan["moved"] = False
        return plan

    st_src = state_dir(project, skill, feature)
    st_target = state_archive_dir(project, skill) / feature
    if st_target.exists():
        st_target = st_target.parent / f"{st_target.name}-{_ts()}"

    # Порядок: сначала ВСЕ стейты (свой + вложенных фиксов стори), доки — последними. Откат
    # стейта — один rename каталога; откат доков, влитых в существующий архив стори (_move_into),
    # был бы поштучным. Падение на доках — откатываем стейты, и ничего не изменилось.
    moves = [(st_src, st_target)]
    for n_skill, n_feature, _ in nested:
        n_dst = state_archive_dir(project, n_skill) / n_feature
        if n_dst.exists():
            n_dst = n_dst.parent / f"{n_dst.name}-{_ts()}"
        moves.append((state_dir(project, n_skill, n_feature), n_dst))
    moved_states = []      # [(откуда, куда)] — и для отката, и в мету
    try:
        for a, b in moves:
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(a), str(b))
            moved_states.append((a, b))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (merge_into and _move_into(src, target)):
            if merge_into:
                target = target.parent / f"{target.name}-{_ts()}"
            shutil.move(str(src), str(target))
    except OSError as e:
        # Частичный перенос хуже отказа: доки на месте, а стейт уехал — прогон выпал из
        # выборок с живыми артефактами. Возвращаем стейты и отказываем целиком.
        for a, b in reversed(moved_states):
            shutil.move(str(b), str(a))
        raise Fail(f"архив не переносится ({src} → {target}): {e}. "
                   f"Стейт возвращён на место, ничего не изменилось.")
    moved_states = moved_states[1:]
    # Husk-каталоги ('<стори>/fixes/') подчищаем ТОЛЬКО когда уехали обе половины. Прибрать
    # раньше не смертельно (shutil.move на откате пересоздаёт путь copytree-фолбэком), но тогда
    # откат — полное копирование дерева доков вместо rename. Порядок «сначала оба переноса,
    # потом уборка» держит откат дешёвым и не полагается на недокументированный фолбэк.
    _prune_empty(src.parent, base)

    dropped = _drop_checkpoints(project, feature)

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
        "state_source": _rel(st_src, ground_dir(project)),
        "state_target": _rel(st_target, ground_dir(project)),
        "checkpoints_deleted": dropped,
        "steps": st["steps"],
        "delta_state": ds,
    }
    if moved_states:
        meta["nested_states"] = [{"state_source": _rel(a, ground_dir(project)),
                                  "state_target": _rel(b, ground_dir(project))}
                                 for a, b in moved_states]
    if force:
        meta["forced"] = True
        meta["reason"] = reason or "(причина не указана)"
    (target / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    plan["moved"] = True
    plan["state_target"] = str(st_target)
    plan["checkpoints_deleted"] = dropped
    return plan


def abandon_run(project, feature: str, skill=None, reason: str = "",
                dry_run: bool = False) -> dict:
    """Убрать БРОШЕННЫЙ прогон: стейт → ground/archive/<skill>/<feature>/, без доков.

    Зачем отдельная команда. `put` уносит ДВЕ половины и требует каталог доков — проверка
    стоит ДО --force и им не снимается. Прогон, умерший до первого артефакта (init.py прошёл,
    фаза не дошла до записи), каталога доков не имеет по определению: для него `put` давал
    DENY при любых флагах, `status` его не показывал вовсе (там `if not docs.is_dir():
    continue`), а руками из ground/statements/ удалять нельзя — это control-plane,
    state-write-guard режет unlink. Единственным выходом оставался `rm -rf` мимо харнеса,
    и всё это время брошенный прогон перехватывал резолв активной фичи на себя.

    Стейт НЕ теряется: он переезжает в тот же ground/archive/, что и у `put`, с
    archive-meta.json рядом. Причина обязательна — это запись в control-plane, и «почему
    прогон брошен» единственное, чего потом не восстановить.

    Есть доки — отказ: значит, прогон не пустой, и уносить надо обе половины (`put --force`),
    иначе доки остаются сиротой, а дельта — несведённой с мастером.
    """
    project = Path(project)
    feature = norm_slug(feature)
    if "/" in feature:
        raise Fail(f"abandon принимает слаг ОДНОГО прогона, не '{feature}' "
                   f"(каталог фикса внутри стори — это доки, ищи их через put)")
    skill = _skill_for(project, feature, skill)

    st_src = state_dir(project, skill, feature)
    if not st_src.is_dir():
        raise Fail(f"нет стейта прогона: {st_src}")

    try:
        docs = task_docs_dir(project, skill, feature)
    except Exception:  # noqa: BLE001 — резолвер доков не поднялся: считаем, что доков нет
        docs = None
    if docs is not None and docs.is_dir():
        raise Fail(f"у прогона '{skill}/{feature}' ЕСТЬ каталог доков ({docs}).\n"
                   f"   abandon уносит только стейт и оставил бы их сиротой. Уносить обе "
                   f"половины — put {feature} --force --reason '<почему>'.", code=3)

    # Манифеста может не быть (стейт уже наполовину снесён руками) — тогда run_state ушёл бы
    # в SystemExit(4) через safe_load_json, и брошенный каталог остался бы неубираемым ВООБЩЕ.
    if manifest_path(project, skill, feature).exists():
        st = run_state(project, skill, feature)
    else:
        st = {"manifest": {}, "summary": {}, "steps": {}, "open": []}
    st_target = state_archive_dir(project, skill) / feature
    if st_target.exists():
        st_target = st_target.parent / f"{st_target.name}-{_ts()}"

    plan = {"ok": True, "skill": skill, "feature": feature, "abandoned": True,
            "state_source": _rel(st_src, ground_dir(project)),
            "state_target": str(st_target), "dry_run": bool(dry_run),
            "status": st["summary"].get("status"), "open": st["open"]}
    if dry_run:
        plan["moved"] = False
        return plan

    st_target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(st_src), str(st_target))
    except OSError as e:
        raise Fail(f"стейт прогона не переносится ({st_src} → {st_target}): {e}. "
                   f"Ничего не изменилось.")

    # Чекпойнты брошенного прогона не удаляются, а паркуются (refs/forge/abandoned/<метка>):
    # с живого namespace их снять надо — новый прогон с тем же слагом иначе откатывался бы на
    # чужой снапшот, — но restore обязан вернуть прогон целиком, с точками отката.
    tag = f"{skill}-{st_target.name}"
    parked = _park_checkpoints(project, feature, tag)

    man = st["manifest"]
    meta = {
        "version": 1,
        "slug": feature,
        "skill": skill,
        "feature": feature,
        "pipeline_id": man.get("pipeline_id"),
        "started_at": man.get("started_at"),
        "last_update": man.get("last_update"),
        "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "abandoned": True,
        "reason": reason or "(причина не указана)",
        "state_source": _rel(st_src, ground_dir(project)),
        "state_target": _rel(st_target, ground_dir(project)),
        "checkpoints_deleted": 0,
        "checkpoints_parked": parked,
        "checkpoints_tag": tag,
        "steps": st["steps"],
    }
    # Мета едет В САМ перенесённый стейт: доков у брошенного прогона нет, а без неё
    # заархивированный каталог не отличить от заготовки под новый прогон.
    (st_target / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    plan["moved"] = True
    plan["checkpoints_parked"] = parked
    return plan


def _require_inside(path: Path, root: Path, field: str) -> None:
    """Путь из archive-meta.json — только внутрь своего корня. Мета лежит в каталоге, который
    правит модель, и `"source": "../…"` выносил restore за пределы docs/ground (tasks/015 п.2)."""
    try:
        p, r = Path(path).resolve(), Path(root).resolve()
    except (OSError, ValueError):
        raise Fail(f"archive-meta: поле {field} не резолвится: {path}")
    if p == r or r not in p.parents:
        raise Fail(f"archive-meta: поле {field} указывает за пределы {root}: {path}. "
                   f"Мета правлена руками — ничего не изменилось.")


def _abandoned_runs(project: Path) -> list:
    """[(каталог стейта, мета)] брошенных прогонов: мета лежит в самом стейте
    ground/archive/<skill>/<feature>/ — доков у них нет."""
    out = []
    sa = state_archive_dir(project)
    if not sa.is_dir():
        return out
    for meta_file in sorted(sa.glob("*/*/" + META_NAME)):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(meta, dict) and meta.get("abandoned"):
            out.append((meta_file.parent, meta))
    return out


def _restore_abandoned(project: Path, slug: str, dry_run: bool) -> dict:
    """Вернуть брошенный прогон: стейт ground/archive/ → statements/, отложенные чекпойнты —
    на место. Раньше abandon был необратим: list/restore видели только docs/archive, а
    чекпойнты удалялись (боевой прогон, A9)."""
    name = slug.split("/")[-1]
    hits = [(d, m) for d, m in _abandoned_runs(project)
            if name in (m.get("feature"), d.name)]
    if not hits:
        raise Fail(f"нет в архиве: ни доков ({archive_docs_dir(project) / slug}), ни "
                   f"брошенного прогона '{name}' (ground/archive/)")
    if len(hits) > 1:
        raise Fail("брошенных прогонов '{}' несколько: {}. Назови каталог."
                   .format(name, ", ".join(_rel(d, ground_dir(project)) for d, _ in hits)), code=3)
    src, meta = hits[0]
    feature = meta.get("feature") or name
    target = state_dir(project, meta.get("skill") or "feature-pipeline", feature)
    _require_inside(target, ground_dir(project) / "statements", "skill/feature")
    if target.exists():
        raise Fail(f"место занято: {target} — идёт прогон с тем же слагом; сначала сними его", code=3)
    plan = {"ok": True, "slug": feature, "abandoned": True, "source": str(src),
            "target": str(target), "state_target": str(target), "dry_run": bool(dry_run)}
    if dry_run:
        plan["moved"] = False
        return plan
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    (target / META_NAME).unlink(missing_ok=True)
    _prune_empty(src.parent, state_archive_dir(project))
    try:
        from checkpoint import unpark_checkpoints
        plan["checkpoints_restored"] = int(unpark_checkpoints(
            project, feature, meta.get("checkpoints_tag") or ""))
    except Exception:  # noqa: BLE001 — не git-репо: вернули стейт без точек отката
        plan["checkpoints_restored"] = 0
    plan["moved"] = True
    return plan


def restore_feature(project, slug, dry_run: bool = False) -> dict:
    """Вернуть заархивированные доки обратно в feature-pipeline/ — либо брошенный прогон
    (abandon) обратно в statements/."""
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
            return _restore_abandoned(project, s, dry_run)

    meta_file = src / META_NAME
    rel = _rel(src, arc)
    meta = {}
    if meta_file.is_file():
        meta = safe_load_json(meta_file, what=META_NAME)
        rel = meta.get("source") or rel
    target = base / rel
    _require_inside(target, base, "source")
    if target.exists():
        raise Fail(f"место занято: {target} — уберите каталог или переименуйте архив")

    g = ground_dir(project)
    st_src = st_target = None
    if meta.get("state_target") and meta.get("state_source"):
        st_src, st_target = g / meta["state_target"], g / meta["state_source"]
        _require_inside(st_src, g, "state_target")
        _require_inside(st_target, g / "statements", "state_source")
        if not st_src.is_dir():
            st_src = st_target = None            # стейт уже убрали руками — вернём одни доки
        elif st_target.exists():
            raise Fail(f"место стейта занято: {st_target} — уберите каталог или переименуйте")

    # Стейты фиксов, уехавших вместе со стори, возвращаются вместе с ней.
    nested = []
    for n in meta.get("nested_states") or []:
        a, b = g / n.get("state_target", ""), g / n.get("state_source", "")
        if n.get("state_target") and n.get("state_source") and a.is_dir():
            _require_inside(a, g, "nested_states.state_target")
            _require_inside(b, g / "statements", "nested_states.state_source")
            if b.exists():
                raise Fail(f"место стейта фикса занято: {b} — уберите каталог или переименуйте")
            nested.append((a, b))

    plan = {"ok": True, "slug": rel, "source": str(src), "target": str(target),
            "nested_states": [str(b) for _, b in nested],
            "state_source": str(st_src) if st_src else None,
            "state_target": str(st_target) if st_target else None,
            "dry_run": bool(dry_run)}
    if dry_run:
        plan["moved"] = False
        return plan
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    if st_src is not None:
        try:
            st_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(st_src), str(st_target))
            _prune_empty(st_src.parent, state_archive_dir(project))
        except OSError as e:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(src))
            raise Fail(f"стейт не возвращается ({st_src} → {st_target}): {e}. "
                       f"Доки оставлены в архиве, ничего не изменилось.")
    for a, b in nested:
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(a), str(b))
        _prune_empty(a.parent, state_archive_dir(project))
    try:
        (target / META_NAME).unlink()
    except OSError:
        pass
    _prune_empty(src.parent, arc)
    plan["moved"] = True
    return plan


def list_archived(project) -> list:
    project = Path(project)
    arc = archive_docs_dir(project)
    out = []
    if arc.is_dir():
        for meta_file in sorted(arc.glob("**/" + META_NAME)):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
            meta.setdefault("slug", _rel(meta_file.parent, arc))
            meta["path"] = str(meta_file.parent)
            out.append(meta)
    # Брошенные прогоны: доков нет, мета в самом стейте. Раньше list отвечал «Архив пуст»
    # при живом ground/archive/ — снятый прогон пропадал из всех выдач.
    for d, meta in _abandoned_runs(project):
        meta.setdefault("slug", meta.get("feature") or d.name)
        meta["path"] = str(d)
        out.append(meta)
    return out


def status(project) -> dict:
    """Что готово к архивации, что держит отказ, что уже в архиве."""
    project = Path(project)
    base = feature_docs_dir(project)
    rows, stateless = [], []
    for skill, feature in find_runs(project):
        try:
            docs = task_docs_dir(project, skill, feature)
        except Exception:  # noqa: BLE001
            continue
        if not docs.is_dir():
            # Стейт есть, доков нет — прогон, умерший до первого артефакта. Раньше такой
            # просто выпадал из выдачи: `status` молчал, `put` отказывал на отсутствии доков,
            # а резолв активной фичи всё это время считал его активным. Показываем отдельно
            # и с выходом (abandon), иначе о нём узнают только по промаху гейта.
            try:
                live = bool(_CL.run_is_live(safe_load_json(
                    manifest_path(project, skill, feature), what="manifest.json")))
            except Exception:  # noqa: BLE001 — битый/пропавший манифест: считаем живым
                live = True
            stateless.append({"skill": skill, "feature": feature, "live": live,
                              "state": _rel(state_dir(project, skill, feature),
                                            ground_dir(project))})
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
            "candidates": rows, "stateless": stateless, "archived": list_archived(project)}


# ── CLI ──────────────────────────────────────────────────────────────────────────────
def _print_status(data: dict) -> None:
    ready = [r for r in data["candidates"] if r["archivable"]]
    held = [r for r in data["candidates"] if not r["archivable"]]
    print(f"Архив: {data['archive']}")
    if ready:
        print(f"\nГотово к архивации ({len(ready)}):")
        for r in ready:
            ds = r["delta_state"] if r["delta_state"] in ("merged", "new", "drifted",
                                                          "unknown-format") else None
            print(f"   ✓ {r['slug']}  [{r['skill']}]{'  дельта: ' + ds if ds else ''}")
        print("   Перенести: /forge-archive put <слаг>  (или само на /forge-merge)")
    if held:
        print(f"\nПока не архивируется ({len(held)}):")
        for r in held:
            print(f"   · {r['slug']}  [{r['skill']}] — {'; '.join(r['blockers'])}")
    orphans = data.get("stateless") or []
    if orphans:
        print(f"\nСтейт без доков ({len(orphans)}) — прогон ещё не дал артефактов "
              f"либо брошен на старте:")
        for r in orphans:
            print(f"   {'⚠' if r['live'] else '·'} {r['skill']}/{r['feature']}  {r['state']}"
                  f"{'  (числится живым — участвует в резолве активной фичи)' if r['live'] else ''}")
        print("   Который из них идёт — знает пользователь; лишний не удаляй, а сними с активных.")
        print("   Убрать штатно: /forge-archive abandon <feature> --skill <S> "
              "--reason \"<почему>\" — по его явному ответу (R4: маркер abandon-<feature>)")
        print("   (стейт переезжает в ground/archive/, руками из ground/statements/ — нельзя)")
    if data["archived"]:
        print(f"\nВ архиве ({len(data['archived'])}):")
        for a in data["archived"]:
            print(f"   {a.get('slug')}  ({a.get('archived_at', '?')})")
    if not ready and not held and not orphans and not data["archived"]:
        print("\nНи одного прогона не найдено.")


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

    ab = sub.add_parser("abandon", help="убрать стейт БРОШЕННОГО прогона (без доков)")
    ab.add_argument("feature")
    ab.add_argument("--skill", default=None, help="namespace прогона (если фича в нескольких)")
    ab.add_argument("--reason", required=True, help="почему прогон брошен (обязательно)")
    ab.add_argument("--dry-run", action="store_true")
    ab.add_argument("--json", action="store_true")

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
                          f"{a.get('archived_at', '?')}  → {a.get('path')}"
                          f"{'  (брошен: ' + str(a.get('reason')) + ')' if a.get('abandoned') else ''}")
            return 0

        if cmd == "put":
            if args.force and not args.reason:
                raise Fail("--force без --reason: причина обхода гейта обязана быть записана")
            consent = (_require_drop_consent(project, "archive-force", args.slug)
                       if args.force and not args.dry_run else None)
            res = archive_feature(project, args.slug, skill=args.skill, force=args.force,
                                  reason=args.reason, dry_run=args.dry_run)
            if consent:
                FE.revoke_approval(project, consent, reason=f"согласие потрачено: put --force {args.slug}")
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif args.dry_run:
                print(f"dry-run: {res['source']} → {res['target']} (ничего не записано)")
            else:
                print(f"✅ {res['slug']} → {res['target']}")
                print(f"   стейт прогона → {res['state_target']}")
                if res.get("checkpoints_deleted"):
                    print(f"   сняты git-чекпойнты фичи: {res['checkpoints_deleted']} шт. "
                          f"(restore их не вернёт)")
                print("   Коммит архива — на тебе, forge не коммитит.")
            return 0

        if cmd == "abandon":
            if not (args.reason or "").strip():
                raise Fail("--reason пустой: причина, по которой прогон брошен, обязана "
                           "быть записана — восстановить её потом нечем")
            consent = (None if args.dry_run
                       else _require_drop_consent(project, "abandon", args.feature))
            res = abandon_run(project, args.feature, skill=args.skill, reason=args.reason,
                              dry_run=args.dry_run)
            if consent:
                FE.revoke_approval(project, consent, reason=f"согласие потрачено: abandon {args.feature}")
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif args.dry_run:
                print(f"dry-run: стейт {res['state_source']} → {res['state_target']} "
                      f"(ничего не записано)")
            else:
                print(f"✅ прогон {res['skill']}/{res['feature']} снят с активных")
                print(f"   стейт → {res['state_target']}")
                if res.get("checkpoints_parked"):
                    print(f"   git-чекпойнты отложены ({res['checkpoints_parked']} шт.) — "
                          f"вернутся вместе с прогоном: archive.py restore {res['feature']}")
                print("   Коммит — на тебе, forge не коммитит.")
            return 0

        if cmd == "restore":
            res = restore_feature(project, args.slug, dry_run=args.dry_run)
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif args.dry_run:
                print(f"dry-run: {res['source']} → {res['target']} (ничего не записано)")
            else:
                print(f"✅ возвращено: {res['target']}")
                if res.get("state_target") and not res.get("abandoned"):
                    print(f"   стейт прогона → {res['state_target']}")
                if res.get("checkpoints_restored"):
                    print(f"   git-чекпойнты возвращены: {res['checkpoints_restored']} шт.")
            return 0
    except Fail as e:
        print(f"[archive] DENY: {e}", file=sys.stderr)
        return e.code

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
