# Задача 009 — `level_requirements` требует шаг `02-design`, которого нет в fix/lite-ветках

## Статус
Закрыта · исправлено 2026-08-28

## Контекст
`hooks/risk-policy.json:125-126`:

```json
"R2": {"mode": "require", "steps": ["02-design"]},
"R3": {"mode": "require", "steps": ["02-design"], "approval": "security-review"},
```

`risk_ladder.check_requirement` резолвит это буквально: `manifest_status(root)["02-design"]`.
В манифестах `forgefix` и `forgelite` такого шага нет — там `fix-diag` и `lite-design`
(`skills/forgefix/references/manifest-steps.json`, `skills/forgelite/references/manifest-steps.json`).
Значит требование R2 не выполнимо в принципе. Проверено (fix-ветка, `fix-green`, критичность
`medium`):

```
[gate-guard] DENY: R2: шаг 02-design не completed (=None).
  Действие=write target='src/main/java/com/x/Foo.java' risk=R2.
```

`path_risk.R2` — это `src/main/**.{java,kt}`, то есть **весь прод-код**. Тупик открывается при
`auto_max_risk ≤ R1`, то есть при любой критичности кроме `low`.

Ловушка замыкается сама. Баннер про невыбранную критичность (`gate-guard.py:506-511`) велит:

> Вызови скрипт атомарной записи — `set_criticality.py` (он деривит auto_max_risk из критичности)

а `set_criticality.py:39-43` для `medium` пишет `R1`, для `high` — `R0`. Модель делает ровно
то, что просит текст отказа, и запирает себя навсегда. Брифы fix/lite обходят это тем, что
выставляют `decisions.auto_max_risk R2` руками (`forgefix/SKILL.md:103`) — enforcement держится
на том, что модель не пойдёт по пути из деного баннера.

Там же в баннере неверный путь: в прозе `pipeline-state/scripts/set_criticality.py`, в самой
команде — `feature-pipeline/scripts/set_criticality.py`. Файл лежит по второму пути.

## Следствие
- forgefix/forgelite не могут написать ни строки прод-кода при штатной критичности.
- Легального выхода нет: `override_judge` — R4, требует approval-маркера.

## Критерии приёмки
- [x] Требование уровня резолвится по ФАЗЕ, а не по литеральному id шага: `02-design`
      засчитывается шагом `02-design*`, `lite-design*` или `fix-diag*`.
- [x] Если в манифесте нет НИ ОДНОГО шага требуемой фазы (ветка её не содержит) — требование
      не применяется, а не блокирует навсегда. Причина попадает в текст reason.
- [x] Баннер критичности показывает существующий путь скрипта.
- [x] Регрессионный тест: fix-манифест с `fix-diag: completed` пропускает запись в
      `src/main/**.java` при `auto_max_risk=R1`; с `fix-diag: pending` — блокирует.

## Файлы
- `hooks/risk_ladder.py` (`check_requirement`)
- `hooks/gate-guard.py` (текст баннера критичности)
- `hooks/test_risk_ladder.py`, `hooks/test_gate-guard.py`
