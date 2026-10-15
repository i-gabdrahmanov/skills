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
- **Растр и SVG** образца в фикстуру не входят: растр 648×293 (JPEG без альфы — поля 24 px под тень
  чёрные) и `Modal.svg` обезличить нельзя без потери смысла; для тестов зрения и диффа (этап 5)
  делаются синтетические картинки.

## Что проверяют тесты

`test_anonymize_pixso.py` → `Fixture`: файл — валидный Pixso DSL; 3 инстанса экрана и 20
определений на месте, все ссылки разрешаются, все определения достижимы; нет ни `SB Sans`, ни
исходных текстов; если оригинал лежит локально — ни один исходный идентификатор/текст не остался и
файл воспроизводится побайтно. Дальше фикстуру используют `test_pixso_pull` (1.2),
`test_normalize_pixso` (1.3), `test_materialize` (1.4), `test_paints_typography` (1.5) и golden
`canonical.golden.json` (появится после 1.3–1.5).
