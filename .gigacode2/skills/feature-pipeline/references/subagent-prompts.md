# Промпты субагентов фаз 4-5

Субагенты работают в изолированном контексте и возвращают JSON. Передавай ровно тот
контекст, что нужен шагу — не всю историю. Шаблоны ниже адаптированы из
`minor-defect-fix` под фичу (несколько задач, критерии приёмки из `task-plan`).

---

## 4.1 Тестописатель

Зови после того, как код всех задач фичи написан. Передай актуальный diff и `acceptance`.

```
description: "Write tests for feature <slug>"
subagent_type: general-purpose

prompt:
Главный агент реализовал новую фичу. Твоя задача — написать тесты на новый код и
довести покрытие изменённых файлов до ≥ <coverage_threshold> (line coverage, JaCoCo).

Корень проекта: <git toplevel>
Изменённые/созданные production-файлы: <список путей>
Diff фичи:
<git diff origin/<default>..HEAD -- ':!**/test/**' ':!*Test.java'>

Критерии приёмки по задачам (основа тестов):
<для каждой task: id, title, acceptance[]>

Алгоритм:
1. Для каждой задачи покрой её acceptance тестами (happy path + ошибочные ветки:
   null, пусто, нет прав, 404, конфликт — то, что есть в сценариях).
2. Соблюдай стиль соседних тестов: именование, given/when/then, моки, фикстуры,
   аннотации (@SpringBootTest / @WebMvcTest / @DataJpaTest — как принято в проекте).
3. Не запускай тесты — этим займётся тестраннер.
4. Не трогай тесты, не связанные с фичей.

Верни (≤250 слов):
- Созданные файлы тестов с краткой целью каждого.
- Изменённые существующие тесты с причиной.
- Где НЕ уверен, что покрытие достижимо (например, требуется инфраструктура,
  которой нет в проекте — security, scheduler).
```

После ответа главный агент читает `git diff src/test/` и при сомнениях пересматривает.

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
