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

def strip_comments(src: str) -> str:
    """Удалить //- и /* */-комментарии, НЕ трогая содержимое строк/символьных литералов.

    Наивная замена регэкспом съедала `//` внутри строк (URL `http://...`, regex-паттерны),
    из-за чего терялись target клиентов и пути. Этот вариант идёт по символам, пропуская
    строковые (`"..."`, текстовые блоки `\"\"\"`) и символьные (`'.'`) литералы целиком.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        # Текстовый блок Java 15+ \"\"\"...\"\"\"
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = j + 3 if j != -1 else n
            out.append(src[i:j])
            i = j
            continue
        if ch == '"' or ch == "'":
            quote = ch
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote or src[j] == "\n":
                    j += 1 if src[j] == quote else 0
                    break
                j += 1
            out.append(src[i:j])
            i = j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = i + 2
            while j < n and src[j] != "\n":
                j += 1
            i = j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = j + 2 if j != -1 else n
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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


def _is_test_source(rel_dir_parts: tuple[str, ...]) -> bool:
    """True, если путь ведёт в тестовый source set.

    Тестовые сорсы — это сегмент сразу после ``src``, имя которого содержит ``test``:
    ``src/test`` (Maven/Gradle), ``src/testFixtures``, ``src/integrationTest``,
    ``src/intTest`` и т.п. Проверяем именно соседство с ``src``, чтобы не зацепить
    легитимный пакет ``...src/main/java/com/x/test/...`` в продакшен-коде.

    Зачем: иначе @Entity-фикстуры, тестовые @RestController и @KafkaListener из
    интеграционных тестов попадают в grounding как реальная поверхность системы и
    раздувают HARD-категории (gate против недобора их не ловит).
    """
    for i, part in enumerate(rel_dir_parts[:-1]):
        if part == "src" and "test" in rel_dir_parts[i + 1].lower():
            return True
    return False


def in_skipped_dir(root: Path, path: Path) -> bool:
    """True, если файл лежит внутри пропускаемого каталога.

    Считаем только части пути ВНУТРИ проекта — абсолютный префикс ($HOME и т.п.)
    может законно содержать точку (напр. ~/.gigacode/...) и не должен влиять на обход.
    """
    try:
        rel_dir_parts = path.resolve().relative_to(root).parts[:-1]
    except ValueError:
        rel_dir_parts = path.parts[:-1]
    if any(is_skipped_dir(part) for part in rel_dir_parts):
        return True
    return _is_test_source(rel_dir_parts)


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
