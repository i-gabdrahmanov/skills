"""accept.py — шаг graph-check: проверка, смоук обхода и приёмка графа человеком (задача 3.5, D13).

Последнее решение человека — принять граф; после приёмки пользователь сам решает, удалять ли плоские спеки
(пайплайн их не удаляет). Поэтому шаг честно отвечает, какой исходник перенесён полностью (раздел «Перенос
исходников» отчёта валидатора).

`run` — что зовёт оркестратор: валидатор (с полнотой и переносом), Obsidian-слой, смоук обхода; сводка для
пользователя и состояние согласия `graph-accept-<KEY>`, привязанного к хешу `_graph/report.md`. Каждый
прогон пишет снимок здоровья (числа и хеши находок, 4.1) и свежий пакет диагностики (4.3) — у пользователя
всегда есть что вынести.
`smoke` — без модели: по каждой бизнес-операции `graph_node` и `graph_impact` отвечают без ошибок, путь от
операции до сервиса каждого её соседа существует, и существует каждый переход `walks` из конфига.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import completeness as CM
from . import consent as CS
from . import diag as DG
from . import metrics as M
from . import obsidian as OB
from . import traverse as TR
from . import validate as VA

CONSENT = "graph-accept"


def smoke(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """Проблемы смоука обхода (пусто — граф обходится)."""
    idx = TR.build_index(root, cfg, use_cache=False)
    group = CM.load()["group_by"]
    bad = []
    for name in sorted(n for n, d in idx.nodes.items() if d["kind"] == group):
        try:
            TR.node(idx, root, cfg, name, "card")
            TR.impact(idx, name)
        except Exception as e:  # noqa: BLE001 — смоук собирает любые отказы, а не падает на первом
            bad.append(f"{name}: обход падает ({type(e).__name__}: {str(e)[:120]})")
            continue
        services = sorted({idx.nodes[o]["service"] for _, _, o, _ in idx.steps(name)
                           if idx.nodes[o].get("service") in idx.nodes})
        for svc in services:
            if not TR.path(idx, name, svc)["paths"]:
                bad.append(f"{name}: нет пути до сервиса {svc}")
    for w in cfg.get("walks") or []:
        try:
            if not TR.path(idx, w["from"], w["to"])["paths"]:
                bad.append(f"переход «{w['from']}» → «{w['to']}» из конфига: пути нет")
        except TR.TraverseError as e:
            bad.append(f"переход «{w['from']}» → «{w['to']}» из конфига: {e}")
    return bad


def run(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    res = VA.validate(root, cfg)
    layer = OB.build(root, cfg)
    problems = smoke(root, cfg)
    record_health(root, res, problems)
    p = VA.report_path(root, cfg)
    st = CS.state(root, CONSENT, p)
    rel = p.relative_to(Path(root)).as_posix()
    holes = {k: v for k, v in res["counts"].items() if k in {r["id"] for r in CM.load()["rules"]}}
    out = {"errors": res["errors"], "warnings": res["warnings"], "counts": res["counts"], "holes": holes,
           "transfer": res["transfer"], "smoke": problems, "obsidian": layer["mode"], "report": rel,
           "consent": st["state"], "key": st["key"]}
    if res["errors"] or problems:
        out["next"] = (f"граф не готов к приёмке: ошибок {res['errors']}, проблем обхода {len(problems)}. Покажи "
                       f"пользователю {rel} и первые нарушения (top); исправлять — шагами конвейера, не руками.")
        out["top"] = res["top"]
    elif st["state"] != "ok":
        out["next"] = (f"покажи пользователю сводку {rel}: счётчики, дыры по операциям (_graph/open-questions.md), "
                       f"перенос исходников; спроси, принимает ли он граф. «Да» — {st['how']}; затем снова graph_check.")
    else:
        out["next"] = "гейт шага: graph_status → run.commands.gate, затем run.commands.close"
    out["diag"] = auto_diag(root, cfg)
    return out


def record_health(root: Path, res: Dict[str, Any], problems: List[str]) -> Dict[str, Any]:
    """Снимок здоровья в историю: только числа; находки — подписи, история хранит их хешами."""
    import json
    issues = json.loads((Path(root) / VA.RESULT_REL).read_text(encoding="utf-8")).get("issues") or []
    holes = sum(1 for i in issues if i.get("hole"))
    snap = {"errors": res["errors"], "warnings": res["warnings"], "notes": res["notes"], "holes": holes,
            "smoke": len(problems), "files_full": sum(1 for t in res["transfer"] if t["full"])}
    findings = [f"{i['code']}|{i['path']}|{i['msg']}" for i in issues] + [f"smoke|{p}" for p in problems]
    return M.record_health(root, snap, findings)


def auto_diag(root: Path, cfg: Dict[str, Any]) -> str:
    """Свежий пакет диагностики в _graph/diag/; сбой пакета не роняет шаг — он назван в ответе."""
    try:
        return DG.build(root, cfg)["file"]
    except DG.DiagError as e:
        return f"не записан: {e.code}"
    except Exception as e:  # noqa: BLE001 — диагностика не важнее шага
        return f"не записан: {type(e).__name__}"
