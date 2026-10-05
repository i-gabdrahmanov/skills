#!/usr/bin/env python3
"""spec_cli.py — работа с требованиями-мастером (`specs/<cap>/spec.md`) ПО ЗАПРОСУ.

Мастер обновляет пользователь командой `/forge-spec`, а не пайплайн: фаза 06 в него не пишет,
она лишь сообщает о расхождении. Здесь сидит весь разбор аргументов, чтобы слэш-команда была
тонкой обёрткой и не зависела от того, как модель поймёт подкоманду.

Подкоманды:
  status                     что в мастере + какие дельты фич ещё не слиты
  diff <slug>                план операций (+ / ~ / =), ничего не пишет
  merge <slug> [--all]       слить дельту в мастер — либо СВЕРИТЬ её с ним (--master-first)
  remove <ID> --reason R     снять требование с указанием причины
  check                      гейт состава мастера (check_master_spec)
  migrate                    перенести плоский легаси-мастер (§Требования + §Сценарии) на ID

Режим — ФЛАГ КОМАНДЫ, а не ключ конфига: это выбор на одну операцию, и ждать перепинивания
политики прогона он не обязан.
  (без флага, дефолт)   мастер собирается ИЗ дельт: merge дописывает в него требования фичи.
  --master-first        мастер ведёт аналитик, дельта выделяется ИЗ него. Тогда merge в мастер
                        не пишет, а СВЕРЯЕТ: план слияния обязан быть пустым, любая операция
                        (+/~) — расхождение (exit 3). Требование действительно введено дельтой —
                        значит это обычное слияние: запусти без флага.

Присутствие задачи в мастере проверяется В ЛЮБОМ РЕЖИМЕ и печатается отчётом — до операции и
ещё раз после записи. Доки уезжают в архив только на ПОДТВЕРЖДЁННОМ merged; всё, что не
сошлось, — exit 3 с диагнозом и списком вариантов, выбирает пользователь.

По успеху слияния/сверки доки фичи уезжают в <docs_base>/archive/ (archive.py; `--no-archive`
отключает). Архивация — best-effort: её отказ не меняет код выхода самого слияния.

Политика forge-no-delivery: пишем только в рабочее дерево клона мастер-репо; коммит/push —
на пользователе.

Exit: 0 = ок, 2 = ошибка, 3 = нужно решение пользователя (неразрешённые modify).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts"))

import check_master_spec as gate          # noqa: E402
import merge_delta_to_master as engine    # noqa: E402
import spec_grammar as SG                 # noqa: E402
import spec_map as SM                     # noqa: E402


def _skill_paths():
    import skill_paths
    return skill_paths


def _resolve(args) -> "tuple[Path, Path, str, dict]":
    """(project_root, spec_path, capability, spec_opts) — для команд над ОДНИМ мастером
    (remove/check/migrate). По карте с несколькими сервисами сервис обязан быть назван:
    молча взять «первый» значит снять требование не в той спеке."""
    root = Path(args.project_root).resolve()
    m = SM.confirmed(root)
    if m and not args.spec and not args.capability and len(m.get("capabilities") or {}) > 1:
        raise SM.MapError("в карте мастера несколько сервисов — укажи --capability "
                          f"({', '.join(sorted(m['capabilities']))})")
    spec_path, capability = engine.resolve_spec(root, args.capability, args.spec)
    return root, spec_path, capability, engine.spec_options(root)


def _prefix(opts: dict, cli: "str | None") -> str:
    return cli or opts.get("id_prefix") or engine.DEFAULT_ID_PREFIX


_GRAMMAR_CACHE: dict = {}


def grammar(root: Path, prefix: "str | None" = None, cap: "str | None" = None,
            m: "dict | None" = None) -> "SG.Grammar":
    """Профиль формы мастера (NATIVE ← детект ← policy.json).

    С подтверждённой картой детект берётся из неё — у каждого сервиса своя форма, и авто-скан
    всей базы (который и выбирал навигационный индекс мастером) не запускается.

    Перед первым обращением снимается ресерч формата (`analyze_spec --if-missing`): без него
    проект со своей спекой разбирался бы форже-грамматикой — то есть никак. Скан идемпотентен
    и по отпечатку мастера, поэтому дешёвый; его отказ не должен ронять команду, поэтому
    best-effort.
    """
    if m is not None and cap:
        key = f"{root}::{cap}"
        if key not in _GRAMMAR_CACHE:
            _GRAMMAR_CACHE[key] = SG.load_profile(
                root, detected={"grammar": SM.grammar_dict(m, cap), "confidence": 1.0,
                                "matches_native": False})
        g = _GRAMMAR_CACHE[key]
        if prefix and prefix != g.id_prefix:
            g.id_prefix = prefix
        return g
    key = str(root)
    if key not in _GRAMMAR_CACHE:
        try:
            import analyze_spec
            analyze_spec.ensure(root)
        except Exception:  # noqa: BLE001 — детект не поднялся: работаем на policy + NATIVE
            pass
        _GRAMMAR_CACHE[key] = SG.load_profile(root)
    g = _GRAMMAR_CACHE[key]
    if prefix and prefix != g.id_prefix:
        g.id_prefix = prefix
    return g


def _cap_grammar(root: Path, prefix: "str | None", cap: "str | None") -> "SG.Grammar":
    m = SM.confirmed(root)
    if m and cap in (m.get("capabilities") or {}):
        return grammar(root, prefix, cap, m)
    return grammar(root, prefix)


def _drop_cache(root: Path) -> None:
    for k in [k for k in _GRAMMAR_CACHE if k == str(root) or k.startswith(f"{root}::")]:
        _GRAMMAR_CACHE.pop(k, None)


def _where(root: Path, args, slug: str, sdd: Path, prefix: "str | None") -> dict:
    """Куда сливать ЭТУ дельту: {spec, cap, g, headings, map, why} либо {problem, error}.

    Подтверждённая карта — сервис дельты по карте. Без карты по-старому работаем ТОЛЬКО на
    однозначном мастере: в базе нет ничего, кроме настроенного пути. Иначе (репо спек с
    сервисами, навигационными индексами и т.п.) — сначала research: угадывать, в какой из
    файлов писать, merge не будет."""
    cli_cap = getattr(args, "capability", None)
    explicit = getattr(args, "spec", None)
    m = SM.confirmed(root)
    if m and not explicit:
        cap, why, cands = SM.delta_capability(m, slug, sdd, cli_cap)
        if cap is None:
            if cli_cap:
                return {"problem": "error",
                        "error": f"сервиса «{cli_cap}» в карте нет (есть: {', '.join(cands)})"}
            return {"problem": "no-capability", "candidates": cands,
                    "error": f"{why} — в спеку какого сервиса сливать, решаешь ты"}
        e = SM.entry_for(m, cap) or {}
        return {"spec": SM.spec_path(root, m, cap), "cap": cap,
                "g": grammar(root, prefix, cap, m),
                "headings": (e.get("requirements_heading"), e.get("audit_heading")),
                "map": m, "why": why}
    if not explicit:
        amb = SM.ambiguous(root)
        if amb:
            draft = SM.load(root)
            if draft:
                err = ("карта мастера не подтверждена (или снята с другой базы) — проверь "
                       "её и подтверди: /forge-spec research --confirm")
            else:
                names = ", ".join(Path(a).name for a in amb[:4]) + (" …" if len(amb) > 4 else "")
                err = (f"структура мастера не снята: в базе {len(amb)} файл(ов) помимо "
                       f"настроенного пути ({names}) — сначала /forge-spec research")
            return {"problem": "no-map", "error": err}
    spec_path, cap = engine.resolve_spec(root, cli_cap, explicit)
    return {"spec": spec_path, "cap": cap, "g": grammar(root, prefix), "headings": (None, None),
            "map": None, "why": None}


def _unsupported(g: "SG.Grammar", root: Path) -> int:
    """Единый отказ «форма мастера не описана профилем» — exit 3 (нужно решение человека)."""
    ok, why = g.supported()
    if ok:
        return 0
    print("✗ формат требований-мастера не описан профилем:", file=sys.stderr)
    for r in why:
        print(f"   - {r}", file=sys.stderr)
    print(g.describe(), file=sys.stderr)
    print("   Разбор и готовые команды: /forge-spec research", file=sys.stderr)
    return 3


def _features(root: Path) -> list[tuple[str, Path]]:
    """Дельты: [(slug, sdd.md)] из <docs_base>/feature-pipeline/.

    Два уровня, потому что фикс живёт ВНУТРИ папки своей стори:
      • дельта фичи   — <feature>/sdd.md            → slug 'STOR-100'
      • дельта фикса  — <feature>/fixes/<bug>/sdd.md → slug 'STOR-100/fixes/BUG-512'
    Фикс без известной стори лежит плоско (<bug>/sdd.md) и попадает в первый случай."""
    try:
        base = _skill_paths().feature_docs_dir(root)
    except Exception:  # noqa: BLE001
        return []
    if not base.exists():
        return []
    out = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        sdd = d / "sdd.md"
        if sdd.exists():
            out.append((d.name, sdd))
        fixes = d / "fixes"
        if fixes.is_dir():
            for f in sorted(p for p in fixes.iterdir() if p.is_dir()):
                fix_sdd = f / "sdd.md"
                if fix_sdd.exists():
                    out.append((f"{d.name}/fixes/{f.name}", fix_sdd))
    return out


def _short(slug: str, feats: list[tuple[str, Path]]) -> str:
    """Как звать дельту в подсказке пользователю. У фикса полный слаг — `STOR-100/fixes/BUG-512`
    (папка внутри стори), но `_targets` принимает и короткий ключ бага, пока он однозначен.
    Печатали при этом длинный: команда мерджа выглядела громоздко, хотя достаточно `BUG-512`."""
    if "/fixes/" not in slug:
        return slug
    bug = slug.rsplit("/fixes/", 1)[1]
    collisions = sum(1 for s, _ in feats if s.endswith(f"/fixes/{bug}") or s == bug)
    return bug if collisions == 1 else slug


def _provenance(slug: str) -> str:
    """Строка провенанса `[from: …]` для мастера. У дельты фикса первым токеном обязана стоять
    СТОРИ: провенанс парсится до первого пробела (find_spec_anchor._FROM_SLUG), и по нему потом
    ищется якорь следующего бага. 'STOR-100/fixes/BUG-512' → 'STOR-100 fix/BUG-512'."""
    if "/fixes/" in slug:
        story, bug = slug.split("/fixes/", 1)
        return f"{story} fix/{bug}"
    return slug


def _master_text(spec_path: Path) -> str:
    return spec_path.read_text(encoding="utf-8", errors="replace") if spec_path.exists() else ""


def _remind(spec_path: Path) -> None:
    print(f"   Мастер: {spec_path}")
    print("   Коммит/push мастер-репо — на тебе, forge не коммитит.")


# ── подкоманды ─────────────────────────────────────────────────────────

def _in_master(master_text: str, slug: str, g: "SG.Grammar") -> "bool | None":
    """Помнит ли мастер эту задачу — по провенансу. None: формат провенанса не ведёт.

    Тем же пробником ищет след дельты судья (run_judge.check_spec), поэтому ответ у них один.
    Запрос строится от `_provenance(slug)`, а не от сырого слага: у дельты фикса провенанс —
    `<стори> fix/<баг>`, и поиск по `STOR-100/fixes/BUG-512` не нашёл бы ничего никогда.

    Гейтом это НЕ служит: у мастера с `provenance: none` тега нет и не будет, и блокировать по
    нему значило бы запретить такому проекту архивацию навсегда. Гейт — состояние дельты.
    """
    if getattr(g, "provenance", "from-bracket") == "none":
        return None
    return g.provenance_query(_provenance(slug)) in master_text


def _master_enabled(root: Path) -> bool:
    """docs.master.enabled — ведётся ли мастер вообще."""
    try:
        from _config_loader import load_project_config
        cfg = load_project_config(Path(root)) or {}
    except Exception:  # noqa: BLE001
        return False
    docs = cfg.get("docs") if isinstance(cfg, dict) else None
    master = (docs or {}).get("master") if isinstance(docs, dict) else None
    return bool((master or {}).get("enabled")) if isinstance(master, dict) else False


def _classify(kinds: list[str]) -> str:
    """Состояние дельты относительно мастера по плану операций."""
    if any(k == "add" for k in kinds):
        return "new"        # требований дельты в мастере нет
    if any(k == "modify" for k in kinds):
        return "drifted"    # дельта изменилась после слияния
    return "merged"


def _state_of(slug: str, sdd: Path, spec_path: Path, capability: str, prefix: str,
              g: "SG.Grammar | None" = None) -> str:
    """Состояние дельты по плану слияния (dry-run): new | drifted | merged | unknown-format.

    `status == "error"` (в дельте нет требований) считаем «делать нечего» — так эта ветка
    вела себя с самого начала, и на ней же стоит гейт архивации."""
    plan = engine.merge(sdd, spec_path, engine.default_template(), slug, capability,
                        prefix=prefix, dry_run=True, grammar=g)
    if plan["status"] == "unsupported":
        # Форму мастера не разобрали — «слито» это или нет, неизвестно. Врать «merged»
        # нельзя: на этом состоянии стоит гейт архивации, и требование уехало бы мимо мастера.
        return "unknown-format"
    if plan["status"] == "no-section":
        return "new"        # раздела требований нет — значит и требований дельты там нет
    if plan["status"] == "error":
        return "merged"
    return _classify(plan.get("kinds", []))


def delta_state(project_root, slug: str) -> str:
    """Публичный вход для archive.py: new | drifted | merged | unknown-format | no-master | no-delta.

    Архивация не имеет права утащить в архив дельту, которую мастер ещё не видел: доки уезжают
    из обхода `_features`, и требование пропало бы молча."""
    root = Path(project_root)
    if not _master_enabled(root):
        return "no-master"
    prefix = _prefix(engine.spec_options(root), None)
    for s, sdd in _features(root):
        if s != slug:
            continue
        try:
            w = _where(root, argparse.Namespace(capability=None, spec=None), s, sdd, prefix)
        except Exception:  # noqa: BLE001 — мастер не резолвится: гейт не давим
            return "no-master"
        if w.get("problem"):
            # Нет карты / сервис не выбран: слита ли дельта — НЕИЗВЕСТНО, архивация держит.
            return "unknown-format"
        return _state_of(s, sdd, w["spec"], w["cap"], prefix, w["g"])
    return "no-delta"


def _archive_one(root: Path, slug: str, dry_run: bool = False) -> dict:
    """Убрать доки сведённой/сверенной фичи в архив. Best-effort: отказ гейта архивации — не
    провал слияния (типичный отказ — сверку запустили посреди незавершённого прогона).

    Гейт состояния дельты архивация пересчитывает САМА: `assume_merged` честится только на
    dry-run, где мастер ещё не записан. На реальном переносе она смотрит на мастер, а не на
    слово вызывающего."""
    _ps = str(SCRIPT_DIR.parents[1] / "pipeline-state" / "scripts")
    if _ps not in sys.path:
        sys.path.insert(0, _ps)
    fail = {"ok": False, "target": None, "source": None}
    try:
        import archive
    except Exception as e:  # noqa: BLE001
        return dict(fail, error=f"архивация недоступна ({e})")
    try:
        res = archive.archive_feature(root, slug, dry_run=dry_run, assume_merged=dry_run)
        return {"ok": True, "error": None, "target": res["target"], "source": res["source"]}
    except archive.Fail as e:
        return dict(fail, error=str(e).split("\n")[0].strip())
    except SystemExit:
        return dict(fail, error="манифест прогона нечитаем")


def cmd_status(args) -> int:
    root = Path(args.project_root).resolve()
    m = SM.confirmed(root)
    if m and not args.spec:
        return _status_map(root, m, args)
    if not args.spec:
        amb = SM.ambiguous(root)
        if amb:
            draft = SM.load(root)
            if args.json:
                print(json.dumps({"map": "draft" if draft else None, "ambiguous": amb,
                                  "features": [s for s, _ in _features(root)]},
                                 ensure_ascii=False, indent=2))
                return 0
            print(f"Требования-мастер: {SM.master_base(root)}")
            print(f"   структура не снята: в базе {len(amb)} файл(ов) помимо настроенного пути "
                  f"({', '.join(Path(a).name for a in amb[:4])}{' …' if len(amb) > 4 else ''})")
            print("   → /forge-spec research --confirm" if draft
                  else "   → /forge-spec research — снять карту мастера, потом --confirm")
            feats = _features(root)
            if feats:
                print(f"   дельт фич: {len(feats)} — слить их можно после подтверждения карты")
            return 0
    return _status_single(args)


def _status_map(root: Path, m: dict, args) -> int:
    """Статус по карте: по каждому сервису — сколько требований и какие дельты куда."""
    prefix = _prefix(engine.spec_options(root), args.id_prefix)
    caps = m.get("capabilities") or {}
    per: dict = {c: {"new": [], "drifted": [], "merged": [], "unknown-format": []} for c in caps}
    unassigned: list = []
    feats = _features(root)
    for slug, sdd in feats:
        cap, why, cands = SM.delta_capability(m, slug, sdd, args.capability)
        if cap is None:
            unassigned.append({"slug": slug, "why": why, "candidates": cands})
            continue
        g = grammar(root, prefix, cap, m)
        st = _state_of(slug, sdd, SM.spec_path(root, m, cap), cap, prefix, g)
        per[cap][st].append(slug)
    out = {}
    for cap in sorted(caps):
        sp = SM.spec_path(root, m, cap)
        g = grammar(root, prefix, cap, m)
        text = _master_text(sp)
        reqs = engine.parse_master(text, prefix, g) if text else []
        out[cap] = {"spec": str(sp), "exists": sp.exists(), "form": caps[cap].get("form"),
                    "requirements": len(reqs),
                    "sections_present": bool(caps[cap].get("sections_present")),
                    **{k.replace("-", "_"): v for k, v in per[cap].items()}}
    if args.json:
        print(json.dumps({"map": "confirmed", "base": m.get("base"), "capabilities": out,
                          "unassigned": unassigned}, ensure_ascii=False, indent=2))
        return 0
    print(f"Требования-мастер по карте ({len(caps)} сервис(ов)): {m.get('base')}")
    for cap, o in out.items():
        line = f"   [{cap}] {Path(o['spec']).name}: требований {o['requirements']}"
        if not o["sections_present"]:
            line += " (раздела требований нет — merge --ensure-sections)"
        print(line)
        for key, label in (("new", "НЕ слито"), ("drifted", "РАЗОШЛОСЬ"), ("merged", "актуально"),
                           ("unknown_format", "ФОРМАТ НЕ РАЗОБРАН")):
            if o[key]:
                print(f"      {label}: {', '.join(o[key])}")
    if unassigned:
        print(f"   без сервиса ({len(unassigned)}): "
              + ", ".join(u["slug"] for u in unassigned))
        u = unassigned[0]
        print(f"   → /forge-merge {_short(u['slug'], feats)} --capability <сервис>"
              f"   (кандидаты: {', '.join(u['candidates'][:6])})")
    todo = [s for o in out.values() for s in o["new"] + o["drifted"]]
    if todo:
        print(f"   → /forge-merge {_short(todo[0], feats)}   (или /forge-merge --all)")
    return 0


def _status_single(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    text = _master_text(spec_path)
    prefix = _prefix(opts, args.id_prefix)
    g = grammar(root, prefix)
    reqs = engine.parse_master(text, prefix, g) if text else []
    scen = sum(len(r["scenarios"]) for r in reqs)

    # «Слито» определяем ПЛАНОМ, а не подстрокой провенанса: отредактированная после merge
    # дельта провенанс сохраняет, но мастер уже расходится с ней.
    state: dict[str, list[str]] = {"new": [], "drifted": [], "merged": [], "unknown-format": []}
    seen: dict = {}
    for slug, sdd in _features(root):
        st = _state_of(slug, sdd, spec_path, capability, prefix, g)
        state[st].append(slug)
        seen[slug] = _in_master(text, slug, g)
    total = sum(len(v) for v in state.values())
    # План говорит «слито», а следа дельты в мастере нет — значит совпали названия, а не
    # происхождение. Молчать об этом нельзя: на «слито» стоит гейт архивации.
    ghosts = [sl for sl in state["merged"] if seen.get(sl) is False]

    if args.json:
        print(json.dumps({"spec": str(spec_path), "exists": spec_path.exists(),
                          "capability": capability, "requirements": len(reqs),
                          "scenarios": scen, "features": total,
                          "grammar_native": g.is_native(),
                          "grammar_supported": g.supported()[0],
                          "in_master": seen,
                          "new": state["new"], "drifted": state["drifted"],
                          "merged": state["merged"],
                          "unknown_format": state["unknown-format"]},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"Требования-мастер [{capability}]: {spec_path}")
    if not spec_path.exists():
        print("   мастера ещё нет — создастся из шаблона при первом merge")
    else:
        print(f"   требований: {len(reqs)}, сценариев: {scen}")
    ok_fmt, why_fmt = g.supported()
    if not ok_fmt:
        print("   ФОРМАТ МАСТЕРА НЕ ОПИСАН ПРОФИЛЕМ: " + "; ".join(why_fmt))
        print("   → /forge-spec research — разбор формы спеки проекта")
    elif not g.is_native():
        print(f"   формат: свой (профиль проекта) — «{g.requirements_section[0] if g.requirements_section else 'без раздела'}», "
              f"{g.kind}/{g.scenario_style}; разбор: /forge-spec research")
    if not total:
        print("   дельт фич не найдено")
        return 0
    if state["new"]:
        print(f"   НЕ слито ({len(state['new'])} из {total}): {', '.join(state['new'])}")
    if state["drifted"]:
        print(f"   РАЗОШЛОСЬ после слияния ({len(state['drifted'])}): "
              f"{', '.join(state['drifted'])}")
    if state["merged"]:
        print(f"   актуально ({len(state['merged'])}): {', '.join(state['merged'])}")
    if ghosts:
        print(f"   ! провенанс не подтверждает ({len(ghosts)}): {', '.join(ghosts)} — "
              f"план говорит «актуально», следа дельты в мастере нет")
    if state["unknown-format"]:
        print(f"   ФОРМАТ МАСТЕРА НЕ РАЗОБРАН ({len(state['unknown-format'])}): "
              f"{', '.join(state['unknown-format'])}")
        print("   → /forge-spec research — снять профиль формата и применить его")
    todo = state["new"] + state["drifted"]
    if todo:
        first = _short(todo[0], _features(root))
        print(f"   → /forge-merge {first}   (или /forge-merge --all)")
        print(f"     мастер ведёт аналитик — сверка без записи: /forge-merge {first} --master-first")
    return 0


def _run_merge(args, slug: str, sdd: Path, spec_path: Path, capability: str,
               prefix: str, dry: bool, g: "SG.Grammar | None" = None,
               headings: "tuple" = (None, None)) -> dict:
    return engine.merge(sdd, spec_path, engine.default_template(), _provenance(slug), capability,
                        prefix=prefix, dry_run=dry, allow_modify=args.allow_modify,
                        modify_ids=set(args.modify or []), grammar=g,
                        ensure=bool(getattr(args, "ensure_sections", False)), headings=headings)


def cmd_diff(args) -> int:
    root = Path(args.project_root).resolve()
    prefix = _prefix(engine.spec_options(root), args.id_prefix)
    targets = _targets(root, args)
    if targets is None:
        return 2
    rc = 0
    for slug, sdd in targets:
        w = _where(root, args, slug, sdd, prefix)
        if w.get("problem"):
            print(f"✗ {slug}: {w['error']}")
            for c in w.get("candidates") or []:
                print(f"     → /forge-spec diff {_short(slug, _features(root))} --capability {c}")
            rc = 2 if w["problem"] == "error" else (rc or 3)
            continue
        spec_path, capability, g = w["spec"], w["cap"], w["g"]
        if _unsupported(g, root):
            rc = rc or 3
            continue
        res = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=True, g=g,
                         headings=w["headings"])
        if res["status"] in ("unsupported", "no-section"):
            print(f"✗ {slug}: {res['error']}")
            rc = rc or 3
            continue
        if res["status"] == "error":
            print(f"✗ {slug}: {res['error']}")
            rc = 2
            continue
        print(f"{slug} → {spec_path.name}{f' [{capability}]' if w['map'] else ''}"
              f"{' (мастер будет создан из шаблона)' if res['created'] else ''}"
              f"{' (будет дописан раздел: ' + ', '.join(res['sections_added']) + ')' if res.get('sections_added') else ''}")
        for line in res["ops"] or ["   (в дельте нет требований)"]:
            print(f"   {line}")
        if args.master_first and _classify(res["kinds"]) != "merged":
            print("   ! расхождение с мастером — мастер первичен, дельту приводят к нему")
            rc = rc or 3
            continue
        if res["blocked"]:
            short = _short(slug, _features(root))
            print(f"   ! modify требует подтверждения: {', '.join(res['blocked'])}")
            print(f"     → /forge-merge {short} --allow-modify"
                  f"  (или --modify {res['blocked'][0]})")
            rc = rc or 3
    return rc


def _targets(root: Path, args) -> "list[tuple[str, Path]] | None":
    """Дельты для обработки. При --all — ВСЕ; актуальные отсеет план (kinds == same),
    иначе отредактированная после merge дельта никогда бы не переисследовалась."""
    feats = _features(root)
    if getattr(args, "all", False):
        if not feats:
            print("Дельт фич не найдено.")
        return feats
    slug = getattr(args, "slug", None)
    if not slug:
        print("✗ укажи <slug> фичи или --all", file=sys.stderr)
        return None
    for s, p in feats:
        if s == slug:
            return [(s, p)]
    # Дельту фикса зовут по ключу бага ('BUG-512'), а лежит она внутри стори
    # ('STOR-100/fixes/BUG-512') — принимаем короткое имя, пока оно однозначно.
    short = [(s, p) for s, p in feats if s.endswith(f"/fixes/{slug}")]
    if len(short) == 1:
        return short
    if len(short) > 1:
        print(f"✗ «{slug}» неоднозначен — найден в нескольких стори: "
              f"{', '.join(s for s, _ in short)}. Укажи полный слаг.", file=sys.stderr)
        return None
    if args.sdd:
        return [(slug, Path(args.sdd))]
    print(f"✗ дельта «{slug}» не найдена (ожидался <docs>/feature-pipeline/{slug}/sdd.md "
          f"либо <docs>/feature-pipeline/<стори>/fixes/{slug}/sdd.md)", file=sys.stderr)
    return None


# ── отчёт о слиянии/сверке ─────────────────────────────────────────────

_STATE_WHY = {
    "new": "требований дельты в мастере нет",
    "drifted": "дельта разошлась с мастером после слияния",
    "merged": "дельта и мастер совпадают",
    "unknown-format": "форма мастера не разобрана — состояние неизвестно",
}

_PROBLEM_WHY = {
    "no-map": "структура мастера не снята",
    "no-capability": "не определено, в спеку какого сервиса сливать",
}


def _presence(v) -> str:
    if v is None:
        return "не определяется (формат мастера не ведёт провенанс)"
    return "да" if v else "нет"


def _choices(row: dict) -> list:
    """Варианты решения по диагнозу — и в текст отчёта, и в --json.

    Выбирает ПОЛЬЗОВАТЕЛЬ: ни один из них команда не применяет сама. Поэтому вариантов всегда
    несколько и среди них есть «ничего не сливать»: односложная подсказка читалась бы как
    указание, а это ровно то место, где угадывать нельзя."""
    slug, short = row["slug"], row["short"]
    p = row.get("problem")
    if p == "divergence":
        return [
            {"id": "align-delta", "slug": slug, "title": "Привести дельту к мастеру",
             "detail": "мастер первичен — правится sdd.md фичи, а не мастер", "command": None},
            {"id": "merge-anyway", "slug": slug,
             "title": "Всё-таки слить эти требования в мастер",
             "detail": "требование действительно введено дельтой",
             "command": f"/forge-merge {short}"},
            {"id": "archive-force", "slug": slug, "title": "Убрать доки мимо мастера",
             "detail": "требования останутся только в дельте — осознанный шаг",
             "command": f"/forge-archive put {short} --force --reason '<почему>'"},
        ]
    if p == "blocked-modify":
        blocked = row.get("blocked") or []
        return [
            {"id": "allow-modify", "slug": slug, "title": "Применить все ~ (modify)",
             "detail": "содержимое требований мастера заменится содержимым дельты",
             "command": f"/forge-merge {short} --allow-modify"},
            {"id": "modify-one", "slug": slug, "title": "Применить точечно",
             "detail": f"блокированы: {', '.join(blocked) or '—'}",
             "command": f"/forge-merge {short} --modify {blocked[0] if blocked else '<ID>'}"},
            {"id": "align-delta", "slug": slug, "title": "Привести дельту к мастеру",
             "detail": "мастер остаётся как есть", "command": None},
        ]
    if p == "no-map":
        return [
            {"id": "research", "slug": slug, "title": "Снять карту мастера",
             "detail": "какие сервисы, где чей файл, какой формы — пишется в ground/spec-map.json",
             "command": "/forge-spec research"},
            {"id": "confirm", "slug": slug, "title": "Подтвердить снятую карту",
             "detail": "после проверки; без подтверждения merge в такой мастер не пишет",
             "command": "/forge-spec research --confirm"},
        ]
    if p == "no-capability":
        out = [{"id": "capability", "slug": slug, "title": f"Слить в спеку сервиса «{c}»",
                "detail": "выбор запомнится в карте для этой дельты",
                "command": f"/forge-merge {short} --capability {c}"}
               for c in (row.get("candidates") or [])[:8]]
        out.append({"id": "map-module", "slug": slug, "title": "Сопоставить модуль кода сервису",
                    "detail": "дальше сервис дельты будет определяться по модулю сам",
                    "command": "/forge-spec research --map-module <модуль>=<сервис>"})
        return out
    if p == "no-section":
        cap = f" --capability {row['capability']}" if row.get("capability") else ""
        return [
            {"id": "ensure-sections", "slug": slug,
             "title": "Дописать раздел требований в конец мастера",
             "detail": "только заголовки в хвост файла; текст выше не меняется",
             "command": f"/forge-merge {short}{cap} --ensure-sections"},
            {"id": "set-spec", "slug": slug, "title": "Выбрать другой файл мастера сервиса",
             "detail": "если требования этого сервиса живут не в этом файле",
             "command": "/forge-spec research --set <сервис>=<путь>"},
        ]
    if p == "unknown-format":
        return [
            {"id": "research", "slug": slug, "title": "Снять профиль формы мастера",
             "detail": "без него движок не разбирает чужую спеку и не пишет в неё",
             "command": "/forge-spec research"},
        ]
    if p == "not-confirmed":
        return [
            {"id": "diff", "slug": slug, "title": "Посмотреть, что осталось несведённым",
             "detail": "мастер записан, но проверка не подтвердила требования",
             "command": f"/forge-spec diff {short}"},
            {"id": "research", "slug": slug, "title": "Снять профиль формы мастера",
             "detail": "разбор мог не увидеть только что записанное",
             "command": "/forge-spec research"},
            {"id": "archive-force", "slug": slug, "title": "Заархивировать осознанно",
             "detail": "доки уедут, хотя присутствие в мастере не подтверждено",
             "command": f"/forge-archive put {short} --force --reason '<почему>'"},
        ]
    return []


def _print_choices(r: dict) -> None:
    ch = _choices(r)
    if not ch:
        return
    print(f"     ! нужно решение — {r['problem']}")
    for i, c in enumerate(ch, 1):
        print(f"        {i}) {c['title']} ({c['detail']})")
        if c["command"]:
            print(f"           {c['command']}")
    print("        Выбор за тобой: forge сам не выбирает.")


def _print_report(rows: list, verify: bool, dry: bool) -> None:
    """Отчёт печатается ВСЕГДА, в обоих режимах и при любом исходе.

    Пользователь обязан видеть три вещи по каждой задаче: помнит ли её мастер, что стало с
    мастером и что стало с доками. Раньше гладкий прогон печатал только «✅ добавлено N» —
    и вопрос «а доехало ли требование до мастера» оставался без ответа."""
    mode = "сверка (--master-first)" if verify else "слияние"
    if dry:
        mode += ", dry-run"
    specs = {(r.get("spec"), r.get("capability")) for r in rows if r.get("spec")}
    head = f"\nОтчёт /forge-merge — {mode}"
    if len(specs) == 1:
        sp, cap = next(iter(specs))
        head += f"     мастер: {sp} [{cap}]"
    print(head)
    for r in rows:
        print(f"\n  {r['short']}")
        if len(specs) > 1 and r.get("spec"):
            print(f"     мастер-файл: {r['spec']} [{r['capability']}]")
        if r["problem"] == "error":
            print(f"     ✗ {r['error']}")
            continue
        if r["problem"] and r["problem"] != "error" and r.get("error"):
            print(f"     ✗ {r['error']}")
        if r["problem"] in _PROBLEM_WHY:
            # Куда сливать — не определено: присутствие и состояние дельты мерить не к чему.
            print(f"     мастер:      {r['master_note']}")
            print(f"     доки:        {r['docs_note']}")
            _print_choices(r)
            continue
        was, now = r["in_master_before"], r["in_master_after"]
        line = _presence(was)
        if now is not None and now != was:
            line += f" → {_presence(now)}"
        print(f"     в мастере:   {line}")
        st, after = r["delta_state_before"], r["delta_state_after"]
        arrow = f" → {after}" if after and after != st else ""
        # Поясняем ИТОГОВОЕ состояние: после стрелки пояснение к исходному читалось бы как
        # противоречие («new → merged — требований дельты в мастере нет»).
        final = after or st
        print(f"     дельта:      {st}{arrow} — {_STATE_WHY.get(final, final)}")
        print(f"     мастер:      {r['master_note']}")
        print(f"     доки:        {r['docs_note']}")
        if r["ops"] and (r["problem"] or r["master_written"] or dry):
            for op in r["ops"]:
                print(f"        {op}")
        _print_choices(r)
    tally = {k: sum(1 for r in rows if r.get("did") == k)
             for k in ("merged", "verified", "actual", "skipped")}
    todo = sum(1 for r in rows if r["problem"] and r["problem"] != "error")
    errs = sum(1 for r in rows if r["problem"] == "error")
    arch = sum(1 for r in rows if r["archived"])
    print(f"\nИтог: слито {tally['merged']}, сверено {tally['verified']}, "
          f"актуально {tally['actual']}, отменено {tally['skipped']}, "
          f"требует решения {todo}, ошибок {errs}; доки в архив — {arch}.")


def _row(slug: str, short: str) -> dict:
    return {"slug": slug, "short": short,
            "in_master_before": None, "in_master_after": None,
            "delta_state_before": None, "delta_state_after": None,
            "ops": [], "added": [], "modified": [], "blocked": [],
            "master_written": False, "archived": False, "archive_target": None,
            "archive_error": None, "problem": None, "error": None, "did": None,
            "master_note": "не тронут", "docs_note": "остались на месте",
            "spec": None, "capability": None, "candidates": []}


def cmd_merge(args) -> int:
    root = Path(args.project_root).resolve()
    prefix = _prefix(engine.spec_options(root), args.id_prefix)
    # --master-first: sdd выделяется ИЗ мастера, значит merge обязан сверять, а не дописывать.
    # Это выбор на одну операцию, поэтому он флаг команды, а не ключ политики проекта.
    verify = bool(args.master_first)
    targets = _targets(root, args)
    if targets is None:
        return 2

    prompt = sys.stderr if args.json else sys.stdout   # --json: stdout остаётся машиночитаемым
    feats = _features(root)
    rows: list = []
    rc = 0
    for slug, sdd in targets:
        row = _row(slug, _short(slug, feats))
        rows.append(row)
        # Куда сливать — решается ДО любых записей и для каждой дельты отдельно: у мастера
        # из нескольких сервисов у каждой дельты своя спека.
        w = _where(root, args, slug, sdd, prefix)
        if w.get("problem"):
            row.update(problem=w["problem"], error=w["error"],
                       candidates=w.get("candidates") or [],
                       delta_state_before="unknown-format")
            rc = 2 if w["problem"] == "error" else (rc or 3)
            continue
        spec_path, capability, g = w["spec"], w["cap"], w["g"]
        row.update(spec=str(spec_path), capability=capability)
        # Fail-closed ДО любых записей: неразобранная форма мастера — решение человека, а не
        # повод дописать чужой документ форже-блоками.
        ok_fmt, why_fmt = g.supported()
        if not ok_fmt:
            row.update(problem="unknown-format", delta_state_before="unknown-format",
                       error="формат требований-мастера не описан профилем: " + "; ".join(why_fmt))
            rc = rc or 3
            continue
        row["in_master_before"] = _in_master(_master_text(spec_path), slug, g)

        plan = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=True, g=g,
                          headings=w["headings"])
        if plan["status"] == "unsupported":
            row.update(problem="unknown-format", error=plan["error"],
                       delta_state_before="unknown-format")
            rc = rc or 3
            continue
        if plan["status"] == "no-section":
            row.update(problem="no-section", error=plan["error"], ops=plan.get("ops") or [],
                       delta_state_before="new")
            rc = rc or 3
            continue
        if plan["status"] == "error":
            row.update(problem="error", error=plan["error"])
            rc = 2
            continue
        row["ops"] = plan["ops"]
        state = _classify(plan["kinds"])
        row["delta_state_before"] = state
        sections = plan.get("sections_added") or []

        if state == "merged":
            row["did"] = "verified" if verify else "actual"
            row["master_note"] = ("не тронут (сверка прошла — дельта совпадает с мастером)"
                                  if verify else "не тронут (мастер актуален, делать нечего)")
        elif verify:
            row["problem"] = "divergence"
            row["master_note"] = "не тронут (режим сверки — мастер первичен)"
            rc = rc or 3
            continue
        elif args.dry_run:
            row["did"] = "dry-run"
            row["master_note"] = (f"будет записано операций: {len(plan['ops'])} (dry-run)"
                                  + (f"; будет дописан раздел: {', '.join(sections)}"
                                     if sections else ""))
        else:
            if not args.yes:
                print(f"{slug} → {spec_path}", file=prompt)
                for sec in sections:
                    print(f"   + раздел «{sec}» в конец файла", file=prompt)
                for op in plan["ops"]:
                    print(f"   {op}", file=prompt)
                if not _confirm("Применить?", prompt):
                    row["did"] = "skipped"
                    row["master_note"] = "не тронут (отменено пользователем)"
                    continue
            res = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=False, g=g,
                             headings=w["headings"])
            if res["status"] in ("error", "no-section"):
                row.update(problem="error", error=res["error"])
                rc = 2
                continue
            row.update(ops=res["ops"], added=res["added"], modified=res["modified"],
                       blocked=res["blocked"], master_written=True)
            row["master_note"] = (f"записано: добавлено {len(res['added'])}, "
                                  f"изменено {len(res['modified'])}"
                                  + (" (мастер создан из шаблона)" if res["created"] else "")
                                  + (f" (дописан раздел: {', '.join(res['sections_added'])})"
                                     if res.get("sections_added") else ""))
            if w["map"] is not None:
                if res.get("sections_added"):
                    SM.mark_sections(root, capability)
                SM.bind_delta(root, slug, capability)
            # Пост-проверка: мастер перечитывается и состояние ПЕРЕСЧИТЫВАЕТСЯ. Доки уезжают
            # только на подтверждённом merged — иначе требование ушло бы с рабочего стола, так
            # и не попав в мастер, а обнаружить это было бы уже нечем.
            row["in_master_after"] = _in_master(_master_text(spec_path), slug, g)
            row["delta_state_after"] = _state_of(slug, sdd, spec_path, capability, prefix, g)
            if res["blocked"]:
                row["problem"] = "blocked-modify"
                rc = rc or 3
                continue
            if row["delta_state_after"] != "merged":
                row["problem"] = "not-confirmed"
                rc = rc or 3
                continue
            row["did"] = "merged"

        if args.no_archive:
            row["docs_note"] = "остались на месте (--no-archive)"
            continue
        a = _archive_one(root, slug, dry_run=args.dry_run)
        row.update(archived=a["ok"], archive_target=a["target"], archive_error=a["error"])
        if a["ok"]:
            row["docs_note"] = (f"{'dry-run: ' if args.dry_run else ''}"
                                f"{a['source']} → {a['target']}")
        else:
            row["docs_note"] = (f"остались на месте: {a['error']}"
                                f"  (когда будет готово: /forge-archive put {row['short']})")

    first = next((r for r in rows if r.get("spec")), None)
    if args.json:
        print(json.dumps({"mode": "master-first" if verify else "delta-first",
                          "spec": first["spec"] if first else None,
                          "capability": first["capability"] if first else None,
                          "dry_run": bool(args.dry_run), "exit": rc,
                          "features": [{k: v for k, v in r.items()
                                        if k not in ("master_note", "docs_note")} for r in rows],
                          "choices": [c for r in rows for c in _choices(r)]},
                         ensure_ascii=False, indent=2))
        return rc

    _print_report(rows, verify, bool(args.dry_run))
    for spec in dict.fromkeys(r["spec"] for r in rows if r.get("spec")):
        _remind(Path(spec))
    if not any(r.get("spec") for r in rows):
        print("   Коммит/push мастер-репо — на тебе, forge не коммитит.")
    return rc


def cmd_remove(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    if not spec_path.exists():
        print(f"✗ мастера нет: {spec_path}", file=sys.stderr)
        return 2
    g = _cap_grammar(root, prefix, capability)
    if _unsupported(g, root):
        return 3
    res = engine.remove_requirement(_master_text(spec_path), args.req_id,
                                    reason=args.reason, today=date.today().isoformat(),
                                    prefix=prefix, grammar=g)
    if res["status"] != "ok":
        print(f"✗ {res['error']}", file=sys.stderr)
        return 2
    if not args.yes and not _confirm(f"Снять {args.req_id} «{res['title']}»?"):
        print("отменено")
        return 0
    spec_path.write_text(res["text"], encoding="utf-8")
    print(f"✅ снято {res['removed']} «{res['title']}» — причина в журнале изменений")
    _remind(spec_path)
    return 0


def cmd_research(args) -> int:
    """Ресерч СТРУКТУРЫ мастера: какие сервисы, где чей файл, какой формы.

    Пишет карту `ground/spec-map.json` (черновик) — в мастер не пишет ничего. Человек её
    проверяет, правит (`--set/--ignore/--map-module`) и подтверждает (`--confirm`); merge в
    неоднозначный мастер идёт только по подтверждённой карте.

    Exit: 0 — карта подтверждена / мастер однозначен; 3 — черновик ждёт решения; 2 — ошибка.
    """
    root = Path(args.project_root).resolve()
    try:
        import analyze_spec
    except Exception as e:  # noqa: BLE001
        print(f"✗ детектор недоступен: {e}", file=sys.stderr)
        return 2

    if args.apply_research:
        raw = Path(args.apply_research)
        try:
            research = json.loads(raw.read_text(encoding="utf-8") if raw.exists()
                                  else args.apply_research)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"✗ не читается JSON ресерчера: {e}", file=sys.stderr)
            return 2
        path = analyze_spec.out_path(root)
        result = analyze_spec.load_cache(path) or analyze_spec.analyze(root)
        result["research"] = research
        result = analyze_spec._apply_research(result, research)
        analyze_spec.write(result, path)
        _drop_cache(root)
        print(analyze_spec.describe(result))
        return 0

    edits = bool(args.confirm or args.set or args.ignore or args.map_module)
    try:
        if edits:
            m = SM.load(root)
            for spec in args.set or []:
                cap, _, rel = spec.partition("=")
                if not cap or not rel:
                    raise SM.MapError(f"--set ждёт <сервис>=<путь>, получено «{spec}»")
                m = SM.set_spec(root, cap.strip(), rel.strip())
            for rel in args.ignore or []:
                m = SM.add_ignore(root, rel.strip())
            for spec in args.map_module or []:
                mod, _, cap = spec.partition("=")
                if not mod or not cap:
                    raise SM.MapError(f"--map-module ждёт <модуль>=<сервис>, получено «{spec}»")
                m = SM.map_module(root, mod.strip(), cap.strip())
            if args.confirm:
                m = SM.confirm(root)
        else:
            prev = SM.load(root)
            m = SM.build(root, prev=None if args.refresh else prev, only=args.capability)
            if prev and prev.get("status") == "confirmed" and _same_structure(prev, m) \
                    and prev.get("base") == m.get("base"):
                m["status"] = "confirmed"
                m["confirmed_at"] = prev.get("confirmed_at")
            SM.save(root, m)
            # Профиль формы для однозначного мастера (одиночный файл по настроенному пути) —
            # как раньше: им пользуются гейт состава и судья.
            try:
                analyze_spec.ensure(root, refresh=args.refresh)
            except Exception:  # noqa: BLE001
                pass
    except SM.MapError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    _drop_cache(root)

    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print(SM.describe(m, SM.map_path(root)))
    if m.get("status") == "confirmed":
        return 0
    return 0 if (m.get("layout") == "empty" or not SM.ambiguous(root)) and not edits else 3


def _same_structure(a: dict, b: dict) -> bool:
    """Карта по сути та же: те же сервисы, файлы и формы — подтверждение переживает рескан."""
    def sig(m):
        return sorted((c, e.get("spec"), e.get("form"))
                      for c, e in (m.get("capabilities") or {}).items())
    return sig(a) == sig(b)


def cmd_check(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    # Phase 0 v2 refactor: убран хардкод ground/pipeline.json — gate._cfg делает
    # двойной рид policy.json → pipeline.json через load_project_config.
    policy = gate._load_policy(None, args.policy, root)
    prefix, floor, profile = gate._load_spec_opts(None, args.id_prefix, None, root)
    foreign = SM.is_foreign(root, spec_path)
    if foreign:
        profile = "detected"          # состав чужой спеки не форжев — проверяем только требования
    v = gate.check(spec_path, policy, id_prefix=prefix, scenario_floor=floor,
                   grammar=_cap_grammar(root, prefix, capability), profile=profile,
                   foreign=foreign)
    if args.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
    else:
        print(f"Master spec check [{policy}, {v['requirements']} требований]: "
              f"{'✓ PASS' if v['status'] == 'pass' else '✗ FAIL'}")
        for e in v["errors"]:
            print(f"  ✗ {e}")
        for w in v["warnings"]:
            print(f"  · warn: {w}")
    return 0 if v["status"] == "pass" else 2


_LEGACY_REQ = re.compile(r"^\s*[-*]\s+(.*)$")


def cmd_migrate(args) -> int:
    """Плоский легаси-мастер (§Требования списком + §Сценарии списком) → блоки с ID.

    Сценарии привязываются к требованию по совпадению провенанса `[from: <feature> ...]`.
    Сценарии без пары уходят в требование-хвост, чтобы ничего не потерять молча.

    Только для форже-родной формы: переписать чужой мастер в форже-грамматику — это не
    миграция, а подмена формата проекта.
    """
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    if not spec_path.exists():
        print(f"✗ мастера нет: {spec_path}", file=sys.stderr)
        return 2
    g = _cap_grammar(root, prefix, capability)
    if not g.is_native():
        print("✗ у проекта своя форма мастера — migrate её не переписывает:", file=sys.stderr)
        print(g.describe(), file=sys.stderr)
        print("   Легаси-переход на ID имеет смысл только для форже-родного формата.",
              file=sys.stderr)
        return 3
    text = _master_text(spec_path)
    if engine.parse_master(text, prefix):
        print("Мастер уже в формате требований с ID — миграция не нужна.")
        return 0

    lines = text.splitlines()
    req_span = engine._h2_span(lines, engine._SEC_REQUIREMENTS)
    scen_span = engine._h2_span(lines, engine._SEC_SCENARIOS_LEGACY)
    if req_span is None:
        print("✗ не найден раздел требований — нечего мигрировать", file=sys.stderr)
        return 2

    def _items(span):
        if span is None:
            return []
        return [l for l in lines[span[0] + 1:span[1]]
                if _LEGACY_REQ.match(l) and l.strip() not in ("-", "*")]

    reqs = [l for l in _items(req_span) if not engine._GWT.search(l)]
    scens = [l for l in _items(scen_span) if engine._GWT.search(l)]

    def _feat(s: str) -> str:
        m = re.search(r"\[from:\s*([^\s\]]+)", s)
        return m.group(1) if m else ""

    blocks, used = [], set()
    for n, r in enumerate(reqs, 1):
        title = engine._FROM_TAG.sub("", _LEGACY_REQ.match(r).group(1)).strip(" .")
        tags = engine._tags(r)
        mine = [s for s in scens if _feat(s) and _feat(s) == _feat(r)]
        used.update(id(s) for s in mine)
        blocks += [""] + engine.render_requirement(
            f"{prefix}-{n:04d}", title, title, [s.strip() for s in mine], tags)
    orphans = [s for s in scens if id(s) not in used]
    if orphans:
        blocks += [""] + engine.render_requirement(
            f"{prefix}-{len(reqs) + 1:04d}", "Требуется уточнение (перенесённые сценарии)",
            "Сценарии легаси-мастера без привязки к требованию — разнести по требованиям вручную.",
            [s.strip() for s in orphans], [])

    head = lines[req_span[0]]
    if "и сценарии" not in head.lower():
        head = re.sub(r"^(#{2}\s*\d*\.?\s*).*$", r"\1Требования и сценарии (Requirements)", head)
    new_section = [head, "Накопленный перечень требований капабилити: одно требование —"
                         " один подзаголовок с ID и вложенными сценариями."] + blocks + [""]

    first, second = sorted([req_span, scen_span or req_span], key=lambda s: s[0])
    out = lines[:first[0]] + new_section
    if scen_span is not None and scen_span != req_span:
        mid = lines[first[1]:second[0]] if first[1] < second[0] else []
        out += mid + lines[second[1]:]
    else:
        out += lines[first[1]:]

    if args.dry_run:
        print("\n".join(new_section))
        print(f"\n(dry-run: перенесено требований {len(reqs)}, сценариев "
              f"{len(scens)}, без пары {len(orphans)})")
        return 0
    spec_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✅ мигрировано: требований {len(reqs)}, сценариев {len(scens)}"
          f"{f', без пары {len(orphans)}' if orphans else ''}")
    print("   Проверь результат: /forge-spec check")
    _remind(spec_path)
    return 0


def _confirm(question: str, stream=None) -> bool:
    """Вопрос идёт в `stream` (дефолт stdout). При --json — в stderr: приглашение в stdout
    сделало бы вывод команды неразбираемым."""
    try:
        print(f"{question} [y/N] ", end="", file=stream or sys.stdout, flush=True)
        return input().strip().lower() in ("y", "yes", "д", "да")
    except EOFError:
        return False


# ── argparse ───────────────────────────────────────────────────────────

def _common_flags(p, *, sub: bool) -> None:
    """Общие флаги. На подкомандах — с SUPPRESS, чтобы не затирать значения из головы:
    работают обе формы, `spec_cli.py --project-root X status` и `... status --project-root X`."""
    d = argparse.SUPPRESS if sub else None
    p.add_argument("--project-root", default="." if not sub else d)
    p.add_argument("--capability", default=d, help="имя капабилити (дефолт docs.master.capability)")
    p.add_argument("--spec", default=d, help="явный путь к specs/<cap>/spec.md")
    p.add_argument("--id-prefix", default=d)
    p.add_argument("--json", action="store_true", default=False if not sub else d)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="spec_cli.py", description=__doc__.split("\n")[0])
    _common_flags(ap, sub=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _merge_flags(p):
        _common_flags(p, sub=True)
        p.add_argument("--allow-modify", action="store_true", help="применить все ~ (modify)")
        p.add_argument("--modify", action="append", default=[], help="применить modify только для ID")
        p.add_argument("--sdd", default=None, help="явный путь к дельте sdd.md")
        p.add_argument("--master-first", action="store_true",
                       help="мастер первичен: не дописывать его, а СВЕРИТЬ с ним дельту")
        p.add_argument("--ensure-sections", action="store_true",
                       help="дописать недостающий раздел требований/журнала в конец мастера")

    s = sub.add_parser("status", help="что в мастере и какие дельты не слиты")
    _common_flags(s, sub=True)
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("diff", help="план операций без записи")
    d.add_argument("slug", nargs="?")
    d.add_argument("--all", action="store_true", help="по всем неслитым дельтам")
    _merge_flags(d)
    d.set_defaults(func=cmd_diff)

    m = sub.add_parser("merge", help="слить дельту в мастер (--master-first — сверить с ним)")
    m.add_argument("slug", nargs="?")
    m.add_argument("--all", action="store_true", help="слить все неслитые дельты")
    m.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждения")
    m.add_argument("--dry-run", action="store_true",
                   help="показать план слияния и предстоящий перенос доков, ничего не записать")
    m.add_argument("--no-archive", action="store_true",
                   help="не убирать доки сведённой фичи в <docs_base>/archive/")
    _merge_flags(m)
    m.set_defaults(func=cmd_merge)

    r = sub.add_parser("remove", help="снять требование")
    r.add_argument("req_id")
    r.add_argument("--reason", required=True)
    r.add_argument("--yes", "-y", action="store_true")
    _common_flags(r, sub=True)
    r.set_defaults(func=cmd_remove)

    c = sub.add_parser("check", help="гейт состава мастера")
    c.add_argument("--policy", choices=("hard", "applicability", "soft"), default=None)
    _common_flags(c, sub=True)
    c.set_defaults(func=cmd_check)

    g = sub.add_parser("migrate", help="плоский легаси-мастер → требования с ID")
    g.add_argument("--dry-run", action="store_true")
    _common_flags(g, sub=True)
    g.set_defaults(func=cmd_migrate)

    rs = sub.add_parser("research", help="структура мастера: карта сервисов и их спек")
    rs.add_argument("--refresh", action="store_true",
                    help="пересканировать с нуля (ручные правки карты сбрасываются)")
    rs.add_argument("--confirm", action="store_true", help="подтвердить карту: merge пойдёт по ней")
    rs.add_argument("--set", action="append", default=[], metavar="СЕРВИС=ПУТЬ",
                    help="выбрать файл мастера сервиса (путь относительно базы мастера)")
    rs.add_argument("--ignore", action="append", default=[], metavar="ПУТЬ",
                    help="исключить файл/каталог базы из карты")
    rs.add_argument("--map-module", action="append", default=[], metavar="МОДУЛЬ=СЕРВИС",
                    help="модуль кода → сервис (по нему определяется сервис дельты)")
    rs.add_argument("--apply-research", default=None,
                    help="JSON субагента-ресерчера: вмержить в детект")
    _common_flags(rs, sub=True)
    rs.set_defaults(func=cmd_research)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
