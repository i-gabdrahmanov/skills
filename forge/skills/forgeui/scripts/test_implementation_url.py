"""test_implementation_url.py — адрес реализации, dev-сервер, рецепты открытия, пин захвата (задача 4.9, спека §14.2, INV-D2, D4, D6, F6, F13, C23, QG-N2, N10).

Пинит: матрицу разрешения адреса по ролям (экран, состояние, диалог — диалог не наследует НИКОГДА) и то, что делает отсутствие адреса или рецепта в двух режимах
(interactive: нужное называется один раз и после записи в манифест вопроса нет; autonomous: именованный `blocked` с именем кадра и `supply`); dev-сервер на
настоящей команде `python -m http.server` (старт, ожидание адреса, гарантированное завершение ВСЕЙ группы вместе с потомками, повтор остановки, чужой работающий
сервер не стартует и не глушится, сервер не поднялся / умер — `implementation-unreachable` с хвостом вывода, interactive отказывает до запуска любого процесса);
рецепт открытия: записан `daiver_track_manifest`, переиспользован `daiver_verify_state`; пин рецепта, clip и viewport: первая записанная итерация закрепляет,
`daiver_screenshot` отказывает на отличающиеся, waitMs и адрес не пинятся.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import devserver as DS  # noqa: E402
from daiver.verify import implementation as IMPL  # noqa: E402
from test_verify_state import World, defect  # noqa: E402

OPEN = {"actions": [{"verb": "click", "selector": "#open-modal"}], "clipSelector": ".modal"}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def manifest(**frames):
    m = TR.new_manifest("K-1")
    for name, fields in frames.items():
        TR.upsert_frame(m, name, **fields)
    return m


class Resolution(unittest.TestCase):
    def test_role_matrix_of_the_address(self):
        m = manifest(main={}, other={}, hover={"role": "state", "stateOf": "main"}, hover2={"role": "state", "stateOf": "other"},
                     popup={"role": "dialog"}, own={"url": "https://app/own"})
        m["implementation"] = {"url": "https://app/track"}
        got = {n: TR.implementation_url(m, n) for n in ("main", "other", "hover", "hover2", "popup", "own")}
        self.assertEqual(got, {"main": "https://app/track", "other": None, "hover": "https://app/track", "hover2": None, "popup": None, "own": "https://app/own"})

    def test_state_follows_its_screens_own_url_and_a_dialog_never_inherits(self):
        m = manifest(main={}, other={"url": "https://app/other"}, st={"role": "state", "stateOf": "other"}, popup={"role": "dialog", "stateOf": "main"})
        m["implementation"] = {"url": "https://app/track"}
        self.assertEqual(TR.implementation_url(m, "st"), "https://app/other")
        self.assertIsNone(TR.implementation_url(m, "popup"), "диалог адрес не наследует, даже у экрана с адресом")
        TR.upsert_frame(m, "popup", url="https://app/modal-page")
        self.assertEqual(TR.implementation_url(m, "popup"), "https://app/modal-page")

    def test_complete_screen_resolves_without_questions(self):
        m = manifest(main={})
        m["implementation"] = {"url": "https://app/track", "devCommand": "npm run dev"}
        for mode in ("interactive", "autonomous"):
            r = IMPL.resolve(m, None, mode)
            self.assertEqual((r["url"], r["devCommand"], r["role"]), ("https://app/track", "npm run dev", "screen"))
            self.assertNotIn("need", r)
            self.assertNotIn("blocked", r)

    def test_interactive_asks_once_and_names_what_it_needs(self):
        m = manifest(main={})
        r = IMPL.resolve(m, None, "interactive")
        self.assertEqual(len(r["ask"]), 1)
        self.assertIn("ОДИН раз", r["need"][0])
        self.assertIn("daiver_track_manifest", r["need"][0])
        self.assertNotIn("blocked", r)

    def test_autonomous_blocks_by_name_with_the_frame_and_supply(self):
        m = manifest(main={}, second={})
        m["implementation"] = {"url": "https://app/track"}
        r = IMPL.resolve(m, "second", "autonomous")
        self.assertEqual((r["blocked"], r["frame"]), ("no-implementation", "second"))
        self.assertIn("url.second=", r["supply"])
        self.assertIn("не угадывай", r["nextStep"])

    def test_dialog_and_state_need_an_open_recipe(self):
        m = manifest(main={}, popup={"role": "dialog", "url": "https://app/page"}, st={"role": "state", "stateOf": "main"})
        m["implementation"] = {"url": "https://app/track"}
        r = IMPL.resolve(m, "popup", "interactive")
        self.assertEqual(len(r["need"]), 1)
        self.assertIn("ОДИН раз вместе с человеком", r["need"][0])
        self.assertIn("clipSelector", r["need"][0])
        r = IMPL.resolve(m, "popup", "autonomous")
        self.assertEqual((r["blocked"], r["frame"]), ("no-open-recipe", "popup"))
        self.assertIn("клик не выдумывай", r["nextStep"])
        self.assertEqual(IMPL.resolve(m, "st", "autonomous")["blocked"], "no-open-recipe")

    def test_a_dialog_without_url_asks_for_the_url_first(self):
        m = manifest(main={}, popup={"role": "dialog"})
        m["implementation"] = {"url": "https://app/track"}
        r = IMPL.resolve(m, "popup", "interactive")
        self.assertEqual(len(r["ask"]), 2)
        self.assertIn("диалог адрес не наследует", r["need"][0])
        self.assertEqual(IMPL.resolve(m, "popup", "autonomous")["blocked"], "no-implementation")

    def test_a_recorded_recipe_ends_the_question(self):
        m = manifest(main={}, popup={"role": "dialog", "url": "https://app/page", "open": OPEN})
        r = IMPL.resolve(m, "popup", "autonomous")
        self.assertEqual((r["open"], r.get("blocked"), r.get("need")), (OPEN, None, None))


class DevServerCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.cmd = f'"{sys.executable}" -m http.server {self.port} --bind 127.0.0.1'

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_waits_for_the_url_and_stops_the_whole_group(self):
        self.assertFalse(DS.reachable(self.url))
        with DS.start(self.cmd, self.dir, self.url, "autonomous", ready_timeout=30) as srv:
            self.assertTrue(srv.owned)
            self.assertTrue(DS.reachable(self.url))
            pid = srv.proc.pid
        self.assertFalse(DS.reachable(self.url), "после выхода из with адрес не отвечает")
        self.assertTrue(srv.stopped)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)                                           # группа убрана целиком

    def test_stop_is_idempotent_and_removes_the_log(self):
        srv = DS.start(self.cmd, self.dir, self.url, "autonomous", ready_timeout=30)
        log = srv.log
        self.assertTrue(log.is_file())
        self.assertTrue(srv.stop())
        self.assertFalse(srv.stop(), "повтор — пустая операция")
        self.assertFalse(log.exists())

    def test_the_shell_gives_env_and_children_leave_with_the_leader(self):
        marker = self.dir / "child.pid"
        cmd = f'FORGE_PORT={self.port} sh -c \'echo $$ > "{marker}"; exec "{sys.executable}" -m http.server $FORGE_PORT --bind 127.0.0.1\''
        with DS.start(cmd, self.dir, self.url, "autonomous", ready_timeout=30):
            self.assertTrue(DS.reachable(self.url))
            child = int(marker.read_text(encoding="utf-8").strip())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_an_already_running_server_is_neither_started_nor_killed(self):
        with DS.start(self.cmd, self.dir, self.url, "autonomous", ready_timeout=30) as first:
            second = DS.start("exit 7", self.dir, self.url, "autonomous", ready_timeout=5)
            self.assertFalse(second.owned)
            self.assertIsNone(second.proc)
            self.assertFalse(second.stop(), "чужое не глушим")
            self.assertTrue(DS.reachable(self.url))
            self.assertTrue(first.owned)

    def test_interactive_never_starts_anything(self):
        marker = self.dir / "started"
        for mode in ("interactive", None, "AUTONOMOUS"):
            with self.assertRaises(DS.DevServerError) as cm:
                DS.start(f'touch "{marker}"', self.dir, self.url, mode)
            self.assertIn("не стартует", str(cm.exception))
        self.assertFalse(marker.exists(), "процесс не запускался вовсе")

    def test_bad_url_is_refused_before_a_process_starts(self):
        marker = self.dir / "started"
        with self.assertRaises(DS.DevServerError):
            DS.start(f'touch "{marker}"', self.dir, "ftp://x/", "autonomous")
        self.assertFalse(marker.exists())

    def test_no_command_and_dead_url_is_a_named_stop(self):
        for cmd in (None, "", "   ", "a\nb"):
            with self.assertRaises(DS.DevServerFailed) as cm:
                DS.start(cmd, self.dir, self.url, "autonomous")
            b = cm.exception.blocked
            self.assertEqual(b["blocked"], "implementation-unreachable")
            self.assertIn("devCommand", b["supply"])
            self.assertEqual(b["url"], self.url)

    def test_a_server_that_dies_early_reports_its_output(self):
        with self.assertRaises(DS.DevServerFailed) as cm:
            DS.start('echo "порт занят: EADDRINUSE"; exit 3', self.dir, self.url, "autonomous", ready_timeout=20)
        b = cm.exception.blocked
        self.assertIn("кодом 3", b["reason"])
        self.assertIn("EADDRINUSE", b["serverOutput"])
        self.assertEqual(b["devCommand"], 'echo "порт занят: EADDRINUSE"; exit 3')

    def test_a_server_that_never_answers_is_stopped_after_the_timeout(self):
        t0 = time.monotonic()
        with self.assertRaises(DS.DevServerFailed) as cm:
            DS.start("sleep 60", self.dir, self.url, "autonomous", ready_timeout=1.0, poll=0.1)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertIn("не поднялся", cm.exception.blocked["reason"])

    def test_any_http_answer_counts_as_alive_connection_refused_does_not(self):
        with DS.start(self.cmd, self.dir, self.url, "autonomous", ready_timeout=30):
            self.assertTrue(DS.reachable(self.url + "нет-такой-страницы"), "404 — сервер жив")
        self.assertFalse(DS.reachable(self.url))
        for bad in (None, "", "file:///etc/passwd", "garbage"):
            self.assertFalse(DS.reachable(bad))

    def test_command_that_cannot_be_started_in_a_missing_directory(self):
        with self.assertRaises(DS.DevServerFailed) as cm:
            DS.start(self.cmd, self.dir / "нет-такого-каталога", self.url, "autonomous", ready_timeout=2)
        self.assertIn("не запустилась", cm.exception.blocked["reason"])


class Recipes(World):
    """Настоящий трек: рецепт хранится в манифесте и переиспользуется, захват пинится первой записанной итерацией."""

    def snap(self, recipe, viewport=(1280, 900)):
        return {"v": 1, "root": None, "elements": 3, "capture": {"ready": True, "recipe": recipe, "viewport": {"width": viewport[0], "height": viewport[1]},
                                                                  "readiness": {"ready": True}}}

    def fake(self, n, recipe, viewport=(1280, 900)):
        d = self.fake_iteration(n, compare="clean")
        (d / "snapshot.json").write_text(json.dumps(self.snap(recipe, viewport)), encoding="utf-8")

    def shot(self, n, **kw):
        args = {"track": self.TB.TRACK, "url": "http://127.0.0.1:1/", "iterationDir": f".daiver/tracks/{self.TB.TRACK}/verify/iter-{n}", "waitMs": 2000}
        args.update(kw)
        return T.call("daiver_screenshot", self.root, args)

    def test_the_open_recipe_is_saved_once_and_reused(self):
        m = TR.load(self.root, self.TB.TRACK)
        TR.upsert_frame(m, "popup", role="dialog")
        TR.save(self.root, m)
        a = self.opened(frame="popup")
        self.assertEqual(len(a["implementation"]["ask"]), 2, "адрес и рецепт спрошены")
        T.call("daiver_track_manifest", self.root, {"track": self.TB.TRACK, "frame": "popup", "set": {"url": "https://app/page", "open": OPEN}})
        b = self.opened(frame="popup")
        self.assertEqual((b["implementation"]["url"], b["implementation"]["open"]), ("https://app/page", OPEN))
        self.assertNotIn("need", b["implementation"], "вопроса больше нет")
        self.assertNotIn("blocked", b)
        self.assertIn("iter-1", b["iterationDir"])

    def test_the_state_tool_names_the_blocker_in_autonomous_mode(self):
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("mode: interactive", "mode: autonomous", 1), encoding="utf-8")
        a = self.opened()
        self.assertEqual((a["mode"], a["blocked"], a["implementation"]["frame"]), ("autonomous", "no-implementation", "main"))
        self.assertIn("не угадывай", a["nextStep"])

    def test_the_interactive_next_step_puts_the_missing_address_first(self):
        a = self.opened()
        self.assertTrue(a["nextStep"].startswith("СНАЧАЛА недостающее"))

    def test_the_first_recorded_iteration_pins_the_recipe_and_the_viewport(self):
        self.assertIsNone(self.opened()["pinned"])
        self.fake(1, OPEN)
        self.rec(1)
        self.assertEqual(self.state()["capture"], {"recipe": OPEN, "viewport": {"width": 1280, "height": 900}})
        self.assertEqual(self.opened()["pinned"]["recipe"], OPEN)
        self.assertIn("pinned", self.opened()["nextStep"])

    def test_a_different_clip_recipe_or_viewport_on_the_next_iteration_is_refused(self):
        self.fake(1, OPEN)
        self.rec(1)
        self.opened()
        with self.assertRaises(T.ToolError) as cm:
            self.shot(2, clipSelector=".other")
        self.assertIn("закреплённого прошлыми итерациями", str(cm.exception))
        self.assertIn("clipSelector", str(cm.exception))
        with self.assertRaises(T.ToolError) as cm:
            self.shot(2, clipSelector=".modal")                       # без actions: рецепт открытия отличается
        self.assertIn("actions", str(cm.exception))
        with self.assertRaises(T.ToolError) as cm:
            self.shot(2, actions=OPEN["actions"], clipSelector=".modal", waitForSelector=".ready")
        self.assertIn("waitForSelector", str(cm.exception))

    def test_viewport_drift_is_refused(self):
        self.fake(1, {})
        self.rec(1)
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("viewport: { width: 1280, height: 900 }", text)
        cfg.write_text(text.replace("viewport: { width: 1280, height: 900 }", "viewport: { width: 1024, height: 900 }"), encoding="utf-8")
        self.opened()
        with self.assertRaises(T.ToolError) as cm:
            self.shot(2)
        self.assertIn("viewport", str(cm.exception))

    def test_the_same_recipe_passes_the_pin_and_wait_and_url_are_not_pinned(self):
        self.fake(1, OPEN)
        self.rec(1)
        self.opened()
        for kw in ({"actions": OPEN["actions"], "clipSelector": ".modal"},
                   {"actions": OPEN["actions"], "clipSelector": ".modal", "waitMs": 5000, "url": "http://127.0.0.1:2/"}):
            try:
                self.shot(2, **kw)
            except T.ToolError as e:
                self.assertNotIn("закреплённого", str(e), "пин пропустил тот же рецепт; дальше — настоящий (недоступный) адрес")

    def test_the_first_iteration_is_free_until_it_is_recorded(self):
        for _ in range(2):
            try:
                self.shot(1, clipSelector=".a" if _ == 0 else ".b")
            except T.ToolError as e:
                self.assertNotIn("закреплённого", str(e))

    def test_baseline_and_reference_captures_are_not_pinned(self):
        self.assertIsNone(IMPL.read_pin(self.root / "нет.json"))
        self.fake(1, OPEN)
        self.rec(1)
        self.assertEqual(IMPL.pin_problems(None, {"clipSelector": "x"}, {"width": 1, "height": 1}), [])
        self.assertEqual(IMPL.pin_problems({"recipe": OPEN, "viewport": {"width": 1, "height": 2}}, OPEN, {"width": 1, "height": 2}), [])

    def test_a_record_with_a_drifted_capture_warns(self):
        self.fake(1, OPEN)
        self.rec(1)
        self.fake(2, {"clipSelector": ".other"})
        res = self.rec(2)
        self.assertTrue(any("INV-C23" in w for w in res["warnings"]))
        self.assertEqual(self.state()["capture"]["recipe"], OPEN, "пин не перезаписывается")

    def test_pin_read_ignores_a_broken_state_file(self):
        p = self.root / "bad.json"
        p.write_text("не json", encoding="utf-8")
        self.assertIsNone(IMPL.read_pin(p))
        p.write_text(json.dumps({"capture": {"recipe": {}}}), encoding="utf-8")
        self.assertIsNone(IMPL.read_pin(p), "нужны и recipe, и viewport")


if __name__ == "__main__":
    unittest.main()
