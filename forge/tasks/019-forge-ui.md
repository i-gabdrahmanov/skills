# 019 — forge-ui: фронтовая поставка forge на основе спеки daiver

**Статус (2026-10-15):** Этап 0 сделан — поставка, ветка `forgeui`, фронт-спека с гейтом, скелет
движка и MCP. Этапы 1–5 (дизайн по макету, сборка, проверка) не начаты. Боевого прогона нет.

## Зачем

У стори бывает бэк-часть, фронт-часть или обе, у каждой своя спека. Бэк и фронт — в разных
репозиториях: в бэк-репо работает forge (`/forge KEY` → `sdd.md` → …), во фронт-репо — **forge-ui**
(`/forge KEY` → `ui-spec.md` → …). Детерминированное ядро forge-ui — воссоздание стороннего
фреймворка daiver по спеке `docs/daiver/daiver spec.md` (эталонного кода нет — только текст).
Образец реального входа — `docs/daiver/`: Pixso DSL модалки (`dsl.json`), её растр и SVG.

Ценность переноса — не копия daiver, а daiver с enforcement'ом forge: запреты, которые в
оригинале живут текстом GIGACODE.md (не читать канон целиком, не писать инлайн-`<svg>`, не
чинить посреди итерации, не подписывать план за человека), здесь форсятся хуками и гейтами.

## Решения

| # | Решение | Почему |
|---|---|---|
| D1 | forge-ui — **поставка из того же источника**, профиль `ui` (`deploy.sh --profile ui`); бэк — профиль `back` (по умолчанию) | control-plane (хуки, стейт, согласия, rollback, archive) общий; форк разошёлся бы с первым же фиксом боевого прогона |
| D2 | Движок — **Python 3.9 stdlib** (выбор пользователя), пакет `skills/forgeui/scripts/daiver/`, MCP `forge-ui` (`hooks/daiver_mcp.py`), инструменты `daiver_*` | один язык и один прогон тестов; гейты зовут тот же код CLI (`ui_gate.py`) |
| D3 | **Одна команда** `/forge` на репо, во фронте **4 шага** = 4 решения человека: `ui-spec` → `ui-design` → `ui-build` → `ui-verify` | восемь шагов и отдельная команда — «слишком много» (пользователь); под-этапы daiver (track/resolve/plan) живут внутри `ui-design` |
| D4 | Фронт-спека — `docs/feature-pipeline/<KEY>/ui-spec.md` в разделах парсера спек daiver (Кадры, Данные, Состояния, События, Валидации, Сценарии) | один документ для ревьюера и движка; кадры → вход `ui-design`, сценарии → e2e |
| D5 | Связь частей стори — ключ Jira и поля шапки: `**Фронт-часть:**` в `sdd.md`, `**Бэк-часть:**` в `ui-spec.md`; «нет» пишется явно, гейты требуют поле | отсутствие поля ≠ «нет»; шов — API (`sdd.md` §5 ↔ «Данные» ui-spec) |
| D6 | Пакет — GigaCode-extension внутри проекта (`<repo>/.gigacode/extensions/forge-ui/`), подключённый к рантайму ССЫЛКОЙ (`gigacode extensions link`), включение — только для workspace; forge — такой же отдельный extension в бэк-репо, установки раздельные (`install.sh forge\|forge-ui`, FORGE.md [BR-22], 2026-10-30) | рантайм (qwen-code 0.21.14 и 0.22.2) читает extensions только из `~/.gigacode/extensions`, `loadExtensionsFromDir` нигде не вызывается, поэтому прежняя проба «extension в каталоге проекта» отменена: link кладёт в домашний каталог только запись с путём источника, файлы остаются в проекте (shell к ним разрешён) |
| D7 | Из daiver **не переносим**: телеметрию §17, `daiver_sdd_integrate`, инструмент `daiver_plan_sign`, parity QG-X, Vue/Svelte код-гейт | forge снял логи (2026-08-04); хост — сам forge-ui; подпись — согласие человека; эталона нет |
| D8 | Подпись плана — согласие `ui-plan-<KEY>` (цитата, `--bind-file` на lock плана); стоп verify с дефектами — `ui-accept-<KEY>`; правило в карту — `ui-map-<KEY>` | все три в `forge_events.CONSENT_PREFIXES`: модель не вправе выписать их себе сама |
| D9 | Автономный прогон без подписанного плана — `blocked: plan-unsigned`, а не «confirmPlan logged» | модель согласий forge строже daiver |
| D10 | Конфиг `.daiver/config.yaml` = схема §5.1 + `project.buildCommand` (гейт сборки) + `verify.vision: on\|off` | спека требует typecheck/build, но команду не называет; зрение — флаг, а не надежда |
| D11 | Модель — GLM 5.3 Flash: картинки читает (JPEG проверен пользователем, PNG — «скорее всего»). Визуальный обзор включён, находки зрения не блокируют | при `vision: off` движок сам пишет гейт `skipped` — «описать непросмотренную картинку» закрыто кодом |
| D12 | YAML-разбор — свой `daiver/yamlite.py`, **не** вынос из `forgetech/_techlib.py` (отступление от плана) | `_techlib` отдаёт плоский словарь «a.b.c» → строка ради diff конфигов; движку нужно дерево с типами и flow-коллекциями |

## Что сделано на Этапе 0

- **Ветка `forgeui`** в `hooks/_branches.py` (mode `ui`, префикс `ui-`, 4 шага, роли spec/design/
  dev/test, `verify_to_impl ui-verify → ui-build`); новое поле реестра `doc_approval` — `update.py`
  берёт утверждение документа ветки (`ui-spec-approved-<KEY>`) из реестра, а не из своей копии.
  Регистрация по карте 018: `params-registry` (`inputs.mode` += ui), `risk-policy` (`gate_cmd_expect`
  ui-*, `phase_approvals` ui-build/ui-verify → `ui-plan-{feature}`), `forge_events.CONSENT_PREFIXES`
  (+ ui-plan, ui-map, ui-accept), `skill-paths.json`, пины `test_branches_registry`,
  `test_phase_enforcement_coverage`, `test_phase_consistency`.
- **Фронт-спека:** шаблон `skills/forgeui/references/ui-spec-template.md`; парсер спек по §11.2
  (`daiver/specs.py`: разделы и RU-алиасы, OpenSpec/GWT, REMOVED, id `<stem>#<n>`); форма ui-spec
  (`daiver/uispec.py`); гейт `check_ui_spec.py`.
- **Бэк-сторона:** поле `**Фронт-часть:**` в `sdd-template.md`, обязательно в `check_sdd_doc.py`;
  SDD-писатель спрашивает, если по стори неясно; скоуп-чек full-пути не дробит стори из-за UI.
- **Движок (скелет):** `daiver/{config,yamlite,init,status,tools}.py`; инструменты `daiver_status`,
  `daiver_init` (детект с доказательствами → запись конфига по ответам, существующий не
  перезаписывает), `daiver_spec`. MCP `hooks/daiver_mcp.py`. Гейт `ui_gate.py` для ui-design/
  build/verify отвечает **exit 3 «не реализовано»** — честная остановка, шаг не закрывается.
- **Поставка:** `profiles/{back,ui}.profile`; `deploy.sh --profile`; реестр установки — из
  положенного; `.gigacode/.forge-profile` → MCP по профилю (`resolve_hook_paths`), проверка путей
  реестра по профилю (`doctor`/`check_paths`), проба MCP профиля (`preflight`); `uninstall` снимает
  профиль и `forge-ui`. `build_system` += npm|pnpm|yarn (детект по lock-файлу), фронтовому стеку не
  нужен `package_root` — preflight проходит на фронт-репо. Бэк-профиль — как раньше, без `forgeui`.
- **Тесты:** `skills/forgeui/scripts/test_{daiver_specs,check_ui_spec,daiver_config}.py`,
  `hooks/test_daiver_mcp.py`, `test_deploy_profiles.py` (настоящие deploy/uninstall в оба профиля),
  плюс правки существующих пинов.

## Чего пока НЕТ (чтобы никто не думал, что это форсится)

- ~~`ui-guard.py`~~ — сделано (задача 3.3): запрет чтения канона/сырого экспорта, запись кода только на
  `ui-build`, запрет инлайн-`<svg>`, сверка подписи плана (`signature_state`).
- ~~`state-write-guard` не закрывает `.daiver/**`; `pii-boundary` блокирует PII-подобные строки в `mocks.ts`,
  даже если они из макета; фронтовый прод-код не поднят до R2; `BUILD_CMD_RE` не знает npm/npx/tsc/vite~~ —
  сделано (задача 3.4): `.daiver/` охраняется, мок-данные макета проходят дословно из канона, `.tsx/.jsx/.vue/.svelte` —
  R2, сборка фронта видна ролям SoD, продуктивные инструменты шага не вызывает главный агент (`phase_tools`).
- ~~Установка extension-раскладкой — после пробы (D6)~~ — сделано 2026-10-30: `install.sh forge-ui` (D6).

## Установка (D6)

`bash install.sh forge-ui <repo>` → `<repo>/.gigacode/extensions/forge-ui/` + ссылка в рантайме (INSTALL.md).
Прежняя проба (`forge-ui/probe/`: грузит ли GigaCode extension из каталога проекта) снята: установка
через ссылку от ответа на неё не зависит. Не проверено на GigaCode: `extensions link` и
`--scope workspace` форка (на стоковом qwen 0.22.2 — работают) — задача X.4.

## Этапы дальше

Нарезка на задачи с критериями готовности — `docs/forge-ui/README.md` (45 задач, этапы 1–7 и
сквозные). Ниже — сжатый обзор.

1. **ui-design, часть 1 — макет → канон** по образцу `docs/daiver/`. Факты образца: DSL 2.1.15 —
   `dsl.{pixTreeDslNodes, pixComponentTreeDslNodes, localStyleMap, …}`; дерево экрана — фрейм и 3
   инстанса без детей, анатомия и все 116 текстов (`PARAGRAPH.nodeText`) — в 20 определениях
   компонентов → **materialize обязателен**; оверрайды — `props[]` по `pathString`, видимость слотов
   — `propAssignMap`, варианты — `propRefMap`, библиотечность — `publishFile`; токены —
   `inherit*StyleID` → `localStyleMap`. DSL 490 КБ ≈ 150 тыс. токенов — только файлом (`dslPath`).
   Растр 648×293 при кадре 600×245 — поля 24 px под тень чёрные (JPEG без альфы): goal обрезается
   по габариту кадра. Шрифт SB Sans Text проверяется при первом запуске. Образец — корп-дизайн:
   в git только обезличенная усечённая копия. Желателен ещё полноэкранный кадр без auto-layout.
2. **ui-design, часть 2** — карта компонентов, DS-требования (node-хелпер на typescript фронт-репо),
   resolve query/finalize, plan.json/buildOrder, контракт готовности, lock плана и подпись.
3. **ui-build** + `ui-guard` и всё из раздела «Чего пока нет».
4. **ui-verify** — CDP-клиент (stdlib WebSocket к найденному Chromium; сделан в 4.1: поиск браузера, WebSocket/CDP,
   `daiver_capabilities`), dom-snapshot, compare,
   код-гейт, verify-state, отчёт. Правка кода — переоткрытием `ui-build` (`verify_to_impl`).
5. **Визуальный слой** — дифф в Chromium (canvas), визуальный обзор (PNG модели; не прочтёт — JPEG),
   e2e по сценариям `ui-spec.md`.

Открытые вопросы: форма фронт-мастера (куда мержить `ui-spec.md`); обмен API-контрактом через
мастер-репо; Jira-сабтаски фронт-части; фронтовые fix/tech.
