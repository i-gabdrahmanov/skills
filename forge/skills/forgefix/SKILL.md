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
- **Стори, к которой относится баг** (`sources.story`) — спрашивается на `fix-intake` (§2).
  Фикс не заводит новую фичу: он живёт ВНУТРИ папки своей стори.
- Если тебя вызвал роутер — конфиг уже выставлен. Автономно — выставь сам (§1.1).

> **Два обязательных вопроса пользователю на этом пути** (оба форсятся хуками, не «по совести»):
> **(1)** к какой стори относится баг — `fix-diag` не сможет писать без `sources.story`;
> **(2)** утверждение мини-плана фикса — `fix-red`/`fix-green` не смогут писать без
> approval-маркера `fix-plan-<KEY|slug>` (§3.1). Молча уйти писать код нельзя.

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
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode_task <KEY|slug>
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.auto_max_risk R2 --confirm
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.criticality medium
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set quality.eval_enabled false
```
Закрытие шага — только после прохождения гейта:
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id <id> --status completed
```

**Папка артефактов фикса — РЕЗОЛВНИ ЕЁ ПОСЛЕ ОТВЕТА ПРО СТОРИ (§2), не подставляй плейсхолдер:**
```
python3 <project>/.gigacode/skills/feature-pipeline/scripts/skill_paths.py fix-docs --project <toplevel> --feature <KEY|slug> --story <STORY|none>
```
Команда печатает абсолютный путь по `docs.*`: со стори →
`<docs>/feature-pipeline/<STORY>/fixes/<KEY|slug>`, со `none` → плоский `<docs>/feature-pipeline/<KEY|slug>`
(separate-repo — тот же путь внутри внешнего репо спеки). Дальше в этом брифе `<fixdir>` = ровно
этот путь; подставляй его субагентам целиком, они конфиг не читают.

Слаг дельты для `/forge-spec` (понадобится в §7.1) — оттуда же:
```
python3 <project>/.gigacode/skills/feature-pipeline/scripts/skill_paths.py fix-docs --project <toplevel> --feature <KEY|slug> --story <STORY|none> --print-slug
```

Туда и только туда пишутся: `fix-plan.md` + `task-plan.json` (шаг `fix-diag`) и `sdd.md` — дельта
спеки (шаг `fix-spec`). Больше фикс ничего не производит.

> **Почему внутрь стори.** Фикс — не фича: он правит поведение, которое стори уже описала.
> Папка `<STORY>/fixes/<баг>` держит эту связь явной (в `docs/feature-pipeline` баги не стоят в
> одном ряду со стори), а `find_spec_anchor` использует прошлые фиксы стори как свидетельство при
> поиске якоря. В мастер дельта всё равно уходит правкой требования стори — по якорю (§7).

> ⛔ **Каталог харнеса — не место для артефактов.** `<project>/.gigacode/skills/...` (или корень
> extension'а) — это КОД форжа, общий на все проекты; артефакт задачи, записанный туда, уедет в
> следующий проект и подменит бриф фазы. `state-write-guard` блокирует такую запись во время
> прогона (exit 2). Не знаешь путь — выполни команду выше, а не пиши рядом со SKILL.md.

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

### 2.1. Вопрос про стори (до закрытия `fix-intake`)

Спроси **обычным текстом** (это свободный ответ — `ask_user_question` требует 2–4 варианта и не
годится): «**К какой стори/фиче относится этот баг?** Дай ключ (напр. STOR-100) — фикс ляжет
внутрь её папки и правкой её требования. Не знаешь — ответь “не знаю”.»

Ключ стори уже назван в аргументе команды («по стори STOR-100») или однозначно виден в Jira
(`parent`/эпик/линк типа *relates to* на Story) — не переспрашивай, назови его пользователю
одной строкой («беру стори STOR-100 — поправь, если не она») и записывай. Запиши ответ:
```
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set sources.story <STORY|none>
```
`none` — осознанный ответ «стори неизвестна» (папка будет плоской, якорь спеки ищется по коду и
связям Jira). Пока ключ не записан, `gate-guard` не даст `fix-diag` писать (fail-closed) — не
обходи это, а получи ответ. Вопрос не отрендерился (headless/форк) — остановись и попроси
предзапись `config.py set sources.story <...>` + перезапуск.

Теперь резолвни `<fixdir>` (§1.1) — путь зависит от ответа.

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
3. Найди, какое ТРЕБОВАНИЕ мастера чинит баг — детерминированно, не глазами:
   python3 <project>/.gigacode/skills/forgefix/scripts/find_spec_anchor.py --project-root <toplevel> --story <STORY> --issue-json <файл-с-issue.json> --changed-file <файл из п.1> --json
   (`--story` — стори из §2.1, она сильнее всех прочих признаков; ответ был «не знаю» — опусти флаг.
   `--changed-file` повтори для каждого места правки из п.1; без Jira — опусти `--issue-json`)
   Скрипт сводит машинные источники: названную стори (её провенанс `[from:]` в мастере + её
   собственная дельта `sdd.md`), старые `task-plan.json` (какая стори заводила/трогала эти файлы)
   и связи Jira бага (parent/links/epic).
   - **exit 0** — якорь однозначен, возьми `anchor.id` и `anchor.title`.
   - **exit 3** — кандидатов несколько или ноль: НЕ выбирай сам. Верни в `open_questions`
     СНАЧАЛА простой вопрос **«по какой стори этот баг? (ключ, напр. STOR-100; не знаешь — так и
     скажи)»** — человек помнит стори, а не ID требований. Список `<REQ-ID>: <название>` со score
     и evidence приложи как второй вопрос (плюс вариант «в спеке не описано»).
4. Запиши <fixdir>/fix-plan.md — не больше 15 строк:
   что сломано → что должно быть, где правим, подход в одном предложении, edge cases, риск регресса.
5. Запиши <fixdir>/task-plan.json — мини-план из ОДНОЙ задачи:
   {"feature_slug":"<KEY|slug>","title":"<кратко>","tasks":[{"id":"F1","title":"...",
    "layers":["service"],"artifacts":["src/main/java/..."],"depends_on":[],
    "acceptance":["Given <условие бага> When <действие> Then <корректное поведение>"],
    "sdd_ref":"<название требования из п.3 или 'в спеке не описано'>"}]}
   Слои — из словаря task-plan (migration|entity|repository|dto|mapper|service|controller|scheduler).
   Задача ОДНА: фикс не декомпозируется. Нужна вторая — значит это не минорный дефект: верни
   status:"failed" с причиной, оркестратор сменит путь.
6. ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-diag --cmd "python3 <project>/.gigacode/skills/tech-design/scripts/check_taskplan.py <fixdir>/task-plan.json"
Код НЕ правь. Верни JSON: {"step_id":"fix-diag","status":"completed|failed","root_cause":"...",
"files":["file:line"],"affected_tests":["..."],
"spec_anchor":{"status":"resolved|ambiguous|not_found","id":"<REQ-ID или null>","title":"<название или null>",
"candidates":[{"id":"...","title":"...","score":N,"evidence":["..."]}]},
"open_questions":["..."]}
```

**Зафиксируй якорь — это обязательное решение.** После ответа субагента:
- `spec_anchor.status = resolved` → запиши найденный ID;
- иначе — **лестница вопросов, от дешёвого к точному**:
  1. стори на §2.1 ответили «не знаю» — спроси её ещё раз, теперь уже зная место правки
     (обычный текстовый вопрос). Ответ запиши и перезапусти резолвер с ним:
     ```
     python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set sources.story <KEY>
     python3 <project>/.gigacode/skills/forgefix/scripts/find_spec_anchor.py --project-root <toplevel> --story <KEY> --issue-json <файл> --changed-file <файл> --json
     ```
     Часто этого хватает: у стори одно требование в мастере → exit 0. Стори появилась только
     сейчас — перерезолвни `<fixdir>` (§1.1) и перенеси уже записанные `fix-plan.md`/`task-plan.json`
     в папку стори, чтобы дельта не осталась плоской.
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

### 3.1. Гейт фикс-плана — утверждение тех-решения (ОБЯЗАТЕЛЬНО, enforced)

Мини-план фикса — это тех-решение по дефекту, и оно утверждается человеком, как `Гейт 2`
утверждает `tech-design.md` на full-пути. **Без approval-маркера `gate-guard` не даст фазам
`fix-red`/`fix-green` писать ни тест, ни код** (deny, exit 2) — это не «желательно спросить».

1. Прочитай `<fixdir>/fix-plan.md` и покажи пользователю **резюме на 5–7 строк**:
   что сломано → что должно быть · root cause (подтверждён/гипотеза) · где правим (файл:строка) ·
   подход одним предложением · какое требование спеки затронуто (якорь) · риск регресса ·
   что НЕ трогаем.
2. Спроси `ask_user_question`: «Делаем так?» — варианты: **«Да, делаем»** / **«Правки в план»** /
   **«Это не минорный фикс — сменить путь»**.
3. Ответ:
   - **«Да»** — только теперь фиксируй согласие:
     ```
     python3 <project>/.gigacode/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key fix-plan-<KEY|slug> --approved-by user --reason "<кратко: что утвердили>"
     ```
     (`<KEY|slug>` — ровно тот, что в `--feature`; ключ маркера строит `gate-guard` из активной
     фичи. Прямая запись в `ground/approvals/` заблокирована `state-write-guard`.)
   - **«Правки»** — верни `fix-diag` субагенту с правками (переоткрытие шага считается,
     `quality.max_step_reopens`). Маркер НЕ выписывай, пока план не согласован.
   - **«Сменить путь»** — СТОП, это lite или full: новый прогон в другом namespace.
4. Вопрос не отрендерился (headless/форк) — НЕ выписывай маркер сам: остановись и попроси
   пользователя либо ответить, либо предзаписать согласие тем же `record_approval.py` до прогона.

> Маркер — аудит-след согласия человека, а не формальность: `record_approval` штампует провенанс,
> и рукописный `approvals/*.json` гейт не снимает.

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
Задача: записать ДЕЛЬТУ спеки фикса — <fixdir>/sdd.md.
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
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgefix --feature <KEY|slug> --step-id fix-spec --cmd "python3 <project>/.gigacode/skills/forgefix/scripts/check_fix_delta.py <fixdir>/sdd.md --plan <fixdir>/task-plan.json --project-root <toplevel> --anchor <REQ-ID|none>"
   FAIL «не правит зафиксированный якорь» — название требования в дельте разошлось с якорем.
   FAIL «сценарии будут потеряны» — ты не перенёс сценарии мастера, вернись к п.2.
   FAIL «лимит требований/строк» — ты переписал спеку, оставь только затронутое.
Верни JSON: {"step_id":"fix-spec","status":"completed|failed","delta":"<путь к sdd.md>",
"requirements":["<названия>"],"anchored":true|false,"summary":"1-2 предложения"}
```

### 7.1. Итог пользователю (финал пайплайна)
Покажи план слияния дельты в мастер — **сам не сливай** (merge правит рабочее дерево чужого
репо и операция `~` требует решения человека). `<fixslug>` — слаг дельты из §1.1
(`--print-slug`; со стори это `<STORY>/fixes/<KEY|slug>`):
```
python3 <project>/.gigacode/skills/system-analyst/scripts/spec_cli.py --project-root <toplevel> diff <fixslug>
```
Выведи итог: что сломано и как починено, изменённые файлы, тесты/покрытие, и строку-подсказку —
дельта в мастер не слита, слить командой `/forge-spec merge <fixslug> --allow-modify` (операции
`~` — это и есть точечная правка требований; точечно — `--modify <ID>`). Коммит, push, PR и
комментарий в Jira пайплайн НЕ делает — их выполняет пользователь сам.

> В мастере правка уедет с провенансом `[from: <STORY> fix/<KEY>]` — требование остаётся за
> стори, а фикс виден как её правка. Короткого имени бага для `merge`/`diff` тоже достаточно,
> пока оно однозначно.

---

## Карта MCP
| Действие | Паттерн инструмента |
|---|---|
| Jira issue | `*jira*get*issue*`, `*atlassian*issue*` |
Не угадывай — бери первый подходящий из доступных.

## Что НЕ делать
- Не уходить в RED/GREEN, не утвердив мини-план у пользователя (§3.1) — гейт форсит, но и по
  смыслу: «просто пошёл писать код» на дефекте = чиним не то и не там.
- Не заводить фикс как новую фичу: артефакты идут в `<STORY>/fixes/<баг>`, а не рядом со стори.
- Не подставлять стори «по догадке»: `sources.story` — ответ пользователя (или честное `none`).
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
