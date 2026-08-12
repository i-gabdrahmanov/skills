# Control plane на хуках (PDLC v3.5) — ростер и подключение

Хуки переносят enforcement пайплайна из текста SKILL.md в **рантайм** и реализуют Forge v3.5:
risk ladder R0–R5, evidence bundle, информационный учёт токен-бюджета, security-хуки. Главный принцип концепции:
**hooks = enforcement, FORGE.md/SKILL.md = только guidance** (модель текст может проигнорировать).
Это конфиг рантайма, НЕ скиллы. Скрипты path-агностичны: состояние берут из
`<project>/ground/...`, гейты ищут по `../skills/...`, политику — из `risk-policy.json` рядом.

> **МОДЕЛЬ — EXTENSION (один пакет на машину, ничего не кладётся в проект).** Канон: `hooks/`
> и `skills/` co-located в корне extension'а — по построению, копировать их никуда не нужно.
> Резолверы кода выводят базу из фактического расположения файла
> (`hooks/_project.gigacode_dir()` → `<this-hook>/..`; `skill_paths` — от `project_root`),
> поэтому **никакой зависимости от домашнего `~/.gigacode`**: одна копия кода обслуживает все
> проекты, а состояние прогона остаётся в `<project>/ground/`. Подключение — `qwen extensions
> link|install <путь>` (см. INSTALL.md). Прежняя проектная раскладка `<project>/.gigacode/`
> снята; если она где-то осталась — грузится ПОВЕРХ extension'а и задваивает цепочки хуков.

## Состав хуков

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit | permission gateway, risk ladder R0–R5, **deny-first**; форсит выбор критичности, `required_decisions` (нет решения фазы → нет записи) и `phase_approvals` (нет approval-маркера плана → нет записи фазы) | exit 2 |
| `tdd-guard.py` | PreToolUse Write/Edit | форсит TDD (блок `src/main` пока RED pending) + тест-стратегию (блок `@DataJpaTest`/`@SpringBootTest` при `test_layer=service-unit`) | exit 2 |
| `eval-guard.py` | PreToolUse Write/Edit | блок записи в `src/main`, пока eval'ы задачи (compile/coverage/test_pass) не пройдены (Eval-Driven) | exit 2 |
| `destructive-blocker.py` | PreToolUse `run_shell_command` | чёрный список (`rm -rf /`, force-push `-f`/`--force`, DROP, base64→sh, xargs rm, rmtree корня) | exit 2 |
| `fork-syntax-guard.py` | PreToolUse `run_shell_command` | инструктивный блок синтаксиса, который режет нативный сейфти форка (`$(...)`, backticks, `find -exec`, `ls -R`) — объясняет замену (Glob/Grep/Read) вместо молчаливого deny | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/секретов вне scope (вкл. inline-python `open()`/`write_text`) | exit 2 |
| `state-write-guard.py` | PreToolUse Write/Edit/Bash | запрет прямой записи моделью в control-plane state (`manifest.json`/`_origins`/`gates`/`overrides`/`approvals`/`pipeline.json`) — только через санкц. скрипты; + запрет писать артефакты фазы в каталог харнеса (skills/hooks/commands), пока идёт прогон | exit 2 |
| `sod-enforcer.py` | PreToolUse Write/Edit/Bash | separation of duties: роль из активного шага (test не пишет src/main; design/spec/jira не билдят). git commit/push не гейтится — доставка на пользователе | exit 2 |
| `inline-phase-guard.py` | PreToolUse Write/Edit/Bash | actor-guard: ГЛАВНЫЙ агент (пустой `agent_type`) не производит артефакты/код subagent-фазы inline | exit 2 |
| `grounding-evidence.py` | PreToolUse Read | пишет запись `grounding` в журнал прогона при чтении grounding-index — `gate-guard` снимает по нему блок фазы `01-grounding` | нет |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch) | детект prompt-injection → additionalContext | нет |
| `file-journal.py` | PostToolUse Write/Edit/Bash | безусловный журнал изменённых файлов активной фичи (`journal/files.jsonl`) — скоуп восстановления кода для `rollback.py` | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим `in_progress` | block |

Не-хуки рядом: `preflight.py` (проверка «харнес активен?» ПЕРЕД пайплайном — ловит «0 hook entries»),
`hooks.json` (проводка: события, матчеры, порядок; пути через `${CLAUDE_PLUGIN_ROOT}`),
`evals/run-evals.py` (eval-набор), `run-hook-tests.sh` (юнит-тесты хуков + evals одной командой).
Статическая диагностика (`doctor.py`) и валидация скиллов живут
в `skills/feature-pipeline/scripts/` — `preflight.py` зовёт их сам.

## Порядок и sequential

PreToolUse `run_shell_command` идёт **sequential**: destructive-blocker → fork-syntax-guard → pii-boundary →
state-write-guard → sod-enforcer → inline-phase-guard → gate-guard → log.
Write/Edit (`write_file|edit|notebook_edit`): pii-boundary → state-write-guard → **tdd-guard** → eval-guard →
sod-enforcer → inline-phase-guard → gate-guard → log. Любой блокирующий может остановить (exit 2) до
действия. Логгер — всегда последний и неблокирующий. Точный блок — в `hooks.json`.

> **Матчеры — по КАНОН-именам рантайма** (`run_shell_command`/`write_file`/`edit`/`notebook_edit`), не
> Claude-нотация (`^Bash$`/`Write|Edit`). Рантайм матчит `new RegExp(matcher).test(canonicalToolName)` —
> `^Bash$` не матчит `run_shell_command`, и вся цепочка молча выпадает из плана (это была дыра BLOCKER-0).
> Пинится `hooks/test_matcher_canonical_names.py` + `preflight._check_matchers_canonical`.

## Расположение

| Каталог | Зачем |
|---|---|
| корень extension'а (родитель `hooks/`) | **source-of-truth и поставка**: `hooks/` + `skills/` + `commands/` + манифест |
| `~/.qwen/extensions/forge` (или `~/.gigacode/…`) | куда рантайм ставит/линкует пакет |
| `<project>/.gigacode/` | **legacy**: раскладка прежнего проектного деплоя, если осталась |

> Гейты вызываются по `<hooks>/../skills/...` → рядом с `hooks/` должны лежать `skills/`. В
> extension'е это выполнено по построению. Привязки к домашнему `~/.gigacode` нет.

## Подключение — ОДНОЙ КОМАНДОЙ (канонический путь)

Полное руководство с примерами — [`../INSTALL.md`](../INSTALL.md).

```bash
qwen extensions link /path/to/forgeExt      # симлинк: правки видны после рестарта сессии
qwen extensions install /path/to/forgeExt   # либо установка копией
```
Рантайм сам читает `hooks/hooks.json` из пакета и резолвит `${CLAUDE_PLUGIN_ROOT}` в его корень —
подстановки путей, merge в `settings.json` проекта и бэкапов нет. Ставится один раз на машину.

Снять — `qwen extensions uninstall forge` (для `link` — удалить симлинк). `ground/` в проектах,
`permissions`/`mcpServers` и чужие хуки оператора к extension'у отношения не имеют.

Снять раскладку прежнего проектного деплоя, если она осталась (`--dry-run` — план,
`--backup-dir` — копия перед удалением):
```bash
bash cleanup-legacy.sh /path/to/target-project
```

> ⚠️ **Не копируй скиллы и хуки вручную по отдельности.** Провальный прогон pprb-kid случился именно
> так: скиллы залили на проектный уровень, а блок `hooks` в `settings.json` НЕ влили → рантайм стартовал
> с `[HOOK_REGISTRY] 0 hook entries`, весь control-plane молчал. Extension исключает этот класс
> ошибок: код и проводка едут одним пакетом.
>
> ⚠️ **И не удаляй legacy-`.gigacode/` руками** — зеркальная поломка: файлов нет, а блок `hooks` в
> `settings.json` проекта остался → рантайм зовёт удалённые скрипты и падает на КАЖДОМ вызове
> инструмента. Для снятия есть `cleanup-legacy.sh` — он чистит и конфиг.

## ⚠️ ЗАПУСК: хуки за флагом `--experimental-hooks` (форк GigaCode)

В форке GigaCode хуки — **экспериментальная опция**, гейтятся CLI-флагом. Без него рантайм стартует с
`[HOOK_REGISTRY] 0 hook entries` — весь control-plane молчит (это и был провал pprb-kid). **Запускай ВСЕГДА с флагом:**
```bash
gigacode --experimental-hooks -p "<задача>"
# или интерактивно:
gigacode --experimental-hooks
```
Флаг — это флаг **запуска бинаря**, его нельзя прописать в settings.json. Установка extension'а
его не ставит (не может — это аргумент процесса); `preflight.py` ловит отсутствие по firing-evidence.
(В апстриме Qwen флага нет — хуки on по умолчанию; это особенность форка.)

## Диагностика ПЕРЕД прогоном (обязательно)

```bash
python3 <forge>/hooks/preflight.py --project <project>   # <forge> = корень extension'а
```
Проверяет: проводка хуков непустая, все хук-скрипты на месте, пути в ней не ведут за пределы
пакета, **skills co-located** рядом с hooks; advisory
прогоняет `skills/feature-pipeline/scripts/doctor.py` (целостность пайплайна, валидность
скиллов — frontmatter name/description, иначе рантайм молча скипнет). Ловит «0 hook entries»,
«skills не рядом» и чужие пути ДО запуска. `exit 1` → Forge не готов: переустанови extension,
а если preflight ругается на задвоение цепочек — сними legacy-раскладку `cleanup-legacy.sh`.

## Конфиг проекта (`ground/pipeline.json`)

Новые блоки v3.5 (создаёт `skills/feature-pipeline/scripts/init_pipeline_config.py`): `evidence.threshold`,
`risk.{policy,deny_first}`, `security.{destructive_blocker,pii_boundary,prompt_guard}`,
`autonomy.{level,auto_max_risk}`.

## Выключение / тюнинг

`"disableAllHooks": true` — отключить всё. Нестабилен один хук — убери его строку из события
(остальные, включая логгер, целы). Политику рисков менять в `risk-policy.json` без правки кода.
