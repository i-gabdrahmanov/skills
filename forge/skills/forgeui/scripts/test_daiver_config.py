#!/usr/bin/env python3
"""test_daiver_config.py — конфиг forge-ui: YAML-подмножество, строгое чтение (спека §5.1,
QG-S3), первый запуск (daiver_init) и проверка аргументов инструментов (QG-S10).

Пинит: дефолты спеки; битый YAML → дефолты без исключения; опечатка не становится значением;
списанный ключ не работает; layout.infer выключают ровно off/false; источник библиотеки и
buildCommand не угадываются (без них проект не инициализирован); init без ответов ничего не
пишет, с ответами — пишет, существующий конфиг не перезаписывает; неизвестный аргумент
инструмента — отказ.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import config as C  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import status as ST  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import yamlite as Y  # noqa: E402

ANSWERS = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
           "buildCommand": "npm run build", "librarySource": "storybook-mcp",
           "storybookMcp": "storybook-ui", "libraryImport": "@corp/ui", "conventions": ["AGENTS.md"],
           "vision": "on"}


class Yaml(unittest.TestCase):
    def test_spec_examples_shape(self):
        doc = Y.loads("verify:\n  viewport: { width: 1280, height: 900 }  # коммент\n"
                      "rules:\n  - match: [Label, LabelCell]\n    outcome: component\n"
                      "  - match: \"/^Basic(.+)$/\"\n    component: \"$1\"\n")
        self.assertEqual(doc["verify"]["viewport"], {"width": 1280, "height": 900})
        self.assertEqual(doc["rules"][0], {"match": ["Label", "LabelCell"], "outcome": "component"})
        self.assertEqual(doc["rules"][1]["match"], "/^Basic(.+)$/")

    def test_yaml12_scalars(self):
        doc = Y.loads("a: on\nb: off\nc: true\nd: 0.5\ne: ~\nf: 'it''s'\ng: 24000\n")
        self.assertEqual(doc, {"a": "on", "b": "off", "c": True, "d": 0.5, "e": None,
                               "f": "it's", "g": 24000})

    def test_errors_carry_line(self):
        with self.assertRaises(Y.YamlError) as cm:
            Y.loads("a: 1\n  b: 2\n")
        self.assertIn("строка", str(cm.exception))
        with self.assertRaises(Y.YamlError):
            Y.loads("a: {x: 1\n")
        with self.assertRaises(Y.YamlError):
            Y.loads("a: 1\na: 2\n")


class Config(unittest.TestCase):
    def test_defaults_match_spec(self):
        cfg, warnings, problems = C.normalize({})
        wf, vf = cfg["workflow"], cfg["verify"]
        self.assertEqual(wf["weightBudget"], 24000)
        self.assertEqual((wf["mode"], wf["embeddedMode"], wf["autoRoute"]),
                         ("interactive", "autonomous", "signal"))
        self.assertEqual((wf["confirmPlan"], wf["confirmResolutions"]), (True, "medium"))
        self.assertEqual((wf["warnAfterVerifyIterations"], wf["autonomous"]["maxVerifyIterations"]), (3, 3))
        self.assertEqual(vf["tolerances"], {"px": 2, "color": 2, "fontSize": 0.5, "lineHeight": 1,
                                            "sizeRatio": 0.01, "sizeRatioMaxPx": 8})
        self.assertEqual(vf["viewport"], {"width": 1280, "height": 900})
        self.assertEqual(vf["visualDiffThreshold"], 0.05)
        self.assertTrue(cfg["layout"]["infer"])
        self.assertTrue(vf["vision"])
        self.assertTrue(any("library.source" in p for p in problems), "источник не угадывается")
        self.assertTrue(any("buildCommand" in p for p in problems))

    def test_strict_values(self):
        raw = {"workflow": {"weightBudget": "много", "mode": "auto", "maxVerifyIterations": 9,
                            "autonomous": {"maxVerifyIterations": 5}},
               "layout": {"infer": "of"},
               "verify": {"viewport": {"width": 1280.5, "height": 900},
                          "tolerances": {"px": 3, "color": "x"}}}
        cfg, warnings, _ = C.normalize(raw)
        self.assertEqual(cfg["workflow"]["weightBudget"], 24000, "опечатка не даёт плохой бюджет")
        self.assertEqual(cfg["workflow"]["mode"], "interactive")
        self.assertEqual(cfg["workflow"]["autonomous"]["maxVerifyIterations"], 5)
        self.assertTrue(any("списанный ключ" in w for w in warnings))
        self.assertTrue(cfg["layout"]["infer"], "опечатка «of» не выключает вывод раскладки")
        self.assertEqual(cfg["verify"]["viewport"], {"width": 1280, "height": 900})
        self.assertEqual(cfg["verify"]["tolerances"]["px"], 3)
        self.assertEqual(cfg["verify"]["tolerances"]["color"], 2, "каждый допуск падает в свой дефолт")

    def test_infer_off_spellings(self):
        for v in ("off", "false", False):
            self.assertFalse(C.normalize({"layout": {"infer": v}})[0]["layout"]["infer"], v)

    def test_library_source_requirements(self):
        _, _, p = C.normalize({"library": {"source": "storybook-mcp"}})
        self.assertTrue(any("storybookMcp" in x for x in p))
        _, _, p = C.normalize({"library": {"source": "stories"}})
        self.assertTrue(any("scanPaths" in x for x in p))
        _, _, p = C.normalize({"library": {"source": "guess"}})
        self.assertTrue(any("library.source" in x for x in p))

    def test_broken_file_falls_back_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".daiver").mkdir()
            (root / ".daiver" / "config.yaml").write_text("project: {oops\n", encoding="utf-8")
            res = C.load(root)
            self.assertFalse(res["initialized"])
            self.assertTrue(any("не разобран" in w for w in res["warnings"]))
            self.assertEqual(res["config"]["workflow"]["weightBudget"], 24000)


class Init(unittest.TestCase):
    def _repo(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.2.0", "@corp/ui": "1.2.3"},
            "devDependencies": {"typescript": "5.4.0", "less": "4.2.0"},
            "scripts": {"build": "vite build", "typecheck": "tsc --noEmit"}}), encoding="utf-8")
        (root / "package-lock.json").write_text("{}", encoding="utf-8")
        (root / "src" / "modules").mkdir(parents=True)
        (root / "AGENTS.md").write_text("conventions", encoding="utf-8")
        return root

    def test_detect_gives_evidence_not_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            d = I.detect(root)
            s = d["suggestions"]
            self.assertEqual(s["framework"][0]["value"], "react")
            self.assertIn("package.json", s["framework"][0]["evidence"])
            self.assertEqual([x["value"] for x in s["buildCommand"]],
                             ["npm run typecheck", "npm run build"])
            self.assertEqual(s["outputDir"][0]["value"], "src/modules")
            self.assertEqual(s["libraryImport"][0]["value"], "@corp/ui")
            self.assertNotIn("librarySource", s, "источник библиотеки не выводится из зависимостей")
            self.assertFalse((root / ".daiver").exists(), "детект ничего не пишет")

    def test_init_writes_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            res = I.init(root, ANSWERS)
            self.assertTrue(res["ok"], res)
            loaded = C.load(root)
            self.assertTrue(loaded["initialized"], loaded["problems"])
            self.assertEqual(loaded["config"]["project"]["buildCommand"], "npm run build")
            self.assertEqual(loaded["config"]["library"]["storybookMcp"], "storybook-ui")
            self.assertEqual(loaded["config"]["project"]["conventions"], ["AGENTS.md"])
            self.assertTrue((root / ".daiver" / "component-map.yaml").is_file())
            again = I.init(root, ANSWERS)
            self.assertFalse(again["ok"])
            self.assertIn("не перезаписывает", again["problems"][0])

    def test_init_refuses_incomplete_answers_in_one_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            bad = dict(ANSWERS, styling="scss")
            bad.pop("buildCommand")
            res = I.init(root, bad)
            self.assertFalse(res["ok"])
            self.assertTrue(any("buildCommand" in p for p in res["problems"]))
            self.assertTrue(any("styling" in p for p in res["problems"]))
            self.assertFalse((root / ".daiver" / "config.yaml").exists())

    def test_status_next_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            self.assertFalse(ST.status(root)["initialized"])
            self.assertIn("init", ST.status(root)["nextStep"])
            I.init(root, ANSWERS)
            st = ST.status(root)
            self.assertTrue(st["initialized"], st["problems"])
            self.assertIn("ui-spec", st["nextStep"])


class Tools(unittest.TestCase):
    def test_unknown_and_wrong_args_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(T.ToolError) as cm:
                T.call("daiver_status", Path(tmp), {"x": 1})
            self.assertIn("неизвестный аргумент", str(cm.exception))
            with self.assertRaises(T.ToolError):
                T.call("daiver_init", Path(tmp), {"answers": "react"})
            with self.assertRaises(T.ToolError):
                T.call("daiver_spec", Path(tmp), {"paths": ["../etc/passwd"]})
            with self.assertRaises(T.ToolError):
                T.call("daiver_spec", Path(tmp), {})

    def test_spec_by_feature_reads_story_ui_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            story = root / "docs" / "feature-pipeline" / "STOR-1"
            story.mkdir(parents=True)
            (story / "ui-spec.md").write_text("## Сценарии\nGiven a x, When b y, Then c z\n",
                                              encoding="utf-8")
            res = T.call("daiver_spec", root, {"feature": "STOR-1"})
            self.assertEqual([s["id"] for s in res["e2e"]], ["ui-spec#1"])
            res = T.call("daiver_spec", root, {"feature": "STOR-2"})
            self.assertFalse(res["hasSpecs"])
            self.assertEqual(len(res["warnings"]), 1, "нет ui-spec.md стори — сказано, не молча")


if __name__ == "__main__":
    unittest.main()
