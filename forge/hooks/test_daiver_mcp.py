#!/usr/bin/env python3
"""test_daiver_mcp.py — MCP-сервер forge-ui (hooks/daiver_mcp.py) поверх stdio.

Пинит: рукопожатие и ТОЧНЫЙ инвентарь инструментов (аналог QG-S1 спеки — лишний или
пропавший инструмент виден сразу); вызов инструментов по JSON-RPC; неверные аргументы —
isError, а не падение; выход за корень репо — отказ; коды -32602/-32601; stdin сервера не
съедается движком (несколько запросов подряд получают ответы).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
SERVER = HOOKS / "daiver_mcp.py"

EXPECTED_TOOLS = ["daiver_build_state", "daiver_build_state_set", "daiver_capabilities", "daiver_code_compare", "daiver_color_tokens", "daiver_directive", "daiver_ds_details",
                  "daiver_ds_inventory", "daiver_ds_lookup", "daiver_ds_requirements", "daiver_e2e_plan", "daiver_e2e_run", "daiver_frame_baseline", "daiver_frame_states",
                  "daiver_init", "daiver_map_append", "daiver_map_lookup", "daiver_normalize", "daiver_outline",
                  "daiver_pixso_pull", "daiver_plan_contract", "daiver_plan_write", "daiver_propose_sources", "daiver_render_reference", "daiver_resolve_candidates",
                  "daiver_resolve_finalize", "daiver_resolve_query", "daiver_resolve_report", "daiver_run", "daiver_screenshot", "daiver_section",
                  "daiver_source_nodes", "daiver_spec", "daiver_status", "daiver_svg_assets", "daiver_track_manifest", "daiver_verify_compare", "daiver_verify_decide", "daiver_verify_report",
                  "daiver_verify_state", "daiver_verify_state_record", "daiver_visual_diff", "daiver_visual_review"]


def _session(root: Path, messages: list) -> list:
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    r = subprocess.run([sys.executable, "-X", "utf8", str(SERVER), "--project-root", str(root)],
                       input=data, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def _call(i: int, name: str, args: dict) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


class DaiverMcp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_handshake_and_inventory(self):
        out = _session(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(len(out), 2, "на уведомление ответа нет")
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "forge-ui")
        names = sorted(t["name"] for t in out[1]["result"]["tools"])
        self.assertEqual(names, EXPECTED_TOOLS)
        self.assertNotIn("daiver_plan_sign", names, "подпись плана — согласие человека, не инструмент")

    def test_status_and_init_flow(self):
        (self.root / "package.json").write_text(json.dumps({"dependencies": {"react": "18"}}),
                                                encoding="utf-8")
        out = _session(self.root, [
            _call(1, "daiver_status", {}),
            _call(2, "daiver_init", {}),
            _call(3, "daiver_init", {"answers": {
                "framework": "react", "language": "ts", "styling": "less",
                "outputDir": "src/modules", "buildCommand": "npm run build",
                "librarySource": "storybook-mcp", "storybookMcp": "sb"}}),
            _call(4, "daiver_status", {}),
        ])
        self.assertEqual(len(out), 4, "все запросы получили ответ — stdin не съеден")
        st1 = json.loads(out[0]["result"]["content"][0]["text"])
        self.assertFalse(st1["initialized"])
        detect = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual(detect["suggestions"]["framework"][0]["value"], "react")
        written = json.loads(out[2]["result"]["content"][0]["text"])
        self.assertTrue(written["ok"], written)
        st2 = json.loads(out[3]["result"]["content"][0]["text"])
        self.assertTrue(st2["initialized"], st2)

    def test_bad_args_and_escape_are_errors_not_crashes(self):
        out = _session(self.root, [
            _call(1, "daiver_status", {"bogus": True}),
            _call(2, "daiver_spec", {"paths": ["../../etc/passwd"]}),
            _call(3, "daiver_spec", {"paths": ["/etc/passwd"]}),
            _call(4, "daiver_nope", {}),
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
            _call(6, "daiver_status", {}),
        ])
        self.assertTrue(out[0]["result"]["isError"])
        self.assertTrue(out[1]["result"]["isError"])
        self.assertTrue(out[2]["result"]["isError"])
        self.assertEqual(out[3]["error"]["code"], -32602)
        self.assertEqual(out[4]["error"]["code"], -32601)
        self.assertFalse(out[5]["result"]["isError"], "сервер жив после ошибок")


if __name__ == "__main__":
    unittest.main()
