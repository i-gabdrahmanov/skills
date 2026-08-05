#!/usr/bin/env python3
"""analyze_tests.py — детерминированный сканер тестовой базы Java-проекта (скилл test-writer).

Зачем: тестописатель должен писать тесты В СТИЛЕ проекта, а не «как обычно». Этот скрипт один
раз сканирует все `src/test/**/*.java`, собирает конвенции (фреймворки, имена, структура,
базовые классы) и отбирает эталонные тесты по эвристикам качества. Результат кэшируется в
`docs/system-analysis/scan/test-conventions.json` (рядом с reuse.json из grounding) и
переиспользуется всеми последующими вызовами тестописателя — «анализ при первом запуске».

Отбор эталонов консервативен: Spring-context тесты (@SpringBootTest/@DataJpaTest/…) и
@Disabled-файлы в эталоны не попадают — red-judge их блокирует, копировать их стиль вредно.

Usage:
    analyze_tests.py --root <project> [--out <path>] [--if-missing | --refresh]
        [--max-exemplars N] [--json]

  --if-missing    выйти без скана, если валидный кэш уже есть (семантика «первого запуска»)
  --refresh       пересканировать, даже если кэш есть
  --json          напечатать полный JSON в stdout (по умолчанию — краткая сводка)

Exit: 0 — кэш записан/актуален (в т.ч. проект без тестов — warnings), 1 — ошибка аргументов/IO.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_OUT = "docs/system-analysis/scan/test-conventions.json"
SKIP_DIRS = {".git", ".gradle", ".idea", ".gigacode", "build", "target", "out", "node_modules"}

FRAMEWORK_MARKERS = {
    "junit5": re.compile(r"import\s+(?:static\s+)?org\.junit\.jupiter\."),
    "junit4": re.compile(r"import\s+(?:static\s+)?org\.junit\.(?:Test|Assert|Before|After|Rule|runner)\b"),
    "mockito": re.compile(r"import\s+(?:static\s+)?org\.mockito\."),
    "assertj": re.compile(r"import\s+(?:static\s+)?org\.assertj\."),
    "hamcrest": re.compile(r"import\s+(?:static\s+)?org\.hamcrest\."),
    "spring_test": re.compile(r"import\s+(?:static\s+)?org\.springframework\.(?:boot\.test|test)\."),
    "testcontainers": re.compile(r"import\s+(?:static\s+)?org\.testcontainers\."),
}
_SPRING_CONTEXT_ANNO = re.compile(
    r"@(?:SpringBootTest|DataJpaTest|WebMvcTest|WebFluxTest|DataMongoTest|JdbcTest|"
    r"DataJdbcTest|RestClientTest|JsonTest|SpringJUnitConfig|ContextConfiguration)\b"
)
_MOCKITO_EXT = re.compile(r"@(?:ExtendWith\(\s*MockitoExtension\.class\s*\)|RunWith\(\s*MockitoJUnitRunner)")
_TEST_ANNO = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory)\b")
_METHOD_SIG = re.compile(r"^\s*(?:public\s+|protected\s+)?(?:static\s+)?[\w<>\[\],.?\s]+?\s(\w+)\s*\(")
_CLASS_DECL = re.compile(r"\bclass\s+(\w+)")
_EXTENDS = re.compile(r"\bclass\s+\w+\s+extends\s+(\w+)")
_GWT_COMMENT = re.compile(r"//\s*(?:given|when|then|arrange|act|assert)\b", re.I)
_DISPLAY_NAME = re.compile(r"@DisplayName\b")
_NESTED = re.compile(r"@Nested\b")
_DISABLED = re.compile(r"@(?:Disabled|Ignore)\b")
_SLEEP = re.compile(r"Thread\.sleep\s*\(")
_ASSERTION = re.compile(
    r"\bassert\w*\s*\(|\bassertThat\s*\(|\bverify\w*\s*\(|\bfail\s*\(|"
    r"\.is[A-Z]\w*\s*\(|\.contains|\.hasSize", )


def _classify_name(name: str) -> str:
    if re.match(r"^should[A-Z_0-9]", name):
        return "should"
    if re.match(r"^given[A-Z_0-9]", name):
        return "given"
    if re.match(r"^test[A-Z_0-9]", name):
        return "test"
    if "_" in name:
        return "snake"
    return "other"


def _test_method_names(lines: list[str]) -> list[str]:
    """Имена методов, аннотированных @Test/@ParameterizedTest/… (сигнатура в ближайших строках)."""
    names = []
    for i, line in enumerate(lines):
        if not _TEST_ANNO.search(line):
            continue
        for look in lines[i + 1: i + 6]:
            m = _METHOD_SIG.match(look)
            if m:
                names.append(m.group(1))
                break
    return names


def _analyze_file(path: Path, root: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    methods = _test_method_names(lines)
    frameworks = {name: bool(rx.search(text)) for name, rx in FRAMEWORK_MARKERS.items()}
    ext = _EXTENDS.search(text)
    cls = _CLASS_DECL.search(text)
    return {
        "path": path.relative_to(root).as_posix(),
        "class_name": cls.group(1) if cls else path.stem,
        "lines": len(lines),
        "methods": methods,
        "frameworks": frameworks,
        "spring_context": bool(_SPRING_CONTEXT_ANNO.search(text)),
        "mockito_ext": bool(_MOCKITO_EXT.search(text)),
        "extends": ext.group(1) if ext else None,
        "gwt": bool(_GWT_COMMENT.search(text)),
        "display_name": bool(_DISPLAY_NAME.search(text)),
        "nested": bool(_NESTED.search(text)),
        "disabled": bool(_DISABLED.search(text)),
        "sleep": bool(_SLEEP.search(text)),
        "has_assertion": bool(_ASSERTION.search(text)),
    }


def _iter_test_files(root: Path):
    """Все *.java под каталогами src/test (любой модуль), минуя build/target/.git и т.п."""
    for path in root.rglob("*.java"):
        parts = path.relative_to(root).parts
        if any(p in SKIP_DIRS for p in parts):
            continue
        joined = "/".join(parts)
        if "src/test/" in joined:
            yield path


def _module_of(rel_posix: str) -> str:
    """Каталог модуля — всё до src/test (пусто для одномодульного проекта → '.')."""
    idx = rel_posix.find("src/test/")
    mod = rel_posix[:idx].rstrip("/")
    return mod or "."


def _score_exemplar(info: dict, dominant_naming: str) -> tuple[int, list[str]] | None:
    """(score, reasons) или None, если файл не годится в эталоны."""
    if info["spring_context"]:
        return None
    if info["disabled"]:
        return None
    if len(info["methods"]) < 2 or not info["has_assertion"]:
        return None
    score, reasons = 0, []
    if info["mockito_ext"]:
        score += 3
        reasons.append("Mockito unit (@ExtendWith(MockitoExtension))")
    if info["frameworks"].get("assertj"):
        score += 1
        reasons.append("AssertJ")
    if info["gwt"]:
        score += 1
        reasons.append("given/when/then")
    if 40 <= info["lines"] <= 400:
        score += 1
        reasons.append("разумный размер")
    if info["display_name"] or info["nested"]:
        score += 1
        reasons.append("@DisplayName/@Nested")
    named = [m for m in info["methods"] if _classify_name(m) == dominant_naming]
    if dominant_naming != "other" and len(named) * 2 >= len(info["methods"]):
        score += 1
        reasons.append(f"имена в доминирующем стиле ({dominant_naming})")
    if info["sleep"]:
        score -= 2
        reasons.append("Thread.sleep (минус)")
    if info["extends"]:
        score -= 2
        reasons.append(f"extends {info['extends']} (возможен транзитивный Spring-контекст, минус)")
    return score, reasons


def analyze(root: Path, max_exemplars: int = 3) -> dict:
    infos = [i for i in (_analyze_file(p, root) for p in sorted(_iter_test_files(root))) if i]
    result: dict = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": str(root),
        "stats": {"test_files": len(infos), "test_methods": 0, "modules": []},
        "frameworks": {},
        "dominant": {},
        "naming": {},
        "structure": {},
        "base_classes": [],
        "exemplars": [],
        "warnings": [],
    }
    if not infos:
        result["warnings"].append("no_tests_found")
        return result

    all_methods = [m for i in infos for m in i["methods"]]
    result["stats"]["test_methods"] = len(all_methods)
    result["stats"]["modules"] = sorted({_module_of(i["path"]) for i in infos})

    fw_counts = {name: sum(1 for i in infos if i["frameworks"][name]) for name in FRAMEWORK_MARKERS}
    result["frameworks"] = fw_counts

    junit = "unknown"
    if fw_counts["junit5"] or fw_counts["junit4"]:
        junit = "junit5" if fw_counts["junit5"] >= fw_counts["junit4"] else "junit4"
    assertions = "unknown"
    if fw_counts["assertj"] or fw_counts["hamcrest"]:
        assertions = "assertj" if fw_counts["assertj"] >= fw_counts["hamcrest"] else "hamcrest"
    elif fw_counts["junit5"] or fw_counts["junit4"]:
        assertions = "junit"
    n = len(infos)
    result["dominant"] = {
        "junit": junit,
        "assertions": assertions,
        "mockito_unit_share": round(sum(1 for i in infos if i["mockito_ext"]) / n, 2),
        "spring_context_share": round(sum(1 for i in infos if i["spring_context"]) / n, 2),
    }

    naming_counts = Counter(_classify_name(m) for m in all_methods)
    dominant_naming = naming_counts.most_common(1)[0][0] if naming_counts else "other"
    result["naming"] = {
        "dominant": dominant_naming,
        "counts": dict(naming_counts),
        "examples": all_methods[:5],
    }
    result["structure"] = {
        "given_when_then_share": round(sum(1 for i in infos if i["gwt"]) / n, 2),
        "display_name_share": round(sum(1 for i in infos if i["display_name"]) / n, 2),
        "nested_used": any(i["nested"] for i in infos),
    }

    spring_classes = {i["class_name"] for i in infos if i["spring_context"]}
    base_counts = Counter(i["extends"] for i in infos if i["extends"])
    result["base_classes"] = [
        {"name": name, "count": cnt, "spring_context": name in spring_classes}
        for name, cnt in base_counts.most_common(10)
    ]

    scored = []
    for info in infos:
        res = _score_exemplar(info, dominant_naming)
        if res is None:
            continue
        score, reasons = res
        scored.append({
            "path": info["path"],
            "score": score,
            "test_methods": len(info["methods"]),
            "reasons": reasons,
        })
    scored.sort(key=lambda e: (-e["score"], e["path"]))
    result["exemplars"] = scored[:max_exemplars]
    if not result["exemplars"]:
        result["warnings"].append("no_exemplars_found")
    return result


def _load_valid_cache(out: Path) -> dict | None:
    if not out.exists():
        return None
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and "schema_version" in data else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Скан тестовой базы -> test-conventions.json")
    parser.add_argument("--root", required=True, help="корень проекта")
    parser.add_argument("--out", default=None, help=f"путь к кэшу (default: <root>/{DEFAULT_OUT})")
    parser.add_argument("--if-missing", action="store_true",
                        help="не пересканировать, если валидный кэш уже есть")
    parser.add_argument("--refresh", action="store_true", help="пересканировать принудительно")
    parser.add_argument("--max-exemplars", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="напечатать полный JSON в stdout")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"FAIL: корень проекта не найден: {root}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else root / DEFAULT_OUT

    if args.if_missing and not args.refresh:
        cached = _load_valid_cache(out)
        if cached is not None:
            print(f"OK (cached): {out}")
            if args.json:
                print(json.dumps(cached, ensure_ascii=False, indent=2))
            return 0

    result = analyze(root, max_exemplars=args.max_exemplars)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"FAIL: не записать кэш {out}: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        s = result["stats"]
        print(f"OK: {out}")
        print(f"  test_files={s['test_files']} test_methods={s['test_methods']} "
              f"modules={len(s['modules'])}")
        if result.get("dominant"):
            d = result["dominant"]
            print(f"  junit={d['junit']} assertions={d['assertions']} "
                  f"mockito_unit_share={d['mockito_unit_share']}")
        for ex in result["exemplars"]:
            print(f"  exemplar: {ex['path']} (score={ex['score']})")
        for w in result["warnings"]:
            print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
