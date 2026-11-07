#!/usr/bin/env python3
"""daiver_mcp.py — MCP-сервер forge-ui (stdio, только stdlib): движок `daiver_*` для агента.

Зачем MCP, а не скрипты в shell. Детерминированная работа forge-ui (разбор экспорта макета,
канон, outline/section, сравнение DOM с макетом, состояние треков) — это десятки операций с
типизированными аргументами и большими данными, которые не должны проходить через контекст
модели (DSL макета — сотни КБ). Инструмент получает пути и параметры, пишет результат на диск
и возвращает компактный ответ. Тот же движок гейты зовут CLI (`ui_gate.py`), так что
«модель решила, что шаг готов» не бывает.

Узко по построению: файлов «по произвольному пути» нет — пути только относительные, внутри
корня репо (`_rel_inside` движка); аргументы проверяются вручную, неизвестный аргумент — отказ.

Протокол: MCP поверх JSON-RPC 2.0, по одному JSON на строку (stdio transport). Python 3.9, без
зависимостей — пол forge (пакет `mcp` требует 3.10+). Образец — forge_master_mcp.py.

Usage (так его запускает рантайм; руками — только для отладки):
    python3 -X utf8 daiver_mcp.py --project-root <корень репо>
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Optional

SERVER_NAME = "forge-ui"
DEFAULT_PROTOCOL = "2024-11-05"

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / "skills" / "forgeui" / "scripts"


def _engine():
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from daiver import VERSION, tools  # noqa: WPS433 — импорт после пути к движку
    return VERSION, tools


def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(payload) -> dict:
    return {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}


def handle(root: Path, msg: dict) -> Optional[dict]:
    """Ответ на одно сообщение; None — уведомление (ответа не бывает)."""
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if mid is None:
        return None
    version, tools = _engine()
    if method == "initialize":
        proto = params.get("protocolVersion") if isinstance(params, dict) else None
        return _ok(mid, {"protocolVersion": proto or DEFAULT_PROTOCOL,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": SERVER_NAME, "version": version},
                         "instructions": "Движок forge-ui. Первый ход — daiver_status. Значения не "
                                         "угадывай: чего нет на диске и в ответах пользователя — "
                                         "спроси."})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": [{"name": n, "description": t["description"],
                                    "inputSchema": t["schema"]} for n, t in tools.TOOLS.items()]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in tools.TOOLS:
            return _err(mid, -32602, f"неизвестный инструмент: {name}")
        if not isinstance(args, dict):
            return _err(mid, -32602, "arguments: ожидается объект")
        out, err = io.StringIO(), io.StringIO()
        saved_stdin = sys.stdin
        sys.stdin = io.StringIO("")     # stdin сервера — канал JSON-RPC, движку его не отдаём
        try:
            with redirect_stdout(out), redirect_stderr(err):
                res = tools.call(name, root, args)
        except tools.ToolError as e:
            return _ok(mid, {"content": [{"type": "text", "text": f"✗ {e}"}], "isError": True})
        except Exception as e:  # noqa: BLE001 — сбой движка не роняет сервер
            return _ok(mid, {"content": [{"type": "text", "text":
                                          f"✗ {type(e).__name__}: {e}\n{traceback.format_exc()}"}],
                             "isError": True})
        finally:
            sys.stdin = saved_stdin
        content = [_text(res)]
        stray = (out.getvalue() + err.getvalue()).strip()
        if stray:
            content.append({"type": "text", "text": "[вывод движка]\n" + stray[-4000:]})
        return _ok(mid, {"content": content, "isError": False})
    return _err(mid, -32601, f"метод не поддерживается: {method}")


def serve(root: Path, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            reply = _err(None, -32700, f"не JSON: {e}")
        else:
            if isinstance(msg, list):     # батчи JSON-RPC: MCP их не шлёт, но не падаем
                replies = [r for r in (handle(root, m) for m in msg if isinstance(m, dict)) if r]
                if replies:
                    stdout.write(json.dumps(replies, ensure_ascii=False) + "\n")
                    stdout.flush()
                continue
            reply = handle(root, msg) if isinstance(msg, dict) else \
                _err(None, -32600, "ожидается объект запроса")
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="MCP-сервер forge-ui (stdio).")
    ap.add_argument("--project-root", required=True, help="корень фронт-репо (где .daiver/ и ground/)")
    args = ap.parse_args(argv)
    root = Path(args.project_root).expanduser().resolve()
    if not (ENGINE / "daiver").is_dir():
        print(f"[forge-ui] движок не найден: {ENGINE / 'daiver'}", file=sys.stderr)
        return 2
    # Сообщения — UTF-8 по построению протокола; на не-UTF-8 локали поток иначе перекодировался
    # бы и кириллица в ответах ломала бы JSON.
    for s in (sys.stdin, sys.stdout):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    return serve(root)


if __name__ == "__main__":
    raise SystemExit(main())
