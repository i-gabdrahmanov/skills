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
выжимку из grounding-excerpt.json, содержащую только релевантные entities, **components**
(service/repository/mapper/dto/controller), API-endpoints, Kafka-топики и таблицы БД для
затронутых модулей. Это снижает размер контекста с ~2840 до 50-200 строк и предотвращает
проектирование дублирующих сущностей и ссылки на несуществующие классы.

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/prepare_design_context.py \
    --sdd "<папка фичи>/sdd.md" \
    --project "<project>" \
    --out "<папка фичи>/design-context.json"
```

Скрипт сузит контекст по модулям, упомянутым в `sdd.md` (BRD выключен — `brd.md` нет, а
`task-plan.json` ещё не создан на этой фазе, поэтому сужаем по SDD). Если SDD не дал модулей —
будет включено всё (без потери), что безопасно для pre-design.

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
- ADR (только если adr.enabled): каталог <master_base>/adr/ — принятые арх-решения как ОГРАНИЧЕНИЕ входа

Шаг 1: Проектируй ПО sdd.md и design-context. К grounding-excerpt.json обращайся
        только если design-context не содержит нужной информации. BRD — лишь справка.
Шаг 2: Создай ДВА файла в <папка фичи>/ (sdd.md уже написан на фазе 02-sdd — НЕ трогай его):
  1. tech-design.md — по шаблону `<project>/.gigacode/skills/tech-design/references/design-template.md`
  2. task-plan.json — по шаблону `<project>/.gigacode/skills/tech-design/references/task-plan-schema.md`
     Каждая задача: непустой acceptance (Given-When-Then) + sdd_ref на раздел sdd.md.
  ADR (только если adr.enabled в pipeline.json): СНАЧАЛА прочитай существующие ADR в <master_base>/adr/
     — accepted = ограничения дизайна, не противоречь молча; релевантные процитируй в §8 как ADR-NNNN.
     Значимые НОВЫЕ арх-решения оформи ADR-файлами в <master_base>/adr/NNNN-<slug>.md по adr-template.md,
     ссылки ADR-NNNN — в §8 tech-design и §11 sdd. Конфликт с accepted ADR — через супер-седес, не игнором.
     Мелкие развилки — инлайн, не плоди ADR.

Gate (обязательно, перед завершением):
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py design <slug> --project-root <project>
  Должен быть PASS (check_taskplan + check_sdd-линковка; при adr.enabled — ещё check_adr: состав ADR
  + резолв ссылок ADR-NNNN). Сохраняет вердикт design-judge в журнал прогона (events.jsonl).

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

После «да»: заведи шаги фазы Build **одной командой** — список выводится из
`task-plan.json`, руками его не пиши:

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/add_steps.py \
    --project-root <project> --skill feature-pipeline --feature <slug> --from-task-plan
```

Скрипт сам решает, что заводить, по конфигу прогона:
- `02-eval-plan` — при `quality.eval_enabled`;
- `04-test-<taskId>` — при `quality.tdd`, если задача пишет код и не освобождена от тестов
  (`quality.no_test_layers`: миграции/DTO/энтити RED не требуют);
- `04-build-<taskId>` — всегда, с зависимостью **только на реально заведённые** шаги;
- `05-tests` довязывается ко всем `04-build-*` (на `init.py` их id ещё не существовали).

Регистр `task-id` сохраняется из плана автоматически, поэтому прежнее требование «пиши
`04-test-T1`, а не `...-t1`» больше не на тебе.

> **Про `05-tests`.** В манифест на `init.py` он заводится с ПУСТЫМ `depends_on`: id
> build-шагов тогда ещё не существуют, вписать их физически нечем. Эта команда довязывает их
> сама. До её запуска `05-tests` формально «готов» и виден в `next_runnable` — не запускай
> полный прогон тестов, пока шаги задач не заведены.

**Используй именно версию из `feature-pipeline/scripts/`** — она знает маску
`required_judges` фазовых шагов. Идемпотентно; манифест руками не правь. Фазовую машину
синхронизировать не нужно: состояние выводится из манифеста, новые шаги видны ей сразу.

> Ручной `--steps` остался для нештатных случаев. Зависимость на несуществующий шаг он
> теперь отбивает с exit 2: такой шаг не станет готовым никогда, и раньше пайплайн от этого
> вставал молча (`next_runnable` пуст, причина ниоткуда не видна).

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
