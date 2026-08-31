# Задача 008 — Пинцет по `agent_type`: gate-guard и inline-phase-guard противоречат друг другу

## Статус
Закрыта · исправлено 2026-08-28

## Контекст
Два хука делают ВЗАИМОИСКЛЮЧАЮЩИЕ предположения об одном поле payload'а.

- `hooks/gate-guard.py:185-190` (проверка 1 фазового гейта): если `agent_type` непустой и его
  нет в `allowed_skills` фазы — DENY.
- `hooks/inline-phase-guard.py:308`: если `agent_type` пустой — это главный агент, и
  productive-работа субагентной фазы — DENY.

`allowed_skills` (`pipeline_phases.py:305-316`) перечисляет ИМЕНА СКИЛЛОВ (`tech-design`,
`system-analyst`, `jira-task-writer`), а субагенты пайплайна вызываются как
`agent(subagent_type="general-purpose")`. Проверено на всех трёх значениях (фаза `02-design`,
запись `docs/tech-design.md`):

| `agent_type` | gate-guard | inline-phase-guard |
|---|---|---|
| `""` | 0 | **DENY** |
| `"general-purpose"` | **DENY** | 0 |
| `"tech-design"` | 0 | 0 |

Какое бы значение ни слал рантайм, один из двух хуков блокирует. Причём при
`general-purpose` gate-guard рубит не только запись — проверка 1 стоит до классификации
риска, поэтому из субагента не проходит даже `ls -la`:

```
[gate-guard] DENY: phase gate: фаза '02-design' (status=in_progress) не разрешает скилл
'general-purpose'. Разрешены: ['tech-design'].
```

Репо само себе противоречит о том, что шлёт форк:
- `docs/v2/01-runtime-config-surface.md:68` — «`agent_type` субагента **всегда**
  `general-purpose`»;
- `hooks/risk-policy.json:132` — «для general-purpose субагентов он **пуст** → слой неактивен».

Затронуты фазы full-ветки с непустым `allowed_skills`: `00-brd`, `01-grounding`, `02-sdd`,
`02-design`, `03-jira`, `04-tdd`, `05-verify`. У `fix-*`/`lite-*` `guess_phase` не находит
префикс → `allowed_skills` пуст → эта половина пинцета не срабатывает.

## Следствие
Full-пайплайн не проходит ни одной субагентной фазы: либо запись рубит inline-phase-guard,
либо любой тул-вызов рубит gate-guard.

## Критерии приёмки
- [x] Проверка `allowed_skills` не считает generic-тип (`general-purpose`, `Task`, `agent`,
      пустой) заявкой на скилл: для них проверка пропускается. DENY остаётся для НАЗВАННОГО
      чужого скилла (`jira-task-writer` в фазе дизайна) — ради этого гейт и заводился.
- [x] `inline-phase-guard` считает субагентом непустой `agent_type` **или** непустой
      `agent_id`: если рантайм не шлёт первое, но шлёт второе, субагент не принимается за
      оркестратора.
- [x] Противоречие в доках устранено: `risk-policy.json` и `docs/v2/01-*.md` говорят одно.
- [x] Регрессионный тест: три значения `agent_type` × (gate-guard, inline-phase-guard) —
      таблица выше становится «0/DENY-по-роли», без клетки «оба блокируют».

## Файлы
- `hooks/gate-guard.py` (`check_phase_gate`, проверка 1)
- `hooks/inline-phase-guard.py` (`main`)
- `hooks/risk-policy.json` (комментарий `agent_caps`), `docs/v2/01-runtime-config-surface.md`
- `hooks/test_gate-guard.py`, `hooks/test_inline-phase-guard.py`

---

## Дополнение по итогам e2e на qwen CLI (2026-08-28)

Прогон на реальном рантайме (qwen-code 0.21.14, метапроект, LM Studio) **опроверг обе версии
доки** и вскрыл третий, живой вариант дефекта.

Замер (хук-рекордер на всех событиях):

```
[15:50:23] SubagentStart   agent_type=general-purpose  agent_id=general-purpose-986521589
[15:53:01] PreToolUse edit agent_type=—                agent_id=—        ← это ВНУТРИ субагента
[15:53:11] SubagentStop    agent_type=general-purpose  agent_id=general-purpose-986521589
```

- `agent_type`/`agent_id` приходят **только** на `SubagentStart`/`SubagentStop`;
- в `PreToolUse` их нет ни у оркестратора, ни у субагента;
- `session_id` и `transcript_path` у субагента те же, что у оркестратора.

То есть по одному payload'у субагент **неотличим**, и `inline-phase-guard` блокировал работу
САМОГО субагента. Живой цикл без выхода: субагент получает «выполняй через agent(...)»,
возвращает текст отказа наверх, оркестратор отвечает «давайте запустим субагента». Прод-код
не пишется вообще. `agent_id`-fallback из основной части задачи здесь не спасает — поля нет.

**Фикс:** `hooks/subagent_scope.py` — отметка активного субагента в сессии, которую ставит
`context-injector` (SubagentStart) и снимает `state-recorder` (SubagentStop). `_is_subagent`
проверяет её третьим признаком. TTL 3 часа — упавший субагент не выключает гейт до конца сессии.

**Проверено вживую, оба направления:**
- субагент правит `src/main/**.java` в фазе `fix-green` → `PostToolUse` есть, файл изменён;
- оркестратор inline то же самое → блок, `PostToolUse` нет, файл не тронут, модель объясняет,
  что нужен субагент.
