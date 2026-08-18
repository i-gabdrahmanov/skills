# Фаза 06-document — Document (спека)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** spec-judge PASS; закрой 06-spec. Требования-мастер
> (`specs/`) фаза НЕ обновляет — это делает пользователь командой `/forge-spec merge`.

## 9. Фаза 5 — Document

**🚨 ЧЕРЕЗ agent(). Оркестратор НЕ правит спеку сам.**

### 9.1 Спецадаптер (agent)

Контракт: `get_prompt.py 5`:
```
agent(
  subagent_type="general-purpose",
  description="Update spec for <slug>",
  prompt="<вывод `get_prompt.py 5` (полный контракт); подставь: slug, docs_path, diff>"
)
```

### 9.2 Обогащать grounding здесь НЕЧЕМ — и не нужно

Раньше тут стоял `enrich_grounding.py`: он пересканировал код, пересобирал закоммиченный
`grounding-excerpt.json` и дописывал строки в `docs/system-analysis/*.md`. Слой снят целиком.

Инвентарь теперь эфемерный (`ground/inventory/`) и снимается заново тем, кому он нужен, —
`ensure_inventory.py` перед гейтами. Поддерживать его «в свежести» между фичами не требуется:
он производен от кода, а не накапливается по дельтам. Ничего в этой фазе для него делать не
надо.

### 9.2b Требования-мастер: пайплайн его НЕ пишет

Мастер (`specs/<cap>/spec.md`) обновляет **пользователь по запросу** командой `/forge-spec merge
<slug>` — фаза 06 в него не пишет. Причина: merge правит рабочее дерево ЧУЖОГО репо (клона
мастер-спеки), и делать это молча посреди пайплайна нельзя; плюс операция `~` («требование
изменилось») требует решения человека, а не автомата.

Твоя задача здесь — только показать расхождение. Если `docs.master.enabled` — выполни:
```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/spec_cli.py \
    --project-root "<project>" status
```
и включи вывод в итоговое сообщение (§9.3). **Сам ничего не сливай.** При
`docs.master.enabled=false` шаг пропусти целиком (работает только grounding-мастер).

### 9.3 Judge-gate spec

```
agent(subagent_type="general-purpose", description="spec-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.4` (spec-judge) + slug + docs_path + task-plan>")
```
Затем:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py spec <slug> --recheck
```

При PASS — закрой `06-spec` явной командой:
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 06-spec --status completed
```

**Это финальная фаза пайплайна.** Покажи пользователю итог: что изменено (файлы),
тесты/покрытие, обновлённая спека. Коммиты, push, PR и отчёты в Jira пайплайн НЕ делает —
их пользователь выполняет сам (промптом или руками), когда сочтёт артефакт готовым.

Если ведётся требования-мастер (`docs.master.enabled`) — добавь в итог вывод `spec_cli status`
из §9.2b и строку-подсказку: дельта в мастер не слита, слить командой `/forge-spec merge <slug>`
(или `/forge-merge <slug>`). Если мастер в отдельном репо (`docs.master.mode=separate-repo`) —
напомни закоммитить/запушить **и мастер-репо** (обновлённые `system-analysis/`, а после слияния
и `specs/<cap>/spec.md`), forge его не коммитит.

---
