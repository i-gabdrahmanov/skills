# Forge (gigacode3) — источник правды

> 🧭 **Навигация:** пользователю (как запускать фичи) → [`GUIDE.md`](GUIDE.md);
> агенту-владельцу обвязки (развернуть/проверить/развивать, прод GigaCode / DeepSeek v4) →
> [`AGENT-RUNBOOK.md`](AGENT-RUNBOOK.md). Этот файл (FORGE.md) — архитектура и «почему так».

> Этот файл — **самодостаточная** документация харнеса: архитектура, решения, разбор прогонов,
> роадмап. Он версионируется и деплоится вместе с `hooks/` и `skills/`, поэтому работает **независимо
> от чьей-либо личной памяти ассистента**. Любой оператор/агент должен опираться на ЭТОТ файл,
> а не на внешние заметки. При изменении харнеса — обновляй ЗДЕСЬ.

## Что это

`~/.gigacode3/` — source-of-truth e2e-обвязки для реализации фич в Java/Spring через GigaCode.
Принцип (PDLC v3.5): **Forge > model; hooks = enforcement, SKILL.md = guidance**. Деплой —
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
- **Выбор критичности фичи форсится** — после BRD SKILL спрашивает критичность (low/medium/high →
  `autonomy.auto_max_risk` R2/R1/R0 в pipeline.json); `gate-guard` блокирует любое R2+ действие, пока
  `autonomy.criticality` не задана. На прошлых прогонах выбор пропускался — теперь нельзя.
- **Evidence bundle** перед доставкой (completeness ≥ `evidence.threshold`).
- **Pipeline-state намеспейсится ПО ФИЧЕ**: `ground/statements/feature-pipeline/<feature>/manifest.json`
  (был один `pipeline/` на все фичи → вытесняли друг друга). Фичи сосуществуют, резюм точечный.
  `--feature <slug>` во всех вызовах init/read/update/add_steps/build_evidence (дефолт `pipeline` —
  совместимость для system-analyst/minor-defect-fix); `read.py --list` — все фичи в работе; хуки
  (`gate-guard`/`phase-gate` через `risk_ladder.active_manifest`) берут АКТИВНУЮ = самый свежий манифест;
  `cost-breaker`/`log-agent` глобят `statements/*/*/manifest.json` (кроме archived).
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

- [x] Ранний вопрос о критичности фичи после BRD → `autonomy.auto_max_risk`; форсится gate-guard'ом.
- [x] Тест-стратегия: `tdd-guard` блокирует `@DataJpaTest`/`@SpringBootTest` при `quality.test_layer=
      service-unit` (падали initializationError); escape-hatch `test_layer=mixed`. + промпт тестописателя.
- [x] Pre-flight self-check харнеса в §0.0 (`preflight.py`: doctor + firing-evidence) — ловит
      «0 hook entries» (кейс pprb-kid) ДО старта; exit 1 → «ENFORCEMENT OFF, остановись».
- [x] Политика отказа/эскалации + гигиена контекста + probe субагентов — секция «Устойчивость» в
      feature-pipeline/SKILL.md (лимит 3, failed+спросить, не force-push, субагенты для тяжёлого, excerpts).
- [x] Аудит исходников Qwen: фикс `additionalContext`→hookSpecificOutput, context-injector без agent_type,
      SoD помечен неактивным, fail-open задокументирован, флаг `--experimental-hooks` (форк) — везде в командах.
- [ ] (опц.) Устойчивость к обрывам стрима глубже: точечный per-file TDD-маппинг, авто-resume.

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
  **Форсится хуком** `tdd-guard`: запись в `src/main` блокируется, пока `04-test-<id>` ещё `pending`
  (на прогоне TDD не происходил — код писали первым; теперь нельзя). Тесты писать можно всегда.
- Лог поведения агентов+субагентов для анализа: `log-agent.py` пишет per-run (`ground/ai-logs/<run>/`)
  И в единый архив `<home>/ai-logs-archive/agents-YYYYMM.jsonl` (кросс-проект/кросс-прогон, поля
  project/cwd, промпты+финалы, TRUNC=4000 env `GIGACODE_LOG_TRUNC`). Анализ: `agentops.py --archive <dir>`.
  Архив-каталог переопределяется env `GIGACODE_AILOG_ARCHIVE`.

## Установка/дистрибуция

- `install.sh [HOME]` — пользовательская установка «всё сразу», канал-агностично (берёт исходник из
  своей папки → работает из git clone / архива / общего каталога). Проверяет пред-условия (python3, CLI),
  зовёт `deploy.sh`, печатает next-steps (запуск с `--experimental-hooks`, preflight, запуск фичи).
  `deploy.sh` теперь копирует в дом и `GUIDE.md`/`AGENT-RUNBOOK.md`/`FORGE.md`/`smoke-cli.sh`.
- Гранулярность: пока «всё сразу» (по решению). Поштучная установка с резолвом зависимостей —
  возможное расширение (нужен манифест `requires:` во frontmatter; пайплайн-связка тянет хуки).
  Канал (Bitbucket-репо vs архив) — не зафиксирован; `install.sh` от канала не зависит.

## Обслуживание

- **`cleanup-jira-artifacts`** — скилл-уборщик: по Jira-ключу/slug удаляет ЛОКАЛЬНЫЕ артефакты фичи
  (pipeline-state `statements/<feature>/`, `docs/feature-pipeline/<slug>/`, `brd-grounding/<slug>.md`,
  `ai-logs/<feature>/`, подтверждённые `evidence/<taskId>.json`). НЕ трогает grounding (`system-analysis`),
  `ai-logs-archive`, git, Jira. Dry-run по умолчанию → подтверждение → `--apply`. Консервативный матчинг:
  общий `evidence/<taskId>` удаляется только если пакет ссылается на фичу, иначе → «неопределённые» (не трогает).

## Известные ограничения и факты по хукам (из аудита исходников Qwen)

- **`additionalContext` только в `hookSpecificOutput`** — рантайм читает контекст-инъекцию ТОЛЬКО из
  `hookSpecificOutput.additionalContext` (core/hooks/types.ts:343). Все наши хуки исправлены под это.
- **Subagent `agent_type` = `general-purpose`** для всех наших субагентов (мы так дёргаем `agent`).
  Поэтому `context-injector` НЕ зависит от типа (инъектит по наличию файлов), а **separation-of-duties
  через `agent_caps` сейчас НЕАКТИВНО** (заработает только с кастомными subagent_type). Не считать его рабочим.
- **Гейт-хуки fail-OPEN при таймауте/краше** (hookEventHandler: блок при сбое только для Todo-событий;
  команд-хук >60с убивается → действие проходит). Поэтому тяжёлые гейты (`check_taskplan`/`check_delivery`/
  coverage) запускает ОРКЕСТРАТОР как execution-gate (так и есть в SKILL), а хуки лёгкие (file-reads) —
  страховка. Не клади тяжёлые subprocess в hook hot-path.
- **Command substitution `$(...)`/backticks РЕЖЕТСЯ** в shell-вызовах агента → в SKILL.md/доках/инструкциях
  её НЕТ (каталог `.`, путь к репо скрипты берут сами через `repo_root()`). Внутри `.sh` — можно.
- **Блокировки (exit 2 + stderr) работают надёжно**; Subagent-события срабатывают для тула `agent`.
- **Хуки за флагом `--experimental-hooks` (форк GigaCode) — КОРЕНЬ pprb-kid `0 hook entries`.** Запускать
  рантайм ВСЕГДА: `gigacode --experimental-hooks -p "..."`. Это флаг ЗАПУСКА бинаря, не ключ settings —
  `deploy.sh`/`doctor` его не ставят. В апстриме Qwen флага нет (хуки on). Все команды запуска (smoke-cli,
  DEPLOY) уже с флагом; `preflight`/`doctor` явно напоминают; `preflight` ловит отсутствие по firing.

> История — это git-история этого репозитория. Хочешь полноценный аудит изменений — `git init` здесь
> и коммить по фичам; тогда «журнал решений» дополняется коммит-сообщениями.
