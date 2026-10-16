# forge-ui — задачи имплементации

Полная реализация движка forge-ui по спеке daiver (`docs/daiver/daiver spec.md`) и его обвязки в
forge. Решения и раскладка — `tasks/019-forge-ui.md`; каркас (Этап 0) — тег `forge/v0.5`.
Каждая задача — отдельный файл в [`tasks/`](tasks/): зачем, ссылки на спеку (§, INV, QG),
что сделать, где, критерий готовности, зависимости.

## Правила для каждой задачи (Definition of Done)

1. Python 3.9, только stdlib; `from __future__ import annotations`; `encoding=` у каждого
   `open/read_text/write_text`. Внешнее — только найденный Chromium и `node`+`typescript` фронт-репо
   (нет их — гейт `skipped` с причиной, не падение).
2. Движок — `skills/forgeui/scripts/daiver/<модуль>.py`; инструмент MCP — запись в `daiver/tools.py`
   (схема + ручная проверка аргументов) и строка в `EXPECTED_TOOLS` `hooks/test_daiver_mcp.py`.
3. Тесты — `skills/forgeui/scripts/test_<группа>.py`, имя группы как в QG-U спеки (`test_pixso_pull`,
   `test_layout_inference`…). Фикстуры — `skills/forgeui/fixtures/` (обезличенные).
4. `references/inv-matrix.json`: закрытые задачей инварианты получают ссылку на тест (задача X.1).
5. Поведение, видимое модели, — в бриф `skills/forgeui/SKILL.md` (кратко, по шагу); гейт шага — в
   `ui_gate.py`, а не только в тексте брифа.
6. `python3 -X utf8 skills/run_all_tests.py` и `bash hooks/run-hook-tests.sh` — зелёные под системным
   3.9. Отклонение от спеки — строкой в разделе «Отклонения» ниже, не молча.

## Карта задач

Статус: `done` · `todo` · `blocked` (причина в файле задачи). Строки идут в порядке зависимостей —
задачи берутся сверху вниз; каждая закрытая задача — отдельный коммит в ветку `forge/forge-ui`.

| Этап | Задача | Зависит от | Статус |
|---|---|---|---|
| 0 | каркас: ветка, ui-spec, конфиг, MCP, поставка (`forge/v0.5`) | — | done |
| 1 | [1.0 Фикстура образца Pixso](tasks/1.0-pixso-fixture.md) | — | done |
| 1 | [1.1 Каноническая модель](tasks/1.1-canonical-model.md) | — | done |
| 1 | [1.9 Трек: track.yaml, кадры, goal image, source_nodes](tasks/1.9-track-manifest.md) | 1.1 | done |
| 1 | [1.2 Pixso pull: slim и регистрация кадра](tasks/1.2-pixso-pull.md) | 1.0, 1.1, 1.9 | done |
| 1 | [1.3 Pixso → канон: структура и геометрия](tasks/1.3-pixso-structure.md) | 1.1, 1.2 | done |
| 1 | [1.4 Pixso → канон: материализация инстансов](tasks/1.4-pixso-materialize.md) | 1.3 | done |
| 1 | [1.5 Pixso → канон: краски, типографика, токены](tasks/1.5-pixso-paints-type.md) | 1.3 | done |
| 1 | [1.6 Векторный арт и svg-ассеты](tasks/1.6-vector-art.md) | 1.3 | done |
| 1 | [1.7 Вывод раскладки](tasks/1.7-layout-inference.md) | 1.3 | done |
| 1 | [1.8 Outline и section](tasks/1.8-outline-section.md) | 1.1, 1.7 | done |
| 1 | [1.10 daiver_status: лестница трека](tasks/1.10-status-ladder.md) | 1.9, 1.8 | todo |
| 2 | [2.1 Карта компонентов](tasks/2.1-component-map.md) | — | todo |
| 2 | [2.2 Требования ДС из сторей/исходников](tasks/2.2-ds-requirements.md) | — | todo |
| 2 | [2.3 ds_inventory / ds_lookup / ds_details](tasks/2.3-ds-lookup.md) | 2.2 | todo |
| 2 | [2.4 Firewall резолва](tasks/2.4-resolve-firewall.md) | 1.8, 2.1 | todo |
| 2 | [2.5 Шаг ui-design: бриф и отчёт резолва](tasks/2.5-ui-design-step.md) | 2.3, 2.4 | todo |
| 2 | [2.6 План, контракт готовности, подпись](tasks/2.6-plan-contract.md) | 2.5 | todo |
| 3 | [3.1 build_state](tasks/3.1-build-state.md) | 1.8 | todo |
| 3 | [3.2 Шаг ui-build: пролог и правила сборки](tasks/3.2-ui-build-step.md) | 2.6, 3.1 | todo |
| 3 | [3.3 Хук ui-guard](tasks/3.3-ui-guard.md) | 2.6 | todo |
| 3 | [3.4 Обобщения control-plane](tasks/3.4-control-plane.md) | — | todo |
| 4 | [4.1 Браузер: Chromium и CDP-клиент](tasks/4.1-browser-cdp.md) | — | todo |
| 4 | [4.2 daiver_screenshot и dom-snapshot](tasks/4.2-screenshot.md) | 4.1 | todo |
| 4 | [4.3 Матчинг макет ↔ страница](tasks/4.3-matching.md) | 1.1, 4.2 | todo |
| 4 | [4.4 Правила проверки и допуски](tasks/4.4-check-rules.md) | 4.3 | todo |
| 4 | [4.5 DS-гейт](tasks/4.5-ds-gate.md) | 4.3, 2.3 | todo |
| 4 | [4.6 Подавление, группировка, id; verify_compare](tasks/4.6-verify-compare.md) | 4.4, 4.5 | todo |
| 4 | [4.7 Код-гейт](tasks/4.7-code-gate.md) | 4.3, 3.1 | todo |
| 4 | [4.8 Цикл verify: состояние, решения, отчёт .md](tasks/4.8-verify-loop.md) | 4.6, 4.7 | todo |
| 4 | [4.9 URL реализации и dev-сервер](tasks/4.9-implementation-url.md) | 4.2 | todo |
| 5 | [5.1 Эталонный рендер и пиксельный дифф](tasks/5.1-reference-diff.md) | 4.2 | todo |
| 5 | [5.2 Визуальный обзор](tasks/5.2-visual-review.md) | 5.1, 4.8 | todo |
| 5 | [5.3 report.html](tasks/5.3-report-html.md) | 4.8, 5.1 | todo |
| 5 | [5.4 Разметка в отчёте и файл решений](tasks/5.4-report-annotate.md) | 5.3 | todo |
| 5 | [5.5 e2e по сценариям ui-spec](tasks/5.5-e2e.md) | 4.8 | todo |
| 6 | [6.1 Мультикадровый трек](tasks/6.1-multi-frame.md) | этап 4 | todo |
| 6 | [6.2 Состояния экрана (stateOf)](tasks/6.2-frame-states.md) | 6.1 | todo |
| 6 | [6.3 Ревизии макета](tasks/6.3-revisions.md) | 6.1 | todo |
| 6 | [6.4 Правка существующей страницы](tasks/6.4-page-baseline.md) | 6.3 | todo |
| 7 | [7.1 Директива и прогон: run, blocked](tasks/7.1-run-directive.md) | этап 4 | todo |
| 7 | [7.2 Автономный триаж verify](tasks/7.2-autonomous-triage.md) | 7.1 | todo |
| X | [X.1 Матрица инвариантов](tasks/X.1-inv-matrix.md) | — | todo |
| X | [X.2 Figma-адаптеры](tasks/X.2-figma-adapters.md) | образцы | blocked |
| X | [X.3 Упаковка extension](tasks/X.3-extension-packaging.md) | проба | blocked |
| X | [X.4 Боевой прогон на GigaCode](tasks/X.4-battle-test.md) | этап 4 | todo |

Критический путь до первого «экран по макету собран и сверен»: 1.0 → 1.1 → 1.9 → 1.2 → 1.3 → 1.4/1.5
→ 1.8 → 2.1 → 2.4 → 2.5 → 2.6 → 3.1 → 3.2 → 4.1 → 4.2 → 4.3 → 4.4 → 4.6 → 4.8. Остальное — ширина.

## Отклонения от спеки daiver (именованные)

| Что | Решение | Почему |
|---|---|---|
| §17 телеметрия, `daiver_tele_note/_export` (INV-H*, C37, F19, G5–G7, G9, G12) | не делаем | forge снял логи 2026-08-04; свидетельства прогона — events.jsonl forge |
| §12.5 `daiver_sdd_integrate` | не делаем | хост — сам forge-ui |
| `daiver_plan_sign` | заменён согласием `ui-plan-<KEY>` | подпись — решение человека, не инструмент модели |
| QG-U как порт эталонной сюиты, QG-X parity | тесты из INV и констант §20.4 | эталонного кода нет |
| autoRoute (§15.7, INV-A12) | конвейер стартует только `/forge` | модель запуска forge — команды |
| confirmPlan «logged» в автономии | `blocked: plan-unsigned` | модель согласий forge |
| Vue/Svelte в код-гейте | `skipped` с причиной | так и в спеке — shippable только React |
| Новые инструменты: `daiver_plan_write` (2.6), `daiver_color_tokens` (3.2), `daiver_e2e_plan` (5.5), `daiver_propose_sources` (6.4) | добавлены к 39 инструментам спеки | план и карта цветов — валидацией и арифметикой в коде, а не файлом модели (INV-A1, A11) |
| Pixso DSL инлайн (`dslJson`) | только `dslPath` | payload через контекст модели запрещён (INV-B3) |
| §7.3 «overridden:true для definition-дефолтов» | дефолты библиотеки — `overridden:false` | противоречие внутри спеки; §6.4: false = плейсхолдер библиотеки, не копировать |
| INFERRED_NESTED_INSTANCE_SWAP, VIRTUAL_HIERARCHY_CYCLE | не реализованы | в образце нет признаков — правила не из чего выводить; вернуться при втором образце |
| §7.4 коллапс арта: FRAME с собственной заливкой/обводкой/тенью | не склеивается (рисунок внутри него — сам по себе) | спека перечисляет исключения без этого случая; иначе кнопка-иконка «фрейм с фоном + глиф» стала бы картинкой; не проверено данными |
| §8.1 п.5: разделительный зазор больше группирующего более чем в 1.3× | к `GAP_JUMP` добавлено «и больше на `LAYOUT_TOLERANCE`» | зазоры 1.0 и 1.4 разнятся меньше допуска — по п.4 это один gap, и делить их на группы нельзя; не проверено данными |
| §8.2 `main: space-between` «только когда дети прижаты к обоим краям» | кандидат ставится при равных ведущем и замыкающем отступах И зазоре больше допуска | дети, заполняющие контейнер впритык, раскидывать нечем; не проверено данными |
| §9.1 `outline` — «компактное дерево» | список строк (одна строка = узел, отступ = глубина) вместо вложенных объектов; поля те же (kind/name/weight/nChildren, text ≤ 80, box, layout/size) + `<figmaName>` у инстанса, `art`, `occluded` | MCP печатает ответ `indent=1`: объект узла раскрывается в десяток строк, дерево из объектов по транспорту лишь 1.1–1.2× легче канона, строки — 2.6–3.8×; имя слоя инстанса («Steps=OFF») компонент не называет |
| §9.1 «дайджест ~5–6× легче канона» | потолок в коде: дерево — остаток четверти канона после остального ответа, но не меньше бюджета секции; не влезает — свёртка простых листьев, затем уровней, `folded` в ответе | свойство «легче» держится кодом, а не надеждой; без потолка текстовый экран давал дайджест вдвое легче канона; не проверено данными (большого экрана нет) |
| §9.3 `daiver_section(canonicalPath, sectionId)` | необязательный `weightBudget` (тот же, что у outline); без него — конфиг/24000, как в спеке | секции зависят от бюджета, а outline принимает его аргументом: без параметра id мелких секций были бы чужими |
| §9.3 `ancestors` — «flex-контекст» | + `style`, `tokens`, `tag` предков | оболочка над секциями ни в одну секцию не входит, и без её стиля её не из чего собрать; не проверено данными |

## Нужно от пользователя

- Проба project-level extension на GigaCode (X.3).
- Второй образец Pixso — полноэкранный, длинный, без auto-layout (для 1.7/1.8). Для 1.8 на нём же проверить: границы секций и их число (соседи в одну секцию не склеиваются — 60 небольших панелей дают 60 секций), вес дайджеста против канона (потолок и «≥ 4×» подобраны по синтетике из узлов «Modal»), форму unproven на реальном кадре. Вывод раскладки (§8) доказан пока только синтетикой (образец «Modal» целиком на auto-layout): на реальном кадре проверить направление и padding одиночного ребёнка, чтение «прижаты к обоим краям» (space-between), порог grid, двухколоночные россыпи без обёрток, обводки рамок против padding и достоверность `width/height` рамок в потоке чужой раскладки (1.7).
- Образцы Figma json-a / json-b / html, если Figma нужна (X.2).
- Образец Pixso с градиентной и картинной заливкой, несколькими заливками на узле, процентными единицами
  шрифта, стилями обводки/эффектов — допущения `ingest/style.py` (порядок слоёв, угол градиента, PERCENT) пока
  не проверены данными.
- Образец Pixso с настоящей векторной иллюстрацией на экране (группа из десятков шейпов: GROUP/FRAME + VECTOR/
  BOOLEAN_OPERATION) и svg-экспорт этого узла — правила §7.4 (что считать группой шейпов, имена слоёв, попадает ли
  opacity верхушки в .svg) пока выведены из текста спеки, а не из данных (1.6).
- Сборщик и загрузчик SVG целевого фронт-репо (Vite + vite-plugin-svgr, react-scripts, webpack): форма import для
  `svg.render` component и inline в `daiver_svg_assets` определяется по package.json и на реальном репо не прогонялась (1.6).
