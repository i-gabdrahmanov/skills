"""texts.py — тексты макета трека для проверок вне движка (хук pii-boundary, задача 3.4; INV A11).

Зачем. Макет — это и реалистичные тексты: имена, телефоны, почты. Сборка кладёт их в `mocks.ts` модуля
(правило ui-build: «тексты макета — в mocks.ts и defaultProps»). Общий хук `pii-boundary` режет запись
PII-подобных строк вне тестов, и без знания о макете он блокировал бы ровно ту запись, которую требует бриф.
Здесь — единственный источник «что в макете написано дословно»: `mockup_texts` отдаёт тексты канона кадров
трека (через `canon.collect_texts`, тот же контракт «текст не теряется», что у outline/section), а `unmatched`
сверяет с ними строки, которые хук считает PII.

Только читает диск. Не отвечает на вопрос «можно ли писать» — это решает хук; модуль даёт факты и именованный
отказ (`TextsError`), когда фактов нет: трека нет, канон повреждён. Для хука отказ = исключения нет (fail-closed).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from . import canon as C
from . import track as TR


class TextsError(ValueError):
    """Тексты макета получить нельзя: трека нет, манифест или канон кадра повреждены, ни у одного кадра нет канона."""


def mockup_texts(root: Path, track: str) -> List[str]:
    """Тексты канона всех кадров трека: уникальные строки дословно, в порядке первого появления (кадры — по манифесту).

    Видимые тексты узлов, `component.content` и `textProps` инстансов (`canon.collect_texts`; occluded-поддеревья
    не входят). Кадр без файла канона пропускается (его ещё не нормализовали); повреждённый канон — `TextsError`, а
    не молчаливый пропуск: часть макета, которую не прочли, нельзя выдавать за полный список."""
    try:
        manifest = TR.load(Path(root), track)
    except TR.TrackError as e:
        raise TextsError(str(e))
    if manifest is None:
        raise TextsError(f"трека «{track}» нет (.daiver/tracks/{track}/track.yaml): сверять тексты не с чем")
    out: List[str] = []
    seen = set()
    read = 0
    for frame in manifest.get("frames") or []:
        path = TR.frame_paths(Path(root), manifest, frame["name"])["canonical"]
        if not path.is_file():
            continue
        try:
            env = C.read_canonical(path)
        except C.CanonError as e:
            raise TextsError(f"канон кадра «{frame['name']}» не читается: {e}")
        read += 1
        for item in C.collect_texts(env["root"]):
            text = item["text"]
            if text not in seen:
                seen.add(text)
                out.append(text)
    if not read:
        raise TextsError(f"у трека «{track}» нет ни одного канона кадра — сверять тексты не с чем "
                         f"(кадр нормализует daiver_normalize)")
    return out


def unmatched(texts: Iterable[str], needles: Iterable[str]) -> List[str]:
    """Строки из `needles`, которых нет ДОСЛОВНО (как подстроки) ни в одном из `texts`; порядок сохранён, повторы убраны.

    Проверить нечего у пустой, пробельной или не-строки — такая считается не найденной (fail-closed)."""
    pool = [t for t in texts if isinstance(t, str)]
    missing: List[str] = []
    for needle in needles:
        shown = needle if isinstance(needle, str) else repr(needle)
        if shown in missing:
            continue
        if not isinstance(needle, str) or not needle.strip() or not any(needle in t for t in pool):
            missing.append(shown)
    return missing
