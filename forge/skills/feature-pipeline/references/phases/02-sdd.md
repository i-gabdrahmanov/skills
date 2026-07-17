# Фаза 02-sdd — Спецификация (SDD)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** check_sdd_doc PASS + Гейт SDD (утверждение; маркер
> `sdd-approved-<slug>` — enforced в update.py); закрой шаг 02-sdd

## 5. Фаза 2 — Спецификация (SDD) и Дизайн

**🚨 ОБЕ подфазы — ОБЯЗАТЕЛЬНО через agent(). Не делай inline.**

Цепочка: **BRD → SDD (§5a) → Tech Design (бриф `02-design.md` §5b)**. Сначала субагент `sdd` пишет
строгую спецификацию `sdd.md` из BRD; после её утверждения субагент `tech-design`
проектирует **по `sdd.md`** (не по BRD напрямую) и выдаёт `tech-design.md` + `task-plan.json`.

### 5.0 Preflight-validate перед запуском (обязательно)

Перед вызовом agent() для каждой подфазы — проверь, что предыдущий шаг был сделан субагентом:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/preflight-validate.py \
    --project <project> \
    --feature <slug> \
    --step-id <id>
```
- **exit 0** — можно вызывать agent()
- **exit 1** — СТОП. Предыдущий шаг был сделан inline. Не продолжай, пока не исправлено.

---

### 5a. Фаза 02-sdd — SDD спецификация → Гейт SDD

**🚨 ОБЯЗАТЕЛЬНО через agent(). Не пиши `sdd.md` сам.**

Запусти субагента SDD-писателя по контракту `get_prompt.py 4.0a`. НЕ читай
`sdd/SKILL.md` в свой контекст — субагент прочитает его сам.
```
agent(
  subagent_type="general-purpose",
  description="Write SDD spec for <slug>",
  prompt="<вывод `get_prompt.py 4.0a`; подставь: slug, пути к brd.md и grounding-excerpt.json, Jira-ключ>"
)
```

**Обработка результата субагента (мини-интервью по неясностям).** Распарсь JSON:
1. **Если есть `pending_questions`** (`status: needs_input`) — задай каждый вопрос
   пользователю через `ask_user_question`, собери ответы. Перезапусти субагента SDD,
   передав `answers` на эти вопросы (sdd.md ещё НЕ написан — gate не гоняем). Повторяй,
   пока `pending_questions` не опустеет.
2. **Когда `status: completed`** (неясностей нет) — субагент написал `sdd.md`; иди к
   execution-gate ниже.

После того как субагент вернул `completed`, прогони детерминированный execution-gate:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py sdd <slug> --project-root <project>
```
- gate fail → верни субагента на доработку (допиши недостающие секции/сценарии Given-When-Then).
- gate pass → **Гейт SDD** (см. ниже).

**Гейт SDD — утверждение спецификации.** Покажи резюме SDD: суть фичи, ключевые сценарии
(включая ошибочные ветки), затрагиваются ли новые API/данные, главный риск. Спроси:
«утверждаем спецификацию / правки?».
- Правки SDD → верни `sdd` на доработку (BRD не трогаем).
- Если всплыло **новое бизнес-требование** → откат к фазе 0 (BRD).
- После явного «да» зафиксируй утверждение СКРИПТОМ — без этого маркера update.py
  детерминированно НЕ закроет шаг:
  ```bash
  python3 <project>/.gigacode/skills/pipeline-state/scripts/record_approval.py \
      --project <project> --key sdd-approved-<slug> --approved-by user \
      --reason "SDD утверждён пользователем"
  ```

Если пользователю нужно вынести `sdd.md` на согласование аналитикам (ветка/коммит/пуш) —
это он делает сам, промптом или руками; пайплайн доков не коммитит.

После утверждения обнови `02-sdd` (только при `pass` execution-gate):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 02-sdd --status completed \
    --artifacts '{"sdd": "docs/feature-pipeline/<slug>/sdd.md"}'
```

---
