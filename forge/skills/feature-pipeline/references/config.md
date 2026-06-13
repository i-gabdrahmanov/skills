# Параметр-стор конвейера: `<project>/ground/pipeline.json`

Единое место для всех параметров, от которых зависит конвейер в конкретном проекте.
Делает конвейер **переносимым**: скиллы не хардкодят пути/пороги/конвенции, а читают их
из файла, который лежит в самом проекте и версионируется вместе с кодом.

## Где живёт и как разрешается

| Слой | Путь | Роль |
|---|---|---|
| Глобальные дефолты (опц.) | `~/.gigacode/pipeline.defaults.json` | общие конвенции на все проекты |
| **Параметры проекта** | `<project>/ground/pipeline.json` | **источник правды**, переопределяет дефолты |
| Рантайм-оверрайд | аргумент в диалоге | разовое переопределение на прогон |

Идентичность проекта = текущая директория (или `git rev-parse --show-toplevel`). Реестра
по абсолютным путям нет — поэтому ничего не ломается при переезде/переименовании проекта.

## Как скиллы это потребляют

Любой скилл читает `<project>/ground/pipeline.json` **напрямую** (обычный JSON, через
Read). Скрипт не нужен для чтения. Если файла нет — скилл откатывается к прежнему
поведению (спросить у пользователя). Что берёт каждый:

| Скилл | Поля |
|---|---|
| `feature-pipeline` | весь файл; на старте грузит и при отсутствии запускает init |
| `tech-design` | `conventions.*` (package_root, migration_tool, changelog_path), `docs.*` |
| `jira-task-writer` | `jira.*` |
| `system-analyst` / спецадаптер | `docs.docs_path`, `project.modules` |
| фаза тестов | `quality.*` (coverage_threshold, test/build команды, отчёт) |
| `resolve_phases.py` | `phases_override` — переопределение фаз |

## Динамический реестр фаз (runtime gating)

Фазы пайплайна не хардкодятся в SKILL.md — они резолвятся динамически через
`resolve_phases.py`. Это позволяет:

1. **Включать/выключать фазы** через конфиг без правки кода скиллов
2. **Добавлять новые фазы** (например security-review) через `phases_override`
3. **Адаптировать пайплайн** под конкретный проект (не всем нужен eval-plan или TDD)

### Базовая конфигурация фаз

Каждая фаза имеет:
- `id` — стабильный идентификатор (используется в pipeline-state manifest)
- `skill` — какой скилл отвечает за фазу (null если встроенная)
- `enabled_by` — условие включения (путь к полю в pipeline.json или `gates.*`)
- `skip_if` — условие пропуска (если уже выполнено, grounding.exists и т.п.)
- `gates` — список гейтов, требующих подтверждения пользователя
- `description` — человекочитаемое описание

### Переопределение через phases_override

`phases_override` — массив фаз, которые мержатся поверх базовых по `id`.
Это аналог GrowthBook dynamic config из Claude Code.

```json
"phases_override": [
  {"id": "02-eval-plan", "enabled_by": false},
  {"id": "05.5-security", "skill": null, "enabled_by": "gates.security_review", 
   "gates": ["security_approved"], "description": "SAST + CVE check"}
]
```

### Вызов resolve_phases.py

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/resolve_phases.py \
    --project <project> --feature <slug> --gates <project>/ground/feature-gates.json [--list]
```

Возвращает JSON:
```json
{
  "phases": [
    {"id": "00-brd", "skill": "business-requirements", "gates": ["brd"], "description": "..."},
    {"id": "02-design", "skill": "tech-design", "gates": ["design"], "description": "..."}
  ],
  "skipped": [
    {"id": "02-eval-plan", "reason": "enabled_by(quality.eval_enabled) = false"},
    {"id": "04-tdd", "reason": "skip_if(tdd_enabled) = false"}
  ],
  "total": 5,
  "skipped_count": 2
}
```

## Инициализация

```bash
python ~/.gigacode/skills/feature-pipeline/scripts/init_pipeline_config.py   # создать (не перезапишет); корень — git toplevel/cwd сам
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
    "jacoco_configured": false,
    "eval_enabled": true,            // Eval-Driven Development: eval-guard хук блокирует
                                      // запись в src/main/ пока eval'ы задачи не пройдены
    "eval_threshold": 0.95           // Порог прохождения eval'ов по умолчанию
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

---

## Приложение: схема `eval-plan.json`

Генерируется скриптом `build_evals_from_design.py` из `task-plan.json` и кладётся в
папку фичи (`docs/feature-pipeline/<slug>/eval-plan.json`).

```jsonc
{
  "$schema": "feature-pipeline/eval-plan@1",
  "feature_slug": "kidpprb-8639-auto-close-empty-tasks",
  "evaluated_at": null,               // ISO-8601, заполняется при прогоне eval-guard
  "evals": [
    {
      "id": "compile-t1",             // уникальный ID eval'а
      "type": "compile",              // compile | coverage | test_pass
      "task_id": "T1",                // привязка к задаче из task-plan
      "command": "./gradlew compileJava",
      "threshold": 0,                 // для compile: 0 (просто exit code)
      "description": "Полная компиляция проекта"
    },
    {
      "id": "coverage-t1",
      "type": "coverage",
      "task_id": "T1",
      "command": "python3 .../check_coverage.py --base HEAD~1 --threshold 0.80",
      "threshold": 0.80,
      "description": "Покрытие кода задачи T1 >= 80%"
    },
    {
      "id": "test_pass-t1",
      "type": "test_pass",
      "task_id": "T1",
      "command": "./gradlew compileJava", // тесты проверяются через сборку
      "threshold": 0.95,
      "description": "Тесты задачи T1 проходят (>= 95%)"
    }
  ],
  "summary": {
    "total": 3,
    "by_type": { "compile": 1, "coverage": 1, "test_pass": 1 },
    "by_task": { "T1": 3 }
  }
}
```

Хук `eval-guard` читает этот файл, идентифицирует активную задачу по манифесту,
прогоняет непройденные eval'ы и кеширует результаты в
`ground/statements/feature-pipeline/<slug>/eval-results.json`.
