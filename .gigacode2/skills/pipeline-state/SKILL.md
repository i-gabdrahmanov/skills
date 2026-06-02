---
name: pipeline-state
description: >
  Управляет состоянием многошаговых пайплайнов с субагентами: сохраняет статус
  каждого шага, JSON-выходы субагентов и контекстные выжимки, чтобы при обрыве
  пайплайна можно было резюмировать с того же места. Конвенция места хранения:
  <project>/.gigacode/statements/<skill-name>/pipeline/. Используется как
  вспомогательный скилл из других многошаговых скиллов (system-analyst,
  minor-defect-fix). НЕ инструмент общего назначения — это служебная утилита
  для оркестраторов с >3 субагентами.
---

# Pipeline State

Общая утилита для надёжного исполнения многошаговых пайплайнов. Решает два
сценария:

1. **Резюмирование после обрыва.** Если 4/10 субагентов упёрлись в лимит и
   главный агент тоже устал — можно перезапустить и продолжить с того же места.
2. **Передача контекста между шагами.** Синтезаторы (например, `glossary-mapper`)
   получают **выжимку** выходов коллекторов (`domain-mapper`), а не полный JSON
   и не нулевой контекст.

## Конвенция места хранения

```
<project>/                                  ← корень проекта пользователя
└── .gigacode/
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

Папку `statements/` можно добавить в `.gitignore` — это технический кэш, не
артефакт.

`--project` принимает **любой путь** к корню проекта; git не требуется. На не-git
проекте передавай `$(pwd)` (например `$(git rev-parse --show-toplevel 2>/dev/null || pwd)`).

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
      "id": "01-structure",
      "title": "Map project structure",
      "status": "completed",
      "output_file": "01-structure.json",
      "started_at": "2026-05-29T11:00:05Z",
      "completed_at": "2026-05-29T11:00:45Z",
      "duration_ms": 40000,
      "attempts": 1,
      "depends_on": []
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

## Как использовать (для скилла-оркестратора)

### Шаг 0: вначале каждого запуска

```bash
python ~/.gigacode/skills/pipeline-state/scripts/read.py \
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
python ~/.gigacode/skills/pipeline-state/scripts/init.py \
    --project /path/to/project \
    --skill system-analysis \
    --steps '[
        {"id": "01-structure", "title": "Map structure"},
        {"id": "02-api", "title": "Map API", "depends_on": ["01-structure"]},
        ...
    ]' \
    --context '{"business_intent": "...", "scope": "full"}'
```

Это создаёт `manifest.json` со всеми шагами в статусе `pending` (или
`in_progress` для тех, что главный агент собирается запускать сразу).

При **резюмировании** (после "Резюмировать" в шаге 0): `init.py` НЕ зовётся —
вместо этого читается существующий manifest. `update.py` помечает failed →
pending перед перезапуском.

### Шаг 2: запуск субагента + сохранение результата

После каждого возврата субагента:

```bash
python ~/.gigacode/skills/pipeline-state/scripts/update.py \
    --project /path/to/project \
    --skill system-analysis \
    --step-id 02-api \
    --status completed \
    --output-file - << 'EOF'
{"openapi_available": false, "endpoints": [...]}
EOF
```

Или для упавшего:

```bash
python ~/.gigacode/skills/pipeline-state/scripts/update.py \
    --project /path/to/project \
    --skill system-analysis \
    --step-id 06-integration \
    --status failed \
    --error "session limit"
```

### Шаг 3: подготовка контекста для зависимых шагов

Перед спавном синтезатора главный агент собирает выжимку из предыдущих шагов:

```bash
python ~/.gigacode/skills/pipeline-state/scripts/read.py \
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
  `.gigacode/statements/<skill>/archived/`, пользователь чистит вручную.

## Скрипты

| Скрипт | Назначение |
|---|---|
| `scripts/init.py` | Создать manifest при первом запуске |
| `scripts/add_steps.py` | Дописать новые шаги в существующий manifest (идемпотентно; для шагов, известных только по ходу прогона) |
| `scripts/update.py` | Обновить статус шага и сохранить JSON-выход |
| `scripts/read.py` | Прочитать state, выдать summary или выжимку шага |

См. `scripts/<name>.py --help` для деталей.

## Интеграция со скиллами

| Скилл | Как использует |
|---|---|
| `system-analyst` | Главный потребитель: 11 шагов (structure + 7 коллекторов + 3 синтезатора + assembled). На больших проектах (>10 модулей) состоит из двух волн, и pipeline-state — единственный способ их корректно связать. |
| `minor-defect-fix` | Шаги: jira-fetch, scope-check, analyze, plan, fix, tests-write, tests-run, pre-commit, spec, commit, pr, report. Резюмирование особенно полезно, когда тестраннер падает на третьей итерации — можно вернуться к фиксу без повторного анализа. |

Для подключения в новый скилл:
1. Определи список шагов с id и зависимостями.
2. В SKILL.md укажи: «На старте проверяй state через
   `~/.gigacode/skills/pipeline-state/scripts/read.py`».
3. После каждого шага вызывай `update.py`.
4. Для зависимых шагов читай выжимки через `read.py --excerpt-of`.
