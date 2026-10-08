# Задача 016 — Бэклог боевого прогона v0.4.5

## Статус
Открыта · заведена 2026-10-07

## Контекст

Боевой прогон v0.4.5 на gigacode 26.9.64 (2026-10-06, другая машина): headless-сессии
`gigacode -p`, ~50 прямых проб хуков, затем три волны перепроверки субагентами —
DEEP-VERIFY, EDGE-CASE-AUDIT, FORGE-CMDS-AUDIT, в сумме около 120 утверждений.

> **Артефакты в git НЕ едут** — лежат у оператора локально в `forge/логи 045 балттестед/`
> (четыре отчёта, транскрипты headless-прогонов, снимки стейта). Скриптов волн 1–2
> (`wave1-scripts/`, `wave2-scripts/`) в папке нет, хотя её README на них ссылается.

v0.4.5 = `ae66e2d`, то есть отчёты сняты ровно с кода, на котором делались правки. Каждая
находка ниже перепроверена здесь пробой на свежем деплое (scratch-проект, хуки на payload).

Главный вывод проверки: почти все находки на уровне кода воспроизводятся, но серьёзность в
отчётах завышена — большинство «P0» требуют, чтобы модель НАМЕРЕННО обходила защиту (glob,
переменная, hardlink, `$(printf …)`, симлинк-алиас). В транскриптах боевых прогонов модель
так себя не вела: дважды отказалась ставить «да» за пользователя. При этом самую тяжёлую
дыру все четыре отчёта пропустили — она ниже первой строкой.

## Сделано 2026-10-07

| что | где |
|---|---|
| **Сверка цитаты --evidence была пустой.** qwen пишет запись модели с текущим `functionCall` и его аргументами в `chats/<session>.jsonl` ДО PreToolUse (`recordAssistantTurn`, qwen 0.21.14); подстрока по всему транскрипту находила выдуманную цитату в самой команде `record_approval`. Не проходили только цитаты с `"` — так и упала первая попытка в `battle-w2-out.txt`. Сверка теперь только с репликами пользователя (`type:"user"`); свои реплики модели и `echo` в tool_result не считаются | `gate-guard._transcript_user_text` |
| `policy-downgrade-*` не требовал цитаты, хотя README/FORGE.md/approval-markers.md его так называли: `record_approval` + `config.py set quality.tdd false` снимали TDD без слова пользователя (воспроизведено целиком) | `forge_events.CONSENT_PREFIXES` — один список на gate-guard и record_approval |
| `--judge a,b,c`: ключ брался до первой запятой, маркер на одного судью снимал любой список | `gate-guard.check_gate_override` — маркер на каждого |
| `override_judge.py` «сам требует approval» — только в FORGE.md; скрипт маркеры не читал | второй слой в скрипте (single и `--batch`), маркер одноразовый, exit 3 |
| `archive.py abandon` / `put --force` не гейтились: модель по подсказке preflight сама сняла прогон FORGE-1 и удалила его чекпойнты (A5). По существу она была права — прогон был брошенной пробой, — но решать это не ей | R4 `archive_drop` в gate-guard + второй слой в archive.py; подсказки preflight/config.py/`/forge-archive` — «спроси пользователя» |
| Гонка писателей манифеста (в FORGE.md с 09-28 как «не закрыто»): 8 параллельных `update.py` теряли записи 3/3 раза | `_project.exclusive_lock`, `manifest.lock`, свой tmp на процесс |
| `state-recorder` резолвил прогон своим обходом «свежайший mtime» — его 29a76ba не заменил. При фиксе внутри стори origin `fix-red` уходил в журнал стори (A8) | `state-recorder._resolve_run` — по принадлежности шага |
| Редеплой заменял блок `hooks` в settings.json целиком — хуки оператора пропадали (CLI-2) | `resolve_hook_paths.merge_forge_hooks`; `--check` больше не считает скрипт оператора «чужим путём» |
| Порог покрытия fix-ветки: `default=0.80` в скрипте и `--threshold 0.80` в брифе forgefix — снимок политики (0.7 на прогоне) не читался (D1) | `check_coverage._effective_threshold`: порог прогона, явный `--threshold` его только поднимает |
| pii-boundary: пример `user@example.com`, UUID в sdd.md, `git@github.com:` в README, `timeout-ms: 1500000000` → DENY; `api_key=…` блокировался «телефоном» (B1/B2) | `risk-policy.json`: `pii_safe_patterns`, телефон только телефонного вида, секреты первыми |
| `git push --force-with-lease origin main` блокировался policy-строкой — единственный безопасный force-push | строка снята; голый `--force` держит core |
| Рекурсивный `rm` по абсолютному пути: регулярка требовала одновременно `-r` и `-f` и цель без кавычек — `rm -r /etc`, `rm -rf "/etc/passwd"`, `${HOME}/x`, перенос строки проходили (A1/A7) | `destructive-blocker._rm_recursive_dangerous_targets` — разбор argv |
| Доки велели `gigacode --experimental-hooks` и `-y`: в 26.9 их нет, сессия с ними не стартует (C1) | README, INSTALL, user-guide, troubleshooting, pipeline-technical, FORGE.md |

Каждая правка — с тестом, падающим до неё (проверено откатом правки через stash). Сьют
119/119, evals 35/35.

## Сделано 2026-10-08 (второй заход)

| что | где |
|---|---|
| RED-гейт давал «PASS (нет задач)» на нечитаемом task-plan.json и на опечатке в `--task` — RED-шаг закрывался без упавшего теста. Старый тест пинил именно это | `check_tests_red.main` — fail-closed |
| `config.py risk list-remove|cap-set` и `phase disable` обязательной фазы снимали защиту с одним `--confirm`, который ставит сама модель (`risk list-remove pii_patterns …` → rc 0) | `gate-guard.check_policy_structure_edit` + второй слой в config.py; список обязательных фаз — `resolve_phases.mandatory_phase_ids` |
| git, стирающий незакоммиченное целиком (`reset --hard`, `clean -f`, `checkout .`, `restore .`, `stash drop|clear`), не гейтился (015 п.5) | `gate-guard.check_git_discard`, маркер `git-discard`, тратится на пропуске |
| pii не видел секрет в JSON-форме, `secretKey`, `github_pat_`, JWT, Bearer, Stripe/AIza, зашифрованный PEM; карта ловилась без Луна (epoch-millis → DENY) | `risk-policy.json`: `pii_patterns`, `pii_luhn_patterns` + `pii-boundary._luhn_ok` |
| Первый запуск: preflight на недозаполненном конфиге звал тот же init (B3); Maven-`groupId` не понимался; совет doctor резался на тире (B5) | `preflight.answer_commands`, `init_pipeline_config.detect_group`, `doctor._first_sentence` |
| Тест 001 проверял, где лежит копия форжа, а не резолвер (в `/private/tmp` машины разработчика лежит посторонний build.gradle — поэтому «не воспроизводилось») | `tests/test_project_resolver.py`; задача 001 закрыта |
| Остальные писатели манифеста без замка — гонка с `update.py` теряла то решение пользователя, то статус шага (3/3) | `_project.locked_json_update`: config.py set/repin, set_criticality, оба add_steps, patch_manifest_judges |
| abandon необратим: list/restore не видели ground/archive, чекпойнты удалялись (A9); restore верил путям из меты (015 п.2) | чекпойнты → `refs/forge/abandoned/<метка>`, `restore <feature>` возвращает прогон целиком; `archive._require_inside` |
| Хук оператора внутри `.gigacode/hooks/` снимался деплоем и деинсталляцией | `resolve_hook_paths.forge_hook_names` — реестр `.forge-deployed` + эталон + надгробия |
| Подсказки record_approval без `--evidence` для ключей, где он обязателен; устаревший путь `ground/approvals/<key>.json` в баннере rollback (D4); `--project` после подкоманды config.py (B4); примеры rollback без `--skill` (D3) | rollback.py, config.py, record_approval.py, брифы forgefix/config-helper, rollback.md |
| state-write-guard: запись в хуки через симлинк-форму пути (A3); снос целиком `ground/`, каталога прогона, `.gigacode/hooks` (PATH-6/7) | `_in_harness` — realpath; `_CP_CONTAINER_RE` |
| `chmod 777` без `-R`, 0777/7777/a+rwx (A2); chmod не было в fail-closed ядре | `risk-policy.json` + `_CORE_BLACKLIST` |
| Квадратичный `_target_path` (32K символов → 4,7 с, ENC-3) | `risk_ladder._target_path` — линейный разбор |

Сьют 119/119, evals 35/35; каждая правка — с тестом, падающим до неё. Свежий деплой в
полигон: матрицы проб обоих заходов и consent-проба подтверждают закрытое.

## Не закрыто — разобрать

### 1. rollback и init пишут манифест мимо замка
Оба — R4/служебные и параллельно шагам не работают, поэтому оставлены. Если понадобится —
та же `locked_json_update`, но rollback считает новое состояние из раннего чтения, его надо
переводить целиком.

### 2. Только при намеренном обходе (держать в голове, не срочно)
- state-write-guard: регистр пути на APFS, glob и переменная в цели редиректа, hardlink;
- destructive-blocker: цель рекурсивного `rm` через подстановку команды или переменную
  (`$DIR`), нерекурсивный `rm -f <абс. путь>`, классы вне домена (`terraform destroy`,
  `kubectl delete ns`);
- `sudo rm -rf ground/` — state-write-guard смотрит только на первый токен сегмента.

### 3. pii: что осталось как было
- Телефон в спеке/README — DENY намеренно (B1): номер поддержки и личный неразличимы.
  Баннер даёт рецепт — маска `+7 (XXX) XXX-XX-XX`.
- Двадцатизначный номер счёта не ловится вовсе.

### 4. Маркеры `policy-downgrade-*` для `config.py set` не расходуются
`set` гейтится независимо от направления (и возврат порога тоже), поэтому одноразовость там
усложнила бы восстановление enforcement. Новые гейты (`risk`/`phase`, `git-discard`,
`abandon`, override) — одноразовые.

### 5. Формат транскрипта gigacode 26.9
Сверка цитаты написана под qwen 0.21.14 (`type:"user"`, `parts[].text`). Если у форка формат
другой, гейт уйдёт в «реплик не распознано → предупреждение и пропуск». Нужен один живой
прогон на 26.9: запись `rollback-*` с выдуманной цитатой обязана получить deny.

### 6. Таймаут state-recorder при сериализации
Закрытия параллельных шагов теперь идут под замком по очереди, а state-recorder ждёт
update.py 20 с. На больших репо (долгий git-чекпойнт) последний в очереди может не успеть —
тогда шаг остаётся открытым, а origin записан; оркестратор закроет его повторно.

## Чему не верить в отчётах

| утверждение | факт |
|---|---|
| D2: юнит-база 117/118 | артефакт окружения: `test_find_project_root` требует, чтобы cwd был в git-дереве, а прогон шёл из распакованного zip. Здесь 16/16. Это же объясняет «не воспроизводится» в задаче 001 |
| C2/C3: петля одобрений сжигает бюджет | DEEP-VERIFY не воспроизвёл (модель 26.9 fail-fast); поведение рантайма в headless, не форжа |
| C5: `closed_by: inline` = дыра в BR-10 | `closed_by` — метка из `--closed-by`; гейт проверяет origin-запись (`_check_subagent_origin`), а субагент SDD в `battle-forge4` реально отработал |
| A4/A6: «скрипт мимо рантайма без маркера / с выдуманной цитатой» | мимо рантайма скрипт запускает человек; внутри рантайма gate-guard держал. Реальной была ложь в FORGE.md (закрыто вторым слоем выше) |
| ENC-1, APPR-4, STATE-6/7, AR-1 | требуют входа, которого рантайм не порождает (битый stdin, `transcript_path=/dev/zero`), либо ручной подделки меты; AR-1 (= 015 п.2) всё же закрыт во втором заходе — дёшево, а функция и так правилась |
