# Задача 007 — Локаут харнеса: fail-closed импорт и битый путь хука блокируют собственную починку

## Статус
Закрыта · исправлено 2026-08-28

## Контекст
Семь PreToolUse-хуков (`destructive-blocker`, `pii-boundary`, `state-write-guard`,
`sod-enforcer`, `inline-phase-guard`, `gate-guard`, `tdd-guard`, `eval-guard`) на неудачном
импорте форж-модулей печатают DENY и отдают `exit 2`. Это сознательный fail-closed
(см. комментарий в шапке каждого), но у него два непредусмотренных следствия.

**(1) Инструкция по восстановлению блокируется тем же хуком.** Проверено:

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"bash .gigacode/deploy-local.sh"}}' \
    | python3 destructive-blocker.py
[destructive-blocker] DENY: бандл forge не грузится (No module named 'risk_ladder').
  ... затем перезапусти: bash .gigacode/deploy-local.sh
exit=2
```

Хук советует ровно ту команду, которую сам же не пропускает. Матчер стоит на
`^(run_shell_command|Bash)$`, то есть заблокированы ВСЕ shell-вызовы, и починить бандл
из сессии нельзя — только руками в терминале.

**(2) Несуществующий путь к хуку даёт тот же локаут, без единой строки про forge.**
`python3` на отсутствующий файл возвращает **exit 2**, а в протоколе хуков это «блокировать»:

```
$ python3 /nonexistent/hooks/gate-guard.py < /dev/null
can't open file '/nonexistent/hooks/gate-guard.py': [Errno 2] No such file or directory
exit=2
```

То есть любой дрейф путей в `settings.json` (известный кейс: `deploy.sh` не пишет в боевой
`~/.qwen`, копии устаревают) кирпичит сессию целиком. `preflight.py` этого не ловит:
`_check_wiring` проверяет, что essential-хуки *перечислены* и что пути не «чужие», но не
проверяет, что файл по пути команды существует и что `${PYTHON}` вообще может импортировать
бандл.

## Следствие
- Сломанный деплой = сессия без Bash и без Write; выход только вне агента.
- Диагностика недоступна: `preflight.py` тоже запускается через Bash.

## Критерии приёмки
- [x] При неудачном импорте хук пропускает (`exit 0` + WARN в stderr) команды восстановления:
      `deploy-local.sh`, `deploy.sh`, `preflight.py`, `doctor.py`, `python --version`.
      Всё остальное по-прежнему `exit 2`.
- [x] Логика восстановительного allowlist'а — в одном месте (`hooks/_failclosed.py`), а не
      скопирована в восемь except-блоков; сам модуль stdlib-only и грузится на python 3.9.
      Если и он не импортируется — поведение прежнее (`exit 2`), без регрессии.
- [x] `preflight.py` проверяет для каждой команды хуков в живом `settings.json`, что файл
      скрипта существует, и что резолвнутый интерпретатор импортирует `risk_ladder`/`_project`.
- [x] Регрессионный тест на оба пункта.

## Файлы
- `hooks/_failclosed.py` (новый)
- `hooks/{destructive-blocker,pii-boundary,state-write-guard,sod-enforcer,inline-phase-guard,gate-guard,tdd-guard,eval-guard}.py`
- `hooks/preflight.py`
- `hooks/test_import_failclosed.py`, `hooks/test_preflight.py`
