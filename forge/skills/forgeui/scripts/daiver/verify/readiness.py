"""readiness.py — «страница готова к снимку» (спека §14.3, константы §20.4). Чистая логика, без браузера.

Готово = за отпущенное время `waitMs` одновременно: (1) нет признаков загрузки (loader/spinner/skeleton/
aria-busy/progressbar, недогруженные картинки, шрифты, документ), (2) DOM не менялся не меньше `domSettleMs`,
(3) подпись содержимого одинакова в `STABLE_SAMPLES` замерах подряд. `waitMs` — потолок, а не стоимость:
страница, готовая раньше, не ждёт до конца.

Не готово — это ответ, а не ошибка: `ready: false` вместе с причинами. Захват грузящегося экрана НЕ сравнивают
и его дефекты не репортят (спека §14.3); поднять `waitMs` или назвать загрузчик через waitForGone/waitForSelector.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

SETTLE_MS = 400
DOM_SETTLE_MS = 1500
POLL_MS = 250
STABLE_SAMPLES = 2
MAX_REASONS = 5


def _reasons(sample: Optional[Dict[str, Any]], stable: int, stable_samples: int, dom_settle_ms: int) -> List[str]:
    """Что мешало готовности в ПОСЛЕДНЕМ замере — по-русски, чтобы агент поднял нужное, а не «ждал ещё»."""
    if not sample:
        return ["ни одного замера не получено"]
    out: List[str] = []
    for item in (sample.get("loading") or [])[:MAX_REASONS]:
        out.append(f"признак загрузки: {item.get('reason')} ({item.get('element')})")
    since = sample.get("sinceMutationMs")
    if isinstance(since, (int, float)) and since < dom_settle_ms:
        out.append(f"DOM менялся {int(since)} мс назад (нужна тишина {dom_settle_ms} мс)")
    if stable < stable_samples:
        out.append(f"содержимое менялось между замерами (одинаковых подряд {stable} из {stable_samples})")
    return out


def wait_ready(poll: Callable[[], Dict[str, Any]], wait_ms: float, *, sleep: Callable[[float], None],
               clock: Callable[[], float], settle_ms: float = SETTLE_MS, dom_settle_ms: float = DOM_SETTLE_MS,
               poll_ms: float = POLL_MS, stable_samples: int = STABLE_SAMPLES) -> Dict[str, Any]:
    """Опрашивает `poll()` → {sig, loading:[{reason, element}], sinceMutationMs}; `sleep(секунды)` и `clock()` — внешние
    (в тестах — поддельные, в боевом режиме sleep качает события браузера, чтобы диалоги закрывались сами).

    → {ready, waitedMs, polls, reasons}. Первый замер — после settleMs от вызова (анимации появления, разметка)."""
    start = clock()
    deadline = start + wait_ms / 1000.0
    sleep(max(0.0, min(settle_ms / 1000.0, deadline - clock())))
    prev: Any = None
    stable = 0
    polls = 0
    sample: Optional[Dict[str, Any]] = None
    while True:
        sample = poll()
        polls += 1
        sig = sample.get("sig") if isinstance(sample, dict) else None
        stable = stable + 1 if (sig is not None and sig == prev) else 1
        prev = sig
        loading = (sample or {}).get("loading") or []
        since = (sample or {}).get("sinceMutationMs")
        quiet = isinstance(since, (int, float)) and since >= dom_settle_ms
        if sig is not None and not loading and quiet and stable >= stable_samples:
            return {"ready": True, "waitedMs": int((clock() - start) * 1000), "polls": polls, "reasons": []}
        left = deadline - clock()
        if left <= 0:
            break
        sleep(min(poll_ms / 1000.0, left))
    return {"ready": False, "waitedMs": int((clock() - start) * 1000), "polls": polls,
            "reasons": _reasons(sample, stable, stable_samples, int(dom_settle_ms))}
