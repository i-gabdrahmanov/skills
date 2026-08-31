# Задача 013 — Отказ гейта закрытия выглядел крахом, а его escape-hatch вёл в стену

## Статус
Закрыта · исправлено 2026-08-28

## Контекст
Найдено на e2e-прогоне харнеса на qwen CLI (метапроект, `forgefix`/BUG-1) — статическим
чтением кода эти три дефекта не видны, они проявляются только при попытке реально пройти
цепочку `record_gate → update.py → судьи`.

### 13.1 Семь гейтов закрытия отдавали ТРЕЙСБЕК с rc=1
`update.py` бросал голый `RuntimeError` в семи местах, а `main()` звался без обёртки:

```
Traceback (most recent call last):
  File ".../update.py", line 922, in <module>
    main()
  ...
RuntimeError: Шаг fix-green нельзя закрыть: нет evidence, что фаза прошла через субагента…
```

Последствия:
- для модели это «скрипт сломан» — она лезет чинить `update.py` вместо того, чтобы
  выполнить требование гейта;
- сам текст отказа — в ХВОСТЕ stderr, после стека; слабая модель читает начало;
- `rc=1` неотличим от настоящего краха (битый JSON, нет файла) — оркестратор не может
  ветвиться по коду возврата.

### 13.2 Рецепт снятия гейта вёл в стену
`_override_hint` (его печатает КАЖДЫЙ баннер гейта) шагом 2 предлагал руками создать
`ground/approvals/gate-override-<judge>.json` с `{"approved_by","reason"}`. Такой маркер:
- блокирует `state-write-guard` (путь `ground/approvals/` в `_CP_PATTERNS`);
- не засчитывает `gate-guard._approval_valid` и сам `override_judge` — нужен провенанс
  `produced_by:"record_approval"`.

Плюс в тексте `_check_subagent_origin` стояла фраза «флаг `--closed-by` считается
доказательством», прямо противоречащая докстрингу функции («Флаг --closed-by больше не
является доказательством») — модель по ней подставляет `--closed-by subagent` и упирается снова.

### 13.3 `--skill` по умолчанию уводил в чужой namespace
Пять скриптов держали `--skill` с дефолтом `feature-pipeline`. На forgefix/forgelite это
молча уводило чтение/запись в другой каталог. Воспроизведено:

```
$ override_judge.py --judge subagent-origin --feature BUG-1 --reason "…"
Теперь можно закрыть шаг:
  python3 update.py --skill feature-pipeline --feature BUG-1 …     ← и skill не тот
$ ls ground/statements
feature-pipeline    forgefix          ← создан фантомный namespace
$ update.py --skill forgefix … --status completed
[DENY] нет evidence, что фаза прошла через субагента               ← override не виден
```

`override_judge` рапортовал `rc=0` и «Теперь можно закрыть шаг», а гейт продолжал блокировать.
Escape-hatch, который печатает каждый баннер, оказывался тупиком — и модель уходила в цикл
«снял override → всё ещё блок».

## Критерии приёмки
- [x] Гейты закрытия бросают `StepGateBlocked` (подтип `RuntimeError`); `__main__` печатает
      `[update] DENY: <причина>` и выходит с `rc=2`. Трейсбек остаётся только у настоящих
      поломок скрипта.
- [x] `_override_hint` печатает `record_approval.py --key gate-override-<judge>` и объясняет,
      почему рукописный маркер не считается.
- [x] Формулировка про `--closed-by` исправлена на «доказательством НЕ считается».
- [x] `--skill` резолвится в namespace активного прогона (`_util.resolve_skill` /
      `_resolve_skill_ns`) в `override_judge`, `run_judge`, `run_pending_evals`,
      `preflight-validate`. `check_paths.py --skill` не трогаем — там это имя скилла для
      поиска `skill-paths.json`, другой смысл.
- [x] Регрессионные тесты: форма отказа (rc=2, без `Traceback`, с `[update] DENY:`) и рецепт,
      указывающий на `record_approval.py`.
- [x] Проверено вживую: `record_approval → override_judge → record_gate → update.py` закрывает
      шаг, предупреждение об override остаётся в манифесте для аудита.

## Файлы
- `skills/pipeline-state/scripts/update.py`, `override_judge.py`, `_util.py`
- `skills/feature-pipeline/scripts/{run_judge,run_pending_evals,preflight-validate}.py`
- `skills/pipeline-state/scripts/test_gate_result.py`

---

## 13.4 Зелёный preflight при полностью снятом enforcement

Найдено там же, на стенде. `deploy.sh` кладёт всё в `<project>/.gigacode/` и туда же пишет
`settings.json`; стоковый qwen-code читает `<project>/.qwen/settings.json`:

```
qwen-бандл:  SETTINGS_DIRECTORY_NAME = ".qwen"     упоминаний ".gigacode" — 0
preflight:   проверял только <base>/settings.json, про .qwen не знал ничего
```

То есть на стоковом qwen деплой отрабатывает, `preflight` возвращает `errors: []`, а рантайм
не загружает НИ ОДНОГО хука. Стенд был бы мёртвым, и я бы этого не заметил — блок `hooks`
пришлось класть в `.qwen/settings.json` руками.

На форке GigaCode расхождения нет по построению (базовый каталог и есть `.gigacode`), поэтому
`_check_runtime_settings_dirs` предупреждает только когда рядом РЕАЛЬНО существует второй
каталог настроек рантайма и хуков forge в нём нет. Уровень — warning, а не error: какой CLI
запускает оператор, из проекта не видно, и ронять деплой из-за постороннего `.qwen/` неверно.

- [x] `preflight._check_runtime_settings_dirs` сверяет каталог деплоя с каталогами настроек
      известных рантаймов (`.gigacode`, `.qwen`) и громко предупреждает о неармленном.
- [x] Тесты: предупреждение при неармленном соседе; тишина, когда сосед армлен; тишина,
      когда соседа нет вовсе (машина только с форком).
