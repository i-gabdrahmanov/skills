---
name: project-grounder
description: >
  Фаза 1 (Grounding) пайплайна feature-pipeline: проверяет наличие готового
  системного обзора проекта (docs/system-analysis/) и либо переиспользует его,
  либо запускает system-analyst для сбора. На выходе — компактная выжимка
  (модули, entity, API, Kafka, клиенты) для передачи в tech-design, и шаг
  01-grounding в pipeline-state feature-pipeline.

  Используй этот скилл когда: пользователь говорит "подготовь контекст для
  аналитика", "запусти grounding", "собери системный обзор для пайплайна",
  "нужен анализ проекта перед дизайном", "инициализируй контекст системы",
  или когда feature-pipeline вызывает Фазу 1. Также активируй при любом
  запросе типа "прогони grounding", "собери данные о системе", "нужна база
  для tech-design".
---

# Project Grounder

Скилл обеспечивает Фазу 1 (Grounding) пайплайна `feature-pipeline`: даёт
дизайнеру (`tech-design`) актуальный срез системы — модули, домен, API, async,
внешние клиенты. Ничего не пишет в исходники.

---

## 0. Предусловия

- Текущая директория — корень репо кода (Java/Spring).
- `<project>/.gigacode/pipeline.json` должен существовать (создаётся скриптом
  инициализации `feature-pipeline`; если его нет, сообщи и остановись).
- Скилл `pipeline-state` доступен — нужен для шага 01-grounding.
- Скилл `system-analyst` доступен — запускается если обзора нет.

---

## 1. Читаем конфиг

```bash
cat "$(pwd)/.gigacode/pipeline.json"
```

Возьми:
- `docs.docs_path` — путь к папке с документацией (относительно корня проекта
  или абсолютный). Если относительный — разворачивай от `$(pwd)`.
- `project.is_git` — нужен для pipeline-state (если `false`, state всё равно
  работает; просто `$(pwd)` вместо `$(git rev-parse --show-toplevel)`).

Если `pipeline.json` не найден — сообщи пользователю:
> "Не найден `.gigacode/pipeline.json`. Запусти сначала `feature-pipeline`, чтобы
>  проект был инициализирован, или создай конфиг вручную."
Остановись.

---

## 2. Проверяем наличие system-analysis

```bash
ls "<docs_path>/system-analysis/README.md" 2>/dev/null && echo EXISTS || echo MISSING
```

### 2a. Обзор уже есть

Прочитай `<docs_path>/system-analysis/README.md` (первые ~80 строк достаточно).
Покажи пользователю краткое резюме: список модулей, количество endpoints/entities,
дату если есть. Спроси:

> "Найден готовый системный обзор.
>  1. Переиспользовать (рекомендую) — пойдём дальше с текущим обзором
>  2. Перегенерировать — запустить `system-analyst` заново"

Если выбрал «1» — переходи к §4 (построение выжимки).
Если выбрал «2» — иди в §3 (запуск system-analyst).

### 2b. Обзора нет

Сообщи:
> "Системный обзор не найден в `<docs_path>/system-analysis/`. Запускаем `system-analyst`."

Переходи к §3.

---

## 3. Запускаем system-analyst

Перед вызовом убедись, что `system-analyst` знает правильный `docs_path`.
Запиши его в конфиг, которым пользуется `system-analyst`:

```bash
python3 - <<'EOF'
import json, pathlib, sys

config_path = pathlib.Path.home() / ".gigacode/skills/minor-defect-fix/config.json"
config_path.parent.mkdir(parents=True, exist_ok=True)

project = pathlib.Path.cwd()
# Читаем docs_path из pipeline.json
pipeline = json.loads((project / ".gigacode/pipeline.json").read_text())
docs_path = pipeline["docs"]["docs_path"]
if not pathlib.Path(docs_path).is_absolute():
    docs_path = str((project / docs_path).resolve())

config = {}
if config_path.exists():
    config = json.loads(config_path.read_text())

# Добавляем/обновляем запись для текущего проекта
projects = config.get("projects", {})
projects[str(project)] = {"docs_path": docs_path}
config["projects"] = projects
# Также ставим как активный проект (system-analyst читает текущий)
config["docs_path"] = docs_path

config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
print(f"OK: docs_path = {docs_path}")
EOF
```

Теперь загрузи скилл `system-analyst`. Он сам проведёт опрос про бизнес-намерение
и скоуп, запустит субагентов и запишет MD-файлы в `<docs_path>/system-analysis/`.

> **Важно:** не передавай в `system-analyst` историю разговора feature-pipeline.
> Он стартует с чистого листа — только контекст проекта из кода и конфига.

После завершения `system-analyst` переходи к §4.

---

## 4. Строим компактную выжимку (grounding excerpt)

Прочитай ключевые файлы из `<docs_path>/system-analysis/`:

| Файл | Что берём |
|---|---|
| `structure.md` | список модулей + зависимости |
| `domain.md` | имена entity + основные поля (без деталей) |
| `api.md` | endpoint'ы верхнего уровня (метод + путь + модуль) |
| `async.md` | топики/очереди + направление (producer/consumer) |
| `integrations.md` | имена внешних клиентов + target |
| `db.md` | список таблиц (без DDL) |

Если какого-то файла нет — пропускай без ошибки.

Собери JSON:

```json
{
  "generated_at": "<ISO timestamp>",
  "modules": [
    {"name": "service-dbservice", "path": "service/dbservice", "depends_on": []}
  ],
  "entities": [
    {"name": "Artifact", "module": "service-dbservice", "key_fields": ["id", "status", "createdAt"]}
  ],
  "api_endpoints": [
    {"method": "GET", "path": "/api/v1/artifacts", "module": "service-dbservice"}
  ],
  "async": [
    {"type": "kafka", "topic": "artifact.created", "direction": "producer", "module": "service-dbservice"}
  ],
  "external_clients": [
    {"name": "UpzClient", "target": "upz-adapter", "module": "service-dbservice"}
  ],
  "tables": ["artifact", "task", "document"]
}
```

Сохрани в `<docs_path>/system-analysis/grounding-excerpt.json`.

Покажи пользователю краткую сводку:
> "Выжимка готова: N модулей, M entities, K endpoints, L Kafka-топиков, P внешних клиентов."

---

## 5. Обновляем pipeline-state

Определи путь к проекту:
```bash
# Если is_git=true:
PROJECT=$(git rev-parse --show-toplevel)
# Если is_git=false:
PROJECT=$(pwd)
```

Обнови шаг `01-grounding` в pipeline feature-pipeline:

```bash
python ~/.gigacode/skills/pipeline-state/scripts/update.py \
    --project "$PROJECT" \
    --skill feature-pipeline \
    --step-id 01-grounding \
    --status completed \
    --output-json '{
      "system_analysis_path": "<docs_path>/system-analysis",
      "excerpt_path": "<docs_path>/system-analysis/grounding-excerpt.json",
      "modules_count": <N>,
      "entities_count": <M>,
      "endpoints_count": <K>
    }'
```

Если pipeline-state ещё не инициализирован для этого прогона (`manifest.json` не
существует) — не падай, просто пропусти обновление state и сообщи:
> "pipeline-state не инициализирован — обновление шага 01-grounding пропущено.
>  Оркестратор инициализирует state при полном прогоне."

---

## 6. Результат

Скилл завершён. Верни итог оркестратору или пользователю:

```
✓ Grounding завершён
  Обзор: <docs_path>/system-analysis/
  Выжимка: <docs_path>/system-analysis/grounding-excerpt.json
  Передать в tech-design: путь к выжимке + <docs_path>/system-analysis/
```

Если запущен standalone (не из feature-pipeline), спроси:
> "Хочешь сразу перейти к тех-дизайну? Для этого нужен BRD."

---

## Что НЕ делать

- Не перезапускать `system-analyst` если обзор уже есть и пользователь выбрал
  «переиспользовать».
- Не передавать в `system-analyst` историю разговора feature-pipeline.
- Не писать в исходники проекта — только в `docs/`.
- Не хранить в pipeline-state сами MD-файлы — только пути и счётчики.
- Не блокироваться на отсутствии pipeline-state manifest — просто пропустить §5.

---

## Ссылки

- `~/.gigacode/skills/system-analyst/SKILL.md` — полный цикл анализа
- `~/.gigacode/skills/pipeline-state/scripts/` — init/read/update
- `docs/feature-pipeline/contracts.md §6` — стык 1→2 (что именно передаётся дизайнеру)
