---
name: plantuml-to-png
description: Конвертирует PlantUML код в PNG локально (без онлайн-сервисов) через plantuml.jar + Java. Используй, когда нужно сгенерировать диаграмму из .puml файла или из встроенного блока plantuml в документации.
---

# PlantUML to PNG

Локальный рендеринг PlantUML диаграмм в PNG. Никаких обращений к `plantuml.com` или другим внешним сервисам.

**Где что хранится:**
- Скрипты, SKILL.md, тесты — в директории скилла (комитятся в git)
- Python-зависимости (`requests`) — в venv проекта (`<project-root>/.venv`)
- `plantuml.jar` (~26 МБ) — в **per-user кэше**, не в git и не в скилле:
  - macOS: `~/Library/Caches/plantuml-skill/`
  - Linux: `$XDG_CACHE_HOME/plantuml-skill/` (fallback `~/.cache/plantuml-skill/`)
  - Windows: `%LOCALAPPDATA%\plantuml-skill\` (fallback `~/AppData/Local/plantuml-skill/`)
  
  Это значит, что между проектами на одной машине jar не дублируется.

## Установка (один раз)

```bash
bash .gigacode/skills/plantuml-to-png/setup.sh
```

Скрипт:
1. Находит venv в корне проекта (`<project-root>/.venv`); если его нет — создаёт.
2. Ставит туда `requests` через pip (нужно только для разовой загрузки jar).
3. Скачивает `plantuml-<version>.jar` в кэш-директорию, если её там ещё нет.

Требования: `python3` и `java` (8+).

## Использование

`puml2png.py` использует только stdlib (jar путь определяется автоматически):

```bash
python3 .gigacode/skills/plantuml-to-png/scripts/puml2png.py <input.puml> [-o <output.png>]
```

Если `-o` не указан, PNG будет создан рядом с входным файлом.

## Проверка работоспособности

```bash
python3 .gigacode/skills/plantuml-to-png/scripts/puml2png.py \
  .gigacode/skills/plantuml-to-png/test/test_diagram.puml
```

## Что поддерживается

**Без дополнительных зависимостей:** sequence, class, usecase, json, yaml, mindmap, gantt, wbs, salt, activity beta.

**Требуют системного Graphviz (`dot`):** component, activity (legacy), state, deployment, object.

Скилл сознательно не ставит Graphviz сам — это нативный бинарь, и установить его кроссплатформенно через pip нельзя. При необходимости:
- **macOS:** `brew install graphviz`
- **Debian/Ubuntu:** `sudo apt-get install graphviz`
- **Fedora:** `sudo dnf install graphviz`
- **Windows:** `choco install graphviz`

После появления `dot` в PATH PlantUML подхватит его автоматически.

## Обновление версии PlantUML

Версия зафиксирована в `scripts/_paths.py` (`PLANTUML_VERSION`). Меняешь там — `setup.sh` скачает новую (старая останется в кэше, можно удалить руками).

## Структура скилла

```
.gigacode/skills/plantuml-to-png/
├── SKILL.md
├── requirements.txt          # requests (ставится в <project-root>/.venv)
├── setup.sh
├── scripts/
│   ├── _paths.py             # общие хелперы: кэш-директория, версия jar
│   ├── download_jar.py
│   └── puml2png.py
└── test/
    └── test_diagram.puml
```
