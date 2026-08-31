# Задача 006 — Дрейф сигнатуры `_is_phase_work` в тесте покрытия фаз

## Статус
Закрыта · неактуальна · проверено 2026-08-28

> Дефект описан по состоянию, которого больше нет: тест вызывает `_is_phase_work` с актуальной
> сигнатурой, `test_phase_enforcement_coverage.py` проходит (2/2) и входит в общий прогон
> (`run_all_tests.py` — 108/108, все 106 `test_*.py` + 2 корневых файла подхвачены).

## Контекст
После миграции на project-модель и перенацеливания control-plane тестов часть ошибок не связана
с этой работой, но всплыла при попытке получить 97/97 зелёных.

`skills/feature-pipeline/scripts/test_phase_enforcement_coverage.py::test_every_subagent_phase_is_enforced`
падает с `TypeError: _is_phase_work() missing 1 required positional argument: 'root'`.

Прод-код: `hooks/inline-phase-guard.py:161`
```python
def _is_phase_work(step_id: str, tool_name: str, tool_input: dict, root: Path) -> str | None:
```
Тест (строка 73): вызывает `IPG._is_phase_work(step_id, tool, tin)` без `root`.

## Следствие
- `python3 skills/run_all_tests.py` даёт `PASS=96 FAIL=1` (поправили 2 теста проводки → 96;
  это падение было и раньше, до захода).
- CI-ворота не зелёные.

## Критерии приёмки
- [ ] Тест вызывает `_is_phase_work` с актуальной сигнатурой (4 аргумента, `root` берётся из
      `tmp_path` фикстуры или `Path.cwd()`).
- [ ] `python3 skills/run_all_tests.py` → 97/97.

## Файлы
- `skills/feature-pipeline/scripts/test_phase_enforcement_coverage.py` (строка 73)
- `hooks/inline-phase-guard.py` (прод-код, не менять без нужды)
