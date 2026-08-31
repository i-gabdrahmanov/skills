# Задача 010 — EDD-гейт требует зелёную тест-сюиту ДО написания кода

## Статус
Закрыта · исправлено 2026-08-28

## Контекст
`build_evals_from_design.py:146-186` заводит на КАЖДУЮ задачу три eval'а:

| id | что проверяет | когда может стать `passed` |
|---|---|---|
| `compile-<t>` | проект компилируется | до кода — да |
| `coverage-<t>` | покрытие задачи ≥ порога | только после кода |
| `test_pass-<t>` | **вся тест-сюита зелёная** | только после кода |

`eval-guard.py:208-217` не пускает запись в `src/main`, пока ВСЕ три не `passed`. А фаза
`04-build-<taskId>` по определению стартует сразу после `04-test-<taskId>` (RED), то есть при
заведомо красной сюите. Проверено:

```
[eval-guard] DENY: Eval-Driven Development: для задачи T1 не пройдены (или не прогонялись)
eval'ы: ['compile-t1', 'test_pass-t1', 'coverage-t1'].
Прогони execution-gate: python3 .../run_pending_evals.py --project . --feature feat-y --task T1
```

Совет из баннера не помогает: `run_pending_evals.py` в этот момент выполнит `./gradlew test`
и получит те же fail (RED-тест красный by design), запишет их в `evals.json`, и гейт
заблокирует снова. Круг: кода нет → сюита красная → писать код нельзя.

`quality.eval_enabled` по умолчанию `true` (`init_pipeline_config.py:511`), так что на
full-ветке тупик активен из коробки. fix/lite спасает только явный
`config.py set quality.eval_enabled false` в их брифах — то есть EDD там просто выключен.

Важно: `coverage` и `test_pass` и так форсятся при ЗАКРЫТИИ шага —
`risk-policy.json.gate_cmd_expect` требует `check_build.py` для `04-build` и
`check_coverage.py`/`run_judge.py` для `05-tests`. Убрав их из pre-write гейта, enforcement не
теряем.

## Следствие
Full-пайплайн не может выйти из фазы 04-tdd при дефолтном конфиге.

## Критерии приёмки
- [x] `eval-guard` блокирует запись только по eval'ам, выполнимым ДО кода (`compile`).
      Post-code типы (`test_pass`, `coverage`) в pre-write гейте не участвуют — их держат
      гейты закрытия шага.
- [x] Список pre-write типов — именованная константа с комментарием, почему список такой.
- [x] Текст DENY перечисляет только реально блокирующие eval'ы.
- [x] Регрессионный тест: eval-plan с тремя типами и пустым `evals.json` → блок только по
      `compile-*`; `compile` passed → запись проходит, хотя `test_pass`/`coverage` не прогонялись.

## Файлы
- `hooks/eval-guard.py`
- `hooks/test_eval-guard.py`
