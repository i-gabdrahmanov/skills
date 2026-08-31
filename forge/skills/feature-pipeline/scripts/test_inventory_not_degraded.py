#!/usr/bin/env python3
"""test_inventory_not_degraded.py — пины против ТИХОГО вырождения гейтов без инвентаря.

Инвентарь (`ground/inventory/`) эфемерный: он не коммитится и на свежем клоне его нет. Два
гейта опираются на него, и оба раньше «мягко» деградировали ровно тогда, когда защита нужна:

  • design-judge → check_taskplan: `--scan` передавался только `if scan_dir.exists()`, а сами
    warning'и внутри check_taskplan завязаны на факт передачи `--scan`. Итог: инвентаря нет →
    кросс-чеки reuses/модулей не выполняются и об этом НИКТО не говорит, судья рапортует PASS.
  • reuse-judge → scan/reuse.json: пустой каталог зависимостей делает `lib_available` всегда
    False, и каждый найденный велосипед падает с блокирующего FAIL до необязательного WARN —
    гейт бесшумно превращается в советчика.

Оба чинятся одинаково: инвентарь снимается на месте (идемпотентно, секунды, без LLM), а если
снять не удалось — это громкий warning, а не тишина.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run_judge as RJ  # noqa: E402

CHECK_TASKPLAN = SCRIPTS.parents[1] / "tech-design" / "scripts" / "check_taskplan.py"

ENTITY = ("package com.x.domain;\nimport javax.persistence.Entity;\n@Entity\n"
          "public class Alpha { private Long id; }\n")
SERVICE = ("package com.x.service;\nimport org.springframework.stereotype.Service;\n@Service\n"
           "public class AService { public void run() {} }\n")
WHEEL = ("package com.x;\npublic class Wheel {\n"
         "  boolean blank(String s){ return s == null || s.trim().isEmpty(); }\n}\n")


def _mkproj(root: Path) -> None:
    (root / "build.gradle").write_text(
        "dependencies { implementation 'org.apache.commons:commons-lang3:3.14.0' }\n",
        encoding="utf-8")
    for sub, name, txt in (("domain", "Alpha.java", ENTITY), ("service", "AService.java", SERVICE)):
        d = root / "src/main/java/com/x" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(txt, encoding="utf-8")


def _plan(module: str, reuses: list[str]) -> dict:
    return {"feature_slug": "demo", "title": "T", "coverage_threshold": 0.8,
            "tasks": [{"id": "T1", "acceptance": "Given X When Y Then Z",
                       "artifacts": ["src/main/java/Foo.java"], "layers": ["service"],
                       "depends_on": [], "module": module, "reuses": reuses}]}


class DesignGateTest(unittest.TestCase):
    def test_gate_catches_hallucinated_class_without_prebuilt_inventory(self):
        """Главный пин: инвентаря на диске НЕТ, а выдуманный класс всё равно валит гейт."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _mkproj(root)
            self.assertFalse((root / "ground" / "inventory").exists())

            note = RJ._ensure_inventory(root)
            self.assertIsNone(note, f"инвентарь не снялся: {note}")
            self.assertTrue((root / "ground/inventory/scan/components.json").exists())

            scan = root / "ground/inventory/scan"
            pf = root / "plan.json"

            pf.write_text(json.dumps(_plan(root.name, ["GhostService"])), encoding="utf-8")
            r = subprocess.run([sys.executable, str(CHECK_TASKPLAN), str(pf), "--scan", str(scan),
                                "--json"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, "выдуманный класс обязан валить гейт")
            self.assertIn("GhostService", r.stdout)

            pf.write_text(json.dumps(_plan(root.name, ["AService"])), encoding="utf-8")
            r = subprocess.run([sys.executable, str(CHECK_TASKPLAN), str(pf), "--scan", str(scan),
                                "--json"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"реальный класс не должен валить: {r.stdout}")

    def test_empty_project_yields_loud_warning_not_silence(self):
        with tempfile.TemporaryDirectory() as d:
            note = RJ._ensure_inventory(Path(d))
            self.assertIsNotNone(note, "пустой инвентарь обязан давать warning, а не тишину")
            self.assertIn("инвентарь пуст", note)


class ReuseGateTest(unittest.TestCase):
    def test_reuse_judge_blocks_without_prebuilt_inventory(self):
        """Велосипед, покрытый зависимостью проекта, обязан быть BLOCKING даже когда
        каталога зависимостей на диске изначально нет."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            git = ["git", "-C", str(root)]
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(git + ["config", "user.email", "t@t"], check=True)
            subprocess.run(git + ["config", "user.name", "t"], check=True)
            _mkproj(root)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-qm", "base"], check=True)

            (root / "src/main/java/com/x/Wheel.java").write_text(WHEEL, encoding="utf-8")
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-qm", "wheel"], check=True)

            self.assertFalse((root / "ground" / "inventory").exists())
            prev_root, prev_base = RJ.PROJECT_ROOT, RJ.DIFF_BASE
            try:
                RJ.PROJECT_ROOT, RJ.DIFF_BASE = root, "HEAD~1"
                verdict = RJ.check_reuse("demo", None)
            finally:
                RJ.PROJECT_ROOT, RJ.DIFF_BASE = prev_root, prev_base

            self.assertFalse(verdict["passed"], "велосипед обязан быть блокирующим")
            self.assertTrue(any("StringUtils" in b for b in verdict["blocking_issues"]),
                            verdict["blocking_issues"])
            self.assertFalse(any("каталог зависимостей пуст" in w for w in verdict["warnings"]),
                             "каталог должен был подняться сам, а не деградировать")


if __name__ == "__main__":
    unittest.main(verbosity=2)
