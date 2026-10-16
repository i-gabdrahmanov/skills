#!/usr/bin/env python3
"""test_component_map.py — карта компонентов `.daiver/component-map.yaml`: lookup и append (задача 2.1; QG U7, N7).

Пинит: сопоставление имени без учёта регистра и разделителей (имена — настоящие, из образца «Modal»:
«Modal Header», «Modal Action», «Button Function», «Steps=OFF», «Stroke / Arrows / Small_Left»…); все виды
правил спеки §5.2 (точное имя, regex с захватами, many-to-one, local, ignore, icon), в том числе на дословном
примере схемы из спеки; `import.statement` дословно (B17); иконки — из своего пакета и никогда из основного;
«пути нет — null»; строгое чтение (опечатка в карте — громкий отказ, все нарушения одним сообщением);
append: согласие `ui-map-<KEY>` (N7, F9), одно «да» — одно правило, комментарии, порядок и переводы строк
файла сохраняются (вставка текстом, не перезапись), дубликаты и конфликты — явная ошибка, битое правило не
тратит согласие, запись атомарна, результат сверяется с исходной картой; сквозная цепочка с настоящим
record_approval.py; поверхность MCP-инструментов (в том числе по JSON-RPC).

Что НЕ проверено данными: образца боевой карты нет — формы `import` (default / {name} / named ≠ имени),
регистр regex, роль `variants` и `wrapper.nameMap` выведены из текста спеки (допущения названы в докстринге
daiver/component_map.py). Правила в тестах синтетические, но имена узлов — настоящие.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

import forge_events as FE  # noqa: E402
from daiver import component_map as CM  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.ingest import normalize as NZ  # noqa: E402
from daiver.pixso import pull as PP  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
RECORD_APPROVAL = HERE.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
MCP_SERVER = HERE.parents[2] / "hooks" / "daiver_mcp.py"

# Схема карты из спеки §5.2 ДОСЛОВНО (4 пробела отступа, комментарии, flow-коллекции, default: true вместе
# с named): единственный образец формата, который у нас есть.
SPEC_SAMPLE = """\
library:
    name: <имя библиотеки>              # метаданные
    import: "@your/ui"                  # пакет по умолчанию для импорта компонентов
    importPath: "@your/ui"              # легаси-алиас того же
icons:
    import:                             # иконки часто живут в ОТДЕЛЬНОМ пакете!
        path: "@your/icons"
        wrapper:                        # необязательно: set-компонент обёртки
            component: Icon             # <Icon name="..."/> вместо одного компонента на иконку
            nameMap:                    # имя иконки макета → имя в наборе
                chevron-down: ChevronDownSmall
    fallback: ask                       # ask | placeholder — что делать с неотслеженной иконкой
rules:                                  # правила маппинга (список)
    - match: Accordion                  # 1) точное имя (без учёта регистра/разделителей)
      outcome: component
      component: Collapse
      props: { variant: accordion }     # пропы, выведенные из вариантов узла
    - match: "/^Basic(.+)$/"            # 2) regex-правило
      outcome: component
      component: "$1"                   # захват
    - match: [Label, LabelCell]         # 3) many-to-one: список имён
      outcome: component
      component: Label
      import:                           # необязательный per-rule override пакета
        path: "@your/ui/label"
        named: Label
        default: true                   # или default-export через шаблон {name}
    - match: CustomCell                 # 4) local: строим сами (нет в библиотеке)
      outcome: local
      component: CustomCell             # имя локального компонента — необязательно, но
                                        # помогает матчеру верификации связать имена
    - match: FigmaDecoration            # 5) ignore: узел не строится вовсе
      outcome: ignore
    - match: ArrowRight                 # 6) иконка через nameMap/setComponent
      outcome: icon
      icon: ChevronRight
"""

LIB = 'library:\n  import: "@fixture/ui"\n'

# Карта проекта: настоящие имена образца «Modal», все виды правил, комментарии и пустая строка внутри блока.
BASE_MAP = """\
# Карта компонентов (тест): комментарии должны пережить append.
library:
  name: Fixture UI
  import: "@fixture/ui"
icons:
  import:
    path: "@fixture/icons"
  fallback: placeholder

rules:
  # --- шапки и действия ---
  - match: Modal Header
    outcome: component
    component: ModalHeader
    props: { steps: false }

  - match: "/^Technical (.+)$/"
    outcome: component
    component: "Tech$1"
  - match: [Modal Action, Button Function]
    outcome: component
    component: ActionBar
  - match: Divider
    outcome: local
    component: Divider
  - match: Steps=OFF
    outcome: ignore
  - match: Stroke / Arrows / Small_Left
    outcome: icon
    icon: ArrowLeft
  # конец списка правил
"""


def one_rule(*lines: str, head: str = "") -> str:
    """Карта с одним правилом: первая строка — после «- », остальные — под ней."""
    return head + "rules:\n  - " + lines[0] + "\n" + "".join("    " + ln + "\n" for ln in lines[1:])


def sample_definitions() -> List[str]:
    """Различные имена 20 определений образца «Modal» (у двух определений имя «Spinning Loader» одно)."""
    dsl = json.loads(FIXTURE.read_text(encoding="utf-8"))["dsl"]
    return sorted({n["name"] for n in dsl["pixComponentTreeDslNodes"]})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @property
    def map_path(self) -> Path:
        return self.root / ".daiver" / "component-map.yaml"

    def write_map(self, text: str) -> None:
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        self.map_path.write_bytes(text.encode("utf-8"))

    def read_map(self) -> str:
        return self.map_path.read_bytes().decode("utf-8")

    def approve(self, track: str = "STOR-100", quote: str = "да, добавляй правило в карту") -> None:
        """Согласие так, как его пишет record_approval: штамп провенанса ставит журнал."""
        FE.append_approval(self.root, CM.consent_key(track), approved_by="user", reason="тест", evidence=quote)

    def consent(self, track: str = "STOR-100") -> Optional[dict]:
        return FE.approval(self.root, CM.consent_key(track))

    def lookup(self, name: str, variants: Optional[dict] = None) -> Dict[str, Any]:
        return CM.lookup(self.root, name, variants)

    def rules(self) -> List[dict]:
        return [r.as_dict() for r in CM.load(self.root).parsed.rules]

    def append(self, rule: Any, track: str = "STOR-100", dry_run: bool = False) -> Dict[str, Any]:
        return CM.append(self.root, track, rule, dry_run)

    def refused(self, fn, *args, **kw) -> str:
        with self.assertRaises(CM.MapError) as cm:
            fn(*args, **kw)
        return str(cm.exception)


# ── имена ────────────────────────────────────────────────────────────────────

class NameKeys(unittest.TestCase):
    def test_case_and_separators_do_not_matter(self):
        base = CM.name_key("Modal Header")
        self.assertEqual(base, "modalheader")
        for v in ("modal header", "MODAL-HEADER", "modal_header", "ModalHeader", "Modal / Header",
                  "  modal   header  ", "modal.header", "Modal Header"):
            self.assertEqual(CM.name_key(v), base, v)

    def test_different_names_stay_different(self):
        names = ("Modal Header", "Modal Action", "Steps=OFF", "Steps=ON", "Icon24", "Icon16", "Button",
                 "Button Function")
        keys = [CM.name_key(n) for n in names]
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(CM.name_key("Icon 24"), CM.name_key("Icon24"), "цифры значимы, пробел между ними — нет")

    def test_unicode_names(self):
        self.assertEqual(CM.name_key("Модальное окно"), CM.name_key("модальное-окно"))
        self.assertEqual(CM.name_key("МОДАЛЬНОЕ_ОКНО"), CM.name_key("Модальное окно"))
        self.assertEqual(CM.name_key("ＢＵＴＴＯＮ"), CM.name_key("Button"), "полноширинные буквы — NFKC")
        self.assertEqual(CM.name_key("й"), CM.name_key("й"), "«й» разложенная и цельная — одно имя")

    def test_name_without_letters_has_no_key(self):
        for v in ("", "---", " / ", "__", None, 5):
            self.assertEqual(CM.name_key(v), "", repr(v))

    def test_definition_names_of_the_sample_have_distinct_keys(self):
        names = sample_definitions()
        self.assertEqual(len(names), 19, "20 определений образца, у двух общее имя «Spinning Loader»")
        keys = {CM.name_key(n) for n in names}
        self.assertEqual(len(keys), len(names), "настоящие имена не слипаются в один ключ")


# ── lookup: все виды правил (QG U7) ──────────────────────────────────────────

class LookupKinds(Base):
    def setUp(self):
        super().setUp()
        self.write_map(BASE_MAP)

    def test_exact_name(self):
        r = self.lookup("Modal Header")
        self.assertEqual(r, {
            "hit": True, "outcome": "component", "component": "ModalHeader", "props": {"steps": False},
            "import": {"statement": 'import { ModalHeader } from "@fixture/ui";', "path": "@fixture/ui"},
            "rule": {"index": 1, "kind": "name", "match": "Modal Header"}, "confidence": "high"})
        self.assertEqual(list(r), ["hit", "outcome", "component", "props", "import", "rule", "confidence"],
                         "порядок ключей ответа фиксирован")

    def test_exact_name_ignores_case_and_separators(self):
        want = self.lookup("Modal Header")
        for v in ("modal header", "MODAL-HEADER", "modal_header", "ModalHeader", " Modal  Header "):
            self.assertEqual(self.lookup(v), want, v)

    def test_regex_with_capture_keeps_the_case_of_the_name(self):
        r = self.lookup("Technical Button")
        self.assertEqual(r["component"], "TechButton", "захват $1 — как написано в имени макета, не в нижнем регистре")
        self.assertEqual(r["import"]["statement"], 'import { TechButton } from "@fixture/ui";')
        self.assertEqual(r["rule"], {"index": 2, "kind": "regex", "match": "/^Technical (.+)$/"})
        self.assertEqual(self.lookup("Technical Icon")["component"], "TechIcon")

    def test_regex_does_not_hit_without_a_capture_or_in_other_case(self):
        self.assertFalse(self.lookup("Technical")["hit"], "(.+) требует хотя бы один символ")
        self.assertFalse(self.lookup("technical button")["hit"], "regex чувствителен к регистру, пока нет флага i")

    def test_regex_flag_i_makes_it_case_insensitive_but_keeps_capture(self):
        self.write_map(one_rule('match: "/^tech_(.+)$/i"', "outcome: component", 'component: "$1"', head=LIB))
        r = self.lookup("Tech_Icon24")
        self.assertEqual((r["hit"], r["component"]), (True, "Icon24"))
        self.write_map(one_rule('match: "/^tech_(.+)$/"', "outcome: component", 'component: "$1"', head=LIB))
        self.assertFalse(self.lookup("Tech_Icon24")["hit"])

    def test_many_to_one(self):
        a, b = self.lookup("Modal Action"), self.lookup("button-function")
        self.assertEqual(a["component"], "ActionBar")
        self.assertEqual(a, b)
        self.assertEqual(a["rule"], {"index": 3, "kind": "names", "match": ["Modal Action", "Button Function"]})
        self.assertFalse(self.lookup("Modal Actions")["hit"], "соседнее имя правилом не захватывается")

    def test_local_has_no_import(self):
        r = self.lookup("Divider")
        self.assertEqual(r, {"hit": True, "outcome": "local", "component": "Divider", "props": {}, "import": None,
                             "rule": {"index": 4, "kind": "name", "match": "Divider"}, "confidence": "high"})
        self.assertNotIn("importNote", r, "local не производит импорта по построению — это не «пути нет»")

    def test_local_without_a_name(self):
        self.write_map(one_rule("match: Divider", "outcome: local", head=LIB))
        self.assertEqual(self.lookup("Divider")["component"], None)

    def test_ignore(self):
        r = self.lookup("steps = off")
        self.assertEqual(r["outcome"], "ignore")
        self.assertEqual((r["component"], r["props"], r["import"]), (None, {}, None))
        self.assertEqual(r["rule"]["match"], "Steps=OFF")

    def test_icon_without_wrapper_is_its_own_component_from_the_icons_package(self):
        r = self.lookup("Stroke / Arrows / Small_Left")
        self.assertEqual(r, {
            "hit": True, "outcome": "icon", "component": "ArrowLeft", "icon": "ArrowLeft", "props": {},
            "import": {"statement": 'import { ArrowLeft } from "@fixture/icons";', "path": "@fixture/icons"},
            "rule": {"index": 6, "kind": "name", "match": "Stroke / Arrows / Small_Left"}, "confidence": "high"})
        self.assertEqual(self.lookup("stroke-arrows-small-left"), r)

    def test_miss_is_an_answer_not_a_failure(self):
        r = self.lookup("Counter")
        self.assertFalse(r["hit"])
        self.assertIn("ds_lookup", r["note"])
        self.assertNotIn("outcome", r)

    def test_variants_are_accepted_but_do_not_change_the_answer(self):
        plain = self.lookup("Modal Header")
        self.assertEqual(self.lookup("Modal Header", {"Steps": "OFF"}), plain)
        self.assertEqual(self.lookup("Modal Header", {"Steps": "ON", "Size": 2, "Dense": True}), plain)

    def test_answer_is_short(self):
        for name in ("Modal Header", "Modal Action", "Divider", "Steps=OFF", "Technical Button", "Counter"):
            text = json.dumps(self.lookup(name), ensure_ascii=False, indent=1)
            self.assertLessEqual(len(text.splitlines()), 40, name)


class Precedence(Base):
    REGEX_FIRST = LIB + """rules:
  - match: "/^Modal (.+)$/"
    outcome: component
    component: "Modal$1"
  - match: Modal Header
    outcome: component
    component: Header
"""

    def test_exact_name_beats_regex_whatever_the_order(self):
        self.write_map(self.REGEX_FIRST)
        self.assertEqual(self.lookup("Modal Header")["component"], "Header")
        self.assertEqual(self.lookup("modal-header")["component"], "Header")
        self.assertEqual(self.lookup("Modal Action")["component"], "ModalAction", "прочее берёт regex")
        swapped = LIB + """rules:
  - match: Modal Header
    outcome: component
    component: Header
  - match: "/^Modal (.+)$/"
    outcome: component
    component: "Modal$1"
"""
        self.write_map(swapped)
        self.assertEqual(self.lookup("Modal Header")["component"], "Header")
        self.assertEqual(self.lookup("Modal Action")["component"], "ModalAction")

    def test_regex_rules_go_in_file_order(self):
        both = LIB + """rules:
  - match: "/^Tech(.+)$/"
    outcome: component
    component: "First$1"
  - match: "/^T(.+)$/"
    outcome: component
    component: "Second$1"
"""
        self.write_map(both)
        self.assertEqual(self.lookup("TechX")["component"], "FirstX")
        self.assertEqual(self.lookup("TabX")["component"], "SecondabX")
        flipped = LIB + """rules:
  - match: "/^T(.+)$/"
    outcome: component
    component: "Second$1"
  - match: "/^Tech(.+)$/"
    outcome: component
    component: "First$1"
"""
        self.write_map(flipped)
        self.assertEqual(self.lookup("TechX")["component"], "SecondechX", "первый сработавший, не самый точный")


# ── import.statement дословно (INV-B17) ───────────────────────────────────────

class ImportStatements(Base):
    def stmt(self, name: str = "Badge") -> Optional[str]:
        r = self.lookup(name)
        return r["import"]["statement"] if r["import"] else None

    def test_statement_is_the_one_from_the_spec(self):
        self.write_map(one_rule("match: Badge", "outcome: component", "component: Badge",
                                head='library:\n  import: "@your/ui"\n'))
        self.assertEqual(self.stmt(), 'import { Badge } from "@your/ui";')
        self.assertEqual(self.lookup("Badge")["import"]["path"], "@your/ui")

    def test_legacy_importPath_is_the_same_value(self):
        rule = ("match: Badge", "outcome: component", "component: Badge")
        self.write_map(one_rule(*rule, head='library:\n  importPath: "@your/ui"\n'))
        self.assertEqual(self.stmt(), 'import { Badge } from "@your/ui";')
        self.write_map(one_rule(*rule, head='library:\n  import: "@your/ui"\n  importPath: "@your/ui"\n'))
        self.assertEqual(self.stmt(), 'import { Badge } from "@your/ui";')
        self.write_map(one_rule(*rule, head='library:\n  import: "@your/ui"\n  importPath: "@other/ui"\n'))
        self.assertIn("расходятся", self.refused(self.lookup, "Badge"))

    def test_rule_import_overrides_the_package(self):
        self.write_map(one_rule("match: Tag", "outcome: component", "component: Tag", "import:",
                                '  path: "@fixture/ui/tag"', head=LIB))
        self.assertEqual(self.stmt("Tag"), 'import { Tag } from "@fixture/ui/tag";')

    def test_named_export_is_aliased_to_the_component_name(self):
        self.write_map(one_rule("match: Tag", "outcome: component", "component: Tag", "import:",
                                '  path: "@fixture/ui/tag"', "  named: TagBase", head=LIB))
        self.assertEqual(self.stmt("Tag"), 'import { TagBase as Tag } from "@fixture/ui/tag";')

    def test_default_export(self):
        self.write_map(one_rule("match: Label", "outcome: component", "component: Label", "import:",
                                '  path: "@fixture/ui/label"', "  default: true", head=LIB))
        self.assertEqual(self.stmt("Label"), 'import Label from "@fixture/ui/label";')

    def test_name_template_in_the_path_and_captures(self):
        self.write_map(one_rule('match: "/^Lazy(.+)$/"', "outcome: component", 'component: "$1"',
                                "props: { label: \"$1\", lazy: true }", "import:",
                                '  path: "@fixture/lazy/{name}"', "  default: true", head=LIB))
        r = self.lookup("LazyChart")
        self.assertEqual(r["component"], "Chart")
        self.assertEqual(r["props"], {"label": "Chart", "lazy": True})
        self.assertEqual(r["import"], {"statement": 'import Chart from "@fixture/lazy/Chart";',
                                       "path": "@fixture/lazy/Chart"})

    def test_compound_component_imports_its_root(self):
        self.write_map(one_rule("match: Form Item", "outcome: component", "component: Form.Item", head=LIB))
        r = self.lookup("form-item")
        self.assertEqual(r["component"], "Form.Item")
        self.assertEqual(r["import"]["statement"], 'import { Form } from "@fixture/ui";')

    def test_override_without_a_path_uses_the_library_path(self):
        self.write_map(one_rule("match: Tag", "outcome: component", "component: Tag", "import:",
                                "  named: TagBase", head=LIB))
        self.assertEqual(self.stmt("Tag"), 'import { TagBase as Tag } from "@fixture/ui";')

    def test_no_path_anywhere_means_null_and_a_note_not_a_guess(self):
        self.write_map(one_rule("match: Badge", "outcome: component", "component: Badge"))
        r = self.lookup("Badge")
        self.assertIsNone(r["import"])
        self.assertIn("не угадывается", r["importNote"])
        self.assertEqual((r["hit"], r["component"]), (True, "Badge"), "вердикт есть, нет только пути")
        self.write_map("library:\n  import: null\nrules:\n  - match: Badge\n    outcome: component\n"
                       "    component: Badge\n")
        self.assertIsNone(self.lookup("Badge")["import"])

    def test_the_icons_package_is_not_a_fallback_for_components(self):
        self.write_map(one_rule("match: Badge", "outcome: component", "component: Badge",
                                head='icons:\n  import:\n    path: "@your/icons"\n'))
        r = self.lookup("Badge")
        self.assertIsNone(r["import"], "пакеты не подменяют друг друга ни в какую сторону")
        self.assertIn("не угадывается", r["importNote"])

    def test_local_and_ignore_never_produce_an_import(self):
        self.write_map(LIB + "rules:\n  - match: Divider\n    outcome: local\n    component: Divider\n"
                             "  - match: Steps=OFF\n    outcome: ignore\n")
        for name in ("Divider", "Steps=OFF"):
            r = self.lookup(name)
            self.assertIsNone(r["import"], name)
            self.assertNotIn("importNote", r, name)

    def test_relative_and_odd_paths_are_refused(self):
        for path in ("./Tag", "../ui/Tag", "/abs/Tag", "bad path", "a;b"):
            self.write_map(one_rule("match: Tag", "outcome: component", "component: Tag", "import:",
                                    f'  path: "{path}"', head=LIB))
            self.assertIn("import.path", self.refused(self.lookup, "Tag"), path)


# ── иконки: свой пакет, обёртка, nameMap ─────────────────────────────────────

class Icons(Base):
    WRAPPER = """library:
  import: "@your/ui"
icons:
  import:
    path: "@your/icons"
    wrapper:
      component: Icon
      nameMap:
        chevron-down: ChevronDownSmall
"""

    def test_icon_comes_from_the_icons_package_never_from_the_main_one(self):
        rule = ("match: ArrowRight", "outcome: icon", "icon: ChevronRight")
        self.write_map(one_rule(*rule, head='library:\n  import: "@your/ui"\nicons:\n  import:\n'
                                            '    path: "@your/icons"\n'))
        r = self.lookup("ArrowRight")
        self.assertEqual(r["import"], {"statement": 'import { ChevronRight } from "@your/icons";',
                                       "path": "@your/icons"})
        self.write_map(one_rule(*rule, head='library:\n  import: "@your/ui"\n'))
        r = self.lookup("ArrowRight")
        self.assertIsNone(r["import"], "пути иконок нет — основной пакет НЕ подставляется")
        self.assertIn("основной", r["importNote"])
        self.assertEqual(r["component"], "ChevronRight")

    def test_wrapper_renders_icon_by_name(self):
        self.write_map(self.WRAPPER + "rules:\n  - match: ArrowRight\n    outcome: icon\n"
                                      "    icon: ChevronRight\n    props: { size: 24 }\n")
        r = self.lookup("arrow-right")
        self.assertEqual((r["outcome"], r["component"], r["icon"]), ("icon", "Icon", "ChevronRight"))
        self.assertEqual(r["props"], {"name": "ChevronRight", "size": 24})
        self.assertEqual(r["import"], {"statement": 'import { Icon } from "@your/icons";', "path": "@your/icons"})

    def test_wrapper_without_icons_path_has_no_import(self):
        self.write_map('library:\n  import: "@your/ui"\nicons:\n  import:\n    wrapper:\n      component: Icon\n'
                       "rules:\n  - match: ArrowRight\n    outcome: icon\n    icon: ChevronRight\n")
        r = self.lookup("ArrowRight")
        self.assertEqual((r["component"], r["import"]), ("Icon", None))
        self.assertIn("icons.import.path", r["importNote"])

    def test_name_map_serves_names_without_a_rule(self):
        self.write_map(self.WRAPPER)
        want = {"hit": True, "outcome": "icon", "component": "Icon", "icon": "ChevronDownSmall",
                "props": {"name": "ChevronDownSmall"},
                "import": {"statement": 'import { Icon } from "@your/icons";', "path": "@your/icons"},
                "rule": {"kind": "nameMap", "match": "chevron-down"}, "confidence": "high"}
        for v in ("chevron-down", "Chevron Down", "ChevronDown", "CHEVRON_DOWN"):
            self.assertEqual(self.lookup(v), want, v)
        self.assertFalse(self.lookup("chevron-up")["hit"])

    def test_explicit_rule_beats_name_map(self):
        self.write_map(self.WRAPPER + "rules:\n  - match: Chevron Down\n    outcome: icon\n"
                                      "    icon: ChevronDown\n")
        r = self.lookup("chevron-down")
        self.assertEqual((r["icon"], r["rule"]["kind"]), ("ChevronDown", "name"))

    def test_captured_icon_name(self):
        self.write_map(self.WRAPPER + 'rules:\n  - match: "/^Icon\\\\/(.+)$/"\n    outcome: icon\n'
                                      '    icon: "$1"\n')
        self.assertEqual(self.lookup("Icon/Trash")["icon"], "Trash")

    def test_icon_name_without_wrapper_must_be_a_component(self):
        self.write_map(LIB + "rules:\n  - match: ArrowRight\n    outcome: icon\n    icon: chevron-right\n")
        self.assertIn("icon", self.refused(self.lookup, "ArrowRight"))

    def test_fallback_is_read_and_defaults_to_ask(self):
        self.assertEqual(CM.parse_text(BASE_MAP).icons_fallback, "placeholder")
        self.assertEqual(CM.parse_text(SPEC_SAMPLE).icons_fallback, "ask")
        self.assertEqual(CM.parse_text("rules: []\n").icons_fallback, "ask")


# ── пример схемы из спеки дословно ───────────────────────────────────────────

class SpecSample(Base):
    def setUp(self):
        super().setUp()
        self.write_map(SPEC_SAMPLE)

    def test_the_sample_is_a_valid_map(self):
        parsed = CM.load(self.root).parsed
        self.assertEqual(parsed.problems, [])
        self.assertEqual(len(parsed.rules), 6)
        self.assertEqual((parsed.library_import, parsed.icons_path, parsed.wrapper),
                         ("@your/ui", "@your/icons", "Icon"))

    def test_every_kind_of_rule_in_the_sample(self):
        a = self.lookup("accordion")
        self.assertEqual((a["component"], a["props"]), ("Collapse", {"variant": "accordion"}))
        self.assertEqual(a["import"]["statement"], 'import { Collapse } from "@your/ui";')
        b = self.lookup("BasicButton")
        self.assertEqual((b["component"], b["import"]["statement"]),
                         ("Button", 'import { Button } from "@your/ui";'))
        self.assertFalse(self.lookup("Basic")["hit"])
        for name in ("Label", "label-cell"):
            m = self.lookup(name)
            self.assertEqual(m["import"]["statement"], 'import Label from "@your/ui/label";', name)
        c = self.lookup("CustomCell")
        self.assertEqual((c["outcome"], c["component"], c["import"]), ("local", "CustomCell", None))
        self.assertEqual(self.lookup("FigmaDecoration")["outcome"], "ignore")
        i = self.lookup("ArrowRight")
        self.assertEqual((i["component"], i["props"]["name"]), ("Icon", "ChevronRight"))
        self.assertEqual(self.lookup("chevron-down")["icon"], "ChevronDownSmall")


# ── строгое чтение: опечатка в карте — громкий отказ ──────────────────────────

class StrictReading(Base):
    CASES = [
        ("битый YAML", "rules:\n\t- match: A\n", "YAML не разобран"),
        ("ключ верхнего уровня", "rulse:\n  - match: A\n    outcome: ignore\n", "rulse: неизвестный ключ верхнего уровня"),
        ("ключ правила", one_rule("match: A", "outcome: ignore", "compnent: X"), "неизвестное поле «compnent»"),
        ("ключ library", "library:\n  imprt: x\n", "library: неизвестное поле «imprt»"),
        ("ключ icons.import", "icons:\n  import:\n    pth: x\n", "icons.import: неизвестное поле «pth»"),
        ("опечатка в outcome", one_rule("match: A", "outcome: componnet", "component: B"), "outcome «componnet»"),
        ("нет outcome", one_rule("match: A"), "нет поля outcome"),
        ("нет match", "rules:\n  - outcome: ignore\n", "нет поля match"),
        ("component без component", one_rule("match: A", "outcome: component"), "требует поле «component»"),
        ("ignore с component", one_rule("match: A", "outcome: ignore", "component: B"), "не принимает поле «component»"),
        ("icon без icon", one_rule("match: A", "outcome: icon"), "требует поле «icon»"),
        ("local с props", one_rule("match: A", "outcome: local", "props: { a: 1 }"), "не принимает поле «props»"),
        ("component с icon", one_rule("match: A", "outcome: component", "component: B", "icon: C"),
         "не принимает поле «icon»"),
        ("regex не компилируется", one_rule('match: "/(/"', "outcome: ignore"), "не компилируется"),
        ("флаг regex", one_rule('match: "/a/g"', "outcome: ignore"), "флаг regex «g»"),
        ("захват вне диапазона", one_rule('match: "/^A(.+)$/"', "outcome: component", 'component: "$2"'),
         "захват $2"),
        ("захват без regex", one_rule("match: A", "outcome: component", 'component: "$1"'), "без regex-match"),
        ("голый доллар", one_rule("match: A", "outcome: component", 'component: "B$"'), "только как захват"),
        ("повтор в списке", one_rule("match: [A, a]", "outcome: ignore"), "повторяет"),
        ("regex в списке", one_rule('match: [A, "/b/"]', "outcome: ignore"), "в списке имён не допускается"),
        ("число в списке", one_rule("match: [1, 2]", "outcome: ignore"), "только непустые строки-имена"),
        ("пустой список", one_rule("match: []", "outcome: ignore"), "пустой список"),
        ("вложенный проп", one_rule("match: A", "outcome: component", "component: B", "props: { a: [1] }"),
         "значение — строка, число или true/false"),
        ("относительный путь", one_rule("match: A", "outcome: component", "component: B", "import: { path: ./B }"),
         "относительный путь"),
        ("тег HTML вместо компонента", one_rule("match: A", "outcome: component", "component: button"),
         "с маленькой буквы"),
        ("default не булево", one_rule("match: A", "outcome: component", "component: B",
                                       'import: { default: "yes" }'), "import.default — true или false"),
        ("default и чужой named", one_rule("match: A", "outcome: component", "component: B",
                                           "import: { named: Other, default: true }"), "default: true с named"),
        ("правило не объект", "rules:\n  - just a string\n", "ожидается объект"),
        ("rules не список", "rules: {}\n", "rules: ожидается список"),
        ("fallback", "icons:\n  fallback: sometimes\n", "icons.fallback"),
        ("расхождение import/importPath", 'library:\n  import: "@a/ui"\n  importPath: "@b/ui"\n', "расходятся"),
        ("wrapper без component", "icons:\n  import:\n    wrapper:\n      nameMap: { a: B }\n",
         "wrapper.component обязателен"),
        ("nameMap повтор", "icons:\n  import:\n    wrapper:\n      component: Icon\n"
                           "      nameMap: { chevron-down: A, Chevron Down: B }\n", "повторяет"),
        ("props.name у иконки с wrapper", "icons:\n  import:\n    wrapper:\n      component: Icon\nrules:\n"
                                          "  - match: A\n    outcome: icon\n    icon: B\n    props: { name: C }\n",
         "props.name у иконки"),
        ("дубликат", "rules:\n  - match: A\n    outcome: ignore\n  - match: a\n    outcome: ignore\n", "дубликат"),
        ("конфликт", "rules:\n  - match: A\n    outcome: ignore\n  - match: a\n    outcome: local\n", "конфликт"),
        ("дубликат regex", "rules:\n  - match: \"/^A(.+)$/\"\n    outcome: ignore\n"
                           "  - match: \"/^A(.+)$/\"\n    outcome: ignore\n", "regex"),
    ]

    def test_each_mistake_is_named(self):
        for label, text, expected in self.CASES:
            with self.subTest(label):
                self.write_map(text)
                msg = self.refused(self.lookup, "Anything")
                self.assertIn(expected, msg)
                self.assertIn("Файл правит человек", msg)

    def test_all_problems_come_in_one_message(self):
        self.write_map("rules:\n  - match: A\n    outcome: componnet\n"
                       "  - match: \"/(/\"\n    outcome: ignore\n"
                       "  - match: C\n    outcome: component\n")
        msg = self.refused(self.lookup, "A")
        self.assertIn("(3)", msg)
        for n in ("правило #1", "правило #2", "правило #3"):
            self.assertIn(n, msg)

    def test_a_broken_map_blocks_every_name_not_only_the_broken_rule(self):
        self.write_map(BASE_MAP + "  - match: Oops\n    outcome: componnet\n")
        self.refused(self.lookup, "Modal Header")

    def test_init_template_is_a_clean_empty_map(self):
        for answers in ({"libraryImport": "@your/ui", "iconsImport": "@your/icons"}, {}):
            self.write_map(I.render_map(answers))
            parsed = CM.load(self.root).parsed
            self.assertEqual((parsed.problems, parsed.rules), ([], []))
            self.assertFalse(self.lookup("Badge")["hit"])

    def test_missing_map_is_a_miss_with_a_pointer_to_init(self):
        r = self.lookup("Badge")
        self.assertFalse(r["hit"])
        self.assertIn("daiver_init", r["note"])
        self.assertFalse(self.map_path.exists(), "lookup ничего не создаёт")

    def test_unreadable_files_are_named(self):
        self.map_path.parent.mkdir(parents=True)
        self.map_path.write_bytes(b"rules: \xff\xfe\n")
        self.assertIn("UTF-8", self.refused(self.lookup, "A"))
        self.map_path.unlink()
        self.map_path.mkdir()
        self.assertIn("не файл", self.refused(self.lookup, "A"))

    def test_map_file_comes_from_config_and_stays_inside_daiver(self):
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("library:\n  mapFile: ./shared.yaml\n", encoding="utf-8")
        (self.root / ".daiver" / "shared.yaml").write_text(BASE_MAP, encoding="utf-8")
        self.assertTrue(self.lookup("Modal Header")["hit"])
        self.assertEqual(CM.load(self.root).rel, ".daiver/shared.yaml")
        for bad in ("../x.yaml", "/etc/x.yaml", "a\\\\b.yaml", "~/x.yaml"):
            cfg.write_text(f'library:\n  mapFile: "{bad}"\n', encoding="utf-8")
            self.assertIn("library.mapFile", self.refused(self.lookup, "A"), bad)


# ── то, что решается в момент поиска ─────────────────────────────────────────

class RuntimeResolution(Base):
    def test_capture_must_leave_a_component_name(self):
        self.write_map(one_rule('match: "/^Basic (.+)$/"', "outcome: component", 'component: "$1"', head=LIB))
        self.assertIn("не имя компонента", self.refused(self.lookup, "Basic Foo Bar"))
        self.assertIn("с маленькой буквы", self.refused(self.lookup, "Basic button"))
        self.assertEqual(self.lookup("Basic Footer")["component"], "Footer")

    def test_empty_capture_is_empty_text_and_the_result_is_judged(self):
        self.write_map(one_rule('match: "/^Modal(.*)$/"', "outcome: component", 'component: "Modal$1"', head=LIB))
        self.assertEqual(self.lookup("Modal")["component"], "Modal")
        self.write_map(one_rule('match: "/^Modal(.*)$/"', "outcome: component", 'component: "$1"', head=LIB))
        self.assertIn("не имя компонента", self.refused(self.lookup, "Modal"))

    def test_captured_import_path_is_checked_after_substitution(self):
        self.write_map(one_rule('match: "/^Pkg (.+)$/"', "outcome: component", "component: Pkg", "import:",
                                '  path: "@fixture/$1"', head=LIB))
        self.assertEqual(self.lookup("Pkg ui")["import"]["path"], "@fixture/ui")
        self.assertIn("import.path", self.refused(self.lookup, "Pkg bad path"))

    def test_figma_name_is_validated(self):
        self.write_map(BASE_MAP)
        for bad in ("", "   ", None, 5, "x" * 201, "a\nb"):
            self.refused(self.lookup, bad)
        self.assertIn("не содержит букв и цифр", self.refused(self.lookup, "---"))

    def test_variants_are_validated(self):
        self.write_map(BASE_MAP)
        for bad in ("Steps=OFF", ["a"], {"": "x"}, {"a": {"b": 1}}, {"a": None}, {"a": "x" * 101},
                    {str(i): "v" for i in range(31)}):
            self.refused(self.lookup, "Modal Header", bad)
        self.assertTrue(self.lookup("Modal Header", {"Steps": "OFF"})["hit"])


# ── append: согласие пользователя (N7, F9, D3) ────────────────────────────────

NEW_RULE = {"match": "Counter", "outcome": "component", "component": "Counter"}


class AppendConsent(Base):
    def setUp(self):
        super().setUp()
        self.write_map(BASE_MAP)
        self.before = self.read_map()

    def assertUntouched(self):
        self.assertEqual(self.read_map(), self.before, "карта не изменилась ни на байт")
        self.assertEqual(sorted(p.name for p in self.map_path.parent.iterdir()), ["component-map.yaml"],
                         "ни .tmp, ни других следов")

    def test_ui_map_is_a_consent_class_with_a_user_quote(self):
        self.assertTrue(FE.consent_required(CM.consent_key("STOR-100")))
        self.assertEqual(CM.consent_key("STOR-100"), "ui-map-STOR-100")

    def test_no_consent_no_write(self):
        msg = self.refused(self.append, NEW_RULE)
        self.assertIn("нет согласия пользователя", msg)
        self.assertIn("ui-map-STOR-100", msg)
        self.assertIn("record_approval.py", msg)
        self.assertIn("--evidence", msg)
        self.assertIn("dryRun", msg)
        self.assertUntouched()
        self.assertFalse(FE.approvals_path(self.root).exists(), "журнал согласий не тронут")

    def test_consent_for_another_story_does_not_count(self):
        self.approve("STOR-200")
        self.assertIn("нет согласия", self.refused(self.append, NEW_RULE, "STOR-100"))
        self.assertUntouched()

    def test_a_line_written_straight_into_the_log_is_not_consent(self):
        log = FE.approvals_path(self.root)
        log.parent.mkdir(parents=True)
        key = CM.consent_key("STOR-100")
        forged = [{"kind": "approval", "key": key, "approved_by": "user", "reason": "сам себе"},
                  {"kind": "approval", "key": key, "produced_by": "model", "reason": "сам себе"},
                  {"kind": "approval", "key": key, "produced_by": "record_approval-fake"}]
        log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in forged), encoding="utf-8")
        self.assertIn("нет согласия", self.refused(self.append, NEW_RULE))
        self.assertUntouched()

    def test_revoked_consent_does_not_count(self):
        self.approve()
        FE.revoke_approval(self.root, CM.consent_key("STOR-100"), reason="тест")
        self.assertIn("нет согласия", self.refused(self.append, NEW_RULE))
        self.assertUntouched()

    def test_with_consent_the_rule_is_added_and_works(self):
        self.approve()
        res = self.append(NEW_RULE)
        self.assertEqual((res["ok"], res["file"], res["index"]), (True, ".daiver/component-map.yaml", 7))
        self.assertEqual(res["rule"], NEW_RULE)
        self.assertEqual(res["yaml"], "  - match: Counter\n    outcome: component\n    component: Counter")
        hit = self.lookup("counter")
        self.assertEqual((hit["component"], hit["rule"]["index"]), ("Counter", 7))
        self.assertEqual(hit["import"]["statement"], 'import { Counter } from "@fixture/ui";')

    def test_one_yes_is_one_rule(self):
        self.approve()
        self.append(NEW_RULE)
        self.assertIsNone(self.consent(), "согласие потрачено записью")
        after_first = self.read_map()
        other = {"match": "Divider 2", "outcome": "local"}
        msg = self.refused(self.append, other)
        self.assertIn("нет согласия", msg)
        self.assertIn("уже потрачен", msg)
        self.assertEqual(self.read_map(), after_first)
        self.approve()
        self.append(other)
        self.assertEqual([r["match"] for r in self.rules()[-2:]], ["Counter", "Divider 2"], "порядок записи")

    def test_a_bad_rule_does_not_burn_the_consent(self):
        self.approve()
        for bad in ({"match": "", "outcome": "ignore"}, {"match": "X", "outcome": "nope"},
                    {"match": "Modal Header", "outcome": "local"},             # конфликт
                    {"match": "modal-header", "outcome": "component", "component": "ModalHeader",
                     "props": {"steps": False}}):                               # дубликат
            self.refused(self.append, bad)
            self.assertIsNotNone(self.consent(), bad)
        self.assertUntouched()
        self.append(NEW_RULE)                       # то же «да» годится для исправленного правила
        self.assertIsNone(self.consent())

    def test_bad_rule_is_one_message_with_everything_wrong(self):
        msg = self.refused(self.append, {"match": "", "outcome": "ignore", "component": "X", "bogus": 1})
        self.assertIn("(3)", msg)
        for part in ("неизвестное поле «bogus»", "match пуст", "не принимает поле «component»"):
            self.assertIn(part, msg)
        self.assertIn("согласие не потрачено", msg)

    def test_duplicate_and_conflict_are_told_apart(self):
        dup = self.refused(self.append, {"match": ["divider"], "outcome": "local", "component": "Divider"})
        self.assertIn("дубликат", dup)
        self.assertIn("правиле #4", dup)
        clash = self.refused(self.append, {"match": "Divider", "outcome": "ignore"})
        self.assertIn("конфликт", clash)
        self.assertIn("outcome: ignore ≠ local", clash)
        part = self.refused(self.append, {"match": ["Counter", "Modal-Action"], "outcome": "ignore"})
        self.assertIn("«Modal-Action»", part, "из списка названо именно пересекающееся имя")
        self.assertIn("правилом #3", part)
        rx = self.refused(self.append, {"match": "/^Technical (.+)$/", "outcome": "ignore"})
        self.assertIn("regex", rx)
        self.assertUntouched()

    def test_exact_name_may_refine_a_regex_rule(self):
        self.approve()
        self.append({"match": "Technical Button", "outcome": "component", "component": "TechBtn"})
        self.assertEqual(self.lookup("Technical Button")["component"], "TechBtn")
        self.assertEqual(self.lookup("Technical Icon")["component"], "TechIcon", "остальные — по regex")

    def test_dry_run_needs_no_consent_and_writes_nothing(self):
        res = self.append(NEW_RULE, dry_run=True)
        self.assertEqual((res["ok"], res["dryRun"], res["index"]), (True, True, 7))
        self.assertEqual(res["yaml"], "  - match: Counter\n    outcome: component\n    component: Counter")
        self.assertEqual(res["consent"], {"key": "ui-map-STOR-100", "granted": False})
        self.assertIn("ui-map-STOR-100", res["next"])
        self.assertIn("«да»", res["next"])
        self.assertUntouched()
        self.assertFalse((self.root / "ground").exists(), "сухой прогон не создаёт ни замков, ни журналов")
        self.approve()
        self.assertTrue(self.append(NEW_RULE, dry_run=True)["consent"]["granted"])
        self.assertIsNotNone(self.consent(), "сухой прогон согласие не тратит")
        self.assertUntouched()

    def test_dry_run_refuses_a_bad_rule_like_the_real_call(self):
        self.assertIn("правило не принято", self.refused(self.append, {"match": "A", "outcome": "nope"}, dry_run=True))
        self.assertIn("правило не принято", self.refused(self.append, {"match": "Divider", "outcome": "ignore"},
                                                       dry_run=True))

    def test_track_name_is_checked_before_the_consent(self):
        for bad in ("../x", "a b", "-lead"):
            self.approve(bad)                      # даже «согласие» под таким ключом не открывает запись
            self.assertIn("имя трека", self.refused(self.append, NEW_RULE, bad), bad)
        for bad in ("", None, 5):
            self.assertIn("имя трека", self.refused(self.append, NEW_RULE, bad), repr(bad))
        self.assertUntouched()

    def test_no_map_no_append(self):
        self.map_path.unlink()
        self.approve()
        self.assertIn("карты нет", self.refused(self.append, NEW_RULE))
        self.assertFalse(self.map_path.exists(), "append файл не создаёт")

    def test_broken_map_is_not_patched(self):
        self.write_map(BASE_MAP + "  - match: Oops\n    outcome: componnet\n")
        self.before = self.read_map()
        self.approve()
        self.assertIn("некорректна", self.refused(self.append, NEW_RULE))
        self.assertIsNotNone(self.consent())
        self.assertUntouched()


# ── append: файл не заменяется — правило вставляется текстом ─────────────────

LOCAL_B = {"match": "B", "outcome": "local"}
BLOCK_B = "  - match: B\n    outcome: local\n"


class AppendText(Base):
    def added(self, old: str, rule: Any = LOCAL_B) -> str:
        """Записать карту, одобрить, дописать правило; вернуть новый текст файла."""
        self.write_map(old)
        self.approve()
        self.append(rule)
        return self.read_map()

    def test_spec_style_file_gets_a_pure_insertion_at_the_end(self):
        rule = {"match": ["Modal Header", "Modal Action"], "outcome": "component", "component": "Modal",
                "props": {"steps": "OFF", "size": 2, "compact": True}}
        new = self.added(SPEC_SAMPLE, rule)
        block = ("    - match: [Modal Header, Modal Action]\n      outcome: component\n      component: Modal\n"
                 '      props: { steps: "OFF", size: 2, compact: true }\n')
        self.assertEqual(new, SPEC_SAMPLE + block, "всё прежнее — побайтно, стиль отступа файла (4) сохранён")
        self.assertEqual(self.lookup("modal-action")["props"], {"steps": "OFF", "size": 2, "compact": True})

    def test_import_block_is_nested_two_spaces_like_in_the_spec(self):
        rule = {"match": "Tag", "outcome": "component", "component": "Tag",
                "import": {"path": "@fixture/ui/tag", "named": "TagBase"}}
        new = self.added(SPEC_SAMPLE, rule)
        self.assertEqual(new[len(SPEC_SAMPLE):],
                         '    - match: Tag\n      outcome: component\n      component: Tag\n      import:\n'
                         '        path: "@fixture/ui/tag"\n        named: TagBase\n')

    def test_rule_goes_after_the_last_rule_before_the_rest_of_the_file(self):
        old = ('# шапка\nlibrary:\n  import: "@your/ui"\n\nrules:\n  - match: A\n    outcome: ignore\n'
               '  # заметка после последнего правила\n\nicons:\n  fallback: ask\n# подвал\n')
        new = self.added(old)
        self.assertEqual(new, old.replace("    outcome: ignore\n", "    outcome: ignore\n" + BLOCK_B, 1))

    def test_empty_inline_rules_become_a_block_and_keep_their_comment(self):
        old = '# шапка\nlibrary:\n  import: "@your/ui"\nrules: []   # правила команды\n'
        new = self.added(old)
        self.assertEqual(new, '# шапка\nlibrary:\n  import: "@your/ui"\nrules:   # правила команды\n' + BLOCK_B)

    def test_init_template_keeps_its_header_and_icons_block(self):
        old = I.render_map({"libraryImport": "@your/ui", "iconsImport": "@your/icons"})
        new = self.added(old, {"match": "Badge", "outcome": "component", "component": "Tag"})
        self.assertEqual(new, old.replace("rules: []\n", "rules:\n  - match: Badge\n    outcome: component\n"
                                                         "    component: Tag\n"))
        self.assertTrue(new.startswith("# Карта компонентов forge-ui"))

    def test_no_rules_block_is_appended_at_the_end(self):
        old = 'library:\n  import: "@your/ui"\n'
        self.assertEqual(self.added(old), old + "\nrules:\n" + BLOCK_B)

    def test_comment_only_file(self):
        old = "# только шапка\n"
        self.assertEqual(self.added(old), old + "\nrules:\n" + BLOCK_B)

    def test_dash_at_the_key_indent_is_respected(self):
        old = "rules:\n- match: A\n  outcome: ignore\nicons:\n  fallback: ask\n"
        self.assertEqual(self.added(old), "rules:\n- match: A\n  outcome: ignore\n- match: B\n  outcome: local\n"
                                          "icons:\n  fallback: ask\n")

    def test_wide_gap_after_the_dash_is_respected(self):
        old = "rules:\n  -   match: A\n      outcome: ignore\n"
        self.assertEqual(self.added(old), old + "  -   match: B\n      outcome: local\n")

    def test_file_without_a_final_newline(self):
        old = SPEC_SAMPLE.rstrip("\n")
        new = self.added(old)
        self.assertEqual(new, old + "\n    - match: B\n      outcome: local\n")

    def test_crlf_file_stays_crlf(self):
        old = SPEC_SAMPLE.replace("\n", "\r\n")
        new = self.added(old)
        self.assertEqual(new, old + "    - match: B\r\n      outcome: local\r\n")
        self.assertEqual(new.count("\n"), new.count("\r\n"), "ни одного «голого» LF")

    def test_bom_is_kept(self):
        self.write_map("﻿" + SPEC_SAMPLE)
        self.approve()
        self.append(LOCAL_B)
        raw = self.map_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw[3:].decode("utf-8"), SPEC_SAMPLE + "    - match: B\n      outcome: local\n")

    def test_earlier_rules_are_untouched_by_the_insertion(self):
        self.write_map(BASE_MAP)
        before = self.rules()
        self.approve()
        self.append(NEW_RULE)
        self.assertEqual(self.rules()[:-1], before)
        self.assertEqual(CM.parse_text(self.read_map()).head(), CM.parse_text(BASE_MAP).head())

    def test_flow_style_rules_are_not_rewritten(self):
        old = "rules: [{match: A, outcome: ignore}]\n"
        self.write_map(old)
        self.approve()
        self.assertIn("не блоком списка", self.refused(self.append, LOCAL_B))
        self.assertEqual(self.read_map(), old)
        self.assertIsNotNone(self.consent())

    def test_awkward_values_round_trip(self):
        rules = [
            {"match": ["Кнопка, основная", "Кнопка: вторичная", "Кнопка # третья", "[скобки]"], "outcome": "local"},
            {"match": ["null", "yes", "123", "true", "~tilde", "On"], "outcome": "ignore"},
            {"match": "Say \"quotes\" and 'single'", "outcome": "ignore"},
            {"match": "Stroke / Arrows / Small_Left 2", "outcome": "ignore"},
            {"match": r"/^Icon[-_ ](\d+)\s#x$/", "outcome": "component", "component": "Icon$1"},
            {"match": "Weird", "outcome": "component", "component": "Weird",
             "props": {"label": "a: b # c", "n": 3, "f": 0.5, "on": True, "s": "null", "q": 'say "hi"'}},
            {"match": "Pathy", "outcome": "component", "component": "Pathy",
             "import": {"path": "@fixture/ui/{name}", "default": True}},
        ]
        self.write_map(LIB + "rules: []\n")
        for rule in rules:
            self.approve()
            res = self.append(rule)
            self.assertEqual(res["rule"], rule)
            self.assertEqual(self.rules()[-1], rule, rule["match"])
        self.assertEqual(len(self.rules()), len(rules))
        self.assertEqual(self.lookup("кнопка вторичная")["rule"]["index"], 1)
        self.assertEqual(self.lookup("Icon 24 #x")["component"], "Icon24")
        self.assertEqual(self.lookup("pathy")["import"]["statement"], 'import Pathy from "@fixture/ui/Pathy";')
        self.assertEqual(self.lookup("ON")["outcome"], "ignore")


# ── append: запись атомарна, результат сверяется до записи ───────────────────

class RobustWrite(Base):
    def setUp(self):
        super().setUp()
        self.write_map(BASE_MAP)
        self.before = self.read_map()
        self.approve()

    def assertSafe(self, msg: str, needle: str) -> None:
        self.assertIn(needle, msg)
        self.assertEqual(self.read_map(), self.before)
        self.assertEqual(sorted(p.name for p in self.map_path.parent.iterdir()), ["component-map.yaml"])
        self.assertIsNotNone(self.consent(), "при отказе записи согласие не тратится")

    def test_failed_replace_leaves_the_file_and_the_consent(self):
        with mock.patch.object(CM.os, "replace", side_effect=OSError("диск полон")):
            msg = self.refused(self.append, NEW_RULE)
        self.assertSafe(msg, "не удалась")

    def test_lock_lives_in_ground_not_in_the_committed_daiver_dir(self):
        self.append(NEW_RULE)
        self.assertTrue((self.root / "ground" / "component-map.lock").is_file())
        self.assertEqual(sorted(p.name for p in self.map_path.parent.iterdir()), ["component-map.yaml"])

    def test_insertion_that_changes_an_earlier_rule_is_refused(self):
        real = CM._splice

        def corrupt(text, rule):
            new, inserted = real(text, rule)
            return new.replace("component: ModalHeader", "component: Other", 1), inserted
        with mock.patch.object(CM, "_splice", corrupt):
            msg = self.refused(self.append, NEW_RULE)
        self.assertSafe(msg, "внутренняя ошибка вставки")

    def test_insertion_that_changes_the_library_block_is_refused(self):
        real = CM._splice

        def corrupt(text, rule):
            new, inserted = real(text, rule)
            return new.replace('import: "@fixture/ui"', 'import: "@evil/ui"', 1), inserted
        with mock.patch.object(CM, "_splice", corrupt):
            msg = self.refused(self.append, NEW_RULE)
        self.assertSafe(msg, "внутренняя ошибка вставки")

    def test_insertion_that_adds_a_different_rule_is_refused(self):
        real = CM._splice

        def corrupt(text, rule):
            new, inserted = real(text, rule)
            return new.replace("component: Counter", "component: Counterfeit"), inserted
        with mock.patch.object(CM, "_splice", corrupt):
            msg = self.refused(self.append, NEW_RULE)
        self.assertSafe(msg, "внутренняя ошибка вставки")

    def test_insertion_that_breaks_the_yaml_is_refused(self):
        with mock.patch.object(CM, "_splice", lambda text, rule: ("{{{ not yaml\n", [])):
            msg = self.refused(self.append, NEW_RULE)
        self.assertSafe(msg, "не проходит проверку")


# ── сквозная цепочка с настоящим record_approval.py ──────────────────────────

class RealRecordApproval(Base):
    def record(self, quote: Optional[str] = None, track: str = "STOR-100") -> "subprocess.CompletedProcess[str]":
        cmd = [sys.executable, "-X", "utf8", str(RECORD_APPROVAL), "--project", str(self.root),
               "--key", CM.consent_key(track), "--approved-by", "user", "--reason", "правило карты"]
        if quote is not None:
            cmd += ["--evidence", quote]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_chain_from_the_users_yes_to_the_rule(self):
        self.write_map(BASE_MAP)
        before = self.read_map()
        self.assertEqual(self.record().returncode, 2, "ключ ui-map требует цитаты пользователя")
        self.assertEqual(self.record("добавь правило для счётчика в карту").returncode, 2,
                         "цитата без слова согласия — не согласие")
        self.refused(self.append, NEW_RULE)
        self.assertEqual(self.read_map(), before)
        done = self.record("да, добавляй правило в карту")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.append(NEW_RULE)
        other = {"match": "Counter 2", "outcome": "local"}
        self.assertIn("нет согласия", self.refused(self.append, other), "одно «да» — одно правило")
        self.assertEqual(self.record("да, и это правило тоже").returncode, 0)
        self.append(other)
        key = CM.consent_key("STOR-100")
        kinds = [(r["kind"], r["key"]) for r in FE.read_log(FE.approvals_path(self.root))]
        self.assertEqual(kinds.count(("approval", key)), 2)
        self.assertEqual(kinds.count(("approval-revoked", key)), 2, "каждое согласие потрачено ровно одной записью")
        self.assertEqual([r["match"] for r in self.rules()[-2:]], ["Counter", "Counter 2"])


# ── настоящие имена образца «Modal» ──────────────────────────────────────────

class RealSample(Base):
    def setUp(self):
        super().setUp()
        dsl = self.root / "dsl" / "modal.json"
        dsl.parent.mkdir()
        dsl.write_bytes(FIXTURE.read_bytes())
        PP.pull(self.root, "STOR-100", dsl, "https://pixso.example/modal", None)
        NZ.normalize_frame(self.root, "STOR-100", None)
        manifest = TR.load(self.root, "STOR-100")
        canon = json.loads(TR.frame_paths(self.root, manifest, None)["canonical"].read_text(encoding="utf-8"))
        self.instances: List[dict] = []

        def walk(node: dict) -> None:
            if node.get("kind") == "instance":
                self.instances.append(node)
            for child in node.get("children") or []:
                walk(child)
        walk(canon["root"])

    def test_names_the_resolver_will_ask_about(self):
        pairs = sorted((n["component"]["figmaName"], json.dumps(n["component"].get("variants"), sort_keys=True))
                       for n in self.instances)
        self.assertEqual(pairs, [("Modal Action", '{"Variant": "Default"}'),
                                 ("Modal Header", '{"Steps": "OFF"}'),
                                 ("Modal Header", '{"Steps": "OFF"}')])
        self.assertEqual(sorted({n["name"] for n in self.instances}), ["Steps=OFF", "Variant=Default"],
                         "имя слоя — строка вариантов, а не имя компонента")

    def test_canon_instances_hit_rules_written_for_their_figma_names(self):
        self.write_map(LIB + "rules:\n  - match: Modal Header\n    outcome: component\n    component: ModalHeader\n"
                             "  - match: Modal Action\n    outcome: component\n    component: ModalActions\n")
        got = []
        for n in self.instances:
            r = self.lookup(n["component"]["figmaName"], n["component"].get("variants"))
            self.assertTrue(r["hit"], n["name"])
            self.assertEqual(r["confidence"], "high")
            got.append(r["component"])
        self.assertEqual(sorted(got), ["ModalActions", "ModalHeader", "ModalHeader"])
        self.assertFalse(self.lookup("Modal")["hit"], "кадр «Modal» — не компонент, правила на него нет")

    def test_layer_names_can_be_matched_too(self):
        self.write_map(LIB + "rules:\n  - match: [Steps=OFF, Variant=Default]\n    outcome: ignore\n")
        for n in self.instances:
            self.assertEqual(self.lookup(n["name"])["outcome"], "ignore", n["name"])
        self.assertTrue(self.lookup("steps = off")["hit"])
        self.assertFalse(self.lookup("Steps=ON")["hit"], "другое значение варианта — другое имя")

    def test_every_definition_name_has_its_own_rule(self):
        names = sample_definitions()
        text = LIB + "rules:\n" + "".join(
            f"  - match: {json.dumps(n, ensure_ascii=False)}\n    outcome: local\n    component: Comp{i}\n"
            for i, n in enumerate(names, 1))
        self.write_map(text)
        self.assertEqual(CM.load(self.root).parsed.problems, [])
        for i, n in enumerate(names, 1):
            r = self.lookup(n)
            self.assertEqual((r["rule"]["index"], r["component"]), (i, f"Comp{i}"), n)
            self.assertEqual(self.lookup(n.lower().replace(" ", "-"))["rule"]["index"], i, n)

    def test_all_definition_names_can_be_appended_one_yes_each(self):
        names = sample_definitions()
        self.write_map(I.render_map({"libraryImport": "@your/ui"}))
        for n in names:
            self.approve()
            self.append({"match": n, "outcome": "local"})
        self.assertEqual([r["match"] for r in self.rules()], names)
        key = CM.consent_key("STOR-100")
        kinds = [(r["kind"], r["key"]) for r in FE.read_log(FE.approvals_path(self.root))]
        self.assertEqual((kinds.count(("approval", key)), kinds.count(("approval-revoked", key))),
                         (len(names), len(names)))
        self.assertIn("нет согласия", self.refused(self.append, {"match": "Brand New Widget", "outcome": "local"}),
                      "все согласия потрачены")
        self.approve()
        self.assertIn("дубликат", self.refused(self.append, {"match": names[0].upper(), "outcome": "local"}))
        self.assertIsNotNone(self.consent(), "дубликат согласие не тратит")


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

    def test_inventory_and_schemas(self):
        lookup, append = T.TOOLS["daiver_map_lookup"], T.TOOLS["daiver_map_append"]
        self.assertEqual(sorted(lookup["schema"]["properties"]), ["figmaName", "variants"])
        self.assertEqual(lookup["schema"]["required"], ["figmaName"])
        self.assertEqual(sorted(append["schema"]["properties"]), ["dryRun", "rule", "track"])
        self.assertEqual(append["schema"]["required"], ["track", "rule"])
        for tool in (lookup, append):
            self.assertFalse(tool["schema"]["additionalProperties"])
        self.assertIn("ДОСЛОВНО", lookup["description"])
        self.assertIn("одно «да» — одно правило", append["description"])
        self.assertNotIn("daiver_plan_sign", T.TOOLS)

    def test_lookup_through_the_registry(self):
        self.write_map(BASE_MAP)
        r = self.call("daiver_map_lookup", {"figmaName": "modal header", "variants": {"Steps": "OFF"}})
        self.assertEqual(r["component"], "ModalHeader")
        json.dumps(r, ensure_ascii=False)
        self.assertFalse(self.call("daiver_map_lookup", {"figmaName": "Counter"})["hit"])

    def test_bad_arguments_are_tool_errors(self):
        self.write_map(BASE_MAP)
        self.assertIn("неизвестный аргумент", self.fails("daiver_map_lookup", {"figmaName": "A", "bogus": 1}))
        self.assertIn("figmaName", self.fails("daiver_map_lookup", {}))
        self.assertIn("ожидается string", self.fails("daiver_map_lookup", {"figmaName": 5}))
        self.assertIn("ожидается object", self.fails("daiver_map_lookup", {"figmaName": "A", "variants": "x"}))
        self.assertIn("figmaName", self.fails("daiver_map_lookup", {"figmaName": "  "}))
        self.assertIn("track", self.fails("daiver_map_append", {"rule": NEW_RULE}))
        self.assertIn("rule", self.fails("daiver_map_append", {"track": "STOR-100"}))
        self.assertIn("ожидается object", self.fails("daiver_map_append", {"track": "STOR-100", "rule": "Counter"}))
        self.assertIn("ожидается boolean", self.fails("daiver_map_append",
                                                       {"track": "STOR-100", "rule": NEW_RULE, "dryRun": "yes"}))
        self.assertIn("имя трека", self.fails("daiver_map_append", {"track": "../x", "rule": NEW_RULE}))

    def test_broken_map_is_a_tool_error_for_both_tools(self):
        self.write_map(BASE_MAP + "  - match: Oops\n    outcome: componnet\n")
        self.assertIn("некорректна", self.fails("daiver_map_lookup", {"figmaName": "A"}))
        self.assertIn("некорректна", self.fails("daiver_map_append", {"track": "STOR-100", "rule": NEW_RULE}))

    def test_append_through_the_registry(self):
        self.write_map(BASE_MAP)
        before = self.read_map()
        self.assertIn("нет согласия", self.fails("daiver_map_append", {"track": "STOR-100", "rule": NEW_RULE}))
        self.assertEqual(self.read_map(), before)
        preview = self.call("daiver_map_append", {"track": "STOR-100", "rule": NEW_RULE, "dryRun": True})
        self.assertTrue(preview["dryRun"])
        self.assertEqual(self.read_map(), before)
        self.approve()
        res = self.call("daiver_map_append", {"track": "STOR-100", "rule": NEW_RULE})
        self.assertEqual((res["ok"], res["index"]), (True, 7))
        self.assertEqual(self.call("daiver_map_lookup", {"figmaName": "counter"})["component"], "Counter")

    def test_json_rpc_session(self):
        self.write_map(BASE_MAP)
        out = mcp_session(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            mcp_call(2, "daiver_map_lookup", {"figmaName": "Technical Button"}),
            mcp_call(3, "daiver_map_append", {"track": "STOR-100", "rule": NEW_RULE}),
            mcp_call(4, "daiver_map_append", {"track": "STOR-100", "rule": NEW_RULE, "dryRun": True}),
            mcp_call(5, "daiver_map_lookup", {"figmaName": "A", "nope": 1}),
        ])
        self.assertEqual(len(out), 5)
        tools = {t["name"]: t for t in out[0]["result"]["tools"]}
        self.assertEqual(tools["daiver_map_append"]["inputSchema"]["required"], ["track", "rule"])
        hit = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual(hit["component"], "TechButton")
        self.assertTrue(out[2]["result"]["isError"])
        self.assertTrue(out[2]["result"]["content"][0]["text"].startswith("✗ нет согласия пользователя"))
        preview = json.loads(out[3]["result"]["content"][0]["text"])
        self.assertEqual((preview["ok"], preview["dryRun"]), (True, True))
        self.assertTrue(out[4]["result"]["isError"])
        self.assertEqual(self.read_map(), BASE_MAP, "ни отказ, ни сухой прогон карту не меняют")


if __name__ == "__main__":
    unittest.main()
