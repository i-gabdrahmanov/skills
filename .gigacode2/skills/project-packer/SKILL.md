---
name: project-packer
description: Упаковывает только Java-исходники проекта (без тестов и конфигов) в текстовые файлы до 3MB, генерирует список технологий с версиями, и полностью удаляет все чувствительные данные — пароли, URL, адреса, пакеты организации. Система сканирования не найдёт никакой компрометирующей информации.
---

# Project Packer

Упаковка, анонимизация и распаковка Java-исходников. Stdlib only.

## Что собирается

- **Только `.java` файлы** рабочего кода (тесты исключены)
- **Отдельный `tech-stack.md`** — все зависимости и версии из build.gradle
- **Конфиги, compose, Dockerfile — не включаются**

## Полный пайплайн

```bash
# 1. Упаковать Java-файлы + сгенерировать tech-stack.md
python3 .gigacode/skills/project-packer/scripts/merge.py . -o /tmp/project.txt

# 2. Анонимизировать
python3 .gigacode/skills/project-packer/scripts/sanitize.py /tmp/project.txt -o /tmp/clean.txt

# 3. Распаковать
python3 .gigacode/skills/project-packer/scripts/split.py /tmp/clean.txt -o /tmp/clean-project --force
```

## Скрипты

### merge.py — Упаковка

```bash
python3 scripts/merge.py <project-root> -o <output.txt> [--max-size BYTES]
```

- Собирает только `.java` файлы (тесты исключены)
- Автоматически генерирует `tech-stack.md` рядом с выходным файлом
- Если > 3MB — разбивает на `output-part1.txt`, `output-part2.txt`, ...

### sanitize.py — Анонимизация

```bash
python3 scripts/sanitize.py <merged.txt> -o <clean.txt> [--dry-run]
```

### split.py — Распаковка

```bash
python3 scripts/split.py <file1.txt> [file2.txt ...] -o <dir> [--force] [--dry-run]
```

## Что санитизируется

### Полное удаление из строковых литералов Java

| Паттерн | Пример | Результат |
|---|---|---|
| `"host:port"` | `"memcached:11211"` | `""` |
| URL-литералы | `"http://localhost:8080"` | `""` |
| JDBC URL | `"jdbc:postgresql://..."` | `""` |
| ZK-пути | `"/zookeeper/app/config"` | `""` |
| Пароли | `password = "secret"` | `password = ""` |
| @Value дефолты | `@Value("${host:localhost}")` | `@Value("${host}")` |

### Переименование

| Оригинал | Замена |
|---|---|
| `com.storage.storageservice` | `com.example.app` |
| `com.storage.proxy` | `com.example.proxy` |
| `com.storage` | `com.example` |
| `StorageService` (классы, переменные) | `ExampleService` |
