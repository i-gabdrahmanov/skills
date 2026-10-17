#!/usr/bin/env python3
"""test_ds_lookup.py — daiver_ds_inventory / daiver_ds_lookup / daiver_ds_details (задача 2.3; QG U9; INV A1, A2, A4, A5).

Пинит: имя — ключ карты компонентов (`component_map.name_key`), слова имени (разделители, camelCase, буква/цифра);
уровни связи имени (exact > word > part > shared) и разрыв между ними, больший любых кредитов — точное имя всегда
максимум; кредиты (объявленный вариант, принимаемое значение — enum/boolean/usedValues, текстовый слот, истории) только
упорядочивают внутри уровня и ограничены потолком; равные баллы — по имени, перемешанный документ даёт тот же ответ
байт в байт; пустой список — настоящий ответ, а не ошибка; limit 5 по умолчанию, 1..20 с приведением к границе;
details — до 10 компонентов, имена без учёта регистра, неизвестное имя — отказ, deprecations никогда не сокращаются,
потолок размера ответа держит код (ступени сокращения + `cut`); inventory читает коммитнутый документ и не
сканирует библиотеку, не режет список имён; пары нет / она битая / построена для другой библиотеки —
`blocked: library-source-unreachable` у всех трёх инструментов, storybook-mcp — отдельный отказ; поверхность
инструментов (реестр, схемы, ручная проверка аргументов, JSON-RPC).

Данные: настоящая синтетическая фикстура `fixtures/fake-ds/` (собирается 2.2-й сборкой `daiver_ds_requirements` в
каталоге теста) и маленькие построители документов внутри файла. Что НЕ проверено данными: боевой ДС у нас нет —
формулы баллов и пороги (MIN_PART, веса кредитов, бюджеты ответов) выведены из текста спеки §10.3 и названы в
докстринге `daiver/ds_lookup.py`; как слабая модель читает `why` и отказы — проба боевого прогона (X.4).
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

from daiver import component_map as CM  # noqa: E402
from daiver import config as CFG  # noqa: E402
from daiver import ds_lookup as DL  # noqa: E402
from daiver import ds_requirements as DR  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import tools as T  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "fake-ds"
MCP_SERVER = HERE.parents[2] / "hooks" / "daiver_mcp.py"
SHA = "a" * 64


# ── построители ──────────────────────────────────────────────────────────────

def init_repo(root, source: str = "stories", scan_paths=("lib/src", "lib/types")) -> Path:
    root = Path(root)
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}", encoding="utf-8")
    answers: Dict[str, Any] = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
                               "buildCommand": "npm run build", "librarySource": source}
    if source == "storybook-mcp":
        answers["storybookMcp"] = "sb"
    else:
        answers["scanPaths"] = list(scan_paths)
    res = I.init(root, answers)
    assert res["ok"], res
    return root


def prop(kind: str = "string", options: Optional[List[Any]] = None, required: bool = False) -> Dict[str, Any]:
    p: Dict[str, Any] = {"kind": kind, "from": "types"}
    if kind == "enum":
        p["options"] = list(options if options is not None else ["a", "b"])
    if required:
        p["required"] = True
    return p


def cent(name: str, props: Optional[Dict[str, Any]] = None, title: Optional[str] = None, stories: int = 0,
         imp: Any = "auto", used: Optional[Dict[str, List[Any]]] = None, closed: bool = False) -> Dict[str, Any]:
    """Запись компонента контракта (формат 2.2). imp="auto" — именованный экспорт из `@t/ui`."""
    if imp == "auto":
        imp = {"path": "@t/ui", "named": name.split(".")[0]}
    return {"title": title, "taxonomy": [s.strip() for s in title.split("/")[:-1]] if title else [],
            "props": props or {}, "propsClosed": closed, "usedValues": used or {}, "import": imp, "stories": stories}


def dent(description: Optional[str] = None, docs: Optional[str] = None, props: Optional[Dict[str, Any]] = None,
         deprecations: Optional[List[Dict[str, Any]]] = None, examples: Optional[List[Dict[str, str]]] = None
         ) -> Dict[str, Any]:
    return {"description": description, "docs": docs, "props": props or {}, "deprecations": deprecations or [],
            "examples": examples or []}


def dep(target: str, name: str, message: str = "", replacement: Optional[str] = None) -> Dict[str, Any]:
    return {"target": target, "name": name, "message": message, "replacement": replacement}


def docs_of(comps: Dict[str, Any], dets: Optional[Dict[str, Any]] = None, source: str = "stories",
            scan_paths=("lib/src", "lib/types"), sha: str = SHA) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    env = lambda d: {"version": "0.1.0", "role": "ds-requirements", "doc": d, "schema": 1}      # noqa: E731
    contract = {"daiver": env("contract"),
                "source": {"librarySource": source, "scanPaths": list(scan_paths), "scanner": "py/1", "sha256": sha,
                           "files": 1, "skipped": []},
                "components": comps}
    details = {"daiver": env("details"), "source": {"sha256": sha}, "components": dets or {}}
    return contract, details


def write_pair(root, comps: Dict[str, Any], dets: Optional[Dict[str, Any]] = None, order: Optional[List[str]] = None,
               validate: bool = True, **kw) -> None:
    """Пара документов на диск. Построенные документы проходят настоящие валидаторы 2.2 (тест не кормит движок тем, что
    сам сборщик не мог бы написать). `order` — порядок вставки компонентов в файл (проверка «порядок вставки не важен»)."""
    contract, details = docs_of(comps, dets, **kw)
    bad = DR.validate_contract(contract) + DR.validate_details(details) + DR.validate_pair(contract, details)
    assert not bad or not validate, bad
    if order is not None:
        contract["components"] = {n: comps[n] for n in order}
        details["components"] = {n: (dets or {})[n] for n in order if n in (dets or {})}
        texts = [json.dumps(d, ensure_ascii=False, indent=1) + "\n" for d in (contract, details)]
    else:
        texts = [DR.dumps(contract), DR.dumps(details)]
    for rel, text in ((DR.CONTRACT_REL, texts[0]), (DR.DETAILS_REL, texts[1])):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def read_pair(root) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return (json.loads((Path(root) / DR.CONTRACT_REL).read_text(encoding="utf-8")),
            json.loads((Path(root) / DR.DETAILS_REL).read_text(encoding="utf-8")))


def dump(x: Any) -> str:
    """Ответ так, как его печатает MCP-сервер."""
    return json.dumps(x, ensure_ascii=False, indent=1)


class Base(unittest.TestCase):
    """Пустой инициализированный репо (stories) во временном каталоге."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = init_repo(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, comps: Dict[str, Any], dets: Optional[Dict[str, Any]] = None, **kw) -> None:
        write_pair(self.root, comps, dets, **kw)

    def lookup(self, name: str, variants=None, slots=None, limit=None) -> Dict[str, Any]:
        return DL.lookup(self.root, name, variants, slots, limit)

    def names(self, name: str, variants=None, slots=None, limit=None) -> List[str]:
        return [c["name"] for c in self.lookup(name, variants, slots, limit)["candidates"]]

    def refused(self, fn, *args, **kw) -> str:
        with self.assertRaises(DL.DsLookupError) as cm:
            fn(*args, **kw)
        return str(cm.exception)


class FixtureBase(unittest.TestCase):
    """Настоящая фикстура fake-ds, собранная 2.2-й сборкой один раз на класс (тесты только читают)."""
    root: Path

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = init_repo(cls._tmp.name)
        shutil.copytree(FIXTURE, cls.root / "lib")
        res = DR.build(cls.root)
        assert res["ok"] and res["components"] == 10, res

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def lookup(self, name: str, variants=None, slots=None, limit=None) -> Dict[str, Any]:
        return DL.lookup(self.root, name, variants, slots, limit)


def n(name: str) -> "DL._Name":
    return DL._Name(name)


# ── имена и слова ────────────────────────────────────────────────────────────

class Words(unittest.TestCase):
    def test_separators_and_camel_case_split_the_same_way(self):
        for name in ("Modal Header", "modal-header", "ModalHeader", "MODAL_HEADER", "Modal / Header", "modal.header"):
            self.assertEqual(DL.words(name), ("modal", "header"), name)

    def test_acronyms_digits_and_unicode(self):
        self.assertEqual(DL.words("XMLParser"), ("xml", "parser"))
        self.assertEqual(DL.words("Icon24"), ("icon", "24"))
        self.assertEqual(DL.words("Icon 24"), ("icon", "24"))
        self.assertEqual(DL.words("ModalV2Header"), ("modal", "v", "2", "header"))
        self.assertEqual(DL.words("МодальноеОкно"), ("модальное", "окно"))
        self.assertEqual(DL.words("модальное-окно"), ("модальное", "окно"))
        self.assertEqual(DL.words("ＢＵＴＴＯＮ"), ("button",), "полноширинные буквы — NFKC")
        self.assertEqual(DL.words("---"), ())

    def test_words_concatenate_to_the_name_key_of_the_map(self):
        # Слова — разбиение ТОГО ЖЕ ключа, что у карты компонентов (второго ключа имён нет).
        for name in ("Modal Header", "XMLParser", "Icon24", "Stroke / Arrows / Small_Left", "Tech_Icon24", "МодальноеОкно",
                     "Steps=OFF", "a"):
            self.assertEqual("".join(DL.words(name)), CM.name_key(name), name)
            self.assertEqual(n(name).key, CM.name_key(name), name)

    def test_limits_are_the_ones_of_the_map_tools(self):
        self.assertEqual((DL.MAX_NAME, DL.MAX_VARIANTS, DL.MAX_VALUE), (CM.MAX_NAME, CM.MAX_VARIANTS, CM.MAX_ICON))


class Relations(unittest.TestCase):
    def rel(self, a: str, b: str):
        return DL.relate(n(a), n(b))

    def test_exact_ignores_case_and_separators(self):
        for a, b in (("Modal Header", "modal-header"), ("ModalHeader", "MODAL_HEADER"), ("Button", "button")):
            r = self.rel(a, b)
            self.assertEqual((r[0], r[1]), ("exact", DL.EXACT), (a, b))

    def test_whole_word_containment_goes_both_ways(self):
        for a, b in (("Primary Button", "Button"), ("Button", "PrimaryButton"), ("Forms / Button / Primary", "Button"),
                     ("Icon 24", "Icon")):
            r = self.rel(a, b)
            self.assertEqual(r[0], "word", (a, b))
            self.assertTrue(DL.WORD <= r[1] < DL.WORD + 100, r)

    def test_containment_inside_a_word_is_a_weaker_level(self):
        self.assertEqual(self.rel("Tab", "Tabs")[0], "part")
        self.assertEqual(self.rel("Checkbox", "Box")[0], "part")
        self.assertEqual(self.rel("Buttons", "Button")[0], "part", "множественное число — не слово: стеммера нет")
        r = self.rel("Tab", "Table")
        self.assertTrue(DL.PART <= r[1] < DL.PART + 100, r)

    def test_a_longer_overlap_is_closer_inside_the_level(self):
        far = self.rel("Modal Header Title", "Modal")[1]
        near = self.rel("Modal Header Title", "ModalHeader")[1]
        self.assertEqual((self.rel("Modal Header Title", "Modal")[0], self.rel("Modal Header Title", "ModalHeader")[0]),
                         ("word", "word"))
        self.assertGreater(near, far)
        self.assertGreater(self.rel("Tab", "Tabs")[1], self.rel("Tab", "Table")[1])

    def test_shared_words_only(self):
        r = self.rel("Modal Header", "Table Header")
        self.assertEqual((r[0], r[1], r[2]), ("shared", DL.SHARED + 100 * 1 // 3, "header"))
        r = self.rel("Header Modal", "ModalHeader")
        self.assertEqual((r[0], r[1]), ("shared", DL.SHARED + 100),
                         "те же слова в другом порядке — не вхождение (ключи не вложены), а все слова общие")

    def test_no_relation_is_none_not_a_guess(self):
        for a, b in (("Toast", "Alert"), ("Notification", "Alert"), ("Button", "Btn"), ("Tag", "Table"),
                     ("Card", "Modal"), ("---", "Button"), ("Button", "---")):
            self.assertIsNone(self.rel(a, b), (a, b))

    def test_short_parts_and_digits_never_relate_names(self):
        self.assertIsNone(self.rel("UI", "Quiz"), "часть короче MIN_PART знаков не считается вхождением")
        self.assertIsNone(self.rel("Ok", "Token"))
        self.assertIsNone(self.rel("Item 1", "Card 1"), "общая цифра — не общее слово")
        self.assertIsNone(self.rel("Ab Cd", "Ab Ef"), "общее слово короче трёх знаков не в счёт")
        self.assertEqual(self.rel("Box", "Checkbox")[0], "part", "ровно MIN_PART знаков — ещё вхождение")

    def test_levels_are_separated_by_more_than_any_credit(self):
        # Свойство, на котором держится «точное имя — максимум»: потолок уровня + потолок кредитов < основы следующего.
        self.assertLess(DL.SHARED + 100 + DL.CREDIT_CAP, DL.PART)
        self.assertLess(DL.PART + 99 + DL.CREDIT_CAP, DL.WORD)
        self.assertLess(DL.WORD + 99 + DL.CREDIT_CAP, DL.TITLE_EXACT)
        self.assertLess(DL.TITLE_EXACT + DL.CREDIT_CAP, DL.EXACT)
        names = ("Button", "Primary Button", "PrimaryButtonGroup", "Tab", "Tabs", "Table", "Modal Header", "Table Header",
                 "Header", "Icon 24", "Icon24", "Box", "Checkbox", "Modal", "ModalHeader", "XMLParser", "Card", "Tag")
        bounds = {"exact": (DL.EXACT, DL.EXACT), "word": (DL.WORD, DL.WORD + 99), "part": (DL.PART, DL.PART + 99),
                  "shared": (DL.SHARED, DL.SHARED + 100)}
        seen = set()
        for a in names:
            for b in names:
                r = self.rel(a, b)
                if r is None:
                    continue
                seen.add(r[0])
                lo, hi = bounds[r[0]]
                self.assertTrue(lo <= r[1] <= hi, (a, b, r))
        self.assertEqual(seen, set(bounds), "пример покрывает все четыре уровня")

    def test_relation_is_symmetric_in_level(self):
        for a, b in (("Primary Button", "Button"), ("Tab", "Tabs"), ("Modal Header", "Table Header"), ("A B C", "A B C")):
            ra, rb = self.rel(a, b), self.rel(b, a)
            self.assertEqual((ra[0], ra[1]), (rb[0], rb[1]), (a, b))

    def test_title_leaf(self):
        self.assertEqual(DL.title_leaf("Forms/Text Input"), "Text Input")
        self.assertEqual(DL.title_leaf("Button"), "Button")
        self.assertIsNone(DL.title_leaf(None))
        self.assertIsNone(DL.title_leaf("Forms/ "))


# ── кредиты ──────────────────────────────────────────────────────────────────

class Credits(unittest.TestCase):
    def cr(self, props, variants=(), slots=(), used=None, stories=0):
        comp = cent("X", props=props, used=used, stories=stories)
        vs = sorted(variants, key=lambda kv: (CM.name_key(kv[0]), kv[0]))
        return DL.credit(comp, vs, sorted(slots))

    def test_declared_variant_is_a_prop_with_the_same_name_key(self):
        c = self.cr({"size": prop("enum", ["sm", "lg"])}, [("Size", "lg")])
        self.assertEqual(c.accepted, [("size", "lg")])
        self.assertEqual(c.points, DL.W_DECLARED + DL.W_ACCEPTED)
        self.assertEqual(self.cr({"buttonSize": prop("enum", ["lg"])}, [("Button Size", "lg")]).accepted,
                         [("buttonSize", "lg")], "camelCase проп ↔ имя с пробелом")
        self.assertEqual(self.cr({"size": prop()}, [("Variant", "x")]).absent, ["Variant"])

    def test_enum_value_is_accepted_only_from_options_in_the_spelling_of_the_library(self):
        props = {"variant": prop("enum", ["primary", "secondary", "ghost"])}
        c = self.cr(props, [("Variant", "Primary")])
        self.assertEqual(c.accepted, [("variant", "primary")], "значение возвращается в написании ДС")
        c = self.cr(props, [("Variant", "Default")])
        self.assertEqual((c.accepted, c.refused), ([], [("variant", "Default")]))
        self.assertEqual(c.points, DL.W_DECLARED, "проп объявлен — кредит есть, значение не принято — баллов за него нет")
        # без догадок: «Large» не станет «lg», «Disabled» не станет «disabled: true»
        self.assertEqual(self.cr({"size": prop("enum", ["sm", "lg"])}, [("Size", "Large")]).accepted, [])

    def test_used_values_never_extend_an_enum(self):
        # enum — закрытый список: значение, которое истории передали, а options не знает, нарушение, а не принятое
        c = self.cr({"size": prop("enum", ["sm", "lg"])}, [("Size", "huge")], used={"size": ["huge"]})
        self.assertEqual(c.accepted, [])

    def test_boolean_accepts_only_the_literals(self):
        props = {"disabled": prop("boolean")}
        self.assertEqual(self.cr(props, [("Disabled", "true")]).accepted, [("disabled", True)])
        self.assertEqual(self.cr(props, [("Disabled", "FALSE")]).accepted, [("disabled", False)])
        self.assertEqual(self.cr(props, [("Disabled", True)]).accepted, [("disabled", True)])
        self.assertEqual(self.cr(props, [("Disabled", "OFF")]).accepted, [], "ON/OFF — не true/false: смысл не угадывается")
        self.assertEqual(self.cr(props, [("Disabled", "OFF")]).refused, [("disabled", "OFF")])

    def test_other_kinds_accept_what_the_stories_really_passed(self):
        props = {"placement": prop("string")}
        used = {"placement": ["top", "bottom"]}
        self.assertEqual(self.cr(props, [("Placement", "Top")], used=used).accepted, [("placement", "top")])
        self.assertEqual(self.cr(props, [("Placement", "left")], used=used).refused, [("placement", "left")])
        self.assertEqual(self.cr(props, [("Placement", "top")]).refused, [("placement", "top")], "без usedValues принять нечем")

    def test_numbers_compare_as_numbers(self):
        props = {"size": prop("enum", [16, 24])}
        for v in ("24", 24, 24.0):
            self.assertEqual(self.cr(props, [("Size", v)]).accepted, [("size", 24)], repr(v))
        self.assertEqual(self.cr(props, [("Size", "24px")]).accepted, [])

    def test_text_slots_are_string_or_node_props(self):
        props = {"label": prop("string"), "icon": prop("node"), "disabled": prop("boolean"), "size": prop("number")}
        c = self.cr(props, slots=["Label", "Icon", "Disabled", "Size", "Title"])
        self.assertEqual(sorted(c.slots), ["icon", "label"])
        self.assertEqual(sorted(c.no_slot), ["Disabled", "Size", "Title"])
        self.assertEqual(c.points, 2 * DL.W_SLOT)

    def test_stories_bonus_is_small_and_capped(self):
        self.assertEqual(self.cr({}, stories=0).points, 0)
        self.assertEqual(self.cr({}, stories=2).points, 2)
        self.assertEqual(self.cr({}, stories=500).points, DL.DOCS_MAX)
        self.assertLess(DL.DOCS_MAX, DL.W_DECLARED, "документированность — небольшой бонус, меньше одного варианта")

    def test_credits_have_a_ceiling(self):
        props = {f"p{i}": prop("enum", ["v"]) for i in range(30)}
        variants = [(f"P{i}", "v") for i in range(30)]
        c = self.cr(props, variants, slots=["p1"], stories=9)
        self.assertEqual(c.points, DL.CREDIT_CAP)

    def test_two_variant_keys_of_one_prop_count_once(self):
        c = self.cr({"size": prop("enum", ["lg"])}, [("Size", "lg"), ("size", "lg")])
        self.assertEqual(c.points, DL.W_DECLARED + DL.W_ACCEPTED)

    def test_props_with_one_key_resolve_to_the_smaller_name(self):
        idx = DL.prop_index({"ariaLabel": prop(), "aria-label": prop(), "other": prop()})
        self.assertEqual(idx["arialabel"], "aria-label")
        self.assertEqual(DL.prop_index({"---": prop()}), {}, "проп без букв и цифр ключа не имеет")

    def test_a_keyless_variant_never_matches_a_prop(self):
        c = self.cr({"size": prop()}, [("---", "x")])
        self.assertEqual((c.absent, c.points), (["---"], 0))


# ── lookup на настоящей фикстуре ─────────────────────────────────────────────

class FixtureLookup(FixtureBase):
    def test_exact_name_is_the_best_and_here_the_only(self):
        r = self.lookup("Button")
        self.assertEqual([c["name"] for c in r["candidates"]], ["Button"])
        c = r["candidates"][0]
        self.assertEqual((c["match"], c["score"], r["total"], r["limit"]), ("exact", DL.EXACT + 4, 1, 5))
        self.assertEqual(c["why"], "имя совпало · истории: 4")

    def test_case_and_separators_do_not_matter(self):
        for q, want in (("button", "Button"), ("BUTTON", "Button"), ("tool tip", "Tooltip"), ("TOOL-TIP", "Tooltip"),
                        ("Tool_Tip", "Tooltip")):
            r = self.lookup(q)
            self.assertEqual([(c["name"], c["match"]) for c in r["candidates"]], [(want, "exact")], q)

    def test_word_level_gets_credits_for_variants_text_slots_and_stories(self):
        r = self.lookup("Primary Button", {"Variant": "Primary", "Size": "lg", "Foo": "Bar"}, ["Label", "Title"])
        (c,) = r["candidates"]
        want = DL.WORD + 100 * 6 // 13 + 2 * DL.W_DECLARED + 2 * DL.W_ACCEPTED + DL.W_SLOT + 4
        self.assertEqual((c["name"], c["match"], c["score"]), ("Button", "word", want))
        self.assertEqual(c["why"], "слово в имени: button · варианты: принимает size=lg, variant=primary; нет пропа Foo · "
                                   "слоты: label; нет пропа Title · истории: 4")

    def test_a_value_the_component_does_not_accept_earns_no_points(self):
        ok = self.lookup("Primary Button", {"Size": "lg"})["candidates"][0]
        no = self.lookup("Primary Button", {"Size": "Large"})["candidates"][0]
        self.assertEqual(ok["score"] - no["score"], DL.W_ACCEPTED)
        self.assertIn("не принимает size=Large", no["why"])
        self.assertNotIn("принимает size", no["why"].replace("не принимает", ""))

    def test_inside_a_word_is_the_part_level(self):
        r = self.lookup("Tab")
        self.assertEqual([(c["name"], c["match"], c["score"]) for c in r["candidates"]],
                         [("Tabs", "part", DL.PART + 100 * 3 // 4 + 2)])
        self.assertEqual(r["candidates"][0]["why"], "часть имени: «tab» в «tabs» · истории: 2")

    def test_nothing_in_the_library_is_an_answer_not_an_error(self):
        for q in ("Toast", "Notification", "Btn", "---x-"):
            r = self.lookup(q)
            self.assertEqual((r["candidates"], r["total"]), ([], 0), q)
            self.assertIn("local", r["note"])
            self.assertIn("daiver_ds_inventory", r["note"])
        json.dumps(r, ensure_ascii=False)

    def test_import_statement_is_the_contract_one_verbatim(self):
        self.assertEqual(self.lookup("Button")["candidates"][0]["import"], 'import { Button } from "@fakeui/core";')
        self.assertEqual(self.lookup("Icon")["candidates"][0]["import"], 'import { Icon } from "@fakeui/icons";')
        self.assertIsNone(self.lookup("Card")["candidates"][0]["import"], "пути в сторях нет — null, пакет не угадывается")

    def test_has_details_follows_the_details_document(self):
        self.assertTrue(self.lookup("Button")["candidates"][0]["hasDetails"])
        self.assertTrue(self.lookup("Avatar")["candidates"][0]["hasDetails"])
        self.assertFalse(self.lookup("Divider")["candidates"][0]["hasDetails"])

    def test_a_deprecated_component_is_flagged_not_hidden(self):
        r = self.lookup("Legacy")
        c = r["candidates"][0]
        self.assertEqual((c["name"], c["match"], c["deprecated"]), ("Legacy", "exact", "Use `Button` instead."))
        self.assertNotIn("deprecated", self.lookup("Button")["candidates"][0])

    def test_several_word_matches_rank_by_overlap_then_credits(self):
        r = self.lookup("Icon Button Tooltip")
        self.assertEqual([(c["name"], c["match"], c["score"]) for c in r["candidates"]],
                         [("Tooltip", "word", DL.WORD + 100 * 7 // 17 + 2), ("Button", "word", DL.WORD + 100 * 6 // 17 + 4),
                          ("Icon", "word", DL.WORD + 100 * 4 // 17)])

    def test_limit_default_total_and_clamp(self):
        q = "Avatar Badge Button Card Divider Icon Tabs Tag Tooltip"
        r = self.lookup(q)
        self.assertEqual((r["limit"], len(r["candidates"]), r["total"]), (5, 5, 9))
        self.assertNotIn("limitNote", r)
        self.assertEqual(len(self.lookup(q, limit=3)["candidates"]), 3)
        self.assertEqual(self.lookup(q, limit=3)["candidates"], r["candidates"][:3], "limit только отрезает хвост")
        r = self.lookup(q, limit=20)
        self.assertEqual((r["limit"], len(r["candidates"]), r["total"]), (20, 9, 9))
        r = self.lookup(q, limit=99)
        self.assertEqual((r["limit"], len(r["candidates"])), (20, 9))
        self.assertIn("99", r["limitNote"])
        for low in (0, -5):
            r = self.lookup(q, limit=low)
            self.assertEqual((r["limit"], len(r["candidates"])), (1, 1), low)
            self.assertIn("1..20", r["limitNote"])
        self.assertEqual(len(self.lookup(q, limit=5.0)["candidates"]), 5, "5.0 — целое")

    def test_same_call_same_bytes(self):
        a = dump(self.lookup("Primary Button", {"Variant": "Primary", "Size": "lg"}, ["Label"]))
        b = dump(self.lookup("Primary Button", {"Size": "lg", "Variant": "Primary"}, ["Label"]))
        self.assertEqual(a, b, "порядок ключей входа на ответ не влияет")
        self.assertEqual(dump(self.lookup("Tab")), dump(self.lookup("Tab")))


# ── lookup на синтетике ──────────────────────────────────────────────────────

class SyntheticLookup(Base):
    def test_shuffled_documents_give_the_same_answer_to_the_byte(self):
        names = ["Button", "ButtonGroup", "IconButton", "ButtonOne", "ButtonTwo", "Tab", "Tabs", "Table", "TableRow",
                 "Modal", "ModalHeader", "ModalFooter", "Header", "Footer", "Card", "CardHeader", "ToggleButton",
                 "RadioButton", "Link", "LinkButton", "button", "Aa Button", "BbButton"]
        comps = {nm: cent(nm, props={"size": prop("enum", ["sm", "lg"]), "label": prop("string")}, stories=i % 4)
                 for i, nm in enumerate(names)}
        dets = {nm: dent(description=f"About {nm}") for nm in names[::3]}
        queries = [("Button", None, None), ("Primary Button Header", {"Size": "lg"}, ["Label"]), ("Tab", None, None),
                   ("Header", {"Size": "sm"}, None), ("Table Header", None, None)]
        rng = random.Random(7)
        orders = [sorted(names), sorted(names, reverse=True)] + [rng.sample(names, len(names)) for _ in range(4)]
        outputs = []
        for order in orders:
            write_pair(self.root, comps, dets, order=order)
            outputs.append([dump(self.lookup(q, v, s, limit=20)) for q, v, s in queries]
                           + [dump(DL.inventory(self.root)), dump(DL.details(self.root, ["Button", "Tab"]))])
        for out in outputs[1:]:
            self.assertEqual(out, outputs[0])

    def test_equal_scores_break_by_name_never_by_insertion_order(self):
        comps = {"BbButton": cent("BbButton"), "AaButton": cent("AaButton")}
        for order in (["BbButton", "AaButton"], ["AaButton", "BbButton"]):
            write_pair(self.root, comps, order=order)
            r = self.lookup("Button")
            self.assertEqual([c["name"] for c in r["candidates"]], ["AaButton", "BbButton"])
            self.assertEqual(r["candidates"][0]["score"], r["candidates"][1]["score"], "это именно ничья по баллам")

    def test_names_with_one_key_break_by_the_raw_name(self):
        self.put({"button": cent("button"), "Button": cent("Button")}, order=["button", "Button"])
        r = self.lookup("button")
        self.assertEqual([(c["name"], c["match"]) for c in r["candidates"]], [("Button", "exact"), ("button", "exact")])

    def test_exact_name_beats_the_best_credited_neighbour(self):
        rich = {"size": prop("enum", ["lg"]), "variant": prop("enum", ["x"]), "label": prop("string")}
        self.put({"Button": cent("Button"), "PrimaryButton": cent("PrimaryButton", props=rich, stories=9)})
        r = self.lookup("Button", {"Size": "lg", "Variant": "x"}, ["Label"])
        self.assertEqual([c["name"] for c in r["candidates"]], ["Button", "PrimaryButton"])
        top, other = r["candidates"]
        self.assertEqual(top["score"], DL.EXACT)
        self.assertEqual(other["score"], DL.WORD + 100 * 6 // 13 + 2 * DL.W_DECLARED + 2 * DL.W_ACCEPTED + DL.W_SLOT + 5)
        self.assertGreater(top["score"], other["score"])

    def test_credits_order_candidates_inside_one_level(self):
        plain = cent("ButtonOne")
        sized = cent("ButtonTwo", props={"size": prop("enum", ["lg"])})
        self.put({"ButtonOne": plain, "ButtonTwo": sized})
        self.assertEqual(self.names("Button"), ["ButtonOne", "ButtonTwo"], "без кредитов ничья — по имени")
        r = self.lookup("Button", {"Size": "lg"})
        self.assertEqual([c["name"] for c in r["candidates"]], ["ButtonTwo", "ButtonOne"])
        self.assertEqual(r["candidates"][0]["score"] - r["candidates"][1]["score"], DL.W_DECLARED + DL.W_ACCEPTED)

    def test_credits_never_lift_a_weaker_level_over_a_stronger_one(self):
        loaded = {f"p{i}": prop("enum", ["v"]) for i in range(20)}
        self.put({"Tab": cent("Tab"), "TabList": cent("TabList"),
                  "Tabs": cent("Tabs", props=dict(loaded, label=prop("string")), stories=50)})
        variants = {f"P{i}": "v" for i in range(20)}
        r = self.lookup("Tab", variants, ["Label"])
        self.assertEqual([(c["name"], c["match"]) for c in r["candidates"]],
                         [("Tab", "exact"), ("TabList", "word"), ("Tabs", "part")])
        self.assertEqual(r["candidates"][2]["score"], DL.PART + 100 * 3 // 4 + DL.CREDIT_CAP)
        self.assertLess(r["candidates"][2]["score"], r["candidates"][1]["score"])

    def test_variants_and_slots_alone_never_make_a_candidate(self):
        self.put({"Foo": cent("Foo", props={"size": prop("enum", ["lg"]), "label": prop("string")}, stories=9)})
        r = self.lookup("Bar", {"Size": "lg"}, ["Label"])
        self.assertEqual((r["candidates"], r["total"]), ([], 0))

    def test_credit_amounts_are_exactly_the_documented_ones(self):
        props = {"size": prop("enum", ["lg"]), "label": prop("string")}
        self.put({"Button": cent("Button", props=props, stories=3)})
        score = lambda *a: self.lookup("Button", *a)["candidates"][0]["score"]      # noqa: E731
        self.assertEqual(score(), DL.EXACT + 3)
        self.assertEqual(score({"Size": "lg"}), DL.EXACT + 3 + DL.W_DECLARED + DL.W_ACCEPTED)
        self.assertEqual(score({"Size": "Large"}), DL.EXACT + 3 + DL.W_DECLARED)
        self.assertEqual(score({"Foo": "x"}), DL.EXACT + 3)
        self.assertEqual(score(None, ["Label"]), DL.EXACT + 3 + DL.W_SLOT)
        self.assertEqual(score(None, ["Title"]), DL.EXACT + 3)

    def test_title_is_a_second_name_of_the_component(self):
        self.put({"TextField": cent("TextField", title="Forms/Text Input"),
                  "ModalHeader": cent("ModalHeader", title="Dialog/Header"),
                  "Btn": cent("Btn", title="Actions/Button")})
        r = self.lookup("Text Input")
        (c,) = r["candidates"]
        self.assertEqual((c["name"], c["match"], c["score"]), ("TextField", "exact", DL.TITLE_EXACT))
        self.assertEqual(c["why"], "имя совпало (по title «Text Input»)")
        c = self.lookup("TextField")["candidates"][0]
        self.assertEqual((c["match"], c["why"]), ("exact", "имя совпало"), "имя из кода — без пометки title")
        c = self.lookup("Header")["candidates"][0]
        self.assertEqual((c["name"], c["match"], c["why"]), ("ModalHeader", "exact", "имя совпало (по title «Header»)"),
                         "title лучше имени из кода — решает лучшее из двух")
        c = self.lookup("Button")["candidates"][0]
        self.assertEqual((c["name"], c["match"]), ("Btn", "exact"))

    def test_code_name_wins_a_tie_with_its_title(self):
        self.put({"Card": cent("Card", title="Layout/Card"), "Panel": cent("Panel", title="Layout/Card")})
        r = self.lookup("Card")
        self.assertEqual([(c["name"], c["why"]) for c in r["candidates"]],
                         [("Card", "имя совпало"), ("Panel", "имя совпало (по title «Card»)")])

    def test_exact_name_in_code_beats_exact_name_in_a_title_of_another_component(self):
        # title — запасное имя: у другого компонента, даже с кредитами до потолка, оно не обгонит имя в коде
        rich = {f"p{i}": prop("enum", ["v"]) for i in range(20)}
        self.put({"LegacyCard": cent("LegacyCard", title="Legacy/Card", props=rich, stories=50), "Card": cent("Card")})
        r = self.lookup("Card", {f"P{i}": "v" for i in range(20)})
        self.assertEqual([(c["name"], c["match"]) for c in r["candidates"]], [("Card", "exact"), ("LegacyCard", "exact")])
        self.assertEqual(r["candidates"][0]["score"], DL.EXACT)
        self.assertEqual(r["candidates"][1]["score"], DL.TITLE_EXACT + DL.CREDIT_CAP)

    def test_exact_by_title_still_beats_any_word_level(self):
        loaded = {f"p{i}": prop("enum", ["v"]) for i in range(20)}
        self.put({"Panel": cent("Panel", title="Layout/Card"), "PrimaryCard": cent("PrimaryCard", props=loaded, stories=50)})
        r = self.lookup("Card", {f"P{i}": "v" for i in range(20)})
        self.assertEqual([(c["name"], c["match"]) for c in r["candidates"]], [("Panel", "exact"), ("PrimaryCard", "word")])

    def test_why_names_every_part_of_the_score(self):
        props = {f"p{i}": prop("enum", ["v"]) for i in range(6)}
        props.update(label=prop("string"), title=prop("node"), size=prop("enum", ["lg"]))
        self.put({"Header": cent("Header", props=props, stories=2),
                  "TableHeader": cent("TableHeader"), "ModalHeaders": cent("ModalHeaders")})
        got = {x["name"]: x for x in self.lookup("Modal Header")["candidates"]}
        self.assertEqual(got["ModalHeaders"]["why"], "часть имени: «modalheader» в «modalheaders»")
        self.assertEqual(got["Header"]["why"], "слово в имени: header · истории: 2")
        self.assertEqual(got["TableHeader"]["why"], "общие слова: header")
        self.assertEqual({x["match"] for x in got.values()}, {"part", "word", "shared"})
        variants = dict({f"P{i}": "v" for i in range(6)}, Size="huge", Zed=1)
        got = {x["name"]: x for x in self.lookup("Header", variants, ["Label", "Title", "Body"])["candidates"]}
        self.assertEqual(got["Header"]["why"],
                         "имя совпало · варианты: принимает p0=v, p1=v, p2=v, p3=v +2; не принимает size=huge; нет пропа "
                         "Zed · слоты: label, title; нет пропа Body · истории: 2")
        self.assertLessEqual(len(got["Header"]["why"]), DL.WHY_MAX)

    def test_why_has_a_ceiling_whatever_the_node_sends(self):
        self.put({"Header": cent("Header")})
        variants = {f"Key{i}": "x" * 100 for i in range(30)}
        why = self.lookup("Header", variants, [f"Slot{i}" for i in range(30)])["candidates"][0]["why"]
        self.assertLessEqual(len(why), DL.WHY_MAX)

    def test_import_statement_forms(self):
        f = DL.import_statement
        self.assertEqual(f("Foo", {"path": "@t/ui", "named": "Foo"}), 'import { Foo } from "@t/ui";')
        self.assertEqual(f("Foo", {"path": "@t/ui", "default": True}), 'import Foo from "@t/ui";')
        self.assertEqual(f("Foo", {"path": "@t/ui", "named": "Bar"}), 'import { Bar as Foo } from "@t/ui";')
        self.assertEqual(f("Form.Item", {"path": "@t/ui", "named": "Form"}), 'import { Form } from "@t/ui";',
                         "составной компонент импортирует корень")
        self.assertIsNone(f("Foo", None))
        self.assertIsNone(f("Foo", {"path": '@t/"ui', "named": "Foo"}), "путь с кавычкой — не код, а null")
        self.assertIsNone(f("Foo", {"path": "@t/ui\\x", "named": "Foo"}))
        self.assertIsNone(f("Not Ident", {"path": "@t/ui", "named": "Foo"}), "имя не идентификатор — кода не будет")
        self.assertIsNone(f("Foo", {"path": "@t/ui", "named": "not ident"}))
        self.put({"Form.Item": cent("Form.Item"), "Plain": cent("Plain", imp=None),
                  "Def": cent("Def", imp={"path": "@t/ui/def", "default": True})})
        got = {c["name"]: c["import"] for c in self.lookup("Form Item Plain Def")["candidates"]}
        self.assertEqual(got, {"Form.Item": 'import { Form } from "@t/ui";', "Plain": None,
                               "Def": 'import Def from "@t/ui/def";'})

    def test_deprecation_texts(self):
        self.put({"A": cent("A"), "B": cent("B"), "C": cent("C"), "D": cent("D", props={"p": prop()})},
                 {"A": dent(deprecations=[dep("component", "A", "Use `X` instead.", "X")]),
                  "B": dent(deprecations=[dep("component", "B", "", "Y")]),
                  "C": dent(deprecations=[dep("component", "C", "", None)]),
                  "D": dent(deprecations=[dep("prop", "p", "old", None)])})
        got = {nm: self.lookup(nm)["candidates"][0].get("deprecated") for nm in "ABCD"}
        self.assertEqual(got, {"A": "Use `X` instead.", "B": "заменён на Y", "C": "deprecated", "D": None})

    def test_total_counts_everything_related_and_limit_cuts_the_tail(self):
        names = [f"Button{c}" for c in "ABCDEFG"]
        self.put({nm: cent(nm) for nm in names})
        r = self.lookup("Button", limit=3)
        self.assertEqual((r["total"], [c["name"] for c in r["candidates"]]), (7, names[:3]))
        self.assertEqual(self.lookup("Button", limit=20)["total"], 7)


# ── аргументы lookup ─────────────────────────────────────────────────────────

class Arguments(unittest.TestCase):
    """Проверка аргументов идёт ДО чтения библиотеки: пары документов в этих тестах нет вообще."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def bad(self, *args, **kw) -> str:
        with self.assertRaises(DL.DsLookupError) as cm:
            DL.lookup(self.root, *args, **kw)
        return str(cm.exception)

    def test_figma_name(self):
        for v in (None, 5, "", "   ", "x" * 201, "a\nb", "---", ["a"]):
            self.assertIn("figmaName", self.bad(v), repr(v))

    def test_variants(self):
        for v in ("x", [], 5, {"": 1}, {"a": None}, {"a": {"b": 1}}, {"a": [1]}, {"a": "x" * 101},
                  {f"k{i}": "v" for i in range(31)}):
            self.assertIn("variants", self.bad("Button", v), repr(v)[:40])

    def test_text_slot_keys(self):
        for v in ("a", [1], [""], ["a" * 101], [f"k{i}" for i in range(31)], {"a": 1}, ["a\nb"]):
            self.assertIn("textSlotKeys", self.bad("Button", None, v), repr(v)[:40])

    def test_limit_must_be_a_whole_number(self):
        for v in (2.5, True, "5", [], float("nan"), float("inf")):
            self.assertIn("limit", self.bad("Button", None, None, v), repr(v))

    def test_all_problems_come_in_one_message(self):
        msg = self.bad("", {"a": None}, "x", 2.5)
        for part in ("figmaName", "variants", "textSlotKeys", "limit"):
            self.assertIn(part, msg)
        self.assertEqual(msg.count("; "), 3, "четыре нарушения — одно сообщение")

    def test_arguments_are_checked_before_the_library(self):
        # пары нет, конфига нет — а ответ про аргумент, а не про библиотеку
        self.assertNotIn("blocked", self.bad(""))
        self.assertNotIn("инициализирован", self.bad(""))
        with self.assertRaises(DL.DsLookupError) as cm:
            DL.lookup(self.root, "Button")
        self.assertIn("инициализирован", str(cm.exception))

    def test_name_is_stripped_and_values_may_be_numbers_and_booleans(self):
        t = tempfile.TemporaryDirectory()
        try:
            root = init_repo(t.name)
            write_pair(root, {"Button": cent("Button", props={"size": prop("enum", [16]), "flag": prop("boolean")})})
            r = DL.lookup(root, "  Button  ", {"Size": 16, "Flag": True, "Other": 1.5}, ["  Label  "])
            self.assertEqual(r["candidates"][0]["score"], DL.EXACT + 2 * DL.W_DECLARED + 2 * DL.W_ACCEPTED)
            self.assertIn("нет пропа Label", r["candidates"][0]["why"])
        finally:
            t.cleanup()

    def test_input_order_of_variants_and_slots_does_not_change_the_answer(self):
        t = tempfile.TemporaryDirectory()
        try:
            root = init_repo(t.name)
            props = {"size": prop("enum", ["lg"]), "tone": prop("enum", ["a"]), "label": prop("string"), "hint": prop("string")}
            write_pair(root, {"Button": cent("Button", props=props)})
            a = dump(DL.lookup(root, "Button", {"Size": "lg", "Tone": "a", "Zed": 1}, ["Label", "Hint", "Gone"]))
            b = dump(DL.lookup(root, "Button", {"Zed": 1, "Tone": "a", "Size": "lg"}, ["Gone", "Hint", "Label", "Label"]))
            self.assertEqual(a, b)
        finally:
            t.cleanup()


# ── details ──────────────────────────────────────────────────────────────────

def huge_pair(count: int = 1, nprops: int = 60, nopts: int = 40, ndeps: int = 3):
    """Контракт и подробности с огромными данными: проза на десятки КБ, длинные списки options и описания пропов,
    восемь примеров по 600 знаков."""
    comps, dets = {}, {}
    for i in range(count):
        name = f"Huge{i:02d}"
        props = {f"prop{j:02d}": prop("enum", [f"value-{k}" for k in range(nopts)]) for j in range(nprops)}
        comps[name] = cent(name, props=props, title=f"Group/{name}", stories=8,
                           used={f"prop{j:02d}": [f"value-{k}" for k in range(30)] for j in range(5)})
        dets[name] = dent(
            description="A long description sentence. " * 80, docs="Documentation paragraph for the component. " * 1500,
            props={p: {"description": "Describes the prop in a very verbose manner. " * 20, "default": "value-1"}
                   for p in props},
            deprecations=[dep("prop", f"prop{j:02d}", "Do not use this prop any more, it will be removed. " * 8, "other")
                          for j in range(ndeps)],
            examples=[{"name": f"Example{k}", "jsx": f"<{name} " + 'a="b" ' * 95 + "/>"} for k in range(8)])
    return comps, dets


class FixtureDetails(FixtureBase):
    def details(self, names) -> List[Dict[str, Any]]:
        return DL.details(self.root, names)["components"]

    def test_facts_of_one_component(self):
        (e,) = self.details(["Button"])
        self.assertEqual((e["name"], e["title"], e["import"], e["propsClosed"], e["stories"], e["hasDetails"]),
                         ("Button", "Components/Inputs/Button", 'import { Button } from "@fakeui/core";', False, 4, True))
        self.assertEqual(e["description"], "Buttons trigger actions.")
        self.assertEqual(e["props"], {
            "disabled": "boolean; used true",
            "label": 'string; used "Button", "Don\'t click"',
            "size": 'enum: "sm" | "md" | "lg"; default "md"',
            "variant": 'enum: "primary" | "secondary" | "ghost" — Visual style'})
        self.assertEqual(list(e["examples"]), ["Primary", "Big one", "With Icon"])
        self.assertEqual(e["examples"]["Primary"], '<Button label="Don\'t click" variant="primary" />')
        self.assertIn("Use one primary button per view", e["docs"])
        self.assertEqual(e["cut"], "examples 3/4", "из четырёх историй показаны три — и это сказано")
        self.assertNotIn("deprecations", e)

    def test_names_resolve_without_case_and_separators_and_keep_the_order_asked(self):
        got = [e["name"] for e in self.details(["button", "TOOL TIP", "Avatar", "BUTTON"])]
        self.assertEqual(got, ["Button", "Tooltip", "Avatar"], "порядок запроса, повтор схлопнут, имя — как в контракте")

    def test_unknown_name_is_refused_whole_with_a_hint(self):
        with self.assertRaises(DL.DsLookupError) as cm:
            DL.details(self.root, ["Button", "Buton", "Primary Button"])
        msg = str(cm.exception)
        self.assertIn("«Buton» нет в контракте (по имени ничего похожего)", msg)
        self.assertIn("«Primary Button» нет в контракте (похожие по имени: Button)", msg)
        self.assertIn("daiver_ds_inventory", msg)
        self.assertIn("придумывать нельзя", msg)

    def test_at_most_ten_components_a_call(self):
        names = [e["name"] for e in self.details(["Avatar", "Badge", "Button", "Card", "Divider", "Icon", "Legacy", "Tabs",
                                                  "Tag", "Tooltip"])]
        self.assertEqual(len(names), DL.MAX_DETAILS, "ровно десять — можно")
        with self.assertRaises(DL.DsLookupError) as cm:
            DL.details(self.root, names + ["Button"])
        self.assertIn("не больше 10", str(cm.exception))
        self.assertIn("11", str(cm.exception))

    def test_odd_component_lists_are_refused(self):
        for v in ([], "Button", [1], [""], ["x" * 201], None, {"a": 1}, ["a\nb"]):
            with self.assertRaises(DL.DsLookupError) as cm:
                DL.details(self.root, v)
            self.assertIn("components", str(cm.exception), repr(v))

    def test_deprecations_are_in_the_answer_in_full(self):
        (e,) = self.details(["Legacy"])
        self.assertEqual(e["deprecations"], ["component Legacy: Use `Button` instead. [замена: Button]"])
        self.assertEqual(e["props"], {"mode": "number; required"})
        self.assertIsNone(e["import"])

    def test_component_without_a_details_entry_keeps_the_contract_facts(self):
        (e,) = self.details(["Divider"])
        self.assertFalse(e["hasDetails"])
        for key in ("description", "docs", "examples", "cut", "deprecations", "title", "moreProps"):
            self.assertNotIn(key, e)
        self.assertEqual(e["props"], {"inset": "boolean", "orientation": 'enum: "horizontal" | "vertical"'})
        self.assertTrue(e["propsClosed"])

    def test_props_from_types_carry_their_documentation(self):
        (e,) = self.details(["Avatar"])
        self.assertEqual(e["description"], "Round user picture.")
        self.assertEqual(e["props"]["initials"], "string — Fallback initials when there is no image.")
        self.assertEqual(e["props"]["shape"], 'enum: "circle" | "square"')

    def test_same_call_same_bytes(self):
        self.assertEqual(dump(DL.details(self.root, ["Button", "Tabs"])), dump(DL.details(self.root, ["Button", "Tabs"])))


class SyntheticDetails(Base):
    def one(self, name, **kw) -> Dict[str, Any]:
        return DL.details(self.root, [name], **kw)["components"][0]

    def test_prop_lines(self):
        props = {"tone": prop("enum", ["a", "b"], required=True), "count": prop("number"), "onClick": prop("function"),
                 "icon": prop("node"), "flag": prop("boolean"), "old": prop("string"), "ratio": prop("number"),
                 "kinds": prop("array"), "mix": prop("other"), "mystery": prop("unknown")}
        dprops = {"count": {"default": 3, "description": "How many"}, "flag": {"default": False},
                  "ratio": {"default": 0.5}, "old": {"description": "Legacy prop"}}
        self.put({"Box": cent("Box", props=props, used={"count": [1, 2], "old": ["x"]})},
                 {"Box": dent(props=dprops, deprecations=[dep("prop", "old", "Use tone.", "tone")])})
        e = self.one("Box")
        self.assertEqual(e["props"], {
            "tone": 'enum: "a" | "b"; required',
            "count": "number; default 3; used 1, 2 — How many",
            "flag": "boolean; default false",
            "icon": "node (слот)",
            "kinds": "array",
            "mix": "other",
            "mystery": "unknown",
            "old": 'string; used "x"; DEPRECATED → tone — Legacy prop',
            "onClick": "function",
            "ratio": "number; default 0.5"})
        self.assertEqual(list(e["props"])[0], "tone", "required — первыми, дальше по имени")
        self.assertEqual(e["deprecations"], ["prop old: Use tone. [замена: tone]"])

    def test_long_lists_are_cut_with_a_count(self):
        self.put({"Box": cent("Box", props={"v": prop("enum", [f"o{i}" for i in range(30)]), "s": prop("string")},
                              used={"s": [f"u{i}" for i in range(10)]})}, {"Box": dent()})
        e = self.one("Box")
        self.assertTrue(e["props"]["v"].endswith('"o11" | …+18'), e["props"]["v"])
        self.assertEqual(e["props"]["v"].count("|"), 12)
        self.assertEqual(e["props"]["s"], 'string; used "u0", "u1", "u2", "u3", "u4", "u5", …+4')

    def test_same_story_names_get_a_suffix(self):
        self.put({"Box": cent("Box")}, {"Box": dent(examples=[{"name": "Primary", "jsx": "<Box a />"},
                                                               {"name": "Primary", "jsx": "<Box b />"}])})
        self.assertEqual(self.one("Box")["examples"], {"Primary": "<Box a />", "Primary (2)": "<Box b />"})

    def test_a_small_component_is_not_cut(self):
        self.put({"Box": cent("Box", props={"a": prop()})},
                 {"Box": dent(description="One. Two.", docs="# Box\n\nShort prose.", props={"a": {"description": "A"}},
                              examples=[{"name": "E", "jsx": "<Box a />"}])})
        e = self.one("Box")
        self.assertNotIn("cut", e)
        self.assertEqual(e["docs"], "# Box\n\nShort prose.")
        self.assertEqual(e["description"], "One. Two.")

    def test_exact_name_beats_the_same_key(self):
        self.put({"Button": cent("Button", stories=1), "button": cent("button", stories=2)})
        self.assertEqual(self.one("Button")["stories"], 1)
        self.assertEqual(self.one("button")["stories"], 2)

    def test_two_components_with_one_key_are_ambiguous_by_key(self):
        self.put({"Button_": cent("Button_"), "button": cent("button")})
        with self.assertRaises(DL.DsLookupError) as cm:
            DL.details(self.root, ["BUTTON"])
        self.assertIn("подходит к нескольким компонентам (Button_, button)", str(cm.exception))

    def test_huge_component_is_cut_by_code_and_the_cut_is_named(self):
        comps, dets = huge_pair(1)
        self.put(comps, dets)
        e = self.one("Huge00")
        size = DL._size(e)
        self.assertLessEqual(size, DL.DETAILS_PER_COMPONENT)
        for part in ("props ", "examples ", "docs"):
            self.assertIn(part, e["cut"], e["cut"])
        shown = len(e["props"])
        self.assertTrue(e["moreProps"].startswith(f"prop{shown:02d}, prop{shown + 1:02d}"),
                        "хвост пропов назван по именам, с того места, где показ оборвался")
        self.assertLessEqual(len(e["moreProps"]), DL.MORE_PROPS_MAX)
        self.assertNotIn("docs", e)
        self.assertEqual(len(e["deprecations"]), 3, "deprecations ступенями не режутся")
        self.assertTrue(all("Do not use this prop" in d for d in e["deprecations"]))
        full = DL._entry(DL.load(self.root), "Huge00", DL.LEVELS[0])
        self.assertGreater(DL._size(full), DL.DETAILS_PER_COMPONENT, "без сокращения этот компонент не влез бы")

    def test_levels_only_shrink(self):
        comps, dets = huge_pair(1)
        self.put(comps, dets)
        lib = DL.load(self.root)
        sizes = [DL._size(DL._entry(lib, "Huge00", lv)) for lv in DL.LEVELS]
        self.assertEqual(sizes, sorted(sizes, reverse=True), sizes)
        self.assertLess(sizes[-1], sizes[0] // 3)
        self.assertLess(sizes[-1], DL.DETAILS_TOTAL // DL.MAX_DETAILS, "последняя ступень умещается в долю из десяти")

    def test_ten_huge_components_stay_under_the_ceiling(self):
        comps, dets = huge_pair(10)
        self.put(comps, dets)
        r = DL.details(self.root, sorted(comps))
        text = dump(r)
        self.assertLessEqual(len(text), DL.DETAILS_TOTAL, len(text))
        self.assertEqual(len(r["components"]), 10)
        for e in r["components"]:
            self.assertIn("cut", e)
            self.assertEqual(len(e["deprecations"]), 3)

    def test_more_components_in_a_call_means_less_of_each(self):
        comps, dets = huge_pair(10, nprops=12, nopts=6, ndeps=0)
        self.put(comps, dets)
        alone = DL.details(self.root, ["Huge00"])["components"][0]
        crowd = DL.details(self.root, sorted(comps))["components"][0]
        self.assertGreaterEqual(DL._size(alone), DL._size(crowd))
        self.assertLess(DL._size(crowd), DL.DETAILS_TOTAL // 10 + 1)

    def test_deprecation_list_has_a_cap_and_says_so(self):
        many = [dep("prop", f"p{i}", "old", None) for i in range(20)]
        props = {f"p{i}": prop() for i in range(20)}
        self.put({"Box": cent("Box", props=props)}, {"Box": dent(deprecations=many)})
        e = self.one("Box")
        self.assertEqual(len(e["deprecations"]), DL.MAX_DEPRECATIONS + 1)
        self.assertEqual(e["deprecations"][-1], "… и ещё 8")
        self.assertTrue(all("DEPRECATED" in e["props"][f"p{i}"] for i in range(20) if f"p{i}" in e["props"]))


# ── inventory ────────────────────────────────────────────────────────────────

class FixtureInventory(FixtureBase):
    def test_lines_and_counters(self):
        r = DL.inventory(self.root)
        self.assertEqual(r["components"], [
            "Avatar — Round user picture.",
            "Badge (Components)",
            "Button (Components/Inputs) — Buttons trigger actions.",
            "Card (Layout)",
            "Divider",
            "Icon",
            "Legacy [DEPRECATED → Button]",
            "Tabs (Navigation) — Tabs switch between related views. Don't nest them.",
            "Tag",
            "Tooltip (Overlay)"])
        self.assertEqual((r["librarySource"], r["count"], r["stories"], r["withDetails"], r["withoutImport"], r["deprecated"]),
                         ("stories", 10, 12, 8, 5, ["Legacy"]))
        contract, _ = read_pair(self.root)
        self.assertEqual(r["build"], "py/1 " + contract["source"]["sha256"][:12])
        self.assertNotIn("trimmed", r)

    def test_every_component_of_the_contract_is_listed_once(self):
        contract, _ = read_pair(self.root)
        listed = [line.split(" ")[0] for line in DL.inventory(self.root)["components"]]
        self.assertEqual(listed, sorted(contract["components"], key=lambda x: (CM.name_key(x), x)))

    def test_the_answer_is_short_and_the_same_every_time(self):
        a = dump(DL.inventory(self.root))
        self.assertEqual(a, dump(DL.inventory(self.root)))
        self.assertLessEqual(len(a), 1200, "десять компонентов — на один экран")


class SyntheticInventory(Base):
    def lib(self, count: int, desc: str = "", group: str = "Group/", name: str = "Component{:04d}") -> List[str]:
        names = [name.format(i) for i in range(count)]
        comps = {nm: cent(nm, title=f"{group}{nm}" if group else None) for nm in names}
        dets = {nm: dent(description=desc) for nm in names} if desc else {}
        self.put(comps, dets)
        return names

    def test_reads_the_committed_document_there_is_no_library_to_scan(self):
        # в репо вообще нет каталога lib/ (scanPaths смотрят в пустоту): сканировать нечего — ответ из документа
        self.assertFalse((self.root / "lib").exists())
        self.put({"Alpha": cent("Alpha"), "Beta": cent("Beta")}, {"Alpha": dent(description="First.")})
        self.assertEqual(DL.inventory(self.root)["components"], ["Alpha — First.", "Beta"])

    def test_nothing_in_the_three_tools_scans_or_rebuilds(self):
        self.put({"Alpha": cent("Alpha", props={"size": prop("enum", ["lg"])}, stories=1)}, {"Alpha": dent(description="A.")})
        boom = AssertionError("библиотеку читать нельзя")
        with mock.patch.object(DR, "build", side_effect=boom), mock.patch.object(DR, "discover", side_effect=boom), \
                mock.patch.object(DR, "read_entries", side_effect=boom), mock.patch.object(DR, "scan_py", side_effect=boom), \
                mock.patch.object(DR, "check", side_effect=boom), mock.patch.object(DR, "sources_hash", side_effect=boom):
            self.assertEqual(DL.inventory(self.root)["count"], 1)
            self.assertEqual(DL.lookup(self.root, "Alpha", {"Size": "lg"})["candidates"][0]["name"], "Alpha")
            self.assertEqual(DL.details(self.root, ["alpha"])["components"][0]["name"], "Alpha")

    def test_pair_is_read_through_load_pair_and_each_time_anew(self):
        self.put({"Alpha": cent("Alpha")})
        with mock.patch.object(DR, "load_pair", wraps=DR.load_pair) as lp:
            DL.inventory(self.root)
            DL.lookup(self.root, "Alpha")
            DL.details(self.root, ["Alpha"])
        self.assertEqual(lp.call_count, 3)
        self.put({"Alpha": cent("Alpha"), "Gamma": cent("Gamma")})
        self.assertEqual(DL.inventory(self.root)["count"], 2, "правка документа видна сразу: кэша нет")

    def test_description_is_one_short_line(self):
        self.put({"Alpha": cent("Alpha")}, {"Alpha": dent(description="First line.\n\n  Second   line " + "word " * 40)})
        (line,) = DL.inventory(self.root)["components"]
        self.assertNotIn("\n", line)
        body = line.split(" — ", 1)[1]
        self.assertLessEqual(len(body), DL.INVENTORY_DESC)
        self.assertTrue(body.startswith("First line. Second line word"))
        self.assertTrue(body.endswith("…"))

    def test_deprecated_names_are_capped_in_the_header_but_not_in_the_lines(self):
        names = [f"Old{i:02d}" for i in range(25)]
        self.put({nm: cent(nm) for nm in names}, {nm: dent(deprecations=[dep("component", nm, "gone", "New")]) for nm in names})
        r = DL.inventory(self.root)
        self.assertEqual(r["deprecated"], names[:20] + ["… и ещё 5"])
        self.assertTrue(all("[DEPRECATED → New]" in line for line in r["components"]))

    def test_without_a_replacement_the_marker_is_bare(self):
        self.put({"Old": cent("Old")}, {"Old": dent(deprecations=[dep("component", "Old", "gone", None)])})
        self.assertEqual(DL.inventory(self.root)["components"], ["Old [DEPRECATED]"])

    def test_descriptions_go_first_when_the_list_does_not_fit(self):
        names = self.lib(700, desc="Describes the component in a rather wordy way, so that lines are long. " * 2)
        r = DL.inventory(self.root)
        self.assertIn("описания отброшены", r["trimmed"])
        self.assertEqual(r["components"], [f"{nm} (Group)" for nm in names])
        self.assertLessEqual(DL._size(r), DL.INVENTORY_BUDGET)
        self.assertNotIn("names", r)

    def test_the_list_of_names_is_never_cut(self):
        names = self.lib(5000, name="ComponentNumber{:05d}")
        r = DL.inventory(self.root)
        self.assertNotIn("components", r)
        self.assertIn("одни имена", r["trimmed"])
        self.assertEqual(r["names"].split(", "), names, "все пять тысяч имён, по порядку")
        self.assertEqual(r["count"], 5000)

    def test_a_list_that_fits_keeps_its_descriptions(self):
        self.lib(150, desc="Short description.")
        r = DL.inventory(self.root)
        self.assertNotIn("trimmed", r)
        self.assertLessEqual(DL._size(r), DL.INVENTORY_BUDGET)
        self.assertTrue(all(line.endswith("— Short description.") for line in r["components"]))


# ── пары нет / она негодна → blocked ─────────────────────────────────────────

class Unreachable(Base):
    CALLS = (("inventory", lambda root: DL.inventory(root)), ("lookup", lambda root: DL.lookup(root, "Button")),
             ("details", lambda root: DL.details(root, ["Button"])))

    def blocked(self, expect: str) -> List[str]:
        """Все три инструмента отказывают одинаково: `blocked: library-source-unreachable` + причина + чем чинить."""
        out = []
        for label, call in self.CALLS:
            with self.assertRaises(DL.LibraryUnreachable, msg=label) as cm:
                call(self.root)
            msg = str(cm.exception)
            self.assertTrue(msg.startswith("blocked: library-source-unreachable — "), msg)
            self.assertIn(expect, msg, label)
            self.assertIn("daiver_ds_requirements", msg, "назван инструмент, который строит требования")
            self.assertEqual(cm.exception.reason, "library-source-unreachable")
            self.assertIsInstance(cm.exception, DL.DsLookupError)
            out.append(msg)
        return out

    def edit(self, rel: Path, fn) -> None:
        path = self.root / rel
        path.write_text(fn(path.read_text(encoding="utf-8")), encoding="utf-8")

    def test_no_documents_at_all(self):
        msg = self.blocked("требований ДС нет")[0]
        self.assertIn(".daiver/design-system-requirements.json", msg)
        self.assertIn("design-system-requirements.details.json", msg)
        self.assertIn("не начинать", msg)

    def test_only_one_of_the_two_documents(self):
        self.put({"Button": cent("Button")})
        (self.root / DR.DETAILS_REL).unlink()
        self.assertIn("нет .daiver/design-system-requirements.details.json", self.blocked("требований ДС нет")[0])

    def test_not_json(self):
        self.put({"Button": cent("Button")})
        (self.root / DR.CONTRACT_REL).write_text("{ not json", encoding="utf-8")
        self.blocked("не читается как JSON")

    def test_broken_schema(self):
        self.put({"Button": cent("Button")})
        self.edit(DR.CONTRACT_REL, lambda t: t.replace('"propsClosed"', '"propsOpen"'))
        self.blocked("требования ДС не читаются")

    def test_zero_components_is_not_a_contract(self):
        self.put({}, validate=False)
        self.blocked("ноль компонентов")

    def test_documents_built_in_different_runs(self):
        self.put({"Button": cent("Button")})
        self.edit(DR.DETAILS_REL, lambda t: t.replace(SHA, "b" * 64))
        self.blocked("хеши источников")

    def test_details_about_a_component_the_contract_lacks(self):
        self.put({"Button": cent("Button")}, {"Button": dent()})
        self.edit(DR.DETAILS_REL, lambda t: t.replace('"Button"', '"Ghost"'))
        self.blocked("компонента «Ghost» нет в контракте")

    def test_scan_paths_changed_after_the_build(self):
        self.put({"Button": cent("Button")})
        self.edit(CFG.CONFIG_REL, lambda t: t.replace('- "lib/types"', '- "lib/other"'))
        self.assertIn("library.scanPaths изменились", self.blocked("устарели")[0])

    def test_library_source_changed_after_the_build(self):
        self.put({"Button": cent("Button")})
        self.edit(CFG.CONFIG_REL, lambda t: t.replace('source: "stories"', 'source: "sources"'))
        self.assertIn("library.source изменился: stories → sources", self.blocked("устарели")[0])

    def test_a_refusal_never_rebuilds_or_scans(self):
        boom = AssertionError("пересобирать нельзя")
        with mock.patch.object(DR, "build", side_effect=boom), mock.patch.object(DR, "discover", side_effect=boom):
            self.blocked("требований ДС нет")

    def test_project_not_initialized_is_a_plain_refusal(self):
        t = tempfile.TemporaryDirectory()
        try:
            for _, call in self.CALLS:
                with self.assertRaises(DL.DsLookupError) as cm:
                    call(Path(t.name))
                self.assertNotIsInstance(cm.exception, DL.LibraryUnreachable)
                self.assertIn("daiver_init", str(cm.exception))
        finally:
            t.cleanup()

    def test_storybook_mcp_has_no_documents_by_construction(self):
        t = tempfile.TemporaryDirectory()
        try:
            root = init_repo(t.name, "storybook-mcp")
            write_pair(root, {"Button": cent("Button")})          # даже лежащие документы не читаются: источник — сервер
            for _, call in self.CALLS:
                with self.assertRaises(DL.DsLookupError) as cm:
                    call(root)
                msg = str(cm.exception)
                self.assertNotIsInstance(cm.exception, DL.LibraryUnreachable)
                self.assertIn("storybook-mcp", msg)
                self.assertIn("«sb»", msg, "назван сервер, которого надо спросить")
                self.assertNotIn("blocked", msg)
        finally:
            t.cleanup()

    def test_stale_by_library_files_is_not_this_tools_job(self):
        # Хеш-устаревание требует чтения библиотеки: его говорит daiver_ds_requirements(check:true). Эти три
        # инструмента библиотеку не читают и по-прежнему отвечают из документа — разделение труда закреплено.
        shutil.copytree(FIXTURE, self.root / "lib")
        DR.build(self.root)
        story = self.root / "lib" / "src" / "Badge" / "Badge.stories.jsx"
        story.write_text(story.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
        self.assertEqual(DR.check(self.root)["state"], "stale")
        self.assertEqual(DL.inventory(self.root)["count"], 10)
        self.assertEqual(DL.lookup(self.root, "Badge")["candidates"][0]["name"], "Badge")


# ── потолок размера lookup ───────────────────────────────────────────────────

class LookupCeiling(Base):
    def test_lookup_does_not_bloat_the_context(self):
        # 300 компонентов с общим словом, длинный путь импорта, огромное сообщение @deprecated, узел с 30 вариантами и 30
        # слотами: ответ на limit 20 остаётся в рамках — ни проза библиотеки, ни аргументы не раздувают его
        names = [f"Button{i:03d}" for i in range(300)]
        path = "@vendor/design-system/components/buttons/button-with-a-long-path"
        props = {f"p{i}": prop("enum", ["v"]) for i in range(30)}
        props.update({f"s{i}": prop("string") for i in range(30)})
        comps = {nm: cent(nm, props=props, title=f"Group/Sub/{nm}", stories=9, imp={"path": path, "named": nm}) for nm in names}
        dets = {nm: dent(description="x" * 5000, deprecations=[dep("component", nm, "gone " * 1000, "New")]) for nm in names}
        self.put(comps, dets)
        variants = {f"P{i}": "v" * 100 for i in range(30)}
        slots = [f"S{i}" + "k" * 90 for i in range(30)]
        r = self.lookup("Button", variants, slots, limit=20)
        text = dump(r)
        self.assertEqual((len(r["candidates"]), r["total"]), (20, 300))
        self.assertLessEqual(len(text), 12500, len(text))      # ≈ 600 знаков на кандидата в самом тяжёлом из возможных
        self.assertLessEqual(len(text.splitlines()), 20 * 10 + 10)
        for c in r["candidates"]:
            self.assertLessEqual(len(c["why"]), DL.WHY_MAX)
            self.assertLessEqual(len(c["deprecated"]), DL.DEPRECATION_TEXT)

    def test_default_answer_is_a_fraction_of_that(self):
        names = [f"Button{i:03d}" for i in range(40)]
        self.put({nm: cent(nm, stories=3) for nm in names})
        text = dump(self.lookup("Button"))
        self.assertEqual(len(self.lookup("Button")["candidates"]), 5)
        self.assertLessEqual(len(text), 3000, len(text))

    def test_empty_answer_is_tiny(self):
        self.put({"Button": cent("Button")})
        self.assertLessEqual(len(dump(self.lookup("Toast"))), 500)


# ── поверхность инструментов ─────────────────────────────────────────────────

def mcp_session(root: Path, messages: List[dict]) -> List[dict]:
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    r = subprocess.run([sys.executable, "-X", "utf8", str(MCP_SERVER), "--project-root", str(root)],
                       input=data, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def mcp_call(i: int, name: str, args: dict) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


class ToolSurface(Base):
    def call(self, name: str, args: dict) -> Dict[str, Any]:
        return T.call(name, self.root, args)

    def fails(self, name: str, args: dict) -> str:
        with self.assertRaises(T.ToolError) as cm:
            self.call(name, args)
        return str(cm.exception)

    def setUp(self):
        super().setUp()
        self.put({"Button": cent("Button", props={"size": prop("enum", ["sm", "lg"])}, stories=2),
                  "Tabs": cent("Tabs")},
                 {"Button": dent(description="Buttons trigger actions.")})

    def test_registry_and_schemas(self):
        inv, lk, dt = (T.TOOLS[n] for n in ("daiver_ds_inventory", "daiver_ds_lookup", "daiver_ds_details"))
        self.assertEqual(inv["schema"]["properties"], {})
        self.assertEqual(sorted(lk["schema"]["properties"]), ["figmaName", "limit", "textSlotKeys", "variants"])
        self.assertEqual(lk["schema"]["required"], ["figmaName"])
        self.assertEqual(sorted(dt["schema"]["properties"]), ["components"])
        self.assertEqual(dt["schema"]["required"], ["components"])
        for tool in (inv, lk, dt):
            self.assertFalse(tool["schema"]["additionalProperties"])
        # числа схемы — те же, что в движке (§20.4), а не вторая копия
        limit = lk["schema"]["properties"]["limit"]
        self.assertEqual((limit["minimum"], limit["maximum"]), (DL.MIN_LIMIT, DL.MAX_LIMIT))
        self.assertEqual((DL.DEFAULT_LIMIT, DL.MIN_LIMIT, DL.MAX_LIMIT, DL.MAX_DETAILS), (5, 1, 20, 10), "спека §20.4")
        self.assertEqual(dt["schema"]["properties"]["components"]["maxItems"], DL.MAX_DETAILS)
        self.assertNotIn("daiver_plan_sign", T.TOOLS)

    def test_descriptions_carry_the_rules_the_reader_needs(self):
        d = {n: T.TOOLS[n]["description"] for n in ("daiver_ds_inventory", "daiver_ds_lookup", "daiver_ds_details")}
        self.assertIn("ОДИН раз на трек", d["daiver_ds_inventory"])
        self.assertIn("blocked: library-source-unreachable", d["daiver_ds_inventory"])
        self.assertIn("daiver_ds_requirements", d["daiver_ds_inventory"])
        self.assertIn("storybook-mcp", d["daiver_ds_inventory"])
        for word in ("exact", "word", "part", "shared", "ДОСЛОВНО", "candidates:[]", "local", "total", "textSlotKeys"):
            self.assertIn(word, d["daiver_ds_lookup"])
        # диапазоны баллов в описании — из констант движка, а не вторая копия, которая разойдётся
        cap = DL.CREDIT_CAP
        for lo, hi in ((DL.TITLE_EXACT, DL.EXACT + cap), (DL.WORD, DL.WORD + 99 + cap), (DL.PART, DL.PART + 99 + cap),
                       (DL.SHARED, DL.SHARED + 100 + cap)):
            self.assertIn(f"{lo}–{hi}", d["daiver_ds_lookup"])
        self.assertIn(f"{DL.MIN_LIMIT}..{DL.MAX_LIMIT}", d["daiver_ds_lookup"])
        self.assertIn(f"по умолчанию {DL.DEFAULT_LIMIT}", d["daiver_ds_lookup"])
        for word in ("ДОСЛОВНО", "propsClosed", "cut", "deprecations", f"не больше {DL.MAX_DETAILS}"):
            self.assertIn(word, d["daiver_ds_details"])

    def test_three_tools_through_the_registry(self):
        inv = self.call("daiver_ds_inventory", {})
        self.assertEqual(inv["components"], ["Button — Buttons trigger actions.", "Tabs"])
        r = self.call("daiver_ds_lookup", {"figmaName": "button", "variants": {"Size": "lg"}, "limit": 3})
        self.assertEqual((r["candidates"][0]["name"], r["limit"]), ("Button", 3))
        e = self.call("daiver_ds_details", {"components": ["button"]})["components"][0]
        self.assertEqual(e["props"], {"size": 'enum: "sm" | "lg"'})
        for res in (inv, r, e):
            json.dumps(res, ensure_ascii=False)

    def test_bad_arguments_are_tool_errors(self):
        self.assertIn("неизвестный аргумент", self.fails("daiver_ds_inventory", {"bogus": 1}))
        self.assertIn("неизвестный аргумент", self.fails("daiver_ds_lookup", {"figmaName": "A", "bogus": 1}))
        self.assertIn("figmaName", self.fails("daiver_ds_lookup", {}))
        self.assertIn("ожидается string", self.fails("daiver_ds_lookup", {"figmaName": 5}))
        self.assertIn("ожидается object", self.fails("daiver_ds_lookup", {"figmaName": "A", "variants": "x"}))
        self.assertIn("ожидается array", self.fails("daiver_ds_lookup", {"figmaName": "A", "textSlotKeys": "Label"}))
        self.assertIn("ожидается number", self.fails("daiver_ds_lookup", {"figmaName": "A", "limit": "5"}))
        self.assertIn("limit", self.fails("daiver_ds_lookup", {"figmaName": "A", "limit": 2.5}))
        self.assertIn("нет обязательного аргумента «components»", self.fails("daiver_ds_details", {}))
        self.assertIn("ожидается array", self.fails("daiver_ds_details", {"components": "Button"}))
        self.assertIn("не больше 10", self.fails("daiver_ds_details", {"components": [f"C{i}" for i in range(11)]}))
        self.assertIn("«Ghost» нет в контракте", self.fails("daiver_ds_details", {"components": ["Ghost"]}))

    def test_refusals_are_tool_errors_with_the_blocked_reason(self):
        (self.root / DR.CONTRACT_REL).unlink()
        for name, args in (("daiver_ds_inventory", {}), ("daiver_ds_lookup", {"figmaName": "A"}),
                           ("daiver_ds_details", {"components": ["A"]})):
            self.assertTrue(self.fails(name, args).startswith("blocked: library-source-unreachable — "), name)

    def test_empty_lookup_is_a_result_not_a_tool_error(self):
        r = self.call("daiver_ds_lookup", {"figmaName": "Toast"})
        self.assertEqual(r["candidates"], [])

    def test_json_rpc_session(self):
        out = mcp_session(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            mcp_call(2, "daiver_ds_inventory", {}),
            mcp_call(3, "daiver_ds_lookup", {"figmaName": "Button", "variants": {"Size": "lg"}}),
            mcp_call(4, "daiver_ds_details", {"components": ["Button", "Tabs"]}),
            mcp_call(5, "daiver_ds_lookup", {"figmaName": "A", "nope": 1}),
            mcp_call(6, "daiver_ds_details", {"components": ["Ghost"]}),
            mcp_call(7, "daiver_ds_lookup", {"figmaName": "Toast"}),
        ])
        self.assertEqual(len(out), 7)
        tools = {t["name"]: t for t in out[0]["result"]["tools"]}
        for name in ("daiver_ds_inventory", "daiver_ds_lookup", "daiver_ds_details"):
            self.assertIn(name, tools)
        self.assertEqual(tools["daiver_ds_lookup"]["inputSchema"]["required"], ["figmaName"])
        inv = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual(inv["count"], 2)
        lk = json.loads(out[2]["result"]["content"][0]["text"])
        self.assertEqual(lk["candidates"][0]["name"], "Button")
        dt = json.loads(out[3]["result"]["content"][0]["text"])
        self.assertEqual([e["name"] for e in dt["components"]], ["Button", "Tabs"])
        self.assertTrue(out[4]["result"]["isError"])
        self.assertTrue(out[5]["result"]["isError"])
        self.assertTrue(out[5]["result"]["content"][0]["text"].startswith("✗ components: «Ghost» нет в контракте"))
        self.assertFalse(out[6]["result"]["isError"])
        self.assertEqual(json.loads(out[6]["result"]["content"][0]["text"])["candidates"], [])

    def test_json_rpc_blocked_answer(self):
        (self.root / DR.DETAILS_REL).unlink()
        out = mcp_session(self.root, [mcp_call(1, "daiver_ds_inventory", {})])
        self.assertTrue(out[0]["result"]["isError"])
        self.assertTrue(out[0]["result"]["content"][0]["text"].startswith("✗ blocked: library-source-unreachable — "))


if __name__ == "__main__":
    unittest.main()
