# Пайплайн forge — план улучшений (качество кода + стабильность)

Исследование от 2026-06-20. Правим поэтапно, по приоритету. Легенда статуса находки:
📌 — подтверждено кодом · ❓ — гипотеза, требует проверки перед правкой.

Отмечай чекбокс по мере выполнения. Каждая задача содержит: где (файл:строка), в чём
проблема, предлагаемый фикс.

---

## P0 — «проверенный» код на самом деле не проверен

- [x] **P0-1. 📌 Coverage-гейт fail-open при отсутствии JaCoCo. ✔ СДЕЛАНО (2026-06-20)**
  - Где: `skills/minor-defect-fix/scripts/check_coverage.py:109-114` (`status: "skipped"` → `return 0`);
    `hooks/eval-guard.py:63,169` (любой `returncode == 0` = `passed`). Дефолт `jacoco_configured: false`.
  - Проблема: на проекте без JaCoCo coverage-eval молча проходит — гейт покрытия фактически
    отключён, сигнала нет. То же для `EMPTY` (`check_coverage.py:126-128`): 0 измеренных строк не фейлит.
  - Сделано: `check_coverage.py` теперь fail-closed по умолчанию (`--strict`): нет JaCoCo-отчёта →
    FAIL (exit 2, `status: missing_report`). Escape-хэтч `--lenient` восстанавливает старый skip=pass
    (только осознанно). Явный `--strict` проставлен в `run_judge.py` (coverage-judge) и в команде,
    которую генерит `build_evals_from_design.py`. Доки обновлены (`config.md`, `coverage.md`). Тест:
    `skills/minor-defect-fix/scripts/test_check_coverage.py` (10 кейсов, зелёные).
  - Решение по `EMPTY` (0 строк): оставлено как pass — это marker-interface/Lombok/package-info,
    где у JaCoCo нет инструментируемых строк (см. `coverage.md:59-61`). Хард-фейл дал бы ложные
    срабатывания на легитимных интерфейсах/DTO. Случай «файл изменён, но не в отчёте» (`MISSING`)
    и так фейлит.
  - Осталось: ~~preflight «JaCoCo подключён, если coverage-гейт включён»~~ → закрыто в **P3-15**
    (`config.py validate` делает эту кросс-проверку; `--strict` для preflight-гейта).

- [x] **P0-2. 📌 `test_pass` eval — ложный сигнал и дорогой. ✔ СДЕЛАНО (2026-06-20)**
  - Где: `skills/feature-pipeline/scripts/build_evals_from_design.py`.
  - Проблема: команда `./gradlew test` (вся сюита), хотя описание «Тесты задачи {tid}». `threshold: 0.95`
    бессмыслен — gradle бинарный (exit 0/1), eval-guard смотрит только код возврата. Плюс
    `run_judge.py:353-372` валидировал этот фиктивный порог как ≥0.8.
  - Сделано (вариант A — per-task регресс-чекпоинт): test_pass переведён в честный бинарный gate
    (`threshold: 0`, `binary: true`), описание «Вся тест-сюита зелёная после задачи N (регрессия)».
    Валидация порога в `run_judge.py` заменена на проверку наличия команды. Скоуп через `--tests`
    отвергнут: мис-паттерн даёт exit 0 (0 тестов = ложный pass). Тесты: `test_build_evals.py` (19, зелёные).
  - **Бонус — Maven (вшит сюда):** генератор больше не хардкодит gradle. Команды compile/test берутся
    из pipeline.json (`project.build_system`, `quality.test_command`), поэтому eval-plan корректен и на
    Maven (`mvn -q compile` / `mvn -q test ...`). Раньше на Maven генерились `./gradlew ...` → падали →
    eval-guard блокировал `src/main` → пайплайн вставал. Stale-пример test_pass в `config.md` исправлен
    (закрыта часть P3-13).

- [x] **P1-16. 📌 Остаток Maven-хардкода в исполнении. ✔ СДЕЛАНО (2026-06-20)**
  - Где: `run_judge.py` RED-judge (`check_red` — gradle-раннер + `--tests`); `check_tests_red.py`
    (gradle-дефолты + `--tests`); `sod-enforcer.py` (роль build/test только по `./gradlew`).
  - Сделано:
    • `run_judge.check_red` — собирает «работы» по `project.build_system` из pipeline.json: Gradle —
      per-module `./gradlew :mod:test --tests glob`; Maven — один `mvn -q test -Dtest=Class1,Class2
      -Dsurefire.failIfNoSpecifiedTests=false`. Сообщение об отсутствии раннера build-system-aware
      (`mvn not found` / `gradlew not found`). Gradle-путь без изменений.
    • `check_tests_red.py` — `_build_system`/`_resolve_compile_test_cmd`/`_resolve_test_cmd`/
      `_apply_test_filter` (Gradle `--tests` / Maven `-Dtest`) + новый `_has_red_tests(output)` —
      build-system-агностичный RED-детект по выводу (ловит и Gradle `BUILD FAILED`, и Maven
      surefire `Failures: N`). **Чинит ранее сломанный `test_check_tests_red.py`** (тест ждал
      `_has_red_tests`, которого не было) + латентный `NameError` в `main()` (печать/return по
      необъявленным локалям `verdict`/`reason`).
    • `sod-enforcer.py` — единый `BUILD_CMD_RE = (?:\./gradlew\s+|\bmvn\b)` в ROLE_POLICY и
      `_detect_command_from_bash`: SoD-кап build/test теперь срабатывает и на `mvn`.
  - Проверка: вся сюита feature-pipeline/scripts **23/23 зелёные** (был единственный красный
    `test_check_tests_red` — закрыт); smoke: `spec`-роль + `mvn test` → BLOCK, `dev` → allow.

- [x] **P0-3. 📌 «Гейт неприменим» ≠ «гейт не смог отработать» — стёрто. ✔ СДЕЛАНО (2026-06-20)**
  - Где: `build_evidence.py` (сырой статус гейта в `gates`), `check_evidence.py` (смотрел только
    completeness), `check_coverage.py` (skip → 0 в `--lenient`).
  - Проблема: degraded-гейт (`skipped`/`missing_report`/`error`) в бандле неотличим от pass; на
    доставке `check_evidence`/`evidence-enforcer` видели только число completeness → тихий пропуск
    проходил как PASS.
  - Сделано: введён третий исход гейта — **degraded** (отработал, но не подтвердил результат), в
    отличие от **absent** (ещё не отрабатывал, напр. delivery до доставки — не долг).
    `build_evidence.py` классифицирует статус (`_gate_outcome`) и пишет `degraded_gates` в бандл.
    `check_evidence.py` — fail-closed по умолчанию: непустой `degraded_gates` валит ворота доставки;
    escape `--degraded-policy warn` / `evidence.degraded_policy` в pipeline.json. `evidence-enforcer.py`
    наследует автоматически (гоняет `check_evidence` с `--pipeline-config`). Доки: `evidence-bundle.md`.
    Тесты: `test_build_evidence.py` (+8 кейсов классификатора и интеграции), `test_check_evidence.py`
    (+4 кейса degraded-политики; попутно починен сломанный `test_completeness_not_numeric`),
    `test_evidence-enforcer.py` (был неисполним — `import` с дефисом; переведён на importlib +
    тесты под реальный stdin-контракт).
  - Осталось: degraded-сигнал из `eval-guard.py` (eval-plan отсутствует при `eval_enabled`) — это
    preflight-концерн, относится к P3-15/P1-8 (валидация конфига/доктор), не к бандлу.

---

## P1 — стабильность пайплайна

- [x] **P1-4. 📌 Два источника истины для фаз (manifest.json ↔ gate.json). ✔ СДЕЛАНО (2026-06-20)**
  - Где: `preflight-validate.py` (`_check_gate_phase` читал статус с диска), `phase_sync.py`
    (своя копия деривации статуса фаз), `pipeline_phases.build_gate` (другая копия — расходились
    по container-шагам).
  - Проблема: ручная синхронизация двух источников + ДВЕ разные реализации «steps → статус фазы»
    (`build_gate` учитывал container-шаги, `phase_sync` их исключал) — класс рассинхронных багов.
    Плюс: если sync молча падал, `_check_gate_phase` решал по устаревшему диску.
  - Сделано: gate.json — производный view, статусы считаются ЕДИНОЙ функцией `build_gate`.
    (1) `pipeline_phases`: публичный `is_container_step` + единая container-aware семантика в
    `build_gate` + `live_phase_decision(manifest)`. (2) `phase_sync.sync_gate_from_manifest`
    больше не держит свой инкрементальный проход — всегда перестраивает через `build_gate`
    (сохраняя мету skip_allowed/артефакты); `_is_container_step` делегирует в `pp`. (3)
    `preflight._check_gate_phase` принимает решение из ЖИВОГО manifest (`live_phase_decision`),
    а не с диска — устаревший/непросинканный gate.json больше не может дать ложную блокировку.
    Попутно починен латентный `NameError` в `_regenerate_gate` (печатал необъявленные
    `phases`/`current` в build_gate-ветке — всплыл бы теперь, раз через неё идёт весь sync).
  - Тесты: `test_phase_derivation.py` (8 новых — container-семантика, live_phase_decision);
    интеграция зелёная: `test_preflight_resync`, `test_state_cycle_golden`, `test_multifeature_gate`,
    `test_phase_consistency`.

- [x] **P1-5. 📌 Тройная копия docs-резолвера. ✔ СДЕЛАНО (2026-06-20)**
  - Где: `skills/feature-pipeline/scripts/skill_paths.py`, `skills/pipeline-state/scripts/_util.py`,
    `hooks/_project.py`. Держится тестом `test_docs_resolver_consistency.py`. `eval-guard.py:123`
    импортирует `_project`.
  - Проблема: разойдутся по кейсу, не покрытому тестом → eval-guard молча ищет eval-plan не там → fail-open.
  - Решение: слить в один импорт НЕЛЬЗЯ — pipeline-state деплоится user-global (`~/.gigacode/skills/
    pipeline-state`, см. config.md), т.е. может быть не co-located с hooks/scripts проекта; три копии —
    осознанный хедж. Поэтому выбран второй вариант из плана: **исчерпывающий property-based тест
    эквивалентности**. `test_docs_resolver_consistency.py` дополнен `TestPropertyBasedConsistency`:
    (а) полный декартов перебор `mode×docs_path×repo_path×feature_subdir` (~2520 комбинаций, вкл.
    traversal/абсолют/не-строки), (б) 3000 псевдослучайных конфигов с фиксированным seed,
    (в) cfg=None/без docs. Любое расхождение `docs_base`/`feature_docs_dir`/`system_analysis_dir`
    между тремя копиями валит сборку В ИСХОДНИКЕ (где все три присутствуют), до деплоя. Сейчас все три
    эквивалентны — тест это фиксирует и ловит будущий дрейф (правка одной копии без других).

- [x] **P1-6. 📌 Хрупкая строковая связность по step-id. ✔ СДЕЛАНО (2026-06-20)**
  - Где: `hooks/eval-guard.py:138` (`04-build-<task>` + `.replace`), `hooks/subagent-enforcer.py:33`
    и `preflight-validate.py:175` (ДВЕ копии набора subagent-фаз), `build_evidence.py:95,97`
    (имена файлов шагов).
  - Проблема: переименуют префикс в одном месте — enforcement отключится молча; набор subagent-фаз
    дублировался в двух хуках/скриптах.
  - Сделано: соглашения об id шагов вынесены в `pipeline_phases` (единый источник):
    `BUILD_STEP_PREFIX`/`TEST_STEP_PREFIX`/`DELIVER_STEP_PREFIX`, `build_task_id()`/`test_task_id()`/
    `deliver_task_id()`, `is_build_step()`, `SUBAGENT_PHASE_PREFIXES` + `requires_subagent()`.
    Потребители переведены на них: `eval-guard` (best-effort импорт pp + inline-fallback, `.replace`
    убран), `subagent-enforcer` (то же), `preflight.check_phase_subagent` (прямой `pp.requires_subagent`),
    `build_evidence` (префиксы из pp). Хуки co-located с feature-pipeline в `.gigacode`, поэтому
    импорт надёжен; fallback пинится тестом. Тест: `test_phase_consistency.py` (+`TestStepIdConventions`,
    5 кейсов — хелперы + что preflight/eval-guard/subagent-enforcer не разошлись с pp). Всего 14 зелёных.
  - **Обновление 2026-06-21:** `eval-guard` переписан в read-only (тяжёлый прогон ушёл в
    execution-gate `run_pending_evals.py`), а `subagent-enforcer` удалён — гарантия «фаза через
    субагента» перенесена на закрытие шага в `update._check_subagent_origin` (PreToolUse-блок
    срабатывал и внутри субагента). Префиксы по-прежнему из `pipeline_phases`; тест-кейс
    переименован в `test_subagent_origin_set_matches_pp`.

- [x] **P1-7. 📌 Идемпотентность необратимых внешних действий. ✔ СДЕЛАНО (2026-06-20)**
  - Проверено (гипотеза ❓ подтверждена): доставка (`stacked-pr-delivery.md`) создавала ветки+push+PR
    БЕЗ проверки «уже есть?» — перезапуск после частичной доставки давал дубль PR (Bitbucket MCP не
    дедуплицирует); `check_delivery.py` — гейт завершённости, не pre-create. Jira-идемпотентность была
    инструкция-only (ledger `jira-tasks-result.json`), без поиска-перед-созданием.
  - Сделано (детерминированно, где возможно):
    • **`delivery_plan.py`** (новый) — идемпотентный resume-aware план доставки ПЕРЕД необратимым
      гейтом. Ключ идемпотентности — имя ветки; сигналы из git (локальные+origin ветки) и manifest
      (`07-deliver-<id>` completed). На задачу: `skip` (доставлено) / `resume` (ветка есть, шаг не
      закрыт — не пересоздавать, проверить существующий PR) / `create`. Offline-устойчив
      (`remote_checked=false`). Тест `test_delivery_plan.py` (18 кейсов).
    • `stacked-pr-delivery.md` — обязательный preflight `delivery_plan.py` перед гейтами 4–5;
      `skip`/`resume` не дублируют ветки/PR.
    • `jira-task-writer/SKILL.md` — маркер-метки `forge:<slug>` + `forge-task:<task_id>` на каждом
      issue + **поиск по метке перед созданием** (JQL) + инкрементальная запись ledger. Делает
      Jira-создание идемпотентным даже при потере ledger до записи.
  - Остаётся LLM-bound: сам вызов Bitbucket/Jira MCP делает модель (не скрипт), поэтому «проверить
    существующий PR перед созданием» — инструкция в доке, опирающаяся на детерминированный план.
    Git-сторона (ветки) — полностью детерминированный ключ.

- [x] **P1-8. 📌 Версия Python не зафиксирована. ✔ СДЕЛАНО (2026-06-20)**
  - Где: комментарий в `preflight-validate.py` упоминал падение на 3.9; в коде `str | None` (PEP604).
  - Сделано: `MIN_PYTHON = (3, 10)` зафиксирован в `doctor.py` (единый источник) + копия в
    `hooks/preflight.py` (пинится `test_doctor`). Новые проверки doctor: `python-version`,
    `git-available`, `config-valid` (переиспускает `config-helper validate --strict` — там же
    кросс-проверка «JaCoCo подключён, если coverage-гейт активен» из P0-1/P3-15, без второй копии).
    Введён новый исход doctor **WARN** (средовой/конфиг-совет) ОТДЕЛЬНО от integrity-FAIL: старый
    Python/нет git/конфиг-проблема не делают doctor «красным», но видны и уходят в preflight как
    warnings (preflight теперь сёрфит и `warnings` doctor, не только `problems`). preflight добавил
    ранний Python-варнинг (до тяжёлых импортов). Тесты: `test_doctor.py` (+5 кейсов). Важное
    наблюдение: текущее dev-окружение — **Python 3.9**, и проверка это ловит (⚠️), что и подтверждает
    нужность пункта.

---

## P2 — рычаги качества produced-кода (кода, который пайплайн производит)

- [x] **P2-9. 📌 Статический анализ — ArchUnit-lite гейт слоёв. ✔ СДЕЛАНО (2026-06-20)**
  - Было: типы eval'ов только `compile/coverage/test_pass` — архитектуру никто не проверял.
  - Сделано: **`check_architecture.py`** — детерминированный ArchUnit-lite БЕЗ запуска Java/ArchUnit
    (статический разбор изменённых `.java`: package + imports + имя класса). Правила:
    `package-root` (пакет под `conventions.package_root`, error), `class-placement` (класс с суффиксом
    слоя в своём пакете, warning), `layer-dependency` (запрет зависимостей: `entity/domain`↛
    `service/controller/repository/mapper/dto`, `repository`↛`service/controller` — error;
    `controller`→`repository` — warning). Консервативен (низкий false-positive): жёстко — только
    универсальные правила, остальное warning; `--strict` ужесточает. Ядро — чистая функция
    (`analyze`/`analyze_file`), тест `test_check_architecture.py` (18 кейсов). Включается
    `quality.architecture_check` (default false; в реестре config-helper + config.md), оркестратор
    гоняет в фазе verify. CLI `--changed`/`--base` (git) / `--json`.
  - Осталось (опц., внешний тулинг целевого проекта): Spotless/Checkstyle, SpotBugs/PMD — требуют
    конфигурации в самом проекте; гейт можно подключить как доп. команду в verify, когда проект их
    настроил. ArchUnit-lite (главный рычаг по плану) самодостаточен и уже работает.

- [x] **P2-10. 📌 Тавтологичные тесты. ✔ СДЕЛАНО (2026-06-20)**
  - Проверено: переход RED→GREEN гарантируется ИСПОЛНЕНИЕМ (`check_tests_red` ловит `assertTrue(true)`,
    т.к. он проходит → RED-гейт FAIL), НО (1) это требует рабочего build (gradlew/mvn часто
    недоступен), (2) `tdd-guard` (всегда-он) смотрит лишь статус шага, не содержимое теста. Значит
    статического флора против тавтологий не было.
  - Сделано: **`check_tautological_tests.py`** — детерминированный статический детектор. Разбирает
    @Test-методы (баланс скобок) и ловит: пустое тело (error), тавтологии `assertTrue(true)`/
    `assertFalse(false)`/`assertEquals(x,x)`/`assert true;` (error), «нет ассерта/verify» (warning,
    с защитой от FP — делегирование в `*assert*/*verify*/*check*`-хелпер не флагается). Работает
    БЕЗ build. Ядро — чистая функция, тест `test_check_tautological_tests.py` (20 кейсов). Опт-ин
    `quality.tautology_check` (реестр config-helper + config.md), гоняется в test/verify.

- [x] **P2-11. 📌 Трассируемость как детерминированный судья. ✔ СДЕЛАНО (2026-06-20)**
  - Было: `check_taskplan`/`check_sdd` проверяли поля по отдельности (acceptance непуст, sdd_ref
    ПРИСУТСТВУЕТ), но цепочку не замыкали: sdd_ref не проверялся на РЕЗОЛВ (битый якорь проходил),
    и НИКТО не проверял, что у задачи есть eval (задача без eval = EDD её не верифицирует).
  - Сделано: **`check_traceability.py`** — сквозной judge, замыкает «требование → SDD → задача →
    eval» детерминированно. Проверки: sdd_ref-якорь реально резолвится в sdd.md (markdown-anchors:
    slug заголовков + явные `<a name>`/`{#a}`/`id=`, кириллица-aware) — error на битую ссылку;
    eval-покрытие (задача без eval — error, eval-сирота — warning); непустой acceptance. Выдаёт
    **матрицу трассировки** `task → sdd✓ → evals:N → acc:N`. Деградирует мягко (нет sdd/eval-plan →
    соответствующая цепочка пропускается). Ядро — чистая функция, тест `test_check_traceability.py`
    (13 кейсов). Опт-ин `quality.traceability_check` (реестр config-helper + config.md), фаза
    02-eval-plan/verify.

- [x] **P2-12. 📌 Security по умолчанию выключен → secret-scan включён. ✔ СДЕЛАНО (2026-06-20)**
  - Было: security жил только за опт-ин фазой `05.5-security` (`gates.security_review=false`);
    secret-scan был одним regex внутри delivery-judge (последняя фаза).
  - Сделано: **`check_secrets.py`** — усиленный детерминированный secret-scan (ЕДИНЫЙ источник
    правил): присваивания `password/secret/api_key/token`, AWS AKIA, PEM private key, JWT,
    Slack/GitHub/Google токены, jdbc-URL с паролем. Фильтр плейсхолдеров (`${...}`, `changeme`,
    env-ссылки) → низкий FP. **Включён по умолчанию** `quality.secret_scan: true` (verify-фаза).
    `run_judge._delivery_floor` теперь ИМПОРТИРУЕТ `check_secrets.scan_text` (убрал дубль-regex,
    best-effort + fallback) — на доставке форсится всегда. Тест `test_check_secrets.py` (16 кейсов).
  - CVE-скан зависимостей: требует БД уязвимостей (OWASP dependency-check / `gradle
    dependencyCheckAnalyze`) — self-contained-скрипта быть не может; задокументирован как
    подключаемая команда фазы security/verify (как Spotless/Checkstyle в P2-9). Честная граница.

---

## P3 — мелочи, бьющие по доверию

- [x] **P3-13. 📌 Документация рассинхронизирована с кодом. ✔ СДЕЛАНО (2026-06-20)**
  - Named-инстанс (stale `test_pass` пример в config.md: `./gradlew compileJava` / threshold 0.95)
    исправлен ещё в P0-2 на `./gradlew test` / threshold 0 / binary.
  - Закреплено тестом **`test_docs_contract.py`**: пинит, что (а) `build_evals` реально отдаёт
    test_pass как бинарный gate, и (б) документированный пример в config.md с этим не разошёлся
    (нет `compileJava`/`0.95`, есть `threshold 0`/`binary true`). Теперь дрейф доков этого класса
    падает в CI. Прочие классы дрейфа уже под пинами: `test_phase_consistency` (константы фаз),
    `test_docs_resolver_consistency` (резолверы), `config validate`/doctor `config-valid` (типы
    конфига), doctor shell-lint/no-hardcoded-paths (SKILL.md).

- [x] **P3-14. 📌 Сквозной e2e-smoke. ✔ СДЕЛАНО (2026-06-20)**
  - Проверено: `test_state_cycle_golden` гоняет цепочку, но только СТАТУСЫ шагов — вердикты судей
    подкладываются руками, реальные гейт-скрипты не запускаются. Прогона артефактной цепочки не было.
  - Сделано: **`test_e2e_smoke.py`** — один связный фикстур крошечной фичи (pipeline.json + task-plan
    + sdd + .java + evidence) прогоняется через РЕАЛЬНЫЕ CLI-гейты: check_taskplan → check_sdd →
    build_evals → check_traceability → check_architecture → check_secrets → check_tautological_tests →
    build_evidence → check_evidence → delivery_plan. Ловит рассинхрон контрактов «на стыках фаз»
    (формат eval-plan, который ждёт traceability; evidence, который ждёт check_evidence; и т.п.).
    Второй кейс — P0-3 сквозняком: skipped coverage → degraded в evidence → check_evidence FAIL
    (доставка заблокирована). 2 кейса зелёные.

- [x] **P3-15. Конфиг не валидируется при чтении. ✔ СДЕЛАНО (2026-06-20)**
  - Скиллы читают `pipeline.json` напрямую с фолбэком `{}`; опечатка (`coverage_threshold: "0.8"` строкой)
    утечёт вглубь. `config-helper` валидировал только на запись.
  - Сделано: подкоманда `config.py validate` (переиспользует `params-registry.json`). Новый строгий
    `validate_typed` в `_util.py` проверяет УЖЕ типизированные значения из файла и ловит именно
    рассинхрон типа (строка вместо float и т.п.) — в отличие от `coerce_and_validate`, который привёл
    бы строку к числу. Плюс кросс-проверка из остатка **P0-1**: coverage-гейт активен
    (`eval_enabled` + `coverage_threshold>0`), но `jacoco_configured=false` → предупреждение (в `--strict`
    блок) — это та самая preflight «JaCoCo подключён, если гейт включён». В реестр добавлен
    `quality.jacoco_configured` (был только в схеме config.md, не управлялся). Exit: ошибки типов → 1,
    предупреждения → 0 (или 1 при `--strict` для preflight-гейта). Тесты: `test_config.py` (+5 кейсов,
    26 зелёных). Доки: SKILL.md config-helper.
  - Осталось (опц.): вызвать `validate --strict` из `preflight-validate.py` как мягкий гейт старта
    пайплайна (тонкая обёртка; логика уже здесь).

---

## Рекомендованный порядок (риск/усилие)

1. [x] **P0-1** Coverage fail-closed — `check_coverage.py` strict по умолчанию + `--strict` в pipeline-вызовах. ✔ Закрыто.
2. [x] **P3-15** `validate`-подкоманда в config-helper — переиспользует готовый реестр; типы/диапазоны + проверка «JaCoCo включён, если coverage-гейт активен» (связано с P0-1, P1-8). ✔ Закрыто.
3. [x] **P0-3** degraded как явный исход в evidence — тихие пропуски гейтов → видимы и валят доставку (fail-closed). ✔ Закрыто.
