---
name: project-grounder
description: >
  Фаза 1 пайплайна feature-pipeline: снимает эфемерный инвентарь проекта
  (модули, классы по слоям, entity, эндпойнты, топики, таблицы, каталог
  переиспользования) в ground/inventory — топливо детерминированных гейтов
  дизайна. Не LLM-работа: один вызов ensure_inventory.py.

  Используй этот скилл когда: пользователь говорит "сними инвентарь",
  "запусти grounding", "подготовь контекст для дизайна", "нужна база для
  tech-design", или когда feature-pipeline вызывает Фазу 1.
---

# Project Grounder

Фаза 1 (`01-grounding`) пайплайна `feature-pipeline`. Даёт гейтам дизайна машинный список
того, что в коде есть на самом деле. Ничего не пишет в исходники.

> **Это не документация.** Человеческий обзор системы (MD + диаграммы) делает `system-analyst`
> в `docs/system-analysis/` — по запросу пользователя, вне пайплайна. Здесь собирается только
> машинный инвентарь, и только потому, что гейт не может опираться на «агент мог бы грепнуть»:
> чтобы поймать выдуманный класс, нужен список настоящих.

---

## 0. Предусловия

- Текущая директория — корень репо кода.
- `<project>/ground/pipeline.json` существует (создаётся инициализацией `feature-pipeline`).
- Скилл `pipeline-state` доступен — нужен для шага `01-grounding`.

---

## 1. Снять инвентарь

```bash
python3 <project>/.gigacode/skills/system-analyst/scripts/ensure_inventory.py --root . --json
```

Скрипт идемпотентен и сам решает, нужен ли рескан: сравнивает отпечаток исходников (сколько
файлов и когда самый свежий) с сохранённым. Код не менялся — не делает ничего; добавили,
удалили или правили файл — пересканирует. Спрашивать пользователя «пересканировать?» не надо.

- **exit 0** — инвентарь в `ground/inventory/`: `scan/*.json` (per-category ground truth) и
  `grounding-excerpt.json` (компактный срез для tech-design и `context-injector`).
- **exit 2** — пусто (0 модулей и 0 entities). Это не «маленький проект», а «не тот корень»
  или «сканировать нечего». СТОП, уточни корень репо кода. Шаг не закрывай — хард-гейт
  `_check_grounding_substance` в `update.py` всё равно не даст.

Каталог самоигнорирующийся (`.gitignore` = `*`): инвентарь производен от кода, в git не едет,
конфликтовать между разработчиками ему нечем.

---

## 2. Архитектурный граунд модулей

Граф межмодульных зависимостей — его читает гейт фазы 05, чтобы отличить принятую связку от
нового арх-связывания:

```bash
python3 <project>/.gigacode/skills/feature-pipeline/scripts/check_architecture.py \
    --root . --emit-ground
```

Без пути пишет `ground/inventory/architecture-ground.json`. Правила уточняются человеком в
`ground/architecture-policy.json` (`module_deps.forbidden`/`allowed_new`).

---

## 3. Закрыть шаг

```bash
python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py \
    --skill feature-pipeline --step-id 01-grounding --status completed \
    --output-json '{"inventory_dir": "ground/inventory", "modules_count": <N>, "entities_count": <M>}'
```

Если `pipeline-state` не инициализирован (`manifest.json` нет) — не падай, сообщи и пропусти:
оркестратор инициализирует state при полном прогоне.

---

## 4. Результат

```
✓ Инвентарь снят
  ground/inventory/ — N модулей, M entities, K классов, L endpoints
  Дальше: tech-design читает grounding-excerpt.json; check_taskplan сверяет по нему reuses
```

---

## Что НЕ делать

- Не спрашивать «пересканировать ли» — скрипт решает сам по отпечатку исходников.
- Не коммитить `ground/inventory/` — он эфемерный и самоигнорирующийся.
- Не собирать человеческую документацию (`system-analyst`) — это отдельный запрос пользователя,
  пайплайну она не нужна.
- Не закрывать шаг при exit 2.

## Связанное

- `system-analyst` — человеческий обзор системы (MD + диаграммы), вне пайплайна.
- `tech-design` — потребитель `grounding-excerpt.json`.
- `feature-pipeline/references/phases/01-grounding.md` — бриф фазы.
