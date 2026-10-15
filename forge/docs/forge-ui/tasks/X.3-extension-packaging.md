# X.3 — Упаковка forge-ui как extension внутри проекта

**Этап:** сквозная · **Зависит от:** проба (`forge-ui/probe/`) · **Статус:** blocked (ждёт пробы на GigaCode)
**Спека:** §3.1 (состав расширения) · **Решение:** D6 в `tasks/019-forge-ui.md`

## Зачем
Задумано ставить forge-ui как GigaCode-extension в `<repo>/.gigacode/extensions/forge-ui/`: скрипты
гейтов тогда внутри проекта, shell к ним разрешён. В базе рантайма (qwen-code 0.21.14) extensions
из каталога проекта не грузятся — решает проба.

## Что сделать (если проба положительная)
- Сборщик пакета по `profiles/ui.profile`: hooks/, нужные skills/, `commands/forge.md`, манифест
  `gigacode-extension.json` (name `forge-ui`, version из `git describe --match 'forge/v*'`,
  `mcpServers.forge-ui` с путём относительно extension); регистрация хуков в формате extension
  (`hooks/hooks.json` и переменная пути extension — сверить на GigaCode); выбор интерпретатора
  запекается установщиком, как в deploy-local.
- Установщик и снятие (только своё); preflight в extension-раскладке (`_project.gigacode_dir` =
  каталог extension); тест установки в обе раскладки.
- Если проба отрицательная — задача закрывается: остаётся `deploy.sh --profile ui` (уже работает).

## Готово, когда
- Установка extension'ом проходит preflight, `/forge` и MCP forge-ui видны рантайму; снятие чистое.
