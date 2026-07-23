# Фаза 05-verify — Verify (полный прогон + покрытие)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** coverage + regression + arch гейты PASS → record_gate → закрой 05-tests

## 8. Фаза 4 — Verify (полный прогон + покрытие)

**🚨 ЧЕРЕЗ agent(). Оркестратор НЕ гоняет тесты и НЕ читает JaCoCo сам.**

Оба шага — явный вызов `agent`:

### 8.1 Тестописатель (добор покрытия)

Контракт: `get_prompt.py 4.4`:
```
agent(
  subagent_type="general-purpose",
  description="Cover gaps for <slug>",
  prompt="<вывод `get_prompt.py 4.4`; подставь: slug, check_coverage отчёт>"
)
```

### 8.2 Тестраннер

Контракт: `get_prompt.py 4.1a` (секция Pre-commit):
```
agent(
  subagent_type="general-purpose",
  description="Run tests + coverage for <slug>",
  prompt="<вывод `get_prompt.py 4.1a` (Pre-commit); подставь: slug>"
)
```

> **Объём прогона — только затронутое, не полный `cleanTest`-сьют.** Гоняй тесты ИЗМЕНЁННЫХ модулей
> и тест-классы фичи (`./gradlew :module:test --tests '*Foo*'`), а не весь проект. `cleanTest` на
> весь репозиторий в проекте с интеграционными тестами (Kafka/внешние сервисы) стирает кэш и роняет
> десятки ЧУЖИХ тестов, требующих внешней инфры — сигнал становится непригоден (на прогоне #3 так
> «упало» 102 несвязанных теста). **Сними baseline pre-existing-падений ДО своих правок** (тот же
> набор тестов на исходном коде) и сравнивай: чужие падения, воспроизводящиеся в baseline, — НЕ твои.
> coverage-judge и так считает покрытие только по изменённым файлам (`git diff`).

### 8.3 Judge-gate coverage (обязательно, перед закрытием `05-tests`)

После тестраннера запусти coverage-judge — он гоняет `check_coverage.py` (JaCoCo) и
сохраняет вердикт в `judges/coverage-judge.json` (имя совпадает с `required_judges`
шага `05-tests`, иначе `update.py` не даст закрыть шаг):
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py coverage <slug> --recheck
```
- **exit 0** — закрой `05-tests` (status completed).
- **exit 1** — покрытие ниже порога → верни тестописателя на доработку (в пределах лимита).
- **exit 3** — лимит ре-итераций исчерпан → **СТОП, спроси пользователя** (§0.6). Не гоняй снова.

coverage-judge также содержит floor **целостности тестов** (блокирует, если ты ослабил СУЩЕСТВУЮЩИЕ
тесты ради зелёного: добавил `@Disabled`/`@Ignore`, поднял `times(N)→times(M)`, выкинул проверки).
Если floor блокирует — **не правь тест/прод-код под зелёное**, разберись с настоящей причиной падения.
coverage исключает из проверки слои, непокрываемые `test_layer` (репозитории/энтити/dto/config при
`service-unit`) — не пытайся «дорисовать» им тесты через `@DataJpaTest` (его блокирует `tdd-guard`).

При fail — верни тестописателя на доработку (лимит §0.6). Закрытие `05-tests` — явной командой
в конце §8.4, после того как **все** verify-гейты (coverage + regression + arch) PASS.

### 8.3b Регресс-гейт затронутых модулей (ОБЯЗАТЕЛЬНО, перед закрытием `05-tests`)

«Успеха нет, пока тесты затронутых модулей не зелёные.» Сверь текущее состояние с baseline (бриф `04-tdd.md` §7.0):
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py regression <slug> --recheck
```
- **exit 0** — регрессий нет (ранее зелёные тесты по-прежнему зелёные). Можно закрывать `05-tests`.
- **exit 1** — **РЕГРЕССИЯ**: ты сломал ранее зелёный тест затронутого модуля. **Не правь тест/прод-код
  ради зелёного и не отрицай** — найди и устрани настоящую причину, затем перезапусти (лимит §0.6).
- **exit 3** — лимит ре-итераций исчерпан → СТОП, спроси пользователя (§0.6).

Пре-существующие/infra-падения (красные и в baseline) гейт НЕ блокирует — только НОВЫЕ регрессии.
Если baseline не снят (`test-baseline.json` нет) — гейт fail-closed (exit 2): вернись в бриф `04-tdd.md` §7.0.

**Второй сервис.** Гейт гоняет `--from-diff` и, кроме baseline-модулей, проверяет модули,
затронутые твоим диффом, которых в baseline НЕ было (тронул второй сервис). Для них «зелёного
ДО кода» нет, поэтому судятся fail-closed: **красный тест ИЛИ «тесты есть, но не прогнались» →
блок** (раньше такое падение молча уходило в new_failures — «затронут другой сервис, его тесты
проигнорированы»). Если гейт заблокировал pre-existing-падение второго сервиса (он и до тебя был
красный) — **включи этот модуль в baseline** (пересними §7.0 с `--modules <mod1>,<mod2>` поверх
task-plan-модулей), тогда его падения классифицируются как pre-existing и блокировать не будут.

### 8.3c Гейт межмодульных зависимостей (ОБЯЗАТЕЛЬНО, перед закрытием `05-tests`)

«Не подключай модуль молча.» На прогоне #3 агент дописал в `build.gradle` зависимость на модуль,
который по правилам проекта подключать нельзя. Гейт ловит **новые межмодульные зависимости**
(Gradle `project(':...')` и Maven `<dependency>` на внутренний модуль в `pom.xml`, в diff
build-файлов) и проверяет их против **архитектурного граунда** проекта
(`docs/system-analysis/architecture-ground.json` — что проект УЖЕ соединяет, эмитится на grounding бриф `01-grounding.md` §4):
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_architecture.py \
    --root "<project>" --pipeline-config "<project>/ground/pipeline.json"
```
- **exit 0** — новых межмодульных зависимостей нет, либо связка уже принята в проекте (модули
  соединять МОЖНО — по правилам архитектуры), либо ребро в allow-list. Продолжай.
- **exit 2** — нарушение: **цикл** между модулями ИЛИ **новая group-связка** `groupA → groupB`,
  которой проект ещё не делает (напр. `service → service`, как подключение УПЗ к task-service).
  **Не вводи новое арх-связывание ради того, чтобы код собрался**: используй существующий
  API-модуль/контракт. Если это осознанное архрешение — внеси ребро в `ground/architecture-policy.json`
  (`module_deps.allowed_new`) или подтверди override (§0.6.1).

Политика `quality.module_dep_policy`: `graph` (дефолт — проверка против архитектурного граунда:
цикл/новая group-связка блокируются, принятые связки проходят) | `deny_new` (блок любой новой
межмодульной зависимости) | `policy` (только `architecture-policy.json: module_deps.forbidden`) | `off`.

### 8.4 Опциональные детерминированные гейты verify (по флагам `pipeline.json`)

Гоняй ПОСЛЕ тестов, до закрытия `05-tests`. Оба по умолчанию `false`; включаются в `pipeline.json`.

- **Архитектура — строгий режим** (`quality.architecture_check: true`) — добавляет `--strict`
  к гейту §8.3c: ArchUnit-lite слои (package_root, чистота домена, запрет entity→service /
  controller→repository) валят и на warning-уровне:
  ```bash
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_architecture.py \
      --root "<project>" --pipeline-config "<project>/ground/pipeline.json" --strict
  ```
- **Тавтологичные тесты** — статический детектор пустых/тавтологичных тестов
  (`assertTrue(true)`, пустое тело, нет ассертов/verify). ВШИТ floor'ом в coverage-judge
  (дефолт ВКЛ; выключение — явное `quality.tautology_check: false`), т.е. форсится гейтом
  `05-tests` автоматически. Ручной прогон — для ранней обратной связи:
  ```bash
  python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_tautological_tests.py \
      --root "<project>"
  ```
`exit 2` любого — blocking: почини нарушения и перезапусти (`--strict` ужесточает warnings).
`exit 0` — зафиксируй verify-гейт через раннер и закрой `05-tests` (только когда coverage +
regression + arch-гейт все PASS; без evidence от `record_gate` update.py шаг не закроет):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/record_gate.py \
    --project <project> --skill feature-pipeline --feature <slug> --step-id 05-tests \
    --cmd "python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py coverage <slug> --recheck"
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 05-tests --status completed
```

---
