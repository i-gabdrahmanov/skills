# forge-ui — поставка forge для фронт-репо

Здесь лежит только то, что отличает поставку фронт-репо от бэковой; всё остальное — общий
источник `forge/` (хуки, стейт, согласия) и ветка `skills/forgeui/` (бриф, движок, гейты).

| Путь | Что это |
|---|---|
| `commands/forge.md` | `/forge` фронт-репо: запускает ветку `forgeui` напрямую (router не нужен) |
| `../profiles/ui.profile` | состав поставки: какие скиллы, команды и MCP едут во фронт-репо |

Установка — отдельным extension'ом, независимо от forge бэк-репо (одна поставка на репо):

```bash
bash install.sh forge-ui /path/to/front-repo      # из корня клона forge
```

Ставится в `<front-repo>/.gigacode/extensions/forge-ui/` (хуки, скиллы `forgeui`, `pipeline-state`,
`config-helper`, скрипты `feature-pipeline`, команда `/forge`, MCP `forge-ui` в манифесте) и подключается к
GigaCode ссылкой — `gigacode extensions link`, включение только для этого workspace. Обновить —
`bash update.sh /path/to/front-repo`, снять — `bash uninstall.sh forge-ui /path/to/front-repo`. Подробно —
[INSTALL.md](../INSTALL.md).

Дизайн и решения — `tasks/019-forge-ui.md`, задачи движка — `docs/forge-ui/`.
