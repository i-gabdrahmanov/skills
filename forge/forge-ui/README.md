# forge-ui — поставка forge для фронт-репо

Здесь лежит только то, что отличает поставку фронт-репо от бэковой; всё остальное — общий
источник `forge/` (хуки, стейт, согласия) и ветка `skills/forgeui/` (бриф, движок, гейты).

| Путь | Что это |
|---|---|
| `commands/forge.md` | `/forge` фронт-репо: запускает ветку `forgeui` напрямую (router не нужен) |
| `probe/` | проба: грузит ли GigaCode extension из каталога ПРОЕКТА (см. ниже) |
| `../profiles/ui.profile` | состав поставки: какие скиллы, команды и MCP едут во фронт-репо |

Установка (сейчас — project-моделью, как бэк):

```bash
bash deploy.sh --profile ui /path/to/front-repo
```

## Проба project-level extension

forge-ui задуман как GigaCode-extension внутри проекта: `<repo>/.gigacode/extensions/forge-ui/` —
скрипты гейтов тогда лежат в проекте, и shell к ним разрешён (причина отката extension-модели в
2026-08 была ровно в том, что они лежали в `~/.gigacode/extensions/`). Но база рантайма —
qwen-code 0.21.14 — грузит extensions только из домашнего каталога: метод загрузки из каталога
проекта в коде есть, но не вызывается. Поэтому сначала проба на настоящем GigaCode:

1. `cp -a forge-ui/probe <repo>/.gigacode/extensions/forge-ui-probe`
2. `gigacode` из корня `<repo>`, набрать `/forge-ui-probe`
3. команда есть — ставим extension'ом; нет — остаёмся на `deploy.sh --profile ui`.

Снять пробу — удалить `<repo>/.gigacode/extensions/forge-ui-probe`.

Дизайн, решения и этапы — `tasks/019-forge-ui.md`.
