# Forge — нативный extension для qwen-code / GigaCode

Упаковка forge (PDLC control-plane) как **нативного extension'а** рантайма qwen-code
(форк = GigaCode) вместо императивного `deploy.sh`, который копировал файлы в
`<target>/.gigacode/` и вручную мержил блок `hooks` в `settings.json`.

## Что внутри

```
forgeExt/
├── qwen-extension.json     # манифест (name: forge, version, displayName, description)
├── hooks/
│   ├── hooks.json          # конфиг хуков (event → matcher → command), пути ${CLAUDE_PLUGIN_ROOT}
│   ├── *.py                # 18 хук-скриптов + зависимости (_project.py, risk_ladder.py)
│   └── risk-policy.json    # deny-политика
├── commands/
│   ├── forge.md            # /forge       → feature-pipeline
│   ├── forge-lite.md       # /forge-lite  → forgelite (исполнение готовой задачи)
│   ├── forge-spec.md       # /forge-spec  → требования-мастер: status/diff/merge/remove/check
│   └── forge-merge.md      # /forge-merge → ярлык слияния дельты в мастер
├── skills/<19 скиллов>/SKILL.md
├── FORGE.md                # справочная дока (НЕ авто-контекст — см. ниже)
└── sync-from-forge.sh      # регенерация из ../forge (пока forge/ = source of truth)
```

## Установка

```bash
# локальная разработка (живой симлинк — правки в forgeExt сразу видны):
qwen extensions link /path/to/forgeExt

# из репозитория:
qwen extensions install <git-url|owner/repo>

# управление:
qwen extensions update forge
qwen extensions disable forge   # временно выключить (все хуки/скиллы гаснут)
qwen extensions uninstall forge
```

После `link`/`install` — перезапустить сессию qwen, если extension не виден сразу.

## Ключевые отличия от deploy.sh (что переписано)

- **Пути хуков.** `${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/X.py` →
  `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/X.py`. `${CLAUDE_PLUGIN_ROOT}` — единственная
  переменная, которую рантайм подставляет в file-based хуках (= корень extension'а).
- **`resolve_hook_paths.py` / `${PYTHON}`-shim выброшен** — интерпретатор зовётся напрямую.
- **Никакого merge в `settings.json`** — рантайм сам грузит `hooks/hooks.json`, `commands/`,
  `skills/` из extension'а. Установка = одна команда.
- **Резолвер путей не тронут.** `_project.gigacode_dir()` = `Path(__file__).resolve().parents[1]`
  вычисляет базу относительно файла хука → скрипты сами находят `risk-policy.json` и `skills/`.
  Данные проекта (`ground/`, `pipeline.json`) ищутся отдельно от cwd, который рантайм ставит
  в корень workspace.

## Проверено (2026-08-05, qwen 0.19.5)

- Линкуется без ошибок; trust-промпт перечисляет все 19 скиллов + 4 команды.
- A/B/A по `n_keep`: forge enabled → 22507, disabled → 21896, enabled → 22507. Ровно +611
  токенов forge-контента грузятся в промпт только при включённом extension'е (скиллы как
  слэш-команды) — воспроизводимо.
- Хук из extension'а реально исполняется рантаймом (`[TRUSTED_HOOKS] Expanding hook command:
  python3 …/hooks/… ` → success), `${CLAUDE_PLUGIN_ROOT}` разворачивается корректно.
- `destructive-blocker` блокирует на точном payload qwen (`git push -f origin main` → exit 2 + stderr).
- Сессионные хуки (`prompt-guard`, `phase-gate`, `gate-guard`, `state-recorder`,
  `context-injector`) корректно **ноопают** (exit 0) вне forge-пайплайна → глобальная
  установка не мешает обычным сессиям.

## Открытые вопросы (не закрыты)

- **`FORGE.md` как контекст.** `contextFileName` НЕ задан — 84KB в каждую сессию не инжектим;
  FORGE.md лежит как справка. Если нужен авто-контекст — сделать отдельный компактный `QWEN.md`.
- **Жёсткий `permissions.deny`** (hook-независимый слой): в манифесте extension'а поля нет →
  вероятно, останется тонким merge в `settings.json`. Подтвердить на боевом бинаре.
- **GigaCode-нейминг.** На форке возможно `.gigacode/extensions` + `gigacode-extension.json`
  (вместо `.qwen`/`qwen-extension.json`) — проверить на боевом бинаре GigaCode.
- **Дрейф глобальных хуков.** Если те же forge-хуки уже прописаны в `~/.qwen/settings.json`,
  они будут срабатывать ДВАЖДЫ (из settings и из extension). При переходе на extension —
  снять их из глобального `settings.json`.
- **Живой блок через модель** не прогнан: локальная модель падает на лимите контекста
  (`n_ctx 19968 < n_keep`), к хукам отношения не имеет.
