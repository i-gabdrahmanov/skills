# Задача 017 — Бэклог боевого прогона v0.4.6

## Статус
Открыта · заведена 2026-10-08

## Контекст

Боевой прогон v0.4.6 (= `4568696`, тег `forge/v0.4.6`): синтетический Java/Spring-проект,
12 треков субагентами (A–H — основной заход, I–L — дозачёт бэклога v0.4.5) и независимый
верификатор (10/10 CONFIRMED). Хуки дёргались payload'ами PreToolUse, живого рантайма
gigacode в прогоне не было. Заявлено ~69 находок (7×P0, 12×P1, ~23×P2, ~27×P3) плюс ~40
позиций дозачёта.

> **Артефакты в git НЕ едут** — сводный отчёт с приложениями A–M лежит у оператора в
> `forge/046 логи/` (`part-00-core.txt` — сводка, `part-01…13` — сырые отчёты треков).

Каждая находка ниже перепроверена здесь по коду и пробой; каждая правка — с тестом,
падающим до неё (проверено откатом правки через stash).

Главный вывод. Отчёт на этот раз точнее прошлого: из 7 «P0» реальны для слабой модели два —
**согласие из любой реплики пользователя** (модель, которой не у кого спросить, цитирует
постановку задачи — это путь наименьшего сопротивления, а не взлом) и **ReDoS**
(heredoc с кодом даёт десятки КБ «путей» в цели — 50 с, таймаут хука = «возражений нет»).
Остальные P0 (`cwd=/`, `mkfifo`, `find -delete`) требуют от модели странного, но закрыты —
это было дёшево. Самую неприятную дыру — `cat > src/main/... <<EOF` в обход RED-гейта —
отчёт поставил P2, хотя это ровно то, что делает слабая модель после отказа write_file.

## Сделано 2026-10-08

### Согласие (P0×2)

| что | где |
|---|---|
| **Согласие собиралось из любой реплики** (E-CONSENT-MINE): отказ «никогда не откатывай прогон автоматически» и постановка задачи проходили сверку — она проверяла, ЧТО фразу писал пользователь, но не что это «да». Теперь: цитата — из ПОСЛЕДНЕЙ реплики пользователя; реплика, начатая отрицанием, не согласие; в цитате слово согласия без отрицания перед ним и сразу за ним, не вопрос; цитата — целые слова | `gate-guard._evidence_problem`, `forge_events.consent_quote_problem` (словарь общий на оба слоя) |
| **Батч выписывал согласие с `zzzzzzzzzzzzzzzzzz`** (E-CONSENT-BATCH): хук пропускал `--batch`, скрипт мерил длину; e2e — `git reset --hard` rc 0. Согласия consent-классов батчем не пишутся: хук читает batch-файл (YAML/JSON + текстовый поиск `key:`) и отказывает; второй слой проверяет смысл цитаты и в батче | `gate-guard.check_record_approval`, `record_approval._check_evidence` |
| Ответ через `ask_user_question` не считался словами пользователя (tool_result) — согласие, данное ровно так, как велят брифы, отбивалось «пользователь этого не говорил». Берётся значение ответа, заголовок вопроса (его пишет модель) — нет. **Находка этой сверки, в отчёте её нет** | `gate-guard._user_texts` |
| Транскрипт субагента: реплика `user` там — постановка от оркестратора. Если рантайм отдаст хуку путь субагента, сверка идёт по `<проект>/chats/<сессия>.jsonl`; плоская форма `{"type":"user","parts":[…]}` распознаётся (иначе — «сверить не с чем», пропуск) | `gate-guard._main_transcript`, `_user_texts` |
| Гонка двух батчей — дубли грантов 12/12: проверка «ключ не активен» и запись под одним замком `ground/approvals.lock` | `record_approval._approvals_lock` |
| `update.py --skip-judges` закрывал 02-sdd без `sdd-approved` (обещал снять судей, gate-result, origin, решения, артефакты — не утверждение документа), а маркер `skip-judges-*` не тратился: одно согласие = вечный обход гейтов закрытия фичи. Утверждение BRD/SDD проверяется всегда, маркер тратится после записи манифеста | `update._apply`, `_check_skip_judges` |
| `consent_required` — без учёта регистра (`GATE-OVERRIDE …` писался без цитаты; гейты такой ключ не спрашивают, обхода не было) | `forge_events.consent_required` |

### ReDoS (P0×2)

| что | где |
|---|---|
| path_risk `(?i).*(…).*` под re.search квадратичен: 32К `a/a/…` → 52 с в gate-guard. Ведущий `.*` срезается при компиляции (ответ тот же), цель — хвост ≤ 4096. 256К команда — 0,75 с всего хука | `risk_ladder._path_re`, `classify` |
| email-паттерн pii (и его safe-двойник) `[…]+@` на 256К без `@` — 50–60 с. Квантификаторы по RFC (64/253/63): 0,03 с | `risk-policy.json:pii_patterns[11]`, `pii_safe_patterns[2]` |

### Целостность control-plane (P0 + P1-кластер)

| что | где |
|---|---|
| Разбор целей записи/удаления вынесен в общий stdlib-only модуль; обёртки (`sudo`, `env`, `timeout`, `nice`, `VAR=x`) снимаются, `sh -c '…'`/`eval` раскрываются на уровень | `hooks/_shell_targets.py` |
| `find … -delete` / `-exec[dir] rm` / `-ok[dir]` / `\| xargs rm`: корни поиска + фильтры имён → задевает ли удаление `ground/`/`.gigacode/` (e2e отчёта: манифест исчез → запись в src/main стала rc 0). `find . -name '*.class' -delete` свободен | `state-write-guard._find_delete_hit` |
| `mkfifo`/`mknod` (FIFO вешал КАЖДЫЙ вызов gate-guard при живом прогоне), `ln -sf /dev/null …/manifest.json`, `curl -o`/`wget -O`/`openssl -out`, `ed`/`ex`, `srm` | `_shell_targets` |
| inline-python: `os.remove/replace/rename`, `shutil.*`, `Path(…).unlink()/rmdir()/touch()`, вложенные литералы (`"…rmtree('ground')"`) | `_shell_targets._PY_WRITE_RE`, `_py_literals` |
| Симлинк на каталог `ground` (`ln -s ground l; echo > l/policy.json`) — цели сверяются и через realpath | `state-write-guard._resolved` |
| `cwd=/`: резолвер без маркеров отдаёт cwd, и `rm -rf /usr` был «уборкой внутри проекта». Корень обязан быть проектом (маркер, не `/`, не дом) | `destructive-blocker._is_real_project` |
| `bash -c 'rm -rf /etc/…'`, `sudo bash -c`, `eval "rm -rf …"`; `find -execdir/-ok/-okdir rm`; `dd of=/dev/sda if=…` (порядок операндов), `dd … of=/dev/null` больше не ложный DENY; `rsync --delete` в каталог вне проекта | `destructive-blocker._segment_tokens`, `_CORE_BLACKLIST`, `risk-policy.json` |

### Гейты фаз и ролей

| что | где |
|---|---|
| **Shell-запись в src/main мимо RED/EDD/SoD** (L-17b): tdd-guard, eval-guard и пути ролей sod-enforcer смотрели только на Write/Edit. Теперь цели shell-записи судятся как Write; tdd-guard и eval-guard стоят и в Bash-цепочке. Попутно: старый tdd-guard, вызванный с Bash-payload, блокировал любое упоминание `src/main/java` (даже `cat`) — подключать его было нельзя | `tdd-guard._check_write`, `eval-guard`, `sod-enforcer`, `settings.hooks.json` |
| `./mvnw` не матчился `\bmvn\b` — сборка Maven-wrapper'ом шла мимо ролей spec/design/jira и inline-guard; константа в двух хуках пинится равенством | `BUILD_CMD_RE` в `sod-enforcer`, `inline-phase-guard` |
| Стаб правкой (`new_string` у Edit) не проверялся | `sod-enforcer` |
| Гейт чтения src/ на 01-grounding: `grep_search` (настоящее имя grep в qwen — 30 вызовов в локальных транскриптах) не было ни в матчере, ни в списках; поиск без `path` (glob по `src/**`, grep по всему проекту) был fail-open | `settings.hooks.json`, `gate-guard.check_phase_gate`, `pipeline_phases._READ_TOOL_NAMES` |
| preflight искал Write-цепочку «по tdd-guard» — после подключения его к Bash отрапортовал бы битый матчер | `preflight._group_matcher_for(without=…)` |

### Стейт и спек-инструменты

| что | где |
|---|---|
| Гонка merge мастера без замка — проигравший терял фичу целиком (10/10). Замок по пути мастера в temp-каталоге (не мусорит в репо доков), атомарная запись; remove/migrate — запись, только если мастер не менялся с чтения | `merge_delta_to_master.master_lock`, `write_master`, `spec_cli._write_if_unchanged` |
| Пустая дельта писала `### REQ-NNN: ` и считалась «слитой»; дубли названий в дельте — отказ ДО записи, состояние `drifted` (держит архивацию) | `merge_delta_to_master.delta_candidates`, `spec_cli._state_of` |
| Один не-UTF8 байт обнулял весь `events.jsonl`/`approvals.jsonl` молча — построчное декодирование; rollback падал на таком журнале даже на `--dry-run` | `forge_events.read_log`, `rollback._journal_scope` |
| Stop-хук молчал на битом манифесте единственного прогона (update.py тот же стейт — rc 4) — теперь блок один раз на сессию | `phase-gate.py` |
| step-id `../../outside` уводил выход шага за каталог прогона, дубли id в init; структурно битый манифест — голый KeyError | `_util.bad_step_id` (init, add_steps), `update._apply` |
| state-recorder писал origin в чужой активный прогон (шага нет ни в одном читаемом манифесте) и создавал фантомный `feature-pipeline/pipeline` на пустом проекте | `state-recorder._owner_run` |

### Конфиг, деплой, CLI

| что | где |
|---|---|
| `deploy.sh:108` безусловным `rm` обнулял `minor-defect-fix/config.json` оператора на КАЖДОМ deploy/update — откладывается и возвращается | `deploy.sh` |
| `config.py set <переключатель> --dry-run` требовал approval (контракт — свободен) | `config.cmd_set` |
| Значение вне словаря строкового параметра принималось (`requirement_kind table-like` → весь спек-контур fail-closed); `nan`/`inf` уезжали в policy.json литералом `NaN` | `config-helper/_util.coerce_and_validate`, `atomic_write(allow_nan=False)` |
| **`set spec.grammar.provenance none` записывал `null`**, и читатель брал дефолт `from-bracket` — явный выбор инвертировался. Находка этой сверки | там же |
| `_live_run` называл в предупреждении первый живой по алфавиту, а не активный для хуков | `config._live_run` |
| preflight: хук оператора через симлинк-алиас корня — «FOREIGN», вердикт ENFORCEMENT OFF; `--project=PATH` анализировал `<cwd>/--project=…` | `preflight._find_foreign_hook_paths`, `resolve_hook_paths --check`, CLI |
| Сырые трейсбеки rc 1 (вне контракта 0/2): check_build (битый/массив/нет плана; плюс опечатка `--task` давала PASS), check_paths (каталог вместо конфига), check_fix_delta (не-UTF8), `override_judge --list` без `--feature`, `init_pipeline_config` при `ground`-файле; артефакт `../outside.md` засчитывался | соответствующие скрипты |
| spec_grammar CLI холодным запуском не видел policy (молча NATIVE), `research` на несуществующем каталоге мастеров — сырой FileNotFoundError | `spec_grammar.load_profile`, `spec_map.build` |

### Доки

DEPLOY.md §ЗАПУСК и деплоимые `router`/`forgefix` SKILL.md велели `gigacode --experimental-hooks`
и `-y` (утечка фикса 016-C1) — переписаны; тест сканирует все доки и SKILL.md на команду
запуска с этими флагами. README: строка `acceptance-<ID>` в таблице R4, правила цитаты,
`jira-create`, числа 119/31. approval-markers.md: правила цитаты, батч, одноразовость
skip-judges, исключение `jira-create`, снята строка про доставку. FORGE.md: R1 для тестов
(R2 — прод-код), правила согласия, четвёртый raw-читатель. INSTALL.md: нумерация §2.

Сьют 119/119, evals 35/35.

## Не закрыто — разобрать

### 1. Критичность и авто-порог меняет сама модель
`set_criticality.py` и `config.py set decisions.auto_max_risk R3 --confirm` модель зовёт без
согласия: первая установка — штатная (ответ пользователя), но ПОНИЖЕНИЕ критичности/подъём
авто-порога посреди прогона — это тоже снятие enforcement (track D P3-3, track B F-7). Пару
`criticality`+`auto_max_risk` в config.py не деривирую намеренно: бриф forgefix ставит
`auto_max_risk R2` при `criticality medium`, автодеривация сломала бы fix-ветку. Решить:
понижение уже выбранной критичности и подъём `auto_max_risk` выше деривированного —
consent-класс `policy-downgrade-decisions.*`?

### 2. Остатки по классам (низкая вероятность для модели)
- `update.py --project <чужой корень>` (E-13): хук-гейты смотрят корень сессии; собственные
  гейты update.py (судьи, origin, gate-result) идут по чужому манифесту — остаются хук-уровня.
- Интерпретаторы кроме python: `ruby -e File.delete`, `perl -e 'unlink glob …'` (E-08).
- Бытовой деструктив системных файлов вне проекта: `dd of=/etc/x`, `truncate -s0`,
  `chmod 000`, `mv /etc/hosts …` (E-15) — запись вне репо форж не гейтит по дизайну.
- FIFO, появившийся иначе чем через shell-команду: читатели открывают файлы блокирующим `open`.
- pii: секрет в `file_path` (E-16), XML-теги/machine-форма/URI с паролем/base64 под коротким
  ключом/NUL и BOM внутри слова (L-8…L-13). URI `scheme://user:pass@host` — самое реальное.
- Классы команд: миграции командой (`flyway migrate`, `liquibase update`, `alembic upgrade`),
  SQL через `mongo`/пайп/переменную, `DELETE`/`ALTER`, `DROP /*c*/ TABLE`; пути
  `db/V1__init.sql`, `flyway.conf` → R1 (L-1, L-2, L-6). Решение политики, не баг кода.
- Циклы и self-dep в `depends_on` принимаются (ST-3) — task-plan пишет субагент дизайна,
  цикл там правдоподобен; `read.py` молчит о стопоре.
- `checkpoint.py` напрямую: ref на несуществующий шаг, dangling-коммиты (ST-4).
- Реестр судей отсутствует/битый → init строит манифест без судей молча (ST-1).
- Диагностика: битый `policy.json` — preflight говорит «not found», `config.py get` —
  трейсбек (APPR-6); порча `policy_snapshot` снимает фиксацию молча, preflight уверяет
  обратное (APPR-7 — достижимо только мимо state-write-guard); `read --list` прячет битую
  фичу, `resolve_phases` на `{}` пишет «not found», PDC глотает флаги, eval-plan рядом с
  манифестом не ищется (ST-5).
- Архив: restore без меты «✅ возвращено» (AR-4), ts-сиблинги и короткий слаг (AR-5),
  чужой стейт через мету (AR-2, частично).
- Спека: петля `provenance: none` от детекта (G-P2), title-only мастер с одним требованием
  → prose (G-P2), CK-2 (slash-форма правил модулей мертва).
- Битый `settings.json` → deploy падает трейсбеком, `.bak` пишется уже битым (CLI-1).
- Мелочь: `record_approval --json` без batch, битый `--artifacts` у update.py — WARN и
  закрытие; DeprecationWarning dual-read не печатается через `config.py get`; `__pycache__`
  переживает uninstall; prompt-guard видит 20К символов и ловит FP на цитатах; порядок
  диагностики write-R4 под fix-планом (L-16).

### 3. Политика `policy-downgrade-*` для `config.py set` — как решено в 016 §4
Маркер `set` не тратится намеренно (гейтится и возврат порога). Отчёт снова пометил P2.

## Чему не верить в отчёте

| утверждение | факт |
|---|---|
| E-FIFO-STALL «fail-open на всю сессию» | только при живом прогоне (верификатор это уточнил) и только если модель сама создаст FIFO на месте policy.json — закрыто на стороне записи, дёшево |
| APPR-2: NaN делает coverage-гейт непроходимым | не подтвердилось и в самом треке K: `_policy_threshold` клампит NaN к 0.80. Реальная беда — невалидный JSON в policy.json (закрыто) |
| D P3-1: «хранилище нормализует ключ в lowercase» → `GATE-OVERRIDE …` обходит класс согласия | ключ пишется как есть (`GATE-OVERRIDE-Blue-Judge`), гейты ищут `gate-override-<judge>` — обхода не было; классификатор выровнен для чистоты журнала |
| B F-6: SoD `agent_caps` «мёртв» | на qwen в PreToolUse нет `agent_type` вовсе — SoD по агенту неактивен по построению (docs/v2/01); роль держит sod-enforcer по шагу |
| E-REPOS-PV на «256×y» | верификатор: строки по 2К с `\n` — 0,5 с; триггер — длинный прогон без разделителя. Закрыто независимо от формы |
| C F-5: pv ↔ state-write «пересечение слоёв» | итог корректный (второй слой ловит), не дефект |
| E-17 `GIT RESET --HARD`, E-18 dup-keys JSON | бинаря `GIT` нет; парсеры рантайма и хука согласованы (last-wins) |
| «7×P0» | реальны для модели без умысла два (согласие, ReDoS); `cwd=/`, `mkfifo`, `find -delete` — закрыты, потому что дёшево |
| ENC-1/2 (не-UTF8 stdin, JSON глубины 100К у четвёрки хуков) | рантайм шлёт JSON.stringify — валидный UTF-8; как и в 016, вход, которого рантайм не порождает |
| Track D, шапка «Дата: 2026-02-27» | опечатка, прогон от 2026-10-08 |
