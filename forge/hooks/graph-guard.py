#!/usr/bin/env python3
"""graph-guard.py — PreToolUse: frontmatter и маркеры заметок графа пишут только инструменты spec-graph.

Рёбра графа — frontmatter заметок vault. Прототип ломал их правкой руками (тройные скобки, ребро с одной
стороны, усечённые списки) и чинил десятками ремонтных проходов. Здесь правка модели мимо движка
невозможна кодом (задача 2.4):

  • Write/Edit заметки vault (`<vault>/**/*.md` из `.spec-graph/config.json`), меняющие frontmatter или
    содержимое/атрибуты маркеров `<!-- graph:* -->`, — отказ. Проза вне маркеров — можно (это работа
    аналитика, и модель может дописать пояснение);
  • создание новой заметки vault моделью — отказ (заметки создаёт нарезка по утверждённому каталогу);
  • любая запись в `.spec-graph/**` (очереди, планы, предложения, каталог, согласия-снимки) — отказ: это
    состояние движка, его пишут инструменты `graph_*`;
  • shell-запись (`>`, `tee`, `sed -i`, `cp`/`mv` …) в vault или `.spec-graph/` — отказ.

Человек правит в Obsidian напрямую — хук его не видит и не мешает; синк принимает его правки (2.4).
Вне проекта spec-graph (нет `.spec-graph/config.json`) — noop. Внутри — fail-CLOSED: непредвиденная ошибка
проверки — отказ с причиной (exit 1 рантайм читает как «возражений нет»).

Матчеры: Write/Edit-инструменты и shell. Блок: exit 2 + stderr.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import risk_ladder as R
    import _shell_targets as ST
except Exception as _e:  # pragma: no cover — сломанный бандл
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[graph-guard] DENY: бандл forge не грузится ({_e})", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("graph-guard", _e))

_WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "replace", "NotebookEdit", "notebook_edit")
_BASH_TOOLS = ("Bash", "run_shell_command")
_PATH_KEYS = ("file_path", "path", "absolute_path", "notebook_path", "filePath")
_ENGINE = Path(__file__).resolve().parents[1] / "skills" / "specgraph" / "scripts"
CONFIG_REL = Path(".spec-graph") / "config.json"
HINT = ("frontmatter и маркеры graph:* заметок графа пишут только инструменты spec-graph (graph_split, graph_sync, "
        "graph_link_put …): правка руками ломает рёбра. Нужна связь — предложи её через graph_link_put; нужен узел — "
        "через каталог (graph_catalog_decide). Прозу вне маркеров дописывать можно.")


def _block(reason: str) -> int:
    print(f"[graph-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _fm():
    if str(_ENGINE) not in sys.path:
        sys.path.insert(0, str(_ENGINE))
    from sgraph import fm as F  # noqa: WPS433 — кодек frontmatter движка (тот же, что у синка)
    return F


def _abs(cwd: str, root: Path, raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(cwd or root) / p
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return p


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _tool_path(tool_input: Dict[str, Any]) -> str:
    for k in _PATH_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _new_text(old: Optional[str], tool_input: Dict[str, Any]) -> Optional[str]:
    """Текст файла после вызова: content целиком или правки old_string → new_string (одна или списком)."""
    for k in ("content", "file_text", "text"):
        if isinstance(tool_input.get(k), str):
            return tool_input[k]
    edits: List[Dict[str, Any]] = []
    if isinstance(tool_input.get("old_string"), str):
        edits.append(tool_input)
    if isinstance(tool_input.get("edits"), list):
        edits += [e for e in tool_input["edits"] if isinstance(e, dict)]
    if old is None or not edits:
        return None
    text = old
    for e in edits:
        a, b = e.get("old_string") or "", e.get("new_string") or ""
        if not a or a not in text:
            # Не «рантайм откажет сам»: edit qwen 0.21.14 при недословном old_string ищет нечётко
            # (типографские кавычки и тире ≡ ASCII, хвостовые пробелы строк не в счёт) и ПРИМЕНЯЕТ
            # правку. Что она изменит, отсюда не проверить — значит, не пропускаем (аудит 2026-11-07).
            raise _Undecidable("old_string не найден в заметке дословно — рантайм применил бы его "
                               "нечётким сопоставлением, и правку frontmatter/маркеров не проверить; "
                               "дай точный текст из файла")
        text = text.replace(a, b) if e.get("replace_all") else text.replace(a, b, 1)
    return text


class _Undecidable(Exception):
    """Правку нельзя смоделировать точно — проверка невозможна."""


def _markers(F, body: str) -> List[tuple]:
    return [(b["name"], json.dumps(b["attrs"], sort_keys=True), b["content"]) for b in F.blocks(body)]


def _judge_note(F, path: Path, tool_input: Dict[str, Any], rel: str) -> Optional[str]:
    old = path.read_bytes().decode("utf-8") if path.is_file() else None
    try:
        new = _new_text(old, tool_input)
    except _Undecidable as e:
        return f"правка заметки {rel}: {e}. {HINT}"
    if old is None:
        return f"новая заметка графа ({rel}) моделью не создаётся — узлы появляются из каталога через graph_split. {HINT}"
    if new is None:
        return None
    try:
        fm_old, body_old = F.read(old)
    except Exception:  # noqa: BLE001 — заметка уже битая: правку не мешаем, её найдёт валидатор
        return None
    try:
        fm_new, body_new = F.read(new)
    except Exception as e:  # noqa: BLE001
        return f"правка ломает frontmatter заметки {rel} ({str(e)[:120]}). {HINT}"
    if dict(fm_old) != dict(fm_new):
        changed = sorted(k for k in set(fm_old) | set(fm_new) if fm_old.get(k) != fm_new.get(k))
        return f"правка меняет frontmatter заметки {rel} (ключи: {', '.join(changed[:6])}). {HINT}"
    try:
        if _markers(F, body_old) != _markers(F, body_new):
            return f"правка меняет маркеры graph:* заметки {rel}. {HINT}"
    except ValueError as e:
        return f"правка ломает маркеры graph:* заметки {rel} ({e}). {HINT}"
    return None


def _is_note(p: Path) -> bool:
    return p.suffix.lower() == ".md"      # `A.MD` на macOS/Windows — тот же файл, что `a.md`


def _engine_file(p: Path, vault: Path) -> bool:
    """Файлы vault, которые пишет движок (не заметки): отчёты и виды `_graph/**`, Bases (`.base`),
    раскраска графа `.obsidian/graph.json`."""
    rel = p.relative_to(vault).as_posix().lower()
    return rel.startswith("_graph/") or rel.endswith(".base") or rel == ".obsidian/graph.json"


def _covers(root_dir: Path, target: Path) -> bool:
    """Удаление/перенос каталога root_dir уносит target (vault, состояние)."""
    return target == root_dir or root_dir in target.parents


def _judge_removal(root: Path, cwd: str, cmd: str, vault: Path, state: Path) -> Optional[str]:
    """Удаление/перенос заметок, файлов движка и состояния: rm, mv-источник, rmtree, find -delete,
    git checkout/restore по ним. До 2026-11-07 хук смотрел только на запись — `rm -rf .spec-graph`,
    `rm` заметки и `find docs/graph -delete` проходили всю цепочку."""
    cands = list(ST.unlink_targets(cmd))
    for argv in ST._segments(cmd):
        if Path(argv[0]).name == "find" and ("-delete" in argv or any(
                a in ("-exec", "-execdir", "-ok", "-okdir") for a in argv)):
            roots = [a for a in argv[1:] if not a.startswith(("-", "(", "!"))][:1] or ["."]
            cands += roots
    for t in cands:
        p = _abs(cwd, root, t)
        if _covers(p, state) or _inside(p, state):
            return f"удаление/перенос {t}: состояние движка spec-graph (очереди, каталог, согласия) — только инструменты graph_*"
        if _covers(p, vault):
            return f"удаление/перенос {t} уносит vault графа целиком. {HINT}"
        if _inside(p, vault) and (_is_note(p) or _engine_file(p, vault) or p.is_dir()):
            return f"удаление/перенос {t} в vault графа: заметки и отчёты убирает движок, не модель. {HINT}"
    return None


def _judge(root: Path, cwd: str, tool_name: str, tool_input: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    vault = _abs("", root, str(root / cfg.get("vault", "docs/graph")))
    state = _abs("", root, str(root / ".spec-graph"))
    if tool_name in _BASH_TOOLS:
        cmd = str(tool_input.get("command") or "")
        for t in ST.write_targets(cmd, cwd):
            p = _abs(cwd, root, t)
            if _inside(p, state):
                return f"shell-запись в {t}: состояние движка spec-graph пишут только инструменты graph_*"
            if _inside(p, vault) and (_is_note(p) or _engine_file(p, vault)):
                return f"shell-запись в заметку/отчёт графа {t}. {HINT}"
            if _covers(p, vault) or (_inside(p, vault) and p.is_dir()):
                return f"shell-запись в каталог {t} (git checkout/restore, распаковка) перезаписывает заметки графа. {HINT}"
        return _judge_removal(root, cwd, cmd, vault, state)
    raw = _tool_path(tool_input)
    if not raw:
        return None
    p = _abs(cwd, root, raw)
    if _inside(p, state):
        return f"запись в {raw}: состояние движка spec-graph пишут только инструменты graph_*"
    if _inside(p, vault) and _engine_file(p, vault):
        rel = p.relative_to(vault).as_posix()
        return f"файл {rel} пишет движок spec-graph — правка руками перезапишется и собьёт согласия"
    if _inside(p, vault) and _is_note(p):
        rel = p.relative_to(vault).as_posix()
        return _judge_note(_fm(), p, tool_input, rel)
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if tool_name not in _WRITE_TOOLS + _BASH_TOOLS or not isinstance(tool_input, dict):
        return 0
    cwd = str(data.get("cwd") or "")
    try:
        root = Path(R.project_root(cwd))
        cfg_p = root / CONFIG_REL
        if not cfg_p.is_file():
            return 0
    except Exception:  # noqa: BLE001 — проект spec-graph не определить: вне его хук — noop
        return 0
    try:
        cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
        reason = _judge(root, cwd, tool_name, tool_input, cfg if isinstance(cfg, dict) else {})
    except Exception as e:  # noqa: BLE001 — в проекте spec-graph без проверки не пропускаем
        return _block(f"ошибка проверки ({type(e).__name__}: {str(e)[:160]}) — в проекте spec-graph запись без "
                      f"проверки не пропускается")
    return _block(reason) if reason else 0


if __name__ == "__main__":
    sys.exit(main())
