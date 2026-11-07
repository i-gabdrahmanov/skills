#!/usr/bin/env python3
"""test_texts.py — daiver.texts: тексты макета трека для проверок вне движка (задача 3.4; INV A11).

Пинит как свойства КОДА: `mockup_texts` отдаёт ровно то, что отдаёт `canon.collect_texts` по каждому кадру трека (видимые тексты
узлов, `component.content`, `textProps`; occluded-поддеревья не входят), уникальные строки дословно в порядке первого появления,
кадры — по манифесту, в том числе в раскладке `frames/<имя>/` (второй кадр); кадр без канона пропускается, но повреждённый канон,
отсутствующий трек и трек без единого канона — `TextsError` (хук pii-boundary читает отказ как «исключения нет»: нечего сверять —
значит нельзя пропускать); только читает (ни одного байта на диске не меняет); `unmatched` — строки, которых нет ДОСЛОВНО (подстрокой)
ни в одном тексте: иное форматирование телефона, склейка из двух текстов, пустая строка и не-строка — не найдены.

Данные. «Modal» — настоящий образец Pixso (5 текстов, прогон pull → normalize); остальное — синтетика с PII-подобными текстами
(почта, телефон), потому что в образце их нет. Не проверено данными: боевых макетов с реалистичными моками нет — как ПДн выглядят в
канонических текстах команды (переносы строк, неразрывные пробелы в телефонах), покажет боевой прогон (X.4).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver import texts as TX  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
TRACK = "STOR-100"
URL = "https://pixso.example/a?item-id=2:153702"
SHA = "a" * 64
EMAIL = "ivan.petrov@client-bank.ru"
PHONE = "+7 (495) 123-45-67"


def text(i: str, value: str, **kw) -> dict:
    n = {"id": i, "kind": "text", "name": i, "text": value}
    n.update(kw)
    return n


def frame(i: str, kids=(), **kw) -> dict:
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def inst(i: str, slots=(), text_props: Optional[Dict[str, str]] = None, **kw) -> dict:
    comp = {"figmaName": "Contact Card", "origin": "library", "childrenRole": "content"}
    if slots:
        comp["content"] = [{"id": f"{i};{n}", "name": n, "text": t, "overridden": ov,
                            "font": {"family": "Fixture Sans", "size": 14, "weight": 400}} for n, t, ov in slots]
    if text_props:
        comp["textProps"] = dict(text_props)
    n = {"id": i, "kind": "instance", "name": i, "component": comp}
    n.update(kw)
    return n


def make_track(root: Path, frames: Dict[str, Optional[dict]], track: str = TRACK) -> dict:
    """Трек с кадрами {имя: корень канона | None (канона нет)} — как после pull; файлы кадров по раскладке манифеста."""
    m = TR.new_manifest(track, created="2026-10-20T12:00:00Z")
    for name in frames:
        TR.upsert_frame(m, name, frameUrl=URL, requirement={
            "kind": "pixso", "path": "visual-requirements.pixso.json", "sha256": SHA, "pulledAt": "2026-10-20T12:00:00Z"})
    TR.save(root, m)
    for name, node in frames.items():
        if node is None:
            continue
        paths = TR.frame_paths(root, m, name)
        C.write_canonical(paths["canonical"], C.new_envelope(node, "pixso", {"sourceSha256": SHA}))
    return m


def snapshot(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class MockupTexts(Env):
    def test_node_slot_and_prop_texts_in_document_order_without_duplicates(self):
        root_node = frame("Page", [
            text("t1", "Контакты"),
            inst("c1", [("Email", EMAIL, True), ("Phone", PHONE, True)]),
            inst("c2", text_props={"title": "Заголовок"}),
            text("t2", "Контакты"),                       # повтор — один раз
            text("t3", "Подвал"),
        ])
        make_track(self.root, {"main": root_node})
        self.assertEqual(TX.mockup_texts(self.root, TRACK), ["Контакты", EMAIL, PHONE, "Заголовок", "Подвал"])

    def test_it_is_what_collect_texts_says_placeholders_of_the_library_included(self):
        root_node = frame("Page", [inst("c1", [("Email", EMAIL, True), ("Hint", "name@host", False)])])
        make_track(self.root, {"main": root_node})
        got = TX.mockup_texts(self.root, TRACK)
        self.assertEqual(got, [t["text"] for t in C.collect_texts(root_node)])
        self.assertIn("name@host", got, "слот-плейсхолдер библиотеки (overridden=false) тоже написан в макете")

    def test_occluded_subtree_is_not_the_text_of_the_screen(self):
        root_node = frame("Page", [text("t1", "Виден"), frame("hidden", [text("t2", EMAIL)], occluded=True)])
        make_track(self.root, {"main": root_node})
        self.assertEqual(TX.mockup_texts(self.root, TRACK), ["Виден"])

    def test_every_frame_of_the_track_in_manifest_order_in_the_multi_frame_layout(self):
        m = make_track(self.root, {"main": frame("A", [text("a", "Один"), text("c", "Общий")]),
                                   "dialog": frame("B", [text("b", "Два"), text("d", "Общий")])})
        self.assertEqual([f["dir"] for f in m["frames"]], ["frames/main", "frames/dialog"], "со второго кадра — frames/<имя>")
        self.assertEqual(TX.mockup_texts(self.root, TRACK), ["Один", "Общий", "Два"])

    def test_a_frame_without_canonical_is_skipped_while_another_one_is_read(self):
        make_track(self.root, {"main": frame("A", [text("a", "Один")]), "later": None})
        self.assertEqual(TX.mockup_texts(self.root, TRACK), ["Один"])

    def test_only_reads(self):
        make_track(self.root, {"main": frame("A", [text("a", EMAIL)])})
        before = snapshot(self.root)
        TX.mockup_texts(self.root, TRACK)
        self.assertEqual(snapshot(self.root), before)

    def test_real_modal_sample_texts_are_collect_texts_of_its_canonical(self):
        (self.root / "dsl.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": TRACK, "dslPath": "dsl.json", "frameUrl": URL})
        T.call("daiver_normalize", self.root, {"track": TRACK})
        m = TR.load(self.root, TRACK)
        env = C.read_canonical(TR.frame_paths(self.root, m)["canonical"])
        expected = []
        for t in C.collect_texts(env["root"]):
            if t["text"] not in expected:
                expected.append(t["text"])
        got = TX.mockup_texts(self.root, TRACK)
        self.assertEqual(got, expected)
        self.assertIn("Текст9 слово - 20", got)
        self.assertEqual(len(got), 5, "в образце «Modal» пять разных текстов")


class Refusals(Env):
    def test_no_track(self):
        with self.assertRaises(TX.TextsError) as cm:
            TX.mockup_texts(self.root, TRACK)
        self.assertIn(TRACK, str(cm.exception))

    def test_a_track_name_that_is_not_a_story_key(self):
        for bad in ("../etc", "a/b", "", ".."):
            with self.subTest(track=bad), self.assertRaises(TX.TextsError):
                TX.mockup_texts(self.root, bad)

    def test_damaged_manifest(self):
        make_track(self.root, {"main": frame("A", [text("a", "x")])})
        (self.root / ".daiver" / "tracks" / TRACK / "track.yaml").write_text("track: [broken\n", encoding="utf-8")
        with self.assertRaises(TX.TextsError):
            TX.mockup_texts(self.root, TRACK)

    def test_damaged_canonical_names_the_frame_and_is_not_skipped(self):
        m = make_track(self.root, {"main": frame("A", [text("a", "Один")]), "dialog": frame("B", [text("b", "Два")])})
        TR.frame_paths(self.root, m, "dialog")["canonical"].write_text("{не json", encoding="utf-8")
        with self.assertRaises(TX.TextsError) as cm:
            TX.mockup_texts(self.root, TRACK)
        self.assertIn("dialog", str(cm.exception))

    def test_invalid_canonical_schema(self):
        m = make_track(self.root, {"main": frame("A", [text("a", "x")])})
        TR.frame_paths(self.root, m)["canonical"].write_text(json.dumps({"root": {"id": 1}}), encoding="utf-8")
        with self.assertRaises(TX.TextsError):
            TX.mockup_texts(self.root, TRACK)

    def test_track_without_any_canonical_has_nothing_to_compare_with(self):
        make_track(self.root, {"main": None})
        with self.assertRaises(TX.TextsError) as cm:
            TX.mockup_texts(self.root, TRACK)
        self.assertIn("daiver_normalize", str(cm.exception))

    def test_track_with_frames_but_empty_manifest_frames(self):
        make_track(self.root, {})
        with self.assertRaises(TX.TextsError):
            TX.mockup_texts(self.root, TRACK)

    def test_the_refusal_is_a_value_error(self):
        self.assertTrue(issubclass(TX.TextsError, ValueError))


class Unmatched(unittest.TestCase):
    POOL = [f"Звоните: {PHONE} с 9 до 18", EMAIL, "Контакты"]

    def test_substring_of_a_text_is_verbatim(self):
        self.assertEqual(TX.unmatched(self.POOL, [PHONE, EMAIL]), [])

    def test_other_formatting_is_not_verbatim(self):
        self.assertEqual(TX.unmatched(self.POOL, ["+7 495 123-45-67", "+7(495)123-45-67"]), ["+7 495 123-45-67", "+7(495)123-45-67"])

    def test_a_string_glued_from_two_texts_is_not_verbatim(self):
        pool = ["ivan.petrov@", "client-bank.ru"]
        self.assertEqual(TX.unmatched(pool, [EMAIL]), [EMAIL])

    def test_case_matters(self):
        self.assertEqual(TX.unmatched(self.POOL, [EMAIL.upper()]), [EMAIL.upper()])

    def test_order_is_kept_and_repeats_are_dropped(self):
        self.assertEqual(TX.unmatched(self.POOL, ["b@x.ru", EMAIL, "a@x.ru", "b@x.ru"]), ["b@x.ru", "a@x.ru"])

    def test_blank_and_non_strings_cannot_be_verified(self):
        self.assertEqual(TX.unmatched(self.POOL, ["", "  ", None, 5]), ["", "  ", "None", "5"])

    def test_empty_pool_matches_nothing(self):
        self.assertEqual(TX.unmatched([], [EMAIL]), [EMAIL])

    def test_no_needles_nothing_missing(self):
        self.assertEqual(TX.unmatched(self.POOL, []), [])

    def test_a_non_string_in_the_pool_is_ignored(self):
        self.assertEqual(TX.unmatched([None, 3, EMAIL], [EMAIL]), [])


if __name__ == "__main__":
    unittest.main()
