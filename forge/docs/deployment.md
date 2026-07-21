# Деплой Forge в проект

Как развернуть Forge (control-plane на хуках: risk ladder, evidence bundle, учёт токен-бюджета,
security-хуки + библиотека скиллов) в репозиторий целевого проекта.

## Модель: всё в проекте

Forge разворачивается **внутрь целевого проекта** в каталог `<project>/.gigacode/` и
коммитится в его репозиторий. `hooks/` и `skills/` лежат рядом (co-located) — иначе гейты
не находят `../skills`. Зависимости от домашнего `~/.gigacode` нет: скрипты выводят свой
корень из фактического расположения файла.

```
<target-project>/
├── .gigacode/
│   ├── hooks/              # enforcement-хуки + resolve_hook_paths.py, preflight.py, settings.hooks.json
│   ├── skills/             # библиотека скиллов
│   ├── deploy-local.sh     # in-project фиксер путей (см. ниже)
│   ├── settings.json       # конфиг рантайма с блоком hooks (генерируется)
│   ├── FORGE.md            # доки рядом для справки
│   └── SKILLS-REGISTRY.md
└── ... (код проекта)
```

> **Никогда не копируйте hooks и skills по отдельности вручную.** Если залить скиллы, но не
> влить блок `hooks` в `settings.json`, рантайм стартует с `[HOOK_REGISTRY] 0 hook entries`
> — весь control-plane молчит. Установщик `deploy.sh` исключает этот класс ошибок.
>
> **Ровно так же не удаляйте `.gigacode/` вручную.** Снести файлы, оставив блок `hooks` в
> `settings.json`, — зеркальная поломка: рантайм зовёт удалённые скрипты и падает на каждом
> вызове инструмента. Для снятия есть `uninstall.sh`.

## Скрипты

| Скрипт | Где лежит | Роль |
|---|---|---|
| `deploy.sh` | корень склонированного Forge | **установщик**: копирует Forge в указанный проект и доводит `settings.json` |
| `update.sh` | корень склонированного Forge | **обновлятор**: `git pull` + переустановка в проект (мягко или `--force` через uninstall) |
| `uninstall.sh` | корень склонированного Forge | **деинсталлятор**: снимает обвязку с указанного проекта (зеркало `deploy.sh`) |
| `deploy-local.sh` | внутри `<project>/.gigacode/` (кладёт установщик) | **in-project фиксер**: чинит пути в `settings.json` на месте, без копирования |

### Обновление до свежей версии

```bash
bash update.sh /path/to/target-project              # мягко: git pull + deploy (перезаписывает форж-файлы)
bash update.sh /path/to/target-project --force       # чисто: git pull + uninstall + deploy (снимает и удалённые из репо форж-скиллы)
bash update.sh /path/to/target-project --no-pull     # без git pull — переустановить из текущего клона
bash update.sh /path/to/target-project --dry-run     # показать план
```

Оба режима **не трогают** данные оператора: `ground/`, самописные скиллы/хуки/команды,
`permissions`/`mcpServers` в `settings.json`, бэкапы `*.bak`. Разница: мягкое обновление —
tar-overlay поверх (быстро, но скилл, удалённый из репо, останется сиротой в таргете);
`--force` сначала точечно снимает всё форж-своё через `uninstall.sh`, потом ставит актуальный
набор — так уходят и скиллы, которых больше нет в репо. `--force` НЕ сносит `ground/`
(для этого у `uninstall.sh` есть отдельный `--purge-state`).

---

## Установка: `deploy.sh`

Запускается **из склонированного репозитория Forge**. Целевая папка проекта —
**обязательный аргумент**. Без него ничего не копируется (проектов может быть несколько,
угадывать нельзя).

```bash
# 1. Клонируем Forge в произвольную папку
git clone <forge-repo-url> ~/src/forge
cd ~/src/forge

# 2. Разворачиваем в целевой проект
bash deploy.sh /path/to/target-project
```

Что делает `deploy.sh`:
1. Копирует `hooks/` и `skills/` (co-located) + доки в `<project>/.gigacode/`.
2. Кладёт туда `deploy-local.sh`.
3. Генерирует/обновляет `<project>/.gigacode/settings.json` (merge блока `hooks` + бэкап).
4. Прогоняет `preflight.py` (диагностика, не блокирует).

### Поведение и защиты

- **Без аргумента** → ошибка с подсказкой, `exit 2`, ноль записей.
- **Несуществующая папка / не каталог** → ошибка, выход.
- **Деплой «в себя»** (target == репо Forge) → запрещён.
- **Повторный деплой** (идемпотентно): `hooks/` и `skills/` перезаписываются из исходника
  (они source-managed); `settings.json` сначала уходит в бэкап (см. ниже), затем
  обновляется только блок `hooks` — `permissions`, `mcpServers`, `$version` сохраняются.

---

## Снятие: `uninstall.sh`

Запускается **из склонированного репозитория Forge**, аргументы — те же, что у `deploy.sh`:
целевая папка обязательна, без неё ничего не удаляется.

```bash
cd ~/src/forge
bash uninstall.sh /path/to/target-project                 # снять обвязку
bash uninstall.sh /path/to/target-project --dry-run       # показать план, ничего не делать
bash uninstall.sh /path/to/target-project --purge-state   # + снести рабочие данные
```

Что делает:
1. Снимает блок `hooks` из `<project>/.gigacode/settings.json` (с бэкапом — см. ниже).
2. Отставляет в сторону локальный конфиг оператора → `.gigacode/minor-defect-fix-config.json.bak`.
3. Удаляет то, что положил `deploy.sh`: `hooks/`, `skills/`, `deploy-local.sh`, доки.
4. Удаляет `.gigacode/`, если он опустел.

> **Порядок «сначала `settings.json`, потом файлы» — не косметика.** Если снести `hooks/`
> первым и упасть на середине, в конфиге останется блок `hooks`, зовущий удалённые скрипты, и
> **каждый** вызов инструмента будет падать. Снятый блок при любом обрыве оставляет проект рабочим.

### Что переживает снятие

| Остаётся | Почему |
|---|---|
| `ground/` | рабочие данные пайплайна (BRD/SDD/манифесты/evidence/логи). Сносит только `--purge-state` |
| `settings.json` | остальные секции (`permissions`, `mcpServers`, `$version`) — не наши |
| чужие хуки в `settings.json` | хук оператора вне `.gigacode/hooks/` — не наш enforcement, снимать не вправе |
| `*.bak` | бэкапы, включая первозданный `settings.json.bak` (каким конфиг был ДО установки Forge) |
| `refs/forge/*` | git-чекпойнты отката. Сносит только `--purge-state` |
| `<home>/ai-logs-archive/` | общий архив логов вне проекта, шарится между проектами |

`--purge-state` дополнительно сносит `ground/` и git-refs чекпойнтов (`refs/forge/*`).
Обычные коммиты, ветки и HEAD проекта не затрагиваются. Это необратимо — сначала `--dry-run`.

### Поведение и защиты

- **Без аргумента / несуществующая папка / снятие «с самого Forge»** → ошибка, `exit 2`, ноль записей.
- **Нечего снимать** (нет `.gigacode/`, хуки уже сняты) → `exit 0`, а не ошибка: повторный
  запуск идемпотентен, лишних бэкапов не плодит.
- **Проект переезжал** — записи с устаревшим путём (`.gigacode/hooks/*` мимо текущего корня)
  тоже снимаются: иначе в конфиге остались бы хуки на несуществующие файлы.
- Вернуть обвязку после снятия: `bash deploy.sh /path/to/target-project`.

---

## Обновление путей на месте: `deploy-local.sh`

Лежит внутри проекта (`<project>/.gigacode/deploy-local.sh`). Нужен, когда **сам Forge
копировать не надо**, а в `settings.json` устарели абсолютные пути к хукам — например,
проект переехал в другую папку или был переклонирован. Его же зовёт `preflight.py`, когда
обнаруживает чужие пути.

```bash
# из корня проекта
cd /path/to/target-project
bash .gigacode/deploy-local.sh                  # проект = родитель .gigacode/
bash .gigacode/deploy-local.sh --project /path  # явный корень проекта
bash .gigacode/deploy-local.sh --dry-run        # показать результат, без записи и бэкапа
bash .gigacode/deploy-local.sh --check          # только валидация (exit 0/1)
```

---

## Бэкапы settings.json

`deploy.sh` (через `deploy-local.sh`), сам `deploy-local.sh` и `uninstall.sh` сохраняют старый
`settings.json` **до записи** — конвенция одна на все три:

- нет `settings.json.bak` → создаётся `settings.json.bak` — **первозданный оригинал,
  вечный**, больше не затирается;
- `settings.json.bak` уже есть → текущая версия уходит в
  `settings.json.<YYYYMMDD-HHMMSS>.bak`.

Так первый оригинал сохраняется навсегда, а промежуточные версии копятся таймстемпами —
ничего не теряется. В режимах `--dry-run` / `--check` бэкап не делается.

```bash
ls /path/to/target-project/.gigacode/settings.json*
# settings.json
# settings.json.bak                    ← первозданный оригинал
# settings.json.20260619-105325.bak    ← версия перед последним деплоем
```

---

## Диагностика перед запуском (обязательно)

```bash
python3 /path/to/target-project/.gigacode/hooks/preflight.py --project /path/to/target-project
```

Проверяет: блок `hooks` непустой, скрипты на месте, в `settings.json` нет путей за пределы
проекта, скиллы co-located. `passed: false` с ошибкой `ground/pipeline.json not found`
до инициализации пайплайна — нормально (конфиг проекта создаётся отдельным шагом).
Если в ошибках «обнаружены пути к хукам вне проекта» — запустите `deploy-local.sh`.

---

## ⚠️ Запуск рантайма: флаг `--experimental-hooks`

В форке GigaCode хуки — экспериментальная опция за CLI-флагом. **Без флага** рантайм
стартует с `[HOOK_REGISTRY] 0 hook entries` — control-plane молчит. Флаг задаётся при
запуске бинаря, его **нельзя** прописать в `settings.json`.

```bash
cd /path/to/target-project
gigacode --experimental-hooks -p "<задача>"
# или интерактивно:
gigacode --experimental-hooks
```

---

## Требования

- **Python ≥ 3.10 рекомендован** для скриптов пайплайна (`run_all_tests.py` и часть
  скилл-скриптов рассчитаны на 3.10+). Сам деплой при этом 3.9-совместим:
  `resolve_hook_paths.py`/`doctor.py` используют ленивые аннотации
  (`from __future__ import annotations`), `preflight.py` — без PEP 604; на системном
  `python3` 3.9 `deploy.sh` отработает.

---

## Типовые сценарии

```bash
# Первый деплой в новый проект
cd ~/src/forge && bash deploy.sh ~/work/my-service

# Обновить Forge в проекте до свежей версии репо
cd ~/src/forge && git pull && bash deploy.sh ~/work/my-service

# Проект переехал/переклонирован — починить пути в settings.json без копирования
cd ~/work/my-service && bash .gigacode/deploy-local.sh

# Проверить готовность перед прогоном
python3 ~/work/my-service/.gigacode/hooks/preflight.py --project ~/work/my-service
```
