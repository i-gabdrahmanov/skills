# Forge — нативный extension для qwen-code / GigaCode

PDLC control-plane для Java/Spring, упакованный как **нативный extension** рантайма qwen-code
(форк = GigaCode). Этот каталог — **source-of-truth**: код, доки и тесты форжа живут здесь и
отсюда же ставятся. Прежняя модель — императивный `deploy.sh`, копировавший файлы в
`<target>/.gigacode/` и мерживший блок `hooks` в `settings.json`, — снята.

## Что внутри

```
forgeExt/
├── qwen-extension.json     # манифест (name: forge, version, displayName, description)
├── hooks/
│   ├── hooks.json          # конфиг хуков (event → matcher → command), пути ${CLAUDE_PLUGIN_ROOT}
│   ├── *.py                # хук-скрипты + зависимости (_project.py, risk_ladder.py, forge_events.py)
│   ├── risk-policy.json    # deny-политика
│   ├── DEPLOY.md           # полный ростер хуков: события, порядок, диагностика
│   ├── test_*.py, tests/   # юнит-тесты control-plane
│   ├── evals/run-evals.py  # eval-набор (поведенческие пины хуков)
│   └── run-hook-tests.sh   # юнит-тесты + evals одной командой
├── commands/
│   ├── forge.md            # /forge       → router (классификация fix | lite | full)
│   ├── forge-fix.md        # /forge-fix   → forgefix (минорный дефект, спека правится точечно)
│   ├── forge-lite.md       # /forge-lite  → forgelite (исполнение готовой задачи)
│   ├── forge-spec.md       # /forge-spec  → требования-мастер: status/diff/merge/remove/check
│   └── forge-merge.md      # /forge-merge → ярлык слияния дельты в мастер
├── skills/
│   ├── <20 скиллов>/SKILL.md
│   ├── SKILLS-REGISTRY.md  # реестр скиллов с owner/validity/evals
│   └── run_all_tests.py    # единый CI-вход: скиллы + хуки + корень
├── docs/                   # user-guide, troubleshooting, pipeline-*, v2/ (исторический анализ)
├── FORGE.md                # справочная дока (НЕ авто-контекст — см. ниже)
├── INSTALL.md              # установка, обновление, корп-контур, чистка legacy
├── cleanup-legacy.sh       # снять остатки СТАРОЙ раскладки (deploy.sh), которые перекрывают extension
└── test_cleanup_legacy.py  # пины к нему (план не трогает файлы, операторское не сносится)
```

## Тесты

```bash
python3 skills/run_all_tests.py          # весь набор: скиллы + хуки + корень
python3 skills/run_all_tests.py --skill hooks   # только control-plane
bash hooks/run-hook-tests.sh             # юнит-тесты хуков + eval-набор
```
Требуется Python 3.10+ (скрипты используют PEP 604).

## Установка

Боевой рантайм — **GigaCode** (`gigacode`, база `~/.gigacode`); на дев-машине те же команды
даёт стоковый `qwen`. Подробности и чистка старой раскладки — `INSTALL.md`.

```bash
# локальная разработка (живой симлинк — правки в forgeExt сразу видны):
gigacode extensions link /path/to/forgeExt

# из репозитория:
gigacode extensions install <git-url|owner/repo>

# управление:
gigacode extensions update forge
gigacode extensions disable forge   # временно выключить (все хуки/скиллы гаснут)
gigacode extensions uninstall forge
```

После `link`/`install` — перезапустить сессию рантайма, если extension не виден сразу.

## Ключевые отличия от deploy.sh (что переписано)

- **Пути хуков.** `${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/X.py` →
  `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/X.py`. `${CLAUDE_PLUGIN_ROOT}` — единственная
  переменная, которую рантайм подставляет в file-based хуках (= корень extension'а).
- **`resolve_hook_paths.py` / `${PYTHON}`-shim выброшен** — интерпретатор зовётся напрямую.
  Вместе с ним ушли `deploy.sh`, `deploy-local.sh`, `update.sh`, `uninstall.sh` и эталон
  `settings.hooks.json`: проводка теперь одна — `hooks/hooks.json`.
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
- ~~**Дрейф глобальных хуков.**~~ Закрыто: `bash cleanup-legacy.sh` снимает и задвоенные хуки из
  `settings.json`, и старые копии скиллов/команд, которые перекрывают extension. По умолчанию —
  план; `--apply` переносит форж-своё в `forge-legacy-backup-<TS>/`, операторское не трогает.
- **Живой блок через модель** не прогнан: локальная модель падает на лимите контекста
  (`n_ctx 19968 < n_keep`), к хукам отношения не имеет.
