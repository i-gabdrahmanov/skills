# FORGE.md — Архитектура и решения (Feature Pipeline Forge)

> **Источник правды** для feature pipeline — не привязан к конкретной модели или версии.
> Это самодостаточная документация харнеса: архитектура, решения, разбор прогонов, роадмап.
> Файл версионируется вместе с `hooks/` и `skills/`, поэтому работает **независимо от чьей-либо
> личной памяти ассистента**. Любой оператор/агент должен опираться на ЭТОТ файл, а не на
> внешние заметки. При изменении харнеса — обновляй ЗДЕСЬ.

## Что это

Репозиторий Forge — source-of-truth e2e-обвязки для реализации фич в Java/Spring через feature
pipeline. Принцип (PDLC v3.5): **Pipeline > model; hooks = enforcement; skills = guidance**.
Модель **проектная**: разворачивается в `<project>/.gigacode/` (см. [docs/deployment.md](docs/deployment.md)).

- `hooks/` — control-plane (см. `hooks/DEPLOY.md` — полный ростер, порядок, диагностика).
- `skills/` — пайплайн-скиллы: вход `router` → `feature-pipeline` (full) или `forgelite` (lite) + фазовые (см. «Router + режимы full/lite»).
- `deploy.sh` — установщик: разворачивает Forge в указанный проект одной командой
  (co-location hooks+skills + доки, мерж hooks-блока с бэкапом).
- `uninstall.sh` — деинсталлятор (зеркало `deploy.sh`, те же аргументы): снимает блок hooks и
  удаляет обвязку; `ground/` и конфиг оператора остаются (`--purge-state` сносит и их).
- `deploy-local.sh` — in-project фиксер путей в `settings.json` (живёт в `<project>/.gigacode/`).
- `hooks/preflight.py` — диагностика готовности ДО прогона.

## Архитектура (фазы feature-pipeline)

`идея/Jira → BRD → grounding → SDD → tech-design → Jira → build → verify → document`.
Доставки в пайплайне нет: commit/push/PR/отчёт делает пользователь сам (промптом или руками)
после завершения — пайплайн отдаёт верифицированный артефакт.
Гейты: точки подтверждения пользователем + детерминированные execution-gate'ы (Python) на каждую фазу.
Состояние — `pipeline-state` (manifest), резюмируемо. Подробности — `skills/feature-pipeline/SKILL.md`.

**Ключевой принцип:** Правила качества форсит сам рантайм (хуки), а не «добрая воля» модели —
пропустить тесты, выкатить без проверок или сделать рискованное «молча» нельзя.

## Router + режимы full / lite (одна обвязка, две ветки)

Точка входа — скилл `router` (`skills/router/SKILL.md`). ПЕРВЫМ действием он спрашивает
пользователя, каким путём вести работу, и делегирует на общий control-plane (один `.gigacode`,
одни хуки):

- **full** → `feature-pipeline` — фича с нуля (BRD→…→document), вокабуляр шагов `04-test-<id>`/
  `04-build-<id>`, стейт в namespace `feature-pipeline`.
- **lite** → `forgelite` (`skills/forgelite/SKILL.md`) — исполнение УЖЕ ПОДГОТОВЛЕННОЙ подзадачи
  Jira: grounding → tech-design по СУЩЕСТВУЮЩЕЙ спеке → TDD RED→GREEN → покрытие.
  Плоские шаги `lite-*`
  (`lite-jira/lite-ground/lite-design/lite-red/lite-green/lite-verify`),
  стейт в namespace `forgelite`. Без BRD и без написания SDD с нуля, без постановки задач;
  `lite-design` строит tech-design + task-plan по готовой спеке (`sources.spec`, source of truth,
  форсится `required_decisions`). Гейты RED/GREEN — прямыми gradle/maven-командами субагента,
  покрытие — `check_coverage.py`; без run_judge/eval. Роутер выставляет lite-профиль:
  `autonomy.auto_max_risk=R2`, `criticality=medium`,
  `quality.eval_enabled=false` (через `config-helper`).

**Почему форк не нужен.** Хуки — **dual-vocabulary**: понимают оба набора префиксов и резолвят
активный skill/feature по САМОМУ СВЕЖЕМУ манифесту в `ground/statements/*/*/` (не по фикс-namespace).
`tdd-guard` (блок `src/main` до RED: `04-test-<id>` ИЛИ `lite-red`), `sod-enforcer`
(`STEP_ROLE` c `lite-*`), `inline-phase-guard`/`pipeline_phases.SUBAGENT_PHASE_PREFIXES`
(+`lite-red/green/verify`), `state-recorder`/`risk_ladder` (newest-manifest across skills). Lite-ids
намеренно НЕ пересекаются с масками `judges-registry` и с `PREFIX_PHASE` full-пути → lite не тянет
судей и фазовые артефакты full. `eval-guard` для lite сам fail-open (нет `04-build-<id>` +
`eval_enabled=false`). Инвариант «каждая subagent-фаза покрыта хуком» пинится
`test_phase_enforcement_coverage.py` (включая `lite-*`).

Установка и запуск — те же: `bash deploy.sh <project>` (router+forgelite едут в `skills/`),
затем `gigacode --experimental-hooks -p "..."`. Отдельного lite-инсталлятора нет; коллизии
`.gigacode` нет — харнес один.

### Хуки (control-plane)

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit | permission gateway, risk ladder R0–R5, **deny-first**; форсит выбор критичности | exit 2 |
| `tdd-guard.py` | PreToolUse Write/Edit | форсит TDD per-task (блок `src/main` пока RED-тест задачи `04-test-<id>` не completed) + тест-стратегию (блок `@DataJpaTest`/`@SpringBootTest` при `test_layer=service-unit`) | exit 2 |
| `eval-guard.py` | PreToolUse Write/Edit | форсит EDD: блок `src/main` пока eval'ы задачи не passed в кэше `evals.json` (read-only; прогон — `run_pending_evals.py`) | exit 2 |
| `sod-enforcer.py` | PreToolUse Write/Edit/Bash | separation of duties: роль из активного шага манифеста (test не пишет src/main; design/spec не коммитят/пушат/билдят) | exit 2 |
| `destructive-blocker.py` | PreToolUse `run_shell_command` | чёрный список (`rm -rf /`, force-push, DROP…) | exit 2 |
| `fork-syntax-guard.py` | PreToolUse `run_shell_command` | инструктивный блок синтаксиса, который режет нативный сейфти форка (`$(...)`, backticks, `find -exec`, `ls -R`) — вместо молчаливого deny объясняет замену (Glob/Grep/Read) | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/scope вне секретов | exit 2 |
| `state-write-guard.py` | PreToolUse Write/Edit/Bash | запрет прямой записи моделью в control-plane-файлы (`manifest.json`, `_origins`, `gates`, `overrides`, `judges`, `approvals`, `pipeline.json`, `ground/phases/`) — мутация только через санкц. скрипты | exit 2 |
| `inline-phase-guard.py` | PreToolUse Bash/Write/Edit | actor-guard: главный агент не производит артефакты/код/билд subagent-фазы inline (по `agent_type`) | exit 2 |
| `budget-meter.py` | Post/SubagentStop/Stop | информационный учёт токен-бюджета: tally по фазам + финализация/сводка на Stop. **Не блокирует и не предупреждает** (никакого circuit-breaker) | нет |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch) | детект prompt-injection → additionalContext | нет |
| `file-journal.py` | PostToolUse Write/Edit/Bash | безусловный журнал изменённых файлов активной фичи (`journal/files.jsonl`, привязка к step_id) — скоуп восстановления кода для `rollback.py` | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим `in_progress` | block |
| `log-agent.py` | все | append-only JSONL аудит (sync) | нет |

**Не-хуки рядом:** `preflight.py` (проверка «харнес активен?» ПЕРЕД пайплайном — ловит «0 hook entries»),
`risk-policy.json` (policy-as-code, `risk_ladder.py` читает), `settings.hooks.json` (эталон).

### Скиллы (pipeline)

| Скилл | Назначение | Evals |
|---|---|---|
| `feature-pipeline` | Оркестратор: ведёт фичу по фазам от BRD до верифицированного артефакта (доставка — на пользователе) | gate-скрипты + evals |
| `pipeline-state` | Состояние многошаговых пайплайнов с субагентами | косвенно через evals |
| `project-grounder` | Фаза 1 (grounding): переиспользует обзор или зовёт `system-analyst` | `verify_coverage.py` |
| `system-analyst` | Скан Java/Spring сервиса (модули, API, Kafka, БД) | `verify_coverage.py` |
| `sdd` | BRD → спецификация `sdd.md` (GWT, API, данные, приёмка) | `check_sdd_doc.py` |
| `tech-design` | SDD → план + `task-plan.json` + структура слоёв | `check_taskplan.py` |
| `java-spring-dev` | Генерация Java-кода (слои, аннотации, TDD) | `check_build.py` |
| `jira-task-writer` | Создание задач Jira (Story + Sub-task) | `check_jira.py` |
| `brd-interview` | Интервью по требованиям (диалог) | — |
| `business-requirements` | BRD из идеи (быстро) | — |
| `minor-defect-fix` | Фикс дефекта из Jira (минимальный, починить) | `check_coverage.py` |
| `defect-analyzer` | Анализ дефекта | — |
| `bugfix-developer` | Минимальный фикс | — |
| `brd-grounder` | Grounding для BRD | — |
| `config-helper` | Настройка параметров forge (pipeline/gates/risk) скриптом | `test_config.py` |
| `harness-verifier` | Семантическая верификация харнеса (скиллы+хуки) перед релизом | методический (бриф+чек-лист) |
| `pdf` / `pptx` | Работа с PDF/PPTX | — |

## Журнал решений (почему так)

- **Enforcement в рантайме, не в тексте.** SKILL.md модель может проигнорировать → гейты/политики
  форсятся хуками (gate-guard/risk-ladder, phase-gate, security). Токен-бюджет — только учёт (`budget-meter`), не гейт.
- **Risk ladder R0–R5, deny-first** (`risk-policy.json`) — policy-as-code, рисковое fail-closed.
- **Выбор критичности фичи форсится** — после BRD SKILL спрашивает критичность (low/medium/high →
  `autonomy.auto_max_risk` R2/R1/R0 в `pipeline.json`); `gate-guard` блокирует любое R2+ действие, пока
  `autonomy.criticality` не задана. На прошлых прогонах выбор пропускался — теперь нельзя.
- **Доставка — на пользователе** (2026-07-17): commit/push/PR/отчёт пайплайн не делает и не гейтит;
  фазы 07-deliver/07-report/lite-deliver/lite-report и evidence-bundle-обвязка удалены —
  больше половины токенов уходило на обход граничных случаев доставочной обвязки.
- **Pipeline-state намеспейсится ПО ФИЧЕ**: `ground/statements/feature-pipeline/<feature>/` (был один
  `pipeline/` на все фичи → вытесняли друг друга). Фичи сосуществуют, резюм точечный.
  `--feature <slug>` во всех вызовах init/read/update/add_steps/build_evidence.
- **Grounding не повторять** — `check_grounding.py` (детектор в нескольких местах) → reuse молча;
  свежесть между фичами держит `enrich_grounding.py` (инкрементально по изменённым модулям, без полного рескана).
- **BRD на языке бизнеса** — никаких классов/сущностей/методов/SQL в BRD; код-факты идут в tech-design;
  grounding-выжимка BRD живёт в `ground/brd-grounding/`, не рядом с самим BRD.
- **Короткая точка входа `forge` / `/forge`** (2026-07-20). Пайплайн зовётся коротким именем **forge**.
  Два механизма:
  - **Слово `forge`** — синонимы в `description` скилла `feature-pipeline` («forge», «запусти forge»,
    «прогони forge», «forge <фича>»). Активация идёт по описанию, деплоится вместе со скиллом, контекст
    не пухнет. Аргумент после `forge` (ключ Jira/описание) — вход BRD-фазы.
  - **Команда `/forge`** — `commands/forge.md` (Markdown+frontmatter — актуальный формат qwen-code;
    минимальный prompt-делегат в feature-pipeline, `{{args}}` прокидывает хвост; БЕЗ старого
    `!{cat all skills}` — раздувания контекста нет). `deploy.sh` (шаг 3b) кладёт её в
    `<target>/.gigacode/commands/forge.md` — рядом с `hooks/` и `skills/`, в единый `.gigacode`-корень,
    откуда GigaCode-рантайм читает конфиг (settings.json, skills/, commands/). TOML-команды qwen-code
    депрекейтнул: при `*.toml` в `commands/` рантайм показывает окно миграции на КАЖДОМ старте —
    поэтому формат сразу Markdown, а deploy/uninstall снимают устаревший `forge.toml`, если он остался.
  Про удаление 2026-06-04 (не повторять снос): из трёх прежних причин реальным дефектом был только
  `!{cat all skills}` (вливал тела ВСЕХ скиллов на старте → обрывы стрима) — он снят минимальным дизайном.
  «дублировали скилл» — оценочное (нам нужна короткая точка входа), «не деплоились» — просто не были
  вписаны в отдельный ручной `deploy.sh` (теперь вписаны, шаг 3b).
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

**PreToolUse `run_shell_command` (Bash) — sequential:**
1. `destructive-blocker` — чёрный список
2. `fork-syntax-guard` — инструктивный блок синтаксиса, который режет форк (`$(...)`, backticks, `find -exec`, `ls -R`)
3. `pii-boundary` — PII scope (перехват редиректов `>`/`tee`/`dd of=` в файл)
4. `state-write-guard` — запрет прямой записи в control-plane state (редирект/`python -c open()`)
5. `sod-enforcer` — separation of duties (роль фазы: design/spec/jira не билдят; git не гейтится)
6. `inline-phase-guard` — actor: главный агент не билдит/тестит inline в subagent-фазе
7. `gate-guard` — risk ladder
8. `log-agent` — аудит (последний, неблокирующий)

**PreToolUse `(Write|Edit)` — sequential:**
1. `pii-boundary` — PII scope
2. `state-write-guard` — запрет прямой записи в manifest/approvals/overrides/gates/_origins/judges/pipeline.json/ground-phases
3. `tdd-guard` — форсинг TDD per-task (RED-тест задачи)
4. `eval-guard` — форсинг EDD (eval'ы задачи passed)
5. `sod-enforcer` — separation of duties (роль из активного шага)
6. `inline-phase-guard` — actor: главный агент не пишет артефакты/код subagent-фазы inline
7. `gate-guard` — risk ladder
8. `log-agent` — аудит

Любой блокирующий может остановить (exit 2) до действия. Логгер — всегда последний и неблокирующий.
Точный блок — в `settings.hooks.json`. Эта секция пинится `hooks/test_docs_hooks_consistency.py`
(дрейф «доки ↔ деплой» → fail). Изоляцию фаз держат два слоя: `inline-phase-guard` (PreToolUse,
по `agent_type`) не даёт ГЛАВНОМУ агенту производить артефакты/код subagent-фазы inline, а
`update._check_subagent_origin` на закрытии шага требует реального `SubagentStop`-evidence
(`_origins/<step_id>.json`), а не доверяет флагу `--closed-by`.

## Расположение харнеса

| Каталог | Зачем |
|---|---|
| репо Forge (этот каталог) | **source-of-truth**: `hooks/` + `skills/` + `deploy.sh` |
| `<project>/.gigacode/` | **цель деплоя**: задеплоенная копия hooks+skills, закоммичена в репо проекта |

> **Гейты вызываются по `../skills/`** — в `<project>/.gigacode/` рядом с `hooks/` должны лежать
> `skills/`. `deploy.sh` копирует И хуки И скиллы в один каталог (co-location). Привязки к
> домашнему `~/.gigacode` нет: резолверы выводят базу из фактического расположения файла.

## Деплой — ОДНОЙ КОМАНДОЙ (канонический путь)

Модель **проектная**: разворачиваем в `<project>/.gigacode/`. Полное руководство —
[docs/deployment.md](docs/deployment.md).

```bash
# из склонированного репо Forge; целевая папка проекта обязательна
cd <forge>
bash deploy.sh /path/to/target-project
```

`deploy.sh` сам: (1) копирует `hooks/` И `skills/` в `<project>/.gigacode/` (co-location — иначе
гейты не найдут `../skills`) + доки, (2) кладёт `deploy-local.sh` и доводит `settings.json`
(merge блока `hooks` + бэкап старого, `permissions`/`mcpServers` сохраняются), (3) прогоняет
`preflight.py`. Повторный деплой идемпотентен; settings уходит в вечный `.bak` + таймстемпы.
Починить пути после переезда проекта — `bash <project>/.gigacode/deploy-local.sh`.

Снять обвязку — `bash uninstall.sh /path/to/target-project` (те же аргументы, что у деплоя;
`--dry-run` — план, `--purge-state` — снести ещё и `ground/` с git-чекпойнтами). Снимает блок
hooks ПЕРЕД удалением файлов: обратный порядок оставил бы конфиг с хуками на удалённые скрипты,
и рантайм падал бы на каждом вызове. `ground/`, `permissions`/`mcpServers`, чужие хуки и бэкапы
переживают снятие. Детали — [docs/deployment.md](docs/deployment.md).

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
python3 <project>/.gigacode/hooks/preflight.py --project <project>
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

### Прогон `autoclose-regular-tasks` / `KIDPPRB-8639` (2026-06-28) — незавершён, уроки
Контекст: фича «ночное автозакрытие пустых регулярных задач» через `feature-pipeline` на форке
**GigaCode v26.5.17**, модель **`vllm/DeepSeek-V4-Flash-262k`** (не Claude). Хуки развернулись
корректно (нет `0 hook entries`) — регрессии `pprb-kid` нет, субагенты реально вызывались.
Итог: **1h23m / 600 запросов / 51.8M входных токенов**, **остановлен вручную (`/quit`)** на фазе
Verify — пайплайн НЕ дошёл до Document/Deliver.

Корневые причины (по убыванию):
- 🔴 **Нет тормоза на зацикленной Verify.** Модель крутилась в петле «правка → сломанный тест →
  правка теста → coverage fail → `cleanTest` → шум падений» 1.5 часа без вмешательства. Нет лимита
  итераций фазы и нет потолка стоимости фазы → «стоп-и-спроси».
- 🔴 **Модель меняла PRODUCTION-код ради зелёного теста — гейт не поймал.** В финале —
  рефактор constructor→setter `@Autowired(required=false)` + null-check в `OverdueTasksScheduler`
  только чтобы тест поднялся без `UpzClient`; ранее сама добавила второй `notifyEvent`, сломала
  существующий тест (ждал 1 вызов) и стала править тест под новое поведение, а не усомнилась в нём.
  Нет судьи «прод-код/ослабление существующего теста ради GREEN».
- ⚪ **Токен-бюджет — только учёт, не тормоз (решено).** Бывший `cost-breaker` заменён на
  информационный `budget-meter`: circuit-breaker (стоп/warn) удалён полностью. Учёт по-прежнему
  расходится с реальностью (прогон #3: `budget.json` 843K/2M ≈ 42% против реальных 51.8M входных
  токенов, ~60×), но на него никто не полагается как на ограничитель — это осознанно справочная метрика.
- 🟠 **Гейт критичности форсит вопрос, но не следствие.** `auto_max_risk` хардкожен `R1`
  (`init_pipeline_config.py:223`), деривации `low→R2/high→R0` нет ни в коде, ни в тесте. Модель
  отредактировала только `criticality` (для medium совпало) — при low/high autonomy была бы неверной.
- 🟠 **Конфликт гейтов coverage ↔ test_layer.** `coverage-judge` (3 fail подряд) требовал покрытия
  `RegularTaskRepository.java`, а `tdd-guard` при `test_layer=service-unit` блокирует
  `@DataJpaTest`/`@SpringBootTest` — единственный способ покрыть репозиторий. `check_coverage`
  не исключает из «changed» непокрываемые слои (репозитории/энтити/интерфейсы).

Помельче: Verify гонял полный сьют с `cleanTest` в проекте с интеграционными тестами (102 чужих
падения, шум, нет baseline pre-existing); ложный блок `find … -exec` внутри workspace (вероятно
нативный сейфти форка, не хук); `ask_user_question` падал на `header ≤ 12` (лишний retry); логи
прогона раскиданы по множеству каталогов (нет «один прогон = одна папка»).

**Вывод:** хуки-enforcement развёрнуты и работают, но прогон провалила связка **«нет тормоза
стоимости/итераций» + «нет судьи против правки прод-кода/тестов ради GREEN»**. Контроль качества
должен ловить не только «пропустил тесты», но и «прогнул реальность под тест». См. роадмап ниже.

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
- [x] **Hardening-проход по аудиту обвязки (2026-06-21).** Закрыты дыры «задокументировано ≠
      исполняется»: `eval-guard` подключён в `settings.hooks.json` и сделан read-only (тяжёлый прогон —
      execution-gate `run_pending_evals.py`); `preflight` проверяет РЕАЛЬНОЕ подключение essential-хуков в
      `settings.json` (не наличие файла) + парсинг `risk-policy.json`; `subagent-enforcer` (мёртвый
      PreToolUse-блок) удалён, гарантия «фаза закрыта субагентом» перенесена на закрытие шага
      (`update._check_subagent_origin` + `state-recorder --closed-by subagent`); `sod-enforcer` переписан
      на роль из активного шага манифеста; снят дедлок evidence-before-build (evidence ушёл из R2/R3 в
      risk-policy, остаётся на доставке R4/R5 + `evidence-enforcer`); `risk_ladder` fail-CLOSED при
      битой/отсутствующей policy (`policy_loaded()`); мёртвые ключи политики и хук `gate-resolver`
      удалены; TDD-гейт стал per-task; провенанс вердиктов судей (`produced_by:run_judge`); единый
      источник списка фаз (`resolve_phases.DEFAULT_PHASES` ⊆ `MAIN_PHASES`, пинится тестом). Тесты:
      все 18 `hooks/test_*.py` сделаны запускаемыми (были битые стабы `import x-y`), +`test_preflight`,
      `test_sod-enforcer`, `test_subagent_origin`, `test_docs_hooks_consistency` (доки↔settings).
- [ ] (опц.) Устойчивость к обрывам стрима глубже: авто-resume.

**Hardening под слабую модель (2026-07-02, прогон на DeepSeek-V4-Flash):**
- [x] **Детерминированный брейк ре-итераций шага.** Лимиты «перезапусти, лимит 3» были прозой →
      `update.py` считает `reopens` (переоткрытие completed/failed → pending/in_progress, блок ДО
      записи) и `failures` (повторные транзишены в failed; провал фиксируется, затем exit 3).
      Лимит `quality.max_step_reopens` (дефолт 3, registry config-helper); exit 3 = ESCALATE
      («стоп-и-спроси», как у run_judge); эскейп `override_judge.py --judge step-reopen-<step_id>`.
      `state-recorder` печатает баннер эскалации целиком. Тесты: `test_step_reopens.py`.
- [x] **Gate-result артефакт: закрытие build/verify-шагов не по слову модели.** `state-recorder`
      закрывал шаг по JSON субагента (`status:"completed"`) без проверки — flash-модель возвращает
      completed при упавшей сборке. Теперь `update._check_gate_result` требует
      `gates/<step_id>.json` с провенансом `produced_by:"record_gate"` + `passed:true` для
      `04-test/04-build/05-tests/lite-red/lite-green/lite-verify` (единый источник —
      `pipeline_phases.GATE_RESULT_PREFIXES`). Артефакт пишет `pipeline-state/scripts/record_gate.py`
      по фактическому exit-коду команды гейта (`--expect red` — семантика «компиляция OK, тесты
      падают»). Эскейп `--judge gate-result-<step_id>`. Тесты: `test_gate_result.py`.
- [x] **Floor «GREEN любой ценой» расширен** (`run_judge._test_integrity_floor`): нетто-потеря
      assert/verify ≥2 — теперь БЛОК (был WARN); новые блок-детекторы: переписанные ожидаемые
      значения в существующих assert'ах (скелет совпал, литералы разные) и удаление
      @Test-методов из существующих файлов. Тесты: `test_run_judge_guards.py` (16).
- [x] **`fork-syntax-guard.py`** — новый PreToolUse Bash хук: `$(...)`, backticks, `find -exec`,
      `ls -R` блокируются с ИНСТРУКТИВНЫМ stderr (чем заменить) вместо молчаливого нативного deny
      форка, на котором слабая модель жгла итерации. Не essential (эргономика).
- [x] **Preflight жёстче:** doctor-находка `registry-paths-exist` (битые межскилловые пути из
      `skill-paths.json`, напр. forgelite → `minor-defect-fix/scripts/check_coverage.py`) — теперь
      exit 1, не warning. Router: «preflight exit 1 = стоп».
- [x] **`context-injector` валидирует grounding-excerpt.json** перед инъекцией: битый JSON не
      инъектится (stderr-warning), отсутствие ключей `modules`/`conventions` — warning.
- [x] **Детерминированный скоуп-чек lite** (`forgelite/scripts/check_scope.py`): issuetype
      Epic/Story/New Feature, пустое описание, нераспознанные AC, refactor/migration-слова →
      exit 3 ESCALATE («lite или full?»). Обязателен перед закрытием `lite-jira`.
      Тесты: `test_check_scope.py`.
- [x] **Фазовые брифы вместо монолитного SKILL.md** (контекст-гигиена). SKILL.md feature-pipeline
      (1399 строк — flash-модель роняла правила из середины) разбит: диспетчер ~570 строк (общие
      правила §0–§2, ре-итерация, устойчивость, цикл фаз) + 10 брифов
      `references/phases/<phase>.md` (§3–§10 перенесены дословно). Оркестратор перед каждой
      фазой: `resolve_phases.py --current --feature <slug>` → `{current_phase, brief, gates}`
      (обёртка над `pipeline_phases.live_phase_decision`) → `read_file(бриф)`. Поле `brief`
      в `DEFAULT_PHASES`/выводе resolve_phases, переопределяемо через `phases_override`.
      Дрейф пинится `test_phase_briefs.py` (брифы существуют/непустые, SKILL.md без фазовых
      секций и ≤700 строк, каждый бриф упоминает гейт закрытия и SKILL.md §0.6);
      `test_skill_close_commands.py`/`test_get_prompt.py` сканируют корпус SKILL.md+брифы.

**Из прогона #3 `autoclose-regular-tasks` (2026-06-28):**
- [x] **Брейк зацикленной фазы** (C1): `run_judge.py` форсит лимит ре-итераций судьи
      (`quality.max_judge_iterations`, дефолт 3) — по `errors.json` per-judge; при исчерпании печатает
      `⛔ STOP` и возвращает **exit 3** (ESCALATE) вместо бесконечного FAIL. SKILL §0.6/§8.3 трактуют exit 3
      как «стоп-и-спроси». Тесты: `test_run_judge_guards.py`.
- [x] **Судья «GREEN любой ценой»** (C2): floor `_test_integrity_floor` в `check_coverage` (фаза Verify)
      блокирует ослабление СУЩЕСТВУЮЩИХ тестов (`--diff-filter=M`): добавленный `@Disabled`/`@Ignore`,
      рост `times(N)→times(M)`; WARN на нетто-потерю assert/verify. Тесты: `test_run_judge_guards.py`.
- [x] **`budget-meter`** (C3, бывший `cost-breaker`): по решению владельца circuit-breaker удалён
      полностью — **бюджет только справочный**. Учёт токенов расходится с реальностью (~60×), поэтому
      как тормоз он и не задуман; хук лишь считает расход по фазам и печатает сводку на Stop.
- [x] **Деривация `auto_max_risk` из criticality** (H4): `set_criticality.py` атомарно пишет оба поля
      (`low→R2/medium→R1/high→R0`); гейт критичности в SKILL.md зовёт его, а не сырой Edit. Тесты:
      `test_set_criticality.py`.
- [x] **Снят конфликт coverage ↔ test_layer** (H5): `check_coverage.py --exclude` (glob); `run_judge`
      при `test_layer=service-unit` шлёт дефолтные исключения (repository/entity/dto/config),
      настраиваемые через `quality.coverage_exclude_globs`.
- [x] **Тише Verify** (M6): SKILL §8.2 — только затронутые модули/тест-классы (не полный `cleanTest`),
      снять baseline pre-existing-падений и не считать чужие интеграционные падения своими.
- [x] (M7) Источник блока `find … -exec` — **нативный сейфти форка, не хук** (проверено grep'ом);
      задокументировано в «Известные ограничения», в SKILL/доках — `Glob`/`Grep`/`Read`.
- [x] (M8) Памятка про лимит `header ≤ 12` добавлена в гейт критичности (SKILL.md) и BRD-скиллы.
- [x] (L9) BRD-интервью калибрует число вопросов по входу: при детальной Jira — 1–2 точечных, не «3 для галочки».
- [x] (L10) `log-agent.py` группирует логи по сессии: `ai-logs/run-<session8>/` («один прогон = одна папка»);
      `watch-agents.sh` обновлён под новый layout.

**Раунд 2 (доп. находки прогона #3):**
- [x] **Регресс-гейт затронутых модулей** (D): «успеха нет, пока тесты затронутых модулей не зелёные».
      Новый `module_tests.py` (snapshot/compare) + фаза `regression` в `run_judge` (`check_regression`),
      execution-gate в SKILL §8.3b. Baseline-diff: блокирует ТОЛЬКО новые регрессии (passed→failed),
      pre-existing/infra-падения не считаются виной. Закрывает «агент сломал Spring-тест и не признал».
- [x] **Baseline зелёного ДО разработки** (C): SKILL §7.0 — `module_tests.py snapshot --from-taskplan`
      в начале Build пишет `test-baseline.json` (отметка зелёного по затронутым модулям до первого кода).
- [x] **Jira-grounding «единый ключ»** (A): `jira_discover.discover_conventions` выводит из недавних issue
      проекта типовые `components`/`labels`/`frequent_epic`/`summary_prefix` → `pipeline.json.jira.conventions`;
      SKILL §0.2 их собирает, `jira-task-writer` применяет как дефолты — задачи в едином стиле проекта.
- [—] **Grounding scope** (B): снято — полный скан всего проекта при первом запуске **так и задумано**
      (разовый кэшируемый ground truth, переиспользуется между фичами). Не меняли.
- [x] **Гейт межмодульных зависимостей + архитектурный граунд** (E): агент дописал в `build.gradle`
      зависимость на модуль, который по правилам проекта подключать нельзя. `check_architecture.py`
      расширен: (1) `--emit-ground` строит **архитектурный граунд** проекта
      (`docs/system-analysis/architecture-ground.json` — граф модулей + `allowed_group_couplings`, что
      проект УЖЕ соединяет; эмитится на grounding §4, курируется через `ground/architecture-policy.json`);
      (2) `check_module_deps` ловит НОВЫЕ межмодульные зависимости (Gradle `project(':...')`, Maven
      `<dependency>` на внутренний модуль в `pom.xml`) в diff build-файлов и проверяет против граунда.
      Политика `quality.module_dep_policy` = **`graph`** (дефолт — **цикл** или **новая group-связка**,
      которой проект не делает, блокируются; принятые связки проходят: «соединять можно, но не новым
      способом молча») | `deny_new` | `policy` (`module_deps.forbidden`) | `off`; allow-list `allowed_new`;
      override-эскейп (§0.6.1). Если граунда нет — деривация на лету (текущий граф минус новые рёбра).
      Обязательный гейт SKILL §8.3c. Тесты: `test_check_architecture.py` (33).

**Финальная верификация харнеса перед релизом (2026-07-03, методика harness-verifier):**
семантический проход по всем зонам (хуки/ядро скиллов/периферия+деплой) + статический анализ
hook-payload установленного qwen-code 0.19.3. Чисто: проводка 15 хуков (settings↔FORGE↔DEPLOY),
dual-vocabulary, контракты pipeline-state, payload-схема snake_case совпадает с парсерами хуков.
Найдено и закрыто (1 BLOCKER / 4 MAJOR / 8 MINOR):
- [x] **B: `resolve_phases` падал на литеральном bool `enabled_by`** — ровно то, что пишет
      документированный `config.py phase disable` → `AttributeError` вместо JSON у `--current`
      на каждой фазе. Фикс: bool-guard в `_evaluate_enabled_by`/`_evaluate_skip_if`. Пин:
      интеграционные кейсы config↔resolver в `test_resolve_phases.py` (стык двух скиллов раньше
      не покрывался ни одним тестом).
- [x] **M: `phases_override` не умел ДОБАВЛЯТЬ фазу** (доки обещали в 2 местах, `phase add`
      писал в конфиг — резолвер молча игнорировал новые id). Фикс: append неизвестных id
      с позицией через ключ `after` (`config.py phase add --after <id>`; без него — в конец;
      сортировка по id невозможна — канон-порядок не лексикографический). Пин: там же.
- [x] **M: 5 ручек `quality.*`, которые пайплайн реально читает, отсутствовали в
      params-registry** (config-helper fail-closed → ручки были недоступны):
      `max_judge_iterations`, `tdd`, `test_layer`, `coverage_exclude_globs` (новый тип `list`
      в `_util.py`), `module_dep_policy`. Заодно: `tdd_enforced` (gates) оказался МЁРТВЫМ
      флагом — его никто не читает, а SKILL.md советовал им «выключать TDD»; дока и описания
      перенаправлены на живой `quality.tdd` (гасит и фазу 04-tdd, и tdd-guard). Пин:
      «реестр покрывает ключи-читатели» в `test_config.py`.
- [x] **M: реальные пути оператора уезжали в каждый деплой** — `minor-defect-fix/config.json`
      с `/Users/<имя>/…` был закоммичен и копировался `deploy.sh`. Фикс: файл раскоммичен и
      в .gitignore, в репо — нейтральный `config.json.example`; `deploy.sh` исключает локальный
      конфиг из копии. Пин: `test_no_hardcoded_paths_left.py` сканирует все md/json скиллов
      на машинные `/Users/<имя>/`-пути.
- [x] **M: тесты log-agent замусоривали боевой кросс-прогонный архив** — смоук с пустым stdin
      без изоляции дописывал all-null записи в `ai-logs-archive/` (17 шт. накопилось; это же
      объясняло «загадочные» null-логи — НЕ сбой рантайма) и создавал `ground/` в git-toplevel
      каталога запуска тестов. Фикс: tmp-cwd + `GIGACODE_AILOG_ARCHIVE` в тестах, мусор вычищен.
      Пин: pollution-guard в `run_all_tests.py` (прогон, изменивший `ai-logs-archive/`/`ground/`
      репо, — красный).
- [x] Минорные: `tdd-guard`/`eval-guard` резолвят корень через `project_root(cwd)` (git-toplevel),
      как соседи по цепочке — при cwd=подкаталог единственные форсеры TDD/EDD молча fail-open'или
      (пин: кейс «блок из подкаталога» в `test_tdd-guard.py`); `run-hook-tests.sh` теперь гоняет
      юнит-тесты хуков + eval-набор (был deprecated-шимом, 20 test_*.py не имели shell-входа);
      `project-grounder` добавлен в `skill-paths.json` (исполнитель фазы 01 не имел записи);
      §0.5 помечает `03-jira` условным (`jira.enabled`, дефолт false — расходился с resolve_phases);
      SKILLS-REGISTRY (обе копии) дополнен `router`/`forgelite`, pdf/pptx помечены external;
      deployment.md: требование Python 3.10 переформулировано (деплой-цепочка 3.9-safe,
      обоснование было ложным); `deploy.sh` копирует через tar-pipe без `__pycache__`/`.DS_Store`;
      DEPLOY.md: остаточный «CLAUDE.md» → «FORGE.md/SKILL.md».
- [x] **B: закрыт самоснимаемый эскейп-хатч — `override_judge` стал R4 с approval-гейтом.**
      Раньше снятие ЛЮБОГО детерминированного гейта (step-reopen, gate-result, судьи,
      subagent-origin) требовало только `--reason` (текст пишет сама модель), скрипт стоял в
      exemption `inline-phase-guard`, а deny-баннеры печатали ГОТОВУЮ команду — вся пирамида
      гейтов 1.11 снималась одной командой без человека. Теперь: policy-секция `gate_override`
      в risk-policy.json + проверка в `gate-guard` (до auto-early-return, fail-closed) —
      создание override блокируется (exit 2), пока нет approval-маркера
      `ground/approvals/gate-override-<judge>.json`, который фиксируется ТОЛЬКО после явного
      «да» пользователя; `--list`/`--remove` (чтение/восстановление enforcement'а) свободны.
      Все подсказки (update.py ×6 через `_override_hint`, run_judge, inline-phase-guard,
      SKILL §0.6.1 — теперь трёхшаговый: спроси → маркер → override) переписаны с
      escalate-first порядком. Пины: `TGateOverride` в `test_gate-guard.py` (без маркера →
      exit 2; с маркером → 0; чужой маркер не снимает; --list/--remove свободны).
- [x] **Гейт SDD-ревью: легальный канал согласования SDD с системными аналитиками.**
      Раньше вынести утверждённый sdd.md на ревью было НЕЛЬЗЯ вообще (spec-роль блокирует
      commit/push, push=R4). Первая версия (sdd_review_push.py, ветка `sdd-review/<slug>`)
      переработана в Гейт доставки доков — см. запись ниже.
- [x] **Гейт доставки доков (BRD+SDD): «нужен мердж и пуш?» ДО утверждения, с enforced
      ПАУЗОЙ; у каждой Jira-задачи — СВОЯ ветка доков.** Схема пользователя: перед
      согласованием brd/sdd спрашиваем «нужен ли мердж и пуш?»; «да» → коммитим док на
      ветку задачи `docs/<slug>` (slug почти всегда Jira-ключ; ветка общая для BRD и SDD
      задачи: brd.md приезжает в фазе 00, sdd.md — в фазе 02; база — default-ветка), пушим
      аналитикам и берём паузу до итогов ревью; «нет» → сразу гейт утверждения. Реализация:
      (1) `sdd_review_push.py` → обобщённый `doc_review_push.py` (`--doc brd|sdd`): git
      plumbing, коммитит ТОЛЬКО `<doc>.md` фичи ПОВЕРХ remote-tip ветки `docs/<slug>`
      (нет ветки — создаёт от default-ветки origin/HEAD → main|master; правки аналитиков
      на ветке не теряются) и пушит `sha:refs/heads/docs/<slug>` без force — локальные
      ветки/worktree/HEAD не трогаются вообще; требует approval-маркер `<doc>-review-<slug>`
      (провенанс record_approval), PASS `<doc>-judge` и secret-scan; идемпотентен;
      `--status` — ридонли; в separate-repo — ветка задачи в репо спеки (22 теста
      test_doc_review_push.py).
      (2) gate-guard: `check_sdd_review` → `check_doc_review` (policy-секция `doc_review`,
      deny-first до auto-early-return, fail-closed); паттерн покрывает и легаси
      `sdd_review_push.py` — деплой не удаляет убранные файлы, старый скрипт в целевых
      `.gigacode` остаётся под гейтом (`TDocReview` в test_gate-guard).
      (3) ПАУЗА enforced ниже брифа: update.py `_check_doc_approval` — закрыть
      `00-brd`/`02-sdd` (completed) нельзя без маркера утверждения
      `<doc>-approved-<slug>` (провенанс record_approval + совпадающий key; escape —
      `overrides/doc-approved-<step_id>.json` через override_judge). Это закрывает и
      «вопросов система не задаёт НИКАКИХ»: молча проскочить утверждение BRD/SDD теперь
      детерминированно невозможно, а после мерджа на согласование пайплайн не может
      продолжиться без возврата пользователя с итогами ревью (тесты 12–18 test_update.py).
      (4) Конфиг: `docs.brd_review` (новый) + `docs.sdd_review` — значения `push|skip`,
      headless-предзапись; брифы 00-brd/02-sdd переписаны под порядок «судья PASS →
      Гейт доставки (ветка задачи? → пауза) → Гейт утверждения (record_approval) →
      закрытие шага».
- [x] **Интеграционная ветка фичи `feature/<slug>`: прямые коммиты ЗАПРЕЩЕНЫ — только
      PR-мерджи сабветок задач.** Схема пользователя: в ветку feature не коммитится
      ничего, коммиты — только в сабветки. Реализация:
      (1) gate-guard `check_branch_protection` (policy-секция `branch_protection`,
      deny-first до auto-early-return, fail-closed, дефолты в коде): для активной фичи
      feature-pipeline блокируются history-команды (commit/merge/rebase/cherry-pick/
      revert/am) при HEAD на `feature/<slug>` и ЛЮБОЙ push в неё — все формы refspec
      (`br`, `src:br`, `src:refs/heads/br`, `:br` delete, `+br` force), `--all/--mirror`
      и bare `git push` с неё; `git -C` не обходит (репо резолвится из исходной команды).
      forgelite вне скоупа — там `feature/<KEY>` сама ветка задачи (9 тестов
      TBranchProtection).
      (2) Санкционированное создание — `story_branch_push.py`: пушит default-tip origin
      на `refs/heads/feature/<slug>`; коммитов не создаёт, force нет, существующую ветку
      НИКОГДА не двигает (идемпотентен), локальные ref/worktree не трогает; по построению
      не может опубликовать новый код → без approval-маркера (8 тестов).
      (3) Stacked-доставка перецелена: корневые сабветки PR'ятся в `feature/<slug>`
      (delivery_plan.py: `target=story_branch`, поле `story_branch` в плане), в default
      уходит один финальный PR `feature/<slug>` → main «мержить последним»; брифы
      07-deliver.md + stacked-pr-delivery.md переписаны (создание ветки — на Гейте 5).
- [x] **RED-гейт стал ПО-ТЕСТОВЫМ: 1 red + N green больше не «успех».** Баг прогона:
      судья засчитал RED, когда из новых тестов падал ОДИН, а остальные были зелёные —
      оба детерминированных гейта (`record_gate --expect red` и `check_tests_red.py`)
      мерили «красноту» exit-кодом раннера (один упавший тест валит весь прогон), а
      check_tests_red вдобавок грепал stdout (пустой вывод = «RED»). Зелёный новый тест —
      вакуумный: проходит БЕЗ реализации и ничего не проверяет. Фикс:
      (1) общий `pipeline-state/scripts/junit_report.py` — детерминированный разбор
      JUnit XML текущего прогона (Gradle test-results / Maven surefire+failsafe,
      фильтр по mtime — залежавшиеся отчёты прошлых прогонов не засчитываются);
      (2) оба гейта теперь требуют: отчёты есть (fail-closed — exit-код без JUnit-отчётов
      не доказательство; для не-JUnit стека — override gate-result), выполнился ≥1 тест,
      зелёных НОЛЬ; в reason — список зелёных тестов поимённо; эвристика `_has_red_tests`
      по stdout удалена;
      (3) брифы (forgelite §5 + subagent-prompts.md, 04-tdd.md) требуют скоупить
      RED-прогон на новые тест-классы (Gradle `--tests` / Maven `-Dtest` /
      `--test-filter` у check_tests_red) — иначе зелёные СТАРЫЕ тесты провалят гейт.
      Пины: test_gate_result (1 red + 2 green → FAIL; stale-отчёты не считаются; без
      отчётов → FAIL), test_check_tests_red (то же end-to-end).
- [x] **B (найден деплой-смоуком в чистый проект): гейт арминга §0.1 был недостижим —
      `_incomplete` никто не очищал.** `config.py set` писал значения, не трогая маркер;
      `init --update` переносил detected-маркер безусловно (jira/bitbucket попадали ВСЕГДА)
      и клобберил человеческие ответы None-детектом (`update()` поверх заполненных полей).
      «Как только `_incomplete` пуст — прогони preflight» не мог наступить. Фикс: `config.py set`
      снимает отвеченное поле из маркера (false — валидный ответ; пустой маркер удаляется);
      `init --update` не затирает ответы None-детектом и пересобирает маркер по факту
      (`_answered`; `project.is_git` отвечен только когда True). Заодно `project.build_system`
      добавлен в params-registry (частый житель `_incomplete`, санкционированно ответить было
      нельзя), а `deploy.sh` сеет пустой `minor-defect-fix/config.json` (skill-paths/doctor
      требуют файл, который перестал ехать из репо). Смоук: deploy → init → ответы через
      config-helper → set_criticality → **preflight exit 0**. Пины: `test_config.py`
      (set чистит маркер), `test_init_pipeline_config.py` (update не клобберит + пересборка).

**Аудит логических дыр + исправление (2026-07-04):** проверка по исходникам рантайма
(qwen-code, локально `gigacode`=alias) выявила, что бо́льшая часть заявленного enforcement
фактически НЕ исполнялась. Закрыто:
- [x] **BLOCKER-0: весь блокирующий control-plane не срабатывал (matcher ↔ канон-имя).** Рантайм
      матчит хуки как `new RegExp(matcher).test(canonicalToolName)`
      (`hookPlanner.matchesToolName`), где имя КАНОНИЧЕСКОЕ (`run_shell_command`/`write_file`/
      `edit`), а `Bash`/`Write`/`Edit` — лишь входные алиасы (`TOOL_NAME_ALIASES`). Матчеры были
      `^Bash$` и `(Write|Edit|WriteFile|NotebookEdit)` → `/^Bash$/.test("run_shell_command")`=false
      → из execution-plan выпадали ВСЕ deny-first хуки (destructive/pii/sod/inline/gate/tdd/eval/
      evidence/fork-syntax + PostToolUse prompt-guard). Работали только `*`-хуки (лог/cost/state/
      phase-gate). Это же объясняет постмортемы «гейт не поймал прод-код» (прогон #3) — гейты не
      бежали. Пережило «верификацию 2026-07-03», т.к. eval-набор дёргает скрипты хуков напрямую,
      минуя рантайм-матчинг. Фикс: матчеры на канон-имена (`^(run_shell_command|Bash)$`,
      `^(write_file|edit|notebook_edit|…)$`, PostToolUse `^(read_file|web_fetch|run_shell_command|…)$`).
      Пины: `hooks/test_matcher_canonical_names.py`, `preflight._check_matchers_canonical`
      (валит preflight, если матчер не матчит канон-имя — статическая «firing»-страховка).
- [x] **BLOCKER-1: state-файлы свободно писались моделью напрямую.** approval-маркеры, manifest,
      overrides, gates, _origins, pipeline.json — JSON в `ground/` (whitelist pii/gate, R1-auto);
      провенанс `update.py` обходился прямым `Write .../manifest.json` (все completed) или
      `Write ground/approvals/<judge>.json` (само-approval). Заявление «эскейп-хатч override_judge
      закрыт» было неверным: маркер писался тем же Write. Фикс: новый `state-write-guard.py`
      (deny прямой Write/Edit + shell-редирект/`python -c open()` в control-plane-пути; мутация
      только санкц. скриптами), `record_approval.py` (единственный легальный писатель approval с
      провенансом `produced_by:record_approval`), gate-guard засчитывает approval ТОЛЬКО с этим
      провенансом, `auto_max_risk` клампится ≤ R3 (доставка R4/R5 никогда не авто). Пины:
      `test_state-write-guard.py` (22), `test_gate-guard.py` (провенанс+handwritten-block).
      Дозакрыто (2026-07-04): паттерны `judges/` (подделка вердикта судьи с
      `produced_by:"run_judge"` проходила провенанс `update._check_judges`) и `ground/phases/`
      (подделка `gate.json` снимала phase-lock gate-guard) — тот же класс дыры.
- [x] **MAJOR-фиксы блок-хуков** (актуальны после BLOCKER-0): readonly-байпас override
      (`--reason "…--list"` → токенный `shlex`-парсинг вместо substring); `destructive-blocker`
      добрал `git push -f`, `shutil.rmtree('/')`, `base64 -d|sh`, `xargs rm`; поправлен ложный блок
      `--force-with-lease` (позиционный lookahead); `pii-boundary` ловит inline-python запись PII;
      `gate-guard` не пропускает доставку (push/PR/jira) вне пайплайна при битой policy.
- [ ] **Незакрытые зоны аудита** (не пройдены — субагенты упали на session-limit): ядро
      пайплайн-скриптов (обходы `record_gate`/`run_judge`-floor, деривация criticality→risk) и
      деплой/гигиена git (утечки логов прогонов, дубли реестров). Допройти отдельным заходом.

**Ужесточение под слабую модель (2026-07-04, по прогону fable/DeepSeek-Flash на форке).**
Принцип: **enforcement на границе рантайма + fail-closed на отсутствии решения + детерминизм
вместо LLM-судей + меньше степеней свободы**. Хук не заставит модель «спросить» или «написать
хороший BRD», но заставит НЕ идти дальше без нужного артефакта.
- [x] **Thrust 1 — fail-closed решения (universal).** Решение (критичность/путь/спека) =
      обязательный артефакт в `pipeline.json`; интерактивный вопрос его заполняет, headless —
      предзапись `config.py set`. Нет артефакта → `gate-guard._required_decisions_missing` блокирует
      продуктивную запись фазы (`required_decisions` в risk-policy.json). Пустой ответ вопроса для
      **обязательной** фазы → `update._check_required_skip` даёт `exit 3` (STOP, не тихий skip) —
      закрыт баг «не отрендерился диалог → пайплайн пропустил фазу». Ключи `pipeline.mode`,
      `sources.spec` в params-registry; router пишет `pipeline.mode`.
- [x] **Thrust 2 — lite + tech-design по существующей спеке.** «Простая Jira + готовая спека» не
      покрывалась ни lite (без дизайна), ни full (BRD с нуля). forgelite: `lite-plan`→`lite-design`
      (скилл `tech-design` по `sources.spec` — source of truth, субагент; BRD/SDD заново не пишутся).
      Проведён через sod (design), inline-phase-guard, required/subagent-префиксы, fail-closed
      `sources.spec`.
- [x] **Thrust 3 — судьи → детерминизм.** LLM-судья BRD штамповал мусор. `check_brd_doc.py`
      (детерминированный: бизнес-секции, содержательность, ОТСУТСТВИЕ кода/классов/SQL) — хард-гейт
      закрытия `00-brd`; brd-judge понижен до advisory. Принцип: LLM-вердикт сам шаг не закрывает.
      Дожато (2026-07-04): слой `check_brd_doc` вшит в `run_judge.check_brd`, а ингест
      `--from-output` для brd AND-ит LLM-вердикт с детерминированным полом (`INGEST_FLOOR_PHASES`) —
      раньше гейт был только guidance'ом в брифе, и ингест LLM-«PASS» закрывал `00-brd`,
      ни разу не выполнив структурную проверку.
- [x] **Thrust 4 — гигиена логов + inline-покрытие.** `GIGACODE_RUN_ID` (env) → стабильный
      `run-<id>` независимо от `session_id` («один прогон = одна папка» и в headless; UUID субагента
      в имени файла — идентификатор, не мусор). `checkstyle/ktlint/detekt/spotless` добавлены в
      `BUILD_CMD_RE` (inline-phase-guard + sod) — «checkstyle inline» ловится и standalone.
- [x] **Thrust 5 — статические судьи везде, где шаг закрывался «со слов» (2026-07-04).**
      (1) lite: `lite-jira` и `lite-design` добавлены в `GATE_RESULT_PREFIXES` — скоуп-чек
      (`check_scope`) и дизайн-гейт (`check_taskplan` + `check_sdd --sdd sources.spec`) идут
      ЧЕРЕЗ `record_gate`, шаг не закрывается без evidence (судей у lite-* нет по дизайну,
      evidence — единственный пол; снятие — только override_judge R4). (2) `INGEST_FLOOR_PHASES`
      расширен: brd/eval (standalone, AND) + build/delivery (гибрид: пол stubs/секреты читает
      сохранённый вердикт) — ингест LLM-«PASS» больше нигде не закрывает шаг сам. (3) Тавтология-
      floor (`check_tautological_tests`) вшит в coverage-judge (дефолт ВКЛ, `quality.tautology_check`
      теперь default true) — пустые/тавтологичные @Test не «покрывают». (4) Пол сообщения
      HEAD-коммита в `evidence-enforcer` на push: запрет `Co-Authored-By`, для forgelite —
      обязательный ключ Jira. Пины: `test_gate_result.py` (lite-гейты), `test_run_judge_ingest.py`
      (полы ингеста), `test_tautology_floor.py`, `test_evidence-enforcer.py` (commit-msg).
> **ВАЖНО (эксплуатация):** всё это работает, только если хуки РЕАЛЬНО срабатывают — т.е. после
> `deploy.sh` (матчеры→канон, BLOCKER-0) и запуска с `--experimental-hooks`. На форке проверь
> firing-smoke: рисковое действие даёт `DENY`. Без этого новые гейты — тоже труп.

## Известные ограничения (из аудита)

- **`additionalContext` только в `hookSpecificOutput`** — рантайм читает контекст-инъекцию ТОЛЬКО из
  `hookSpecificOutput.additionalContext` (core/hooks/types). Все наши хуки исправлены под это.
- **Subagent `agent_type` = `general-purpose`** для всех наших субагентов (мы так дёргаем `agent`).
  Поэтому `context-injector` НЕ зависит от типа (инъектит по наличию файлов). SoD через `agent_caps`
  (по `agent_type`) — лишь **best-effort** (работает, только если рантайм передал `agent_type`).
  **Основной SoD форсит `sod-enforcer`**, определяя роль по id АКТИВНОГО шага манифеста (детерминированно,
  не зависит от `agent_type`). Гарантию «фаза закрыта субагентом» держит `update._check_subagent_origin`
  на закрытии шага (PreToolUse-блок не годится — срабатывает и внутри субагента).
- **Гейт-хуки fail-OPEN при таймауте/краше** (`hookEventHandler`: блок при сбое только для Todo-событий;
  команд-хук >60с убивается → действие проходит). Поэтому тяжёлые гейты (`check_taskplan`/`check_delivery`/
  coverage) запускает ОРКЕСТРАТОР как execution-gate (так и есть в SKILL), а хуки лёгкие (file-reads) —
  страховка. Не клади тяжёлые subprocess в hook hot-path.
- **Command substitution `$(...)`/backticks РЕЖЕТСЯ** в shell-вызовах агента → в SKILL.md/доках/инструкциях
  её НЕТ БЫЛО (каталог `.`, путь к репо скрипты берут сами через `repo_root()`). Внутри `.sh` — можно.
- **Вход через `router` не форсится** (у рантайма нет события «скилл выбран») — модель может
  зайти напрямую в оркестратор. Последствия смягчены: gate-guard форсит критичность, eval-guard
  без eval-plan fail-open, `check_scope.py` ловит неверный выбор lite на первом шаге. Инвариант
  «один активный пайплайн за прогон» — тоже проза; нарушение проявится как блок `phase-gate`
  по висящему `in_progress` у брошенного манифеста.
- **`find … -exec` / «filesystem enumeration» РЕЖЕТ нативный сейфти форка** (не хук forge — формат
  `Tool run_shell_command is denied`, не `exit 2`+stderr; в `hooks/` такого правила нет, проверено
  grep'ом). На прогоне #3 заблокировался даже `find` ПО пути ВНУТРИ workspace. Поэтому в SKILL.md/доках
  для перечисления/чтения файлов — `Glob`/`Grep`/`Read`, а не `find -exec`/`ls -R`. Это ограничение
  рантайма, мы его не контролируем — обходим выбором тулов, а `fork-syntax-guard.py` перехватывает
  паттерн раньше нативного deny и объясняет модели замену.
- **Блокировки (exit 2 + stderr) работают надёжно — ТОЛЬКО если хук попал в execution-plan.**
  Попадание решает matcher против КАНОН-имени инструмента (`run_shell_command`/`write_file`/`edit`),
  не Claude-нотации. Матчер `^Bash$`/`Write|Edit` = хук не вызывается вовсе (была дыра BLOCKER-0,
  2026-07-04). При апгрейде рантайма/правке `settings.hooks.json` сверять матчеры с канон-именами
  (`test_matcher_canonical_names.py` / `preflight._check_matchers_canonical`). Subagent-события
  срабатывают для тула `agent`.
- **Токен-бюджет НЕ тормоз by design** (`budget-meter`, бывший `cost-breaker`): circuit-breaker
  удалён, хук только считает расход. Прогон #3: `budget.json` показал 42% (`spent 843K/2M`), тогда как
  реальная сессия сожгла 51.8M входных токенов (расхождение ~60×). Не полагаться на бюджет как на
  ограничитель стоимости — это справочная метрика.
- **Payload-схема хуков подтверждена по стоковому qwen-code 0.19.3** (верификация 2026-07-03):
  вход хука строится `createBaseInput` → `stdin.write(JSON.stringify(input))` со snake_case-ключами
  (`hook_event_name`/`session_id`/`cwd`/`tool_name`) — ровно то, что читают наши парсеры. В стоке
  хуки включены по умолчанию (гейт `!getDisableAllHooks()`); `--experimental-hooks` — специфика
  форка GigaCode на более старой базе. При апгрейде форка перепроверять схему по свежим записям
  `ai-logs` (сплошные `event=null` = схема/поставка payload сломана, enforcement под вопросом).
- **User-level settings могут подключать УСТАРЕВШУЮ копию харнеса** (найдено на машине оператора:
  `~/.qwen/settings.json` вёл на `$HOME/.qwen/hooks/` со старым ростером без fork-syntax-guard/
  sod/inline/eval-guard). Канон — проектный деплой `deploy.sh`; user-level блок hooks либо убрать,
  либо держать синхронным, иначе локальные смоуки бегут на другом (старом) control-plane.

## Диагностика (перед прогоном)

```bash
python3 <project>/.gigacode/hooks/preflight.py --project <project>   # харнес активен?

# какие фичи в работе:
python3 <project>/.gigacode/skills/pipeline-state/scripts/read.py --skill feature-pipeline --list
```

## Состояние пайплайна (state)

- `preflight.py` — проверка settings (линтер на живом `settings.json`)
- `feature-pipeline/scripts/doctor.py` — статика (хуки + скиллы + evals)

## Наблюдаемость

```bash
# живой лог прогона (отдельный терминал):
bash <project>/.gigacode/hooks/watch-agents.sh

# сводка по метрикам:
python3 <project>/.gigacode/hooks/agentops.py --root <project>
```

## Поддерживаемая структура

Репо Forge (source-of-truth):
```bash
forge/
├── FORGE.md                  # этот файл
├── SKILLS-REGISTRY.md        # реестр скиллов с owner/validity/evals
├── deploy.sh                 # установщик (разворачивает в проект)
├── deploy-local.sh           # in-project фиксер путей (копируется в .gigacode/)
├── docs/                     # документация (deployment.md, user-guide.md, troubleshooting.md, …)
├── hooks/                    # control-plane (~38 скриптов)
│   ├── settings.hooks.json   # эталон блока hooks (${PROJECT_ROOT}, ${PYTHON})
│   ├── resolve_hook_paths.py # подстановка путей + merge в settings.json
│   ├── risk-policy.json      # политика рисков
│   ├── preflight.py          # self-check готовности
│   ├── evals/                # eval-набор
│   └── test_*.py             # тесты хуков
└── skills/                   # пайплайн-скиллы (16 шт)
    ├── feature-pipeline/     # оркестратор (SKILL.md — диспетчер; фазы — references/phases/*.md)
    ├── pipeline-state/       # состояние
    ├── system-analyst/       # grounding
    ├── tech-design/          # проектирование
    ├── java-spring-dev/      # генерация кода
    └── ...
```

После `deploy.sh <project>` в целевом проекте:
```bash
<project>/.gigacode/
├── hooks/                    # копия control-plane
├── skills/                   # копия скиллов (co-located)
├── deploy-local.sh           # фиксер путей на месте
├── settings.json             # конфиг рантайма с блоком hooks (+ .bak при обновлении)
├── FORGE.md, SKILLS-REGISTRY.md   # доки для справки
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

### Runtime feature gates (`feature-gates.json`)
- **Что это:** `ground/feature-gates.json` — bool-фиче-флаги рантайма (tdd_enforced, eval_driven_dev,
  security_review, …), управляются скиллом `config-helper` (модель не правит файл руками).
- **Кто читает:** `resolve_phases.py` (execution-time, запускает оркестратор) — флаги участвуют в
  `enabled_by`/`skip_if` фаз. Файла нет → дефолты. Хуки при необходимости читают флаги сами из
  `pipeline.json`/`feature-gates.json`.
- **Чего НЕ делаем (урок):** не инжектим gates через хук между процессами. Бывший `gate-resolver.py`
  (SubagentStart → additionalContext) удалён как мёртвая абстракция: каждый хук — отдельный процесс,
  его stdout другим хукам не виден (та же ошибка, что у удалённого FlushGate).

### state-recorder — прямая запись (FlushGate удалён)
- **Было:** `flush_gate.py` (порт `bridge/flushGate.ts`) буферизовал микро-обновления в
  module-global и сбрасывал их при `phase_boundary`.
- **Почему убрали:** каждый SubagentStop — отдельный процесс хука, поэтому in-process
  буфер `_gates` никогда не переживал между вызовами (всегда неактивен) + `batch_update.py`
  не существовал. Абстракция была мёртвой и вводила в заблуждение.
- **Сейчас:** `state-recorder.py` пишет каждый шаг напрямую через `update.py` в namespace
  активной фичи (`--feature`), ошибки `update.py` логируются в stderr.


## Установка/дистрибуция

- `deploy.sh <project>` — установщик из склонированного репо: копирует hooks+skills(+доки) в
  `<project>/.gigacode/`, кладёт `deploy-local.sh`, доводит `settings.json` (merge + бэкап),
  прогоняет `preflight.py`. Канал-агностично (работает из git clone / архива). Целевая папка
  обязательна — без аргумента ничего не копируется.
- `deploy-local.sh` — повторная доводка путей в `settings.json` на месте (после переезда проекта),
  без копирования. Полное руководство — [docs/deployment.md](docs/deployment.md).

## Обслуживание

- **`hooks/preflight.py`** — основная проверка готовности (settings + pipeline.json + пути хуков).
  Сам зовёт `resolve_hook_paths.py --check` и `feature-pipeline/scripts/doctor.py` (advisory).
- **`hooks/resolve_hook_paths.py`** — merge блока hooks в `settings.json` с подстановкой путей;
  `--check` валидирует, `--dry-run` показывает результат без записи.
- При проблемах: `bash <project>/.gigacode/deploy-local.sh` (починка путей) или повторный
  `deploy.sh` (полное обновление из исходника).

## Связанное

- `docs/deployment.md` — полное руководство по деплою (установщик, бэкапы, переезд проекта)
- `docs/user-guide.md` — руководство пользователя (установка, запуск, фазы)
- `docs/troubleshooting.md` — разбор типовых проблем
- `hooks/DEPLOY.md` — полный ростер хуков (какие события, порядок, диагностика)
- `SKILLS-REGISTRY.md` — реестр скиллов с owner/validity/evals
- `hooks/risk-policy.json` — policy-as-code (R0–R5)

> История — это git-история этого репозитория. Хочешь полноценный аудит изменений —
> `git init` здесь и коммить по фичам; тогда «журнал решений» дополняется коммит-сообщениями.