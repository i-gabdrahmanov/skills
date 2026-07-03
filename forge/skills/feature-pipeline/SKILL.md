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
> `state-recorder` не пометит шаг. В повторном (fix) прогоне после FAIL судьи — **тот же
> контракт и тот же `step_id`**, что и в первичном (см. §0.6), иначе fix-прогон не запишется.
>
> **Шаг закрывает явный `update.py --status completed` ПОСЛЕ PASS судьи — не `state-recorder`.**
> На `SubagentStop` рабочего субагента судья ещё не прошёл, поэтому авто-закрытие там
> детерминированно блокируется (by-design: `update._check_judges`). Закрытие делается один раз в
> конце фазы явной командой (см. бриф фазы, §3–10) и переживает любое число fix-раундов.
> Пропустишь — шаг застрянет в `in_progress`, и на ресьюме фаза прогонится заново.
>
> **Лимиты `ask_user_question` (любой гейт/вопрос оркестратора).** Иначе вызов падает на
> валидации и тратит round-trip (на прогоне №3 — 5 раз):
> - `header` — **≤ 12 символов** (это короткий тег-чип, не вопрос; вопрос кладётся в `question`);
> - `options` — **ровно 2–4** непустых варианта, у каждого непустой `description`;
> - нужен **свободный ответ** (например «опиши задачу в 2–3 предложениях») — `ask_user_question`
>   не подходит (он всегда требует 2–4 опции): задай вопрос обычным текстом и жди ответа.

---

## 0. Предусловия

### 0.0 Pre-flight: харнес реально активен? (САМЫМ ПЕРВЫМ)
Прежде чем что-либо делать — убедись, что control-plane включён (иначе гейты/risk/TDD/evidence
молчат, как на провальном прогоне с `0 hook entries`):
```bash
python3 <project>/.gigacode/hooks/preflight.py --project .
```
- **exit 0** — харнес активен, продолжай.
- **exit 1** — посмотри `errors`. Различай две причины (они требуют разного):
  - **Только** `ground/pipeline.json not found` (или `incomplete`) — это нормальный первый запуск.
    Инициализируй конфиг (§0.1) и **обязательно перезапусти preflight** — он должен стать **exit 0**
    до первого субагента.
  - Ошибки про `settings.json` / `hooks block empty` / `essential hook НЕ подключён` /
    `resolve_hook_paths` — это **ENFORCEMENT OFF**: хуки реально не срабатывают. **Остановись и
    предупреди пользователя**, сначала `deploy.sh` + `bash .gigacode/deploy-local.sh` + `doctor.py`.
    Дальше только после подтверждения, что харнес поднят.

> **ЖЁСТКИЙ ГЕЙТ АРМИНГА.** Не вызывай **ни одного `agent()`**, пока `preflight.py` не вернул
> **exit 0**. Хуки рантайм связывает при запуске сессии; если на старте было `enforcement off`,
> ранние субагентные фазы (02-sdd, 02-design, 03-jira, build) пройдут **без записи**
> `_origins/<step>.json` от SubagentStop → каждый шаг придётся закрывать через `override_judge.py`
> (шторм override на прогоне №3). Сначала PASS — потом субагенты.

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
новую (например `security-review`). Новая фаза (id вне базового списка) вставляется
сразу после фазы из ключа `after`, без него — в конец. Не правь JSON руками —
`config-helper` (`phase enable|disable|add … [--after ID]`). Пример:
```json
"phases_override": [
  {"id": "02-eval-plan", "enabled_by": false},
  {"id": "05.5-security", "skill": null, "enabled_by": "gates.security_review", "gates": ["security_approved"], "after": "05-verify"}
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

5. **Перезапусти preflight — гейт арминга.** Как только `_incomplete` пуст, **повторно** прогони
   `python3 <project>/.gigacode/hooks/preflight.py --project .` и убедись в **exit 0**. Это
   единственная проверка, что харнес действительно поднят, прежде чем пойдут субагенты. Пока не
   PASS — `agent()` не вызывай (см. жёсткий гейт в §0.0).

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
   jira_search("project=<KEY> ORDER BY updated DESC", limit=20)  # недавние issue — для КОНВЕНЦИЙ
   ```
   Последние ~20 issue нужны, чтобы создавать задачи **«в едином ключе»**: изучить типовые
   `components`, `labels`, родительский `epic` и стиль нейминга проекта (по ним `jira_discover`
   выведет `jira.conventions`). Из каждого issue возьми `summary`, `components`, `labels`, Epic Link.
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
     "boards": [{"id": 27992, "name": "Развитие и поддержка КИД (sprint)", "type": "scrum"}, ...],
     "issues": [{"summary": "[KID] …", "components": ["task-service"], "labels": ["kid"], "epic": "KIDPPRB-100"}, ...]
   }
   ```
5. После успешного прогона в `pipeline.json.jira` появятся:
   - `issue_type_story`, `issue_type_subtask`, `issue_type_epic`, `issue_type_bug`
   - `epic_link_field`, `epic_name_field`, `sprint_field`, `system_field` и др.
   - `board` с id, именем, шаблоном имени спринта
   - `conventions`: `common_components`, `common_labels`, `frequent_epic`, `summary_prefix` —
     **применяй их при создании задач** (jira-task-writer), чтобы Story/Sub-task были в едином ключе проекта
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
| `03-jira` | Jira issues created — УСЛОВНЫЙ: только при `jira.enabled=true` (по умолчанию false, resolve_phases фазу пропустит — тогда шаг в манифест не включай) | `02-design` |
| `04-test-<taskId>` | TDD RED: тесты компилируются и падают | `02-design` |
| `04-build-<taskId>` | TDD GREEN: код зеленит тесты задачи | `04-test-<taskId>`, `02-eval-plan` |
| `05-tests` | Полный прогон + coverage | все `04-build-*` |
| `06-spec` | Spec updated | `05-tests` |
| `07-deliver-<taskId>` | Ветка+коммит+stacked PR задачи | `05-tests`, `06-spec` |
| `07-report` | Отчёт в Story | все `07-deliver-*` |

`04-test-*`, `04-build-*` и `07-deliver-*` добавляются после фазы 2 через
`feature-pipeline/scripts/add_steps.py` (см. бриф `references/phases/02-design.md`),
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

1. `run_judge.py <phase> <slug>` — exit-коды: **1** = FAIL (чини и перезапусти), **3** = ESCALATE
   (лимит ре-итераций исчерпан — `run_judge` сам это форсит, см. шаг 3), **0** = PASS.
2. **Прочитай errors.json** для получения `accumulated_errors` и счётчика `iterations`
3. **Лимит ре-итераций форсится детерминированно:** при `iterations >= quality.max_judge_iterations`
   (дефолт 3) `run_judge` печатает `⛔ STOP` и возвращает **exit 3**. Получив exit 3 — **НЕ запускай
   судью снова и НЕ правь прод-код/существующие тесты ради зелёного**; **спроси пользователя** (три
   варианта): «Попыток больше нет. (a) сбросить errors.json и начать заново; (b) отменить шаг;
   (c) пропустить гейт вручную с обоснованием (override, см. §0.6.1) — выбирай (c) только если причина
   FAIL внешняя и не устранима правкой артефактов (нет тестовой БД, внешний сервис недоступен и т.п.)»
4. Если `< 3` — собери промпт повторного прогона как **тот же контракт фазы**
   (`get_prompt.py <§>` того же рабочего субагента, что и в первичном прогоне) **плюс** блок
   ошибок в конце:
   ```
   **⚠️ Ошибки предыдущих прогонов (из errors.json):**
   - <accumulated_errors[0]>
   - <accumulated_errors[1]>
   ...

   НЕ повторяй эти ошибки. Проверь, что каждая из них исправлена.
   iteration=NN из 3 max.
   ```
   Субагент **обязан вернуть тот же финальный JSON с `step_id`**, что и в первичном прогоне —
   иначе `state-recorder` уйдёт в ветку «нет step_id — не угадываем» и **не запишет** fix-прогон
   (ни `_origins`, ни вывод). НЕ отправляй один лишь блок ошибок без контракта фазы.
5. **Запусти субагента той же фазы повторно** этим контрактом. Субагент НЕ пишет артефакты
   с нуля — он **исправляет** ошибки из списка.
6. Запусти judge снова. **FAIL → loop на шаг 2.** **PASS → закрой шаг явной командой**
   `update.py --status completed` (per-phase блок — в брифе фазы). Это закрытие — единственное, что
   реально пишет статус шага; его НЕ делает ни `state-recorder` (на момент `SubagentStop` судья
   ещё не прошёл), ни сам судья, и оно переживает любое число fix-раундов. Пропустишь — шаг
   останется `in_progress`.

**После judge PASS:** errors.json автоматически удаляется, ошибки считаются исправленными.

**Второй детерминированный брейк — на уровне шагов манифеста** (`quality.max_step_reopens`,
дефолт 3): `update.py` считает переоткрытия шага (completed/failed → pending/in_progress) и
повторные провалы; на исчерпании возвращает **exit 3 (ESCALATE)** с баннером. Трактовка та же,
что у exit 3 судьи: **СТОП, спроси пользователя** — не продолжай цикл правок молча.
Эскейп (только с согласия пользователя): `override_judge.py --judge step-reopen-<step_id> …` —
R4, `gate-guard` требует approval-маркер `gate-override-step-reopen-<step_id>.json` (§0.6.1).

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

> **Снятие гейта — R4-класс и форсится рантаймом:** `gate-guard` блокирует запуск
> `override_judge.py` (exit 2), пока нет approval-маркера
> `ground/approvals/gate-override-<judge-name>.json`. Маркер фиксируется ТОЛЬКО после
> явного «да» пользователя — молча снять гейт нельзя. `--list`/`--remove` не гейтятся
> (чтение и восстановление enforcement'а свободны).

**Шаг 1.** Останови работу, покажи пользователю blocking issues и спроси. После явного
«да» зафиксируй согласие approval-маркером (это аудит-след санкции):
```bash
python3 -c "import json,os; os.makedirs('ground/approvals',exist_ok=True); json.dump({'approved_by':'user','reason':'<кто и почему разрешил>'},open('ground/approvals/gate-override-<judge-name>.json','w'),ensure_ascii=False)"
```

**Шаг 2.** Создай override-файл (`--reason` обязателен — это аудит-след):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/override_judge.py --judge <judge-name> --feature <slug> --step-id <step-id> --reason "<почему пропуск допустим>"
```

**Шаг 3.** Закрой шаг как обычно — `update.py` увидит override, пропустит блокировку и
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

## 3–10. Цикл фаз (фазовые брифы)

Фазовые инструкции вынесены в `references/phases/<phase>.md` — SKILL.md держит только общие
правила, бриф фазы читается непосредственно перед её выполнением (контекст-гигиена: не выполняй
фазу по памяти или по чужому брифу).

**Перед КАЖДОЙ фазой:**
1. Узнай текущую фазу и её бриф (один вызов):
   ```
   python3 <project>/.gigacode/skills/feature-pipeline/scripts/resolve_phases.py --project <project> --feature <slug> --current
   ```
   Вывод: `{"current_phase": "...", "brief": "references/phases/<id>.md", "gates": [...]}`.
2. Прочитай бриф: `read_file("<project>/.gigacode/skills/feature-pipeline/<brief>")`.
3. Выполни бриф. Закрытие шагов и гейты — как написано в брифе (`update.py` / `record_gate.py` /
   `run_judge.py`); **exit 3 в любом месте = ESCALATE: стоп-и-спроси (§0.6)**.
4. Фаза закрыта → вернись к шагу 1 (следующая фаза). Пустой `current_phase` — пайплайн завершён.

| Фаза | Бриф | Гейт закрытия |
|---|---|---|
| 00-brd | `references/phases/00-brd.md` | Гейт 1 + brd-judge + критичность |
| 01-grounding | `references/phases/01-grounding.md` | grounding-excerpt готов |
| 02-sdd | `references/phases/02-sdd.md` | check_sdd_doc + Гейт SDD |
| 02-design | `references/phases/02-design.md` | check_taskplan + Гейт 2 |
| 02-eval-plan | `references/phases/02-eval-plan.md` | eval-judge |
| 03-jira | `references/phases/03-jira.md` | Гейт 3 + check_jira |
| 04-tdd | `references/phases/04-tdd.md` | red/build/reuse-judge + record_gate per-task |
| 05-verify | `references/phases/05-verify.md` | coverage+regression+arch + record_gate |
| 06-document | `references/phases/06-document.md` | spec-judge + enrich_grounding |
| 07-deliver | `references/phases/07-deliver.md` | Гейты 4-6 + delivery-judge |

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
