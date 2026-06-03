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

## Деплой (прод, `~/.gigacode/`)

```bash
mkdir -p ~/.gigacode/hooks
cp -a ~/.gigacode3/hooks/. ~/.gigacode/hooks/        # включая risk-policy.json и evals/
cp -a ~/.gigacode3/skills/. ~/.gigacode/skills/      # нужны для вызова гейтов из хуков
chmod +x ~/.gigacode/hooks/*.py ~/.gigacode/hooks/*.sh ~/.gigacode/hooks/evals/*.py
```

Влить блок `hooks` из `settings.hooks.json` (пути уже `$HOME/.gigacode/`) в `~/.gigacode/settings.json` — мерж.

Проверка на месте:
```bash
python3 -c "import json; json.load(open('$HOME/.gigacode/settings.json'))"
python3 ~/.gigacode/hooks/evals/run-evals.py        # ожидается PASS по всем
python3 ~/.gigacode/hooks/agentops.py --root <project>   # Trust-метрики из аудита
bash ~/.gigacode/hooks/watch-agents.sh "$(pwd)"     # живой просмотр в отдельном терминале
```

## Локальный тест (`~/.qwen/`)

То же, но дом `~/.qwen/`: скопировать `hooks/` и `skills/`, влить блок `hooks` в `~/.qwen/settings.json`,
заменив `$HOME/.gigacode/` → `$HOME/.qwen/`. Затем `python3 ~/.qwen/hooks/evals/run-evals.py`.

## Конфиг проекта (`ground/pipeline.json`)

Новые блоки v3.5 (создаёт `init_pipeline_config.py`): `quality.token_budget`, `evidence.threshold`,
`risk.{policy,deny_first}`, `security.{destructive_blocker,pii_boundary,prompt_guard}`,
`autonomy.{level,auto_max_risk}`.

## Выключение / тюнинг

`"disableAllHooks": true` — отключить всё. Нестабилен один хук — убери его строку из события
(остальные, включая логгер, целы). Политику рисков менять в `risk-policy.json` без правки кода.
