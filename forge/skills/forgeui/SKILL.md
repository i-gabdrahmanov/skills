---
name: forgeui
description: >
  forge-ui — фронтовая ветка forge для ФРОНТ-ЧАСТИ стори во фронт-репо: макет экрана (Pixso,
  Figma-экспорт) → UI-код на компонентной библиотеке проекта → проверка против макета до чистого
  результата. Спека фронт-части — ui-spec.md (кадры, данные, состояния, события, валидации,
  сценарии); бэк-часть той же стори, если она есть, живёт в бэк-репо со своей sdd.md. Четыре шага —
  четыре решения человека: утвердить спеку, подписать план секций, решать по итерациям проверки.
  Коммиты/push/PR пайплайн НЕ делает. Используй, когда во фронт-репо говорят «сверстай экран по
  макету», «сделай фронт стори», «вот ссылка на Pixso/Figma», «UI по дизайну», «/forge KEY»,
  или передают Jira-стори с макетами. Бэк-задачи (Java/Spring) — не сюда: это forge бэк-репо.
---

# Forgeui — фронт-часть стори: макет → UI-код → проверка против макета

> **Пути.** `<project>` — корень фронт-репо (там `ground/` и `.daiver/`). `<forge>` — корень кода
> forge-ui: каталог на два уровня выше этого SKILL.md (preflight печатает его в `layout.base`).
> Все команды ниже — `python3 <forge>/...`; не используй `~/.gigacode/...`.

> **Рантайм — форк GigaCode (Qwen). Жёсткие правила:**
> - Запуск — `gigacode` из корня проекта, без флагов: `--experimental-hooks` и `-y` в 26.9 нет.
>   Headless требует `--allowed-tools` — без него `agent` не стартует.
> - В командах — только однострочные, без `$(...)` и обратных кавычек (рантайм режет).
> - Тяжёлую фазу — только через `agent(subagent_type="general-purpose", ...)`. `agent()` и
>   `ask_user_question` не активны одновременно: субагент не спрашивает сам, он возвращает
>   `pending_questions`, вопросы задаёшь ты.

Плоский цикл (feature = ключ Jira стори — тот же, что у её бэк-части; стейт в namespace
`forgeui`): **спека → дизайн по макету → сборка → проверка**.
Шаги стейта: `ui-spec → ui-design → ui-build → ui-verify`.

> **Философия ветки.** Детерминированное — кодом движка (MCP `forge-ui`, инструменты `daiver_*`):
> разбор макета, канон, раскладка, сравнение страницы с макетом, состояние. Суждения — тебе:
> выбор компонента, разметка, триаж. **Число с картинки — число, которое никто не мерил:** размеры,
> цвета, отступы берутся только из макета через движок, картинка решает лишь группировку.
> **Ничего не выдумывать:** кадр, ссылка на макет, компонент, проп, путь импорта, тип/клиент
> данных — либо из диска и ответов пользователя, либо вопрос.

---

## 0. Предусловия

- Фронт-репо (`package.json` в корне), git. MCP-сервер **`forge-ui`** подключён (инструменты
  `daiver_status`, `daiver_init`, `daiver_spec`). Нет инструментов — СТОП: поставка forge-ui не
  установлена, чини её, а не обходи.
- **Первый ход — `daiver_status`.** Каталог `.daiver/` руками не листай: что готово, решает код.
- **Читай ответ `daiver_status` как есть.** У трека в `tracks[]`: `stage` — ступень (`empty → requirement → normalized →
  planned → building → built → verifying → clean`; её считает код по артефактам, «на глаз» не выводи), `frame` — кадр, на
  котором трек стоит, `nextStep` — шаг forge-ui + кадр + что делать. Верхний `nextStep` — про первый незавершённый трек; трека
  с ключом твоей стори нет — стори новая, начинай с `ui-spec`. `warnings` пересказывай пользователю вслух до первого действия
  (устаревшее требование, нет goal image, арты без .svg, спеки не на месте…): код говорит это не для красоты.
- `initialized: false` → **первый запуск в репо**: вызови `daiver_init` без аргументов — получишь
  кандидатов с доказательствами (package.json, lock-файл, каталоги, файлы конвенций) и список
  вопросов. Задай их пользователю (`ask_user_question`), кандидатов предлагай первыми, но решает
  он: **источник библиотеки (`librarySource`), `outputDir` и `buildCommand` не угадываются**.
  Затем `daiver_init` с `answers`. `.daiver/config.yaml` руками не пиши — только так.
- Ключ стори (`[A-Z]+-\d+`) — это `--feature`. Нет ключа — попроси его у пользователя: фронт и
  бэк одной стори связывает именно он.

> **Обязательные решения пользователя** (форсятся хуками и `update.py`):
> **(1)** утверждение фронт-спеки — маркер `ui-spec-approved-<KEY>` (без него `ui-spec` не
> закроется); **(2)** подпись плана секций — согласие `ui-plan-<KEY>` с цитатой (без него
> `gate-guard` не пустит запись на `ui-build`/`ui-verify`); **(3)** остановка проверки с
> открытыми дефектами — согласие `ui-accept-<KEY>` с цитатой. Внутри `ui-design` — ещё два вида согласия (§3.1):
> подтверждение эвристических вердиктов резолва `ui-resolve-<KEY>` (low — всегда) и каждое правило в карту `ui-map-<KEY>`.

## 1. Архитектура (кто что делает)

| Шаг | step-id | Кто | Решение человека | Гейт (`record_gate`) |
|---|---|---|---|---|
| Фронт-спека `ui-spec.md` | `ui-spec` | субагент | утвердить спеку | `check_ui_spec.py` |
| Макеты → канон → компоненты → план секций | `ui-design` | субагент | подписать план | `ui_gate.py design` |
| Сборка по секциям + buildCommand | `ui-build` | субагент | — | `ui_gate.py build` |
| Итерации проверки против макета + отчёт | `ui-verify` | субагент на итерацию | чинить / стоп | `ui_gate.py verify` |

> **Субагент = ЯВНЫЙ вызов `agent(subagent_type="general-purpose", ...)`.** Инлайн фазу не
> делай — заблокирует `inline-phase-guard`, а без `SubagentStop` шаг не закроется. Субагент
> ПОСЛЕДНИМ действием гоняет свой гейт через раннер и возвращает JSON с `step_id` и `status`.

> **Этап реализации.** forge-ui исполняет `ui-spec`, `ui-design` и `ui-build` полностью, а `ui-verify` — цикл итераций с модельным сравнением и код-гейтом (§5); визуальный дифф,
> визуальный обзор и e2e в движке пока нет (в записи итерации они `skipped` с причиной). Код вручную «вместо движка» не пиши.

### 1.1. Инициализация прогона
```
python3 <forge>/hooks/preflight.py --project <toplevel>
```
exit 0 — продолжаем; exit 1 — стоп, чинить установку; exit 2 — конфиг forge не инициализирован:
выполни команду из поля `init_command` и повтори preflight.

**Шаг 1 — стейт (namespace forgeui):**
```
python3 <forge>/skills/pipeline-state/scripts/init.py --project <toplevel> --skill forgeui --feature <KEY> --steps @<forge>/skills/forgeui/references/manifest-steps.json
```

**Шаг 2 — решения прогона, ПОСЛЕ `init.py`** (`auto_max_risk` — sensitive, нужен `--confirm`):
```
python3 <forge>/skills/config-helper/scripts/config.py --project <toplevel> set inputs.mode ui --skill forgeui --feature <KEY>
python3 <forge>/skills/config-helper/scripts/config.py --project <toplevel> set decisions.mode_task <KEY> --skill forgeui --feature <KEY>
python3 <forge>/skills/config-helper/scripts/config.py --project <toplevel> set decisions.auto_max_risk R2 --confirm --skill forgeui --feature <KEY>
python3 <forge>/skills/config-helper/scripts/config.py --project <toplevel> set decisions.criticality medium --skill forgeui --feature <KEY>
```
Закрытие шага — только после прохождения его гейта:
```
python3 <forge>/skills/pipeline-state/scripts/update.py --project <toplevel> --skill forgeui --feature <KEY> --step-id <id> --status completed
```

**Каталог стори — резолвни, не подставляй плейсхолдер:**
```
python3 <forge>/skills/feature-pipeline/scripts/skill_paths.py feature-docs --project <toplevel> --feature <KEY>
```
Печатает `<docs>/feature-pipeline/<KEY>`. Дальше `<storydir>` = ровно этот путь. Спека — строго
`<storydir>/ui-spec.md`: по этому имени работают гейт, `update.py` и движок.

---

## 2. Фронт-спека → `ui-spec`

Собери вход: Jira-стори (есть MCP — summary, description, вложения/ссылки на макеты; сохрани в
`<storydir>/issue.json`), иначе описание от пользователя в `<storydir>/issue.txt`. **Ссылки на
макеты** (URL Pixso/Figma с id узла или файлы экспорта) — из стори или от пользователя; нет — спроси.
Спроси и про бэк-часть: «У стори есть бэк-часть? Если да — её ключ», ответ «нет» — тоже ответ.

```
description: "Write ui-spec.md for story <KEY>"
subagent_type: general-purpose
prompt:
Сначала прочитай и строго следуй шаблону: read_file("<forge>/skills/forgeui/references/ui-spec-template.md")
Корень репо: <toplevel>. Стори: <KEY>. Каталог стори: <storydir> (там issue.json/issue.txt).
Макеты от пользователя: <список: имя кадра — URL/файл — роль>. Бэк-часть: <нет | есть: KEY>.
Шаги:
1. Прочитай стори. Пойми, какие экраны/модалки/состояния входят в фронт-часть.
2. «Данные»: найди в репо реальные типы и клиент API, которыми экран будет пользоваться
   (grep по названию сущности/эндпоинта). Нашёл — `types:`/`client:` с путём в бэктиках.
   Не нашёл — «моки» и строка в «Вне охвата»: какого контракта ждём от бэк-части.
3. Запиши <storydir>/ui-spec.md строго по шаблону (часть после «---»). Кадры и ссылки — только
   переданные тебе. Раздел без логики — «нет», не пустой.
4. Чего не хватает (ссылки на макет, поведение при ошибке, кто видит экран) — НЕ додумывай:
   верни pending_questions.
5. ПОСЛЕДНИМ действием прогони гейт ЧЕРЕЗ РАННЕР (без него шаг не закроется):
   python3 <forge>/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgeui --feature <KEY> --step-id ui-spec --cmd "python3 <forge>/skills/forgeui/scripts/check_ui_spec.py <storydir> --project <toplevel>"
Код проекта не правь. Верни JSON: {"step_id":"ui-spec","status":"completed|failed|needs_input",
"frames":["<имя> (<роль>)"],"scenarios":<N>,"back_part":"нет|есть: KEY",
"pending_questions":[{"id":"...","question":"..."}]}
```
Есть `pending_questions` — задай их пользователю и перезапусти субагента с `answers`.

### 2.1. Утверждение спеки (ОБЯЗАТЕЛЬНО, enforced)

1. Покажи пользователю резюме `ui-spec.md` на 5–8 строк: кадры (имя, роль, макет) · откуда данные
   (пути или «моки») · ключевые состояния и события · число сценариев · бэк-часть · вне охвата.
2. Спроси `ask_user_question`: «Утверждаем фронт-спеку?» — **«Да, утверждаю»** / **«Правки»**.
3. «Да» — зафиксируй утверждение и закрой шаг:
   ```
   python3 <forge>/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key ui-spec-approved-<KEY> --approved-by user --reason "<кратко: что утвердили>"
   python3 <forge>/skills/pipeline-state/scripts/update.py --project <toplevel> --skill forgeui --feature <KEY> --step-id ui-spec --status completed
   ```
   «Правки» — верни субагенту с правками; утверждение не выписывай.
4. Вопрос не отрендерился (headless) — не выписывай утверждение сам: остановись и попроси ответ.

## 3. Дизайн по макету → `ui-design` (макет → канон → компоненты → план секций → подпись)

Шаг ведёт субагент, резолв компонентов — в нём же. Вопросы человеку и согласия — дело оркестратора (§3.1). Кадры — ТОЛЬКО из таблицы
«Кадры» `<storydir>/ui-spec.md`: имя и ссылку на макет берут оттуда или у пользователя; нет ссылки — вопрос, а не правдоподобный URL.

```
description: "Design step for <KEY>"
subagent_type: general-purpose
prompt:
Корень репо: <toplevel>. Стори = имя трека: <KEY>. Каталог стори: <storydir>. Ответы пользователя на прошлые вопросы (`<id>: <ответ>`): <дословно | нет>.
Всё сделанное лежит на диске: после перезапуска иди с п. 1 и пропускай готовое. Отказ инструмента читай целиком: он называет, что
исправить. Узел человеку называй ссылкой `[id](url)` по `nodeUrlTemplate` (п. 3); шаблона нет — голый id, адрес не выдумывать.
Ответы примени сразу, до п. 1:
- `map-rule-<nodeId>: Нет…` — перезапиши вердикт этого узла без `mapRuleProposal`; `Да…` — правило уже в карте (записал оркестратор),
  узел перерешай по карте.
- `confirm-resolutions: Правки …` — исправь вердикты по этим словам (`daiver_resolve_finalize`: уверенность указанного пользователем —
  high, основание — его слова) и вернись к п. 6; `Да…` — согласие записал оркестратор, доведи п. 6–8.
- Слова, которым нужно правило карты (например, «иконка Icon24 — это ArrowLeft»), — не вердикт: верни вопрос `map-rule` с `rule` и
  `yaml` (его даёт `daiver_map_append` с `dryRun: true`). Прочие ответы (путь к выгрузке, ссылка на макет) — исходные данные шага.
1. `daiver_status`: `warnings` перескажи в ответе; `initialized: false` — СТОП, `needs_input` (настройки проекта задаёт оркестратор).
   Кадры — из таблицы «Кадры» (read_file `<storydir>/ui-spec.md`); работай по ним по очереди.
2. Кадра нет в треке или его stage `empty`. Попроси Pixso MCP пользователя сохранить DSL кадра В ФАЙЛ внутри репо (например
   `.daiver/inbox/<кадр>.dsl.json`). DSL в контекст не читай и не вставляй (сотни КБ): MCP отдаёт его только ответом или Pixso MCP нет —
   не вызывай его за DSL, верни `needs_input`: пользователь выгрузит файл сам. Затем `daiver_pixso_pull(track, dslPath, frameUrl, frame)`:
   `frameUrl` — ссылка из таблицы, `frame` — имя кадра (латиница, цифры, `-`, `_`); второй и следующий кадр — тот же вызов, `layout` в ответе («LAYOUT — …»: семья первого кадра переехала в `frames/<имя>/`) перескажи человеком, а ссылка у двух кадров не может совпадать. Макет СОБРАННОГО кадра изменился — тот же вызов по новой выгрузке: ответ `mode: revision` («REVISION — …» перескажи; прежняя версия ушла в `versions/<n>/`, план и подпись — заново), дальше п. 3 и далее как обычно; `daiver_frame_baseline(track, frame)` покажет регионы изменения и файлы, где они собраны. Растр кадра (JPEG/PNG: Pixso MCP или пользователь) —
   `daiver_track_manifest(track, frame, rasterPath)`; без растра идти можно, но `goal-image-missing` перескажи.
3. Stage `requirement` — `daiver_normalize(track, frame)`. Потом `daiver_svg_assets(track, frame)`: на каждый `missingArt` — SVG
   иллюстрации (Pixso MCP или пользователь) → `daiver_svg_assets(track, frame, slug, svgPath)`; иллюстрацию не рисовать.
   Страница кадра УЖЕ есть в коде (маршрут из таблицы ведёт на существующий экран или так сказал пользователь)? — `daiver_propose_sources(track, frame)`: прочитай кандидатов, `routes` и их импорты; файлы, которые РЕНДЕРЯТ этот экран (не угаданные), — `daiver_track_manifest(track, frame, set)` `{sources, scope}`, scope — вопрос человеку (change — точечное изменение по макету, page — привести к макету всю страницу). Затем, пока код не тронут, база страницы: `daiver_screenshot` → `daiver_verify_compare` → `daiver_code_compare`, все с iterationDir `<папка кадра>/baseline` (адреса нет — вопрос; работающей страницы нет вовсе — только со слов человека `set` `{baselineSkip: "<причина>"}`). Дальше outline, резолв и план идут по регионам изменения (`page` в ответе outline; `daiver_frame_baseline` — регионы, их `location` в коде, `preExisting`).
   `daiver_outline(track, frame)` — при каждом запуске: из ответа нужны `nodeUrlTemplate`, `unproven`, `goalImage` и `goalImageNext`.
4. Библиотека — только если есть нерешённые кандидаты (п. 5а непуст хоть в одном кадре), ОДИН раз за запуск и до первого кандидата.
   `storybook-mcp` — имена компонентов у его MCP-сервера; `stories | sources` — `daiver_ds_inventory` (ответ не пересказывай).
   `ds-requirements-missing` в `warnings` или `blocked: library-source-unreachable — …`: построй требования ОДИН раз
   `daiver_ds_requirements` (файлы библиотеки не читай; `check:true` — устарели ли) и повтори п. 4. Не вышло (ноль компонентов —
   `library.scanPaths` правит человек; сервер сторибука не отвечает) — СТОП: `status:"blocked"`, `blocked:"library-source-unreachable"`,
   причина из отказа дословно. Без списка библиотеки резолв не начинать.
5. Резолв, по кадру. Отдельного субагента `resolver` в рантайме нет, поэтому веди его сам, только через инструменты `daiver_*`, и объяви это
   одной строкой в ответе перед JSON.
   а) `daiver_resolve_candidates(track, frame)` — кандидатов называет код, id не придумывать. «×N» решается ОДИН раз, остальные получат
      вердикт сами; `[stale]` — прежний вердикт не годится, решай как новый; пусто — кадр решён.
   б) На строку — `daiver_resolve_query(track, frame, nodeId)`: это всё, что ты видишь о макете (геометрии и стилей нет, для выбора они
      не нужны). По ответу: `recorded` — вердикт есть, пропусти; `reuse` — для того же figmaName и вариантов он уже есть, пиши ровно его;
      `map.hit: true` — правило карты, вердикт high по нему (decision, component, props как в `map`), библиотеку не читай; иконка (есть
      `iconFallback`) — библиотеку не угадывай, делай по `next` ответа; иначе — библиотека (в).
   в) Библиотека. `storybook-mcp` — запрос к его MCP; `stories | sources` — `daiver_ds_lookup(figmaName, variants, textSlotKeys)` (`match`:
      exact и word надёжны, part и shared — подсказка), 1–2 финалиста → `daiver_ds_details(components)` (дефолты повторно не передавать;
      `cut` называет сокращённое — недостающее не додумывать). `candidates: []` — «такого нет»: `local`. Путь импорта — только готовый
      `import` из ответа; `null` — вопрос пользователю.
   г) Вердикт — `daiver_resolve_finalize(track, frame, verdict)`: `{nodeId, decision: component|local|ignore, component?, props?, confidence,
      rationale ≤ 400, mapRuleProposal?}`. confidence честно: high — имя и пропы совпали; medium — имя неточное, но состав детей совпал;
      low — догадка. Не повышай её ради прохода: low человек увидит всегда. `mapRuleProposal` — только при уверенности в новом правиле карты.
      Отказ перечисляет ВСЕ нарушения: исправь и повтори. «Карта некорректна» — СТОП (`needs_input`: файл карты правит человек).
   д) Повторяй а–г, пока список не пуст во всех кадрах.
6. `daiver_resolve_report(track)`: пишет `resolution-report.md` и отвечает, что спросить человека. По ответу:
   - `unresolved > 0` — назад к п. 5.
   - `proposals` — предложенные правила карты, СНАЧАЛА они: вопрос на каждое (`id:"map-rule-<nodeId>"`, `kind:"map-rule"`, `rule`, текст с `yaml`
     дословно) → `needs_input`. `invalid` у предложения — перезапиши вердикт без `mapRuleProposal`.
   - `toConfirm` не пуст и `confirmed: false` — ОДИН вопрос (`id:"confirm-resolutions"`, `kind:"confirm-resolutions"`): строки `toConfirm`
     дословно (low первыми) и путь из `report` → `needs_input`. Согласие не выписывай: его пишет оркестратор.
   - `confirmed: true` — дальше.
7. layoutHints — для узлов из `unproven`, лестница сверху вниз: (а) `daiver_source_nodes(track, frame, nodeIds)` по id из `children`
   (у layout-hints — сам `id`): что дизайнер объявил (constraints, auto-layout), то сильнее впечатления, и вопросов не нужно;
   (б) не объявлено, а `goalImageNext` велит открыть картинку — открой `goalImage` ТОЛЬКО чтобы сгруппировать контейнер, числа
   (px, цвета, шрифты) с неё не брать; (в) осталось неясным — вопрос пользователю, не догадка. Решение запишет план (п. 8).
8. План, по каждому кадру: `daiver_plan_write(track, frame, plan)`. В `plan.layout` — решение по КАЖДОЙ записи `unproven` (`{id, reason, decision,
   evidence}` из записи; decision: apply | keep-px у layout-hints, keep-px | group у absolute-children, при group ещё `direction` и `groups`; evidence:
   declaration | goal-image | user | none; apply и group без свидетельства код не примет). Не решаешь — `plan.layoutRest: "keep-px"` (px остаётся, узел
   попадёт в planUncertainty). Кадр-состояние (`stateOf`) — НЕ второй экран: `daiver_frame_states(track, frame)` покажет регионы дельты (added / changed / removed / shared), `triggers` и `available`; `available: false` — чини причину (stateOf не на тот кадр), а не планируй целую страницу; outline, section, резолв и план такого кадра идут по регионам, а в плане обязателен `plan.state` `{variable, screenValue, value, toggle: {trigger: id из triggers} | {named: слова пользователя}}`. Ревизия (`baseline` в ответе outline) — изменение собранного: секции только изменённое, решения shared-частей и место корня план берёт у собранной версии сам. У не-первичного кадра (не `stateOf`) корень в едином модуле — РЕШЕНИЕ плана: `plan.placement` `{mode: own-file | inside-primary, component?, host?, reason?}` (по умолчанию own-file; inside-primary — `host` = id узла первичного кадра из его канона). Секции, хеши, решения по узлам и порядок сборки считает код — в plan их не пиши. Отказ перечисляет ВСЕ нарушения: исправь и повтори.
9. `daiver_plan_contract(track)` — что автономная сборка ещё спросила бы. Пункт из `open[]` с `ask` и владельцем human — вопрос
   пользователю ДОСЛОВНО (`id:"plan-<id>"`, у пункта с кадром `plan-<id>-<кадр>`, `kind:"plan-question"`) → `needs_input`; ответ запиши по
   `record` и вызови контракт снова. Пункты владельца plan чини сам по `detail` и `record`. Неблокирующий пункт без ответа (в том числе
   владельца host) — принятая деградация: она попадёт в ui-plan.md с ценой. `ready:true` и `signed:false` — ОДИН вопрос `id:"sign-plan"`, `kind:"sign-plan"`:
   строки `toSign` дословно и путь ui-plan.md → `needs_input`. Подпись не выписывай: согласие запишет оркестратор.
10. Конец. `signed:true` — гейт через раннер, затем `status:"completed"`:
   `python3 <forge>/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgeui --feature <KEY> --step-id ui-design --cmd "python3 <forge>/skills/forgeui/scripts/ui_gate.py design --project <toplevel> --feature <KEY>"`
   Раннер причин не печатает: упал — запусти `ui_gate.py design` напрямую, исправь и повтори. Шаг не обходи.
Верни JSON: {"step_id":"ui-design","status":"needs_input|blocked|completed|failed","frames":["<кадр> (<stage>)"],"warnings":["…"],
"blocked":"<причина>|null","pending_questions":[{"id":"…","kind":"map-rule|confirm-resolutions|plan-question|sign-plan|other","question":"…","rule":{…}}]}
```

### 3.1. Вопросы субагента и согласия (оркестратор)

`needs_input` — задай каждый вопрос из `pending_questions` через `ask_user_question` дословно и перезапусти субагента, передав ответы как
`<id>: <ответ дословно>`. Три вида вопросов несут согласие (`confirm-resolutions`, `map-rule`, `sign-plan`); его пишешь ты, сразу после
явного «да» (цитата — ответ пользователя дословно; вопрос не отрендерился — согласия нет):
- `confirm-resolutions` (варианты «Да, всё верно» / «Правки»). На «Да, всё верно»:
  `python3 <forge>/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key ui-resolve-<KEY> --bind-file .daiver/tracks/<KEY>/resolution-report.md --approved-by user --reason "вердикты резолва подтверждены" --evidence "<ответ дословно>"`
  Согласие привязано к ТЕКУЩЕМУ тексту отчёта: вердикт поправили — отчёт другой, подтверждение слетит и спросят снова. На «Правки»
  перезапусти субагента с их текстом, согласия не пиши.
- `map-rule` (в вопросе `rule`; варианты «Да, добавить» / «Нет»). На «Да, добавить» сразу:
  `python3 <forge>/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key ui-map-<KEY> --approved-by user --reason "правило карты: <match>" --evidence "<ответ дословно>"`
  затем `daiver_map_append(track, rule)` с этим `rule`, без `dryRun`. Согласие тратится на одно правило: следующему нужно своё «да».
  На «Нет» просто передай ответ субагенту.
- `sign-plan` (варианты «Да, подписываю» / «Правки»). На «Да, подписываю» сразу:
  `python3 <forge>/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key ui-plan-<KEY> --bind-file .daiver/tracks/<KEY>/plan.lock.json --approved-by user --reason "план секций подписан" --evidence "<ответ дословно>"`
  Подпись привязана к ТЕКУЩЕМУ plan.lock.json: правка плана, перетяжка макета, новый вердикт или спека её снимут, и спросят заново. Затем
  перезапусти субагента с `sign-plan: <ответ>`. На «Правки» перезапусти его с их текстом, согласия не пиши.

`plan-question` (адрес страницы, команда запуска, иллюстрации… из контракта плана) согласия не несёт: задай `question` дословно, ничего
не записывай, ответ верни субагенту как `<id>: <ответ>`.

`blocked` — скажи пользователю причину дословно: `library-source-unreachable` значит, что у резолва нет списка библиотеки (требования ДС не
построены или устарели, `library.scanPaths` пуст, сервер сторибука не отвечает); после исправления перезапусти шаг. `completed` — гейт
пройден через раннер, шаг закрыт (подпись плана записана): дальше сборка (§4).

## 4. Сборка → `ui-build`

Шаг ведёт субагент (роль dev): код пишется только здесь и только по подписанному плану (§3). Начинай, когда `daiver_status` ведёт кадр на `ui-build`; нет — вернись к §3.

```
description: "Build step for <KEY>"
subagent_type: general-purpose
prompt:
Корень репо: <toplevel>. Стори = имя трека: <KEY>. Каталог стори: <storydir>. Ответы пользователя на прошлые вопросы (`<id>: <ответ>`): <дословно | нет>.
Прогресс лежит на диске (`build-state.json` пишет только движок): после перезапуска иди с п. 1, готовые секции не трогай. Отказ инструмента читай целиком: он называет, что
исправить. Ничего не выдумывай: компонент, проп, путь импорта, тип данных — из плана, карты, библиотеки, спеки или вопрос; вопрос человеку — это `status:"needs_input"` с
`pending_questions` (сам не спрашивай). Значения (px, цвета, шрифты) — только из `daiver_section`, не с картинки.
1. `daiver_status`: ступень кадра `planned`, `building` или `built`, иначе СТОП (`failed`). Затем `daiver_build_state(track)`: `next` — твоя секция, `weightBudget` — бюджет плана
   (только с ним `daiver_section` отдаёт те же секции; бюджет конфига не подставляй), `planMd` — путь плана кадра. «План не соответствует макету» или «плана нет» — СТОП
   (`failed`, причина в `warnings`): план пересобирается на `ui-design`, подпись даёт человек.
2. Пролог (в каждом запуске; существующие файлы не пересоздавай; у ревизии — ответ `daiver_build_state` несёт `revision` — модуль, моки и токены уже в коде: ничего не создавай, только читай; у правки страницы — `page` в ответе — то же, пишешь только в её файлы `sources`): прочитай КАЖДЫЙ файл из `project.conventions` (`.daiver/config.yaml`; список пуст — скажи в итоге) — раскладка
   файлов, нейминг, экспорт, стили: они сильнее любых примеров; прочитай `planMd`; `daiver_color_tokens(track)` даёт `matched` (цвет → переменная проекта) и `raw` (сырое
   значение макета); каталог модуля — `<project.outputDir>/<имя ПЕРВИЧНОГО кадра>` — ОДИН на весь трек (конвенции важнее), в нём пустой `mocks.ts`, если его нет; кадр-состояние строится ВНУТРИ модуля своего экрана (план «Состояние экрана»: переменная, условие на регион, переключатель; removed — условие на скрытие; shared не пишется заново); корень не-первичного кадра — по разделу «Модуль» его `planMd` (own-file — свой компонент `component`; inside-primary — внутри компонента первичного кадра, в узле `host`), сам место не выбирай.
3. Каждая секция `next`, строго по порядку: а) `daiver_build_state_set(track, sectionId, status:"in_progress")`; б) `daiver_section(track, sectionId, weightBudget)` — строй ТОЛЬКО
   `node` (`ancestors` — контекст раскладки, не код) по правилам п. 4; в) `daiver_build_state_set(track, sectionId, status:"done", outputs, hash)`: `outputs` — файлы секции от
   корня проекта, `hash` — `section.hash` из ответа `daiver_section`; предупреждение о несуществующем пути исправь сразу. В ответе — следующая `next`; её нет — п. 5.
   Ревизия (`baseline.region` в ответе `daiver_section`): прочитай `files` ДО правки и правь их (added — в файлах родителя, changed — на месте, стабы `inferred:ref:…` не трогай, нового
   модуля и маршрута нет); `revision.removed` — удали этот код (используется ещё где-то — чужое оставь и назови в итоге). Правка страницы (`page.region` в ответе `daiver_section`): файл — чужой код, прочитай его ДО правки и пиши в его стиле; own/inside — правка на месте элемента `location` (`file:line…endLine`), ancestor — внутрь найденного предка между соседями; `page.removed` — удали элемент; вне региона ни строчки (реформат, переименование, «заодно») — гейт сверит дифф с базой.
4. Правила сборки (решения по узлам — в `planMd`; секции без кода — `noCode` в ответе `daiver_build_state` — движок закрыл сам, их не строй):
   (1) Стек — `daiver_status` → `config` (framework, language, styling, outputDir); имена и экспорт — по конвенциям.
   (2) Узел с решением `component` — компонент библиотеки чёрным ящиком: props плана, тексты из `content`, внутренности не разворачивай; `local` — своя разметка из анатомии
       узла; `ignore` — не строй; записи нет — простая разметка по канону. Строка плана с «×N» — все узлы с тем же figmaName и variants.
   (3) Пропсы — ровно из плана, сверь с библиотекой (`daiver_ds_details(components)`): дефолты не передавай, набор не расширяй.
   (4) Импорты дословно: `import` из `daiver_map_lookup` / `daiver_ds_lookup`, иконки — из своего пакета (`icons.import` карты), одинаковые пути сгруппируй; готового `import` нет — вопрос, не сочиняй.
   (5) Иконка — только компонент библиотеки; уверенного нет — `icons.fallback` (вопрос | помеченный плейсхолдер с именем иконки). Инлайн-`<svg>` не пиши никогда.
   (6) Арт (`art`) — файл: `daiver_svg_assets(track, moduleDir)` даёт import и JSX; файла нет — плейсхолдер из ответа, недостающий файл назови в итоге. Не рисуй, голый `<div>` не ставь.
   (7) Раскладка 1:1: direction → flex-direction, gap, padding, align.main → justify-content, align.cross → align-items, wrap; fill → flex:1/stretch, hug → auto/fit-content,
       число → точные px; `box` вне layout → absolute x/y/w/h. Раздел плана «Раскладка»: apply — hints обязательны, keep-px — px, group — ряд/колонка с gap и box оттуда; непринятое не применяй.
   (8) Корневой px-размер кадра не хардкодь (модуль занимает отведённую ширину), внутренние px — точно. Не работает фиксированный внутренний размер — вопрос, не решай сам.
   (9) Цвета: переменная — только из `matched`, иначе сырое значение макета; ближний цвет не подбирай.
   (10) `local → Имя` в плане — файл `<outputDir>/local/Имя` (расширение по конвенциям); общий (`shared` в «Local-компоненты» плана) пишет секция-создатель, остальные импортируют.
   (11) Тексты макета — в `mocks.ts` и `defaultProps`, не в разметку (`mocks.ts` дополняй). (12) Семантика по `tag` и структуре: section, header, ul. (13) `occluded` не строй.
   (14) Спеки («Спеки» плана, `daiver_spec`): States, Events, Validations подключают логику; «Данные» с путями типов/клиента — импорт оттуда, вызов в контейнере или хуке рядом,
       секция props-driven; Данные молчат — моки и `onX`-пропсы с `// TODO(forge-ui): wire business logic`; тип и путь клиента не выдумывай.
   Оболочка: ПОСЛЕДНЯЯ секция порядка плана после себя пишет корневой компонент экрана по разделу «Оболочка над секциями» (контейнеры с раскладкой, секции — импорты построенных
   компонентов); его файл — в её `outputs`. Экран из одной секции — оболочки нет.
5. Когда `next` пуст — гейт через раннер, он же собирает проект командой `project.buildCommand`:
   python3 <forge>/skills/pipeline-state/scripts/record_gate.py --project <toplevel> --skill forgeui --feature <KEY> --step-id ui-build --cmd "python3 <forge>/skills/forgeui/scripts/ui_gate.py build --project <toplevel> --feature <KEY>"
   Раннер причин не печатает: упал — запусти `python3 <forge>/skills/forgeui/scripts/ui_gate.py build --project <toplevel> --feature <KEY>` сам: он назовёт неготовые секции или
   напечатает хвост вывода сборки. Проект не собирается — чини код сам (это не вопрос человеку) и повтори; шаг не обходи.
Верни JSON: {"step_id":"ui-build","status":"completed|failed|needs_input","frames":["<кадр> (<готово>/<всего> секций)"],"warnings":["…"],"pending_questions":[{"id":"…","question":"…"}]}
```

`needs_input` — задай каждый вопрос через `ask_user_question` дословно и перезапусти субагента с `<id>: <ответ>`: сборка продолжится с `next`, а не с нуля; согласий этот шаг не пишет.
`failed` — скажи причину из `warnings` дословно (чаще всего макет или план менялись: план пересобирается на `ui-design`). `completed` — гейт пройден через раннер, шаг закрыт: дальше
`ui-verify` (§5).

## 5. Проверка → `ui-verify`

Итерацию ведёт СУБАГЕНТ, вопрос человеку и его решение — оркестратор. Цикл заканчивает человек: чисто или стоп. Правка кода — только через переоткрытие
`ui-build`; посреди итерации не чинят: гейты лишь собирают свидетельства.

**Ход итерации (субагент).** 0) `daiver_capabilities`: `ready: false` (`blocked: no-browser`) — цикл не стартует, скажи `reason` и `supply` дословно; браузер не
скачивай, не ставь (ни `npx playwright install`, ни brew/apt) и не запускай сам; `warnings` перескажи. 1) `daiver_verify_state` — номер, `iterationDir`, `priorReview`,
`goalImage`, `implementation`, `declinedSoFar`; `unrecordedIterations` — СНАЧАЛА запиши их. Нет goal image, адреса страницы или рецепта открытия (`implementation.need`) — спроси человека ОДИН раз (`needs_input`) и запиши ответ `daiver_track_manifest`, не
выдумывай; dev-сервер сам не запускай; `blocked` — останов, назови причину; рецепт, clip и viewport закреплены (`pinned`) и одинаковы во всех итерациях. 2) `daiver_screenshot` в `iterationDir`. 3) `daiver_verify_compare`. 4) `daiver_code_compare`. 5) Визуальный дифф: нет картинки дизайна у кадра — сначала `daiver_render_reference`, затем `daiver_visual_diff` — это СИГНАЛ, не вердикт (дефектов не создаёт, гейт не валит): `diffPercent` — ПРОЦЕНТ (3.18 = 3.18 %), записывай как есть, зоны без `explainedBy` — материал для обзора. Визуальный обзор — `daiver_visual_review` ДВУМЯ вызовами: первый отдаёт `images` (дизайн ПЕРВОЙ) и `prompt`, картинки открываешь САМ и отвечаешь только по тому, что на них видно (после доказанных дефектов — только остаток и БЕЗ значений); второй (`review` по форме, `opened` — пути как есть) пишет `visual-review.md`; не открыл картинки — `review: {skipped: причина}`, ничего не описывай по имени файла; находки (`zone: vision`) НЕПОДТВЕРЖДЁННЫЕ — показывай, они не блокируют. e2e: `daiver_e2e_plan` (имена файлов из `file`; сценарии со `skip` — REMOVED — НЕ пиши, но скажи, что скипнуты) → напиши тесты playwright по ОДНОМУ файлу на сценарий (ассерты по текстам `mocks.ts`; только в каталог `dir`, вне outputDir) → `daiver_e2e_run`; `gate: skipped` / `skipped` (нет сценариев, файлов, `@playwright/test`) — причину скажи ОДИН раз, ничего не ставь; падения — дефекты `zone: e2e` и блокируют, тесты не глуши. 6) `daiver_verify_state_record(iteration, gates{compare, code, visualDiff, review, e2e})` СРАЗУ, до слов человеку: статусы
compare/code и дефекты движок берёт из артефактов итерации сам; у кадра-состояния сравнение несёт `state.verdict` (reached | not-reached | ambiguous | unknown): not-reached — страница в другом состоянии, чини рецепт открытия, не код; у ревизии — `baseline.verdict` (current | previous | ambiguous | unknown): previous — на странице прежняя версия, сначала убедись, что dev-сервер отдаёт свежую сборку (это не жёсткий гейт); у правки страницы — `standing` дефекта (change | pre-existing | regression, три числа — в `nextStep`): pre-existing при scope change не блокирует, регрессия блокирует всегда. 7) `daiver_verify_report(iterationDir, date)` (interactive; автономно не рендерится): путь `report.html` — человеку ВСЕГДА, до слов о результате; `stateRecorded: false` — сначала запись; на странице человек отвечает на дефекты и ставит метки «Пометить проблему», файл `verify-decisions.json` скачивает и кладёт сам. 8) Верни JSON: `{"step_id":"ui-verify","status":"completed|needs_input|failed","iteration":N,"clean":bool,
"report":"<путь verify-report.md>","html":"<путь report.html>","blocking":["<id>"],"pending_questions":[…]}`.

**Оркестратор после итерации.** Назови путь `verify-report.md`. `clean` — спроси подтверждение и `daiver_verify_decide(decision: clean)`. Иначе покажи БЛОКИРУЮЩИЕ дефекты из
записи (severity, code, element, file:line, progress: FIXED / NOT FIXED / REGRESSED по `lifecycle`, `warnStalling`; отклонённые раньше — `declinedSoFar` — НЕ переспрашивай) и
спроси через `ask_user_question`: «чинить всё / пункты / стоп». Ответ записывай дословно: `daiver_verify_decide` (`fix_all` | `fix_some` + `fixed` | `stop`). Файл
`verify-decisions.json` кладёт ТОЛЬКО человек (хук закрывает путь модели): skip → declined, false_positive → noise, `reported` (метки руками) — наблюдения человека: чини дословно по заметке (`problem`), они не блокируют clean; `answeredElsewhere` — чинится в этом проходе, записывается итерацией своего кадра. Чинить: переоткрой `ui-build` и правь РОВНО согласованные пункты
по их fix-строкам, затем новая итерация. Стоп — немедленно, без «ещё одной правки»; с открытыми дефектами гейт пустит только согласие `ui-accept-<KEY>` после явного «да».

Снимок страницы — `daiver_screenshot` (его зовёт субагент итерации, не оркестратор): ОДИН вызов за итерацию, `waitMs` выбирай осознанно
(холодный dev-сервер 60000+, обычно 25000, перепроверка 5000). `ready: false` — страницу НЕ сравнивай и дефекты НЕ репорти: подними `waitMs`
или назови загрузчик через `waitForGone`; повтор без изменений отказан. `sanity.underRendered: true` — захват недорисован. Рецепт и `clipSelector`
(для попапа/модалки — их элемент) выбери один раз и держи одинаковыми во всех итерациях. Код-гейт — `daiver_code_compare` (без браузера, по исходникам из build-state): `status: skipped` читай вместе с `reason`, `unconfirmed` MISSING — не дефект, `unverified` и `dismissals` перескажи; DS_* он не выдаёт. Жёсткий гейт итерации — `daiver_verify_compare` (в ту же папку, что `daiver_screenshot`): `status` clean | defects | hard-gate-fail (страница — не этот кадр: чини захват, не код) | recapture (перезахвати); читай ОБА числа `matchRate` и `fidelity` и `unmatched`; у дефекта стабильный `id` (не от значений) и `lifecycle`, `minor` не блокирует.

---

## Что НЕ делать

- Не выдумывать кадры, ссылки на макеты, компоненты, пропсы, пути импортов и типов.
- Не читать целиком `.daiver/**/*.canonical.json` и сырые экспорты макета (DSL — сотни КБ) —
  только через инструменты движка (`daiver_outline` / `daiver_section` / `daiver_source_nodes`).
- Это форсит хук `ui-guard` (`exit 2`; отказ называет замену — повторять вызов и искать обход не надо): чтение канона и
  сырых экспортов (read_file, grep_search, cat/head/jq…; в shell можно лишь `ls`/`wc`/`stat`/`shasum`), код модуля вне
  `ui-build` или при неподписанном плане (правка на `ui-verify` — только через переоткрытие `ui-build`), инлайн-`<svg>`.
- Не писать в `.daiver/` руками (конфиг, карта, трек, канон, план, прогресс — только инструменты движка; форсит `state-write-guard`); не выписывать согласия
  `ui-plan-*`/`ui-accept-*`/`ui-map-*`/`ui-resolve-*` без явного «да» пользователя (нужна цитата).
- Не брать значения (px, цвета, шрифты) с картинки.
- Не коммитить и не пушить — доставку делает пользователь.

## Связь с бэк-частью

Стори с обеими частями — два независимых прогона с одним ключом: `/forge KEY` в бэк-репо (forge,
`sdd.md` с полем `**Фронт-часть:**`) и здесь (`ui-spec.md` с полем `**Бэк-часть:**`). Шов — API:
бэк описывает его в `sdd.md` §5, фронт в «Данных» ссылается на реальные типы/клиент или на моки.
Порядок не навязан: фронт может идти на моках раньше бэка.
