# Control plane на хуках (PDLC v3.5) — ростер и подключение

Forge v3.5 переносит enforcement пайплайна из текста SKILL.md в **рантайм**: risk ladder
R0–R5, evidence bundle, security-хуки, информационный учёт токен-бюджета. Главный
принцип концепции: **hooks = enforcement, FORGE.md/SKILL.md = только guidance** (модель
текст может проигнорировать). Это конфиг рантайма, а не скиллы: скрипты path-агностичны,
состояние берут из `<project>/ground/...`, гейты ищут по `../skills/...`, политику — из
`risk-policy.json` рядом.

> **Модель установки — extension внутри проекта.** Канон: `bash install.sh forge|forge-ui <project>`
> → `<project>/.gigacode/extensions/<kind>/` (манифест, `hooks/` + `hooks/hooks.json`, `skills/`,
> `commands/`) + ссылка в `~/.gigacode/extensions` (`gigacode extensions link`), включение — только
> для этого workspace. Старую co-located раскладку (`.gigacode/{hooks,skills,commands}` + блок hooks в
> `settings.json`) `install.sh` снимает сам; остатки на user-уровне (`~/.gigacode/skills|hooks`) —
> `cleanup-legacy.sh`. Подробно — INSTALL.md.

Этот файл — **единственный источник правды по составу хуков**. README.md, INSTALL.md и
FORGE.md ссылаются на него односторонне. Дрейф «доки ↔ проводка» пинится
`hooks/test_docs_hooks_consistency.py` (fail на любом расхождении).

## Состав хуков (17 шт.)

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit/Read | permission gateway, risk ladder R0–R5, **deny-first**; форсит выбор критичности, `required_decisions` (нет решения фазы → нет записи) и `phase_approvals` (нет approval-маркера плана → нет записи фазы) | exit 2 |
| `tdd-guard.py` | PreToolUse Write/Edit + Bash | форсит TDD (блок `src/main` пока RED pending) + тест-стратегию (блок `@DataJpaTest`/`@SpringBootTest` при `test_layer=service-unit` — только для файлов в `src/test/`) | exit 2 |
| `tech-guard.py` | PreToolUse Write/Edit + Bash | tech-ветка (`forgetech`): запись в файл проекта (вне `docs/`, `ground/`, выходных каталогов сборки) только на шаге `tech-apply`, при согласии `tech-plan-<F>`, привязанном к sha256 текущего `change-plan.json`, после снимка «до» (`check_invariants.py snapshot`) и только в `targets` плана. Вне прогона forgetech — noop | exit 2 |
| `ui-guard.py` | PreToolUse Write/Edit + Bash + Read/Grep | ui-ветка (`forgeui`, forge-ui во фронт-репо): запреты daiver слоя F кодом. (1) `read_file`/`grep_search`/`read_many_files` и shell-читатели не пускаются к канону и сырым экспортам макета (`*.canonical.json`, `visual-requirements.pixso*.json`, `visual-requirements.json\|html` и любой файл с головой Pixso DSL `{"dsl":{"converterVersion"`); в shell разрешены только перечисление и измерение (ls, stat, wc, file, shasum); отказ называет `daiver_outline`/`daiver_section`/`daiver_source_nodes`. (2) Запись в `project.outputDir` (из `.daiver/config.yaml`) — только на шаге `ui-build`; на `ui-verify` правка = переоткрытие `ui-build` (подсказка как у sod-enforcer). (3) Запись на `ui-build` — только при действительной подписи плана: `plan_contract.signature_state` (согласие `ui-plan-<KEY>`, привязанное к lock'у, собранному из текущих артефактов); вердикт кешируется в `ground/statements/forgeui/<KEY>/ui-guard-cache.json` по снимку stat'ов входов. (4) Инлайн-`<svg` (и data-URI svg) в кодовых файлах outputDir — отказ, кроме помеченного плейсхолдера арта. Вне прогона forgeui — noop; внутри — fail-closed | exit 2 |
| `eval-guard.py` | PreToolUse Write/Edit + Bash | блок записи в `src/main`, пока pre-write eval задачи (`compile`) не пройден (Eval-Driven). `coverage`/`test_pass` выполнимы только ПОСЛЕ кода — их держат гейты закрытия шага | exit 2 |
| `destructive-blocker.py` | PreToolUse `run_shell_command` | чёрный список (`rm -rf /`, force-push `-f`/`--force`, DROP, base64→sh, xargs rm, rmtree корня) | exit 2 |
| `fork-syntax-guard.py` | PreToolUse `run_shell_command` | инструктивный блок синтаксиса, который режет нативный сейфти форка (`$(...)`, backticks, `find -exec`, `ls -R`) — объясняет замену (Glob/Grep/Read) вместо молчаливого deny | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/секретов вне scope (вкл. inline-python `open()`/`write_text`). Исключение для мок-данных макета (forge-ui): запись только в `<project.outputDir>/**/mocks.ts` и `__mocks__/`, активный прогон `forgeui`, `.daiver/config.yaml` на месте и проходит проверку, и КАЖДАЯ PII-подобная строка дословно есть в текстах канона трека (`daiver.texts.mockup_texts`); нет движка, трека, конфига или канона — хук как раньше, сбой сверки — отказ, не пропуск | exit 2 |
| `state-write-guard.py` | PreToolUse Write/Edit/Bash | запрет прямой записи моделью в control-plane state (`manifest.json`, `policy.json`, `approvals.jsonl`, `events.jsonl`, `evals.json`, `feature-gates.json`, `task-plan.json`, `ui-guard-cache.json`, `ground/inventory/**`, `ground/archive/**`, а во фронт-репо — `.daiver/**` состояние движка forge-ui: любой `*.yaml` (конфиг, карта компонентов, `track.yaml`), `design-system-requirements*.json`, а в `tracks/<KEY>/…` `plan.json`/`plan.lock.json`/`resolutions.json`/`build-state.json`/`verify-state.json`/`run.json`, `plan.md`/`ui-plan.md`/`resolution-report.md`, канон и `visual-requirements.*`, `verify/`, `versions/`, `baseline/`; `inbox/` (выгрузки макета) и `assets/` не охраняются) — только через санкц. скрипты; сравнение регистронезависимое; отдельно ловится УДАЛЕНИЕ живого control-plane (`rm`/`mv`/`touch`/`shred`) — без манифеста все хуки становятся noop; + запрет писать артефакты фазы в каталог харнеса (skills/hooks/commands), пока идёт прогон | exit 2 |
| `sod-enforcer.py` | PreToolUse Write/Edit/Bash | separation of duties: роль из активного шага (test не пишет src/main; design/spec/jira не билдят: gradle, mvn, линтеры, `npm`/`pnpm`/`yarn`/`npx` с `run\|build\|test\|exec`, `tsc`, `vite`, `playwright` — как команда в начале сегмента; роли dev и test их запускают). git commit/push не гейтится — доставка на пользователе | exit 2 |
| `inline-phase-guard.py` | PreToolUse Write/Edit/Bash + `mcp__.*` | actor-guard: ГЛАВНЫЙ агент (пустой `agent_type`) не производит артефакты/код subagent-фазы inline и не вызывает продуктивные MCP-инструменты шага (поле реестра веток `phase_tools`: `daiver_pixso_pull`/`daiver_normalize`/`daiver_resolve_*`/`daiver_plan_*` на ui-design, `daiver_build_state*`/`daiver_svg_assets` на ui-build, `daiver_screenshot`/`daiver_verify_*` на ui-verify) | exit 2 |
| `grounding-evidence.py` | PreToolUse Read | пишет запись `grounding` в журнал прогона при чтении grounding-excerpt — `gate-guard` снимает по нему блок фазы `01-grounding` | нет |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch/shell) | детект prompt-injection → additionalContext | нет |
| `file-journal.py` | PostToolUse Write/Edit/Bash | безусловный журнал изменённых файлов активной фичи (`journal/files.jsonl`) — скоуп восстановления кода для `rollback.py` | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим шагом (`in_progress` либо `pending` с gate-result/origin); одноразово на сессию | block |

Не-хуки рядом: `preflight.py` (проверка «харнес активен?» ПЕРЕД пайплайном — ловит
«0 hook entries»), `risk-policy.json` (policy-as-code, читает `risk_ladder.py`),
`settings.hooks.json` (эталон блока hooks: из него `forge_extension.py` собирает
`hooks/hooks.json` extension'а), `settings.mcp.json` (эталон MCP-серверов → манифест),
`forge_extension.py` (сборка манифеста и `hooks.json`, ссылка в рантайме, проверка — его зовут
`install.sh` и `deploy-local.sh`), `_run_hook.py` (запуск хука только в своём проекте),
`resolve_hook_paths.py` (снятие форжевых хуков и MCP из `settings.json` старой раскладки — его
зовёт `uninstall.sh`), `_shell_targets.py` (общий разбор целей записи/удаления в
shell-команде для state-write-guard, tdd-guard, tech-guard, ui-guard, eval-guard и sod-enforcer; stdlib-only),
`_branches.py` (реестр плоских веток fix/tech/ui: шаги, роли, артефакты — единый источник для
хуков и скриптов),
`evals/run-evals.py` (eval-набор), `run-hook-tests.sh`
(юнит-тесты хуков + evals одной командой). Статическая диагностика (`doctor.py`) и
валидация скиллов живут в `skills/feature-pipeline/scripts/` — `preflight.py` зовёт их сам.

## Порядок и sequential

PreToolUse `run_shell_command` идёт **sequential**: destructive-blocker → fork-syntax-guard →
pii-boundary → state-write-guard → tdd-guard → tech-guard → ui-guard → eval-guard → sod-enforcer →
inline-phase-guard → gate-guard. tdd-guard/tech-guard/ui-guard/eval-guard/sod-enforcer судят в ней цели записи shell-команды (общий
разбор `_shell_targets.py`) так же, как Write: `cat > src/main/... <<EOF` — та же запись.
Write/Edit (`write_file|edit|notebook_edit`): pii-boundary → state-write-guard →
tdd-guard → tech-guard → ui-guard → eval-guard → sod-enforcer → inline-phase-guard → gate-guard. Любой
блокирующий может остановить (`exit 2`) до действия. Точный блок и порядок — в
`settings.hooks.json`.

PreToolUse на читающих инструментах (`read_file|grep_search|search_file_content|glob`) — тоже sequential:
ui-guard → grounding-evidence → gate-guard (ui-guard отказывает на канон и сырые экспорты макета во фронтовом
прогоне; вне forgeui — noop). `read_many_files` — отдельная группа, в ней один ui-guard. Там у gate-guard работает ровно одна проверка — фазовая
(блок чтения `src/` до завершения `01-grounding`); ladder к чтению неприменим.

## Матчеры — канон-имена рантайма

`${PYTHON}` и `${PROJECT_ROOT}` в командах эталона — плейсхолдеры; при установке
`forge_extension.py build` пишет `hooks/hooks.json` с командами
`<python> <ext>/hooks/_run_hook.py <ext>/hooks/<хук>.py`: `<python>` — `sys.executable` здорового
интерпретатора, пути абсолютные, слэши прямые (важно для Windows/git-bash: рантайм режет команду
POSIX-правилами, backslash съедается). `_run_hook.py` исполняет хук в том же процессе, но только
если рабочий каталог сессии внутри проекта extension'а; иначе — молча `exit 0`. Matcher-ы цепочек матчат **канон-имена инструментов рантайма**
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
| исходный репо `forge/` (родитель `hooks/`) | **source-of-truth**: `hooks/` + `skills/` + `commands/` + профили; отсюда `install.sh` собирает поставку |
| `<project>/.gigacode/extensions/<forge\|forge-ui>/` | установленный extension: манифест, `hooks/` + `hooks/hooks.json`, `skills/`, `commands/` — каталог целиком форжевый |
| `~/.gigacode/extensions/<forge-…-хеш>/` | только ссылка рантайма на каталог выше (`{type: link, source}`) |
| `<project>/.gigacode/{hooks,skills,commands}` + `settings.json`, `~/.gigacode/skills\|hooks` | старые раскладки; ПЕРЕКРЫВАЮТ extension (project > extension) и задваивают хуки; в проекте снимает `install.sh`, на user-уровне — `cleanup-legacy.sh` |

> Гейты вызываются по `<hooks>/../skills/...` → рядом с `hooks/` должны лежать `skills/`.
> В extension'е это выполнено по построению: оба каталога в `<project>/.gigacode/extensions/<kind>/`.

## Подключение — одной командой

```bash
bash install.sh forge    /path/to/back-repo
bash install.sh forge-ui /path/to/front-repo
```

`install.sh` снимает старую раскладку (если была), собирает поставку по профилю и подменяет ею
каталог extension'а целиком, пишет манифест и `hooks/hooks.json` (`forge_extension.py build`),
ставит ссылку и включает extension только для этого workspace (`forge_extension.py link`; с другими
forge-проектами машины — взаимно выключен), кладёт блок в `.gitignore`, прогоняет `preflight.py`
(advisory). Подробнее — INSTALL.md §1.

Снять — `bash uninstall.sh forge|forge-ui /path/to/repo` (ссылка в рантайме + каталог extension'а).
Проект переехал или сменился python — `bash .gigacode/extensions/<kind>/deploy-local.sh`.

> ⚠️ **Не копируй скиллы и хуки вручную по отдельности.** Провальный прогон pprb-kid
> случился именно так: скиллы залили на проектный уровень, а проводку хуков НЕ влили →
> рантайм стартовал с `[HOOK_REGISTRY] 0 hook entries`, весь control-plane молчал.
> `install.sh` исключает этот класс ошибок: код и проводка (`hooks/hooks.json`) едут одним пакетом.
>
> ⚠️ **И не удаляй каталог extension'а руками** — зеркальная поломка: файлов нет, а ссылка в
> рантайме осталась. Снимай `uninstall.sh` — он снимает и ссылку.

## ⚠️ ЗАПУСК: gigacode без флагов, из корня проекта

```bash
gigacode                               # интерактивно — канон; дальше команда /forge <задача>
```

В gigacode 26.9 флагов `--experimental-hooks` и `-y` нет: сессия с ними не стартует
(`Unknown arguments`, exit 1). Ранние сборки форка держали хуки за `--experimental-hooks` —
этот совет из прежних инструкций передавать нельзя. Хуки рантайм берёт из extension'а, который
включён только для workspace проекта, поэтому запуск — из корня проекта (или изнутри него). Признак, что хуки не
загрузились, — `[HOOK_REGISTRY] 0 hook entries` на старте: весь control-plane молчит (это и
был провал pprb-kid), первым делом — preflight ниже.

Headless (`gigacode "<задача>"`) — отдельный режим: инструменты с подтверждением (`agent`,
`skill`, `run_shell_command`) разрешаются заранее списком `--allowed-tools` (YOLO в 26.9 нет),
иначе субагент не стартует и фаза упирается в `inline-phase-guard`; `ask_user_question` не
рендерится, поэтому все решения и approval-маркеры предзаписываются ДО прогона (INSTALL.md
§«Headless»).

## Диагностика ПЕРЕД прогоном (обязательно)

```bash
python3 <project>/.gigacode/extensions/<kind>/hooks/preflight.py --project <project>
```

Проверяет: проводка хуков непустая, все essential-хук-скрипты на месте и **РЕАЛЬНО
перечислены** в `command`-полях `hooks/hooks.json` (наличие файла ≠ подключение — кейс
eval-guard: файл был, в проводке — нет, и preflight давал зелёный свет при выключенном
enforcement), пути не ведут за пределы extension'а, манифест собран для этого пути проекта,
ссылка в рантайме есть, рядом нет старой раскладки (задвоила бы хуки) и второй поставки,
`risk-policy.json` парсится (иначе `risk_ladder` тихо деградирует до
allow-all). Advisory прогоняет `skills/feature-pipeline/scripts/doctor.py` (целостность
пакета, валидность скиллов — frontmatter `name`/`description`, иначе рантайм молча
скипнет). Ловит «0 hook entries», «skills не рядом», неверные матчеры и чужие пути ДО
запуска пайплайна.

Exit-коды:
- `0` — армирован, можно работать.
- `1` — ENFORCEMENT OFF (essential-хук не подключён / старая раскладка рядом / risk-policy):
  стоп-и-предупреди; текст ошибки называет починку (`deploy-local.sh` extension'а или `install.sh`).
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
в выводе JSON, а если файл есть, но не дозаполнен — `answer_commands`: `config.py set` по
каждому полю из `_incomplete`). Правится только через `config-helper/scripts/config.py set` — прямая
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
  подключаются/отключаются только проводкой (`hooks/hooks.json` extension'а), эти флаги не читает никто;
- `risk.deny_first` — deny-first вшит в `gate-guard`/`risk_ladder`, флагом не управляется;
- `evidence.threshold` — `risk_ladder.evidence_ok` берёт порог из аргумента по умолчанию;
- `autonomy.{level,auto_max_risk}` — v1-имена. В v2 потолок авто-прохода живёт в манифесте
  как `decisions.auto_max_risk` (+ `decisions.criticality`), и читает его `gate-guard`.

`risk.policy` — путь к `risk-policy.json`; фактически хуки резолвят её co-located рядом
с собой, так что значение справочное.

## Выключение / тюнинг

- `"disableAllHooks": true` в `<project>/.gigacode/settings.json` — отключить ВСЁ сразу (настройка
  рантайма, действует и на хуки extension'а).
- Выключить forge в проекте целиком — `gigacode extensions disable --scope workspace <имя>` (имя — в
  `gigacode-extension.json` extension'а).
- Нестабилен один хук — убери его строку из события в `hooks/hooks.json` extension'а (переустановка
  вернёт). Чтобы выключить хук во ВСЕХ установках, убери строку из эталона `settings.hooks.json`
  в исходнике и переустанови (`update.sh`).
- Политику рисков менять в `risk-policy.json` без правки кода: да/нет конкретному
  действию определяется regex-паттернами (`destructive_blacklist`, `pii_patterns` и т.д.).
- Диагностику без запуска пайплайна — `preflight.py` (см. выше).
