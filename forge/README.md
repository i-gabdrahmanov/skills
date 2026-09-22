# Forge — PDLC control-plane для Java/Spring

Харнес (обвязка) для реализации фич в Java/Spring через **feature pipeline** в рантайме
**GigaCode** (`gigacode`, база `~/.gigacode`). Этот каталог — **source-of-truth**:
код, доки и тесты форжа живут здесь и отсюда же раскладываются в целевой Java/Spring-проект
через `deploy.sh` в `<project>/.gigacode/`.

Принцип (PDLC v3.5): **Pipeline > model; hooks = enforcement; skills = guidance**.
Правила качества форсит рантайм (хуки с `exit 2`), а не «добрая воля» модели —
пропустить тесты, выкатить без проверок или сделать рискованное «молча» нельзя.

> **Канон установки — `bash deploy.sh <project>`.** Прежняя модель с
> `qwen/gigacode extensions link|install` снята: манифестов extension'а в репо нет,
> `hooks/hooks.json` с `${CLAUDE_PLUGIN_ROOT}` не поддерживается. Если forge где-то
> остался как extension (user-уровень `~/.gigacode/skills/`, `~/.gigacode/hooks/`,
> блок hooks в `settings.json`) — это ПЕРЕКРЫВАЕТ project-деплой и ломает проводку;
> снимается одной командой `cleanup-legacy.sh` (см. INSTALL.md §1).

## Что внутри

```
forge/
├── hooks/
│   ├── settings.hooks.json   # эталон блока hooks для project-модели (${PYTHON}, ${PROJECT_ROOT})
│   ├── *.py                  # хук-скрипты + зависимости (_project.py, risk_ladder.py, forge_events.py)
│   ├── risk-policy.json      # deny-политика (R0–R5 ladder)
│   ├── DEPLOY.md             # полный ростер хуков: события, порядок, диагностика
│   ├── test_*.py, tests/     # юнит-тесты control-plane (30 файлов)
│   ├── evals/run-evals.py    # eval-набор (поведенческие пины хуков)
│   ├── resolve_hook_paths.py # подстановка ${PYTHON}/${PROJECT_ROOT} в settings.hooks.json
│   └── run-hook-tests.sh     # юнит-тесты хуков + evals одной командой
├── commands/
│   ├── forge.md            # /forge       → router (классификация fix | lite | full)
│   ├── forge-fix.md        # /forge-fix   → forgefix (минорный дефект, спека правится точечно)
│   ├── forge-lite.md       # /forge-lite  → forgelite (исполнение готовой задачи)
│   ├── forge-spec.md       # /forge-spec  → требования-мастер: status/diff/merge/remove/check
│   ├── forge-merge.md      # /forge-merge → свести дельту с мастером и убрать доки в архив
│   └── forge-archive.md    # /forge-archive → архив доков готовых строек: status/put/list/restore
├── skills/
│   ├── 20 скиллов/SKILL.md        # router, feature-pipeline, forgelite, forgefix, sdd, tech-design, …
│   ├── SKILLS-REGISTRY.md         # реестр с owner/validity/evals
│   └── run_all_tests.py           # единый CI-вход: скиллы + хуки + корень
├── docs/                   # user-guide, troubleshooting, pipeline-*, v2/ (исторический анализ)
├── tasks/                  # открытые задачи/наблюдения (см. tasks/*.md)
├── deploy.sh               # развернуть hooks/ + skills/ + команды в <project>/.gigacode/
├── deploy-local.sh         # in-project фиксер: подставляет пути в settings.json
├── update.sh               # обновить деплой (rsync hooks/skills)
├── uninstall.sh            # снять деплой из <project>/.gigacode/
├── cleanup-legacy.sh       # снять остатки ПРЕЖНЕЙ extension-раскладки (если перекрывает)
├── FORGE.md                # архитектура и решения (НЕ авто-контекст)
├── INSTALL.md              # установка, обновление, корп-контур, деинсталляция
├── SKILLS-REGISTRY.md      # реестр скиллов
└── test_cleanup_legacy.py  # пины к cleanup-legacy.sh
```

## Состав

- **Скиллы:** 20 (`router`, `feature-pipeline`, `forgelite`, `forgefix`, `sdd`, `tech-design`,
  `jira-task-writer`, `java-spring-dev`, `test-writer`, `system-analyst`, `project-grounder`,
  `pipeline-state`, `brd-grounder`, `brd-interview`, `business-requirements`, `defect-analyzer`,
  `bugfix-developer`, `minor-defect-fix`, `config-helper`, `harness-verifier`).
  Полный реестр с owner/validity/evals — [`SKILLS-REGISTRY.md`](SKILLS-REGISTRY.md).
- **Команды:** 6 (`/forge`, `/forge-fix`, `/forge-lite`, `/forge-spec`, `/forge-merge`,
  `/forge-archive`).
- **Хуки:** 15 (`gate-guard`, `tdd-guard`, `eval-guard`, `sod-enforcer`, `inline-phase-guard`,
  `state-write-guard`, `pii-boundary`, `destructive-blocker`, `fork-syntax-guard`,
  `grounding-evidence`, `prompt-guard`, `file-journal`, `state-recorder`, `context-injector`,
  `phase-gate`) + `preflight.py` для самопроверки.

## Тесты

```bash
python3 skills/run_all_tests.py          # весь набор: скиллы + хуки + корень (108 файлов тестов)
python3 skills/run_all_tests.py --skill hooks   # только control-plane (30)
bash hooks/run-hook-tests.sh             # юнит-тесты хуков + eval-набор
```
Пол интерпретатора — **Python 3.9** (`hooks/test_python_floor.py` держит его кодом: всё дерево
парсится под 3.9, PEP 604 в аннотациях требует `from __future__ import annotations`). Пол поднят
намеренно НЕ будет: хук, падающий на импорте, отдаёт `exit 1`, а рантайм читает это как
«возражений нет» — то есть молча снятый enforcement на корпоративных машинах с 3.9.

## Установка

Полное руководство — [`INSTALL.md`](INSTALL.md). Короткий путь:

```bash
# 1. снять extension-остатки ПРЕЖНЕЙ раскладки (если forge ставился как extension):
bash cleanup-legacy.sh --apply   # план по умолчанию; --apply переносит в forge-legacy-backup-<TS>/

# 2. развернуть в целевой Java/Spring-проект:
bash deploy.sh /path/to/target-project

# 3. проверить готовность (ENFORCEMENT ON?):
python3 /path/to/target-project/.gigacode/hooks/preflight.py --project /path/to/target-project
# ✅ exit 0 — можно работать
# ❌ exit 1 — ENFORCEMENT OFF, чини деплой/флаг

# 4. запустить рантайм с хуками — ИНТЕРАКТИВНО (форк GigaCode — флаг обязателен):
gigacode --experimental-hooks
```

Дальше в сессии — команда `/forge <ключ Jira или описание>`: `router` классифицирует задачу и
уводит в fix / lite / full. **Headless (`-p`) для пайплайна не годится** без `-y`/YOLO и
предзаписи решений: рантайм не даёт выполнить `agent`, фаза уходит inline и упирается в
`inline-phase-guard` (см. INSTALL.md §4).

**Обновление** — `bash update.sh /path/to/target-project` (или повторный `deploy.sh`).
**Деинсталляция** — `bash uninstall.sh /path/to/target-project` (снимает hooks/ + skills/ + блок
hooks из `settings.json`; `--purge-state` дополнительно сносит `ground/` и git-refs чекпойнтов).

## Ключевые отличия от extension-модели (что переписано)

- **Канон — `settings.hooks.json`** (project-модель), не `hooks/hooks.json` (extension).
  Плейсхолдеры `${PYTHON}` (интерпретатор, `sys.executable`) и `${PROJECT_ROOT}` (абсолютный
  путь проекта) подставляет `resolve_hook_paths.py` при `deploy-local.sh`.
- **`${CLAUDE_PLUGIN_ROOT}` НЕ используется** — manifest'ов extension'а нет,
  рантайм подхватывает hooks/skills/commands из `<project>/.gigacode/`, который `deploy.sh`
  создаёт сам.
- **`deploy.sh` копирует всё одним пакетом** — hooks/ + skills/ co-located +
  команды + `deploy-local.sh` + доки. Исключает класс ошибок «0 hook entries»
  (хуки не подключены, а скиллы разложены) и «skills не рядом» (hooks в одном
  месте, `../skills/` — в другом, гейты ничего не находят).
- **Резолвер путей не трогает логику.** `_project.gigacode_dir()` =
  `Path(__file__).resolve().parents[1]` вычисляет базу относительно файла хука →
  скрипты сами находят `risk-policy.json` и `skills/`. Данные проекта (`ground/`,
  `pipeline.json`) ищутся отдельно от cwd, который рантайм ставит в корень workspace.

## Проверено

- `python3 skills/run_all_tests.py` — 108/108 (control-plane + skills + корень).
- `bash cleanup-legacy.sh --apply` на user-уровне с extension-остатками — успех,
  операторские скиллы (pptx/pdf/skill-creator) не тронуты.
- `destructive-blocker` блокирует на точном payload qwen (`git push -f origin main` →
  exit 2 + stderr).
- Сессионные хуки (`prompt-guard`, `phase-gate`, `gate-guard`, `state-recorder`,
  `context-injector`) корректно **ноопают** (exit 0) вне forge-пайплайна →
  глобальный деплой не мешает обычным сессиям.

## Открытые задачи

См. [`tasks/`](tasks/). Текущий бэклог (см. файлы для деталей):

- **001** — падающий `hooks/tests/test_project_resolver.py::test_find_project_root` в
  source-репо (нет git/build-маркеров; ложно-красный в extension-окружении).
- **004** — инициализация git в каталоге форжа (или адаптация тестов к вложенной git-раскладке:
  git-корень внешний, `forge/` — подкаталог).
- **005** — накладные расходы `grounding-evidence` на каждый `read_file` (наблюдение,
  пренебрежимо на малых проектах).

`FORGE.md` как контекст **не инжектится** (`contextFileName` не задан) — 84 KB в каждую
сессию не нужны. Если потребуется авто-контекст — сделать отдельный компактный `QWEN.md`.
