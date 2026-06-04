# GigaCode Harness (gigacode3) — источник правды

> Этот файл — **самодостаточная** документация харнеса: архитектура, решения, разбор прогонов,
> роадмап. Он версионируется и деплоится вместе с `hooks/` и `skills/`, поэтому работает **независимо
> от чьей-либо личной памяти ассистента**. Любой оператор/агент должен опираться на ЭТОТ файл,
> а не на внешние заметки. При изменении харнеса — обновляй ЗДЕСЬ.

## Что это

`~/.gigacode3/` — source-of-truth e2e-обвязки для реализации фич в Java/Spring через GigaCode.
Принцип (PDLC v3.5): **harness > model; hooks = enforcement, SKILL.md = guidance**. Деплой —
`deploy.sh` в конфиг-дом рантайма (прод `~/.gigacode/`, локальный тест `~/.qwen/`).

- `hooks/` — control-plane (см. `hooks/DEPLOY.md` — полный ростер, порядок, диагностика).
- `skills/` — пайплайн-скиллы (оркестратор `feature-pipeline` + фазовые).
- `deploy.sh` — развёртывание одной командой (co-location hooks+skills, мерж hooks-блока).
- `hooks/doctor.py` — диагностика готовности ДО прогона.

## Архитектура (фазы feature-pipeline)

`идея/Jira → BRD → grounding → tech-design → Jira → build → verify → document → deliver`.
Гейты: точки подтверждения пользователем + детерминированные execution-gate'ы (Python) на каждую фазу.
Состояние — `pipeline-state` (manifest), резюмируемо. Подробности — `skills/feature-pipeline/SKILL.md`.

## Журнал решений (почему так)

- **Enforcement в рантайме, не в тексте.** SKILL.md модель может проигнорировать → гейты/политики
  форсятся хуками (gate-guard/risk-ladder, evidence-enforcer, cost-breaker, phase-gate, security).
- **Risk ladder R0–R5, deny-first** (`hooks/risk-policy.json`) — policy-as-code, рисковое fail-closed.
- **Evidence bundle** перед доставкой (completeness ≥ `evidence.threshold`).
- **Grounding не повторять** — `check_grounding.py` (детектор в нескольких местах) → reuse молча;
  свежесть между фичами держит `enrich_grounding.py` (инкрементально по изменённым модулям, без полного рескана).
- **BRD на языке бизнеса** — никаких классов/сущностей/методов/SQL в BRD; код-факты идут в tech-design;
  grounding-выжимка BRD живёт в `ground/brd-grounding/`, не рядом с самим BRD.
- **Кастомные слэш-команды (`commands/`) удалены** (2026-06-04). Причина: дублировали скиллы (точка входа
  и так есть — триггер по описанию или `/skills feature-pipeline`), не деплоились (`deploy.sh` копирует
  только hooks+skills), а их дизайн `!{cat all skills}` вливал тела всех скиллов в один контекст на старте
  → раздувание контекста → обрывы стрима. Точка входа = скилл, не команда.

## Разбор прогонов

### Прогон pprb-kid (2026-06-04) — провальный, уроки
Корневые причины (из debug-лога рантайма):
- 🔴 `[HOOK_REGISTRY] 0 hook entries` — **хуки не были развёрнуты** (залили только скиллы, hooks-блок
  в settings.json не влили). Весь control-plane молчал → «не спросил критичность», «нестабильно».
- 🔴 co-location нарушена: skills на проектном уровне, user `~/.gigacode/skills` пуст → гейты не нашли бы `../skills`.
- 🟠 субагенты не использовались — **НЕ из-за рантайма** (general-purpose субагенты в GigaCode
  стартуют нормально; `agent models: []` в логе — про другой каталог, не блокер). Реальная причина:
  фазы-субагенты в SKILL.md были описаны прозой («тестописатель пишет тесты») без ЯВНОГО вызова тула
  `agent` → модель делала работу inline. Фикс: явные `agent(subagent_type=..., prompt=...)` в фазах
  Verify/Document + правило «субагент = вызов тула, не сделай сам». **Прошлись по ВСЕМУ пайплайну:**
  явный диспатч добавлен в `feature-pipeline` (Verify/Document), `system-analyst` (structure-mapper +
  все мапперы этапа 2) и `minor-defect-fix` (тестописатель/тестраннер/спецадаптер). Все fenced-блоки
  `subagent_type: general-purpose` теперь явно = аргументы вызова тула `agent`.
- 🟠 рантайм: `Invalid stream [NO_FINISH_REASON]` ретраи — нестабильность стрима МОДЕЛИ; усугублялась
  тем, что всё шло в одном контексте (см. выше). Харнесом смягчается субагентами + чекпойнтами.
- 🟠 грундинг искался узко; BRD был с код-деталями.
**Вывод:** всегда деплоить через `deploy.sh` и проверять `doctor.py` ДО прогона — это убирает класс
ошибок «0 hook entries / skills не рядом».

## Роадмап (что дальше)

- [ ] Ранний вопрос о критичности фичи после BRD → задаёт `autonomy.auto_max_risk` и жёсткость гейтов.
- [ ] Тест-стратегия: НЕ писать `@DataJpaTest` репозиторные тесты (падают `initializationError` в
      multimodule); предпочитать сервисные unit-тесты с моками.
- [ ] Устойчивость к обрывам стрима: мельче шаги + чекпойнт каждого в pipeline-state (resume).

## Сделано (changelog кратко)

- Control-plane хуки + risk ladder + evidence + cost + security + evals (18/18).
- Инкрементальное обогащение grounding (`enrich_grounding.py`) вместо полного рескана.
- `deploy.sh` + `doctor.py` (фикс «0 hook entries» / co-location).
- `validate_skills.py` — валидатор frontmatter (name/description) всех скиллов; встроен в `doctor.py`,
  ловит «мёртвые» скиллы (без шапки → рантайм молча скипает, как 7 командных на pprb-kid). Наши 21 — валидны.
- `smoke-cli.sh [HOME] [--live]` — runtime-контракт: проверяет, что пайплайн СТАРТУЕТ на CLI+модели по
  команде. Статика (CLI/версия/doctor/скиллы/evals) — всегда; `--live` — хуки реально срабатывают,
  субагент стартует, gate блокирует (graceful SKIP, если нет ключа модели). Три слоя тестов:
  eval (логика) + doctor (статика) + smoke-cli (runtime).
- `check_grounding.py` (не повторять грундинг).
- BRD на языке бизнеса; grounding-выжимка → `ground/brd-grounding/`.
- Ключ Jira в шапке `brd.md`/`tech-design.md`/`sdd.md` (первой строкой `**Jira:**`, если задача из Jira;
  протягивается feature-pipeline → BRD → tech-design).
- tech-design проектирует по grounding (`grounding-excerpt.json`/`system-analysis`), НЕ по коду.
- **TDD по умолчанию** (`quality.tdd:true`): per-task RED→GREEN. Тесты вперёд (service-unit+моки, валидные
  данные, избегать @DataJpaTest), затем стабы сигнатур → `check_tests_red.py` (компилируется+падает) →
  минимальная реализация до зелёного → `check_build` → coverage. Шаги манифеста `04-test-<id>`→`04-build-<id>`.
- Лог поведения агентов+субагентов для анализа: `log-agent.py` пишет per-run (`ground/ai-logs/<run>/`)
  И в единый архив `<home>/ai-logs-archive/agents-YYYYMM.jsonl` (кросс-проект/кросс-прогон, поля
  project/cwd, промпты+финалы, TRUNC=4000 env `GIGACODE_LOG_TRUNC`). Анализ: `agentops.py --archive <dir>`.
  Архив-каталог переопределяется env `GIGACODE_AILOG_ARCHIVE`.

> История — это git-история этого репозитория. Хочешь полноценный аудит изменений — `git init` здесь
> и коммить по фичам; тогда «журнал решений» дополняется коммит-сообщениями.
