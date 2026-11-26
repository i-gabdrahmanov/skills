# Покрытие forge-config

Каталог строится из реестра при запуске. ID, описание, псевдонимы, допустимые значения и
ограничения берутся из установленной версии Forge. Изменение реестра отражается в форме
без отдельного списка ID. [Реестр параметров](../skills/config-helper/references/params-registry.json).

| Операция или параметры | Варианты и ограничения | Файл и область | Форма |
| --- | --- | --- | --- |
| Реестр, `file=pipeline` | Тип, `enum`, `min`, `max`, правила `coerce_and_validate` | `ground/policy.json`, проект. Чтение из `ground/pipeline.json` при отсутствии policy | По типу, см. ниже |
| Реестр, `file=gates` | Булевы значения | `ground/feature-gates.json`, проект | Список |
| Реестр, `file=risk` | Значения реестра | `risk-policy.json` рядом с хуками, установка | Список |
| Реестр, `file=manifest` | Реестр и соответствие `inputs.mode` типу фичи | `ground/statements/<skill>/<feature>/manifest.json` версии 2 | Выбор фичи и поля по типу |
| `phase` | `enable`, `disable`, `add`, условие включения, скилл, гейты, описание, позиция | `phases_override` в `ground/policy.json` | Поля операции по фазам из резолвера и проекта |
| `risk` | Добавление или удаление записи списка, лимит роли R0–R5 | `risk-policy.json`, установка | Список для выбора группы и записи, поля паттерна и лимита |

`enum` и строковые перечисления показываются списками. `bool` предлагает вкл/выкл и
`null` по правилам CLI. Поля `int` и `float` показывают формат ввода и пределы. Для текста
формат описан в записи параметра. Список строк можно ввести через запятую или JSON-массивом.
Основания для операций и проверок описаны в [config-helper](../skills/config-helper/SKILL.md) и
[резолвере фаз](../skills/feature-pipeline/scripts/resolve_phases.py).

| Отдельная конфигурация | Штатная точка входа |
| --- | --- |
| `.spec-graph/config.json` | `graph_init` и проверка схемы spec-graph. [Руководство](../skills/specgraph/SKILL.md). |
| `.daiver/config.yaml`, `.daiver/component-map.yaml` | Инициализация `daiver_init` и настройки проекта forge-ui. [Скилл forgeui](../skills/forgeui/SKILL.md). |
| Локальный `minor-defect-fix/config.json` | Настройка сопоставления багов и репозиториев по шаблону скилла. [Скилл minor-defect-fix](../skills/minor-defect-fix/SKILL.md). |
| Регистрация хуков и MCP GigaCode | `install.sh`, профиль поставки и настройки рантайма. [Установка](../INSTALL.md). |
