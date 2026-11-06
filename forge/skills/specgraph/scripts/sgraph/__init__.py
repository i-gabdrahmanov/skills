"""sgraph — детерминированное ядро spec-graph: плоская Markdown-спека → граф заметок Obsidian.

Python 3.9, только stdlib: его импортируют гейт (graph_gate.py), MCP-сервер
(hooks/specgraph_mcp.py) и хуки forge. Модули без побочных эффектов при импорте.
План и решения — tasks/020-spec-graph.md, задачи — docs/specgraph/.
"""
from __future__ import annotations

VERSION = "0.1.0"
