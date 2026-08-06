# Фаза 02-sdd — Спецификация (SDD)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** check_sdd_doc PASS (валидатор состава SDD — обязательные разделы по
> политике `sdd.security_gate` из pipeline.json: hard | applicability | soft, дефолт
> applicability) + Гейт SDD (утверждение; маркер `sdd-approved-<slug>` — enforced в update.py);
> закрой шаг 02-sdd

## 5. Фаза 2 — Спецификация (SDD) и Дизайн

**🚨 ОБЕ подфазы — ОБЯЗАТЕЛЬНО через agent(). Не делай inline.**

Цепочка: **идея/Jira → SDD (§5a) → Tech Design (бриф `02-design.md` §5b)**. Сначала субагент
`sdd` пишет строгую спецификацию `sdd.md` из исходной идеи/Jira + grounding (бизнес-анализ/BRD
выключен — `pipeline_phases.BRD_ENABLED`; если включён — `sdd` берёт `brd.md`); после её
утверждения субагент `tech-design` проектирует **по `sdd.md`** и выдаёт `tech-design.md` +
`task-plan.json`.

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
  prompt="<вывод `get_prompt.py 4.0a`; подставь: slug, исходную идею/описание фичи, путь к grounding-excerpt.json, Jira-ключ (и путь к brd.md, только если BRD включён)>"
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
- Правки SDD → верни `sdd` на доработку.
- Если всплыло **новое требование/пробел** → верни `sdd` на доработку с уточнением через
  мини-интервью (`pending_questions`); отдельной фазы бизнес-анализа нет (BRD выключен).
- После явного «да» зафиксируй утверждение СКРИПТОМ — без этого маркера update.py
  детерминированно НЕ закроет шаг:
  ```bash
  python3 <project>/.gigacode/skills/pipeline-state/scripts/record_approval.py \
      --project <project> --key sdd-approved-<slug> --approved-by user \
      --reason "SDD утверждён пользователем"
  ```

Если пользователю нужно вынести `sdd.md` на согласование аналитикам (ветка/коммит/пуш) —
это он делает сам, промптом или руками; пайплайн доков не коммитит.

### Гейт критичности (ОБЯЗАТЕЛЬНО, сразу после утверждения SDD)

> Раньше этот гейт стоял в фазе BRD; бизнес-анализ выключен (`pipeline_phases.BRD_ENABLED`),
> поэтому критичность спрашиваем здесь — сразу после утверждения спецификации (пользователь
> уже видел полный SDD и может здраво оценить риск). Момент выбран так, чтобы порог был
> выставлен ДО design/jira/TDD. (Если BRD включат обратно — гейт остаётся и в фазе BRD, и
> здесь; повторный `set_criticality.py` идемпотентен, просто перезапишет то же значение.)

Спроси у пользователя **критичность фичи** (`ask_user_question`) — это задаёт, насколько агрессивно
форсятся гейты. Без выбора `gate-guard` заблокирует любое R2+ действие. _(Лимит рантайма: поле
`header` у `ask_user_question` — **≤ 12 символов**, иначе вызов падает с ошибкой валидации.)_

| Критичность | Что это | `auto_max_risk` | Поведение гейтов |
|---|---|---|---|
| **Низкая** | эксперимент, non-prod, внутр. инструмент | `R2` | фичекод авто; гейтятся R3+ пути (auth/PII/инфра) |
| **Средняя** | обычная прод-фича (дефолт) | `R1` | jira/секьюрные пути — под гейтами |
| **Высокая** | auth / платежи / PII / инфра / критичный путь | `R0` | почти всё требует подтверждения/approval/evidence |

После ответа **запиши критичность скриптом** — он атомарно проставит И `criticality`, И производный
`auto_max_risk` по карте (`low→R2 / medium→R1 / high→R0`). **Не правь `pipeline.json` руками**:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/set_criticality.py --criticality <low|medium|high>
```
Только теперь иди дальше — `gate-guard` читает `autonomy.auto_max_risk` из конфига и применяет порог
per-feature.

После утверждения обнови `02-sdd` (только при `pass` execution-gate):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 02-sdd --status completed \
    --artifacts '{"sdd": "docs/feature-pipeline/<slug>/sdd.md"}'
```

---
