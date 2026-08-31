# Задача 002 — Обновить счётчик скиллов и команд в README.md

## Статус
Закрыта · 2026-08-22

## Контекст
В `README.md` (секция «Проверено (2026-08-05, qwen 0.19.5)») указано:
> Линкуется без ошибок; trust-промпт перечисляет все **19 скиллов + 4 команды**.

Фактическое состояние расходится с датой фиксации.

## Симптом расхождения
- Фактически в `skills/` — **20** каталогов с `SKILL.md` (`brd-grounder, brd-interview,
  bugfix-developer, business-requirements, config-helper, defect-analyzer, feature-pipeline,
  forgefix, forgelite, harness-verifier, java-spring-dev, jira-task-writer, minor-defect-fix,
  pipeline-state, project-grounder, router, sdd, system-analyst, tech-design, test-writer`).
- Фактически в `commands/` — **5** команд (`forge-fix.md, forge-lite.md, forge-merge.md,
  forge-spec.md, forge.md`).
- `skills/SKILLS-REGISTRY.md` перечисляет ровно 20 скиллов — он корректен.

## Следствие
Документация не отражает фактический состав харнеса; количество «19 скиллов + 4 команды»
устарело и вводит в заблуждение при аудите состава пакета.

## Критерии приёмки
- [ ] В `README.md` корректный счётчик (20 скиллов + 5 команд) или счётчик удалён в пользу
      ссылки на актуальный `skills/SKILLS-REGISTRY.md`.
- [ ] Указанная дата проверки/версия при необходимости уточнена.
- [ ] Реестр `skills/SKILLS-REGISTRY.md` не менялся зря (он уже корректен).

## Решение
`README.md` переписан под project-модель; в новой версии явно перечислены все 20 скиллов
и 5 команд (см. раздел «Состав»). Старая секция «Проверено (2026-08-05, qwen 0.19.5)» с
цифрой «19 скиллов + 4 команды» удалена вместе с extension-нарративом — её актуальность
восстанавливать незачем, проверки приведены к project-сценарию.

## Файлы
- `README.md` (раздел «Состав»)