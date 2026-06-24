# Control plane на хуках (PDLC v3.5) — деплой

Хуки переносят enforcement пайплайна из текста SKILL.md в **рантайм** и реализуют Forge v3.5:
risk ladder R0–R5, evidence bundle, cost circuit breaker, security-хуки. Главный принцип концепции:
**hooks = enforcement, CLAUDE.md/SKILL.md = только guidance** (модель текст может проигнорировать).
Это конфиг рантайма, НЕ скиллы. Скрипты path-агностичны: состояние берут из
`<project>/ground/...`, гейты ищут по `../skills/...`, политику — из `risk-policy.json` рядом.

> **МОДЕЛЬ — ПРОЕКТНАЯ (всё под git, ничего в `~/.gigacode`).** Канон: `hooks/` и `skills/`
> co-located **внутри проекта** в `<project>/.gigacode/` и закоммичены в репо. Резолверы кода
> выводят базу из фактического расположения файла (`hooks/_project.gigacode_dir()` →
> `<this-hook>/..`; `skill_paths` — от `project_root`), поэтому **никакой зависимости от
> домашнего `~/.gigacode`**. Деплой = положить/обновить `.gigacode/` в репозитории проекта
> (`bash deploy.sh <project>` из репо Forge — см. ниже), а не в домашний каталог. Домашний дом,
> если используется, — лишь место для бинаря форка, но код пайплайна читается из проекта.

## Состав хуков

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit | permission gateway, risk ladder R0–R5, **deny-first**; форсит выбор критичности | exit 2 |
| `tdd-guard.py` | PreToolUse Write/Edit | форсит TDD (блок `src/main` пока RED pending) + тест-стратегию (блок `@DataJpaTest`/`@SpringBootTest` при `test_layer=service-unit`) | exit 2 |
| `eval-guard.py` | PreToolUse Write/Edit | блок записи в `src/main`, пока eval'ы задачи (compile/coverage/test_pass) не пройдены (Eval-Driven) | exit 2 |
| `destructive-blocker.py` | PreToolUse `^Bash$` | чёрный список (`rm -rf /`, force-push, DROP…) | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/секретов вне scope | exit 2 |
| `evidence-enforcer.py` | PreToolUse `^Bash$` | блок доставки без полного evidence bundle | exit 2 |
| `sod-enforcer.py` | PreToolUse Write/Edit/Bash | separation of duties: роль из активного шага (test не пишет src/main; design/spec/jira не коммитят/билдят) | exit 2 |
| `inline-phase-guard.py` | PreToolUse Write/Edit/Bash | actor-guard: ГЛАВНЫЙ агент (пустой `agent_type`) не производит артефакты/код subagent-фазы inline | exit 2 |
| `cost-breaker.py` | Pre/Post/Stop/SubagentStop/UserPromptSubmit | token budget warn ≥80% (стоп 120% **временно отключён — токены безлимитны**) | нет (warn-only) |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch) | детект prompt-injection → additionalContext | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим `in_progress` | block |
| `log-agent.py` | все | append-only JSONL аудит (sync) | нет |

Не-хуки рядом: `preflight.py` (проверка «харнес активен?» ПЕРЕД пайплайном — ловит «0 hook entries»),
`resolve_hook_paths.py` (merge блока hooks в settings.json + `--check`/`--dry-run`),
`agentops.py` (Trust-метрики), `evals/run-evals.py` (eval-набор), `watch-agents.sh` (живой просмотр),
`settings.hooks.json` (эталон). Статическая диагностика (`doctor.py`) и валидация скиллов живут
в `skills/feature-pipeline/scripts/` — `preflight.py` зовёт их сам.

## Порядок и sequential

PreToolUse `^Bash$` идёт **sequential**: destructive-blocker → pii-boundary → cost-breaker →
evidence-enforcer → sod-enforcer → inline-phase-guard → gate-guard → log. Write/Edit: pii-boundary →
**tdd-guard** → eval-guard → sod-enforcer → inline-phase-guard → gate-guard → log. Любой блокирующий
может остановить (exit 2) до действия. Логгер — всегда последний и неблокирующий. Точный блок — в `settings.hooks.json`.

## Расположение

| Каталог | Зачем |
|---|---|
| репо Forge (этот каталог — родитель `hooks/`) | **source-of-truth**: `hooks/` + `skills/` + `deploy.sh` |
| `<project>/.gigacode/` | **цель деплоя**: задеплоенная копия, закоммичена в репо проекта |

> Гейты вызываются по `<hooks>/../skills/...` → в `<project>/.gigacode/` рядом с `hooks/` должны
> лежать `skills/`. Привязки к домашнему `~/.gigacode` нет.

## Деплой — ОДНОЙ КОМАНДОЙ (канонический путь)

Полное руководство с примерами — [`../docs/deployment.md`](../docs/deployment.md).

```bash
# из склонированного репо Forge; целевая папка проекта обязательна
cd <forge>
bash deploy.sh /path/to/target-project
```
`deploy.sh` сам: (1) копирует `hooks/` И `skills/` в `<project>/.gigacode/` (co-location — иначе
гейты не найдут `../skills`) + доки, (2) кладёт `deploy-local.sh` и мержит блок `hooks` в
`settings.json` с ретаргетом путей и бэкапом старого файла (permissions/mcpServers сохраняются),
(3) прогоняет `preflight.py`. Повторный деплой идемпотентен.

Починить пути в `settings.json` после переезда/переклонирования проекта (без копирования Forge):
```bash
bash <project>/.gigacode/deploy-local.sh
```

> ⚠️ **Не копируй скиллы и хуки вручную по отдельности.** Провальный прогон pprb-kid случился именно
> так: скиллы залили на проектный уровень, а блок `hooks` в `settings.json` НЕ влили → рантайм стартовал
> с `[HOOK_REGISTRY] 0 hook entries`, весь control-plane молчал. `deploy.sh` исключает этот класс ошибок.

## ⚠️ ЗАПУСК: хуки за флагом `--experimental-hooks` (форк GigaCode)

В форке GigaCode хуки — **экспериментальная опция**, гейтятся CLI-флагом. Без него рантайм стартует с
`[HOOK_REGISTRY] 0 hook entries` — весь control-plane молчит (это и был провал pprb-kid). **Запускай ВСЕГДА с флагом:**
```bash
gigacode --experimental-hooks -p "<задача>"
# или интерактивно:
gigacode --experimental-hooks
```
Флаг — это флаг **запуска бинаря**, его нельзя прописать в settings.json. `deploy.sh` его не
ставит (не может — это аргумент процесса); `preflight.py` ловит отсутствие по firing-evidence.
(В апстриме Qwen флага нет — хуки on по умолчанию; это особенность форка.)

## Диагностика ПЕРЕД прогоном (обязательно)

```bash
python3 <project>/.gigacode/hooks/preflight.py --project <project>
```
Проверяет: блок `hooks` непустой, все хук-скрипты на месте, пути в `settings.json` не ведут за
пределы проекта (`resolve_hook_paths.py --check`), **skills co-located** рядом с hooks; advisory
прогоняет `skills/feature-pipeline/scripts/doctor.py` (целостность пайплайна, валидность
скиллов — frontmatter name/description, иначе рантайм молча скипнет). Ловит «0 hook entries»,
«skills не рядом» и чужие пути ДО запуска. `exit 1` → Forge не готов: гони `deploy.sh` или
(если только пути устарели) `deploy-local.sh`.

Дополнительно:
```bash
python3 <project>/.gigacode/hooks/agentops.py --root <project>   # Trust-метрики из аудита
bash <project>/.gigacode/hooks/watch-agents.sh                   # живой просмотр (отдельный терминал)
```

## Конфиг проекта (`ground/pipeline.json`)

Новые блоки v3.5 (создаёт `skills/feature-pipeline/scripts/init_pipeline_config.py`): `quality.token_budget`, `evidence.threshold`,
`risk.{policy,deny_first}`, `security.{destructive_blocker,pii_boundary,prompt_guard}`,
`autonomy.{level,auto_max_risk}`.

## Выключение / тюнинг

`"disableAllHooks": true` — отключить всё. Нестабилен один хук — убери его строку из события
(остальные, включая логгер, целы). Политику рисков менять в `risk-policy.json` без правки кода.
