#!/usr/bin/env python3
"""forge_master_mcp.py — MCP-сервер доступа к требованиям-мастеру (stdio, только stdlib).

Зачем. Мастер-спека часто живёт в ОТДЕЛЬНОМ репо рядом с проектом (docs.master.mode =
separate-repo). Рантайм GigaCode/qwen не даёт агенту ни править файлы вне каталога запуска CLI
(Edit → workspace_escape), ни выполнять там shell (run_shell_command → default deny). Без
отдельного канала `/forge-merge` в такой мастер не пишет вообще, а модель в ответ начинает
собирать свои обёртки. Этот сервер и есть тот канал, и он входит в поставку: deploy регистрирует
его в settings.json проекта (mcpServers.forge-master), сервер запускается отдельным процессом
и пишет в мастер-репо сам.

Узко по построению — generic-записи «файл по пути» нет:
  • база мастера берётся из конфига проекта (docs.master.*), файл сервиса — только из карты
    мастера (ground/spec-map.json), с проверкой «внутри базы» после разрешения симлинков;
  • инструменты — тонкие обёртки над spec_cli.py: те же гейты (fail-closed по форме мастера,
    подтверждённая карта, dry-run по умолчанию, отчёт как есть), логика не дублируется;
  • аргументы проходят белые списки (слаг, имя сервиса, путь без `..`).

Протокол: MCP поверх JSON-RPC 2.0, сообщения — по одному JSON на строку (stdio transport).
Python 3.9, без зависимостей: pip-пакет `mcp` требует 3.10+, а пол forge — 3.9.

Usage (так его запускает рантайм; руками — только для отладки):
    python3 -X utf8 forge_master_mcp.py --project-root <корень проекта>
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, List, Optional

SERVER_NAME = "forge-master"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2024-11-05"
READ_LIMIT = 200_000          # символов за один master_read

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "system-analyst" / "scripts"

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/fixes/[A-Za-z0-9][A-Za-z0-9._-]*)?$")
_CAP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REQ_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MODULE = re.compile(r"^[A-Za-z0-9._/-]+$")


class ToolError(Exception):
    """Неверные аргументы инструмента — ответ isError, сервер продолжает работу."""


# ── доступ к движку ────────────────────────────────────────────────────

def _engine():
    for p in (str(SCRIPTS), str(HERE)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import spec_cli   # noqa: WPS433 — импорт после пути к скиллу
    import spec_map
    return spec_cli, spec_map


def _run_cli(root: Path, argv: List[str]) -> dict:
    """spec_cli.main в этом же процессе: вывод перехватывается, stdin глушится.

    stdin сервера — канал JSON-RPC; любой input() внутри движка съел бы следующий запрос.
    Поэтому записи всегда идут с -y (подтверждение у пользователя спрашивает агент ДО вызова,
    так требует бриф команды), а stdin на время вызова подменяется пустым."""
    spec_cli, _ = _engine()
    spec_cli._GRAMMAR_CACHE.clear()     # карта могла поменяться между вызовами
    out, err = io.StringIO(), io.StringIO()
    saved_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = spec_cli.main(["--project-root", str(root)] + argv)
    finally:
        sys.stdin = saved_stdin
    return {"exit": rc, "stdout": out.getvalue(), "stderr": err.getvalue()}


def _need(args: dict, key: str, rx: "re.Pattern[str]", what: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not rx.match(v):
        raise ToolError(f"{key}: ожидается {what}, получено {v!r}")
    return v


def _opt(args: dict, key: str, rx: "re.Pattern[str]", what: str) -> Optional[str]:
    if args.get(key) in (None, ""):
        return None
    return _need(args, key, rx, what)


def _flag(args: dict, key: str, default: bool = False) -> bool:
    v = args.get(key, default)
    if not isinstance(v, bool):
        raise ToolError(f"{key}: ожидается true/false, получено {v!r}")
    return v


def _rel_path(v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ToolError("path: ожидается путь относительно базы мастера")
    p = Path(v.strip())
    if p.is_absolute() or ".." in p.parts or v.strip().startswith("~"):
        raise ToolError(f"path «{v}» должен быть относительным путём внутри базы мастера")
    return p.as_posix()


# ── инструменты ────────────────────────────────────────────────────────

def t_research(root: Path, a: dict) -> dict:
    argv = ["research"]
    cap = _opt(a, "capability", _CAP, "имя сервиса")
    if cap:
        argv += ["--capability", cap]
    if _flag(a, "refresh"):
        argv.append("--refresh")
    return _run_cli(root, argv)


def t_map_edit(root: Path, a: dict) -> dict:
    argv = ["research"]
    for item in a.get("set") or []:
        if not isinstance(item, dict):
            raise ToolError("set: ожидается список {capability, path}")
        cap = _need(item, "capability", _CAP, "имя сервиса")
        argv += ["--set", f"{cap}={_rel_path(item.get('path'))}"]
    for path in a.get("ignore") or []:
        argv += ["--ignore", _rel_path(path)]
    for item in a.get("map_module") or []:
        if not isinstance(item, dict):
            raise ToolError("map_module: ожидается список {module, capability}")
        mod = _need(item, "module", _MODULE, "модуль кода (буквы, цифры, ./_-)")
        if ".." in Path(mod).parts:
            raise ToolError(f"module «{mod}» с `..` недопустим")
        cap = _need(item, "capability", _CAP, "имя сервиса")
        argv += ["--map-module", f"{mod}={cap}"]
    if len(argv) == 1:
        raise ToolError("нечего править: передай set, ignore или map_module")
    return _run_cli(root, argv)


def t_map_confirm(root: Path, a: dict) -> dict:
    return _run_cli(root, ["research", "--confirm"])


def t_status(root: Path, a: dict) -> dict:
    argv = ["status"]
    cap = _opt(a, "capability", _CAP, "имя сервиса")
    if cap:
        argv += ["--capability", cap]
    return _run_cli(root, argv)


def t_diff(root: Path, a: dict) -> dict:
    argv = ["diff", _need(a, "slug", _SLUG, "слаг дельты (STOR-100 или STOR-100/fixes/BUG-1)")]
    cap = _opt(a, "capability", _CAP, "имя сервиса")
    if cap:
        argv += ["--capability", cap]
    if _flag(a, "master_first"):
        argv.append("--master-first")
    return _run_cli(root, argv)


def t_merge(root: Path, a: dict) -> dict:
    if _flag(a, "all"):
        argv = ["merge", "--all"]
    else:
        argv = ["merge", _need(a, "slug", _SLUG, "слаг дельты или all=true")]
    cap = _opt(a, "capability", _CAP, "имя сервиса")
    if cap:
        argv += ["--capability", cap]
    if _flag(a, "dry_run", True):
        argv.append("--dry-run")
    else:
        argv.append("-y")      # подтверждение — у пользователя, ДО вызова (см. бриф команды)
    for key, flag in (("ensure_sections", "--ensure-sections"), ("master_first", "--master-first"),
                      ("allow_modify", "--allow-modify"), ("no_archive", "--no-archive")):
        if _flag(a, key):
            argv.append(flag)
    for rid in a.get("modify") or []:
        if not isinstance(rid, str) or not _REQ_ID.match(rid):
            raise ToolError(f"modify: недопустимый ID требования {rid!r}")
        argv += ["--modify", rid]
    return _run_cli(root, argv)


def t_read(root: Path, a: dict) -> dict:
    """Текст мастера сервиса по карте (подтверждённой или черновику) — для сверки и ревью."""
    _, spec_map = _engine()
    cap = _need(a, "capability", _CAP, "имя сервиса")
    m = spec_map.load(root)
    if not m:
        raise ToolError("карты мастера нет — сначала master_research")
    try:
        path = spec_map.spec_path(root, m, cap)
    except spec_map.MapError as e:
        raise ToolError(str(e))
    if not path.is_file():
        raise ToolError(f"файла мастера нет: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    offset = a.get("offset", 0)
    if not isinstance(offset, int) or offset < 0:
        raise ToolError("offset: ожидается неотрицательное целое (символы)")
    chunk = text[offset:offset + READ_LIMIT]
    tail = len(text) - offset - len(chunk)
    return {"exit": 0, "path": str(path), "chars": len(text),
            "stdout": chunk + (f"\n\n[… ещё {tail} символов — offset={offset + len(chunk)}]"
                               if tail > 0 else ""),
            "stderr": ""}


_S = {"type": "string"}
_B = {"type": "boolean"}

TOOLS: Dict[str, dict] = {
    "master_research": {
        "fn": t_research,
        "description": (
            "Снять СТРУКТУРУ требований-мастера: какие сервисы, где чей файл, какой формы. "
            "Пишет черновик карты в ground/spec-map.json проекта; в мастер не пишет. Покажи "
            "вывод пользователю как есть — карту проверяет и подтверждает он."),
        "schema": {"type": "object", "properties": {
            "capability": {**_S, "description": "пересканировать только этот сервис"},
            "refresh": {**_B, "description": "скан с нуля (ручные правки карты сбрасываются)"}},
            "additionalProperties": False},
    },
    "master_map_edit": {
        "fn": t_map_edit,
        "description": (
            "Поправить черновик карты мастера по решению пользователя: выбрать файл сервиса, "
            "исключить файл, сопоставить модуль кода сервису. Карта снова становится черновиком."),
        "schema": {"type": "object", "properties": {
            "set": {"type": "array", "items": {"type": "object", "properties": {
                "capability": _S, "path": {**_S, "description": "относительно базы мастера"}},
                "required": ["capability", "path"], "additionalProperties": False}},
            "ignore": {"type": "array", "items": _S},
            "map_module": {"type": "array", "items": {"type": "object", "properties": {
                "module": _S, "capability": _S},
                "required": ["module", "capability"], "additionalProperties": False}}},
            "additionalProperties": False},
    },
    "master_map_confirm": {
        "fn": t_map_confirm,
        "description": ("Подтвердить карту мастера — ТОЛЬКО после явного «да» пользователя. "
                        "Без подтверждённой карты merge в неоднозначный мастер не пишет."),
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "master_status": {
        "fn": t_status,
        "description": "Что в мастере и какие дельты фич не слиты (по сервисам карты). Только чтение.",
        "schema": {"type": "object", "properties": {"capability": _S},
                   "additionalProperties": False},
    },
    "master_diff": {
        "fn": t_diff,
        "description": "План слияния дельты в мастер сервиса (+/~/=). Только чтение.",
        "schema": {"type": "object", "properties": {
            "slug": _S, "capability": _S, "master_first": _B},
            "required": ["slug"], "additionalProperties": False},
    },
    "master_merge": {
        "fn": t_merge,
        "description": (
            "Слить дельту фичи в мастер сервиса (spec_cli merge). По умолчанию dry_run=true — "
            "план без записи. Запись (dry_run=false) — только после того, как пользователь "
            "увидел план и сказал «да». exit 3 в ответе — нужно решение пользователя: покажи "
            "варианты из отчёта и спроси, сам не выбирай."),
        "schema": {"type": "object", "properties": {
            "slug": _S, "all": _B, "capability": _S,
            "dry_run": {**_B, "default": True},
            "ensure_sections": {**_B, "description": "дописать раздел требований в конец мастера"},
            "master_first": _B, "allow_modify": _B,
            "modify": {"type": "array", "items": _S}, "no_archive": _B},
            "additionalProperties": False},
    },
    "master_read": {
        "fn": t_read,
        "description": "Прочитать файл мастера сервиса из карты (по 200k символов, offset).",
        "schema": {"type": "object", "properties": {
            "capability": _S, "offset": {"type": "integer", "minimum": 0}},
            "required": ["capability"], "additionalProperties": False},
    },
}


# ── JSON-RPC ───────────────────────────────────────────────────────────

def _result_text(res: dict) -> str:
    parts = []
    if res.get("stdout"):
        parts.append(res["stdout"].rstrip())
    if res.get("stderr"):
        parts.append("[stderr]\n" + res["stderr"].rstrip())
    parts.append(f"[exit {res.get('exit')}]")
    return "\n\n".join(parts)


def handle(root: Path, msg: dict) -> Optional[dict]:
    """Ответ на одно сообщение; None — уведомление (ответа не бывает)."""
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        proto = params.get("protocolVersion") if isinstance(params, dict) else None
        return _ok(mid, {"protocolVersion": proto or DEFAULT_PROTOCOL,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                         "instructions": "Доступ к требованиям-мастеру forge. Записи — только "
                                         "после подтверждения пользователя; отчёты показывай как есть."})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": [{"name": n, "description": t["description"],
                                    "inputSchema": t["schema"]} for n, t in TOOLS.items()]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if tool is None:
            return _err(mid, -32602, f"неизвестный инструмент: {name}")
        if not isinstance(args, dict):
            return _err(mid, -32602, "arguments: ожидается объект")
        try:
            res = tool["fn"](root, args)
        except ToolError as e:
            return _ok(mid, {"content": [{"type": "text", "text": f"✗ {e}"}], "isError": True})
        except Exception as e:  # noqa: BLE001 — сбой движка не роняет сервер
            return _ok(mid, {"content": [{"type": "text", "text":
                                          f"✗ {type(e).__name__}: {e}\n{traceback.format_exc()}"}],
                             "isError": True})
        return _ok(mid, {"content": [{"type": "text", "text": _result_text(res)}],
                         "isError": res.get("exit") == 2})
    return _err(mid, -32601, f"метод не поддерживается: {method}")


def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


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
    ap = argparse.ArgumentParser(description="MCP-сервер требований-мастера forge (stdio).")
    ap.add_argument("--project-root", required=True, help="корень проекта (где ground/)")
    args = ap.parse_args(argv)
    root = Path(args.project_root).expanduser().resolve()
    if not SCRIPTS.is_dir():
        print(f"[forge-master] движок не найден: {SCRIPTS}", file=sys.stderr)
        return 2
    # Сообщения — UTF-8 по построению протокола; на не-UTF-8 локали (cp1251) поток иначе
    # перекодировался бы и кириллица в отчётах ломала бы JSON.
    for s in (sys.stdin, sys.stdout):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    return serve(root)


if __name__ == "__main__":
    raise SystemExit(main())
