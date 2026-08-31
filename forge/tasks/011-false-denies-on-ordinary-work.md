# Задача 011 — Ложные блоки на обычной работе

## Статус
Закрыта · исправлено 2026-08-28

## Контекст
Шесть независимых источников `exit 2` на действиях, в которых нет ни риска, ни нарушения
пайплайна. Все воспроизведены запуском хука.

### 11.1 Ladder гейтит ЧТЕНИЕ
`gate-guard` не отличает читающую shell-команду от пишущей: `risk_ladder._target_path`
вытаскивает из команды любой путь и классифицирует его по `path_risk`.

```
$ cat src/main/resources/application.yml
[gate-guard] DENY: R4: нет валидного approval-маркера 'human-approval'. risk=R4
$ grep -rn foo src/main/java/com/x/auth/Service.java
[gate-guard] DENY: R3: шаг 02-design не completed (=None). risk=R3
```

R4 срабатывает и ВНЕ пайплайна (для R4+ deny-first держится всегда). Плюс `path_risk.R4:45`
делает суффикс `-prod` необязательным — под «прод-конфиг» попадает любой `application*.yml`.

### 11.2 Создание Jira-задачи требует маркер `security-review`
`command_risk.R3` ловит `acli|jira … create`, а `level_requirements.R3.approval` —
`security-review`. Ключ семантически чужой, и текст отказа (`check_requirement`) не даёт
рецепта, как маркер получить (в отличие от gate-override/rollback/skip-judges, где печатается
готовая команда `record_approval.py`). Модель уходит в `override_judge` → это R4 → ещё DENY.

### 11.3 `fork-syntax-guard` рубит по подстроке
Хук объявлен «эргономика, не enforcement», но возвращает жёсткий `exit 2` при любом вхождении
паттерна в строку команды — включая кавычки и heredoc:

```
[DENY] echo "версия $(git rev-parse HEAD)"          # ожидаемо для форка
[DENY] cat > docs/x.md <<'EOF' … `update.py` … EOF  # backtick в теле дока
[DENY] grep -rn "find . -exec" docs/                # паттерн внутри аргумента grep
```

Запись почти любого дока форжа содержит backticks → блокируется.

### 11.4 `pii-boundary` считает плейсхолдер секретом
```
$ Write src/main/resources/application.yml  ← "password: ${DB_PASSWORD}"
[pii-boundary] DENY: запись PII/секрета (паттерн /(?i)(api[_-]?key|secret|password…/)
```
`${...}`, `{{...}}`, `<...>` — это ссылка на секрет, а не секрет.

### 11.5 `tdd-guard`: JPA-гейт стоит ДО фильтра пути
`tdd-guard.py:218` проверяет `@DataJpaTest|@SpringBootTest` в контенте раньше, чем
определяется, что цель — тестовый исходник. Результат: блокируется любой файл, где аннотация
просто УПОМЯНУТА, включая `docs/tech-design.md`.

### 11.6 `destructive-blocker`: подстрочные матчи + дыра в покрытии
```
[DENY] echo "-- DROP TABLE users" >> notes.md      # SQL в тексте
[DENY] find . -name "*.tmp" | xargs rm             # штатная уборка
[ 0 ]  rm -rf /etc/passwd                          # ← а вот это проходит
[ 0 ]  rm -rf /usr/local/lib                       # ← и это
```
Причина дыры: `_CORE_BLACKLIST[1]` мёртв (`\b` перед `-` в `\b(?:-[a-z]*r[a-z]*)\b` не даёт
границы — лукахеды не срабатывают никогда), а `_CORE_BLACKLIST[0]` и policy-паттерн требуют,
чтобы цель была РОВНО `/`, `~`, `*`, `$HOME` или `.`.

## Критерии приёмки
- [x] 11.1 — read-only shell-команда (`cat/head/tail/less/grep/rg/ls/find/wc/file/stat/diff`,
      `git show|diff|log|status`, `sed -n` без `-i`) не гейтится ladder'ом; наличие записи
      (`>`, `>>`, `tee`, `dd of=`, `sed -i`) снимает признак read-only. `path_risk.R4` требует
      явного `-prod`/`-production` в имени конфига.
- [x] 11.2 — у kind'а `jira` свой approval-ключ; текст любого отказа `check_requirement`
      содержит готовую команду `record_approval.py`.
- [x] 11.3 — перед матчингом из команды вырезаются тела heredoc и одинарные кавычки; для
      `find -exec`/`ls -R` — и двойные. Есть выключатель `harness.fork_syntax_guard=false`
      (для рантаймов, где `$(...)` легален).
- [x] 11.4 — значение-плейсхолдер (`${…}`, `{{…}}`, `<…>`, `%(…)s`, `***`, `changeit`) не
      считается секретом.
- [x] 11.5 — JPA-гейт применяется только к тестовым исходникам.
- [x] 11.6 — SQL-деструктив ловится только в контексте БД-клиента или начала сегмента команды;
      `xargs rm` — только при опасном корне; `rm -r -f` по АБСОЛЮТНОМУ пути и по `~/$HOME`
      блокируется (дыра закрыта), по относительному (`build/tmp`) — нет.
- [x] Регрессионные тесты на каждый подпункт.

## Файлы
- `hooks/risk_ladder.py`, `hooks/gate-guard.py`, `hooks/risk-policy.json`
- `hooks/fork-syntax-guard.py`, `hooks/pii-boundary.py`, `hooks/tdd-guard.py`,
  `hooks/destructive-blocker.py`
- соответствующие `hooks/test_*.py`
