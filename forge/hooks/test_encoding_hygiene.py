#!/usr/bin/env python3
"""test_encoding_hygiene.py — гард против регресса cp1251-бага (Windows non-UTF8 locale).

Живой репорт: деплой на Windows с русской локалью падал в preflight.py на
json.loads(risk_policy_p.read_text()) — Path.read_text()/write_text()/open() без
явного encoding= берут кодировку из локали ОС (cp1251), а не UTF-8, и не могут
декодировать кириллицу. Починено точечно в 241 месте (см. историю коммитов) —
этот тест AST-сканирует ВЕСЬ hooks/+skills/ и не даёт новому месту появиться
незамеченным.

AST, не regex: корректно распознаёт многострочные/вложенные вызовы вида
write_text(json.dumps(data, ensure_ascii=False, indent=2)).
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
REPO = HOOKS.parent


def _is_binary_open(call: ast.Call) -> bool:
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and "b" in mode


def _has_encoding_kw(call: ast.Call) -> bool:
    return any(kw.arg == "encoding" for kw in call.keywords)


def find_missing_encoding(path: Path) -> list[str]:
    """Возвращает ["<line>: <call>", ...] для read_text/write_text/open без encoding=."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []  # не наш файл — пусть падает где-то ещё, не здесь
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = None
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("read_text", "write_text"):
            target = node.func.attr
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            target = "open"
        if target is None:
            continue
        if _has_encoding_kw(node):
            continue
        if target == "read_text" and len(node.args) >= 1:
            continue  # Path.read_text(encoding, errors) — 1-й позиционный это и есть encoding
        if target == "open":
            if _is_binary_open(node):
                continue
            if len(node.args) == 0 and not node.keywords:
                continue  # open() без аргументов — не файловый вызов
        missing.append(f"{path}:{node.lineno}: {target}(...)")
    return missing


class TestEncodingHygiene(unittest.TestCase):
    def test_no_missing_encoding_in_hooks_and_skills(self):
        problems: list[str] = []
        for root in (REPO / "hooks", REPO / "skills"):
            for py in sorted(root.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                problems.extend(find_missing_encoding(py))
        self.assertEqual(
            problems, [],
            f"{len(problems)} мест без encoding=\"utf-8\" — сломает Windows с не-UTF8 "
            f"локалью (cp1251 и т.п.), см. docstring теста:\n" + "\n".join(problems[:30])
        )


if __name__ == "__main__":
    unittest.main()
