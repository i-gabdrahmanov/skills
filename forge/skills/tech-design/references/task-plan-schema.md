# Схема task-plan.json

`task-plan.json` — машиночитаемый контракт. Его потребляют:
- `jira-task-writer` — создаёт Story (из sdd.md) и Sub-task (из каждой задачи);
- build-фаза `feature-pipeline` — пишет код по `layers`/`artifacts`;
- deliver-фаза — по `depends_on` строит порядок stacked-PR.

Поэтому имена слоёв должны быть из фиксированного словаря, а пути артефактов —
валидными относительно `src/main/java` (или `src/main/resources` для миграций).

## Полная схема

```json
{
  "feature_slug": "bulk-artifact-export",
  "title": "Массовый экспорт артефактов",
  "sdd_path": "docs/feature-pipeline/bulk-artifact-export/sdd.md",
  "design_path": "docs/feature-pipeline/bulk-artifact-export/tech-design.md",
  "modules": ["storage-core"],
  "coverage_threshold": 0.80,
  "migrations": [
    {"changeset": "db/changelog/changes/2026-05-add-export-job.xml", "summary": "таблица export_job", "task_id": "T1"}
  ],
  "tasks": [
    {
      "id": "T1",
      "title": "Сущность ExportJob + миграция + репозиторий",
      "modules": ["storage-core"],
      "layers": ["migration", "entity", "repository"],
      "artifacts": [
        "db/changelog/changes/2026-05-add-export-job.xml",
        "entity/ExportJob.java",
        "repository/ExportJobRepository.java"
      ],
      "acceptance": ["Сущность маппится на таблицу", "CRUD через репозиторий"],
      "sdd_ref": "sdd.md#хранение-export-job",
      "reuses": [],
      "depends_on": []
    },
    {
      "id": "T2",
      "title": "Сервис экспорта + DTO + маппер",
      "modules": ["storage-core"],
      "layers": ["dto", "mapper", "service"],
      "artifacts": [
        "dto/ExportRequest.java", "dto/ExportResponse.java",
        "mapper/ExportMapper.java",
        "service/ExportService.java", "service/ExportServiceImpl.java"
      ],
      "acceptance": ["Запуск экспорта создаёт job", "Ошибка при пустом наборе"],
      "sdd_ref": "sdd.md#запуск-экспорта",
      "reuses": [],
      "depends_on": ["T1"]
    },
    {
      "id": "T3",
      "title": "REST-эндпойнт экспорта в существующем контроллере",
      "modules": ["storage-core"],
      "layers": ["controller"],
      "artifacts": ["controller/ArtifactController.java"],
      "acceptance": ["POST /api/v1/exports → 202", "401 без авторизации"],
      "sdd_ref": "sdd.md#rest-эндпойнт-экспорта",
      "reuses": ["controller/ArtifactController.java"],
      "depends_on": ["T2"]
    }
  ]
}
```

## Правила по полям

| Поле | Правило |
|---|---|
| `feature_slug` | kebab-case, латиница/транслит; совпадает с именем папки фичи |
| `title` | человекочитаемое имя — станет заголовком Story в Jira |
| `sdd_path` / `design_path` | относительные пути к артефактам фичи |
| `modules` | модули из system-analysis, которые затрагиваются |
| `coverage_threshold` | порог покрытия изменённых файлов; дефолт **0.80** |
| `migrations[].changeset` | путь к Liquibase changeset (НЕ Flyway `V__*.sql`) |
| `migrations[].task_id` | задача, в рамках которой пишется changeset |
| `tasks[].id` | короткий стабильный ID (`T1`, `T2`…); используется в `pipeline-state` как `04-build-<id>` |
| `tasks[].modules` | модули, затрагиваемые задачей (**массив** — задача бывает кросс-модульной: напр. сущность в `service-X` + DTO в `utils-web`). Допустима строка `tasks[].module` как одно-модульное сокращение |
| `tasks[].layers` | из словаря: `migration`, `entity`, `repository`, `dto`, `mapper`, `service`, `controller`, `scheduler`. Только реально затрагиваемые |
| `tasks[].artifacts` | для multi-module — пути **от корня репо** (напр. `service/dbservice/src/main/java/...`, `utils/web/src/main/java/...`); для одного модуля допустимо относительно его `src/main/java` (или `src/main/resources` для миграций) |
| `tasks[].acceptance` | проверяемые утверждения из критериев приёмки BRD; основа тестов |
| `tasks[].sdd_ref` | **обязателен** (гейт `check_sdd.py` валит пустой): якорь на раздел/сценарий `sdd.md` (напр. `sdd.md#запуск-экспорта`) — трассировка задача → спецификация |
| `tasks[].reuses` | **опционально, но проверяется гейтом.** Список существующих классов/путей, которые задача **изменяет или переиспользует** — т.е. соответствуют строкам «изм.» в `tech-design.md §3`. Каждый обязан существовать в проекте (сверяется с инвентарём `scan/components.json`: service/repository/mapper/dto/controller + entity + util). Формат — путь (`controller/ArtifactController.java`), FQN (`com.x.ArtifactController`) или голое имя класса. **Не путать с `artifacts`** — там создаваемые НОВЫЕ файлы, здесь то, что уже есть |
| `tasks[].depends_on` | ID задач, от которых зависит компиляция; задаёт порядок build, Sub-task и stacked-PR |

## Частые ошибки

- **Дробление вертикального среза** — `entity` и `repository` в разных задачах. Объединяй:
  `java-spring-dev` всё равно генерит их вместе.
- **Циклические `depends_on`** — недопустимо; зависимости образуют DAG (порядок stacked-PR).
- **Слой не из словаря** (`exception`, `config`) — допустимо только если в проекте есть
  соответствующий пакет; иначе уложи в существующий слой или опиши в `tech-design.md §3`.
- **Артефакт без слоя или слой без артефакта** — каждая пара должна сходиться.
- **Задача без `acceptance`** — тогда build не сможет проверить, что сделал её правильно.
- **Выдуманный «существующий» класс в `reuses`** — гейт `check_taskplan.py` валит фазу, если
  класс из `reuses` не найден в инвентаре проекта (`scan/components.json`). Проектируй
  переиспользование только по реальным классам из `design-context.json → components`.
  Если класс на самом деле новый — он в `artifacts`, а не в `reuses`.
