# Установка forge и forge-ui (extension в проекте)

Forge ставится в проект **extension'ом GigaCode**, и поставок две, установки раздельные:

| Поставка | Куда | Что внутри | Команда |
|---|---|---|---|
| **forge** | бэк-репо (Java/Spring) | весь конвейер: спека → дизайн → TDD, `/forge`, `/forge-fix`, `/forge-tech`, `/forge-spec`, `/forge-merge`, `/forge-archive`; MCP `forge-master` | `bash install.sh forge <repo>` |
| **forge-ui** | фронт-репо | фронт-часть стори: макет → UI-код → проверка против макета, `/forge`; MCP `forge-ui` (движок `daiver_*`) | `bash install.sh forge-ui <repo>` |

Одна поставка на репо: хуки control-plane у них общие, и две поставки в одном репо выполняли бы
каждый хук дважды. `install.sh` на второй отказывает и подсказывает, как снять первую.

Боевой рантайм — **GigaCode** (бинарь `gigacode`, базовый каталог `~/.gigacode`). На дев-машине то же
самое проверяется стоковым `qwen` (`~/.qwen`, `FORGE_RUNTIME=qwen`).

## Как это устроено

```
<repo>/.gigacode/extensions/forge/          ← каталог целиком форжевый (forge-ui — свой, рядом не ставится)
    gigacode-extension.json  qwen-extension.json   манифест: имя, версия, MCP-серверы поставки
    hooks/  hooks/hooks.json                        control-plane и его подключение
    skills/<скилл>/…  commands/*.md                 скиллы и слэш-команды поставки
    deploy-local.sh  .forge-profile  .forge-deployed
~/.gigacode/extensions/forge-<repo>-<хеш>/        ← только ССЫЛКА на каталог выше (gigacode extensions link)
```

- **Файлы — в проекте.** GigaCode режет shell к скриптам вне каталога проекта, а гейты forge зовут
  свои скрипты шеллом. Поэтому extension в `~/.gigacode/extensions/` (как в августе) не работал.
- **В рантайме — ссылка.** Рантайм грузит extensions только из `~/.gigacode/extensions` (загрузка из
  каталога проекта в его коде есть, но не вызывается). `gigacode extensions link <каталог>` кладёт туда
  запись `{type: link, source: <каталог>}`, и рантайм читает манифест, хуки, скиллы и команды по пути
  источника.
- **Имя уникально на проект**: `forge-<каталог>-<хеш пути>`. Реестр ссылок общий на пользователя,
  а два extension'а с одним именем рантайм не ставит («already installed»).
- **Включён только здесь.** Установщик выключает extension под `$HOME` (`disable --scope user`) и
  включает для этого workspace (`enable --scope workspace`). С другими forge-проектами машины он
  выключается взаимно: у каждого репо свой `/forge`.
- **Хук молчит вне своего проекта.** Каждый хук идёт через `hooks/_run_hook.py`. Если рабочий каталог
  сессии не внутри проекта extension'а, хук выходит с 0, ничего не делая. Это страховка на случай,
  если правила включения не сработали (см. «Ограничения»).
- **Пути — абсолютные.** Интерпретатор и пути к хукам и MCP-серверам зашиты в манифест и `hooks.json`
  при установке. Проект переехал — `bash .gigacode/extensions/<forge|forge-ui>/deploy-local.sh`.

## 0. Требования

- Python **3.9+** с рабочим `expat` (модуль `xml.etree`). Установщик не берёт «первый python из PATH»
  вслепую: он пробует кандидатов (`python3`, `python`, `py -3`, затем `python3.13…3.9`) и зашивает
  первый ЗДОРОВЫЙ в команду КАЖДОГО хука. Хук, падающий на импорте, отдаёт `exit 1`, и рантайм читает
  это как «возражений нет». Кривой python здесь = молча снятый enforcement.
- `gigacode` с поддержкой extensions в `PATH` (`gigacode extensions --help`: есть `link`, `enable`,
  `disable`, `uninstall`). Нет CLI — установщик соберёт extension и напечатает три команды подключения.
- gigacode запускается в корне проекта (или внутри него). В 26.9 — без флагов: `--experimental-hooks`
  и `-y` убраны, с ними сессия не стартует.

## 1. Установка

```bash
bash install.sh forge    /path/to/back-repo      # бэк-репо
bash install.sh forge-ui /path/to/front-repo     # фронт-репо
```

Что делает `install.sh`:
1. **Снимает старую раскладку** (forge ≤ 0.5: код в `.gigacode/{skills,hooks,commands}`, хуки и MCP —
   в `.gigacode/settings.json`). Без этого старые хуки задвоятся с хуками extension'а, а проектные
   скиллы перекроют скиллы extension'а (рантайм резолвит project > extension). Снимается только
   форж-своё: по исходнику, реестру прошлой установки (`.forge-deployed`) и `tombstones.txt`.
   Самописные скиллы, хуки и команды оператора остаются. `settings.json` бэкапится, чужие хуки и MCP
   в нём остаются.
2. **Собирает поставку по профилю** (`profiles/back.profile` / `profiles/ui.profile`) во временный
   каталог и подменяет им прежний **целиком**. Снятое из репо в проекте не остаётся.
   Локальный `minor-defect-fix/config.json` переживает обновление.
3. **Манифест и `hooks/hooks.json`** (`hooks/forge_extension.py build`). Тексты брифов называют пути
   поставки: `.gigacode/skills/…` → `.gigacode/extensions/<поставка>/skills/…`.
4. **Ссылка в рантайме** и включение только для этого workspace (`forge_extension.py link`).
   `--no-link` — пропустить (CI, ручная установка). `--runtime qwen` или `FORGE_RUNTIME=qwen` — дев-машина.
5. **`.gitignore`** — блок между маркерами:

   ```gitignore
   # >>> forge: рабочие данные пайплайна >>>
   ground/*
   !ground/policy.json
   .gigacode/
   # <<< forge <<<
   ```

   Рабочие данные прогонов (манифесты, evidence, вердикты судей, журналы, архив) — производное,
   в истории это шум и конфликт на каждый merge. `ground/policy.json` оставлен под версионированием —
   это конфигурация проекта. `.gigacode/` — установленный forge: манифест и `hooks.json` в нём держат
   абсолютные пути этой машины, у коллеги они зовут несуществующие файлы. Свои скиллы оператор
   версионирует точечно: `git add -f .gigacode/skills/<своё>`. Блок идемпотентен, строки оператора
   не трогаются, вне git-репо шаг пропускается.

   > Если `ground/` уже попал в историю, `.gitignore` его не уберёт. Снять с учёта, сохранив на диске:
   > `git rm -r --cached ground && git add ground/policy.json`.
6. **`preflight.py`** (advisory).

После установки **перезапусти сессию gigacode** в проекте: рантайм читает extensions на старте.

### Фронт-репо: forge-ui

Из скиллов едут `forgeui` (ветка фронт-части), `pipeline-state`, `config-helper` и скрипты
`feature-pipeline` без её SKILL.md (их зовут хуки); Java-скиллов нет. Команда одна — `/forge`
(запускает `forgeui` напрямую). В манифесте — MCP **`forge-ui`**. `init_pipeline_config.py`
распознаёт `npm|pnpm|yarn` по lock-файлу. Первый `/forge` один раз спрашивает настройки фронта и
пишет `.daiver/config.yaml`.

## 2. Проверка готовности

```bash
python3 /path/to/repo/.gigacode/extensions/forge/hooks/preflight.py --project /path/to/repo
```

- ✅ `exit 0` — можно работать (`layout.mode: extension`).
- ❌ `exit 1` — ENFORCEMENT OFF: хуки не подключены, рядом старая раскладка или вторая поставка, битый
  интерпретатор. Текст ошибки называет команду починки.
- ❌ `exit 2` — конфиг не инициализирован (`ground/policy.json`). Нормально для первого запуска:
  выполни `init_command` из вывода.

Предупреждение «extension не найден среди ссылок рантайма» — ссылка не поставлена:
`bash .gigacode/extensions/<forge|forge-ui>/deploy-local.sh`.

Проверить, что рантайм видит extension: `gigacode extensions list` из корня проекта. Там должно быть
`forge-<repo>-<хеш>` с «Включено (Рабочее пространство): true» и командами `/forge…`.

## 3. Запуск

```bash
gigacode
```

В сессии:

```
/forge <ключ Jira или описание задачи>
```

В бэк-репо `/forge` зовёт `router`. Он классифицирует задачу и уводит в **fix** (минорный дефект),
**full** (новая функциональность) или **tech** (техзадача без изменения поведения). Путь известен
заранее — `/forge-fix` или `/forge-tech` напрямую. Во фронт-репо `/forge` сразу запускает forge-ui.

### Headless — отдельный режим, не дефолт

`gigacode "<задача>"` для пайплайна **не работает** без подготовки, и обе причины — ограничения рантайма:

1. **Инструменты с подтверждением надо разрешить заранее**, иначе субагент фазы не стартует («requires
   user approval but cannot execute in non-interactive mode»). Модель тогда делает работу фазы сама и
   упирается в `inline-phase-guard` (`exit 2`). Флага `-y`/YOLO в 26.9 нет — нужен `--allowed-tools`.
2. **`ask_user_question` в headless не рендерится** — ответить на пользовательские гейты нечем.

Если headless нужен (CI, пакетный прогон) — разрешить инструменты **и** предзаписать всё, что пайплайн
иначе спросит, ДО прогона:

```bash
python3 <project>/.gigacode/extensions/forge/skills/config-helper/scripts/config.py set <key> <value> \
        --skill <skill> --feature <slug>
python3 <project>/.gigacode/extensions/forge/skills/pipeline-state/scripts/record_approval.py \
        --project <project> --key <key> --approved-by user --reason "<кто/почему>"

gigacode --approval-mode auto-edit \
  --allowed-tools "run_shell_command,write_file,edit,read_file,glob,grep_search,agent,skill" \
  "<задача>"
```

Нет предзаписи — `gate-guard` заблокирует продуктивную запись фазы. Так и должно быть: согласие
человека моделью не подделывается.

## 4. Обновление

```bash
bash update.sh /path/to/repo               # git pull этого клона + install.sh той поставки, что стоит
bash update.sh /path/to/repo --no-pull     # из текущего клона
bash update.sh forge-ui /path/to/repo      # поставку назвать явно
```

Поставку `update.sh` определяет сам: по каталогу extension'а, а у старой раскладки — по её профилю, и
`install.sh` её мигрирует. Ссылка в рантайме указывает на каталог в проекте, поэтому после
обновления её не надо ставить заново. Достаточно перезапустить сессию.

## 5. Снятие

```bash
bash uninstall.sh forge /path/to/repo       # или forge-ui; без имени — то, что стоит
bash uninstall.sh forge /path/to/repo --purge-state   # + ground/ и git-refs чекпойнтов refs/forge/*
```

Снимает ссылку в рантайме (`gigacode extensions uninstall <имя>` — у link-установки исходники не
трогаются), каталог extension'а целиком, остатки старой раскладки и блок forge из `.gitignore`.
`minor-defect-fix/config.json` уходит в `.gigacode/minor-defect-fix-config.json.bak`. Своё оператора
в `.gigacode/{skills,commands,hooks}` и `ground/` (без `--purge-state`) остаются.

`deploy.sh` оставлен для совместимости: `deploy.sh <repo>` → `install.sh forge <repo>`,
`deploy.sh --profile ui <repo>` → `install.sh forge-ui <repo>`.

## 6. Остатки на уровне пользователя

Если forge когда-то стоял в `~/.gigacode/` (user-уровень `skills/`, `hooks/`, блок hooks в
`~/.gigacode/settings.json`), эти копии перекрывают extension. Снимает `cleanup-legacy.sh` (по умолчанию
только план):

```bash
bash cleanup-legacy.sh --apply                            # $HOME
bash cleanup-legacy.sh --apply --backup-dir /tmp/forge-bak  # корп-контур: бэкап в доступное место
```

Каталоги `extensions/` он не трогает. Старую раскладку внутри проекта `install.sh` снимает сам.

## 7. Ограничения и корпоративный контур

- **Проект вне `$HOME`.** `disable --scope user` у рантайма означает «выключен под `$HOME`». В
  проектах вне `$HOME` без forge extension остаётся включённым: хуки там молчат (`_run_hook.py`),
  но `/forge` и MCP видны. Установщик об этом предупреждает. Выключить в таком проекте:
  `gigacode extensions disable --scope workspace <имя>`. С forge-проектами это делается само.
- **Проверено на стоковом qwen 0.22.2**: link, включение по workspace, загрузка `hooks/hooks.json`,
  MCP из манифеста, снятие ссылки. На GigaCode не проверялось. Если `gigacode extensions link` или
  `--scope workspace` там ведут себя иначе, установщик скажет, какой шаг не прошёл, и напечатает
  команды для ручного подключения.
- **Нестандартный python**: поставь нужный первым в `PATH` и перезапусти
  `bash .gigacode/extensions/<поставка>/deploy-local.sh`. Без записи: `… deploy-local.sh --check`.
