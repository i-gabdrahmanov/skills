# Установка forge (extension)

Проверено на `qwen 0.19.5`. Для форка GigaCode — см. раздел в конце.

## 0. Требования

- Рантайм с поддержкой `extensions`: `qwen` (dev) или `gigacode` (prod).
  Проверить: `qwen extensions --help` (должны быть `install/link/list/...`).
- `python3` в `PATH` (хуки forge исполняются как `python3 …`).

## 1. Убрать дрейф — ОБЯЗАТЕЛЬНО, если forge уже деплоился раньше

Extension несёт свои хуки в `hooks/hooks.json` и рантайм грузит их **поверх** `settings.json`
(они не заменяют, а складываются). Если те же forge-хуки уже прописаны в `~/.qwen/settings.json`
(старый `deploy.sh`), они **задвоятся**. `link`/`install` сам это НЕ чистит — settings.json не трогается.

Проверить, что там есть forge-хуки:

```bash
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.qwen/settings.json")   # для GigaCode: ~/.gigacode/settings.json
d = json.load(open(p)); names=set()
for arr in (d.get("hooks") or {}).values():
    for e in arr:
        for h in (e.get("hooks") or []):
            if h.get("name"): names.add(h["name"])
forge = {"destructive-blocker","fork-syntax-guard","pii-boundary","state-write-guard",
         "sod-enforcer","inline-phase-guard","gate-guard","tdd-guard","eval-guard",
         "grounding-evidence","prompt-guard","file-journal","context-injector",
         "state-recorder","phase-gate"}
print("forge-хуки в settings.json:", sorted(names & forge))
print("прочие (НЕ трогать):", sorted(names - forge))
PY
```

Если пересечение непустое — сделать бэкап и убрать **только** forge-хуки из блока `hooks`
(остальные, напр. свои `agent-logger`, оставить):

```bash
cp ~/.qwen/settings.json ~/.qwen/settings.json.bak   # бэкап
# затем вручную удалить forge-записи из "hooks" (или удалить блок целиком, если он весь форжевый)
```

## 2. Установить

**Вариант A — `link` (разработка, живые правки).** Копий не делает: пишет указатель на папку,
рантайм читает `forgeExt/` напрямую. Папку нельзя двигать/удалять.

```bash
qwen extensions link /Users/iskandergabdrahmanov/Documents/dev/skills/forgeExt
```

**Вариант B — `install` (копия-снимок, «поставить насовсем»).** Копирует папку в
`~/.qwen/extensions/forge/`, живёт независимо от источника.

```bash
qwen extensions install /Users/iskandergabdrahmanov/Documents/dev/skills/forgeExt --consent
```

Без `--consent` покажется trust-промпт со списком хуков/скиллов/команд — ответить `Y`.

## 3. Проверить

```bash
qwen extensions list      # → ✓ Forge (1.0.0), команды /forge, /forge-lite
```

Если `/forge` не виден в текущей сессии — перезапустить `qwen`.

## 4. Обновить

- **`link`:** ничего не нужно. Поменял forge/ → `bash forgeExt/sync-from-forge.sh` → рестарт сессии.
- **`install` (копия):** поднять `version` в `qwen-extension.json`, затем
  `qwen extensions update forge`. Либо гарантированно: `uninstall` + `install` заново.

## 5. Снять / сменить способ

```bash
qwen extensions uninstall forge
```

Смена копия ↔ link: имя `forge` уникально, поэтому сначала `uninstall`, потом `link`/`install` заново.
Временно выключить без снятия: `qwen extensions disable forge` / `enable forge`.

## GigaCode (prod, другая машина)

Механизм тот же, отличия:

- бинарь `gigacode`, каталог расширений `.gigacode/extensions/`, settings — `~/.gigacode/settings.json`;
- если `gigacode extensions link` ругнётся на манифест — скопировать
  `qwen-extension.json` → `gigacode-extension.json` (ре-бренд форка);
- шаг 1 (дрейф) делать в `~/.gigacode/settings.json`.

## Установка по git-URL — пока НЕ из этого репо

`qwen extensions install <repo> --ref <branch>` ждёт манифест в **корне** репозитория, а forgeExt —
подкаталог `skills/forgeExt/`. Чтобы включить git-install: отдельный репо (корень = extension)
либо orphan-ветка с содержимым forgeExt в корне. Пока — ставить локально из клона (`link`/`install <путь>`).
