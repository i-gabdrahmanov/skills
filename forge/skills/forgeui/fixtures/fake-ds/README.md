# fake-ds — синтетическая библиотека-фикстура

Выдуманная мини-библиотека `@fakeui/core` для теста сканера требований ДС (`test_ds_requirements.py`, задача 2.2).
Боевой библиотеки у нас нет: файлы написаны по знанию форматов Storybook 6/7 и TypeScript, имена нейтральные. Ничего
из неё не исполняется и не импортируется — это текст для разбора.

| Файл | Что проверяет |
|---|---|
| `src/Button/Button.stories.tsx` | CSF3: `satisfies Meta`, `argTypes` с `options` из константы `as const`, `control`, `table.defaultValue`, `parameters.docs.description.component`, `render` со `{...args}` и слотом-иконкой, `name`, `__namedExportsOrder` |
| `src/Button/Button.mdx` | MDX-документация (`<Meta of={ButtonStories} />`) — проза привязывается к компоненту через импорт CSF-файла |
| `src/Button/Button.tsx` | исходник (режим `sources`): `ButtonProps` + alias `ButtonVariant`, JSDoc `@default`, `@deprecated` + замена, слот `React.ReactNode`, обработчик |
| `src/Badge/Badge.stories.jsx` | CSF2: `Template.bind({})`, `Name.args = {…}`, spread `...Primary.args`, `storyName` |
| `src/Card/Card.stories.jsx` | CSF1: истории-функции, компонент выведен по JSX и `title`; относительный импорт (import-пути нет); апостроф в JSX-тексте |
| `src/Tooltip/Tooltip.stories.js` | `storiesOf(…).addParameters({component}).add(…)` |
| `src/Tabs/Tabs.stories.mdx` | MDX-истории (`<Meta>` + `<Story>`), проза с блоком кода |
| `types/avatar.d.ts` | `.d.ts`: `React.FC`, `forwardRef`-тип с `extends` + `Omit<React.HTMLAttributes>` (набор открыт), функция с inline-типом, класс с `@deprecated` |
| `types/icons.d.ts` | `declare module "@fakeui/icons"` — имя модуля становится import-путём |

Ожидаемый результат зафиксирован тестами `FixtureTests` (10 компонентов; режим `stories` читает 8 файлов, `sources` — 9).
Что нужно от пользователя: образец настоящих stories / исходников / `.d.ts` корпоративной ДС (см. README плана,
раздел «Нужно от пользователя»).
