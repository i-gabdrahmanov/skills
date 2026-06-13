#!/usr/bin/env python3
"""tdd-guard.py — PreToolUse TDD-gate: блокирует запись в src/main, пока нет RED-теста.

PDLC v3.5. Принцип deny-first: код в src/main/ не пишется, пока для задачи
не создан тест со статусом RED (pending). На src/test/ ограничений нет.

Дополнительно:
  • @DataJpaTest / @SpringBootTest блокируются при
    quality.block_jpa_test=true (по умолчанию true), кроме случаев когда
    test_layer=mixed (escape-hatch).
  • Интеграционные тесты (с @SpringBootTest / @EmbeddedKafka) исключаются
    из TDD-цикла: если все написанные тесты интеграционные — RED не требуется.
    Управляется quality.tdd_integration_skip=true (по умолчанию true).

Матчеры: (Write|Edit|WriteFile|NotebookEdit). Блок: exit 2 + stderr.
fail-open: если manifest не найден или tdd выключен — пропускает.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import risk_ladder as R


# Аннотации, которые делают тест "интеграционным" (непригодным для TDD RED)
INTEGRATION_ANNOTATIONS = [
    r"@SpringBootTest",
    r"@EmbeddedKafka",
    r"@Testcontainers",
    r"@DataJpaTest",
    r"@DataMongoTest",
    r"@JdbcTest",
]


def _block(reason: str) -> int:
    print(f"[tdd-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _is_integration_test(content: str) -> bool:
    """Проверить, содержит ли контент интеграционные аннотации."""
    if not content:
        return False
    for pat in INTEGRATION_ANNOTATIONS:
        if re.search(pat, content):
            return True
    return False


def _scan_test_directory(test_dir: Path) -> dict:
    """Сканировать src/test/ на наличие unit и integration тестов.

    Возвращает:
        {"unit_count": N, "integration_count": M, "has_unit": bool, "has_integration": bool}
    """
    result = {"unit_count": 0, "integration_count": 0, "has_unit": False, "has_integration": False}
    if not test_dir.is_dir():
        return result

    java_files = list(test_dir.rglob("*.java"))
    for jf in java_files:
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if _is_integration_test(content):
            result["integration_count"] += 1
        else:
            result["unit_count"] += 1

    result["has_unit"] = result["unit_count"] > 0
    result["has_integration"] = result["integration_count"] > 0
    return result


def main() -> int:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        return 0

    cwd = data.get("cwd", "")
    root = Path(cwd) if cwd else Path.cwd()
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    content = tool_input.get("content") or ""
    target = R._target_path(tool_name, tool_input)

    # Загружаем конфиг качества
    cfg = R.pipeline_cfg(root)
    quality_cfg = cfg.get("quality") or {}

    # ── Гейт @DataJpaTest/@SpringBootTest ──
    test_layer = quality_cfg.get("test_layer", "unit")
    block_jpa = quality_cfg.get("block_jpa_test", True)
    has_jpa_annotation = re.search(r"@(DataJpaTest|SpringBootTest)", content)
    if has_jpa_annotation and block_jpa and test_layer != "mixed":
        return _block(
            "test_layer=service-unit + @DataJpaTest/@SpringBootTest запрещены "
            "(падают initializationError). "
            "Установи quality.block_jpa_test=false или quality.test_layer=mixed "
            "в pipeline.json, чтобы разрешить."
        )

    # ── src/test — разрешено (но проверяем TDD-цикл при интеграционных тестах) ──
    target_str = str(target).replace("\\", "/")

    if "/src/test/" in target_str:
        # Если пишем тест с интеграционными аннотациями — логируем предупреждение,
        # но не блокируем (тесты можно писать даже без TDD)
        if _is_integration_test(content):
            tdd_int_skip = quality_cfg.get("tdd_integration_skip", True)
            if tdd_int_skip:
                print("[tdd-guard] INFO: Интеграционный тест — TDD-цикл для него пропускается.",
                      file=sys.stderr)
        return 0

    # Не src/main — не наш сценарий
    if "/src/main/" not in target_str:
        return 0

    # TDD выключен — пропускаем
    tdd_enabled = quality_cfg.get("tdd", True)
    if not tdd_enabled:
        return 0

    tdd_int_skip = quality_cfg.get("tdd_integration_skip", True)

    # ── Эвристика: если ВСЕ существующие тесты интеграционные — TDD не обязателен ──
    if tdd_int_skip:
        # Определяем модуль цели
        # target = service/pprbulservice/src/main/java/...
        # ищем соответствующий src/test/
        parts = target_str.split("/")
        test_dir = None
        for i, part in enumerate(parts):
            if part == "src" and i + 1 < len(parts) and parts[i + 1] == "main":
                # Заменяем src/main на src/test
                test_parts = parts[:i] + ["src", "test"] + parts[i + 2:]
                # Убираем имя файла, оставляем директорию
                candidate = root / "/".join(test_parts)
                # Находим src/test/java/<package_path>
                test_dir = candidate.parent if candidate.suffix == ".java" else candidate
                while test_dir and "src" in test_dir.parts:
                    if (test_dir / "java").is_dir():
                        test_dir = test_dir / "java"
                        break
                    test_dir = test_dir.parent
                break

        if test_dir and test_dir.is_dir():
            scan = _scan_test_directory(test_dir)
            # Если есть unit-тесты — TDD-цикл активен, нужен RED
            # Если только интеграционные — пропускаем
            if scan["has_integration"] and not scan["has_unit"]:
                print(f"[tdd-guard] INFO: В {test_dir} найдены только интеграционные тесты "
                      f"({scan['integration_count']} шт.) — TDD пропущен.", file=sys.stderr)
                return 0

    # ── Проверка RED-теста в manifest ──
    steps = R.manifest_status(root)
    has_red = any(
        s == "pending" and ("test" in step_id.lower() or "tdd" in step_id.lower())
        for step_id, s in steps.items()
    )
    if has_red:
        return _block(
            "RED-тест для задачи ещё не завершён. "
            "Напиши сначала тест (src/test/), потом код (src/main/)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
