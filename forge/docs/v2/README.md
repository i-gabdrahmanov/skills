# Forge v2 — усиление enforcement ниже уровня хуков

Пакет документов по итогам сессии анализа обходов DeepSeek на форке GigaCode (2026-07-04).
Проблема: слабая модель обходит hook-based ограничения forge, т.к. хук-слой fail-open
(таймаут/краш/пустой stdin) и самоуязвим (модель правит сами хуки/settings.json). Решение —
добавить слои enforcement НИЖЕ хуков: нативные `permissions` рантайма, macOS Seatbelt,
операторский driver с пофазными permissions.

## Файлы

| Файл | Содержание |
|---|---|
| [`00-plan.md`](00-plan.md) | **Главный план реализации** — WS0–WS4, риски, верификация, ключевые файлы |
| [`01-runtime-config-surface.md`](01-runtime-config-surface.md) | Что форк конфигурирует ниже хуков; как деплой регистрирует хуки; канон имён тулов и пробелы |
| [`02-bypass-vectors.md`](02-bypass-vectors.md) | Каталог исправленных обходов, незакрытых зон, полный fail-open каталог, наблюдённое поведение модели в прогонах |
| [`03-qwen-native-mechanisms.md`](03-qwen-native-mechanisms.md) | Нативные механизмы qwen-code (permissions/approvalMode/sandbox) из офиц. документации |
| [`04-decisions-and-layers.md`](04-decisions-and-layers.md) | Ответы пользователя, карта слоёв A–F, маппинг «вектор → слой» |

## TL;DR плана

1. **WS2 (сразу)** — закрыть наблюдённые обходы на хук-уровне: самозащита `.gigacode/**` в
   state-write-guard; блок verification-bypass-флагов (`-x checkstyle*`, `-DskipTests`) в
   destructive-blocker + record_gate; детект user-level settings-shadowing в preflight.
2. **WS0 (probe)** — `hooks/probe_runtime.py` + `docs/fork-probe.md`: проверить на реальном
   бинаре, что permissions.deny форсится, как ведёт себя shell-эквивалентность (ключевой риск),
   есть ли sandbox/yolo, какой каталог профилей.
3. **WS1 (главный слой)** — `hooks/settings.permissions.json` (deny `.gigacode/**` + control-plane
   ground + деструктив; ask push/commit; allow санкц. команды); merge в деплой через
   `resolve_hook_paths.py`; жёсткое `disableAllHooks=false`; preflight-проверки.
4. **WS3** — Seatbelt: `sandbox/sandbox-macos-forge.sb` + `forge-run.sh` (preflight-гейт запуска +
   env sandbox).
5. **WS4** — гибридный `driver/`: пофазные permissions-overlay (RED deny Edit src/main, GREEN deny
   Edit src/test), межфазные гейты вне модели, лимиты на вызов.

## Статус
План к реализации. Не начато. Порядок: WS2 → WS0 → WS1 → WS3 → WS4.
