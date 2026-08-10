---
name: forgefix
description: >
  Fix-ветка forge для МИНОРНОГО ДЕФЕКТА (баг из Jira или описанием): диагностика root cause →
  RED-тест, воспроизводящий баг → минимальный фикс → прогон с регрессом и покрытием →
  ТОЧЕЧНАЯ дельта-правка спеки (правки существующих требований, а не написание SDD заново).
  Без BRD, без SDD с нуля, без tech-design.md и без постановки задач в Jira: мини-план фикса
  (одна задача) пишет фаза диагностики. Коммиты/push/PR/отчёты в Jira пайплайн НЕ делает —
  их выполняет пользователь сам после завершения. Используй когда пользователь говорит
  «почини баг», «исправь дефект», «fix STOR-123», «баг из jira», «не работает X — поправь»,
  или передаёт тикет типа Bug. Отличие от feature-pipeline (full) — тот пишет спецификацию
  с нуля для НОВОЙ фичи; от forgelite (lite) — тот исполняет готовую подзадачу по спеке и
  спеку не правит. Обычно вызывается роутером (skills/router), работает и автономно.
---

# Forgefix — fix-ветка: минорный дефект

> **Пути** — `feature-pipeline/references/skill-paths.json` (общие скрипты forge) и локально
> `references/manifest-steps.json`. Зовём как `python3 <project>/.gigacode/<path>`.
> `<project>` = корень репо кода (там же `ground/`). Не используй `~/.gigacode/...`.

> **Рантайм — форк GigaCode (Qwen). Жёсткие правила:**
> - Хуки за флагом запуска: `gigacode --experimental-hooks -p "..."` (иначе `0 hook entries`).
> - В командах — только однострочные, без `$(...)` и обратных кавычек (рантайм режет).
> - Тяжёлую фазу — только через `agent(subagent_type="general-purpose", ...)`. `agent()` и
>   `ask_user_question` не активны одновременно.

Плоский цикл (feature = ключ Jira либо slug; стейт в namespace `forgefix`):
**вход → диагностика → RED → GREEN → verify → дельта спеки**. На этом пайплайн заканчивается:
commit/push/PR/отчёт в Jira делает пользователь сам.

Шаги стейта (`fix-*`, отдельно от `04-*` full-пути и `lite-*`):
`fix-intake → fix-diag → fix-red → fix-green → fix-verify → fix-spec`.

> **Философия ветки — минимальное изменение.** Ни код, ни спека не переписываются: фикс правит
> то, что сломано, дельта спеки правит те требования, которые баг показал неверными. Grounding
> целиком, BRD, SDD с нуля, tech-design.md, задачи в Jira и eval-план на этом пути **не
> производятся** — это full-путь (`feature-pipeline`). Если в ходе работы выясняется, что задача
> тянет на фичу — СТОП и смена пути, а не расширение фикса.

---

## 0. Предусловия

- Java/Spring (gradle или maven). MCP **Atlassian (Jira)** — опционален: нет MCP → баг описывает
  пользователь текстом.
- cwd = корень репо кода (`<toplevel>`). Харнес развёрнут; preflight зелёный.
- Ключ задачи (`[A-Z]+-\d+`) — если он есть, используем как `--feature`. Нет тикета — спроси у
  пользователя короткий slug вида `fix-npe-empty-email` и используй его.
- Если тебя вызвал роутер — конфиг уже выставлен. Автономно — выставь сам (§1.1).

## 1. Архитектура (кто что делает)

| Этап | step-id | Кто | Механизм |
|---|---|---|---|
| Вход + скоуп-чек «это правда минорный баг» | `fix-intake` | главный агент | MCP/текст + гейт |
| Диагностика: root cause + мини-план | `fix-diag` | субагент (`defect-analyzer`) | agent() |
| RED: тест воспроизводит баг | `fix-red` | субагент-тестописатель | agent() |
| GREEN: минимальный фикс | `fix-green` | субагент (`bugfix-developer`) | agent() |
| Прогон + регресс + покрытие | `fix-verify` | субагент-раннер | agent() |
| Дельта-правка спеки | `fix-spec` | субагент-спецадаптер | agent() |

> **Субагент = ЯВНЫЙ вызов `agent(subagent_type="general-purpose", ...)`.** Диагностику,
> RED-тесты, код фикса, прогон и правку спеки inline не делай — заблокирует `inline-phase-guard`,
> и без `SubagentStop` молчат проверки. Субагент ПОСЛЕДНИМ действием сам гоняет свой
> детерминированный гейт через раннер и возвращает JSON с `step_id` и `status` (`completed`
> только при exit 0 раннера). Инлайн-шаг (`fix-intake`) закрывает главный агент через `update.py`.

### 1.1. Инициализация (один раз)
```
python3 <forge>/hooks/preflight.py --project <toplevel>
```
> **`<forge>` — корень кода форжа: каталог на два уровня выше этого SKILL.md** (`<project>/.gigacode`
> при legacy-деплое либо корень extension'а). preflight печатает его в `layout.base`; ниже любой
> путь `<project>/.gigacode/skills/...` читай как `<forge>/skills/...`.

exit 0 — продолжаем; exit 1 — стоп, чинить деплой/установку. Заведи стейт (namespace forgefix):
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/init.py --project <toplevel> --skill forgefix --feature <KEY|slug> --steps @<project>/.gigacode/skills/forgefix/references/manifest-steps.json
```
Автономный запуск (не из роутера) — выставь fix-конфиг (`--project` ДО подкоманды `set`;
`auto_max_risk` — sensitive, нужен `--confirm`):
```
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode fix
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.auto_max_risk R2 --confirm
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.criticality medium
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set quality.eval_enabled false
```
Закрытие шага — только после прохождения гейта:
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id <id> --status completed
```

**Папка артефактов фикса** `<docs>/feature-pipeline/<KEY|slug>/` (тот же резолвер `docs.*`, что у
остальных веток): `fix-plan.md` + `task-plan.json` (шаг `fix-diag`) и `sdd.md` — дельта спеки
(шаг `fix-spec`). Больше фикс ничего не производит.

---

## 2. Вход + скоуп-чек → `fix-intake`

Есть Jira MCP и ключ — прочитай issue: summary, description, issuetype, priority, статус,
последние 5–10 комментариев, имена вложений. Нет MCP/тикета — попроси у пользователя описание:
**симптом, шаги воспроизведения, ожидаемое поведение** (без этого чинить нечего).

**Скоуп-чек — детерминированный** (enforced: `update.py` НЕ закроет `fix-intake` без evidence от
`record_gate`). Он отвечает на два вопроса сразу: это вообще дефект (а не фича) и он минорный.
Сохрани JSON issue в файл (либо описание — в текстовый файл) и прогони ЧЕРЕЗ РАННЕР:
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-intake --cmd "python3 <project>/.gigacode/skills/forgefix/scripts/check_fix_scope.py --issue-json <файл-с-issue.json>"
```
(без Jira — тот же вызов с `--text-file <файл-с-описанием.txt>`)

- **exit 0** — минорный дефект, продолжай fix.
- **exit 1** (внутри exit 3 ESCALATE, причины в артефакте гейта) — СТОП, спроси пользователя:
  «Похоже, это не минорный дефект (причины: …). Взять fix, lite (готовая подзадача по спеке) или
  full (фича с нуля)?» Не решай молча. Явное «продолжаем fix» — это R4: сначала
  `record_approval.py --key gate-override-gate-result-fix-intake --approved-by user --reason "..."`,
  затем `override_judge.py --judge gate-result-fix-intake --reason "..."`, после чего закрывай шаг.
- Нечитаемый вход — перечитай issue из MCP и повтори раннер.

Закрой `fix-intake` (`update.py`, см. §1.1).

## 3. Диагностика + мини-план → `fix-diag`

Один субагент делает две вещи: разбирает дефект по методике `defect-analyzer` (он read-only) и
уже от себя записывает **мини-план фикса** — это и есть «тех-дизайн» фикса. Полноценный
`tech-design.md` на этом пути не пишется.

```
description: "Diagnose defect <KEY> + write fix plan"
subagent_type: general-purpose
prompt:
Сначала прочитай и строго следуй методике анализа:
read_file("<project>/.gigacode/skills/defect-analyzer/SKILL.md")
(он read-only — это ограничение фазы АНАЛИЗА; записать мини-план ниже ты обязан уже как fix-planner).
Корень репо: <toplevel>. Дефект: <summary> / симптом + шаги воспроизведения + ожидаемое: <описание>.
Grounding: если есть <toplevel>/docs/system-analysis/grounding-excerpt.json — возьми оттуда модули и
конвенции. НЕТ — не запускай полный скан: грепни прицельно по именам классов/методов/ошибок из тикета.
Шаги:
1. Локализуй место правки (файл:строка), сформулируй root cause [подтверждено|гипотеза].
2. Найди затронутые тесты (пути) и стиль тестовой базы.
3. Найди, к какой СТОРИ и какому ТРЕБОВАНИЮ мастера относится баг — детерминированно, не глазами:
   python3 <project>/.gigacode/skills/forgefix/scripts/find_spec_anchor.py --project-root <toplevel> --issue-json <файл-с-issue.json> --changed-file <файл из п.1> --json
   (`--changed-file` повтори для каждого места правки из п.1; без Jira — просто опусти `--issue-json`;
   если слаг стори уже известен — сразу добавь `--story <KEY>`, он сильнее всех прочих признаков)
   Скрипт сводит машинные источники: названную стори (её провенанс `[from:]` в мастере + её
   собственная дельта `sdd.md`), старые `task-plan.json` (какая стори заводила/трогала эти файлы)
   и связи Jira бага (parent/links/epic).
   - **exit 0** — якорь однозначен, возьми `anchor.id` и `anchor.title`.
   - **exit 3** — кандидатов несколько или ноль: НЕ выбирай сам. Верни в `open_questions`
     СНАЧАЛА простой вопрос **«по какой стори этот баг? (ключ, напр. STOR-100; не знаешь — так и
     скажи)»** — человек помнит стори, а не ID требований. Список `<REQ-ID>: <название>` со score
     и evidence приложи как второй вопрос (плюс вариант «в спеке не описано»).
4. Запиши <docs>/feature-pipeline/<KEY|slug>/fix-plan.md — не больше 15 строк:
   что сломано → что должно быть, где правим, подход в одном предложении, edge cases, риск регресса.
5. Запиши <docs>/feature-pipeline/<KEY|slug>/task-plan.json — мини-план из ОДНОЙ задачи:
   {"feature_slug":"<KEY|slug>","title":"<кратко>","tasks":[{"id":"F1","title":"...",
    "layers":["service"],"artifacts":["src/main/java/..."],"depends_on":[],
    "acceptance":["Given <условие бага> When <действие> Then <корректное поведение>"],
    "sdd_ref":"<название требования из п.3 или 'в спеке не описано'>"}]}
   Слои — из словаря task-plan (migration|entity|repository|dto|mapper|service|controller|scheduler).
   Задача ОДНА: фикс не декомпозируется. Нужна вторая — значит это не минорный дефект: верни
   status:"failed" с причиной, оркестратор сменит путь.
6. ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-diag --cmd "python3 <project>/.gigacode/skills/tech-design/scripts/check_taskplan.py <docs>/feature-pipeline/<KEY|slug>/task-plan.json"
Код НЕ правь. Верни JSON: {"step_id":"fix-diag","status":"completed|failed","root_cause":"...",
"files":["file:line"],"affected_tests":["..."],
"spec_anchor":{"status":"resolved|ambiguous|not_found","id":"<REQ-ID или null>","title":"<название или null>",
"candidates":[{"id":"...","title":"...","score":N,"evidence":["..."]}]},
"open_questions":["..."]}
```

**Зафиксируй якорь — это обязательное решение.** После ответа субагента:
- `spec_anchor.status = resolved` → запиши найденный ID;
- иначе — **лестница вопросов, от дешёвого к точному**:
  1. спроси у пользователя слаг основной стори (обычный текстовый вопрос — `ask_user_question`
     требует 2–4 варианта и для свободного ответа не годится). Ответ запиши и перезапусти
     резолвер с ним:
     ```
     python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set sources.story <KEY>
     python3 <project>/.gigacode/skills/forgefix/scripts/find_spec_anchor.py --project-root <toplevel> --story <KEY> --issue-json <файл> --changed-file <файл> --json
     ```
     Часто этого хватает: у стори одно требование в мастере → exit 0.
  2. всё ещё неоднозначно — спроси `ask_user_question` из ОСТАВШИХСЯ кандидатов (их уже мало:
     резолвер отбросил требования чужих стори) + вариант «в спеке не описано»; показывай evidence.
  Сам не выбирай ни на одном шаге.
```
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set sources.spec_anchor <REQ-ID|none>
```
Пока `sources.spec_anchor` не записан, `gate-guard` заблокирует запись дельты на фазе `fix-spec`
(fail-closed). Вопрос не отрендерился (headless/форк) — НЕ угадывай: остановись и попроси
предзапись `config.py set sources.spec_anchor <...>` + перезапуск.

Есть другие `open_questions` — задай их так же и перезапусти субагента с `answers`, не додумывай
сам. Субагент закрывает `fix-diag` сам (SubagentStop → `state-recorder`).

## 4. RED: тест воспроизводит баг → `fix-red`

Хук `tdd-guard` не даст писать `src/main/java/`, пока `fix-red` не закрыт. Задача не про код
(вся правка в `src/main/resources` — yml/changeset) — **не заводи шаг `fix-red`**, иди в
`fix-green` (без шага в манифесте хук пропустит запись как test-exempt).

```
description: "RED test reproducing defect <KEY>"
subagent_type: general-purpose
prompt:
Сначала прочитай и строго следуй: read_file("<project>/.gigacode/skills/test-writer/SKILL.md") (режим RED).
Конвенции тестовой базы (первый вызов сканирует, дальше кэш):
python3 <project>/.gigacode/skills/test-writer/scripts/analyze_tests.py --root <toplevel> --if-missing
Напиши тест, ВОСПРОИЗВОДЯЩИЙ баг: он должен падать на текущем коде и позеленеть после фикса.
Корень репо: <toplevel>. Сборка: <gradle|maven>.
Дефект: <symptom> / ожидаемое: <expected>. Root cause и место: <из fix-diag>.
Соседние тесты (стиль, фикстуры): <из fix-diag>.
Правила:
1. Тест — в существующий тест-класс затронутого кода, если он есть (не плоди новый класс без нужды).
2. Один регресс-тест на симптом + edge cases из fix-diag. Без @Disabled. Не трогай src/main/.
3. Не ослабляй и не переписывай соседние тесты, чтобы «стало зелено».
4. ПОСЛЕДНИМ действием прогони RED-гейт ЧЕРЕЗ РАННЕР (он пишет evidence — без него шаг не закроется).
   Гейт ПО-ТЕСТОВЫЙ: должны выполниться ТОЛЬКО твои новые тесты и ВСЕ упасть.
   Gradle: python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-red --expect red --compile-cmd "./gradlew compileTestJava" --cmd "./gradlew test --tests 'FooTest'"
   Maven:  тот же вызов с --compile-cmd "mvn -q test-compile" --cmd "mvn -q test -Dtest=FooTest"
   exit 0 = RED корректен. «компиляция упала» — чини сигнатуры/импорты; «тесты прошли» — тест НЕ
   воспроизводит баг (перепиши: он обязан падать ровно из-за дефекта, а не из-за опечатки).
Верни JSON: {"step_id":"fix-red","status":"completed|failed","tests_written":["..."],
"compile_ok":true,"tests_failed":true,"reproduces":"<чем именно падает — сообщение/ассерт>"}
status:"completed" ТОЛЬКО если compile_ok=true И tests_failed=true.
```
Прочитай `git diff src/test/`. Убедись, что тест падает **по причине дефекта** (сообщение из
`reproduces` совпадает с симптомом), а не по кривому моку — это единственная защита от
«зелёного фикса несуществующего бага». `status:"failed"` — разбери и перезапусти. Лимит
ре-итераций форсится детерминированно (`quality.max_step_reopens`, дефолт 3): `update.py` вернёт
**exit 3 (ESCALATE)** — СТОП, покажи пользователю и спроси.

## 5. GREEN: минимальный фикс → `fix-green`
```
description: "Minimal fix for <KEY> (bugfix-developer)"
subagent_type: general-purpose
prompt:
Сначала прочитай и строго следуй: read_file("<project>/.gigacode/skills/bugfix-developer/SKILL.md")
(принципы багфикса: минимальное изменение, не подметать рядом, публичные сигнатуры не менять).
Нужны конвенции проекта (Lombok, пакеты, слои) — точечно: read_file("<project>/.gigacode/skills/java-spring-dev/SKILL.md").
Реализуй фикс, чтобы RED-тест позеленел. Корень репо: <toplevel>.
Дефект и root cause: <из fix-diag>. Место правки: <file:line>. RED-тесты: <из fix-red>.
Правила:
1. Минимальное изменение под root cause. Не рефактори соседнее, не вводи абстракций,
   не добавляй логирование/try-catch «на всякий случай».
2. Правь только src/main/. Тесты уже есть — не ослабляй их и не подгоняй под зелёное.
3. Публичные сигнатуры и контракты API не меняй; понадобилось — это не минорный фикс,
   верни status:"failed" с причиной.
4. ПОСЛЕДНИМ действием прогони BUILD-гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
   Gradle: python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-green --cmd "./gradlew build"
   Maven:  тот же вызов с --cmd "mvn -q verify"
Верни JSON: {"step_id":"fix-green","status":"completed|failed","files_changed":["..."],"build_ok":true}
status:"completed" ТОЛЬКО если build_ok=true.
```
Посмотри `git diff src/main/`: правка должна быть соразмерна дефекту. Разрослась (новые классы,
переезд логики) — останови и покажи пользователю: возможно, путь выбран неверно.

## 6. Прогон + регресс + покрытие → `fix-verify`
```
description: "Run tests + regression guard + coverage for <KEY>"
subagent_type: general-purpose
prompt:
Прогони тесты, регресс-гейт затронутых модулей и гейт покрытия изменённых файлов (порог 0.80).
Корень репо: <toplevel>. Сборка: <gradle|maven>. Изменённые файлы (без тестов): <список>.
Шаги:
1. Gradle: ./gradlew test jacocoTestReport   |  Maven: mvn -q test jacoco:report
2. ЕДИНЫМ РАННЕРОМ прогони составной гейт (он пишет evidence — без него шаг не закроется).
   Первый — module_tests.py guard: он через git stash снимает эталон «зелёного ДО» по ВСЕМ
   модулям, затронутым диффом, и сверяет. Сломал тест соседнего сервиса = FAIL.
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-verify --timeout 3000 --cmd "python3 <project>/.gigacode/skills/feature-pipeline/scripts/module_tests.py guard --root <toplevel> --base HEAD && python3 <project>/.gigacode/skills/minor-defect-fix/scripts/check_coverage.py --root <toplevel> --base HEAD --threshold 0.80 --json"
   Регресс — НЕ подгоняй тест/код под зелёное: найди настоящую причину. Ниже порога — допиши
   тесты в src/test/ (стиль соседних) на ветки, которые ввёл фикс. После правок повтори раннер.
Верни JSON: {"step_id":"fix-verify","status":"completed|failed","tests":{"passed":N,"failed":N,
"skipped":N},"regression_ok":true|false,"coverage_gate":<вывод check_coverage.py>}
status:"completed" ТОЛЬКО если составной раннер вернул exit 0.
```
Лимит итераций GREEN↔verify форсится (`quality.max_step_reopens`): exit 3 = СТОП и вопрос
пользователю.

## 7. Дельта-правка спеки → `fix-spec`

**Это то, ради чего fix — отдельный путь.** Спека не пишется заново: правятся ТЕ требования,
которые баг показал неверными или неполными, и в них добавляется регресс-сценарий.

```
description: "Spec delta for <KEY> (точечная правка требований)"
subagent_type: general-purpose
prompt:
Сначала прочитай и строго следуй: read_file("<project>/.gigacode/skills/forgefix/references/fix-delta-template.md")
Задача: записать ДЕЛЬТУ спеки фикса — <docs>/feature-pipeline/<KEY|slug>/sdd.md.
Корень репо: <toplevel>.
Что чинили: <root cause + суть фикса, 2-3 предложения>.
ЯКОРЬ (зафиксированное решение sources.spec_anchor): <REQ-ID и название | none>.
Регресс-сценарий (из fix-red, дословно по смыслу теста): <Given … When … Then …>.
Diff фикса (только src/main): <git diff HEAD -- src/main>.
Шаги:
1. Открой мастер требований и найди в нём требование ЯКОРЯ (по ID):
   python3 <project>/.gigacode/skills/system-analyst/scripts/spec_cli.py --project-root <toplevel> status
   Якорь = none → требования нет в спеке, заводишь ОДНО новое (название по сути поведения).
   Мастер выключен/не заведён — пиши дельту без якоря и отметь это в summary.
2. Скопируй блок требования ЦЕЛИКОМ (название дословно + утверждение + ВСЕ его сценарии),
   внеси точечную правку и добавь регресс-сценарий. Не переписывай требование заново.
   Баг вскрыл поведение, которого в спеке нет вовсе — заведи ОДНО новое требование.
3. Больше ничего в дельту не клади: ни архитектуру, ни NFR, ни код, ни описание самого фикса.
4. ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-spec --cmd "python3 <project>/.gigacode/skills/forgefix/scripts/check_fix_delta.py <docs>/feature-pipeline/<KEY|slug>/sdd.md --plan <docs>/feature-pipeline/<KEY|slug>/task-plan.json --project-root <toplevel> --anchor <REQ-ID|none>"
   FAIL «не правит зафиксированный якорь» — название требования в дельте разошлось с якорем.
   FAIL «сценарии будут потеряны» — ты не перенёс сценарии мастера, вернись к п.2.
   FAIL «лимит требований/строк» — ты переписал спеку, оставь только затронутое.
Верни JSON: {"step_id":"fix-spec","status":"completed|failed","delta":"<путь к sdd.md>",
"requirements":["<названия>"],"anchored":true|false,"summary":"1-2 предложения"}
```

### 7.1. Итог пользователю (финал пайплайна)
Покажи план слияния дельты в мастер — **сам не сливай** (merge правит рабочее дерево чужого
репо и операция `~` требует решения человека):
```
python3 <project>/.gigacode/skills/system-analyst/scripts/spec_cli.py --project-root <toplevel> diff <KEY|slug>
```
Выведи итог: что сломано и как починено, изменённые файлы, тесты/покрытие, и строку-подсказку —
дельта в мастер не слита, слить командой `/forge-spec merge <KEY|slug> --allow-modify` (операции
`~` — это и есть точечная правка требований; точечно — `--modify <ID>`). Коммит, push, PR и
комментарий в Jira пайплайн НЕ делает — их выполняет пользователь сам.

---

## Карта MCP
| Действие | Паттерн инструмента |
|---|---|
| Jira issue | `*jira*get*issue*`, `*atlassian*issue*` |
Не угадывай — бери первый подходящий из доступных.

## Что НЕ делать
- Не писать SDD с нуля, tech-design.md, BRD и не ставить задачи в Jira — это full-путь.
- Не переписывать требования спеки заново: дельта = правка существующих (гейт форсит).
- Не выбирать якорь «на глаз», когда `find_spec_anchor.py` дал ambiguous/not_found — это решение
  пользователя. Молча заведённое новое требование раздваивает мастер.
- Не расширять фикс («заодно отрефакторил») — минимальное изменение, соразмерное дефекту.
- Не коммитить, не пушить, не создавать PR и не писать в Jira — доставку делает пользователь.
- Диагностику/RED/GREEN/прогон/дельту не делать inline (заблокирует `inline-phase-guard`).
- Не обходить проблему через `reset --hard` / `checkout .` / ослабление тестов.

## Связь
Ветка forge: вызывается роутером (`skills/router`) при выборе «баг»; фича с нуля —
`feature-pipeline`, готовая подзадача по спеке — `forgelite`. Анализ — `defect-analyzer`,
правка кода — `bugfix-developer`, тесты — `test-writer`. Стейт — `pipeline-state`
(namespace `forgefix`). Мастер требований — `/forge-spec`.
