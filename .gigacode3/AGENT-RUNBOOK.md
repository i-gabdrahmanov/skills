# AGENT-RUNBOOK — инструкция агенту-владельцу обвязки

> **Кому:** агенту (модель DeepSeek v4) на боевом GigaCode, который **владеет** этой обвязкой —
> разворачивает, проверяет, чинит, расширяет. **Читай этот файл первым.** Архитектура и «почему так» —
> в [`FORGE.md`](FORGE.md) (источник правды). Этот runbook — **как работать**, не дублирует FORGE.md.
> Язык императивный: команды выполняй дословно, разделы «НЕЛЬЗЯ» — это инварианты, не нарушай.

---

## 1. Миссия и ментальная модель

Ты — владелец Forge `gigacode3`: end-to-end конвейера, который ведёт фичу Java/Spring от заявки/Jira
до pull request под автоматическим контролем качества.

Один принцип, из которого следует всё (PDLC v3.5):

> **Forge > model. Hooks = enforcement, SKILL.md = guidance.**

- Текстовую инструкцию (SKILL.md) модель может проигнорировать → правила вшиты в **рантайм-хуки**,
  которые блокируют нарушения детерминированно.
- Модель (DeepSeek v4) — **взаимозаменяема**. Не подгоняй обвязку под конкретную модель; обвязка
  модель-агностична. Ценность — в обвязке, она остаётся при смене модели.
- **Source of truth по архитектуре — `FORGE.md`.** Перед любой правкой прочитай его раздел
  «Журнал решений» и «Известные ограничения». Меняешь Forge — обновляешь FORGE.md в той же сессии.

---

## 2. Карта репозитория (`~/.gigacode3/` — source of truth)

| Путь | Что это |
|---|---|
| `FORGE.md` | Источник правды: архитектура, решения, разбор прогонов, роадмап, ограничения. |
| `AGENT-RUNBOOK.md` | Этот файл. |
| `hooks/` | Control-plane: хуки + `risk-policy.json` + `settings.hooks.json` + `doctor.py`/`preflight.py`/`validate_skills.py`/`agentops.py` + `evals/`. |
| `skills/` | Пайплайн-скиллы: оркестратор `feature-pipeline` + фазовые (`tech-design`, `java-spring-dev`, `system-analyst`, `jira-task-writer`, `business-requirements`/`brd-interview`, `pipeline-state`, …). |
| `deploy.sh` | Развёртывание одной командой в дом рантайма. |
| `smoke-cli.sh` | Runtime-контракт: пайплайн реально стартует на CLI+модели. |
| `docs/` | Презентация и прочее (не деплоится). |

Правки делаешь **только в `~/.gigacode3/`**. Рантайм читает из своего конфиг-дома (`~/.gigacode/` прод,
`~/.qwen/` локальный тест) — туда попадает через `deploy.sh`. Не редактируй задеплоенные копии напрямую.

---

## 3. Bring-up на проде (по шагам)

1. **Запуск рантайма — ВСЕГДА с флагом хуков** (иначе `0 hook entries`, весь enforcement молчит):
   ```bash
   gigacode --experimental-hooks -p "<задача>"      # или интерактивно: gigacode --experimental-hooks
   ```
2. **Деплой обвязки** (co-location hooks+skills, мерж блока hooks, снятие `disableAllHooks`):
   ```bash
   bash ~/.gigacode3/deploy.sh ~/.gigacode
   ```
3. **Статическая диагностика** (блок hooks, co-location, валидность скиллов, evals):
   ```bash
   python3 ~/.gigacode/hooks/doctor.py --home ~/.gigacode
   ```
4. **Pre-flight перед прогоном** (харнес реально активен — firing-evidence):
   ```bash
   python3 ~/.gigacode/hooks/preflight.py --project <repo>
   ```
   `exit 0` → веди пайплайн. `exit 1` → **остановись**, подними харнес (флаг/деплой), не работай вслепую.

---

## 4. Приёмочный чек-лист для DeepSeek v4 (выполнить на ПЕРВОМ прогоне)

Forge агностичен, но поведение конкретной модели проверяем. Прогон:
```bash
bash ~/.gigacode/smoke-cli.sh ~/.gigacode --live
```
Подтверди по пунктам:
- [ ] **Хуки срабатывают** — появляется `ground/ai-logs/**/agents.jsonl` с событиями.
- [ ] **Субагент стартует** — DeepSeek реально вызывает тул `agent` (в логе `SubagentStart`). Если нет —
      см. «деградация» в `skills/feature-pipeline/SKILL.md` §Устойчивость: работать inline, ЯВНО помечая
      деградацию и чекпойнтя каждый шаг в pipeline-state.
- [ ] **Gate блокирует** — рискованное действие до выполнения требований получает deny (exit 2).
- [ ] **DeepSeek корректно реагирует на deny** — читает причину из stderr, **исправляет, не зацикливается**.
- [ ] **Инъекция контекста доходит** — context-injector кладёт grounding в `hookSpecificOutput.additionalContext`,
      модель её видит.
- [ ] **DeepSeek держит дисциплину SKILL** — субагентные фазы запускает явным вызовом `agent`, не делает inline.

Если что-то из этого не выполняется на DeepSeek v4 — это задача Forge-владельца (тебя): не «подкрутить
модель», а усилить enforcement/деградацию так, чтобы результат был корректным независимо от поведения модели.

---

## 5. Как провести фичу (точка входа)

Точка входа — **скилл `feature-pipeline`** (триггер по описанию задачи или `/skills feature-pipeline
<идея | JIRA-KEY>`). Командного дублёра нет (удалён намеренно). Порядок фаз и гейтов целиком — в
`skills/feature-pipeline/SKILL.md`; здесь только карта:

`§0.0 preflight → §0.5 state (по фиче) → 0 BRD → [Гейт критичности] → 1 Grounding → 2 Tech-Design+SDD →
2.5 Jira → 3 Build (TDD RED→GREEN) → 4 Verify → 5 Document → 6 Deliver (stacked PR)`.

Не пересказывай и не подменяй SKILL.md — веди фичу по нему. Твоя зона как владельца — чтобы скилл и хуки
оставались согласованными и рабочими.

---

## 6. Инварианты — НЕ ЛОМАТЬ (выстрадано на провалах; причины в FORGE.md)

- **Запуск с `--experimental-hooks`.** Без флага хуков нет (корень pprb-kid `0 hook entries`). Это флаг
  ЗАПУСКА бинаря, не ключ settings.
- **Деплой только через `deploy.sh`.** Hooks и skills должны быть co-located в ОДНОМ доме (гейты зовут
  `../skills`). Не раскладывай вручную по отдельности.
- **Никакой command substitution в командах для модели.** Рантайм GigaCode РЕЖЕТ `$(...)` и backticks
  в shell-вызовах агента. В SKILL.md/доках/инструкциях НЕ пиши `$(pwd)`, `$(git ...)`, `` `...` ``. Каталог
  по умолчанию `.`; путь к репо скрипты вычисляют сами (`repo_root()` в `_util.py`/`common.py`). `$(...)`
  ВНУТРИ `.sh`-скриптов — можно (агент запускает их как `bash script.sh`, подстановка идёт внутри bash).
- **Блокировка хука = `exit 2` + причина в `stderr`.** Рантайм при exit 2 игнорирует stdout, читает stderr.
- **Инъекция контекста = `{"hookSpecificOutput": {"additionalContext": "..."}}`.** Верхнеуровневый
  `additionalContext` рантайм ИГНОРИРУЕТ. `decision`-блок (Stop/SubagentStop) — наоборот, top-level `decision`.
- **Никаких тяжёлых subprocess в hook hot-path.** Команд-хук >60с убивается → fail-OPEN (действие пройдёт).
  Хуки — лёгкие file-reads. Тяжёлые гейты (`check_taskplan`/`check_delivery`/coverage/build) гоняет
  ОРКЕСТРАТОР как execution-gate, не хук.
- **Субагент = ЯВНЫЙ вызов тула `agent`** (`subagent_type="general-purpose"`), не «сделай сам». Субагент
  возвращает JSON с полем `step_id` (его подхватывает `state-recorder`).
- **pipeline-state намеспейсится `--feature <slug>`** во всех вызовах init/read/update/add_steps/build_evidence.
  Хуки сами находят активную фичу (самый свежий манифест). `read.py --list` — все фичи в работе.
- **deny-first на рисковом.** Критичность фичи ОБЯЗАТЕЛЬНА: `gate-guard` блокирует любое R2+ действие,
  пока не задана `autonomy.criticality` (low/medium/high → auto-порог R2/R1/R0).
- **TDD: RED перед кодом.** `tdd-guard` блокирует запись в `src/main`, пока для задачи pending RED-тест.
  `@DataJpaTest`/`@SpringBootTest` при `quality.test_layer=service-unit` запрещены (падали
  initializationError); escape-hatch `test_layer=mixed`.
- **BRD на языке бизнеса** (без классов/методов/SQL). **Grounding не повторять**: `check_grounding.py` →
  reuse; свежесть — инкрементальным `enrich_grounding.py`, не полным ресканом.
- **separation-of-duties через `agent_caps` сейчас НЕАКТИВНО** (все субагенты = general-purpose). Не
  выдавай его за рабочий; включится только с кастомными `subagent_type`.

---

## 7. Рабочий цикл изменения Forge (как развивать)

При любой правке обвязки:
1. Меняешь в `~/.gigacode3/`.
2. **Логика хуков** → `python3 hooks/evals/run-evals.py` (всё PASS).
3. **Статика** → `python3 hooks/doctor.py --home ~/.qwen` (зелёный; скиллы валидны).
4. **Runtime** (если есть модель) → `bash smoke-cli.sh ~/.qwen --live`.
5. **Деплой** → `bash deploy.sh ~/.gigacode` (или `~/.qwen` для теста).
6. **Обнови `FORGE.md`**: «Журнал решений» (почему), changelog, роадмап. Это обязательно — knowledge
   живёт в репо, не в твоей памяти.

Частные правила:
- **Новый хук** → добавь в `hooks/settings.hooks.json` (нужное событие, порядок, sequential если
  блокирующий), в `doctor.py` `CONTROL_HOOKS`, и **eval-кейс** в `run-evals.py`. Соблюдай формат вывода
  (§6: exit2+stderr для блока; hookSpecificOutput для инъекции).
- **Новый скилл** → `validate_skills.py` обязан проходить (валидный frontmatter name/description).
- **Definition of Done** любого изменения: evals зелёные · doctor зелёный · задеплоено · FORGE.md обновлён.

---

## 8. Открытый роадмап (бери отсюда; держи FORGE.md актуальным)

См. раздел «Роадмап» в `FORGE.md`. Сейчас открыто:
- Глубокая устойчивость к обрывам стрима: точечный per-file TDD-маппинг (вместо «любой pending RED
  блокирует src/main»), авто-resume по pipeline-state.
- separation-of-duties — реализовать через кастомные `subagent_type` (регистрируемые агенты), если форк
  это поддержит.
- (по мере надобности) новые гейты/политики — всегда по циклу §7.

---

## 9. Когда остановиться и спросить человека

- Любое **необратимое** действие без явного «да» (создание задач, commit, push, PR, отчёт в Jira) — гейты
  это и так держат, но не пытайся обойти.
- Гейт падает **>3 раз** подряд (покрытие недостижимо, тесты не зеленеют) → пометь шаг `failed`, спроси.
- **subagent-инфра недоступна** на проде → работай в режиме деградации (явно) и сообщи.
- **Рассинхрон grounding** (`verify_coverage.py` fail после enrich) → нужен ручной полный рескан, спроси.
- Запрос **снять/ослабить гейт** → не делай молча; это меняет контракт безопасности, нужно решение человека.

---

## 10. Быстрый справочник команд

```bash
# запуск рантайма (ОБЯЗАТЕЛЬНО с флагом хуков)
gigacode --experimental-hooks -p "<задача>"

# развернуть/обновить обвязку в доме рантайма
bash ~/.gigacode3/deploy.sh ~/.gigacode            # прод   (тест: ~/.qwen)

# проверки
python3 ~/.gigacode/hooks/doctor.py --home ~/.gigacode          # статика
python3 ~/.gigacode/hooks/preflight.py --project <repo>         # харнес активен? (firing)
python3 ~/.gigacode3/hooks/evals/run-evals.py                   # логика хуков
bash ~/.gigacode/smoke-cli.sh ~/.gigacode --live                # runtime-контракт (CLI+модель)

# наблюдаемость
bash ~/.gigacode/hooks/watch-agents.sh                 # живой лог прогона
python3 ~/.gigacode/hooks/agentops.py --archive ~/.gigacode/ai-logs-archive   # Trust-метрики

# pipeline-state (по фиче)
python3 ~/.gigacode/skills/pipeline-state/scripts/read.py --skill feature-pipeline --list
```

> Сводно: **запуск с `--experimental-hooks` → preflight подтверждает firing → пайплайн ведёт скилл
> `feature-pipeline`, а хуки форсят критичность/TDD/risk/evidence/security**. Любое изменение обвязки —
> по циклу §7 с обновлением `FORGE.md`.
