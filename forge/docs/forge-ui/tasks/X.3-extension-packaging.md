# X.3 — Упаковка forge-ui как extension внутри проекта

**Этап:** сквозная · **Зависит от:** — · **Статус:** done (2026-10-30; проба отменена — установка через ссылку)
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

## Результат

Сделано шире задачи и без пробы: extension'ами ставятся ОБЕ поставки, раздельно — `install.sh forge`
(бэк-репо) и `install.sh forge-ui` (фронт-репо), одна на репо. Решение — FORGE.md [BR-22], руководство —
INSTALL.md.

- **Почему без пробы.** Рантайм (qwen-code 0.21.14 и 0.22.2) читает extensions только из
  `~/.gigacode/extensions`. Вместо загрузки из каталога проекта — ССЫЛКА: `gigacode extensions link
  <repo>/.gigacode/extensions/<kind>` кладёт в домашний каталог запись `{type: link, source}`, и
  рантайм читает манифест, `hooks/hooks.json`, скиллы и команды по пути источника. Файлы — в проекте,
  shell к ним разрешён. Проба `forge-ui/probe/` снята.
- **Сборка** — `hooks/forge_extension.py`: манифест (`gigacode-extension.json` + `qwen-extension.json`,
  имя `<kind>-<каталог>-<хеш пути>` — реестр ссылок общий на пользователя, MCP профиля) и
  `hooks/hooks.json` из `settings.hooks.json` с абсолютными путями и здоровым интерпретатором, без
  `${CLAUDE_PLUGIN_ROOT}`. Тексты брифов называют пути поставки.
- **Ссылка и включение** — `forge_extension.py link`: link, `disable --scope user` + `enable --scope
  workspace`, взаимное выключение с другими forge-проектами машины. Правила рантайма не покрывают
  проекты вне `$HOME`, поэтому каждый хук идёт через `hooks/_run_hook.py`: если рабочий каталог сессии
  вне проекта extension'а, хук молчит (exit 0).
- **Установщики** — `install.sh` (снимает старую co-located раскладку, подменяет каталог extension'а
  целиком), `uninstall.sh` (ссылка + каталог; `--legacy-only` — только старая раскладка), `update.sh`
  (поставку определяет сам), `deploy-local.sh` внутри extension'а (переезд проекта, смена python),
  `deploy.sh` — совместимость.
- **preflight** — режим `extension`: `hooks/hooks.json`, манифест под этот путь проекта, ссылка в
  рантайме; ошибка на старую раскладку рядом (хуки задвоятся), старые копии скиллов в `.gigacode/skills`
  (перекрывают) и вторую поставку. doctor находит пути реестра `.gigacode/…` внутри поставки.
- **Тесты**: `hooks/test_forge_extension.py` (17: сборка, check, запускатель, link/unlink на поддельном
  CLI), `hooks/test_uninstall.py` (18: круговой цикл, миграция, `.gitignore`, аргументы),
  `hooks/test_update.py`, `test_install_kinds.py` (обе поставки: состав, MCP initialize, preflight,
  каждый путь из брифов существует), `test_tombstones.sh`.
- **Проверено** на стоковом qwen 0.22.2 в песочнице (`HOME` во временном каталоге): link, включение по
  workspace, взаимное выключение двух проектов, `extensions list` видит команды, хук через запускатель
  в своём проекте блокирует (rc 2), в чужом каталоге — 0, миграция старой раскладки, снятие ссылки
  (исходники link-установки рантайм не трогает). **На GigaCode — не проверено** (X.4): `extensions
  link`, `--scope workspace`, имя install-маркера, приходит ли `QWEN_PROJECT_DIR`/cwd хуку.
- Не сделано: живой прогон модели со срабатыванием хука из extension'а (нужна авторизация рантайма);
  MCP-серверы extension'а другого проекта вне `$HOME` по-прежнему стартуют (их запуск рантайм не
  гейтит, а сервер не видит рабочий каталог сессии).
