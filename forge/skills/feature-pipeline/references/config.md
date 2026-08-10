# Параметр-стор конвейера: `<project>/ground/pipeline.json`

Единое место для всех параметров, от которых зависит конвейер в конкретном проекте.
Делает конвейер **переносимым**: скиллы не хардкодят пути/пороги/конвенции, а читают их
из файла, который лежит в самом проекте и версионируется вместе с кодом.

## Где живёт и как разрешается

| Слой | Путь | Роль |
|---|---|---|
| **Параметры проекта** | `<project>/ground/pipeline.json` | **источник правды** для всех скиллов |
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
| `sdd` | `docs.*` (пишет `sdd.md`; фаза `02-sdd`), `sdd.security_gate` (строгость состава) |
| `tech-design` | `conventions.*` (package_root, migration_tool, changelog_path, **migration_services** — в монорепо выбирает changelog затрагиваемого сервиса), `docs.*` |
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
    {"id": "02-sdd", "skill": "sdd", "gates": ["sdd"], "description": "..."},
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
python <project>/.gigacode/skills/feature-pipeline/scripts/init_pipeline_config.py   # создать (не перезапишет); корень — git toplevel/cwd сам
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
    "migration_tool": "none",        // liquibase | flyway | none — ПЕРВИЧНЫЙ (лучшая одиночная догадка)
    "changelog_path": null,          // ПЕРВИЧНЫЙ changelog (исходник, не build-артефакт)
    "migration_services": [          // ВСЕ сервисы монорепо с миграциями (авто-детект, по одному
                                     // на changelog-каталог); [] если миграций нет; в одиночном
                                     // репо — одна запись, совпадающая со скалярами выше.
      {"service": "pprbulservice", "migration_tool": "liquibase",
       "changelog_path": "pprbulservice/src/main/resources/db/changelog"},
      {"service": "upzservice", "migration_tool": "flyway",
       "changelog_path": "upzservice/src/main/resources/db/migration"}
    ]
  },
  "quality": {
    "coverage_threshold": 0.80,
    "build_command": "./gradlew clean build",
    "test_command": "./gradlew test jacocoTestReport",
    "coverage_report": "build/reports/jacoco/test/jacocoTestReport.xml",
    "jacoco_configured": false,
    "no_test_layers": null,          // слои task-plan, освобождаемые от RED-гейта (tdd-guard/
                                      // check_tests_red/red-judge). null → migration/entity/dto/
                                      // repository (совпадает с coverage-исключениями). Задача
                                      // exempt, если ВСЕ её слои в списке ЛИБО task.no_test=true.
                                      // src/main/resources (liquibase/yml) не гейтится независимо
    "eval_enabled": true,            // Eval-Driven Development: eval-guard хук блокирует
                                      // запись в src/main/ пока eval'ы задачи не пройдены
    "eval_threshold": 0.95,          // Порог прохождения eval'ов по умолчанию
    "architecture_check": false,     // вкл. ArchUnit-lite гейт слоёв в verify (check_architecture.py)
    "tautology_check": false,        // вкл. детектор пустых/тавтологичных тестов (check_tautological_tests.py)
    "traceability_check": false      // вкл. сквозной judge трассируемости (check_traceability.py)
  },
  "docs": {                          // ГДЕ живут документные артефакты (brd/sdd/tech-design/
                                     // task-plan, system-analysis/grounding). Резолвится ЕДИНО
                                     // через skill_paths.docs_base / _project.docs_base.
    "mode": "in-repo",               // in-repo | separate-repo
    "docs_path": "docs",             // in-repo: база под корнем проекта
    "repo_path": null,               // separate-repo: АБСОЛЮТНЫЙ путь к внешнему репо спеки
    "feature_subdir": "feature-pipeline",       // подпапка фич под docs-базой
    "system_analysis_subdir": "system-analysis", // подпапка системного обзора под docs-базой
    "master": {                      // МАСТЕР (system-analysis + specs/) ОТДЕЛЬНО от дельт.
                                     // null/нет → мастер там же, где docs (как сегодня).
      "mode": null,                  // null | in-repo | separate-repo (фолбэк на docs.mode)
      "repo_path": null,             // АБСОЛЮТНЫЙ путь к КЛОНУ репо мастер-спеки (separate-repo)
      "repo_url": null,              // origin для гайд-клона (forge авто-clone НЕ делает)
      "enabled": false,              // вести требования-мастер specs/<cap>/spec.md (обновляется командой)
      "capability": null,            // имя капабилити (spec.md); null → project.name
      "adr_subdir": "adr"            // подпапка ADR под master-базой (<master_base>/adr)
    }
  },
  "sdd": {                           // спецификация (фаза 02-sdd)
    "security_gate": "applicability", // hard | applicability | soft — строгость ДКБ-разделов SDD
                                     // (арх. контекст/границы доверия, модель угроз, регуляторка)
    "pull_before_grounding": false   // git pull --ff-only мастер-репо перед grounding (separate-repo)
  },
  "adr": {                           // Architecture Decision Records (rationale+статус)
    "enabled": false,                // вести ADR-файлы в <master_base>/adr и гейтить их на 02-design
    "enforce_couplings": false       // новая межмодульная связка требует accepted ADR (05-verify)
  },
  "spec": {                          // ПОВЕДЕНИЕ требований-мастера (где он лежит — в docs.master)
    "id_prefix": "REQ",              // префикс стабильных ID требований (### REQ-0007: ...)
    "drift": "warn",                 // off | warn — реакция spec-judge на неслитую дельту
    "scenario_floor": true,          // каждое требование обязано иметь ≥1 Given-When-Then
    "profile": "forge"               // состав разделов мастера (forge = разделы с ДКБ)
  },
  "jira": {
    "enabled": null,                 // TODO
    "project_key": null,             // TODO напр. "NPF"
    "issue_type_story": "Story",
    "issue_type_subtask": "Sub-task"
  },
  "autonomy": { "mode": "gated", "gates": ["brd","design","jira"] }
}
```

## Гейт архитектуры (ArchUnit-lite, `quality.architecture_check`)

Детерминированная проверка слоёв БЕЗ запуска Java/ArchUnit — статический разбор изменённых
`.java` (`check_architecture.py`). Ловит то, что покрытие и компиляция не видят:

- **package-root** (error): пакет файла обязан быть под `conventions.package_root`.
- **class-placement** (warning): класс с суффиксом слоя лежит не в своём пакете (`FooController`
  вне `.controller` и т.п.).
- **layer-dependency** (error/warning): запрещённые зависимости слоёв — `entity/domain` не
  импортирует `service/controller/repository/mapper/dto`; `repository` не импортирует
  `service/controller` (error); `controller`→`repository` напрямую (warning).

Включается `quality.architecture_check: true` (по умолчанию `false`). При включении оркестратор
гоняет гейт в фазе **verify (05)** на изменённых файлах фичи; `error` валит фазу:

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_architecture.py \
    --root <project> --base <branch-base> \
    --pipeline-config <project>/ground/pipeline.json [--strict] [--json]
# exit 2 → есть нарушения слоёв (error); --strict делает warning тоже блокирующими.
```

## Security: CVE (опц.)

**CVE-скан зависимостей** — требует БД уязвимостей (OWASP dependency-check / `gradle
dependencyCheckAnalyze` / `mvn org.owasp:dependency-check`), поэтому self-contained-скрипта нет:
подключается как доп. команда в фазе security/verify, когда проект настроил тулинг (аналогично
Spotless/Checkstyle). Фаза `05.5-security` (`gates.security_review`) — место для SAST+CVE.

## Гейт трассируемости (`quality.traceability_check`)

Сквозной judge `check_traceability.py` замыкает цепочку **требование → раздел SDD → задача →
eval**, которую `check_taskplan`/`check_sdd` проверяли лишь по частям. Детерминированно (без LLM):

- **sdd_ref резолвится** (error): якорь `sdd.md#anchor` реально существует в sdd.md (битая
  ссылка раньше проходила — проверялось лишь наличие строки).
- **eval-покрытие** (error): у каждой задачи есть ≥1 eval в eval-plan (задача без eval = EDD её не
  верифицирует); eval-сирота (`task_id` не из плана) — warning.
- **acceptance** (error): непустой.

Выдаёт матрицу трассировки `task → sdd✓ → evals:N → acc:N`. Включается
`quality.traceability_check: true`; гоняется после генерации eval-plan (фаза 02-eval-plan/verify):

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_traceability.py \
    "<папка фичи>/task-plan.json" --sdd "<папка фичи>/sdd.md" \
    --eval-plan "<папка фичи>/eval-plan.json" [--strict] [--json]
```

Деградирует мягко: нет sdd.md → резолв пропущен; нет eval-plan (eval выключен) → eval-цепочка пропущена.

## Строгость состава SDD (`sdd.security_gate`)

`sdd.md` пишется по обязательному составу разделов (SDD-шаблон, `sdd/references/sdd-template.md`).
Гейт `check_sdd_doc.py` валидирует состав; насколько жёстко он требует ДКБ-разделы —
задаётся `sdd.security_gate` (дефолт `applicability`). Ядро (§1–7 + Given-When-Then + запрет
кода в спеке) жёстко всегда, независимо от значения.

| Значение | §8 Арх. контекст + §9 Модель угроз | §10 User story / §11 Принятые решения / §12 Регуляторка |
|---|---|---|
| `hard` | обязательны с контентом (можно «не применимо: <причина>») | обязательны с контентом |
| `applicability` (дефолт) | обязательны (контент **или** «не применимо: <причина>») | warning, если нет |
| `soft` | warning, если нет | warning, если нет |

Механика «по применимости»: раздел засчитан, если заголовок есть и под ним либо контент, либо
явная пометка «не применимо: <причина>» — раздел закрывают осознанно, а не молча выпускают.
Меняется через `config-helper` (`config.py set sdd.security_gate <hard|applicability|soft>`);
`run_judge.py sdd` прокидывает путь конфига в гейт, автономный вызов гейта берёт политику из
`<cwd>/ground/pipeline.json` или дефолт.

## Гейт тавтологичных тестов (`quality.tautology_check`)

Статический детектор тестов, которые ничего не доказывают (`check_tautological_tests.py`) —
дополняет RED→GREEN-исполнение (которому нужен build): пустое тело `@Test` и тавтологии
(`assertTrue(true)`, `assertEquals(x, x)`) — error; «есть код, но не видно ассерта/verify» —
warning (возможно делегирование в хелпер). Включается `quality.tautology_check: true`; гоняется
в фазе **test/verify** на изменённых тест-файлах:

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_tautological_tests.py \
    --root <project> --base <branch-base> [--strict] [--json]
```

## Расположение документных артефактов: in-repo / separate-repo

Артефакты `brd.md` / `sdd.md` / `tech-design.md` / `task-plan.json` и системный обзор
`system-analysis/` + `grounding-excerpt.json` могут лежать **в самом репо кода** или
**в отдельном репозитории спеки**. Всё расположение задаётся секцией `docs` и резолвится
ЕДИНО — функциями `skill_paths.docs_base()` (скрипты) и `_project.docs_base()` (хуки).
Скрипты и хуки НЕ хардкодят `docs/...`, а спрашивают резолвер.

| Режим | Конфиг | Куда резолвится `feature-pipeline/` и `system-analysis/` |
|---|---|---|
| **in-repo** (дефолт) | `{"mode":"in-repo","docs_path":"docs"}` | `<project_root>/docs/...` |
| in-repo, другая папка | `{"docs_path":"documentation"}` | `<project_root>/documentation/...` |
| **separate-repo** | `{"mode":"separate-repo","repo_path":"/abs/spec-repo"}` | `/abs/spec-repo/...` (внешний репо, как есть) |

- `feature_subdir` / `system_analysis_subdir` — имена подпапок под базой (дефолты
  `feature-pipeline` / `system-analysis`).
- В режиме `separate-repo` спецадаптер и system-analyst работают в `repo_path` через
  `git -C <repo_path>` (отдельная ветка/коммит спеки).
- **Два способа настроить:** (1) прописать `docs` в `ground/pipeline.json`; (2) просто сказать
  агенту, куда класть артефакты — он впишет `docs` в нужном формате (init/обновление конфига),
  после чего весь пайплайн (скрипты, хуки, судьи) подхватит расположение из резолвера.
- **Legacy:** старые ключи `docs.feature_docs_path` / `docs.system_analysis_path` (полные
  относительные пути) ещё поддерживаются резолвером в in-repo режиме.

### Раздельная локация: мастер отдельно от дельт (`docs.master`)

Мастер (системный обзор `system-analysis/` + требования-мастер `specs/<cap>/spec.md`) можно
держать в **отдельном (в т.ч. удалённом) репозитории**, пока дельты (`feature-pipeline/`:
`sdd.md`/`tech-design.md`/`task-plan.json`) лежат рядом с кодом. Блок `docs.master`:

| Поле | Значение |
|---|---|
| `mode` | `null` → мастер там же, где `docs` (фолбэк на `docs.mode`); `separate-repo` → в `repo_path` |
| `repo_path` | АБСОЛЮТНЫЙ путь к **уже склонированному** репо мастер-спеки |
| `repo_url` | origin для гайд-клона (forge авто-clone НЕ делает — только подсказывает) |
| `enabled` | вести требования-мастер `specs/<cap>/spec.md` и сливать в неё дельты (фаза 06) |
| `capability` | имя капабилити для `spec.md`; `null` → `project.name` |

Резолв: `system_analysis_dir`/`grounding_excerpt_path`/`scan_dir` и (при `enabled`)
`specs/<cap>/spec.md` берут базу из `docs.master` (функция `_master_base`), а `feature_docs_dir`
(дельты) — из глобального `docs`. Синхронность скрипт/хук пинит `test_docs_resolver_consistency.py`.

Git-политика (forge-no-delivery): forge **пишет** обновление мастера в рабочее дерево клона
(`enrich_grounding` на фазе 06 и `/forge-spec merge` по запросу пользователя), но коммит/push
мастер-репо делает пользователь.
`sdd.pull_before_grounding: true` включает `git -C <repo_path> pull --ff-only` перед grounding
(мягко: на ошибке — предупреждение).

### ADR — архитектурные решения (третья ось: `adr.*`)

Триада артефактов: **grounding** = «как построено» (из кода), **мастер-спека** = «что должно
делать» (требования), **ADR** = «почему так решили» (rationale + статус). ADR из кода не
выводится (это не grounding) — авторский MADR-файл. Живут в `<master_base>/adr/` (едут в тот же
мастер-репо, что `specs/`), формат — `references/adr-template.md` в `system-analyst`.

| Поле | Значение |
|---|---|
| `adr.enabled` | учитывать существующие ADR как вход-ограничение tech-design, вести новые ADR-файлы и гейтить их на 02-design (`check_adr` в design-judge). Дефолт false |
| `adr.enforce_couplings` | новая межмодульная связка проходит 05-verify только с accepted ADR (см. ниже). Дефолт false |
| `docs.master.adr_subdir` | подпапка ADR под master-базой (дефолт `adr`) |

**Связь с grounding (триггер+энфорс):** `check_architecture` (граф связей из кода) на 05-verify
детектит новую group-связку модулей — это момент, когда нужен ADR. При `adr.enforce_couplings`
запись в `ground/architecture-policy.json` → `module_deps.allowed_new` должна ссылаться на ADR:
`{"edge": ["service-a","service-b"], "adr": "ADR-0007"}` (accepted). Так `architecture-policy.json`
становится энфорсимой проекцией принятого ADR. Старый формат `["A","B"]` (без ADR) поддерживается.

### Поведение требований-мастера (`spec.*`)

`docs.master` отвечает на вопрос **где** лежит мастер и ведётся ли он вообще; блок `spec` — на
вопрос **как он устроен и как проверяется**. Разделение не косметическое: `docs.master.*` читают
резолверы и скриптов, и хуков (синхронность пинится `test_docs_resolver_consistency.py`), а `spec.*`
читают только `spec_cli.py` и spec-judge — новые поведенческие ручки не тянут правку резолверов.

| Поле | Значение |
|---|---|
| `spec.id_prefix` | префикс стабильных ID требований мастера (`### REQ-0007: <название>`). Дефолт `REQ` |
| `spec.drift` | `off` \| `warn` — что делает spec-judge, если дельта фичи не слита в мастер. Дефолт `warn` (сообщает, не блокирует фазу) |
| `spec.scenario_floor` | каждое требование обязано нести ≥1 сценарий Given-When-Then (`check_master_spec`). Дефолт true |
| `spec.profile` | профиль состава разделов мастера. Дефолт `forge` (разделы с ДКБ: границы доверия, модель угроз, регуляторка) |

**Мастер обновляется по запросу, а не пайплайном.** Фаза 06 в `specs/<cap>/spec.md` не пишет —
она лишь сообщает о расхождении. Слияние дельты делает пользователь командой `/forge-spec merge
<slug>` (предпросмотр — `/forge-spec diff <slug>`, состояние — `/forge-spec status`). Запись идёт
в рабочее дерево клона мастер-репо, коммит/push — на пользователе (forge-no-delivery).

## Что заполнить вручную после init

Поля из `_incomplete` — их скрипт знать не может:
- `jira.enabled` / `jira.project_key` — есть ли Jira и ключ проекта.
- `conventions.migration_tool` — если в проекте Liquibase ещё не подключён, но он целевой,
  поставь `liquibase` и заведи baseline changelog (см. `migrations.md`).
- `conventions.migration_services` — **авто-детект** (сканирует весь репо: все `db/changelog`/
  `db/migration`, кроме build-артефактов, с атрибуцией сервису). Руками обычно не трогают;
  пересобирается `init_pipeline_config.py --update`. В монорепо потребители (`tech-design`,
  `migrations.md`) выбирают отсюда changelog того сервиса, который затрагивает фича.
- `project.is_git=false` → сделай `git init` (иначе не работают чекпойнты rollback и pipeline-state).

## Обратная совместимость

Старый `~/.gigacode/skills/minor-defect-fix/config.json` (path-keyed `docs_path`) остаётся
рабочим. `feature-pipeline` при наличии `pipeline.json` берёт `docs.docs_path` из него;
если старый конфиг содержит запись для проекта — импортирует её. Миграция остальных скиллов
на `pipeline.json` — постепенная, ломать ничего не нужно.

## Переносимость скиллов (не параметр, а упаковка)

`feature-pipeline` ссылается на `../minor-defect-fix/references/*` (общие jira/bitbucket/
coverage воркфлоу). `deploy.sh` раскладывает ВСЕ скиллы co-located в `<project>/.gigacode/skills/`,
поэтому `minor-defect-fix` и `pipeline-state` лежат рядом и вызываются по проектному пути
`<project>/.gigacode/skills/<skill>/scripts/` — отдельная глобальная установка не нужна.

---

## Приложение: схема `eval-plan.json`

Генерируется скриптом `build_evals_from_design.py` из `task-plan.json` и кладётся в
папку фичи (`docs/feature-pipeline/<slug>/eval-plan.json`).

```jsonc
{
  "$schema": "feature-pipeline/eval-plan@1",
  "feature_slug": "kidpprb-8639-auto-close-empty-tasks",
  "evaluated_at": null,               // ISO-8601, заполняется при прогоне run_pending_evals.py (eval-guard — read-only, только читает кэш)
  "evals": [
    {
      "id": "compile-t1",             // уникальный ID eval'а
      "type": "compile",              // compile | coverage | test_pass
      "task_id": "T1",                // привязка к задаче из task-plan
      "command": "./gradlew compileJava", // команда из pipeline.json (Maven: "mvn -q compile")
      "threshold": 0,                 // для compile: 0 (просто exit code)
      "binary": true,
      "description": "Проект компилируется"
    },
    {
      "id": "coverage-t1",
      "type": "coverage",
      "task_id": "T1",
      "command": "python3 .../check_coverage.py --base HEAD~1 --threshold 0.80 --strict",
      "threshold": 0.80,
      "description": "Покрытие кода задачи T1 >= 80%"
    },
    {
      "id": "test_pass-t1",
      "type": "test_pass",
      "task_id": "T1",
      "command": "./gradlew test",        // команда из pipeline.json (Maven: "mvn -q test ...")
      "threshold": 0,                      // бинарный gate (exit-код), не ratio
      "binary": true,
      "description": "Вся тест-сюита зелёная после задачи T1 (регрессия)"
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
