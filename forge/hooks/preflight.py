#!/usr/bin/env python3
"""
Pre-flight check для feature-pipeline.
Проверяет, что control-plane включён и конфигурация доступна.
Вызывается самым первым при старте пайплайна.

Exit 0 — харнес активен, можно продолжать.
Exit 1 — ENFORCEMENT OFF. Предупредить пользователя.
"""

import json
import os
import re
import sys
from pathlib import Path


def _find_foreign_hook_paths(settings: dict, project_root: str) -> list[str]:
    """Ищет в блоке hooks пути, ведущие за пределы .gigacode/hooks/ текущего проекта."""
    hooks = settings.get("hooks", {})
    found = []
    expected_prefix = os.path.join(project_root, ".gigacode", "hooks") + "/"

    def _walk(node, path=""):
        if isinstance(node, str) and path.endswith("command"):
            m = re.search(r"python3\s+(/\S+)", node)
            if m:
                p = m.group(1)
                if not p.startswith(expected_prefix):
                    found.append(p)
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(hooks)
    return found


def preflight(project_root: str) -> dict:
    errors = []
    warnings = []

    # 1. pipeline.json
    pipeline_json = Path(project_root) / "ground" / "pipeline.json"
    if not pipeline_json.exists():
        errors.append("ground/pipeline.json not found — конфигурация не инициализирована")
    else:
        try:
            cfg = json.loads(pipeline_json.read_text())
            if cfg.get("_incomplete"):
                errors.append(f"pipeline.json incomplete: {cfg['_incomplete']}")
        except json.JSONDecodeError as e:
            errors.append(f"pipeline.json parse error: {e}")

    # 2. settings.hooks.json
    hooks_settings = Path(project_root) / ".gigacode" / "hooks" / "settings.hooks.json"
    if not hooks_settings.exists():
        errors.append(
            ".gigacode/hooks/settings.hooks.json not found — хуки не задеплоены"
        )
    else:
        try:
            hs = json.loads(hooks_settings.read_text())
            hooks_block = hs.get("hooks", {})
            if not hooks_block:
                errors.append("hooks block is empty — enforcement inactive")
        except json.JSONDecodeError as e:
            errors.append(f"settings.hooks.json parse error: {e}")

    # 3. Проверка наличия ключевых хуков
    essential_hooks = [
        "gate-guard.py",
        "phase-gate.py",
        "state-recorder.py",
        "eval-guard.py",
        "log-agent.py",
    ]
    hooks_dir = Path(project_root) / ".gigacode" / "hooks"
    for hook in essential_hooks:
        if not (hooks_dir / hook).exists():
            errors.append(f"hook not found: .gigacode/hooks/{hook}")

    # 4. Проверка settings.json (потребляется рантаймом)
    settings_json = Path(project_root) / ".gigacode" / "settings.json"
    resolver_script = Path(project_root) / ".gigacode" / "hooks" / "resolve_hook_paths.py"
    deploy_script = Path(project_root) / ".gigacode" / "deploy-local.sh"

    if settings_json.exists():
        try:
            stg = json.loads(settings_json.read_text())
            hooks_block = stg.get("hooks", {})
            if not hooks_block:
                errors.append(
                    ".gigacode/settings.json: hooks block is empty — enforcement inactive"
                )
            else:
                foreign = _find_foreign_hook_paths(stg, project_root)
                if foreign:
                    errors.append(
                        f".gigacode/settings.json: обнаружены пути к хукам вне проекта: "
                        f"{foreign}. Запусти bash .gigacode/deploy-local.sh для исправления."
                    )
        except json.JSONDecodeError as e:
            errors.append(f".gigacode/settings.json parse error: {e}")
    else:
        # settings.json не существует — предупреждаем, но не блокируем (если есть эталон)
        if resolver_script.exists():
            warnings.append(
                ".gigacode/settings.json не найден. Запусти bash .gigacode/deploy-local.sh "
                "для генерации из .gigacode/hooks/settings.hooks.json"
            )
        else:
            errors.append(
                ".gigacode/settings.json не найден. "
                "Скопируй .gigacode/hooks/settings.hooks.json в .gigacode/settings.json "
                "или создай resolve_hook_paths.py."
            )

    # 5. Проверка resolve_hook_paths.py
    if resolver_script.exists():
        try:
            import subprocess
            res = subprocess.run(
                [sys.executable, str(resolver_script), "--check", "--project", project_root],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode != 0:
                out = res.stdout.strip()
                if out:
                    try:
                        detail = json.loads(out)
                        for iss in detail.get("issues", []):
                            errors.append(f"hook path check: {iss}")
                    except json.JSONDecodeError:
                        errors.append(f"resolve_hook_paths.py --check вернул ошибку: {out}")
                else:
                    errors.append("resolve_hook_paths.py --check exit != 0 (см. stderr)")
        except Exception as e:
            warnings.append(f"resolve_hook_paths.py --check не выполнен: {e}")
    else:
        if deploy_script.exists():
            warnings.append(
                "resolve_hook_paths.py не найден. "
                "Пути в settings.hooks.json могут быть неактуальными."
            )

    # 6. doctor.py — self-check целостности пайплайна (advisory: предупреждение, не блок)
    doctor_script = (Path(project_root) / ".gigacode" / "skills" / "feature-pipeline"
                     / "scripts" / "doctor.py")
    if doctor_script.exists():
        try:
            import subprocess
            res = subprocess.run(
                [sys.executable, str(doctor_script), "--project", project_root, "--json"],
                capture_output=True, text=True, timeout=20,
            )
            if res.returncode == 1:
                try:
                    detail = json.loads(res.stdout)
                    for prob in detail.get("problems", []):
                        warnings.append(f"doctor: {prob}")
                except json.JSONDecodeError:
                    warnings.append("doctor: обнаружены проблемы целостности (см. doctor.py)")
            elif res.returncode == 2:
                warnings.append(f"doctor: не выполнен ({res.stderr.strip()[:200]})")
        except Exception as e:
            warnings.append(f"doctor.py не выполнен: {e}")

    passed = len(errors) == 0
    result = {"passed": passed, "errors": errors}
    if warnings:
        result["warnings"] = warnings
    return result


if __name__ == "__main__":
    project_root = Path.cwd()
    for arg in sys.argv[1:]:
        if arg in ("-h", "--help"):
            print("Usage: python preflight.py [project_root]")
            print("       python preflight.py --project <path>")
            sys.exit(0)
        if arg in ("--project", "-p"):
            continue
        if arg.startswith("-") and not arg.startswith("--project="):
            continue
        project_root = Path(arg).resolve()
    if any(a == "--project" for a in sys.argv[1:]):
        idx = sys.argv.index("--project")
        if idx + 1 < len(sys.argv):
            project_root = Path(sys.argv[idx + 1]).resolve()
    result = preflight(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["passed"] else 1)