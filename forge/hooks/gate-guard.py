#!/usr/bin/env python3
"""gate-guard.py — PreToolUse permission gateway с risk-adaptive ladder R0–R5 (PDLC v3.5).

Заменяет фиксированную политику на risk-adaptive (см. risk_ladder.py + risk-policy.json).
Принцип **deny-first**: рисковое (R3+) действие блокируется, пока не выполнено требование
уровня (manifest-шаги / approval-маркер / evidence). На R3+ при внутренней ошибке/неясности —
тоже блок (fail-CLOSED). R0/R1 и любые читающие команды — проходят мгновенно (fail-open).

Матчеры: вешать на `^Bash$` и `(Write|Edit|WriteFile|NotebookEdit)`. Блок: exit 2 + stderr.
Separation of duties: если действие выше cap роли (agent_type) — deny.

Дополнительно: **phase-lock state machine** — проверка последовательности фаз пайплайна
через ground/phases/gate.json + phase-defs.json. Три проверки:
  1. Скилл соответствует allowed_skills текущей фазы
  2. Read/Grep/Glob в src/ заблокированы, пока grounding не завершён
  3. depends_on фазы выполнены
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

import risk_ladder as R
from _project import active_feature, gate_file, defs_file, evidence_file


def _block(reason: str) -> int:
    print(f"[gate-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _deny() -> bool:
    """Обёртка: блокировка в check_phase_gate. Возвращает False (bool для branch)."""
    return False


def _read_json(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


_WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")


def _required_decisions_missing(root: Path) -> str | None:
    """Первый не-записанный required-ключ для активной фазы (fail-closed решения), иначе None.
    Карта required_decisions в risk-policy.json: префикс id шага → [dot-path ключей pipeline.json]."""
    try:
        policy = R.load_policy().get("required_decisions") or {}
        if not policy:
            return None
        step = R.active_step_id(root)
        if not step:
            return None
        for prefix, keys in policy.items():
            if prefix.startswith("_"):
                continue
            if step.startswith(prefix):
                for k in keys:
                    if not R.config_get(root, k):
                        return k
                return None
    except Exception:
        return None
    return None


def _approval_valid(root: Path, key: str) -> bool:
    """approval-маркер засчитывается ТОЛЬКО с провенансом produced_by:"record_approval"
    (BLOCKER-1): рукописный/самовыписанный маркер без провенанса не снимает гейт, даже если
    он как-то просочился мимо state-write-guard."""
    d = _read_json(root / "ground" / "approvals" / f"{key}.json")
    return isinstance(d, dict) and d.get("produced_by") == "record_approval"


def check_phase_gate(tool_name: str, tool_input: dict, agent_type: str | None,
                     root: Path) -> bool:
    """Трёхуровневая проверка последовательности фаз пайплайна.
    Возвращает True (пропустить) или False (блокировать — сообщение уже в stderr)."""
    feat = active_feature(root)
    gate_path = gate_file(root, feat)
    defs_path = defs_file(root, feat)

    gate = _read_json(gate_path)
    if not gate:
        return True  # вне пайплайна — не блокируем

    defs_list = _read_json(defs_path)
    defs_map = {}
    if defs_list:
        for p in defs_list.get("phases", []):
            defs_map[p["id"]] = p

    current_phase_id = gate.get("current_phase", "")
    phase = next((p for p in gate.get("phases", []) if p["id"] == current_phase_id), None)
    if not phase:
        return True  # некорректное состояние — пропускаем (не наша ошибка)

    pd = defs_map.get(current_phase_id, {})
    phase_status = phase.get("status", "pending")

    # ════════════════════════════════════════════════════════════════════
    # Проверка 1: скилл соответствует фазе (только для agent-вызовов)
    # ════════════════════════════════════════════════════════════════════
    if agent_type:
        allowed = pd.get("allowed_skills", [])
        if allowed and agent_type not in allowed:
            _block(
                f"phase gate: фаза '{current_phase_id}' (status={phase_status}) "
                f"не разрешает скилл '{agent_type}'. "
                f"Разрешены: {allowed}. "
                f"Пропусти фазу или заверши сначала."
            )
            return _deny()

    # ════════════════════════════════════════════════════════════════════
    # Проверка 2: блокировка инструментов до завершения фазы
    # ════════════════════════════════════════════════════════════════════
    blocked_tools = pd.get("blocked_tools_until_complete", [])
    blocked_paths = pd.get("blocked_paths", [])

    if phase_status != "completed" and blocked_tools and tool_name in blocked_tools:
        file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        command = str(tool_input.get("command") or "")

        # Пропускаем системные пути и конфиги
        if any(safe in file_path for safe in
               ("ground/phases", "grounding-index", "ground/pipeline", ".gigacode/")):
            return True

        # Пропускаем чтение README, .md, .json, .yml — если не в src/
        if not any(bp in file_path for bp in blocked_paths):
            return True

        # Пропускаем тесты — по сегментам пути, не по подстроке (иначе src/main/Testimonials
        # ложно считался тестом → обход гейта)
        try:
            import _project
            if _project.is_test_path(file_path):
                return True
        except Exception:
            if "/test/" in file_path or "/Test" in file_path:
                return True

        # Проверяем, прочитал ли агент grounding-index (через evidence-лог)
        ev_path = evidence_file(root, feat)
        has_grounding = False
        if ev_path.exists():
            try:
                for line in ev_path.read_text(encoding="utf-8").splitlines():
                    if "grounding-index" in line and "read_grounding" in line:
                        has_grounding = True
                        break
            except Exception:
                pass

        if current_phase_id == "01-grounding" and phase_status != "completed":
            if has_grounding:
                return True  # grounding прочитан — снимаем блокировку

        _block(
            f"phase gate: фаза '{current_phase_id}' не завершена (status={phase_status}). "
            f"Инструмент '{tool_name}' заблокирован до её завершения. "
            f"Агент '{agent_type or '?'}' пытается читать: {file_path or command[:80]}. "
            f"Требуется завершить текущую фазу или прочитать grounding-index.json."
        )
        return _deny()

    # ════════════════════════════════════════════════════════════════════
    # Проверка 3: зависимости фазы выполнены
    # ════════════════════════════════════════════════════════════════════
    for dep_id in phase.get("depends_on", []):
        dep = next((p for p in gate.get("phases", []) if p["id"] == dep_id), None)
        if dep and dep.get("status") not in ("completed", "skipped"):
            _block(
                f"phase gate: фаза '{current_phase_id}' зависит от '{dep_id}', "
                f"но та не завершена (status={dep.get('status')}). "
                f"Сначала заверши зависимую фазу."
            )
            return _deny()

    return True


def check_gate_override(command: str, root: Path) -> str | None:
    """R4-класс: снятие детерминированного гейта через override_judge.py требует
    approval-маркера ground/approvals/gate-override-<judge>.json (кладётся ТОЛЬКО после
    явного согласия пользователя). Возвращает причину блокировки или None (пропустить).

    --list/--remove свободны: чтение и ВОССТАНОВЛЕНИЕ enforcement'а не гейтятся.
    Держится всегда (и вне пайплайна) — как deny-first для R4+. Ошибка разбора →
    fail-CLOSED (снятие гейта без ясности опаснее ложного блока)."""
    try:
        policy = R.load_policy().get("gate_override") or {}
        pat = policy.get("command_pattern", r"override_judge\.py")
        if not command or not re.search(pat, command):
            return None
        # readonly (--list/--remove) свободны — но проверяем по РЕАЛЬНЫМ токенам-аргументам,
        # а не подстрокой: иначе `--reason "cleanup --list"` ложно трактуется как readonly (обход).
        ro_flags = policy.get("readonly_arg_flags") or ["--list", "--remove"]
        try:
            toks = shlex.split(command)
        except ValueError:
            toks = command.split()
        if any(f in toks for f in ro_flags):
            return None
        m = re.search(r"--judge[\s=]+[\"']?([\w./-]+)", command)
        judge = m.group(1) if m else ""
        prefix = policy.get("approval_prefix", "gate-override")
        key = f"{prefix}-{judge}" if judge else prefix
        if _approval_valid(root, key):
            return None
        exists_no_prov = R.approval_exists(root, key) and not _approval_valid(root, key)
        prov_note = (
            " Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не считается "
            "(его мог выписать сам агент). " if exists_no_prov else " "
        )
        return (
            f"снятие гейта (override_judge) — R4-класс, нужен approval-маркер "
            f"ground/approvals/{key}.json.{prov_note}Порядок: (1) останови работу и спроси "
            f"пользователя (покажи, что не сходится); (2) ТОЛЬКО после явного «да» зафиксируй "
            f"согласие СКРИПТОМ pipeline-state/scripts/record_approval.py --key {key} "
            f"--approved-by user --reason \"<кто/почему>\" (он штампует провенанс; прямой Write "
            f"в approvals/ заблокирован state-write-guard); (3) повтори команду. "
            f"Молча снимать гейт нельзя. --list/--remove не гейтятся."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки gate-override ({e})."


def _kind(tool_name: str, command: str) -> str:
    if tool_name in ("Bash", "run_shell_command"):
        if re.search(r"\bgit\s+commit\b", command):
            return "commit"
        if re.search(r"\bgit\s+push\b|pull[-_ ]?request|pullrequests|\bacli\b.*\bpr\b", command, re.I):
            return "push"
        if re.search(r"\bacli\b.*\bcreate\b|\bjira\b.*\bcreate\b|rest/api/\d+/issue\b", command, re.I):
            return "jira"
        return "other"
    return "write"


def main() -> int:
    level = "R0"
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        agent_type = data.get("agent_type")
        root = Path(R.project_root(data.get("cwd", "")))

        # Без загруженной risk-policy ladder не может классифицировать → в пайплайне deny-first
        # (иначе всё прошло бы как R1-auto). preflight ловит это ДО старта; здесь — backstop.
        if not R.policy_loaded() and R.manifest_exists(root):
            return _block("risk-policy.json не загружена/битая — risk ladder неактивен; "
                          "почини .gigacode/hooks/risk-policy.json (см. preflight).")

        info = R.classify(tool_name, tool_input, str(root))
        level = info["level"]
        command = info["command"]
        kind = _kind(tool_name, command)

        # M6: policy битая ВНЕ пайплайна — классификатор слеп (command_risk пуст → push/PR/jira
        # упали бы в R1-auto). Не пропускаем доставку по слепому auto; deny-first для delivery.
        if not R.policy_loaded() and not R.manifest_exists(root) and kind in ("push", "jira"):
            return _block(
                "risk-policy.json не загружена/битая — классификатор рисков неактивен; "
                f"доставка ({kind}) заблокирована вне пайплайна до починки policy."
            )

        # ── R4-класс: снятие детерминированного гейта (override_judge) без approval ──
        # ДО auto-early-return: classify даёт таким командам default-R1 → иначе прошли бы авто.
        deny = check_gate_override(command, root)
        if deny:
            return _block(deny)

        # ── Phase gate: проверка последовательности фаз пайплайна ──────────
        if not check_phase_gate(tool_name, tool_input, agent_type, root):
            return 2  # блокировка уже выдана в check_phase_gate

        # ── Fail-closed решения: продуктивная запись фазы блокируется, пока требуемое
        #    решение не записано (напр. sources.spec для lite-design). Только write-инструменты,
        #    чтобы не заблокировать config.py set / ask, которыми решение и записывается.
        if tool_name in _WRITE_TOOLS:
            miss = _required_decisions_missing(root)
            if miss:
                return _block(
                    f"фаза требует решения '{miss}', которого нет в pipeline.json (fail-closed). "
                    f"Запиши: config.py set {miss} <value> (интерактивно — ответь на вопрос "
                    f"оркестратора; headless — предзапись ДО прогона), затем повтори."
                )

        # R0/R1 (или ниже порога критичности фичи) — авто. Не вмешиваемся.
        if R.level_order(level) <= R.level_order(R.auto_max_risk(root)):
            return 0

        in_pipeline = R.manifest_exists(root)

        # вне пайплайна (нет manifest) — gateway не форсит пайплайн-требования, но
        # deny-first для R4+ всё равно держим (необратимое без контекста — опасно).
        if not in_pipeline and R.level_order(level) < R.level_order("R4"):
            return 0

        # В пайплайне рисковое действие (R2+) нельзя делать, пока НЕ выбрана критичность фичи.
        # Это форсит шаг «выбор критичности» — он не выполнялся на прогонах.
        if in_pipeline and not R.criticality_set(root):
            return _block(
                "не выбрана критичность фичи. Задай autonomy.criticality + autonomy.auto_max_risk "
                "в ground/pipeline.json (Гейт критичности после BRD), затем продолжай. "
                f"Действие risk={level} ({info['reason']})."
            )

        # separation of duties: действие выше cap роли субагента → deny
        cap = R.agent_cap(agent_type)
        if cap and R.level_order(level) > R.level_order(cap):
            return _block(
                f"separation of duties: роль '{agent_type}' ограничена {cap}, "
                f"а действие классифицировано как {level} ({info['reason']})."
            )

        req = R.requirement(level)
        allowed, why = R.check_requirement(level, req, root, kind, agent_type)
        if not allowed:
            return _block(f"{why}. Действие={kind} target='{info['target']}' risk={level}.")
        return 0

    except Exception as e:
        # fail-CLOSED на рисковых, fail-open на низких
        if R.level_order(level) >= R.level_order("R3"):
            return _block(f"deny-first: ошибка оценки риска на {level} ({e}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
