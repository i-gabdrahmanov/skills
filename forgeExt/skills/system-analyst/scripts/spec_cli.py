#!/usr/bin/env python3
"""spec_cli.py — работа с требованиями-мастером (`specs/<cap>/spec.md`) ПО ЗАПРОСУ.

Мастер обновляет пользователь командой `/forge-spec`, а не пайплайн: фаза 06 в него не пишет,
она лишь сообщает о расхождении. Здесь сидит весь разбор аргументов, чтобы слэш-команда была
тонкой обёрткой и не зависела от того, как модель поймёт подкоманду.

Подкоманды:
  status                     что в мастере + какие дельты фич ещё не слиты
  diff <slug>                план операций (+ / ~ / =), ничего не пишет
  merge <slug> [--all]       слить дельту (или все неслитые) в мастер
  remove <ID> --reason R     снять требование с указанием причины
  check                      гейт состава мастера (check_master_spec)
  migrate                    перенести плоский легаси-мастер (§Требования + §Сценарии) на ID

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

def _classify(kinds: list[str]) -> str:
    """Состояние дельты относительно мастера по плану операций."""
    if any(k == "add" for k in kinds):
        return "new"        # требований дельты в мастере нет
    if any(k == "modify" for k in kinds):
        return "drifted"    # дельта изменилась после слияния
    return "merged"


def cmd_status(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    text = _master_text(spec_path)
    prefix = _prefix(opts, args.id_prefix)
    reqs = engine.parse_master(text, prefix) if text else []
    scen = sum(len(r["scenarios"]) for r in reqs)

    # «Слито» определяем ПЛАНОМ, а не подстрокой провенанса: отредактированная после merge
    # дельта провенанс сохраняет, но мастер уже расходится с ней.
    state: dict[str, list[str]] = {"new": [], "drifted": [], "merged": []}
    for slug, sdd in _features(root):
        plan = engine.merge(sdd, spec_path, engine.default_template(), slug, capability,
                            prefix=prefix, dry_run=True)
        state["merged" if plan["status"] == "error"
              else _classify(plan.get("kinds", []))].append(slug)
    total = sum(len(v) for v in state.values())

    if args.json:
        print(json.dumps({"spec": str(spec_path), "exists": spec_path.exists(),
                          "capability": capability, "requirements": len(reqs),
                          "scenarios": scen, "features": total,
                          "new": state["new"], "drifted": state["drifted"],
                          "merged": state["merged"]}, ensure_ascii=False, indent=2))
        return 0

    print(f"Требования-мастер [{capability}]: {spec_path}")
    if not spec_path.exists():
        print("   мастера ещё нет — создастся из шаблона при первом merge")
    else:
        print(f"   требований: {len(reqs)}, сценариев: {scen}")
    if not total:
        print("   дельт фич не найдено")
        return 0
    if state["new"]:
        print(f"   НЕ слито ({len(state['new'])} из {total}): {', '.join(state['new'])}")
    if state["drifted"]:
        print(f"   РАЗОШЛОСЬ после слияния ({len(state['drifted'])}): {', '.join(state['drifted'])}")
    if state["merged"]:
        print(f"   актуально ({len(state['merged'])}): {', '.join(state['merged'])}")
    todo = state["new"] + state["drifted"]
    if todo:
        feats = _features(root)
        print(f"   → /forge-merge {_short(todo[0], feats)}   (или /forge-merge --all)")
    return 0


def _run_merge(args, slug: str, sdd: Path, spec_path: Path, capability: str,
               prefix: str, dry: bool) -> dict:
    return engine.merge(sdd, spec_path, engine.default_template(), _provenance(slug), capability,
                        prefix=prefix, dry_run=dry, allow_modify=args.allow_modify,
                        modify_ids=set(args.modify or []))


def cmd_diff(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    targets = _targets(root, args)
    if targets is None:
        return 2
    rc = 0
    for slug, sdd in targets:
        res = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=True)
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
    targets = _targets(root, args)
    if targets is None:
        return 2

    skipped: list[str] = []
    rc = 0
    for slug, sdd in targets:
        plan = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=True)
        if plan["status"] == "error":
            print(f"✗ {slug}: {plan['error']}")
            rc = 2
            continue
        if _classify(plan["kinds"]) == "merged":
            print(f"= {slug}: мастер актуален, делать нечего")
            continue
        if not args.yes:
            print(f"{slug} → {spec_path}")
            for line in plan["ops"]:
                print(f"   {line}")
            if not _confirm("Применить?"):
                skipped.append(slug)
                continue

        res = _run_merge(args, slug, sdd, spec_path, capability, prefix, dry=False)
        if res["status"] == "error":
            print(f"✗ {slug}: {res['error']}")
            rc = 2
            continue
        mark = "✅" if (res["added"] or res["modified"]) else "·"
        print(f"{mark} {slug}: добавлено {len(res['added'])}, изменено {len(res['modified'])}"
              f"{' (мастер создан из шаблона)' if res['created'] else ''}")
        for line in res["ops"]:
            print(f"   {line}")
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
    return rc


def cmd_remove(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    if not spec_path.exists():
        print(f"✗ мастера нет: {spec_path}", file=sys.stderr)
        return 2
    res = engine.remove_requirement(_master_text(spec_path), args.req_id,
                                    reason=args.reason, today=date.today().isoformat(),
                                    prefix=prefix)
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


def cmd_check(args) -> int:
    root, spec_path, capability, opts = _resolve(args)
    pcfg = root / "ground" / "pipeline.json"
    policy = gate._load_policy(pcfg, args.policy)
    prefix, floor = gate._load_spec_opts(pcfg, args.id_prefix, None)
    v = gate.check(spec_path, policy, id_prefix=prefix, scenario_floor=floor)
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
    """
    root, spec_path, capability, opts = _resolve(args)
    prefix = _prefix(opts, args.id_prefix)
    if not spec_path.exists():
        print(f"✗ мастера нет: {spec_path}", file=sys.stderr)
        return 2
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

    m = sub.add_parser("merge", help="слить дельту в мастер")
    m.add_argument("slug", nargs="?")
    m.add_argument("--all", action="store_true", help="слить все неслитые дельты")
    m.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждения")
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
