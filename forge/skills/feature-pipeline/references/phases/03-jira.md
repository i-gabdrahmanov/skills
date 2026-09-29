# Фаза 03-jira — Постановка задач в Jira → Гейт 3

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** approval-маркер `jira-plan-<slug>` (пользователь подтвердил СОСТАВ задач)
> + evidence `record_gate` по `check_jira.py`; только после этого `update.py --status completed`.
>
> **Почему строже остальных.** Это единственная фаза с необратимым внешним эффектом: задачи
> в трекере создаются насовсем, пайплайн их не удалит. До 2026-09-28 она не гейтилась НИЧЕМ —
> ни судьями, ни gate-result, ни approval, — а `state-recorder` на `SubagentStop` закрывает
> шаг как `completed` по умолчанию (статус `failed` он выводит только из полей
> `status`/`result`/`ok`/`passed`, произвольное «error» в тексте не читает). MCP-инструменты
> при этом не видел ни один хук; теперь write-цепочка матчит и `mcp__.*`.

## 6. Фаза 2.5 — Jira → Гейт 3

**🚨 ОБЯЗАТЕЛЬНО через `agent()`. Не делай inline.** Как фаза Design (бриф `02-sdd.md` §5): прочитав
`jira-task-writer/SKILL.md` в свой контекст и «выполнив его сам», ты запустишь его inline —
тогда цикл `pending_questions` не отработает и **вопрос про Epic потеряется** (это уже
случалось). Подробную MCP-логику читает САМ субагент, не ты.

**Второе правило: субагент НЕ вызывает `ask_user_question`.** Он собирает черновик и
возвращает JSON с `pending_questions` (Epic, спринт). Все вопросы пользователю задаёшь ТЫ.

НЕ читай `jira-task-writer/SKILL.md` в свой контекст — субагент прочитает его сам.
Запусти субагента по контракту `get_prompt.py 4.5` (он сам прочитает task-plan.json,
brd.md, pipeline.json по путям):
```
agent(subagent_type="general-purpose",
      description="Jira tasks for <slug>",
      prompt="<вывод `get_prompt.py 4.5`; подставь: пути к task-plan/brd/pipeline.json, slug>")
```

### Обработка результата субагента

Субагент возвращает JSON в `llmContent`. Распарсь его:

1. **Если есть `pending_questions`** — задай каждый вопрос пользователю через
   `ask_user_question` (по одному, последовательно). Собери ответы.
   Перезапусти субагента, передав ему `answers` на `pending_questions`.
   Повторяй, пока `pending_questions` не опустеет.

2. **Когда `pending_questions` пуст** — покажи черновик пользователю ЯВНО: Story + список
   Sub-task с их числом («Story + 4 подзадачи: T1…T4»). Спроси `ask_user_question`
   «Создавать эти задачи в Jira?» с вариантами:
   - «Да» — создавай **ровно показанный черновик** (см. шаг 4)
   - «Правки» — см. шаг 3 (цикл правок)
   - «Не создавать» — `skipped: true`

3. **На «Правки» — цикл, а не разовая реплика.** Уточни у пользователя, что изменить
   (например: «нужна 1 задача вместо 4», «объедини T2–T4», «переименуй Story», «убери
   подзадачи»). **Перезапусти субагента** с полем `revision: "<что изменить>"` и
   `confirmed: false`. Субагент вернёт НОВЫЙ черновик — **вернись к шагу 2** (покажи новый
   черновик, снова спроси Да/Правки/Не создавать). Зацикливай, пока не «Да» или
   «Не создавать».
   > **🚨 Никогда не создавай задачи, пока пользователь не подтвердил «Да» на ПОСЛЕДНЕМ
   > показанном черновике.** Создание идёт по подтверждённому черновику, НЕ по исходному
   > `task-plan.json`. Если пользователь сказал «нужна одна задача» — в Jira должна уйти
   > одна, даже если в task-plan их 4.
   > **Если меняется ЧИСЛО задач** (4→1) — это расхождение с дизайном. Рекомендуемый путь:
   > вернуться в `tech-design` (бриф `02-design.md` §5b), поправить `task-plan.json` до нужной разбивки и заново
   > собрать Jira-черновик — тогда сойдётся и downstream TDD/Build (`04-test/build-<taskId>`),
   > и гейт `check_jira` (он требует паритет: 1 Story + по задаче на каждую запись task-plan).
   > Осознанное расхождение (Jira укрупнённо, task-plan детально) приведёт к FAIL `check_jira`
   > — тогда закрывай шаг только через ручной override (§0.6.1) с обоснованием.

4. **На «Да» — СНАЧАЛА зафиксируй согласие, потом создавай.** Без маркера
   `jira-plan-<slug>` `gate-guard` не пропустит продуктивную запись фазы (`phase_approvals`),
   и это намеренно: задачи в трекере необратимы, поэтому «да» пользователя должно остаться
   в журнале, а не только в диалоге.
   ```bash
   python3 <project>/.gigacode/skills/pipeline-state/scripts/record_approval.py \
       --project <toplevel> --key jira-plan-<slug> --approved-by user \
       --reason "подтверждён черновик: Story + <N> подзадач"
   ```
   > Цитата (`--evidence`) здесь НЕ обязательна: это approval ПЛАНА, он двигает прогон
   > вперёд, а не снимает защиту. Цитату требуют только ключи класса
   > `gate-override-*`/`rollback-*`/`skip-judges-*`/`policy-downgrade-*`.

   Затем создай Story и Sub-task **строго по последнему подтверждённому черновику**
   (не по сырому task-plan) через Jira MCP, следуя `references/jira-create-workflow.md`.
   Сохрани результат:
   ```bash
   python3 <project>/.gigacode/skills/jira-task-writer/scripts/check_jira.py \
       "<папка фичи>/task-plan.json" --result "<папка фичи>/jira-tasks-result.json" \
       --pipeline-config "<project>/ground/policy.json"
   ```

5. **На пустой ответ** (не отобразился вопрос) — не паникуй:
   - Запиши `jira-tasks-result.json` с `skipped: true` и причиной
   - Напиши пользователю: «Не получил ответа на вопрос о создании Jira-задач — пропущено.
     Если хочешь создать задачи позже, скажи "создай задачи по task-plan.json"»
   - Иди дальше в режиме «без Jira»

Результат — `jira-tasks-result.json` (`{story, tasks:{task_id→key}, skipped}`).

**Зафиксируй гейт** (без evidence `update.py` шаг не закроет):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py \
    --project <toplevel> --skill feature-pipeline --feature <slug> \
    --step-id 03-jira --cmd "python3 <project>/.gigacode/skills/jira-task-writer/scripts/check_jira.py ..."
```

Обнови `03-jira` с артефактами:

```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 03-jira --status completed \
    --artifacts '{"jira-result": "docs/feature-pipeline/<slug>/jira-tasks-result.json"}'
```

---
