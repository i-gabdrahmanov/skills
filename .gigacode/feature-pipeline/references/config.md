# Параметр-стор конвейера: `<project>/.gigacode/pipeline.json`

Единое место для всех параметров, от которых зависит конвейер в конкретном проекте.
Делает конвейер **переносимым**: скиллы не хардкодят пути/пороги/конвенции, а читают их
из файла, который лежит в самом проекте и версионируется вместе с кодом.

## Где живёт и как разрешается

| Слой | Путь | Роль |
|---|---|---|
| Глобальные дефолты (опц.) | `~/.gigacode/pipeline.defaults.json` | общие конвенции на все проекты |
| **Параметры проекта** | `<project>/.gigacode/pipeline.json` | **источник правды**, переопределяет дефолты |
| Рантайм-оверрайд | аргумент в диалоге | разовое переопределение на прогон |

Идентичность проекта = текущая директория (или `git rev-parse --show-toplevel`). Реестра
по абсолютным путям нет — поэтому ничего не ломается при переезде/переименовании проекта.

## Как скиллы это потребляют

Любой скилл читает `<project>/.gigacode/pipeline.json` **напрямую** (обычный JSON, через
Read). Скрипт не нужен для чтения. Если файла нет — скилл откатывается к прежнему
поведению (спросить у пользователя). Что берёт каждый:

| Скилл | Поля |
|---|---|
| `feature-pipeline` | весь файл; на старте грузит и при отсутствии запускает init |
| `tech-design` | `conventions.*` (package_root, migration_tool, changelog_path), `docs.*` |
| `jira-task-writer` | `jira.*` |
| `system-analyst` / спецадаптер | `docs.docs_path`, `project.modules` |
| фаза тестов | `quality.*` (coverage_threshold, test/build команды, отчёт) |
| фаза доставки | `delivery.*`, `project.default_branch`, `bitbucket.*` |

## Инициализация

```bash
python ~/.gigacode/skills/feature-pipeline/scripts/init_pipeline_config.py \
    --project "$(pwd)"            # создать (не перезапишет существующий)
    # --print   показать детект без записи
    # --update  обновить только авто-детект-поля, сохранив заполненное вручную
    # --force   перезаписать целиком
```

Скрипт авто-детектит: build-систему, мульти-модульность и список модулей, `default_branch`,
версии Java/Spring, инструмент миграций (по зависимостям), наличие JaCoCo, корневой пакет
(по `group`). Незаполняемое автоматически ставит в `null` и собирает в `_incomplete` —
оркестратор по этому списку понимает, о чём спросить.

## Схема (v1)

```jsonc
{
  "$schema": "feature-pipeline/config@1",
  "project": {
    "name": "npf",
    "build_system": "gradle",        // gradle | maven
    "is_multi_module": true,
    "modules": ["service-...", "..."],
    "default_branch": "main",
    "java_version": "21",
    "spring_boot_version": "3.3.0",
    "is_git": false                  // false → нужен git init для фаз 6 и pipeline-state
  },
  "conventions": {
    "package_root": "ru.sbrf.pprb.npf",
    "migration_tool": "none",        // liquibase | flyway | none
    "changelog_path": null
  },
  "quality": {
    "coverage_threshold": 0.80,
    "build_command": "./gradlew clean build",
    "test_command": "./gradlew test jacocoTestReport",
    "coverage_report": "build/reports/jacoco/test/jacocoTestReport.xml",
    "jacoco_configured": false
  },
  "docs": {
    "mode": "in-repo",               // in-repo | separate-repo
    "docs_path": "docs",
    "feature_docs_path": "docs/feature-pipeline"
  },
  "jira": {
    "enabled": null,                 // TODO
    "project_key": null,             // TODO напр. "NPF"
    "issue_type_story": "Story",
    "issue_type_subtask": "Sub-task"
  },
  "bitbucket": { "enabled": null, "workspace": null, "repo_slug": null },
  "delivery": { "pr_strategy": "stacked", "branch_prefix": "feature/" },
  "autonomy": { "mode": "gated", "gates": ["brd","design","jira","commit","pr","report"] }
}
```

## Что заполнить вручную после init

Поля из `_incomplete` — их скрипт знать не может:
- `jira.enabled` / `jira.project_key` — есть ли Jira и ключ проекта.
- `bitbucket.enabled` / `workspace` / `repo_slug` — куда создавать PR.
- `conventions.migration_tool` — если в проекте Liquibase ещё не подключён, но он целевой,
  поставь `liquibase` и заведи baseline changelog (см. `migrations.md`).
- `project.is_git=false` → сделай `git init` (иначе не работают ветки/PR и ключ pipeline-state).

## Обратная совместимость

Старый `~/.gigacode/skills/minor-defect-fix/config.json` (path-keyed `docs_path`) остаётся
рабочим. `feature-pipeline` при наличии `pipeline.json` берёт `docs.docs_path` из него;
если старый конфиг содержит запись для проекта — импортирует её. Миграция остальных скиллов
на `pipeline.json` — постепенная, ломать ничего не нужно.

## Переносимость скиллов (не параметр, а упаковка)

`feature-pipeline` ссылается на `../minor-defect-fix/references/*` (общие jira/bitbucket/
coverage воркфлоу). Поэтому при экспорте конвейера в другой проект `minor-defect-fix`
копируется рядом. `pipeline-state` вызывается по user-level пути
`~/.gigacode/skills/pipeline-state/scripts/` — он глобальный, копировать в проект не нужно.
