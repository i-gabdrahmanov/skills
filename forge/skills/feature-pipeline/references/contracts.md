# Контракты судей (Judges)

> **Статус:** Утверждено
> **Дата:** 2026-06-07
> **PDLC:** v3.5

## 1. Назначение

Судьи — субагенты-верификаторы, которые проверяют качество артефактов фазы feature-pipeline
**до** того, как шаг закрывается в pipeline-state. Каждый судья отвечает за свою фазу,
возвращает JSON-вердикт. Если `passed: false` — главный агент **блокирует** переход
к следующей фазе.

Судьи решают проблему «слепого прогона» — когда пайплайн помечает шаги как completed,
но качество артефактов не проверено (реальные дефекты KIDPPRB-8639: stubs, мёртвый код,
отсутствие тестов, eval'ы не прогонялись).

## 2. Архитектура

```
┌────────────────────────────────────────────────────────────┐
│                    Главный агент                           │
│  после каждой фазы: запускает судью → проверяет вердикт   │
└──────┬────────────────────────────────────────┬───────────┘
       │ agent() call                            │ run_judge.py --recheck
       ▼                                         ▼
┌──────────────┐                        ┌──────────────────┐
│  LLM-судья   │  ─── JSON-вердикт ──▶  │ Детерминированная │
│  (субагент)  │                        │ проверка (exit 0) │
└──────────────┘                        └──────────────────┘
```

**Два уровня проверки:**
1. **LLM-судья (субагент)** — читает код/документы/контекст, выносит качественную оценку.
   Возвращает JSON с checks, blocking_issues, warnings.
2. **Детерминированная проверка (`run_judge.py --recheck`)** — верифицирует, что вердикт
   LLM-судьи существует и `passed: true`. Exit 0 = PASS, exit 1 = FAIL.

## 3. Состав судей

| # | Судья | Фаза | Что проверяет | Блокирует |
|---|-------|------|---------------|-----------|
| 1 | eval-judge | 2.5 (после eval-plan) | Eval'ы покрывают acceptance? Пороги адекватны? Нет дубликатов? | Шаг `02-eval-plan` |
| 2 | red-judge | 3 — RED (после тестов) | Тесты специфицируют acceptance? Нет assertTrue? Есть негативные сценарии? | Шаг `04-test-<taskId>` |
| 3 | build-judge | 3 — GREEN (после кода) | Нет stubs? Нет мёртвого кода? Код = tech-design? | Шаг `04-build-<taskId>` |
| 4 | spec-judge | 5 (после Document) | Docs полны? Ground актуален? Нет мусора? | Шаг `06-spec` |
| 5 | delivery-judge | 6 (перед коммитом) | Jira консистентна? Нет секретов? git status чист? | Гейт 4 (коммиты) |

## 4. Формат вердикта

```json
{
  "$schema": "feature-pipeline/judge-verdict@1",
  "judge": "build-judge",
  "feature_slug": "kidpprb-8639",
  "evaluated_at": "2026-06-07T12:00:00Z",
  "verdict": "PASS" | "WARN" | "FAIL",
  "passed": true | false,
  "checks": [
    {
      "name": "No stubs in production code",
      "status": "PASS",
      "detail": "0 stub methods found",
      "severity": "error"
    }
  ],
  "blocking_issues": [
    "EmptyTaskCloserScheduler not deleted as per tech-design §3"
  ],
  "warnings": [
    "Scheduler calls stub methods — will throw at runtime"
  ],
  "summary": "4/5 checks passed. 1 blocking issue."
}
```

- `FAIL` + `passed: false` → **блокировка**: шаг не закрывается
- `WARN` + `passed: true` → предупреждения, не блокирует
- `PASS` + `passed: true` → всё ок, шаг можно закрывать

## 5. Хранение вердиктов

```
ground/statements/feature-pipeline/<slug>/
├── manifest.json
├── steps.json
├── judges/
│   ├── eval-judge.json
│   ├── red-judge.json
│   ├── build-judge.json
│   ├── spec-judge.json
│   └── delivery-judge.json
```

## 6. Порядок интеграции в пайплайн

Подробные инструкции по вызову каждого судьи — в SKILL.md feature-pipeline.
Кратко:

1. **eval-judge**: после `build_evals_from_design.py`, перед закрытием `02-eval-plan`
2. **red-judge**: после субагента-тестописателя, ДО стабов, перед закрытием `04-test-<taskId>`
3. **build-judge**: после реализации + зелёных тестов, перед закрытием `04-build-<taskId>`
4. **spec-judge**: после спецадаптера + enrich_grounding, перед закрытием `06-spec`
5. **delivery-judge**: перед Гейтом 4 (до показа плана коммитов)

## 7. Критерии FAIL по каждому судье

### 7.1 eval-judge
- Хотя бы одна задача без compile eval'а
- Хотя бы один acceptance criteria не покрыт eval'ом
- coverage_threshold < 0.5 или test_pass_threshold < 0.8
- Eval'ы ссылаются на несуществующий task_id

### 7.2 red-judge
- Хотя бы одна задача без тестов
- Есть тест с assertTrue(true) или без assert'ов
- Acceptance criteria не покрыт ни одним тестом
- Все тесты — только happy path (нет негативных)
- Использован @SpringBootTest без уважительной причины

### 7.3 build-judge
- Stubs в production-коде (UnsupportedOperationException)
- Не удалён класс, который tech-design предписывает удалить
- Coverage ниже порога для любого изменённого файла
- @Transactional + Kafka без afterCommit (если это проблема)
- Код не компилируется
- Новые checkstyle-нарушения

### 7.4 spec-judge
- brd.md, tech-design.md или task-plan.json отсутствуют
- manifest.json в ground отсутствует
- enrich_grounding не запускался

### 7.5 delivery-judge
- Stubs/missing implementation
- Найдены секреты/credentials в коде
- git status показывает неожиданные изменения
- TODO/FIXME без явного разрешения
- Jira не консистентна (если Jira enabled)
- Сообщения коммитов без Jira-ключа