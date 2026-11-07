#!/usr/bin/env python3
"""demo_vault.py — vault фикстуры ТЗ для ручной проверки в Obsidian (задача 3.3).

Строит проект из `fixtures/tz-mini` и проводит его шагами map и link «ответами модели» (как тесты), затем
пишет Obsidian-слой и проверку графа. Открыть в Obsidian каталог `<out>/repo/docs/graph` как vault и
проверить глазами: цвета групп по видам, `_graph/Начало.md`, виды, переходы ТЗ §10.

    python3 -X utf8 tools/demo_vault.py --out /tmp/sg-demo [--bases]

`--bases` включает core-плагин Bases в `.obsidian/core-plugins.json` до сборки слоя (виды — .base).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    import _testkit as K
    from sgraph import tools as T
    ap = argparse.ArgumentParser(description="Vault фикстуры для Obsidian.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bases", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    if (out / "repo").exists():
        shutil.rmtree(out / "repo")
    root = K.make_project(out / "repo")
    K.through_review(root)
    K.approve_edges(root)
    vault = root / "docs" / "graph"
    if args.bases:
        (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
        (vault / ".obsidian" / "core-plugins.json").write_text(json.dumps({"bases": True, "graph": True}), encoding="utf-8")
    layer = T.call("graph_obsidian", root, {})
    check = T.call("graph_validate", root, {})
    print(f"vault: {vault}\nслой: {layer['mode']}, видов {layer['views']}; проверка: ошибок {check['errors']}, "
          f"предупреждений {check['warnings']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
