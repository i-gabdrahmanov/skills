# forgelite — переехал в forge (объединён)

Отдельного standalone-харнеса `forgelite` больше нет. Lite-исполнение подготовленной
задачи Jira объединено с `forge` в один харнес с роутером на входе:

- **Вход:** `../forge/skills/router/SKILL.md` — первым действием спрашивает full или lite и делегирует.
- **Lite-оркестратор:** `../forge/skills/forgelite/SKILL.md` (плоские шаги `lite-*`, стейт в namespace `forgelite`).
- **Full-путь:** `../forge/skills/feature-pipeline/SKILL.md`.
- **Хуки:** общие `../forge/hooks/` — dual-vocabulary (понимают и `04-test-/04-build-`, и `lite-*`).

Установка и запуск — только через forge:
```
bash ../forge/deploy.sh <project>
gigacode --experimental-hooks -p "..."
```

Подробнее — раздел «Router + режимы full/lite» в [`../forge/FORGE.md`](../forge/FORGE.md).
