"""daiver — детерминированное ядро forge-ui (воссоздание по docs/daiver/daiver spec.md).

Python 3.9, только stdlib: его импортируют гейты (ui_gate.py, check_ui_spec.py), MCP-сервер
(hooks/daiver_mcp.py) и хуки forge. Модули без побочных эффектов при импорте.
"""
from __future__ import annotations

VERSION = "0.1.0"
