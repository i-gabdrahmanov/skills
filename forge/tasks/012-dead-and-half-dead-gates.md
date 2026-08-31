# Задача 012 — Мёртвые и полу-мёртвые гейты

## Статус
Закрыта · исправлено 2026-08-28 (12.6 — принятый риск, задокументирован в FORGE.md)

## Контекст
Обратная сторона аудита блокировок: гейты, которые числятся активными, но не стреляют.

### 12.1 Проверка 2 фазового гейта недостижима
`gate-guard.check_phase_gate` блокирует `blocked_tools_until_complete` =
`["Read","GrepSearch","Glob","Grep"]` (`pipeline_phases.blocked_tools`), но сам `gate-guard` в
`settings.hooks.json` привязан только к `run_shell_command|Bash` и write-матчерам. На
`read_file` висит один `grounding-evidence`. Значит блокировка чтения `src/` до завершения
grounding'а не существует. Вдобавок список содержит Claude-нотацию (`Read`), а рантайм шлёт
канон `read_file` — даже при правильной проводке сравнение `tool_name in blocked_tools` не
совпало бы.

### 12.2 `live_state` теряет eligibility
`gate-guard._live_state` зовёт `pipeline_phases.live_state(manifest)` без `project_root`,
поэтому `apply_eligibility` = False (`pipeline_phases.py:481`) и `enabled_by`/`skip_if` не
применяются. Фаза, отключённая конфигом (напр. `02-eval-plan` при `eval_enabled=false`),
остаётся `pending`, `current_phase` встаёт на неё навсегда, и её `allowed_skills`/`depends_on`
начинают блокировать всё подряд.

### 12.3 `phase-gate.py` (Stop) мёртв и потенциально зациклен
- Ждёт шаг в статусе `in_progress`, а `update.py` ведёт `pending → completed`; промежуточную
  пометку не ставит никто (это зафиксировано в докстрингах `risk_ladder.current_step_id` и
  `inline-phase-guard._active_step_id`). Хук не срабатывает никогда.
- Защита от петли — единственная, через `stop_hook_active`. Этого поля нет в списке
  подтверждённых для форка (`docs/v2/01-runtime-config-surface.md:56-58`). Если рантайм его не
  шлёт, блокировка завершения хода повторяется бесконечно.

### 12.4 `pii-boundary._allowed_scope` — приоритет операторов
```python
return "ground" in segs or "resources" in segs and _project.is_test_path(target)
```
`and` связывает сильнее, а `is_test_path` выше по функции уже вернул False → вся ветка
`resources` мертва.

### 12.5 Два резолвера корня проекта · 12.6 «активная фича» по mtime
- `risk_ladder.project_root` = только `git rev-parse --show-toplevel`;
  `_project.find_project_root` = приоритетная цепочка `.git` → `build.gradle|pom.xml` →
  `ground/{policy,pipeline}.json`. В git-репозитории ответы совпадают, расходятся вне git.
- `risk_ladder.active_manifest` берёт самый свежий `manifest.json` по mtime **по всем**
  namespace. Две фичи в работе — гейты чужой применяются к текущей.

12.5 чиним делегированием (строгое улучшение). 12.6 — **принятый риск**: корректный выбор
активной фичи это отдельная работа (маркер активного прогона), в рамках этой задачи не делаем.

## Критерии приёмки
- [x] 12.1 — `blocked_tools` содержит и канон-имена рантайма (`read_file`, `grep`, `glob`),
      `gate-guard` проведён на read-матчер в `settings.hooks.json`, чтение вне пайплайна и на
      завершённой фазе не деградирует (проверка риска для read остаётся fail-open).
- [x] 12.2 — `live_state` принимает `project_root` и `gate-guard` его передаёт.
- [x] 12.3 — `phase-gate` смотрит не только `in_progress`, но и «шаг открыт, а гейт уже
      пройден/фаза не закрыта»; защита от петли не зависит от `stop_hook_active` (одноразовый
      маркер на сессию).
- [x] 12.4 — скобки расставлены, ветка `resources` работает.
- [x] 12.5 — `risk_ladder.project_root` делегирует в `_project.find_project_root`.
- [x] Тесты на 12.1–12.5; 12.6 задокументирован как принятый риск в `FORGE.md`.

## Файлы
- `hooks/gate-guard.py`, `hooks/settings.hooks.json`, `hooks/phase-gate.py`,
  `hooks/pii-boundary.py`, `hooks/risk_ladder.py`
- `skills/feature-pipeline/scripts/pipeline_phases.py`
- `hooks/test_phase-gate.py`, `hooks/test_gate-guard.py`, `hooks/test_pii-boundary.py`

---

## Дополнение по итогам e2e на qwen CLI (2026-08-28)

**12.3 — `stop_hook_active` оказался хуже, чем «не подтверждён».** qwen-code 0.21.14 шлёт
`stop_hook_active: true` на КАЖДОМ `Stop`, включая самый первый в свежей сессии (замерено в
двух независимых прогонах). Проверка этого поля первой строкой означала «не блокировать
никогда» — то есть даже после починки детекта висящих шагов хук остался бы мёртвым. Проверка
снята; одноразовость держит собственный маркер сессии, не зависящий от семантики поля.

Заодно подтверждено на живом рантайме:
- канон-имена инструментов — `read_file` / `edit` / `run_shell_command` (12.1 бьётся);
- payload PreToolUse: `session_id`, `cwd`, `hook_event_name`, `timestamp`, `permission_mode`,
  `tool_name`, `tool_input`, `tool_use_id`, `tool_call_id`, `transcript_path`;
- на `Stop` дополнительно: `stop_hook_active`, `last_assistant_message`, `background_tasks`,
  `crons`, `context_usage`, `context_limit`, `input_tokens`.
