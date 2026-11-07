#!/usr/bin/env python3
"""anonymize_pixso.py — обезличить Pixso DSL для фикстуры в git (задача 1.0).

Заменяет идентификаторы библиотеки (publishFile, publishID, overrideKey, componentKey) и тексты,
выбрасывает недостижимые из экрана определения; структура, guid и геометрия остаются. Результат
детерминирован: одинаковые вход, соль и опции — побайтно тот же файл.

exit 0 — готово, в выходе нет исходных значений; 2 — вход не Pixso DSL / неверные аргументы;
3 — в выходе остались исходные значения (самопроверка; файл НЕ записан).

Usage:
    python3 anonymize_pixso.py <src.json> <dst.json> [--keep-texts] [--no-prune]
                               [--font "СТАРОЕ=НОВОЕ"]... [--salt СОЛЬ]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daiver.pixso import anonymize as A  # noqa: E402
from daiver.pixso import dsl as D  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Обезличить Pixso DSL для фикстуры.")
    ap.add_argument("src", help="исходный DSL (JSON)")
    ap.add_argument("dst", help="куда записать обезличенный файл")
    ap.add_argument("--salt", default=A.DEFAULT_SALT, help="соль подмен (по умолчанию фиксированная)")
    ap.add_argument("--keep-texts", action="store_true", help="тексты не заменять")
    ap.add_argument("--no-prune", action="store_true", help="не выбрасывать недостижимые определения")
    ap.add_argument("--font", action="append", default=[], metavar="СТАРОЕ=НОВОЕ",
                    help="переименовать шрифт (подстрокой во всех значениях и ключах); можно повторять")
    args = ap.parse_args(argv)

    fonts = {}
    for pair in args.font:
        old, sep, new = pair.partition("=")
        if not sep or not old:
            print(f"✗ --font ожидает «СТАРОЕ=НОВОЕ», получено {pair!r}", file=sys.stderr)
            return 2
        fonts[old] = new
    try:
        doc = D.load(Path(args.src))
    except D.DslError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    new_doc, report = A.anonymize(doc, salt=args.salt, keep_texts=args.keep_texts, fonts=fonts,
                                  prune=not args.no_prune)
    if report["leaks"]:
        print("✗ в выходе остались исходные значения — файл не записан:\n  "
              + "\n  ".join(report["leaks"][:20]), file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3
    dst = Path(args.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(A.dumps(new_doc), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
