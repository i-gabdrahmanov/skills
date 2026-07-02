# Фаза 02-design — Tech Design → Гейт 2

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** check_taskplan PASS + Гейт 2 (пользователь утвердил дизайн); закрой шаг 02-design

### 5b. Фаза 02-design — Tech Design → Гейт 2

**🚨 ОБЯЗАТЕЛЬНО через agent(). Не делай inline.** Вход — утверждённый `sdd.md` (бриф `02-sdd.md` §5a).

#### 5b.0 Pre-design: подготовка компактного data-context

До вызова субагента tech-design сгенерируй **design-context.json** — отфильтрованную
выжимку из grounding-excerpt.json, содержащую только релевантные entities, API-endpoints,
Kafka-топики и таблицы БД для затронутых модулей. Это снижает размер контекста с ~2840
до 50-200 строк и предотвращает проектирование дублирующих сущностей.

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/prepare_design_context.py \
    --brd "<папка фичи>/brd.md" \
    --task-plan "<папка фичи>/task-plan.json" \
    --grounding "<project>/docs/system-analysis/grounding-excerpt.json" \
    --out "<папка фичи>/design-context.json"
```

Если `task-plan.json` ещё не существует (до дизайна), скрипт определит модули по
ключевым словам из BRD. Если и BRD не даёт модулей — будет включено всё (без потери),
что безопасно для pre-design.

Полученный `design-context.json` передаётся в контракт субагента ниже.

#### 5b.1 Запуск субагента tech-design

Вызови agent() со следующим контрактом. НЕ читай SKILL.md тех-дизайнера сам — субагент прочитает.

```
agent(
  subagent_type="general-purpose",
  description="Tech Design for <slug>",
  prompt="""Ты — техлид/архитектор в пайплайне feature-pipeline.

Шаг 0: Прочитай `<project>/.gigacode/skills/tech-design/SKILL.md` целиком.

Вход:
- SDD (спецификация — ОСНОВНОЙ вход): <путь к sdd.md>
- Design context (компактная выжимка grounding под фичу): <путь к design-context.json>
- Grounding (полный — для редких справок): <путь к grounding-excerpt.json>
- BRD (первоисточник, только как справка): <путь к brd.md>

Шаг 1: Проектируй ПО sdd.md и design-context. К grounding-excerpt.json обращайся
        только если design-context не содержит нужной информации. BRD — лишь справка.
Шаг 2: Создай ДВА файла в <папка фичи>/ (sdd.md уже написан на фазе 02-sdd — НЕ трогай его):
  1. tech-design.md — по шаблону `<project>/.gigacode/skills/tech-design/references/design-template.md`
  2. task-plan.json — по шаблону `<project>/.gigacode/skills/tech-design/references/task-plan-schema.md`
     Каждая задача: непустой acceptance (Given-When-Then) + sdd_ref на раздел sdd.md.

Gate (обязательно, перед завершением):
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py design <slug> --project-root <project>
  Должен быть PASS (check_taskplan + check_sdd-линковка). Сохраняет вердикт в judges/design-judge.json.

Выходной JSON:
  {"step_id": "02-design", "status": "completed", "path": "...", "gates": {"design-judge": "PASS"}}
"""
)
```

#### 5b.2 Получение результата

После возврата субагента:
1. Прочитай результат (JSON с полем `step_id`, `path`, `gates`)
2. Прогони execution-gates:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py design <slug> --project-root <project>
```
3. Если gates fail — скажи пользователю, верни субагента на доработку.
4. Если gates pass — покажи **Гейт 2** (см. ниже).

#### 5b.3 Гейт 2 — утверждение дизайна

Покажи резюме: затронутые модули, новые/изменяемые сущности, нужны ли
миграции, число задач, главный риск. Спроси: «делаем так / правки?».
- Правки дизайна → верни `tech-design` на доработку (SDD и BRD не трогаем).
- Если правка по сути меняет **спецификацию** (новый сценарий/контракт) → откат к бриф `02-sdd.md` §5a (SDD).
- Если на гейте всплыло **новое бизнес-требование** → откат к фазе 0 (BRD).

После «да»: добавь в манифест шаги `02-eval-plan` (Eval-Driven),
`04-test-<taskId>` (RED, при `quality.tdd:true`),
`04-build-<taskId>` (depends_on `04-test-<taskId>` и `02-eval-plan`) и
`07-deliver-<taskId>` по `task-plan.tasks` скриптом
`<project>/.gigacode/skills/feature-pipeline/scripts/add_steps.py --skill feature-pipeline
--feature <slug> --steps '<...>'`
(идемпотентно, манифест руками не правь). **Используй именно версию из
`feature-pipeline/scripts/` — она безусловно пересобирает И `gate.json`, И `phase-defs.json`
(фазовую машину). Версию из `pipeline-state/scripts/add_steps.py` здесь НЕ применяй: судей
`required_judges` она проставляет (паритет), но `phase-defs.json` не пересобирает, а `gate.json`
обновляет лишь при его наличии — для новой фичи этого недостаточно.**

> **🚨 Сохраняй регистр task-id из task-plan в id шагов.** Если задача в `task-plan.json` —
> `T1`, то шаги должны быть `04-test-T1`, `04-build-T1`, `07-deliver-T1` (а не `...-t1`).
> Иначе гейты (`check_delivery.py` и др.) не сопоставят шаг с задачей. Деттерминированные
> гейты сопоставляют суффикс регистронезависимо как страховку, но не полагайся на это —
> пиши id ровно как task-id.

Обнови `02-design` только при `pass` execution-gates, передав артефакты:

```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> \
    --step-id 02-design --status completed \
    --artifacts '{
        "tech-design": "docs/feature-pipeline/<slug>/tech-design.md",
        "task-plan": "docs/feature-pipeline/<slug>/task-plan.json"
    }'
```
