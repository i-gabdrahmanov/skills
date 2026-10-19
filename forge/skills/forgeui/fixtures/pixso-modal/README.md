# pixso-modal — обезличенная фикстура Pixso DSL

Кадр «Modal» (диалог подтверждения с заголовком, пояснением и кнопками «Отмена»/«Подтвердить»)
корпоративного дизайна — настоящий экспорт Pixso (`dslVersion 2.1.15`, `converterVersion 2.2.13`).
Оригинал (`docs/daiver/dsl.json`, 490 КБ) в git не лежит; здесь — его обезличенная копия.

Получена командой (из корня `forge/`; результат побайтно воспроизводится, если оригинал есть локально —
это проверяет `test_anonymize_pixso.py`):

```
python3 skills/forgeui/scripts/tools/anonymize_pixso.py docs/daiver/dsl.json \
    skills/forgeui/fixtures/pixso-modal/dsl.json --font "SB Sans Text=Fixture Sans"
```

| Файл | Что это |
|---|---|
| `dsl.json` | DSL кадра, обезличенный; 489 798 байт, минифицированный, одна строка |

## Что заменено, что оставлено

Заменено (детерминированно, по значению — одно значение везде получает одну подмену):
- идентификаторы библиотеки: `publishFile`, `publishID`, `overrideKey`, `componentKey` — 235
  значений, форма сохранена (22 символа base64url / 40 hex / «число:число»), включая вхождения
  `publishID` как `defaultValue` свойств-слотов типа `instance`;
- тексты `nodeText`: первое слово из букв → `Текст<k>`, остальные → `слово`, цифры и знаки на месте
  (`«Количество документов - 20» → «Текст9 слово - 20»`); 13 уникальных текстов заменено, число «3»
  осталось; один исходный текст — одна подмена в мастере и в оверрайдах инстансов;
- шрифт `SB Sans Text` → `Fixture Sans` (во всех значениях и ключах, в том числе `sourceMapByUrl`).

Оставлено как структура: `guid`, типы узлов, геометрия, краски, auto-layout, названия компонентов
и слоёв, названия стилей (`Static/T&I/Base/Primary`, `Text/B1`), ссылки между узлами. Недостижимых
из экрана определений в образце нет — все 20 на месте.

## Устройство образца (факты, на которые опираются задачи 1.2–1.8)

- Обёртка `{"dsl": {…}}`; ключи тела: `converterVersion, dslVersion, isContainFixed, localStyleMap
  (42 стиля), pixComponentTreeDslNodes (20), pixTreeDslNodes (1), sourceMapByUrl (шрифты),
  specialNode (6), variableMap, variableSetMap`.
- **Экран** — фрейм `Modal` 600×245, auto-layout колонка (`autoLayoutDirection: 2`), две
  `DROP_SHADOW`, и ТРИ инстанса БЕЗ детей: `Steps=OFF`, `Variant=Default`, `Steps=OFF`. Текстов в
  дереве экрана нет вовсе.
- **Определения** — 20 верхнеуровневых `SYMBOL` (360 узлов: SYMBOL 122, INSTANCE 132, PARAGRAPH 69,
  FRAME 13, RECTANGLE 11, ELLIPSE 7, VECTOR 5, BOOLEAN_OPERATION 1). Вся анатомия и все тексты —
  здесь: `PARAGRAPH.nodeText`, шрифт/размер/межстрочный/трекинг записаны на самом узле
  (`fontFamily, fontSize, fontStyle, lineHeightNumber/Unit, letterSpacingNumber/Unit`), цвета —
  `fillPaints[{type:"SOLID", color:{r,g,b: 0..255, a: 0..1}}]`, токены — `inherit{Fill,Text,Stroke,
  Effect}StyleID` → ключ в `localStyleMap` (163 узла со ссылкой).
- **Инстанс → определение.** 135 инстансов (3 на экране, 132 внутри определений), все
  `mainComponent` разрешаются. Два вида: 77 — варианты набора (`layerType: COMPONENT_SET`):
  `mainComponent` — guid варианта внутри определения, `mainStateGroup` — guid самого определения-набора;
  58 — обычные компоненты (`layerType: COMPONENT`): `mainStateGroup` у инстанса НЕТ, а
  `mainComponent` — guid самого определения. Отсутствие `mainStateGroup` — норма, пропавшей базой
  считается только указанная и не найденная группа. `componentKey` инстанса равен `componentKey`
  определения. У `SYMBOL` `publishID` ==
  `overrideKey`, `publishable` везде false.
- **Свойства компонента.** Типы: `visible` (видимость слоя; на слое `propRefMap.visible →
  {key, name}`, на инстансе значение в `propAssignMap[key].value`, умолчание в
  `propDefMap[key].defaultValue`), `variant` (`propRefMap.variant → [{name, value}]`), `instance`
  (слот подмены: `propDefMap.defaultValue` — publishID компонента по умолчанию, `propAssignMap.value`
  — guid подставленного: 6 присвоений, 2 различных значения, определений для них в экспорте нет). Скрытых
  слоёв (`visible:false`) в определениях 4.
- **Оверрайды** — `props[]` у экранных инстансов (54) и у вложенных (всего 516): `pathString`,
  `componentId`, `type` + переопределяемые поля (`nodeText` — 43 записи, `fillPaints`, `width/height/
  left/top`, `name`, `vectorPaints`…). `pathString` — цепочка guid через `/`: первый узел лежит в
  мастере инстанса, каждый следующий — в мастере предыдущего звена (проверено: `2:170353` — INSTANCE
  в «Modal Action», `2:170486` — PARAGRAPH «Label» в «Button»). `componentId` равен последнему звену
  в 495 записях из 516; в остальных 21 pathString оканчивается вложенным инстансом, а `componentId`
  — это `mainComponent` ЭТОГО вложенного инстанса, то есть компонент, который в этом месте реально
  рисуется (проверено: 21 из 21; случая «componentId ≠ mainComponent» — признака подмены — в образце
  нет). 32 цели из 516 не найдены в экспорте (определение не выгружено) — диагностика
  `MISSING_COMPONENT_DEFINITION`.
- **Как читать оверрайды (задача 1.4; сверено с картинкой кадра).** Первое звено цепочки —
  прямой ребёнок мастера, поэтому корень мастера в цепочку не входит; запись с `pathString`, равным
  guid самого варианта (`mainComponent`), — КОРЕНЬ: в ней настоящие `width/height/left/top` инстанса
  (у шапки узел экрана говорит 600×64, в записи корня 560×44 и 560×80, у блока действий 560×81 при
  узле 600×81 — внутренняя ширина кадра). Записи экранного инстанса — «копия этого экрана»; записи
  вложенных инстансов внутри определений (`props[]` у узла-INSTANCE в мастере) — настройка самой
  библиотеки: «Отмена» у вторичной кнопки лежит там, «Подтвердить» — в записях экрана. Видимость
  слоя: `visible` самого узла → свойство-видимость (`propAssignMap` ближайшего инстанса, иначе
  `propDefMap` определения-владельца) → `visible` в записи оверрайда. У первой шапки Description
  выключен присвоением (на картинке описания нет), у второй включён; «Close» у второй выключен записью
  оверрайда `visible:false` (крестик на картинке один).
- **Краски, шрифты, стили (задача 1.5).** Заливки узлов — только `SOLID` (198 узлов, по одной на узел), у
  обводок ещё 4 `GRADIENT_ANGULAR` (дуги индикатора); `blendMode` везде `NORMAL`; 35 красок с `visible:false`.
  Эффекты: 2 `DROP_SHADOW` у корня и `INNER_SHADOW` (spread 3, белая «кайма» фокуса) у вариантов. `cornerRadius` —
  целое (81 узел), по углам — `rectangle{TopLeft,TopRight,BottomLeft,BottomRight}CornerRadius` при
  `rectangleCornerToolIndependent` (отсутствующий угол = 0: у шапки заданы верхние, у блока действий нижние).
  Узел с `border{Top,…}Weight` может нести и `strokeWeight` другого значения (1 против 2) — стороны сильнее.
  Шрифт — на самом узле (`fontFamily, fontSize, fontStyle` Medium/Regular/Semibold, `lineHeight*`/
  `letterSpacing*` в PIXELS), выравнивания текста в экспорте нет. `localStyleMap` — 38 `FILL` и 4 `TEXT`; ссылок на
  стили: fill 29 различных (27 найдены), text 4 (4), stroke 3 (1), effect 1 (0) — не найденные библиотечные. В
  записи оверрайда `fillPaints: []` встречается 3 раза, всегда у текста с заливкой у мастера, и цвет на картинке
  при этом есть — пустой список значит «не переопределено».
- **Auto-layout (проверено на геометрии образца, 214 контейнеров).** Контейнер — узел со
  `autoLayout.stackMode` HORIZONTAL|VERTICAL (`autoLayoutDirection` 1/2 с ним согласован: 1 — row,
  2 — column). Выравнивание по главной оси — `autoLayoutPrimaryAlign` (`flex-start|center|flex-end|
  space-between`; 16 наблюдений из 16 совпали с геометрией), по поперечной — `autoLayoutCounterAlign`;
  `autoLayoutAlignType` выравниванием НЕ является. Размер по оси — коды `autoLayoutWidthResize`/
  `HeightResize`: 0 фиксированный, 1 по содержимому, 2 на всю доступную (согласие с
  `stackPrimarySizing/CounterSizing` у контейнеров — 100 %: 0↔FIXED, 1↔RESIZE_TO_FIT); у текста
  «по содержимому» задаёт `textAutoResize` (`WIDTH_AND_HEIGHT` — обе оси, `HEIGHT` — высота), коды
  у него 0/2. Ребёнок в потоке — `autoLayoutByParent` у ребёнка контейнера без
  `autoLayoutItemAbsolutePos`. Числа `width/height` у узлов с кодом 1 и 2 — размер мастера или
  вычисленный (у шапки в потоке `width 600` при внутренней ширине кадра 560): верить им нельзя.
  Признака переноса (wrap) в образце нет — адаптер его не выводит.
- **Векторы и арт (задача 1.6).** На экране векторных групп нет — канон образца арта не содержит. Векторы живут в определениях:
  четыре иконки-мастера — SYMBOL 24×24 с ОДНИМ составным VECTOR «Outline» (11–20 px), у «Approve_Circle» ещё скрытая
  BOOLEAN_OPERATION «Stroke» с [VECTOR, ELLIPSE]; узлов типа GROUP в образце нет вовсе. `test_art.py` делает такое определение
  local: его вектор (20 px) остаётся иконкой-кандидатом, а он же ×6 склеивается в `art` со slug `outline`. `Modal.svg` (экспорт всего
  кадра: viewBox 648×293, width/height в 6 знаках, служебные `effect` в `defs`, 69 `rect`, 18 `path`) валидатор
  `daiver_svg_assets` принимает; в git файла нет.
- **Раскладка без auto-layout (задача 1.7).** Контейнеров без `autoLayout` с детьми на экране образца нет (корень и три инстанса объявлены, у инстансов нет детей), поэтому вывод раскладки ничего не меняет: канон побайтно тот же с `layout.infer` on и off, `stats.layout` и `layoutHints` в нём нет (пин в `test_layout_inference.py`). Правила §8 образцом не проверены — только синтетика.
- **Outline и секции (задача 1.8).** Канон образца — 4 узла, 3101 байт: целиком влезает в бюджет 24000, секция одна (корень, `hash` `0ccf768ee9d7` равен `rootHash` normalize), unproven пуст (экран полностью объявлен), подсказок раскладки нет. При бюджете 1500 корень режется на три инстанса (659 / 1063 / 886 Б), при 500 — три oversize-листа. Большого экрана в образце нет: границы секций, вес дайджеста и правила unproven доказаны синтетикой из копий его инстансов (`test_outline.py`).
- **Статус и лестница (задача 1.10).** Образец после `daiver_pixso_pull` — ступень `requirement`, после `daiver_normalize` — `normalized`; единственное предупреждение до приложения картинки — `goal-image-missing` (арта в каноне нет — `art-svg-missing` молчит; `canonical-stale` тоже: `stats.sourceSha256` канона равен `requirement.sha256` манифеста). Ступени выше строятся синтетическими state-файлами (`test_status_ladder.py`, класс `SampleFlow`): писатель `plan.json` появился в задаче 2.6, у `build-state.json`/`verify-state.json` писателей ещё нет.
- **Имена для карты компонентов (задача 2.1).** Инстансы экрана называются по ОПРЕДЕЛЕНИЮ: `component.figmaName` — «Modal Header» (×2, варианты `{Steps: OFF}`) и «Modal Action» (`{Variant: Default}`); имя слоя («Steps=OFF», «Variant=Default») — строка вариантов и компонент не называет, поэтому карта сопоставляется с `figmaName`. 20 определений дают 19 различных имён («Spinning Loader» — у двух), все с разными ключами `component_map.name_key` (регистр и разделители не различаются, цифры значимы: «Icon24» ≠ «Icon16»; в именах есть «/», «_» и пробелы: «Stroke / Arrows / Small_Left», «Tech_Icon24»).
- **Кандидаты резолва (задача 2.4).** Экран даёт 4 кандидата: корень-фрейм «Modal» (осмысленное имя) и три инстанса. Два «Modal Header» с `{Steps: OFF}` — один ключ переиспользования `modalheader|Steps=OFF` (слоты `Header` и `Header,Description`), «Modal Action» — слоты `Label×2` (два слота с одним именем — две кнопки). У инстансов образца нет детей и `textProps`: ключи текстовых слотов — `component.content[].name`. Проекция корня («Modal»): `fingerprint instance=3` и три строки `tree`; ни один из пяти текстов слотов, шрифт, токен, цвет и число геометрии в неё не попадают.
- **Отчёт резолва (задача 2.5).** После резолва четырёх кандидатов в `resolution-report.md` три строки таблицы: корень-фрейм «Modal» и «Modal Action» — по одной, два «Modal Header» с `{Steps: OFF}` (`2:153703`, `22:63141`) — одна строка «×2» с двумя ссылками. Адрес кадра в образце обезличен, поэтому ссылки — на `pixso.example`; шаблон строит `outline.node_url_template` (Pixso — подмена `item-id`), id вида `22:63141` в адрес подставляются как есть. Золотой текст отчёта — `GOLDEN_MODAL` в `test_resolution_report.py`.
- **План и подпись (задача 2.6).** «Modal» целиком — ОДНА секция (корень `2:153702`, `hash` `0ccf768ee9d7`, 3101 Б; так при бюджете по умолчанию 24000 и при любом ≥ 3101): оболочки над секциями нет (`tree: {section: «2:153702»}`), `buildOrder` из одной записи, `unproven` пуст (решений по раскладке нет), общих local-компонентов нет. Четыре кандидата — три строки таблицы `plan.md` (два «Modal Header» с `{Steps: OFF}` — одна строка «×2»: эталон `2:153703`, копия `22:63141`). В `design` попали 0 токенов экрана (токены образца живут в определениях библиотечных инстансов, а не на узлах экрана) и два цвета без привязанного токена: `rgba(255, 255, 255, 1)`, `rgba(31, 20, 51, 0.12)`. Размеры: `plan.json` 3117 Б, `plan.md` 2602 Б, `ui-plan.md` 3825 Б, `plan.lock.json` 439 Б. Золотой `plan.md` — `GOLDEN_MODAL_PLAN_MD` в `test_plan_contract.py`. Перетяжка того же экспорта ничего не меняет; перетяжка экспорта с изменённым текстом слота («Текст9 слово - 20») меняет sha256 требования, и подпись плана слетает (канон удаляет сам pull, план `stale`).
- **Состояние сборки (задача 3.1).** «Modal» — одна секция, поэтому `build-state.json` — одна запись: `2:153702` «Modal», `hash` `0ccf768ee9d7` (равен хешу из `daiver_section` и `buildOrder` плана), `weightBudget` 24000; файл 199 Б после первой сверки и 286 Б после `done` с двумя outputs; ответы `daiver_build_state` / `daiver_build_state_set` — 14 и 16 строк. Цикл in_progress → `daiver_section` → done поднимает ступень `building` → `built`. Повторный pull кадра с состоянием pull отказывает («уже собран», 6.3). Несколько секций, стухание и порядок — синтетика (`test_build_state.py`).
- **Растр и SVG** образца в фикстуру не входят: растр 648×293 (JPEG без альфы — поля 24 px под тень
  чёрные) и `Modal.svg` обезличить нельзя без потери смысла; для тестов зрения и диффа (этап 5)
  делаются синтетические картинки.

## Что проверяют тесты

`test_anonymize_pixso.py` → `Fixture`: файл — валидный Pixso DSL; 3 инстанса экрана и 20
определений на месте, все ссылки разрешаются, все определения достижимы; нет ни `SB Sans`, ни
исходных текстов; если оригинал лежит локально — ни один исходный идентификатор/текст не остался и
файл воспроизводится побайтно. Дальше фикстуру используют `test_pixso_pull` (1.2),
`test_normalize_pixso` (1.3), `test_materialize` (1.4), `test_paints_typography` (1.5), `test_art` (1.6), `test_layout_inference` (1.7), `test_outline` (1.8), `test_status_ladder` (1.10), `test_component_map` (2.1), `test_resolve` (2.4), `test_resolution_report` (2.5), `test_plan_contract` (2.6), `test_build_state` (3.1) и golden
`canonical.golden.json` (появится после 1.3–1.5).
