---
name: router
description: >
  Единая точка входа forge: определяет, каким путём вести работу, и делегирует. ПЕРВЫМ
  действием спрашивает пользователя: «фича с нуля (full-путь feature-pipeline) или уже
  подготовленная задача из Jira (lite-путь forgelite)?» — и запускает выбранный оркестратор
  на общем control-plane (один .gigacode, одни хуки). Используй этот скилл, когда запрос
  неоднозначен между «сделать фичу end-to-end» и «выполнить готовый тикет»: «сделай задачу
  из jira», «прогони KIDPPRB-1234», «нужно реализовать <фичу/задачу> до PR», «запусти forge».
  Упоминание НАЗВАНИЯ харнеса («через feature pipeline / forge / фичепайплайн») — это НЕ выбор
  пути full, а просто «прогони через forge»: всё равно классифицируй. Сигнал full — только явное
  «с нуля / собери требования / нет тикета / нужен BRD/анализ». Есть Jira-ключ + «сделай задачу/
  фичу [KEY]» и спека уже существует → скорее lite (tech-design по готовой спеке). Во всех
  неоднозначных случаях — спроси. Роутер сам не пишет код и не трогает Jira — только выбирает
  путь, выставляет конфиг и делегирует.
---

# Router — выбор пути full | lite

> Один харнес (`<project>/.gigacode/`), одни хуки. Роутер только классифицирует и делегирует —
> вся работа идёт в выбранном оркестраторе. Запуск харнеса: `gigacode --experimental-hooks -p "..."`.

## 0. Предусловия
- cwd = корень репо кода (`<toplevel>`), там же `.gigacode/`. Харнес развёрнут.
- Прогони preflight — **exit 1 = стоп** (ENFORCEMENT OFF или битые пути харнеса; чини деплой, не продолжай):
  ```
  python3 <project>/.gigacode/hooks/preflight.py --project <toplevel>
  ```

## 1. Выбор пути (ПЕРВОЕ действие)

> **Путь — это ОБЯЗАТЕЛЬНОЕ решение (`pipeline.mode`), а не догадка.** Порядок: (1) если
> `pipeline.mode` уже записан в `pipeline.json` (headless-предзапись) — используй его, не
> переспрашивай; (2) иначе спроси `ask_user_question`; (3) если вопрос не отрендерился (headless/
> форк — пустой ответ), НЕ угадывай и НЕ уходи в full по названию харнеса: остановись и попроси
> предзапись `config.py set pipeline.mode lite|full` + перезапуск. «feature pipeline» в промпте
> ≠ full.

Спроси пользователя (`ask_user_question`) — до любого субагента/агента:

> **Что делаем?**
> - **full** — фича/изменение С НУЛЯ: собрать требования (BRD), спроектировать (SDD/tech-design),
>   завести задачи в Jira, реализовать и довести до PR. Путь `feature-pipeline`.
> - **lite** — исполнить УЖЕ ПОДГОТОВЛЕННУЮ подзадачу из Jira (есть описание + acceptance
>   criteria) по СУЩЕСТВУЮЩЕЙ спеке: grounding → tech-design по спеке → TDD → покрытие → PR →
>   отчёт. Путь `forgelite`.

Подсказки для рекомендации (не решай молча, но можешь предложить):
- Есть ключ Jira и это Sub-task/Task/Bug с внятными AC, спека уже есть, один сценарий → **lite**.
- Свободная идея, Story/Epic, нет AC/нет спеки, несколько сценариев, нужен BRD с нуля → **full**.
- Просто назвали «feature pipeline / forge» без «с нуля/BRD» — это НЕ сигнал full; классифицируй по сути.

## 2. Делегирование

### Выбран **lite**
1. Выстави lite-конфиг (один общий `.gigacode`; `--project` ДО `set`; `auto_max_risk` sensitive → `--confirm`).
   **`pipeline.mode` — записать первым** (артефакт решения о пути, universal-режим fail-closed):
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode lite
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.auto_max_risk R2 --confirm
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set autonomy.criticality medium
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set quality.eval_enabled false
   ```
   (R2 → код/тесты/доки идут авто; TDD/SoD держат хуки. eval off — у lite нет eval-plan-фазы.)
2. Прочитай и строго следуй: `read_file("<project>/.gigacode/skills/forgelite/SKILL.md")`.
   Дальше веди задачу по нему (стейт в namespace `forgelite`).

### Выбран **full**
1. Запиши путь и не переопределяй autonomy — у full свой гейт критичности (после BRD):
   ```
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <toplevel> set pipeline.mode full
   ```
2. Прочитай и строго следуй: `read_file("<project>/.gigacode/skills/feature-pipeline/SKILL.md")`.
   Дальше веди фичу по нему (стейт в namespace `feature-pipeline`).

## 3. Границы роутера
- Роутер НЕ дублирует фазы, НЕ пишет код, НЕ ходит в Jira/Bitbucket. Только: спросить →
  (для lite) выставить конфиг → `read_file` нужного оркестратора → следовать ему.
- Скоуп-несоответствие (например, выбрали lite, а задача — Epic) ловит скоуп-чек выбранного
  оркестратора — он остановится и предложит сменить путь.
- Один активный пайплайн за прогон. Хуки резолвят активный skill/feature по самому свежему
  манифесту в `ground/statements/*/*/`, поэтому full и lite не мешают друг другу.
