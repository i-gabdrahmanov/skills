"""test_suppress.py — каскад подавления находок (задача 4.6, спека §14.4.6, INV-C25, QG-U17, F4).

Пинит три объяснения (целиком неверный элемент → поддерево; другая копия → геометрия ЭТОГО узла; токен → тот же цвет того же узла) и то,
чего каскад НЕ делает: F4 — «высота корня» не глушит 16 цветовых находок детей; один слой не глушит один слой; подавленная находка
подавителем не становится; ORDER и DS_DEPRECATED ничего не объясняют; ближайшая причина побеждает; всё пересчитывается с нуля; ничего не
теряется (подавленное хранится с `suppressedBy`/`suppressedReason`, счётчики по слоям). Деревья — маленькие синтетические.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver.verify import ids as I  # noqa: E402
from daiver.verify import suppress as S  # noqa: E402


def node(i, kind="frame", kids=(), **kw):
    n = {"id": i, "kind": kind, "children": list(kids)}
    n.update(kw)
    return n


def index(root):
    nodes, parent, stack = {}, {}, [(root, None)]
    while stack:
        n, p = stack.pop()
        nodes[n["id"]] = n
        parent[n["id"]] = p
        stack.extend((k, n["id"]) for k in n["children"])
    return nodes, parent


def find(code, layer, design=None, impl=None, prop=None, **kw):
    f = {"code": code, "layer": layer, "design": design, "impl": impl, "severity": "major", "message": code}
    if prop:
        f["prop"] = prop
    f.update(kw)
    return f


TREE = node("root", kids=[node("card", kids=[node("title", "text"), node("body", "text")]), node("other", kids=[node("x", "text")])])


def run(findings, tree=TREE, itree=None):
    d, p = index(tree)
    i = index(itree)[0] if itree else None
    return S.apply(findings, d, p, i)


def by(findings, **kw):
    return next(f for f in findings if all(f.get(k) == v for k, v in kw.items()))


class Doctrine(unittest.TestCase):
    def test_root_height_does_not_silence_sixteen_child_colours(self):
        """F4: контейнерность без объяснения хуже отсутствия каскада."""
        kids = [node(f"c{k}", "text") for k in range(16)]
        tree = node("root", kids=kids)
        fs = [find("SIZE", "geometry", "root", prop="h")] + [find("COLOR", "color", f"c{k}", prop="color") for k in range(16)]
        res = run(fs, tree)
        self.assertEqual([f for f in fs if f.get("suppressedBy")], [])
        self.assertEqual(res["suppressed"], 0)

    def test_missing_element_silences_the_later_layers_in_its_design_subtree(self):
        fs = [find("MISSING", "structure", "card"), find("COLOR", "color", "title", prop="color"), find("SIZE", "geometry", "body", prop="w"),
              find("COLOR", "color", "x", prop="color")]
        run(fs)
        self.assertEqual(by(fs, design="title")["suppressedBy"], by(fs, code="MISSING")["id"])
        self.assertTrue(by(fs, design="body").get("suppressedReason"))
        self.assertNotIn("suppressedBy", by(fs, design="x"), "чужое поддерево не тронуто")
        self.assertNotIn("suppressedBy", by(fs, code="MISSING"))

    def test_extra_element_silences_the_impl_subtree(self):
        itree = node("p:root", kids=[node("p:extra", kids=[node("p:t", "text")])])
        fs = [find("EXTRA", "structure", None, "p:extra"), find("COLOR", "color", None, "p:t", prop="color")]
        run(fs, TREE, itree)
        self.assertEqual(fs[1]["suppressedBy"], fs[0]["id"])

    def test_text_difference_silences_the_geometry_of_that_node_only(self):
        fs = [find("TEXT", "structure", "title", prop="text"), find("SIZE", "geometry", "title", prop="w"), find("POSITION", "geometry", "title", prop="x"),
              find("SPACING", "geometry", "title", prop="padding.left"), find("SIZE", "geometry", "body", prop="w"),
              find("COLOR", "color", "title", prop="color")]
        run(fs)
        self.assertTrue(by(fs, code="SIZE", design="title").get("suppressedBy"))
        self.assertTrue(by(fs, code="POSITION").get("suppressedBy"))
        self.assertNotIn("suppressedBy", by(fs, code="SPACING"), "отступы контейнера текст не объясняет")
        self.assertNotIn("suppressedBy", by(fs, code="SIZE", design="body"))
        self.assertNotIn("suppressedBy", by(fs, code="COLOR"), "цвет — не геометрия")

    def test_token_silences_the_value_of_the_same_property_on_the_same_node(self):
        fs = [find("DS_TOKEN_MISSED", "ds", "title", prop="Brand/Primary", slot="text", file="a.tsx", line=1),
              find("COLOR", "color", "title", prop="color"), find("COLOR", "color", "title", prop="background"),
              find("COLOR", "color", "body", prop="color")]
        run(fs)
        self.assertEqual(by(fs, code="COLOR", design="title", prop="color")["suppressedBy"], by(fs, code="DS_TOKEN_MISSED")["id"])
        self.assertNotIn("suppressedBy", by(fs, code="COLOR", prop="background"), "другое свойство того же узла")
        self.assertNotIn("suppressedBy", by(fs, code="COLOR", design="body"), "тот же цвет другого узла")

    def test_same_layer_never_silences_itself(self):
        fs = [find("MISSING", "structure", "card"), find("MISSING", "structure", "title"), find("SIZE", "geometry", "body", prop="w"),
              find("POSITION", "geometry", "body", prop="x"), find("TEXT", "structure", "body", prop="text")]
        run(fs)
        self.assertNotIn("suppressedBy", fs[1], "MISSING внутри MISSING — тот же слой")
        self.assertNotIn("suppressedBy", fs[4], "TEXT внутри MISSING-поддерева — тот же слой")
        self.assertTrue(fs[2].get("suppressedBy") and fs[3].get("suppressedBy"), "а геометрия — слоем ниже — заглушена")

    def test_a_suppressed_finding_does_not_become_a_suppressor(self):
        fs = [find("MISSING", "structure", "card"), find("DS_TOKEN_MISSED", "ds", "title", prop="T", slot="text", file="a.tsx", line=1),
              find("COLOR", "color", "title", prop="color")]
        run(fs)
        self.assertTrue(by(fs, code="DS_TOKEN_MISSED").get("suppressedBy"))
        self.assertEqual(by(fs, code="COLOR")["suppressedBy"], by(fs, code="MISSING")["id"], "причину назвал заглушивший токен MISSING, а не токен")

    def test_order_and_deprecated_explain_nothing(self):
        fs = [find("ORDER", "structure", "card"), find("COLOR", "color", "title", prop="color"), find("SIZE", "geometry", "title", prop="w"),
              find("DS_DEPRECATED", "ds", "title", prop="Old", file="a.tsx", line=2, severity="minor"), find("COLOR", "color", "title", prop="background")]
        res = run(fs)
        self.assertEqual(res["suppressed"], 0)

    def test_ds_identity_findings_are_independent(self):
        fs = [find("TEXT", "structure", "title", prop="text"), find("DS_COMPONENT_WRONG", "ds", "title", prop="Button"),
              find("MISSING", "structure", "other"), find("DS_PROP_WRONG", "ds", None, prop="size", file="a.tsx", line=3)]
        run(fs)
        self.assertEqual([f.get("suppressedBy") for f in fs if f["layer"] == "ds"], [None, None])

    def test_nearest_cause_wins_not_the_screen_root(self):
        fs = [find("MISSING", "structure", "root"), find("MISSING", "structure", "card"), find("COLOR", "color", "title", prop="color")]
        run(fs)
        self.assertEqual(by(fs, code="COLOR")["suppressedBy"], by(fs, design="card")["id"])

    def test_recomputed_from_scratch_every_iteration(self):
        fs = [find("MISSING", "structure", "card"), find("COLOR", "color", "title", prop="color")]
        run(fs)
        first = [(f["id"], f.get("suppressedBy")) for f in fs]
        fs[1]["suppressedBy"], fs[1]["suppressedReason"] = "stale", "протухло"
        fs[0]["suppressedBy"] = "stale"
        run(fs)
        self.assertEqual([(f["id"], f.get("suppressedBy")) for f in fs], first)
        gone = [find("COLOR", "color", "title", prop="color", suppressedBy="stale", suppressedReason="x")]
        run(gone)
        self.assertNotIn("suppressedBy", gone[0], "причины нет — аннотация сброшена")

    def test_nothing_is_dropped_and_layers_are_counted(self):
        fs = [find("MISSING", "structure", "card"), find("COLOR", "color", "title", prop="color"), find("SIZE", "geometry", "body", prop="w"),
              find("COLOR", "color", "x", prop="color")]
        res = run(fs)
        self.assertEqual(len(fs), 4)
        self.assertEqual(res["layers"], {"color": {"total": 2, "suppressed": 1}, "geometry": {"total": 1, "suppressed": 1},
                                         "structure": {"total": 1, "suppressed": 0}})
        self.assertEqual(res["suppressed"], 2)
        self.assertTrue(all(f["suppressedReason"] for f in fs if f.get("suppressedBy")))

    def test_ids_are_assigned_and_stable_across_values(self):
        a = [find("SIZE", "geometry", "title", "p:t", prop="w", expected=100, actual=130)]
        b = [find("SIZE", "geometry", "title", "p:t", prop="w", expected=100, actual=190)]
        run(a)
        run(b)
        self.assertEqual(a[0]["id"], b[0]["id"])
        self.assertEqual(a[0]["id"], I.defect_id("SIZE", "title", "p:t", "w"))


if __name__ == "__main__":
    unittest.main()
