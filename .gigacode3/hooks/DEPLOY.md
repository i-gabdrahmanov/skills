# Control plane на хуках (PDLC v3.5) — деплой

Хуки переносят enforcement пайплайна из текста SKILL.md в **рантайм** и реализуют harness v3.5:
risk ladder R0–R5, evidence bundle, cost circuit breaker, security-хуки. Главный принцип концепции:
**hooks = enforcement, CLAUDE.md/SKILL.md = только guidance** (модель текст может проигнорировать).
Это конфиг рантайма, НЕ скиллы; читается запущенным бинарём из своего конфиг-дома. Скрипты
path-агностичны: состояние берут из `<project>/ground/...`, гейты ищут по `../skills/...`,
политику — из `risk-policy.json` рядом.

## Состав хуков

| Скрипт | Событие | Назначение | Блок |
|---|---|---|---|
| `gate-guard.py` (+`risk_ladder.py`,`risk-policy.json`) | PreToolUse Bash/Write/Edit | permission gateway, risk ladder R0–R5, **deny-first** | exit 2 |
| `destructive-blocker.py` | PreToolUse `^Bash$` | чёрный список (`rm -rf /`, force-push, DROP…) | exit 2 |
| `pii-boundary.py` | PreToolUse Write/Edit/Bash | блок записи PII/секретов вне scope | exit 2 |
| `evidence-enforcer.py` | PreToolUse `^Bash$` | блок доставки без полного evidence bundle | exit 2 |
| `cost-breaker.py` | Pre/Post/Stop/SubagentStop/UserPromptSubmit | token budget warn 80% / stop 120% | exit 2 / block |
| `prompt-guard.py` | UserPromptSubmit + PostToolUse(read/fetch) | детект prompt-injection → additionalContext | нет |
| `state-recorder.py` | SubagentStop | авто-запись шага в pipeline-state по `step_id` | нет |
| `context-injector.py` | SubagentStart | инъекция grounding-excerpt/conventions | нет |
| `phase-gate.py` | Stop | блок завершения с висящим `in_progress` | block |
| `log-agent.py` | все | append-only JSONL аудит (sync) | нет |

Не-хуки рядом: `agentops.py` (Trust-метрики из JSONL), `evals/run-evals.py` (eval-набор),
`run-hook-tests.sh` (базовые тесты), `watch-agents.sh` (живой просмотр), `settings.hooks.json` (эталон блока).

## Порядок и sequential

PreToolUse `^Bash$` идёт **sequential**: destructive-blocker → cost-breaker → evidence-enforcer →
gate-guard → log. Любой из первых четырёх может заблокировать (exit 2) до выполнения команды. Логгер —
всегда последний и неблокирующий. Точный блок — в `settings.hooks.json`.

## Три расположения

| Каталог | Зачем | Конфиг-дом |
|---|---|---|
| `~/.gigacode3/` | **source-of-truth** (этот каталог) | — |
| `~/.qwen/` | локальный тест (бинарь на dev-машине, дом захардкожен `.qwen`) | `~/.qwen/` |
| `~/.gigacode/` | **прод-цель**: форк Qwen под брендом GigaCode | `~/.gigacode/` |

> Гейты вызываются по `<hooks>/../skills/...` → в целевом доме рядом с `hooks/` должны лежать `skills/`.

## Деплой — ОДНОЙ КОМАНДОЙ (канонический путь)

```bash
bash ~/.gigacode3/deploy.sh              # прод-дом ~/.gigacode
bash ~/.gigacode3/deploy.sh ~/.qwen      # локальный тест-дом
```
`deploy.sh` сам: (1) копирует `hooks/` И `skills/` в ОДИН дом (co-location — иначе гейты не найдут
`../skills`), (2) мержит блок `hooks` в `settings.json` с ретаргетом путей на этот дом и снимает
`disableAllHooks`, (3) прогоняет `doctor.py`.

> ⚠️ **Не копируй скиллы и хуки вручную по отдельности.** Провальный прогон pprb-kid случился именно
> так: скиллы залили на проектный уровень, а блок `hooks` в `settings.json` НЕ влили → рантайм стартовал
> с `[HOOK_REGISTRY] 0 hook entries`, весь control-plane молчал. `deploy.sh` исключает этот класс ошибок.

## Диагностика ПЕРЕД прогоном (обязательно)

```bash
python3 ~/.gigacode/hooks/doctor.py --home ~/.gigacode
```
Проверяет: блок `hooks` непустой, `disableAllHooks` снят, все хук-скрипты на месте, пути в settings
валидны, **skills co-located** рядом с hooks, gate-скрипты достижимы, **все скиллы валидны**
(frontmatter name/description — иначе рантайм молча скипнет), evals зелёные. Ловит «0 hook entries»,
«skills не рядом» и «мёртвые скиллы» ДО запуска. exit≠0 → harness не готов, гони `deploy.sh`.

Отдельно валидатор скиллов:
```bash
python3 ~/.gigacode/hooks/validate_skills.py --skills ~/.gigacode/skills   # + --strict для warnings
```

Дополнительно:
```bash
python3 ~/.gigacode/hooks/agentops.py --root <project>   # Trust-метрики из аудита
bash ~/.gigacode/hooks/watch-agents.sh "$(pwd)"          # живой просмотр (отдельный терминал)
```

## Конфиг проекта (`ground/pipeline.json`)

Новые блоки v3.5 (создаёт `init_pipeline_config.py`): `quality.token_budget`, `evidence.threshold`,
`risk.{policy,deny_first}`, `security.{destructive_blocker,pii_boundary,prompt_guard}`,
`autonomy.{level,auto_max_risk}`.

## Выключение / тюнинг

`"disableAllHooks": true` — отключить всё. Нестабилен один хук — убери его строку из события
(остальные, включая логгер, целы). Политику рисков менять в `risk-policy.json` без правки кода.
