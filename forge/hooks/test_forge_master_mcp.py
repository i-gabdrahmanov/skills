#!/usr/bin/env python3
"""test_forge_master_mcp.py — MCP-канал к требованиям-мастеру вне каталога проекта.

Рантайм GigaCode не даёт агенту Edit/shell за пределами каталога запуска, поэтому мастер в
отдельном репо доступен только через этот сервер. Пины:
  • протокол: initialize / tools/list / tools/call по строкам stdio, уведомления без ответа;
  • сервер — обёртка над spec_cli: merge по умолчанию dry-run и не пишет;
  • узость: пути вне базы и кривые аргументы отклоняются, generic-записи нет;
  • stdin сервера не съедается движком (input() внутри merge видит пустой поток).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
SERVER = HOOKS / "forge_master_mcp.py"

PROSE = "# Сервис\n\n## 1. Общее\nТекст.\n"
SDD = ("# SDD: Метрика\n\n## 1. Назначение\nМодуль service/inboundservice.\n\n"
       "## 3. Функциональные требования (Given-When-Then)\n"
       "- **Given** очередь **When** метрика **Then** число\n")


def rpc(root: Path, *messages: dict) -> list:
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    r = subprocess.run([sys.executable, "-X", "utf8", str(SERVER), "--project-root", str(root)],
                       input=data, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]


def call(i: int, name: str, args: "dict | None" = None) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}}}


class McpTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.root, self.spec = tmp / "proj", tmp / "spec"
        (self.root / "ground").mkdir(parents=True)
        (self.root / "ground" / "policy.json").write_text(json.dumps({
            "project": {"name": "npf"},
            "docs": {"mode": "in-repo", "docs_path": "docs",
                     "master": {"enabled": True, "mode": "separate-repo",
                                "repo_path": str(self.spec)}}}), encoding="utf-8")
        for cap in ("inbound-adapter", "db-service"):
            (self.spec / cap).mkdir(parents=True)
            (self.spec / cap / f"{cap}.md").write_text(PROSE, encoding="utf-8")
        (self.spec / "PROJECT_MAP.md").write_text("# Map\n### 1. a\n### 2. b\n", encoding="utf-8")
        d = self.root / "docs" / "feature-pipeline" / "NPF-42"
        d.mkdir(parents=True)
        (d / "sdd.md").write_text(SDD, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def text(self, reply: dict) -> str:
        return reply["result"]["content"][0]["text"]

    def test_handshake_and_tools(self):
        out = rpc(self.root, INIT, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(out), 2, "уведомление не получает ответа")
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "forge-master")
        names = {t["name"] for t in out[1]["result"]["tools"]}
        self.assertEqual(names, {"master_research", "master_map_edit", "master_map_confirm",
                                 "master_status", "master_diff", "master_merge", "master_read"})
        self.assertFalse(any("write" in n or "file" in n for n in names),
                         "generic-записи файлов быть не должно")

    def test_research_confirm_merge_flow(self):
        out = rpc(self.root, INIT, call(2, "master_research"), call(3, "master_map_confirm"),
                  call(4, "master_merge", {"slug": "NPF-42", "capability": "inbound-adapter",
                                           "ensure_sections": True}))
        self.assertIn("inbound-adapter", self.text(out[1]))
        self.assertIn("[exit 0]", self.text(out[2]))
        self.assertIn("dry-run", self.text(out[3]))
        self.assertEqual((self.spec / "inbound-adapter" / "inbound-adapter.md")
                         .read_text(encoding="utf-8"), PROSE, "dry_run по умолчанию не пишет")

        out = rpc(self.root, INIT, call(2, "master_merge", {
            "slug": "NPF-42", "capability": "inbound-adapter", "ensure_sections": True,
            "dry_run": False, "no_archive": True}))
        self.assertIn("[exit 0]", self.text(out[1]))
        self.assertIn("REQ-0001", (self.spec / "inbound-adapter" / "inbound-adapter.md")
                      .read_text(encoding="utf-8"))

    def test_merge_without_map_is_decision_not_error(self):
        out = rpc(self.root, INIT, call(2, "master_merge", {"slug": "NPF-42", "dry_run": False}))
        self.assertFalse(out[1]["result"]["isError"])
        self.assertIn("no-map", self.text(out[1]))
        self.assertIn("[exit 3]", self.text(out[1]))

    def test_escape_attempts_refused(self):
        out = rpc(self.root, INIT, call(2, "master_research"),
                  call(3, "master_map_edit", {"set": [{"capability": "db-service",
                                                       "path": "../proj/ground/policy.json"}]}),
                  call(4, "master_map_edit", {"set": [{"capability": "db-service",
                                                       "path": "/etc/passwd"}]}),
                  call(5, "master_merge", {"slug": "../../x"}),
                  call(6, "master_read", {"capability": "../ground"}))
        for reply in out[2:]:
            self.assertTrue(reply["result"]["isError"], reply)

    def test_read_returns_master_text(self):
        out = rpc(self.root, INIT, call(2, "master_research"),
                  call(3, "master_read", {"capability": "db-service"}))
        self.assertIn("## 1. Общее", self.text(out[2]))

    def test_unknown_tool_and_method(self):
        out = rpc(self.root, INIT, call(2, "write_file", {"path": "x"}),
                  {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(out[1]["error"]["code"], -32602)
        self.assertEqual(out[2]["error"]["code"], -32601)

    def test_stdin_not_consumed_by_engine(self):
        """merge без dry_run идёт с -y; следующий запрос в том же потоке обязан дойти."""
        out = rpc(self.root, INIT, call(2, "master_research"), call(3, "master_map_confirm"),
                  call(4, "master_merge", {"slug": "NPF-42", "capability": "db-service",
                                           "dry_run": False, "ensure_sections": True,
                                           "no_archive": True}),
                  {"jsonrpc": "2.0", "id": 5, "method": "ping"})
        self.assertEqual([r["id"] for r in out], [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
