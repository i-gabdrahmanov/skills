#!/usr/bin/env python3
"""graph_doctor.py — доктор окружения spec-graph без рантайма (задача X.2).

    python3 .gigacode/extensions/spec-graph/hooks/graph_doctor.py --project .
    python3 .gigacode/extensions/forge/hooks/graph_doctor.py --project .      # forge --with-graph

Запасной путь, когда `graph_status` недоступен совсем (MCP не поднялся): печатает то же, что раздел
«Окружение» пакета диагностики (4.3), плюс живые проверки поставки. Вывод без текста проекта и без путей
машины (корень проекта — `<проект>`, домашний каталог — `~`) — его можно вынести из окружения.

Проверки: собранный extension цел (манифесты, MCP, hooks.json — forge_extension.check); в профиле поставки
есть specgraph и MCP spec-graph; команда /forge-graph на месте; хук graph-guard зарегистрирован; ссылка в
рантайме (gigacode или qwen) ведёт на эту поставку; MCP spec-graph отвечает на initialize; движок
импортируется и читает конфиг проекта.

Коды: 0 — всё на месте, 1 — есть проблемы (перечислены), 2 — неверный вызов.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
EXT = HERE.parent
ENGINE = EXT / "skills" / "specgraph" / "scripts"
sys.path.insert(0, str(HERE))


def _mask(text: str, root: Path) -> str:
    return str(text).replace(str(root), "<проект>").replace(str(Path.home()), "~")


def _mcp_answers(cfg: dict) -> Tuple[bool, str]:
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    try:
        r = subprocess.run([cfg["command"], *cfg.get("args", [])], cwd=cfg.get("cwd"), input=init + "\n",
                           capture_output=True, text=True, encoding="utf-8", timeout=60,
                           env=dict(os.environ, **(cfg.get("env") or {})))
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"не запускается ({type(e).__name__})"
    return ('"spec-graph"' in r.stdout), ("отвечает" if '"spec-graph"' in r.stdout else f"молчит (код {r.returncode})")


def run(root: Path) -> Tuple[List[str], List[str]]:
    """(строки отчёта, проблемы)."""
    import forge_extension as FE
    lines, bad = [], []
    profile = (EXT / FE.PROFILE_FILE).read_text(encoding="utf-8") if (EXT / FE.PROFILE_FILE).is_file() else ""
    kinds = {ln.split()[0]: ln.split()[1:] for ln in profile.splitlines() if ln.strip() and not ln.startswith("#")}
    graph = EXT.name == "spec-graph" or "with graph" in profile.splitlines()
    lines.append(f"- поставка: {EXT.name}; профиль: {(kinds.get('profile') or ['?'])[0]}; граф спеки: {graph}")
    if not graph:
        bad.append("в поставке нет графа спеки: spec-graph ставится install.sh spec-graph <репо спеки> "
                   "или install.sh forge <репо> --with-graph")
    for p in FE.check(EXT, root):
        bad.append(_mask(p, root))
    man = {}
    for m in FE.MANIFESTS:
        if (EXT / m).is_file():
            man = json.loads((EXT / m).read_text(encoding="utf-8"))
            break
    lines.append(f"- ревизия forge: {man.get('forgeRevision', 'нет манифеста')}; python: {'.'.join(map(str, sys.version_info[:3]))}")
    mcp = (man.get("mcpServers") or {}).get("spec-graph")
    if mcp is None:
        bad.append("MCP spec-graph нет в манифесте поставки")
        lines.append("- MCP spec-graph: нет в манифесте")
    else:
        ok, how = _mcp_answers(mcp)
        lines.append(f"- MCP spec-graph: в манифесте, {how}")
        if not ok:
            bad.append(f"MCP spec-graph {how}")
    cmd = EXT / "commands" / "forge-graph.md"
    lines.append(f"- команда /forge-graph: {'на месте' if cmd.is_file() else 'нет'}")
    if not cmd.is_file():
        bad.append("нет команды commands/forge-graph.md")
    hooks = (EXT / "hooks" / FE.HOOKS_FILE).read_text(encoding="utf-8") if (EXT / "hooks" / FE.HOOKS_FILE).is_file() else ""
    lines.append(f"- хук graph-guard: {'зарегистрирован' if 'graph-guard.py' in hooks else 'нет'}")
    if "graph-guard.py" not in hooks:
        bad.append("хук graph-guard не зарегистрирован в hooks/hooks.json")
    cli = FE.runtime_cli()
    if not cli:
        lines.append("- рантайм: gigacode/qwen не найден в PATH — ссылку не проверить")
    else:
        name = man.get("name") or FE.extension_name(EXT.name, root)
        src = FE.links(FE.runtime_home(cli)).get(name)
        linked = bool(src) and Path(src).resolve() == EXT.resolve()
        lines.append(f"- рантайм {Path(cli).name}: ссылка {name} — {'на эту поставку' if linked else 'нет' if not src else 'на другой путь'}; "
                     f"включение для workspace: `{Path(cli).name} extensions list` из корня проекта")
        if not linked:
            bad.append(f"нет ссылки рантайма на поставку — bash {FE.install_rel(EXT.name)}/deploy-local.sh")
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    try:
        from sgraph import config as C
        from sgraph import diag as D
        from sgraph import ontology as O
        cfg, errors = C.load(root)
        v = D._versions(root, O.load(root))
        lines.append(f"- движок: {v['engine']}; онтология: v{v['ontology']}, локальная: {v['ontology_local']}")
        if cfg is None:
            lines.append(f"- конфиг .spec-graph/config.json: {'нет — первый запуск /forge-graph' if not errors else 'с ошибками'}")
        else:
            env = D._environment(root, cfg)
            lines.append(f"- Obsidian: core-plugins.json — {env['obsidian_core_plugins']}; bases: {env['obsidian_bases']}; "
                         f"слой видов: {env['obsidian_layer']}")
    except Exception as e:  # noqa: BLE001 — доктор называет поломку, а не падает
        bad.append(f"движок spec-graph не импортируется ({type(e).__name__})")
    return lines, bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Доктор окружения spec-graph (без рантайма).")
    ap.add_argument("--project", default=".")
    args = ap.parse_args(argv)
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"✗ нет каталога проекта: {args.project}", file=sys.stderr)
        return 2
    lines, bad = run(root)
    print("== spec-graph: окружение ==")
    print("\n".join(lines))
    if bad:
        print(f"✗ проблем: {len(bad)}")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("✓ поставка на месте")
    return 0


if __name__ == "__main__":
    sys.exit(main())
