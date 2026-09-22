# FORGE.md — Архитектура и решения (Feature Pipeline Forge)

> **Источник правды** для feature pipeline — не привязан к конкретной модели или версии.
> Версионируется вместе с `hooks/` и `skills/`, поэтому работает независимо от личной
> памяти ассистента. При изменении харнеса обновляй здесь архитектурные решения.

## Зачем этот документ

Аудитория — разработчик форжа. Отличается от соседних: `README.md` — входной обзор;
`INSTALL.md` — установка; `hooks/DEPLOY.md` — полный ростер хуков и диагностика;
`SKILLS-REGISTRY.md` — реестр скиллов (owner/validity/evals). **FORGE.md — архитектура и
история решений**, не дубль остальных. Ростер хуков тут не дублируется (полный — в DEPLOY.md).

Канон установки — единственный: `deploy.sh <project>` (project-модель). Прежняя раскладка
через манифесты и плейсхолдеры плагин-рута снята 2026-08-22; артефактов прежней модели в
репо нет. Решения фиксируются в формате «[ID] Одной строкой (YYYY-MM-DD)»; детали —
git-история и `tasks/`.

## Принципы

1. **Hooks = enforcement, SKILL.md = guidance.** Модель может проигнорировать текст →
   гейты/политики форсятся хуками (`gate-guard`/`risk_ladder`, `phase-gate`, security).
   Блокировка — `exit 2` + причина в `stderr`.
2. **Fail-closed на отсутствии решения.** Решение (критичность, путь, спека) — обязательный
   артефакт в `pipeline.json`. Нет артефакта → `gate-guard._required_decisions_missing`
   блокирует запись фазы. Пустой ответ → `exit 3` (STOP).
3. **Детерминизм > LLM-судьи.** LLM-вердикт сам шаг не закрывает. Шаг закрывается по
   evidence детерминированного гейта (`gates/<step_id>.json` с провенансом
   `produced_by:"record_gate"` + `passed:true`). Полы `INGEST_FLOOR_PHASES` AND-ят LLM-вердикт
   с детерминированной проверкой; тавтология-floor ловит вакуумные тесты.
4. **Меньше степеней свободы.** Risk ladder R0–R5, deny-first (`risk-policy.json`,
   fail-closed при битой policy). `criticality` → `auto_max_risk` атомарно через
   `set_criticality.py` (`low→R2 / medium→R1 / high→R0`). TDD/EDD по умолчанию, escape —
   только через явный approval (R4 + маркер).
5. **Провенанс обязателен.** Каждый stateful-артефакт (`manifest.json`, `_origins`, `gates`,
   `overrides`, `approvals`, `pipeline.json`, `judges/`, `ground/phases/`) пишется только
   санкционированным скриптом с `produced_by:<script>`. Прямая запись моделью (Write/Edit,
   shell-редирект, `python -c open()`) запрещена (`state-write-guard.py`). Approval-маркеры
   пишет только `record_approval.py`.
6. **Совместное размещение `hooks/` и `skills/` обязательно.** Гейты вызываются по
   `../skills/`. `deploy.sh` копирует оба каталога одним пакетом (исключает класс ошибок
   «0 hook entries»).

## Архитектура PDLC v3.5

Pipeline = оркестратор + control-plane из 15 хуков + 20 скиллов; общее состояние —
`<project>/ground/`, артефакты фаз — `<project>/docs/feature-pipeline/...`. PDLC v3.5 ввёл
risk ladder, evidence bundle и security-гейты; доставка (commit/push/PR/отчёт) остаётся на
пользователе — пайплайн её не делает и не гейтит.

### Где живёт код

| Слой | Расположение | Зачем |
|---|---|---|
| Source | исходный репо (родитель `hooks/`) | source-of-truth; отсюда `deploy.sh` копирует файлы |
| Deploy | `<project>/.gigacode/` | co-located `hooks/` + `skills/` + `commands/` + сгенерированный `settings.json` |

`deploy.sh <project>` раскладывает `hooks/` + `skills/` + `commands/` + `deploy-local.sh`
в `<project>/.gigacode/`. `deploy-local.sh` подставляет в `settings.json` абсолютные пути
из `settings.hooks.json` (плейсхолдеры `${PROJECT_ROOT}` и `${PYTHON}`). `preflight.py`
(advisory) проверяет готовность; exit 1 = ENFORCEMENT OFF. Артефакты фаз — в
`<project>/docs/feature-pipeline/...`; `state-write-guard` блокирует запись в каталог
харнеса, пока идёт прогон.

### Risk ladder R0–R5

Политика-as-code в `hooks/risk-policy.json` (`risk_ladder.py` — потребитель). Deny-first:
**R0** — чтение, навигация, ground. **R1** — авто-мутация state через санкц. скрипты
(`update.py`, `record_gate.py`, `record_approval.py`). **R2** — запись артефактов, тесты,
RED/GREEN. **R3** — коммиты, мержа сабветок, push `feature/<slug>`. **R4** — PR-мерджи,
доставка, push в default, override гейтов, `skip-judges`. **R5** — force-push,
`--force-with-lease`, деструктивные операции (`destructive-blocker`). `gate-guard` блокирует
любое R2+ действие, пока `autonomy.criticality` не задана.

### Phases: full vs lite vs fix

Точка входа — скилл `router` (`skills/router/SKILL.md`); классифицирует задачу и делегирует
на общий control-plane (один `.gigacode`, одни хуки):

- **full** (`feature-pipeline`) — фича с нуля: `идея/Jira → BRD → grounding → SDD →
  tech-design → Jira → build → verify → document`. Шаги `04-test-<id>` / `04-build-<id>`,
  state в namespace `feature-pipeline`.
- **lite** (`forgelite`) — исполнение подготовленной подзадачи Jira по существующей спеке:
  grounding → tech-design по `sources.spec` → RED → GREEN → verify. Шаги плоские `lite-*`.
  Профиль: `autonomy.auto_max_risk=R2`, `criticality=medium`, `quality.eval_enabled=false`.
- **fix** (`forgefix`) — минорный дефект: диагностика → гейт фикс-плана → RED воспроизводит
  баг → минимальный фикс → verify → точечная дельта спеки внутри папки стори
  (`<стори>/fixes/<баг>`). Шаги плоские `fix-*`. Два обязательных вопроса: «к какой стори
  относится баг?» (`sources.story`) и утверждение фикс-плана (approval `fix-plan-<KEY|slug>`
  через `record_approval.py`).

Доставка (commit/push/PR/отчёт) — на пользователе. **Multi-vocabulary хуков:** все три
ветки делят control-plane; активный skill/feature резолвится по самому свежему манифесту в
`ground/statements/*/*/` (не по фикс-namespace). Lite/fix-ids намеренно не пересекаются с
масками `judges-registry` и `PREFIX_PHASE` full-пути → лёгкие ветки не тянут судей.
Инвариант «каждая subagent-фаза покрыта хуком» пинится `test_phase_enforcement_coverage.py`.

### Evidence bundle

`<project>/ground/statements/<skill>/<feature>/`: `gates/<step_id>.json` (провенанс
`produced_by:"record_gate"`, `passed:true` для build/verify), `_origins/<step_id>.json`
(реальный SubagentStop; гарантию «фаза закрыта субагентом» держит
`update._check_subagent_origin` на закрытии шага), judges-вердикты. Валидация:
`state-recorder.py` на SubagentStop (последний объект с `step_id` — не по длине JSON),
`update._check_gate_result` (fail-closed без evidence).

### Control plane vs guidance

| Слой | Где | Что делает |
|---|---|---|
| Control plane (хуки) | `<project>/.gigacode/hooks/` | Блоки (`exit 2`) + логирование. Полный ростер → `hooks/DEPLOY.md`. |
| Guidance (скиллы) | `<project>/.gigacode/skills/` | SKILL.md, брифы `references/phases/*.md`, скрипты фаз. Реестр → `SKILLS-REGISTRY.md`. |

Тяжёлые гейты (`check_taskplan`, `check_delivery`, coverage-judge) запускает **оркестратор**
как execution-gate (`record_gate.py`), а хуки — лёгкие file-reads. Это закрывает
ограничение «command-хуки fail-open при таймауте >60с».

### Структура хуков (пин docs↔settings)

Минимальный каркас, который требует `test_docs_hooks_consistency.py`. Полное описание
(события, столбцы, диагностика) — `hooks/DEPLOY.md`.

| Скрипт | Событие | Роль |
|---|---|---|
| `gate-guard.py` | PreToolUse | permission gateway + risk ladder + deny-first |
| `tdd-guard.py` | PreToolUse | блок `src/main` пока RED pending |
| `eval-guard.py` | PreToolUse | блок `src/main` пока eval'ы задачи не passed |
| `sod-enforcer.py` | PreToolUse | separation of duties по id активного шага |
| `inline-phase-guard.py` | PreToolUse | actor-guard: ГЛАВНЫЙ не производит subagent-фазу inline |
| `state-write-guard.py` | PreToolUse | deny прямой записи в control-plane + каталог харнеса |
| `destructive-blocker.py` | PreToolUse | чёрный список деструктивных команд |
| `fork-syntax-guard.py` | PreToolUse | блок синтаксиса, который режет нативный сейфти |
| `pii-boundary.py` | PreToolUse | блок записи PII/секретов вне scope |
| `grounding-evidence.py` | PreToolUse (read) | evidence `kind:"grounding"` при чтении excerpt |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse | детект prompt-injection → additionalContext |
| `file-journal.py` | PostToolUse | журнал изменённых файлов активной фичи |
| `state-recorder.py` | SubagentStop | авто-запись шага по `step_id` |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions |
| `phase-gate.py` | Stop | блок завершения с висящим `in_progress` |

**PreToolUse `run_shell_command` (Bash) — sequential:**
1. `destructive-blocker`
2. `fork-syntax-guard`
3. `pii-boundary`
4. `state-write-guard`
5. `sod-enforcer`
6. `inline-phase-guard`
7. `gate-guard`

**PreToolUse `(Write|Edit)` — sequential:**
1. `pii-boundary`
2. `state-write-guard`
3. `tdd-guard`
4. `eval-guard`
5. `sod-enforcer`
6. `inline-phase-guard`
7. `gate-guard`

### Approval markers

«Человек сказал да» для рисковых решений (override гейта судьи, утверждение плана фикса,
PR-доставка, change-advisory). Физически — запись в проектном журнале
`<project>/ground/approvals.jsonl` (новая раскладка; legacy — `<project>/ground/approvals/<key>.json`).
Провенанс `produced_by:"record_approval"` обязателен; читает `gate-guard._approval_valid`
через `FE.approval` (`hooks/forge_events.py`), которая фильтрует и по провенансу, и по
совпадению `key` внутри записи.

Карта маркеров — `hooks/risk-policy.json` (`phase_approvals`, `level_requirements`):
`fix-red`/`fix-green` → `fix-plan-{feature}`, `R3` → `security-review`, `R4` →
`human-approval`, `R5` → `change-advisory`, override гейта → `gate-override-<judge>`,
закрытие BRD/SDD → `<doc>-approved-<feature>`.

Легитимные пути записи (полный список — `docs/approval-markers.md`):

* `record_approval.py --project <root> --key <key> --approver <name> --reason "..."`
  — одиночное согласие; пишет в `.jsonl` через `FE.append_approval`.
* `record_approval.py --project <root> --batch approvals.yaml` — атомарный батч
  (KIDPPRB-9254 п.6): flock, всё-или-ничего, идемпотентность по ключу.
* `override_judge.py --project <root> --judge <name> --feature <slug> --reason "..."`
  — override гейта судьи (R4-класс; сам требует approval `gate-override-<judge>` ДО
  создания).

Двойной backstop BLOCKER-1: **deny на запись** (`state-write-guard.py` ловит Write/Edit в
`ground/approvals*` и Bash-редиректы `>`/`tee`/`dd of=`/`sed -i`/`cp`/`mv`/`truncate`/
inline-python `open(...,'w')`) **+ фильтрация по провенансу на чтении**
(`gate-guard._approval_valid` через `FE.approval`). Рукописный файл без
`produced_by:"record_approval"` НЕ снимает гейт — это by design, пины тестами
(`hooks/test_gate-guard.py::ApprovalMarkerProvenanceTests`).

Миграция legacy (`ground/approvals/<key>.json`) — переиздание через `record_approval.py --batch`
с теми же `reason`/`evidence`; специального `--import-legacy` нет (KIDPPRB-9254 п.7).

## Migration: v1 (pipeline.json) → v2 (policy.json + manifest.json)

v2-рефакторинг разделил project-wide конфиг (build system, conventions, gates, risk,
docs) и per-feature артефакты (inputs, decisions). В dual-read-режиме (текущая версия)
читаются оба пути; на legacy-fallback эмитится `DeprecationWarning` в stderr.
В v3.0 legacy `ground/pipeline.json` будет проигнорирован — мигрируйте заранее.

**v1 layout:**

```
ground/
└── pipeline.json          # всё в одном файле: project-wide + per-feature
```

**v2 layout:**

```
ground/
├── policy.json                                # только project-wide (build/conventions/quality/jira/docs/...)
└── statements/<skill>/<feature>/manifest.json # только per-feature (inputs/decisions/steps)
```

### Auto-migration

Для существующих v1-проектов (есть только `ground/pipeline.json`):

```bash
# dry-run: печатает, что будет перемещено; записей на диск нет
python3 skills/feature-pipeline/scripts/init_pipeline_config.py \
    --project <root> --check-migration

# do: pipeline.json → policy.json (project-wide поля)
#     pipeline.json["features"][...] → statements/<skill>/<feature>/manifest.json
#     бэкап оригинала — ground/pipeline.json.v1.bak
python3 skills/feature-pipeline/scripts/init_pipeline_config.py \
    --project <root> --migrate
```

### Manual migration (если `--migrate` не справляется)

1. Прочитайте `ground/pipeline.json`.
2. Перенесите `quality.*`, `docs.*`, `jira.*`, `bitbucket.*`, `delivery.*`,
   `conventions.*`, `project.*` → `ground/policy.json`.
3. Для каждой записи `features.<key>`: создайте
   `ground/statements/<skill>/<feature>/manifest.json` со следующим маппингом:

   | v1 (pipeline.json)              | v2 (manifest.json)                                |
   |---------------------------------|---------------------------------------------------|
   | `sources.story`                 | `inputs.story`                                    |
   | `sources.spec`                  | `inputs.spec`                                     |
   | `sources.spec_anchor`           | `inputs.spec_anchor`                              |
   | `pipeline.mode`                 | `inputs.mode`                                     |
   | `pipeline.mode_task`            | `decisions.mode_task`                             |
   | `autonomy.criticality`          | `decisions.criticality`                           |
   | `autonomy.auto_max_risk`        | `decisions.auto_max_risk` (пересчитать через `set_criticality.py`) |
   | `steps`                         | `steps` (без изменений)                           |

4. Удалите старый `pipeline.json` (или оставьте как `.v1.bak` для отката).
5. Прогоните `python3 skills/config-helper/scripts/config.py --project <root> validate --strict`
   для верификации.

### Dual-read shims (текущее состояние, удалено в v3.0)

До v3.0 код читает оба пути (v2 приоритет, v1 fallback). После v3.0:

- `ground/pipeline.json` ИГНОРИРУЕТСЯ — проекты на v2-лейауте молча получат `{}`.
- Per-feature manifest — единственный авторитетный источник.
- `set_criticality.py` пишет ТОЛЬКО в `manifest.json`; legacy keypath-вызовы
  (`set autonomy.auto_max_risk=R2`) → `exit 3`.

Мигрируйте до апгрейда на v3.0.

## Реестр решений

Сжатая история ключевых решений в формате «[ID] Одной строкой (YYYY-MM-DD)». Детали —
git-история и связанные `tasks/`.

### Базовая модель (PDLC v3.5)

- [BR-01] Pipeline > model: hooks = enforcement, SKILL.md = guidance.
- [BR-02] Risk ladder R0–R5, deny-first через `risk-policy.json` (policy-as-code,
  fail-closed при битой policy).
- [BR-03] `criticality` → `auto_max_risk` атомарно через `set_criticality.py`; выбор
  критичности фичи форсится после BRD.
- [BR-04] Pipeline-state намеспейсится по фиче (`ground/statements/<skill>/<feature>/`);
  фичи сосуществуют, `--feature <slug>` во всех вызовах.
- [BR-05] Инвентарь эфемерный (`ensure_inventory.py` → `ground/inventory/`, не в git).
- [BR-06] BRD на языке бизнеса (никаких классов/SQL); grounding-выжимка в
  `ground/brd-grounding/`.
- [BR-07] `/forge` → `router` (классификация fix/lite/full); `/forge-lite`/`/forge-fix` минуя
  router. Команды `commands/*.md` (Markdown+frontmatter), без `!{cat all skills}`.
- [BR-08] TDD по умолчанию (`quality.tdd:true`): per-task RED→GREEN, форсится `tdd-guard`.
  `tdd_enforced` — мёртвый флаг; живой `quality.tdd`.
- [BR-09] Pre-flight self-check (`preflight.py`) ловит «0 hook entries» ДО старта; exit 1 =
  ENFORCEMENT OFF.
- [BR-10] Субагент = явный вызов `agent`, не inline.
- [BR-11] Блокировка хука = `exit 2` + причина в `stderr`.
- [BR-12] Динамический реестр фаз (`resolve_phases.py`): фазы резолвятся из `pipeline.json`
  + `feature-gates.json`. `phases_override` умеет добавлять фазы.
- [BR-13] Доставка на пользователе: commit/push/PR/отчёт пайплайн не делает и не гейтит.
- [BR-14] Фазовые брифы вместо монолитного SKILL.md: диспетчер +
  `references/phases/<phase>.md`. Дрейф пинится `test_phase_briefs.py`.
- [BR-15] `GIGACODE_RUN_ID` (env) → стабильный `run-<id>` независимо от `session_id`
  («один прогон = одна папка»).
- [BR-16] `spec.master_source`: `delta-first` (дефолт — мастер собирается ИЗ дельт) |
  `master-first` (мастер ведёт аналитик, `sdd.md` выделяется ИЗ него). В `master-first`
  `/forge-merge` в мастер НЕ пишет, а сверяет: план слияния обязан быть пустым, любая операция
  `+`/`~` — расхождение и `exit 3`. Эскейп на одну команду — `--allow-merge`.
- [BR-17] Завершённая стройка уезжает в архив по успеху `/forge-merge` (`--no-archive`
  отключает; ручной разбор — `/forge-archive`): доки → `<docs_base>/archive/<слаг>`, стейт →
  `ground/archive/<skill>/<feature>/`, git-чекпойнты фичи удаляются (`restore` их не вернёт).
  Оба архива — **сиблинги**, а не папки внутри исходных каталогов: дельты ищутся обходом
  `<docs_base>/feature-pipeline/` (`spec_cli._features`), активная фича — обходом
  `ground/statements/*/*/`; архив внутри них продолжал бы попадать в обе выборки. Именно
  перенос стейта снимает риск «активная фича по mtime» для завершённых прогонов.
  `archive.py` тем самым встаёт в ряд санкционированных писателей стейта рядом с
  `init.py`/`rollback.py` (BLOCKER-1). «Готово» вычисляется (`read.summarize` + финальный шаг
  фазы закрыт `completed`): persisted-поля «прогон завершён» в манифесте нет.
- [BR-18] `deploy.sh` кладёт в `<target>/.gitignore` блок между маркерами: `ground/*` +
  `!ground/policy.json` + `.gigacode/`; `uninstall.sh` снимает ровно его. Стейт прогонов и
  задеплоенный харнес в историю проекта не едут, конфигурация проекта — едет. Форма
  `ground/*`, а не `ground/`: re-include внутри исключённого каталога git не выполняет.
  `.gigacode/settings.json` держит абсолютные пути машины — коммит вреднее отсутствия файла;
  своё co-located версионируется через `git add -f`.
- [BR-19] Граница деструктива у `rm -rf <абсолютный путь>` проходит по КОРНЮ ПРОЕКТА:
  внутри — штатная уборка, снаружи (и сам корень, `/`, `~`, глоб, смешанные цели) — блок,
  fail-closed при нерезолвимом корне. tasks/011 расширил «опасную цель» до любого абсолютного
  пути ради `rm -rf /etc/passwd` и заодно накрыл `rm -rf <проект>/build`; eval пинил прежнее
  ожидание и был красным с тех пор. Пины: `TRmInsideProject`, три eval-проверки.

### BLOCKER и Thrust (компактно)

- [BLOCKER-0 / 2026-07-04] Матчеры на канон-имена (`run_shell_command`/`write_file`/`edit`),
  не на `Bash`/`Write` — иначе deny-first хуки выпадают из execution-plan.
- [BLOCKER-1 / 2026-07-04] `state-write-guard.py` + `record_approval.py` как единственный
  легальный писатель; whitelist R1-auto для `judges/` и `ground/phases/`.
- [Thrust-1 / universal] Решение = обязательный артефакт `pipeline.json`; пустой ответ →
  `update._check_required_skip` → `exit 3`.
- [Thrust-2] `forgelite` исполняет готовую подзадачу Jira по `sources.spec` (BRD/SDD не
  переписываются); `check_scope.py` ловит Epic/Story/рефактор-слова → `exit 3 ESCALATE`.
- [Thrust-3] `check_brd_doc.py` (детерминированный: бизнес-секции, отсутствие кода/SQL) —
  хард-гейт `00-brd`; brd-judge понижен до advisory; `--from-output` AND-ит LLM с полом.
- [Thrust-4] `checkstyle/ktlint/detekt/spotless` в `BUILD_CMD_RE`
  (inline-phase-guard + sod).
- [Thrust-5 / 2026-07-04] Lite-jira/lite-design в `GATE_RESULT_PREFIXES`; `INGEST_FLOOR_PHASES`
  расширен (brd/eval standalone AND, build/delivery гибрид); тавтология-floor вшит;
  `evidence-enforcer` запрещает `Co-Authored-By`, для forgelite требует ключ Jira
  (сам хук снят позже вместе с доставкой — BR-13).
- [Thrust-6 / 2026-08-10] `risk_ladder.current_step_id` ставит `in_progress`; `phase_approvals`
  требуют `fix-plan-<feature>` для `fix-red`/`fix-green`; `sources.story` — required_decisions
  `fix-diag`; `pipeline.mode` действует только в паре с `pipeline.mode_task`.
- [Thrust-7 / 2026-08-12] `state-write-guard` извлекает реальные ЦЕЛИ записи в Bash
  (`_write_targets`); `required_decisions_on_close` форсит решение в точке вопроса;
  `update.py` не закрывает `lite-design`/`fix-diag`/`fix-spec` без канонических имён;
  промпты содержат ЯВНЫЕ ПУТИ к артефактам плана + правило «расходишься с планом —
  `status:"failed"`»; `spec_cli` подсказывает короткую форму.
- [Thrust-8 / 2026-08-12] `inline-phase-guard` смотрит не один шаг
  (`risk_ladder.ready_step_ids`); `tdd-guard` привязан к задаче через
  `pipeline_phases.task_of_artifact`; `eval-guard` использует слаг из
  `manifest.context.feature` (опционален) и `current_step_id`; `skill_paths` — двухшаговый
  резолв (проект → бандл); `config.py set` различает `null` и осознанный `none` через
  `none_is_value`; `state-recorder` берёт последний объект с `step_id`, не по длине JSON.
- [Thrust-9 / 2026-08-12] `gate_cmd_expect` (префикс шага → допустимые токены `--cmd`/
  `--compile-cmd`) — отказ ДО запуска, если гейта фазы нет в команде; `update.py --skip-judges`
  теперь R4 с approval-маркером `skip-judges-<feature>`; пропуск RED-шага — детерминированно
  по task-plan (`all_tasks_test_exempt`/`task_is_test_exempt`).

### Документно/веточные гейты

- [DVT-01] Override гейтов = R4 с approval-маркером (`gate-override-<judge>.json`).
  Пины: `TGateOverride`.
- [DVT-02] `doc_review_push.py --doc brd|sdd`: коммитит только `<doc>.md` поверх
  `docs/<slug>` (идемпотентен, без force); требует approval + PASS `<doc>-judge` + secret-scan.
  Закрыть `00-brd`/`02-sdd` нельзя без `<doc>-approved-<slug>`. Пины: `TDocReview`.
- [DVT-03] Интеграционная ветка `feature/<slug>`: прямые коммиты ЗАПРЕЩЕНЫ — только PR-мерджи
  сабветок; `gate-guard.check_branch_protection` блокирует history-команды при HEAD на
  `feature/<slug>` + любой push; `git -C` не обходит; `story_branch_push.py` — санкц.
  создатель. Пины: `TBranchProtection`.
- [DVT-04] Stacked-доставка: корневые сабветки PR'ятся в `feature/<slug>`, в default —
  финальный PR `feature/<slug>` → main.
- [DVT-05] RED-гейт по-тестовый: 1 red + N green ≠ успех. Общий `junit_report.py` требует
  отчёты (fail-closed без них), ≥1 выполненный тест, зелёных НОЛЬ.
- [DVT-06] Baseline зелёного ДО разработки: `module_tests.py snapshot --from-taskplan` пишет
  `test-baseline.json`; `check_regression` блокирует ТОЛЬКО новые регрессии.
- [DVT-08] Требование spec-judge «в `docs/feature-pipeline/` только текущая фича» стало
  выполнимым: доки прошлых фич убирает архивация (BR-17), архив-сиблинг в проверку не входит.
- [DVT-07] Архитектурный граунд + гейт межмодульных зависимостей:
  `check_architecture.py --emit-ground` строит `architecture-ground.json`;
  `check_module_deps` ловит НОВЫЕ межмодульные зависимости. Политика
  `quality.module_dep_policy` = `graph` | `deny_new` | `policy` | `off`.

## Известные ограничения и не закрытые зоны

### Ограничения рантайма

- **`additionalContext` только в `hookSpecificOutput`** — рантайм читает ТОЛЬКО оттуда.
- **`agent_type`/`agent_id` приходят ТОЛЬКО на `SubagentStart`/`SubagentStop`** — в
  `PreToolUse` их нет ни у оркестратора, ни у субагента (замерено e2e на qwen-code 0.21.14).
  SoD через `agent_caps` поэтому неактивен by design; основной SoD форсит `sod-enforcer` по id
  активного шага, а актора для `inline-phase-guard` даёт отметка сессии `subagent_scope.py`.
- **Инструмент `agent` в headless (`-p`) требует `-y`/YOLO** — иначе рантайм не даёт его
  выполнить, и модель уходит делать работу фазы сама (прямо в блок inline-phase-guard). Отсюда
  канон запуска — **интерактив** (`gigacode --experimental-hooks` → `/forge <задача>`); headless
  описан отдельным режимом с `-y` и предзаписью решений (INSTALL.md §4). Отказ
  inline-phase-guard различает «actor-сигнал работает» и «`SubagentStart` не приходил вовсе»,
  чтобы вторая причина не читалась как первая и не уводила в цикл перезапусков (tasks/008).
- **Гейт-хуки fail-OPEN при таймауте/краше** (>60с). Тяжёлые гейты запускает оркестратор.
- **`$(...)`/backticks/`find -exec`/`ls -R` РЕЖЕТСЯ** → в SKILL.md/доках заменены на
  `Glob`/`Grep`/`Read`. `fork-syntax-guard.py` объясняет замену.
- **Вход через `router` не форсится** (нет события «скилл выбран»). Смягчения: `gate-guard`
  форсит критичность, `check_scope.py` ловит неверный выбор lite.
- **Блокировки работают ТОЛЬКО если хук попал в execution-plan** — матчер против КАНОН-имени
  (`run_shell_command`/`write_file`/`edit`).
- **Payload-схема хуков подтверждена по snake_case** (`hook_event_name`/`session_id`/`cwd`/
  `tool_name`); `gate-guard` не сравнивает generic-тип с `allowed_skills` фазы (`tasks/008`).
- **`stop_hook_active` на `Stop` приходит `true` ВСЕГДА**, включая первый Stop свежей сессии
  (замерено e2e). Как защита от петли поле бесполезно — `phase-gate` его не читает и держит
  одноразовость собственным маркером сессии (`tasks/012`).

### Принятые риски

- **Активная фича — по mtime манифеста.** `risk_ladder.active_manifest` берёт самый свежий
  `ground/statements/*/*/manifest.json` по ВСЕМ namespace (full/lite/fix). Если в репозитории
  две фичи В РАБОТЕ, гейты более свежей применяются к работе по другой. Корректный выбор
  требует явного маркера активного прогона — отдельная работа, в `tasks/012` не входит.
  Завершённые прогоны из выборки убраны архивацией (BR-17) — риск сузился с «все прогоны,
  что когда-либо были» до «две незакрытые фичи одновременно».

### Открытые задачи (`tasks/`)

- **001** — падающий `hooks/tests/test_project_resolver.py::test_find_project_root` в
  source-репо (нет git/build-маркеров).
- **004** — инициализация git в source-репо (или адаптация тестов). Связано с 001.
- **005** — накладные расходы `grounding-evidence` на каждый `read_file` (наблюдение).

Закрытые: 002 (счётчик скиллов в README), 003 (манифесты, won't-fix — модель снята), 006 (неактуальна: тест проходит и входит в прогон),
**007** (локаут харнеса: форточка на команды восстановления + проверка путей/интерпретатора
в preflight), **008** (пинцет по `agent_type`), **009** (`level_requirements` по фазе, а не по
литеральному id шага), **010** (EDD требовал зелёную сюиту до кода), **011** (ложные блоки на
обычной работе: чтение, jira-ключ, fork-syntax, плейсхолдеры-секреты, JPA-гейт, `rm -rf`),
**012** (мёртвые гейты: фазовый блок чтения, eligibility в `live_state`, Stop-хук, два
резолвера корня), **013** (отказ гейта закрытия выглядел крахом; escape-hatch и `--skill`
вели в чужой namespace; preflight не видел, что рантайм читает ДРУГОЙ каталог настроек — всё найдено e2e-прогоном на qwen CLI), **014** (инвертированная полярность RED-судьи, слепые причины отказа чекеров, однострочный детектор Given-When-Then — найдено первым прогоном full-ветки против настоящей сборки Gradle+JaCoCo, а также гейт покрытия, молча проходивший на любом одномодульном проекте).

### Незакрытые зоны аудита (субагенты упали на session-limit)

- Ядро пайплайн-скриптов: обходы `record_gate`/`run_judge`-floor, деривация
  `criticality`→`risk`.
- Деплой/гигиена git: утечки логов прогонов, дубли реестров.

Допройти отдельным заходом.

## Что НЕ входит в скоуп этого документа

- **Установка** → `INSTALL.md`
- **Control-plane хуков** (полный ростер, порядок, диагностика) → `hooks/DEPLOY.md`
- **Входной обзор** → `README.md`
- **Реестр скиллов** (owner/validity/evals) → `SKILLS-REGISTRY.md`
- **Пользовательское руководство** → `docs/user-guide.md`
- **Типовые проблемы** → `docs/troubleshooting.md`

История изменений — git-история этого репозитория.
