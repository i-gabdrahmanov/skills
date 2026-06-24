---
name: feature-pipeline
description: >
  End-to-end рабочий процесс для реализации НОВОЙ ФИЧИ от бизнес-анализа до pull request
  в Bitbucket: собрать BRD (интервью или из идеи/Jira), спроектировать решение, завести
  задачи в Jira, написать код по слоям, довести тесты до покрытия, обновить спецификацию,
  и аккуратно создать stacked-PR по задачам с отчётом в Jira. Это «старший брат»
  minor-defect-fix: тот — для минорного дефекта, этот — для фичи с нуля. Используй когда
  пользователь говорит "сделай фичу X", "проведи фичу от анализа до PR", "запусти feature
  pipeline", "реализуй фичу end-to-end", или описывает идею продукта и хочет довести её
  до кода и PR. Скилл автономен между гейтами, но никогда не делает необратимое (создание
  задач, коммит, push, PR, отчёт в Jira) без явного подтверждения.
---

# Feature Pipeline

> **Все пути — в `references/skill-paths.json`, секция `skills.feature-pipeline`.**  
> Пути к другим скиллам — в `skills.<skill-name>`, к хукам — в `hooks.*`,  
> к ground-файлам — в `ground.*`, к docs — в `docs.*`.  
> Не используй `~/.gigacode/...` — читай из конфига.

Скилл ведёт фичу по циклу: **идея/Jira → BRD → контекст системы → SDD (спецификация) →
тех-дизайн → задачи в Jira → код → тесты → спека → stacked-PR → отчёт**.

## ⚠️ ЖЕЛЕЗОБЕТОННОЕ ПРАВИЛО: СУБАГЕНТЫ ОБЯЗАТЕЛЬНЫ

**Каждая фаза Design, Build, Verify, Document выполняется ТОЛЬКО через явный вызов
`agent(subagent_type="general-purpose", ...)`.** Оркестратор (ты) НЕ создаёт файлы фаз,
НЕ пишет код, НЕ редактирует артефакты фазы — только:
- Обновляет pipeline-state (`init`/`update`/`read`)
- Показывает пользователю гейты (Gate 1-6) и ждёт «да»
- Вызывает agent() с контрактом фазы → получает JSON → закрывает шаг
- Запускает execution-gates (check_taskplan, check_sdd, check_jira, check_build...)

**Самопроверка до начала каждой фазы:**

Перед вызовом agent() для фазы остановись и ответь:
1. Текущий шаг манифеста — `02-sdd`, `02-design`, `04-build-*`, `04-test-*`, `05-tests`, `06-spec`?
2. Если ДА — **ты ОБЯЗАН вызвать `agent()`, а не делать inline.**
3. Если вместо этого ты начал читать MD-шаблоны или писать код — **СТОП**. Это баг.
   Закрой чтение. Вызови agent().

**Симптомы inline-ошибки (не допускать):**
- `read_file(".../SKILL.md")` для фазы 2, 3, 4, 5 → а потом пишешь файлы сам
- создание `sdd.md`, `tech-design.md`, `task-plan.json` через `write_file()` → должен субагент
- компиляция/запуск тестов через `run_shell_command` → должен субагент
- редактирование `*.java` файлов → должен субагент (`java-spring-dev`)

**Исключение:** если `agent` недоступен (tool error) — выполни inline как деградацию,
НО явно отметь это в ответе и запиши в pipeline-state `degraded: true`.

> **Контроль-плейн на хуках.** Подробности enforcement — см. `hooks/DEPLOY.md`.
> hooks = enforcement, SKILL.md = guidance.
>
> **Возвращай из субагентов JSON с полем `step_id`** — иначе
> `state-recorder` не пометит шаг.

---

## 0. Предусловия

### 0.0 Pre-flight: харнес реально активен? (САМЫМ ПЕРВЫМ)
Прежде чем что-либо делать — убедись, что control-plane включён (иначе гейты/risk/TDD/evidence
молчат, как на провальном прогоне с `0 hook entries`):
```bash
python3 <project>/.gigacode/hooks/preflight.py --project .
```
- **exit 0** — харнес активен, продолжай.
- **exit 1** — ENFORCEMENT OFF. **Остановись и предупреди пользователя**: хуки не срабатывают
  (нет блока `hooks` в settings / `disableAllHooks` / не задеплоено). Не веди пайплайн вслепую —
  сначала `deploy.sh` + `doctor.py`. Дальше только после подтверждения, что харнес поднят.

- Текущая директория — корень репо кода (Java/Spring, Gradle/Maven).
- Подключены MCP **Atlassian (Jira)** и **Bitbucket** — для фаз 2.5 и 6. Если их нет,
  пайплайн всё равно идёт в режиме «без Jira / до коммита» (см. гейты).
- Доступен скилл **`pipeline-state`** — без него нельзя резюмировать после обрыва (§0.5).
- Вложенные скиллы фаз: `brd-interview`/`business-requirements`, `system-analyst`,
  `tech-design`, `jira-task-writer`, `java-spring-dev`.

### 0.0a Динамический реестр фаз (вместо хардкода)

Фазы пайплайна определяются **не в этом SKILL.md**, а в `ground/pipeline.json` через скрипт
`resolve_phases.py` (аналог GrowthBook runtime feature gating из Claude Code).

**В начале работы** (после pre-flight, до всего остального) выполни:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/resolve_phases.py \
    --project <project> --feature <slug> --gates <project>/ground/feature-gates.json
```
Скрипт вернёт JSON с массивами `phases` (активные) и `skipped` (отключённые по условию).

**Как это работает:**
- Каждая фаза имеет поле `enabled_by` — путь к булеву полю в `pipeline.json` или `gates.*`.
  Если поле `false` — фаза пропускается (аналог `feature('XYZ')` из Bun).
- Поле `skip_if` — условие для пропуска при уже выполненном условии (например grounding уже есть).
- Поле `gates` — какие гейты требуют подтверждения пользователя.

**Правило:** оркестратор **НЕ** перечисляет фазы хардкодом. Вместо этого он итерирует
по массиву `phases` из resolve_phases.py, вызывая для каждой соответствующий субагент/скилл.
Если resolve_phases.py недоступен — используй манифест шагов из §0.5 как fallback.

**Переопределение фаз:** можно добавить секцию `phases_override` в `pipeline.json`,
чтобы переопределить `enabled_by`/`skip_if`/`gates` для конкретной фазы или добавить
новую (например `security-review`). Пример:
```json
"phases_override": [
  {"id": "02-eval-plan", "enabled_by": false},
  {"id": "05.5-security", "skill": null, "enabled_by": "gates.security_review", "gates": ["security_approved"]}
]
```

### 0.1 Конфигурация проекта (делай это первым)

Все параметры конвейера живут в `<project>/ground/pipeline.json` — единый стор, который
путешествует с проектом. Полная схема и правила — [`references/config.md`](references/config.md).

1. Прочитай `<project>/ground/pipeline.json`.
2. **Если файла нет** — создай:
   ```bash
   python <project>/.gigacode/skills/feature-pipeline/scripts/init_pipeline_config.py
   ```
   Скрипт авто-детектит build-систему, модули, пакет, версии, инструмент миграций и кладёт
   незаполняемое в `_incomplete`.
3. **Пройди по `_incomplete`** — спроси у пользователя ровно эти поля (Jira-ключ, Bitbucket
   workspace/repo, инструмент миграций, нужен ли `git init`) и впиши в файл.
4. Дальше бери из конфига: `docs.*` (расположение артефактов, см. ниже),
   `quality.coverage_threshold`, `conventions.migration_tool`, `delivery.pr_strategy`,
   `project.default_branch`, `autonomy.*`. Не хардкодь эти значения в шагах — читай из конфига.

Если `project.is_git=false`, а пользователь хочет дойти до PR — предложи `git init` до фазы 6
(иначе ветки/stacked-PR и ключ `pipeline-state` не работают).

**Расположение документных артефактов `<docs_path>` (in-repo / separate-repo).**
Везде ниже `<docs_path>` = база документов, резолвится из `docs.*`:
- **in-repo** (дефолт): `<docs_path>` = `<project>/<docs.docs_path>` (обычно `<project>/docs`).
- **separate-repo**: `<docs_path>` = `docs.repo_path` (внешний репо спеки); subagent'ы и скрипты
  работают там через `git -C <docs_path>`.

Артефакты лежат под `<docs_path>/feature-pipeline/<slug>/` (brd/sdd/tech-design/task-plan)
и `<docs_path>/system-analysis/` (обзор + grounding). **Скрипты и хуки резолвят это
автоматически** (`skill_paths.docs_base` / `_project.docs_base`) — им docs-пути не передавай.
**Субагентам** же подставляй конкретный `<docs_path>` из конфига. Если пользователь говорит
«артефакты в другом репо» — пропиши `docs.mode=separate-repo` + `docs.repo_path` в конфиг
(детали — [`references/config.md`](references/config.md)). См. также [`skill-paths.json`](references/skill-paths.json).

### 0.2 Автоопределение Jira-конфига

После того как `jira.project_key` заполнен (пользователем или из конфига), выполни
автоопределение кастомных полей, типов задач и Agile-доски через Jira MCP.

1. Проверь `jira.auto_discovered` в `pipeline.json`. Если `true` — шаг пропускается.
2. Если `jira.enabled=false` или `jira.project_key=null` — пропусти.
3. Собери метаданные проекта через MCP-инструменты:
   ```
   jira_search_fields(keyword="")           # все кастомные поля
   jira_get_agile_boards(project_key=...)   # Agile-доски
   jira_search(...) по issuetype            # типы задач (можно через createmeta)
   ```
   Если MCP-инструментов нет — шаг пропускается (`auto_discovered` остаётся `false`).
4. Передай собранную мету в скрипт автоопределения:
   ```bash
   echo '<JSON-мета>' | python3 <project>/.gigacode/skills/jira-task-writer/scripts/jira_discover.py
   ```
   Формат входного JSON:
   ```json
   {
     "project_key": "KIDPPRB",
     "issue_types": [{"name": "Story", "subtask": false}, ...],
     "fields": [{"id": "customfield_11400", "name": "Epic Link"}, ...],
     "boards": [{"id": 27992, "name": "Развитие и поддержка КИД (sprint)", "type": "scrum"}, ...]
   }
   ```
5. После успешного прогона в `pipeline.json.jira` появятся:
   - `issue_type_story`, `issue_type_subtask`, `issue_type_epic`, `issue_type_bug`
   - `epic_link_field`, `epic_name_field`, `sprint_field`, `system_field` и др.
   - `board` с id, именем, шаблоном имени спринта
   - `auto_discovered: true`

   Если какие-то поля не найдены (нет в мете) — они не попадут в конфиг,
   и `jira-task-writer` будет использовать MCP-fallback для них.

## 0.5 Pipeline-state (резюмирование при обрыве)

Каждый прогон — пайплайн из шагов (см. манифест ниже). Если субагент упёрся в лимит или
процесс прервался — без сохранения state теряется всё сделанное.

**State намеспейсится ПО ФИЧЕ** (чтобы фичи не вытесняли друг друга):
`<project>/ground/statements/feature-pipeline/<feature>/`, где `<feature>` — slug фичи или
Jira-ключ (тот же, что папка `docs/feature-pipeline/<slug>/`). Все вызовы pipeline-state —
с `--feature <slug>`.

**В самом начале**, до вопросов и субагентов, посмотри, какие фичи уже в работе:
```bash
python <project>/.gigacode/skills/pipeline-state/scripts/read.py --skill feature-pipeline --list
```
- пусто (`no_state`) — свежий старт.
- есть `in_flight` фичи — покажи список и спроси: резюмировать одну из них (тогда дальше
  все вызовы с её `--feature`) / начать новую фичу / показать собранное. **Не вытесняй** чужой
  in-flight стейт молча.

Дальше проверяй/резюмируй конкретную фичу:
```bash
python <project>/.gigacode/skills/pipeline-state/scripts/read.py --skill feature-pipeline --feature <slug>
```

**Инициализируй state** (после входа и скоуп-чека §2, до первого субагента) — с `--feature <slug>`:
```bash
python <project>/.gigacode/skills/pipeline-state/scripts/init.py \
    --skill feature-pipeline --feature <slug> --steps '<...>' --context '{"feature":"<slug>","iteration":N}'
```
Манифест шагов:

| step-id | title | depends_on |
|---|---|---|
| `00-brd` | Discovery / BRD | — |
| `01-grounding` | System overview ensured | — |
| `02-sdd` | SDD specification (sdd.md) | `00-brd`, `01-grounding` |
| `02-design` | Tech design + task plan | `02-sdd` |
| `02-eval-plan` | Eval-plan generated (eval-plan.json) | `02-design` |
| `03-jira` | Jira issues created | `02-design` |
| `04-test-<taskId>` | TDD RED: тесты компилируются и падают | `02-design` |
| `04-build-<taskId>` | TDD GREEN: код зеленит тесты задачи | `04-test-<taskId>`, `02-eval-plan` |
| `05-tests` | Полный прогон + coverage | все `04-build-*` |
| `06-spec` | Spec updated | `05-tests` |
| `07-deliver-<taskId>` | Ветка+коммит+stacked PR задачи | `05-tests`, `06-spec` |
| `07-report` | Отчёт в Story | все `07-deliver-*` |

`04-test-*`, `04-build-*` и `07-deliver-*` добавляются после фазы 2 через
`feature-pipeline/scripts/add_steps.py` (см. §5),
когда известна разбивка задач. (При `quality.tdd: false` шаг `04-test-*` опускается.)

> `--context` поля `feature` (slug/Jira-ключ) и `iteration` — по ним хук `agent-logger`
> группирует живые логи в `ground/ai-logs/<feature>/iter-NN/`.
После каждого завершённого субагента/шага — `update.py --skill feature-pipeline --feature <slug>
--step-id <id> --status completed` с его JSON. Для шагов, создающих файловые артефакты
(02-sdd, 02-design, 02-eval-plan, 03-jira), обязательно передавай `--artifacts '{"key":"path"}'`.
Перед синтезаторами/дизайнером — выжимки через
`--excerpt-of` (тоже с `--feature`). Не храни в state секреты и сами MD-файлы.
Хуки (`gate-guard`/`phase-gate`) сами находят АКТИВНУЮ фичу как самый свежий манифест — отдельно
передавать им ничего не нужно.

### 0.6 Правило ре-итерации (режим исправления после judge FAIL)

Если любой judge (brd/eval/red/build/reuse/spec/delivery) вернул FAIL — **НЕ прави артефакты inline**
в основном агенте. Используй **perpetual error store**:

**Файл:** `ground/statements/feature-pipeline/<slug>/judges/errors.json`

Автоматически создаётся `run_judge.py` при каждом FAIL и удаляется при PASS.

**Алгоритм при каждом judge FAIL:**

1. `run_judge.py <phase> <slug>` (уже exit 1 и сохранил blocking_issues в errors.json)
2. **Прочитай errors.json** для получения `accumulated_errors` и счётчика `iterations`
3. Если `len(iterations) >= 3` — **спроси пользователя** (три варианта): «Попыток больше нет.
   (a) сбросить errors.json и начать заново; (b) отменить шаг; (c) пропустить гейт вручную
   с обоснованием (override, см. §0.6.1) — выбирай (c) только если причина FAIL внешняя и
   не устранима правкой артефактов (нет тестовой БД, внешний сервис недоступен и т.п.)»
4. Если `< 3` — сформируй промпт для повторного субагента:
   ```
   **⚠️ Ошибки предыдущих прогонов (из errors.json):**
   - <accumulated_errors[0]>
   - <accumulated_errors[1]>
   ...

   НЕ повторяй эти ошибки. Проверь, что каждая из них исправлена.
   iteration=NN из 3 max.
   ```
5. **Запусти субагента той же фазы повторно** с blocking issues в промпте.
   Субагент НЕ пишет артефакты с нуля — он **исправляет** ошибки из списка.
6. Запусти judge снова (PASS → закрыть шаг; FAIL → loop на шаг 2)

**После judge PASS:** errors.json автоматически удаляется, ошибки считаются исправленными.

> **Почему это правило:** на прошлых прогонах пайплайн делал inline-правку после judge FAIL,
> что приводило к пропуску TDD-цикла, нарушению изоляции и потере контекста при обрыве
> сессии. Error store решает все три проблемы.

### 0.6.1 Ручной пропуск гейта (override) — последнее средство

Иногда судья падает по причине, которую **нельзя устранить правкой артефактов**: нет тестовой
БД в окружении, внешний сервис недоступен, acceptance намеренно ослаблен по согласованию.
Тогда пользователь может разрешить пропуск гейта. Это работает для **любого** судьи на
**любом уровне** (brd/eval/red/build/reuse/coverage/spec/delivery).

**Когда применять:** только после исчерпания 3 ре-итераций (§0.6) и **только с явного
согласия пользователя**. Не предлагай override на первом FAIL — сначала чини.

**Шаг 1.** Создай override-файл (`--reason` обязателен — это аудит-след):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/override_judge.py --judge <judge-name> --feature <slug> --step-id <step-id> --reason "<почему пропуск допустим>"
```

**Шаг 2.** Закрой шаг как обычно — `update.py` увидит override, пропустит блокировку и
запишет предупреждение в `step.override_warnings` манифеста (для аудита):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py --skill feature-pipeline --feature <slug> --step-id <step-id> --status completed
```

**Просмотр и снятие override:**
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/override_judge.py --feature <slug> --list
python3 <project>/.gigacode/skills/pipeline-state/scripts/override_judge.py --judge <judge-name> --feature <slug> --remove
```

> Override **не подделывает вердикт судьи** — FAIL остаётся в `judges/<judge>.json`. Override
> лишь снимает блокировку закрытия шага и фиксирует, кто и почему её снял. Частичный случай:
> если на шаге два судьи (напр. `build-judge`+`reuse-judge`), override нужен на каждый
> упавший — без override любой оставшийся FAIL по-прежнему блокирует.

---

## 1. Архитектура: кто что делает

| Фаза | Исполнитель | Механизм | Гейт |
|---|---|---|---|
| Конфиг, чтение Jira-входа | главный агент | — | — |
| Скоуп-чек | главный агент | — | — |
| 0 Discovery (BRD) | интервью inline + BRD-писатель | вложенный скилл (интервью) + субагент (черновение) | **Гейт 1** |
| 1 Grounding | `system-analyst` (если нет обзора) | оркестратор-субагентов | — |
| 2 SDD (спецификация) | `sdd` | субагент general-purpose (контракт §4.0a) | **Гейт SDD** |
| 2 Design (вход — `sdd.md`) | `tech-design` | вложенный скилл/субагент | **Гейт 2** |
| 2.5 Jira | `jira-task-writer` | субагент general-purpose (контракт §4.5) | **Гейт 3** |
| 3 Build (per task) | `java-spring-dev` + changeset | вложенный скилл | — |
| 4 Verify | тестописатель + тестраннер | субагенты general-purpose | — |
| 5 Document | спецадаптер | субагент general-purpose | — |
| 6 Deliver (per task, stacked) | главный агент | Bitbucket/Jira MCP | **Гейты 4-6** |

**Вложенный скилл vs субагент:** скилл загружается в контекст главного агента (тесная
интеракция, может задать вопрос); субагент работает изолированно и возвращает JSON
(тяжёлый вывод — gradle, JaCoCo, сканы). Не передавай субагентам всю историю разговора —
только нужный контракт фазы (см. `references/contracts.md §6`).

> **Субагент = ЯВНЫЙ вызов тула `agent`, не «сделай сам».** Где фаза помечена «субагент»,
> ОБЯЗАТЕЛЬНО вызови тул, а не выполняй работу inline:
> ```
> agent(
>   subagent_type="general-purpose",
>   description="<кратко: что за шаг>",
>   prompt="<контракт фазы + конкретные пути/задача>"
> )
> ```
> **Контракт фазы достаём так** (НЕ читай весь `subagent-prompts.md` — это ~13K токенов):
> ```
> python <scripts>/get_prompt.py <§>   # печатает только нужную секцию, напр. 4.0, 7.3
> ```
> где `<scripts>` — `skills.feature-pipeline.scripts` из `skill-paths.json`. Дальше в фазах
> «контракт §X.Y» = `get_prompt.py X.Y` + подстановка путей/задачи.
> Субагент вернёт JSON со своим результатом и полем `step_id` (его подхватит хук `state-recorder`).
> Inline-выполнение субагентной фазы — это ошибка: теряется изоляция контекста и устойчивость
> (большой единый контекст чаще ловит обрыв стрима). Если `agent` реально недоступен в рантайме —
> выполни inline, но ЯВНО отметь это в логе/ответе как деградацию.

**Два типа гейтов.** «Гейт 1-6» — точки подтверждения пользователем (необратимое не
делается без «да»). Отдельно у каждой фазы есть **детерминированный execution-gate**
(Python), который проверяет, что фаза реально отработала, ДО закрытия её шага в
pipeline-state: sdd→`check_sdd_doc.py`, design→`check_taskplan.py`+`check_sdd.py`, eval-plan→`build_evals_from_design.py`
(сама генерация, без gate — ошибка только если скрипт упал),
jira→`check_jira.py`, build→`check_build.py` (с дополнительным
**хуком `eval-guard`**, который проверяет прохождение eval'ов в рантайме),
tests→`check_coverage.py`, document→`enrich_grounding.py` (инкрементально),
deliver→`check_delivery.py`. Шаг не
закрывается, пока execution-gate не вернул `pass` (exit 0) — это ловит молчаливый
недосчёт/провал (как `verify_coverage.py` в grounding).

---

## 2. Вход и скоуп-чек

Определи вход:
- **Свободная идея** → фаза 0 через `brd-interview` (диалог) или `business-requirements`
  (быстро, без интервью — спроси, что предпочесть, если неочевидно).
- **Ключ Jira** (`STOR-123`, формат `[A-Z]+-\d+`) → прочитай issue через MCP, используй
  summary/description как затравку BRD. Если это эпик/стори с готовым описанием — BRD
  можно собрать из неё, подтвердив с пользователем.

### Скоуп-чек
Это пайплайн для **одной фичи за прогон**. Останови и спроси, если:
- в идее несколько независимых фич («и ещё», «а также», разные подсистемы);
- это явно тянет на несколько релизов / эпик;
- требование — на самом деле дефект (тогда уместнее `minor-defect-fix`).

Покажи причину сомнения и спроси, продолжать ли или разбить.

---

## 3. Фаза 0 — Discovery (BRD) → Гейт 1

BRD-фаза **двухстадийная**: интервью ведётся inline (субагент не умеет спрашивать
пользователя), а многословное черновение `brd.md` уходит в субагент — чтобы не засорять
контекст оркестратора черновым текстом, который потом тащится через все фазы.

**Стадия 1 — интервью inline (сбор ответов, НЕ черновение).**
Прочитай инструкции и веди диалог по ним (выбери один вариант):
- интерактивное интервью: `read_file("<project>/.gigacode/skills/brd-interview/SKILL.md")` (путь: `skills.brd-interview.skill_md`)
- быстрый сбор требований: `read_file("<project>/.gigacode/skills/business-requirements/SKILL.md")` (путь: `skills.business-requirements.skill_md`)

Если выбор не очевиден — спроси пользователя, какой формат предпочесть. Используй файл
**только как источник вопросов**: задай 3-7 уточняющих вопросов по одному, собери ответы.
**Не пиши полный BRD inline** — это работа субагента (Стадия 2). `<slug>` — kebab-case по сути фичи.

**Стадия 2 — черновение в субагенте.** Передай собранные ответы интервью BRD-писателю
(контракт: `get_prompt.py 4.0`). Он напишет `brd.md` в `docs/feature-pipeline/<slug>/`
и вернёт только путь + саммари (допущения, открытые вопросы) — без текста BRD в контексте:
```
agent(subagent_type="general-purpose", description="draft BRD for <slug>",
      prompt="<вывод `get_prompt.py 4.0`; подставь: slug, Jira-ключ, идею, ответы интервью, grounding>")
```
Если субагент вернул `pending_questions` — задай их пользователю и перезапусти его с `answers`.

**Ключ Jira (почти всегда есть).** Если фича пришла из Jira — передай issue-ключ (`[A-Z]+-\d+`)
BRD-писателю (§4.0), чтобы он встал ПЕРВОЙ строкой шапки `brd.md` (`**Jira:**`). Этот ключ протягивается
дальше: `sdd` ставит его в шапку `sdd.md`, `tech-design` — в `tech-design.md`. Если задача не из Jira —
строку опускаем во всех документах.

### Judge-gate: brd-judge (обязательно, ДО Гейта 1)

**Сразу после создания `brd.md`, до показа Гейта 1** запусти brd-judge — он гарантирует, что БТ
написаны языком бизнеса, а не как спецификация с кодом. Гибрид: LLM-субагент (стиль) +
детерминированная проверка код-токенов.

Шаг 1 — LLM-субагент (контракт: `get_prompt.py 7.6`):
```
agent(subagent_type="general-purpose", description="brd-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.6` (brd-judge) + путь к brd.md>")
```
Шаг 2 — ингест вердикта субагента и детерминированная проверка код-токенов:
```
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py brd <slug> \
  --from-output <verdict.json> --project-root <project>
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py brd <slug> \
  --recheck --project-root <project>
```
Оба должны быть PASS (exit 0). Вердикт — в `judges/brd-judge.json`.

**FAIL → ре-итерация** (раздел 0.6): ошибки в `errors.json`, верни BRD-скилл на доработку
(перепиши требования языком бизнеса, удали код-детали — они идут в SDD/`tech-design`, не в BRD).
Не закрывай `00-brd`, пока brd-judge не PASS.

**Важно:** brd-judge — это gate, а не опция. Если `brd.md` создан, но brd-judge не запущен —
шаг `00-brd` не закрывается (его `required_judges: ["brd-judge"]`).

**Гейт 1.** Покажи вердикт brd-judge (PASS/предупреждения), 2-3 ключевых допущения и самый критичный открытый вопрос из раздела BRD «Открытые вопросы» (номер раздела зависит от шаблона — ссылайся по названию).
Спроси: «утверждаем BRD / доработать?». Дальше — только после «да». Обнови `00-brd` (`gates: {"brd-judge": "PASS"}`).

### Гейт критичности (ОБЯЗАТЕЛЬНО, сразу после утверждения BRD)

Спроси у пользователя **критичность фичи** (`ask_user_question`) — это задаёт, насколько агрессивно
форсятся гейты. Без выбора `gate-guard` заблокирует любое R2+ действие (это и форсит шаг — на прошлых
прогонах его пропускали).

| Критичность | Что это | `auto_max_risk` | Поведение гейтов |
|---|---|---|---|
| **Низкая** | эксперимент, non-prod, внутр. инструмент | `R2` | фичекод авто; гейтятся доставка и R3+ пути (auth/PII/инфра) |
| **Средняя** | обычная прод-фича (дефолт) | `R1` | commit/push/jira/секьюрные пути — под гейтами |
| **Высокая** | auth / платежи / PII / инфра / критичный путь | `R0` | почти всё требует подтверждения/approval/evidence |

После ответа **запиши в `ground/pipeline.json`** блок `autonomy`:
```json
"autonomy": { "criticality": "<low|medium|high>", "auto_max_risk": "<R2|R1|R0>" }
```
(Можно через `init_pipeline_config.py` он уже есть, либо допиши поле.) Только теперь иди дальше —
`gate-guard` читает `autonomy.auto_max_risk` из конфига и применяет порог per-feature.

---

## 4. Фаза 1 — Grounding

**Сначала детерминированно проверь, есть ли grounding (НЕ повторяй его снова и снова):**
```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/check_grounding.py --root . --json
```
- **exit 0 (есть)** — переиспользуй найденный обзор, `system-analyst` НЕ запускай. Если `kind=scan`
  или `overview` без `grounding-excerpt.json` — собери выжимку (project-grounder §4). **Не спрашивай и не
  пересканируй.**
- **exit 1 (нет)** — только тогда прочитай инструкции и запусти полный обзор:
  ```
  read_file("<project>/.gigacode/skills/system-analyst/SKILL.md")
  ```
  У него свой цикл и свой гейт коммита спеки. После завершения — grounding готов.

Свежесть между фичами поддерживается инкрементально в фазе Document (`enrich_grounding.py`), поэтому
полный рескан на каждом прогоне не нужен.

**Гейт Grounding (ОБЯЗАТЕЛЬНО, перед переходом к спецификации).**
Спроси у пользователя: «Обзор системы (grounding) собран и актуален. Переходим к
спецификации (SDD)?». Только после «да». Если grounding не собран — НЕ переходи к §5a,
выполни полный обзор через `system-analyst` (см. exit 1 выше).

Обнови `01-grounding` (completed, в output — `path`/`excerpt_path` из вердикта).

Этот контекст нужен дизайнеру: модули, существующие сущности, API, схема БД.

---

## 5. Фаза 2 — Спецификация (SDD) и Дизайн

**🚨 ОБЕ подфазы — ОБЯЗАТЕЛЬНО через agent(). Не делай inline.**

Цепочка: **BRD → SDD (§5a) → Tech Design (§5b)**. Сначала субагент `sdd` пишет
строгую спецификацию `sdd.md` из BRD; после её утверждения субагент `tech-design`
проектирует **по `sdd.md`** (не по BRD напрямую) и выдаёт `tech-design.md` + `task-plan.json`.

### 5.0 Preflight-validate перед запуском (обязательно)

Перед вызовом agent() для каждой подфазы — проверь, что предыдущий шаг был сделан субагентом:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/preflight-validate.py \
    --project <project> \
    --feature <slug> \
    --step-id <id>
```
- **exit 0** — можно вызывать agent()
- **exit 1** — СТОП. Предыдущий шаг был сделан inline. Не продолжай, пока не исправлено.

---

### 5a. Фаза 02-sdd — SDD спецификация → Гейт SDD

**🚨 ОБЯЗАТЕЛЬНО через agent(). Не пиши `sdd.md` сам.**

Запусти субагента SDD-писателя по контракту `get_prompt.py 4.0a`. НЕ читай
`sdd/SKILL.md` в свой контекст — субагент прочитает его сам.
```
agent(
  subagent_type="general-purpose",
  description="Write SDD spec for <slug>",
  prompt="<вывод `get_prompt.py 4.0a`; подставь: slug, пути к brd.md и grounding-excerpt.json, Jira-ключ>"
)
```

**Обработка результата субагента (мини-интервью по неясностям).** Распарсь JSON:
1. **Если есть `pending_questions`** (`status: needs_input`) — задай каждый вопрос
   пользователю через `ask_user_question`, собери ответы. Перезапусти субагента SDD,
   передав `answers` на эти вопросы (sdd.md ещё НЕ написан — gate не гоняем). Повторяй,
   пока `pending_questions` не опустеет.
2. **Когда `status: completed`** (неясностей нет) — субагент написал `sdd.md`; иди к
   execution-gate ниже.

После того как субагент вернул `completed`, прогони детерминированный execution-gate:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py sdd <slug> --project-root <project>
```
- gate fail → верни субагента на доработку (допиши недостающие секции/сценарии Given-When-Then).
- gate pass → **Гейт SDD** (см. ниже).

**Гейт SDD — утверждение спецификации.** Покажи резюме SDD: суть фичи, ключевые сценарии
(включая ошибочные ветки), затрагиваются ли новые API/данные, главный риск. Спроси:
«утверждаем спецификацию / правки?».
- Правки SDD → верни `sdd` на доработку (BRD не трогаем).
- Если всплыло **новое бизнес-требование** → откат к фазе 0 (BRD).

После «да» обнови `02-sdd` (только при `pass` execution-gate), передав артефакт:
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 02-sdd --status completed \
    --artifacts '{"sdd": "docs/feature-pipeline/<slug>/sdd.md"}'
```

---

### 5b. Фаза 02-design — Tech Design → Гейт 2

**🚨 ОБЯЗАТЕЛЬНО через agent(). Не делай inline.** Вход — утверждённый `sdd.md` (§5a).

#### 5b.0 Pre-design: подготовка компактного data-context

До вызова субагента tech-design сгенерируй **design-context.json** — отфильтрованную
выжимку из grounding-excerpt.json, содержащую только релевантные entities, API-endpoints,
Kafka-топики и таблицы БД для затронутых модулей. Это снижает размер контекста с ~2840
до 50-200 строк и предотвращает проектирование дублирующих сущностей.

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/prepare_design_context.py \
    --brd "<папка фичи>/brd.md" \
    --task-plan "<папка фичи>/task-plan.json" \
    --grounding "<project>/docs/system-analysis/grounding-excerpt.json" \
    --out "<папка фичи>/design-context.json"
```

Если `task-plan.json` ещё не существует (до дизайна), скрипт определит модули по
ключевым словам из BRD. Если и BRD не даёт модулей — будет включено всё (без потери),
что безопасно для pre-design.

Полученный `design-context.json` передаётся в контракт субагента ниже.

#### 5b.1 Запуск субагента tech-design

Вызови agent() со следующим контрактом. НЕ читай SKILL.md тех-дизайнера сам — субагент прочитает.

```
agent(
  subagent_type="general-purpose",
  description="Tech Design for <slug>",
  prompt="""Ты — техлид/архитектор в пайплайне feature-pipeline.

Шаг 0: Прочитай `<project>/.gigacode/skills/tech-design/SKILL.md` целиком.

Вход:
- SDD (спецификация — ОСНОВНОЙ вход): <путь к sdd.md>
- Design context (компактная выжимка grounding под фичу): <путь к design-context.json>
- Grounding (полный — для редких справок): <путь к grounding-excerpt.json>
- BRD (первоисточник, только как справка): <путь к brd.md>

Шаг 1: Проектируй ПО sdd.md и design-context. К grounding-excerpt.json обращайся
        только если design-context не содержит нужной информации. BRD — лишь справка.
Шаг 2: Создай ДВА файла в <папка фичи>/ (sdd.md уже написан на фазе 02-sdd — НЕ трогай его):
  1. tech-design.md — по шаблону `<project>/.gigacode/skills/tech-design/references/design-template.md`
  2. task-plan.json — по шаблону `<project>/.gigacode/skills/tech-design/references/task-plan-schema.md`
     Каждая задача: непустой acceptance (Given-When-Then) + sdd_ref на раздел sdd.md.

Gate (обязательно, перед завершением):
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py design <slug> --project-root <project>
  Должен быть PASS (check_taskplan + check_sdd-линковка). Сохраняет вердикт в judges/design-judge.json.

Выходной JSON:
  {"step_id": "02-design", "status": "completed", "path": "...", "gates": {"design-judge": "PASS"}}
"""
)
```

#### 5b.2 Получение результата

После возврата субагента:
1. Прочитай результат (JSON с полем `step_id`, `path`, `gates`)
2. Прогони execution-gates:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py design <slug> --project-root <project>
```
3. Если gates fail — скажи пользователю, верни субагента на доработку.
4. Если gates pass — покажи **Гейт 2** (см. ниже).

#### 5b.3 Гейт 2 — утверждение дизайна

Покажи резюме: затронутые модули, новые/изменяемые сущности, нужны ли
миграции, число задач, главный риск. Спроси: «делаем так / правки?».
- Правки дизайна → верни `tech-design` на доработку (SDD и BRD не трогаем).
- Если правка по сути меняет **спецификацию** (новый сценарий/контракт) → откат к §5a (SDD).
- Если на гейте всплыло **новое бизнес-требование** → откат к фазе 0 (BRD).

После «да»: добавь в манифест шаги `02-eval-plan` (Eval-Driven),
`04-test-<taskId>` (RED, при `quality.tdd:true`),
`04-build-<taskId>` (depends_on `04-test-<taskId>` и `02-eval-plan`) и
`07-deliver-<taskId>` по `task-plan.tasks` скриптом
`<project>/.gigacode/skills/feature-pipeline/scripts/add_steps.py --skill feature-pipeline
--feature <slug> --steps '<...>'`
(идемпотентно, манифест руками не правь). **Используй именно версию из
`feature-pipeline/scripts/` — она безусловно пересобирает И `gate.json`, И `phase-defs.json`
(фазовую машину). Версию из `pipeline-state/scripts/add_steps.py` здесь НЕ применяй: судей
`required_judges` она проставляет (паритет), но `phase-defs.json` не пересобирает, а `gate.json`
обновляет лишь при его наличии — для новой фичи этого недостаточно.**

> **🚨 Сохраняй регистр task-id из task-plan в id шагов.** Если задача в `task-plan.json` —
> `T1`, то шаги должны быть `04-test-T1`, `04-build-T1`, `07-deliver-T1` (а не `...-t1`).
> Иначе гейты (`check_delivery.py` и др.) не сопоставят шаг с задачей. Деттерминированные
> гейты сопоставляют суффикс регистронезависимо как страховку, но не полагайся на это —
> пиши id ровно как task-id.

Обнови `02-design` только при `pass` execution-gates, передав артефакты:

```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 02-design --status completed \
    --artifacts '{
        "tech-design": "docs/feature-pipeline/<slug>/tech-design.md",
        "task-plan": "docs/feature-pipeline/<slug>/task-plan.json"
    }'
```

### 5c. Eval-Driven Development: генерация eval-plan (PDLC v3.5)

**Сразу после утверждения Гейта 2 и до манифеста:** сгенерируй `eval-plan.json`
из `task-plan.json`. Eval'ы — детерминированные автоматические проверки, которые
пишутся ДО кода и форсят Eval-Driven Development: агент не может записать файл в
`src/main/`, пока eval'ы его задачи не пройдены.

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/build_evals_from_design.py \
    "<папка фичи>/task-plan.json" \
    --pipeline-config "<project>/ground/pipeline.json" \
    --coverage-script "<project>/.gigacode/skills/minor-defect-fix/scripts/check_coverage.py"
```

Скрипт генерирует для каждой задачи три типовых eval'а:
- **compile** — проверка, что проект компилируется
- **coverage** — проверка JaCoCo покрытия через `check_coverage.py` (инкрементально по diff задачи; база зафиксирована `HEAD~1`)
- **test_pass** — бинарный регресс-гейт: вся тест-сюита зелёная (exit 0) после задачи, без порога и без скоупа на задачу

Пороги берутся из `pipeline.json quality.*`. Результат — `<папка фичи>/eval-plan.json`.

> **Eval'ы — это не опциональные тесты.** Хук `eval-guard` блокирует запись кода,
> пока eval'ы задачи не пройдены (PreToolUse-хук). Если eval-plan не сгенерирован —
> блокировка не срабатывает (fail-open), но это деградация: без eval-plan пайплайн
> теряет Eval-Driven гарантию качества и работает как обычный TDD-пайплайн.

**Конфигурация eval в `pipeline.json`:**
```json
"quality": {
    "eval_enabled": true,
    "eval_threshold": 0.95,
    ...
}
```
По умолчанию `eval_enabled: true`. Отключить можно установкой `eval_enabled: false`
(например, для экспериментов или прототипов).

### Опциональный гейт: трассируемость (`quality.traceability_check`)

Если `quality.traceability_check: true` в `pipeline.json` (по умолчанию `false`) — сразу после
генерации eval-plan и ДО закрытия `02-eval-plan` прогони детерминированную проверку матрицы
«требование → SDD → задача → eval → тест»:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_traceability.py \
    "<папка фичи>/task-plan.json"
```
- **exit 0** — цепочка замкнута, продолжай.
- **exit 2** — есть задача без eval / битый `sdd_ref` / пустой acceptance → почини task-plan/eval-plan
  и перезапусти. (`--strict` валит и на warnings.)

### Judge-gate: eval-judge (обязательно, перед закрытием `02-eval-plan`)

**Сразу после генерации eval-plan, ДО того как начинается код**, запусти eval-judge.
Он проверяет, что eval'ы покрывают все acceptance criteria, пороги адекватны, нет дубликатов.

Запусти субагента eval-judge (контракт: `get_prompt.py 7.1`):
```
agent(subagent_type="general-purpose", description="eval-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.1` (eval-judge) + пути к task-plan.json и eval-plan.json>")
```

Затем выполни детерминированную проверку через run_judge.py:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py eval <slug>
```

- **exit 0** — `passed: true` → шаг `02-eval-plan` можно закрывать (с `--artifacts`):

  ```bash
  python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
      --skill feature-pipeline --feature <slug> \
      --step-id 02-eval-plan --status completed \
      --artifacts '{"eval-plan": "docs/feature-pipeline/<slug>/eval-plan.json"}'
  ```
- **exit 1** — `passed: false` → покажи blocking_issues пользователю, НЕ закрывай шаг.
  Верни eval-judge на доработку (перегенерируй eval-plan или task-plan).
- **exit 2** — техническая ошибка (нет файлов) → остановись и разберись.

**Важно:** eval-judge — это gate, а не опция. Если eval-plan сгенерирован, но eval-judge
не запущен или вернул FAIL — шаг `02-eval-plan` НЕ закрывается. Это блокирует переход
к Build (RED) и предотвращает ситуацию «eval'ы есть, но никто не проверил их качество»
(дефект #5 из KIDPPRB-8639).

Вердикт сохраняется в `ground/statements/feature-pipeline/<slug>/judges/eval-judge.json`.

---

## 6. Фаза 2.5 — Jira → Гейт 3

**🚨 ОБЯЗАТЕЛЬНО через `agent()`. Не делай inline.** Как фаза Design (§5): прочитав
`jira-task-writer/SKILL.md` в свой контекст и «выполнив его сам», ты запустишь его inline —
тогда цикл `pending_questions` не отработает и **вопрос про Epic потеряется** (это уже
случалось). Подробную MCP-логику читает САМ субагент, не ты.

**Второе правило: субагент НЕ вызывает `ask_user_question`.** Он собирает черновик и
возвращает JSON с `pending_questions` (Epic, спринт). Все вопросы пользователю задаёшь ТЫ.

НЕ читай `jira-task-writer/SKILL.md` в свой контекст — субагент прочитает его сам.
Запусти субагента по контракту `get_prompt.py 4.5` (он сам прочитает task-plan.json,
brd.md, pipeline.json по путям):
```
agent(subagent_type="general-purpose",
      description="Jira tasks for <slug>",
      prompt="<вывод `get_prompt.py 4.5`; подставь: пути к task-plan/brd/pipeline.json, slug>")
```

### Обработка результата субагента

Субагент возвращает JSON в `llmContent`. Распарсь его:

1. **Если есть `pending_questions`** — задай каждый вопрос пользователю через
   `ask_user_question` (по одному, последовательно). Собери ответы.
   Перезапусти субагента, передав ему `answers` на `pending_questions`.
   Повторяй, пока `pending_questions` не опустеет.

2. **Когда `pending_questions` пуст** — покажи черновик пользователю ЯВНО: Story + список
   Sub-task с их числом («Story + 4 подзадачи: T1…T4»). Спроси `ask_user_question`
   «Создавать эти задачи в Jira?» с вариантами:
   - «Да» — создавай **ровно показанный черновик** (см. шаг 4)
   - «Правки» — см. шаг 3 (цикл правок)
   - «Не создавать» — `skipped: true`

3. **На «Правки» — цикл, а не разовая реплика.** Уточни у пользователя, что изменить
   (например: «нужна 1 задача вместо 4», «объедини T2–T4», «переименуй Story», «убери
   подзадачи»). **Перезапусти субагента** с полем `revision: "<что изменить>"` и
   `confirmed: false`. Субагент вернёт НОВЫЙ черновик — **вернись к шагу 2** (покажи новый
   черновик, снова спроси Да/Правки/Не создавать). Зацикливай, пока не «Да» или
   «Не создавать».
   > **🚨 Никогда не создавай задачи, пока пользователь не подтвердил «Да» на ПОСЛЕДНЕМ
   > показанном черновике.** Создание идёт по подтверждённому черновику, НЕ по исходному
   > `task-plan.json`. Если пользователь сказал «нужна одна задача» — в Jira должна уйти
   > одна, даже если в task-plan их 4.
   > **Если меняется ЧИСЛО задач** (4→1) — это расхождение с дизайном. Рекомендуемый путь:
   > вернуться в `tech-design` (§5), поправить `task-plan.json` до нужной разбивки и заново
   > собрать Jira-черновик — тогда сойдётся и downstream TDD/Build (`04-test/build-<taskId>`),
   > и гейт `check_jira` (он требует паритет: 1 Story + по задаче на каждую запись task-plan).
   > Осознанное расхождение (Jira укрупнённо, task-plan детально) приведёт к FAIL `check_jira`
   > — тогда закрывай шаг только через ручной override (§0.6.1) с обоснованием.

4. **На «Да»** — создай Story и Sub-task **строго по последнему подтверждённому черновику**
   (не по сырому task-plan) через Jira MCP, следуя `references/jira-create-workflow.md`.
   Сохрани результат:
   ```bash
   python3 <project>/.gigacode/skills/jira-task-writer/scripts/check_jira.py \
       "<папка фичи>/task-plan.json" --result "<папка фичи>/jira-tasks-result.json" \
       --pipeline-config "<project>/ground/pipeline.json"
   ```

5. **На пустой ответ** (не отобразился вопрос) — не паникуй:
   - Запиши `jira-tasks-result.json` с `skipped: true` и причиной
   - Напиши пользователю: «Не получил ответа на вопрос о создании Jira-задач — пропущено.
     Если хочешь создать задачи позже, скажи "создай задачи по task-plan.json"»
   - Иди дальше в режиме «без Jira»

Результат — `jira-tasks-result.json` (`{story, tasks:{task_id→key}, skipped}`).
Обнови `03-jira` с артефактами:

```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 03-jira --status completed \
    --artifacts '{"jira-result": "docs/feature-pipeline/<slug>/jira-tasks-result.json"}'
```

---

## 7. Фаза 3 — Build (по задачам, **TDD: RED → GREEN**)

**🚨 ВСЕ шаги Build — через agent(). Оркестратор НЕ пишет код, НЕ правит файлы.**

По умолчанию (`pipeline.json quality.tdd: true`) каждая задача делается по TDD: **сначала тесты
(они падают), потом код, который их зеленит.** Иди по `task-plan.tasks` в порядке `depends_on`.

### 7.1 Per-task: RED (субагент-тестописатель)

> **ВАЖНО: TDD RED = тесты ОБЯЗАНЫ падать.** Если метод/класс уже частично реализован
> в кодовой базе — тесты должны падать на assert'ах времени выполнения (не компиляции):
> - проверять, что возвращаемое значение не соответствует ожидаемому (`assertNull`, `assertThrows`)
> - использовать разные сценарии (пустой список, неверный статус, умерший поток)
> - не использовать mock-стабы, которые «просто проходят» — mock должен верифицировать
>   НЕВЕРНОЕ поведение, которое будет исправлено в GREEN
>
> **Проверка RED:** `check_tests_red.py` — compile OK + test fail (exit code != 0).
> Если все тесты проходят (exit code 0) — это GREEN, не RED. Такой сценарий блокируется.

Для каждой задачи вызови agent() с контрактом тестописателя `get_prompt.py 4.1`:
```
agent(
  subagent_type="general-purpose",
  description="TDD red: tests for <taskId> in <slug>",
  prompt="<вывод `get_prompt.py 4.1`; подставь: taskId, slug, acceptance, tech-design сигнатуры>"
)
```

**После возврата субагента**, до стабов — выполни red-judge:
```
agent(subagent_type="general-purpose", description="red-judge for <taskId>",
      prompt="<вывод `get_prompt.py 7.2` (red-judge)>")
```
Затем:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py red <slug> --recheck
```

### 7.2 Per-task: стабы сигнатур (оркестратор)

После прохождения red-judge — запусти субагента для создания стабов. Контракт: `get_prompt.py 4.2`:
```
agent(
  subagent_type="general-purpose",
  description="Stubs for <taskId> in <slug>",
  prompt="<вывод `get_prompt.py 4.2`; подставь: taskId, slug>"
)
```

### 7.3 Per-task: GREEN — реализация (субагент java-spring-dev)

```
agent(
  subagent_type="general-purpose",
  description="GREEN: code for <taskId> in <slug>",
  prompt="<вывод `get_prompt.py 4.3` (полный контракт); подставь: taskId, slug, task-plan, tech-design>"
)
```

### 7.4 Judge-gate GREEN: build-judge

После возврата субагента — запусти build-judge:
```
agent(subagent_type="general-purpose", description="build-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.3` (build-judge)>")
```
build-judge — pass-through судья: его вердикт считает СУБАГЕНТ, run_judge сам ничего не
проверяет. Поэтому **сохрани JSON-вердикт субагента в файл и передай его через `--from-output`**
(иначе вердикта на диске не будет и шаг не закроется), затем подтверди `--recheck`:
```bash
# verdict.json — JSON, который вернул субагент build-judge ({"passed":..., "blocking_issues":[...]})
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py build <slug> --from-output verdict.json
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py build <slug> --recheck
```

И execution-gate:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_build.py "<папка>/task-plan.json" --task <taskId>
```

### 7.5 Judge-gate GREEN: reuse-judge (после build-judge, ДО закрытия шага)

После того как build-judge дал PASS — запусти reuse-judge (судья качества: нет велосипедов,
дублирующих доступные библиотеки/util проекта). Гибрид: LLM-субагент + детерминированный regex.

Шаг 1 — LLM-субагент (контракт: `get_prompt.py 7.7`):
```
agent(subagent_type="general-purpose", description="reuse-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.7` (reuse-judge) + git diff + путь к scan/reuse.json>")
```
Шаг 2 — ингест вердикта и детерминированная проверка велосипедов по git diff:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py reuse <slug> \
  --from-output verdict.json --diff-base <base> --project-root <project>
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py reuse <slug> \
  --recheck --diff-base <base> --project-root <project>
```
`<base>` — родительская ветка/коммит задачи (как в check_coverage). Если каталога
`scan/reuse.json` нет — он создаётся в фазе 1 (grounding) через project-grounder.

**FAIL → ре-итерация** (раздел 0.6): ошибки в errors.json, верни код java-spring-dev на
доработку — замени велосипед на библиотеку/util из каталога. Закрывай `04-build-<taskId>`
только когда **оба** судьи (build-judge И reuse-judge) PASS (`required_judges` шага — оба).

Обнови `04-build-<taskId>` (completed) при pass.

> Если `quality.eval_enabled: false` — хук пропускает eval-проверки.
> Если `quality.tdd: false` — допускается старый порядок.

---

## 8. Фаза 4 — Verify (полный прогон + покрытие)

**🚨 ЧЕРЕЗ agent(). Оркестратор НЕ гоняет тесты и НЕ читает JaCoCo сам.**

Оба шага — явный вызов `agent`:

### 8.1 Тестописатель (добор покрытия)

Контракт: `get_prompt.py 4.4`:
```
agent(
  subagent_type="general-purpose",
  description="Cover gaps for <slug>",
  prompt="<вывод `get_prompt.py 4.4`; подставь: slug, check_coverage отчёт>"
)
```

### 8.2 Тестраннер

Контракт: `get_prompt.py 4.1a` (секция Pre-commit):
```
agent(
  subagent_type="general-purpose",
  description="Run tests + coverage for <slug>",
  prompt="<вывод `get_prompt.py 4.1a` (Pre-commit); подставь: slug>"
)
```

### 8.3 Judge-gate coverage (обязательно, перед закрытием `05-tests`)

После тестраннера запусти coverage-judge — он гоняет `check_coverage.py` (JaCoCo) и
сохраняет вердикт в `judges/coverage-judge.json` (имя совпадает с `required_judges`
шага `05-tests`, иначе `update.py` не даст закрыть шаг):
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py coverage <slug> --recheck
```
- **exit 0** — закрой `05-tests` (status completed).
- **exit 1** — покрытие ниже порога → верни тестописателя на доработку (лимит 3).

После возврата — закрой `05-tests` при pass. При fail — верни тестописателя на доработку (лимит 3).

### 8.4 Опциональные детерминированные гейты verify (по флагам `pipeline.json`)

Гоняй ПОСЛЕ тестов, до закрытия `05-tests`. Оба по умолчанию `false`; включаются в `pipeline.json`.

- **Архитектура** (`quality.architecture_check: true`) — ArchUnit-lite гейт слоёв (package_root,
  чистота домена, запрет entity→service / controller→repository) статически по изменённым `.java`:
  ```bash
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_architecture.py \
      --root "<project>" --pipeline-config "<project>/ground/pipeline.json"
  ```
- **Тавтологичные тесты** (`quality.tautology_check: true`) — статический детектор пустых/
  тавтологичных тестов (`assertTrue(true)`, пустое тело, нет ассертов/verify):
  ```bash
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_tautological_tests.py \
      --root "<project>"
  ```
`exit 2` любого — blocking: почини нарушения и перезапусти (`--strict` ужесточает warnings).
`exit 0` — продолжай к закрытию `05-tests`.

---

## 9. Фаза 5 — Document

**🚨 ЧЕРЕЗ agent(). Оркестратор НЕ правит спеку и НЕ запускает enrich_grounding сам.**

### 9.1 Спецадаптер (agent)

Контракт: `get_prompt.py 5`:
```
agent(
  subagent_type="general-purpose",
  description="Update spec for <slug>",
  prompt="<вывод `get_prompt.py 5` (полный контракт); подставь: slug, docs_path, diff>"
)
```

### 9.2 Gate: enrich_grounding (детерминированно)

После спецадаптера выполни — **пересканирует код** по `--project-root`, пересобирает
`grounding-excerpt.json` из свежего scan (scan = источник истины: новые артефакты появляются,
удалённые выпадают) и инкрементально дополняет `docs/system-analysis/*.md`:
```bash
python3 .gigacode/skills/system-analyst/scripts/enrich_grounding.py \
    --task-plan "<папка фичи>/task-plan.json" \
    --project-root "<project>" \
    --feature "<slug>"
```

По умолчанию scan освежается по коду (`--no-rescan` отключает — нужно лишь если код
недоступен из cwd). `--system-analysis` и `--scan` НЕ передаём — скрипт сам резолвит их по
`docs.*` из `ground/pipeline.json` (in-repo или separate-repo).

Если `enrich_grounding.py` вернул non-zero (coverage не сошёлся) — нужен полный рескан через
`system-analyst` (см. фазу 1).

### 9.3 Judge-gate spec

```
agent(subagent_type="general-purpose", description="spec-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.4` (spec-judge) + slug + docs_path + task-plan>")
```
Затем:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py spec <slug> --recheck
```

Обнови `06-spec` только при pass.

---

## 10. Фаза 6 — Deliver (per-task, stacked) → Гейты 4-6

Полная механика веток и stacked-PR —
[`references/stacked-pr-delivery.md`](references/stacked-pr-delivery.md). Кратко:

- Каждая задача → своя ветка `feature/<jira-key>` (или `feature/<slug>-<taskId>` без Jira).
  Ветки **stacked**: ветка задачи ответвляется от ветки той, от которой она зависит
  (`depends_on`); корневые — от default-ветки.
- Коммит каждой задачи — только её файлы; сообщение в стиле проекта (`git log`),
  с ключом Jira, **без** `Co-Authored-By`.

**Judge-gate deliver: delivery-judge (перед Гейтом 4, до коммитов).**
Запусти delivery-judge перед тем, как показывать план коммитов. Он проверяет готовность
к доставке: нет stubs, Jira консистентна, нет секретов, git status чист.

```
agent(subagent_type="general-purpose", description="delivery-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.5` (delivery-judge) + task-plan + jira-result + git status + diff>")
```

delivery-judge — pass-through (вердикт считает субагент). Сохрани его JSON и передай
через `--from-output`, затем подтверди `--recheck`:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py delivery <slug> --from-output verdict.json
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py delivery <slug> --recheck
```

Результаты exit-кодов — как у eval-judge (§6): exit 0 = pass, exit 1 = покажи blocking_issues пользователю, exit 2 = техническая ошибка.

**Гейт 4 — коммиты.** Покажи план коммитов (какая задача → какие файлы → сообщение) по
всем задачам сразу. Спроси «коммитим?». Только после «да».

**Перед Гейтом 5 — детерминированный план доставки (идемпотентность, защита от дублей PR).**
Доставка необратима, а Bitbucket-PR не дедуплицируется: ре-ран после падения вслепую создаст дубль
ветки/PR. ДО push/PR посчитай из git-веток + manifest, что уже сделано:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/delivery_plan.py \
    "<папка фичи>/task-plan.json" \
    --manifest "<project>/ground/statements/feature-pipeline/<slug>/manifest.json" \
    --pipeline-config "<project>/ground/pipeline.json"
```
План даёт `create / resume / skip` на задачу: `skip` — `07-deliver-<id>` уже completed; `resume` —
ветка есть, шаг не закрыт (не пересоздавай ветку — доведи push/PR, проверив существующий PR);
`create` — с нуля. Действуй строго по плану.

**Гейт 5 — push + stacked PR.** Покажи план веток и PR (для каждого: source→target,
заголовок, тело со ссылкой на Jira). Спроси «пушим и создаём PR?». После «да» — push в
порядке зависимостей, затем PR через Bitbucket MCP (target = ветка-родитель или default).
Сюда же — push/PR ветки спеки (фаза 5).

**Гейт 6 — отчёт в Jira.** Подготовь черновик комментария в Story (что сделано, файлы,
тесты/покрытие, ссылки на PR по задачам, статус спеки). Покажи целиком, спроси «отправить?».
Отправляй только после «да».

**Gate перед закрытием доставки:**
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_delivery.py \
    "<папка фичи>/task-plan.json" \
    --manifest "<project>/ground/statements/feature-pipeline/<slug>/manifest.json" \
    --pipeline-config "<project>/ground/pipeline.json"
```
По закрытому `07-deliver-<id>` на каждую задачу (при bitbucket off — skip). Обнови
`07-deliver-*` и `07-report`.

Детали MCP-команд (Jira-комментарий, создание PR, workspace/repo) — общие с
`minor-defect-fix`: `<project>/.gigacode/skills/minor-defect-fix/references/{jira-workflow,bitbucket-workflow,coverage}.md`.

---

## Карта инструментов MCP

| Действие | Фаза | Что искать |
|---|---|---|
| Прочитать Jira issue (вход) | 2 | `*jira*get*issue*`, `*atlassian*issue*` |
| Создать Story/Sub-task | 2.5 | `*create*issue*` (через `jira-task-writer`) |
| Добавить комментарий | 6 | `*jira*add*comment*` |
| Создать PR | 6 | `*bitbucket*create*pull*request*` |

Точные имена зависят от сервера — **не угадывай**, проверь список доступных инструментов.

---

## Устойчивость: отказы, контекст, субагенты

**Политика отказа (не зацикливайся, не ломись напролом).** Если шаг не проходит:
- execution-gate падает повторно (напр. `check_coverage`/`check_tests_red`) — **лимит 3 попытки**,
  потом пометь шаг `failed` (`update.py --status failed --error ...`), **остановись и спроси** пользователя
  (что делать: снизить порог, помочь руками, отложить задачу). Не обходи гейт правкой порога молча.
- покрытие недостижимо без инфраструктуры — зафиксируй как ограничение, не выдумывай фейковые тесты.
- **никогда** `git push --force` / `git reset --hard` / правка манифеста руками, чтобы «протолкнуть».
- частичная доставка допустима только явно с пользователем (какие задачи доставляем, какие — нет).

**Гигиена контекста (против обрывов стрима).** Главный контекст не должен пухнуть:
- тяжёлый вывод (gradle/JaCoCo/сканы) — в **субагентах**, не в главном; передавай контракт, не историю.
- между фазами опирайся на `pipeline-state` (`read.py --excerpt-of`), не таскай полные выводы.
- если контекст близок к лимиту — сожми (выжимки шагов) перед следующей тяжёлой фазой.

**Probe субагентов.** Если `agent` в рантайме недоступен (субагент не стартует) — НЕ выполняй
субагентные фазы молча inline. Сделай работу inline как **деградацию**: явно отметь это, и
чекпойнти каждый микрошаг в `pipeline-state`, чтобы обрыв не терял прогресс.

**Синхронизация agent() и ask_user_question.** `agent()` и `ask_user_question` НЕ
должны быть активны одновременно в одном контексте. Если запущен субагент (`agent()`) —
не вызывай `ask_user_question` до его завершения. И наоборот: не запускай субагента,
пока ожидаешь ответа от пользователя. Все вопросы пользователю — строго до запуска
субагента или после получения его результата. Нарушение этого правила приводит к
race condition: ответ пользователя теряется, и оркестратор зацикливается (дефект #7
из KIDPPRB-8639).

**Делегированные вопросы субагента.** Если субагенту нужно что-то уточнить у
пользователя, он НЕ вызывает `ask_user_question` напрямую. Вместо этого он возвращает
в JSON-результате массив `pending_questions`:

```json
{
  "draft": { "...": "..." },
  "pending_questions": [
    {"id": "epic", "question": "К какому Epic привязать Story? Укажи ключ (например EPIC-123) или 'нет'."},
    {"id": "sprint", "question": "Добавить в спринт? Укажи ID спринта или 'нет'."}
  ]
}
```

Оркестратор после получения результата:
1. Читает `pending_questions`.
2. Для каждого вопроса вызывает `ask_user_question`.
3. **Передаёт ответы обратно субагенту:** запускает `agent()` повторно с теми же
   входными данными + поле `answers: {"epic": "EPIC-123", "sprint": "нет"}`.

Такой цикл может повторяться, пока `pending_questions` не опустеет. Главное правило
соблюдено: `agent()` и `ask_user_question` никогда не активны одновременно.

**Защита от пустого ответа ask_user_question.** Если `ask_user_question` вернул пустой
ответ (пользователь не ответил / ответ не доставлен) — повтори вопрос **не более 1 раза**.
Второй пустой ответ подряд означает, что пользователь не видит вопрос или не может
ответить. В этом случае:
1. Остановись.
2. Напиши пользователю текстовое сообщение: «Я задал вопрос, но не получил ответ.
   Пожалуйста, ответь на вопрос выше или напиши "продолжить" / "отменить", чтобы я
   двигался дальше.»
3. Не пытайся вызвать `ask_user_question` в третий раз — перейди к fallback-сценарию
   (пропуск шага с `skipped: true`, если это возможно, или остановка пайплайна).

---

## Что НЕ делать

- Не проскакивать гейты «молча» — создание задач, коммит, push, PR, отчёт требуют «да».
- Не вести несколько фич одним прогоном — один прогон = одна фича.
- Не передавать субагентам всю историю — только контракт фазы.
- Не писать спеку в репо кода и код в репо спеки (спецадаптер работает в `docs_path`).
- Не использовать `git push --force`, `git reset --hard` для обхода проблемы.
- Не запускать `system-analyst` на каждом прогоне — переиспользуй обзор.
- Не раздувать код сверх `task-plan` (лишние слои, абстракции, логирование).
- Не создавать Jira-задачи до Гейта 3 и не пушить до Гейта 5.

---

## Ссылки

- `references/{contracts.md,subagent-prompts.md,evidence-bundle.md,stacked-pr-delivery.md,migrations.md,config.md}` — дизайн, контракты и референсы пайплайна.
- `references/subagent-prompts.md` — промпты тестописателя, тестраннера, спецадаптера и судей.
  Достаём по одной секции через `scripts/get_prompt.py <§>` (не читаем файл целиком).
- `references/stacked-pr-delivery.md` — механика веток и stacked-PR (фаза 6).
- `references/migrations.md` — Liquibase changeset и случай отсутствия миграций.
- `../minor-defect-fix/references/` — общие jira/bitbucket/coverage воркфлоу.
