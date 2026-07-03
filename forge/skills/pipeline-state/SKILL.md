---
name: pipeline-state
description: >
  Управляет состоянием многошаговых пайплайнов с субагентами: сохраняет статус
  каждого шага, JSON-выходы субагентов и контекстные выжимки, чтобы при обрыве
  пайплайна можно было резюмировать с того же места. Конвенция места хранения:
  <project>/ground/statements/<skill-name>/pipeline/. Используется как
  вспомогательный скилл из других многошаговых скиллов (system-analyst,
  minor-defect-fix). НЕ инструмент общего назначения — это служебная утилита
  для оркестраторов с >3 субагентами.
---

# Pipeline State

> **Все пути — в `feature-pipeline/references/skill-paths.json` (секция `skills.pipeline-state`).**
> Пути к хукам — в `hooks.*`. Не используй `~/.gigacode/...` — читай из конфига.

Общая утилита для надёжного исполнения многошаговых пайплайнов. Решает два
сценария:

1. **Резюмирование после обрыва.** Если 4/10 субагентов упёрлись в лимит и
   главный агент тоже устал — можно перезапустить и продолжить с того же места.
2. **Передача контекста между шагами.** Синтезаторы (например, `glossary-mapper`)
   получают **выжимку** выходов коллекторов (`domain-mapper`), а не полный JSON
   и не нулевой контекст.

> **Хуки gigacode3.** В `feature-pipeline` манифест теперь дополнительно обновляется
> рантайм-хуком `state-recorder` (SubagentStop) — он вызывает `update.py` по полю `step_id`
> из JSON-вывода субагента. Ручной `update.py` остаётся основным путём; хук — страховка от
> «забыл закрыть шаг». Конвенция манифеста (steps[].id/status/depends_on) — без изменений.

## Конвенция места хранения

```
<project>/                                  ← корень проекта пользователя
└── ground/                                 ← НЕ dot-папка (иначе рантайм режет доступ)
    └── statements/                         ← общая папка для всех state-ов
        ├── system-analysis/                ← имя скилла-оркестратора
        │   └── pipeline/
        │       ├── manifest.json           ← статусы шагов + метаданные
        │       ├── 01-structure.json       ← кэш JSON-выхода каждого шага
        │       ├── 02-api.json
        │       └── ...
        ├── minor-defect-fix/
        │   └── pipeline/
        │       └── manifest.json
        └── ...
```

Папку `ground/statements/` можно добавить в `.gitignore` — это технический кэш, не
артефакт.

`--project` опционален: по умолчанию скрипт берёт корень сам (git toplevel или cwd).
Передавай `--project <путь>` только если корень проекта отличается от текущего каталога.

## Формат manifest.json

```json
{
  "version": 1,
  "skill": "system-analysis",
  "pipeline_id": "2026-05-29-110000",
  "started_at": "2026-05-29T11:00:00Z",
  "last_update": "2026-05-29T11:15:42Z",
  "project_root": "/Users/.../npf",
  "context": {
    "business_intent": "Микросервисная система логистических документов",
    "scope": "full",
    "extra": { "modules_count": 23 }
  },
  "steps": [
    {
      "id": "02-sdd",
      "title": "SDD specification",
      "status": "completed",
      "output_file": "02-sdd.json",
      "artifacts": {
        "sdd": "docs/feature-pipeline/kidpprb-8639/sdd.md"
      },
      "required_judges": ["sdd-judge"],
      "depends_on": ["00-brd", "01-grounding"]
    },
    {
      "id": "02-design",
      "title": "Tech design + task plan",
      "status": "completed",
      "output_file": "02-design.json",
      "artifacts": {
        "tech-design": "docs/feature-pipeline/kidpprb-8639/tech-design.md",
        "task-plan": "docs/feature-pipeline/kidpprb-8639/task-plan.json"
      },
      "started_at": "2026-05-29T11:00:05Z",
      "completed_at": "2026-05-29T11:00:45Z",
      "duration_ms": 40000,
      "attempts": 1,
      "required_judges": ["design-judge"],
      "depends_on": ["02-sdd"]
    },
    {
      "id": "06-integration",
      "title": "Map external integrations",
      "status": "failed",
      "error": "session limit reached",
      "attempts": 1,
      "depends_on": ["01-structure"]
    },
    {
      "id": "09-use-cases",
      "title": "Extract use cases",
      "status": "pending",
      "depends_on": ["01-structure", "02-api", "04-domain", "06-integration"]
    }
  ]
}
```

**Статусы шагов:**
- `pending` — ещё не запускался
- `in_progress` — запущен, ждём ответа
- `completed` — успешно завершён, JSON в `output_file`
- `failed` — упал, есть `error`
- `skipped` — пропущен пользователем

**Поле `artifacts`** (опционально): объект string→string, ссылки на артефакты,
созданные на этом шаге (например, `tech-design.md`, `task-plan.json`).
Пути нормализуются относительно корня проекта.
Заполняется:
- **автоматически** при `init.py` (если файлы уже существуют на диске по convention paths)
- **явно** через `--artifacts` при вызове `update.py`
- отображается в выводе `read.py` в поле `artifacts`

## Как использовать (для скилла-оркестратора)

### Шаг 0: вначале каждого запуска

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/read.py \
    --project /path/to/project \
    --skill system-analysis
```

Возвращает один из:
- `{"status": "no_state"}` — пайплайн ещё не запускался, начинай с нуля
- `{"status": "in_flight", "summary": {...}}` — есть незавершённые шаги
- `{"status": "completed", "summary": {...}}` — всё было успешно

Главный агент **всегда показывает пользователю summary** и спрашивает:
> "Найден предыдущий запуск (начат: …, завершено N/M шагов).
>  1. Резюмировать — продолжить с failed/pending шагов.
>  2. Начать с нуля — старый state будет архивирован.
>  3. Показать собранную аналитику и выйти (если всё completed)."

### Шаг 1: инициализация пайплайна

Перед первым субагентом главный агент инициализирует state:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/init.py \
    --project /path/to/project \
    --skill system-analysis \
    --steps '[
        {"id": "01-structure", "title": "Map structure"},
        {"id": "02-api", "title": "Map API", "depends_on": ["01-structure"]},
        ...
    ]' \
    --context '{"business_intent": "...", "scope": "full", "feature": "<slug>", "iteration": 1}'
```

Это создаёт `manifest.json` со всеми шагами в статусе `pending` (или
`in_progress` для тех, что главный агент собирается запускать сразу).

> Хук-логгер `agent-logger` группирует живые логи тул-вызовов агента и субагентов в
> `<project>/ground/ai-logs/<feature>/iter-NN/` (см. `<project>/.gigacode/hooks/log-agent.py`).
> Папка `<feature>` берётся из namespace-флага `--feature` (имя каталога манифеста), а **не** из
> `context.feature`; из `context` логгер использует только `iteration` (→ `iter-NN`). Без `--feature`
> логи лягут под именем скилла + хвост `pipeline_id`.

При **резюмировании** (после "Резюмировать" в шаге 0): `init.py` НЕ зовётся —
вместо этого читается существующий manifest. `update.py` помечает failed →
pending перед перезапуском.

### Шаг 2: запуск субагента + сохранение результата

После каждого возврата субагента:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --project /path/to/project \
    --skill system-analysis \
    --step-id 02-api \
    --status completed \
    --output-stdin << 'EOF'
{"openapi_available": false, "endpoints": [...]}
EOF
```

**Важно: если шаг создаёт файловые артефакты** (tech-design.md, task-plan.json,
sdd.md, eval-plan.json, jira-tasks-result.json), передавай их через `--artifacts`:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --project /path/to/project \
    --skill feature-pipeline \
    --feature <slug> \
    --step-id 02-design \
    --status completed \
    --artifacts '{
        "tech-design": "docs/feature-pipeline/<slug>/tech-design.md",
        "task-plan": "docs/feature-pipeline/<slug>/task-plan.json"
    }'
```

(Артефакт `sdd.md` закрывается отдельно на шаге `02-sdd`:
`--step-id 02-sdd --artifacts '{"sdd": "docs/feature-pipeline/<slug>/sdd.md"}'`.)

`--artifacts` принимает JSON-объект string→string. Пути нормализуются
относительно корня проекта. Сохранённые artifacts видны в `read.py`.

Если нужно **восстановить статусы** после `init.py --force` (например, при
сбросе пайплайна), используй `--skip-judges`, чтобы обойти проверку
`required_judges`:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --project /path/to/project \
    --skill feature-pipeline \
    --feature <slug> \
    --step-id 02-design \
    --status completed \
    --skip-judges
```

Или для упавшего:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --project /path/to/project \
    --skill system-analysis \
    --step-id 06-integration \
    --status failed \
    --error "session limit"
```

### Шаг 3: подготовка контекста для зависимых шагов

Перед спавном синтезатора главный агент собирает выжимку из предыдущих шагов:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/read.py \
    --project /path/to/project \
    --skill system-analysis \
    --excerpt-of 04-domain
```

Возвращает компактный JSON (имена entity + counts, без полей). Эту выжимку
главный агент вставляет в промпт синтезатора.

## Правила резюмирования

1. **Шаги без `depends_on` запускаются сразу.**
2. **Шаги с `depends_on`** — только когда все зависимости в статусе `completed`.
3. **При резюмировании** все шаги в статусе `failed`/`pending` сбрасываются и
   запускаются заново. **Шаги `completed` не перезапускаются** (их выход уже
   сохранён).
4. **Шаги `skipped`** — не перезапускаются никогда, пока пользователь явно не
   попросит.

## Что НЕ делает этот скилл

- **Не запускает субагентов сам.** Это работа оркестратора.
- **Не парсит выходы субагентов.** Принимает любой JSON, не валидирует структуру.
- **Не делает merge'ов параллельных запусков.** Один пайплайн — один state.
  Параллельные запуски одного скилла на одном проекте не поддерживаются.
- **Не чистит старые state'ы.** Старые архивы накапливаются в
  `ground/statements/<skill>/archived/`, пользователь чистит вручную.

## Скрипты

| Скрипт | Назначение |
|---|---|
| `scripts/init.py` | Создать manifest при первом запуске |
| `scripts/add_steps.py` | Дописать новые шаги в существующий manifest (идемпотентно; для шагов, известных только по ходу прогона). Проставляет `required_judges` по единой маске и, если у фичи уже есть `gate.json`, пересобирает его. **Для feature-pipeline канон — `skills/feature-pipeline/scripts/add_steps.py`** (он же дополнительно ведёт `phase-defs.json`); эта generic-версия — для прочих скиллов. |
| `scripts/update.py` | Обновить статус шага и сохранить JSON-выход. При блокировке судьёй/проверкой subagent-origin печатает путь разблокировки через `override_judge.py` |
| `scripts/read.py` | Прочитать state, выдать summary или выжимку шага |
| `scripts/override_judge.py` | Ручной пропуск гейта судьи (`--judge … --feature … --reason …`) — единственный путь закрыть шаг, заблокированный отсутствующим/проваленным вердиктом `required_judges`. **Создание override — R4**: `gate-guard` пропустит команду только при approval-маркере `ground/approvals/gate-override-<judge>.json`, фиксируемом после ЯВНОГО «да» пользователя (молча — exit 2); `--list`/`--remove` свободны |

См. `scripts/<name>.py --help` для деталей.

## Интеграция со скиллами

| Скилл | Как использует |
|---|---|
| `system-analyst` | Главный потребитель: 11 шагов (structure + 7 коллекторов + 3 синтезатора + assembled). На больших проектах (>10 модулей) состоит из двух волн, и pipeline-state — единственный способ их корректно связать. |
| `minor-defect-fix` | Шаги: jira-fetch, scope-check, analyze, plan, fix, tests-write, tests-run, pre-commit, spec, commit, pr, report. Резюмирование особенно полезно, когда тестраннер падает на третьей итерации — можно вернуться к фиксу без повторного анализа. |

Для подключения в новый скилл:
1. Определи список шагов с id и зависимостями.
2. В SKILL.md укажи: «На старте проверяй state через
   `<project>/.gigacode/skills/pipeline-state/scripts/read.py`».
3. После каждого шага вызывай `update.py`.
4. Для зависимых шагов читай выжимки через `read.py --excerpt-of`.
