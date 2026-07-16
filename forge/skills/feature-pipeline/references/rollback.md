# Откат пайплайна к шагу (rollback)

Механизм для случаев, когда ре-итерация одной фазы не спасает: гейт SDD вскрыл новое
бизнес-требование («откат к BRD»), дизайн оказался нежизнеспособным после провала сборки
сверх лимитов, пользователь передумал на середине TDD. Откат согласованно сбрасывает
state (манифест + evidence) и код — «не ломая всё»: шаги до точки отката остаются
закрытыми, ручные правки человека вне пайплайна не затираются.

## Семантика

`rollback.py --to-step X` — **X переделывается**: X и всё после него (по порядку манифеста
+ транзитивное замыкание `depends_on`) → `pending`; код восстанавливается на чекпойнт
последнего **остающегося** completed-шага (перед X). «Откат до SDD» = `--to-step 02-sdd`:
SDD пишется заново, код возвращается к состоянию после grounding.

Три механизма под капотом:

1. **Git-чекпойнты** (`pipeline-state/scripts/checkpoint.py`): на каждом закрытии шага
   `update.py` снимает снапшот worktree на служебный ref
   `refs/forge/checkpoints/<feature>/<step-id>` (ветки/HEAD/индекс не трогаются; untracked
   захватывается, .gitignore уважается; `init.py` пишет baseline `00-baseline`).
   Подделка refs заблокирована state-write-guard (`git update-ref refs/forge/*` → deny).
2. **Журнал изменённых файлов** (хук `file-journal.py`, PostToolUse): каждый Write/Edit и
   мутирующий Bash безусловно пишется в `ground/statements/<skill>/<feature>/journal/files.jsonl`
   с привязкой к step_id. Restore-set отката = (git diff worktree↔чекпойнт) ∩ (пути журнала
   после чекпойнта) — потому правки человека мимо пайплайна целы. `--unscoped` — полный
   diff без пересечения (когда журнала нет или нужен тотальный откат).
3. **Evidence-инвалидация**: `_origins/`, `gates/`, `judges/` (по `required_judges`),
   step-scoped `overrides/`, доко-approvals (`brd|sdd-approved-<slug>`), выходы шагов —
   **перемещаются** (не удаляются) в `rollbacks/<ts>/`. Повторное закрытие reset-шага по
   старым доказательствам детерминированно блокируется update.py.

Дополнительно: динамические шаги (`04-test-*`/`04-build-*`/`07-deliver-*`) при откате фазы
дизайна **удаляются** из манифеста (полные копии — в `rollback_history`); `add_steps.py`
пересоздаст их по новому task-plan. Счётчики `reopens`/`failures` reset-шагов обнуляются
(прежние значения — в `rollback_history.prev_counters`): человек одобрил переделку — бюджет
итераций свежий.

## Порядок для оркестратора (Гейт отката, R4)

Откат уничтожает рабочие результаты — это R4-класс, deny-first (`gate-guard.check_rollback`,
секция `rollback` в risk-policy.json). Молча откатывать нельзя:

```bash
# 1. План (readonly, не гейтится): какие шаги сбросятся, какой код восстановится/удалится
python3 <project>/.gigacode/skills/pipeline-state/scripts/rollback.py \
  --project <root> --skill feature-pipeline --feature <slug> --to-step 02-sdd --dry-run

# 2. Покажи план пользователю (ask_user_question) и дождись ЯВНОГО «да»

# 3. Зафиксируй согласие (одноразовое: rollback потребляет маркер)
python3 .../pipeline-state/scripts/record_approval.py --project <root> \
  --key rollback-<slug>-02-sdd --approved-by user --reason "<кто/почему>"

# 4. Откат
python3 .../pipeline-state/scripts/rollback.py \
  --project <root> --skill feature-pipeline --feature <slug> --to-step 02-sdd

# 5. Продолжай как обычный resume: read.py покажет next_runnable
```

`--to-phase <id>` — сахар (первый шаг фазы); `--no-code` — только state;
`--list` — чекпойнты + история откатов. Ключ маркера: `rollback-<feature>-<значение
--to-step|--to-phase>` — как в команде, так и в гейте.

## Сироты и ограничения

- **Jira / ветки / PR не трогаются** (политика stacked-pr-delivery «Откат / отмена»):
  rollback печатает список сирот (Story/сабтаски из `jira-tasks-result.json`, ветки/PR из
  `pr-info.json`) и черновик комментария в Story — постить и убирать решает человек.
- **Docs в отдельном репо** чекпойнтом репо проекта не покрываются — пути из журнала вне
  project root печатаются WARNING'ом, откатывать руками (доко-фазы обычно переписывают док
  заново, так что чаще это не нужно).
- **`in_progress`-шаги** блокируют откат (гонка с работающим субагентом): сначала пометь
  failed через update.py или дождись завершения.
- **Нет чекпойнтов** (фича старше механизма): код не трогается, подсказка `--no-code`.
- **forgelite** покрыт автоматически: чекпойнты пишет update.py (общий для обеих веток),
  reset generic по манифесту; специфики lite-шагов нет.
