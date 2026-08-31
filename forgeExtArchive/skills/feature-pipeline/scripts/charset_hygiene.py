#!/usr/bin/env python3
"""charset_hygiene.py — ДЕТЕРМИНИРОВАННЫЙ gate чистоты текста спеки: китайские/CJK-символы и мусор.

Живой репорт: слабая модель (Qwen/GigaCode) вставляет в brd.md/sdd.md/tech-design.md
китайские иероглифы (实现, 服务), «странные слова» с латиницей внутри кириллицы (homoglyph:
латинская `o`/`a`/`c` в русском слове), битую кодировку (U+FFFD) и невидимые управляющие
символы. Судьи это не ловили.

Разделение труда: правописание ВАЛИДНОЙ кириллицы (орфография, странные слова) проверяет
LLM-судья (brd-judge §7.6 / spec-judge §7.4) — словарём/грамматикой, статикой это не сделать.
Здесь — только то, что детерминируется по Unicode, без словаря и без зависимостей:
китайский/CJK всегда БЛОК; остальное advisory (тюнинг в pipeline.json quality.charset_gate).

Контракт (как check_brd_doc.py):
    scan(text[, cfg]) -> (errors, warnings)   # списки строк-находок с координатами
CLI:
    charset_hygiene.py <file> [--json]        # exit 0 = чисто, 2 = есть блокирующие находки
"""
from __future__ import annotations

import argparse
import json
import re
import string
import unicodedata
from pathlib import Path

# --- Диапазоны Unicode (start, end, метка) --------------------------------------------------
# CJK и родственные восточноазиатские блоки. В русской/английской спеке нелегитимны никогда.
_CJK_RANGES: list[tuple[int, int, str]] = [
    (0x3000, 0x303F, "CJK-пунктуация"),
    (0x3040, 0x309F, "Hiragana"),
    (0x30A0, 0x30FF, "Katakana"),
    (0x3400, 0x4DBF, "CJK Ext-A"),
    (0x4E00, 0x9FFF, "CJK-иероглиф"),
    (0xAC00, 0xD7AF, "Hangul"),
    (0xF900, 0xFAFF, "CJK-совместимость"),
    (0xFF00, 0xFFEF, "Fullwidth-формы"),
    (0x20000, 0x2A6DF, "CJK Ext-B"),
    (0x2A700, 0x2EBEF, "CJK Ext-C..F"),
]

# Невидимые/служебные: zero-width, bidi-контролы, BOM. Часто вставляются моделью незаметно.
_ZERO_WIDTH: set[int] = {
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,   # ZWSP/ZWNJ/ZWJ/LRM/RLM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,   # bidi embeddings/overrides
    0x2060, 0xFEFF,                            # word-joiner / BOM (ZWNBSP)
}

_REPLACEMENT = 0xFFFD  # символ замены — признак битой перекодировки (mojibake)

_ASCII_LETTERS = set(string.ascii_letters)

DEFAULT_CFG: dict[str, object] = {
    "enabled": True,
    "cjk": "block",          # китайские/CJK-символы: block | warn | off
    "mojibake": "warn",      # U+FFFD: block | warn | off
    "zero_width": "warn",    # невидимые/bidi: block | warn | off
    "mixed_script": "warn",  # слова кириллица+латиница: block | warn | off
}


def _cjk_label(cp: int) -> str | None:
    for lo, hi, label in _CJK_RANGES:
        if lo <= cp <= hi:
            return label
    return None


def _describe(ch: str) -> str:
    cp = ord(ch)
    try:
        name = unicodedata.name(ch)
    except ValueError:
        name = "без имени"
    return f"U+{cp:04X} {name}"


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _blank(m: "re.Match[str]") -> str:
    """Замена одинаковой длины — сохраняет смещения символов для номеров строк."""
    return " " * (m.end() - m.start())


def _mask_code_and_urls(text: str) -> str:
    """Гасит fenced/inline-код и URL пробелами той же длины: mixed_script не должен шуметь
    на `OrderService`, ```java ...``` и http://... — там латиница легитимна."""
    masked = re.sub(r"```.*?```", _blank, text, flags=re.DOTALL)
    masked = re.sub(r"`[^`]*`", _blank, masked)
    masked = re.sub(r"https?://\S+|www\.\S+", _blank, masked)
    return masked


def scan(text: str, cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """Сканирует текст. Возвращает (errors, warnings) — списки готовых строк-находок.

    severity каждой категории берётся из cfg (см. DEFAULT_CFG): block → errors, warn → warnings,
    off → пропуск. cfg — обычно pipeline.json quality.charset_gate.
    """
    merged = {**DEFAULT_CFG, **(cfg or {})}
    errors: list[str] = []
    warnings: list[str] = []
    if merged.get("enabled", True) is False:
        return errors, warnings

    def emit(key: str, message: str) -> None:
        mode = merged.get(key, DEFAULT_CFG[key])
        if mode == "off":
            return
        (errors if mode == "block" else warnings).append(message)

    # 1. Посимвольно: CJK, mojibake, невидимые.
    cjk_hits: list[tuple[int, str]] = []
    moji_hits: list[tuple[int, str]] = []
    zw_hits: list[tuple[int, str]] = []
    for idx, ch in enumerate(text):
        cp = ord(ch)
        if _cjk_label(cp) is not None:
            cjk_hits.append((idx, ch))
        elif cp == _REPLACEMENT:
            moji_hits.append((idx, ch))
        elif cp in _ZERO_WIDTH:
            zw_hits.append((idx, ch))

    def summarize(hits: list[tuple[int, str]], head: str) -> str:
        examples = [f"'{ch}' ({_describe(ch)}) стр.{_line_of(text, idx)}" for idx, ch in hits[:6]]
        more = f" +ещё {len(hits) - 6}" if len(hits) > 6 else ""
        return f"{head}: {len(hits)} шт — " + "; ".join(examples) + more

    if cjk_hits:
        emit("cjk", summarize(cjk_hits, "китайские/CJK-символы"))
    if moji_hits:
        emit("mojibake", summarize(moji_hits, "битая кодировка (U+FFFD)"))
    if zw_hits:
        emit("zero_width", summarize(zw_hits, "невидимые/zero-width/bidi-символы"))

    # 2. Слова со смешанными кириллицей+латиницей (homoglyph — «странные слова»).
    #    Гасим код/URL, чтобы не ловить латинские идентификаторы.
    masked = _mask_code_and_urls(text)
    mixed: list[tuple[int, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r"[A-Za-zЀ-ӿ]{2,}", masked):
        tok = m.group(0)
        has_lat = any(c in _ASCII_LETTERS for c in tok)
        has_cyr = any("Ѐ" <= c <= "ӿ" for c in tok)
        if has_lat and has_cyr and tok not in seen:
            seen.add(tok)
            mixed.append((m.start(), tok))
    if mixed:
        examples = [f"'{tok}' стр.{_line_of(text, idx)}" for idx, tok in mixed[:6]]
        more = f" +ещё {len(mixed) - 6}" if len(mixed) > 6 else ""
        emit("mixed_script",
             "слова со смешанной кириллицей+латиницей (homoglyph, вероятно «странные слова»): "
             + "; ".join(examples) + more)

    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Детектор китайских/CJK-символов и текстового мусора в документе спеки.")
    ap.add_argument("file", help="путь к документу (brd.md/sdd.md/tech-design.md/spec.md/...)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        errors, warnings = [f"нет файла: {path}"], []
    else:
        errors, warnings = scan(path.read_text(encoding="utf-8", errors="replace"))

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "file": str(path), "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"charset check: {'✓ PASS' if status == 'pass' else '✗ FAIL'}")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  · warn: {w}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
