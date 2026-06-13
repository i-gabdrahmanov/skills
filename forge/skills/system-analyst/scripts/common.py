"""Общие хелперы детерминированных сканеров system-analyst.

Цель сканеров — recall ≈ 100% по механическим артефактам (без LLM, без лимитов
токенов). Каждый сканер возвращает плоский список dict-ов, где у каждого элемента
есть поле ``file`` — по нему ``attribute_module`` приписывает находку к модулю.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

SKIP_DIRS = {"build", "out", "target", ".gradle", ".idea", "node_modules", ".git", "bin"}
# Каталог, куда скиллы складывают свои данные (scan-JSON, pipeline-state). НЕ dot-папка
# (иначе рантайм режет доступ по path-гарду), но сканер не должен заходить в свой вывод.
DATA_DIR = "ground"
BUILD_FILES = ("build.gradle", "build.gradle.kts", "pom.xml")

_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"//[^\n]*")


def strip_comments(src: str) -> str:
    return _COMMENT_LINE_RE.sub("", _COMMENT_BLOCK_RE.sub("", src))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def repo_root() -> str:
    """Корень репо: git toplevel или cwd. Чтобы скиллам не нужен $(pwd)/$(git ...) в
    shell-вызове — рантайм Qwen/GigaCode жёстко режет command substitution ($(), backticks)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def is_skipped_dir(part: str) -> bool:
    # Любая dot-папка (.git, .gigacode, .idea, …), явный SKIP_DIRS и каталог данных скиллов.
    return part.startswith(".") or part in SKIP_DIRS or part == DATA_DIR


def in_skipped_dir(root: Path, path: Path) -> bool:
    """True, если файл лежит внутри пропускаемого каталога.

    Считаем только части пути ВНУТРИ проекта — абсолютный префикс ($HOME и т.п.)
    может законно содержать точку (напр. ~/.gigacode/...) и не должен влиять на обход.
    """
    try:
        rel_dir_parts = path.resolve().relative_to(root).parts[:-1]
    except ValueError:
        rel_dir_parts = path.parts[:-1]
    return any(is_skipped_dir(part) for part in rel_dir_parts)


def iter_files(root: Path, suffixes: tuple[str, ...]) -> Iterable[Path]:
    root = root.resolve()
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if in_skipped_dir(root, path):
            continue
        if not suffixes or path.suffix in suffixes or path.name in suffixes:
            yield path


def iter_java(root: Path) -> Iterable[Path]:
    yield from iter_files(root, (".java", ".kt"))


def attribute_module(file_path: Path, module_dirs: list[tuple[str, Path]]) -> str:
    """Вернуть имя модуля — ближайший предок-модуль для файла (самый длинный путь-префикс)."""
    fp = str(Path(file_path).resolve())
    best, best_len = "?", -1
    for name, mp in module_dirs:
        mps = str(Path(mp).resolve())
        if (fp == mps or fp.startswith(mps + "/")) and len(mps) > best_len:
            best, best_len = name, len(mps)
    return best
