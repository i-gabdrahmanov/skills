#!/usr/bin/env python3
"""
preflight-validate.py — проверка готовности шага к выполнению: фаза,
                      субагент, судьи, порядок прохождения.

Запускается оркестратором ПЕРЕД каждым agent()-вызовом фазы (Design, Build, Verify, Document).

Что проверяет:
1. Если gate.json отсутствует — создаёт gate.json + phase-defs.json из manifest
2. Жёсткая блокировка: step-id должен соответствовать current_phase в gate.json.
   Если не соответствует — exit 1 (фаза пропущена или нарушен порядок).
3. Проверяет, что output предыдущего шага содержит step_id (признак субагента)
4. Проверяет, что все required_judges предыдущего шага пройдены

Usage:
    python3 preflight-validate.py --project <root> --feature <slug> --step-id <step_id>

Exit codes:
    0 — pass (шаг готов к субагенту или уже был сделан субагентом)
    1 — fail (шаг НЕ был сделан субагентом или данные отсутствуют)
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_phases as pp

# Единый источник истины фаз — pipeline_phases.
PREFIX_PHASE = pp.PREFIX_PHASE
MAIN_PHASES = pp.MAIN_PHASES
_guess_phase = pp.guess_phase


def _ensure_phases(project_root: str, feature: str, skill: str = "feature-pipeline") -> None:
    """Создать ground/phases/<feature>/gate.json из manifest, если их нет."""
    phases_dir = str(pp.gate_dir(Path(project_root), feature))
    # уже есть (per-feature или legacy) — ничего не делаем
    if pp.gate_path(Path(project_root), feature).exists():
        return
    gate_path = os.path.join(phases_dir, "gate.json")
    manifest_path = os.path.join(
        project_root, "ground", "statements", skill, feature, "manifest.json",
    )
    if not os.path.exists(manifest_path):
        return
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception:
        return
    steps = manifest.get("steps", [])
    if not steps:
        return
    os.makedirs(phases_dir, exist_ok=True)

    # Единая реализация построения gate/defs — pipeline_phases.
    gate = pp.build_gate(steps, manifest)
    gate["feature"] = feature
    with open(gate_path, "w") as f:
        json.dump(gate, f, indent=2, ensure_ascii=False)
    print(f"preflight-validate: created {gate_path} (current={gate['current_phase']})",
          file=sys.stderr)

    defs_path = os.path.join(phases_dir, "phase-defs.json")
    if not os.path.exists(defs_path):
        with open(defs_path, "w") as f:
            json.dump(pp.build_defs(steps), f, indent=2, ensure_ascii=False)
        print(f"preflight-validate: created {defs_path}", file=sys.stderr)


def load_manifest(project_root: str, feature: str, skill: str = "feature-pipeline") -> dict | None:
    manifest_path = os.path.join(
        project_root,
        "ground",
        "statements",
        skill,
        feature,
        "manifest.json",
    )
    if not os.path.exists(manifest_path):
        print(f"preflight-validate: manifest not found at {manifest_path}", file=sys.stderr)
        return None
    with open(manifest_path, "r") as f:
        return json.load(f)


def _check_prev_step_judges(manifest: dict, project_root: str, feature: str,
                              step_id: str, skill: str = "feature-pipeline") -> bool:
    """
    Проверяет, что у всех шагов, от которых зависит текущий (depends_on),
    если они имеют required_judges, все их судьи пройдены (verdict файлы с passed=true).
    """
    steps = manifest.get("steps", [])

    # Находим текущий шаг и его depends_on
    current_step = None
    for s in steps:
        if s.get("id") == step_id:
            current_step = s
            break
    if current_step is None:
        return True  # шаг не найден — нечего проверять

    depends_on = current_step.get("depends_on", []) or []
    if not depends_on:
        return True  # нет зависимостей — нечего проверять

    # Собираем статусы шагов для lookup
    step_map = {s["id"]: s for s in steps}

    judges_dir = os.path.join(
        project_root, "ground", "statements", skill, feature, "judges"
    )

    all_ok = True
    for dep_id in depends_on:
        dep_step = step_map.get(dep_id)
        if dep_step is None:
            continue  # шаг зависимости не найден в manifest — не блокируем

        dep_status = dep_step.get("status", "")
        required = dep_step.get("required_judges", []) or []

        # Проверяем только completed шаги с required_judges
        if not required or dep_status != "completed":
            continue

        blocking = []
        for judge_name in required:
            verdict_path = os.path.join(judges_dir, f"{judge_name}.json")
            if not os.path.exists(verdict_path):
                blocking.append(f"'{judge_name}.json' не найден")
                continue
            try:
                with open(verdict_path) as f:
                    verdict = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                blocking.append(f"'{judge_name}.json' повреждён: {e}")
                continue
            if not verdict.get("passed", False):
                blocking.append(f"'{judge_name}.json' — FAIL")

        if blocking:
            all_ok = False
            print(
                f"preflight-validate: FAIL — зависимый шаг '{dep_id}' (completed) "
                f"требует судей, но не все пройдены. Проблемы: {'; '.join(blocking)}",
                file=sys.stderr,
            )

    return all_ok


def check_phase_subagent(manifest: dict, step_id: str) -> bool:
    """Проверяет, что фазы Design/Build/Verify/Document делались через субагента."""
    phases_require_subagent = {"02-design", "04-test", "04-build", "05-tests", "06-spec"}

    # Определяем, относится ли шаг к фазе, требующей субагента
    matches = any(step_id.startswith(prefix) for prefix in phases_require_subagent)
    if not matches:
        # Шаг не требует субагента (00-brd, 01-grounding, 03-jira, 07-deliver) — пропускаем
        return True

    # Ищем шаг в manifest
    for step in manifest.get("steps", []):
        if step.get("id") == step_id:
            status = step.get("status", "")

            # Если шаг ещё не завершён (in_progress/pending) — пропускаем (первый запуск)
            if status in ("in_progress", "pending"):
                return True

            # Пробуем прочитать output: сначала из step.output, затем из output_file
            output = step.get("output", {})
            if not output and step.get("output_file"):
                import json as _json
                try:
                    output_file = Path(step["output_file"])
                    if output_file.exists():
                        output = _json.loads(output_file.read_text())
                except Exception:
                    output = {}

            # Если шаг завершён — проверяем, есть ли step_id в output
            if "step_id" in output:
                return True

            # Шаг завершён, но step_id нет — inline!
            print(
                f"preflight-validate: FAIL — step '{step_id}' completed WITHOUT step_id in output. "
                f"Это признак inline-выполнения (байпас субагента). "
                f"Output keys: {list(output.keys())}",
                file=sys.stderr,
            )
            return False

    # Шаг не найден в manifest — возможно, не создан. Пропускаем.
    print(
        f"preflight-validate: WARN — step '{step_id}' not found in manifest, skipping check",
        file=sys.stderr,
    )
    return True


def _safe_read_json(path: str) -> dict | None:
    """Атомарное чтение JSON-файла с повторной попыткой при race condition.

    Некоторые файловые системы и параллельные хуки могут создавать
    временную inconsistent state. Делаем до 3 попыток с паузой.
    """
    import time
    for attempt in range(3):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
            print(f"preflight-validate: WARN — не удалось прочитать {path}: {e}", file=sys.stderr)
            return None
        except OSError as e:
            print(f"preflight-validate: WARN — не удалось открыть {path}: {e}", file=sys.stderr)
            return None
    return None


def _check_gate_phase(project_root: str, step_id: str, feature: str = "pipeline") -> bool:
    """Жёсткая блокировка: step-id должен соответствовать current_phase в gate.json.

    Правила:
    - Точное совпадение step_id или его фазы с current_phase — разрешено.
    - Префиксное совпадение (например, "04-test-T1" в фазе "04-tdd") — разрешено.
    - Если current_phase пустая (все фазы завершены) — БЛОКИРУЕМ всё.
    - Повторный проход той же фазы (она уже completed) — разрешаем, но
      ТОЛЬКО если шаг относится к этой же фазе (не байпас через неё).
    - Во всех остальных случаях — БЛОКИРУЕМ (фаза пропущена).
    """
    gate_path = str(pp.gate_path(Path(project_root), feature))
    if not os.path.exists(gate_path):
        return True

    gate = _safe_read_json(gate_path)
    if gate is None:
        return True

    current_phase = gate.get("current_phase", "")
    expected_phase = _guess_phase(step_id)

    # Все фазы завершены
    if current_phase == "":
        # Разрешаем только шаги фазы 07-report (финальный отчёт)
        if expected_phase == "07-report":
            return True
        print(
            f"preflight-validate: FAIL — все фазы завершены (current_phase=''), "
            f"шаг '{step_id}' (фаза '{expected_phase}') не может быть начат.",
            file=sys.stderr,
        )
        return False

    # Точное или префиксное совпадение с current_phase
    if expected_phase == current_phase:
        return True
    if expected_phase.startswith(current_phase) or current_phase.startswith(expected_phase):
        return True

    # Повторный проход: шаг относится к current_phase, которая уже completed
    current_phase_obj = next((p for p in gate.get("phases", []) if p["id"] == current_phase), None)
    if current_phase_obj and current_phase_obj.get("status") == "completed":
        return True

    # Повторный проход: шаг относится к уже завершённой фазе, 
    # НО ТОЛЬКО если эта фаза совпадает с current_phase
    expected_phase_obj = next((p for p in gate.get("phases", []) if p["id"] == expected_phase), None)
    if expected_phase_obj and expected_phase_obj.get("status") == "completed":
        # Байпас через завершённую фазу — блокируем
        print(
            f"preflight-validate: FAIL — байпас: шаг '{step_id}' относится к уже "
            f"завершённой фазе '{expected_phase}', но current_phase '{current_phase}' "
            f"ещё не завершена. Пропускать фазы нельзя.",
            file=sys.stderr,
        )
        return False

    # Блокировка
    print(
        f"preflight-validate: FAIL — жёсткая блокировка: шаг '{step_id}' "
        f"(фаза '{expected_phase}') не соответствует current_phase "
        f"'{current_phase}'. Предыдущая фаза не завершена. "
        f"Запустите preflight-validate с шагом, соответствующим '{current_phase}'.",
        file=sys.stderr,
    )
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Проверка готовности шага: фаза, субагент, судьи"
    )
    parser.add_argument("--project", default=os.getcwd(), help="Корень проекта")
    parser.add_argument("--feature", required=True, help="Slug фичи")
    parser.add_argument("--step-id", required=True, help="ID шага для проверки")
    parser.add_argument("--skill", default="feature-pipeline", help="Имя скилла (для резолвинга путей к manifest)")
    args = parser.parse_args()

    _ensure_phases(args.project, args.feature, skill=args.skill)

    manifest = load_manifest(args.project, args.feature, skill=args.skill)
    if manifest is None:
        print("preflight-validate: WARN — manifest not found, skipping", file=sys.stderr)
        sys.exit(0)

    # Проверка depends_on: все зависимости шага должны быть completed/skipped
    steps = manifest.get("steps", [])
    step = next((s for s in steps if s["id"] == args.step_id), None)
    if step:
        deps = step.get("depends_on", [])
        missing = []
        for dep_id in deps:
            dep = next((s for s in steps if s["id"] == dep_id), None)
            if dep and dep.get("status") not in ("completed", "skipped"):
                missing.append(f"'{dep_id}' (status={dep.get('status', 'unknown')})")
        if missing:
            print(
                f"preflight-validate: FAIL — depends_on not satisfied for '{args.step_id}': "
                f"{', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Жёсткая блокировка: проверка current_phase
    if not _check_gate_phase(args.project, args.step_id, args.feature):
        sys.exit(1)

    # Проверка судей предыдущего шага
    if not _check_prev_step_judges(manifest, args.project, args.feature, args.step_id, skill=args.skill):
        sys.exit(1)

    result = check_phase_subagent(manifest, args.step_id)
    if result:
        print(f"preflight-validate: PASS — step '{args.step_id}' ready for subagent")
        sys.exit(0)
    else:
        print(f"preflight-validate: FAIL — step '{args.step_id}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()