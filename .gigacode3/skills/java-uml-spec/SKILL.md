---
name: java-uml-spec
description: Сканирует Java/Spring проект и генерирует MD-спецификацию с UML-диаграммами по REST-эндпойнтам и Kafka. SVG-диаграммы рендерятся встроенным Python-генератором (без Java/jar/Graphviz), PlantUML-исходник сохраняется в свёрнутом блоке для ручного редактирования. Используй, когда нужно собрать обзор API и асинхронных взаимодействий сервиса в виде markdown-документа, который IDEA и GitHub откроют без плагинов.
---

# Java UML Spec

Сканирует исходники Java/Spring приложения и собирает markdown с разделами:

- **Endpoints** — таблица REST-эндпойнтов и sequence-диаграммы по контроллерам (Client → Controller → Service).
- **Kafka** — таблицы topics/producers/consumers, component- и sequence-диаграммы.

Парсер — только `re` из stdlib. Покрывает типичные Spring-аннотации: `@RestController`, `@Controller`, `@*Mapping`, `@KafkaListener`, `KafkaTemplate.send(...)`, `@SendTo`. Частично распознаёт Kotlin-контроллеры (`fun method(): Type`).

## Пайплайн рендера

Для каждой диаграммы пишутся **два представления**:

1. `![](…svg)` — SVG-файл, сгенерированный собственным Python-рендером (`svg_render.py`).
2. `<details>` с ```plantuml текстом — для тех, кто хочет посмотреть/отредактировать исходник в IDE с PlantUML Integration плагином.

**Почему так:**

- SVG напрямую вставляется в MD и рендерится **везде** без плагинов: IDEA Markdown preview, GitHub, VS Code, любой браузер.
- Никакой зависимости от Java/plantuml.jar/Graphviz/Node.js — только Python 3.7+ из stdlib.
- PlantUML-текст остаётся как человеко-читаемый формат на случай ручной правки.

## Использование

```bash
python3 .gigacode/skills/java-uml-spec/scripts/scan.py <src-root> [-o <output.md>] [--title <text>]
```

Аргументы:

- `<src-root>` — директория с исходниками (`src/main/java`, корень модуля или весь проект — скрипт рекурсивно ищет `.java`/`.kt`).
- `-o, --output` — путь к выходному `.md`. По умолчанию `docs/spec.md` относительно cwd.
- `--title <text>` — заголовок документа (по умолчанию имя проекта из `build.gradle` / `pom.xml`).

Пример:

```bash
python3 .gigacode/skills/java-uml-spec/scripts/scan.py src/main/java -o docs/spec.md
```

После запуска создаётся:
- `docs/spec.md` — markdown-документ.
- `docs/spec_diagrams/diagram_NN_*.svg` — SVG-файлы, на которые ссылается markdown.

## Что извлекается

**Endpoints:**
- Классы с `@RestController` или `@Controller`.
- Базовый путь из `@RequestMapping` на классе.
- Методы с `@GetMapping/@PostMapping/@PutMapping/@DeleteMapping/@PatchMapping/@RequestMapping`.
- HTTP-метод, путь, имя java-метода, параметры (`@PathVariable`, `@RequestParam`, `@RequestBody`), тип ответа.
- Поля-сервисы (`private final XxxService` / Kotlin `val service: XxxService`) и их вызовы в теле метода — для построения sequence-диаграммы.

**Kafka:**
- Consumers: `@KafkaListener(topics = "...")` или `topics = {"a", "b"}`, c разворачиванием констант `TOPIC_X = "..."`.
- Producers: `kafkaTemplate.send("topic", ...)`, `@SendTo("topic")`.
- Группы из `groupId = "..."` показываются на стрелке consume.

## Диаграммы и их типы

| Раздел | Стиль | Что показывает |
|---|---|---|
| Endpoints (на каждый контроллер) | sequence | Client → Controller → Service/Dao/…  |
| Kafka — component | flowchart (3 колонки) | Producers слева, topics в центре, consumers справа. Сервис, который и producer, и consumer, помещается в одну колонку; стрелка produce идёт обратно к топику. |
| Kafka — sequence | sequence | Типовой поток publish/consume по каждому топику, разделители `==` по имени топика. |

Если Kafka в проекте не найдена — соответствующий раздел не пишется. Если эндпойнтов нет — не пишется раздел Endpoints.

## Структура скилла

```
.gigacode/skills/java-uml-spec/
├── SKILL.md
├── scripts/
│   ├── scan.py              # точка входа (CLI)
│   ├── endpoints.py         # парсер контроллеров (regex по аннотациям)
│   ├── kafka.py             # парсер Kafka
│   ├── diagrams.py          # генератор PlantUML-текста (как fallback / для ручной правки)
│   ├── svg_render.py        # собственный SVG-рендер из AST (без зависимостей)
│   └── md_writer.py         # сборка markdown
└── test/
    └── fixtures/            # минимальные java-файлы с Kafka для самопроверки
```

## Расширение

- **Стили / цвета** — константы `C_*` в `svg_render.py`.
- **Размеры боксов / отступы** — константы вверху `svg_render.py`.
- **Эвристика «это бин-зависимость»** — кортеж `DEPENDENCY_SUFFIXES` в `endpoints.py` (`Service`, `Dao`, `Repository`, …).
- **Поддерживаемые аннотации маппинга** — словарь `MAPPING_ANNOTATIONS` в `endpoints.py`.
