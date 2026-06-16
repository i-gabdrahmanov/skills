# Промпты субагентов (фазы 0, 4-5 + судьи)

Субагенты работают в изолированном контексте и возвращают JSON. Передавай ровно тот
контекст, что нужен шагу — не всю историю. Шаблоны ниже адаптированы из
`minor-defect-fix` под фичу (несколько задач, критерии приёмки из `task-plan`).

## Общее правило для всех субагентов

**Ты НЕ вызываешь `ask_user_question`.** Это делает оркестратор.
Если тебе не хватает данных для работы — верни массив `pending_questions` в JSON:

```json
{
  "step_id": "...",
  ...,
  "pending_questions": [
    {"id": "epic", "question": "К какому Epic привязать Story?"}
  ]
}
```

Оркестратор задаст вопросы через `ask_user_question` и перезапустит тебя с полем
`answers: {"epic": "EPIC-123"}`. Подробнее — в SKILL.md §«Делегированные вопросы
субагента».

---

## 4.0 BRD-писатель (фаза 0 — черновение из ответов интервью)

Зови **после того, как интервью с пользователем проведено inline** (оркестратор собрал
ответы). Этот субагент делает многословную работу — пишет `brd.md` — в изолированном
контексте, чтобы не засорять контекст оркестратора черновым текстом. Интервью НЕ ведёт
(вопросы уже заданы); если данных всё же не хватает — возвращает `pending_questions`.

```
description: "Draft BRD for feature <slug> from interview answers"
subagent_type: general-purpose

prompt:
Ты пишешь документ бизнес-требований (BRD) на русском по уже собранным ответам интервью.
Пиши ЯЗЫКОМ БИЗНЕСА — без кода, имён классов/полей/таблиц, методов, SQL, топиков, метрик,
типов исключений. Техническая раскладка — работа tech-design, не BRD (это проверит brd-judge).

Контекст:
- Фича (slug): <slug>
- Jira-ключ (если есть): <KEY-123 или «нет»>
- Исходная идея/описание: <текст идеи или Jira summary/description>
- Ответы интервью (собраны оркестратором): <вопрос→ответ, списком>
- Grounding (если есть): <путь к grounding-excerpt.json / выжимка brd-grounder: смежные БТ>

Структура brd.md — по шаблону business-requirements (Контекст и проблема, Цель,
Бизнес-ценность, Заинтересованные стороны, Scope [входит/не входит], Функциональные
требования, Нефункциональные, Допущения, Открытые вопросы, Пользовательские сценарии).

Правила:
1. Всё, по чему нет данных из ответов/идеи — выноси в «Допущения» (что принял за данность)
   или «Открытые вопросы» (что прояснить до разработки, с пометкой «к кому»). НЕ выдумывай
   факты и НЕ блокируйся — пиши документ, фиксируя пробелы.
2. Если Jira-ключ есть — первой строкой шапки `**Jira:** <KEY-123>`.
3. Цель формулируй как бизнес-изменение/эффект, а не как решение.
4. Не пиши реализацию (последовательность вызовов, внутренние модули как дизайн).

Сохрани в `docs/feature-pipeline/<slug>/brd.md`.

Верни JSON (ТОЛЬКО его — без brd.md в теле ответа, чтобы не раздувать контекст):
{
  "step_id": "00-brd-<slug>",
  "brd_path": "docs/feature-pipeline/<slug>/brd.md",
  "summary": "3-5 строк: о чём BRD",
  "assumptions": ["ключевое допущение 1", "..."],
  "open_questions": ["критичный открытый вопрос (к кому) 1", "..."],
  "pending_questions": []
}
```

После возврата — независимый **brd-judge** (§7.6) субагентом + детерминированный
`run_judge.py brd <slug> --recheck`. Оба PASS → Гейт 1. См. SKILL.md §3.

---

## 4.1 Тестописатель (TDD — RED: тесты ДО кода)

Зови **до** написания production-кода задачи. Тесты пишутся первыми из `acceptance` и контракта
tech-design — они должны компилироваться и **падать** (реализации ещё нет). Это шаг RED.

```
description: "Write FAILING tests (TDD red) for task <id>"
subagent_type: general-purpose

prompt:
TDD, фаза RED. Реализации ещё НЕТ — твоя задача написать тесты, которые её специфицируют:
они должны КОМПИЛИРОВАТЬСЯ и ПАДАТЬ (не проходить), пока код не написан.

Корень проекта: <git toplevel>
Задача: <id, title>
Критерии приёмки (основа тестов, Given-When-Then): <acceptance[]>
Контракт из tech-design (сигнатуры/слои, которые появятся): <из tech-design.md §3 для задачи>
Целевой слой/класс: <напр. OverdueTaskServiceImpl.closeEmptyRegularTasks()>

Правила:
1. **По умолчанию пиши ТОЛЬКО Mockito unit-тесты** — `@ExtendWith(MockitoExtension.class)`,
   `@Mock` зависимости, `@InjectMocks` тестируемый сервис. Это база **для ЛЮБОГО слоя задачи**
   (dto/repository/service/controller), а не только для сервисного.
   **JPA / persistence / Spring-context тесты НЕ пиши вообще:** ни `@DataJpaTest`, ни
   `@SpringBootTest`, ни наследование от Spring-базы (`extends BaseTest`/`*IntegrationTest`/
   `*IT`). В multimodule они падают `initializationError`, и red-judge их блокирует (в т.ч.
   транзитивно через базовый класс).
   **Репозиторий/persistence — мокируй** (`@Mock FooRepository`), а не тестируй через БД.
   Единственное исключение — если в `design-context.json` поле `test_layer` явно НЕ
   `service-unit` (редкий осознанный случай); по умолчанию `test_layer == service-unit`.
2. По каждому acceptance — отдельный тест (happy + ошибочные ветки: null, пусто, нет прав, 404, конфликт).
   given/when/then, имена `should...When...`.
3. **Валидные, реалистичные данные:** стройте сущности билдерами/конструкторами с корректными типами и
   связями (как в домене), а не заглушками-нулями. Тест должен быть осмысленным, не «assertTrue(true)».
4. Тесты ССЫЛАЮТСЯ на ещё не существующие методы/классы из контракта tech-design — это норма для RED
   (компилятор увидит их, когда появится код; на этом шаге допустимо, что не компилится ИМЕННО из-за
   отсутствия целевого метода — гейт это учитывает; всё ОСТАЛЬНОЕ должно быть корректно).
5. Не запускай тесты и не пиши production-код. Только тесты.

Верни JSON:
{ "step_id": "04-test-<id>", "test_files": ["src/test/java/.../FooServiceTest.java"],
  "cases": [{"name":"...","acceptance":"Given..When..Then.."}], "layer": "service-unit",
  "notes": "что НЕ покрыть без инфраструктуры" }
```

После RED-тестов главный агент прогоняет `check_tests_red.py` (compile+fail), затем зовёт
java-spring-dev писать минимальный код до зелёного.

---

## 4.1a Тестраннер

Зови сразу после тестописателя. Только запускает, ничего не чинит.

```
description: "Run tests + JaCoCo for feature <slug>"
subagent_type: general-purpose

prompt:
Запусти тесты и JaCoCo, верни структурированный отчёт.

Корень проекта: <git toplevel>
Тип сборки: <gradle | maven>
Изменённые production-файлы: <список>

Шаги:
1. Gradle: `./gradlew test jacocoTestReport`. Maven: `mvn -q test jacoco:report`.
2. Открой XML JaCoCo (gradle: build/reports/jacoco/test/jacocoTestReport.xml;
   maven: target/site/jacoco/jacoco.xml).
3. Для каждого изменённого файла посчитай line coverage.

Верни ровно такой JSON (без обёрток):
{
  "tests": {"passed": N, "failed": N, "skipped": N,
            "failed_tests": [{"name":"...","message":"..."}]},
  "coverage": [{"file":"src/main/java/...","covered_lines":N,"missed_lines":M,"percent":0.83}],
  "below_threshold": ["src/main/java/..."],
  "missing_in_report": ["..."]
}
Если compile error до тестов — верни {"build_error":"..."}.
```

Лимит итераций тестописатель↔тестраннер — **3**. Не зелёное на третьей — стоп.

### Pre-commit (тот же тестраннер)
После зелёных тестов — полный прогон: `./gradlew clean build` + линтеры (spotless/
checkstyle, если есть — на падении форматирования предложи `spotlessApply`, не запускай
молча) + JaCoCo. Детали — `../minor-defect-fix/references/coverage.md`.

---

## 4.2 Стабы сигнатур (TDD — перед GREEN)

Зови после red-judge и до реализации. Создаёт минимальные сигнатуры классов/методов,
чтобы тесты компилировались и падали.

```
description: "Stubs for task <id>"
subagent_type: general-purpose

prompt:
Ты — java-spring-dev разработчик. Прочитай ~/.gigacode/skills/java-spring-dev/SKILL.md.

Создай ТОЛЬКО сигнатуры классов/методов из tech-design.md §3 с телом-заглушкой.
Цель: тесты (из 04-test-<taskId>) компилируются и падают.
Не добавляй лишних слоёв.

Gate: ./gradlew compileTestJava (должен пройти, тесты должны падать).
python3 ~/.gigacode/skills/feature-pipeline/scripts/check_tests_red.py --root . --task <taskId>

Выходной JSON:
  {"step_id": "stubs-<taskId>", "status": "completed", "compiles": true, "tests_fail": true}
```

---

## 4.3 GREEN — реализация (java-spring-dev)

Зови после прохождения red-judge и стабов. Реализует тела методов минимально — пока тесты не позеленеют.

```
description: "GREEN: implement code for task <id>"
subagent_type: general-purpose

prompt:
Ты — java-spring-dev разработчик. Прочитай ~/.gigacode/skills/java-spring-dev/SKILL.md.

Реализуй тела методов для задачи <taskId> из task-plan.json.
Минимально, пока тесты не позеленеют. Не добавляй слои «на всякий случай».

Если eval-guard блокирует запись — выполни:
python3 ~/.gigacode/skills/feature-pipeline/scripts/run_pending_evals.py --project . --feature <slug> --task <taskId>

Gate перед завершением:
1. ./gradlew test (задачи <taskId>)
2. python3 ~/.gigacode/skills/feature-pipeline/scripts/check_build.py "<папка>/task-plan.json" --task <taskId>

Выходной JSON:
  {"step_id": "04-build-<taskId>", "status": "completed", "tests_green": true}
```

---

## 4.4 Cover gaps — добор покрытия

Зови после pre-commit, если check_coverage показал LOW/MISSING.

```
description: "Cover gaps for feature <slug>"
subagent_type: general-purpose

prompt:
Прочитай check_coverage.py отчёт (LOW/MISSING файлы).
Допиши тесты только под непокрытое. Не переписывай зелёные тесты.
**Только Mockito unit-тесты** (`@ExtendWith(MockitoExtension.class)`); JPA/Spring-context
тесты (`@DataJpaTest`/`@SpringBootTest`/наследование от Spring-базы) НЕ пиши — red-judge их
блокирует, а покрытие добирай через моки.

Корень проекта: <git toplevel>
Фича (slug): <slug>
check_coverage отчёт: <путь или содержимое>

Gate:
python3 ~/.gigacode/tools/check_coverage.py --base dev --threshold 0.80

Выходной JSON:
  {"step_id": "cover-gaps", "files_added": [...], "coverage_ok": true}
```

---

## 4.5 Jira-писатель (фаза 2.5 — задачи в Jira)

Зови **в субагенте** (не inline). Субагент читает свой скилл сам — так его подробная
MCP-логика не попадает в контекст оркестратора, а цикл `pending_questions` отрабатывает
штатно (именно при inline-исполнении терялся вопрос про Epic). Субагент НЕ вызывает
`ask_user_question` — вопросы (Epic, спринт) возвращает в `pending_questions`, их задаёт
оркестратор и перезапускает субагента с `answers`.

```
description: "Jira tasks draft for <slug>"
subagent_type: general-purpose

prompt:
Сначала прочитай свой контракт: read_file("<project>/.gigacode/skills/jira-task-writer/SKILL.md")
и строго следуй ему. Ты генератор черновика задач Jira — НЕ создаёшь задачи на первом
запуске и НИКОГДА не зовёшь ask_user_question.

Вход (прочитай файлы сам по путям):
- task-plan: <папка фичи>/task-plan.json
- brd: <папка фичи>/brd.md (или business-requirements/<slug>.md)
- pipeline-config: <project>/ground/pipeline.json  (бери jira.* — project_key, issue_type_*,
  epic_link_field, sprint_field; флаг jira.enabled / auto_discovered)
- answers (если перезапуск): <ответы на прошлые pending_questions или «нет»>
- revision (если перезапуск с правкой): <что изменить в черновике, напр. «нужна 1 задача
  вместо 4», «объедини T2–T4 в одну подзадачу», «убери подзадачи, только Story»>

Правила:
1. Собери draft: Story (summary=task-plan.title, description из BRD §контекст/цель/критерии)
   + Sub-task на каждую задачу task-plan (в порядке depends_on).
   **Если пришёл `revision`** — пересобери draft с учётом правки (измени число/состав
   подзадач, объедини, переименуй, убери). Черновик после правки — НОВЫЙ источник истины;
   при `confirmed:true` создавай РОВНО его, не сырой task-plan. Снова верни `created:false`
   с обновлённым draft (пользователь подтвердит уже исправленный черновик).
2. **Epic — обязательный вопрос.** Если `jira.enabled` и создаёшь Story — ВСЕГДА включи в
   `pending_questions` вопрос `{"id":"epic","question":"К какому Epic привязать Story? Укажи
   ключ Epic (например EPIC-123) или 'нет'."}`, даже если `epic_link_field` не авто-обнаружен
   (пользователь может ответить «нет»; ключ доступен через MCP). Аналогично спринт — если
   `sprint_field` задан.
3. Если в `answers` уже пришёл epic/sprint — НЕ переспрашивай, убери из pending_questions.
4. Создавай задачи (через Jira MCP по §4 скилла) ТОЛЬКО когда оркестратор пришлёт
   `confirmed: true`.

Верни JSON по контракту скилла (§5): {created, draft:{story, subtasks[]}, pending_questions[], skipped}.
```

После возврата — оркестратор обрабатывает `pending_questions` через `ask_user_question`
(по одному), затем показывает черновик и на «Да» перезапускает субагента с `confirmed:true`.
См. SKILL.md §6.

---

## 5. Спецадаптер

Зови после pre-commit, до коммита кода. Работает в репо спеки (`docs_path`), не в коде.

```
description: "Update spec for feature <slug>"
subagent_type: general-purpose

prompt:
Главный агент реализовал новую фичу в репозитории кода (тесты зелёные). Обнови
спецификацию в репо спеки.

Контекст:
- Фича (slug): <slug>; задачи Jira: <ключи или "без Jira">
- Корень репо спеки: <docs_path>   ← здесь работаешь
- Корень репо кода: <git toplevel>  ← только для чтения diff

Diff фичи (production, без тестов):
<git -C <toplevel> diff origin/<default>..HEAD -- ':!**/test/**' ':!*Test.java'>

Суть фичи (2-3 предложения): <...>
tech-design: <путь к tech-design.md>

Алгоритм:
1. Перейди в <docs_path>. Ветка `feature/<slug>` (создай от default, если нет).
2. Обнови разделы, описывающие изменённое поведение. Новые эндпойнты → добавь в
   system-analysis/api.md; новые сущности → domain.md. Стиль документа сохраняй.
3. Не выдумывай разделы — если части нет, зафиксируй в отчёте.
4. Один коммит в стиле спец-репо. НЕ пушь (push/PR решает главный агент на Гейте 5).

Верни JSON:
{"no_changes":false,"branch":"feature/<slug>","commit_sha":"...","default_branch":"main",
 "files_changed":["api/...","domain/..."],"summary":"...","uncovered_in_spec":[]}
```

---

## 7. Судьи (Judges) — верификация качества с блокировкой

Судьи — субагенты-верификаторы, которые проверяют качество артефактов фазы **до** того, как
шаг закрывается в pipeline-state. Каждый судья возвращает JSON-вердикт. Если `passed: false` —
главный агент **блокирует** переход к следующей фазе и показывает blocking_issues пользователю.

Вердикты сохраняются в: `ground/statements/feature-pipeline/<feature>/judges/<judge-name>.json`

---

### 7.1 eval-judge — проверка eval-plan (фаза 2.5)

Зови сразу после `build_evals_from_design.py`, ДО того как начинается код. Проверяет,
что eval'ы адекватны, покрывают acceptance criteria, не пропущены граничные случаи.

```
description: "Verify eval-plan for feature <slug>"
subagent_type: general-purpose

prompt:
Проверь eval-plan.json на полноту и адекватность. Eval'ы — это детерминированные
автоматические проверки, которые форсят Eval-Driven Development. Они пишутся ДО кода.

Контекст:
- Фича (slug): <slug>
- task-plan.json: <путь к task-plan.json>
- eval-plan.json: <путь к eval-plan.json>

Алгоритм:
1. Прочитай task-plan.json — извлеки все tasks с их acceptance criteria.
2. Прочитай eval-plan.json — извлеки все evals.
3. Для каждой задачи проверь:
   a. Есть ли compile eval? (должен быть — гарантия компиляции)
   b. Есть ли test_pass eval? (должен быть — гарантия прохождения тестов)
   c. Есть ли coverage eval? (должен быть — гарантия покрытия)
   d. Каждый acceptance criteria маппится хотя бы на один eval (по тексту/id/task_id).
      Если acceptance описывает «Kafka отправку», а eval'ы только compile+coverage —
      это пробел.
4. Проверь пороги:
   a. coverage_threshold >= 0.5 и < 1.0 (разумный диапазон)
   b. test_pass_threshold >= 0.8 (иначе тесты не дают гарантии)
5. Проверь, что нет дубликатов eval'ов (одинаковые id или command).
6. Проверь, что каждый eval ссылается на существующий task_id из task-plan.

Критерии FAIL:
- Хотя бы одна задача без compile eval'а
- Хотя бы один acceptance criteria не покрыт eval'ом
- coverage_threshold < 0.5 или test_pass_threshold < 0.8
- Есть eval'ы с несуществующим task_id

Верни JSON:
{
  "step_id": "eval-judge-<slug>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {
      "name": "Compile eval exists for all tasks",
      "status": "PASS" | "FAIL",
      "detail": "8/8 tasks have compile eval",
      "severity": "error"
    }
  ],
  "blocking_issues": ["task-3: acceptance 'Kafka send on close' not covered by any eval"],
  "warnings": ["task-7: coverage threshold 0.3 is very low"],
  "summary": "4/5 checks passed. 1 blocking issue."
}
```

---

### 7.2 red-judge — проверка RED-тестов (фаза 3, после тестописателя)

Зови после того, как субагент-тестописатель написал тесты, ДО стабов и ДО кода.
Проверяет, что тесты — настоящие, специфицируют acceptance, не тривиальны.

```
description: "Verify RED tests for task <taskId>"
subagent_type: general-purpose

prompt:
Проверь, что написанные RED-тесты действительно специфицируют acceptance criteria,
не являются пустышками, и корректно падают (реализации ещё нет).

Контекст:
- Фича (slug): <slug>
- Задача: <id, title>
- Критерии приёмки (acceptance): <список Given-When-Then>
- Контракт tech-design: <сигнатуры/слои из tech-design>
- Тестовые файлы: <список путей>

Алгоритм:
1. Прочитай каждый тестовый файл.
2. Для каждого теста проверь:
   a. Есть ли assert (не assertTrue(true), не assertNotNull(null))?
   b. Маппится ли на acceptance criteria? (Given-When-Then в комментарии или имени теста)
   c. Использует ли реалистичные данные (не нули, не пустые заглушки)?
   d. Слой корректен: @ExtendWith(MockitoExtension.class) для всех слоёв.
      Если test_layer="service-unit" — @DataJpaTest и @SpringBootTest **запрещены**
      (red-judge блокирует автоматически; не пытайся обосновать исключение).
   e. Нет @SpringBootTest при test_layer=service-unit.
3. Проверь покрытие acceptance:
   a. Каждый acceptance имеет хотя бы один happy-path тест.
   b. Хотя бы один негативный сценарий (ошибка, null, пустой список, 404).
4. Проверь, что тесты действительно будут падать без реализации:
   a. Тесты ссылаются на классы/методы из контракта, которые ещё не реализованы.
   b. Нет моков, которые «перекрывают» отсутствие реализации (when(...).thenReturn(...) —
      это норма, но сам вызов метода должен быть).

Критерии FAIL:
- Хотя бы одна задача без тестов
- Есть тест с assertTrue(true) или без assert'ов
- acceptance criteria не покрыт ни одним тестом
- Все тесты — только happy path (нет негативных)
- Использован @DataJpaTest при test_layer=service-unit (блокирует red-judge)
- Использован @SpringBootTest при test_layer=service-unit (блокирует red-judge)

Верни JSON:
{
  "step_id": "red-judge-<slug>-<taskId>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {
      "name": "Acceptance coverage",
      "status": "PASS" | "FAIL",
      "detail": "3/3 acceptance criteria covered",
      "severity": "error"
    }
  ],
  "test_files_analyzed": ["..."],
  "assertion_issues": ["testShouldReturnEmptyListWhenNoTasks: assertNull(result) — weak assertion"],
  "missing_negative_tests": ["testShouldThrowWhenKafkaFails — not found"],
  "blocking_issues": ["... тесты не покрывают acceptance '...'"],
  "summary": "..."
}
```

---

### 7.3 build-judge — проверка реализации (фаза 3, после GREEN)

Зови после того, как java-spring-dev написал код и тесты зелёные. Проверяет,
что реализация соответствует tech-design, нет stubs, нет мёртвого кода.

```
description: "Verify build quality for feature <slug>"
subagent_type: general-purpose

prompt:
Проверь качество реализации фичи. Код написан, тесты зелёные. Убедись,
что нет критических проблем: stubs, мёртвый код, рассинхрон с дизайном,
runtime-опасности.

Контекст:
- Фича (slug): <slug>
- task-plan.json: <путь>
- tech-design.md: <путь>
- Изменённые production-файлы: <git diff --name-only>
- Полный git diff (без тестов): <git diff>
- Результат тестраннера: <откуда взять>
- Результат check_coverage.py: <откуда взять>

Алгоритм:
1. Прочитай все изменённые production-файлы.
2. Проверь на **stubs**:
   a. Есть ли `throw new UnsupportedOperationException()` или `throw new RuntimeException("not implemented")`
      в production-коде? Если да — FAIL.
   b. Есть ли `// TODO implement` или `// FIXME` в production-коде? Если да — WARN (не FAIL).
3. Проверь на **мёртвый код**:
   a. Сверься с tech-design: какие классы отмечены «удалить»? Удалены ли они?
   b. Есть ли import'ы на несуществующие классы?
   c. Есть ли @Autowired поля без соответствующих бинов?
4. Проверь **соответствие tech-design**:
   a. Сигнатуры методов совпадают с описанными в tech-design.
   b. Слои (repository, service, controller) те же, что в плане.
   c. Аннотации (@Transactional, @Query) соответствуют контракту.
5. Проверь **runtime-безопасность**:
   a. Kafka-отправка внутри @Transactional без `afterCommit`? (если это проблема для проекта)
   b. Вызов методов, которые могут кинуть NPE, без null-check?
   c. Scheduler вызывает методы, которые могут упасть, и это не обработано?
6. Проверь **качество тестов** (кросс-проверка с red-judge):
   a. Новые тесты действительно тестируют новый код (не только pre-existing).
   b. Coverage для изменённых файлов >= порога.
7. Проверь **code style**:
   a. Checkstyle/spotless проходит (если есть).
   b. Нет закомментированного кода.

Критерии FAIL:
- Stubs в production-коде (UnsupportedOperationException)
- Не удалён класс, который tech-design предписывает удалить
- Coverage ниже порога для любого изменённого файла
- Есть @Transactional + Kafka без afterCommit (если это проблема)
- Код не компилируется
- Новые checkstyle-нарушения

Верни JSON:
{
  "step_id": "build-judge-<slug>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {"name": "No stubs in production code", "status": "PASS", "detail": "0 stub methods found", "severity": "error"},
    {"name": "Dead code removal", "status": "FAIL", "detail": "EmptyTaskCloserScheduler not deleted as per tech-design", "severity": "error"},
    {"name": "Coverage threshold", "status": "PASS", "detail": "All changed files >= 80%", "severity": "error"},
    {"name": "Tech design compliance", "status": "PASS", "detail": "All signatures match", "severity": "warning"}
  ],
  "blocking_issues": [
    "EmptyTaskCloserScheduler still exists — tech-design §3 marks it for deletion"
  ],
  "warnings": ["Scheduler calls stub methods — will throw UnsupportedOperationException at runtime 21:00 MSK"],
  "summary": "4/5 checks passed. 1 blocking issue."
}
```

---

### 7.4 spec-judge — проверка документации (фаза 5, после Document)

Зови после спецадаптера. Проверяет, что спецификация актуальна и ground обновлён.

```
description: "Verify spec quality for feature <slug>"
subagent_type: general-purpose

prompt:
Проверь, что документация фичи полна и актуальна. Документация должна отражать
реализованные изменения, а ground — быть готовым для следующей фичи.

Контекст:
- Фича (slug): <slug>
- docs_path: <docs_path>
- Папка фичи: <docs/feature-pipeline/<slug>/>
- task-plan.json: <путь>
- git diff (production): <git diff --name-only>

Алгоритм:
1. Проверь наличие обязательных документов:
   a. docs/feature-pipeline/<slug>/brd.md — существует?
   b. docs/feature-pipeline/<slug>/tech-design.md — существует?
   c. docs/feature-pipeline/<slug>/task-plan.json — существует?
2. Проверь актуальность ground (если есть enrich_grounding):
   a. ground/statements/feature-pipeline/<slug>/manifest.json — exists?
   b. docs/system-analysis/scan/ — обновлён для изменённых модулей?
   c. docs/system-analysis/grounding-excerpt.json — существует и не пуст?
3. Проверь, что docs/feature-pipeline/ не замусорен:
   a. Нет папок предыдущих фич с incomplete-статусом.
   b. Только текущая фича.
4. Проверь артефакты (если применимо):
   a. UML-диаграммы (system-analysis/api.md) — обновлены для изменённых эндпойнтов?
   b. ADR (Architecture Decision Records) — если нужно, созданы?

Критерии FAIL:
- brd.md, tech-design.md или task-plan.json отсутствуют
- ground/statements/feature-pipeline/<slug> пуст или отсутствует manifest
- enrich_grounding не запускался (проверить по времени файлов ground)

Верни JSON:
{
  "step_id": "spec-judge-<slug>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {"name": "BRD exists", "status": "PASS", "detail": "brd.md found", "severity": "error"},
    {"name": "Tech design exists", "status": "PASS", "detail": "tech-design.md found", "severity": "error"},
    {"name": "Grounding updated", "status": "FAIL", "detail": "grounding-excerpt.json not updated since last run", "severity": "error"}
  ],
  "missing_docs": [],
  "stale_grounding_modules": ["service/taskservice"],
  "blocking_issues": ["grounding-excerpt.json не обновлён — следующая фича стартует без контекста"],
  "summary": "2/3 checks passed. 1 blocking issue."
}
```

---

### 7.5 delivery-judge — проверка готовности к доставке (фаза 6, перед коммитом)

Зови перед Гейтом 4 (коммиты). Проверяет, что всё готово к созданию PR и отчёту в Jira.

```
description: "Verify delivery readiness for feature <slug>"
subagent_type: general-purpose

prompt:
Проверь готовность фичи к доставке: код, Jira, коммиты, PR, секреты, техдолг.

Контекст:
- Фича (slug): <slug>
- task-plan.json: <путь>
- jira-tasks-result.json: <путь> (если есть, иначе Jira skipped)
- git status: <вывод git status>
- git log (последние коммиты): <git log -5>
- Ветки: <git branch>
- Изменённые файлы: <git diff --name-only>
- Все production-файлы задачи: <cat каждого изменённого production-файла>

Алгоритм:
1. Проверь **консистентность Jira** (если Jira enabled):
   a. У каждой задачи из task-plan есть jira-ключ в jira-tasks-result.json?
   b. Каждый коммит содержит Jira-ключ в сообщении?
2. Проверь **build-статус** (кросс-проверка с build-judge):
   a. Все build-шаги реально completed (нет stubs, нет мёртвого кода)?
   b. Все eval'ы пройдены (evaluated_at не null)?
3. Проверь **безопасность**:
   a. Есть ли hardcoded пароли, токены, URL стейджинга?
      (grep: password, secret, token, .env, jdbc:postgresql://localhost — но не в тестах)
   b. Есть ли .env файлы в diff?
4. Проверь **техдолг**:
   a. Есть ли TODO / FIXME / HACK / XXX в production-коде?
   b. Если есть — они явно documented в blocking_issues как warnings?
5. Проверь **git-гигиену**:
   a. git status чист? (нет незакоммиченных изменений кроме ожидаемых)
   b. Ветки соответствуют stacked-модели? (каждая от родительской по depends_on)
   c. Сообщения коммитов в стиле проекта: с Jira-ключом, без Co-Authored-By.
6. Проверь **готовность PR**:
   a. Для каждой задачи: source branch, target branch, title, body.
   b. Body содержит ссылку на Jira и список изменённых файлов.

Критерии FAIL:
- Stubs/missing implementation (делегировано build-judge, но delivery-judge перепроверяет)
- Найдены секреты/credentials в коде
- git status показывает неожиданные изменения
- TODO/FIXME без явного разрешения
- Jira не консистентна (если Jira enabled)
- Сообщения коммитов без Jira-ключа

Верни JSON:
{
  "step_id": "delivery-judge-<slug>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {"name": "No secrets in code", "status": "PASS", "detail": "No hardcoded credentials found", "severity": "error"},
    {"name": "Jira consistency", "status": "FAIL", "detail": "Jira skipped but tasks have no keys", "severity": "error"},
    {"name": "Git status", "status": "PASS", "detail": "Working tree clean", "severity": "error"},
    {"name": "No stubs", "status": "FAIL", "detail": "UnsupportedOperationException in 2 methods", "severity": "error"}
  ],
  "blocking_issues": [
    "getEmptyTasksWithEmployee() — throw UnsupportedOperationException",
    "closeEmptyTasksWithEmployee() — throw UnsupportedOperationException"
  ],
  "warnings": [
    "EmptyTaskCloserScheduler not deleted (tech-design §3 marks for deletion)",
    "Jira integration skipped — no link between code and tasks"
  ],
  "commit_plan": [
    {"task": "task-1", "files": ["database/.../TaskUpzRepository.java"], "message": "KIDPPRB-8639: add findAllEmptyTasksWithEmployee query"},
    {"task": "task-3", "files": [".../OverdueTaskServiceImpl.java"], "message": "KIDPPRB-8639: implement closeEmptyTasksWithEmployee"}
  ],
  "summary": "2/4 checks passed. 2 blocking issues."
}
```

---

### 7.6 brd-judge — проверка БТ на язык бизнеса (фаза 0, после BRD)

Зови сразу после того, как создан `brd.md`, **до Гейта 1**. Проверяет, что БТ написаны как
бизнес-требования, а не как техническая спецификация с кодом. Это семантический слой —
литеральные код-токены уже ловит детерминированный `run_judge.py brd`, а субагент проверяет
**стиль и altitude**: описывают ли требования бизнес-эффект и правило, а не реализацию.

```
description: "Verify BRD is business-language for feature <slug>"
subagent_type: general-purpose

prompt:
Проверь документ БТ (бизнес-требований): написан ли он на языке бизнеса, а не как
техническая спецификация с кодом. БТ читают заказчик и аналитик — в нём не должно быть
ни кода, ни деталей реализации. Техническая раскладка — это работа tech-design, не БТ.

Контекст:
- Фича (slug): <slug>
- brd.md: <путь к brd.md>

Антипаттерн (так НЕЛЬЗЯ):
> Система должна вызвать EmptyTaskCloserScheduler, который использует
> RegularTaskRepository.findByEmployeeIsNotNull(), отправляет AUTO_CLOSE в топик
> UPZ_ADAPTER_REQUEST и инкрементирует метрику CLOSED_EMPTY_REGULAR_TASKS_COUNT.

Правильно (так НАДО — тот же смысл, язык бизнеса):
> Система должна ежедневно в 06:00 проверять регулярные задачи, закреплённые за
> сотрудниками. Если в задаче нет ни одного документа, письма или файла — она
> закрывается автоматически. Каждое закрытие фиксируется в журнале для контроля.

Алгоритм:
1. Прочитай brd.md целиком.
2. Найди код-токены: имена классов/сущностей/полей/таблиц, методы и сигнатуры, SQL/JPQL,
   пакеты, фрагменты кода, имена переменных, имена метрик, названия топиков/очередей,
   типы исключений. Каждый — blocking.
3. Найди «спецификацию вместо требования»: последовательность шагов реализации
   («система вызывает X, затем Y»), внутренние имена модулей/сервисов, поданные как дизайн,
   описание «как сделано» вместо «какой результат для бизнеса» в Scope/ФТ/сценариях.
4. Различай: имя внешней бизнес-системы по имени («заявка уходит в систему УПЗ») — это ОК;
   имя внутреннего класса/модуля как реализация — это FAIL.
5. Для каждой находки дай конкретную цитату и предложи бизнес-переформулировку.

Критерии FAIL:
- Любой код-токен в тексте БТ
- Требование описывает реализацию (последовательность вызовов), а не бизнес-эффект/правило
- Внутренние имена модулей/сервисов поданы как дизайн
- Scope/ФТ/сценарии про «как сделано», а не «какой результат для бизнеса»

Критерии WARN (не блокируют, но отметь):
- Пограничные CamelCase-термины, которые могут быть названиями систем/продуктов

Верни JSON:
{
  "step_id": "brd-judge-<slug>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {"name": "Нет код-токенов", "status": "FAIL", "detail": "Найдены: AUTO_CLOSE, checkIfEmptyTask()", "severity": "error"}
  ],
  "blocking_issues": ["§Функциональные требования: 'вызвать EmptyTaskCloserScheduler' — опиши бизнес-эффект: 'пустые задачи закрываются автоматически'"],
  "warnings": ["Термин 'TaskFlow' — проверь, это название системы или класса"],
  "summary": "3/5 checks passed. 2 blocking issues."
}
```

Сохрани вердикт субагента в файл и ингесть его детерминированным раннером:
`run_judge.py brd <slug> --from-output <verdict.json> --project-root <project>` (он добавит
ошибки в errors.json для ре-итерации). Затем `run_judge.py brd <slug> --recheck` подтверждает,
что детерминированный слой (код-токены) тоже зелёный.

---

### 7.7 reuse-judge — судья качества: нет велосипедов (фаза 3 GREEN, после build-judge)

Зови после build-judge (код написан, тесты зелёные), ДО закрытия `04-build-<taskId>`. Проверяет,
что новый код не пишет «велосипеды» — не дублирует доступные в проекте библиотеки/утилиты.
Гибрид: детерминированный `run_judge.py reuse` (regex анти-паттернов по git diff) + этот LLM-судья
(семантика по каталогу переиспользования).

```
description: "Verify code reuse for feature <slug>"
subagent_type: general-purpose

prompt:
Проверь, что новый код не изобретает велосипед — не пишет вручную то, что уже доступно
в проекте: библиотечные утилиты (на classpath) или существующие util/helper-классы проекта.

Контекст:
- Фича (slug): <slug>
- git diff (production, без тестов): <git diff>
- Каталог переиспользования: <docs/system-analysis/scan/reuse.json> (dependencies + project_utils)
  и секция reuse из grounding-excerpt.json (инъектится context-injector)
- tech-design.md: <путь>

Алгоритм:
1. Прочитай git diff — выдели добавленный production-код.
2. Сверь с каталогом:
   a. Ручная проверка коллекции на null/пустоту → есть ли CollectionUtils/ObjectUtils в deps?
   b. Ручная проверка строки на blank/пустоту → StringUtils (commons-lang3/spring)?
   c. Ручной null-default / null-check → java.util.Objects / Optional (stdlib, всегда доступны).
   d. Новый helper/util дублирует существующий project_util (по имени/сигнатуре/намерению)?
   e. Ручная итерация для map/filter/find → Stream API.
3. Имя замены предлагай ТОЛЬКО если библиотека реально есть в каталоге (или это stdlib).
4. Для каждой находки — цитата hunk + точная замена (Class.method из каталога).

Критерии FAIL (blocking):
- Код дублирует доступную библиотечную утилиту (библиотека есть в dependencies)
- Новый класс дублирует существующий project_util из каталога

Критерии WARN (не блокируют):
- Замена из stdlib (Objects/Optional/Stream) — эвристика, может быть ложным срабатыванием
- Похожий util, но сигнатуры/намерение отличаются

Верни JSON:
{
  "step_id": "reuse-judge-<slug>",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {"name": "Нет ручной проверки коллекции", "status": "FAIL",
     "detail": "OrderService.java:42 — используй CollectionUtils.isEmpty()", "severity": "error"}
  ],
  "blocking_issues": ["OrderService.java:42 ручная проверка списка → CollectionUtils.isEmpty() (commons-collections в deps)"],
  "warnings": ["Util.java:10 ручной null-check → Objects.requireNonNull()"],
  "summary": "3/5 checks passed. 1 blocking."
}
```

Сохрани вердикт субагента в файл и ингесть детерминированным раннером (он добавит ошибки в
errors.json для ре-итерации), затем подтверди:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py reuse <slug> \
  --from-output <verdict.json> --diff-base <base> --project-root <project>
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py reuse <slug> \
  --recheck --diff-base <base> --project-root <project>
```

---

### Сводка: когда какого судью вызывать

| Фаза | Судья | Условие вызова | Блокирует |
|------|-------|---------------|-----------|
| 0 (после BRD) | brd-judge | brd.md создан, ДО Гейта 1 | Шаг `00-brd` не закрывается |
| 2.5 (после eval-plan) | eval-judge | eval-plan.json создан | Шаг `02-eval-plan` не закрывается |
| 3 — RED (после тестов) | red-judge | Тесты написаны, ДО стабов | Шаг `04-test-<taskId>` не закрывается |
| 3 — GREEN (после кода) | build-judge | Код написан, тесты зелёные | Шаг `04-build-<taskId>` не закрывается |
| 3 — GREEN (после build-judge) | reuse-judge | Код написан, build-judge PASS | Шаг `04-build-<taskId>` не закрывается |
| 5 (после spec) | spec-judge | Спецадаптер завершён | Шаг `06-spec` не закрывается |
| 6 (перед коммитом) | delivery-judge | Все build-шаги completed | Гейт 4 (коммиты) не открывается |
```
