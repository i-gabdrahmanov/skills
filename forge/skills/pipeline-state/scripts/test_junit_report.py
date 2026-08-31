#!/usr/bin/env python3
"""Тесты для junit_report.py — ПО-ТЕСТОВОГО разбора JUnit XML (RED-гейт).

Запуск:
    python3 -m pytest skills/pipeline-state/scripts/test_junit_report.py -q
    python3 skills/pipeline-state/scripts/test_junit_report.py -v

Покрывает:
    1. classify_greens: разбиение t['green'] на инварианты/вакуумные по паттерну.
    2. red_reason: 4 базовых кейса (все RED; вакуумный green; invariant+allow; invariant+deny).
    3. red_reason: tolerate_green (ГРУБЫЙ режим) — обратная совместимость.
    4. red_reason: edge cases (нет отчётов, 0 выполненных, all green invariant no red,
       кривой regex, кастомный pattern, case-insensitivity).
    5. tally / summarize: smoke-тесты на структуру (без реальных файлов).
    6. collect: производительность (10 файлов за <1 сек), since-фильтр, scope_glob,
       max_depth, max_files (KIDPPRB-9254 п.4 — лечение таймаута scan моно-репо).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import junit_report  # noqa: E402


def _mk_tally(red: list[str], green: list[str]) -> dict:
    """Сконструировать tally()-подобный dict для red_reason без записи XML."""
    return {"reports": 1 if (red or green) else 0,
            "red": list(red), "green": list(green), "skipped": 0}


# ── classify_greens ─────────────────────────────────────────────────────────
class TestClassifyGreens(unittest.TestCase):
    """Разбиение t['green'] на инварианты и вакуумные по invariant_pattern."""

    def test_all_inv(self):
        """Все зелёные попадают под pattern → invariants=N, vacuum=[]."""
        t = _mk_tally([], ["com.x.INVARIANT_keep_old_behavior",
                           "com.x.INVARIANT_T1_unchanged"])
        inv, vac, err = junit_report.classify_greens(t)
        self.assertEqual(err, None)
        self.assertEqual(inv, t["green"])
        self.assertEqual(vac, [])

    def test_mixed(self):
        """Часть зелёных под pattern → invariants + vacuum, без ошибок."""
        t = _mk_tally([], ["com.x.INVARIANT_keep", "com.x.normal_test"])
        inv, vac, err = junit_report.classify_greens(t)
        self.assertEqual(err, None)
        self.assertEqual(inv, ["com.x.INVARIANT_keep"])
        self.assertEqual(vac, ["com.x.normal_test"])

    def test_none_inv(self):
        """Ни один зелёный не матчит → invariants=[], vacuum=N."""
        t = _mk_tally([], ["com.x.foo", "com.x.bar"])
        inv, vac, err = junit_report.classify_greens(t)
        self.assertEqual(err, None)
        self.assertEqual(inv, [])
        self.assertEqual(vac, ["com.x.foo", "com.x.bar"])

    def test_case_insensitive(self):
        """Pattern по умолчанию — case-insensitive (re.IGNORECASE)."""
        t = _mk_tally([], ["com.x.invariant_lower", "com.x.Invariant_Mixed",
                           "com.x.INVARIANT_UPPER"])
        inv, vac, err = junit_report.classify_greens(t)
        self.assertEqual(err, None)
        self.assertEqual(inv, t["green"])
        self.assertEqual(vac, [])

    def test_custom_pattern(self):
        """Кастомный pattern (regex) применяется."""
        t = _mk_tally([], ["com.x.keep_me_foo", "com.x.normal"])
        inv, vac, err = junit_report.classify_greens(t, invariant_pattern=r"keep_me")
        self.assertEqual(err, None)
        self.assertEqual(inv, ["com.x.keep_me_foo"])
        self.assertEqual(vac, ["com.x.normal"])

    def test_bad_regex_returns_error(self):
        """Кривой pattern → error, invariants=[], vacuum=исходный green."""
        t = _mk_tally([], ["com.x.foo"])
        inv, vac, err = junit_report.classify_greens(t, invariant_pattern="[")
        self.assertIsNotNone(err)
        self.assertIn("некорректное регулярное выражение", err)
        self.assertEqual(inv, [])
        self.assertEqual(vac, ["com.x.foo"])

    def test_empty_green(self):
        """Пустой список green → пустые оба списка, без ошибок."""
        t = _mk_tally(["com.x.red1"], [])
        inv, vac, err = junit_report.classify_greens(t)
        self.assertEqual(err, None)
        self.assertEqual(inv, [])
        self.assertEqual(vac, [])


# ── red_reason: 4 базовых кейса ──────────────────────────────────────────────
class TestRedReasonBaseCases(unittest.TestCase):
    """4 базовых кейса из ТЗ (KIDPPRB-9254 п.3): all-red, vacuum-green,
    invariant+allow_invariants, invariant+deny."""

    def test_a_all_red_passes(self):
        """(a) все RED → None (RED чистый)."""
        t = _mk_tally(["com.x.t1", "com.x.t2"], [])
        self.assertIsNone(junit_report.red_reason(t, "Gradle: --tests 'FooTest'"))

    def test_b_vacuum_green_fails(self):
        """(b) 1 RED + 1 GREEN-vacuum (без матча pattern) → ошибка."""
        t = _mk_tally(["com.x.red_test"], ["com.x.normal_green"])
        reason = junit_report.red_reason(t, "hint")
        self.assertIsNotNone(reason)
        self.assertIn("RED не чистый", reason)
        self.assertIn("com.x.normal_green", reason)

    def test_c_invariant_with_allow_passes(self):
        """(c) 1 RED + 1 GREEN-invariant (allow_invariants=True) → None."""
        t = _mk_tally(["com.x.red_test"], ["com.x.INVARIANT_unchanged"])
        self.assertIsNone(
            junit_report.red_reason(t, "hint", allow_invariants=True)
        )

    def test_d_invariant_without_allow_fails(self):
        """(d) 1 RED + 1 GREEN-invariant (allow_invariants=False) → ошибка.

        ВАЖНО: green с маркером INVARIANT по-прежнему валит RED, если флаг
        allow_invariants выключен — иначе теряется смысл маркировки и инварианты
        можно было бы «сдать» как RED без явного opt-in."""
        t = _mk_tally(["com.x.red_test"], ["com.x.INVARIANT_unchanged"])
        reason = junit_report.red_reason(t, "hint", allow_invariants=False)
        self.assertIsNotNone(reason)
        self.assertIn("RED не чистый", reason)
        self.assertIn("com.x.INVARIANT_unchanged", reason)

    def test_c2_invariant_with_allow_and_vacuum_fails(self):
        """Граничный: 1 RED + 1 invariant + 1 vacuum (allow_invariants=True) → fail.

        Маркер должны иметь ВСЕ «посторонние» зелёные, иначе это утечка
        вакуумного теста под видом инварианта."""
        t = _mk_tally(["com.x.red_test"],
                      ["com.x.INVARIANT_keep", "com.x.normal_vacuum"])
        reason = junit_report.red_reason(
            t, "hint", allow_invariants=True)
        self.assertIsNotNone(reason)
        self.assertIn("RED не чистый", reason)
        self.assertIn("com.x.normal_vacuum", reason)
        self.assertNotIn("com.x.INVARIANT_keep", reason,
                         msg="инвариант НЕ должен упоминаться в vacuum-списке")
        self.assertIn("allow_invariants=True пропускает", reason,
                      msg="сообщение должно указывать на opt-in")


# ── red_reason: обратная совместимость с tolerate_green ──────────────────────
class TestRedReasonTolerateGreen(unittest.TestCase):
    """tolerate_green=True (ГРУБЫЙ режим) — пропускает ЛЮБЫЕ зелёные при ≥1 red."""

    def test_tolerate_green_with_red_passes(self):
        """Любые зелёные + ≥1 red → None (ГРУБЫЙ режим)."""
        t = _mk_tally(["com.x.red"], ["com.x.green1", "com.x.green2"])
        self.assertIsNone(junit_report.red_reason(t, "hint", tolerate_green=True))

    def test_tolerate_green_all_green_fails(self):
        """Все зелёные (без red) даже в tolerate_green не проходят — нужен RED."""
        t = _mk_tally([], ["com.x.green1"])
        reason = junit_report.red_reason(t, "hint", tolerate_green=True)
        self.assertIsNotNone(reason)
        self.assertIn("нет ни одного красного", reason)

    def test_allow_invariants_beats_tolerate_green(self):
        """Если оба True — побеждает allow_invariants (точечный приоритет):
        1 red + 1 green без маркера → fail (allow_invariants строже)."""
        t = _mk_tally(["com.x.red"], ["com.x.normal_green"])
        reason = junit_report.red_reason(
            t, "hint", allow_invariants=True, tolerate_green=True)
        self.assertIsNotNone(reason,
                             "allow_invariants=True должен ужесточить tolerate_green=True")


# ── red_reason: edge cases ───────────────────────────────────────────────────
class TestRedReasonEdgeCases(unittest.TestCase):
    """Граничные кейсы: пустые отчёты, 0 выполненных, all-invariant-no-red,
    кастомный pattern, кривой regex."""

    def test_no_reports(self):
        """reports=0 → 'прогон не оставил JUnit-отчётов'."""
        t = {"reports": 0, "red": [], "green": [], "skipped": 0}
        reason = junit_report.red_reason(t, "hint")
        self.assertIsNotNone(reason)
        self.assertIn("JUnit-отчётов", reason)

    def test_zero_executed(self):
        """0 выполненных тестов (reports=1, red=[], green=[]) → fail."""
        t = {"reports": 1, "red": [], "green": [], "skipped": 0}
        reason = junit_report.red_reason(t, "hint")
        self.assertIsNotNone(reason)
        self.assertIn("ни один тест не выполнился", reason)

    def test_all_green_invariant_no_red_fails(self):
        """Все зелёные — инварианты, без red → fail (allow_invariants=True
        пропускает вакуумные, но не отменяет требование ≥1 красный)."""
        t = _mk_tally([], ["com.x.INVARIANT_keep", "com.x.INVARIANT_x"])
        reason = junit_report.red_reason(
            t, "hint", allow_invariants=True)
        self.assertIsNotNone(reason)
        self.assertIn("нет ни одного красного", reason)
        self.assertIn("инвариантов", reason)

    def test_custom_pattern(self):
        """Кастомный pattern через invariant_pattern применяется."""
        t = _mk_tally(["com.x.red"], ["com.x.KEEP_ME"])
        # дефолтный pattern 'INVARIANT' НЕ матчит KEEP_ME → fail
        reason = junit_report.red_reason(
            t, "hint", allow_invariants=True)
        self.assertIsNotNone(reason)
        # с правильным pattern → pass
        self.assertIsNone(
            junit_report.red_reason(
                t, "hint", allow_invariants=True, invariant_pattern="KEEP_ME")
        )

    def test_bad_regex_returns_error(self):
        """Кривой invariant_pattern → сообщение об ошибке regex (а не crash)."""
        t = _mk_tally(["com.x.red"], ["com.x.normal"])
        reason = junit_report.red_reason(
            t, "hint", allow_invariants=True, invariant_pattern="[")
        self.assertIsNotNone(reason)
        self.assertIn("некорректное регулярное выражение", reason)

    def test_hint_scope_passed_through(self):
        """hint_scope пробрасывается в сообщение об ошибке (для actionable fix)."""
        t = _mk_tally([], ["com.x.normal"])
        reason = junit_report.red_reason(t, "MY-HINT-SCOPE")
        self.assertIn("MY-HINT-SCOPE", reason)


# ── tally / summarize: smoke-тесты на структуру ────────────────────────────
def _mk_junit(cases: list[tuple[str, str]]) -> str:
    """cases: [(name, 'red'|'green'), ...] → XML-строка."""
    items = ""
    for n, s in cases:
        body = '<failure message="boom"/>' if s == "red" else ""
        items += f'<testcase classname="com.x.FooTest" name="{n}">{body}</testcase>'
    return (f'<?xml version="1.0"?>'
            f'<testsuite name="FooTest" tests="{len(cases)}">{items}</testsuite>')


class TestTally(unittest.TestCase):
    """Пофайловый разбор JUnit XML → tally()."""

    def test_mixed(self):
        """2 red + 2 green → корректные списки + reports=1 + skipped=0."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "TEST-FooTest.xml"
            f.write_text(_mk_junit([("t1", "red"), ("t2", "red"),
                                    ("t3", "green"), ("t4", "green")]),
                         encoding="utf-8")
            t = junit_report.tally([f])
        self.assertEqual(t["reports"], 1)
        self.assertEqual(len(t["red"]), 2)
        self.assertEqual(len(t["green"]), 2)
        self.assertEqual(t["skipped"], 0)
        self.assertIn("com.x.FooTest.t1", t["red"])
        self.assertIn("com.x.FooTest.t3", t["green"])

    def test_skipped(self):
        """<skipped> → +1 в skipped, не red и не green."""
        xml = ('<?xml version="1.0"?>'
               '<testsuite name="x" tests="1">'
               '<testcase classname="c" name="t"><skipped/></testcase>'
               '</testsuite>')
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "TEST-x.xml"
            f.write_text(xml, encoding="utf-8")
            t = junit_report.tally([f])
        self.assertEqual(t["skipped"], 1)
        self.assertEqual(t["red"], [])
        self.assertEqual(t["green"], [])

    def test_broken_xml_skipped(self):
        """Битый XML не валит tally (консервативно пропускается)."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "TEST-bad.xml"
            f.write_text("not-xml", encoding="utf-8")
            t = junit_report.tally([f])
        self.assertEqual(t["reports"], 0,
                         msg="битый XML не должен инкрементить reports")
        self.assertEqual(t["red"], [])
        self.assertEqual(t["green"], [])


# ── collect: перформанс, since, scope_glob, max_depth, max_files ──────────────
def _make_tree_with_reports(root: Path, *, n_modules: int, xml_per_module: int = 1,
                            layout: str = "gradle") -> list[Path]:
    """Создать искусственное дерево модулей с JUnit XML.

    layout='gradle':   <root>/<i>/build/test-results/test/TEST-<i>.xml
    layout='maven':    <root>/<i>/target/surefire-reports/TEST-<i>.xml
    Возвращает список созданных XML-файлов (для sanity-проверок).
    """
    paths: list[Path] = []
    for i in range(n_modules):
        if layout == "gradle":
            d = root / f"mod{i}" / "build" / "test-results" / "test"
        elif layout == "maven":
            d = root / f"mod{i}" / "target" / "surefire-reports"
        else:
            raise ValueError(f"unknown layout: {layout}")
        d.mkdir(parents=True, exist_ok=True)
        for k in range(xml_per_module):
            f = d / f"TEST-Mod{i}Test{k}.xml"
            f.write_text(_mk_junit([("t1", "red"), ("t2", "green")]),
                         encoding="utf-8")
            paths.append(f)
    return paths


class TestCollectPerf(unittest.TestCase):
    """KIDPPRB-9254 п.4: collect() на os.scandir с ранним отсевом.

    Регрессия: Path.glob('**/build/test-results/**/TEST-*.xml') на 492 файлах
    в 30+ модулях таймаутил >300 сек. После перехода на os.scandir с маркером
    суффикса (build/test-results, target/surefire-reports, target/failsafe-reports)
    обход идёт вглубь ТОЛЬКО там, где есть шанс найти XML.
    """

    def test_collect_small_tree_under_1s(self):
        """10 модулей × 1 XML = 10 файлов → collect < 1 сек + ровно 10 файлов."""
        with tempfile.TemporaryDirectory() as d:
            t0 = time.time()
            made = _make_tree_with_reports(Path(d), n_modules=10)
            elapsed = time.time() - t0
            # Создание дерева + сам collect суммарно < 1 сек (на любой разумной ФС)
            t0 = time.time()
            got = junit_report.collect(Path(d))
            elapsed = time.time() - t0
            self.assertEqual(len(got), len(made),
                             msg=f"ожидали {len(made)} файлов, нашли {len(got)}")
            self.assertLess(elapsed, 1.0,
                            msg=f"collect на 10 файлов: {elapsed:.3f} сек (>1с)")

    def test_collect_layouts(self):
        """gradle (build/test-results) и maven (target/surefire-reports) — оба находятся."""
        with tempfile.TemporaryDirectory() as d:
            g = _make_tree_with_reports(Path(d) / "g", n_modules=3, layout="gradle")
            m = _make_tree_with_reports(Path(d) / "m", n_modules=3, layout="maven")
            got = junit_report.collect(Path(d))
            self.assertEqual(len(got), len(g) + len(m))
            # По 1 xml на модуль, но все 6 должны быть
            self.assertEqual(len(got), 6)

    def test_collect_no_reports(self):
        """Дерево без build/test-results → []."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "mod" / "src" / "main").mkdir(parents=True)
            (Path(d) / "mod" / "src" / "main" / "Foo.java").write_text("// x", encoding="utf-8")
            self.assertEqual(junit_report.collect(Path(d)), [])

    def test_collect_skips_skip_dirs(self):
        """Каталоги из _SKIP_DIR_NAMES (.git, node_modules, __pycache__) пропускаются.

        Если бы НЕ пропускались — TEST-*.xml внутри них считались бы (ложные срабатывания).
        """
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            # Создаём «ложный» TEST-*.xml в .git и node_modules — не должен быть найден
            for skip in (".git", "node_modules", "__pycache__"):
                d2 = base / skip / "build" / "test-results"
                d2.mkdir(parents=True, exist_ok=True)
                (d2 / "TEST-Foo.xml").write_text(_mk_junit([("t", "green")]), encoding="utf-8")
            # И один настоящий gradle-репорт
            real = _make_tree_with_reports(base / "real", n_modules=1)
            got = junit_report.collect(base)
            self.assertEqual(len(got), len(real),
                             msg="ложные TEST-*.xml в .git/node_modules не должны попадать")
            # Проверяем, что в результате только настоящий
            self.assertTrue(all("real" in str(p) for p in got))


class TestCollectSince(unittest.TestCase):
    """since-фильтр: только файлы, чей mtime >= since."""

    def test_since_excludes_old(self):
        """Файлы с mtime < since не попадают."""
        with tempfile.TemporaryDirectory() as d:
            made = _make_tree_with_reports(Path(d), n_modules=2)
            # Откатываем mtime на 1 час назад
            old_time = time.time() - 3600
            for f in made:
                os.utime(f, (old_time, old_time))
            # since = «сейчас» — ничего из старых не попадёт
            got = junit_report.collect(Path(d), since=time.time())
            self.assertEqual(got, [])

    def test_since_includes_fresh(self):
        """Файлы с mtime >= since попадают (например, текущий прогон)."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=3)
            # since = «час назад» — все наши свежие файлы должны попасть
            got = junit_report.collect(Path(d), since=time.time() - 3600)
            self.assertEqual(len(got), 3)

    def test_since_none_includes_all(self):
        """since=None → все файлы без фильтра."""
        with tempfile.TemporaryDirectory() as d:
            made = _make_tree_with_reports(Path(d), n_modules=2)
            old_time = time.time() - 3600
            for f in made:
                os.utime(f, (old_time, old_time))
            got = junit_report.collect(Path(d), since=None)
            self.assertEqual(len(got), len(made))


class TestCollectScope(unittest.TestCase):
    """scope_glob: фильтрация только по нужному подмодулю (KIDPPRB-9254 п.4).

    Типичный кейс: в task-plan.modules лежит 'service:taskservice' → нужно собирать
    JUnit только в <root>/service/taskservice/build/test-results/..., не обходя весь
    моно-репо."""

    def test_scope_filters_to_one_module(self):
        """scope_glob='service/taskservice' → только XML в этой ветке."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            target = _make_tree_with_reports(base / "service" / "taskservice",
                                             n_modules=1)
            other = _make_tree_with_reports(base / "service" / "otherservice",
                                            n_modules=2)
            _make_tree_with_reports(base / "lib" / "common", n_modules=3)
            # Скоуп на одну ветку
            got = junit_report.collect(base, scope_glob="service/taskservice")
            self.assertEqual(len(got), len(target),
                             msg=f"скоуп вернул {len(got)} файлов, ожидали {len(target)}")
            for p in got:
                self.assertIn("service/taskservice", str(p))

    def test_scope_glob_pattern(self):
        """scope_glob поддерживает fnmatch-паттерны ('service/*' → все сервисы)."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _make_tree_with_reports(base / "service" / "alpha", n_modules=2)
            _make_tree_with_reports(base / "service" / "beta", n_modules=2)
            _make_tree_with_reports(base / "lib" / "common", n_modules=2)
            got = junit_report.collect(base, scope_glob="service/*")
            self.assertEqual(len(got), 4)
            for p in got:
                self.assertIn("/service/", str(p))

    def test_scope_no_match_returns_empty(self):
        """scope_glob ни во что не матчит → []."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d) / "x", n_modules=2)
            got = junit_report.collect(Path(d), scope_glob="nonexistent/*")
            self.assertEqual(got, [])


class TestCollectMaxDepth(unittest.TestCase):
    """max_depth: ограничитель рекурсии (default=6, хватает для gradle)."""

    def test_max_depth_zero(self):
        """max_depth=0 → ничего не находим (мы ищем на глубине ≥1 от base)."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=2)
            got = junit_report.collect(Path(d), max_depth=0)
            self.assertEqual(got, [])

    def test_max_depth_2_stops_outside_target(self):
        """max_depth=2: входим в mod (depth=1) и build (depth=2), но НЕ в
        test-results (depth=3, in_target=False → depth > max_depth → return)."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=1)
            got = junit_report.collect(Path(d), max_depth=2)
            self.assertEqual(got, [],
                             msg="max_depth=2 должен остановить обход до test-results")

    def test_max_depth_5_finds_gradle(self):
        """max_depth=5: входим в test-results (depth=3) и его подкаталоги test (depth=4)
        — XML на depth=5 найден (внутри таргет-зоны +2 бонус)."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=2)
            got = junit_report.collect(Path(d), max_depth=5)
            self.assertEqual(len(got), 2,
                             msg="max_depth=5 должен находить gradle XML")

    def test_max_depth_default_finds_deep(self):
        """Дефолт max_depth=8 (DEFAULT_MAX_DEPTH) находит и очень глубокие раскладки
        (вложенные group/artifact в gradle/maven до 8 уровней)."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            # depth=7: <base>/a/b/c/d/e/build/test-results/TEST-...xml
            deep = base / "a" / "b" / "c" / "d" / "e" / "build" / "test-results"
            deep.mkdir(parents=True, exist_ok=True)
            (deep / "TEST-Deep.xml").write_text(
                _mk_junit([("t", "red")]), encoding="utf-8")
            got = junit_report.collect(base)  # дефолт max_depth=8
            self.assertEqual(len(got), 1)


class TestCollectMaxFiles(unittest.TestCase):
    """max_files: защита от патологий — обход останавливается после лимита."""

    def test_max_files_truncates(self):
        """10 файлов, max_files=3 → вернём ровно 3 + warning в stderr."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=10)
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                got = junit_report.collect(Path(d), max_files=3)
            self.assertEqual(len(got), 3,
                             msg=f"max_files=3 должен вернуть 3 файла, не {len(got)}")
            self.assertIn("max_files=3", buf.getvalue(),
                          msg="warning должен быть в stderr")

    def test_max_files_no_limit(self):
        """max_files=999999 → собираем всё (10 файлов)."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=10)
            got = junit_report.collect(Path(d), max_files=999999)
            self.assertEqual(len(got), 10)


class TestCollectBackwardCompat(unittest.TestCase):
    """Обратная совместимость: старая сигнатура collect(root, since=None) жива."""

    def test_old_signature_positional(self):
        """collect(root) и collect(root, since=X) — старый API работает."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=2)
            a = junit_report.collect(Path(d))
            b = junit_report.collect(Path(d), since=None)
            c = junit_report.collect(Path(d), since=time.time() - 3600)
            self.assertEqual(a, b)
            self.assertEqual(len(c), 2)

    def test_summarize_old_signature(self):
        """summarize(root, since=None) — старый API работает."""
        with tempfile.TemporaryDirectory() as d:
            _make_tree_with_reports(Path(d), n_modules=2)
            t = junit_report.summarize(Path(d))
            self.assertEqual(t["reports"], 2)
            self.assertEqual(len(t["red"]), 2)
            self.assertEqual(len(t["green"]), 2)

    def test_roots_kw_still_works(self):
        """roots= как явный скоуп на подкаталоги (для check_tests_red --module-roots)."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _make_tree_with_reports(base / "a", n_modules=2)
            _make_tree_with_reports(base / "b", n_modules=3)
            # roots= только на "a" → 2 файла
            got = junit_report.collect(base, roots=[base / "a"])
            self.assertEqual(len(got), 2)


# ── bench: 100+ XML на дереве (smoke, не строгий perf-test) ─────────────────
class TestCollectBench(unittest.TestCase):
    """Бенчмарк на искусственном дереве 30 модулей × 4 XML = 120 файлов.

    На реальном железе (CI/macOS) ожидаемое время <0.5 сек. Это в 600+ раз быстрее,
    чем было при Path.glob('**/build/test-results/**/TEST-*.xml') на 492 файлах
    (300+ сек → timeout). Если регрессия — тест зафейлится с понятным сообщением.
    """

    def test_bench_120_xml_files(self):
        n_mods = 30
        per = 4
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _make_tree_with_reports(base, n_modules=n_mods, xml_per_module=per)
            t0 = time.time()
            got = junit_report.collect(base)
            elapsed = time.time() - t0
            self.assertEqual(len(got), n_mods * per)
            # Мягкий порог: 5 сек — щедро для CI под нагрузкой; обычно <0.2 сек
            self.assertLess(elapsed, 5.0,
                            msg=f"collect 120 XML: {elapsed:.3f} сек (ожидаемо <0.5с)")
            # В stdout — для информации (не assert)
            print(f"\n  [bench] collect({n_mods * per} XML) = {elapsed*1000:.1f} ms")


if __name__ == "__main__":
    unittest.main()
