# Evidence Bundle — доказательный пакет задачи (PDLC v3.5)

«Evidence bundle» — то, что агент **обязан собрать к завершению задачи**, чтобы доказать, что
сделал её правильно: тесты, покрытие, результаты гейтов, артефакты, обоснование, ссылка на SDD.
Это ворота между Implementation Loop и доставкой (стр. 62, 155, 244 концепции).

## Где лежит

`<project>/ground/evidence/<taskId>.json` — по одному пакету на задачу task-plan.

## Схема

```json
{
  "task": "T1",
  "title": "…",
  "tests": { "ran": 42, "passed": 42 },        // из шага 05-tests
  "coverage": 0.86,                              // доля, из JaCoCo
  "gates": { "build": "pass", "coverage": "pass", "delivery": "pass" },
  "artifacts": ["module/src/main/java/.../Foo.java"],
  "rationale": "почему так реализовано (1–3 предложения)",
  "sdd_ref": "feature-folder/sdd.md#T1",        // ссылка на раздел SDD
  "acceptance": ["Given … When … Then …"],       // критерии приёмки задачи
  "timestamp": "2026-…Z",
  "completeness": 0.95                            // считается автоматически
}
```

## Поля для completeness

Обязательные (вес равный): `task, tests, coverage, gates, artifacts, rationale, sdd_ref`.
`completeness = (заполненных обязательных) / (всего обязательных)`. Порог по умолчанию **0.95**
(переопределяется `pipeline.json → evidence.threshold`).

## Как собирается и проверяется

- Сборка: `feature-pipeline/scripts/build_evidence.py <task-plan> --task <id> --root .`
  — тянет данные из `ground/statements/feature-pipeline/pipeline/{04-build-<id>,05-tests,07-deliver-<id>}.json`
  и из task-plan; пишет пакет, считает completeness.
- Гейт: `check_evidence.py <task-plan> --root . --pipeline-config ground/pipeline.json` → exit 2 если
  на любую задачу пакета нет или completeness ниже порога.
- Принуждение в рантайме: хук `evidence-enforcer.py` (PreToolUse) блокирует `git push` / создание PR /
  отчёт в Jira, пока гейт не зелёный. Дополнительно risk-ladder (R2+) проверяет наличие evidence.

## Когда собирать

В фазе Build — после закрытия `04-build-<id>` и зелёного `05-tests`; обновить перед фазой Deliver.
Без полного пакета доставка (push/PR/report) будет заблокирована хуком.
