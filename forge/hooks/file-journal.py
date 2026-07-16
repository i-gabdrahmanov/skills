#!/usr/bin/env python3
"""file-journal.py — PostToolUse: безусловный журнал изменённых файлов пайплайна.

Каждая успешная запись (Write/Edit/NotebookEdit) и каждая мутирующая shell-команда
дописывается строкой JSONL в ground/statements/<skill>/<feature>/journal/files.jsonl
активной фичи. Журнал ведёт рантайм-хук — модель не участвует и не может «забыть»;
подделку/затирание инструментами блокирует state-write-guard (journal в _CP_PATTERNS),
сам хук пишет через open() и под guard не попадает.

Потребитель — pipeline-state/scripts/rollback.py: restore-set отката считается как
(git diff worktree↔checkpoint) ∩ (пути журнала после чекпойнта) — журнал скоупит
восстановление, чтобы не затереть ручные правки человека вне пайплайна и знать,
какие untracked-файлы удалять.

Bash-эвристика мутаций best-effort по определению: если write-токен есть, а пути не
извлеклись — пишется op:"bash-opaque" с командой (rollback печатает такие WARNING'ом).
Полноту отката это не подрывает: полноту даёт git-diff от чекпойнта, журнал лишь скоуп.

Вне пайплайна (нет активного манифеста) хук молчит. Никогда не возвращает exit != 0.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # POSIX
except ImportError:
    fcntl = None
    import msvcrt  # Windows

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import risk_ladder as R
except Exception:
    R = None

WRITE_TOOL_OPS = {
    "Write": "write", "WriteFile": "write", "write_file": "write",
    "Edit": "edit", "edit": "edit",
    "NotebookEdit": "notebook", "notebook_edit": "notebook",
}
BASH_TOOLS = ("Bash", "run_shell_command")

# Не журналируем control-plane и служебные каталоги: ground/ откатывается
# манифест-хирургией rollback.py, а не git-restore; .git/ и код control-plane — не цель.
_SKIP_RE = re.compile(r"^(?:ground|\.gigacode|\.git|\.qwen|\.claude)(?:/|$)")

# Write-токены shell-команды (расширение _WRITE_TOKEN_RE из state-write-guard:
# + rm/touch/patch/git apply/git checkout --/git restore).
_WRITE_TOKEN_RE = re.compile(
    r">>?|<>|\btee\b|\bdd\b[^|]*\bof=|\bsed\b[^|]*-i|\bcp\b|\bmv\b|\brm\b|\btouch\b"
    r"|\binstall\b|\bpatch\b|\bgit\s+apply\b|\bgit\s+checkout\b[^|;]*\s--\s|\bgit\s+restore\b"
    r"|\bopen\s*\([^)]*['\"][^'\"]+['\"]\s*,\s*['\"][aw]|\.write(?:_text)?\s*\(|\btruncate\b"
)

# Маппинг префиксов шагов → фазы (копия state-recorder/init_phase_gate; lite-шаги — как есть).
_PREFIX_PHASE = {
    "00-": "00-brd",
    "01-": "01-grounding",
    "02-sdd": "02-sdd",
    "02-eval-plan": "02-eval-plan",
    "02-": "02-design",
    "03-": "03-jira",
    "04-": "04-tdd",
    "05-": "05-verify",
    "06-": "06-document",
    "07-deliver-": "07-deliver",
    "07-report": "07-report",
    "07-": "07-deliver",
}

_SHELL_SEPARATORS = {"&&", "||", ";", "|", "&"}


def _phase_of(step_id: str | None) -> str | None:
    if not step_id:
        return None
    for prefix, pid in sorted(_PREFIX_PHASE.items(), key=lambda kv: -len(kv[0])):
        if step_id.startswith(prefix):
            return pid
    return None


def _agent_label(data: dict) -> str:
    at = data.get("agent_type")
    if not at:
        return "main"
    aid = re.sub(r"[^A-Za-z0-9._-]+", "-", str(data.get("agent_id", "")))[:8]
    return str(at) + (f"-{aid}" if aid else "")


def _norm_path(p: str, root: Path) -> str | None:
    """project-relative posix-путь; вне проекта — абсолютный posix (rollback предупредит).
    None — путь надо пропустить (control-plane/служебный/мусорный токен)."""
    p = (p or "").strip().strip("'\"")
    if not p or p.startswith("-") or p in ("/dev/null", ".", ".."):
        return None
    p = p.replace("\\", "/")
    try:
        pp = Path(p)
        if pp.is_absolute():
            try:
                rel = posixpath.normpath(pp.resolve().relative_to(root.resolve()).as_posix())
            except ValueError:
                return pp.as_posix()  # вне project root — абсолютным (docs-репо и т.п.)
        else:
            rel = posixpath.normpath(p)
    except Exception:
        return None
    if rel.startswith("..") or _SKIP_RE.match(rel):
        return None
    return rel


def _segments(cmd: str) -> list[list[str]]:
    """Токены команды, разбитые по shell-разделителям (&&, ||, ;, |, &)."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    segs, cur = [], []
    for t in toks:
        if t in _SHELL_SEPARATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _nonflag_args(toks: list[str]) -> list[str]:
    return [t for t in toks if not t.startswith("-")]


def _bash_mutation_paths(cmd: str) -> list[str]:
    """Пути, которые команда предположительно мутирует (сырые, до нормализации)."""
    paths: list[str] = []

    # Редиректы (в т.ч. 2>file); дескрипторные (>&2, 2>&1) отсеиваются по '&'.
    for m in re.finditer(r">>?\s*([^\s;|&<>]+)", cmd):
        paths.append(m.group(1))

    for seg in _segments(cmd):
        if not seg:
            continue
        head = seg[0]
        args = seg[1:]
        if head == "tee":
            paths.extend(_nonflag_args(args))
        elif head == "sed" and any(a.startswith("-i") for a in args):
            rest = _nonflag_args(args)
            # sed [флаги] <скрипт> <файлы...>: первый non-flag — скрипт, дальше файлы
            paths.extend(rest[1:])
        elif head in ("mv", "cp"):
            # mv затрагивает и src (исчезает), и dst; cp — dst. Пишем все non-flag args:
            # для отката важен весь blast-radius.
            paths.extend(_nonflag_args(args))
        elif head in ("rm", "touch", "truncate"):
            paths.extend(_nonflag_args(args))
        elif head == "git" and args:
            sub = args[0]
            rest = args[1:]
            if sub == "checkout" and "--" in rest:
                paths.extend(_nonflag_args(rest[rest.index("--") + 1:]))
            elif sub == "restore":
                after = rest[rest.index("--") + 1:] if "--" in rest else rest
                paths.extend(t for t in _nonflag_args(after))
    return paths


def _append(path: str, text: str) -> None:
    """Конкурентный append (flock/msvcrt) — как log-agent._append."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        try:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            f.write(text)
            f.flush()
        finally:
            try:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict) or R is None:
            return 0
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}

        root = Path(R.project_root(data.get("cwd", "")))
        mp = R.active_manifest(root)
        if mp is None:
            return 0  # вне пайплайна — журнал не ведём
        # .../ground/statements/<skill>/<feature>/manifest.json
        feature, skill = mp.parent.name, mp.parent.parent.name

        op: str | None = None
        paths: list[str] = []
        command: str | None = None

        if tool_name in WRITE_TOOL_OPS:
            op = WRITE_TOOL_OPS[tool_name]
            target = str(tool_input.get("file_path") or tool_input.get("path") or "")
            norm = _norm_path(target, root)
            if not norm:
                return 0
            paths = [norm]
        elif tool_name in BASH_TOOLS:
            cmd = str(tool_input.get("command") or "")
            if not cmd or not _WRITE_TOKEN_RE.search(cmd):
                return 0  # читающая команда — не журналируем (шум)
            raw_paths = _bash_mutation_paths(cmd)
            paths = sorted({p for p in (_norm_path(rp, root) for rp in raw_paths) if p})
            if paths:
                op = "bash-mutation"
            else:
                op = "bash-opaque"
                command = cmd[:500]
        else:
            return 0

        step_id = R.active_step_id(root)
        rec = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": data.get("session_id"),
            "agent": _agent_label(data),
            "step_id": step_id,
            "phase": _phase_of(step_id),
            "tool": tool_name,
            "op": op,
            "paths": paths,
        }
        if command:
            rec["command"] = command
        journal = root / "ground" / "statements" / skill / feature / "journal" / "files.jsonl"
        _append(str(journal), json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        return 0  # журнал никогда не роняет прогон
    return 0


if __name__ == "__main__":
    sys.exit(main())
