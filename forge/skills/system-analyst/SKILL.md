---
name: system-analyst
description: >
  Сканирует Java/Spring микросервис (одиночный или многомодульный) и собирает
  системный обзор: модули и их зависимости, REST API, async-интеграции (Kafka/
  RabbitMQ), JPA-модель, схему БД из миграций (Flyway/Liquibase), внешние клиенты,
  конфиг по профилям, сквозные аспекты (фильтры, AOP, scheduled). На выходе —
  папка с MD-файлами и Mermaid-диаграммами в репо спецификации. Используй когда
  пользователь говорит "проанализируй сервис", "собери обзор системы", "сделай
  системную аналитику", "что у нас тут за сервис", "распиши архитектуру проекта",
  или приходит на новый проект и хочет быстро понять, что в нём.
---

# System Analyst

> **Все пути — в `references/skill-paths.json` (секция `skills.system-analyst`).**
> Пути к pipeline-state — `skills.pipeline-state.scripts.*`, к хукам — `hooks.*`.
> Не используй `~/.gigacode/...` — читай из конфига.

Скилл собирает системный обзор существующего Java/Spring микросервиса. Не правит
код. Не подключается к БД (только миграции). На выходе — папка с MD-файлами и
Mermaid-диаграммами, готовая к коммиту в репо спецификации.

**Архитектура работы:** оркестратор → опрос пользователя о бизнес-намерении →
`structure-mapper` (синхронно, находит модули) → **детерминированный скан
`scripts/scan_all.py`** (ground truth по всем модулям: entities, endpoints, Kafka,
clients, config, aspects — без LLM, без лимитов токенов, без compact-усечения) →
LLM-обогащение и синтезаторы (use-cases, glossary, operations) поверх scan-JSON →
сборка MD → **самопроверка `scripts/verify_coverage.py`** (полнота против ground truth).

---

## 0. Предусловия

- Текущая директория — корень репо кода (Java/Spring, Gradle или Maven).
- В `<project>/.gigacode/skills/minor-defect-fix/config.json` есть `docs_path` для текущего
  проекта. Если нет — спроси у пользователя одним вопросом и сохрани в тот же
  конфиг (это общий файл с другими скиллами).
- БД: только миграции. Если нет Flyway/Liquibase — db-mapper вернёт пустой
  результат, это нормально.
- Подключён скилл **`pipeline-state`** — без него нельзя резюмировать пайплайн
  после обрыва. См. раздел 0.5.

## 0.5 Pipeline state (резюмирование при обрыве)

Каждый запуск system-analyst — это пайплайн из 11 шагов. Если один из
субагентов упёрся в свой лимит токенов или процесс прервался — без сохранения
state'а пользователь теряет всё сделанное.

**Конвенция state'а:**
`<project>/ground/statements/system-analysis/pipeline/`
- `manifest.json` — статусы шагов
- `<step-id>.json` — JSON-выход каждого завершённого субагента

### Шаг 0.5.1: проверка state'а в самом начале

Перед любыми вопросами и субагентами:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/read.py --skill system-analysis
```

Возможные результаты:
- `{"status": "no_state", ...}` — пайплайн не запускался. Иди по обычному пути.
- `{"status": "in_flight", "counts": {...}, "next_runnable": [...]}` — есть
  незавершённые шаги. **Покажи пользователю summary** и спроси:
  > "Найден предыдущий запуск пайплайна (начат: …, завершено: N/11, упало: M).
  >  Что делаем?
  >  1. Резюмировать — продолжить с неуспешных/незавершённых шагов
  >  2. Начать с нуля — старый state будет архивирован
  >  3. Показать собранную аналитику (если есть)"
- `{"status": "completed", ...}` — всё завершено. Спроси:
  > "Аналитика уже собрана для этого проекта.
  >  1. Перегенерировать с нуля
  >  2. Открыть готовые MD-файлы и выйти"

### Шаг 0.5.2: инициализация state'а (при свежем запуске)

Когда пользователь подтвердил скоуп и бизнес-намерение, инициализируй state
**до** запуска первого субагента:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/init.py --skill system-analysis \
    --steps '[
        {"id": "01-structure",      "title": "Map project structure"},
        {"id": "01b-scan",          "title": "Deterministic scan (ground truth)", "depends_on": ["01-structure"]},
        {"id": "02-api",            "title": "Map REST API",         "depends_on": ["01b-scan"]},
        {"id": "03-async",          "title": "Map Kafka/RabbitMQ",   "depends_on": ["01-structure"]},
        {"id": "04-domain",         "title": "Map JPA entities",     "depends_on": ["01-structure"]},
        {"id": "05-db",             "title": "DB schema",            "depends_on": ["01-structure"]},
        {"id": "06-integration",    "title": "External integrations","depends_on": ["01-structure"]},
        {"id": "07-config",         "title": "Configuration",        "depends_on": ["01-structure"]},
        {"id": "08-cross-cutting",  "title": "Filters/AOP/Scheduled","depends_on": ["01-structure"]},
        {"id": "09-use-cases",      "title": "Top use cases",        "depends_on": ["01-structure","02-api","04-domain","06-integration"]},
        {"id": "10-glossary",       "title": "Domain glossary",      "depends_on": ["01-structure","04-domain"]},
        {"id": "11-operations",     "title": "Ops & deployment",     "depends_on": ["01-structure"]},
        {"id": "99-assembled",      "title": "MD files assembled",   "depends_on": ["02-api","03-async","04-domain","05-db","06-integration","07-config","08-cross-cutting","09-use-cases","10-glossary","11-operations"]},
        {"id": "99-verify",         "title": "Coverage self-check",  "depends_on": ["99-assembled"]}
    ]' \
    --context '{"business_intent": "<кратко суть задачи>", "scope": "full"}'
```

### Шаг 0.5.3: обновление state'а после каждого субагента

После того как субагент N вернул JSON, **сразу же**:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py --skill system-analysis \
    --step-id 02-api \
    --status completed \
    --output-stdin <<'EOF'
{ ... JSON, который вернул субагент ... }
EOF
```

Если субагент упал — обнови как `failed`:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --project ... --skill system-analysis \
    --step-id 06-integration \
    --status failed \
    --error "session limit"
```

После всех 10 коллекторов/синтезаторов и сборки MD — финально:

```bash
python <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --project ... --skill system-analysis \
    --step-id 99-assembled \
    --status completed
```

### Шаг 0.5.4: выжимки для синтезаторов

Перед спавном **синтезатора** (use-cases, glossary, operations) собери
выжимки выходов коллекторов, на которые он опирается:

```bash
# Например для use-case-mapper:
python <project>/.gigacode/skills/pipeline-state/scripts/read.py \
    --project ... --skill system-analysis --excerpt-of 02-api > /tmp/api_ex.json
python <project>/.gigacode/skills/pipeline-state/scripts/read.py \
    --project ... --skill system-analysis --excerpt-of 04-domain > /tmp/domain_ex.json
python <project>/.gigacode/skills/pipeline-state/scripts/read.py \
    --project ... --skill system-analysis --excerpt-of 06-integration > /tmp/integration_ex.json
```

Вставляй эти выжимки прямо в промпт синтезатора (как контекст «вот что найдено
коллекторами, опирайся на это»). Это **не** полный JSON — это компактная сводка
(имена + counts + 5 примеров на массив).

### Что НЕ хранить в state'е

- Сами MD-файлы (они уже в `<docs_path>/system-analysis/`)
- Логи разговора (они в GigaCode)
- Учётки / токены / секреты (никогда)

---

## 1. Бизнес-намерение (важно!)

Код не отвечает на вопрос "зачем эта система существует". Без этого ответа отчёт
становится "техническим срезом", а не "системной аналитикой".

Спроси пользователя **одним коротким сообщением**:

> "Опиши в 2-3 предложениях, зачем этот сервис и какую бизнес-задачу он решает.
>  Если не уверен — можно сказать 'не знаю', и я попробую вывести из кода."

Запомни ответ — он пойдёт в README раздел "Бизнес-намерение". Если пользователь
говорит "не знаю" / "выведи сам" — после сканирования сделай гипотезу из имён
доменов, эндпоинтов, README и пометь её как `[гипотеза]`.

## 2. Скоуп

Спроси пользователя одним сообщением:

> "Что собираем?
>  1. Полный обзор (рекомендую) — все слои
>  2. Только API (REST + async)
>  3. Только данные (domain + DB)
>  4. Кастомный — перечисли, что нужно"

По умолчанию — полный. Если пользователь говорит "просто всё" или "давай" — иди в
полный.

Запомни выбор. На этапе 3 будешь вызывать только нужные субагенты.

---

## 2. Этап 1: structure-mapper (синхронно, всегда)

Этот субагент идёт первым. Его выход нужен другим — они получают список модулей.
**Запусти его явным вызовом тула `agent`** (не выполняй скан структуры сам) — блок ниже это
аргументы вызова `agent(subagent_type=..., description=..., prompt=...)`:

```
description: "Map project structure"
subagent_type: general-purpose

prompt:
Просканируй структуру Java/Spring проекта в текущей рабочей директории.

Шаги:
1. Определи систему сборки:
   - Gradle: есть build.gradle / build.gradle.kts и settings.gradle.
   - Maven: есть pom.xml.
2. Если settings.gradle содержит `include(...)` или pom.xml содержит <modules> —
   проект многомодульный. Иначе — одиночный (модуль = корень).
3. Для каждого модуля собери:
   - имя (из settings.gradle или <artifactId>),
   - путь относительно корня,
   - наличие src/main/java и src/test/java,
   - зависимости от ДРУГИХ модулей того же проекта (из build.gradle: `implementation project(':foo')`, или maven <dependency> на проектный artifactId).
4. Найди классы с @SpringBootApplication во всех модулях — это application entry points.
5. Прочитай build.gradle / pom.xml корня для общих зависимостей и версий Spring Boot.

Верни JSON:
{
  "build_system": "gradle" | "maven",
  "is_multi_module": true|false,
  "spring_boot_version": "3.2.1" | null,
  "java_version": "17" | null,
  "modules": [
    {
      "name": "proxy-service",
      "path": "proxy-service",
      "has_src_main": true,
      "has_src_test": true,
      "depends_on": ["springproxy"],
      "spring_boot_application": "com.example.proxy.ProxyApplication"
    }
  ]
}

Не читай исходники модулей — это сделают другие субагенты. Только структура.
```

---

## 2.5 Этап 1.5: детерминированный скан (ground truth) — ОБЯЗАТЕЛЬНО

Сразу после structure-mapper и **до** LLM-субагентов прогони детерминированный
сканер. За один проход по всем модулям он извлекает механические артефакты
(entities, endpoints, Kafka, clients, config, cross-cutting) и приписывает их к
модулям — без LLM, без compact-усечения, без лимитов токенов. Это источник истины,
против которого идёт самопроверка (§4.5).

```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/scan_all.py \
    -o "<docs_path>/system-analysis/scan"
# несколько микросервис-репо: перечисли корни через пробел
```

На выходе — каталог `scan/`: `domain.json`, `api.json`, `async_consumers.json`,
`async_producers.json`, `integration.json`, `config.json`, `cross_cutting.json`,
`db.json`, `reuse.json`, `structure.json`, `summary.json`. У каждой категории — `total`,
`gate_total`, `counts_by_module`, полный `items[]`.

- **HARD** (точный счёт, проверяется гейтом): `domain` (@Entity), `api` (эндпойнты),
  `async_consumers` (@KafkaListener).
- **ADVISORY** (нечёткая семантика «что считать единицей» — гейт только предупреждает):
  integration, config, cross_cutting, db, async_producers, **reuse**.

> **`reuse.json` — каталог переиспользования** (для судьи качества `reuse-judge` и
> разработчика): `dependencies` (внешние библиотеки из build.gradle/pom — что доступно на
> classpath) + `project_utils` (внутрипроектные util/helper-классы с публичными сигнатурами).
> Строится за один детерминированный проход по всем модулям — фан-аут не нужен.
>
> **Опционально (большой/микросервисный проект): LLM-синтезатор `reuse-mapper`.** Для курации
> каталога (что именно даёт каждая зависимость — commons-lang3→StringUtils/ObjectUtils, guava→…;
> поиск дублей util-классов) добавь синтезатор-субагента, который читает срез `scan/reuse.json`
> своих модулей и пишет человекочитаемый `docs/system-analysis/reuse-catalog.md`. Фан-аут — по
> тому же правилу масштабирования, что и остальные синтезаторы (см. «Масштабирование»: >10
> модулей → волнами; несколько микросервис-репо → субагент на корень/группу). Малый проект —
> один проход без фан-аута, детерминированного `reuse.json` достаточно.

Сохрани шаг `01b-scan` в pipeline-state (output = `summary.json`).

> **Почему это главное:** раньше LLM-коллекторы грепали сами и из-за compact-режима +
> лимитов токенов находили лишь ~20% (14 из 55 entities, 21 из 93 endpoints — молча).
> Детерминированный скан даёт recall ≈ 100%; LLM остаётся для смысла, не для
> механического перечисления.

---

## 3. Этап 2: обогащение и синтез (LLM поверх scan-JSON)

> **Диспатч субагентов — ЯВНЫЙ вызов тула `agent`, не «сделай сам».** Каждый блок субагента ниже
> (и structure-mapper в §2) запускай вызовом тула, передавая его `prompt` как есть:
> ```
> agent(subagent_type="general-purpose", description="<имя маппера>",
>       prompt="<prompt блока + путь к корню, список модулей, относящийся срез scan-JSON>")
> ```
> Залп = несколько вызовов `agent(...)` в одном сообщении (см. «Масштабирование» — волнами на больших
> проектах). НЕ выполняй работу маппера inline в главном контексте — теряется изоляция и устойчивость.
> Каждый субагент возвращает JSON со своим `step_id` (его подхватывает хук `state-recorder`).

После скана LLM-субагенты больше **не грепают с нуля**. Для механических категорий
бери готовый полный список из scan-JSON и **обогащай** (описания, связи, назначение) —
**НИЧЕГО не выкидывай и не усекай**. Чисто механические таблицы (api/domain/async)
можно собрать прямо из scan-JSON вообще без субагента. Субагенты-синтезаторы
(use-cases, glossary, operations) получают полный scan-JSON как вход. Каждый субагент
получает: путь к корню, список модулей, скоуп и **относящийся к нему срез scan-JSON**.

### Масштабирование по размеру проекта

Количество субагентов в одном залпе зависит от размера:

| Модулей | Стратегия |
|---|---|
| ≤ 5 | Все 10 субагентов в одном сообщении — норм |
| 6-10 | Все 10 в одном сообщении, но усиль "будь лаконичен" в промптах |
| **> 10** | **Раздели на 2 волны**: волна 1 = коллекторы (api, async, domain, db, integration, config, cross-cutting); после её возврата — волна 2 = синтезаторы (use-case, glossary, operations) |

**Почему волны на больших проектах:** у каждого субагента свой лимит токенов
(независимый от главного агента). На проекте с 20+ модулями субагент может
прочитать 50+ файлов и упереться в лимит до возврата JSON. Залп из 10 на
большом проекте → обрывы у 30-50% субагентов.

Сначала прикинь размер по выходу structure-mapper. Многомодульный проект с
большим числом сервисов (≥10) и подмодулей (≥20) — обязательно волнами.

См. `references/subagents.md` для полных промптов каждого. Здесь — краткие описания.

### 3.1 api-mapper
Грепает `@RestController`, `@Controller`, `@RequestMapping`, `@GetMapping`, ...
Для каждого endpoint: HTTP метод, путь, request type, response type, security
аннотации, модуль. Возвращает JSON с массивом endpoints.

### 3.2 async-mapper
Грепает `@KafkaListener`, `KafkaTemplate.send`, `@RabbitListener`, `RabbitTemplate`.
Для каждого: topic/queue, message type, направление (producer/consumer), модуль.

### 3.3 domain-mapper
Грепает `@Entity`, `@Embeddable`, `@MappedSuperclass`. Для каждой entity: имя,
поля с типами, связи (`@OneToMany`/`@ManyToOne`/`@OneToOne`/`@ManyToMany`),
ключи, модуль.

### 3.4 db-mapper
Читает миграции Flyway (`src/main/resources/db/migration/V*.sql`) или Liquibase
(`db.changelog-master.xml/yaml` + дочерние). Восстанавливает финальную схему:
таблицы, колонки, типы, FK, индексы. Считает количество миграций.

### 3.5 integration-mapper
Грепает `@FeignClient`, `WebClient.create`, `RestTemplate`, внешние SDK-клиенты
(если узнаваемы по импортам). Для каждого: target URL/имя сервиса (если есть в
аннотации или конфиге), методы.

### 3.6 config-mapper
Читает `application.yml`/`application-*.yml`/`application.properties` во всех
модулях. Возвращает: список профилей, ключевые свойства (datasource, kafka,
порт), feature flags (если есть). Diff между профилями по верхнеуровневым
ключам.

### 3.7 cross-cutting-mapper
Грепает `@Aspect`, `OncePerRequestFilter`, `HandlerInterceptor`, `@Scheduled`,
`@EventListener`, `ApplicationEventPublisher`. Для каждого: класс, метод,
краткая цель (из имени + 1-2 строки реализации).

### 3.8 use-case-mapper (синтезатор)
Анализирует связки `controller → service → repository/cache/external client`,
выбирает топ-5 типичных use case и для каждого собирает sequence-схему (кто кого
зовёт, в каком порядке). Возвращает JSON со списком use case и шагами вызовов.

### 3.9 glossary-mapper (синтезатор)
Выделяет доменные термины (классы entity/DTO/enum), группирует похожие, пытается
отличить их друг от друга по полям/использованию. Цель — глоссарий, чтобы
читатель понял "Artifact vs Document vs DynamicDocument — это одно или разное".

### 3.10 operations-mapper (синтезатор)
Читает деплой/опс-файлы: Dockerfile, compose.yaml, build.gradle plugins,
actuator-конфиг, env-переменные (из `${...}` плейсхолдеров), logback.xml,
скрипты. Возвращает: топология деплоя, список env-переменных, мониторинг-точки.

---

## 4. Этап 3: сборка MD-документов

Таблицы механических разделов (`api.md`, `domain.md`, `async.md`, `integrations.md`,
`config.md`, `cross-cutting.md`, `db.md`) строй **из scan-JSON** — это полные списки,
ничего не теряется. LLM добавляет нарратив, Mermaid-диаграммы и описания. Разделы
синтезаторов (`use-cases.md`, `glossary.md`, `operations.md`) — из их JSON. Пиши в
`<docs_path>/system-analysis/`:

```
<docs_path>/system-analysis/
├── README.md           # обзор + бизнес-намерение + ссылки + C4-Context Mermaid
├── structure.md        # модули + диаграмма зависимостей
├── api.md              # REST каталог + Mermaid sequence (если уместно)
├── async.md            # Kafka/RabbitMQ каталог + flowchart producers→consumers
├── domain.md           # JPA entities + Mermaid erDiagram
├── db.md               # DDL + Mermaid erDiagram (если миграций нет — заглушка)
├── integrations.md     # внешние клиенты + flowchart
├── config.md           # таблица свойств по профилям
├── cross-cutting.md    # фильтры/AOP/scheduled
├── use-cases.md        # топ-5 сценариев + Mermaid sequence-диаграммы
├── glossary.md         # доменные термины с различиями
└── operations.md       # Docker/compose, env vars, actuator, мониторинг
```

Если в скоупе только часть слоёв — генерируй только их. README всегда обновляется.

Шаблоны каждого файла — в `references/output-templates.md`. Главное:
- Заголовки `# / ##` строго по шаблону, чтобы потом скрипты могли парсить.
- Все диаграммы — Mermaid в ```mermaid блоках (GitHub их рендерит без плагинов).
- Таблицы с фиксированными колонками — для удобства diff'ов.

**Не перезаписывай существующие файлы без подтверждения.** Если папка
`system-analysis/` уже есть:
1. Скажи пользователю: "Папка `system-analysis/` уже существует. Перезаписать,
   обновить только изменённые секции, или сохранить в `system-analysis-<date>/`?"
2. По умолчанию — обновить (сравни с прошлой версией, замени только разделы с
   реальными изменениями).

---

## 4.5 Самопроверка полноты (ОБЯЗАТЕЛЬНО — gate)

После сборки MD прогони гейт — он сверяет записанное против scan-JSON:

```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/verify_coverage.py \
    --scan "<docs_path>/system-analysis/scan" \
    --reported-counts '{"entities":<N в domain.md>,"endpoints":<N в api.md>,"async":<N consumer в async.md>}'
```

- exit 0 / `pass` — полнота HARD-категорий подтверждена.
- exit 2 / `fail` — артефакты потеряны. Гейт печатает `missing N: <имена>` (например
  пропущенные entity). **НЕ закрывай шаг 99.** Допиши недостающее в MD прямо из
  scan-JSON (`items[]` полные) и прогони гейт снова, пока не `pass`.

Сохрани результат как шаг `99-verify` в pipeline-state. Так недосчёт перестаёт быть
молчаливым: оркестратор не пройдёт дальше с дырой в обзоре.

## 5. Уведомление пользователю

После записи:
1. Покажи путь к папке.
2. Сводка цифрами **с вердиктом самопроверки**:
   > "Просканировано 23 модуля. Полнота (self-check ✓): 55 entities, 93 REST endpoint,
   > 2 Kafka listener, 55 таблиц, 14 внешних клиентов."
3. Спроси:
   > "Коммитить в репо спеки (ветка `chore/system-analysis-<date>`)?
   > Или оставить локально?"
4. Если "коммитить" — создай ветку, коммит, push, опционально PR (по аналогии со
   спецадаптером в minor-defect-fix).

---

## Когда чего НЕ делать

- Не подключайся к БД (даже если в config есть JDBC URL — это другая версия скилла).
- Не правь исходники проекта — только READ.
- Не генерируй sequence-диаграммы для всех endpoints — только если пользователь явно
  попросит топ-3-5. Это шумно и часто бесполезно.
- Не пытайся анализировать бизнес-логику сервисов — это не системная аналитика,
  это другая задача (потенциально defect-analyzer или manual review).
- Не запрашивай документацию у пользователя — скилл рисует то, что видит в коде.
  Если документация отличается — это сигнал для пользователя, не для скилла.

---

## Связь с другими скиллами

- `java-uml-spec` — пересечение по REST/Kafka диаграммам. system-analyst шире:
  он покрывает домен/БД/конфиг. Если java-uml-spec уже запускался — его выход
  не используется (своя генерация на чистом срезе).
- `minor-defect-fix` — общий конфиг (`docs_path`). При фиксе бага в новом для
  пользователя сервисе полезно сначала запустить system-analyst для контекста.
- `defect-analyzer` — может ссылаться на разделы `system-analysis/api.md` и
  `domain.md`, если пользователь явно дал ссылки.

---

## Ссылки

- `scripts/scan_all.py` — детерминированный скан (ground truth), пишет `scan/*.json`.
- `scripts/verify_coverage.py` — gate самопроверки полноты (reported vs ground truth).
- `references/subagents.md` — промпты субагентов-обогатителей и синтезаторов этапа 2.
- `references/output-templates.md` — шаблоны MD-файлов с Mermaid-блоками.
