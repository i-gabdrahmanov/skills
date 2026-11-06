#!/usr/bin/env python3
"""test_specgraph_mcp.py — MCP-сервер spec-graph (hooks/specgraph_mcp.py) поверх stdio.

Пинит: рукопожатие и ТОЧНЫЙ инвентарь инструментов (лишний или пропавший инструмент виден сразу);
вызов инструментов по JSON-RPC; неверные аргументы — isError, а не падение; коды -32602/-32601;
несколько запросов подряд получают ответы (stdin сервера не съедается движком).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
SERVER = HOOKS / "specgraph_mcp.py"

EXPECTED_TOOLS = ["graph_assign_put", "graph_assign_start", "graph_catalog", "graph_catalog_decide", "graph_draft_put", "graph_init", "graph_next", "graph_profile", "graph_queue_status", "graph_resolve", "graph_scan",
                  "graph_split", "graph_status", "graph_survey"]


def _session(root: Path, messages: list) -> list:
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    r = subprocess.run([sys.executable, "-X", "utf8", str(SERVER), "--project-root", str(root)],
                       input=data, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def _call(i: int, name: str, args: dict) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


def _payload(reply: dict) -> dict:
    return json.loads(reply["result"]["content"][0]["text"])


class SpecGraphMcp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "spec").mkdir(parents=True)
        (self.root / "docs" / "spec" / "task-service.md").write_text("# task-service\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_handshake_and_inventory(self):
        out = _session(self.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(len(out), 2, "на уведомление ответа нет")
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "spec-graph")
        self.assertIn("graph_status", out[0]["result"]["instructions"])
        self.assertEqual(sorted(t["name"] for t in out[1]["result"]["tools"]), EXPECTED_TOOLS)

    def test_status_and_init_flow(self):
        out = _session(self.root, [
            _call(1, "graph_status", {}),
            _call(2, "graph_init", {"sources": ["docs/spec"]}),
            _call(3, "graph_status", {}),
        ])
        self.assertEqual(len(out), 3)
        first = _payload(out[0])
        self.assertFalse(first["initialized"])
        self.assertEqual(first["questions"][0]["candidates"][0]["root"], "docs/spec")
        self.assertEqual(_payload(out[1])["status"], "written")
        self.assertTrue(_payload(out[2])["initialized"])

    def test_bad_args_are_errors_not_crashes(self):
        out = _session(self.root, [
            _call(1, "graph_init", {"sources": ["../escape"]}),
            _call(2, "graph_status", {"bogus": True}),
            _call(3, "graph_nope", {}),
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
            _call(5, "graph_status", {}),
        ])
        self.assertTrue(out[0]["result"]["isError"])
        self.assertIn("sources[0].root", out[0]["result"]["content"][0]["text"])
        self.assertTrue(out[1]["result"]["isError"])
        self.assertEqual(out[2]["error"]["code"], -32602)
        self.assertEqual(out[3]["error"]["code"], -32601)
        self.assertFalse(out[4]["result"]["isError"], "сервер жив после ошибок")


if __name__ == "__main__":
    unittest.main()
