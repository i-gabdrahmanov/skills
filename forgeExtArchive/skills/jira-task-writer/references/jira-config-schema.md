# Jira Config Schema

Описание секции `jira` в `ground/pipeline.json`. Этот файл — справочник для
`jira-task-writer`: какие поля могут быть в конфиге, как они заполняются
(авто или вручную).

---

## Схема (минимальная — начальная)

Создаётся `init_pipeline_config.py`:

```jsonc
"jira": {
    "enabled": true|false|null,    // включена ли интеграция с Jira
    "project_key": "KIDPPRB|null", // ключ проекта в Jira
    "auto_discovered": false       // true — после прогона jira_discover.py
}
```

## Схема (полная — после автоопределения)

Заполняется скриптом `jira_discover.py` на основе метаданных Jira (MCP):

```jsonc
"jira": {
    // ─── Обязательные ─────────────────────────────────
    "enabled": true,
    "project_key": "KIDPPRB",
    "auto_discovered": true,

    // ─── Типы задач ───────────────────────────────────
    "issue_type_story": "Story",       // верхний уровень фичи
    "issue_type_subtask": "Sub-task",  // подзадача
    "issue_type_epic": "Epic",         // эпик
    "issue_type_bug": "Bug",           // дефект

    // ─── Кастомные поля (cf-идентификаторы) ───────────
    "epic_link_field": "customfield_11400",   // Epic Link
    "epic_name_field": "customfield_11404",   // Epic Name
    "sprint_field": "customfield_10008",      // Sprint
    "system_field": "customfield_22200",      // Система/подсистема
    "br_st_field": "customfield_38000",       // BR_ST (бизнес-требования)
    "fr_st_field": "customfield_38001",       // FR_ST (функц. требования)
    "process_field": "customfield_28000",     // Процесс (BS-опера)
    "fo_stream_field": "customfield_26800",   // ФО/Стрим
    "acceptance_criteria_field": "customfield_25800", // Acceptance Criteria

    // ─── Типы связей ────────────────────────────────
    "parent_link_type": "parent",
    "epic_link_type": "Epic Link",
    "blocked_link_type": "is blocked by",

    // ─── Agile-доска (основная scrum-доска проекта) ──
    "board": {
        "id": 27992,                                    // ID доски
        "name": "Развитие и поддержка КИД (sprint)",     // название
        "sprint_naming": "Развитие и поддержка КИД Sprint {N}_{YYYY}"  // шаблон имени спринта
    }
}
```

## Как это работает

1. **init_pipeline_config.py** создаёт минимальную секцию с `enabled: null`, `project_key: null`, `auto_discovered: false`.
2. Пользователь (или оркестратор) указывает `enabled: true` и `project_key`.
3. **feature-pipeline** на шаге 0.1 Config собирает метаданные через MCP:
   - `jira_search_fields` — все кастомные поля проекта
   - `jira_get_agile_boards` — доски
   - `jira_search` или `createmeta` — типы задач
4. Метаданные передаются в `jira_discover.py` (stdin), скрипт заполняет секцию целиком.
5. **jira-task-writer** читает готовый конфиг и использует его при создании задач.

## Fallback

Если `jira_discover.py` не запущен (или MCP недоступен, `auto_discovered: false`) —
`jira-task-writer` использует MCP напрямую на шаге 1 (как работал раньше) с минимальными
полями: только `project_key` и базовые типы.

## Маппинг имён полей (FIELD_MAP)

Скрипт `jira_discover.py` сопоставляет кастомные поля по имени:

| Имя поля в Jira | Ключ в конфиге |
|---|---|
| `Epic Link` | `epic_link_field` |
| `Epic Name` | `epic_name_field` |
| `Sprint` | `sprint_field` |
| `Система` | `system_field` |
| `BR_ST` | `br_st_field` |
| `FR_ST` | `fr_st_field` |
| `Процесс` | `process_field` |
| `ФО/Стрим` | `fo_stream_field` |
| `Acceptance Criteria` / `Критерии приемки` | `acceptance_criteria_field` |
| `Story Points` | `story_points_field` |