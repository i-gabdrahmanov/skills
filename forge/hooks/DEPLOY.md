# Control plane на хуках (PDLC v3.5) — ростер и подключение

Forge v3.5 переносит enforcement пайплайна из текста SKILL.md в **рантайм**: risk ladder
R0–R5, evidence bundle, security-хуки, информационный учёт токен-бюджета. Главный
принцип концепции: **hooks = enforcement, FORGE.md/SKILL.md = только guidance** (модель
текст может проигнорировать). Это конфиг рантайма, а не скиллы: скрипты path-агностичны,
состояние берут из `<project>/ground/...`, гейты ищут по `../skills/...`, политику — из
`risk-policy.json` рядом.

> **Модель установки — единственная (project).** Канон: `bash deploy.sh <project>` →
> `<project>/.gigacode/` (co-located `hooks/` + `skills/` + `commands/` + `settings.json`).
> Прежняя extension-раскладка снята 2026-08-22: манифестов extension'а в репо нет,
> `hooks/hooks.json` с `${CLAUDE_PLUGIN_ROOT}` не поддерживается. Если forge где-то остался
> на user-уровне (`~/.gigacode/skills/`, `~/.gigacode/hooks/`, блок hooks в `settings.json`)
> — он ПЕРЕКРЫВАЕТ project-деплой и ломает проводку. Снимается одной командой
> `cleanup-legacy.sh` (см. INSTALL.md §1).

Этот файл — **единственный источник правды по составу хуков**. README.md, INSTALL.md и
FORGE.md ссылаются на него односторонне. Дрейф «доки ↔ проводка» пинится
`hooks/test_docs_hooks_consistency.py` (fail на любом расхождении).

## Состав хуков (15 шт.)

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit/Read | permission gateway, risk ladder R0–R5, **deny-first**; форсит выбор критичности, `required_decisions` (нет решения фазы → нет записи) и `phase_approvals` (нет approval-маркера плана → нет записи фазы) | exit 2 |
| `tdd-guard.py` | PreToolUse Write/Edit | форсит TDD (блок `src/main` пока RED pending) + тест-стратегию (блок `@DataJpaTest`/`@SpringBootTest` при `test_layer=service-unit` — только для файлов в `src/test/`) | exit 2 |
| `eval-guard.py` | PreToolUse Write/Edit | блок записи в `src/main`, пока pre-write eval задачи (`compile`) не пройден (Eval-Driven). `coverage`/`test_pass` выполнимы только ПОСЛЕ кода — их держат гейты закрытия шага | exit 2 |
| `destructive-blocker.py` | PreToolUse `run_shell_command` | чёрный список (`rm -rf /`, force-push `-f`/`--force`, DROP, base64→sh, xargs rm, rmtree корня) | exit 2 |
| `fork-syntax-guard.py` | PreToolUse `run_shell_command` | инструктивный блок синтаксиса, который режет нативный сейфти форка (`$(...)`, backticks, `find -exec`, `ls -R`) — объясняет замену (Glob/Grep/Read) вместо молчаливого deny | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/секретов вне scope (вкл. inline-python `open()`/`write_text`) | exit 2 |
| `state-write-guard.py` | PreToolUse Write/Edit/Bash | запрет прямой записи моделью в control-plane state (`manifest.json`, `policy.json`, `approvals.jsonl`, `events.jsonl`, `evals.json`, `feature-gates.json`, `task-plan.json`, `ground/inventory/**`, `ground/archive/**`) — только через санкц. скрипты; сравнение регистронезависимое; отдельно ловится УДАЛЕНИЕ живого control-plane (`rm`/`mv`/`touch`/`shred`) — без манифеста все хуки становятся noop; + запрет писать артефакты фазы в каталог харнеса (skills/hooks/commands), пока идёт прогон | exit 2 |
| `sod-enforcer.py` | PreToolUse Write/Edit/Bash | separation of duties: роль из активного шага (test не пишет src/main; design/spec/jira не билдят). git commit/push не гейтится — доставка на пользователе | exit 2 |
| `inline-phase-guard.py` | PreToolUse Write/Edit/Bash | actor-guard: ГЛАВНЫЙ агент (пустой `agent_type`) не производит артефакты/код subagent-фазы inline | exit 2 |
| `grounding-evidence.py` | PreToolUse Read | пишет запись `grounding` в журнал прогона при чтении grounding-excerpt — `gate-guard` снимает по нему блок фазы `01-grounding` | нет |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch) | детект prompt-injection → additionalContext | нет |
| `file-journal.py` | PostToolUse Write/Edit/Bash | безусловный журнал изменённых файлов активной фичи (`journal/files.jsonl`) — скоуп восстановления кода для `rollback.py` | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим шагом (`in_progress` либо `pending` с gate-result/origin); одноразово на сессию | block |

Не-хуки рядом: `preflight.py` (проверка «харнес активен?» ПЕРЕД пайплайном — ловит
«0 hook entries»), `risk-policy.json` (policy-as-code, читает `risk_ladder.py`),
`settings.hooks.json` (эталон блока hooks, плейсхолдеры `${PYTHON}` и `${PROJECT_ROOT}`
подставляет `resolve_hook_paths.py`), `resolve_hook_paths.py` (in-project фиксер путей,
его зовёт `deploy-local.sh`), `evals/run-evals.py` (eval-набор), `run-hook-tests.sh`
(юнит-тесты хуков + evals одной командой). Статическая диагностика (`doctor.py`) и
валидация скиллов живут в `skills/feature-pipeline/scripts/` — `preflight.py` зовёт их сам.

## Порядок и sequential

PreToolUse `run_shell_command` идёт **sequential**: destructive-blocker → fork-syntax-guard →
pii-boundary → state-write-guard → sod-enforcer → inline-phase-guard → gate-guard.
Write/Edit (`write_file|edit|notebook_edit`): pii-boundary → state-write-guard →
tdd-guard → eval-guard → sod-enforcer → inline-phase-guard → gate-guard. Любой
блокирующий может остановить (`exit 2`) до действия. Точный блок и порядок — в
`settings.hooks.json`.

PreToolUse на читающих инструментах (`read_file|search_file_content|glob`) — тоже sequential:
grounding-evidence → gate-guard. Там у gate-guard работает ровно одна проверка — фазовая
(блок чтения `src/` до завершения `01-grounding`); ladder к чтению неприменим.

## Матчеры — канон-имена рантайма

`${PYTHON}` и `${PROJECT_ROOT}` в командах — плейсхолдеры; реальные пути подставляет
`resolve_hook_paths.py` (`deploy-local.sh` запускает его при деплое, см. INSTALL.md §2):
`${PYTHON}` → `sys.executable` рабочего интерпретатора, `${PROJECT_ROOT}` → абсолютный
путь проекта. Подстановка кроссплатформенная (важно для Windows/git-bash, где `python3`
не всегда есть в PATH). Matcher-ы цепочек матчат **канон-имена инструментов рантайма**
(`run_shell_command`/`write_file`/`edit`/`notebook_edit`/`read_file`/`web_fetch`), а не
Claude-нотацию (`^Bash$`/`Write|Edit`). Рантайм матчит `new RegExp(matcher).test(
canonicalToolName)` — Claude-имя `Bash` лишь входной алиас, целью матчинга не бывает.
Пинится `hooks/test_matcher_canonical_names.py` (регрессия BLOCKER-0) и
`preflight._check_matchers_canonical`. **Порядок в массивах значим** — блокирующие
на PreToolUse идут sequential.

Write/Edit-цепочка матчит ещё и `mcp__.*`: создание задач в Jira идёт MCP-инструментом, и
без этого фаза `03-jira` — единственная с необратимым внешним эффектом — проходила мимо
ВСЕХ хуков.

Группы `SubagentStart`/`SubagentStop` matcher'а не имеют вовсе (как `UserPromptSubmit` и
`Stop`). Раньше там стояло `"*"` — это **невалидный** regex: `new RegExp("*")` падает с
«nothing to repeat», то есть `context-injector` и `state-recorder` могли не попадать в
план вообще. «На всё» в этом формате выражается отсутствием поля, а не звёздочкой.

## Расположение

| Каталог | Зачем |
|---|---|
| исходный репо `forge/` (родитель `hooks/`) | **source-of-truth**: `hooks/` + `skills/` + `commands/` + манифест; отсюда `deploy.sh` копирует в проект |
| `<project>/.gigacode/` | куда развёрнут харнес: co-located `hooks/` + `skills/` + `commands/` + сгенерированный `settings.json` — боевой каталог рантайма |
| `~/.gigacode/skills/`, `~/.gigacode/hooks/`, `~/.qwen/extensions/forge` | остатки ПРЕЖНЕЙ extension-раскладки, если остались на машине; ПЕРЕКРЫВАЮТ project-деплой и ломают проводку; снимаются `cleanup-legacy.sh` |

> Гейты вызываются по `<hooks>/../skills/...` → рядом с `hooks/` должны лежать `skills/`.
> В project-модели это выполнено по построению: оба каталога в `<project>/.gigacode/`.
> Привязки к домашнему `~/.gigacode` нет — никакой зависимости от user-уровня.

## Подключение — одной командой

```bash
bash deploy.sh /path/to/target-project
```

`deploy.sh` делает: копирует `hooks/` + `skills/` + `commands/` co-located в
`<project>/.gigacode/`, удаляет `__pycache__`/`.DS_Store`/локальный `config.json` и
хуки-сироты (были в старом деплое, но нет в исходнике), кладёт `deploy-local.sh` и доки,
запускает его для генерации `<project>/.gigacode/settings.json` из `settings.hooks.json`
(подставляет `${PROJECT_ROOT}` и `${PYTHON}`), прогоняет `preflight.py` (advisory).
Подробнее — INSTALL.md §2.

Снять — `bash uninstall.sh /path/to/target-project` (зеркало `deploy.sh`).

Снять остатки прежней extension-раскладки (`~/.gigacode/skills/`, `~/.gigacode/hooks/`,
блок hooks в `settings.json`), если они остались после миграции —
`bash cleanup-legacy.sh /path/to/target-project --apply` (по умолчанию — только план,
ничего не меняется). Корп-контур, где писать в `$HOME` или в проект нельзя:
`--backup-dir /tmp/forge-bak`. Снесённое переезжает в `forge-legacy-backup-<TS>/`
рядом с базой (или под `--backup-dir`).

> ⚠️ **Не копируй скиллы и хуки вручную по отдельности.** Провальный прогон pprb-kid
> случился именно так: скиллы залили на проектный уровень, а блок `hooks` в `settings.json`
> НЕ влили → рантайм стартовал с `[HOOK_REGISTRY] 0 hook entries`, весь control-plane
> молчал. `deploy.sh` исключает этот класс ошибок: код и проводка едут одним пакетом.
>
> ⚠️ **И не удаляй legacy-`.gigacode/` руками** — зеркальная поломка: файлов нет, а блок
> `hooks` в `settings.json` остался → рантайм зовёт удалённые скрипты и падает на КАЖДОМ
> вызове инструмента. Для снятия есть `cleanup-legacy.sh` — он чистит и конфиг.

## ⚠️ ЗАПУСК: хуки за флагом `--experimental-hooks` (форк GigaCode)

В форке GigaCode хуки — **экспериментальная опция**, гейтятся CLI-флагом. Без него рантайм
стартует с `[HOOK_REGISTRY] 0 hook entries` — весь control-plane молчит (это и был провал
pprb-kid). **Запускай ВСЕГДА с флагом:**

```bash
gigacode --experimental-hooks          # интерактивно — канон; дальше команда /forge <задача>
```

Headless (`-p`) — отдельный режим: `agent` в нём требует `-y`/YOLO, иначе субагент не стартует и
фаза упирается в `inline-phase-guard`; плюс `ask_user_question` не рендерится, поэтому все решения
и approval-маркеры должны быть предзаписаны ДО прогона (INSTALL.md §4).

Флаг — это флаг **запуска бинаря**, его нельзя прописать в `settings.json`. Установка
через `deploy.sh` его не ставит (не может — это аргумент процесса); `preflight.py` ловит
отсутствие по firing-evidence. (В апстриме Qwen флага нет — хуки on по умолчанию; это
особенность форка.)

## Диагностика ПЕРЕД прогоном (обязательно)

```bash
python3 <project>/.gigacode/hooks/preflight.py --project <project>
```

Проверяет: проводка хуков непустая, все essential-хук-скрипты на месте и **РЕАЛЬНО
перечислены** в `command`-полях `settings.json` (наличие файла ≠ подключение — кейс
eval-guard: файл был, в settings — нет, и preflight давал зелёный свет при выключенном
enforcement), пути не ведут за пределы `<project>/.gigacode/`, **skills co-located**
рядом с hooks, `risk-policy.json` парсится (иначе `risk_ladder` тихо деградирует до
allow-all). Advisory прогоняет `skills/feature-pipeline/scripts/doctor.py` (целостность
пакета, валидность скиллов — frontmatter `name`/`description`, иначе рантайм молча
скипнет). Ловит «0 hook entries», «skills не рядом», неверные матчеры и чужие пути ДО
запуска пайплайна.

Exit-коды:
- `0` — армирован, можно работать.
- `1` — ENFORCEMENT OFF (essential-хук не подключён / settings / risk-policy):
  стоп-и-предупреди; переустанови `deploy.sh`.
- `2` — `ground/policy.json` не инициализирован (нормальный первый запуск): создай
  конфиг и перезапусти preflight.

## Конфиг проекта (`ground/policy.json`)

> **v2.** Конфиг разделён: project-wide живёт в `<project>/ground/policy.json`
> (`$schema: "feature-pipeline/config@2"`), per-feature решения — в `inputs.*`/`decisions.*`
> манифеста фичи. Единый v1-файл `ground/pipeline.json` снят; он ещё читается dual-read
> шимом для старых проектов, но НЕ создаётся. Раздел ниже раньше описывал именно v1 —
> расхождение с кодом найдено аудитом 2026-09-28.

Создаёт `skills/feature-pipeline/scripts/init_pipeline_config.py` (вызывается первым
запуском пайплайна; preflight при `exit 2` подсказывает готовую команду `init_command`
в выводе JSON). Правится только через `config-helper/scripts/config.py set` — прямая
запись режется `state-write-guard`.

**Живые блоки** (их реально читают хуки и скрипты):
- `project.*` — build_system, модули, package_root, default_branch, is_git.
- `conventions.*` — миграции, changelog.
- `quality.*` — пороги и команды сборки/тестов. Делятся на два класса:
  - **факты о проекте** (`build_command`, `test_command`, `coverage_report`,
    `jacoco_configured`, `test_layer`) — настраиваются свободно, это работа `config-helper`;
  - **переключатели enforcement** (`tdd`, `eval_enabled`, `coverage_threshold`,
    `eval_threshold`, `max_judge_iterations`, `max_step_reopens`, `*_check`,
    `module_dep_policy`, `coverage_exclude_globs`, `no_test_layers`) — **R4**: `config.py set`
    по ним требует approval-маркера `policy-downgrade-<параметр>` с цитатой пользователя.
    Список — `risk-policy.json:quality_downgrade.params`.
- `docs.*` — куда пишутся артефакты фаз.
- `jira.*` — включение Jira-фазы и ключ проекта.

**Инертные блоки.** `init_pipeline_config.py` их всё ещё пишет, но **читателей у них нет**
(проверено grep'ом по дереву) — не опирайся на них как на переключатели:
- `security.{destructive_blocker,pii_boundary,prompt_guard}` — соответствующие хуки
  подключаются/отключаются только через `settings.json`, эти флаги не читает никто;
- `risk.deny_first` — deny-first вшит в `gate-guard`/`risk_ladder`, флагом не управляется;
- `evidence.threshold` — `risk_ladder.evidence_ok` берёт порог из аргумента по умолчанию;
- `autonomy.{level,auto_max_risk}` — v1-имена. В v2 потолок авто-прохода живёт в манифесте
  как `decisions.auto_max_risk` (+ `decisions.criticality`), и читает его `gate-guard`.

`risk.policy` — путь к `risk-policy.json`; фактически хуки резолвят её co-located рядом
с собой, так что значение справочное.

## Выключение / тюнинг

- `"disableAllHooks": true` в `<project>/.gigacode/settings.json` — отключить ВСЁ сразу.
- Нестабилен один хук — убери его строку из события в `settings.json` (остальные, включая
  логгер, целы). Чтобы выключить хук во ВСЕХ проектах, закомментируй строку в
  `settings.hooks.json` и перезапусти `deploy.sh`.
- Политику рисков менять в `risk-policy.json` без правки кода: да/нет конкретному
  действию определяется regex-паттернами (`destructive_blacklist`, `pii_patterns` и т.д.).
- Диагностику без запуска пайплайна — `preflight.py` (см. выше).
