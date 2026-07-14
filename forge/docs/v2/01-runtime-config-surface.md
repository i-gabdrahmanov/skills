# Контекст: runtime config surface форка (отчёт Explore-агента 1)

> Что форк GigaCode/Qwen конфигурирует НИЖЕ уровня хуков и что репо уже использует/документирует.

## Summary

Forge работает почти целиком на **hook-слое**. Он подробно документирует нативное поведение
форка, но **почти ничего не конфигурирует ниже хуков**: единственное, что деплой пишет в
settings.json — блок `hooks`, `disableAllHooks`, `$version`. Нативные approval-режимы,
allow/denylist'ы тулов, sandbox-флаги **никогда не выставляются**; где рантайм что-то форсит
нативно — репо считает это неконтролируемым ограничением и работает *вокруг*.

## (a) Как деплой регистрирует хуки

Ключевые файлы: `deploy.sh`, `deploy-local.sh`, `hooks/resolve_hook_paths.py`,
`hooks/settings.hooks.json`. Модель деплоя — **project-level**, всё в `<target>/.gigacode/`.

`deploy.sh <target>`:
1. tar-копирует `hooks/` и `skills/` в `<target>/.gigacode/` (исключая `__pycache__`, `.pyc`,
   `.DS_Store`, локальный `minor-defect-fix/config.json` оператора).
2. Кладёт `deploy-local.sh` + доки (`FORGE.md`, `SKILLS-REGISTRY.md`).
3. Вызывает `deploy-local.sh` → бэкап существующего settings.json (`.bak` + timestamped) → resolver.
4. `preflight.py` (advisory).

**Запись settings.json делает `resolve_hook_paths.py`** (строки ~194-207): читает шаблон
`settings.hooks.json`, заменяет `${PROJECT_ROOT}`, **обновляет ТОЛЬКО ключ `hooks`**
(`existing["hooks"] = resolved_hooks`); `mcpServers`, `permissions`, `$version` явно сохраняются;
`setdefault("disableAllHooks", False)`, `setdefault("$version", 3)`.

**Формат hooks-блока**: событие → массив групп `{ "matcher": <regex>, "sequential": <bool>,
"hooks": [ {"type":"command", "command":"python3 ${PROJECT_ROOT}/.gigacode/hooks/<x>.py",
"name":"<x>"} ] }`. События: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
`UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `SessionStart`, `Stop`.
Матчеры — заякоренные alternation-regex, покрывают канон + Claude-алиасы:
- `"^(run_shell_command|Bash)$"` (sequential blocking)
- `"^(write_file|edit|notebook_edit|Write|Edit|WriteFile|NotebookEdit)$"` (sequential blocking)
- `"^(read_file|web_fetch|run_shell_command|Read|ReadFile|Fetch|WebFetch|Bash)$"` (PostToolUse prompt-guard)
- `"*"` → только `log-agent.py`.

`deploy-local.sh`: поддерживает `--dry-run` и `--check`.

## (b) Runtime-knobs, упомянутые в репо

1. **`--experimental-hooks`** — важнейший, и это **аргумент процесса, не поле settings**.
   `hooks/DEPLOY.md`: без него `[HOOK_REGISTRY] 0 hook entries`; в апстриме Qwen флага нет
   (хуки on по умолчанию) — специфика форка на старой базе.
2. **`disableAllHooks`** (bool в settings.json) — мастер-выключатель. `resolve_hook_paths.py`
   дефолтит в `False` (через setdefault — НЕ перезаписывает существующий `true`!).
3. **`getDisableAllHooks()`** — апстрим-гейт (`FORGE.md`: «в стоке хуки on по умолчанию,
   гейт `!getDisableAllHooks()`»).
4. **`$version`** = 3.
5. **`permission_mode`** — нативное поле approval-режима; репо его **не задаёт**, только пассивно
   пишет в аудит-логи: значения `auto-edit` (92×) и `default` (55×) в `прогоны харнес/.../ai-logs`.
6. **`permissions`/`mcpServers`** — упомянуты только как секции, которые resolver **сохраняет**.
7. Семантика хуков (`FORGE.md` «Известные ограничения»): stdin JSON snake_case
   (`hook_event_name`/`session_id`/`cwd`/`tool_name`), подтверждено на qwen-code 0.19.3;
   `additionalContext` только из `hookSpecificOutput.additionalContext`; **гейт-хуки fail-OPEN
   на таймауте** (>60с kill → действие проходит); блок = `exit 2` + stderr.

## (c) Что форк НЕ поддерживает / не форсит нативно (по репо)

- **Нативный сейфти форка молча режет shell-синтаксис**, который forge не контролирует
  (`find … -exec`, `$(...)`, backticks, `ls -R`): формат `Tool run_shell_command is denied`,
  не `exit 2`+stderr. `fork-syntax-guard.py` перехватывает ДО нативного deny только ради читаемой
  ошибки — «эргономика, не enforcement».
- **Нет события «скилл выбран»** → вход через `router` не форсится.
- **Хуки за экспериментальным флагом** (в отличие от апстрима).
- **`agent_type` субагента всегда `general-purpose`** → SoD по agent_type — best-effort.
- **Нет нативного token/cost-потолка** на который опираются: `cost-breaker` hard-stop отключён.
- **НИГДЕ не упомянуты** (grep по .md/.json/.sh/.py): `approvalMode`, `yolo`, `autoAccept`,
  `coreTools`, `excludeTools`, `allowedTools`, `sandbox`, `seatbelt`, `docker`. Нет ни одного
  шаблона settings.json для нативной permission-системы — только hooks-блок.

## (d) Канонические имена тулов + пробелы покрытия

Канон (`test_matcher_canonical_names.py`, `preflight.py`): `run_shell_command`, `write_file`,
`edit`, `notebook_edit`, `read_file`, `web_fetch`. Матчинг:
`new RegExp(matcher).test(canonicalToolName)`, где `toolName = canonicalToolName(rawName)`,
алиасы в `permissions/rule-parser.ts`. Claude-имена `Bash`/`Write`/… — только входные алиасы.

**BLOCKER-0**: матчеры `^Bash$`/`(Write|Edit|…)` не матчили канон → весь deny-first control-plane
выпадал из плана; только `*`-хуки работали. Фикс — канон-имена + пины (`test_matcher_canonical_names.py`,
`preflight._check_matchers_canonical`).

Пробелы:
- Блокирующие (exit 2) цепочки — только shell (`run_shell_command`) и write
  (`write_file`/`edit`/`notebook_edit`).
- `read_file`/`web_fetch` — только PostToolUse (prompt-guard scan), не блок.
- Прочие тулы (Glob, Grep, LS, `agent`) — только `*` → log-agent (audit).
- preflight канон-проверка частична (не валидирует PostToolUse read/fetch матчеры).
- Риск user-level stale config: `~/.qwen/settings.json` может затенять project-деплой.
