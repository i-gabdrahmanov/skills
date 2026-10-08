# Approval-маркеры

> **Аудитория:** разработчик форжа + оператор прогона пайплайна.
> **Связанные:** `FORGE.md` (архитектура PDLC), `hooks/DEPLOY.md` (полный ростер хуков),
> `hooks/state-write-guard.py` (deny гард), `hooks/gate-guard.py` (`_approval_valid`),
> `hooks/risk-policy.json` (карта `phase_approvals`/`level_requirements`).

## Что это такое

Approval-маркер — это «человек сказал да» на рисковое решение в прогоне: снять
детерминированный гейт судьи, утвердить план фикса, разрешить доставку, согласовать
изменение политик безопасности. Физически — запись в проектном журнале
`<project>/ground/approvals.jsonl` (новая раскладка) или, для обратной совместимости,
файл `<project>/ground/approvals/<key>.json` (legacy-раскладка). Каждая запись несёт
штамп `produced_by:"record_approval"` — единственный провенанс, который принимает
`gate-guard._approval_valid` при чтении.

Маркер НЕ доказывает согласие сам по себе — это лишь способ **централизованно зафиксировать**
уже полученное «да» оператора. Перед запуском писатель маркера обязан спросить через
`ask_user_question` и получить ответ — молча выписать маркер ради само-разблокировки
прямое нарушение BLOCKER-1.

## Когда нужен

Карта approval-маркеров лежит в `hooks/risk-policy.json`:

* `phase_approvals` — обязательное согласие перед фазой:
  * `fix-red` / `fix-green` → маркер `fix-plan-{feature}` (утверждение плана фикса)
  * `03-jira` → маркер `jira-plan-{feature}` (утверждение состава задач ДО их создания:
    задачи в трекере необратимы, из пайплайна их не удалить)
  * Без маркера фаза блокируется `gate-guard._phase_approval_missing`.
* `level_requirements` — обязательное согласие по уровню риска:
  * `R3` (security-sensitive пути) → `security-review`; создание Jira-задач (тоже R3) —
    свой ключ `jira-create` (`kind_approval` перекрывает ключ уровня)
  * `R4` (override гейтов, `skip-judges`, откат, репин политики) → `human-approval`
  * `R5` (деструктив) → `change-advisory`
* Override гейта судьи (R4-класс) → маркер `gate-override-<judge>` на КАЖДОГО судью
  (`--judge a,b,c` — три маркера; ставится через `record_approval.py` ДО создания override'а).
  Без маркера `override_judge.py` блокирует gate-guard, а вторым слоем — сам скрипт (exit 3,
  в т.ч. в `--batch`). Маркер одноразовый: созданный override его потребляет.
* Обход гейтов закрытия шага (`update.py --skip-judges`, R4-класс) → `skip-judges-<feature>`.
  Маркер одноразовый — тратится на одно закрытие (раньше не тратился, и одно согласие навсегда
  снимало гейты всех шагов фичи). Утверждение BRD/SDD (`<doc>-approved-*`) флаг не снимает.
* Снять прогон с активных (R4-класс: прогон перестаёт числиться живым; у `put --force` ещё и
  удаляются git-чекпойнты, `abandon` их откладывает) → `abandon-<feature>` для `archive.py abandon`,
  `archive-force-<slug>` для `archive.py put --force`. Какой прогон брошен, решает
  пользователь. Маркер одноразовый; вторым слоем его сверяет сам `archive.py` (exit 3).
* Стереть незакоммиченную работу целиком (`git reset --hard`, `clean -f`, `checkout .` /
  `restore .`, `checkout -f`, `switch --discard-changes`, `stash drop|clear`) → `git-discard`.
  Маркер одноразовый — gate-guard тратит его на пропуске команды. Точечный откат своего файла
  (`git checkout -- <файл>`, `git restore <файл>`), `restore --staged`, `clean -n` свободны.
* Понижение порогов харнеса (R4-класс) → `policy-downgrade-<параметр>`. Список
  параметров — `risk-policy.json:quality_downgrade.params` (`quality.tdd`,
  `quality.eval_enabled`, `coverage_threshold`, `max_judge_iterations`,
  `coverage_exclude_globs`, `security.*` и т.д.). ФАКТЫ о проекте в том же namespace
  (`build_command`, `test_command`, `jacoco_configured`) под гейт НЕ попадают.
  Тот же класс — ослабление политики мимо `set`: `config.py risk list-remove <ключ>` →
  `policy-downgrade-risk.<ключ>`, `risk cap-set` → `policy-downgrade-risk.agent_caps`,
  `phase disable` обязательной фазы (или `--enabled-by`, делающий её условной) →
  `policy-downgrade-phase.<фаза>`. `--confirm` этих команд согласием не считается — его
  ставит сама модель. Маркер одноразовый; `risk list-add` и включение фазы свободны.
* Закрытие документа фазы (`00-brd` / `02-sdd`) → `<doc>-approved-<feature>`.

Полный список ключей — в `risk-policy.json` (`phase_approvals`, `level_requirements`,
`quality_downgrade`) и в `_DOC_APPROVAL_STEPS` скрипта `update.py`.

## `--evidence` обязателен для ключей, снимающих enforcement

Сам `record_approval.py` долго не гейтился ничем. DENY-баннер соседнего R4-гейта печатал
готовую к вставке команду — и модель её выполняла, никого не спросив: цепочка «баннер →
маркер → гейт снят» целиком помещалась внутри модели. Скрипт централизует согласие, но
сам по себе его не доказывает.

Поэтому для ключей **`gate-override-*`, `rollback-*`, `skip-judges-*`, `policy-repin-*`,
`policy-downgrade-*`, `acceptance-*`, `abandon-*`, `archive-force-*`, `git-discard`** (список
один на оба слоя — `forge_events.CONSENT_PREFIXES`) обязателен `--evidence "<дословная фраза пользователя>"`:

* `gate-guard.check_record_approval` сверяет цитату с **репликами пользователя** в транскрипте
  сессии (записи `type:"user"`: qwen/gigacode — `parts[].text`, Claude Code — `content`; плюс
  ответы `ask_user_question` — выбранный вариант или введённый текст, без заголовка вопроса).
  Весь транскрипт целиком для сверки не годится: рантайм пишет запись модели с текущим
  `functionCall` и его аргументами ДО PreToolUse, и подстрока находила выдуманную цитату в
  самой команде `record_approval`. Свои реплики модели и вывод её команд (`echo` в
  tool_result) тоже не считаются. Транскрипт субагента не годится: его реплика `user` —
  постановка задачи от оркестратора; сверка идёт по основной сессии.
* **Цитата обязана быть согласием на ЭТО действие** (боевой прогон v0.4.6: отказ «никогда не
  откатывай прогон автоматически» и постановка задачи проходили как согласие):
  * она берётся из **последней** реплики пользователя — модель спросила, пользователь
    ответил, модель фиксирует; цитата из старой реплики — deny с просьбой спросить сейчас;
  * реплика, начатая отрицанием («Нет. Да, понимаю, но…»), согласием не бывает;
  * в цитате есть слово согласия («да», «согласен», «подтверждаю», «разрешаю», «ок», «давай»,
    «go ahead», для `acceptance-*` — «проверено», «работает»…), перед ним нет отрицания, сразу
    за ним — тоже («да нет», «давай не будем»), и это не вопрос;
  * цитата — целые слова реплики («да-нибудь» из «когда-нибудь» не вырезается).
  Ответ без слова согласия («откатывай») — переспроси: «ответь «да, откатывай», если согласен».
  В `ask_user_question` давай вариант-фразу: «Да, <действие>» (не короче 12 символов).
* Транскрипт недоступен или в нём не распознано ни одной реплики пользователя (формат
  рантайма не тот) → предупреждение и пропуск: `--evidence` всё равно обязателен и уходит в
  журнал под аудит. Запирать единственный аварийный выход наглухо нельзя.
* Второй слой — сам `record_approval.py` (`_check_evidence`), включая `--batch`: та же
  проверка смысла цитаты (без сверки с транскриптом — его скрипт не видит), гейт держится и
  при запуске мимо харнеса. Валидация батча атомарна — одна плохая запись отменяет весь батч.
* **Согласия этих классов батчем не пишутся**: gate-guard читает batch-файл и отказывает, если
  в нём есть такой ключ, — цитату в файле сверить не с чем (через батч маркер `git-discard`
  выписывался с цитатой `zzzzzzzzzzzzzzzzzz`). По одному вызову на ключ. Батч остаётся для
  согласий плана и документов; оператор вне сессии (headless-предзапись) пишет и согласия.
* Цитата короче 12 символов не принимается: «да»/«ок»/«+» цитатой не являются.

Approval'ы **плана** (`fix-plan-*`, `jira-plan-*`, `<doc>-approved-*`) цитаты не требуют:
они двигают прогон вперёд, а не убирают защиту.

## Легитимные способы создания

> **Любой другой путь — обход провенанса.** Этот список полный; всё, что в него не
> входит, либо отказывается в `state-write-guard`, либо игнорируется в `gate-guard`.

### 1. Одиночное согласие — `record_approval.py`

```bash
python3 <harness>/skills/pipeline-state/scripts/record_approval.py \
    --project <repo-root> \
    --key <phase-or-judge-key> \
    [--kind approval|gate-override-<judge>|human-approval|doc-approved|...] \
    --approver <name> \
    --reason "<объяснение для аудита>" \
    [--evidence "<дословная цитата пользователя | sha256 | ticket | url>"]
```

Примеры:

```bash
# снять гейт по subagent-origin для конкретной фичи
record_approval.py --project . \
    --key gate-override-subagent-origin \
    --reason "agent() недоступен на этом рантайме, деградация согласована" \
    --approver user

# утвердить BRD перед фазой 02-design
record_approval.py --project . \
    --key brd-approved-KIDPPRB-9254 \
    --reason "BRD утверждён PO в Jira KIDPPRB-9254" \
    --evidence "https://jira/.../KIDPPRB-9254" \
    --approver product-owner

# human-approval для R4-доставки
record_approval.py --project . \
    --key human-approval \
    --reason "merge в main согласован release-manager" \
    --approver release-manager
```

Параметры:

* `--project` — корень репозитория проекта (не Jira-key; корень резолвится из git toplevel
  или `cwd`).
* `--key` — нормализованное имя маркера (`safe_component`); обычно `<phase|judge>-<feature>`.
* `--kind` — категория (`approval` по умолчанию; явно ставится для `gate-override-<judge>`,
  `human-approval`, `doc-approved`, `fix-plan`).
* `--approver` (или устаревший алиас `--approved-by`) — кто согласовал; для аудита.
* `--reason` — обязательное объяснение.
* `--evidence` — необязательная ссылка на доказательство.

Exit-коды: `0` — маркер записан (или ничего не изменилось при идемпотентности),
`2` — ошибка аргументов, `1` — прочая ошибка.

### 2. Batch — `record_approval.py --batch`

Для прогонов на 30+ модулях (KIDPPRB-9254 п.6): один вызов = много маркеров, атомарная
запись в `ground/approvals.jsonl` под flock, идемпотентность по ключу (повторный прогон
пропускает уже активные; после `rollback` маркер пишется заново).

```bash
python3 <harness>/skills/pipeline-state/scripts/record_approval.py \
    --project <repo-root> \
    --batch approvals.yaml
```

Формат файла (YAML):

```yaml
approvals:
  - project: KIDPPRB-9254          # Jira-key / feature-slug (для аудита)
    key: fix-plan-KIDPPRB-9254     # обязательное поле
    kind: approval                 # категория
    evidence: "sha256:abc..."      # доказательство
    approver: "tech-lead"          # кто согласовал
    reason: "план фикса согласован на ревью 2026-08-22"
  - project: KIDPPRB-9254
    key: gate-override-red-judge
    kind: gate-override-red-judge
    evidence: "ticket SUP-123"
    approver: "release-manager"
    reason: "RED временно подавлен, см. план"
```

Допускается баре-список `[...]` без обёртки `approvals:`. JSON — то же содержимое в JSON.

Свойства batch:

* **Атомарность:** все записи валидируются ДО первого write, потом одним flock-вызовом
  пишутся в `ground/approvals.jsonl`. Падение на любой записи → ничего не записано.
* **Идемпотентность:** повторный прогон того же батча пропускает ключи, по которым УЖЕ
  есть активное согласие (не revoked). После отзыва (rollback) — запись проходит заново.
* **Валидация:** пустые `key`/`reason` отвергают весь батч целиком (всё или ничего).

### 3. Override гейта судьи — `override_judge.py`

> Это НЕ approval-маркер. Override — отдельный вид артефакта (событие
> `produced_by:"override_judge"` в `events.jsonl`). Но он сам относится к R4-классу и
> требует approval-маркер `gate-override-<judge>` ПЕРЕД созданием.

```bash
python3 <harness>/skills/pipeline-state/scripts/override_judge.py \
    --project <repo-root> \
    --judge <judge-name> \
    --feature <slug> \
    --reason "<объяснение>" \
    [--evidence "<ref>"] \
    [--approver "<who>"]
```

Batch (KIDPPRB-9254 п.6):

```bash
python3 <harness>/skills/pipeline-state/scripts/override_judge.py \
    --project <repo-root> \
    --batch overrides.yaml
```

Формат (YAML):

```yaml
overrides:
  - task: KIDPPRB-9254              # = feature
    gate: quality                   # = judge
    reason: "Quality confirmed by QA"
    evidence: "qa-report-2026-08-22.html"
    approver: "qa-lead"
  - task: KIDPPRB-9254
    gate: red
    reason: "Tests suppressed by supervisor"
    evidence: "ticket SUP-123"
    approver: "release-manager"
```

Допускается баре-список `[...]` без обёртки `overrides:`. Формат `.json` — то же
содержимое в JSON. Атомарность и идемпотентность — те же, что у `record_approval.py`.

## Что произойдёт, если создать маркер вручную

> **By design отказ.** Это не баг, не ошибка конфигурации — это двойной backstop
> BLOCKER-1: deny на запись + фильтрация по провенансу на чтении.

### Слой 1: `hooks/state-write-guard.py`

Любая прямая запись моделью в `ground/approvals/` или `ground/approvals.jsonl`
(через `Write`/`Edit`/`write_file`/`edit`, или через Bash-редирект `>`/`>>`/`tee`/
`dd of=`/`sed -i`/`cp`/`mv`/`truncate`/inline-python `open(...,'w')`) →

```
[state-write-guard] DENY: прямая запись в control-plane-файл 'ground/approvals/<key>.json'
запрещена. State меняется ТОЛЬКО санкционированными скриптами (провенанс форсится update.py):
  • шаги/manifest → pipeline-state/scripts/update.py (--feature ...)
  • gate-result → pipeline-state/scripts/record_gate.py
  • вердикт судьи → feature-pipeline/scripts/run_judge.py (--from-output / --recheck)
  • фазовая машина — не файл: выводится из manifest.json (шаги закрывает update.py)
  • снятие судьи → pipeline-state/scripts/override_judge.py
  • параметры pipeline.json → config-helper/scripts/config.py set
  • approval-маркер → pipeline-state/scripts/record_approval.py (ТОЛЬКО после явного
    «да» пользователя; сначала спроси через ask_user_question).
Прямой Write/echo>/tee/python -c open() сюда — обход провенанса, не делай так.
```

`exit 2`. Тул-вызов прерывается до фактической записи. Это касается и старой раскладки
(`ground/approvals/<key>.json`), и нового журнала (`ground/approvals.jsonl`).

### Слой 2: `hooks/gate-guard._approval_valid`

Если файл каким-то образом обошёл `state-write-guard` (например, был создан ДО установки
гарда, или был скопирован `cp`/`rsync` извне как готовый артефакт) — чтение всё равно
фильтрует:

```python
def _approval_valid(root: Path, key: str) -> bool:
    """approval-маркер засчитывается ТОЛЬКО с провенансом produced_by:"record_approval"…
    Читаем через forge_events.approval(), а не файл approvals/<key>.json напрямую."""
    return FE.approval(root, key) is not None
```

`FE.approval` (в `hooks/forge_events.py`):

1. Сначала читает `ground/approvals.jsonl`. Строка без `produced_by == "record_approval"`
   или с подделанным `produced_by` (`_authentic` сверяет по `PRODUCERS` из
   `forge_events.py`) — игнорируется.
2. Затем fallback на legacy `ground/approvals/<key>.json`. Файл без поля
   `produced_by:"record_approval"` в теле — игнорируется (`_legacy_marker` со
   `produced_by="record_approval"`).

Итог: рукописный `ground/approvals/human-approval.json` с любым содержимым, кроме
правильного провенанса, **НЕ снимает гейт**. Это подтверждено тестами:

* `hooks/test_gate-guard.py::ApprovalMarkerProvenanceTests::test_legacy_marker_without_provenance_blocked`
  — legacy-файл без `produced_by` блокирован, `assertIn("провенанс", stderr.lower())`.
* `::test_jsonl_with_forged_provenance_blocked` — строка с `produced_by != "record_approval"`
  блокирована.
* `::test_jsonl_overrides_legacy_when_both_present` — новая запись в `.jsonl` засчитывается
  даже при наличии мусорного legacy-файла рядом.

## Миграция legacy

> **By design:** legacy `ground/approvals/<key>.json` поддерживается как fallback для
> прогонов, начатых до миграции на журнал. Это путь ЧТЕНИЯ (`FE.approval` ищет сначала
> в `.jsonl`, потом в legacy-файле). Путь ЗАПИСИ — только через `record_approval.py`
> (новые маркеры идут в `.jsonl`).

### Импорт approvals из старой системы

Специального `--import-legacy <dir>` в `record_approval.py` нет (см. KIDPPRB-9254 п.7).
Перенос делается **переизданием** через `--batch`:

1. Собрать approvals из старой системы в YAML/JSON со списком объектов
   `{project, key, kind, evidence, approver, reason}` (те же поля, что в batch-формате).
2. Запустить:

   ```bash
   python3 <harness>/skills/pipeline-state/scripts/record_approval.py \
       --project <repo-root> --batch approvals.yaml
   ```

3. Скрипт запишет маркеры в `ground/approvals.jsonl` со штампом
   `produced_by:"record_approval"`. Старые файлы `ground/approvals/<key>.json` остаются
   на диске как evidence (читатель их больше не использует, если нашёл запись в `.jsonl`;
   см. `test_gate-guard.py::test_jsonl_overrides_legacy_when_both_present`) и могут быть
   удалены вручную после проверки прогона.

> **Важно:** при переиздании нужно сохранить исходные `reason` и `evidence` — это
> аудитный след. Если в старой системе этих полей не было, в новой записи нужно указать
> хотя бы `reason: "<оригинальный источник>"` со ссылкой на старую систему/тикет.

### Отзыв маркера (revoke)

Согласие можно отозвать через `rollback.py` или напрямую вызовом `FE.revoke_approval`
(используется rollback'ом). Отзыв пишется в `.jsonl` отдельной записью
(`produced_by:"rollback"`, kind:`approval-revoked`) и перекрывает любое предыдущее
согласие при чтении. **Файл не удаляется** — это evidence для аудита.

После отзыва батч с тем же ключом снова проходит (идемпотентность по «активному»
состоянию, не по факту записи).

## Сводка

| Действие | Кто делает | Чем | Где живёт результат |
|---|---|---|---|
| Снять гейт судьи (R4) | оператор через модель | `record_approval.py` одиночный | `ground/approvals.jsonl` |
| Утвердить план фикса | оператор | `record_approval.py --batch` | `ground/approvals.jsonl` |
| Утвердить BRD/SDD | PO / lead | `record_approval.py` одиночный (`<doc>-approved-<feature>`) | `ground/approvals.jsonl` |
| Override гейта судьи | оператор | `override_judge.py` (требует предыдущий approval `gate-override-<judge>`) | `ground/statements/<skill>/<feature>/events.jsonl` |
| Создать задачи в Jira (R3) | оператор через модель | `record_approval.py` одиночный (`jira-create`) | `ground/approvals.jsonl` |
| **Запрещено** | — | `Write ground/approvals/<key>.json` (без `produced_by`) | `state-write-guard` DENY + `_approval_valid` REFUSE |
| **Запрещено** | — | `echo ... > ground/approvals.jsonl` | `state-write-guard` DENY (Bash-вектор) |
