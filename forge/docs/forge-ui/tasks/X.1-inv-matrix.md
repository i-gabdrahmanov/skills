# X.1 — Матрица инвариантов

**Этап:** сквозная · **Зависит от:** — · **Статус:** todo
**Спека:** §18 (135 INV), §19.8 (механическая проверка покрытия)

## Зачем
«Сделали всё» должно проверяться кодом: у каждого из 135 инвариантов спеки — тест, именованная
деградация или задача, в которой он будет закрыт.

## Что сделать
- `skills/forgeui/references/inv-matrix.json`: на каждый id A1–A13, B1–B23, C1–C39, D1–D10, E1–E8,
  F1–F20, G1–G12, H1–H10 — `{status: covered|degraded|planned, tests:[…], task:"4.6", note}`.
  Сразу: covered — уже закрытое Этапом 0 (B19, B20, A5, часть C36…); degraded — телеметрия (H1–H10,
  C37, F19, G5–G7, G9, G12) и autoRoute (A12) с причиной; остальное — planned со ссылкой на задачу.
- `skills/forgeui/scripts/test_inv_matrix.py`: все 135 id на месте и без лишних; у covered —
  существующие файлы тестов; у degraded — причина; у planned — существующий файл задачи в
  `docs/forge-ui/tasks/`. Финальная приёмка (после этапа 7): planned не остаётся.
- Каждая задача, закрывая инвариант, переводит его в covered — часть Definition of Done.

## Готово, когда
- Матрица заполнена, тест зелёный, README задач ссылается на неё.

*Заметка (6.4).* INV-C18 (часть page: sources записаны, location, файл читается до правки), INV-C19 и QG-U22 (вторая половина: sources-location, standing, scope) и P4 — `skills/forgeui/scripts/test_page_baseline.py` (классы `Regions`, `OutsideRegions`, `Standing`, `Capture`); запрет записи в `sources` вне ui-build — `hooks/test_ui-guard.py::PageSources`. INV-F18 (конвенции сильнее примеров) — только бриф (ui-build п. 2, «домашний стиль» правки страницы), кодом не проверяется.

*Заметка (7.1).* INV-B22, E1–E3, E7 (разбор и режим), C38, D10 (закрытие из истории, until), D2, D7, G8 (blocked с supply, конфликт трека, запись остановов), E8 (устаревшая запись), D3 (часть: init, запись карты, report.html) и QG S12, U11 — `skills/forgeui/scripts/test_run_directive.py` (классы `Parse`, `Malformed`, `Mode`, `StartsAt`, `Blocked`, `Finish`, `Records`, `AutonomyBans`). INV-D5 (человеческий шаг не в таск-листе) — только бриф §0 («в autonomous вопросов нет: именованный останов»).
