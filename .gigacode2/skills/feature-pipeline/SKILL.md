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

Скилл ведёт фичу по циклу: **идея/Jira → BRD → контекст системы → тех-дизайн → задачи
в Jira → код → тесты → спека → stacked-PR → отчёт**.

Каждая фаза автономна ровно до точки, где нужно решение пользователя (гейт). Не ускоряй
эти моменты — лучше пауза и вопрос, чем необратимое действие «молча». Полный дизайн
пайплайна и принятые решения — в `docs/feature-pipeline/` (README, contracts,
new-components); этот SKILL.md — исполняемая инструкция.

---

## 0. Предусловия

- Текущая директория — корень репо кода (Java/Spring, Gradle/Maven).
- Подключены MCP **Atlassian (Jira)** и **Bitbucket** — для фаз 2.5 и 6. Если их нет,
  пайплайн всё равно идёт в режиме «без Jira / до коммита» (см. гейты).
- Доступен скилл **`pipeline-state`** — без него нельзя резюмировать после обрыва (§0.5).
- Вложенные скиллы фаз: `brd-interview`/`business-requirements`, `system-analyst`,
  `tech-design`, `jira-task-writer`, `java-spring-dev`.

### 0.1 Конфигурация проекта (делай это первым)

Все параметры конвейера живут в `<project>/.gigacode/pipeline.json` — единый стор, который
путешествует с проектом. Полная схема и правила — [`references/config.md`](references/config.md).

1. Прочитай `<project>/.gigacode/pipeline.json`.
2. **Если файла нет** — создай:
   ```bash
   python ~/.gigacode/skills/feature-pipeline/scripts/init_pipeline_config.py --project "$(pwd)"
   ```
   Скрипт авто-детектит build-систему, модули, пакет, версии, инструмент миграций и кладёт
   незаполняемое в `_incomplete`.
3. **Пройди по `_incomplete`** — спроси у пользователя ровно эти поля (Jira-ключ, Bitbucket
   workspace/repo, инструмент миграций, нужен ли `git init`) и впиши в файл.
4. Дальше бери из конфига: `docs.docs_path` (вместо старого
   `~/.gigacode/skills/minor-defect-fix/config.json` — он остаётся фоллбэком),
   `quality.coverage_threshold`, `conventions.migration_tool`, `delivery.pr_strategy`,
   `project.default_branch`, `autonomy.*`. Не хардкодь эти значения в шагах — читай из конфига.

Если `project.is_git=false`, а пользователь хочет дойти до PR — предложи `git init` до фазы 6
(иначе ветки/stacked-PR и ключ `pipeline-state` не работают).

## 0.5 Pipeline-state (резюмирование при обрыве)

Каждый прогон — пайплайн из шагов (см. манифест ниже). Если субагент упёрся в лимит или
процесс прервался — без сохранения state теряется всё сделанное.

Конвенция: `<project>/.gigacode/statements/feature-pipeline/pipeline/`.

**В самом начале**, до вопросов и субагентов, проверь state:
```bash
python ~/.gigacode/skills/pipeline-state/scripts/read.py \
    --project "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" --skill feature-pipeline
```
- `no_state` — свежий запуск, иди обычным путём.
- `in_flight` — покажи summary (завершено N/всего, что упало) и спроси: резюмировать /
  начать заново (архивировать) / показать собранное.
- `completed` — спроси: перегенерировать или открыть готовое.

**Инициализируй state** после того, как определён вход и пройден скоуп-чек (§2), но до
первого субагента. Манифест шагов:

| step-id | title | depends_on |
|---|---|---|
| `00-brd` | Discovery / BRD | — |
| `01-grounding` | System overview ensured | — |
| `02-design` | Tech design + task plan | `00-brd`, `01-grounding` |
| `03-jira` | Jira issues created | `02-design` |
| `04-build-<taskId>` | Build task (по task-plan) | `02-design` |
| `05-tests` | Tests green + coverage | все `04-build-*` |
| `06-spec` | Spec updated | `05-tests` |
| `07-deliver-<taskId>` | Ветка+коммит+stacked PR задачи | `05-tests`, `06-spec` |
| `07-report` | Отчёт в Story | все `07-deliver-*` |

`04-build-*` и `07-deliver-*` добавляются после фазы 2 через `add_steps.py` (см. §5), когда известна разбивка задач.
После каждого завершённого субагента/шага — `update.py ... --status completed` с его
JSON. Перед синтезаторами/дизайнером передавай выжимки через `--excerpt-of`. Не храни в
state секреты и сами MD-файлы (они уже в репозиториях).

---

## 1. Архитектура: кто что делает

| Фаза | Исполнитель | Механизм | Гейт |
|---|---|---|---|
| Конфиг, чтение Jira-входа | главный агент | — | — |
| Скоуп-чек | главный агент | — | — |
| 0 Discovery (BRD) | `brd-interview` / `business-requirements` | вложенный скилл | **Гейт 1** |
| 1 Grounding | `system-analyst` (если нет обзора) | оркестратор-субагентов | — |
| 2 Design | `tech-design` | вложенный скилл | **Гейт 2** |
| 2.5 Jira | `jira-task-writer` | вложенный скилл | **Гейт 3** |
| 3 Build (per task) | `java-spring-dev` + changeset | вложенный скилл | — |
| 4 Verify | тестописатель + тестраннер | субагенты general-purpose | — |
| 5 Document | спецадаптер + `java-uml-spec` | субагент + скилл | — |
| 6 Deliver (per task, stacked) | главный агент | Bitbucket/Jira MCP | **Гейты 4-6** |

**Вложенный скилл vs субагент:** скилл загружается в контекст главного агента (тесная
интеракция, может задать вопрос); субагент работает изолированно и возвращает JSON
(тяжёлый вывод — gradle, JaCoCo, сканы). Не передавай субагентам всю историю разговора —
только нужный контракт фазы (см. `docs/feature-pipeline/contracts.md §6`).

**Два типа гейтов.** «Гейт 1-6» — точки подтверждения пользователем (необратимое не
делается без «да»). Отдельно у каждой фазы есть **детерминированный execution-gate**
(Python), который проверяет, что фаза реально отработала, ДО закрытия её шага в
pipeline-state: design→`check_taskplan.py`, jira→`check_jira.py`, build→`check_build.py`,
tests→`check_coverage.py`, document→`scan_all.py`, deliver→`check_delivery.py`. Шаг не
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

Прочитай инструкции и немедленно следуй им (выбери один вариант):
- интерактивное интервью: `read_file("~/.gigacode/skills/brd-interview/SKILL.md")`
- быстрый сбор требований: `read_file("~/.gigacode/skills/business-requirements/SKILL.md")`

Если выбор не очевиден — спроси пользователя, какой формат предпочесть.
После чтения строго следуй инструкциям из файла. Результат — `brd.md` в
`docs/feature-pipeline/<slug>/`. `<slug>` — kebab-case по сути фичи.

**Гейт 1.** Покажи 2-3 ключевых допущения и самый критичный открытый вопрос из раздела BRD «Открытые вопросы» (номер раздела зависит от шаблона — ссылайся по названию).
Спроси: «утверждаем BRD / доработать?». Дальше — только после «да». Обнови `00-brd`.

---

## 4. Фаза 1 — Grounding

Проверь, есть ли `docs/system-analysis/`. Если **нет или устарел** — прочитай
инструкции и немедленно следуй им:
```
read_file("~/.gigacode/skills/system-analyst/SKILL.md")
```
У него свой цикл и свой гейт коммита спеки. Если обзор есть — переиспользуй.
Не пересканируй проект на каждом прогоне. Обнови `01-grounding`.

Этот контекст нужен дизайнеру: модули, существующие сущности, API, схема БД.

---

## 5. Фаза 2 — Design → Гейт 2

Прочитай инструкции тех-дизайнера и немедленно следуй им:
```
read_file("~/.gigacode/skills/tech-design/SKILL.md")
```
Передай дизайнеру: путь к `brd.md`, путь к `docs/system-analysis/`.
Он вернёт `tech-design.md` + `task-plan.json` в папке фичи.

**Гейт 2.** Покажи резюме: затронутые модули, новые/изменяемые сущности, нужны ли
миграции, число задач, главный риск. Спроси: «делаем так / правки?».
- Правки дизайна → верни `tech-design` на доработку (BRD не трогаем).
- Если на гейте всплыло **новое бизнес-требование** → откат к фазе 0.

После «да»: добавь в манифест шаги `04-build-<taskId>` и `07-deliver-<taskId>` по
`task-plan.tasks` скриптом `~/.gigacode/skills/pipeline-state/scripts/add_steps.py`
(идемпотентно, манифест руками не правь).

**Gate перед закрытием `02-design`:**
```bash
python3 ~/.gigacode/skills/tech-design/scripts/check_taskplan.py \
    "<папка фичи>/task-plan.json" --scan "docs/system-analysis/scan"
```
Схема, словарь слоёв, DAG `depends_on`, непустой `acceptance` + кросс-чек `modules`
против ground-truth `structure.json`. При `fail` — верни `tech-design` на доработку.
Обнови `02-design` только при `pass`.

---

## 6. Фаза 2.5 — Jira → Гейт 3

Прочитай инструкции и немедленно следуй им:
```
read_file("~/.gigacode/skills/jira-task-writer/SKILL.md")
```
Передай: `task-plan.json` + `brd.md` + ключ проекта. Он соберёт черновик Story + Sub-task.

**Гейт 3.** Черновик показывает сам `jira-task-writer`; создание — только после «да».
Результат — `jira-tasks-result.json` (`{story, tasks:{task_id→key}, skipped}`). Если
пользователь отказался или MCP нет — `skipped:true`, идём в режиме «без Jira» (ветки по slug+id).

**Gate перед закрытием `03-jira`:**
```bash
python3 ~/.gigacode/skills/jira-task-writer/scripts/check_jira.py \
    "<папка фичи>/task-plan.json" --result "<папка фичи>/jira-tasks-result.json" \
    --pipeline-config "<project>/.gigacode/pipeline.json"
```
Паритет: 1 Story + N задач, у каждой задачи есть key (при `skipped`/jira off — skip).
Обнови `03-jira`.

---

## 7. Фаза 3 — Build (по задачам)

Иди по `task-plan.tasks` в порядке `depends_on`. Для каждой задачи:
1. Если в задаче есть слой `migration` — напиши Liquibase changeset по `tech-design §4`,
   копируя стиль существующего `db/changelog` (формат и нейминг — по факту проекта).
   **Подробности про миграции и случай отсутствия Liquibase** — `references/migrations.md`.
2. Прочитай инструкции разработчика и немедленно следуй им:
   ```
   read_file("~/.gigacode/skills/java-spring-dev/SKILL.md")
   ```
   Если в репо есть `<project>/.gigacode/conventions.md` — передай её как раскладку слоёв
   проекта (она приоритетнее generic-шаблона скилла). Создай Java-слои задачи
   (entity→repo→dto→mapper→service→controller — только те, что в `task.layers`). Diff виден
   пользователю.
3. **Gate перед закрытием `04-build-<taskId>`:**
   ```bash
   python3 ~/.gigacode/skills/feature-pipeline/scripts/check_build.py \
       "<папка фичи>/task-plan.json" --root "$(git rev-parse --show-toplevel)" --task <taskId>
   ```
   Все `artifacts` задачи реально на диске (ловит «написал код, а файла нет»). Обнови
   `04-build-<taskId>` (completed + список файлов) только при `pass`.

Не дроби задачу и не добавляй слои «на всякий случай» сверх плана.

---

## 8. Фаза 4 — Verify

После того как код всех задач написан — тесты. Два субагента, промпты —
[`references/subagent-prompts.md`](references/subagent-prompts.md):
1. **тестописатель** — пишет/актуализирует тесты по `acceptance` задач и diff’у.
2. **тестраннер** — гоняет `./gradlew test jacocoTestReport`, затем покрытие проверяет
   **детерминированно** (не глазами LLM):

```bash
python3 ~/.gigacode/skills/minor-defect-fix/scripts/check_coverage.py \
    --root "$(git rev-parse --show-toplevel)" --base "<база ветки фичи>" \
    --threshold "<task-plan.coverage_threshold | 0.80>"
```

`check_coverage.py` сам берёт изменённые `*.java` (git diff), парсит JaCoCo XML и даёт
per-file `OK/LOW/MISSING` (exit 2 при недоборе). Тестописатель дописывает тесты прицельно
под `LOW`/`MISSING`, а не «что-нибудь сверху». Лимит итераций — **3**. Закрывай `05-tests`
только при `pass` (exit 0). Затем полный pre-commit (build + coverage).

---

## 9. Фаза 5 — Document

Спецадаптер (субагент, промпт в `references/subagent-prompts.md`) обновляет затронутые
`.md` в `docs/` (включая `system-analysis/api.md`, если добавились эндпойнты) в ветке
репо спеки, **без push** до гейта доставки. Опционально — `java-uml-spec` перерисовывает
диаграммы затронутых контроллеров.

**Gate перед закрытием `06-spec`:** перепрогони детерминированный скан, чтобы ground
truth учёл новый код, и убедись, что новые entity/endpoint из `task-plan` попали в
`api.md`/`domain.md`:
```bash
python3 ~/.gigacode/skills/system-analyst/scripts/scan_all.py \
    "$(git rev-parse --show-toplevel)" -o "docs/system-analysis/scan"
```
Обнови `06-spec`.

---

## 10. Фаза 6 — Deliver (per-task, stacked) → Гейты 4-6

Полная механика веток и stacked-PR —
[`references/stacked-pr-delivery.md`](references/stacked-pr-delivery.md). Кратко:

- Каждая задача → своя ветка `feature/<jira-key>` (или `feature/<slug>-<taskId>` без Jira).
  Ветки **stacked**: ветка задачи ответвляется от ветки той, от которой она зависит
  (`depends_on`); корневые — от default-ветки.
- Коммит каждой задачи — только её файлы; сообщение в стиле проекта (`git log`),
  с ключом Jira, **без** `Co-Authored-By`.

**Гейт 4 — коммиты.** Покажи план коммитов (какая задача → какие файлы → сообщение) по
всем задачам сразу. Спроси «коммитим?». Только после «да».

**Гейт 5 — push + stacked PR.** Покажи план веток и PR (для каждого: source→target,
заголовок, тело со ссылкой на Jira). Спроси «пушим и создаём PR?». После «да» — push в
порядке зависимостей, затем PR через Bitbucket MCP (target = ветка-родитель или default).
Сюда же — push/PR ветки спеки (фаза 5).

**Гейт 6 — отчёт в Jira.** Подготовь черновик комментария в Story (что сделано, файлы,
тесты/покрытие, ссылки на PR по задачам, статус спеки). Покажи целиком, спроси «отправить?».
Отправляй только после «да».

**Gate перед закрытием доставки:**
```bash
python3 ~/.gigacode/skills/feature-pipeline/scripts/check_delivery.py \
    "<папка фичи>/task-plan.json" \
    --manifest "<project>/.gigacode/statements/feature-pipeline/pipeline/manifest.json" \
    --pipeline-config "<project>/.gigacode/pipeline.json"
```
По закрытому `07-deliver-<id>` на каждую задачу (при bitbucket off — skip). Обнови
`07-deliver-*` и `07-report`.

Детали MCP-команд (Jira-комментарий, создание PR, workspace/repo) — общие с
`minor-defect-fix`: `../minor-defect-fix/references/{jira-workflow,bitbucket-workflow,coverage}.md`.

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

- `docs/feature-pipeline/{README,contracts,new-components}.md` — дизайн и контракты.
- `references/subagent-prompts.md` — промпты тестописателя, тестраннера, спецадаптера.
- `references/stacked-pr-delivery.md` — механика веток и stacked-PR (фаза 6).
- `references/migrations.md` — Liquibase changeset и случай отсутствия миграций.
- `../minor-defect-fix/references/` — общие jira/bitbucket/coverage воркфлоу.
