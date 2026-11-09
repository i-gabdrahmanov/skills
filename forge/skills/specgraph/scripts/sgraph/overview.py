"""overview.py — навигационные блоки заметок: обзор сервиса и ход процесса (обратная связь аналитика 2026-11-09).

Нарезка разносит спеку по заметкам, связи кладут синк и таблица оснований, но человек, открывший
заметку, видел набор ссылок, а не работу системы: «четыре алгоритма — непонятно, какое сообщение их
запускает и в каком порядке», «главная страница сервиса — один общий список». Здесь из уже проверенных
рёбер (ничего нового в граф не добавляется) собираются блоки для чтения:

  • `graph:overview` — у сервиса, сразу под именем: назначение (первый абзац его текста), входы (REST,
    Kafka-чтение, шедулеры) — что каждый делает, выходы (публикации, вызовы чужих сервисов), шаги-алгоритмы,
    бизнес-операции, данные, связанные сервисы;
  • `graph:flow` — у входа (обработчик сообщения, шедулер, эндпойнт): запуск → шаги по порядку упоминания в
    тексте → что получается и кто читает дальше; у шага (алгоритм) — где он выполняется (цепочки от входов);
    у бизнес-операции — жизненный цикл задачи (формирование → UI → закрытие).

Какие виды — входы, шаги, операции и какими рёбрами они связаны, задаёт раздел `flow` в
`references/obsidian.json` (виды знает только онтология и её данные). Блоки пересобираются синком
целиком; проза человека пишется вне маркеров.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import fm as F
from . import ontology as O

MAX_DEPTH = 5
PURPOSE_MAX = 420
_NOT_PROSE = re.compile(r"^\s*(?:#|\||```|~~~|<|[-*+]\s|\d+[.)]\s|!\[|>|↳ )")


def settings() -> Dict[str, Any]:
    from . import obsidian as OB
    return OB.load()["flow"]


class View:
    """Граф vault для чтения: вид, роль, сервис узла; прямые рёбра и обратные (выведенные из прямых)."""

    def __init__(self, ont: O.Ontology, index: List[Dict[str, Any]], forward: Dict[str, Dict[str, List[str]]],
                 texts: Optional[Dict[str, str]] = None, cfg: Optional[Dict[str, Any]] = None):
        self.ont = ont
        self.cfg = cfg or settings()
        self.nodes = {n["name"]: n for n in index}
        self.fwd = forward
        self.texts = texts or {}
        self.rev: Dict[str, Dict[str, List[str]]] = {}
        for src, by_edge in forward.items():
            for edge, targets in by_edge.items():
                for t in targets:
                    self.rev.setdefault(t, {}).setdefault(edge, []).append(src)
        for t in self.rev.values():
            for e in t:
                t[e] = sorted(set(t[e]))

    def kind(self, name: str) -> str:
        return (self.nodes.get(name) or {}).get("kind", "")

    def role(self, name: str) -> Optional[str]:
        return (self.nodes.get(name) or {}).get("role")

    def service(self, name: str) -> Optional[str]:
        return (self.nodes.get(name) or {}).get("service")

    def out(self, name: str, edge: Optional[str]) -> List[str]:
        return [t for t in self.fwd.get(name, {}).get(edge, []) if t in self.nodes] if edge else []

    def inn(self, name: str, edge: Optional[str]) -> List[str]:
        return [s for s in self.rev.get(name, {}).get(edge, []) if s in self.nodes] if edge else []

    def entry(self, name: str) -> Optional[Dict[str, Any]]:
        """Описание входа из конфига (вид и, если задана, роль совпали) или None."""
        for e in self.cfg["entries"]:
            if e["kind"] == self.kind(name) and e.get("role") in (None, self.role(name)):
                return e
        return None

    def is_step(self, name: str) -> bool:
        return self.kind(name) in self.cfg["steps"]

    def position(self, name: str, step: str) -> Optional[int]:
        """Где в тексте узла стоит шаг: ссылка `[[…]]` (строка «↳» стоит там, где был вынесенный раздел)
        или имя; None — шаг в тексте не упомянут, и его место в порядке не известно."""
        text = self.texts.get(name, "")
        hits = [i for i in (text.find(f"[[{step}"), text.lower().find(step.lower())) if i >= 0]
        return min(hits) if hits else None

    def steps_of(self, name: str) -> List[str]:
        """Что узел запускает — по порядку в его тексте; не нашлось в тексте — в конце по имени."""
        def pos(t: str) -> Tuple[int, str]:
            i = self.position(name, t)
            return (10 ** 9 if i is None else i, t)
        return sorted(self.out(name, self.cfg["step_edge"]), key=pos)

    def unordered(self, name: str) -> List[str]:
        """Шаги узла без места в его тексте — при двух и более шагах их порядок в «Ходе обработки» угадан."""
        steps = self.out(name, self.cfg["step_edge"])
        return [t for t in steps if self.position(name, t) is None] if len(steps) > 1 else []

    def reaches_entry(self, name: str) -> bool:
        """Есть ли цепочка «вход → … → узел» по рёбрам шагов (вверх, через любые циклы)."""
        seen, todo = {name}, [name]
        while todo:
            for c in self.callers_of_step(todo.pop()):
                if self.entry(c) is not None and self.entry(c).get("input", True):
                    return True
                if c not in seen:
                    seen.add(c)
                    todo.append(c)
        return False

    def cycles(self) -> List[List[str]]:
        """Циклы по рёбрам шагов (A запускает B, B запускает A): каждый один раз, с наименьшего имени."""
        edge, found = self.cfg["step_edge"], set()

        def walk(cur: str, path: List[str]) -> None:
            for t in self.out(cur, edge):
                if t == path[0]:
                    found.add(tuple(path))
                elif t not in path and t > path[0] and len(path) < 2 * MAX_DEPTH:
                    walk(t, path + [t])
        for n in sorted(self.nodes):
            walk(n, [n])
        return [list(c) for c in sorted(found)]

    def callers_of_step(self, name: str) -> List[str]:
        return self.inn(name, self.cfg["step_edge"])


def note_text(body: str) -> str:
    """Текст спеки в заметке — содержимое её блоков source (то, по чему ищется порядок шагов)."""
    return "\n".join(b["content"] for b in F.blocks(body) if b["name"].startswith("source"))


def from_notes(ont: O.Ontology, nodes: Dict[str, Dict[str, Any]]) -> "View":
    """View по заметкам vault ({name, kind, role, service, data, body}) — как его видит валидатор."""
    index = [{"name": n, "kind": d["kind"], "role": d.get("role"), "service": d.get("service")} for n, d in nodes.items()]
    fwd = {n: {e: [t for t in (F.link_target(x) for x in (v if isinstance(v, list) else [v])) if t]
               for e, v in d["data"].items() if e in ont.edges}
           for n, d in nodes.items()}
    texts = {}
    for n, d in nodes.items():
        try:
            texts[n] = note_text(d.get("body") or "")
        except ValueError:
            texts[n] = ""
    return View(ont, index, fwd, texts)


def _link(v: View, name: str, here: Optional[str] = None) -> str:
    """Ссылка; узел чужого сервиса — с сервисом в скобках, если имя его ещё не несёт."""
    svc = v.service(name)
    tail = f" ({svc})" if svc and svc != here and not name.startswith(f"{svc} ") else ""
    return F.link(name) + tail


def _links(v: View, names: List[str], here: Optional[str] = None) -> str:
    return ", ".join(_link(v, n, here) for n in names)


def outputs(v: View, name: str, here: Optional[str] = None, edges: Optional[List[str]] = None) -> str:
    """Что узел делает сам: «формирует задачу [[X]]; публикует в топик [[t]]» (рёбра онтологии по-русски)."""
    parts = []
    for e in edges if edges is not None else v.cfg["outputs"]:
        t = v.out(name, e)
        if t and e in v.ont.edges:
            parts.append(f"{v.ont.edges[e]['title_ru']} {_links(v, t, here)}")
    return "; ".join(parts)


def _readers(v: View, topic: str, here: Optional[str]) -> str:
    r = v.inn(topic, v.cfg["consume_edge"])
    return f" → читает {_links(v, r, here)}" if r else " → читатель в спеке не назван"


def trigger(v: View, name: str) -> str:
    """Чем запускается вход: сообщение топика (и кто его публикует), расписание, вызов (и кто вызывает)."""
    e = v.entry(name)
    if e is None:
        return ""
    here = v.service(name)
    text = e["trigger"]
    if e.get("via"):
        srcs = v.out(name, e["via"])
        if not srcs:
            return f"{text} — в спеке не назван"
        rows = []
        for s in srcs:
            pubs = v.inn(s, e.get("source"))
            rows.append(F.link(s) + (f" ← {e.get('source_label', '')} {_links(v, pubs, here)}" if pubs
                                     else f" ({e.get('missing', 'источник в спеке не назван')})"))
        return f"{text} " + "; ".join(rows)
    if e.get("callers"):
        callers = v.inn(name, e["callers"])
        if callers:
            return f"{text} — {e.get('callers_label', '')} {_links(v, callers, here)}"
        return f"{text} — {e['missing']}" if e.get("missing") else text
    return text


def _steps(v: View, name: str, here: Optional[str], depth: int, seen: Set[str], lines: List[str], indent: str) -> None:
    for k, t in enumerate(v.steps_of(name), 1):
        loop = t in seen
        did = "" if loop else outputs(v, t, here, [e for e in v.cfg["outputs"] if e != v.cfg["publish_edge"]])
        lines.append(f"{indent}{k}. {_link(v, t, here)}" + (f" — {did}" if did else "") + (" (уже выше — цикл)" if loop else ""))
        if loop:
            continue
        if depth < MAX_DEPTH:
            _steps(v, t, here, depth + 1, seen | {t}, lines, indent + "   ")
        for topic in v.out(t, v.cfg["publish_edge"]):
            lines.append(f"{indent}   - {F.link(topic)}{_readers(v, topic, here)}")


def flow(v: View, name: str) -> Optional[str]:
    """Блок «Ход обработки» входа; у шага — «Где выполняется»; у бизнес-операции — «Жизненный цикл
    задачи». Показать нечего — None."""
    if v.entry(name) is not None:
        return _entry_flow(v, name)
    if v.is_step(name):
        return _step_flow(v, name)
    if v.kind(name) == v.cfg["lifecycle"]["kind"]:
        return _operation_flow(v, name)
    return None


def _entry_flow(v: View, name: str) -> Optional[str]:
    here = v.service(name)
    e = v.entry(name) or {}
    if not (v.steps_of(name) or outputs(v, name, here) or v.out(name, e.get("via"))):
        return None
    lines = ["**Ход обработки** (собран из связей графа; шаги — по порядку в тексте спеки)", "",
             f"1. {trigger(v, name)}"]
    own = outputs(v, name, here)
    lines.append(f"2. **{name}** — эта заметка" + (f": {own}" if own else ""))
    for topic in v.out(name, v.cfg["publish_edge"]):
        lines.append(f"   - {F.link(topic)}{_readers(v, topic, here)}")
    if v.steps_of(name):
        lines.append("3. Шаги:")
        _steps(v, name, here, 1, {name}, lines, "   ")
    return "\n".join(lines)


def _chains(v: View, name: str) -> List[List[str]]:
    """Цепочки вызовов от входов до узла: [вход, …, узел]."""
    out: List[List[str]] = []

    def up(cur: str, path: List[str]) -> None:
        callers = [c for c in v.callers_of_step(cur) if c not in path]
        if not callers or len(path) > MAX_DEPTH:
            out.append(list(reversed(path)))
            return
        for c in callers:
            up(c, path + [c])
    up(name, [name])
    return [c for c in out if len(c) > 1]


def _step_flow(v: View, name: str) -> str:
    here = v.service(name)
    lines = ["**Где выполняется** (собрано из связей графа)", ""]
    chains = _chains(v, name)
    for ch in chains:
        head = trigger(v, ch[0])
        sib = v.steps_of(ch[-2])
        k = sib.index(name) + 1 if name in sib else 0
        nxt = sib[k] if 0 < k < len(sib) else None
        path = " → ".join(_link(v, x, here) for x in ch[:-1]) + f" → **{name}**"
        lines.append(f"- {path}" + (f" — шаг {k} из {len(sib)}" if k and len(sib) > 1 else "")
                     + (f", дальше {_link(v, nxt, here)}" if nxt else "") + (f". {head}" if head else ""))
    if not chains:
        lines.append("- Кто запускает, в спеке не указано (вопрос — в `_graph/open-questions.md`)")
    inner = v.steps_of(name)
    if inner:
        lines += ["", "Внутри по порядку: " + " → ".join(_link(v, x, here) for x in inner)]
    own = outputs(v, name, here)
    if own:
        lines += ["", f"Сам: {own}"]
    return "\n".join(lines)


def _operation_flow(v: View, name: str) -> Optional[str]:
    lc = v.cfg["lifecycle"]
    made, closed, screens = v.inn(name, lc["make"]), v.inn(name, lc["close"]), v.inn(name, lc["ui"])
    if not (made or closed or screens):
        return None

    def who(names: List[str]) -> str:
        rows = []
        for x in names:
            t = trigger(v, x)
            rows.append(_link(v, x) + (f" — {t[0].lower() + t[1:]}" if t else ""))
        return "; ".join(rows)
    lines = ["**Жизненный цикл задачи** (собран из связей графа)", ""]
    lines.append("1. Формирование: " + (who(made) if made else "кто формирует, в спеке не указано"))
    if screens:
        rows = []
        for s in screens:
            calls = v.out(s, v.cfg["call_edge"])
            rows.append(_link(v, s) + (f" → вызывает {_links(v, calls)}" if calls else ""))
        lines.append("2. Работа пользователя: " + "; ".join(rows))
    else:
        lines.append("2. Работа пользователя: экран в спеке не назван")
    lines.append("3. Закрытие: " + (who(closed) if closed else "кто закрывает, в спеке не указано"))
    procs = v.out(name, lc.get("process"))
    if procs:
        lines += ["", f"Входит в процесс: {_links(v, procs)}"]
    return "\n".join(lines)


def purpose(text: str) -> str:
    """Первый абзац прозы текста сервиса (без заголовков, таблиц, списков, кода) — до PURPOSE_MAX знаков."""
    from . import split as SP
    for para in re.split(r"\n\s*\n", SP.strip_carved(text).replace("\r\n", "\n")):
        lines = [l for l in para.split("\n") if l.strip()]
        if not lines or _NOT_PROSE.match(lines[0]):
            continue
        p = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if len(p) < 30:
            continue
        if len(p) > PURPOSE_MAX:
            cut = p[:PURPOSE_MAX]
            end = max(cut.rfind(". "), cut.rfind("; "))
            p = (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0]) + " …"
        return p
    return ""


def service(v: View, name: str, purpose_text: str = "") -> str:
    """Обзор сервиса: точка входа в его документацию."""
    comps = v.inn(name, "belongs_to")
    lines = ["## Обзор сервиса", "", "_Собран из графа: что сервис делает, через что получает данные и с кем "
             "работает. Полный текст спеки — ниже._", ""]
    if purpose_text:
        lines += [f"**Назначение.** {purpose_text}", ""]
    if not comps:
        return "\n".join(lines + ["_Составных частей в графе нет._"])
    groups: Dict[int, List[str]] = {}
    for c in comps:
        e = v.entry(c)
        if e is not None:
            groups.setdefault(v.cfg["entries"].index(e), []).append(c)
    inputs = [(i, groups[i]) for i in sorted(groups) if v.cfg["entries"][i].get("input", True)]
    lines += ["**Входы**", ""]
    for i, members in inputs:
        e = v.cfg["entries"][i]
        lines.append(f"- {e['group']} — {len(members)}:")
        for c in members:
            src = v.out(c, e.get("via"))
            head = (_links(v, src, name) + " → ") if src else ""
            did = _does(v, c, name)
            callers = v.inn(c, e.get("callers")) if e.get("callers") else []
            lines.append(f"  - {head}{F.link(c)}" + (f" — {did}" if did else "")
                         + (f" · {e.get('callers_label', '')} {_links(v, callers, name)}" if callers else ""))
    if not inputs:
        lines.append("- входов в графе нет")
    pubs = [(src, t) for src in comps for t in v.out(src, v.cfg["publish_edge"])]
    calls = [(src, t) for src in comps for t in v.out(src, v.cfg["call_edge"]) if v.service(t) != name]
    if pubs or calls:
        lines += ["", "**Выходы**", ""]
        lines += [f"- {F.link(src)} → публикует в {F.link(t)}{_readers(v, t, name)}" for src, t in pubs]
        lines += [f"- {F.link(src)} → вызывает {_link(v, t, name)}" for src, t in calls]
    steps = [c for c in comps if v.is_step(c)]
    if steps:
        lines += ["", f"**{v.cfg.get('steps_title', 'Шаги')}** — {len(steps)}", ""]
        for a in steps:
            callers = v.callers_of_step(a)
            lines.append(f"- {F.link(a)} — " + (f"запускают {_links(v, callers, name)}" if callers
                                                 else "кто запускает, в спеке не указано"))
    for i in sorted(groups):
        e = v.cfg["entries"][i]
        if e.get("input", True):
            continue
        lines += ["", f"**{e['group']}** — {len(groups[i])}", ""]
        for c in groups[i]:
            did = outputs(v, c, name, [v.cfg["publish_edge"]])
            lines.append(f"- {F.link(c)}" + (f" — {did}" if did else ""))
    lc = v.cfg["lifecycle"]
    ops = sorted({t for c in comps for e in (lc["make"], lc["close"]) for t in v.out(c, e)})
    if ops:
        lines += ["", f"**{v.ont.kinds[lc['kind']]['title_ru']}**", ""]
        for o in ops:
            mk = [c for c in comps if o in v.out(c, lc["make"])]
            cl = [c for c in comps if o in v.out(c, lc["close"])]
            lines.append(f"- {F.link(o)} — " + "; ".join(x for x in (
                f"формирует {_links(v, mk, name)}" if mk else "", f"закрывает {_links(v, cl, name)}" if cl else "") if x))
    data = sorted({t for c in comps for t in v.out(c, v.cfg["data_edge"])})
    if data:
        lines += ["", f"**Данные**: {_links(v, data, name)}"]
    peers = _peers(v, name, comps)
    if peers:
        lines += ["", "**Связанные сервисы**", ""]
        lines += [f"- {F.link(p)} — {'; '.join(sorted(why))}" for p, why in sorted(peers.items())]
    return "\n".join(lines)


def _does(v: View, name: str, here: str) -> str:
    lc = v.cfg["lifecycle"]
    did = outputs(v, name, here, [lc["make"], lc["close"], v.cfg["publish_edge"]])
    inv = v.steps_of(name)
    if inv:
        did = (did + "; " if did else "") + "запускает " + " → ".join(F.link(x) for x in inv)
    return did


def _peers(v: View, name: str, comps: List[str]) -> Dict[str, Set[str]]:
    """Сервисы, с которыми этот работает: вызовы REST в обе стороны и обмен через топики."""
    out: Dict[str, Set[str]] = {}
    call, pub, con = v.cfg["call_edge"], v.cfg["publish_edge"], v.cfg["consume_edge"]

    def add(svc: Optional[str], why: str) -> None:
        if svc and svc != name and svc in v.nodes:
            out.setdefault(svc, set()).add(why)
    for c in comps:
        for t in v.out(c, call):
            add(v.service(t), "вызываем его REST")
        for caller in v.inn(c, call):
            add(v.service(caller), "вызывает наш REST")
        for t in v.out(c, pub):
            for r in v.inn(t, con):
                add(v.service(r), f"читает наш топик {t}")
        for t in v.out(c, con):
            for p in v.inn(t, pub):
                add(v.service(p), f"мы читаем его топик {t}")
    return out
