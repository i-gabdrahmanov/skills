# Bitbucket через MCP

## Определение target-ветки

Bitbucket требует явный `destination` (target branch). Алгоритм определения:

1. Если в репозитории есть `develop` — это самый частый target в gitflow-проектах.
2. Иначе — `main` или `master`.
3. Сверь с тем, от чего форкнута текущая ветка:
   ```bash
   git merge-base --fork-point develop HEAD 2>/dev/null || \
   git merge-base --fork-point main HEAD 2>/dev/null
   ```
4. Если не определилось — спроси пользователя.

## Определение workspace/repo

Перед созданием PR нужны `workspace` и `repo_slug`. Возьми из remote URL:

```bash
git remote get-url origin
# https://bitbucket.example.com/scm/PROJ/repo-name.git → workspace=PROJ, repo=repo-name
# https://bitbucket.org/myws/repo-name.git → workspace=myws, repo=repo-name
```

В Bitbucket Server (on-premise) часто используется термин `project` вместо `workspace`.
Подстрой под имена параметров конкретного MCP.

## Тело PR

- **title**: первая строка коммита. Если коммитов несколько — формат `[STOR-123] <summary>`.
- **description**: тот же текст-отчёт, что отправлен в Jira, плюс первой строкой
  ссылка на Jira-задачу. Например:
  ```markdown
  Jira: https://your.atlassian.net/browse/STOR-123

  **Что сделано:** ...
  ```
- **reviewers**: не назначай автоматически. Если MCP требует список — передавай пустой
  или спроси пользователя.
- **close_source_branch**: false по умолчанию.

## После создания

- Верни URL PR пользователю отдельной строкой — он должен быть кликабельным.
- Если у пользователя нет Jira-DC интеграции, предложи добавить ссылку на PR в Jira
  отдельным коротким комментарием (одной строкой).
