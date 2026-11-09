# spec-graph — поставка forge для репо спеки

Здесь лежит только то, что отличает поставку репо спеки; всё остальное — общий источник `forge/`
(хуки, стейт, согласия) и ветка `skills/specgraph/` (бриф, движок, гейты).

| Путь | Что это |
|---|---|
| `commands/forge-graph.md` | `/forge-graph` репо спеки: запускает ветку `specgraph` напрямую |
| `../profiles/graph.profile` | состав поставки: какие скиллы, команды и MCP едут в репо спеки |

Установка — отдельным extension'ом (одна поставка на репо):

```bash
bash install.sh spec-graph /path/to/spec-repo     # из корня клона forge
```

Ставится в `<spec-repo>/.gigacode/extensions/spec-graph/` (хуки, скиллы `specgraph`,
`pipeline-state`, `config-helper`, скрипты `feature-pipeline`, команда `/forge-graph`, MCP `spec-graph`
в манифесте) и подключается к GigaCode ссылкой — `gigacode extensions link`, включение только для
этого workspace. Обновить — `bash update.sh /path/to/spec-repo`, снять — `bash uninstall.sh
spec-graph /path/to/spec-repo`.

Дизайн и решения — `tasks/020-spec-graph.md`, задачи — `docs/specgraph/`.
