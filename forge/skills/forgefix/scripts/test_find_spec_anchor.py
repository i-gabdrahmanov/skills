#!/usr/bin/env python3
"""Tests for find_spec_anchor.py — «к какой стори относится баг» из провенанса мастера,
старых task-plan'ов и связей Jira.

Ключевая семантика, которую фиксируют тесты: ключ стори сам по себе НЕ всегда однозначен —
за жизнь стори трогает несколько требований. Тогда либо тай-брейк даёт requirement-level
свидетельство (`sdd_ref` задачи, чей файл правит фикс), либо это решение пользователя (exit 3).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "find_spec_anchor.py"

MASTER = """# Master Spec: claims

## 5. Требования и сценарии

### REQ-0007: Создание заявки
Система создаёт заявку по валидному запросу.  [from: STOR-100 2026-01-01]
- **Given** валидный запрос **When** POST /api/claims **Then** 201

### REQ-0008: Поиск заявок
Система ищет заявки по фильтрам.  [from: STOR-200 2026-02-01]
- **Given** есть заявки **When** GET /api/claims **Then** список

### REQ-0009: Экспорт заявок
Система выгружает заявки.  [from: STOR-300 2026-03-01] [from: STOR-900 2026-04-01]
- **Given** есть заявки **When** GET /api/claims/export **Then** файл
"""

# Мастер, где STOR-100 за свою жизнь правила ДВА требования (типичная зрелая стори).
MASTER_TWO_REQS = MASTER.replace("[from: STOR-200 2026-02-01]", "[from: STOR-100 2026-02-01]")

PLAN_100 = {"feature_slug": "STOR-100", "title": "Создание заявки", "tasks": [
    {"id": "T1", "layers": ["service"],
     "artifacts": ["service/src/main/java/com/x/claim/ClaimService.java"],
     "reuses": ["com.x.claim.ClaimValidator"],
     "acceptance": ["Given a When b Then c"], "sdd_ref": "sdd.md#создание-заявки"}]}


def _project(master=MASTER, plans=None, deltas=None):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name).resolve()
    (root / "ground").mkdir(parents=True)
    (root / "ground/pipeline.json").write_text(json.dumps({
        "docs": {"mode": "in-repo", "docs_path": "docs", "feature_subdir": "feature-pipeline"},
    }), encoding="utf-8")
    (root / "spec.md").write_text(master, encoding="utf-8")
    for slug, plan in (plans or {}).items():
        d = root / "docs/feature-pipeline" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "task-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    for slug, delta in (deltas or {}).items():
        d = root / "docs/feature-pipeline" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "sdd.md").write_text(delta, encoding="utf-8")
    return td, root


STORY_DELTA = """# SDD: Создание заявки

## 3. Функциональные требования (Given-When-Then)

### Создание заявки
Система создаёт заявку по валидному запросу.
- **Given** валидный запрос **When** POST /api/claims **Then** 201
"""


def _run(root: Path, issue=None, changed=(), story=()):
    cmd = [sys.executable, str(SCRIPT), "--project-root", str(root),
           "--spec", str(root / "spec.md"), "--json"]
    for s in story:
        cmd += ["--story", s]
    for c in changed:
        cmd += ["--changed-file", c]
    if issue is not None:
        cmd += ["--issue-json", "-"]
        return subprocess.run(cmd, input=json.dumps(issue), capture_output=True, text=True)
    return subprocess.run(cmd, capture_output=True, text=True)


def _bug(**fields):
    base = {"summary": "NPE при сохранении", "description": "падает 500"}
    base.update(fields)
    return {"key": "BUG-1", "fields": base}


class TestFindSpecAnchor(unittest.TestCase):
    def test_parent_story_resolves_anchor(self):
        """Баг — подзадача стори STOR-100, требование помнит её в [from:]."""
        td, root = _project()
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-100"}))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["anchor"]["id"], "REQ-0007")

    def test_issuelink_resolves_anchor(self):
        td, root = _project()
        with td:
            r = _run(root, issue=_bug(issuelinks=[
                {"type": {"name": "Relates"}, "outwardIssue": {"key": "STOR-200"}}]))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["anchor"]["id"], "REQ-0008")

    def test_multi_tag_requirement_matches_any_of_its_stories(self):
        td, root = _project()
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-300"}))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["anchor"]["id"], "REQ-0009")

    def test_changed_file_resolves_anchor_without_jira_link(self):
        """Связи в Jira нет вовсе, но фикс правит файл, заведённый стори STOR-100."""
        td, root = _project(plans={"STOR-100": PLAN_100})
        with td:
            r = _run(root, issue=_bug(),
                     changed=["service/src/main/java/com/x/claim/ClaimService.java"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0007")
            self.assertTrue(any("task-plan" in e for e in v["candidates"][0]["evidence"]))

    def test_reuses_match_counts(self):
        """Файл не создавался задачей, но был в reuses (изменялся) — связь тоже засчитывается."""
        td, root = _project(plans={"STOR-100": PLAN_100})
        with td:
            r = _run(root, changed=["service/src/main/java/com/x/claim/ClaimValidator.java"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["anchor"]["id"], "REQ-0007")

    def test_story_touching_two_requirements_is_ambiguous(self):
        """Одного ключа стори мало: STOR-100 правила и REQ-0007, и REQ-0008 — выбор за
        пользователем, модель не угадывает."""
        td, root = _project(master=MASTER_TWO_REQS)
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-100"}))
            self.assertEqual(r.returncode, 3)
            v = json.loads(r.stdout)
            self.assertEqual(v["status"], "ambiguous")
            self.assertEqual({c["id"] for c in v["candidates"]}, {"REQ-0007", "REQ-0008"})
            self.assertIsNone(v["anchor"])

    def test_sdd_ref_breaks_tie_between_requirements_of_same_story(self):
        """Тай-брейк — requirement-level свидетельство: sdd_ref задачи, чей файл правит фикс."""
        td, root = _project(master=MASTER_TWO_REQS, plans={"STOR-100": PLAN_100})
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-100"}),
                     changed=["service/src/main/java/com/x/claim/ClaimService.java"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0007")
            self.assertGreater(v["candidates"][0]["score"], v["candidates"][1]["score"])

    def test_not_found_when_no_evidence(self):
        td, root = _project()
        with td:
            r = _run(root, issue=_bug())
            self.assertEqual(r.returncode, 3)
            v = json.loads(r.stdout)
            self.assertEqual(v["status"], "not_found")
            self.assertEqual(v["candidates"], [])

    def test_mention_weaker_than_parent(self):
        """Упоминание ключа в тексте не перебивает parent — иначе якорь тянуло бы на любой ключ."""
        td, root = _project()
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-100"},
                                      summary="как в STOR-200", description="см. STOR-200"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0007")
            self.assertGreater(v["candidates"][0]["score"], v["candidates"][1]["score"])

    # ── самый дешёвый источник: пользователь назвал слаг стори ────────────────────
    def test_explicit_story_resolves_without_any_other_evidence(self):
        """Ни связей Jira, ни изменённых файлов — просто «баг по STOR-200»."""
        td, root = _project()
        with td:
            r = _run(root, issue=_bug(), story=["STOR-200"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0008")
            self.assertIn("названа пользователем", " ".join(v["candidates"][0]["evidence"]))

    def test_explicit_story_narrows_ambiguity(self):
        """Баг связан с STOR-100 (два требования), но пользователь уточнил стори — шум ушёл."""
        td, root = _project(master=MASTER_TWO_REQS)
        with td:
            # без уточнения — два кандидата
            self.assertEqual(_run(root, issue=_bug(parent={"key": "STOR-100"})).returncode, 3)
            # уточнили другую стори — кандидаты только её
            r = _run(root, issue=_bug(parent={"key": "STOR-100"}), story=["STOR-300"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0009")
            self.assertEqual([c["id"] for c in v["candidates"]], ["REQ-0009"])

    def test_story_delta_matches_even_without_provenance(self):
        """Провенанс [from:] в мастере потёрли руками — связь даёт СОБСТВЕННАЯ дельта стори."""
        master_no_tags = MASTER.replace("  [from: STOR-100 2026-01-01]", "")
        td, root = _project(master=master_no_tags, deltas={"STOR-100": STORY_DELTA})
        with td:
            r = _run(root, issue=_bug(), story=["STOR-100"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0007")
            self.assertIn("дельте стори", " ".join(v["candidates"][0]["evidence"]))

    def test_story_delta_breaks_tie_inside_one_story(self):
        """Стори правила два требования, но её дельта называет одно — оно и якорь."""
        td, root = _project(master=MASTER_TWO_REQS, deltas={"STOR-100": STORY_DELTA})
        with td:
            r = _run(root, issue=_bug(), story=["STOR-100"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0007")
            self.assertGreater(v["candidates"][0]["score"], v["candidates"][1]["score"])

    def test_unknown_story_falls_back_to_other_evidence(self):
        """Названная стори не оставила следов — не молчим, показываем что нашли иначе."""
        td, root = _project()
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-100"}), story=["STOR-999"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["anchor"]["id"], "REQ-0007")

    def test_hint_asks_for_story_first(self):
        """Подсказка при exit 3 — сначала простой вопрос про стори, а не список REQ-ID."""
        td, root = _project()
        with td:
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--project-root", str(root),
                 "--spec", str(root / "spec.md"), "--issue-json", "-"],
                input=json.dumps(_bug()), capture_output=True, text=True)
            self.assertEqual(r.returncode, 3)
            self.assertIn("по какой стори", r.stderr.lower())

    def test_empty_master_is_not_found_not_crash(self):
        td, root = _project(master="# Master Spec: claims\n\n## 5. Требования и сценарии\n")
        with td:
            r = _run(root, issue=_bug(parent={"key": "STOR-100"}))
            self.assertEqual(r.returncode, 3)
            self.assertEqual(json.loads(r.stdout)["master_requirements"], 0)

    # ── прошлые фиксы стори (<стори>/fixes/<баг>/) — такое же свидетельство ────────────
    def test_past_fix_plan_of_story_counts_as_file_evidence(self):
        """Файл уже чинили внутри STOR-100 — кандидат СТОРИ, а не баг: требование за стори."""
        plan = json.loads(json.dumps(PLAN_100))
        plan["feature_slug"] = "BUG-500"
        td, root = _project(plans={"STOR-100/fixes/BUG-500": plan})
        with td:
            r = _run(root, issue=_bug(),
                     changed=["service/src/main/java/com/x/claim/ClaimService.java"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v = json.loads(r.stdout)
            self.assertEqual(v["anchor"]["id"], "REQ-0007")
            self.assertTrue(any("STOR-100/fixes/BUG-500" in e
                                for e in v["candidates"][0]["evidence"]))

    def test_past_fix_delta_titles_belong_to_story(self):
        """Провенанс мастера потёрли, собственной дельты у стори нет — связь даёт дельта её фикса."""
        master_no_tags = MASTER.replace("  [from: STOR-100 2026-01-01]", "")
        td, root = _project(master=master_no_tags,
                            deltas={"STOR-100/fixes/BUG-500": STORY_DELTA})
        with td:
            r = _run(root, issue=_bug(), story=["STOR-100"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["anchor"]["id"], "REQ-0007")


if __name__ == "__main__":
    unittest.main()
