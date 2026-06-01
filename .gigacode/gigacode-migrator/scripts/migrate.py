#!/usr/bin/env python3
"""Мигрирует дерево скиллов Gigacode -> GigaCode по правилам из mapping.json.

По умолчанию НЕ деструктивен: копирует <source> -> <target> и правит копию.
Правила применяются по порядку (см. mapping.json). Модели не трогаются —
скиллы их не выбирают, это делает рантайм.

Примеры:
  # dry-run: что изменится, без записи
  python migrate.py --dry-run
  # реальная миграция ~/.gigacode/skills -> ~/.gigacode/skills
  python migrate.py
  # своя пара путей
  python migrate.py --source ~/.gigacode --target ~/.gigacode
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAPPING = os.path.join(os.path.dirname(HERE), "references", "mapping.json")

# Бинарь/неподлежащее правке — копируем как есть.
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".jar", ".zip", ".gz",
    ".pptx", ".docx", ".xlsx", ".pyc", ".so", ".dylib", ".woff", ".woff2",
    ".ttf", ".eot", ".svg",
}


def load_rules(mapping_path):
    with open(mapping_path, encoding="utf-8") as fh:
        data = json.load(fh)
    rules = []
    for r in data["rules"]:
        flags = re.IGNORECASE if "i" in r.get("flags", "") else 0
        rules.append({
            "category": r.get("category", ""),
            "regex": re.compile(r["pattern"], flags),
            "replacement": r["replacement"],
            "case_preserve": bool(r.get("case_preserve", False)),
        })
    return rules


def _apply_case(matched, repl):
    """Перенести регистр найденного фрагмента на замену."""
    if matched.isupper():
        return repl.upper()
    if matched[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def transform(text, rules):
    """Применить все правила по порядку. Вернуть (новый_текст, число_замен)."""
    total = 0
    for rule in rules:
        repl, cp = rule["replacement"], rule["case_preserve"]

        def _sub(m, repl=repl, cp=cp):
            return _apply_case(m.group(0), repl) if cp else repl

        text, n = rule["regex"].subn(_sub, text)
        total += n
    return text, total


def is_text_file(path):
    if os.path.splitext(path)[1].lower() in SKIP_EXT:
        return False
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return False
    return b"\x00" not in chunk


def rename_path(path, rules):
    """Имя файла/папки -> мигрированное имя (тем же набором правил)."""
    base = os.path.basename(path)
    new_base, n = transform(base, rules)
    if n == 0 or new_base == base:
        return path
    new_path = os.path.join(os.path.dirname(path), new_base)
    return new_path


def main():
    ap = argparse.ArgumentParser(description="Gigacode -> GigaCode migrator")
    home = os.path.expanduser("~")
    ap.add_argument("--source", default=os.path.join(home, ".gigacode", "skills"),
                    help="что мигрировать (default: ~/.gigacode/skills)")
    ap.add_argument("--target", default=os.path.join(home, ".gigacode", "skills"),
                    help="куда положить результат (default: ~/.gigacode/skills)")
    ap.add_argument("--mapping", default=DEFAULT_MAPPING,
                    help="путь к mapping.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать сводку, без записи")
    ap.add_argument("--in-place", action="store_true",
                    help="править source напрямую (target игнорируется). Необратимо.")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать target, если уже существует")
    ap.add_argument("--no-rename", action="store_true",
                    help="не переименовывать файлы/папки, только содержимое")
    args = ap.parse_args()

    source = os.path.abspath(os.path.expanduser(args.source))
    if not os.path.isdir(source):
        print(json.dumps({"error": f"source не найден: {source}"}, ensure_ascii=False))
        return 2
    rules = load_rules(os.path.expanduser(args.mapping))

    # Куда пишем.
    if args.in_place:
        workdir = source
    else:
        target = os.path.abspath(os.path.expanduser(args.target))
        if os.path.commonpath([source, target]) == source and target != source:
            print(json.dumps({"error": "target внутри source — выбери другой target"},
                             ensure_ascii=False))
            return 2
        if os.path.exists(target):
            if not args.force and not args.dry_run:
                print(json.dumps({"error": f"target существует: {target} (используй --force)"},
                                 ensure_ascii=False))
                return 2
            if args.force and not args.dry_run:
                shutil.rmtree(target)
        if not args.dry_run:
            shutil.copytree(source, target)
        workdir = target if not args.dry_run else source

    # Обход. В dry-run считаем по source, ничего не трогая.
    files_changed = 0
    repl_total = 0
    renames = []
    sample = []
    for root, dirs, files in os.walk(workdir, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            if not is_text_file(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    original = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            new_text, n = transform(original, rules)
            if n:
                repl_total += n
                files_changed += 1
                if len(sample) < 12:
                    sample.append({"file": os.path.relpath(path, workdir), "replacements": n})
                if not args.dry_run:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(new_text)
        # переименование (снизу вверх: сначала файлы, потом папки)
        if not args.no_rename:
            for name in files + dirs:
                path = os.path.join(root, name)
                new_path = rename_path(path, rules)
                if new_path != path:
                    renames.append({"from": os.path.relpath(path, workdir),
                                    "to": os.path.relpath(new_path, workdir)})
                    if not args.dry_run:
                        os.rename(path, new_path)

    print(json.dumps({
        "mode": "dry-run" if args.dry_run else ("in-place" if args.in_place else "copy"),
        "source": source,
        "target": (source if args.in_place else os.path.abspath(os.path.expanduser(args.target))),
        "files_changed": files_changed,
        "replacements_total": repl_total,
        "renames": renames,
        "sample": sample,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
