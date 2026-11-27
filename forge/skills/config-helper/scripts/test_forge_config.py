from __future__ import annotations

import json
from pathlib import Path
from socket import create_connection
import tempfile
from threading import Event, Lock, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import forge_config as ui


class ConfigEditor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project / "ground").mkdir()
        self.editor = ui.Editor(self.project)
        self.policy = self.project / "ground/policy.json"

    def preview(self, file, changes, feature=""):
        return self.editor.preview({
            "revision": self.editor.catalog(feature)["revisions"][file],
            "changes": changes,
            "feature": feature,
        })

    def test_catalog_reads_legacy_and_keeps_files_untouched(self):
        legacy = self.project / "ground/pipeline.json"
        legacy.write_text('{"docs": {"mode": "separate-repo"}}', encoding="utf-8")
        result = self.editor.catalog("")
        self.assertEqual({row["id"] for row in result["rows"]}, set(self.editor.entries))
        docs = next(row for row in result["rows"] if row["id"] == "docs.mode")
        self.assertEqual(docs["value"], "separate-repo")
        self.assertEqual(docs["source"], "file:pipeline.json (legacy)")
        self.assertFalse(docs["editable"])
        self.assertEqual(list(legacy.parent.iterdir()), [legacy])
        self.policy.write_text("{oops", encoding="utf-8")
        self.assertIn(str(self.policy), self.editor.catalog("")["errors"]["pipeline"])

    def test_batch_conflict_and_protected_change(self):
        original = '{"custom": 5, "_incomplete": ["project.build_system answer"]}'
        self.policy.write_text(original, encoding="utf-8")
        build = dict(op="set", id="project.build_system", value="none")
        invalid = dict(op="set", id="quality.coverage_threshold", value="1,5")
        with self.assertRaisesRegex(ui.FieldError, "больше максимума"):
            self.preview("pipeline", [build, invalid])
        protected = dict(invalid, value="0,85")
        with patch.object(ui.config, "_is_enforcement_switch",
                          side_effect=lambda key: key == protected["id"]):
            result = self.preview("pipeline", [build, protected])
            self.assertEqual(result["diff"][1]["new"], 0.85)
            with patch("forge_events.approval", return_value=None):
                with self.assertRaisesRegex(ui.FieldError, "approval-маркер"):
                    self.editor.save(result)
        self.assertEqual(self.policy.read_text(encoding="utf-8"), original)
        result = self.preview("pipeline", [build])
        self.policy.write_text('{"custom": 6}', encoding="utf-8")
        with self.assertRaises(ui.Conflict):
            self.editor.save(result)
        self.assertEqual(json.loads(self.policy.read_text(encoding="utf-8")), {"custom": 6})
        result = self.preview("pipeline", [build])
        self.editor.save(result)
        self.assertEqual(json.loads(self.policy.read_text(encoding="utf-8")),
                         {"custom": 6, "project": {"build_system": "none"}})

    def test_manifest_scope_mask_and_shared_lock(self):
        feature = "forgefix/one"
        target = self.project / f"ground/statements/{feature}/manifest.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"version":2,"skill":"forgefix","decisions":{"auto_max_risk":"R1"},"custom":42}',
                          encoding="utf-8")
        with self.assertRaisesRegex(ui.FieldError, "manifest.skill"):
            self.preview("manifest", [dict(op="set", id="inputs.mode", value="full")], feature)
        result = self.preview("manifest", [
            dict(op="set", id="decisions.auto_max_risk", value="R2"),
            dict(op="set", id="inputs.story", value="STOR-12"),
        ], feature)
        self.assertEqual(result["diff"][0]["old"], ui.MASK)
        self.assertEqual(result["diff"][0]["new"], ui.MASK)
        self.editor.save(result)
        self.assertTrue(target.with_suffix(".lock").exists())
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["custom"], 42)
        self.assertEqual(data["inputs"]["story"], "STOR-12")
        self.assertEqual(data["decisions"]["auto_max_risk"], "R2")

    def test_structured_phase_and_risk_use_standard_checks(self):
        self.policy.write_text("{}", encoding="utf-8")
        phase = dict(op="phase", action="disable", phase_id="04-tdd")
        result = self.preview("pipeline", [phase])
        self.assertFalse(result["diff"][0]["new"][0]["enabled_by"])
        with patch("forge_events.approval", return_value=None):
            with self.assertRaisesRegex(ValueError, "approval-маркер"):
                self.editor.save(result)
        self.assertEqual(self.policy.read_text(encoding="utf-8"), "{}")
        risk = self.project / "hooks/risk-policy.json"
        risk.parent.mkdir()
        risk.write_text('{"pii_patterns":["pattern"]}', encoding="utf-8")
        result = self.preview("risk", [
            dict(op="risk", action="list-add", key="pii_patterns", value="fixture-[0-9]+"),
        ])
        self.editor.save(result)
        self.assertEqual(json.loads(risk.read_text(encoding="utf-8"))["pii_patterns"],
                         ["pattern", "fixture-[0-9]+"])

    def test_http_save_and_reopen(self):
        self.policy.write_text("{}", encoding="utf-8")
        with ui.bind_server() as server:
            server.editor = self.editor
            server.editor_lock = Lock()
            server.token = "fixture"
            thread = Thread(target=server.serve_forever)
            thread.start()
            try:
                with ui.bind_server() as other:
                    self.assertNotEqual(server.server_port, other.server_port)
                self.assertNotIn(server.server_port, {8080, 8084})
                origin = f"http://127.0.0.1:{server.server_port}"
                with urlopen(origin + "/", timeout=5) as page:
                    self.assertIn("Настройки Forge", page.read().decode())

                def api(path, body=None, headers=None):
                    req = Request(origin + "/api/" + path,
                                  data=None if body is None else json.dumps(body).encode(),
                                  headers=headers or {})
                    with urlopen(req, timeout=5) as response:
                        return json.load(response)

                catalog = api("catalog")
                request = {"revision": catalog["revisions"]["pipeline"],
                           "changes": [dict(op="set", id="docs.mode", value="separate-repo")]}
                headers = {"Content-Type": "application/json", "Origin": origin,
                           "X-Forge-Session": catalog["session"]}
                with self.assertRaises(HTTPError) as rejection:
                    api("preview", request, dict(headers, Origin="https://example.invalid"))
                rejection.exception.close()
                self.assertEqual(rejection.exception.code, 403)
                pending = api("preview", request, headers)
                self.assertEqual(json.loads(self.policy.read_text(encoding="utf-8")), {})
                api("save", {"token": pending["token"]}, headers)
                current = next(row for row in api("catalog")["rows"] if row["id"] == "docs.mode")
                self.assertEqual(current["value"], "separate-repo")
            finally:
                server.shutdown()
                thread.join()

    def test_opens_browser_once_with_ready_url(self):
        with ui.bind_server() as server:
            requested = []

            def browser(url):
                try:
                    with urlopen(url, timeout=5) as page:
                        requested.append((url, page.status))
                finally:
                    server.shutdown()

            with patch.object(ui, "bind_server", return_value=server), \
                    patch.object(ui.webbrowser, "open", side_effect=browser) as opened, \
                    patch("sys.argv", ["forge-config", "--project", str(self.project)]), \
                    patch("sys.stdout"):
                ui.main()
            opened.assert_called_once()
            self.assertEqual(requested, [(f"http://127.0.0.1:{server.server_port}/", 200)])

    def test_request_extends_idle_timeout_and_port_is_released(self):
        timeout = ui.IDLE_TIMEOUT_SECONDS
        clock = 0
        finished = Event()
        with patch.object(ui, "monotonic", side_effect=lambda: clock), ui.bind_server() as server:
            address = server.server_address
            process_request = server.process_request_thread

            def signal_completion(*args):
                process_request(*args)
                finished.set()

            with patch.object(server, "process_request_thread", side_effect=signal_completion):
                thread = Thread(target=server.serve_forever)
                thread.start()
                try:
                    clock = timeout - 1
                    with urlopen(f"http://{address[0]}:{address[1]}/", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                    self.assertTrue(finished.wait(5))

                    clock = timeout
                    server.service_actions()
                    self.assertFalse(server.idle_expired)

                    clock = 2 * timeout - 1
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                    self.assertTrue(server.idle_expired)
                finally:
                    if thread.is_alive():
                        server.shutdown()
                        thread.join(5)

        with self.assertRaises(ConnectionRefusedError):
            create_connection(address, timeout=5).close()

    def test_rejects_both_reserved_port_assignments(self):
        from unittest.mock import Mock

        servers = [SimpleNamespace(server_port=port, server_close=Mock())
                   for port in (8080, 8084, 12345)]
        with patch.object(ui, "ConfigServer", side_effect=servers) as bind:
            self.assertIs(ui.bind_server(), servers[2])
        self.assertEqual(bind.call_count, 3)
        for server in servers[:2]:
            server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
