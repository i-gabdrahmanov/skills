# Шаблон техзадачи (`tech-sdd.md` + `change-plan.json` на шаге `tech-diag`)

Техзадача — **не SDD фичи и не дельта фикса**. Пользователя, сценариев Given-When-Then и
требований мастер-спеки у неё нет: поведение системы обязано остаться прежним. Документ
описывает **целевое состояние и инварианты**: что было → что станет, в каких файлах, что
обязано остаться, как откатить. Гейт — `forgetech/scripts/check_tech_sdd.py`.

Два артефакта в каталоге техзадачи (`skill_paths.py tech-docs --feature <KEY>`), имена —
контракт:

- `tech-sdd.md` — для человека: его показывают пользователю на утверждении;
- `change-plan.json` — для гейтов: по нему снимается снимок, сверяется diff и держится скоуп
  записи (tech-guard).

## Правило 1. «Было» — из инструмента, а не из файла на глаз

Значение в файле и итоговое значение — разные вещи: ключ перекрыт профилем, задан
плейсхолдером `${X:default}`, версия приходит из BOM, а не из `pom.xml`. Поэтому «было» бери
ровно в той форме, в какой его видит гейт:

```
python3 <harness>/skills/forgetech/scripts/check_invariants.py show --project <root> --grep <ключ>
python3 <harness>/skills/forgetech/scripts/check_invariants.py deps --project <root> --grep <group>
```

`show` печатает `config: <модуль>|<профиль>|<ключ> = <итоговое значение>` и
`build: <файл>:<что> = <объявленная версия>`; `deps` — резолвнутое дерево `group:artifact =
версия`. Ошибёшься в «было» — снимок на `tech-apply` откажет («план не сходится с текущим
состоянием»), и план вернётся на доработку.

## Правило 2. Цель — точно, область — маской

- **Цель** (`expected_changes` без `match`): `kind` + ключ + `from` + `to`. Гейт требует, чтобы
  после правки значение было ровно `to`, а до — ровно `from`. `from: null` — значения не было
  (новый ключ), `to: null` — удаление.
- **Область** (`{"kind": "deps", "match": "org.springframework*:*"}`) — разрешённые побочные
  изменения (транзитивы BOM). Это не цель: что именно сдвинулось внутри маски, покажет отчёт.
- Всё, что изменится вне целей и областей, — FAIL на `tech-apply` (лишняя правка) или ESCALATE
  на `tech-verify` (незаявленное изменение контракта).

Виды целей:

| kind | ключ | откуда «было» |
|---|---|---|
| `config` | `key` + `profile` (по умолчанию `default`) + `module` (если модулей несколько) | `show`, строки `config:` |
| `build` | `key` ровно как в `show` (`pom.xml:parent:org.springframework.boot:spring-boot-starter-parent`, `pom.xml:properties.jackson.version`, `gradle/libs.versions.toml:versions.spring-boot`) | `show`, строки `build:` |
| `deps` | `coord` = `group:artifact` | `deps` |

## Правило 3. Скоуп — поимённо

`targets` — явный список файлов, которые будут правиться (без глобов). tech-guard не пустит
правку ничего другого, включая `src/test/**`. Не угадал файл — это не повод расширять список
«на всякий случай»: на `tech-apply` недостающий файл добавляется циклом `needs_scope` →
`tech_plan.py amend` → новое согласие пользователя.

---

## `tech-sdd.md`

```markdown
# <Заголовок задачи>
**Jira:** KEY-123 · **Архетип:** config-change

## 1. Цель и мотивация
Зачем: CVE-…, EOL, переезд сервиса X на новый хост, требование платформы. Ссылка на источник.

## 2. Текущее → целевое
| Что | Модуль/профиль | Было | Стало |
|---|---|---|---|
| clients.x.url | . / dev | https://x-old.dev.local | https://x.dev.local |

## 3. Скоуп (targets)
- src/main/resources/application-dev.yml
- src/main/resources/application-ift.yml

## 4. Инварианты (Unchanged)
- API, Kafka-топики, схема БД SHALL CONTINUE TO совпадать со снимком «до».
- Тесты затронутых модулей SHALL CONTINUE TO проходить.
- Остальные ключи конфигурации во всех профилях SHALL CONTINUE TO не меняться.

## 5. Ожидаемые изменения
Ровно цели из таблицы §2. Побочных изменений нет.

## 6. Пробы
P1 — pattern-absent: старого хоста не осталось нигде в src/ (тестовые профили, стабы).

## 7. Вне репозитория
Значение для prod задаётся в helm values — меняет пользователь. (Нет — так и напиши «нет».)

## 8. Риски / breaking changes
Для config-change — кто потребитель ключа и что сломается при ошибке.
Для dependency-upgrade — ОБЯЗАТЕЛЬНО таблица по release notes, решение по КАЖДОЙ строке и
ссылка на источник:

| Изменение в новой версии | Касается нас? | Решение |
|---|---|---|
| RestTemplateBuilder.setConnectTimeout удалён | да, ClientConfig.java:41 | → connectTimeout() |
| Дефолт graceful shutdown = on | да, влияет на k8s preStop | оставляем; preStop — вне репо |

Источник: https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.4-Release-Notes

## 9. Откат
git revert коммита; миграций нет.
```

## `change-plan.json`

Смена хоста (`config-change`):

```json
{
  "feature_slug": "KEY-123",
  "archetype": "config-change",
  "targets": ["src/main/resources/application-dev.yml", "src/main/resources/application-ift.yml"],
  "expected_changes": [
    {"kind": "config", "key": "clients.x.url", "profile": "dev",
     "from": "https://x-old.dev.local", "to": "https://x.dev.local"},
    {"kind": "config", "key": "clients.x.url", "profile": "ift",
     "from": "https://x-old.ift.local", "to": "https://x.ift.local"}
  ],
  "probes": [
    {"id": "P1", "kind": "pattern-absent", "glob": "src/**", "regex": "x-old\\.(dev|ift)\\.local"}
  ],
  "invariants": ["tests", "api", "kafka", "db-schema", "config-keys"],
  "outside_repo": "значения для prod задаются в helm values — меняет пользователь",
  "rollback": "git revert"
}
```

Апгрейд (`dependency-upgrade`):

```json
{
  "feature_slug": "KEY-777",
  "archetype": "dependency-upgrade",
  "targets": ["pom.xml", "src/main/java/ru/acme/config/ClientConfig.java"],
  "expected_changes": [
    {"kind": "build", "key": "pom.xml:parent:org.springframework.boot:spring-boot-starter-parent",
     "from": "3.3.5", "to": "3.4.1"},
    {"kind": "deps", "coord": "com.fasterxml.jackson.core:jackson-databind",
     "from": "2.17.1", "to": "2.18.2"},
    {"kind": "deps", "match": "org.springframework*:*"},
    {"kind": "deps", "match": "com.fasterxml.jackson*:*"},
    {"kind": "deps", "match": "io.micrometer:*"}
  ],
  "probes": [
    {"id": "P1", "kind": "dep-version", "coord": "org.springframework.boot:spring-boot", "expect": "3.4.1"}
  ],
  "invariants": ["tests", "api", "kafka", "db-schema", "config-keys"],
  "outside_repo": "проверить preStop в k8s под graceful shutdown",
  "rollback": "git revert; миграций нет"
}
```

Поля:

| Поле | Обязательно | Смысл |
|---|---|---|
| `archetype` | да | `dependency-upgrade` \| `config-change`; совпадает с решением `inputs.archetype` |
| `targets` | да | файлы правки, поимённо; новый файл — ещё и в `new_files` |
| `expected_changes` | да | цели (`from`/`to`) и области (`match`) |
| `probes` | нет | только то, чего не видит diff: `pattern-absent`/`pattern-present` (glob+regex), `file-absent`/`file-present` (path), `dep-version` (coord+expect) |
| `invariants` | да | минимум: upgrade — tests, api, kafka, db-schema; config — tests, api, config-keys |
| `outside_repo` | да | что остаётся пользователю вне репозитория (или «нет») |
| `rollback` | да | как откатить |
| `amendments` | — | пишет только `tech_plan.py amend` |

Проба, которая проходит ДО правки, ничего не доказывает — снимок её отвергнет. Для
`config-change` пробы не обязательны: цель и так задана точно.
