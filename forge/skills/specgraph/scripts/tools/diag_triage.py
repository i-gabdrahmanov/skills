#!/usr/bin/env python3
"""diag_triage.py — черновик разбора пакета диагностики (задача 4.4): `diag_triage.py <diag.md> [--prev <diag.md>]`.

Читает машинный блок пакета `/graph diag` (раздел 9) и печатает таблицу находок по классам директивы
`docs/specgraph/FEEDBACK-DIRECTIVE.md`: правило, бриф, онтология, баг, окружение, данные — с серьёзностью
P0–P3 и тем, чем находка подтверждена (числа и коды из пакета). С `--prev` — динамика против прошлого
пакета: какие коды и промахи выросли, какие ушли. Это черновик для разработчика: решение о классе и правке
остаётся за ним (директива, шаги 2–4).

Пороги — константы ниже: доля ложных срабатываний правила, отказы одного кода, доля неразмещённого.

Коды: 0 — разобрано, 2 — нет машинного блока или он не читается.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

FALSE_SHARE = 0.3          # доля ложных срабатываний правила, выше которой — находка
FALSE_MIN = 3              # и не меньше стольких срабатываний вообще
CODE_MIN = 5               # отказов одного кода за прогон — модель буксует
UNPLACED_SHARE = 0.10      # доля неразмещённых блоков — низкое качество раскладки
_GRAPH_LIES = ("E-evidence", "E-verbatim", "E-coverage")      # граф врёт или текст потерян
_STRUCTURAL = ("E-edge-kind", "E-inverse", "E-ghost", "E-fm-parse", "E-fm-key", "E-path", "E-marker", "E-index")


def load(path: Path) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r"```json\n(.*?)\n```", text, re.S)
    if not m:
        raise ValueError("нет машинного блока (раздел 9) — это пакет /graph diag?")
    return json.loads(m.group(1))


def _f(cls: str, sev: str, what: str, evidence: str) -> Dict[str, str]:
    return {"class": cls, "severity": sev, "what": what, "evidence": evidence}


def triage(data: Dict[str, Any], prev: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    h = data.get("health") or {}
    counts = h.get("counts") or {}
    for code in _GRAPH_LIES:
        if counts.get(code):
            out.append(_f("баг", "P0", f"граф врёт или текст потерян: {code}", f"{code} ×{counts[code]}"))
    for code in _STRUCTURAL:
        if counts.get(code):
            out.append(_f("баг", "P1", f"структурная ошибка графа {code}", f"{code} ×{counts[code]}"))
    for step, n in (data.get("steps") or {}).items():
        if n == -1:
            out.append(_f("баг", "P1", f"гейт {step} не отработал (исключение)", "steps." + step + " = -1"))
    for run in data.get("runs") or []:
        for err, n in run.get("engine_errors") or []:
            out.append(_f("баг", "P1", f"ошибка движка {err}", f"прогон {run['key']}: ×{n}"))
        for code, n in run.get("top_codes") or []:
            if n >= CODE_MIN and not code.startswith("E-arg"):
                out.append(_f("бриф", "P2", f"модель буксует на {code}", f"прогон {run['key']}: ×{n}"))
            elif n >= CODE_MIN:
                out.append(_f("бриф", "P2", f"модель путает аргументы инструментов ({code})", f"прогон {run['key']}: ×{n}"))
    for q, info in (data.get("queues") or {}).items():
        if info.get("abandoned"):
            codes = sorted({c for it in info["abandoned"] for c in it.get("codes") or []})
            out.append(_f("бриф", "P2", f"брошены единицы очереди {q}", f"{len(info['abandoned'])} шт., коды {', '.join(codes) or '—'}"))
    rules = data.get("rules") or {}
    for kind, m in (rules.get("misses") or {}).items():
        shapes = ", ".join(f"{s} ×{n}" for s, n in sorted((m.get("shapes") or {}).items()))
        out.append(_f("правило", "P2", f"экстрактор не видит узлы вида {kind} ({', '.join(m.get('rules') or []) or 'правила нет'})",
                      f"промахов {m['count']}; формы заголовков: {shapes or '—'}"))
    for rid, r in (rules.get("per_rule") or {}).items():
        total = r.get("hits", 0) + r.get("false", 0)
        if total >= FALSE_MIN and r.get("false", 0) / total > FALSE_SHARE:
            out.append(_f("правило", "P2", f"правило {rid} ложно срабатывает", f"ложных {r['false']} из {total}"))
    for edge, c in (rules.get("edges") or {}).items():
        made = sum(v for k, v in c.items() if k != "rejected")
        if c.get("rejected") and made and c["rejected"] / made > FALSE_SHARE:
            out.append(_f("бриф", "P2", f"человек отклоняет рёбра {edge}", f"отклонено {c['rejected']} из {made}"))
    cov = h.get("coverage") or {}
    if cov.get("blocks") and cov.get("unplaced", 0) / cov["blocks"] > UNPLACED_SHARE:
        out.append(_f("правило", "P2", "много неразмещённого текста", f"{cov['unplaced']} из {cov['blocks']} блоков"))
    holes = {k: v for k, v in counts.items() if k.startswith("W-") and k.split("-")[1] in ("op", "sched", "ep", "topic", "screen")}
    if holes:
        out.append(_f("данные", "P3", "дыры полноты (спека не описывает — вопросы аналитику)",
                      ", ".join(f"{k} ×{v}" for k, v in sorted(holes.items()))))
    if (data.get("profile") or {}).get("new_kind_candidates"):
        out.append(_f("онтология", "P2", "кандидаты в новые виды (4.9)",
                      f"{len(data['profile']['new_kind_candidates'])} шт."))
    env, ver = data.get("environment") or {}, data.get("versions") or {}
    installed = not str(ver.get("forge", "")).startswith("источник")
    if installed and not env.get("mcp_spec_graph"):
        out.append(_f("окружение", "P1", "MCP spec-graph не в манифесте поставки", "environment.mcp_spec_graph = false"))
    if installed and not env.get("hook_graph_guard"):
        out.append(_f("окружение", "P1", "хук graph-guard не зарегистрирован", "environment.hook_graph_guard = false"))
    if prev is not None:
        out += dynamics(data, prev)
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "—": 4}
    return sorted(out, key=lambda f: (order.get(f["severity"], 9), f["class"], f["what"]))


def dynamics(data: Dict[str, Any], prev: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    a, b = (prev.get("health") or {}).get("counts") or {}, (data.get("health") or {}).get("counts") or {}
    for code in sorted(set(a) | set(b)):
        if a.get(code, 0) != b.get(code, 0):
            out.append(_f("динамика", "—", f"{code}: {a.get(code, 0)} → {b.get(code, 0)}", "против прошлого пакета"))
    ma, mb = (prev.get("rules") or {}).get("misses") or {}, (data.get("rules") or {}).get("misses") or {}
    for kind in sorted(set(ma) | set(mb)):
        x, y = (ma.get(kind) or {}).get("count", 0), (mb.get(kind) or {}).get("count", 0)
        if x != y:
            out.append(_f("динамика", "—", f"промахи вида {kind}: {x} → {y}", "против прошлого пакета"))
    return out


QUESTIONS = """Вопросы пользователю, когда пакета мало (данные спеки не просим):
1. Что делали: команда (/graph, /graph diag), scope, на каком шаге остановились?
2. Что ожидали увидеть и что увидели (в ответе агента, в Obsidian)?
3. Был ли вопрос к вам, который показался странным или лишним? На каком шаге?
4. Что сказал гейт (строка «✗ … шаг не закрывается» и следующие две)?
5. Пакет из _graph/diag/ — самый свежий файл; если его нет — вывод `graph_diag`."""


def render(findings: List[Dict[str, str]], data: Dict[str, Any]) -> str:
    ver = data.get("versions") or {}
    lines = [f"# Разбор пакета: forge {ver.get('forge')}, движок {ver.get('engine')}, онтология v{ver.get('ontology')}", "",
             "| серьёзность | класс | находка | чем подтверждено |", "|---|---|---|---|"]
    lines += [f"| {f['severity']} | {f['class']} | {f['what']} | {f['evidence']} |" for f in findings]
    if not findings:
        lines.append("| — | — | находок нет | — |")
    if not data.get("runs"):
        lines += ["", "Прогонов в пакете нет — пакета мало.", "", QUESTIONS]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Черновик разбора пакета /graph diag.")
    ap.add_argument("diag")
    ap.add_argument("--prev", default=None, help="прошлый пакет того же окружения — для динамики")
    ap.add_argument("--json", action="store_true", help="находки JSON-ом (для скриптов)")
    args = ap.parse_args(argv)
    try:
        data = load(Path(args.diag))
        prev = load(Path(args.prev)) if args.prev else None
    except (OSError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    findings = triage(data, prev)
    print(json.dumps(findings, ensure_ascii=False, indent=1) if args.json else render(findings, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
