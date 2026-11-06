#!/usr/bin/env python3
"""graph_gate.py — гейт шагов ветки specgraph: `graph_gate.py map|link|check --project <repo>`.

Шаг закрывается только этим гейтом (record_gate с командой из risk-policy.gate_cmd_expect), а не
словами модели «готово».

map (задача 1.7): очередь draft-file закрыта; каталог утверждён — согласие graph-catalog-<KEY> привязано
к снимку catalog.approved.json; очередь assign-section построена из него и закрыта; нарезка сделана по
снимку (заметка на каждый узел); полнота и дословность — 0 нарушений (E-coverage, E-verbatim);
frontmatter всех заметок читается. Брошенные единицы очередей перечисляются в ответе.
link (задача 2.6): очереди link-note и card построены и закрыты; висящих предложений (proposed) нет; по каждому
узлу дельты есть решение; синк чистый (сухой повтор — 0 изменений); отчёт связей свежий (пересборка даёт тот же
текст); согласие graph-edges-<KEY> привязано к хешу _graph/edges-review.md.
check (задача 3.5): валидатор — 0 ошибок E-*; отчёт _graph/report.md и вопросы свежие (пересчёт без записи даёт тот
же текст); Obsidian-слой свежий (сухая сборка — 0 изменений); смоук обхода без проблем; согласие graph-accept-<KEY>
привязано к хешу отчёта.

Коды: 0 — гейт пройден, 1 — проверки не прошли, 2 — неверный вызов.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STEPS = {"map": "graph-map", "link": "graph-link", "check": "graph-check"}
_OK = {"map": "каталог утверждён, нарезка полная и дословная",
       "link": "связи решены, синк чистый, отчёт связей утверждён",
       "check": "граф без ошибок, отчёт и Obsidian-слой свежие, обход работает, граф принят"}


def check_map(root: Path) -> Tuple[List[str], List[str]]:
    """(нарушения, заметки): нарушений нет — шаг можно закрывать."""
    from sgraph import catalog as CT
    from sgraph import config as C
    from sgraph import fm as F
    from sgraph import ontology as O
    from sgraph import queue as Q
    from sgraph import split as SP
    bad, notes = [], []
    cfg, errors = C.load(root)
    if cfg is None:
        return [f"конфиг: {'; '.join(errors) or 'нет .spec-graph/config.json — graph_init'}"], notes
    for name in ("draft-file", "assign-section"):
        if not (root / Q.QUEUE_REL / f"{name}.json").is_file():
            bad.append(f"очередь {name} не построена")
            continue
        closed, abandoned = Q.closed(root, name)
        if not closed:
            st = Q.status(root, name)
            bad.append(f"очередь {name} не закрыта: pending {st['pending']}, в аренде {st['leased']}")
        if abandoned:
            notes.append(f"очередь {name}: брошено единиц {len(abandoned)} — в отчёт человеку")
    ok, why = CT.approved(root)
    if not ok:
        bad.append(why)
        return bad, notes
    ont = O.load(root)
    vault = root / cfg["vault"]
    approved = CT.load_approved(root)
    missing = [n["name"] for n in approved["nodes"] if n.get("in_scope", True)
               and not (vault / ont.path_for(n["kind"], n["name"], n.get("service"), n.get("subkind"))).is_file()]
    if missing:
        bad.append(f"нарезка не сделана по снимку: нет заметок {len(missing)} (например «{missing[0]}») — graph_split")
    for code, msg in SP.verify(root, cfg)[:10]:
        bad.append(f"[{code}] {msg}")
    for p in sorted(vault.rglob("*.md")):
        if "_graph" in p.relative_to(vault).parts:
            continue
        try:
            F.read(SP.read_note(p))
        except F.FMError as e:
            bad.append(f"{p.relative_to(vault)}: frontmatter {e}")
    delta = json.loads((root / ".spec-graph/catalog.delta.json").read_text(encoding="utf-8")) \
        if (root / ".spec-graph/catalog.delta.json").is_file() else []
    if delta:
        notes.append(f"узлов, добавленных раскладкой: {len(delta)} — решаются в отчёте связей (graph-link)")
    return bad, notes


def check_link(root: Path) -> Tuple[List[str], List[str]]:
    from sgraph import config as C
    from sgraph import consent as CS
    from sgraph import queue as Q
    from sgraph import review as RV
    from sgraph import sync as SY
    bad, notes = [], []
    cfg, errors = C.load(root)
    if cfg is None:
        return [f"конфиг: {'; '.join(errors) or 'нет .spec-graph/config.json — graph_init'}"], notes
    for name in ("link-note", "card"):
        if not (root / Q.QUEUE_REL / f"{name}.json").is_file():
            bad.append(f"очередь {name} не построена")
            continue
        closed, abandoned = Q.closed(root, name)
        if not closed:
            st = Q.status(root, name)
            bad.append(f"очередь {name} не закрыта: pending {st['pending']}, в аренде {st['leased']}")
        if abandoned:
            notes.append(f"очередь {name}: брошено единиц {len(abandoned)} — они в отчёте связей (раздел 4)")
    left = RV.undecided_delta(root)
    if left:
        bad.append(f"узлы дельты без решения: {len(left)} (например «{left[0]}») — graph_delta_decide")
    dry = SY.sync(root, cfg, dry=True)
    if dry["written"] or dry["removed_by_human"] or dry["journal_changed"]:
        bad.append(f"синк не чистый: изменится заметок {dry['written']} — graph_sync, затем graph_edges_review")
    p = RV.review_path(root, cfg)
    if not p.is_file():
        bad.append("нет отчёта связей — graph_edges_review")
        return bad, notes
    if RV.report(root, cfg)[0] != p.read_text(encoding="utf-8"):
        bad.append("отчёт связей устарел — graph_edges_review и снова вопрос пользователю")
    st = CS.state(root, RV.CONSENT, p)
    if st["state"] != "ok":
        bad.append(f"согласие {st['key']}: {st['state']} — покажи отчёт, спроси пользователя; «да» — {st.get('how', '')}")
    elif RV.pending(root):
        bad.append(f"висящих предложений {RV.pending(root)} — согласие есть: снова graph_edges_review их примет")
    return bad, notes


def check_check(root: Path) -> Tuple[List[str], List[str]]:
    from sgraph import accept as AC
    from sgraph import completeness as CM
    from sgraph import config as C
    from sgraph import consent as CS
    from sgraph import obsidian as OB
    from sgraph import validate as VA
    bad, notes = [], []
    cfg, errors = C.load(root)
    if cfg is None:
        return [f"конфиг: {'; '.join(errors) or 'нет .spec-graph/config.json — graph_init'}"], notes
    res = VA.validate(root, cfg, write=False)
    if res["errors"]:
        bad.append(f"ошибок валидатора: {res['errors']} — " + "; ".join(res["top"][:3]))
    report = VA.report_path(root, cfg)
    if not report.is_file() or report.read_text(encoding="utf-8") != res["report_text"]:
        bad.append("отчёт _graph/report.md устарел или не записан — graph_check")
    qp = CM.questions_path(root, cfg)
    if not qp.is_file() or qp.read_text(encoding="utf-8") != res["questions_text"]:
        bad.append("_graph/open-questions.md устарел — graph_check")
    try:
        stale = OB.build(root, cfg, dry=True)["changed"]
    except Exception as e:  # noqa: BLE001 — слой не собирается: это нарушение, а не падение гейта
        stale = [f"слой не собирается: {e}"]
    if stale:
        bad.append(f"Obsidian-слой устарел ({', '.join(stale[:3])}) — graph_check")
    for p in AC.smoke(root, cfg)[:5]:
        bad.append(f"смоук обхода: {p}")
    partial = [t["file"] for t in res["transfer"] if not t["full"]]
    if partial:
        notes.append(f"перенесены не полностью: {', '.join(partial[:5])} — их исходники удалять рано")
    st = CS.state(root, AC.CONSENT, report)
    if st["state"] != "ok":
        bad.append(f"согласие {st['key']}: {st['state']} — покажи сводку отчёта, спроси о приёмке; «да» — {st.get('how', '')}")
    return bad, notes


def _diag(root: Path) -> str:
    """Остановка гейта — свежий пакет диагностики (4.3): у пользователя всегда есть что вынести."""
    try:
        from sgraph import config as C
        from sgraph import diag as DG
        cfg, _ = C.load(root)
        if cfg is None:
            return "нет — проект не инициализирован"
        return DG.build(root, cfg)["file"]
    except Exception as e:  # noqa: BLE001 — пакет не важнее ответа гейта
        return f"не записан ({getattr(e, 'code', type(e).__name__)})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейт шага ветки specgraph.")
    ap.add_argument("step", choices=sorted(STEPS))
    ap.add_argument("--project", default=".", help="корень репо спеки")
    args = ap.parse_args(argv)
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"✗ нет каталога проекта: {args.project}", file=sys.stderr)
        return 2
    bad, notes = {"map": check_map, "link": check_link, "check": check_check}[args.step](root)
    for n in notes:
        print(f"• {n}")
    if bad:
        print(f"✗ {STEPS[args.step]}: шаг не закрывается:")
        for b in bad[:15]:
            print(f"  - {b}")
        print(f"• пакет диагностики: {_diag(root)}")
        return 1
    print(f"✓ {STEPS[args.step]}: {_OK[args.step]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
