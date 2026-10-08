# Задача 001 — Починить падающий тест `hooks/tests/test_project_resolver.py`

## Статус
Закрыта · 2026-10-07

> Причина найдена: тест звал `find_project_root()` от cwd процесса и требовал `.git` или
> `build.gradle` в ответе — то есть проверял, ГДЕ лежит копия форжа, а не резолвер. Проверка
> 2026-08-28 «проходит в копии без git-предков» была ложно-зелёной: резолвер поднимается до
> первого build-маркера, а в `/private/tmp` на машине разработчика лежит посторонний
> `build.gradle`. На боевой машине (распакованный zip в `~/Downloads`) маркеров выше нет —
> отсюда 117/118 в отчёте боевого прогона v0.4.5. Тест переписан на временное дерево с `.git`
> и вложенным каталогом (tasks/016).

## Контекст
При прогоне `skills/run_all_tests.py` (и `hooks/run-hook-tests.sh`) падает 1 тест из 94 —
`hooks/tests/test_project_resolver.py::test_find_project_root`, весь набор даёт `FAIL=1` и exit 1.

## Симптом
```
FAIL: test_find_project_root (__main__.TestProjectResolver.test_find_project_root)
AssertionError: False is not true : /Users/21879313/Documents/forgeExt не содержит .git или build.gradle
```

## Причина
Тест `test_find_project_root` ожидает, что `find_project_root()` вернёт каталог с `.git`
или `build.gradle`. В окружении этого source-репо (`forgeExt`) каталог:
- не инициализирован как git-репозиторий (нет `.git` ни в `forgeExt`, ни в предках);
- не Java-проект (нет `build.gradle`/`pom.xml`).

`find_project_root()` корректно возвращает `cwd` (это его fallback), но тест проверяет
наличие маркеров корня, которых в extension-репо нет. **Дефект логики отсутствует** —
это ложный красный тест, вызванный запуском в extension-окружении вместо Java-проекта.

## Следствие
CI-ворота любых проверок харнеса в source-репо всегда красные (exit 1), даже когда весь
продуктивный код исправен. Защитный eval-набор при этом полностью зелёный (33/33).

## Критерии приёмки
- [ ] `python3 skills/run_all_tests.py` → `PASS` без `FAIL` (или тест осознанно переработан).
- [ ] `bash hooks/run-hook-tests.sh` → exit 0, обе части проходят.
- [ ] Логика `find_project_root()` (функция в `hooks/_project.py`) не изменена/изменена без
      смены поведения.
- [ ] Не сломаны прочие тесты из `hooks/tests/TestProjectResolver` (8 штук).

## Возможные направления решения (необязательно к реализации сейчас)
- Адаптировать инвариант теста под запуск в extension-репо (например, проверять, что корень
  содержит `hooks/`+`skills/`, т.е. корень extension'а, а не `.git`/build-файл).
- Либо явно инициализировать git-репо в `forgeExt` (см. задачу 004).

## Файлы
- `hooks/tests/test_project_resolver.py`
- `hooks/_project.py` (функция `find_project_root`)