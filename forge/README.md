# Forge — PDLC control-plane для Java/Spring

Харнес (обвязка) для реализации фич в Java/Spring через **feature pipeline** в рантайме
**GigaCode** (`gigacode`, база `~/.gigacode`). Этот каталог — **source-of-truth**:
код, доки и тесты форжа живут здесь и отсюда же ставятся в проект extension'ом GigaCode:
`bash install.sh forge <бэк-репо>` или `bash install.sh forge-ui <фронт-репо>` — каталог
`<project>/.gigacode/extensions/<forge|forge-ui>/` плюс ссылка на него в рантайме.

Принцип (PDLC v3.5): **Pipeline > model; hooks = enforcement; skills = guidance**.
Правила качества форсит рантайм (хуки с `exit 2`), а не «добрая воля» модели —
пропустить тесты, выкатить без проверок или сделать рискованное «молча» нельзя.

> **Канон установки — `bash install.sh forge|forge-ui <project>`** (раздельные поставки, одна на
> репо; см. [INSTALL.md](INSTALL.md)). Файлы extension'а лежат В ПРОЕКТЕ (GigaCode режет shell к
> скриптам вне каталога проекта), в `~/.gigacode/extensions/` — только ссылка. Старую раскладку
> (≤ 0.5: `.gigacode/{skills,hooks,commands}` + блок hooks в `settings.json`) `install.sh` снимает
> сам; остатки на user-уровне (`~/.gigacode/skills|hooks`) — `cleanup-legacy.sh`.

## Что внутри

```
forge/
├── hooks/
│   ├── settings.hooks.json   # эталон блока hooks (${PYTHON}, ${PROJECT_ROOT}) → hooks/hooks.json extension'а
│   ├── settings.mcp.json     # эталон MCP-серверов (forge-master, forge-ui) → манифест extension'а
│   ├── forge_extension.py    # сборка манифеста/hooks.json, ссылка в рантайме (link/unlink), check
│   ├── _run_hook.py          # запуск хука только в своём проекте (вне его — молча 0)
│   ├── *.py                  # хук-скрипты + зависимости (_project.py, risk_ladder.py, forge_events.py)
│   ├── risk-policy.json      # deny-политика (R0–R5 ladder)
│   ├── DEPLOY.md             # полный ростер хуков: события, порядок, диагностика
│   ├── test_*.py, tests/     # юнит-тесты control-plane (31 файл)
│   ├── evals/run-evals.py    # eval-набор (поведенческие пины хуков)
│   ├── resolve_hook_paths.py # старая раскладка: снятие форжевых хуков/MCP из .gigacode/settings.json
│   └── run-hook-tests.sh     # юнит-тесты хуков + evals одной командой
├── commands/
│   ├── forge.md            # /forge       → router (классификация fix | full | tech)
│   ├── forge-fix.md        # /forge-fix   → forgefix (минорный дефект, спека правится точечно)
│   ├── forge-tech.md       # /forge-tech  → forgetech (техзадача: апгрейд/конфиг, поведение не меняется)
│   ├── forge-spec.md       # /forge-spec  → требования-мастер: status/diff/merge/remove/check/research
│   ├── forge-merge.md      # /forge-merge → свести дельту с мастером и убрать доки в архив
│   └── forge-archive.md    # /forge-archive → архив доков готовых строек: status/put/list/restore
├── skills/
│   ├── 21 скилл/SKILL.md          # router, feature-pipeline, forgefix, forgetech, forgeui, sdd, …
│   ├── SKILLS-REGISTRY.md         # реестр с owner/validity/evals
│   └── run_all_tests.py           # единый CI-вход: скиллы + хуки + корень
├── forge-ui/               # поставка фронт-репо: /forge фронта (commands/)
├── profiles/               # профили поставки: back (install.sh forge) | ui (install.sh forge-ui)
├── docs/                   # user-guide, troubleshooting, pipeline-*, v2/; daiver/ — спека daiver + образец Pixso
├── tasks/                  # открытые задачи/наблюдения (см. tasks/*.md)
├── install.sh              # поставить forge | forge-ui в <project>/.gigacode/extensions/<kind>/ + ссылка
│                           #   + блок ground/* и .gigacode/ в .gitignore; снимает старую раскладку
├── deploy-local.sh         # кладётся в extension: перепечь манифест/hooks.json и ссылку (переезд проекта)
├── update.sh               # git pull + install.sh той поставки, что стоит
├── uninstall.sh            # снять extension (и остатки старой раскладки) с проекта
├── deploy.sh               # совместимость: → install.sh forge | forge-ui (--profile ui)
├── cleanup-legacy.sh       # снять остатки старых раскладок на уровне пользователя ($HOME)
├── FORGE.md                # архитектура и решения (НЕ авто-контекст)
├── INSTALL.md              # установка, обновление, корп-контур, деинсталляция
├── SKILLS-REGISTRY.md      # реестр скиллов
└── test_cleanup_legacy.py  # пины к cleanup-legacy.sh
```

## Состав

- **Скиллы:** 21 (`router`, `feature-pipeline`, `forgefix`, `forgetech`, `forgeui`, `sdd`, `tech-design`,
  `jira-task-writer`, `java-spring-dev`, `test-writer`, `system-analyst`, `project-grounder`,
  `pipeline-state`, `brd-grounder`, `brd-interview`, `business-requirements`, `defect-analyzer`,
  `bugfix-developer`, `minor-defect-fix`, `config-helper`, `harness-verifier`).
  Полный реестр с owner/validity/evals — [`SKILLS-REGISTRY.md`](SKILLS-REGISTRY.md).
- **Команды:** 6 в бэк-репо (`/forge`, `/forge-fix`, `/forge-tech`, `/forge-spec`, `/forge-merge`,
  `/forge-archive`); во фронт-репо одна — `/forge` (forge-ui, `forge-ui/commands/`).
- **forge-ui (фронт-репо):** у стори бывает бэк-часть, фронт-часть или обе. Фронт-часть строит
  ветка `forgeui` во фронт-репо: `ui-spec.md` → дизайн по макету → сборка → проверка против макета;
  ставится `bash install.sh forge-ui <front-repo>`. Задачи — `docs/forge-ui/`, решения — `tasks/019-forge-ui.md`.
- **Хуки:** 17 (`gate-guard`, `tdd-guard`, `tech-guard`, `ui-guard`, `eval-guard`, `sod-enforcer`, `inline-phase-guard`,
  `state-write-guard`, `pii-boundary`, `destructive-blocker`, `fork-syntax-guard`,
  `grounding-evidence`, `prompt-guard`, `file-journal`, `state-recorder`, `context-injector`,
  `phase-gate`) + `preflight.py` для самопроверки.

## Что именно форсится (R4-классы)

Обычная работа идёт авто. Отдельно стоят действия, которые **снимают enforcement с самого
харнеса** — их нельзя сделать молча, нужен approval-маркер с провенансом `record_approval`:

| Действие | Ключ маркера |
|---|---|
| `override_judge.py` — снять вердикт судьи | `gate-override-<judge>` |
| `rollback.py` — откатить прогон к шагу | `rollback-<feature>-<step>` |
| `update.py --skip-judges` — обойти гейты закрытия шага (утверждение BRD/SDD не снимает) | `skip-judges-<feature>` (тратится на одно закрытие) |
| `config.py repin` — переснять политику идущего прогона | `policy-repin-<feature>` |
| `config.py set` по переключателю enforcement (`quality.tdd`, `coverage_threshold`, `security.*`, …) | `policy-downgrade-<параметр>` |
| `archive.py abandon` / `put --force` — снять прогон с активных | `abandon-<feature>` / `archive-force-<slug>` |
| `config.py risk list-remove` / `cap-set`, `phase disable` обязательной фазы — ослабить политику мимо `set` | `policy-downgrade-risk.<ключ>` / `policy-downgrade-phase.<фаза>` |
| `git reset --hard`, `clean -f`, `checkout .` / `restore .`, `stash drop\|clear` — стереть незакоммиченное целиком | `git-discard` (тратится на одну команду) |
| `check_acceptance` — засчитать критерий приёмки ручной проверкой вместо теста | `acceptance-<ID>` |
| утвердить план техзадачи (tech-путь): согласие задаёт скоуп записи и привязано к sha256 `change-plan.json` (`--bind-file`) | `tech-plan-<feature>` (гасится `tech_plan.py amend`/`revoke`) |

Для этих классов `record_approval.py` требует **`--evidence "<дословный ответ
пользователя>"`**. Цитата обязана быть СОГЛАСИЕМ на это действие: она берётся из ПОСЛЕДНЕЙ
реплики пользователя (ответ в чате или в `ask_user_question`), содержит слово согласия
(«да, …», «согласен», «подтверждаю», «разрешаю», «ок, …», «давай») без отрицания рядом и не
является вопросом. Постановка задачи, старые реплики, отказ («никогда не откатывай…»), свои
реплики модели и вывод её команд согласием не считаются. Согласия этих классов пишутся только
одиночным вызовом — `--batch` с ними хук не пропускает. Approval'ы ПЛАНА (`fix-plan-*`,
`jira-plan-*`, `<doc>-approved-*`) цитаты не требуют — они двигают прогон вперёд, а не
убирают защиту. Создание Jira-задач (R3) ждёт маркер `jira-create`
(`risk-policy.json: kind_approval`), а не общий `security-review`.

ФАКТЫ о проекте под гейт не попадают: `quality.build_command`, `test_command`,
`jacoco_configured`, `test_layer` настраиваются свободно — это работа `config-helper`.

Подробно: [`FORGE.md`](FORGE.md) §Approval markers, [`docs/approval-markers.md`](docs/approval-markers.md).

## Тесты

```bash
python3 skills/run_all_tests.py          # весь набор: скиллы + хуки + корень (119 файлов тестов)
python3 skills/run_all_tests.py --skill hooks   # только control-plane (31)
bash hooks/run-hook-tests.sh             # юнит-тесты хуков + eval-набор
```
Пол интерпретатора — **Python 3.9** (`hooks/test_python_floor.py` держит его кодом: всё дерево
парсится под 3.9, PEP 604 в аннотациях требует `from __future__ import annotations`). Пол поднят
намеренно НЕ будет: хук, падающий на импорте, отдаёт `exit 1`, а рантайм читает это как
«возражений нет» — то есть молча снятый enforcement на корпоративных машинах с 3.9.

## Установка

Полное руководство — [`INSTALL.md`](INSTALL.md). Короткий путь:

```bash
# 1. поставить в проект (бэк-репо — forge, фронт-репо — forge-ui; старую раскладку снимет сам):
bash install.sh forge /path/to/back-repo

# 2. проверить готовность (ENFORCEMENT ON?):
python3 /path/to/back-repo/.gigacode/extensions/forge/hooks/preflight.py --project /path/to/back-repo
# ✅ exit 0 — можно работать
# ❌ exit 1 — ENFORCEMENT OFF, текст ошибки называет починку

# 3. запустить рантайм — ИНТЕРАКТИВНО, в корне проекта (extensions читаются на старте):
gigacode
```

Дальше в сессии — команда `/forge <ключ Jira или описание>`: `router` классифицирует задачу и
уводит в fix / full. **Headless для пайплайна не годится** без разрешённых заранее инструментов
(`--allowed-tools` с `agent`, `skill`, `run_shell_command`) и предзаписи решений: иначе рантайм
не даёт выполнить `agent`, фаза уходит inline и упирается в `inline-phase-guard` (см. INSTALL.md §4).
Флаги прежних инструкций не передавайте — в gigacode 26.9 флагов `--experimental-hooks` и `-y`
нет: с ними сессия не стартует (`Unknown arguments`, exit 1).

**Обновление** — `bash update.sh /path/to/repo` (git pull + install.sh той поставки, что стоит).
**Снятие** — `bash uninstall.sh forge|forge-ui /path/to/repo` (ссылка в рантайме + каталог
extension'а; `--purge-state` дополнительно сносит `ground/` и git-refs чекпойнтов).

## Раскладка: extension внутри проекта

- **Файлы — в `<project>/.gigacode/extensions/<forge|forge-ui>/`**, каталог целиком форжевый: install
  подменяет его целиком, uninstall сносит целиком. Co-located `.gigacode/{skills,hooks,commands}`
  остаются оператору.
- **Подключение — манифест и `hooks/hooks.json`**, их собирает `hooks/forge_extension.py` из
  `settings.hooks.json` / `settings.mcp.json`: интерпретатор и пути — абсолютные, без
  `${CLAUDE_PLUGIN_ROOT}`. Каждый хук идёт через `_run_hook.py` (вне своего проекта — молча 0).
- **В рантайме — ссылка** (`gigacode extensions link`) с уникальным на проект именем; включение —
  только для этого workspace, с другими forge-проектами машины — взаимно выключены.
- **Резолвер путей не трогает логику.** `_project.gigacode_dir()` =
  `Path(__file__).resolve().parents[1]` — база относительно файла хука (каталог extension'а), скрипты
  сами находят `risk-policy.json` и `skills/`; подсказки строит `_project.install_rel()`. Данные проекта
  (`ground/`, `policy.json`) ищутся от cwd, который рантайм ставит в корень workspace. Тексты брифов
  при установке переписываются: `.gigacode/skills/…` → `.gigacode/extensions/<kind>/skills/…`.

## Проверено

- `python3 skills/run_all_tests.py` — все зелёные (control-plane + skills + корень; счёт файлов
  растёт — актуальное число печатает сам раннер).
- `bash cleanup-legacy.sh --apply` на user-уровне с extension-остатками — успех,
  операторские скиллы (pptx/pdf/skill-creator) не тронуты.
- `destructive-blocker` блокирует на точном payload qwen (`git push -f origin main` →
  exit 2 + stderr).
- Сессионные хуки (`prompt-guard`, `phase-gate`, `gate-guard`, `state-recorder`,
  `context-injector`) корректно **ноопают** (exit 0) вне forge-пайплайна.
- Extension на стоковом qwen 0.22.2 (2026-10-30): link, включение по workspace и взаимное
  выключение двух forge-проектов, хук через `_run_hook.py` (в своём проекте блокирует, в чужом
  каталоге — 0), MCP из манифеста, миграция старой раскладки, снятие ссылки. На GigaCode — не
  проверялось.
- **Аудит харнеса 2026-09-28** (внешний, два независимых отчёта). Подтверждённые обходы
  закрыты и запинены — подробности в `FORGE.md` §Thrust-10: трейлинг-комментарий снимал
  три R4-гейта из четырёх; `record_approval` не гейтился ничем; `rm`/`touch` манифеста
  выключали все хуки разом; ReDoS в `destructive-blocker` (131.8 с при таймауте хука 40 с)
  гасил блокировщик; `config.py set quality.tdd false` снимал TDD с exit 0; фаза `03-jira`
  не гейтилась вообще. Опровергнуто: `rm -rf /etc/passwd` блокировался и до аудита,
  матчеры уже были на канон-именах.

## Открытые задачи

См. [`tasks/`](tasks/). Текущий бэклог (см. файлы для деталей):

- **004** — инициализация git в каталоге форжа (или адаптация тестов к вложенной git-раскладке:
  git-корень внешний, `forge/` — подкаталог).
- **005** — накладные расходы `grounding-evidence` на каждый `read_file` (наблюдение,
  пренебрежимо на малых проектах).
- **016** — остаток боевого прогона v0.4.5 (2026-10-06): что закрыто и что нет — в файле.
- **Из аудита 2026-09-28, НЕ закрыто** (подтверждено, но вне объёма правок):
  `in_progress` не проставляет никто автоматически, из-за чего мёртв интерлок отката
  (`rollback.py:421`);
  `minor-defect-fix` дублирует триггеры `forgefix` при противоположном контракте доставки;
  `.gigacode/` рядом с репо содержит PII (пути машины, прод-спека) и **уже в git-истории**,
  `.gitignore` его не покрывает.

`FORGE.md` как контекст **не инжектится** (`contextFileName` не задан) — 84 KB в каждую
сессию не нужны. Если потребуется авто-контекст — сделать отдельный компактный `QWEN.md`.
