# Фаза 02-eval-plan — Eval-plan (EDD)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** eval-judge PASS; закрой шаг 02-eval-plan

### 5c. Eval-Driven Development: генерация eval-plan (PDLC v3.5)

**Сразу после утверждения Гейта 2 и до манифеста:** сгенерируй `eval-plan.json`
из `task-plan.json`. Eval'ы — детерминированные автоматические проверки, которые
пишутся ДО кода и форсят Eval-Driven Development: агент не может записать файл в
`src/main/`, пока eval'ы его задачи не пройдены.

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/build_evals_from_design.py \
    "<папка фичи>/task-plan.json" \
    --pipeline-config "<project>/ground/pipeline.json" \
    --coverage-script "<project>/.gigacode/skills/minor-defect-fix/scripts/check_coverage.py"
```

Скрипт генерирует для каждой задачи три типовых eval'а:
- **compile** — проверка, что проект компилируется
- **coverage** — проверка JaCoCo покрытия через `check_coverage.py` (инкрементально по diff задачи; база зафиксирована `HEAD~1`)
- **test_pass** — бинарный регресс-гейт: вся тест-сюита зелёная (exit 0) после задачи, без порога и без скоупа на задачу

Пороги берутся из `pipeline.json quality.*`. Результат — `<папка фичи>/eval-plan.json`.

> **Eval'ы — это не опциональные тесты.** Хук `eval-guard` блокирует запись кода,
> пока eval'ы задачи не пройдены (PreToolUse-хук). Если eval-plan не сгенерирован —
> блокировка не срабатывает (fail-open), но это деградация: без eval-plan пайплайн
> теряет Eval-Driven гарантию качества и работает как обычный TDD-пайплайн.

**Конфигурация eval в `pipeline.json`:**
```json
"quality": {
    "eval_enabled": true,
    "eval_threshold": 0.95,
    ...
}
```
По умолчанию `eval_enabled: true`. Отключить можно установкой `eval_enabled: false`
(например, для экспериментов или прототипов).

### Опциональный гейт: трассируемость (`quality.traceability_check`)

Если `quality.traceability_check: true` в `pipeline.json` (по умолчанию `false`) — сразу после
генерации eval-plan и ДО закрытия `02-eval-plan` прогони детерминированную проверку матрицы
«требование → SDD → задача → eval → тест»:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_traceability.py \
    "<папка фичи>/task-plan.json"
```
- **exit 0** — цепочка замкнута, продолжай.
- **exit 2** — есть задача без eval / битый `sdd_ref` / пустой acceptance → почини task-plan/eval-plan
  и перезапусти. (`--strict` валит и на warnings.)

### Judge-gate: eval-judge (обязательно, перед закрытием `02-eval-plan`)

**Сразу после генерации eval-plan, ДО того как начинается код**, запусти eval-judge.
Он проверяет, что eval'ы покрывают все acceptance criteria, пороги адекватны, нет дубликатов.

Запусти субагента eval-judge (контракт: `get_prompt.py 7.1`):
```
agent(subagent_type="general-purpose", description="eval-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.1` (eval-judge) + пути к task-plan.json и eval-plan.json>")
```

Затем выполни детерминированную проверку через run_judge.py:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py eval <slug>
```

- **exit 0** — `passed: true` → шаг `02-eval-plan` можно закрывать (с `--artifacts`):

  ```bash
  python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
      --skill feature-pipeline --feature <slug> \
      --step-id 02-eval-plan --status completed \
      --artifacts '{"eval-plan": "docs/feature-pipeline/<slug>/eval-plan.json"}'
  ```
- **exit 1** — `passed: false` → покажи blocking_issues пользователю, НЕ закрывай шаг.
  Верни eval-judge на доработку (перегенерируй eval-plan или task-plan).
- **exit 2** — техническая ошибка (нет файлов) → остановись и разберись.

**Важно:** eval-judge — это gate, а не опция. Если eval-plan сгенерирован, но eval-judge
не запущен или вернул FAIL — шаг `02-eval-plan` НЕ закрывается. Это блокирует переход
к Build (RED) и предотвращает ситуацию «eval'ы есть, но никто не проверил их качество»
(дефект #5 из KIDPPRB-8639).

Вердикт сохраняется в `ground/statements/feature-pipeline/<slug>/judges/eval-judge.json`.

---
