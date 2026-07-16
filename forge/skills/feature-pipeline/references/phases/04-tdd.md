# Фаза 04-tdd — Build (per-task, TDD RED → GREEN)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** per-task: red-judge PASS → record_gate(check_tests_red) → закрой 04-test-<id>; build+reuse-judge PASS → record_gate(check_build) → закрой 04-build-<id>

## 7. Фаза 3 — Build (по задачам, **TDD: RED → GREEN**)

**🚨 ВСЕ шаги Build — через agent(). Оркестратор НЕ пишет код, НЕ правит файлы.**

По умолчанию (`pipeline.json quality.tdd: true`) каждая задача делается по TDD: **сначала тесты
(они падают), потом код, который их зеленит.** Иди по `task-plan.tasks` в порядке `depends_on`.

### 7.0 Baseline зелёного (ОБЯЗАТЕЛЬНО, ОДИН раз, ДО первого кода)

Перед любой правкой кода сними **отметку зелёного** по тестам затронутых модулей — чтобы потом
детерминированно отличить «я сломал существующий тест» (регресс) от «тест и так был красный»
(pre-existing/infra). На прогоне #3 агент сломал Spring-тесты и не признал — это закрывает дыру.
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/module_tests.py snapshot \
    --root "<project>" --from-taskplan "<папка фичи>/task-plan.json" \
    --out "<project>/ground/statements/feature-pipeline/<slug>/test-baseline.json"
```
Гоняется ОДИН раз (модули из `task-plan.tasks[].modules`). Файл `test-baseline.json` = отметка;
её сверяет регресс-гейт в бриф `05-verify.md` §8. Если затронутый модуль уже красный — это зафиксируется как
pre-existing и НЕ будет считаться твоей виной (но видно в отчёте).

### 7.1 Per-task: RED (субагент-тестописатель)

> **ВАЖНО: TDD RED = тесты ОБЯЗАНЫ падать.** Если метод/класс уже частично реализован
> в кодовой базе — тесты должны падать на assert'ах времени выполнения (не компиляции):
> - проверять, что возвращаемое значение не соответствует ожидаемому (`assertNull`, `assertThrows`)
> - использовать разные сценарии (пустой список, неверный статус, умерший поток)
> - не использовать mock-стабы, которые «просто проходят» — mock должен верифицировать
>   НЕВЕРНОЕ поведение, которое будет исправлено в GREEN
>
> **Проверка RED:** `check_tests_red.py` — compile OK + ПО-ТЕСТОВО (JUnit XML прогона):
> выполнился ≥1 тест и ВСЕ выполненные упали. Exit-кода раннера НЕдостаточно: один красный
> тест валит весь прогон, и N зелёных новых тестов (вакуумных — проходят без реализации)
> сходили бы за RED. Все тесты проходят — это GREEN; есть зелёные в прогоне — FAIL
> (перепиши их падающими). Скоупь прогон на тест-классы задачи через `--test-filter`.

Для каждой задачи вызови agent() с контрактом тестописателя `get_prompt.py 4.1`:
```
agent(
  subagent_type="general-purpose",
  description="TDD red: tests for <taskId> in <slug>",
  prompt="<вывод `get_prompt.py 4.1`; подставь: taskId, slug, acceptance, tech-design сигнатуры>"
)
```

**После возврата субагента**, до стабов — выполни red-judge:
```
agent(subagent_type="general-purpose", description="red-judge for <taskId>",
      prompt="<вывод `get_prompt.py 7.2` (red-judge)>")
```
Затем:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py red <slug> --recheck
```

**При PASS — зафиксируй RED-гейт через раннер и закрой `04-test-<taskId>`** (без evidence от
`record_gate` update.py шаг не закроет — самоотчёт субагента не доказательство):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py \
    --project <project> --skill feature-pipeline --feature <slug> --step-id 04-test-<taskId> \
    --cmd "python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_tests_red.py <папка>/task-plan.json --root <project> --task <taskId> --test-filter '<имя нового тест-класса задачи>'"
```
`--test-filter` ОБЯЗАТЕЛЕН: без него прогоняется весь сьют, зелёные СТАРЫЕ тесты провалят
по-тестовый RED-гейт. Фильтр — glob по новым тест-классам задачи (напр. `OrderExportServiceTest`).
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 04-test-<taskId> --status completed
```

### 7.2 Per-task: стабы сигнатур (оркестратор)

После прохождения red-judge — запусти субагента для создания стабов. Контракт: `get_prompt.py 4.2`:
```
agent(
  subagent_type="general-purpose",
  description="Stubs for <taskId> in <slug>",
  prompt="<вывод `get_prompt.py 4.2`; подставь: taskId, slug>"
)
```

### 7.3 Per-task: GREEN — реализация (субагент java-spring-dev)

```
agent(
  subagent_type="general-purpose",
  description="GREEN: code for <taskId> in <slug>",
  prompt="<вывод `get_prompt.py 4.3` (полный контракт); подставь: taskId, slug, task-plan, tech-design>"
)
```

### 7.4 Judge-gate GREEN: build-judge

После возврата субагента — запусти build-judge:
```
agent(subagent_type="general-purpose", description="build-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.3` (build-judge)>")
```
build-judge — гибрид: вердикт считает СУБАГЕНТ, а run_judge на ингесте АВТОМАТИЧЕСКИ
применяет детерминированный пол (stubs/TODO в изменённом src/main) и AND-ит с вердиктом
(`INGEST_FLOOR_PHASES`) — LLM-«PASS» на коде со стабами не сохранится как passed:true.
**Сохрани JSON-вердикт субагента в файл и передай его через `--from-output`**
(иначе вердикта на диске не будет и шаг не закроется), затем подтверди `--recheck`:
```bash
# verdict.json — JSON, который вернул субагент build-judge ({"passed":..., "blocking_issues":[...]})
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py build <slug> --from-output verdict.json
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py build <slug> --recheck
```

И execution-gate:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_build.py "<папка>/task-plan.json" --task <taskId>
```

### 7.5 Judge-gate GREEN: reuse-judge (после build-judge, ДО закрытия шага)

После того как build-judge дал PASS — запусти reuse-judge (судья качества: нет велосипедов,
дублирующих доступные библиотеки/util проекта). Гибрид: LLM-субагент + детерминированный regex.

Шаг 1 — LLM-субагент (контракт: `get_prompt.py 7.7`):
```
agent(subagent_type="general-purpose", description="reuse-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.7` (reuse-judge) + git diff + путь к scan/reuse.json>")
```
Шаг 2 — ингест вердикта и детерминированная проверка велосипедов по git diff:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py reuse <slug> \
  --from-output verdict.json --diff-base <base> --project-root <project>
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py reuse <slug> \
  --recheck --diff-base <base> --project-root <project>
```
`<base>` — родительская ветка/коммит задачи (как в check_coverage). Если каталога
`scan/reuse.json` нет — он создаётся в фазе 1 (grounding) через project-grounder.

**FAIL → ре-итерация** (раздел 0.6): ошибки в errors.json, верни код java-spring-dev на
доработку — замени велосипед на библиотеку/util из каталога. Закрывай `04-build-<taskId>`
только когда **оба** судьи (build-judge И reuse-judge) PASS (`required_judges` шага — оба).

Закрой `04-build-<taskId>` явной командой, только когда **оба** судьи PASS. Перед закрытием
зафиксируй build-гейт через раннер (без evidence от `record_gate` update.py шаг не закроет):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py \
    --project <project> --skill feature-pipeline --feature <slug> --step-id 04-build-<taskId> \
    --cmd "python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_build.py <папка>/task-plan.json --root <project> --task <taskId>"
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 04-build-<taskId> --status completed
```

> Если `quality.eval_enabled: false` — хук пропускает eval-проверки.
> Если `quality.tdd: false` — допускается старый порядок.

---
