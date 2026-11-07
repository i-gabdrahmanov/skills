"""implementation.py — как разрешается страница реализации кадра и что делать, когда её нет (спека §14.2; задача 4.9; INV-D2, D4, D6, F6, F13, C23).

Адрес (`track.implementation_url`): `frames[].url`; первичный кадр падает на `implementation.url` трека; состояние — на адрес своего экрана; ДИАЛОГ не
наследует НИКОГДА. Рецепт открытия (`frames[].open`) нужен диалогу и состоянию: их показывает действие поверх страницы, а не адрес.

Чего нет — решает режим, не догадка:
  • interactive: нужное называется ОДИН раз (`need` + `ask`), человек отвечает, ответ записывает `daiver_track_manifest` — после записи вопроса больше нет;
    dev-сервер interactive не стартует сам никогда;
  • autonomous: именованный останов `blocked: no-implementation` (с именем кадра) или `blocked: no-open-recipe` с тем, кто и что даёт (`supply`) — клик
    и адрес автономия не выдумывает; запуск записанного `devCommand` — `devserver.start`.

Рецепт (actions + clipSelector…), clip и viewport выбираются один раз и держатся одинаковыми во ВСЕХ итерациях: первая записанная итерация пинит их в
`verify-state.json` (`capture`), `daiver_screenshot` отказывает на отличающийся (`check_pin`). Не пинятся: адрес (порт dev-сервера меняется) и `waitMs`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import track as TR

NEEDS_RECIPE = ("dialog", "state")
PIN_KEYS = ("recipe", "viewport")

RECIPE_HOW = ("вырабатывается ОДИН раз вместе с человеком: найди в исходниках модуля то, что открывает этот вид (кнопка, ссылка), попробуй "
              "daiver_screenshot(actions=…, clipSelector=…), посмотри actual.png, и когда на картинке именно макет — запиши "
              "daiver_track_manifest(frame, set: {open: {actions, clipSelector}}); для модалки/попапа clipSelector — их элемент, для целого экрана — не указывай")


def resolve(manifest: Dict[str, Any], frame: Optional[str], mode: str) -> Dict[str, Any]:
    """{url, open, devCommand, role, need?, ask?, blocked?, supply?} для кадра; `blocked` бывает только в автономном режиме."""
    f = TR.get_frame(manifest, frame)
    role = f.get("role") or "screen"
    url = TR.implementation_url(manifest, f["name"])
    out: Dict[str, Any] = {"url": url, "open": f.get("open"), "devCommand": (manifest.get("implementation") or {}).get("devCommand"), "role": role}
    missing: List[str] = []
    if url is None:
        missing.append("url")
    if role in NEEDS_RECIPE and not f.get("open"):
        missing.append("open")
    if not missing:
        return out
    if mode == "autonomous":
        if "url" in missing:
            out.update(blocked="no-implementation", frame=f["name"],
                       supply=f"человек: адрес страницы реализации кадра «{f['name']}» — `url=` / `url.{f['name']}=` в строке задачи или запись daiver_track_manifest",
                       nextStep="остановись и скажи пользователю, какому кадру не хватает адреса; адрес не угадывай")
        else:
            out.update(blocked="no-open-recipe", frame=f["name"],
                       supply=f"рецепт открытия кадра «{f['name']}» (actions + clipSelector) — от того, кто добился его видимости на странице",
                       nextStep="остановись и скажи пользователю, какому кадру не хватает рецепта; клик не выдумывай")
        return out
    need: List[str] = []
    asks: List[str] = []
    if "url" in missing:
        where = "страницы, на которой открывается этот диалог" if role == "dialog" else "страницы реализации"
        need.append(f"адреса нет: диалог адрес не наследует, спроси у человека адрес {where}" if role == "dialog" else
                    "адреса страницы реализации нет: спроси у человека ОДИН раз и запиши daiver_track_manifest "
                    "(implementation.url — у первичного кадра, frames[].url — у остальных)")
        asks.append(f"Какой адрес {where}? (dev-сервер я сам не запускаю)")
    if "open" in missing:
        need.append("рецепта открытия нет: " + RECIPE_HOW)
        asks.append(f"Как увидеть «{f['name']}» на странице? Подскажите триггер, если не нашёл в исходниках")
    out["need"] = need
    out["ask"] = asks
    return out


def pin_of(snapshot_capture: Any) -> Optional[Dict[str, Any]]:
    """Что пинится из метаданных захвата (`snapshot.json → capture`): рецепт и viewport."""
    if not isinstance(snapshot_capture, dict) or not isinstance(snapshot_capture.get("viewport"), dict):
        return None
    return {"recipe": snapshot_capture.get("recipe") or {}, "viewport": dict(snapshot_capture["viewport"])}


def read_pin(verify_state: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(verify_state).read_text(encoding="utf-8")) if Path(verify_state).is_file() else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    pin = data.get("capture") if isinstance(data, dict) else None
    return pin if isinstance(pin, dict) and all(k in pin for k in PIN_KEYS) else None


def pin_problems(pin: Optional[Dict[str, Any]], recipe: Dict[str, Any], viewport: Dict[str, Any]) -> List[str]:
    """Чем текущий захват отличается от пина прошлых итераций (INV-C23); пусто — одинаково или пина ещё нет."""
    if pin is None:
        return []
    out: List[str] = []
    if pin["viewport"] != viewport:
        out.append(f"viewport {viewport['width']}×{viewport['height']}, а на прошлых итерациях {pin['viewport'].get('width')}×{pin['viewport'].get('height')}")
    for key in sorted(set(pin["recipe"]) | set(recipe)):
        if pin["recipe"].get(key) != recipe.get(key):
            label = {"clipSelector": "clipSelector", "actions": "рецепт открытия (actions)", "waitForSelector": "waitForSelector",
                     "waitForGone": "waitForGone"}.get(key, key)
            out.append(f"{label}: сейчас {json.dumps(recipe.get(key), ensure_ascii=False)}, на прошлых итерациях "
                       f"{json.dumps(pin['recipe'].get(key), ensure_ascii=False)}")
    return out
