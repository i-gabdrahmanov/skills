---
name: config-helper
description: >
  Скилл-сопроводитель для настройки параметров forge: покрытие тестами, режим автономии и
  порог авто-аппрува (R0–R5), фиче-флаги (TDD, eval-driven, security-review), расположение
  доков (in-repo/separate-repo), Jira/Bitbucket, инструмент миграций, фазы пайплайна и
  политику рисков. Пользователь говорит словами («подними покрытие до 90%», «выключи TDD»,
  «разреши авто-аппрув до R2», «куда класть доки», «включи security review», «настрой Jira»),
  а скилл находит нужный параметр и МЕНЯЕТ ЕГО ТОЛЬКО СКРИПТОМ — с валидацией, бэкапом и
  атомарной записью, никогда не правя JSON руками. Используй этот скилл всегда, когда нужно
  посмотреть или изменить любой параметр конвейера: «настрой параметр», «поменяй конфиг»,
  «измени настройку», «что сейчас стоит в настройках», «включи/выключи <гейт>», «поставь
  порог», «настрой пайплайн под проект».
---

# config-helper — безопасная настройка параметров forge

Все настройки конвейера лежат в трёх файлах, и менять их руками рискованно (сломать enum,
выйти за диапазон, затереть соседний ключ — а от этих значений зависят хуки безопасности).
Этот скилл превращает свободный запрос пользователя в детерминированный вызов скрипта.

## ⚠️ Железное правило

**Ты (модель) НИКОГДА не редактируешь `pipeline.json`, `feature-gates.json` или
`risk-policy.json` через Edit/Write.** Любое изменение — только через `config.py`. Скрипт —
единственный, кто пишет в конфиг: он валидирует значение по реестру, делает бэкап и пишет
атомарно. Твоя задача — понять намерение, выбрать параметр и вызвать скрипт.

## Что где живёт

| Логический файл | Путь | Что внутри |
|---|---|---|
| `pipeline` | `ground/pipeline.json` | quality, autonomy, docs, conventions, jira, bitbucket, delivery, phases_override |
| `gates` | `ground/feature-gates.json` | bool-фиче-флаги рантайма (tdd_enforced, eval_driven_dev, security_review, …) |
| `risk` 🔒 | `.gigacode/hooks/risk-policy.json` | autonomy_auto_max, default_level, destructive_blacklist, agent_caps (advanced, чувствительно) |

Реестр допустимых параметров — `references/params-registry.json`. Скрипт пишет **только**
то, что описано в реестре (fail-closed).

## Алгоритм работы

1. **Найди параметр.** Запусти `list` и сопоставь запрос пользователя с `id` по полям
   `title` / `description` / `aliases`. Команды (полные пути — рантайм режет `$()`):
   ```bash
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <project> list
   python3 <project>/.gigacode/skills/config-helper/scripts/config.py --project <project> list --category quality --json
   ```
   `--project` можно опустить — скрипт сам возьмёт git toplevel / cwd.

2. **Покажи «было → станет».** Перед записью получи текущее значение и озвучь пользователю,
   что меняется:
   ```bash
   python3 .../config.py get quality.coverage_threshold
   ```

3. **Подтверждение.** Для `sensitive`-параметров (помечены 🔒: risk-policy, autonomy.auto_max_risk),
   для правок `risk` и для изменения `phase` — **спроси явное подтверждение пользователя**, прежде
   чем писать. Для обычных параметров достаточно показать «было → станет».

4. **Примени.** Сначала можно прогнать `--dry-run`, затем записать:
   ```bash
   python3 .../config.py set quality.coverage_threshold 0.9 --dry-run
   python3 .../config.py set quality.coverage_threshold 0.9
   ```
   Для sensitive добавляй `--confirm` (только после согласия пользователя):
   ```bash
   python3 .../config.py set risk.autonomy_auto_max R2 --confirm
   ```

5. **Если запрос неоднозначен** (несколько кандидатов-параметров) — уточни у пользователя,
   какой именно. Не угадывай.

## Подкоманды config.py

| Команда | Назначение |
|---|---|
| `list [--category C] [--file pipeline\|gates\|risk] [--json]` | каталог: id, текущее значение, источник (file/default), диапазон/enum, 🔒 |
| `get <id>` | текущее значение + default + источник |
| `set <id> <value> [--dry-run] [--confirm]` | валидирует, бэкапит, пишет атомарно; печатает было→стало |
| `phase <enable\|disable\|add> <phase-id> [--enabled-by EXPR] [--skill S] [--gates G...] [--desc D]` | мержит фазу в `phases_override` |
| `risk <list-add\|list-remove> <key> <pattern> --confirm` | правка списков risk-policy (destructive_blacklist и др.) |
| `risk cap-set <agent-regex> <R-level> --confirm` | separation-of-duties: кап риска по типу агента |
| `validate [--strict] [--json]` | проверка конфига на ЧТЕНИЕ: типы/диапазоны/enum + кросс-проверки |

Exit-коды: `0` ок · `1` валидация/блок (sensitive без `--confirm`; `validate` нашёл ошибку) ·
`2` ошибка аргументов · `3` файл/параметр не найден.

### validate — конфиг проверяется на чтение, а не только на запись

`set` валидирует одно значение при записи. Но скиллы читают `pipeline.json` **напрямую** с
фолбэком `{}`, и опечатка вроде `coverage_threshold: "0.8"` (строкой вместо числа) утекает
вглубь незамеченной. `validate` закрывает это: переиспользует тот же `params-registry.json` и
проверяет **уже лежащие в файле** значения:

- **типы/диапазоны/enum** известных параметров — ловит именно рассинхрон типа (строка там, где
  ждём число; значение вне диапазона; не из enum). Незаданные параметры берут валидный default —
  их не трогаем.
- **кросс-проверка JaCoCo**: если coverage-гейт активен (`quality.eval_enabled` + `coverage_threshold>0`),
  а `quality.jacoco_configured=false` — предупреждение: coverage в `--strict` будет FAIL без отчёта.

По умолчанию ошибки типов валят (`exit 1`), предупреждения только печатаются (`exit 0`).
`--strict` делает предупреждения тоже блокирующими — для использования как preflight-гейт.

```bash
python3 .../config.py validate            # дружелюбный вывод
python3 .../config.py validate --json     # для автоматики
python3 .../config.py validate --strict   # preflight: варнинги = ошибки
```

## Поведение при отсутствии файла

- `feature-gates.json` нет → `set` создаёт его со всеми дефолтами и применяет изменение.
- `pipeline.json` нет → `set` просит сначала запустить `init_pipeline_config.py` (exit 3).
- `risk-policy.json` нет → не создаётся автоматически (exit 3).

## Примеры маппинга запроса → команды

| Пользователь сказал | Команда |
|---|---|
| «подними покрытие до 90%» | `set quality.coverage_threshold 0.9` |
| «выключи TDD» | `set tdd_enforced false` |
| «включи security review» | `set security_review true` |
| «разреши авто-аппрув до R2» 🔒 | `set autonomy.auto_max_risk R2 --confirm` (после подтверждения) |
| «доки клади в отдельный репо /abs/spec» | `set docs.mode separate-repo` + `set docs.repo_path /abs/spec` |
| «настрой Jira, ключ NPF» | `set jira.enabled true` + `set jira.project_key NPF` |
| «отключи фазу tdd в пайплайне» 🔒 | `phase disable 04-tdd` (после подтверждения) |
| «запрети команду DROP SCHEMA» 🔒 | `risk list-add destructive_blacklist "DROP SCHEMA" --confirm` |
| «проверь, нет ли ошибок в конфиге» | `validate` |
| «JaCoCo подключён?» / «почему coverage падает» | `validate` (покажет кросс-проверку JaCoCo) |

## Тесты

`python3 scripts/test_config.py` — позитивные и негативные кейсы (валидация fail-closed,
бэкап, gates-скелет, phase-override, risk-мутации) на временном проекте.
