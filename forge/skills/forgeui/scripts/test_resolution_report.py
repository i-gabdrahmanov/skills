#!/usr/bin/env python3
"""test_resolution_report.py — resolution-report.md, политика подтверждений и гейт резолва (задача 2.5; спека §10.5, §16;
INV C6, C7, F7; QG N5, R4).

Пинит как свойства КОДА (не текста брифа):
  • формат отчёта: один файл на трек `.daiver/tracks/<KEY>/resolution-report.md`, секция `## frame: <имя>` на кадр, таблица
    ЭВРИСТИЧЕСКИХ вердиктов (попадания в карту в неё не входят, но посчитаны), одинаковые вердикты одного figmaName+variants —
    одна строка «×N», нерешённое названо, предложенные правила карты — блоком YAML «как ляжет в файл»;
  • ссылки на узлы (§16): каждая — результат `outline.node_url` по `nodeUrlTemplate` кадра (тот же, что отдаёт daiver_outline);
    шаблона нет — голые id, и ни одного `](url)` в файле; путь внутри инстанса ведёт на инстанс; синтетический узел — голый id;
    скобки в адресе не ломают ссылку; адрес не выдумывается ни при каком входе;
  • детерминизм (принцип 2): один вход — побайтно один отчёт, порядок ключей хранилища и повторный вызов ничего не меняют,
    времени и случайности в модуле нет;
  • политика подтверждений: low спрашивается ВСЕГДА при любом пороге, порог confirmResolutions (high — всё, medium — medium и
    low, low — только low), иконка без маппинга при icons.fallback: ask; попадания в карту и копии чужого кадра — не
    спрашиваются; low идёт первым и не отрезается капом;
  • подтверждение человека — согласие `ui-resolve-<KEY>` на ТЕКУЩИЙ отчёт (`record_approval.py --bind-file`, настоящим скриптом):
    нет согласия / привязано к другому тексту / записано без привязки / дописано мимо писателя — не подтверждено; перегенерация
    отчёта снимает устаревшее согласие сама (иначе `record_approval` не записал бы новое по активному ключу);
  • инструмент `daiver_resolve_report` и гейт `ui_gate.py design`: нерешённое, отсутствующий и устаревший отчёт, неподтверждённые
    вердикты — exit 1 с причинами; резолв в порядке — причин резолва в выводе нет, но exit остаётся 1, пока нет плана и подписи
    (задача 2.6: гейт растёт вместе с шагом); гейт только читает диск;
  • `icons.fallback` в ответе `daiver_resolve_query` для иконки без правила карты.

Данные. «Modal» — настоящий образец (корень-фрейм и три инстанса, «Modal Header» ×2 с одинаковыми вариантами); большие кадры,
иконки, чужие форматы и многокадровые треки — СИНТЕТИКА из маленьких построителей ниже. Что НЕ проверено данными: боевых экранов
нет — число строк таблицы и вопросов на большом кадре, формулировки вопроса и то, как слабая модель читает `toConfirm`, выведены
из текста спеки; формулировки проверяются по смыслу (ключевые слова), боевой прогон — X.4.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

import forge_events as FE  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import ds_requirements as DR  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import outline as OL  # noqa: E402
from daiver import resolution_report as RR  # noqa: E402
from daiver import resolve as RS  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
RECORD_APPROVAL = HERE.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
UI_GATE = HERE / "ui_gate.py"
MODULE = HERE / "daiver" / "resolution_report.py"
URL = "https://pixso.example/a?item-id=2:153702"
TRACK = "STOR-100"
SHA = "a" * 64
REPORT_REL = f".daiver/tracks/{TRACK}/resolution-report.md"


# ── построители ───────────────────────────────────────────────────────────────

def leaf(i, kind="text", **kw):
    n = {"id": i, "kind": kind, "name": i}
    n.update(kw)
    return n


def frame(i, kids=(), **kw):
    n = {"id": i, "kind": "frame", "name": i, "children": list(kids)}
    n.update(kw)
    return n


def inst(i, figma, variants=None, slots=None, origin="library", **kw):
    """Инстанс как у адаптера Pixso: слоты — в `component.content`, детей нет."""
    comp: Dict[str, Any] = {"figmaName": figma, "origin": origin, "childrenRole": "content"}
    if variants:
        comp["variants"] = dict(variants)
    if slots:
        comp["content"] = [{"id": f"{i};{n}", "name": n, "text": t, "overridden": True,
                            "font": {"family": "Fixture Sans", "size": 14, "weight": 400}} for n, t in slots]
    n = {"id": i, "kind": "instance", "name": kw.pop("name", i), "component": comp}
    n.update(kw)
    return n


def icon(i, figma="Icon24", variants=None):
    return leaf(i, "icon", component={"figmaName": figma, "variants": dict(variants or {"Size": "24"})})


def screen(extra=()):
    """Корень «Page»: две одинаковые шапки, действие, именованный фрейм, иконка и текст (не кандидат)."""
    return frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"}, [("Header", "x")]),
                          inst("h2", "Modal Header", {"Steps": "OFF"}, [("Header", "y")]),
                          inst("a", "Modal Action", {"Variant": "Default"}, [("Label", "Ok"), ("Label", "No")]),
                          frame("header_bf_01"), icon("ic"), leaf("t")] + list(extra))


def init_repo(root: Path, source: str = "stories", scan_paths=("lib",)) -> None:
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}", encoding="utf-8")
    answers: Dict[str, Any] = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
                               "buildCommand": "npm run build", "librarySource": source, "scanPaths": list(scan_paths)}
    res = I.init(root, answers)
    assert res["ok"], res


def write_ds(root: Path, names: List[str], source: str = "stories", scan_paths=("lib",)) -> None:
    """Пара требований ДС с названными компонентами (формат 2.2, проходит настоящие валидаторы)."""
    comps = {n: {"title": None, "taxonomy": [], "props": {}, "propsClosed": False, "usedValues": {},
                 "import": {"path": "@t/ui", "named": n.split(".")[0]}, "stories": 1} for n in names}
    env = lambda d: {"version": "0.1.0", "role": "ds-requirements", "doc": d, "schema": 1}      # noqa: E731
    contract = {"daiver": env("contract"),
                "source": {"librarySource": source, "scanPaths": list(scan_paths), "scanner": "py/1", "sha256": SHA,
                           "files": 1, "skipped": []},
                "components": comps}
    details = {"daiver": env("details"), "source": {"sha256": SHA}, "components": {}}
    bad = DR.validate_contract(contract) + DR.validate_details(details) + DR.validate_pair(contract, details)
    assert not bad, bad
    for rel, doc in ((DR.CONTRACT_REL, contract), (DR.DETAILS_REL, details)):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DR.dumps(doc), encoding="utf-8")


def build_repo(root: Path, root_node, frames: Optional[Dict[str, dict]] = None, track: str = TRACK, fmt: str = "pixso",
               frame_url: str = URL):
    """Трек с кадром main (канон = root_node) и, если нужно, ещё кадрами {имя: корень} (раскладка как у 6.1)."""
    m = TR.new_manifest(track, created="2026-10-15T12:00:00Z")
    req = {"kind": "pixso", "path": "visual-requirements.pixso.json", "sha256": SHA, "pulledAt": "2026-10-15T12:00:00Z"}
    TR.upsert_frame(m, "main", frameUrl=frame_url, requirement=dict(req))
    for name in (frames or {}):
        TR.upsert_frame(m, name, frameUrl=frame_url, requirement=dict(req))
    TR.save(root, m)
    C.write_canonical(TR.frame_paths(root, m, "main")["canonical"], C.new_envelope(root_node, fmt))
    for name, node in (frames or {}).items():
        C.write_canonical(TR.frame_paths(root, m, name)["canonical"], C.new_envelope(node, fmt))
    return m


def verdict(node_id, decision="local", confidence="medium", rationale="потому что так видно по составу", **kw):
    v = {"nodeId": node_id, "decision": decision, "confidence": confidence, "rationale": rationale}
    v.update(kw)
    return v


def comp_verdict(node_id, component, confidence="medium", **kw):
    return verdict(node_id, "component", confidence, component=component, **kw)


def sections(text: str) -> Dict[str, str]:
    """Отчёт по кадрам: {имя кадра: текст секции}."""
    parts = re.split(r"^## frame: (.+)$", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def rows_of(section: str) -> List[List[str]]:
    """Строки таблицы секции без шапки и разделителя; ячейки делятся по неэкранированному «|»."""
    out = []
    for line in section.splitlines():
        if line.startswith("| ") and not line.startswith("| Узлы") and not line.startswith("|---"):
            out.append([c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]])
    return out


class Base(unittest.TestCase):
    """Репо с треком; ДС «ModalHeader/ModalAction/Modal» и конфиг есть, карты нет."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, root_node=None, frames=None, fmt="pixso", frame_url=URL, ds=("ModalHeader", "ModalAction", "Modal")):
        init_repo(self.root)
        write_ds(self.root, list(ds))
        return build_repo(self.root, root_node if root_node is not None else screen(), frames, fmt=fmt, frame_url=frame_url)

    def set_canon(self, root_node, name: str = "main") -> None:
        m = TR.load(self.root, TRACK)
        C.write_canonical(TR.frame_paths(self.root, m, name)["canonical"], C.new_envelope(root_node, "pixso"))

    def write_map(self, text: str) -> None:
        p = self.root / ".daiver" / "component-map.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def call(self, name, **args):
        return T.call(name, self.root, args)

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            T.call(name, self.root, args)
        return str(cm.exception)

    def fin(self, v, **kw):
        return self.call("daiver_resolve_finalize", track=TRACK, verdict=v, **kw)

    def report(self):
        return self.call("daiver_resolve_report", track=TRACK)

    def text(self) -> str:
        return (self.root / REPORT_REL).read_text(encoding="utf-8")

    def resolve_screen(self, low_action=False, skip_action=False):
        """Все шесть кандидатов «Page» решены: шапки — medium, действие — medium (или low), фрейм и иконка — high."""
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало, слоты Header"))
        if not skip_action:
            self.fin(comp_verdict("a", "ModalAction", "low" if low_action else "medium",
                                  rationale="две кнопки по слотам Label×2"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка экрана со своей тенью"))
        self.fin(verdict("header_bf_01", "local", "high", rationale="простая шапка страницы без аналога в библиотеке"))
        self.fin(verdict("ic", "local", "high", rationale="иконка 24 без правила карты: плейсхолдер Icon24"))

    def record(self, quote: Optional[str], bind: Optional[str] = REPORT_REL, track: str = TRACK, key: Optional[str] = None
               ) -> "subprocess.CompletedProcess[str]":
        cmd = [sys.executable, "-X", "utf8", str(RECORD_APPROVAL), "--project", str(self.root),
               "--key", key or RR.consent_key(track), "--approved-by", "user", "--reason", "вердикты резолва подтверждены"]
        if quote is not None:
            cmd += ["--evidence", quote]
        if bind:
            cmd += ["--bind-file", bind]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)

    def assertResolveClean(self, r: "subprocess.CompletedProcess[str]") -> None:
        """В выводе гейта нет ни одной причины резолва (дальше его останавливают план и подпись — задача 2.6)."""
        for phrase in ("не решено кандидатов", "отчёта резолва нет", "отчёт резолва устарел", "не подтверждены"):
            self.assertNotIn(phrase, r.stderr)

    def gate(self, step="design", feature=TRACK) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([sys.executable, "-X", "utf8", str(UI_GATE), step, "--project", str(self.root),
                               "--feature", feature], capture_output=True, text=True, encoding="utf-8", timeout=60)


# ── формат отчёта ─────────────────────────────────────────────────────────────

class Format(Base):
    def test_one_file_per_track_with_a_section_per_frame_in_manifest_order(self):
        self.repo(screen(), {"detail": frame("Detail", [inst("d1", "Modal Action", {"Variant": "Default"})])})
        self.resolve_screen()
        self.fin(verdict("Detail", "local", "high", rationale="оболочка второго кадра"), frame="detail")
        out = self.report()
        self.assertEqual(out["report"], REPORT_REL)
        text = self.text()
        self.assertTrue(text.startswith(f"# Отчёт резолва — {TRACK}\n"))
        self.assertEqual(list(sections(text)), ["main", "detail"])
        found = sorted(p.relative_to(self.root).as_posix() for p in (self.root / ".daiver").rglob("resolution-report.md"))
        self.assertEqual(found, [REPORT_REL], "один файл на трек, не по файлу на кадр")
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))

    def test_heuristic_verdicts_in_document_order_and_equal_ones_collapse_into_one_row(self):
        self.repo()
        self.resolve_screen()
        self.report()
        sec = sections(self.text())["main"]
        self.assertIn("Кандидатов: 6 · по карте: 0 · эвристических: 6 (строк 5) · не решено: 0", sec)
        rows = rows_of(sec)
        self.assertEqual([r[1] for r in rows],
                         ["фрейм «Page»", "Modal Header {Steps=OFF} ×2", "Modal Action {Variant=Default}",
                          "фрейм «header_bf_01»", "иконка Icon24 {Size=24}"])
        twins = rows[1]
        self.assertEqual(re.findall(r"\[(h\d)\]", twins[0]), ["h1", "h2"], "обе шапки — ссылками в одной ячейке")
        self.assertEqual(twins[5], "резолвер", "эталон — запись резолвера, а не копия")
        self.assertEqual(sec.count("Modal Header"), 1, "копия не размножает строку")

    def test_header_row_columns_and_policy_line(self):
        self.repo()
        self.resolve_screen()
        self.report()
        text = self.text()
        self.assertIn("| Узлы | Макет | Решение | Уверенность | Основание | Откуда | Спросить |\n|---|---|---|---|---|---|---|", text)
        self.assertIn("`workflow.confirmResolutions`): medium", text)
        self.assertIn("Попадания в карту сюда не входят", text)

    def test_map_hits_are_counted_but_never_listed(self):
        self.repo()
        self.write_map("rules:\n  - match: Modal Action\n    outcome: component\n    component: ModalAction\n")
        self.resolve_screen(skip_action=True)
        self.fin(comp_verdict("a", "ModalAction", "high", rationale="правило карты"))
        self.report()
        sec = sections(self.text())["main"]
        self.assertIn("по карте: 1 · эвристических: 5 (строк 4)", sec)
        self.assertNotIn("Modal Action", sec, "попадание в карту — не эвристический вердикт")
        self.assertEqual(len(rows_of(sec)), 4)

    def test_decision_cells_component_with_props_local_ignore_and_icon_placeholder(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"}), frame("header_bf_01"), icon("ic"),
                                 frame("footer_bf_02")]))
        self.fin(comp_verdict("h1", "ModalHeader", "high", props={"size": "lg", "closable": True}, rationale="имя и пропы"))
        self.fin(verdict("header_bf_01", "ignore", "high", rationale="служебный контейнер без содержимого"))
        self.fin(verdict("ic", "local", "high", rationale="иконка без правила карты"))
        self.fin(verdict("footer_bf_02", "local", "high", rationale="нижняя панель"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.report()
        cells = {r[1]: r[2] for r in rows_of(sections(self.text())["main"])}
        self.assertEqual(cells["Modal Header {Steps=OFF}"], 'component `ModalHeader` `{"closable": true, "size": "lg"}`')
        self.assertEqual(cells["фрейм «header_bf_01»"], "ignore")
        self.assertEqual(cells["иконка Icon24 {Size=24}"], "local — плейсхолдер иконки")
        self.assertEqual(cells["фрейм «footer_bf_02»"], "local")

    def test_hostile_text_cannot_break_the_table(self):
        self.repo(frame("Page", [inst("h1", "Modal | Header", {"Steps": "OFF"})]))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.fin(verdict("h1", "local", "medium", rationale="строка с | чертой,\nпереводом строки и `кодом` внутри"))
        self.report()
        rows = rows_of(sections(self.text())["main"])
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(len(r), 7, f"в строке ровно семь ячеек: {r}")
        hostile = [r for r in rows if "Modal" in r[1]][0]
        self.assertIn("строка с \\| чертой, переводом строки и 'кодом' внутри", hostile[4])

    def test_unresolved_candidates_are_named(self):
        self.repo()
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.report()
        sec = sections(self.text())["main"]
        self.assertIn("не решено: 5", sec)
        self.assertRegex(sec, r"Не решено \(5\): .*\[h1\]")
        self.assertEqual(len(rows_of(sec)), 1)

    def test_a_frame_without_heuristic_verdicts_has_no_table(self):
        self.repo(frame("Plain", [leaf("t")]))
        self.fin(verdict("Plain", "local", "high", rationale="контейнер экрана"))
        self.write_map("rules:\n  - match: Plain\n    outcome: local\n")
        self.fin(verdict("Plain", "local", "high", rationale="правило карты"))
        self.report()
        sec = sections(self.text())["main"]
        self.assertIn("Эвристических вердиктов нет.", sec)
        self.assertIn("по карте: 1", sec)
        self.assertNotIn("| Узлы", sec)

    def test_report_is_byte_deterministic_and_a_repeat_changes_nothing(self):
        self.repo()
        self.resolve_screen()
        first = self.report()
        data = (self.root / REPORT_REL).read_bytes()
        again = self.report()
        self.assertTrue(first["written"])
        self.assertFalse(again["written"], "тот же вход — файл не переписывается")
        self.assertEqual((self.root / REPORT_REL).read_bytes(), data)
        # порядок ключей хранилища (как и порядок записи) на отчёт не влияет
        store_path = TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["resolutions"]
        store = json.loads(store_path.read_text(encoding="utf-8"))
        store["verdicts"] = dict(reversed(list(store["verdicts"].items())))
        store_path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        self.assertFalse(self.report()["written"])
        self.assertEqual((self.root / REPORT_REL).read_bytes(), data)

    def test_module_has_no_clock_and_no_randomness(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                imported.add(n.module.split(".")[0])
        self.assertFalse(imported & {"time", "datetime", "random", "uuid", "secrets"}, imported)

    def test_map_rule_proposal_is_shown_as_paste_ready_yaml(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        rule = {"match": "Modal Header", "outcome": "component", "component": "ModalHeader"}
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало", mapRuleProposal=rule))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        sec = sections(self.text())["main"]
        self.assertIn("### Предложенные правила карты (в карте их нет)", sec)
        dry = self.call("daiver_map_append", track=TRACK, rule=rule, dryRun=True)
        self.assertIn("```yaml\n" + dry["yaml"] + "\n```", sec, "ровно тот YAML, который ляжет в файл после «да»")
        self.assertEqual([p["nodeId"] for p in out["proposals"]], ["h1"])
        self.assertEqual(out["proposals"][0]["yaml"], dry["yaml"])
        self.assertIn("предложено правило карты", out["toConfirm"][0])

    def test_a_proposal_that_no_longer_passes_the_map_check_is_said_so(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        rule = {"match": "Modal Header", "outcome": "component", "component": "ModalHeader"}
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало", mapRuleProposal=rule))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        (self.root / ".daiver" / "component-map.yaml").unlink()      # карту убрали после вердикта: append записать нечего
        out = self.report()
        self.assertIn("не проходит проверку карты", self.text())
        self.assertIn("invalid", out["proposals"][0])


# ── ссылки на узлы (§16) ──────────────────────────────────────────────────────

class Links(Base):
    def test_every_link_is_the_outline_template_applied_to_the_node(self):
        self.repo()
        self.resolve_screen()
        self.report()
        tpl = self.call("daiver_outline", track=TRACK)["nodeUrlTemplate"]
        self.assertEqual(tpl, "https://pixso.example/a?item-id={id}")
        text = self.text()
        links = re.findall(r"\[([^\]]+)\]\(([^)]*)\)", text)
        self.assertGreaterEqual(len(links), 6)
        for node_id, url in links:
            self.assertEqual(url, tpl.replace("{id}", node_id), "адрес собран по шаблону outline, а не придуман")
        self.assertEqual({i for i, _ in links}, {"Page", "h1", "h2", "a", "header_bf_01", "ic"})

    def test_no_template_means_bare_ids_and_not_a_single_invented_address(self):
        self.repo(fmt="dom")                      # у dom-канона ссылки на макет нет (§16): шаблон null
        self.resolve_screen()
        self.report()
        self.assertIsNone(self.call("daiver_outline", track=TRACK)["nodeUrlTemplate"])
        text = self.text()
        self.assertNotIn("](", text)
        self.assertNotIn("http", text.split("Кандидатов")[1], "ни одного адреса в таблице")
        self.assertIn("ссылок на узлы нет, вместо них голые id (адрес не выдумывается)", text)
        self.assertRegex(text, r"\| `h1`, `h2` \|")

    def test_figma_ids_use_dashes_and_the_node_id_parameter(self):
        self.repo(fmt="figma-json-a", frame_url="https://www.figma.com/design/KEY/Name?node-id=1-2&t=abc")
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.report()
        tpl = self.call("daiver_outline", track=TRACK)["nodeUrlTemplate"]
        self.assertEqual(tpl, "https://www.figma.com/design/KEY/Name?node-id={id}&t=abc")
        self.assertIn("(https://www.figma.com/design/KEY/Name?node-id=Page&t=abc)", self.text())
        self.assertEqual(OL.node_url(tpl, "12:34"), "https://www.figma.com/design/KEY/Name?node-id=12-34&t=abc")
        self.assertEqual(RR._link(tpl, "12:34"), "[12:34](https://www.figma.com/design/KEY/Name?node-id=12-34&t=abc)")

    def test_inner_path_links_the_instance_and_synthetic_nodes_stay_bare(self):
        tpl = OL.node_url_template("https://pixso.example/a?item-id=1:1", "pixso")
        self.assertEqual(RR._link(tpl, "I5:1;6:2"), "[I5:1;6:2](https://pixso.example/a?item-id=I5:1)")
        for synthetic in ("inferred:wrap-1", "dom:3", "code:main"):
            self.assertEqual(RR._link(tpl, synthetic), f"`{synthetic}`", "ссылки на синтетический узел нет")
        self.assertEqual(RR._link(None, "2:1"), "`2:1`")

    def test_parentheses_and_spaces_in_the_address_do_not_break_the_markdown_link(self):
        tpl = "https://pixso.example/a(b)?item-id={id}&x=1 2"
        link = RR._link(tpl, "2:1")
        self.assertEqual(link, "[2:1](https://pixso.example/a%28b%29?item-id=2:1&x=1%202)")
        self.assertEqual(len(re.findall(r"\)", link)), 1, "закрывающая скобка адреса — одна, последняя")

    def test_a_long_group_shows_five_links_and_counts_the_rest(self):
        twins = [inst(f"t{i}", "Modal Header", {"Steps": "OFF"}) for i in range(9)]
        self.repo(frame("Page", twins))
        self.fin(comp_verdict("t0", "ModalHeader", "high", rationale="имя совпало"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.report()
        cell = [r for r in rows_of(sections(self.text())["main"]) if "Modal Header" in r[1]][0]
        self.assertEqual(len(re.findall(r"\[t\d\]", cell[0])), 5)
        self.assertIn("… +4", cell[0])
        self.assertIn("×9", cell[1])


# ── политика подтверждений ────────────────────────────────────────────────────

def rec_of(source="resolver", conf="medium", decision="component", kind="instance"):
    return {"source": source, "confidence": conf, "decision": decision, "kind": kind}


class PolicyMatrix(unittest.TestCase):
    """`confirm_reason` — чистая функция: спрашивать или нет. Таблица записана руками, а не пересчитана правилом."""

    ASKED = {   # порог → какие уверенности спрашиваются у эвристического вердикта
        "high": {"high", "medium", "low"},
        "medium": {"medium", "low"},
        "low": {"low"},
    }

    def test_threshold_matrix(self):
        for threshold, asked in self.ASKED.items():
            for conf in ("high", "medium", "low"):
                got = RR.confirm_reason(rec_of(conf=conf), threshold, "placeholder")
                self.assertEqual(bool(got), conf in asked, f"порог {threshold}, уверенность {conf}: {got}")

    def test_low_is_always_asked_whatever_the_threshold_and_the_decision(self):
        for threshold in ("high", "medium", "low"):
            for decision in ("component", "local", "ignore"):
                for fallback in ("ask", "placeholder"):
                    reason = RR.confirm_reason(rec_of(conf="low", decision=decision), threshold, fallback)
                    self.assertTrue(reason, f"молчаливая low-резолюция запрещена (INV-F7): {threshold}/{decision}/{fallback}")
                    self.assertIn("всегда", reason)

    def test_map_hits_and_copies_are_not_asked_whatever_their_confidence(self):
        for source in ("map", "reuse"):
            for conf in ("high", "medium", "low"):
                self.assertIsNone(RR.confirm_reason(rec_of(source=source, conf=conf), "high", "ask"),
                                  f"{source}: правило карты решил человек, копию спрашивают у эталона")

    def test_an_icon_placeholder_is_asked_only_when_icons_fallback_is_ask(self):
        icon_local = rec_of(conf="high", decision="local", kind="icon")
        self.assertIn("icons.fallback", RR.confirm_reason(icon_local, "low", "ask"))
        self.assertIsNone(RR.confirm_reason(icon_local, "low", "placeholder"))
        self.assertIsNone(RR.confirm_reason(rec_of(conf="high", decision="local", kind="frame"), "low", "ask"),
                          "обычный local-фрейм под это правило не попадает")
        self.assertIsNone(RR.confirm_reason(rec_of(conf="high", decision="component", kind="icon"), "low", "ask"),
                          "иконка с компонентом — не плейсхолдер")

    def test_unknown_confidence_is_not_silently_trusted(self):
        # хранилище такого не допускает (схема), но политика не должна считать неизвестное «высоким»
        self.assertIsNone(RR.confirm_reason(rec_of(conf="mystery"), "low", "ask"))


class ToConfirm(Base):
    def set_threshold(self, value: str) -> None:
        p = self.root / ".daiver" / "config.yaml"
        text = p.read_text(encoding="utf-8")
        new, n = re.subn(r"confirmResolutions: \w+", f"confirmResolutions: {value}", text, count=1)
        self.assertEqual(n, 1)
        p.write_text(new, encoding="utf-8")

    def asked(self) -> List[str]:
        return self.report().get("toConfirm", [])

    def test_default_threshold_asks_medium_and_low_and_not_high(self):
        self.repo()
        self.resolve_screen(low_action=True)
        asked = self.asked()
        self.assertEqual(len(asked), 3)
        self.assertIn("Modal Action", asked[0])
        self.assertIn("Modal Header", asked[1])
        self.assertIn("иконка Icon24", asked[2], "иконка без маппинга при icons.fallback: ask — последней (high)")
        self.assertFalse(any("Page" in a or "header_bf_01" in a for a in asked), "high — не спрашиваем")

    def test_threshold_high_asks_everything_and_low_only_keeps_just_low(self):
        self.repo()
        self.resolve_screen(low_action=True)
        self.set_threshold("high")
        self.assertEqual(len(self.asked()), 5, "Page, header_bf_01, иконка (high), Modal Header (medium), Modal Action (low)")
        self.set_threshold("low")
        asked = self.asked()
        self.assertEqual(len(asked), 2, "low всегда и иконка при ask")
        self.assertIn("Modal Action", asked[0])
        self.assertIn("· low —", asked[0])
        self.assertIn("иконка Icon24", asked[1])

    def test_low_goes_first_and_the_cap_never_cuts_it_off(self):
        lows = [inst(f"l{i:02d}", f"Widget {i:02d}") for i in range(22)]
        mids = [inst(f"m{i}", f"Gadget {i}") for i in range(3)]
        self.repo(frame("Page", mids + lows), ds=[f"W{i:02d}" for i in range(22)] + [f"G{i}" for i in range(3)])
        for i in range(22):
            self.fin(comp_verdict(f"l{i:02d}", f"W{i:02d}", "low", rationale="догадка по имени"))
        for i in range(3):
            self.fin(comp_verdict(f"m{i}", f"G{i}", "medium", rationale="имя похоже"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        self.assertEqual(len(out["toConfirm"]), RR.ASK_LINES)
        self.assertEqual(out["toConfirmMore"], 5)
        self.assertTrue(all("· low —" in line for line in out["toConfirm"]), "обрезаются medium, а не low")
        self.assertIn("Остальное (5) — в файле", out["next"])
        text = self.text()
        self.assertEqual(text.count("| да |"), 25, "в файле отчёта помечены все 25 — человек может открыть его целиком")

    def test_a_copy_of_a_foreign_frame_is_asked_where_its_reference_lives_and_once(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"}), frame("header_bf_01")]),
                  {"detail": frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"})])})
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.fin(verdict("header_bf_01", "local", "high", rationale="простая шапка"))
        self.fin(verdict("Detail", "local", "high", rationale="оболочка второго кадра"), frame="detail")
        out = self.report()                      # раздаёт эталон в кадр detail
        self.assertEqual(out["unresolved"], 0)
        self.assertEqual(len(out["toConfirm"]), 1, "шапка спрашивается один раз")
        self.assertTrue(out["toConfirm"][0].startswith("[main] "), "в треке из двух кадров строка называет кадр")
        detail = rows_of(sections(self.text())["detail"])
        copy = [r for r in detail if "Modal Header" in r[1]][0]
        self.assertIn("копия: кадр «main», узел `h1`", copy[5])
        self.assertEqual(copy[6], "—", "копию человек видел у эталона")

    def test_single_frame_lines_carry_no_frame_prefix(self):
        self.repo()
        self.resolve_screen()
        self.assertFalse(any(line.startswith("[main]") for line in self.asked()))

    def test_icon_placeholder_is_asked_under_ask_and_silent_under_placeholder(self):
        self.repo(frame("Page", [icon("ic")]))
        self.fin(verdict("ic", "local", "high", rationale="иконка без правила карты: плейсхолдер Icon24"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        self.assertEqual(len(out["toConfirm"]), 1)
        self.assertIn("иконка без маппинга, icons.fallback: ask", out["toConfirm"][0])
        self.assertIn("local — плейсхолдер иконки", out["toConfirm"][0])
        cfg = self.root / ".daiver" / "component-map.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("fallback: ask", "fallback: placeholder"), encoding="utf-8")
        again = self.report()
        self.assertEqual(again["toConfirm"], [], "placeholder: человек иконки не видит")
        self.assertTrue(again["confirmed"])
        self.assertEqual(again["policy"].split("icons.fallback=")[1], "placeholder")

    def test_line_is_ready_to_paste_link_layout_decision_confidence_reason(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        self.fin(comp_verdict("h1", "ModalHeader", "low", props={"size": "lg"}, rationale="x" * 300))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        line = self.report()["toConfirm"][0]
        self.assertTrue(line.startswith("[h1](https://pixso.example/a?item-id=h1) Modal Header {Steps=OFF} → component "
                                        "`ModalHeader` `{\"size\": \"lg\"}` · low — "), line)
        self.assertLessEqual(len(line.split(" — ", 1)[1]), RR.ASK_RATIONALE, "основание в вопросе сжато, полное — в таблице")
        self.assertIn("x" * 300, self.text(), "в отчёте основание целиком")
        self.assertNotIn("\n", line)


# ── подтверждение человеком: согласие ui-resolve-<KEY>, привязанное к отчёту ──

class Consent(Base):
    def ready(self, low_action=True):
        self.repo()
        self.resolve_screen(low_action=low_action)
        return self.report()

    def approvals(self) -> List[dict]:
        return [r for r in FE.read_log(FE.approvals_path(self.root)) if r.get("key") == RR.consent_key(TRACK)]

    def test_the_class_is_a_consent_class_with_a_quote_required(self):
        self.assertTrue(FE.consent_required(RR.consent_key(TRACK)), "ui-resolve — в forge_events.CONSENT_PREFIXES")
        self.assertEqual(RR.consent_key(TRACK), "ui-resolve-STOR-100")
        self.ready()
        self.assertEqual(self.record(None).returncode, 2, "без цитаты пользователя согласие не пишется")
        self.assertEqual(self.record("верни как было, я передумал").returncode, 2, "цитата без слова согласия — не согласие")
        self.assertEqual(self.approvals(), [])
        done = self.record("да, все вердикты верны")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.approvals()), 1)

    def test_consent_bound_to_the_current_report_confirms(self):
        out = self.ready()
        self.assertFalse(out["confirmed"])
        self.assertEqual(out["consent"], {"key": "ui-resolve-STOR-100", "state": "none"})
        self.assertTrue(RR.check(self.root, TRACK), "до согласия гейт резолва не пускает")
        self.assertEqual(self.record("да, все вердикты верны").returncode, 0)
        out = self.report()
        self.assertTrue(out["confirmed"])
        self.assertEqual(out["consent"]["state"], "ok")
        self.assertNotIn("consentRevoked", out)
        self.assertEqual(RR.check(self.root, TRACK), [])

    def test_reading_never_spends_or_revokes_the_consent(self):
        self.ready()
        self.record("да, все вердикты верны")
        for _ in range(3):
            self.assertTrue(self.report()["confirmed"])
            self.assertEqual(RR.check(self.root, TRACK), [])
        self.assertEqual([r["kind"] for r in self.approvals()], ["approval"], "согласие не тратится чтением (в отличие от ui-map)")

    def test_changed_verdict_stales_the_report_then_regeneration_revokes_the_consent_and_a_new_one_works(self):
        self.ready()
        self.record("да, все вердикты верны")
        self.fin(comp_verdict("a", "ModalAction", "medium", rationale="пересмотрено: имя и слоты совпали"))
        before = (self.root / REPORT_REL).read_bytes()
        stale = RR.check(self.root, TRACK)
        self.assertEqual(len(stale), 1)
        self.assertIn("устарел", stale[0])
        self.assertEqual((self.root / REPORT_REL).read_bytes(), before, "гейт только читает")
        out = self.report()
        self.assertTrue(out["written"])
        self.assertFalse(out["confirmed"])
        self.assertEqual(out["consent"]["state"], "none")
        self.assertIn("прежнее согласие снято", out["consentRevoked"])
        self.assertEqual([r["kind"] for r in self.approvals()], ["approval", "approval-revoked"])
        # record_approval не пишет второе согласие по АКТИВНОМУ ключу — поэтому снимает движок, и новое «да» теперь пишется
        again = self.record("да, теперь верно")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("уже активен", again.stdout)
        self.assertTrue(self.report()["confirmed"])
        self.assertEqual(RR.check(self.root, TRACK), [])

    def test_a_new_candidate_after_the_consent_revokes_it_and_says_so_while_unresolved(self):
        self.ready()
        self.record("да, все вердикты верны")
        self.assertTrue(self.report()["confirmed"])
        self.set_canon(screen([inst("n", "Modal Header", {"Steps": "ON"})]))      # в макете появился ещё один инстанс
        out = self.report()
        self.assertEqual(out["unresolved"], 1)
        self.assertIn("consentRevoked", out)
        self.assertNotIn("confirmed", out)
        self.assertEqual([r["kind"] for r in self.approvals()], ["approval", "approval-revoked"])

    def test_consent_recorded_without_bind_file_does_not_count_and_is_cleared(self):
        self.ready()
        self.assertEqual(self.record("да, все вердикты верны", bind=None).returncode, 0)
        problems = RR.check(self.root, TRACK)
        self.assertEqual(len(problems), 1)
        self.assertIn("без --bind-file", problems[0])
        out = self.report()
        self.assertFalse(out["confirmed"])
        self.assertIn("consentRevoked", out)
        self.assertEqual(self.record("да, привязываю к отчёту").returncode, 0)
        self.assertTrue(self.report()["confirmed"])

    def test_consent_bound_to_another_file_does_not_count(self):
        self.ready()
        self.assertEqual(self.record("да, все вердикты верны", bind=".daiver/config.yaml").returncode, 0)
        problems = RR.check(self.root, TRACK)
        self.assertEqual(len(problems), 1)
        self.assertIn("привязано к другому тексту отчёта", problems[0])

    def test_consent_of_another_track_does_not_count(self):
        self.ready()
        self.assertEqual(self.record("да, все вердикты верны", key="ui-resolve-OTHER-7").returncode, 0)
        self.assertFalse(self.report()["confirmed"])
        self.assertEqual(self.record("да, и этот трек тоже", key="ui-map-STOR-100").returncode, 0)
        self.assertFalse(self.report()["confirmed"], "согласие на правило карты — не подтверждение вердиктов")

    def test_a_line_appended_by_hand_is_not_a_consent(self):
        self.ready()
        sha = RR.sha256_bytes((self.root / REPORT_REL).read_bytes())
        path = FE.approvals_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-10-18T00:00:00Z", "kind": "approval", "key": RR.consent_key(TRACK),
                                "bound_sha256": sha, "approved_by": "user", "evidence": "да, всё верно"}) + "\n")
        out = self.report()
        self.assertEqual(out["consent"]["state"], "none", "запись мимо record_approval (нет провенанса) не считается")
        self.assertFalse(out["confirmed"])

    def test_nothing_to_confirm_needs_no_consent(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="имя и пропы совпали"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        self.assertEqual(out["toConfirm"], [])
        self.assertTrue(out["confirmed"])
        self.assertEqual(RR.check(self.root, TRACK), [])
        self.assertIn("layoutHints", out["next"])

    def test_low_needs_the_consent_even_when_everything_else_is_high(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        self.fin(comp_verdict("h1", "ModalHeader", "low", rationale="догадка"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        problems = RR.check(self.root, TRACK)
        self.assertEqual(len(problems), 1, problems)      # отчёта ещё нет — это первое, что называет гейт
        self.report()
        problems = RR.check(self.root, TRACK)
        self.assertIn("не подтверждены", problems[0])
        self.assertIn("(1)", problems[0])
        self.assertIn(f"--bind-file {REPORT_REL}", problems[0], "гейт называет точную команду привязки")


# ── инструмент daiver_resolve_report ──────────────────────────────────────────

class ReportTool(Base):
    def test_unresolved_candidates_stop_the_question_but_the_report_is_written(self):
        self.repo()
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        self.assertEqual(out["unresolved"], 5)
        for key in ("toConfirm", "confirmed", "consent", "proposals"):
            self.assertNotIn(key, out, "пока есть нерешённое, человека не спрашивают")
        self.assertIn("Человека пока не спрашивай", out["next"])
        self.assertIn("daiver_resolve_candidates", out["next"])
        self.assertEqual(out["frames"], ["main: кандидатов 6 · по карте 0 · эвристических 1 · не решено 5"])
        self.assertTrue((self.root / REPORT_REL).is_file())

    def test_the_answer_is_thin_even_at_the_cap(self):
        lows = [inst(f"l{i:02d}", f"Widget {i:02d}", {"Size": "L"}) for i in range(30)]
        self.repo(frame("Page", lows), ds=[f"W{i:02d}" for i in range(30)])
        for i in range(30):
            self.fin(comp_verdict(f"l{i:02d}", f"W{i:02d}", "low", rationale="д" * 400))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        self.assertEqual(len(out["toConfirm"]), RR.ASK_LINES)
        size = len(json.dumps(out, ensure_ascii=False, indent=1))
        self.assertLess(size, 12000, f"ответ {size} знаков: слабая модель читает его целиком (принцип 4)")

    def test_refusals_name_the_cause(self):
        self.assertIn("трека «STOR-100» нет", self.refusal("daiver_resolve_report", track=TRACK))
        self.assertIn("ключ стори", self.refusal("daiver_resolve_report", track="../etc"))
        self.assertIn("неизвестный аргумент «frame»", self.refusal("daiver_resolve_report", track=TRACK, frame="main"))
        self.assertIn("нет обязательного аргумента «track»", self.refusal("daiver_resolve_report"))
        self.repo()
        canon = TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["canonical"]
        canon.unlink()
        msg = self.refusal("daiver_resolve_report", track=TRACK)
        self.assertIn("отчёт не построен", msg)
        self.assertIn("«main»", msg)
        self.assertIn("daiver_normalize", msg)
        self.assertFalse((self.root / REPORT_REL).exists(), "частичный отчёт не пишется")

    def test_a_broken_store_is_a_refusal_not_a_silent_reset(self):
        self.repo()
        self.resolve_screen()
        store = TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["resolutions"]
        store.write_text("{не json", encoding="utf-8")
        msg = self.refusal("daiver_resolve_report", track=TRACK)
        self.assertIn("resolutions.json", msg)
        self.assertIn("не JSON", msg)

    def test_proposals_are_listed_first_and_capped(self):
        names = [f"Thing {i}" for i in range(7)]
        self.repo(frame("Page", [inst(f"x{i}", n, {"V": "1"}) for i, n in enumerate(names)]),
                  ds=[f"Thing{i}" for i in range(7)])
        for i in range(7):
            rule = {"match": f"Thing {i}", "outcome": "component", "component": f"Thing{i}"}
            self.fin(comp_verdict(f"x{i}", f"Thing{i}", "medium", rationale="имя совпало", mapRuleProposal=rule))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        out = self.report()
        self.assertEqual(len(out["proposals"]), RR.PROPOSALS_SHOWN)
        self.assertEqual(out["proposalsMore"], 2)
        self.assertTrue(out["next"].startswith("сначала предложенные правила карты"))
        self.assertIn("перезапиши вердикт узла без mapRuleProposal", out["next"])
        self.assertEqual({p["nodeId"] for p in out["proposals"]} <= {f"x{i}" for i in range(7)}, True)

    def test_a_rule_the_human_accepted_takes_the_node_out_of_the_table(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        rule = {"match": "Modal Header", "outcome": "component", "component": "ModalHeader"}
        self.fin(comp_verdict("h1", "ModalHeader", "medium", rationale="имя совпало", mapRuleProposal=rule))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.assertEqual(len(self.report()["proposals"]), 1)
        self.assertEqual(self.record("да, добавляй правило", bind=None, key="ui-map-STOR-100").returncode, 0)
        self.call("daiver_map_append", track=TRACK, rule=rule)
        out = self.report()
        self.assertEqual(out["unresolved"], 1, "запись устарела: теперь это попадание в карту, вердикт перерешать по ней")
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="правило карты"))
        out = self.report()
        self.assertNotIn("proposals", out)
        self.assertEqual(out["toConfirm"], [])
        self.assertNotIn("Modal Header", self.text(), "теперь это попадание в карту — в таблице его нет")

    def test_a_resolver_record_that_the_map_now_agrees_with_counts_as_a_map_hit(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        rule = {"match": "Modal Header", "outcome": "component", "component": "ModalHeader"}
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="имя и пропы совпали", mapRuleProposal=rule))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.assertEqual(len(self.report()["proposals"]), 1)
        self.assertEqual(self.record("да, добавляй правило", bind=None, key="ui-map-STOR-100").returncode, 0)
        self.call("daiver_map_append", track=TRACK, rule=rule)
        out = self.report()
        self.assertEqual(out["unresolved"], 0, "вердикт high совпал с правилом — запись остаётся годной")
        self.assertNotIn("proposals", out, "правило уже в карте, предлагать нечего")
        self.assertEqual(out["frames"], ["main: кандидатов 2 · по карте 1 · эвристических 1 · не решено 0"])
        self.assertNotIn("Modal Header", self.text())

    def test_references_are_distributed_between_frames_by_the_report_writer(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]),
                  {"detail": frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"})])})
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="имя и пропы"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.fin(verdict("Detail", "local", "high", rationale="оболочка"), frame="detail")
        self.assertEqual(RR.build(self.root, TRACK, sync=False).frames[1].todo, ["d1"], "читающий путь ничего не раздаёт")
        out = self.report()
        self.assertEqual(out["unresolved"], 0)
        store = json.loads(TR.frame_paths(self.root, TR.load(self.root, TRACK), "detail")["resolutions"]
                           .read_text(encoding="utf-8"))
        self.assertEqual(store["verdicts"]["d1"]["source"], "reuse")

    def test_schema_is_closed_and_the_tool_works_over_json_rpc(self):
        schema = T.TOOLS["daiver_resolve_report"]["schema"]
        self.assertEqual((schema["required"], schema["additionalProperties"]), (["track"], False))
        self.assertEqual(list(schema["properties"]), ["track"])
        self.repo()
        self.resolve_screen()
        server = HERE.parents[2] / "hooks" / "daiver_mcp.py"
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "daiver_resolve_report", "arguments": {"track": TRACK}}}]
        r = subprocess.run([sys.executable, "-X", "utf8", str(server), "--project-root", str(self.root)],
                           input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        res = json.loads(r.stdout.splitlines()[0])["result"]
        self.assertFalse(res.get("isError"), res)
        body = json.loads(res["content"][0]["text"])
        self.assertEqual(body["report"], REPORT_REL)
        self.assertEqual(body["unresolved"], 0)


# ── гейт ui_gate.py design ────────────────────────────────────────────────────

def snapshot(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Gate(Base):
    def test_no_track_is_not_ready(self):
        init_repo(self.root)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("трека «STOR-100» нет", r.stderr)

    def test_unresolved_candidates_are_named_per_frame(self):
        self.repo()
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("кадр «main»: не решено кандидатов 5", r.stderr)
        self.assertIn("daiver_resolve_finalize", r.stderr)

    def test_missing_report_stale_report_and_unconfirmed_verdicts_each_stop_the_gate(self):
        self.repo()
        self.resolve_screen(low_action=True)
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("отчёта резолва нет", r.stderr)
        self.report()
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("не подтверждены", r.stderr)
        self.assertIn("record_approval.py --key ui-resolve-STOR-100", r.stderr)
        self.record("да, все вердикты верны")
        r = self.gate()
        self.assertEqual(r.returncode, 1, "резолв подтверждён, но плана секций ещё нет")
        self.assertResolveClean(r)
        self.assertIn("плана кадра нет", r.stderr)
        self.fin(comp_verdict("a", "ModalAction", "medium", rationale="пересмотрено"))
        r = self.gate()
        self.assertEqual(r.returncode, 1, "вердикт изменён после отчёта и согласия")
        self.assertIn("устарел", r.stderr)

    def test_everything_resolved_and_confirmed_still_stops_at_the_plan(self):
        self.repo()
        self.resolve_screen(low_action=True)
        self.report()
        self.record("да, все вердикты верны")
        r = self.gate()
        self.assertEqual(r.returncode, 1, "exit 0 невозможен без плана секций и подписи плана (задача 2.6)")
        self.assertResolveClean(r)
        self.assertIn("plan: плана кадра нет", r.stderr)
        self.assertIn("daiver_plan_write", r.stderr)

    def test_other_steps_are_untouched_and_arguments_are_checked(self):
        r = self.gate("verify")
        self.assertEqual(r.returncode, 3)
        self.assertIn("не реализована", r.stderr)
        r = self.gate("build")                          # ui-build реализован (3.2): сборки здесь не было — «не готово», а не «не реализовано»
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("не реализована", r.stderr)
        r = subprocess.run([sys.executable, "-X", "utf8", str(UI_GATE), "design", "--project", str(self.root)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_a_hand_edited_report_is_caught_and_the_consent_does_not_survive_it(self):
        self.repo()
        self.resolve_screen(low_action=True)
        self.report()
        self.record("да, все вердикты верны")
        self.assertResolveClean(self.gate())
        path = self.root / REPORT_REL
        path.write_text(path.read_text(encoding="utf-8").replace("| да |", "| — |"), encoding="utf-8")
        r = self.gate()
        self.assertEqual(r.returncode, 1, "правка файла руками не проходит: гейт пересобирает отчёт из вердиктов и сравнивает")
        self.assertIn("устарел", r.stderr)
        out = self.report()                                   # писатель возвращает файл к тому, что вытекает из вердиктов
        self.assertTrue(out["written"])
        self.assertTrue(out["confirmed"], "согласие было привязано к настоящему тексту, он вернулся — подтверждение снова верно")
        self.assertResolveClean(self.gate())

    def test_the_gate_only_reads(self):
        self.repo()
        self.resolve_screen(low_action=True)
        self.report()
        self.record("да, все вердикты верны")
        before = snapshot(self.root)
        for _ in range(2):
            self.gate()
        self.assertEqual(snapshot(self.root), before, "гейт не пишет ни отчёт, ни хранилища, ни журнал согласий")

    def test_the_gate_does_not_distribute_references_it_leaves_that_to_the_report_writer(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]),
                  {"detail": frame("Detail", [inst("d1", "Modal Header", {"Steps": "OFF"})])})
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="имя и пропы"))
        self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.fin(verdict("Detail", "local", "high", rationale="оболочка"), frame="detail")
        r = self.gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("кадр «detail»: не решено кандидатов 1", r.stderr)
        self.report()
        self.assertResolveClean(self.gate())


# ── иконка без правила карты: icons.fallback в ответе резолва ─────────────────

class IconQuery(Base):
    def query(self, node_id):
        return self.call("daiver_resolve_query", track=TRACK, nodeId=node_id)

    def test_an_unmapped_icon_gets_the_fallback_and_is_told_not_to_guess_a_library_icon(self):
        self.repo(frame("Page", [icon("ic")]))
        out = self.query("ic")
        self.assertEqual(out["iconFallback"], "ask")
        self.assertIn("библиотеку не угадывай", out["next"])
        self.assertIn("icons.fallback=ask", out["next"])
        self.assertIn("decision=local", out["next"])
        self.assertIn("daiver_resolve_report", out["next"])
        self.assertNotIn("daiver_ds_lookup", out["next"], "иконки не ищут в основной библиотеке")

    def test_placeholder_fallback_is_read_from_the_map(self):
        self.repo(frame("Page", [icon("ic")]))
        cfg = self.root / ".daiver" / "component-map.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("fallback: ask", "fallback: placeholder"), encoding="utf-8")
        out = self.query("ic")
        self.assertEqual(out["iconFallback"], "placeholder")
        self.assertIn("без вопроса", out["next"])

    def test_a_mapped_icon_a_plain_instance_and_a_resolved_icon_carry_no_fallback(self):
        self.repo(frame("Page", [icon("ic"), icon("ic2", "Icon16", {"Size": "16"}), inst("h1", "Modal Header")]))
        self.write_map("icons:\n  import:\n    path: \"@t/icons\"\n  fallback: ask\nrules:\n  - match: Icon24\n"
                       "    outcome: icon\n    icon: ArrowLeft\n")
        mapped = self.query("ic")
        self.assertNotIn("iconFallback", mapped)
        self.assertIn("правило карты сработало", mapped["next"])
        self.assertNotIn("iconFallback", self.query("h1"))
        self.fin(verdict("ic2", "local", "high", rationale="иконка без правила карты: плейсхолдер"))
        self.assertNotIn("iconFallback", self.query("ic2"), "вердикт уже записан")


# ── настоящий образец «Modal» ─────────────────────────────────────────────────

GOLDEN_MODAL = """\
# Отчёт резолва — STOR-100

Эвристические вердикты резолва: компонент выбран не по правилу карты (`component-map.yaml`), а по библиотеке или по составу узла. Попадания в карту сюда не входят. Файл строит движок (`daiver_resolve_report`), руками не правится.

Порог подтверждения (`workflow.confirmResolutions`): medium — вердикты с уверенностью medium и ниже показываются человеку, low — всегда; иконка без маппинга при `icons.fallback: ask` — тоже.

## frame: main

Макет: https://pixso.example/a?item-id=2:153702

Кандидатов: 4 · по карте: 0 · эвристических: 4 (строк 3) · не решено: 0

| Узлы | Макет | Решение | Уверенность | Основание | Откуда | Спросить |
|---|---|---|---|---|---|---|
| [2:153702](https://pixso.example/a?item-id=2:153702) | фрейм «Modal» | local | high | оболочка экрана со своей тенью | резолвер | — |
| [2:153703](https://pixso.example/a?item-id=2:153703), [22:63141](https://pixso.example/a?item-id=22:63141) | Modal Header {Steps=OFF} ×2 | component `ModalHeader` | medium | имя совпало, слоты Header и Description | резолвер | да |
| [2:153704](https://pixso.example/a?item-id=2:153704) | Modal Action {Variant=Default} | component `ModalAction` `{"variant": "default"}` | low | два слота Label — две кнопки; имя совпало неточно | резолвер | да |
"""


class RealSample(unittest.TestCase):
    """Настоящий экран: pull + normalize образца, вердикты руками. Золотой текст — единственное место, где формат отчёта
    зафиксирован целиком: меняется формат осознанно — меняется и он."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "repo"
        cls.root.mkdir()
        shutil.copyfile(str(FIXTURE), str(cls.root / "dsl.json"))
        T.call("daiver_pixso_pull", cls.root, {"track": TRACK, "dslPath": "dsl.json", "frameUrl": URL})
        T.call("daiver_normalize", cls.root, {"track": TRACK})
        init_repo(cls.root)
        write_ds(cls.root, ["ModalHeader", "ModalAction", "Modal"])
        call = lambda name, **a: T.call(name, cls.root, a)      # noqa: E731
        call("daiver_resolve_finalize", track=TRACK,
             verdict=comp_verdict("2:153703", "ModalHeader", "medium", rationale="имя совпало, слоты Header и Description"))
        call("daiver_resolve_finalize", track=TRACK,
             verdict=comp_verdict("2:153704", "ModalAction", "low", props={"variant": "default"},
                                  rationale="два слота Label — две кнопки; имя совпало неточно"))
        call("daiver_resolve_finalize", track=TRACK,
             verdict=verdict("2:153702", "local", "high", rationale="оболочка экрана со своей тенью"))
        cls.out = call("daiver_resolve_report", track=TRACK)
        cls.text = (cls.root / REPORT_REL).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_golden_report(self):
        self.assertEqual(self.text, GOLDEN_MODAL)

    def test_links_follow_the_outline_template_of_the_real_frame(self):
        tpl = T.call("daiver_outline", self.root, {"track": TRACK})["nodeUrlTemplate"]
        ids = re.findall(r"\[([^\]]+)\]\(([^)]*)\)", self.text)
        self.assertEqual([i for i, _ in ids], ["2:153702", "2:153703", "22:63141", "2:153704"])
        for node_id, url in ids:
            self.assertEqual(url, OL.node_url(tpl, node_id))

    def test_the_question_has_the_low_verdict_first_and_the_twin_headers_as_one_line(self):
        self.assertEqual(len(self.out["toConfirm"]), 2)
        self.assertIn("Modal Action", self.out["toConfirm"][0])
        self.assertIn("Modal Header {Steps=OFF} ×2", self.out["toConfirm"][1])
        self.assertEqual(self.out["frames"], ["main: кандидатов 4 · по карте 0 · эвристических 4 · не решено 0"])
        self.assertFalse(self.out["confirmed"])


# ── связка с остальными инструментами резолва ─────────────────────────────────

class Wiring(Base):
    def test_resolve_tools_point_to_the_report_when_the_frame_is_done(self):
        self.repo(frame("Page", [inst("h1", "Modal Header", {"Steps": "OFF"})]))
        self.fin(comp_verdict("h1", "ModalHeader", "high", rationale="имя и пропы"))
        out = self.fin(verdict("Page", "local", "high", rationale="оболочка"))
        self.assertIn("daiver_resolve_report", out["next"])
        self.assertIn("daiver_resolve_report", self.call("daiver_resolve_candidates", track=TRACK)["next"])

    def test_tool_description_names_what_the_model_must_read_from_the_answer(self):
        desc = T.TOOLS["daiver_resolve_report"]["description"]
        for word in ("toConfirm", "proposals", "confirmed", "unresolved", "ui-resolve", "low", "resolution-report.md"):
            self.assertIn(word, desc)


if __name__ == "__main__":
    unittest.main()
