# Установка forge (extension)

Боевой рантайм — **GigaCode**: бинарь `gigacode`, базовый каталог `~/.gigacode`, манифест
`gigacode-extension.json`. Все команды ниже — про него. На дев-машине то же самое проверяется
стоковым `qwen` (`~/.qwen`, `qwen-extension.json`) — механизм идентичный, отличаются только
бинарь и базовый каталог (см. раздел в конце). Проверено на `qwen 0.19.5`.

## 0. Требования

- Рантайм с поддержкой `extensions`.
  Проверить: `gigacode extensions --help` (должны быть `install/link/list/...`).
- `python3` в `PATH` (хуки forge исполняются как `python3 …`).

## 1. Убрать дрейф — ОБЯЗАТЕЛЬНО, если forge уже деплоился раньше

Старая раскладка не удаляется сама и **побеждает** extension в двух местах сразу: хуки
складываются (1.1), а скиллы и команды прямо перекрываются (1.2). `preflight.py` проверяет оба
пункта: 1.2 — жёсткая ошибка (exit 1), 1.1 — предупреждение.

**Делает это одна команда** — `cleanup-legacy.sh` (лежит рядом с манифестом). По умолчанию он
только печатает план; форж-своё определяет по составу самого extension'а плюс списку снятых
артефактов (`log-agent.py`, `evidence-enforcer.py`, `forge.toml`, …), а самописные скиллы
оператора не трогает и ничего не удаляет безвозвратно — переносит в `forge-legacy-backup-<TS>/`:

```bash
bash <ext>/cleanup-legacy.sh                                  # план: что будет снято
bash <ext>/cleanup-legacy.sh --apply                          # $HOME: .gigacode/.qwen/.agents
bash <ext>/cleanup-legacy.sh --apply /path/repo                # + legacy-раскладка в проекте
```

Корпоративный контур, где писать в `$HOME` или в каталог проекта не дают: увести бэкап в
доступное место — `--backup-dir /tmp/forge-bak`. Скрипт скопирован из extension'а (в `~/bin`
и т.п.) — покажи ему эталон состава: `--ext ~/.gigacode/extensions/forge`; установленный
extension он находит и сам, а вот без эталона не запустится вовсе (снял бы лишь часть). Отказ доступа на отдельном файле прогон не
роняет: скрипт называет файл и причину, снимает остальное и возвращает 1 — повтор безопасен.

Затем **перезапустить сессию** (рантайм кэширует список скиллов на старте) и проверить
`preflight.py` — ошибок «старые копии перекрывают extension» быть не должно.

Ниже — то же самое вручную, если скрипт запускать негде.

### 1.1 Хуки в `settings.json`

Extension несёт свои хуки в `hooks/hooks.json` и рантайм грузит их **поверх** `settings.json`
(они не заменяют, а складываются). Если те же forge-хуки уже прописаны в `~/.gigacode/settings.json`
(старый `deploy.sh`), они **задвоятся**. `link`/`install` сам это НЕ чистит — settings.json не трогается.

Проверить, что там есть forge-хуки:

```bash
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.gigacode/settings.json")   # на дев-машине с qwen: ~/.qwen/settings.json
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

Смотреть надо на `command`, а не на `name`: запись может называться как угодно, а звать
форжевый скрипт — так, `agent-logger` в старых деплоях указывает на снятый `log-agent.py`.
Если пересечение непустое — сделать бэкап и убрать **только** forge-хуки из блока `hooks`
(чужие оставить):

```bash
cp ~/.gigacode/settings.json ~/.gigacode/settings.json.bak   # бэкап
# затем вручную удалить forge-записи из "hooks" (или удалить блок целиком, если он весь форжевый)
```

### 1.2 Скиллы и команды старого деплоя — ГЛАВНАЯ ловушка

Скилл рантайм резолвит в порядке **project > user > extension**, а одноимённую слэш-команду
extension'а — переименовывает. Значит любой оставшийся от `deploy.sh` каталог
(`~/.gigacode/skills/feature-pipeline`, `<project>/.gigacode/skills/...`) **молча подменяет** брифы
фаз версией годичной давности. Снаружи это выглядит непонятно: хуки-то новые и preflight их
видит зелёными, но оркестратор идёт по мёртвым путям старого SKILL.md
(`python3 ~/.gigacode/hooks/preflight.py`) и валится на первом шаге.

Найти пересечение (проверяются `.gigacode`, `.qwen`, `.agents` — на уровне проекта и `$HOME`):

```bash
python3 <ext>/hooks/preflight.py --project .   # ошибка «старые копии перекрывают extension»
```

Убрать **только форж-своё**: в тех же каталогах лежат самописные скиллы оператора
(`pptx`, `pdf`, `skill-creator`, …) — их не трогать. Безопаснее не удалять, а отложить:

```bash
mkdir -p ~/forge-legacy-backup
for s in $(ls <ext>/skills); do
  [ -d ~/.gigacode/skills/"$s" ] && mv ~/.gigacode/skills/"$s" ~/forge-legacy-backup/
done
```

Затем перезапустить сессию — рантайм кэширует список скиллов на старте.

## 2. Установить

**Вариант A — `link` (разработка, живые правки).** Копий не делает: пишет указатель на папку,
рантайм читает `forgeExt/` напрямую. Папку нельзя двигать/удалять.

```bash
gigacode extensions link <путь>/forgeExt
```

**Вариант B — `install` (копия-снимок, «поставить насовсем»).** Копирует папку в
`~/.gigacode/extensions/forge/`, живёт независимо от источника.

```bash
gigacode extensions install <путь>/forgeExt --consent
```

Без `--consent` покажется trust-промпт со списком хуков/скиллов/команд — ответить `Y`.

## 3. Проверить

```bash
gigacode extensions list  # → ✓ Forge (1.0.0), команды /forge, /forge-fix, /forge-lite, /forge-spec, /forge-merge
```

Если `/forge` не виден в текущей сессии — перезапустить рантайм.

## 4. Обновить

- **`link`:** ничего не нужно. Правишь этот каталог напрямую (он и есть source-of-truth) →
  рестарт сессии.
- **`install` (копия):** поднять `version` в манифестах (`gigacode-extension.json` И
  `qwen-extension.json` — они побайтово одинаковы), затем `gigacode extensions update forge`.
  Либо гарантированно: `uninstall` + `install` заново.

## 5. Снять / сменить способ

```bash
gigacode extensions uninstall forge
```

Снимает только сам extension. Остатки СТАРОЙ раскладки (скиллы/команды/хуки прошлого `deploy.sh`
в `~/.gigacode`, `~/.qwen`, `<project>/.gigacode`) это не трогает — они продолжат перекрывать
любую следующую установку: чистить их отдельно, `bash <ext>/cleanup-legacy.sh` (§1).

Смена копия ↔ link: имя `forge` уникально, поэтому сначала `uninstall`, потом `link`/`install` заново.
Временно выключить без снятия: `gigacode extensions disable forge` / `enable forge`.

## Стоковый qwen (дев-машина)

Механизм тот же, отличия только в именах:

- бинарь `qwen`, каталог расширений `~/.qwen/extensions/`, settings — `~/.qwen/settings.json`;
- манифест `qwen-extension.json` (побайтовая копия `gigacode-extension.json`) уже в комплекте —
  копировать руками не нужно; при бампе `version` править **оба** — они не генерируются;
- шаг 1 (дрейф) — тот же `cleanup-legacy.sh`: он проходит по всем базам (`.gigacode`, `.qwen`,
  `.agents`), поэтому и дев-, и боевую раскладку чистит один вызов.

## Установка по git-URL — пока НЕ из этого репо

`gigacode extensions install <repo> --ref <branch>` ждёт манифест в **корне** репозитория, а forgeExt —
подкаталог `skills/forgeExt/`. Чтобы включить git-install: отдельный репо (корень = extension)
либо orphan-ветка с содержимым forgeExt в корне. Пока — ставить локально из клона (`link`/`install <путь>`).
