# Установка forge (project-модель)

Forge ставится в целевой Java/Spring-проект через `deploy.sh` — один скрипт копирует
hooks/ + skills/ + команды в `<project>/.gigacode/` и доводит `settings.json`.
Боевой рантайм — **GigaCode**: бинарь `gigacode`, базовый каталог `~/.gigacode`.
Проверено на `gigacode v26.5+`.

> **Модель установки — единственная (project).** Манифестов extension'а (`qwen-extension.json`
> / `gigacode-extension.json`) в репо нет, `qwen/gigacode extensions link|install` НЕ
> поддерживается. Если forge где-то остался от прежней extension-раскладки (user-уровень
> `~/.gigacode/skills/`, `~/.gigacode/hooks/`, блок hooks в `settings.json`) — он ПЕРЕКРЫВАЕТ
> project-деплой: задваивает цепочки хуков, подменяет скиллы старыми копиями и уводит
> `/forge` к устаревшей команде. Снимается `cleanup-legacy.sh` (см. §1).

## 0. Требования

- Python **3.9+** в `PATH` с рабочим `expat` (модуль `xml.etree`).
  Установщик не берёт «первый python из PATH» вслепую: он пробует кандидатов
  (`python3`, `python`, `py -3`, затем `python3.13…3.9`) и запекает в `settings.json`
  первый ЗДОРОВЫЙ — версия не ниже пола и живой stdlib. Это не косметика: выбранный
  интерпретатор подставляется как `${PYTHON}` в команду КАЖДОГО хука, а хук, падающий
  на импорте, отдаёт `exit 1` — рантайм читает это как «возражений нет» и выполняет
  вызов. Кривой python здесь = молча снятый enforcement.
  Если годного нет, установщик берёт что есть и печатает `⚠ ВНИМАНИЕ` — читай вывод.
- gigacode, запущенный в корне проекта: хуки он читает из `<project>/.gigacode/settings.json`.
  В 26.9 — без флагов: `--experimental-hooks` (за ним хуки стояли в ранних сборках форка) и
  `-y` из CLI убраны, с ними сессия не стартует (`Unknown arguments`, exit 1). Признак, что
  хуки не загрузились, — `[HOOK_REGISTRY] 0 hook entries` на старте; тогда первым делом §3.

## 1. Убрать остатки прежней extension-раскладки (если была)

Если forge раньше стоял как extension (`qwen extensions link|install`), нужно снять
user-уровень (`~/.gigacode/skills/`, `~/.gigacode/hooks/`), иначе они перекроют project-деплой.

**Одна команда** — `cleanup-legacy.sh` (по умолчанию только план, ничего не меняет):

```bash
bash cleanup-legacy.sh --apply                           # user-уровень ($HOME)
bash cleanup-legacy.sh /path/to/target-project --apply   # + legacy в проекте (если был)
```

Корпоративный контур, где писать в `$HOME` или в проект нельзя:

```bash
bash cleanup-legacy.sh --apply --backup-dir /tmp/forge-bak
```

Перенести ещё `ground/` и git-refs чекпойнтов (рабочие данные прогона):

```bash
bash cleanup-legacy.sh /path/to/target-project --apply --purge-state
```

Затем — **перезапустить сессию** (рантайм кэширует список скиллов на старте) и проверить
`preflight.py` — ошибок «старые копии перекрывают» быть не должно.

## 2. Установка в проект

```bash
bash deploy.sh /path/to/target-project
```

`deploy.sh` делает:
1. Копирует `hooks/` и `skills/` (co-located) в `<target>/.gigacode/`.
2. Удаляет `__pycache__`, `.DS_Store` и локальный `config.json` исходника; конфиг оператора
   таргета (`skills/minor-defect-fix/config.json`) переживает редеплой.
3. Удаляет хуки-сироты (были в старом деплое, но нет в исходнике).
4. Снимает надгробия — скиллы/команды/хуки, которые форж клал раньше и удалил из репо
   (по реестру `.gigacode/.forge-deployed` прошлой установки + `tombstones.txt`).
5. Кладёт `deploy-local.sh`, `FORGE.md`, `SKILLS-REGISTRY.md`.
6. Копирует `commands/*.md`.
7. Пишет реестр установки `.gigacode/.forge-deployed` — по нему следующий апгрейд отличит
   «форж это удалил» от «оператор это добавил».
8. Добавляет в `<target>/.gitignore` блок между маркерами:

   ```gitignore
   # >>> forge: рабочие данные пайплайна >>>
   ground/*
   !ground/policy.json
   .gigacode/
   # <<< forge <<<
   ```

   Рабочие данные прогонов (манифесты, evidence, вердикты судей, журналы, архив) —
   производное, переписываемое на каждом шаге: в истории это шум и конфликт на каждый merge.
   `ground/policy.json` намеренно оставлен под версионированием — это конфигурация проекта
   (где доки, какие гейты, какая критичность), её команда держит в git. Форма `ground/*`, а
   не `ground/`: re-include внутри исключённого каталога git не выполняет.

   `.gigacode/` — задеплоенный харнес, сразу после установки это 286 файлов: в таком
   `git status` не видно собственных изменений проекта. Он генерируется `deploy.sh` из репо
   forge, а `settings.json` внутри держит АБСОЛЮТНЫЕ пути к проекту и интерпретатору — у
   коллеги на другой машине такой файл зовёт несуществующие скрипты, то есть коммит вреднее
   отсутствия файла. Свои co-located скиллы версионируются точечно:
   `git add -f .gigacode/skills/<своё>` (уже отслеживаемые файлы `.gitignore` не трогает).

   Блок идемпотентен (повторный деплой не дублирует), строки оператора не трогаются, вне
   git-репозитория шаг пропускается. `uninstall.sh` снимает ровно этот блок.

   > Если `ground/` уже попал в историю, `.gitignore` его оттуда не уберёт — файлы уже
   > отслеживаются. Снять с учёта, сохранив на диске:
   > `git rm -r --cached ground && git add ground/policy.json`.
9. Запускает `deploy-local.sh` — генерирует `settings.json` из `settings.hooks.json`
   (подставляет `${PYTHON}` = выбранный здоровый интерпретатор и `${PROJECT_ROOT}` =
   абсолютный путь проекта).
10. Прогоняет `preflight.py` (advisory).

## 3. Проверка готовности

```bash
python3 /path/to/target-project/.gigacode/hooks/preflight.py --project /path/to/target-project
```

- ✅ `exit 0` — можно работать.
- ❌ `exit 1` — ENFORCEMENT OFF, проверь `deploy.sh` и что рантайм запущен в корне проекта.
- ❌ `exit 2` — конфиг не инициализирован (`ground/policy.json`; legacy-имя — `pipeline.json`).
  Нормально для первого запуска.

## 4. Запуск рантайма

**Канон — интерактивный запуск:**

```bash
gigacode
```

Дальше в сессии — команда:

```
/forge <ключ Jira или описание задачи>
```

`/forge` зовёт `router`: он классифицирует задачу и уводит в **fix** (минорный дефект)
или **full** (новая функциональность). Путь известен заранее — `/forge-fix` напрямую.

### Headless — отдельный режим, не дефолт

`gigacode "<задача>"` (позиционный промпт; `-p` в 26.9 объявлен устаревшим) для пайплайна
**не работает** без подготовки по двум независимым причинам, и обе — ограничения рантайма:

1. **Инструменты с подтверждением надо разрешить заранее.** Без этого вызов `agent`, `skill`,
   `run_shell_command` получает «requires user approval but cannot execute in non-interactive
   mode»: субагент фазы не стартует, а каждая продуктивная фаза обязана идти субагентом (BR-10).
   Модель уходит делать работу фазы сама и упирается в `inline-phase-guard` (`exit 2`). Флага
   `-y`/YOLO в 26.9 нет — разрешение даётся списком `--allowed-tools`.
2. **`ask_user_question` в headless не рендерится** — пользовательские гейты (BRD, SDD, дизайн,
   критичность, «создавать задачи в Jira?») ответить нечем.

Если headless нужен (CI, пакетный прогон) — разрешить инструменты **и** предзаписать всё, что
пайплайн иначе спросит, ДО прогона:

```bash
# решения (критичность, режим, пути) — в manifest/policy:
python3 <project>/.gigacode/skills/config-helper/scripts/config.py set <key> <value> \
        --skill <skill> --feature <slug>
# согласия человека (BRD/SDD/план) — approval-маркеры с провенансом:
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_approval.py \
        --project <project> --key <key> --approved-by user --reason "<кто/почему>"

gigacode --approval-mode auto-edit \
  --allowed-tools "run_shell_command,write_file,edit,read_file,glob,grep_search,agent,skill" \
  "<задача>"
```

Нет предзаписи → `gate-guard` заблокирует продуктивную запись фазы. Это правильное поведение:
согласие человека не подделывается моделью.

## 5. Обновление

```bash
bash update.sh /path/to/target-project
```

Или повторный `deploy.sh` — он перезапишет копиями из исходника. Сироты в таргете
подчищаются автоматически: хуки — по маске, а скилл/команда, удалённые из репо, — по реестру
прошлой установки и `tombstones.txt`. Раньше они оставались навсегда и оставались
достижимыми: снятая ветка `forgelite` пережила и мягкий апгрейд, и `--force`, а её брифы
продолжали предписывать режим, которого актуальный резолвер уже не знает.

## 6. Деинсталляция

```bash
bash uninstall.sh /path/to/target-project
```

Снимает:
- блок hooks из `<project>/.gigacode/settings.json` (с бэкапом);
- `hooks/`, `skills/`, `commands/`, `deploy-local.sh`, `FORGE.md`, `SKILLS-REGISTRY.md`;
- блок forge из `<project>/.gitignore` (ровно между маркерами; строки оператора остаются);
- `--purge-state` дополнительно сносит `ground/` и git-refs чекпойнтов.

Операторские скиллы/хуки (свопские, не из исходника forge) остаются.

## 7. Корпоративный контур

- Нестандартный python: запусти установщик нужным интерпретатором явно — в `settings.json`
  попадёт `sys.executable` того процесса, что выполнил `resolve_hook_paths.py`. Проверить
  выбор без записи: `bash .gigacode/deploy-local.sh --dry-run`.
- `cleanup-legacy.sh --backup-dir /tmp/forge-bak` — увести бэкап в доступное место
  (в `$HOME`/проект писать нельзя).
- Снять прежнюю extension-раскладку в репо-исходнике негде (manifest'ов нет) — снимается
  только на целевой машине через `cleanup-legacy.sh`.
