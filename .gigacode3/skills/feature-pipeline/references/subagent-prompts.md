# Промпты субагентов фаз 4-5

Субагенты работают в изолированном контексте и возвращают JSON. Передавай ровно тот
контекст, что нужен шагу — не всю историю. Шаблоны ниже адаптированы из
`minor-defect-fix` под фичу (несколько задач, критерии приёмки из `task-plan`).

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
1. **Слой — сервисные unit-тесты с моками (Mockito): `@ExtendWith(MockitoExtension.class)`,
   `@Mock` зависимости, `@InjectMocks` тестируемый сервис.** ИЗБЕГАЙ `@DataJpaTest` и интеграционных
   `@SpringBootTest` (в multimodule они падают initializationError) — только если задача буквально про
   репозиторный запрос и в проекте УЖЕ есть рабочий рабочий пример такого теста.
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

## 4.2 Тестраннер

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
