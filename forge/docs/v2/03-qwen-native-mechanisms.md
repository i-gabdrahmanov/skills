# Контекст: нативные механизмы qwen-code для enforcement (документация, июль 2026)

> Официальная документация qwen-code. Форк GigaCode базируется на qwen-code; подтверждено, что
> система `permissions` в форке ЕСТЬ (косвенно — репо ссылается на `permissions/rule-parser.ts`
> форка для канонизации имён тулов). Точная версия базы форка неизвестна → нужен probe (WS0).

## 1. `permissions` (современная система)

settings.json, приоритет **deny > ask > allow > default**:
```json
{
  "permissions": {
    "allow": ["Bash(git *)", "Read(./src/**)"],
    "ask":   ["Edit", "Bash(git push *)"],
    "deny":  ["Bash(rm -rf *)", "Read(.env)"]
  }
}
```
- Синтаксис правил: специфаеры `"Bash(git *)"` (префикс-матчинг команд) и path-паттерны
  (`/src/**`, `~/.env`, `./secrets/**`).
- Мета-категории: `Read` (покрывает file read, grep, glob, list), `Edit` (edit, write_file,
  notebook_edit).
- Работает на уровне **tool registry** — НЕ зависит от хуков.
- Легаси `tools.core`/`tools.exclude`/`tools.allowed` — авто-мигрируются в `permissions` при
  первой загрузке.
- Документация заявляет: «permissions system also prevents shell-command bypass; rules for
  Read/Edit/WebFetch enforced even when agents attempt equivalent shell operations like
  cat/grep/curl». ⚠️ Это ключевое обещание и одновременно риск (может заблокировать
  санкционированные python-writer'ы state) → проба WS0 2(c).
- Легаси `excludeTools` для run_shell_command — простой string-matching, официально «not a
  security mechanism».

## 2. `tools.approvalMode`

Значения: `plan` (analyze-only), `default` (требует approval), `auto-edit`, `auto` (LLM-классификатор),
`yolo` (auto-approve all). В логах форка наблюдались `permission_mode: auto-edit` и `default`.

## 3. Sandbox

Приоритет активации (env побеждает всё):
- **env**: `QWEN_SANDBOX=true|false|docker|podman|sandbox-exec`
- **CLI**: `-s`, `--sandbox`, `--sandbox=<provider>`
- **settings**: `tools.sandbox` (bool или path), `tools.sandboxImage`.

Методы:
- **macOS Seatbelt** (`sandbox-exec`): «restricts writes outside the project directory, but allows
  most other operations» — лёгкий, встроенный.
- **Docker/Podman**: полная изоляция процесса, кроссплатформенно.

macOS Seatbelt-профили (через `SEATBELT_PROFILE`):
`permissive-open` (default), `permissive-closed`, `permissive-proxied`,
`restrictive-open`, `restrictive-closed`, `restrictive-proxied`.
Кастомный профиль: `.qwen/sandbox-macos-<name>.sb` в проекте (форк может читать из `.gigacode/` —
неизвестно, проба WS0 3).

Сеть: `QWEN_SANDBOX_PROXY_COMMAND` — локальный прокси рядом с sandbox; профили `-proxied`
поддерживают allowlist через прокси.

Монтирование (container): workspace + `~/.qwen` монтируются в контейнер; read-only опции в доке
явно не расписаны.

## 4. Security / Trust

- `security.folderTrust.enabled` (bool).
- `security.auth.enforcedType` — enterprise-enforced тип аутентификации.

## Источники
- https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/
- https://qwenlm.github.io/qwen-code-docs/en/users/features/sandbox/
- https://qwenlm.github.io/qwen-code-docs/en/developers/tools/shell/

## Как это ложится на форк (выводы для плана)
- `permissions.deny` — главный слой: не зависит от хуков, stdin, таймаутов. Закрывает
  самовосстановление хуков (`Edit(./.gigacode/**)`) и `disableAllHooks`.
- `approvalMode` — preflight должен проверять, что не `yolo`.
- Seatbelt `sandbox-exec` — доступен на macOS локально; кастомный `.sb` профиль ограничивает
  запись вне проекта + сетевой allowlist. НЕ решает записи ВНУТРИ проекта тем же пользователем
  (это остаётся зоной permissions + хуков).
- Всё гейтится пробой на реальном бинаре (WS0): какая версия базы, работает ли deny, какой
  каталог профилей, есть ли yolo, как ведёт себя shell-эквивалентность.
