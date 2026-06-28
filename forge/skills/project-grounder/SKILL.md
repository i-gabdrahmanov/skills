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

> **Хуки gigacode3.** Выжимку `docs/system-analysis/grounding-excerpt.json` рантайм-хук
> `context-injector` (SubagentStart) теперь сам подкладывает субагентам `tech-design`/`build`.
> Поэтому обязательно сохраняй её по этому пути — она стала точкой инъекции контекста.

---

## 0. Предусловия

- Текущая директория — корень репо кода (Java/Spring).
- `<project>/ground/pipeline.json` должен существовать (создаётся скриптом
  инициализации `feature-pipeline`; если его нет, сообщи и остановись).
- Скилл `pipeline-state` доступен — нужен для шага 01-grounding.
- Скилл `system-analyst` доступен — запускается если обзора нет.

---

## 1. Читаем конфиг

```bash
cat ground/pipeline.json
```

Возьми:
- `docs.docs_path` — путь к папке с документацией (относительно корня проекта
  или абсолютный). Если относительный — разворачивай от корня проекта (cwd).
- `project.is_git` — нужен для pipeline-state (если `false`, state всё равно
  работает; скрипты берут cwd вместо git toplevel).

Если `pipeline.json` не найден — сообщи пользователю:
> "Не найден `ground/pipeline.json`. Запусти сначала `feature-pipeline`, чтобы
>  проект был инициализирован, или создай конфиг вручную."
Остановись.

---

## 2. Проверяем наличие system-analysis (детерминированно, в нескольких местах)

Не проверяй один путь руками — используй детектор, он ищет grounding в типовых местах
(`system-analysis/`: `grounding-excerpt.json`, `overview.md`, `scan/`) и не даёт повторять грундинг снова и снова:
```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/check_grounding.py --root . --json
```
- **exit 0** → §2a (есть, переиспользуй). Вердикт содержит `kind` (excerpt|scan|overview),
  `path`, `excerpt_path`.
- **exit 1** → §2b (нет, запускай system-analyst).

### 2a. Обзор уже есть → переиспользуй (без повторного вопроса)

Грундинг найден — **переиспользуй по умолчанию, НЕ спрашивай и НЕ пересканируй** (повторный
грундинг на каждом прогоне — это и есть то, что раздражало). Кратко сообщи, что обзор найден
(`kind`, `path` из вердикта) и идём дальше:
- `kind=excerpt` → выжимка готова, сразу §5 (сохранение шага).
- `kind=scan` или `overview` (нет `grounding-excerpt.json`) → собери выжимку из scan (§4) и иди дальше.

Полный рескан (§3) запускай **только** при явном запросе пользователя или если `verify_coverage.py`
не сходится (реальный рассинхрон). По умолчанию — reuse.

> **Свежесть обзора.** Переиспользовать (1) обычно безопасно: после каждой фичи `feature-pipeline`
> в фазе Document **инкрементально обогащает** ground дельтой фичи (`enrich_grounding.py`, только
> изменённые модули, без полного рескана), так что обзор не «протухает». Полный рескан (2) нужен
> лишь при явном подозрении на рассинхрон — например, если правки шли мимо пайплайна или
> `verify_coverage.py` падает.

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

project = pathlib.Path.cwd()
config_path = project / ".gigacode/skills/minor-defect-fix/config.json"
config_path.parent.mkdir(parents=True, exist_ok=True)

# Читаем docs_path из pipeline.json
pipeline = json.loads((project / "ground/pipeline.json").read_text())
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

## 4. Строим компактную выжимку (grounding excerpt) — из scan-JSON

**Не перечитывай MD глазами LLM** — именно так терялись артефакты (14 entities из 55,
21 endpoint из 93). Источник выжимки — детерминированный scan-JSON. Если его ещё нет
(переиспользуем старый обзор без папки `scan/`), прогони сканер сам — он дешёвый и
без LLM:

```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/scan_all.py \
    -o "<docs_path>/system-analysis/scan"
```

Собери excerpt **напрямую из `scan/*.json`** (полные `items[]`, ничего не сокращая):
`entities` ← `domain.json`, `api_endpoints` ← `api.json`, `async` ← `async_consumers.json`
+ `async_producers.json`, `external_clients` ← `integration.json`, `tables` ← `db.json`,
`modules` ← `structure.json`, `reuse` ← `reuse.json` (каталог переиспользования —
зависимости и util-классы; компактно: координаты `artifact:version` и имена классов).

Структура JSON:

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
  "tables": ["artifact", "task", "document"],
  "reuse": {
    "dependencies": ["commons-lang3:3.14.0", "spring-boot-starter-web:3.2.1"],
    "project_utils": ["com.x.common.DateUtils", "com.x.common.JsonHelper"]
  }
}
```

> Каталог `reuse` нужен судье качества `reuse-judge` и разработчику: знать, что уже доступно
> на classpath и какие util-классы есть в проекте, чтобы не писать велосипеды.

Сохрани в `<docs_path>/system-analysis/grounding-excerpt.json`.

### 4.1 Самопроверка полноты (gate — ОБЯЗАТЕЛЬНО)

Сверь выжимку против ground truth, прежде чем отдавать дальше:

```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/verify_coverage.py \
    --scan "<docs_path>/system-analysis/scan" \
    --reported "<docs_path>/system-analysis/grounding-excerpt.json" \
    --code-root .
```

`--code-root .` включает независимый кросс-чек: грубый счёт `@Entity`/`@KafkaListener`/
`@*Mapping` по коду как нижняя граница против недосчёта самого сканера (основной gate
сверяет excerpt со scan и эту дыру не видит). Предупреждение `⚠ сканер недосчитал` —
повод на полный рескан через `system-analyst`, даже если HARD-категории формально `pass`.

- `pass` (exit 0) — полнота HARD-категорий подтверждена, иди в §5.
- `fail` (exit 2) — выжимка недосчитала (гейт печатает `missing N: <имена>`).
  Перестрой excerpt из scan-JSON (не из MD) и прогони гейт снова. **Не передавай
  неполную выжимку в `tech-design`** — иначе дизайнер спроектирует по дырявому обзору.

Покажи пользователю краткую сводку **с вердиктом**:
> "Выжимка готова (self-check ✓): N модулей, M entities, K endpoints, L Kafka-топиков,
>  P внешних клиентов."

---

## 5. Обновляем pipeline-state

Обнови шаг `01-grounding` в pipeline feature-pipeline (скрипт сам берёт корень проекта —
git toplevel или cwd; `--project <путь>` нужен только если корень другой):

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
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
- Не строить excerpt перечитыванием MD глазами LLM — только из `scan/*.json`
  (иначе теряются артефакты). И не передавать excerpt дальше без `pass` от
  `verify_coverage.py`.
- Не передавать в `system-analyst` историю разговора feature-pipeline.
- Не писать в исходники проекта — только в `docs/`.
- Не хранить в pipeline-state сами MD-файлы — только пути и счётчики.
- Не блокироваться на отсутствии pipeline-state manifest — просто пропустить §5.

---

## Ссылки

- `<project>/.gigacode/skills/system-analyst/SKILL.md` — полный цикл анализа
- `<project>/.gigacode/skills/system-analyst/scripts/scan_all.py` — детерминированный скан (ground truth для excerpt)
- `<project>/.gigacode/skills/system-analyst/scripts/verify_coverage.py` — gate самопроверки полноты
- `<project>/.gigacode/skills/pipeline-state/scripts/` — init/read/update
- `<project>/.gigacode/skills/tech-design/SKILL.md` — потребитель `grounding-excerpt.json` (что именно передаётся дизайнеру на стыке 1→2)
