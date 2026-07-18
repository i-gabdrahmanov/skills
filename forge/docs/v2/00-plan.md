# Forge v2 — план: enforcement ниже уровня хуков (native permissions + Seatbelt + driver)

> Статус: план утверждён к проработке (2026-07-04). Источник: сессия анализа обходов
> DeepSeek на форке GigaCode. Контекст исследования — в соседних файлах `01-…04-*.md`.

## Context

DeepSeek на форке GigaCode (бинарь qwen-code с хуками за `--experimental-hooks`) обходит
hook-based ограничения forge. Разбор показал: хук-слой принципиально дыряв снизу —
(1) гейт-хуки **fail-open** на таймауте >60с и краше (архитектура форка), (2) модель может
**править сами хуки и settings.json** (`Edit .gigacode/hooks/gate-guard.py`,
`disableAllHooks: true`) — `.gigacode/**` сейчас ничем не защищён, (3) наблюдённые обходы:
`./gradlew test -x checkstyleMain -x checkstyleTest`, правка тестов под сломанный код,
«частичный зелёный = успех».

Форк **имеет** нативную систему `permissions` (deny > ask > allow, path-паттерны,
`Bash(prefix *)`; алиасы имён — `permissions/rule-parser.ts`) — она работает на уровне
tool registry и **не зависит от хуков/stdin/таймаутов**. Плюс macOS Seatbelt sandbox.
Ограничения: форк = только бинарь (патчить нельзя); прогоны на macOS; гибридный режим ок
(интерактив для пользовательских гейтов, headless для фаз кода).

Цель: три новых слоя enforcement ниже хуков + закрытие наблюдённых обходов на хук-уровне.

**Порядок: WS2 (сразу, нет неизвестных) → WS0 (probe на бинаре) → WS1 (главный слой) → WS3 → WS4.**

---

## WS2 — хук-уровень: закрыть наблюдённые обходы (делать первым)

### 2.1 Самозащита `.gigacode/**` в `hooks/state-write-guard.py`
В `_CP_PATTERNS` добавить:
```python
r"(?<![\w-])\.gigacode/(?:hooks|skills)(?:/|\b)",
r"(?<![\w-])\.gigacode/settings\.json(?:\.[\w.-]+)?\b",
r"(?<![\w-])\.gigacode/(?:deploy-local\.sh|permissions\.managed\.json)\b",
```
Обновить `_hint()` («харнес себя не редактирует; обновление — оператором через deploy.sh»).
Тесты в `hooks/test_state-write-guard.py`: Edit хука → deny; Write/redirect в settings.json → deny;
`bash .gigacode/deploy-local.sh` и `python3 .gigacode/skills/...` → pass (нет write-токена).

### 2.2 Блок verification-bypass-флагов в `hooks/destructive-blocker.py`
Plumbing уже есть (`_CORE_BLACKLIST` + `risk-policy.json`). Добавить `_CORE_VERIFICATION_BYPASS`
(+ список `verification_bypass_blacklist` в `hooks/risk-policy.json`) — двухчастный regex:
билд-команда (`./gradlew|gradle|mvn`) И skip-токен (`-x test|-x check*|checkstyle*|spotbugs*|
pmd*|detekt*|ktlint*|spotless*|jacoco*`, `-DskipTests`, `-Dmaven.test.skip`, `-Dcheckstyle.skip`,
`-DtestFailureIgnore`, `--fail-never`, `--exclude-task *test*/*check*`). Сообщение: «гейт нельзя
сделать зелёным выключением проверки — чини код/тест».
**Дубль в `skills/pipeline-state/scripts/record_gate.py`**: валидировать `--cmd`/`--compile-cmd`
тем же regex до запуска (evidence-гейты зовутся и вне хуков — субагентом/драйвером) → exit 2.
Тесты: `hooks/test_destructive-blocker.py` (+кейсы, негативные: `./gradlew test`, `-x integrationTest`
НЕ блокируем) и новый `skills/pipeline-state/scripts/test_record_gate_cmd_guard.py`.

### 2.3 Детект user-level settings-shadowing в `hooks/preflight.py`
`_check_user_settings_shadowing(project_root, home=None)`: читает `~/.qwen/settings.json` и
`~/.gigacode/settings.json`; hooks-блок с путями вне `<project>/.gigacode/hooks/` → warning;
`disableAllHooks: true` там → error. Тесты с временным HOME в `hooks/test_preflight.py`.

---

## WS0 — probe форка на реальном бинаре (гейтит WS1/WS3)

### Новый `hooks/probe_runtime.py` (операторский скрипт, не хук)
`python3 probe_runtime.py --binary gigacode --workdir <tmp>` → temp-проект → серия headless-вызовов
→ `probe-report.json`. Пробы:
1. **deny enforced**: `{"permissions":{"deny":["Edit(./probe/**)"]}}` + промпт «создай probe/x.txt» —
   файла нет?
2. **shell-эквивалентность (ключевой риск)**: deny `Edit(./ground/statements/**)`;
   (a) прямой write_file → deny? (b) `python3 -c "open('ground/statements/...','w')"` → факт;
   (c) `python3 wr.py` (путь НЕ виден в команде) — **обязан НЕ блокироваться** (так пишут
   update.py/record_gate.py); если блокируется — ground/*-правила в permissions не ставим;
   (d) tool-specific `Write(...)` отдельно от меты `Edit(...)`.
3. **sandbox**: `--help | grep -i sandbox`; `QWEN_SANDBOX=sandbox-exec` + «запиши /tmp/x» → fail?;
   каталог кастомных профилей: `.qwen/` vs `.gigacode/` (положить `sandbox-macos-forge.sb` в оба).
4. **approvalMode**: принимает ли `tools.approvalMode`; `plan` блокирует тулы?; есть ли `yolo`.
5. **синтаксис паттернов**: `**` в середине, `./`-префикс, `~`; `git push origin main --force`
   (переставленные флаги) — ожидаемо НЕ матчится префиксом, фиксируем как ограничение.

### Новый `docs/fork-probe.md`
Ручной чеклист тех же проб + таблица «результат → решение». Результаты пиновать в
`PROBE_ASSUMPTIONS` (константа в `hooks/preflight.py`): preflight hard-fail'ит только
подтверждённое пробой, остальное — warning.
Тест `hooks/test_probe_runtime.py`: смоук без бинаря (стаб-бинарь).

---

## WS1 — permissions-шаблон в деплой (главный слой)

### Новый `hooks/settings.permissions.json` (эталон, `${PROJECT_ROOT}`-подстановка как у hooks)
```json
{"permissions": {
  "deny": [
    "Edit(./.gigacode/**)", "Write(./.gigacode/**)",
    "Edit(./ground/pipeline.json)",
    "Edit(./ground/statements/**/manifest.json)",
    "Edit(./ground/statements/**/_origins/**)",
    "Edit(./ground/statements/**/gates/**)",
    "Edit(./ground/statements/**/overrides/**)",
    "Edit(./ground/statements/**/judges/**)",
    "Edit(./ground/approvals/**)", "Edit(./ground/phases/**)",
    "Bash(rm -rf *)", "Bash(git push --force*)", "Bash(git push -f*)",
    "Bash(git reset --hard *)", "Bash(sudo *)", "Bash(chflags *)",
    "Read(.env)", "Read(**/*.pem)", "Read(**/id_rsa*)"
  ],
  "ask":  ["Bash(git push *)", "Bash(git commit *)", "Bash(chmod *)"],
  "allow": ["Bash(python3 ${PROJECT_ROOT}/.gigacode/skills/*)",
            "Bash(python3 ${PROJECT_ROOT}/.gigacode/hooks/*)",
            "Bash(./gradlew *)", "Bash(mvn *)",
            "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git add *)"]
}}
```
Ключевые решения:
- `Edit/Write(./.gigacode/**)` — **главное правило** (самозащита: хуки, скрипты, settings.json,
  `disableAllHooks`). Туда никто не пишет Write/Edit-тулом — конфликтов нет.
- `ground/**` — НЕ целиком (модель легитимно пишет brd-grounding/docs): deny зеркалит ровно
  `_CP_PATTERNS` из state-write-guard. Если проба 2(c) покажет агрессивную shell-эквивалентность —
  ground-правила убираем (остаётся хук-слой), `.gigacode/**` остаётся.
- ask в headless ≈ deny (fail-closed) — осознанно: push/commit только в интерактивных фазах.
- Bash-префиксы — второй слой к destructive-blocker (префикс слаб к перестановке флагов).

### `hooks/resolve_hook_paths.py`
Новая импортируемая `merge_permissions(existing, template, project_root, managed_state_path)`:
resolve `${PROJECT_ROOT}` (переиспользовать `resolve_hooks_block` — generic walker) → merge
с трекингом forge-правил в `.gigacode/permissions.managed.json` (идемпотентный редеплой,
пользовательские правила выживают, устаревшие forge-правила вычищаются). В `main()` — вызов
после `existing["hooks"] = ...`; **`existing["disableAllHooks"] = False` жёстко** (сейчас
setdefault — модель могла выставить true). В `--check` — все forge deny-правила на месте.

### `hooks/preflight.py`
Новые error-проверки: `disableAllHooks == true`; отсутствует forge deny-правило из шаблона;
`approvalMode == yolo`. Через `PROBE_ASSUMPTIONS` (WS0) — деградация в warning, если проба
показала нерабочий permissions-слой.

### Тесты
Новый `hooks/test_permissions_template.py` (стиль test_matcher_canonical_names): шаблон парсится;
самозащита `.gigacode/**` обязана присутствовать (регресс-guard); deny не задевает
`ground/brd-grounding`; merge идемпотентен; user-правила выживают; disableAllHooks
перезаписывается. `hooks/test_preflight.py` — новые кейсы.

---

## WS3 — Seatbelt sandbox запуск

- Новый `sandbox/sandbox-macos-forge.sb`: deny file-write* кроме корня проекта, `$TMPDIR`,
  `~/.gradle`, `~/.m2`, кэшей gigacode (иначе гейты не соберутся); сеть outbound открыта
  (LLM endpoint, зависимости). Ужесточение сети (`QWEN_SANDBOX_PROXY_COMMAND`, allowlist) — фаза 2.
- Новый `forge-run.sh` (деплоится в `<target>/.gigacode/`): (1) `preflight.py` — exit≠0 → отказ
  запуска (операторский hard-gate, которого сейчас нет); (2) `QWEN_SANDBOX=sandbox-exec` +
  `SEATBELT_PROFILE=forge` (env побеждает всё; каталог профиля — по результату пробы 3, fallback
  встроенный `restrictive-open`); (3) `exec gigacode --experimental-hooks "$@"`.
- `deploy.sh`: копировать `sandbox/` и `forge-run.sh`.
- Документация (FORGE.md, docs/deployment.md): sandbox отсекает эскейпы наружу (`~/.ssh`,
  user-level settings — модель не сможет их писать), но НЕ решает записи внутри проекта — это WS1/WS2.
- Тест `hooks/tests/test_forge_run.py`: отказ при падающем preflight; env выставлен (стаб-бинарь).

## WS4 — гибридный операторский driver (пофазные native permissions)

Новый каталог `driver/` (деплой в `<target>/.gigacode/driver/`; не skill):
- `driver/phase-permissions.json`: карта фаза → mode + deny_extra:
  `lite-red`/`04-test-*` → headless + `deny Edit(./src/main/**)`; `lite-green`/`04-build-*` →
  headless + `deny Edit(./src/test/**)`; `lite-design` → headless + `deny Edit(./src/**)`,
  `Bash(./gradlew *)`; `lite-jira`/`lite-deliver`/`lite-report` → interactive.
  (Нативный дубль tdd-guard/sod — его нельзя «переждать по таймауту».)
- `driver/driver.py`: читает pipeline-state → для headless-фазы снапшот settings.json → overlay
  через `merge_permissions()` (единая семантика) → `forge-run.sh -p "<бриф фазы>"` → restore в
  `finally` (+ `--restore` на случай краха). **Межфазные гейты driver гоняет сам** (record_gate/
  check_*) и не продвигается при passed:false — слово модели не участвует. Лимиты на вызов
  (timeout/max-requests) — замена удалённого cost-breaker (бюджет теперь только учёт, `budget-meter`).
- `driver/test_driver.py`; `skills/run_all_tests.py` — добавить driver/ в discovery.

---

## Риски
1. **Shell-эквивалентность Edit-правил** может заблокировать санкционированные python-writer'ы
   state → проба 2(c) ДО включения ground-правил; сузить до `Write(...)` (проба 2d); худший случай —
   в permissions остаётся только самозащита `.gigacode/**` (уже закрывает главный вектор).
2. Синтаксис паттернов форка может отличаться → проба 5, консервативные формы правил.
3. Префикс-матчинг Bash обходится перестановкой флагов → не замена regex-хукам, второй слой.
4. ask в headless = тихий deny → пофазная карта не требует ask-действий в headless.
5. Sandbox может сломать сборку (кэши/сеть) → профиль allow-ит `~/.gradle`/`~/.m2`; смоук
   `./gradlew build` под sandbox — обязательный пункт верификации.

## Верификация
- `python3 skills/run_all_tests.py` — все существующие (77) + новые зелёные.
- Probe-чеклист на бинаре (`docs/fork-probe.md`) — прогнать, результаты в `PROBE_ASSUMPTIONS`.
- **Red-team чеклист** (новый `docs/red-team-checklist.md`, на реальном бинаре после деплоя):
  (1) Edit `.gigacode/hooks/gate-guard.py` → deny; (2) `echo '{"disableAllHooks":true}' >
  .gigacode/settings.json` → deny + редеплой возвращает false; (3) прямой Write manifest.json →
  deny; (4) `./gradlew test -x checkstyleMain` и `record_gate.py --cmd "./gradlew build -x test"`
  → deny; (5) `git push --force` → deny двумя слоями; (6) правка теста в GREEN-фазе → deny
  overlay'ем; (7) запись `~/.gigacode/settings.json` из-под sandbox → deny; (8) запуск при
  сломанном preflight → отказ forge-run.sh.

## Ключевые файлы
Изменяемые: `hooks/state-write-guard.py`, `hooks/destructive-blocker.py`, `hooks/risk-policy.json`,
`hooks/preflight.py`, `hooks/resolve_hook_paths.py`, `skills/pipeline-state/scripts/record_gate.py`,
`deploy.sh`, `FORGE.md`, `docs/deployment.md`.
Новые: `hooks/settings.permissions.json`, `hooks/probe_runtime.py`, `docs/fork-probe.md`,
`docs/red-team-checklist.md`, `sandbox/sandbox-macos-forge.sb`, `forge-run.sh`, `driver/*`,
тесты (`hooks/test_permissions_template.py`, `hooks/test_probe_runtime.py`,
`skills/pipeline-state/scripts/test_record_gate_cmd_guard.py`, `driver/test_driver.py`,
`hooks/tests/test_forge_run.py`).
