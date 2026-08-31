# ADR — Architecture Decision Record (MADR-style)

ADR фиксирует **почему** принято архитектурное решение: контекст, само решение, рассмотренные
альтернативы и последствия — то, что НЕ выводится из кода (grounding даёт факты «как построено»,
ADR — обоснование «почему так»). ADR-файлы живут в мастер-репо (`<master_base>/adr/`),
накапливаются на уровне капабилити и версионируются git'ом (это и есть история/аудит решений).

Имя файла: `NNNN-<kebab-slug>.md` (например `0007-events-via-kafka.md`), `NNNN` — сквозной
возрастающий номер. На ADR ссылаются по ID `ADR-NNNN` из `tech-design.md §8`, `sdd.md §11`,
раздела «Архитектурные решения» мастер-спеки и (для связок модулей) из `architecture-policy.json`.

## Обязательный состав `adr/NNNN-<slug>.md`

```markdown
# ADR-<NNNN>: <короткое решение в одной строке>

**Status:** proposed | accepted | rejected | superseded | deprecated
**Date:** <YYYY-MM-DD>   **Deciders:** <кто принимал>
**Supersedes:** ADR-<NNNN>        <опусти, если ничего не заменяет>
**Superseded-by:** ADR-<NNNN>     <ОБЯЗАТЕЛЬНО, если Status = superseded/deprecated>

## Context
Проблема и силы, толкающие к решению. Факты из grounding (текущие модули/связки/эндпойнты),
ограничения (NFR, регуляторка), требования из мастер-спеки/дельты, на которые это влияет.

## Decision
Что именно решили. Конкретно (стек/библиотека/протокол/связка модулей), одним-двумя абзацами.

## Consequences
Последствия — и положительные, и отрицательные (что усложняется, какие компромиссы приняты,
какие новые ограничения/энфорсы вводятся — напр. новая связка групп модулей).

## Alternatives
1–3 рассмотренных и **отвергнутых** варианта с причиной отказа.
```

## Статусы и жизненный цикл

- `proposed` → `accepted` (принято) / `rejected` (отклонено).
- `accepted` → `superseded` (заменено новым ADR) / `deprecated` (устарело). В обоих случаях
  обязателен резолвящийся `Superseded-by: ADR-NNNN`.
- Принятые ADR (`accepted`) — источник правды: на них ссылаются артефакты, а связки модулей в
  `architecture-policy.json` (`allowed_new`) при `adr.enforce_couplings` должны ссылаться на
  **accepted** ADR.

## Гейт

`check_adr.py` проверяет детерминированно: состав (Status/Context/Decision/Consequences),
валидный Status, наличие резолвящегося `Superseded-by` при superseded/deprecated, формат имени
`NNNN-slug.md`, уникальность ID, и (в режиме `--refs`) что все `ADR-NNNN`, упомянутые в
tech-design/sdd/policy, существуют. Включается `adr.enabled`.
