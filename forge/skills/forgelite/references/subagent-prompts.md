# Промпты субагентов forgelite

Вынесено из `../SKILL.md` §3–§6 в отдельный файл по конвенции `feature-pipeline`, чтобы
каталог обвязки матчился автосканером ForgeIDE (Import scaffold) без ручной привязки.
SKILL.md остаётся источником процесса (гейты, порядок, hooks) — при правке промпта
синхронизируй оба места.

## §4.1 lite-ground — лёгкий grounding области кода

Если `<toplevel>/docs/system-analysis/grounding-excerpt.json` уже есть — переиспользуй, закрой
шаг инлайн. Иначе субагент-orientation:

```
description: "Lite grounding orientation for <JIRA-KEY>"
subagent_type: general-purpose

prompt:
Собери КОМПАКТНЫЙ обзор области кода под задачу — без полного скана.
Корень репо: <toplevel>
Задача: <summary> / AC: <core description>
Действия (грепом по именам классов/сущностей/эндпойнтов из задачи):
1. Затронутый модуль(и) и 3–8 ключевых классов.
2. Соседние тесты (пути) и их стиль (JUnit5/Mockito, given/when/then).
3. Конвенции: сборка (gradle/maven), Lombok, структура пакетов, слои.
Запиши в <toplevel>/docs/system-analysis/grounding-excerpt.json:
{"modules":[...],"touched_classes":[...],"neighbor_tests":[...],"conventions":{...},"build":"gradle|maven"}
Верни JSON: {"step_id":"lite-ground","status":"completed","summary":"<1-2 предложения>"}
Не смог локализовать — status:"failed" и что мешает.
```

Файл подхватывает `context-injector` (вкладывает в последующих субагентов).

---

## §4.2 lite-design — tech-design по существующей спеке (Gate 1)

Спека (SDD) для задачи уже существует — это source of truth, BRD/SDD заново не пишутся.
Путь к спеке фиксируется заранее в `sources.spec` (иначе `gate-guard` блокирует запись фазы).

```
description: "tech-design by existing spec for <JIRA-KEY>"
subagent_type: general-purpose

prompt:
Ты — tech-design. Прочитай СУЩЕСТВУЮЩУЮ спеку (source of truth) по пути <sources.spec> и
grounding-excerpt. Построй tech-design.md + task-plan.json по слоям СТРОГО по этой спеке
(sdd_ref якори — на её разделы). НЕ пиши BRD/SDD заново.
ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-design --cmd "python3 <project>/.gigacode/skills/tech-design/scripts/check_taskplan.py <путь-к-task-plan.json> && python3 <project>/.gigacode/skills/tech-design/scripts/check_sdd.py <путь-к-task-plan.json> --sdd <sources.spec>"
Верни JSON {"step_id":"lite-design","status":"completed"} только если раннер дал exit 0.
```

Gate 1: «Дизайн такой?» — к коду только после «да» (или предзаписи в headless).

---

## §4.3 lite-red — TDD RED, тесты до кода

Хук `tdd-guard` не даёт писать `src/main/`, пока `lite-red` не закрыт.

```
description: "TDD RED tests for <JIRA-KEY>"
subagent_type: general-purpose

prompt:
Напиши падающие unit-тесты (TDD RED) по acceptance criteria. НЕ трогай src/main/.
Корень репо: <toplevel>. Сборка: <gradle|maven>.
Задача: <summary> / AC: <acceptance criteria>. Grounding: <классы/соседние тесты>.
Правила:
1. Тесты только в src/test/, стиль соседних (JUnit5/Mockito, given/when/then).
2. Каждый пункт AC — отдельным тестом + edge cases (null/пусто/граница). Без @Disabled.
3. Тесты ДОЛЖНЫ компилироваться и ПАДАТЬ. Никакого production-кода/заглушек в src/main/.
4. ПОСЛЕДНИМ действием прогони RED-гейт ЧЕРЕЗ РАННЕР (он пишет evidence — без него шаг не закроется):
   Gradle: python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-red --expect red --compile-cmd "./gradlew compileTestJava" --cmd "./gradlew test"
   Maven:  тот же вызов с --compile-cmd "mvn -q test-compile" --cmd "mvn -q test"
   exit 0 = RED корректен (компиляция прошла, тесты падают).
Верни JSON: {"step_id":"lite-red","status":"completed|failed","tests_written":["..."],"compile_ok":true,"tests_failed":true}
status:"completed" ТОЛЬКО если compile_ok=true И tests_failed=true.
```

Лимит ре-итераций — `quality.max_step_reopens` (дефолт 3); на исчерпании `update.py` даёт
exit 3 (ESCALATE) — стоп, спроси пользователя.

---

## §4.4 lite-green — TDD GREEN, код зеленит тесты

```
description: "TDD GREEN implementation for <JIRA-KEY> (java-spring-dev)"
subagent_type: general-purpose

prompt:
Сначала прочитай и строго следуй: read_file("<project>/.gigacode/skills/java-spring-dev/SKILL.md")
Затем реализуй production-код, чтобы RED-тесты стали зелёными (TDD GREEN).
Корень репо: <toplevel>. Задача: <summary> / AC: <acceptance criteria>.
RED-тесты (пути): <из lite-red>. Конвенции: <из grounding>.
Правила:
1. Минимальное изменение под AC. Не рефактори соседнее, не вводи лишних абстракций.
2. Конвенции проекта (Lombok, пакеты, слои) — как в java-spring-dev.
3. Переиспользуй существующие util/библиотеки classpath — без велосипедов.
4. Правь только src/main/ (тесты уже есть; не ослабляй их).
5. ПОСЛЕДНИМ действием прогони BUILD-гейт ЧЕРЕЗ РАННЕР (он пишет evidence — без него шаг не закроется):
   Gradle: python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-green --cmd "./gradlew build"
   Maven:  тот же вызов с --cmd "mvn -q verify"
Верни JSON: {"step_id":"lite-green","status":"completed|failed","files_changed":["..."],"build_ok":true}
status:"completed" ТОЛЬКО если build_ok=true (сборка и тесты зелёные).
```

reuse-judge (advisory, не блокирует): проверь diff `src/main/` на дублирование доступных
util/библиотек из grounding — нашёл велосипед, предложи пользователю заменить.
