#!/usr/bin/env python3
"""spec_cli.py — работа с требованиями-мастером (`specs/<cap>/spec.md`) ПО ЗАПРОСУ.

Мастер обновляет пользователь командой `/forge-spec`, а не пайплайн: фаза 06 в него не пишет,
она лишь сообщает о расхождении. Здесь сидит весь разбор аргументов, чтобы слэш-команда была
тонкой обёрткой и не зависела от того, как модель поймёт подкоманду.

Подкоманды:
  status                     что в мастере + какие дельты фич ещё не слиты
  diff <slug>                план операций (+ / ~ / =), ничего не пишет
  merge <slug> [--all]       слить дельту в мастер — либо СВЕРИТЬ её с ним (master-first)
  remove <ID> --reason R     снять требование с указанием причины
  check                      гейт состава мастера (check_master_spec)
  migrate                    перенести плоский легаси-мастер (§Требования + §Сценарии) на ID

Два потока, ключ `spec.master_source` в ground/policy.json:
  delta-first (дефолт)  мастер собирается ИЗ дельт: merge дописывает в него требования фичи.
  master-first          мастер пишет аналитик ДО sdd.md, и дельта выделяется из мастера.
                        Тогда merge в мастер не пишет, а СВЕРЯЕТ: план слияния обязан быть
                        пустым, любая операция (+/~) — расхождение (exit 3). Явное исключение —
                        `--allow-merge`, когда требование действительно введено дельтой.

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


def _skill_paths():
    import skill_paths
    return skill_paths


def _resolve(args) -> "tuple[Path, Path, str, dict]":
    """(project_root, spec_path, capability, spec_opts)."""
    root = Path(args.project_root).resolve()
    spec_path, capability = engine.resolve_spec(root, args.capability, args.spec)
    return root, spec_path, capability, engine.spec_options(root)


def _prefix(opts: dict, cli: "str | None") -> str:
    return cli or opts.get("id_prefix") or engine.DEFAULT_ID_PREFIX


_GRAMMAR_CACHE: dict = {}


def grammar(root: Path, prefix: "str | None" = None) -> "SG.Grammar":
    """Профиль формы мастера для этого проекта (NATIVE ← детект ← policy.json).

    Перед первым обращением снимается ресерч формата (`analyze_spec --if-missing`): без него
    проект со своей спекой разбирался бы форже-грамматикой — то есть никак. Скан идемпотентен
    и по отпечатку мастера, поэтому дешёвый; его отказ не должен ронять команду, поэтому
    best-effort.
    """
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

MASTER_SOURCES = ("delta-first", "master-first")
DEFAULT_MASTER_SOURCE = "delta-first"


def master_source(root: Path, opts: "dict | None" = None) -> str:
    """`spec.master_source`: кто первичен — дельта или мастер. Неизвестное значение → дефолт."""
    try:
        o = opts if opts is not None else engine.spec_options(Path(root))
    except Exception:  # noqa: BLE001 — конфиг не поднялся: ведём себя как раньше
        return DEFAULT_MASTER_SOURCE
    v = (o or {}).get("master_source")
    return v if v in MASTER_SOURCES else DEFAULT_MASTER_SOURCE


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
    try:
        spec_path, capability = engine.resolve_spec(root, None, None)
    except Exception:  # noqa: BLE001 — мастер не резолвится: гейт не давим
        return "no-master"
    prefix = _prefix(engine.spec_options(root), None)
    g = grammar(root, prefix)
    for s, sdd in _features(root):
        if s == slug:
            return _state_of(s, sdd, spec_path, capability, prefix, g)
    return "no-delta"


def _archive_merged(root: Path, slugs: list, dry_run: bool = False) -> None:
    """Убрать доки сведённых/сверенных фич в архив. Best-effort: отказ гейта архивации —
    не провал слияния (типичный отказ — сверку запустили посреди незавершённого прогона)."""
    if not slugs:
        return
    _ps = str(SCRIPT_DIR.parents[1] / "pipeline-state" / "scripts")
    if _ps not in sys.path:
        sys.path.insert(0, _ps)
    try:
        import archive
    except Exception as e:  # noqa: BLE001
        print(f"\n! архивация недоступна ({e}) — доки остались на месте")
        return
    print("")
    for slug in slugs:
        try:
            res = archive.archive_feature(root, slug, dry_run=dry_run, delta_checked=True)
            if dry_run:
                print(f"   dry-run архива: {res['source']} → {res['target']}")
            else:
                print(f"   🗄  доки {slug} → {res['target']}")
        except archive.Fail as e:
            print(f"   · доки {slug} не заархивированы: {e}")
            print(f"     когда будет готово: /forge-archive put {slug}")
        except SystemExit:
            print(f"   · доки {slug} не заархивированы: манифест прогона нечитаем")


def cmd_status(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    text = _master_text(spec_path)
    prefix = _prefix(opts, args.id_prefix)
    g = grammar(root, prefix)
    reqs = engine.parse_master(text, prefix, g) if text else []
    scen = sum(len(r["scenarios"]) for r in reqs)

    # «Слито» определяем ПЛАНОМ, а не подстрокой провенанса: отредактированная после merge
    # дельта провенанс сохраняет, но мастер уже расходится с ней.
    state: dict[str, list[str]] = {"new": [], "drifted": [], "merged": [], "unknown-format": []}
    for slug, sdd in _features(root):
        state[_state_of(slug, sdd, spec_path, capability, prefix, g)].append(slug)
    total = sum(len(v) for v in state.values())
    mode = master_source(root, opts)

    if args.json:
        print(json.dumps({"spec": str(spec_path), "exists": spec_path.exists(),
                          "capability": capability, "requirements": len(reqs),
                          "scenarios": scen, "features": total, "master_source": mode,
                          "grammar_native": g.is_native(),
                          "grammar_supported": g.supported()[0],
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
    verify = mode == "master-first"
    if verify:
        print("   режим: мастер первичен — дельты сверяются с ним, а не дописывают его")
    if state["new"]:
        label = "РАСХОЖДЕНИЕ, нет в мастере" if verify else "НЕ слито"
        print(f"   {label} ({len(state['new'])} из {total}): {', '.join(state['new'])}")
    if state["drifted"]:
        label = "РАСХОЖДЕНИЕ, дельта разошлась с мастером" if verify \
            else "РАЗОШЛОСЬ после слияния"
        print(f"   {label} ({len(state['drifted'])}): {', '.join(state['drifted'])}")
    if state["merged"]:
        label = "сверено" if verify else "актуально"
        print(f"   {label} ({len(state['merged'])}): {', '.join(state['merged'])}")
    if state["unknown-format"]:
        print(f"   ФОРМАТ МАСТЕРА НЕ РАЗОБРАН ({len(state['unknown-format'])}): "
              f"{', '.join(state['unknown-format'])}")
        print("   → /forge-spec research — снять профиль формата и применить его")
    todo = state["new"] + state["drifted"]
    if todo:
        feats = _features(root)
        print(f"   → /forge-merge {_short(todo[0], feats)}   (или /forge-merge --all)")
    return 0


def _run_merge(args, slug: str, sdd: Path, spec_path: Path, capability: str,
               prefix: str, dry: bool, g: "SG.Grammar | None" = None) -> dict:
    return engine.merge(sdd, spec_path, engine.default_template(), _provenance(slug), capability,
                        prefix=prefix, dry_run=dry, allow_modify=args.allow_modify,
                        modify_ids=set(args.modify or []), grammar=g)


def cmd_diff(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    g = grammar(root, prefix)
    if _unsupported(g, root):
        return 3
    targets = _targets(root, args)
    if targets is None:
        return 2
    rc = 0
    for slug, sdd in targets:
        res = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=True, g=g)
        if res["status"] == "unsupported":
            print(f"✗ {slug}: {res['error']}")
            rc = 3
            continue
        if res["status"] == "error":
            print(f"✗ {slug}: {res['error']}")
            rc = 2
            continue
        print(f"{slug} → {spec_path.name}"
              f"{' (мастер будет создан из шаблона)' if res['created'] else ''}")
        for line in res["ops"] or ["   (в дельте нет требований)"]:
            print(f"   {line}")
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


def cmd_merge(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    g = grammar(root, prefix)
    # Fail-closed ДО любых записей: неразобранная форма мастера — решение человека, а не
    # повод дописать чужой документ форже-блоками.
    if _unsupported(g, root):
        return 3
    # master-first: sdd выделяется ИЗ мастера, значит merge обязан сверять, а не дописывать.
    # --allow-merge — явное исключение для требования, действительно введённого дельтой.
    verify_only = master_source(root, opts) == "master-first" and not args.allow_merge
    targets = _targets(root, args)
    if targets is None:
        return 2

    skipped: list[str] = []
    settled: list[str] = []          # сведено или сверено → доки можно убирать в архив
    rc = 0
    for slug, sdd in targets:
        plan = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=True, g=g)
        if plan["status"] == "unsupported":
            print(f"✗ {slug}: {plan['error']}")
            print("   Разбор формата спеки проекта: /forge-spec research")
            rc = 3
            continue
        if plan["status"] == "error":
            print(f"✗ {slug}: {plan['error']}")
            rc = 2
            continue
        if _classify(plan["kinds"]) == "merged":
            print(f"= {slug}: " + ("сверка прошла — дельта совпадает с мастером" if verify_only
                                   else "мастер актуален, делать нечего"))
            settled.append(slug)
            continue
        if verify_only:
            print(f"✗ {slug}: дельта расходится с мастером ({spec_path.name})")
            for line in plan["ops"]:
                print(f"   {line}")
            print("   Мастер первичен (spec.master_source=master-first): дельта выделяется ИЗ "
                  "него, поэтому forge его не дописывает.")
            print(f"   Приведи дельту к мастеру — либо, если требование действительно новое: "
                  f"/forge-merge {_short(slug, _features(root))} --allow-merge")
            rc = rc or 3
            continue
        if args.dry_run:
            print(f"{slug} → {spec_path} (dry-run, ничего не записано)")
            for line in plan["ops"]:
                print(f"   {line}")
            settled.append(slug)
            continue
        if not args.yes:
            print(f"{slug} → {spec_path}")
            for line in plan["ops"]:
                print(f"   {line}")
            if not _confirm("Применить?"):
                skipped.append(slug)
                continue

        res = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=False, g=g)
        if res["status"] == "error":
            print(f"✗ {slug}: {res['error']}")
            rc = 2
            continue
        mark = "✅" if (res["added"] or res["modified"]) else "·"
        print(f"{mark} {slug}: добавлено {len(res['added'])}, изменено {len(res['modified'])}"
              f"{' (мастер создан из шаблона)' if res['created'] else ''}")
        for line in res["ops"]:
            print(f"   {line}")
        if not res["blocked"]:
            settled.append(slug)
        if res["blocked"]:
            # --all не валится целиком: конфликтную дельту пропускаем и перечисляем в конце
            skipped.append(f"{_short(slug, _features(root))} "
                           f"(modify: {', '.join(res['blocked'])})")
            rc = rc or 3

    if skipped:
        print(f"\n! пропущено: {'; '.join(skipped)}")
        print("  modify применяется явно: /forge-merge <слаг> --allow-modify (все) "
              "или --modify <ID> (точечно)")
    _remind(spec_path)
    if not args.no_archive:
        _archive_merged(root, settled, dry_run=args.dry_run)
    return rc


def cmd_remove(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    if not spec_path.exists():
        print(f"✗ мастера нет: {spec_path}", file=sys.stderr)
        return 2
    g = grammar(root, prefix)
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
    """Ресерч формата: что за спека у проекта, насколько уверен детект, что применить.

    Читает, не пишет в мастер: результат — `ground/inventory/spec-conventions.json` плюс
    готовые команды `config.py set spec.grammar.…`, которые применяет человек.
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
    else:
        result = analyze_spec.ensure(root, refresh=args.refresh)
    _GRAMMAR_CACHE.pop(str(root), None)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(analyze_spec.describe(result))
    print(f"\nПрофиль: {analyze_spec.out_path(root)}")
    g = grammar(root)
    ok, why = g.supported()
    if not ok:
        print("\n✗ работать с мастером нельзя, пока форма не описана: " + "; ".join(why))
        return 3
    if "no_master" in result.get("warnings", []):
        return 0
    if not result.get("matches_native") and not g.layers:
        print("\n! профиль ещё не подтверждён в policy.json — merge/check идут по детекту; "
              "закрепи его командами выше, чтобы прогон видел те же правила")
    return 0 if float(result.get("confidence") or 0) >= analyze_spec.CONFIDENCE_FLOOR else 3


def cmd_check(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    # Phase 0 v2 refactor: убран хардкод ground/pipeline.json — gate._cfg делает
    # двойной рид policy.json → pipeline.json через load_project_config.
    policy = gate._load_policy(None, args.policy, root)
    prefix, floor, profile = gate._load_spec_opts(None, args.id_prefix, None, root)
    v = gate.check(spec_path, policy, id_prefix=prefix, scenario_floor=floor,
                   grammar=grammar(root, prefix), profile=profile)
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
    g = grammar(root, prefix)
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


def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes", "д", "да")
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

    s = sub.add_parser("status", help="что в мастере и какие дельты не слиты")
    _common_flags(s, sub=True)
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("diff", help="план операций без записи")
    d.add_argument("slug", nargs="?")
    d.add_argument("--all", action="store_true", help="по всем неслитым дельтам")
    _merge_flags(d)
    d.set_defaults(func=cmd_diff)

    m = sub.add_parser("merge", help="слить дельту в мастер (master-first — сверить с ним)")
    m.add_argument("slug", nargs="?")
    m.add_argument("--all", action="store_true", help="слить все неслитые дельты")
    m.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждения")
    m.add_argument("--dry-run", action="store_true",
                   help="показать план слияния и предстоящий перенос доков, ничего не записать")
    m.add_argument("--allow-merge", action="store_true",
                   help="master-first: всё-таки дописать мастер (требование введено дельтой)")
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

    rs = sub.add_parser("research", help="как устроена спека проекта (профиль формы мастера)")
    rs.add_argument("--refresh", action="store_true", help="пересканировать мастер")
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
