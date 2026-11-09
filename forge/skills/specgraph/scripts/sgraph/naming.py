"""naming.py — имена узлов, по которым понятно, что это, не открывая заметку (обратная связь аналитика 2026-11-09).

Имя узла — это имя заметки и текст ссылки `[[…]]` на всех переходах графа. Из заголовков спеки приходили
имена, ничего не говорящие без контекста: «Шаги обработки» (какой обработки? — в разделе Kafka),
«Описание», «Получение данных по заявке REST» (это шедулер? какой?), а один и тот же вид UI назывался
то «UI …», то «Экран …», то «… — экран». Правила здесь — детерминированные и данными
(`naming` в `references/extract-defaults.json`, проект переопределяет в `.spec-graph/config.json`):

  • общий заголовок (все слова — из `generic_words`) уточняется: у Kafka-операции — топиком по шаблону роли
    («Обработка сообщений <топик>»), у прочих — ближайшим содержательным разделом-предком
    («Шаги обработки (Приём заявки)»), без предка — файлом;
  • у вида с `prefix` (UI, шедулер) приставки и хвосты-синонимы снимаются (`strip`) и ставится одна приставка:
    «Гибридная почта — экран», «Экран гибридной почты» → «UI Гибридная почта», «UI гибридной почты»; у
    шедулера без слова-маркера (`keep_if`) первое слово ставится в родительный падеж:
    «Получение данных по заявке» → «Шедулер получения данных по заявке» (не склоняется — «Шедулер — …»).

Прежнее имя остаётся в names_seen узла каталога: решения и планы раскладки по нему находят узел.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_WORD = re.compile(r"[^\W\d_]{2,}")
_LATIN = re.compile(r"[A-Za-z]")
# родительный падеж первого слова: (окончание, замена) — по порядку, первое совпавшее
_GENITIVE = (("ние", "ния"), ("тие", "тия"), ("ье", "ья"), ("ция", "ции"), ("сия", "сии"), ("ия", "ии"),
             ("ка", "ки"), ("га", "ги"), ("ха", "хи"), ("жа", "жи"), ("ша", "ши"), ("ча", "чи"), ("ща", "щи"),
             ("а", "ы"), ("я", "и"))
_ADJ = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие")
_CONSONANT = set("бвгджзклмнпрстфхцчшщ")


def settings(rules: Dict[str, Any]) -> Dict[str, Any]:
    return rules.get("naming") or {}


def _norm(w: str) -> str:
    return w.lower().replace("ё", "е")


def is_generic(name: str, cfg: Dict[str, Any]) -> bool:
    """Все слова имени — служебные (`generic_words`): имя не отличает узел от соседей."""
    words = {_norm(w) for w in _WORD.findall(name)}
    generic = {_norm(w) for w in cfg.get("generic_words") or []}
    return bool(words) and words <= generic


def genitive(phrase: str) -> Optional[str]:
    """Фраза с первым словом в родительном падеже («Получение данных» → «получения данных») или None,
    если первое слово так не склоняется (латиница, прилагательное, мягкий знак, мн. число)."""
    first, _, rest = phrase.partition(" ")
    if not first or _LATIN.search(first) or (first.isupper() and len(first) > 1):
        return None
    low = first.lower()
    # отглагольные «-ние/-тие» — существительные, хотя кончаются как прилагательное на «-ие»
    if low.endswith(_ADJ) and not low.endswith(("ние", "тие")) or low.endswith(("ь", "и", "ы")):
        return None
    for end, rep in _GENITIVE:
        if low.endswith(end):
            return (low[: -len(end)] + rep + (" " + rest if rest else "")).strip()
    if low[-1] in _CONSONANT:
        return (low + "а" + (" " + rest if rest else "")).strip()
    if low.endswith("й"):
        return (low[:-1] + "я" + (" " + rest if rest else "")).strip()
    return None


def _strip(name: str, patterns: List[str]) -> str:
    out = name
    changed = True
    while changed:
        changed = False
        for rx in patterns:
            n = re.sub(rx, "", out, count=1, flags=re.I).strip(" -—–:")
            if n != out and n:
                out, changed = n, True
    return out


def normalize(kind: str, role: Optional[str], name: str, ctx: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """Имя узла по правилам `naming`; правило не применимо — имя как есть. ctx: parents — заголовки
    разделов-предков (ближайший первым, уже очищенные), topics — топики раздела, file — имя файла без .md."""
    if not cfg or not isinstance(name, str) or not name.strip():
        return name
    spec = (cfg.get("kinds") or {}).get(kind) or {}
    out = name.strip()
    if spec.get("prefix"):
        bare = _strip(out, spec.get("strip") or [])
        keep = any(re.search(r"(?<![^\W\d_])" + re.escape(m), out, re.I) for m in spec.get("keep_if") or [])
        if keep:
            return out
        if is_generic(bare, cfg):
            q = _qualifier(ctx, cfg)
            bare = q or bare
        if spec.get("genitive"):
            g = genitive(bare)
            return f"{spec['prefix']} {g}" if g else f"{spec['prefix']} — {bare}"
        return f"{spec['prefix']} {bare}"
    if is_generic(out, cfg):
        tmpl = (spec.get("generic_template") or {}).get(role or "")
        topics = ctx.get("topics") or []
        if tmpl and topics:
            return tmpl.format(topic=topics[0])
        q = _qualifier(ctx, cfg)
        if q:
            return f"{out} ({q})"
    return out


_FOLDER_CODE = re.compile(r"^\s*[\[(][^\])]+[\])]\s*")
_INDEX_FILES = {"readme", "index"}


def _qualifier(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """Чем уточнить общее имя: ближайший содержательный раздел-предок, иначе имя файла (не README),
    иначе папка без кода (`[kid_seizure] Изъятие` → «Изъятие»: так же различает одноимённых _suggest)."""
    for t in ctx.get("parents") or []:
        if t and not is_generic(t, cfg):
            return t
    f = (ctx.get("file") or "").replace("-", " ").replace("_", " ").strip()
    if f and f.lower() not in _INDEX_FILES and not is_generic(f, cfg) and len(_WORD.findall(f)) and len(f) > 2:
        return f
    d = _FOLDER_CODE.sub("", ctx.get("folder") or "").replace("-", " ").replace("_", " ").strip()
    return d if d and not is_generic(d, cfg) else None


def context(blocks: List[Dict[str, Any]], secs: List[Dict[str, Any]], si: Optional[int], rel: str,
            topic_re: "re.Pattern[str]") -> Dict[str, Any]:
    """Контекст раздела si файла: заголовки предков (ближайший первым), топики в тексте раздела, имя файла."""
    from . import extract as X
    parents, topics = [], []
    if si is not None:
        p = secs[si]["parent"]
        while p is not None:
            parents.append(X.clean_title(secs[p]["title"]))
            p = secs[p]["parent"]
        s = secs[si]
        topics = sorted(set(topic_re.findall("".join(b["text"] for b in blocks[s["start"]:s["end"]]))),
                        key=lambda t: "".join(b["text"] for b in blocks[s["start"]:s["end"]]).find(t))
    parts = rel.rsplit("/", 2)
    stem = parts[-1][:-3] if parts[-1].lower().endswith(".md") else parts[-1]
    return {"parents": parents, "topics": topics, "file": stem, "folder": parts[-2] if len(parts) > 1 else ""}
