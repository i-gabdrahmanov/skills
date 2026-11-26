#!/usr/bin/env python3
"""pause.py — поставить прогон на паузу и вернуть его, чтобы переключиться на другую задачу.

Зачем. Активный прогон один на репозиторий: все хуки (gate-guard, tdd-guard, inline-phase-guard,
sod-enforcer, phase-gate, state-write-guard…) берут его из _project.resolve_active_run. Пока
незавершённый прогон лежит в ground/statements/, он по статусу шагов живой, и гейты его фазы
применяются к ЛЮБОЙ работе в репозитории — к другой задаче вне forge и к новому прогону (два
живых → выбор по mtime, config.py set inputs.* отказывает). Выйти можно было только через
`archive.py abandon` → `restore`: стейт переезжает, чекпойнты паркуются, а прогон числится
брошенным, хотя к нему собирались вернуться.

Пауза ничего не переносит: рядом с манифестом кладётся paused.json (провенанс
produced_by:"pause"), и прогон выпадает из резолва у всех хуков сразу (_project.iter_runs).
Стейт, чекпойнты, evidence — на месте. `resume` снимает маркер.

Согласие (R4). Пауза снимает с прогона enforcement: самовыписанная пауза = модель сама сняла
с себя TDD/SoD, написала что хотела и вернула прогон. Поэтому `pause` требует согласия
пользователя — одной командой: `--evidence "<его ответ дословно>"` (gate-guard сверяет цитату
с транскриптом той же проверкой, что у record_approval; скрипт вторым слоем проверяет, что это
согласие, и пишет его в ground/approvals.jsonl). Готовый маркер `pause-<feature>` от
record_approval.py тоже принимается. Согласие одноразовое и обязано назвать сам прогон. Пока
работает субагент этого хода, gate-guard паузу не пропускает: его результат в паузный прогон
уже не ляжет. Импорт модуля мимо скрипта (`python -c "import pause"`) gate-guard режет: так
он не видит ни слага, ни согласия. `resume` и `status` свободны — они enforcement возвращают
или только читают.

Пока прогон на паузе, update.py его шаги не двигает (exit 3): работа по нему без гейтов —
ровно то, что пауза не должна разрешать.

`resume` отказывает (exit 3), пока идёт другой живой прогон: два живых — то самое состояние,
от которого пауза уводит. Сначала на паузу его (по ответу пользователя), потом resume.
На возврате печатается, какие файлы менялись за время паузы (advisory: правки другой задачи
могли задеть код прогона).

Usage:
    python3 pause.py [--project <root>] status [--json]
    python3 pause.py [--project <root>] pause <feature> [--skill S] --reason R
                                             --evidence "<ответ пользователя>" [--json]
    python3 pause.py [--project <root>] resume <feature> [--skill S] [--json]

Exit: 0 — ок; 2 — отказ/ошибка; 3 — нужно решение пользователя.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# `_util` живёт в двух scripts-каталогах с разным содержимым (см. шапку update.py).
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

from _util import repo_root, safe_component, safe_slug, state_dir  # noqa: E402
# _util кладёт hooks/ в sys.path — оттуда единый обход прогонов и предикат живости.
from _project import iter_runs, live_runs, pause_marker_path, run_paused  # noqa: E402
import _config_loader as _CL  # noqa: E402
import forge_events as FE  # noqa: E402 — согласия пользователя (ground/approvals.jsonl)
import record_approval as RA  # noqa: E402 — та же проверка цитаты, что у record_approval.py

PRODUCED_BY = "pause"
MAX_LISTED = 20
# Стейт и поставка харнеса: в проекте они в .gitignore, но если нет — шумом в отчёте не идут.
_HARNESS_DIRS = ("ground", ".gigacode")


class Fail(RuntimeError):
    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _git(project: Path, *args: str) -> "str | None":
    try:
        r = subprocess.run(["git", "-C", str(project), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=10)
    except Exception:  # noqa: BLE001 — нет git: снимок рабочего дерева просто не делается
        return None
    return r.stdout if r.returncode == 0 else None


def _dirty_files(project: Path) -> dict:
    """{путь: sha256|"deleted"} незакоммиченных файлов (tracked + untracked, .gitignore учтён)."""
    out = _git(project, "status", "--porcelain", "-z", "--untracked-files=all")
    if out is None:
        return {}
    files, parts, i = {}, out.split("\0"), 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if len(rec) < 4:
            continue
        code, path = rec[:2], rec[3:]
        if "R" in code or "C" in code:
            i += 1                      # у переименования следом идёт исходный путь
        if path.split("/", 1)[0] in _HARNESS_DIRS:
            continue                    # стейт харнеса — не правки задачи
        files[path] = _disk_hash(project, path)
    return files


def _disk_hash(project: Path, path: str) -> str:
    p = project / path
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "deleted"
    except OSError:
        return "deleted"


def _runs(project: Path) -> list:
    """[{skill, feature, paused, live, step, status, marker}] — все прогоны, паузные тоже."""
    rows = []
    for _mt, sk, ft, mp in iter_runs(project, include_paused=True):
        try:
            man = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            man = None
        paused = run_paused(mp.parent)
        marker = None
        if paused:
            try:
                marker = json.loads(pause_marker_path(project, sk, ft).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = None
        steps = man.get("steps", []) if isinstance(man, dict) else []
        open_steps = [s for s in steps if isinstance(s, dict)
                      and s.get("status") in _CL._LIVE_STEP_STATUSES]
        cur = next((s for s in open_steps if s.get("status") == "in_progress"), None) \
            or (open_steps[0] if open_steps else {})
        rows.append({"skill": sk, "feature": ft, "paused": paused,
                     "live": man is None or _CL.run_is_live(man),
                     "step": cur.get("id"), "status": cur.get("status"), "marker": marker})
    return rows


def _pick(project: Path, feature: str, skill) -> dict:
    try:
        safe_slug(feature)
    except ValueError as e:
        raise Fail(str(e))
    hits = [r for r in _runs(project) if r["feature"] == feature
            and (skill is None or r["skill"] == skill)]
    if not hits:
        raise Fail(f"нет прогона '{feature}'{' в namespace ' + skill if skill else ''} "
                   f"в ground/statements/ (список: pause.py status)")
    if len(hits) > 1:
        raise Fail("прогон '{}' есть в нескольких namespace ({}) — уточни --skill."
                   .format(feature, ", ".join(r["skill"] for r in hits)), code=3)
    return hits[0]


def _describe(r: dict) -> str:
    where = f", стоит на шаге {r['step']} ({r['status']})" if r.get("step") else ""
    return f"{r['skill']}/{r['feature']}{where}"


def pause_run(project: Path, feature: str, skill=None, reason: str = "",
              evidence: str = "") -> dict:
    run = _pick(project, feature, skill)
    sk, ft = run["skill"], run["feature"]
    if run["paused"]:
        return {"ok": True, "skill": sk, "feature": ft, "paused": True, "already": True}
    if not run["live"]:
        raise Fail(f"прогон {sk}/{ft} завершён — на паузу ставить нечего; убрать его из "
                   f"резолва — archive.py put {ft}")
    key = safe_component(f"pause-{ft}")
    if evidence:
        problem = RA._check_evidence(key, evidence)
        if problem:
            raise Fail(problem)
        if FE.approval(project, key) is None:
            FE.append_approval(project, key, approved_by="user", reason=reason,
                               approval_kind="approval", evidence=evidence, feature=ft,
                               recorded_by="pause")
    if FE.approval(project, key) is None:
        raise Fail(f"поставить прогон {_describe(run)} на паузу — R4: гейты его фазы "
                   f"перестанут действовать. Нужно согласие пользователя: спроси его (что "
                   f"делаем с прогоном: на паузу / продолжить / бросить), и ТОЛЬКО после "
                   f"явного «да, на паузу» — pause.py pause {ft} --reason \"<зачем пауза>\" "
                   f"--evidence \"<его ответ дословно>\". Ничего не изменилось.", code=3)
    head = (_git(project, "rev-parse", "HEAD") or "").strip() or None
    marker = {"version": 1, "produced_by": PRODUCED_BY, "skill": sk, "feature": ft,
              "paused_at": _now(), "reason": reason, "step": run["step"],
              "head": head, "dirty": _dirty_files(project)}
    mp = pause_marker_path(project, sk, ft)
    tmp = mp.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, mp)
    except OSError as e:
        # Согласие тратится и на провале: неистраченное пережило бы этот вызов, и следующая
        # пауза прошла бы без вопроса пользователю.
        FE.revoke_approval(project, key, reason=f"пауза {ft} не записалась: {e}")
        raise Fail(f"маркер паузы не записался ({mp}): {e}. Прогон не на паузе; согласие "
                   f"снято — спроси пользователя заново.")
    FE.revoke_approval(project, key, reason=f"согласие потрачено: pause {ft}")
    return {"ok": True, "skill": sk, "feature": ft, "paused": True, "already": False,
            "step": run["step"]}


def _changed_since(project: Path, marker: dict) -> list:
    """Файлы, чьё содержимое изменилось с момента паузы (новые коммиты + рабочее дерево).

    Кандидаты — всё, что с тех пор попало в коммиты или грязно сейчас, и всё, что было грязным
    на паузе. Грязный на паузе файл сверяется со СВОИМ содержимым на тот момент: закоммитил
    пользователь ту же правку — это не изменение. Чистый на паузе совпадал со старым HEAD,
    значит, раз он попал в кандидаты, он изменился."""
    candidates = set()
    head = marker.get("head")
    now_head = (_git(project, "rev-parse", "HEAD") or "").strip()
    if head and now_head and head != now_head:
        diff = _git(project, "diff", "--name-only", head, now_head)
        candidates.update(x for x in (diff or "").splitlines()
                          if x and x.split("/", 1)[0] not in _HARNESS_DIRS)
    before = marker.get("dirty") if isinstance(marker.get("dirty"), dict) else {}
    candidates |= set(before) | set(_dirty_files(project))
    return sorted(p for p in candidates
                  if p not in before or _disk_hash(project, p) != before[p])


def resume_run(project: Path, feature: str, skill=None) -> dict:
    run = _pick(project, feature, skill)
    sk, ft = run["skill"], run["feature"]
    if not run["paused"]:
        return {"ok": True, "skill": sk, "feature": ft, "resumed": False, "already": True}
    others = live_runs(project, exclude=(sk, ft))
    if others:
        names = "; ".join(_describe(o) for o in others)
        raise Fail(f"идёт другой прогон: {names}. Два живых прогона — гейты поедут по одному "
                   f"из них наугад. Спроси пользователя, что с ним: поставить на паузу "
                   f"(pause.py pause <feature> — R4, по его ответу) или довести/бросить; "
                   f"потом повтори resume. Ничего не изменилось.", code=3)
    marker = run["marker"] or {}
    changed = _changed_since(project, marker)
    pause_marker_path(project, sk, ft).unlink()
    return {"ok": True, "skill": sk, "feature": ft, "resumed": True, "already": False,
            "step": run["step"], "paused_at": marker.get("paused_at"),
            "changed_files": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Пауза прогона forge (переключение на другую задачу).")
    ap.add_argument("--project", default=None, help="корень проекта (по умолчанию git toplevel)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("status", help="какие прогоны идут и какие на паузе")
    s.add_argument("--json", action="store_true")
    p = sub.add_parser("pause", help="поставить прогон на паузу (R4: согласие pause-<feature>)")
    p.add_argument("feature")
    p.add_argument("--skill", default=None)
    p.add_argument("--reason", required=True, help="зачем пауза (на какую задачу переключаемся)")
    p.add_argument("--evidence", default="", help="ответ пользователя дословно (согласие на паузу)")
    p.add_argument("--json", action="store_true")
    r = sub.add_parser("resume", help="вернуть прогон с паузы")
    r.add_argument("feature")
    r.add_argument("--skill", default=None)
    r.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    project = Path(args.project).resolve() if args.project else Path(repo_root())
    cmd = args.cmd or "status"
    as_json = getattr(args, "json", False)

    try:
        if cmd == "status":
            rows = [r for r in _runs(project) if r["paused"] or r["live"]]
            if as_json:
                out = []
                for r in rows:
                    m = r.pop("marker") or {}
                    if r["paused"]:
                        r.update(paused_at=m.get("paused_at"), reason=m.get("reason"))
                    out.append(r)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            live = [r for r in rows if not r["paused"]]
            paused = [r for r in rows if r["paused"]]
            if not rows:
                print("Незавершённых прогонов нет.")
            if live:
                print(f"Идут ({len(live)}):")
                for r in live:
                    print(f"   ▶ {_describe(r)}")
            if paused:
                print(f"На паузе ({len(paused)}):")
                for r in paused:
                    m = r["marker"] or {}
                    print(f"   ⏸ {_describe(r)} — с {m.get('paused_at', '?')}"
                          f"{': ' + m['reason'] if m.get('reason') else ''}")
                print("   Вернуть: pause.py resume <feature>")
            return 0

        if cmd == "pause":
            if not (args.reason or "").strip():
                raise Fail("--reason пустой: зачем пауза, обязано быть записано")
            res = pause_run(project, args.feature, skill=args.skill, reason=args.reason,
                            evidence=args.evidence)
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif res["already"]:
                print(f"прогон {res['skill']}/{res['feature']} уже на паузе")
            else:
                print(f"⏸ прогон {res['skill']}/{res['feature']} на паузе"
                      f"{' (шаг ' + res['step'] + ')' if res.get('step') else ''} — гейты его "
                      f"фазы сняты, стейт на месте")
                print(f"   Вернуть: pause.py resume {res['feature']}")
            return 0

        if cmd == "resume":
            res = resume_run(project, args.feature, skill=args.skill)
            if as_json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            elif res["already"]:
                print(f"прогон {res['skill']}/{res['feature']} не на паузе — продолжай как есть")
            else:
                print(f"▶ прогон {res['skill']}/{res['feature']} снова активен"
                      f"{' (шаг ' + res['step'] + ')' if res.get('step') else ''}")
                ch = res["changed_files"]
                if ch:
                    print(f"   ⚠ за время паузы менялись файлы ({len(ch)}) — проверь, не задели "
                          f"ли они код прогона:")
                    for f in ch[:MAX_LISTED]:
                        print(f"      {f}")
                    if len(ch) > MAX_LISTED:
                        print(f"      … и ещё {len(ch) - MAX_LISTED}")
            return 0
    except Fail as e:
        print(f"[pause] DENY: {e}", file=sys.stderr)
        return e.code

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
