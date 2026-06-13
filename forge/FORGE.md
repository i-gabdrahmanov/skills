# FORGE.md — Архитектура и решения (Feature Pipeline Forge)

> **Источник правды** для feature pipeline — не привязан к конкретной модели или версии.
> Это самодостаточная документация харнеса: архитектура, решения, разбор прогонов, роадмап.
> Файл версионируется вместе с `hooks/` и `skills/`, поэтому работает **независимо от чьей-либо
> личной памяти ассистента**. Любой оператор/агент должен опираться на ЭТОТ файл, а не на
> внешние заметки. При изменении харнеса — обновляй ЗДЕСЬ.

## Что это

`~/.gigacode/` — source-of-truth e2e-обвязки для реализации фич в Java/Spring через feature pipeline.
Принцип (PDLC v3.5): **Pipeline > model; hooks = enforcement; skills = guidance**. Деплой —
`~/.gigacode/deploy.sh` в конфиг-дом рантайма.

- `hooks/` — control-plane (см. `hooks/DEPLOY.md` — полный ростер, порядок, диагностика).
- `skills/` — пайплайн-скиллы (оркестратор `feature-pipeline` + фазовые).
- `deploy.sh` — развёртывание одной командой (co-location hooks+skills, мерж hooks-блока).
- `hooks/preflight.py` — диагностика готовности ДО прогона.

## Архитектура (фазы feature-pipeline)

`идея/Jira → BRD → grounding → tech-design → Jira → build → verify → document → deliver`.
Гейты: точки подтверждения пользователем + детерминированные execution-gate'ы (Python) на каждую фазу.
Состояние — `pipeline-state` (manifest), резюмируемо. Подробности — `skills/feature-pipeline/SKILL.md`.

**Ключевой принцип:** Правила качества форсит сам рантайм (хуки), а не «добрая воля» модели —
пропустить тесты, выкатить без проверок или сделать рискованное «молча» нельзя.

### Хуки (control-plane)

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit | permission gateway, risk ladder R0–R5, **deny-first**; форсит выбор критичности | exit 2 |
| `tdd-guard.py` | PreToolUse Write/Edit | форсит TDD (блок `src/main` пока RED pending) + тест-стратегию (блок `@DataJpaTest`/`@SpringBootTest` при `test_layer=service-unit`) | exit 2 |
| `destructive-blocker.py` | PreToolUse `^Bash$` | чёрный список (`rm -rf /`, force-push, DROP…) | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/scope вне секретов | exit 2 |
| `evidence-enforcer.py` | PreToolUse `^Bash$` | блок доставки без полного evidence bundle | exit 2 |
| `cost-breaker.py` | Pre/Post/Stop/SubagentStop/UserPromptSubmit | token budget warn 80% / stop 120% | exit 2 / block |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch) | детект prompt-injection → additionalContext | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим `in_progress` | block |
| `log-agent.py` | все | append-only JSONL аудит (sync) | нет |

**Не-хуки рядом:** `preflight.py` (проверка «харнес активен?» ПЕРЕД пайплайном — ловит «0 hook entries»),
`risk-policy.json` (policy-as-code, `risk_ladder.py` читает), `settings.hooks.json` (эталон).

### Скиллы (pipeline)

| Скилл | Назначение | Evals |
|---|---|---|
| `feature-pipeline` | Оркестратор: ведёт фичу по фазам от BRD до PR | gate-скрипты + evals |
| `pipeline-state` | Состояние многошаговых пайплайнов с субагентами | косвенно через evals |
| `system-analyst` | Скан Java/Spring сервиса (модули, API, Kafka, БД) | `verify_coverage.py` |
| `tech-design` | BRD → план + `task-plan.json` + структура слоёв | `check_taskplan.py` |
| `java-spring-dev` | Генерация Java-кода (слои, аннотации, TDD) | `check_build.py` |
| `jira-task-writer` | Создание задач Jira (Story + Sub-task) | `check_jira.py` |
| `brd-interview` | Интервью по требованиям (диалог) | — |
| `business-requirements` | BRD из идеи (быстро) | — |
| `minor-defect-fix` | Фикс дефекта из Jira (минимальный, починить) | `check_coverage.py` |
| `defect-analyzer` | Анализ дефекта | — |
| `bugfix-developer` | Минимальный фикс | — |
| `brd-grounder` | Grounding для BRD | — |
| `java-uml-spec` | MD-спека + UML-диаграммы | — |
| `project-packer` | Упаковка исходников (без чувствительных) | — |
| `project-assembler` | Сборка проекта из склейки | — |
| `gigacode-migrator` | Миграция скиллов между CLI-системами | dry-run |
| `skill-creator` | Создание/правка скиллов | — |
| `plantuml-to-png` | PlantUML → PNG | — |
| `pdf` / `pptx` | Работа с PDF/PPTX | — |

## Журнал решений (почему так)

- **Enforcement в рантайме, не в тексте.** SKILL.md модель может проигнорировать → гейты/политики
  форсятся хуками (gate-guard/risk-ladder, evidence-enforcer, cost-breaker, phase-gate, security).
- **Risk ladder R0–R5, deny-first** (`risk-policy.json`) — policy-as-code, рисковое fail-closed.
- **Выбор критичности фичи форсится** — после BRD SKILL спрашивает критичность (low/medium/high →
  `autonomy.auto_max_risk` R2/R1/R0 в `pipeline.json`); `gate-guard` блокирует любое R2+ действие, пока
  `autonomy.criticality` не задана. На прошлых прогонах выбор пропускался — теперь нельзя.
- **Evidence bundle** перед доставкой (completeness ≥ `evidence.threshold`).
- **Pipeline-state намеспейсится ПО ФИЧЕ**: `ground/statements/feature-pipeline/<feature>/` (был один
  `pipeline/` на все фичи → вытесняли друг друга). Фичи сосуществуют, резюм точечный.
  `--feature <slug>` во всех вызовах init/read/update/add_steps/build_evidence.
- **Grounding не повторять** — `check_grounding.py` (детектор в нескольких местах) → reuse молча;
  свежесть между фичами держит `enrich_grounding.py` (инкрементально по изменённым модулям, без полного рескана).
- **BRD на языке бизнеса** — никаких классов/сущностей/методов/SQL в BRD; код-факты идут в tech-design;
  grounding-выжимка BRD живёт в `ground/brd-grounding/`, не рядом с самим BRD.
- **Кастомные слэш-команды (`commands/`) удалены** (2026-06-04). Причина: дублировали скиллы (точка входа
  и так есть — триггер по описанию или `/skills feature-pipeline`), не деплоились (`deploy.sh` копирует
  только hooks+skills), а их дизайн `!{cat all skills}` вливал тела всех скиллов в один контекст на старте
  → раздувание контекста → обрывы стрима. Точка входа = скилл, не команда.
- **TDD по умолчанию** (`quality.tdd:true`): per-task RED→GREEN. Тесты вперёд (service-unit+моки, валидные
  данные, избегать @DataJpaTest), затем стабы сигнатур → `check_tests_red.py` (компилируется+падает) →
  минимальная реализация до зелёного → `check_build` → coverage. Шаги манифеста `04-test-<id>`→`04-build-<id>`.
  **Форсится хуком** `tdd-guard`: запись в `src/main` блокируется, пока `04-test-<id>` ещё `pending`
  (на прогоне TDD не происходил — код писали первым; теперь нельзя). Тесты писать можно всегда.
- **Лог поведения агентов+субагентов для анализа** (`log-agent.py` пишет `ground/ai-logs/<run>/`
  И в единый архив `<home>/ai-logs-archive/agents-YYYYMM.jsonl`).
- **Pre-flight self-check харнеса** (`preflight.py`: `settings.hooks.json` + `pipeline.json`) — ловит
  незадеплоенный харнес ДО старта; exit 1 → «ENFORCEMENT OFF, остановись».
- **Субагент = явный вызов `agent`**, не inline. Всё, что помечено «субагент» — через `agent` (иначе
  теряется изоляция контекста и устойчивость к обрыву стрима).
- **Блокировка хука = `exit 2` + причина в `stderr`.** Рантайм при exit 2 игнорирует stdout, читает stderr.

### Структура хуков (порядок в массивах значим)

**PreToolUse `^Bash$` — sequential:**
1. `destructive-blocker` — чёрный список
2. `cost-breaker` — token budget
3. `evidence-enforcer` — полнота пакета
4. `gate-guard` — risk ladder
5. `log-agent` — аудит (последний, неблокирующий)

**PreToolUse `(Write|Edit)` — sequential:**
1. `pii-boundary` — PII scope
2. `tdd-guard` — форсинг TDD
3. `gate-guard` — risk ladder
4. `log-agent` — аудит

Любой блокирующий может остановить (exit 2) до действия. Логгер — всегда последний и неблокирующий.
Точный блок — в `settings.hooks.json`.

## Три расположения харнеса

| Каталог | Зачем | Конфиг-дом |
|---|---|---|
| `~/.gigacode/` (source) | **source-of-truth** (этот каталог) | — |
| `~/.qwen/` | локальный тест (бинарь на dev) | `~/.qwen/` |
| `~/.gigacode/` | **прод-цель** (задеплоено) | `~/.gigacode/` |

> **Гейты вызываются по `../skills/`** — в целевом доме рядом с `hooks/` должны лежать `skills/`.
> `deploy.sh` копирует И хуки И скиллы в один дом (co-location).

## Деплой — ОДНОЙ КОМАНДОЙ (канонический путь)

```bash
bash ~/.gigacode/deploy.sh            # прод
bash ~/.gigacode/deploy.sh ~/.qwen    # тест-дом
```

`deploy.sh` сам: (1) копирует `hooks/` И `skills/` в ОДИН дом (co-location — иначе гейты не найдут
`../skills`), (2) мержит блок `hooks` в `settings.json` с ретаргетом путей на этот дом и снимает
`disableAllHooks`, (3) прогоняет `doctor.py`.

> ⚠️ **Не копируй скиллы и хуки вручную по отдельности.** Если скиллы на проектном уровне,
> а блок `hooks` в `settings.json` не влит → `[HOOK_REGISTRY] 0 hook entries`, весь control-plane
> молчит. `deploy.sh` исключает этот класс ошибок.

## Запуск рантайма (ОБЯЗАТЕЛЬНО с флагом хуков)

В форке GigaCode хуки — **экспериментальная опция**, гейтятся CLI-флагом. Без него рантайм
стартует с `[HOOK_REGISTRY] 0 hook entries` — весь control-plane молчит.

```bash
gigacode --experimental-hooks -p "<задача>"
# или интерактивно:
gigacode --experimental-hooks
```

Флаг — это флаг **запуска бинаря**, его нельзя прописать в `settings.json`. `deploy.sh`/`doctor`
его не ставят (не могут — это аргумент процесса); `preflight.py` ловит отсутствие по firing-evidence.

**Перед каждым серьёзным прогоном — быстрый self-check, что контроль реально включён:**
```bash
python3 ~/.gigacode/hooks/preflight.py --project .
```
- ✅ `exit 0` — можно работать
- ❌ `exit 1` — ENFORCEMENT OFF, подними флаг/деплой

## Разбор прогонов

### Прогон `pprb-kid` (2026-06-04) — провальный, уроки
Корневые причины:
- 🔴 `[HOOK_REGISTRY] 0 hook entries` — **хуки не были развёрнуты** (залили только скиллы,
  hooks-блок в `settings.json` не влили). Весь control-plane молчал → «не спросил критичность»,
  «нестабильно».
- 🔴 Co-location нарушена: skills на проектном уровне, `~/.gigacode/skills` пуст → гейты не нашли бы
  `../skills`.
- 🟠 Субагенты не использовались — НЕ из-за рантайма (general-purpose субагенты стартуют нормально;
  `agent models: []` — про другой каталог, не блокер). Реальная причина: фазы-субагенты в SKILL.md
  были описаны прозой («тестописатель пишет тесты») без ЯВНОГО вызова тула `agent` → модель
  делала работу inline. Фикс: явные `agent(subagent_type=..., prompt=...)` в фазах
  Verify/Document + правило «субагент = вызов тула, не сделай сам».
- 🟠 Grounding искался узко; BRD был с код-деталями.

**Вывод:** всегда деплоить через `deploy.sh` и проверять `preflight` ДО прогона — это убирает
класс ошибок «0 hook entries / skills не рядом».

## Роадмап (что дальше)

- [x] Ранний вопрос о критичности фичи после BRD → `autonomy.auto_max_risk`; форсится `gate-guard`.
- [x] Тест-стратегия: `tdd-guard` блокирует `@DataJpaTest`/`@SpringBootTest` при `quality.test_layer=
      service-unit` (падали `initializationError`); escape-hatch `test_layer=mixed`.
- [x] Pre-flight self-check харнеса в §0.0 (`preflight.py`: `settings.hooks.json` + `pipeline.json`) —
      ловит «0 hook entries» (кейс `pprb-kid`) ДО старта; exit 1 → «ENFORCEMENT OFF, остановись».
- [x] Политика отказа/эскалации + гигиена контекста + probe субагентов — секция «Устойчивость» в
      `feature-pipeline/SKILL.md` (лимит 3, failed+спросить, не `force-push`, не обходить, субагенты для
      тяжёлого, excerpts).
- [x] Аудит исходников: фикс `additionalContext`→`hookSpecificOutput`, context-injector без `agent_type`,
      SoD помечен неактивным, fail-open задокументирован, флаг `--experimental-hooks` (форк) — везде в командах.
- [ ] (опц.) Устойчивость к обрывам стрима глубже: точечный per-file TDD-маппинг, авто-resume.

## Известные ограничения (из аудита)

- **`additionalContext` только в `hookSpecificOutput`** — рантайм читает контекст-инъекцию ТОЛЬКО из
  `hookSpecificOutput.additionalContext` (core/hooks/types). Все наши хуки исправлены под это.
- **Subagent `agent_type` = `general-purpose`** для всех наших субагентов (мы так дёргаем `agent`).
  Поэтому `context-injector` НЕ зависит от типа (инъектит по наличию файлов), а **separation-of-duties
  через `agent_caps` сейчас НЕАКТИВНО** (заработает только с кастомными `subagent_type`). Не считать его рабочим.
- **Гейт-хуки fail-OPEN при таймауте/краше** (`hookEventHandler`: блок при сбое только для Todo-событий;
  команд-хук >60с убивается → действие проходит). Поэтому тяжёлые гейты (`check_taskplan`/`check_delivery`/
  coverage) запускает ОРКЕСТРАТОР как execution-gate (так и есть в SKILL), а хуки лёгкие (file-reads) —
  страховка. Не клади тяжёлые subprocess в hook hot-path.
- **Command substitution `$(...)`/backticks РЕЖЕТСЯ** в shell-вызовах агента → в SKILL.md/доках/инструкциях
  её НЕТ БЫЛО (каталог `.`, путь к репо скрипты берут сами через `repo_root()`). Внутри `.sh` — можно.
- **Блокировки (exit 2 + stderr) работают надёжно**; Subagent-события срабатывают для тула `agent`.

## Диагностика (перед прогоном)

```bash
python3 ~/.gigacode/hooks/preflight.py --project <repo>     # харнес активен?
# или коротко:
bash smoke-cli.sh ~/.gigacode --live                        # runtime-контракт

# какие фичи в работе:
python3 ~/.gigacode/skills/pipeline-state/scripts/read.py --skill feature-pipeline --list
```

## Состояние пайплайна (state)

- `preflight` — проверка settings (линтер на живом `settings.json`)
- `doctor` — статика (хуки + скиллы + evals)
- `smoke-cli` — runtime-контракт через CLI

## Наблюдаемость

```bash
# живой лог прогона (отдельный терминал):
bash ~/.gigacode/hooks/watch-agents.sh

# сводка по метрикам:
python3 ~/.gigacode/hooks/agentops.py --archive ~/.gigacode/ai-logs-archive
```

## Поддерживаемая структура

```bash
~/.gigacode/
├── FORGE.md                  # этот файл
├── deploy.sh                 # развёртывание
├── smoke-cli.sh              # runtime-контракт
├── GUIDE.md                  # руководство для пользователя
├── AGENT-RUNBOOK.md          # runbook для агента-владельца
├── hooks/                    # control-plane (18 скриптов)
│   ├── settings.hooks.json   # эталон
│   ├── risk-policy.json       # политика рисков
│   ├── preflight.py          # self-check
│   ├── evals/run-evals.py   # eval-набор
│   └── test_*.py            # тесты каждого хука
└── skills/                   # пайплайн-скиллы (22 шт)
    ├── SKILLS-REGISTRY.md    # реестр с owner/validity/evals
    ├── feature-pipeline/     # оркестратор
    ├── pipeline-state/       # состояние
    ├── system-analyst/      # grounding
    ├── tech-design/         # проектирование
    ├── java-spring-dev/     # генерация кода
    ├── jira-task-writer/    # задачи Jira
    ├── brd-interview/       # интервью
    ├── business-requirements/ # BRD
    └── ...

```

## Решения на основе Claude Code (2026-06-13)

### Динамический реестр фаз (вместо хардкода)
- **Мотивация:** Фазы были жёстко прописаны в SKILL.md → изменение пайплайна требовало правки SKILL.md.
- **Решение:** Введён `resolve_phases.py` — аналог GrowthBook runtime feature gating из Claude Code.
  Фазы резолвятся динамически из `pipeline.json` + `feature-gates.json` перед каждой итерацией.
  `enabled_by` — аналог `feature('XYZ')` (compile-time gate), `skip_if` — runtime условие.
- **Преимущество:** Можно включать/выключать фазы через конфиг без правки SKILL.md.
  `phases_override` в pipeline.json позволяет добавить новую фазу (например security-review)
  без изменения кода скилла.

### Runtime feature gates с дисковым кэшем
- **Мотивация:** В Claude Code используется GrowthBook с трёхуровневой стратегией (env → disk cache → default).
- **Решение:** `ground/feature-gates.json` — аналог GrowthBook disk cache. Хук `gate-resolver.py`
  читает его на SubagentStart и внедряет gates как `additionalContext`. Стратегия загрузки:
  1. Environment overrides (`GATE_OVERRIDE_<NAME>`)
  2. Disk cache (`feature-gates.json`)
  3. Defaults (вшитые в `gate-resolver.py`)
- **Преимущество:** Feature gates доступны и хукам, и модели. Не требуют внешнего сервиса.

### state-recorder — прямая запись (FlushGate удалён)
- **Было:** `flush_gate.py` (порт `bridge/flushGate.ts`) буферизовал микро-обновления в
  module-global и сбрасывал их при `phase_boundary`.
- **Почему убрали:** каждый SubagentStop — отдельный процесс хука, поэтому in-process
  буфер `_gates` никогда не переживал между вызовами (всегда неактивен) + `batch_update.py`
  не существовал. Абстракция была мёртвой и вводила в заблуждение.
- **Сейчас:** `state-recorder.py` пишет каждый шаг напрямую через `update.py` в namespace
  активной фичи (`--feature`), ошибки `update.py` логируются в stderr.

### Permission-хуки с уровнями риска (gate-resolver)
- **Мотивация:** В Claude Code PreToolUse хуки могут вернуть `permissionDecision: allow|deny|ask`,
  что меняет поведение permission-системы.
- **Решение:** `gate-resolver.py` возвращает gates как `additionalContext` + `hookSpecificOutput`.
  Это позволяет другим хукам (tdd-guard, evidence-enforcer) принимать решения на основе gates.
- **Преимущество:** Унифицированный механизм включения/выключения enforcement-хуков.

## Установка/дистрибуция

- `install.sh` — пользовательская установка «всё сразу», канал-агностично (берёт исходник из
  своей папки → работает из git clone / архива / общего каталога).
- `install.sh` проверяет пред-условия (python3, CLI),
  зовёт `deploy.sh`, печатает next-steps (запуск с `--experimental-hooks`, `preflight`, запуск фичи).

## Обслуживание

- **`doctor.py`** — не существует как отдельный файл (если есть — в `hooks/`). Диагностика:
  - `preflight.py` → `deploy.sh` → `doctor` (через `risk-policy.json` / `settings.hooks.json`)
- **`validate_skills.py`** — валидатор frontmatter всех скиллов (name/description);
  ловит «мёртвые» скиллы (без шапки → рантайм молча скипает).

## Связанное

- `AGENT-RUNBOOK.md` — инструкция владельцу (как деплоить, какими командами)
- `hooks/DEPLOY.md` — полный ростер хуков (какие события, порядок, диагностика)
- `skills/SKILLS-REGISTRY.md` — реестр скиллов с owner/validity/evals
- `risk-policy.json` — policy-as-code (R0–R5)

> История — это git-история этого репозитория. Хочешь полноценный аудит изменений —
> `git init` здесь и коммить по фичам; тогда «журнал решений» дополняется коммит-сообщениями.