#!/usr/bin/env python3
"""test_anonymize_pixso.py — обезличивание Pixso DSL и фикстура pixso-modal (задача 1.0).

Пинит: подмена идентификаторов библиотеки ПО ЗНАЧЕНИЮ (publishID == overrideKey, defaultValue
слота-instance — одна и та же подмена, форма сохранена); тексты — «Текст<k>»/«слово», цифры и
знаки на месте, один исходный текст — одна подмена везде (и у слоя-текста, названного по
содержимому); структура (guid, типы, ссылки) не тронута; усечение определений идёт по
достижимости, включая ссылку через publishID слота-instance; результат детерминирован; утечка
исходного значения (в том числе подстрокой) останавливает запись.

Фикстура — обезличенная копия настоящего образца. Проверки «ничего исходного не осталось» и
«воспроизводится побайтно» идут только когда оригинал лежит локально (docs/daiver/dsl.json
не в git) — иначе skip с причиной.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver.pixso import anonymize as A  # noqa: E402
from daiver.pixso import dsl as D  # noqa: E402

REPO = HERE.parents[2]
TOOL = HERE / "tools" / "anonymize_pixso.py"
FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
ORIGINAL = REPO / "docs" / "daiver" / "dsl.json"
FIXTURE_FONT = {"SB Sans Text": "Fixture Sans"}

KEY1 = "05a5ea85a698fd83b3d6cf60ad628231349b4eb4"
KEY2 = "6dabeb3a9d03214e88b1f6b11513ea08ca3889cb"
KEY3 = "f89a633e663f0000000000000000000000000001"
FILE1 = "8abSrJ4ynoUh5tshArUTbw"


def mini() -> dict:
    """Минимальный DSL с формой настоящего: экран без анатомии, определения с идентификаторами."""
    return {"dsl": {
        "converterVersion": "2.2.13", "dslVersion": "2.1.15",
        "localStyleMap": {"2:1": {"name": "Text/B1", "styleType": "TEXT",
                                  "text": {"fontFamily": "Acme Sans", "fontSize": 13}}},
        "sourceMapByUrl": {"Acme SansRegular": {"content": "Acme SansRegular", "type": "font",
                                                "fontKey": {"family": "Acme Sans", "style": "Regular"}}},
        "pixTreeDslNodes": [{
            "guid": "2:10", "type": "FRAME", "name": "Screen", "width": 100, "height": 50,
            "childNode": [{
                "guid": "2:11", "type": "INSTANCE", "name": "Variant=Default", "width": 80, "height": 20,
                "mainComponent": "2:21", "mainStateGroup": "2:20", "componentKey": KEY1,
                "overrideKey": "1500:84011", "publishFile": FILE1,
                "props": [{"componentId": "2:22", "pathString": "2:22", "nodeText": "Привет 42"}]}]}],
        "pixComponentTreeDslNodes": [
            {"guid": "2:20", "type": "SYMBOL", "name": "Card", "publishID": "100:200", "overrideKey": "100:200",
             "componentKey": KEY2, "publishFile": FILE1, "childNode": [
                 {"guid": "2:21", "type": "SYMBOL", "name": "Variant=Default", "childNode": [
                     {"guid": "2:22", "type": "PARAGRAPH", "name": "Title", "nodeText": "Привет 42",
                      "fontFamily": "Acme Sans"},
                     {"guid": "2:23", "type": "PARAGRAPH", "name": "Подпись внизу", "nodeText": "Подпись внизу"},
                     {"guid": "2:24", "type": "PARAGRAPH", "name": "Num", "nodeText": "3"},
                     {"guid": "2:25", "type": "INSTANCE", "name": "Icon", "mainComponent": "2:31",
                      "mainStateGroup": "2:30"}]}]},
            {"guid": "2:30", "type": "SYMBOL", "name": "Icon", "publishID": "300:400", "overrideKey": "300:400",
             "componentKey": KEY3, "publishFile": FILE1, "childNode": [
                 {"guid": "2:31", "type": "SYMBOL", "name": "Size=24",
                  "propDefMap": {"instance_1": {"type": "instance", "key": "instance_1",
                                                "defaultValue": "700:800", "name": "IconReplace"}}}]},
            {"guid": "2:50", "type": "SYMBOL", "name": "Badge", "publishID": "700:800", "overrideKey": "700:800",
             "componentKey": "0" * 40, "publishFile": FILE1},
            {"guid": "2:40", "type": "SYMBOL", "name": "Unused", "publishID": "500:600", "overrideKey": "500:600",
             "componentKey": "1" * 40, "publishFile": FILE1}],
    }}


def body_of(doc):
    return D.unwrap(doc)[0]


def by_guid(doc) -> dict:
    return {n["guid"]: n for n in D.all_nodes(body_of(doc))}


class Identifiers(unittest.TestCase):
    def setUp(self):
        self.src = mini()
        self.out, self.report = A.anonymize(copy.deepcopy(self.src))

    def test_no_original_identifier_survives(self):
        dumped = A.dumps(self.out)
        for v in (KEY1, KEY2, KEY3, FILE1, "1500:84011", "100:200", "300:400", "700:800"):
            self.assertNotIn(v, dumped, v)
        self.assertEqual(self.report["leaks"], [])

    def test_same_value_same_fake_across_fields(self):
        g = by_guid(self.out)
        card = g["2:20"]
        self.assertEqual(card["publishID"], card["overrideKey"], "publishID == overrideKey на узле")
        default = g["2:31"]["propDefMap"]["instance_1"]["defaultValue"]
        self.assertEqual(default, g["2:50"]["publishID"], "defaultValue слота ссылается на подмену publishID")
        self.assertEqual(g["2:11"]["publishFile"], g["2:20"]["publishFile"])

    def test_shape_is_preserved(self):
        g = by_guid(self.out)
        self.assertRegex(g["2:11"]["componentKey"], r"^[0-9a-f]{40}$")
        self.assertRegex(g["2:11"]["publishFile"], r"^[A-Za-z0-9_-]{22}$")
        self.assertRegex(g["2:20"]["publishID"], r"^\d+:\d+$")
        self.assertRegex(g["2:11"]["overrideKey"], r"^\d+:\d+$")

    def test_fakes_never_collide_with_guids_or_each_other(self):
        fakes = []
        for n in D.all_nodes(body_of(self.out)):
            for f in A.ID_FIELDS:
                if f in n:
                    fakes.append(n[f])
        guids = set(by_guid(self.out))
        self.assertFalse(set(fakes) & guids)
        self.assertEqual(len(set(fakes)), len({v for v in fakes}))
        taken = set()
        first = A.fake_id("100:200", "s", taken)
        again = A.fake_id("100:200", "s", {first})
        self.assertNotEqual(first, again, "занятая подмена не повторяется")

    def test_structure_untouched(self):
        unpruned, _ = A.anonymize(copy.deepcopy(self.src), prune=False)
        a, b = by_guid(self.src), by_guid(unpruned)
        self.assertEqual(sorted(a), sorted(b))
        for guid in a:
            for field in ("type", "width", "height", "mainComponent", "mainStateGroup"):
                self.assertEqual(a[guid].get(field), b[guid].get(field), (guid, field))
        self.assertEqual(self.out["dsl"]["pixTreeDslNodes"][0]["childNode"][0]["props"][0]["pathString"], "2:22")
        self.assertEqual(self.out["dsl"]["pixTreeDslNodes"][0]["childNode"][0]["props"][0]["componentId"], "2:22")

    def test_source_document_is_not_mutated(self):
        self.assertEqual(self.src, mini())


class Texts(unittest.TestCase):
    def test_replacement_rules_and_consistency(self):
        out, report = A.anonymize(mini())
        g = by_guid(out)
        title = g["2:22"]["nodeText"]
        self.assertEqual(title, "Текст1 42", "первое слово — «Текст<k>», число осталось")
        self.assertEqual(g["2:11"]["props"][0]["nodeText"], title,
                         "оверрайд совпал с мастером — подмена одна")
        self.assertEqual(g["2:24"]["nodeText"], "3", "число без букв — как есть")
        self.assertEqual(g["2:23"]["nodeText"], "Текст2 слово")
        self.assertEqual(g["2:23"]["name"], "Текст2 слово", "слой-текст, названный по содержимому, — вместе с ним")
        self.assertEqual(g["2:22"]["name"], "Title", "обычное имя слоя не трогается")
        self.assertEqual(report["textsReplaced"], 2)
        self.assertNotIn("Привет", A.dumps(out))
        self.assertNotIn("Подпись", A.dumps(out))

    def test_fake_text_keeps_digits_punctuation_whitespace(self):
        self.assertEqual(A.fake_text("Место хранения 1-105_2-СПБ.1", 5), "Текст5 слово 1-105_2-слово.1")
        self.assertEqual(A.fake_text('Нажмите "Отмена"!\n  Ещё', 2), 'Текст2 "слово"!\n  слово')
        self.assertEqual(A.fake_text("20", 1), "20")

    def test_keep_texts(self):
        out, report = A.anonymize(mini(), keep_texts=True)
        self.assertIn("Привет 42", A.dumps(out))
        self.assertEqual(report["textsReplaced"], 0)

    def test_distinct_originals_get_distinct_fakes(self):
        doc = mini()
        extra = {"guid": "2:99", "type": "PARAGRAPH", "name": "X", "nodeText": "Иное слово"}
        doc["dsl"]["pixComponentTreeDslNodes"][0]["childNode"][0]["childNode"].append(extra)
        out, _ = A.anonymize(doc)
        texts = [n["nodeText"] for n in D.all_nodes(body_of(out)) if "nodeText" in n]
        firsts = {t.split()[0] for t in texts if re.search(r"[^\W\d_]", t)}
        self.assertEqual(len(firsts), 3, texts)


class FontsAndPruning(unittest.TestCase):
    def test_font_rename_in_keys_and_values(self):
        out, report = A.anonymize(mini(), fonts={"Acme Sans": "Fixture Sans"})
        dumped = A.dumps(out)
        self.assertNotIn("Acme Sans", dumped)
        self.assertIn("Fixture SansRegular", out["dsl"]["sourceMapByUrl"], "ключ тоже переименован")
        self.assertEqual(out["dsl"]["sourceMapByUrl"]["Fixture SansRegular"]["fontKey"]["family"], "Fixture Sans")
        self.assertEqual(report["fontsRenamed"], ["Acme Sans"])

    def test_prune_by_reachability_including_instance_slot_default(self):
        out, report = A.anonymize(mini())
        names = [t["name"] for t in out["dsl"]["pixComponentTreeDslNodes"]]
        self.assertEqual(names, ["Card", "Icon", "Badge"], "Unused выброшен, Badge достижим через defaultValue")
        self.assertEqual(report["definitionsDropped"], 1)
        self.assertEqual(report["definitionsKept"], 3)

    def test_no_prune_keeps_everything(self):
        out, report = A.anonymize(mini(), prune=False)
        self.assertEqual(len(out["dsl"]["pixComponentTreeDslNodes"]), 4)
        self.assertEqual(report["definitionsDropped"], 0)

    def test_reachable_helper_order_follows_file(self):
        self.assertEqual(D.reachable_definitions(body_of(mini())), ["2:20", "2:30", "2:50"])


class Determinism(unittest.TestCase):
    def test_same_input_same_bytes_and_salt_changes_fakes(self):
        a1 = A.dumps(A.anonymize(mini())[0])
        a2 = A.dumps(A.anonymize(mini())[0])
        self.assertEqual(a1, a2)
        b = A.dumps(A.anonymize(mini(), salt="другая-соль")[0])
        self.assertNotEqual(a1, b)
        self.assertEqual(len(a1), len(b), "форма значений не зависит от соли")


class Cli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_end_to_end_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "in.json", Path(tmp) / "out" / "dsl.json"
            src.write_text(json.dumps(mini(), ensure_ascii=False), encoding="utf-8")
            r = self._run(str(src), str(dst), "--font", "Acme Sans=Fixture Sans")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["leaks"], [])
            first = dst.read_text(encoding="utf-8")
            D.load(dst)                                   # валидный Pixso DSL
            self._run(str(src), str(dst), "--font", "Acme Sans=Fixture Sans")
            self.assertEqual(dst.read_text(encoding="utf-8"), first, "повторный запуск — те же байты")
            self.assertEqual(self._run(str(src), str(dst), "--font", "без-равно").returncode, 2)
            notpixso = Path(tmp) / "x.json"
            notpixso.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(self._run(str(notpixso), str(dst)).returncode, 2)
            self.assertEqual(self._run(str(Path(tmp) / "нет.json"), str(dst)).returncode, 2)

    def test_leak_stops_the_write(self):
        """Исходное значение внутри чужой строки (подстрока) — не подмена, а утечка: файл не пишется."""
        doc = mini()
        doc["dsl"]["pixTreeDslNodes"][0]["name"] = "см. 100:200 в библиотеке"
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "in.json", Path(tmp) / "out.json"
            src.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            r = self._run(str(src), str(dst))
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertFalse(dst.exists())


class NotPixso(unittest.TestCase):
    def test_rejected(self):
        for bad in ({}, {"dsl": {}}, {"dsl": {"pixTreeDslNodes": []}}, {"dsl": {"pixTreeDslNodes": [{"x": 1}]}},
                    [], "str"):
            with self.assertRaises(D.DslError, msg=repr(bad)):
                D.unwrap(bad)
        self.assertEqual(D.unwrap(mini())[1], True)
        self.assertEqual(D.unwrap(mini()["dsl"])[1], False, "тело без обёртки тоже принимается")


class Fixture(unittest.TestCase):
    """Обезличенная копия настоящего образца в git."""

    def setUp(self):
        self.doc = D.load(FIXTURE)
        self.body = body_of(self.doc)

    def test_fixture_is_valid_pixso_with_resolvable_references(self):
        owner = D.owner_index(self.body)
        screen = list(D.screen_nodes(self.body))
        self.assertEqual(screen[0]["name"], "Modal")
        instances = [n for n in screen if n["type"] == "INSTANCE"]
        self.assertEqual(len(instances), 3)
        for n in instances:
            self.assertIn(n["mainComponent"], owner)
            self.assertIn(n["mainStateGroup"], owner)
        self.assertEqual(len(self.body[D.DEFS_KEY]), 20)
        self.assertEqual(D.reachable_definitions(self.body), [t["guid"] for t in self.body[D.DEFS_KEY]],
                         "в образце все 20 определений достижимы")

    def test_fixture_carries_no_corporate_values(self):
        raw = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("SB Sans", raw)
        self.assertNotRegex(raw, r"Место хранения|Количество документов|Подтвердить|Изменение статуса")
        texts = {n["nodeText"] for n in D.all_nodes(self.body) if "nodeText" in n}
        for t in texts:
            if re.search(r"[^\W\d_]", t):
                self.assertRegex(t, r"^(Текст\d+|\d)", t)

    @unittest.skipUnless(ORIGINAL.is_file(), "оригинал docs/daiver/dsl.json не лежит локально (он не в git)")
    def test_nothing_original_remains_and_fixture_is_reproducible(self):
        original = D.load(ORIGINAL)
        raw = FIXTURE.read_text(encoding="utf-8")
        orig_ids = A._collect_ids(original)
        self.assertGreater(len(orig_ids), 100)
        for v in orig_ids:
            self.assertNotIn(v, raw, f"в фикстуре остался исходный идентификатор {v[:6]}…")
        for t in A._collect_texts(original):
            if re.search(r"[^\W\d_]", t):
                self.assertNotIn(json.dumps(t, ensure_ascii=False)[1:-1], raw)
        regenerated, report = A.anonymize(original, fonts=FIXTURE_FONT)
        self.assertEqual(report["leaks"], [])
        self.assertEqual(A.dumps(regenerated), raw, "фикстура воспроизводится командой из README")


if __name__ == "__main__":
    unittest.main()
