# Фаза 01-grounding — Grounding

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** grounding-excerpt готов/переиспользован; закрой шаг 01-grounding

## 4. Фаза 1 — Grounding

**Сначала детерминированно проверь, есть ли grounding (НЕ повторяй его снова и снова):**
```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/check_grounding.py --root . --json
```
- **exit 0 (есть)** — переиспользуй найденный обзор, `system-analyst` НЕ запускай. Если `kind=scan`
  или `overview` без `grounding-excerpt.json` — собери выжимку (project-grounder §4). **Не спрашивай и не
  пересканируй.**
- **exit 1 (нет)** — только тогда прочитай инструкции и запусти полный обзор:
  ```
  read_file("<project>/.gigacode/skills/system-analyst/SKILL.md")
  ```
  У него свой цикл и свой гейт коммита спеки. После завершения — grounding готов.

Свежесть между фичами поддерживается инкрементально в фазе Document (`enrich_grounding.py`), поэтому
полный рескан на каждом прогоне не нужен.

**Архитектурный граунд модулей (эмитни здесь — дёшево, идемпотентно).** Построй граф межмодульных
зависимостей проекта (что проект УЖЕ соединяет; сканирует и `build.gradle`, и `pom.xml`) — его
использует гейт бриф `05-verify.md` §8.3c, чтобы отличать принятые связки от новых арх-связываний:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_architecture.py \
    --root "<project>" --emit-ground "<project>/docs/system-analysis/architecture-ground.json"
```
(Если grounding в отдельном репо — путь к `docs/system-analysis/` оттуда.) Файл курируемый: архитектор
может уточнить правила в `ground/architecture-policy.json` (`module_deps.forbidden`/`allowed_new`).

**Гейт Grounding (ОБЯЗАТЕЛЬНО, перед переходом к спецификации).**
Спроси у пользователя: «Обзор системы (grounding) собран и актуален. Переходим к
спецификации (SDD)?». Только после «да». Если grounding не собран — НЕ переходи к бриф `02-sdd.md` §5a,
выполни полный обзор через `system-analyst` (см. exit 1 выше).

Закрой шаг явной командой (в output — `path`/`excerpt_path` из вердикта):
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 01-grounding --status completed
```

Этот контекст нужен дизайнеру: модули, существующие сущности, API, схема БД.

---
