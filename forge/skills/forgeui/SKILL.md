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

> **Этап реализации.** Сейчас forge-ui исполняет `ui-spec` полностью, а `ui-design` — до подтверждённого резолва
> компонентов (§3). План секций и подпись плана, `ui-build`, `ui-verify` ещё строятся (tasks/019-forge-ui.md): их гейт
> отвечает exit 3 «не реализовано». Дойдя до него, честно остановись и скажи пользователю, докуда конвейер дошёл.
> Код вручную «вместо движка» не пиши.

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

## 3. Дизайн по макету → `ui-design` (макет → канон → компоненты; план секций — следующий этап)

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
   `frameUrl` — ссылка из таблицы, `frame` — имя кадра (латиница, цифры, `-`, `_`). Растр кадра (JPEG/PNG: Pixso MCP или пользователь) —
   `daiver_track_manifest(track, frame, rasterPath)`; без растра идти можно, но `goal-image-missing` перескажи.
3. Stage `requirement` — `daiver_normalize(track, frame)`. Потом `daiver_svg_assets(track, frame)`: на каждый `missingArt` — SVG
   иллюстрации (Pixso MCP или пользователь) → `daiver_svg_assets(track, frame, slug, svgPath)`; иллюстрацию не рисовать.
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
   (px, цвета, шрифты) с неё не брать; (в) осталось неясным — вопрос пользователю, не догадка. Раскладку px → flex меняет только
   план секций; код на этом шаге не пишется.
8. Конец. Гейт напрямую (раннер `record_gate` причин не печатает):
   `python3 <forge>/skills/forgeui/scripts/ui_gate.py design --project <toplevel> --feature <KEY>`. exit 1 — причины в выводе,
   исправь и повтори. exit 3 — резолв закончен и подтверждён, плана секций и подписи в движке пока нет: `status:"stopped"`.
   Шаг не закрывай и не обходи.
Верни JSON: {"step_id":"ui-design","status":"needs_input|blocked|stopped|failed","frames":["<кадр> (<stage>)"],"warnings":["…"],
"blocked":"<причина>|null","pending_questions":[{"id":"…","kind":"map-rule|confirm-resolutions|other","question":"…","rule":{…}}]}
```

### 3.1. Вопросы субагента и согласия (оркестратор)

`needs_input` — задай каждый вопрос из `pending_questions` через `ask_user_question` дословно и перезапусти субагента, передав ответы как
`<id>: <ответ дословно>`. Два вида вопросов несут согласие; его пишешь ты, сразу после явного «да» (цитата — ответ пользователя
дословно; вопрос не отрендерился — согласия нет):
- `confirm-resolutions` (варианты «Да, всё верно» / «Правки»). На «Да, всё верно»:
  `python3 <forge>/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key ui-resolve-<KEY> --bind-file .daiver/tracks/<KEY>/resolution-report.md --approved-by user --reason "вердикты резолва подтверждены" --evidence "<ответ дословно>"`
  Согласие привязано к ТЕКУЩЕМУ тексту отчёта: вердикт поправили — отчёт другой, подтверждение слетит и спросят снова. На «Правки»
  перезапусти субагента с их текстом, согласия не пиши.
- `map-rule` (в вопросе `rule`; варианты «Да, добавить» / «Нет»). На «Да, добавить» сразу:
  `python3 <forge>/skills/pipeline-state/scripts/record_approval.py --project <toplevel> --key ui-map-<KEY> --approved-by user --reason "правило карты: <match>" --evidence "<ответ дословно>"`
  затем `daiver_map_append(track, rule)` с этим `rule`, без `dryRun`. Согласие тратится на одно правило: следующему нужно своё «да».
  На «Нет» просто передай ответ субагенту.

`blocked` — скажи пользователю причину дословно: `library-source-unreachable` значит, что у резолва нет списка библиотеки (требования ДС не
построены или устарели, `library.scanPaths` пуст, сервер сторибука не отвечает); после исправления перезапусти шаг. `stopped` — скажи:
«резолв компонентов закончен и подтверждён; план секций и подпись плана — следующий этап, forge-ui его пока не умеет». Шаг не закрывай.

## 4. Сборка → `ui-build` (строится: этап 3)

Будет: сборка модуля строго по плану секций (`daiver_build_state` / `daiver_section`), затем
`project.buildCommand`. Запись кода — только на этом шаге и только по подписанному плану.

## 5. Проверка → `ui-verify` (строится: этапы 4–5)

Будет: итерации — захват страницы → сравнение с макетом (жёсткий гейт) → код-гейт → визуальный
обзор (находки зрения не блокируют) → решение пользователя «чинить / стоп». Правка кода — через
переоткрытие `ui-build`. Цикл заканчивает пользователь: чисто или стоп с согласием
`ui-accept-<KEY>`.

---

## Что НЕ делать

- Не выдумывать кадры, ссылки на макеты, компоненты, пропсы, пути импортов и типов.
- Не читать целиком `.daiver/**/*.canonical.json` и сырые экспорты макета (DSL — сотни КБ) —
  только через инструменты движка.
- Не писать `.daiver/config.yaml` и `component-map.yaml` руками; не выписывать согласия
  `ui-plan-*`/`ui-accept-*`/`ui-map-*`/`ui-resolve-*` без явного «да» пользователя (нужна цитата).
- Не брать значения (px, цвета, шрифты) с картинки.
- Не коммитить и не пушить — доставку делает пользователь.

## Связь с бэк-частью

Стори с обеими частями — два независимых прогона с одним ключом: `/forge KEY` в бэк-репо (forge,
`sdd.md` с полем `**Фронт-часть:**`) и здесь (`ui-spec.md` с полем `**Бэк-часть:**`). Шов — API:
бэк описывает его в `sdd.md` §5, фронт в «Данных» ссылается на реальные типы/клиент или на моки.
Порядок не навязан: фронт может идти на моках раньше бэка.
