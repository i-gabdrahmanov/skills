# Задача 018 — Путь `tech`: техзадачи без бизнес-наполнения

## Статус
v0 + v1 реализованы · 2026-10-09 · v2/v3 — не начаты. Боевой прогон v1 ещё не проводился.

### Что сделано (v0 + v1)
- **v0 — реестр плоских веток** `hooks/_branches.py`: шаги, субагентные/гейтовые/билд-шаги,
  RED/design/final-шаги, роли SoD, артефакты, phase-work, docs-таргет. Кортежи `fix-*` убраны
  из update.py, tdd-guard, eval-guard, inline-phase-guard, sod-enforcer, risk_ladder, archive.py,
  pipeline_phases, resolve_phases, _util. Пин — `hooks/test_branches_registry.py`.
- **v1 — ветка `forgetech`**: `skills/forgetech/` (SKILL.md, manifest-steps.json,
  tech-sdd-template.md; скрипты `_techlib.py`, `check_tech_scope.py`, `check_tech_sdd.py`,
  `check_invariants.py`, `tech_plan.py`, `tech_report.py`), хук `hooks/tech-guard.py`, команда
  `/forge-tech`, router (fix | full | tech), risk-policy (phase_approvals, required_decisions,
  gate_cmd_expect), params-registry (`inputs.mode=tech`, `inputs.archetype`), skill-paths,
  `skill_paths.py tech-docs`, evidence `tech-snapshot` (forge_events), защита `snapshots/`
  (state-write-guard), `record_approval.py --bind-file`, согласие `tech-plan-*` — класс с
  цитатой (CONSENT_PREFIXES). Тесты: `test_forgetech.py`, `test_tech_cycle.py`,
  `test_tech-guard.py`, `test_branches_registry.py`. Полный прогон 122/122 + evals 35/35.

### Решения по открытым вопросам (приняты по умолчанию — пересмотреть после боевого прогона)
- **Q1** — v1: `dependency-upgrade` + `config-change` (enum `inputs.archetype`).
- **Q2** — техзадачи приходят готовыми; Jira-фазы нет.
- **Q3** — строгий вариант: утверждение плана R4-пути НЕ покрывает; запись prod-конфига/миграции
  требует отдельного `human-approval` (gate-guard как раньше).
- **Q4** — закрыт (репозиторий артефактов доступен).
- **Q5** — свой парсер YAML (блочный стиль, мультидокументы, on-profile, плейсхолдеры);
  PyYAML не используется — результат не зависит от машины.

### Отличия реализации от дизайна ниже
- Скоуп после согласия держит не state-write-guard, а **привязка согласия к sha256 плана**
  (`bound_sha256`): правка `change-plan.json` мимо `tech_plan.py amend` делает согласие
  недействительным, и tech-guard не пускает запись. Для плана, переписанного на tech-diag
  после согласия, — `tech_plan.py revoke` (гасит устаревшее согласие, чтобы записать новое).
- Утверждение плана `tech-plan-<F>` — класс согласия с цитатой пользователя (`--evidence`),
  как снятие гейта: оно задаёт скоуп записи, самовыписанное согласие = самовольно расширенный скоуп.
- Кроме `config` и `deps` есть класс `build` — объявленные версии в билд-файлах (parent,
  properties, зависимости/плагины pom.xml, libs.versions.toml, gradle.properties). Ключи —
  как печатает `check_invariants.py show`.
- `tests` судит `module_tests.py guard` на tech-verify, а не снимок.
- `src/test/**` вне targets тоже под скоупом: тесты — инвариант техзадачи.
- tech-record не гейтит ADR (на v1 — предложение пользователю для major-апгрейда).

## Проблема
У forge два пути: `full` (фича: BRD → SDD → tech-design → build) и `fix` (дефект: RED → фикс →
дельта спеки). Техзадачи — апгрейд библиотек, правка конфигурации, рефакторинг, сборка/CI — не
ложатся ни в один:
- `full` требует SDD «что строим» в Given-When-Then и раскладку по слоям — у апгрейда нет ни
  пользователя, ни сценариев; судьи и task-plan по слоям — балласт.
- `fix` требует стори, якорь требования в мастер-спеке и RED-тест, воспроизводящий баг —
  у техзадачи нет ни бага, ни требования, которое она правит.
- И главное: обе ветки охраняют `src/main/java` (`tdd-guard`), а техзадача пишет в `pom.xml`,
  `build.gradle`, `libs.versions.toml`, `application*.yml`, Dockerfile, CI — сейчас **никем не
  гейтится** (default `R1`, кроме prod-конфигов и миграций на R4).

## Решение одной строкой
Третий путь `tech` (оркестратор `forgetech`, namespace `forgetech`, шаги `tech-*`) с одним
артефактом `tech-sdd.md` + машиночитаемым `change-plan.json`, архетипами задач и
детерминированными гейтами: **снимок «до» → diff «после» совпадает с заявленным изменением в
точности + инварианты не тронуты + скоуп по утверждённому списку файлов**; типизированные
пробы — только там, где diff не видит цели. LLM-судей на пути нет.

Мировые аналоги, на которые опираемся: Kiro bugfix spec (раздел *Unchanged Behavior*),
OpenSpec (пустая дельта спеки у поведенчески-нейтральных изменений), Spec Kit `refactor`
(«без изменения поведения» как критерий классификации), SAFe enabler story (критерий приёмки —
измеримая проба, а не сценарий пользователя), BMAD Quick Flow (лёгкий путь + правила эскалации).

---

## 1. Классификация (router)

Роутер спрашивает уже не «fix | full», а «fix | full | tech». Опорный вопрос — **меняется ли
наблюдаемое поведение системы** (API, сообщения, данные, ответы пользователю):

| Что меняется | Путь |
|---|---|
| Поведение сломано, надо починить | `fix` |
| Нужно новое/изменённое поведение | `full` |
| Поведение должно остаться прежним, меняется «как устроено» (версии, конфиг, код, сборка) | `tech` |

Сигналы `tech`: issuetype Task/Technical debt/Chore; «обнови/подними версию», «CVE», «EOL»,
«переведи на», «вынеси в конфиг», «рефакторинг», «почисти», «миграция на Java 21», «CI».

Контр-сигналы (→ спросить, не уводить в tech): «и заодно добавить…», изменение публичного
контракта, новая таблица/топик. Конфиг, меняющий поведение по замыслу (лимит, таймаут, флаг
фичи), — это **tech с явным `expected_changes`** (см. §4), а не full: поведение меняется в
заявленных пределах.

`check_tech_scope.py` (аналог `check_fix_scope.py`) — детерминированный скоуп-чек на
`tech-intake`: ключевые слова + issuetype; exit 1 → ESCALATE с вопросом пользователю.

---

## 2. Шаги

```
tech-intake → tech-diag → [approval tech-plan-<F>] → tech-apply → tech-verify → tech-record
                                                      └─ первым действием: снимок «до»
```

| Шаг | Кто | Что делает | Детерминированный гейт (`record_gate`) |
|---|---|---|---|
| `tech-intake` | главный | Jira/описание, скоуп-чек, вопрос про архетип | `check_tech_scope.py` |
| `tech-diag` | субагент | текущее состояние, release notes / migration guide, пишет `tech-sdd.md` + `change-plan.json` | `check_tech_sdd.py` |
| *approval* | человек | утверждение спеки: «Да, делаем» → `record_approval --key tech-plan-<F>` | `phase_approvals` на `tech-apply` |
| `tech-apply` | субагент | **сначала** снимок «до» (`check_invariants.py snapshot`), затем правка строго в пределах `targets` | `check_invariants.py expect` (diff = заявленное) + пробы, если есть + сборка |
| `tech-verify` | субагент | регресс тестов, инварианты против снимка | `module_tests.py compare` + `check_invariants.py compare` |
| `tech-record` | субагент | ADR (если архетип требует и `adr.enabled`), итоговая сводка (§3.3) | `check_adr.py` / `check_tech_sdd.py --final` |

**Почему нет отдельного RED-шага.** В первой редакции был `tech-red`: «пробы падают до
правки». Для части задач он вырождается в тавтологию — смена хоста в конфиге: проба
`clients.x.url == new-host` проверяет лишь, что в файл записали то, что собирались. Работает ли
хост — пайплайн проверить не может (это смоук после выкатки, на пользователе). Реальную
ценность дают две вещи, и обе — свойства снимка, а не отдельного шага:
1. **«Было» сходится с планом.** Снимок фиксирует текущее значение; не равно заявленному
   `from` → план ошибочен (не тот ключ/профиль, задача уже сделана) → FAIL. Это и есть
   «RED»: гейт способен увидеть разницу между «до» и целью.
2. **«Стало» совпадает с заявленным в точности.** Лишняя правка (соседний ключ) или
   недоделка (профиль, перекрытие в `application-dev.yml`, плейсхолдер `${X:old}`) → FAIL.

Снимок — **предусловие правки, а не шаг**: `tech-guard` не пускает первую запись в `targets`,
пока для прогона нет `snapshot.json` с провенансом `check_invariants` на нетронутом дереве
(git-чистота `targets` проверяется в момент снимка). Ровно схема `tdd-guard`, но без лишнего
субагента и закрытия шага.

Нет на этом пути: BRD, SDD, tech-design по слоям, Jira-декомпозиции, судей, eval-плана,
дельты мастер-спеки.

---

## 3. Артефакты

Папка: `<docs>/feature-pipeline/tech/<KEY|slug>/` (новый таргет `skill_paths.py tech-docs`).
Мастер-спеку путь не трогает — поведенческих требований он не меняет (как пустая дельта
OpenSpec). Если всё-таки поменял — это эскалация (§5), а не дельта.

### 3.1. `tech-sdd.md` — для человека

```
# <Заголовок>
**Jira:** KEY-123 · **Архетип:** dependency-upgrade

## 1. Цель и мотивация        — зачем: CVE-…, EOL, требование платформы; ссылка на источник
## 2. Текущее → целевое       — точные версии/ключи/значения; таблица «было | стало»
## 3. Скоуп (targets)          — файлы, которые будут правиться; всё остальное — вне
## 4. Инварианты (Unchanged)   — что ОБЯЗАНО остаться: «SHALL CONTINUE TO …»
## 5. Ожидаемые изменения      — что меняется сознательно, точно: ключ · профиль · было · стало
## 6. Пробы (если нужны)       — только то, чего не видит diff (§4.2)
## 7. Вне репозитория          — что меняется не в коде: helm values, env в k8s, Vault —
                                 forge этого не видит и не проверяет; делает пользователь
## 8. Риски / breaking changes — по release notes (для upgrade обязательно, с источником)
## 9. Откат                    — как вернуть; обратимо ли (для миграций — обязательно)
## 10. <Блок архетипа>         — см. §6
```

### 3.2. `change-plan.json` — для гейтов

Пример — смена хоста (`config-change`), самый частый и самый простой случай:
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
`P1` — не RED-проба, а страховка: старый хост мог остаться в тестовом профиле или в
wiremock-стабе, которые не входят в итоговый конфиг.

Пример — апгрейд (`dependency-upgrade`), где без пробы не обойтись (транзитивы и BOM в diff
`pom.xml` не видны):
```json
{
  "archetype": "dependency-upgrade",
  "targets": ["pom.xml"],
  "expected_changes": [
    {"kind": "deps", "coord": "org.springframework.boot:spring-boot-starter-parent",
     "from": "3.3.5", "to": "3.4.1"},
    {"kind": "deps", "match": "org.springframework*:*"}
  ],
  "probes": [
    {"id": "P1", "kind": "dep-version", "coord": "org.springframework.boot:spring-boot", "expect": "3.4.1"}
  ],
  "invariants": ["tests", "api", "kafka", "db-schema", "config-keys"]
}
```
`expected_changes` с `match` (без `from`/`to`) — разрешённая область побочных изменений
(транзитивы), а не цель; цель всегда задаётся точно.

`check_tech_sdd.py` проверяет: обязательные разделы md; схему json; `targets` — явные
пути, непусты, без R5; у каждой цели `kind`+`to` (и `from`, где значение уже существует);
каждая цель лежит в файлах `targets`; для архетипа заполнен его блок; `invariants` содержат
обязательный минимум архетипа (§6); раздел «Вне репозитория» есть (пусть «нет»).

---

### 3.3. Итоговая сводка (`tech-record`) — `report.md`
Генерируется **скриптом** из снимка и `expect`, не пересказывается моделью:
- цели: было → стало (по каждой);
- **фактический diff внутри `match`-областей — полным списком**. `org.springframework*:*`
  разрешает сотни артефактов; что именно сдвинулось, человек должен видеть, а не верить
  маске;
- циклы `amend`: какие файлы добавлены в скоуп и почему;
- инварианты: что сверено и с каким результатом;
- раздел «Вне репозитория» из спеки — как чек-лист для пользователя.

`check_tech_sdd.py --final` сверяет, что `report.md` содержит все цели и все пути из
фактического diff.

## 4. Снимок, точный diff, пробы, инварианты

### 4.1. Точный diff — `check_invariants.py snapshot | expect | compare`
Основной гейт пути. Одна утилита, три режима над одним снимком:

| Режим | Когда | Проверяет | Exit |
|---|---|---|---|
| `snapshot` | начало `tech-apply`, до первой записи | `targets` чисты в git; снимает классы из §4.3; для каждой цели `from` == текущее значение | 0 / 1 «план не сходится с текущим состоянием» |
| `expect` | конец `tech-apply` | для каждой цели текущее == `to`; в целевых классах нет изменений вне целей и `match`-областей | 0 / 1 |
| `compare` | `tech-verify` | инварианты (§4.3) равны снимку, за вычетом заявленного | 0 / 3 ESCALATE |

Значения конфига сравниваются **итоговые по профилю** (база `application.yml` + профиль +
резолв `${VAR:default}` по дефолту), а не строки файла — иначе правка не в том файле или
перекрытая профилем проходила бы гейт.

Цель, чьё `from` уже равно `to`, — FAIL на `snapshot` («задача уже выполнена или не тот
ключ»). Это заменяет RED: гейт обязан видеть разницу между «до» и целью.

### 4.2. Пробы — `tech_probe.py`
Нужны только там, где diff не видит цели: резолвнутые транзитивы и BOM, использования API по
коду, структура каталогов. Для `config-change` проба не обязательна (хватает §4.1).

Только типизированные пробы из библиотеки — **не произвольный shell** (иначе `--cmd "true"` в
новой обёртке; это урок `gate_cmd_expect`):

| kind | Что проверяет | Как |
|---|---|---|
| `dep-version` | резолвнутая версия артефакта | `mvn dependency:list` / `gradle dependencies`, нормализация |
| `config-key` | значение ключа в профиле | плоский дамп `application*.yml|properties` |
| `pattern-absent` / `pattern-present` | нет/есть использований (javax→jakarta, deprecated API) | regex по глобу |
| `file-absent` / `file-present` | структура (вынесли модуль, удалили файл) | fs |
| `java-release` | `maven.compiler.release` / toolchain | парсинг билд-файла |
| `cve-absent` *(v3)* | сканер не находит CVE | OWASP dependency-check, если доступен офлайн |

Пробы прогоняются внутри `snapshot` (ожидаются красными) и внутри `expect` (зелёными). Проба,
проходящая до правки, ничего не доказывает (аналог «тест не воспроизводит баг») → FAIL
`snapshot`. Для `refactor` целей-значений нет (`expected_changes` пуст), поэтому там пробы —
единственный способ задать цель, и хотя бы одна обязательна.

### 4.3. Инварианты — классы снимка
Снимаются в `snapshot`, сверяются в `compare` (`tech-verify`):

| Класс | Источник | Сравниваем |
|---|---|---|
| `tests` | `module_tests.py snapshot/compare` (есть) | зелёное до → зелёное после |
| `api` | сканер `endpoints.py` (есть) | метод+путь+параметры |
| `kafka` | `kafka.py` (есть) | топики, направление |
| `db-schema` | `db.py` (есть) | таблицы/колонки из миграций |
| `config-keys` | **новый** плоский дамп ключей по профилям | ключ → значение |
| `deps` | **новый** резолв дерева | координаты → версии |

Внутренние классы/компоненты в инварианты **не входят** — рефакторинг их и меняет.

`compare`: любой diff, не покрытый `expected_changes`, → **exit 3 ESCALATE** (не FAIL):
«изменилось то, что не заявлено: <diff>». Это и есть граница «техзадача перестала быть
технической».

---

## 5. Эскалация

Undeclared diff на `tech-verify` → вопрос пользователю (`ask_user_question`):
- **«Это ожидаемо — дописать в спеку»** → возврат на `tech-diag` (переоткрытие), правка
  `expected_changes`, повторное утверждение (новый маркер — старый по хешу плана не подходит,
  §7.3).
- **«Откатить и сделать иначе»** → переоткрытие `tech-apply`.
- **«Это фича/баг»** → стоп, новый прогон в `full`/`fix`.

Молча расширять `expected_changes` модель не может: `change-plan.json` после утверждения пишет
только санкционированный скрипт с проверкой хеша (§7.3).

### 5.1. Донастройка скоупа — `tech_plan.py amend`
Типовой случай апгрейда: diag по release notes нашёл `ClientConfig.java`, а после подъёма
версии компиляция падает ещё в трёх файлах, о которых release notes молчат. `tech-guard`
блокирует их правку — и это правильно: скоуп утверждён человеком. Выход — короткий цикл, а
не откат на `tech-diag`:

1. Субагент `tech-apply` возвращает `status:"needs_scope"` со списком
   `{"path": "...", "reason": "compile: RestTemplateBuilder.setConnectTimeout удалён"}`
   (причина — из вывода компилятора/тестов, не пересказ).
2. Оркестратор: `tech_plan.py amend --feature <F> --add-target <path> --reason "<...>"` —
   дописывает `targets` (и при необходимости `expected_changes`), пересчитывает хеш,
   **гасит** действующий маркер `tech-plan-<F>`.
3. Вопрос пользователю (`ask_user_question`): «Нужно поправить ещё N файлов: <путь — причина>.
   Ок?» → «Да» → новый `record_approval` с новым хешем; «Нет» → ESCALATE (§5).
4. Перезапуск `tech-apply` с того же места. **Снимок не переснимается**: добавленные файлы
   ещё не правились, снимок «до» для них валиден; `amend` отказывает, если добавляемый путь
   уже грязный в git относительно снимка.

Ограничения `amend`: только добавление (сузить скоуп — тоже через `amend`, но удалять
уже правленый путь нельзя); путь R5 — отказ; путь R4 — по правилу §7.2; лимит циклов —
`quality.max_step_reopens`, исчерпан → ESCALATE. Альтернатива «заранее разрешить
`src/main/**/*.java` под адаптацию» отвергнута: скоуп-гейт превращается в формальность, а цикл
стоит одного вопроса.

---

## 6. Архетипы

| Архетип | Блок спеки | Цель задаётся | Обязательные инварианты | Особое |
|---|---|---|---|---|
| `dependency-upgrade` | версии было/стало, ссылки на release notes, breaking changes с решением по каждому | `deps` from→to **+ проба `dep-version`** (транзитивы/BOM) | tests, api, kafka, db-schema | major-апгрейд → ADR на `tech-record` |
| `config-change` | ключ · профиль · было/стало · кто потребитель · что вне репо | `config` from→to по каждому профилю; пробы не нужны (`pattern-absent` старого значения — по желанию) | tests, api, config-keys (кроме заявленных) | prod-профиль = R4-путь (§7.2) |
| `refactor` | что переносим/переименовываем, почему | только пробы `pattern-*` / `file-*` (≥1), `expected_changes` пуст | tests, api, kafka, db-schema, config-keys — **все** | покрытие изменённых файлов не ниже базы |
| `build-infra` | сборка, плагины, Java, CI, Dockerfile | `deps`/`build` from→to + проба `java-release` / `file-*` где нужно | tests, deps (кроме заявленных) | CI-файлы — в targets явно |
| `data-migration` *(v3)* | changeset, обратимость, объём данных | проба `pattern-present` (changeset) + `db-schema` from→to | tests, api | R4 всегда, откат обязателен |

Архетип — обязательное решение `inputs.archetype` (`required_decisions` на `tech-diag`).

---

## 7. Enforcement — что меняется в хуках

### 7.1. Новый хук или ветка в `tdd-guard`: `tech-guard`
На прогоне `forgetech` блок записи (Write/Edit **и** shell-запись — `_shell_targets`) в:
- любой путь из `targets`, пока нет снимка прогона (`snapshot.json` с провенансом
  `check_invariants` и `passed:true`) — снимок снимается на нетронутом дереве;
- **любой путь вне `targets`** на `tech-apply` (кроме `src/test/**` и артефактов в docs).

Снимок пишется в evidence прогона (`ground/statements/forgetech/<F>/`) и под
`state-write-guard`: подделать его записью модели нельзя, переснять после начала правки —
`snapshot` откажет (грязные `targets`).

Это скоуп-гейт: утверждено «правим pom.xml» — правка `application.yml` заблокирована.

### 7.2. R4-пути (prod-конфиг, миграции) через утверждение
Сейчас запись в `application-prod.yml` — R4 с отдельным маркером на каждое действие. На
tech-пути предлагается: approval `tech-plan-<F>` **покрывает R4-пути, явно перечисленные в
`targets`** (без глобов). R5 — никогда. Альтернатива (строже): отдельный маркер на каждый
R4-путь. → Открытый вопрос Q3.

### 7.3. Утверждение привязано к содержимому плана
`record_approval --key tech-plan-<F>` пишет `sha256(change-plan.json)`; `gate-guard` на
`tech-apply` сверяет хеш. Поменял план после утверждения → маркер недействителен.
Легальный способ поменять — только `tech_plan.py amend` (§5.1): он гасит маркер и требует
нового согласия.
(У fix-пути этого нет: там план не определяет скоуп записи, здесь — определяет.)

### 7.4. Регистрация в политике
- `phase_approvals`: `tech-apply` → `tech-plan-{feature}`.
- `required_decisions`: `tech-diag` → `inputs.archetype`.
- `gate_cmd_expect`: `tech-intake`→`check_tech_scope.py`, `tech-diag`→`check_tech_sdd.py`,
  `tech-apply`→`check_invariants.py expect`, `tech-verify`→`check_invariants.py compare` +
  `module_tests.py`. Пробы вызываются изнутри `check_invariants`, отдельного токена не нужно.
- `sod-enforcer` роли: diag=design, apply=dev, verify=test, record=spec;
  `_VERIFY_TO_IMPL["tech-verify"]="tech-apply"`.
- `update.py` артефакты: `tech-diag` → `tech-sdd.md`, `change-plan.json`.
- `state-write-guard`: `change-plan.json` после утверждения — только через
  `tech_plan.py amend` (снимает маркер).

### 7.5. Предусловие: реестр плоских веток
`fix-*` сейчас захардкожен кортежами в ~15 местах: `update.py`, `tdd-guard`, `eval-guard`,
`inline-phase-guard`, `sod-enforcer`, `risk_ladder`, `archive.py`, `pipeline_phases.py`,
`resolve_phases.py`, `skill_paths.py`, `_util.py`… Третья ветка копипастой = ещё 15 кортежей и
гарантированный дрейф (прецедент: «except в хуках прячет NameError» — пропущенное место = тихо
снятый гейт).

**Шаг 0** — вынести в один реестр `skills/pipeline-state/references/branches.json`:
```json
{"forgefix":  {"mode": "fix",  "prefix": "fix-",  "steps": "...", "red_step": "fix-red",
               "final_step": "fix-spec", "docs_target": "fix-docs", "roles": {...}},
 "forgetech": {"mode": "tech", "prefix": "tech-", "...": "..."}}
```
и читать его везде; тест-пин: каждый потребитель покрывает каждую ветку реестра (расширение
`test_phase_enforcement_coverage.py`). Без шага 0 tech-путь не начинать.

---

## 8. Порядок внедрения

| Инкремент | Состав | Критерий готовности |
|---|---|---|
| **v0** | реестр веток (§7.5), fix-путь на нём, без новых веток | 119/119 + evals зелёные, поведение fix не изменилось |
| **v1** | `forgetech` + router + архетипы `config-change`, `dependency-upgrade`; `check_invariants` snapshot/expect/compare с итоговым конфигом по профилям и `deps`; пробы `dep-version`, `pattern-*`; `tech-guard` (снимок-предусловие + скоуп); хеш-привязка утверждения; `tech_plan.py amend`; `report.md` | боевой прогон: смена хоста в dev/ift + апгрейд Spring Boot patch/minor |
| **v2** | `refactor`, `build-infra`; пробы `file-*`, `java-release` | боевой прогон javax→jakarta |
| **v3** | `data-migration`, `cve-absent` | — |

---

## 9. Открытые вопросы

- **Q1. Архетипы v1.** Какие техзадачи реально идут чаще — подтвердить, что upgrade + config.
- **Q2. Jira.** Техзадачи приходят готовым тикетом (как в fix) или их надо ставить (как в full)?
  Дизайн исходит из «приходят готовыми», Jira-фазы нет.
- **Q3. R4-пути.** Утверждение плана покрывает перечисленные prod-конфиги/миграции (§7.2) — или
  отдельный маркер на каждый?
- ~~**Q4. Сеть.**~~ **Закрыт 2026-10-09:** репозиторий артефактов с машины прогона доступен.
  Класс `deps` и проба `dep-version` резолвят дерево штатно (`mvn dependency:list` /
  `gradle dependencies`) — транзитивы и BOM видны. Офлайн-фолбэк (парсинг билд-файла) в v1
  не делаем; недоступность репо на прогоне = FAIL `snapshot` с причиной, а не тихий
  откат на слабую проверку.
- **Q5. YAML без PyYAML.** Плоский дамп конфига под системным python3 3.9: PyYAML на целевой
  машине гарантирован? Иначе — свой минимальный парсер ключей (как `config.py` сканера) и
  ограничение на якоря/мультидокументы.
- **Q6. Предел гарантий.** Diff инвариантов ловит изменения контракта и красные тесты, но не
  изменение runtime-семантики при том же контракте (новый дефолт Jackson при апгрейде Boot).
  Это закрывается только тестами — поэтому для `dependency-upgrade` раздел «breaking changes»
  с решением по каждому пункту обязателен, а его пустота — FAIL `check_tech_sdd`.
