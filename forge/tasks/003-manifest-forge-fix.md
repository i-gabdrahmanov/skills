# Задача 003 — Добавить `/forge-fix` в описания манифестов

## Статус
Закрыта · won't-fix · 2026-08-22

## Решение
Манифесты extension (`qwen-extension.json` / `gigacode-extension.json`) больше не существуют
и не будут существовать: forge оставлен только в project-модели (`deploy.sh` → `<project>/.gigacode/`),
extension-модель снята (см. README.md «Канон установки»). Состав 5 команд и 20 скиллов
зафиксирован в README.md (раздел «Состав») и `SKILLS-REGISTRY.md` — они и есть single
source of truth.

## Контекст (исторический)
Манифесты extension (`qwen-extension.json` и `gigacode-extension.json`) содержали одинаковое
`description`, в котором были перечислены только **4 команды**:
```
... слэш-команды /forge, /forge-lite, /forge-spec, /forge-merge.
```
Файл команды `/forge-fix` существовал (`commands/forge-fix.md`), но в описании не упоминался.

## Симптом (исторический)
При установке/линковке extension (trust-промпт рантайма) пользователь видел неполный список
доступных слэш-команд — без `/forge-fix`.

## Критерии приёмки (архив)
- [ ] В `description` обоих манифестов (qwen + gigacode) перечислены все 5 команд.
- [ ] `qwen-extension.json` и `gigacode-extension.json` содержат идентичный текст описания.

## Файлы
- (не существуют) `qwen-extension.json`
- (не существуют) `gigacode-extension.json`
- `README.md` (раздел «Состав» — актуальный источник правды по составу команд)
