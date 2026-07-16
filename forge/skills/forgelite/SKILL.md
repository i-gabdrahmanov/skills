---
name: forgelite
description: >
  Лёгкая ветка forge для исполнения УЖЕ ПОДГОТОВЛЕННОЙ подзадачи из Jira (есть описание и
  acceptance criteria) для Java/Spring: grounding → tech-design по СУЩЕСТВУЮЩЕЙ спеке (source of
  truth) → TDD RED→GREEN → покрытие → ветка/commit/PR → отчёт в Jira. Без BRD и без написания
  SDD с нуля, без постановки задач — только исполнение готового тикета (tech-design строится по
  уже готовой спеке, а не пишется заново). Обычно вызывается роутером (skills/router), когда пользователь
  выбрал путь «готовая задача»; работает и автономно. Триггеры: «выполни задачу из jira»,
  «сделай KIDPPRB-1234», «прогони готовый subtask до PR». Отличие от feature-pipeline —
  тот с нуля (BRD→PR); от minor-defect-fix — тот баг + спека в отдельном репо. Никогда не
  коммитит, не пушит, не создаёт PR и не пишет в Jira без явного «да».
---

# Forgelite — lite-ветка: исполнение готовой задачи Jira

> **Пути — через `feature-pipeline/references/skill-paths.json`** (общие скрипты forge) и
> локально `references/manifest-steps.json`. Зовём как `python3 <project>/.gigacode/<path>`.
> `<project>` = корень репо кода (там же `.gigacode/`). Не используй `~/.gigacode/...`.

> **Рантайм — форк GigaCode (Qwen). Жёсткие правила:**
> - Хуки за флагом запуска: `gigacode --experimental-hooks -p "..."` (иначе `0 hook entries`).
> - В командах — только однострочные, без `$(...)` и обратных кавычек (рантайм режет).
> - Тяжёлую фазу — только через `agent(subagent_type="general-purpose", ...)`. `agent()` и
>   `ask_user_question` не активны одновременно.

Плоский цикл (feature = ключ Jira; стейт в namespace `forgelite`, отдельно от `feature-pipeline`):
**Jira → grounding → tech-design по спеке → RED → GREEN → покрытие → commit → PR → отчёт**. Ничего
необратимого (commit, push, PR, комментарий в Jira) — без явного «да» (Gate 1–4).

Шаги стейта (`lite-*`, чтобы НЕ пересекаться с фазовой машиной full-пути и масками судей):
`lite-jira → lite-ground → lite-design → lite-red → lite-green → lite-verify → lite-deliver → lite-report`.

> **lite ≠ «без дизайна».** Для простой задачи спека (SDD) уже существует — это **source of
> truth**. `lite-design` строит tech-design/task-plan ПО НЕЙ (скилл `tech-design`, субагент),
> BRD/SDD заново НЕ пишутся. Путь к спеке — обязательное решение `sources.spec`: пока оно не
> записано, `gate-guard` блокирует запись фазы `lite-design` (fail-closed). См. §4.

---

## 0. Предусловия

- Java/Spring (gradle или maven). MCP **Atlassian (Jira)** и **Bitbucket** подключены (иначе стоп).
- cwd = корень репо кода (`<toplevel>`). Харнес развёрнут; preflight должен быть зелёным.
- Если тебя вызвал роутер — критичность/конфиг уже выставлены (`autonomy.auto_max_risk=R2`,
  `quality.eval_enabled=false`). Если запускаешься автономно — проверь, что они выставлены (см. §1.1).
- Ключ задачи (`[A-Z]+-\d+`) не передан — спроси один раз, провалидируй.

## 1. Архитектура (кто что делает)

| Этап | step-id | Кто | Механизм |
|---|---|---|---|
| Fetch Jira + скоуп-чек | `lite-jira` | главный агент | MCP |
| Лёгкий grounding | `lite-ground` | reuse или субагент | agent() |
| Tech-design по спеке (Gate 1) | `lite-design` | субагент (`tech-design`) | agent() |
| TDD RED | `lite-red` | субагент-тестописатель | agent() |
| TDD GREEN | `lite-green` | java-spring-dev (субагент) | agent() |
| Тесты + покрытие | `lite-verify` | субагент-раннер | agent() |
| commit/push/PR (Gate 2/3) | `lite-deliver` | главный агент | git + Bitbucket MCP |
| Отчёт в Jira (Gate 4) | `lite-report` | главный агент | Jira MCP |

> **Субагент = ЯВНЫЙ вызов `agent(subagent_type="general-purpose", ...)`.** RED-тесты / GREEN-код /
> прогон тестов не пиши inline (заблокирует `inline-phase-guard`; нет SubagentStop → молчат
> проверки). Субагент ПОСЛЕДНИМ действием сам гоняет свой детерминированный гейт и возвращает
> JSON с `step_id` и `status` (`completed` только при прохождении гейта). Хук `state-recorder`
> закрывает шаг по этому статусу. Инлайн-шаги (lite-jira/lite-deliver/lite-report) закрывает
> главный агент через `update.py`. `lite-design` — субагентная фаза (tech-design; главный агент не
> пишет tech-design.md/task-plan.json inline — заблокирует `inline-phase-guard`).

### 1.1. Инициализация (один раз)
```
python3 <project>/.gigacode/hooks/preflight.py --project <toplevel>
```
exit 0 — продолжаем; exit 1 — стоп, чинить деплой. Заведи стейт (namespace forgelite):
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/init.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --steps @<project>/.gigacode/skills/forgelite/references/manifest-steps.json
```
Автономный запуск (не из роутера) — выставь lite-конфиг через config-helper (`--project` идёт
ДО подкоманды `set`; `auto_max_risk` — sensitive, нужен `--confirm`):
```
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.auto_max_risk R2 --confirm
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.criticality medium
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set quality.eval_enabled false
```
Закрытие шага — только после прохождения гейта:
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id <id> --status completed
```

---

## 2. Fetch Jira + скоуп-чек → `lite-jira`
Через MCP получи: summary, description, **acceptance criteria**, issuetype, priority, статус,
последние 5–10 комментариев, имена вложений.

**Скоуп-чек — детерминированный (enforced: `update.py` НЕ закроет `lite-jira` без
evidence-артефакта от record_gate).** Сохрани JSON issue из MCP в файл и прогони ЧЕРЕЗ РАННЕР:
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-jira --cmd "python3 <project>/.gigacode/skills/forgelite/scripts/check_scope.py --issue-json <файл-с-issue.json>"
```
- **exit 0** — скоуп ок, продолжай.
- **exit 1** (внутри — exit 3 ESCALATE от check_scope, причины в артефакте гейта) — СТОП, спроси
  пользователя: «Задача не похожа на готовую подзадачу. Продолжить в lite или взять full
  (feature-pipeline)?» Не решай молча. Если пользователь явно сказал «продолжаем lite» —
  зафиксируй его согласие и сними гейт (это R4: сначала `record_approval.py --key
  gate-override-gate-result-lite-jira --approved-by user --reason "..."`, затем
  `override_judge.py --judge gate-result-lite-jira --reason "..."`), после чего закрывай шаг.
- Нечитаемый JSON issue — перечитай из MCP и повтори раннер.

Дополнительно останови и спроси сам, если видишь несколько независимых сценариев в одной задаче.

Закрой `lite-jira` (команда update.py как в §1.1).

## 3. Лёгкий grounding → `lite-ground`
1. Если `<toplevel>/docs/system-analysis/grounding-excerpt.json` есть — переиспользуй, закрой инлайн.
2. Иначе субагент-orientation (agent()):
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
Файл подхватывает `context-injector` (вкладывает в последующих субагентов). Reuse — закрой инлайн.

## 4. Tech-design по существующей спеке → `lite-design` (Gate 1)

Спека (SDD) для задачи **уже существует** — это source of truth. НЕ пиши BRD/SDD заново.

1. **Зафиксируй путь к спеке** (обязательное решение `sources.spec`). Интерактивно — спроси
   пользователя «где лежит спецификация задачи?» (`ask_user_question`); headless — путь предзаписан.
   Запиши артефакт:
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set sources.spec <путь-к-спеке>
   ```
   Пока `sources.spec` не записан, `gate-guard` заблокирует запись фазы `lite-design` (fail-closed).
   **Если путь не получить интерактивно** (вопрос не отрендерился — headless/форк): НЕ угадывай и
   НЕ пропускай `lite-design` (это обязательный шаг — `update.py` даст `exit 3`). Остановись и
   попроси предзапись `config.py set sources.spec <путь>` + перезапуск.
2. **Субагентом** (`tech-design`) построй `tech-design.md` + `task-plan.json` ПО ЭТОЙ спеке
   (вход — `sources.spec`, а не свежий `sdd.md`). Главный агент tech-design.md/task-plan.json inline
   НЕ пишет (заблокирует `inline-phase-guard`). Гейт: `check_taskplan.py` + `check_sdd.py --sdd
   <sources.spec>` — ЧЕРЕЗ РАННЕР `record_gate.py` (он пишет evidence; `update.py` НЕ закроет
   `lite-design` без него — слово субагента не доказательство).
   ```
   subagent_type: general-purpose
   prompt: |
     Ты — tech-design. Прочитай СУЩЕСТВУЮЩУЮ спеку (source of truth) по пути <sources.spec> и
     grounding-excerpt. Построй tech-design.md + task-plan.json по слоям СТРОГО по этой спеке
     (sdd_ref якори — на её разделы). НЕ пиши BRD/SDD заново.
     ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
     python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-design --cmd "python3 <project>/.gigacode/skills/tech-design/scripts/check_taskplan.py <путь-к-task-plan.json> && python3 <project>/.gigacode/skills/tech-design/scripts/check_sdd.py <путь-к-task-plan.json> --sdd <sources.spec>"
     Верни JSON {"step_id":"lite-design","status":"completed"} только если раннер дал exit 0.
   ```
> **Gate 1:** «Дизайн такой?» — к коду только после «да» (или предзаписи в headless).

Субагент закрывает `lite-design` сам (SubagentStop → `state-recorder`).

## 5. TDD RED — субагент → `lite-red`
Хук `tdd-guard` не даст писать `src/main/`, пока `lite-red` не закрыт.
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
4. ПОСЛЕДНИМ действием прогони RED-гейт ЧЕРЕЗ РАННЕР (он пишет evidence — без него шаг не закроется).
   Гейт ПО-ТЕСТОВЫЙ (JUnit XML прогона): должны выполниться ТОЛЬКО твои новые тесты и ВСЕ упасть —
   один red + остальные green = FAIL (зелёный новый тест вакуумен: проходит без реализации).
   Поэтому скоупь --cmd на СВОИ тест-классы:
   Gradle: python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-red --expect red --compile-cmd "./gradlew compileTestJava" --cmd "./gradlew test --tests 'FooTest' --tests 'BarTest'"
   Maven:  тот же вызов с --compile-cmd "mvn -q test-compile" --cmd "mvn -q test -Dtest=FooTest,BarTest"
   exit 0 = RED корректен (компиляция прошла, ВСЕ тесты прогона падают). Если раннер FAILED
   «компиляция упала» — это НЕ RED (чини сигнатуры/импорты); «тесты прошли» — это GREEN
   (не годится); «RED не чистый: N зелёных» — вакуумные тесты, перепиши их падающими.
Верни JSON: {"step_id":"lite-red","status":"completed|failed","tests_written":["..."],"compile_ok":true,"tests_failed":true}
status:"completed" ТОЛЬКО если compile_ok=true И tests_failed=true.
```
После ответа прочитай `git diff src/test/`. `status:"failed"` — разбери и перезапусти.
Лимит ре-итераций форсится детерминированно (`quality.max_step_reopens`, дефолт 3): `update.py`
вернёт **exit 3 (ESCALATE)** — тогда СТОП, покажи пользователю, что не сходится, и спроси.

## 6. TDD GREEN — java-spring-dev (субагент) → `lite-green`
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
   Должно пройти (exit 0 = сборка и тесты зелёные).
Верни JSON: {"step_id":"lite-green","status":"completed|failed","files_changed":["..."],"build_ok":true}
status:"completed" ТОЛЬКО если build_ok=true (сборка и тесты зелёные).
```
### 6.1. reuse-judge (advisory)
Окинь diff `src/main/` на «велосипеды» (дублирование доступных util/библиотек из grounding).
Нашёл — **покажи пользователю и предложи** заменить. Не блокирует, решает пользователь.

## 7. Тесты + покрытие — субагент → `lite-verify`
```
description: "Run tests + JaCoCo coverage gate for <JIRA-KEY>"
subagent_type: general-purpose
prompt:
Прогони тесты и детерминированный gate покрытия изменённых файлов (порог 0.80).
Корень репо: <toplevel>. Сборка: <gradle|maven>. Изменённые файлы (без тестов): <список>.
Шаги:
1. Gradle: ./gradlew test jacocoTestReport   |  Maven: mvn -q test jacoco:report
2. Покрытие НЕ глазами — прогони gate ЧЕРЕЗ РАННЕР (он пишет evidence — без него шаг не закроется):
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgelite --feature <JIRA-KEY> --step-id lite-verify --cmd "python3 <project>/.gigacode/skills/minor-defect-fix/scripts/check_coverage.py --root <toplevel> --base HEAD --threshold 0.80 --json"
   Ниже порога — допиши тесты в src/test/ (стиль соседних) и повтори раннер.
Верни JSON: {"step_id":"lite-verify","status":"completed|failed","tests":{"passed":N,"failed":N,"skipped":N},"coverage_gate":<вывод check_coverage.py>}
status:"completed" ТОЛЬКО если тесты зелёные И coverage_gate exit 0. Компайл-эррор — status:"failed","build_error":"...".
```
Лимит итераций GREEN↔verify форсится детерминированно (`quality.max_step_reopens`, дефолт 3) —
на исчерпании `update.py` вернёт exit 3 (ESCALATE): СТОП, покажи пользователю и спроси.

## 8. Deliver: ветка + commit + push + PR → `lite-deliver` (Gate 2/3)
`evidence-enforcer`/`gate-guard` не дадут `git push` до закрытия `lite-green`+`lite-verify`.

### 8.1. Коммит (Gate 2)
Стиль коммитов: `git log -30 --pretty=format:%s`. Сообщение: по стилю, «почему», с ключом Jira,
**без** `Co-Authored-By`. Это enforced: на `git push` хук `evidence-enforcer` детерминированно
проверяет HEAD-коммит (Co-Authored-By → блок; нет ключа Jira → блок) — чинить `git commit --amend`.
> **Gate 2:** «Коммитим с этим сообщением? (да / правки)».
`git add` только нужное. Ветка `feature/<JIRA-KEY>` (от default-ветки).

### 8.2. Push + PR (Gate 3)
> **Gate 3:** «Пушим и создаём PR в Bitbucket? (да / только push / нет)».
```
git push -u origin feature/<JIRA-KEY>
```
PR через Bitbucket MCP: title = первая строка коммита; description = будущий отчёт (§9) + ссылка
на задачу; target = default-ветка. Запомни PR URL и short-sha. Закрой `lite-deliver`.

## 9. Отчёт в Jira → `lite-report` (Gate 4)
Черновик комментария (Markdown):
```markdown
**Что сделано:** суть реализации по AC.
**Изменённые файлы:** `path/File.java` — суть …
**Тесты:** добавлены `FooTest#…`; покрытие изменённых файлов NN% (порог 80%).
**Код:** ветка `feature/<JIRA-KEY>`; PR <url>; коммит <short sha>.
```
> **Gate 4:** «Отправить комментарий в Jira? (да / отредактировать / не отправлять)».
После «да» — MCP `*jira*add*comment*`. Закрой `lite-report`.

---

## Карта MCP
| Действие | Паттерн инструмента |
|---|---|
| Jira issue | `*jira*get*issue*`, `*atlassian*issue*` |
| Комментарий | `*jira*add*comment*` |
| PR | `*bitbucket*create*pull*request*` |
Не угадывай — бери первый подходящий из доступных.

## Что НЕ делать
- Необратимое (commit/push/PR/Jira) — только после «да» (Gate 2–4).
- RED/GREEN/прогон — только субагентом (иначе `inline-phase-guard`).
- Не обходи через `git push --force`/`reset --hard`/`checkout .`.
- Не пиши BRD и не пиши SDD с нуля, не ставь задачи в Jira (это full-путь feature-pipeline).
  Tech-design (`lite-design`) строй ТОЛЬКО по существующей спеке (`sources.spec`), а не заново.
  Спеку в отдельном репо не бери (это minor-defect-fix).

## Связь
Ветка forge: вызывается роутером (`skills/router`) при выборе «готовая задача», full-путь —
`feature-pipeline`. Разработчик GREEN — `java-spring-dev`. Стейт — `pipeline-state` (namespace
`forgelite`).
