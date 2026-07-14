# Контекст: решения пользователя и карта слоёв enforcement

## Ответы пользователя (зафиксировано, 2026-07-04)

| Вопрос | Ответ | Следствие для плана |
|---|---|---|
| Контроль над форком GigaCode? | **Только готовый бинарь** | Слой F (патчи рантайма: policy в ToolRegistry, HMAC-подпись state) исключён. Максимум — settings.json/env/CLI-флаги. |
| Где идут прогоны с DeepSeek? | **macOS локально** | Sandbox = Seatbelt (`sandbox-exec`), кастомные `.sb`-профили доступны. Docker-путь не нужен. |
| Есть ли `permissions` в форке? | **Есть** | deny/ask/allow с path-паттернами доступны (главный слой A). Всё равно верифицируется пробой WS0. |
| Операторская обёртка (headless per-phase)? | **Гибрид** | Интерактив для пользовательских гейтов (BRD, коммиты); headless-обёртка для фаз кода/тестов. WS4 строит гибридный driver. |

## Карта слоёв enforcement (A–F)

- **Слой A — нативные permissions рантайма** (WS1): deny на запись control-plane
  (`Edit(./.gigacode/**)`, `Edit(./ground/...)`, approvals/judges/gates), deny/ask на push,
  deny деструктива; allowlist Bash по префиксам. Не зависит от хуков/stdin/таймаутов.
- **Слой B — ОС-sandbox** (WS3): Seatbelt (macOS) — запрет записи вне проекта, сетевой allowlist
  через прокси. Защищает хост и канал exfil, НЕ различает санкц. скрипт от `python -c open()`
  внутри проекта.
- **Слой C — вынос control-plane из trust-domain модели (провенанс на уровне ОС)**: `ground/`
  принадлежит другому пользователю ОС; санкц. скрипты (update.py, record_gate.py и т.д.) —
  единственные в sudoers (NOPASSWD, конкретные пути). Модель физически не может подделать state.
  **НЕ в текущем плане** (требует раздельных ОС-пользователей; зафиксировано как опция будущего).
- **Слой D — операторская обёртка per-phase** (WS4): внешний driver гонит фазы отдельными
  `gigacode -p`, каждая со СВОИМ settings.json (permissions по фазе). Гейты driver проверяет сам.
- **Слой E — серверная сторона git**: Bitbucket branch permissions + pre-receive (PR-only).
  Модель в принципе не может обойти. **Вне репо forge** (конфиг Bitbucket) — рекомендация оператору.
- **Слой F — правки самого форка**: policy-engine в ToolRegistry, HMAC-подпись state-файлов.
  **Исключён** (нет контроля над бинарём).

## Маппинг «вектор обхода → слой, который его убивает»

| Вектор | Чем закрывается |
|---|---|
| Модель правит `.gigacode/hooks/*.py` или `settings.json` (`disableAllHooks:true`) — НЕ защищено сейчас | permissions deny `Edit(./.gigacode/**)` (A) + паттерн в state-write-guard (WS2, пояс+подтяжки) |
| Хук-таймаут >60с → kill → действие проходит (архитектурный fail-open форка) | permissions deny (registry, не зависит от хуков) + sandbox |
| Пустой/битый stdin хука → exit 0 (универсальный контракт) | permissions deny — не зависит от stdin |
| `./gradlew test -x checkstyleMain -x checkstyleTest` (наблюдено, прогон #2) | хук-уровень: детерминированный блок skip-флагов (WS2.2) |
| Запись вне проекта / сетевой exfil / установка пакетов | Seatbelt sandbox (B) + сетевой allowlist-прокси |
| `git push --force`, прямой push в защищённые ветки | permissions deny/ask + серверная сторона Bitbucket (E) |
| Правка существующих тестов под сломанный код, «частичный зелёный = успех» | уже закрыто: integrity/tautology floors + record_gate evidence; + пофазный overlay WS4 (deny Edit src/test в GREEN) |
| user-level `~/.qwen|~/.gigacode/settings.json` подключает устаревший харнес | preflight-проверка shadowing (WS2.3) + sandbox запрещает запись туда |
| Роутер-вход не форсится (нет события «скилл выбран») | операторская обёртка (D) — фазы гонит driver, а не модель |
| cost-breaker не тормозит (60× расхождение) | слой D: лимиты на вызов в driver (timeout, max-requests) |

## Открытый архитектурный факт

Ключевой риск слоя A — **shell-эквивалентность Edit-правил**. Документация qwen обещает, что
deny на `Edit(...)`/`Read(...)` форсится даже для эквивалентных shell-операций (cat/grep/redirect).
Если это верно и агрессивно — deny на `ground/**` может заблокировать санкционированные
python-writer'ы (`update.py`, `record_gate.py`), которые пишут через `open()` внутри
`run_shell_command`. Поэтому:
1. Проба WS0 2(c) — ДО включения ground-правил.
2. Если блокирует — в permissions остаётся только самозащита `.gigacode/**` (туда Write-тулом
   не пишет никто), а control-plane `ground/**` держит хук-слой (state-write-guard).
3. Возможное сужение — tool-specific `Write(...)` вместо меты `Edit(...)` (проба 2d).
