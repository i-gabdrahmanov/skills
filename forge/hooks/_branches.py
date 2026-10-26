#!/usr/bin/env python3
"""_branches.py — реестр ПЛОСКИХ веток forge (fix, tech, ui): единый источник истины.

Зачем. Плоская ветка — это namespace стейта (`ground/statements/<skill>/…`) с фиксированным
списком шагов `<prefix>*`. Fix-ветка жила кортежами `("fix-diag", "fix-red", …)`, раскиданными
по ~15 местам: update.py, tdd-guard, eval-guard, inline-phase-guard, sod-enforcer, risk_ladder,
archive.py, pipeline_phases, resolve_phases, skill_paths, _util… Третья ветка копипастой дала бы
ещё 15 кортежей, и любой пропущенный — тихо снятый гейт (прецедент: «except в хуках прячет
NameError»). Поэтому ветки описаны ЗДЕСЬ, а потребители их читают.

Модуль — stdlib-only и без побочных эффектов: его импортируют хуки (тот же каталог, без
fallback'ов) и скрипты скиллов (через hooks/ в sys.path, как risk_ladder/_project).
Покрытие «каждый потребитель видит каждую ветку» пинит test_branches_registry.py.

Full-ветка (feature-pipeline) сюда НЕ входит: у неё динамические шаги по задачам
(04-test-<id>/04-build-<id>) и фазовая машина pipeline_phases — другая модель.
"""
from __future__ import annotations

import fnmatch
from typing import Optional

# Поля ветки:
#   mode            — пользовательский словарь router/inputs.mode (fix|tech|ui)
#   prefix          — префикс id всех шагов ветки
#   steps           — шаги в порядке (источник истины для манифеста — references/manifest-steps.json
#                     скилла; совпадение пинит тест)
#   subagent_steps  — фазы, ОБЯЗАННЫЕ идти субагентом (inline-phase-guard, update._check_subagent_origin)
#   gate_steps      — шаги, закрытие которых требует evidence record_gate (update._check_gate_result)
#   build_steps     — фазы, где gradle/mvn — продуктивная работа (inline-запуск оркестратором = deny)
#   red_step        — шаг RED (tdd-guard держит src/main/java до его закрытия); None — RED нет
#   design_step     — шаг, играющий роль 02-design (level_requirements.R2 → «план готов»)
#   final_step      — «стройка готова» (archive.py)
#   roles           — шаг → роль SoD (sod-enforcer.ROLE_POLICY)
#   verify_to_impl  — фаза прогона → шаг реализации, который надо переоткрыть (подсказка SoD)
#   artifacts       — шаг → канонические имена файлов в docs-каталоге задачи (update.py)
#   phase_work      — шаг → (regex пути, описание): productive-артефакт фазы (inline-phase-guard)
#   phase_tools     — шаг → имена/шаблоны (fnmatch) продуктивных MCP-инструментов шага: ГЛАВНЫЙ агент их не вызывает,
#                     фазу исполняет субагент (inline-phase-guard; у веток без MCP-движка — пусто). Читающие
#                     инструменты и те, что вызывает сам оркестратор после решения человека (daiver_init, daiver_map_append),
#                     сюда не входят — иначе оркестратор заблокировал бы собственную работу
#   docs_target     — CLI-таргет skill_paths.py, печатающий docs-каталог задачи
#   doc_approval    — шаг → имя документа: закрыть шаг можно только при маркере
#                     <doc>-approved-<feature> (update._check_doc_approval, как 00-brd/02-sdd)
BRANCHES = {
    "forgefix": {
        "mode": "fix",
        "prefix": "fix-",
        "steps": ("fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"),
        "subagent_steps": ("fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"),
        "gate_steps": ("fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"),
        "build_steps": ("fix-red", "fix-green", "fix-verify"),
        "red_step": "fix-red",
        "design_step": "fix-diag",
        "final_step": "fix-spec",
        # fix-diag играет роль дизайна (мини-план), fix-spec — роль спецадаптера (правит
        # только дельту спеки, не код).
        "roles": {"fix-diag": "design", "fix-red": "test", "fix-green": "dev",
                  "fix-verify": "test", "fix-spec": "spec"},
        "verify_to_impl": {"fix-verify": "fix-green"},
        "artifacts": {"fix-diag": ("fix-plan.md", "task-plan.json"), "fix-spec": ("sdd.md",)},
        "phase_work": {
            "fix-diag": (r"(^|/)(fix-plan\.md|task-plan\.json)$", "запись fix-plan.md / task-plan.json"),
            "fix-red": (r"src/test/", "запись RED-теста в src/test/"),
            "fix-green": (r"src/main/|\.java$", "запись кода фикса в src/main/ (*.java)"),
            "fix-verify": (r"src/", "правка src/ в фазе прогона тестов"),
            "fix-spec": (r"(^|/)sdd\.md$", "запись дельты спеки (sdd.md)"),
        },
        "phase_tools": {},
        "docs_target": "fix-docs",
        "doc_approval": {},
    },
    # Tech-ветка (forgetech): техзадача без бизнес-наполнения — апгрейд библиотек, правка
    # конфигурации. Поведение системы должно остаться прежним; гейт — снимок «до» и точный
    # diff «после» (check_invariants.py), а не RED-тест. См. tasks/018-tech-path.md.
    "forgetech": {
        "mode": "tech",
        "prefix": "tech-",
        "steps": ("tech-intake", "tech-diag", "tech-apply", "tech-verify", "tech-record"),
        "subagent_steps": ("tech-diag", "tech-apply", "tech-verify", "tech-record"),
        "gate_steps": ("tech-intake", "tech-diag", "tech-apply", "tech-verify", "tech-record"),
        "build_steps": ("tech-apply", "tech-verify"),
        "red_step": None,
        "design_step": "tech-diag",
        "final_step": "tech-record",
        "roles": {"tech-diag": "design", "tech-apply": "dev", "tech-verify": "test",
                  "tech-record": "spec"},
        "verify_to_impl": {"tech-verify": "tech-apply"},
        "artifacts": {"tech-diag": ("tech-sdd.md", "change-plan.json"),
                      "tech-record": ("report.md",)},
        "phase_work": {
            "tech-diag": (r"(^|/)(tech-sdd\.md|change-plan\.json)$",
                          "запись tech-sdd.md / change-plan.json"),
            "tech-apply": (r"src/main/|\.java$|(^|/)(pom\.xml|build\.gradle(\.kts)?|"
                           r"settings\.gradle(\.kts)?|gradle\.properties|libs\.versions\.toml)$|"
                           r"(^|/)application[^/]*\.(ya?ml|properties)$",
                           "правка билд-файлов / конфигурации / кода техзадачи"),
            "tech-verify": (r"src/", "правка src/ в фазе прогона тестов"),
            "tech-record": (r"(^|/)report\.md$", "запись итоговой сводки (report.md)"),
        },
        "phase_tools": {},
        "docs_target": "tech-docs",
        "doc_approval": {},
    },
    # UI-ветка (forgeui = forge-ui во фронт-репо): макет → UI-код на библиотеке проекта →
    # verify-цикл против макета. Детерминированное ядро — движок по спеке daiver
    # (skills/forgeui/scripts/daiver/, MCP forge-ui). Четыре шага = четыре решения человека:
    # утвердить ui-spec.md, подписать план секций, решать по итерациям проверки.
    # См. tasks/019-forge-ui.md.
    "forgeui": {
        "mode": "ui",
        "prefix": "ui-",
        "steps": ("ui-spec", "ui-design", "ui-build", "ui-verify"),
        "subagent_steps": ("ui-spec", "ui-design", "ui-build", "ui-verify"),
        "gate_steps": ("ui-spec", "ui-design", "ui-build", "ui-verify"),
        # npm/npx/tsc/vite — продуктивная работа сборки и прогона проверок
        "build_steps": ("ui-build", "ui-verify"),
        "red_step": None,
        "design_step": "ui-design",
        "final_step": "ui-verify",
        # ui-spec пишет только документ (роль spec: src/ закрыт); ui-design — канон, план и
        # отчёт резолва в .daiver/ (design: сборки нет); ui-build пишет код; ui-verify гоняет
        # проверки, правка кода — переоткрытием ui-build (verify_to_impl).
        "roles": {"ui-spec": "spec", "ui-design": "design", "ui-build": "dev",
                  "ui-verify": "test"},
        "verify_to_impl": {"ui-verify": "ui-build"},
        "artifacts": {"ui-spec": ("ui-spec.md",)},
        "phase_work": {
            "ui-spec": (r"(^|/)ui-spec\.md$", "запись фронт-спеки (ui-spec.md)"),
            "ui-design": (r"(^|/)\.daiver/tracks/[^/]+/(.+/)?(plan\.(json|md)|resolution-report\.md|ui-plan\.md)$",
                          "запись плана секций / отчёта резолва (.daiver/tracks/…)"),
            "ui-build": (r"\.(tsx|jsx|vue|svelte|ts|js|css|less|scss)$",
                         "запись фронтового кода модуля"),
            "ui-verify": (r"\.(tsx|jsx|vue|svelte|ts|js|css|less|scss)$",
                          "правка фронтового кода в фазе проверки"),
        },
        # Продуктивные инструменты движка forge-ui (MCP `forge-ui`, имена без префикса mcp__forge-ui__). ui-design: заведение
        # кадра и канон, резолв (кандидаты, вердикты, отчёт), план и контракт готовности (пишет ui-plan.md/lock, снимает
        # устаревшее согласие); ui-build: состояние сборки (создаёт и двигает) и svg-арты (с moduleDir копирует в модуль);
        # ui-verify: снимок страницы и инструменты итерации (инструментов пока нет — шаблоны ждут задач 4.x).
        # НЕ входят: daiver_status/outline/section/source_nodes/color_tokens/ds_*/map_lookup/spec/resolve-чтение — читают;
        # daiver_init и daiver_map_append вызывает ОРКЕСТРАТОР после ответа пользователя (согласие ui-map-<KEY>);
        # daiver_track_manifest и daiver_svg_assets на ui-design — вне списка задачи 3.4.
        "phase_tools": {
            "ui-design": ("daiver_pixso_pull", "daiver_normalize", "daiver_resolve_*", "daiver_plan_write",
                          "daiver_plan_contract"),
            "ui-build": ("daiver_build_state", "daiver_build_state_set", "daiver_svg_assets"),
            "ui-verify": ("daiver_screenshot", "daiver_code_compare", "daiver_verify_*"),
        },
        "docs_target": "feature-docs",
        "doc_approval": {"ui-spec": "ui-spec"},
    },
}


def skills() -> tuple:
    """Имена namespace плоских веток."""
    return tuple(BRANCHES)


def get(skill: Optional[str]) -> Optional[dict]:
    return BRANCHES.get(skill or "")


def by_mode(mode: Optional[str]) -> Optional[str]:
    """skill по пользовательскому режиму (fix → forgefix, tech → forgetech)."""
    for skill, b in BRANCHES.items():
        if b["mode"] == mode:
            return skill
    return None


def branch_of_step(step_id) -> Optional[str]:
    """skill плоской ветки, которой принадлежит шаг (по префиксу id); None — не плоский шаг."""
    if not isinstance(step_id, str):
        return None
    for skill, b in BRANCHES.items():
        if step_id.startswith(b["prefix"]):
            return skill
    return None


def is_flat_step(step_id) -> bool:
    return branch_of_step(step_id) is not None


def _collect(field: str) -> tuple:
    out: list = []
    for b in BRANCHES.values():
        out.extend(b[field])
    return tuple(out)


def subagent_steps() -> tuple:
    return _collect("subagent_steps")


def gate_steps() -> tuple:
    return _collect("gate_steps")


def build_steps() -> tuple:
    return _collect("build_steps")


def red_steps() -> tuple:
    return tuple(b["red_step"] for b in BRANCHES.values() if b["red_step"])


def design_steps() -> tuple:
    return tuple(b["design_step"] for b in BRANCHES.values() if b["design_step"])


def roles() -> dict:
    out: dict = {}
    for b in BRANCHES.values():
        out.update(b["roles"])
    return out


def verify_to_impl() -> dict:
    out: dict = {}
    for b in BRANCHES.values():
        out.update(b["verify_to_impl"])
    return out


def artifacts() -> dict:
    out: dict = {}
    for b in BRANCHES.values():
        out.update(b["artifacts"])
    return out


def phase_work() -> dict:
    out: dict = {}
    for b in BRANCHES.values():
        out.update(b["phase_work"])
    return out


def phase_tools() -> dict:
    """шаг → шаблоны имён продуктивных MCP-инструментов шага (fnmatch, регистр важен)."""
    out: dict = {}
    for b in BRANCHES.values():
        for step, patterns in (b.get("phase_tools") or {}).items():
            out[step] = tuple(patterns)
    return out


def tool_basename(tool_name) -> str:
    """Имя инструмента без префикса MCP-сервера: `mcp__forge-ui__daiver_plan_write` → `daiver_plan_write`.
    Имя без префикса возвращается как есть; не строка — пустая строка."""
    return tool_name.rsplit("__", 1)[-1] if isinstance(tool_name, str) else ""


def phase_tool(step_id, tool_name) -> Optional[str]:
    """Шаблон из `phase_tools` шага, под который подходит инструмент; None — это не продуктивный инструмент шага."""
    if not isinstance(step_id, str):
        return None                               # id шага из манифеста — строка; нехэшируемое в .get() упало бы
    base = tool_basename(tool_name)
    for pattern in phase_tools().get(step_id, ()):
        if fnmatch.fnmatchcase(base, pattern):
            return pattern
    return None


def tool_steps(tool_name) -> tuple:
    """Шаги, чьим продуктивным инструментом является `tool_name` (пусто — ничьим)."""
    return tuple(step for step in phase_tools() if phase_tool(step, tool_name))


def doc_approvals() -> dict:
    """шаг → имя документа, утверждение которого человеком нужно для закрытия шага."""
    out: dict = {}
    for b in BRANCHES.values():
        out.update(b.get("doc_approval") or {})
    return out


def final_steps() -> dict:
    """skill → финальный шаг (фолбэк архивации, если реестр шагов скилла не прочитался)."""
    return {s: b["final_step"] for s, b in BRANCHES.items()}


def mode_to_skills() -> dict:
    """mode → {skill} для сверки manifest.inputs.mode ↔ manifest.skill (resolve_phases)."""
    return {b["mode"]: {s} for s, b in BRANCHES.items()}
