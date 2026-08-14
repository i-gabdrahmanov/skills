---
name: router
description: >
  Единая точка входа forge: определяет, каким путём вести работу, и делегирует. ПЕРВЫМ
  действием классифицирует задачу и (если неоднозначно) спрашивает пользователя: «минорный баг
  (fix-путь forgefix), уже подготовленная задача из Jira (lite-путь forgelite) или фича с нуля
  (full-путь feature-pipeline)?» — и запускает выбранный оркестратор на общем control-plane
  (один .gigacode, одни хуки). Используй этот скилл, когда запрос неоднозначен между
  «починить дефект», «выполнить готовый тикет» и «сделать фичу end-to-end»: «сделай задачу из
  jira», «прогони KIDPPRB-1234», «нужно реализовать <фичу/задачу>», «запусти forge».
  Упоминание НАЗВАНИЯ харнеса («через feature pipeline / forge / фичепайплайн») — это НЕ выбор
  пути full, а просто «прогони через forge»: всё равно классифицируй. Сигнал full — только явное
  «с нуля / собери требования / нет тикета / нет готовой спеки». Есть Jira-ключ + «сделай задачу/
  фичу [KEY]» и спека уже существует → скорее lite. Тип Bug / «не работает», «падает», «ошибка»,
  «почини» → fix. Во всех неоднозначных случаях — спроси. Роутер сам не пишет код и не трогает
  Jira — только выбирает путь, выставляет конфиг и делегирует.
---

# Router — выбор пути fix | lite | full

> Один харнес (`<project>/.gigacode/`), одни хуки. Роутер только классифицирует и делегирует —
> вся работа идёт в выбранном оркестраторе. Запуск харнеса: `gigacode --experimental-hooks -p "..."`.

## 0. Предусловия
- cwd = корень репо кода (`<toplevel>`). Харнес развёрнут: либо `.gigacode/` в проекте
  (legacy `deploy.sh`), либо установленный extension. `<forge>` = корень кода форжа —
  каталог на два уровня выше этого SKILL.md; preflight печатает его в `layout.base`, и
  ниже любой путь `<project>/.gigacode/skills/...` читается как `<forge>/skills/...`.
- Прогони preflight — **exit 1 = стоп** (ENFORCEMENT OFF или битые пути харнеса; чини деплой/установку, не продолжай):
  ```
  python3 <forge>/hooks/preflight.py --project <toplevel>
  ```
  **exit 2 = конфиг ещё не инициализирован** (первый прогон в проекте: нет `ground/pipeline.json`).
  Это не ошибка — выполни готовую команду из поля `init_command` вывода preflight
  (`init_pipeline_config.py --project <toplevel>`) и повтори preflight до exit 0. **Не пропускай
  этот шаг:** без `pipeline.json` КАЖДЫЙ `config.py set` ниже вернёт exit 3 «pipeline.json не
  найден», и все решения прогона (`pipeline.mode`, `sources.story`) молча не запишутся.

## 1. Выбор пути (ПЕРВОЕ действие)

> **Путь — это ОБЯЗАТЕЛЬНОЕ решение (`pipeline.mode`), а не догадка.** Порядок: (1) если
> `pipeline.mode` записан **и** `pipeline.mode_task` совпадает с текущей задачей (тот же ключ
> Jira/слаг) — это предзапись для ЭТОГО прогона, используй её и не переспрашивай; (2) иначе
> спроси `ask_user_question`; (3) если вопрос не отрендерился (headless/форк — пустой ответ),
> НЕ угадывай и НЕ уходи в full по названию харнеса: остановись и попроси предзапись
> `config.py set pipeline.mode fix|lite|full` + `set pipeline.mode_task <KEY|slug>` +
> перезапуск. «feature pipeline» в промпте ≠ full.
>
> ⚠️ **Чужой `pipeline.mode` не наследуется.** `pipeline.json` живёт в проекте и переживает
> прогоны: записанный вчера `fix` — это решение по вчерашнему багу, а не по сегодняшней фиче.
> Поэтому режим считается действующим только вместе с `mode_task`. Не совпало (или `mode_task`
> пуст) — **спроси заново**, даже если `pipeline.mode` заполнен.

Спроси пользователя (`ask_user_question`) — до любого субагента/агента:

> **Что делаем?**
> - **fix** — МИНОРНЫЙ ДЕФЕКТ: что-то работает не так, надо починить. Диагностика → RED-тест,
>   воспроизводящий баг → минимальный фикс → прогон с регрессом → **точечная дельта-правка
>   спеки** (правки существующих требований, не SDD заново). Путь `forgefix`.
> - **lite** — исполнить УЖЕ ПОДГОТОВЛЕННУЮ подзадачу из Jira (есть описание + acceptance
>   criteria) по СУЩЕСТВУЮЩЕЙ спеке: grounding → tech-design по спеке → TDD → покрытие.
>   Путь `forgelite`.
> - **full** — фича/изменение С НУЛЯ: написать спецификацию (SDD/tech-design) из идеи/Jira,
>   завести задачи в Jira, реализовать и довести до верифицированного артефакта.
>   Путь `feature-pipeline`.
>
> (Коммиты/PR/отчёты во всех трёх — на пользователе.)

Подсказки для рекомендации (не решай молча, но можешь предложить):
- **fix:** issuetype = Bug/Дефект; текст про поломку («не работает», «падает», «ошибка», «NPE»,
  «некорректно», «регресс»); просьба «почини/поправь/исправь»; правка ожидается точечная, а
  поведение системы уже описано в спеке. **Именно сюда, а не в full, идёт багфикс с ключом Jira** —
  иначе харнес заведёт новую фичу и перепишет спеку вместо правки.
- **lite:** Sub-task/Task/Bug с внятными AC, спека уже есть, один сценарий, работа — «сделать по
  описанию», а не «починить сломанное».
- **full:** свободная идея, Story/Epic, нет AC/нет спеки, несколько сценариев, спека пишется с нуля.
- Просто назвали «feature pipeline / forge» без «с нуля/без спеки» — это НЕ сигнал full;
  классифицируй по сути.
- Дефект, но крупный (Blocker/Critical, требует миграции/рефакторинга/смены контракта) — это НЕ
  fix: предложи lite или full. Границу проверит скоуп-чек выбранной ветки.

## 2. Делегирование

### Выбран **fix**
1. Выстави fix-конфиг (один общий `.gigacode`; `--project` ДО `set`; `auto_max_risk` sensitive → `--confirm`).
   **`pipeline.mode` + `pipeline.mode_task` — записать первыми** (артефакт решения о пути и о том,
   ДЛЯ КАКОЙ задачи оно принято; universal-режим fail-closed):
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode fix
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode_task <KEY|slug>
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.auto_max_risk R2 --confirm
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.criticality medium
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set quality.eval_enabled false
   ```
2. Пользователь назвал стори («баг по STOR-100») — запиши сразу, это снимет вопрос на `fix-intake`:
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set sources.story <STORY>
   ```
   Сверяй exit-код каждого `set`: 0 = записано, 3 = конфига нет (вернись к §0 и инициализируй).
   `update.py` не закроет `fix-intake` с незаписанным `sources.story` — молча потерять ответ нельзя.
3. Прочитай и строго следуй: `read_file("<project>/.gigacode/skills/forgefix/SKILL.md")`.
   Дальше веди дефект по нему (стейт в namespace `forgefix`). У fix два обязательных вопроса
   пользователю: стори бага (§2.1) и утверждение мини-плана фикса (§3.1) — оба форсятся хуками.

### Выбран **lite**
1. Выстави lite-конфиг (те же правила вызова config.py):
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode lite
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode_task <JIRA-KEY>
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.auto_max_risk R2 --confirm
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.criticality medium
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set quality.eval_enabled false
   ```
   (R2 → код/тесты/доки идут авто; TDD/SoD держат хуки. eval off — у lite нет eval-plan-фазы.)
2. Прочитай и строго следуй: `read_file("<project>/.gigacode/skills/forgelite/SKILL.md")`.
   Дальше веди задачу по нему (стейт в namespace `forgelite`).

### Выбран **full**
1. Запиши путь и не переопределяй autonomy — у full свой гейт критичности (после SDD):
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode full
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode_task <KEY|slug>
   ```
2. Прочитай и строго следуй: `read_file("<project>/.gigacode/skills/feature-pipeline/SKILL.md")`.
   Дальше веди фичу по нему (стейт в namespace `feature-pipeline`).

## 3. Границы роутера
- Роутер НЕ дублирует фазы, НЕ пишет код, НЕ ходит в Jira/Bitbucket. Только: классифицировать →
  (при неоднозначности) спросить → выставить конфиг → `read_file` нужного оркестратора → следовать ему.
- Скоуп-несоответствие ловит детерминированный скоуп-чек выбранной ветки на первом шаге
  (`check_fix_scope.py` у fix, `check_scope.py` у lite) — он остановится и предложит сменить путь.
  Смена пути после старта — это новый прогон с другим namespace, а не правка манифеста.
- Один активный пайплайн за прогон. Хуки резолвят активный skill/feature по самому свежему
  манифесту в `ground/statements/*/*/`, поэтому ветки не мешают друг другу.
