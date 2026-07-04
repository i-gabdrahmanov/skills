# Skills Registry — Context Supply Chain (PDLC v3.5)

Концепция (стр. 180, 294): AGENTS.md / skills / evals — это **supply chain**. У каждого артефакта
должен быть **owner**, **validity** (срок переоценки), **scope** (где применим) и **eval coverage**.
Иначе они тихо устаревают и начинают вредить. Этот реестр — лёгкая реализация: один источник правды
по жизненному циклу скиллов forge (без правки 18 frontmatter).

- **Owner**: `@team` — заменить на реального владельца на целевой машине.
- **Validity**: дата следующей ревизии (пересмотреть актуальность к этой дате).
- **Scope**: к каким проектам/контексту применим.
- **Evals**: чем покрыт (есть ли детерминированная проверка/гейт/eval).

| Skill | Owner | Validity | Scope | Evals |
|---|---|---|---|---|
| router | @team | 2026-12 | точка входа: выбор режима full/lite, делегация на общий control-plane | — (вход не форсится рантаймом; смягчения — preflight, check_scope) |
| feature-pipeline | @team | 2026-12 | Java/Spring фичи end-to-end (режим full) | gate-скрипты + hooks/evals |
| forgelite | @team | 2026-12 | исполнение подготовленной подзадачи Jira (режим lite: grounding→tech-design по спеке→RED→GREEN→PR) | check_scope.py + record_gate (RED/GREEN) + check_coverage.py |
| pipeline-state | @team | 2026-12 | оркестраторы >3 субагентов | косвенно через evals |
| project-grounder | @team | 2026-12 | фаза grounding | verify_coverage.py |
| system-analyst | @team | 2026-12 | скан Java/Spring сервиса | verify_coverage.py |
| sdd | @team | 2026-12 | BRD → спецификация (sdd.md) | check_sdd_doc.py |
| tech-design | @team | 2026-12 | SDD → план + слои | check_taskplan.py, check_sdd.py |
| java-spring-dev | @team | 2026-12 | генерация Java-кода | check_build.py |
| jira-task-writer | @team | 2026-12 | создание задач Jira | check_jira.py |
| minor-defect-fix | @team | 2026-12 | минорный дефект из Jira | check_coverage.py |
| defect-analyzer | @team | 2026-12 | анализ дефекта | — |
| bugfix-developer | @team | 2026-12 | минимальный фикс | — |
| brd-interview | @team | 2026-12 | сбор требований интервью | — |
| brd-grounder | @team | 2026-12 | grounding для BRD | — |
| business-requirements | @team | 2026-12 | BRD из идеи | — |
| config-helper | @team | 2026-12 | настройка параметров forge (pipeline/gates/risk) | test_config.py |
| harness-verifier | @team | 2026-12 | семантическая верификация харнеса (скиллы+хуки) перед релизом | методический (бриф+чек-лист) |
| pdf / pptx | @team | 2026-12 | работа с PDF/PPTX — внешние скиллы рантайма, каталогов в `skills/` этого репо НЕТ (не деплоятся Forge) | — |

## Control-plane хуки (тоже часть supply chain)

| Hook | Owner | Validity | Назначение | Evals |
|---|---|---|---|---|
| gate-guard + risk_ladder + risk-policy.json | @team | 2026-12 | risk ladder R0–R5, deny-first | hooks/evals/run-evals.py |
| destructive-blocker | @team | 2026-12 | блок деструктивных команд | run-evals.py |
| tdd-guard / eval-guard | @team | 2026-12 | блок src/main без RED-теста / без пройденных eval'ов задачи | run-evals.py |
| sod-enforcer / inline-phase-guard | @team | 2026-12 | separation of duties / блок inline-работы в subagent-фазах | run-evals.py |
| pii-boundary | @team | 2026-12 | граница PII при записи | run-evals.py |
| prompt-guard | @team | 2026-12 | детект prompt-injection | run-evals.py |
| cost-breaker | @team | 2026-12 | token budget: учёт + warn ≥80% (circuit-breaker/стоп 120% временно отключён — токены безлимитны) | run-evals.py |
| evidence-enforcer | @team | 2026-12 | полнота evidence перед доставкой | косвенно |
| state-recorder / context-injector / phase-gate / log-agent | @team | 2026-12 | state/context/stop/audit | run-evals.py |

> При изменении любого скилла/хука — обнови строку (validity, evals). Реестр ревьюится на каждой
> validity-дате; протухшие артефакты деактивируются, а не оставляются «как есть».
