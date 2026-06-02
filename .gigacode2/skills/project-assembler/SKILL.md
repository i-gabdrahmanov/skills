---
name: project-assembler
description: Собирает готовый к сборке Java/Spring проект из склеенного файла с исходниками и tech-stack.md. Генерирует build.gradle, settings.gradle, application.yml. Автоматически определяет модули и зависимости. Используй, когда получил Java-код из project-packer и нужно восстановить проект.
---

# Project Assembler

Собирает рабочий проект из двух файлов:
- **Склеенный Java-код** (выход project-packer → sanitize)
- **tech-stack.md** (список технологий и версий)

## Использование

```bash
python3 .gigacode/skills/project-assembler/scripts/assemble.py \
  clean.txt \
  -t tech-stack.md \
  -o ./assembled-project \
  --force
```

| Аргумент | Описание |
|---|---|
| `файлы` | Один или несколько склеенных файлов (parts) |
| `-t, --tech-stack` | Путь к tech-stack.md |
| `-o, --output` | Директория для проекта |
| `--name` | Имя проекта (авто-определяется из заголовка файла) |
| `--force` | Записывать в непустую директорию |

## Что генерируется

- **Java-исходники** — восстанавливается полная структура директорий
- **build.gradle** — для каждого модуля, с зависимостями из tech-stack.md
- **settings.gradle** — с именем проекта и списком подмодулей
- **application.yml** — минимальный, для Spring Boot модулей

## Что автоматически определяется

- **Модули** — по путям файлов (`proxy-service/src/...` → подмодуль `proxy-service`)
- **Зависимости** — по тегам `[module]` в tech-stack.md
- **Micronaut vs Spring** — Micronaut-модули получают другой набор plugins
- **Имя проекта** — из заголовка merged-файла

## Полный пайплайн (packer → assembler)

```bash
# На исходной машине:
python3 .gigacode/skills/project-packer/scripts/merge.py . -o project.txt
python3 .gigacode/skills/project-packer/scripts/sanitize.py project.txt -o clean.txt

# Передаём clean.txt + tech-stack.md

# На целевой машине:
python3 .gigacode/skills/project-assembler/scripts/assemble.py \
  clean.txt -t tech-stack.md -o ./my-project --force

cd ./my-project
gradle wrapper
./gradlew build
```
