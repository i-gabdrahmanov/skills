#!/usr/bin/env python3
"""context-injector.py — SubagentStart-хук: авто-инъекция контекста субагенту.

Рантайм подкладывает субагенту grounding-выжимку и конвенции, чтобы он проектировал/кодил по
актуальному срезу системы, не перечитывая код.

ВАЖНО (по исходникам Qwen): рантайм читает контекст ТОЛЬКО из `hookSpecificOutput.additionalContext`
(`getAdditionalContext()` в core/hooks/types.ts), а на SubagentStart кладёт его в контекст субагента
(`agent.ts`: contextState 'hook_context'). Поэтому печатаем именно `hookSpecificOutput.additionalContext`.

НЕ зависим от `agent_type`: в пайплайне все субагенты дёргаются как `subagent_type=general-purpose`,
поэтому матчинг по типу не работал бы. Инъектим то, что есть в проекте, всем субагентам (выжимка дёшева;
роль субагента и так задаётся его промптом от оркестратора).

Вывод: `{"hookSpecificOutput": {"additionalContext": "<текст>"}}` (пусто → нет вывода). Всегда exit 0.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PER_FILE_LIMIT = 6000  # символов на файл


def _env_hint() -> str | None:
    """Подсказка субагенту, если буквальные примеры из SKILL.md/phase-доков вида
    "python3 <project>/.../script.py" или "./gradlew ..." не сработают на ЭТОЙ машине
    (Windows без python3 в PATH — только python.exe/py.exe; cmd.exe без поддержки
    shebang/"./" для gradlew без расширения). Доки (112+ мест) переписывать под каждую
    платформу нецелесообразно — вместо этого выдаём готовую замену один раз здесь.

    Пусто на типичной macOS/Linux/WSL-машине, где буквальные примеры и так рабочие —
    не шумим зря в контексте субагента."""
    lines = []
    if not shutil.which("python3"):
        py = shutil.which("python") or shutil.which("py")
        if py:
            lines.append(
                f"- Команды вида `python3 <path>/script.py` из документации НЕ сработают "
                f"(на этой машине нет `python3` в PATH) — используй `{Path(py).name} <path>/script.py`."
            )
    if sys.platform == "win32":
        lines.append(
            "- Команды вида `./gradlew ...` из документации НЕ сработают (Windows-shell "
            "не исполняет shebang/`./` без расширения) — используй `gradlew.bat ...` вместо `./gradlew ...`."
        )
    if not lines:
        return None
    return "### Окружение этой машины (переопределяет буквальные примеры команд в доках)\n" + "\n".join(lines)


def _inject_targets(root: Path) -> list[tuple[str, Path]]:
    """(label, абсолютный путь) файлов для инъекции, в порядке важности.

    grounding-excerpt резолвится по docs-конфигу (in-repo/separate-repo); conventions.md
    project-relative (рабочее состояние всегда в репо кода)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _project  # type: ignore
        excerpt = _project.grounding_excerpt_path(root)
    except Exception:
        excerpt = root / "docs/system-analysis/grounding-excerpt.json"  # fallback (резолвер недоступен)
    return [
        ("system-analysis/grounding-excerpt.json", excerpt),  # компактный срез системы
        ("ground/conventions.md", root / "ground/conventions.md"),  # раскладка слоёв проекта
    ]


def _project_root(cwd: str) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return Path(top)
    except Exception:
        pass
    return Path(cwd or os.getcwd())


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        root = _project_root(data.get("cwd", ""))

        chunks: list[str] = []
        env_hint = _env_hint()
        if env_hint:
            chunks.append(env_hint)
        for label, p in _inject_targets(root):
            if not p.exists():
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # JSON-файлы валидируем перед инъекцией: битый grounding-excerpt (недописанный
            # субагентом/обрыв) размножился бы во всех последующих субагентов как мусор.
            if p.suffix == ".json":
                try:
                    parsed = json.loads(txt)
                except json.JSONDecodeError as e:
                    print(f"[context-injector] {label} битый JSON — не инъектится: {e}",
                          file=sys.stderr)
                    continue
                if label.endswith("grounding-excerpt.json") and isinstance(parsed, dict):
                    missing = [k for k in ("modules", "conventions") if k not in parsed]
                    if missing:
                        print(f"[context-injector] WARNING: {label} без ключей {missing} — "
                              f"инъектится как есть, но grounding может быть неполным",
                              file=sys.stderr)
            if len(txt) > PER_FILE_LIMIT:
                txt = txt[:PER_FILE_LIMIT] + f"\n…(усечено, всего {len(txt)} символов)"
            chunks.append(f"### Контекст пайплайна: `{label}`\n```\n{txt}\n```")

        if not chunks:
            return 0
        print(json.dumps({"hookSpecificOutput": {"additionalContext": "\n\n".join(chunks)}},
                         ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
