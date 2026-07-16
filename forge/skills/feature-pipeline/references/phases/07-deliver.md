# Фаза 07-deliver — Deliver (per-task, stacked PR) → Гейты 4-6

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** Гейты 4-6 (commit/push/PR — только после «да») + delivery-judge; закрой 07-deliver-<id>/07-report

## 10. Фаза 6 — Deliver (per-task, stacked) → Гейты 4-6

Полная механика веток и stacked-PR —
[`references/stacked-pr-delivery.md`](references/stacked-pr-delivery.md). Кратко:

- **Интеграционная ветка фичи `feature/<slug>`** — коммитить в неё ЗАПРЕЩЕНО (enforced:
  gate-guard блокирует commit/merge/push в неё); она собирается ТОЛЬКО мерджем PR сабветок
  задач, а создаёт её `story_branch_push.py` (Гейт 5, идемпотентно, от default-tip).
- Каждая задача → своя **сабветка** `feature/<jira-key>` (или `feature/<slug>-<taskId>`
  без Jira). Сабветки **stacked**: ветка задачи ответвляется от ветки той, от которой она
  зависит (`depends_on`); корневые — от default-tip, их PR таргетят `feature/<slug>`.
- Коммит каждой задачи — только её файлы и только в её сабветку; сообщение в стиле
  проекта (`git log`), с ключом Jira, **без** `Co-Authored-By`.

**Judge-gate deliver: delivery-judge (перед Гейтом 4, до коммитов).**
Запусти delivery-judge перед тем, как показывать план коммитов. Он проверяет готовность
к доставке: нет stubs, Jira консистентна, нет секретов, git status чист.

```
agent(subagent_type="general-purpose", description="delivery-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.5` (delivery-judge) + task-plan + jira-result + git status + diff>")
```

delivery-judge — гибрид: вердикт считает субагент, а run_judge на ингесте АВТОМАТИЧЕСКИ
применяет детерминированный пол (секреты в изменённых файлах, `INGEST_FLOOR_PHASES`) —
LLM-«PASS» при утёкшем секрете не сохранится как passed:true. Сохрани его JSON и передай
через `--from-output`, затем подтверди `--recheck`:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py delivery <slug> --from-output verdict.json
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py delivery <slug> --recheck
```

Результаты exit-кодов — как у eval-judge (бриф `03-jira.md` §6): exit 0 = pass, exit 1 = покажи blocking_issues пользователю, exit 2 = техническая ошибка.

**Гейт 4 — коммиты.** Покажи план коммитов (какая задача → какие файлы → сообщение) по
всем задачам сразу. Спроси «коммитим?». Только после «да».

**Перед Гейтом 5 — детерминированный план доставки (идемпотентность, защита от дублей PR).**
Доставка необратима, а Bitbucket-PR не дедуплицируется: ре-ран после падения вслепую создаст дубль
ветки/PR. ДО push/PR посчитай из git-веток + manifest, что уже сделано:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/delivery_plan.py \
    "<папка фичи>/task-plan.json" \
    --manifest "<project>/ground/statements/feature-pipeline/<slug>/manifest.json" \
    --pipeline-config "<project>/ground/pipeline.json"
```
План даёт `create / resume / skip` на задачу: `skip` — `07-deliver-<id>` уже completed; `resume` —
ветка есть, шаг не закрыт (не пересоздавай ветку — доведи push/PR, проверив существующий PR);
`create` — с нуля. Действуй строго по плану.

**Гейт 5 — push + stacked PR.** Покажи план веток и PR (для каждого: source→target,
заголовок, тело со ссылкой на Jira). Спроси «пушим и создаём PR?». После «да» — сначала
заведи интеграционную ветку (`story_branch_push.py --feature <slug>`, идемпотентно),
затем push сабветок в порядке зависимостей, затем PR через Bitbucket MCP (target =
сабветка-родитель, для корневых — `feature/<slug>`; последним — PR `feature/<slug>` →
default, «мержить после всех»). Сюда же — push/PR ветки спеки (фаза 5). На каждый
`git push` хук `evidence-enforcer` детерминированно проверяет сообщение HEAD-коммита:
трейлер `Co-Authored-By` запрещён (блок → `git commit --amend`).

**Гейт 6 — отчёт в Jira.** Подготовь черновик комментария в Story (что сделано, файлы,
тесты/покрытие, ссылки на PR по задачам, статус спеки). Покажи целиком, спроси «отправить?».
Отправляй только после «да».

**Gate перед закрытием доставки:**
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_delivery.py \
    "<папка фичи>/task-plan.json" \
    --manifest "<project>/ground/statements/feature-pipeline/<slug>/manifest.json" \
    --pipeline-config "<project>/ground/pipeline.json"
```
На каждую задачу (при bitbucket off — skip) закрывай `07-deliver-<id>` явной командой, а в
самом конце — `07-report`:
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 07-deliver-<taskId> --status completed
# после доставки всех задач и отчёта в Jira:
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 07-report --status completed
```

Детали MCP-команд (Jira-комментарий, создание PR, workspace/repo) — общие с
`minor-defect-fix`: `<project>/.gigacode/skills/minor-defect-fix/references/{jira-workflow,bitbucket-workflow,coverage}.md`.

---
