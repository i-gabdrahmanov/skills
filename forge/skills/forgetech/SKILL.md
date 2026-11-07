---
name: forgetech
description: >
  Tech-ветка forge для ТЕХЗАДАЧИ без бизнес-наполнения: поднять версии библиотек/Spring Boot,
  закрыть CVE в зависимости, поменять хост/таймаут/ключ конфигурации по профилям. Поведение
  системы должно остаться прежним, поэтому вместо BRD/SDD и RED-теста — документ техзадачи
  tech-sdd.md + change-plan.json (было → стало, скоуп файлов, инварианты, откат), утверждение
  плана человеком, снимок «до» и точный diff «после»: цель достигнута ровно, контракты, конфиг и
  тесты не изменились сверх заявленного. Без BRD, SDD, tech-design, Jira-задач и правки
  мастер-спеки. Коммиты/push/PR/отчёты пайплайн НЕ делает — их выполняет пользователь.
  Используй, когда пользователь говорит «подними версию», «обнови spring boot/библиотеки»,
  «закрой CVE», «поменяй хост/url/таймаут в конфиге», «техзадача», «техдолг», «bump
  dependencies», или передаёт тикет типа Task/Technical debt про версии и конфигурацию.
  Отличие от forgefix — там чинится сломанное поведение (RED-тест), здесь поведение не
  меняется. Обычно вызывается роутером (skills/router), работает и автономно.
---

# Forgetech — tech-ветка: техзадача без изменения поведения

> **Пути** — `feature-pipeline/references/skill-paths.json` (общие скрипты forge) и локально
> `references/manifest-steps.json`. Зовём как `python3 <project>/.gigacode/<path>`.
> `<project>` = корень репо кода (там же `ground/`). Не используй `~/.gigacode/...`.

> **Рантайм — форк GigaCode (Qwen). Жёсткие правила:**
> - Запуск — `gigacode` из корня проекта, без флагов: `--experimental-hooks` и `-y` в 26.9 нет,
>   сессия с ними не стартует. `0 hook entries` на старте — хуки не загрузились, сначала
>   preflight. Headless требует `--allowed-tools` — без него `agent` не стартует и фаза упирается
>   в `inline-phase-guard`.
> - В командах — только однострочные, без `$(...)` и обратных кавычек (рантайм режет).
> - Тяжёлую фазу — только через `agent(subagent_type="general-purpose", ...)`. `agent()` и
>   `ask_user_question` не активны одновременно.

Плоский цикл (feature = ключ Jira либо slug; стейт в namespace `forgetech`):
**вход → диагностика и план → утверждение → снимок и правка → проверка → сводка**.
Шаги стейта: `tech-intake → tech-diag → tech-apply → tech-verify → tech-record`.

> **Философия ветки — поведение не меняется.** У техзадачи нет пользователя и бага, значит нет
> сценариев Given-When-Then и RED-теста. Её гейт — снимок «до» и точный diff «после»
> (`check_invariants.py`): «было» в плане сходится с реальным, «стало» совпадает с заявленным в
> точности, а контракты (API/Kafka/таблицы), конфиг и тесты вне заявленного не меняются.
> Изменилось незаявленное — это уже не техзадача: СТОП и вопрос пользователю, а не расширение.

---

## 0. Предусловия

- Java/Spring (gradle или maven), git-репозиторий. Репозиторий артефактов доступен с машины
  прогона — резолв зависимостей (`mvn dependency:list` / `gradle dependencies`) обязателен для
  `dependency-upgrade`; недоступен — гейт отказывает с причиной, тихого отката на слабую проверку
  нет.
- MCP **Atlassian (Jira)** — опционален: нет MCP → задачу описывает пользователь текстом.
- Ключ задачи (`[A-Z]+-\d+`) — если есть, это `--feature`. Нет — короткий slug вида
  `tech-bump-boot-34`.
- Архетипы v1: **`dependency-upgrade`** (версии библиотек/BOM/parent) и **`config-change`**
  (ключи `application*.yml|properties` по профилям — хосты, таймауты, флаги).

> **Два обязательных решения пользователя** (оба форсятся хуками):
> **(1)** архетип задачи — `inputs.archetype` (без него `tech-intake` не закроется, а `tech-diag`
> не сможет писать); **(2)** утверждение плана — согласие `tech-plan-<KEY|slug>`, привязанное к
> `change-plan.json` (без него `tech-guard` и `gate-guard` не пустят ни одной правки проекта).

## 1. Архитектура (кто что делает)

| Этап | step-id | Кто | Гейт (`record_gate`) |
|---|---|---|---|
| Вход + скоуп-чек «это техзадача» + архетип | `tech-intake` | главный агент | `check_tech_scope.py` |
| Текущее состояние + `tech-sdd.md` + `change-plan.json` | `tech-diag` | субагент | `check_tech_sdd.py` |
| Утверждение плана | — | человек | согласие `tech-plan-<F>` (`--bind-file`) |
| Снимок «до» → правка в пределах targets | `tech-apply` | субагент | сборка + `check_invariants.py expect` |
| Регресс тестов + инварианты против снимка | `tech-verify` | субагент | `module_tests.py guard` + `check_invariants.py compare` |
| Итоговая сводка `report.md` | `tech-record` | субагент | `tech_report.py check` |

> **Субагент = ЯВНЫЙ вызов `agent(subagent_type="general-purpose", ...)`.** Диагностику, правку,
> прогон и сводку inline не делай — заблокирует `inline-phase-guard`, и без `SubagentStop` шаг не
> закроется. Субагент ПОСЛЕДНИМ действием гоняет свой гейт через раннер и возвращает JSON с
> `step_id` и `status`. Инлайн-шаг (`tech-intake`) закрывает главный агент через `update.py`.

> **`tech-guard` на этом пути:** любая запись в файл проекта (вне `docs/`, `ground/`, выходных
> каталогов сборки) проходит только на шаге `tech-apply`, при согласии на ТЕКУЩИЙ план, при снятом
> снимке «до» и только в файлы из `targets`. Это не «желательно», это deny (exit 2).

### 1.1. Инициализация (один раз)
```
python3 <forge>/hooks/preflight.py --project <toplevel>
```
> **`<forge>` — корень кода форжа: каталог на два уровня выше этого SKILL.md**
> (`<project>/.gigacode/extensions/forge`, ставит `install.sh forge`). preflight печатает его в `layout.base`; ниже любой путь
> `<project>/.gigacode/skills/...` читай как `<forge>/skills/...`.

exit 0 — продолжаем; exit 1 — стоп, чинить деплой. **exit 2 — конфиг не инициализирован**:
выполни команду из поля `init_command` и повтори preflight. Пришли `answer_commands` — спроси
пользователя эти значения, запиши их командами из списка и повтори preflight.

**Шаг 1 — стейт (namespace forgetech):**
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/init.py --project <toplevel> --skill forgetech --feature <KEY|slug> --steps @<project>/.gigacode/skills/forgetech/references/manifest-steps.json
```

**Шаг 2 — per-feature решения, ПОСЛЕ `init.py`** (`--project` ДО подкоманды `set`;
`auto_max_risk` — sensitive, нужен `--confirm`):
```
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set inputs.mode tech --skill forgetech --feature <KEY|slug>
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set decisions.mode_task <KEY|slug> --skill forgetech --feature <KEY|slug>
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set decisions.auto_max_risk R2 --confirm --skill forgetech --feature <KEY|slug>
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set decisions.criticality medium --skill forgetech --feature <KEY|slug>
```
Закрытие шага — только после прохождения гейта:
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py --project <toplevel> --skill forgetech --feature <KEY|slug> --step-id <id> --status completed
```

**Каталог артефактов техзадачи — резолвни, не подставляй плейсхолдер:**
```
python3 <project>/.gigacode/skills/feature-pipeline/scripts/skill_paths.py tech-docs --project <toplevel> --feature <KEY|slug>
```
Печатает `<docs>/feature-pipeline/tech/<KEY|slug>`. Дальше `<techdir>` = ровно этот путь;
подставляй его субагентам целиком. Туда и только туда пишутся `tech-sdd.md` + `change-plan.json`
(`tech-diag`) и `report.md` (`tech-record`). Имена — контракт: по ним работают гейты и
`update.py`; документ, названный по ключу задачи, для них не существует.

> ⛔ **Каталог харнеса — не место для артефактов.** `<project>/.gigacode/skills/...` — код форжа;
> `state-write-guard` блокирует запись туда во время прогона.

---

## 2. Вход + скоуп-чек → `tech-intake`

Есть Jira MCP и ключ — прочитай issue (summary, description, issuetype, последние комментарии) и
сохрани JSON в `<techdir>/issue.json`. Нет MCP — попроси описание (что меняем, с какого на какое,
зачем) и сохрани в `<techdir>/issue.txt`.

**Скоуп-чек — детерминированный** (`update.py` не закроет `tech-intake` без evidence):
```
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgetech --feature <KEY|slug> --step-id tech-intake --cmd "python3 <project>/.gigacode/skills/forgetech/scripts/check_tech_scope.py --issue-json <techdir>/issue.json"
```
(без Jira — тот же вызов с `--text-file <techdir>/issue.txt`)

- **exit 0** — техзадача, продолжай.
- **ESCALATE** (evidence `passed:false`, причины в выводе) — СТОП, спроси пользователя: «Похоже,
  это не техзадача (причины: …). Взять tech, fix (баг) или full (фича)?» Не решай молча. Явное
  «продолжаем tech» — это R4: `record_approval.py --key gate-override-gate-result-tech-intake
  --approved-by user --reason "..." --evidence "<его дословная фраза>"`, затем
  `override_judge.py --judge gate-result-tech-intake --reason "..."`.

### 2.1. Архетип (до закрытия `tech-intake`)

Спроси `ask_user_question` — «Что за техзадача?»: **«Апгрейд зависимостей»**
(`dependency-upgrade`) / **«Правка конфигурации»** (`config-change`). Очевидно из тикета (CVE в
библиотеке, «поменять хост») — предложи вариант первым, но всё равно спроси. Запиши:
```
python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set inputs.archetype <dependency-upgrade|config-change> --skill forgetech --feature <KEY|slug>
```
Проверь exit-код `set` (0 = записано). Вопрос не отрендерился (headless) — остановись и попроси
предзапись `config.py set inputs.archetype …` + перезапуск. Закрой `tech-intake` (`update.py`).

## 3. Диагностика + план → `tech-diag`

```
description: "Diagnose tech task <KEY> + write tech-sdd.md / change-plan.json"
subagent_type: general-purpose
prompt:
Сначала прочитай и строго следуй шаблону: read_file("<project>/.gigacode/skills/forgetech/references/tech-sdd-template.md")
Корень репо: <toplevel>. Техзадача: <summary/описание>. Архетип (решение пользователя): <archetype>.
Каталог техзадачи: <techdir> (там же issue.json/issue.txt).
Шаги:
1. Текущее состояние — ТОЛЬКО инструментами гейта, не на глаз из файлов:
   python3 <project>/.gigacode/skills/forgetech/scripts/check_invariants.py show --project <toplevel> --grep <ключ|артефакт>
   python3 <project>/.gigacode/skills/forgetech/scripts/check_invariants.py deps --project <toplevel> --grep <group>
   (deps запускает сборку — может идти минуты; mvn/gradle напрямую не зови — роль фазы это запрещает)
2. dependency-upgrade: найди release notes / migration guide целевой версии (ссылка обязательна),
   выпиши breaking changes и по КАЖДОМУ — касается ли нас (грепни использования) и решение.
   config-change: найди всех потребителей ключа (кто читает clients.x.url) и профили, где он задан.
3. Определи targets — поимённо, без глобов: билд-файлы/конфиги и те файлы кода, правку которых
   уже видно по breaking changes. Не расширяй «на всякий случай»: недостающее добавится циклом
   needs_scope с согласия пользователя.
4. Запиши <techdir>/tech-sdd.md и <techdir>/change-plan.json строго по шаблону. «Было» (from) —
   ровно как печатает show/deps; цель — точно (from/to), побочные изменения BOM — областями match.
5. ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgetech --feature <KEY|slug> --step-id tech-diag --cmd "python3 <project>/.gigacode/skills/forgetech/scripts/check_tech_sdd.py <techdir> --project <toplevel> --archetype <archetype>"
Проект НЕ правь (tech-guard не пустит). Верни JSON: {"step_id":"tech-diag","status":"completed|failed",
"targets":["..."],"changes":["<kind> <ключ>: <from> → <to>"],"risks":["..."],"outside_repo":"...",
"open_questions":["..."]}
```
Есть `open_questions` — задай их пользователю и перезапусти субагента с ответами, не додумывай.

### 3.1. Утверждение плана (ОБЯЗАТЕЛЬНО, enforced)

1. Прочитай `<techdir>/tech-sdd.md` и покажи пользователю **резюме на 5–8 строк**: что меняем
   (было → стало по каждой цели) · какие файлы правятся (targets) · что обязано остаться
   (инварианты) · риски/breaking changes и решения · что остаётся вне репозитория · откат.
2. Спроси `ask_user_question`: «Делаем по этому плану?» — варианты: **«Да, делаем по плану»** /
   **«Правки в план»** / **«Это не техзадача — сменить путь»**.
3. Ответ:
   - **«Да, делаем по плану»** — фиксируй согласие, привязанное к плану (цитата — ответ
     пользователя дословно; без `--bind-file` tech-guard согласие не примет):
     ```
     python3 <project>/.gigacode/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key tech-plan-<KEY|slug> --approved-by user --reason "<кратко: что утвердили>" --bind-file <techdir>/change-plan.json --evidence "Да, делаем по плану"
     ```
   - **«Правки»** — верни `tech-diag` субагенту с правками; согласие не выписывай.
4. План пришлось переписать ПОСЛЕ согласия (вернули на `tech-diag`) — прежнее согласие привязано
   к старому плану и не действует, но мешает записать новое. Погаси его и спроси заново:
   ```
   python3 <project>/.gigacode/skills/forgetech/scripts/tech_plan.py revoke --project <toplevel> --feature <KEY|slug> --reason "план переписан на tech-diag"
   ```
   - **«Сменить путь»** — СТОП, новый прогон в другом namespace (fix/full).
5. В targets есть prod-конфиг или миграция (R4-путь) — утверждения плана для их записи мало:
   `gate-guard` потребует отдельное R4-согласие `human-approval` на каждое такое действие.
6. Вопрос не отрендерился (headless) — не выписывай согласие сам: остановись и попроси
   пользователя ответить.

## 4. Снимок и правка → `tech-apply`

```
description: "Apply tech change <KEY> strictly within approved scope"
subagent_type: general-purpose
prompt:
Корень репо: <toplevel>. Техзадача: <summary>. Сборка: <gradle|maven>.
УТВЕРЖДЁННЫЙ ПОЛЬЗОВАТЕЛЕМ ПЛАН — прочитай ЦЕЛИКОМ перед первой правкой:
read_file("<techdir>/tech-sdd.md")       (решения по breaking changes, инварианты)
read_file("<techdir>/change-plan.json")  (targets — ЕДИНСТВЕННЫЕ файлы, которые можно править; цели from→to)
Нужны конвенции кода (правка Java по breaking changes) — точечно: read_file("<project>/.gigacode/skills/java-spring-dev/SKILL.md").
Шаги:
1. ПЕРВЫМ действием — снимок «до» (до него tech-guard не пустит ни одной правки):
   python3 <project>/.gigacode/skills/forgetech/scripts/check_invariants.py snapshot --project <toplevel> --feature <KEY|slug>
   FAIL «план не сходится с текущим состоянием» / «проба уже выполнена» — НЕ правь план сам:
   верни status:"failed" с выводом (план утверждён человеком, он вернётся на tech-diag).
2. Правь ТОЛЬКО файлы из targets и ровно до целей плана. Не подметай рядом, не поднимай «заодно»
   соседние версии, не меняй тесты (src/test тоже вне скоупа, если его нет в targets).
3. Сборка упала из-за файла ВНЕ targets (удалённый API, сменившаяся сигнатура) — НЕ обходи
   правкой соседнего файла: верни status:"needs_scope" и список
   {"path":"<файл>","reason":"<строка ошибки компилятора/теста дословно>"}.
4. ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (сборка без тестов + точный diff):
   Maven:  python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgetech --feature <KEY|slug> --step-id tech-apply --cmd "mvn -q -DskipTests package && python3 <project>/.gigacode/skills/forgetech/scripts/check_invariants.py expect --project <toplevel> --feature <KEY|slug>"
   Gradle: тот же вызов с --cmd "./gradlew assemble && python3 <project>/.gigacode/skills/forgetech/scripts/check_invariants.py expect --project <toplevel> --feature <KEY|slug>"
   FAIL «незаявленное изменение» — убери лишнюю правку; «ожидается X, сейчас Y» — доделай цель.
Верни JSON: {"step_id":"tech-apply","status":"completed|failed|needs_scope","files_changed":["..."],
"needs_scope":[{"path":"...","reason":"..."}]}
status:"completed" ТОЛЬКО если раннер вернул exit 0.
```

### 4.1. `needs_scope` — донастройка скоупа

Субагент вернул `needs_scope` — это не провал, а вопрос пользователю:
1. Дополни план (он ГАСИТ согласие — оно давалось на прежний скоуп):
   ```
   python3 <project>/.gigacode/skills/forgetech/scripts/tech_plan.py amend --project <toplevel> --feature <KEY|slug> --add-target <path> --reason "<строка ошибки из вывода>"
   ```
   (`--add-target` повтори на каждый файл; понадобилась новая цель — `--add-change '<json>'`)
2. Спроси `ask_user_question`: «Нужно поправить ещё N файл(ов): <путь — причина>. Ок?» —
   **«Да, добавляем в скоуп»** / **«Нет — вернуть на tech-diag»**.
3. «Да» → новое согласие с привязкой к дополненному плану (та же команда §3.1 с
   `--evidence "Да, добавляем в скоуп"`), затем перезапусти субагента `tech-apply` с тем же
   брифом: снимок НЕ переснимается (`snapshot` и не даст — targets уже правлены), он продолжит
   правку. «Нет» → верни `tech-diag` на доработку.
4. `tech_plan.py` отказал по лимиту (`quality.max_step_reopens`) — ESCALATE: скоуп оценён
   неверно, СТОП и вопрос пользователю.

## 5. Регресс и инварианты → `tech-verify`

```
description: "Verify tech change <KEY>: regression + invariants vs snapshot"
subagent_type: general-purpose
prompt:
Проверь техзадачу: тесты затронутых модулей не сломаны, контракты и конфиг не изменились сверх
заявленного. Корень репо: <toplevel>. Сборка: <gradle|maven>.
ЕДИНЫМ РАННЕРОМ прогони составной гейт (он пишет evidence — без него шаг не закроется):
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgetech --feature <KEY|slug> --step-id tech-verify --timeout 3000 --cmd "python3 <project>/.gigacode/skills/feature-pipeline/scripts/module_tests.py guard --root <toplevel> --base HEAD && python3 <project>/.gigacode/skills/forgetech/scripts/check_invariants.py compare --project <toplevel> --feature <KEY|slug>"
module_tests.py guard сам снимает эталон «зелёного ДО» через git stash и сверяет. Код и тесты
на этой фазе НЕ правь (роль фазы — test). Регресс или ESCALATE — верни status:"failed" с выводом.
Верни JSON: {"step_id":"tech-verify","status":"completed|failed","regression_ok":true|false,
"undeclared":["<класс>: <ключ>: <было> → <стало>"]}
```

- **Регресс тестов** — переоткрой `tech-apply` (`update.py --status pending`, счётчик
  `quality.max_step_reopens`) и почини там; тесты не ослабляй.
- **ESCALATE (незаявленное изменение контракта/конфига)** — СТОП, покажи пользователю diff и
  спроси: **«Ожидаемо — дописать в план»** (`tech_plan.py amend --add-change …` + новое
  согласие, затем повтор `tech-verify`) / **«Откатить и сделать иначе»** (переоткрыть
  `tech-apply`) / **«Это фича или баг»** (новый прогон full/fix). Сам не решай.

## 6. Сводка → `tech-record`

```
description: "Write tech report for <KEY>"
subagent_type: general-purpose
prompt:
Собери итоговую сводку техзадачи СКРИПТОМ (не пересказом) и проверь её гейтом.
1. python3 <project>/.gigacode/skills/forgetech/scripts/tech_report.py write --project <toplevel> --feature <KEY|slug>
2. ПОСЛЕДНИМ действием — гейт ЧЕРЕЗ РАННЕР:
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgetech --feature <KEY|slug> --step-id tech-record --cmd "python3 <project>/.gigacode/skills/forgetech/scripts/tech_report.py check --project <toplevel> --feature <KEY|slug>"
Код и конфиги не трогай. Верни JSON: {"step_id":"tech-record","status":"completed|failed","report":"<techdir>/report.md"}
```

Major-апгрейд (смена мажорной версии фреймворка) — предложи пользователю зафиксировать решение
ADR (`adr.enabled`); на v1 это не гейт.

### 6.1. Итог пользователю (финал пайплайна)
Покажи из `<techdir>/report.md`: цели было → стало, фактический diff внутри match-областей
(что реально поднялось), донастройки скоупа, итог инвариантов и **чек-лист «вне репозитория»** —
то, что пайплайн не видит и не проверяет (helm values, переменные окружения, Vault, preStop).
Мастер-спека не правится. Коммит, push, PR и комментарий в Jira пайплайн НЕ делает — их
выполняет пользователь сам.

---

## Карта MCP
| Действие | Паттерн инструмента |
|---|---|
| Jira issue | `*jira*get*issue*`, `*atlassian*issue*` |
Не угадывай — бери первый подходящий из доступных.

## Что НЕ делать
- Не править проект до утверждения плана и до снимка «до» — tech-guard форсит, но и по смыслу:
  без снимка «стало» не с чем сравнить.
- Не брать «было» на глаз из файла — только `check_invariants.py show|deps`.
- Не расширять скоуп правкой соседнего файла — только `needs_scope` → `amend` → согласие.
- Не править `change-plan.json` руками после согласия — согласие привязано к его sha256.
- Не ослаблять и не править тесты, чтобы «стало зелено»: тесты — инвариант задачи.
- Не поднимать «заодно» версии, которых нет в плане, — незаявленный diff = FAIL/ESCALATE.
- Не писать BRD, SDD, tech-design, задачи в Jira и дельту мастер-спеки — это full/fix.
- Не коммитить, не пушить, не создавать PR и не писать в Jira — доставку делает пользователь.
- Диагностику/правку/прогон/сводку не делать inline (заблокирует `inline-phase-guard`).
- Не обходить проблему через `reset --hard` / `checkout .` (R4, согласие пользователя).

## Связь
Ветка forge: вызывается роутером (`skills/router`) при выборе «tech»; баг — `forgefix`, фича —
`feature-pipeline`. Ядро гейтов — `scripts/_techlib.py`; регресс — `feature-pipeline/scripts/
module_tests.py`; стейт — `pipeline-state` (namespace `forgetech`). Дизайн — `tasks/018-tech-path.md`.

## v2 model (manifest v2 + policy.json)

Писатель конфига — только `skills/config-helper/scripts/config.py set` (валидация по реестру).

- **Project-wide (`ground/policy.json`)** — `quality.*`, `conventions.*`, `docs.*`, `jira.*`.
  Эта ветка их не пишет: `quality.*`/`security.*` — R4-класс.
- **Per-feature (`ground/statements/forgetech/<KEY|slug>/manifest.json`)** — `inputs.mode`,
  `inputs.archetype`, `decisions.mode_task`/`criticality`/`auto_max_risk`. Без активного
  манифеста `set inputs.*`/`decisions.*` падает (exit 3) — сначала `init.py`.
