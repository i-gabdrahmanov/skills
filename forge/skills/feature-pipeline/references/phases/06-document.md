# Фаза 06-document — Document (спека)

> Бриф фазы feature-pipeline. Общие правила — в SKILL.md (он уже в контексте): субагенты
> ОБЯЗАТЕЛЬНЫ (явный `agent()`), стейт — SKILL.md §0.5, ре-итерация и exit 3 = стоп-и-спроси —
> SKILL.md §0.6, override — SKILL.md §0.6.1. Нумерация секций ниже — историческая (§ из
> монолитного SKILL.md), внутри брифа она самодостаточна.
>
> **Гейт закрытия фазы:** spec-judge PASS + enrich_grounding; закрой 06-spec

## 9. Фаза 5 — Document

**🚨 ЧЕРЕЗ agent(). Оркестратор НЕ правит спеку и НЕ запускает enrich_grounding сам.**

### 9.1 Спецадаптер (agent)

Контракт: `get_prompt.py 5`:
```
agent(
  subagent_type="general-purpose",
  description="Update spec for <slug>",
  prompt="<вывод `get_prompt.py 5` (полный контракт); подставь: slug, docs_path, diff>"
)
```

### 9.2 Gate: enrich_grounding (детерминированно)

После спецадаптера выполни — **пересканирует код** по `--project-root`, пересобирает
`grounding-excerpt.json` из свежего scan (scan = источник истины: новые артефакты появляются,
удалённые выпадают) и инкрементально дополняет `docs/system-analysis/*.md`:
```bash
python3 .gigacode/skills/system-analyst/scripts/enrich_grounding.py \
    --task-plan "<папка фичи>/task-plan.json" \
    --project-root "<project>" \
    --feature "<slug>"
```

По умолчанию scan освежается по коду (`--no-rescan` отключает — нужно лишь если код
недоступен из cwd). `--system-analysis` и `--scan` НЕ передаём — скрипт сам резолвит их по
`docs.*` из `ground/pipeline.json` (in-repo или separate-repo).

Если `enrich_grounding.py` вернул non-zero (coverage не сошёлся) — нужен полный рескан через
`system-analyst` (см. фазу 1).

### 9.3 Judge-gate spec

```
agent(subagent_type="general-purpose", description="spec-judge for <slug>",
      prompt="<вывод `get_prompt.py 7.4` (spec-judge) + slug + docs_path + task-plan>")
```
Затем:
```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/run_judge.py spec <slug> --recheck
```

При PASS — закрой `06-spec` явной командой:
```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --feature <slug> --step-id 06-spec --status completed
```

---
