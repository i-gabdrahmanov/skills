"""tools.py — реестр инструментов движка spec-graph (`graph_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется. Нарушения
собираются в одно сообщение: слабой модели легче починить разом.

Сейчас: graph_status, graph_init, graph_next, graph_queue_status, graph_scan, graph_profile, graph_survey, graph_draft_put, graph_catalog, graph_catalog_decide, graph_assign_start, graph_assign_put, graph_split, graph_resolve, graph_edges_mechanical, graph_link_start, graph_link_put, graph_sync, graph_card_start, graph_card_put, graph_edges_review, graph_edges_decide, graph_delta_decide, graph_validate, graph_obsidian, graph_find, graph_node, graph_neighbors, graph_path, graph_impact, graph_service_links, graph_check. Остальные появляются по задачам docs/specgraph/; владельцы
продуктивных инструментов — `phase_tools` ветки specgraph в hooks/_branches.py.

Каждый вызов считает `metrics` (задача 4.1): исход, коды отказа, id единиц работы (аргументы,
названные в `x-ref-args` схемы), длительность, размер ответа. Аргументы и тексты не пишутся.

Инструмент результата единицы работы объявляет в схеме `x-queue: <имя очереди>` и аргумент
`item_id` (задача 0.6): `call` до работы проверяет, что единица этой очереди в аренде, после —
закрывает её (принято) или возвращает с попыткой + 1 (ToolError). Сбой движка единицу не трогает:
вина не модели, аренда истечёт сама.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import accept as AC
from . import assign as AS
from . import blocks as B
from . import card as CD
from . import catalog as CT
from . import config as C
from . import edges as ED
from . import link as LK
from . import metrics as M
from . import obsidian as OB
from . import ontology as O
from . import profile as PF
from . import queue as Q
from . import resolve as RS
from . import review as RV
from . import split as SP
from . import status as ST
from . import survey as SV
from . import sync as SY
from . import traverse as TR
from . import validate as VA


class ToolError(Exception):
    """Отказ инструмента (неверные аргументы, не прошла проверка) — ответ isError, сервер работает.

    `codes` — машинные коды отказа (`E-…`) для счётчиков и диагностики: по ним видно, где модель
    буксует, без текста сообщения."""

    def __init__(self, message: str, codes: Optional[List[str]] = None):
        super().__init__(message)
        self.codes = list(codes or [])


_TYPES = {"string": str, "object": dict, "boolean": bool, "array": list}


def _check_args(args: Dict[str, Any], schema: Dict[str, Any]) -> None:
    errors: List[str] = []
    codes: List[str] = []
    props = schema.get("properties", {})
    for k in args:
        if k not in props:
            errors.append(f"неизвестный аргумент «{k}» (допустимы: {', '.join(sorted(props)) or 'нет'})")
            codes.append("E-arg-unknown")
    for k in schema.get("required", []):
        if k not in args:
            errors.append(f"нет обязательного аргумента «{k}»")
            codes.append("E-arg-missing")
    for k, v in args.items():
        spec = props.get(k) or {}
        t = _TYPES.get(spec.get("type"))
        if t is not None and (not isinstance(v, t) or (t is not bool and isinstance(v, bool))):
            errors.append(f"«{k}»: ожидается {spec['type']}")
            codes.append("E-arg-type")
        elif spec.get("type") == "array" and (spec.get("items") or {}).get("type") == "string" \
                and not all(isinstance(x, str) for x in v):
            errors.append(f"«{k}»: ожидается список строк")
            codes.append("E-arg-type")
        elif spec.get("type") == "array" and (spec.get("items") or {}).get("type") == "object" \
                and not all(isinstance(x, dict) for x in v):
            errors.append(f"«{k}»: ожидается список объектов")
            codes.append("E-arg-type")
    if errors:
        raise ToolError("; ".join(errors), codes)


def _status(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return ST.status(root)


def _init(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    res = C.init(root, args)
    if res.get("status") == "invalid":
        raise ToolError("конфиг не записан: " + "; ".join(res["errors"]), ["E-config-invalid"])
    return res


def _queue_arg(args: Dict[str, Any]) -> str:
    name = args.get("queue")
    if name not in Q.QUEUES:
        raise ToolError(f"queue: одна из {', '.join(Q.QUEUES)}", ["E-queue-name"])
    return name


def _cfg(root: Path) -> Optional[Dict[str, Any]]:
    cfg, _ = C.load(root)
    return cfg


def _next(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    res = Q.lease(root, _queue_arg(args), _cfg(root))
    if res.get("status") == Q.EMPTY:
        res["next"] = "очередь пуста — переходи к гейту шага"
    elif res.get("status") == "единица":
        res["next"] = (f"запусти субагента: бриф {res['brief']}, вход {res['input']}; результат он сдаёт "
                       f"инструментом {res['tool']} с item_id «{res['item_id']}»")
    return res


def _queue_status(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return Q.status(root, _queue_arg(args))


def _need_cfg(root: Path) -> Dict[str, Any]:
    cfg, errors = C.load(root)
    if cfg is None:
        raise ToolError("проект не инициализирован или конфиг с ошибками — сначала graph_status / graph_init"
                        + (": " + "; ".join(errors) if errors else ""), ["E-config-missing"])
    return cfg


def _scan(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return B.scan(root, _need_cfg(root), args.get("scope"))


def _profile(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _need_cfg(root)
    if args.get("reveal"):
        return PF.reveal(root, cfg, args["reveal"])
    return PF.profile(root, cfg, args.get("scope"))


def _survey(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return SV.survey(root, _need_cfg(root), args.get("scope"))


def _draft_put(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    nodes, not_nodes = args.get("nodes") or [], args.get("not_nodes") or []
    draft, errs = SV.put_draft(root, args["item_id"], nodes, not_nodes, _need_cfg(root))
    if errs:
        raise ToolError("черновик не принят:\n" + "\n".join(f"- [{c}] {m}" for c, m in errs),
                        sorted({c for c, _ in errs}))
    return {"accepted": True, "nodes": len(nodes), "not_nodes": len(not_nodes), "file": draft["file"]}


def _catalog(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _need_cfg(root)
    cat = CT.build(root, cfg)
    st = CT.approval(root)
    res = {"nodes": len(cat["nodes"]), "review": len(cat["review"]), "conflicts": len(cat["conflicts"]),
           "collisions": len(cat["collisions"]), "not_nodes": len(cat["not_nodes"]), "scope": cat["scope"],
           "report": f"{cfg['vault']}/_graph/catalog.md", "consent": st["state"], "key": st["key"]}
    if st["state"] == "ok":
        res["next"] = "каталог утверждён (снимок catalog.approved.json) — дальше цикл очереди assign-section"
    else:
        res["next"] = ("покажи пользователю отчёт и спроси: утвердить каталог и scope или что поменять. Правки — "
                       "graph_catalog_decide, затем снова graph_catalog. Согласие: " + st["how"])
    return res


def _catalog_decide(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _need_cfg(root)
    res = CT.decide(root, args["actions"], cfg["vault"])
    if res.get("errors"):
        raise ToolError("решения не приняты: " + "; ".join(res["errors"]), ["E-catalog-action"])
    return dict(res, next="graph_catalog — пересобрать каталог с решениями")


def _assign_start(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _need_cfg(root)
    try:
        return AS.start(root, cfg)
    except AS.AssignError as e:
        raise ToolError(str(e), [e.code])


def _assign_put(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    ok, why = CT.approved(root)
    if not ok:
        raise ToolError(why, ["E-catalog-unapproved"])
    plan, errs = AS.put(root, args["item_id"], args)
    if errs:
        raise ToolError("раскладка не принята:\n" + "\n".join(f"- [{c}] {m}" for c, m in errs),
                        sorted({c for c, _ in errs}))
    return {"accepted": True, "file": plan["file"], "assigned": sum(len(a["blocks"]) for a in plan["assign"]),
            "new_nodes": len(args.get("new_nodes") or [])}


def _split(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _need_cfg(root)
    try:
        res = SP.split(root, cfg)
    except SP.SplitError as e:
        raise ToolError(str(e), [e.code])
    errs = SP.verify(root, cfg)
    res["verify"] = {"errors": len(errs), "first": [f"[{c}] {m}" for c, m in errs[:10]]}
    res["next"] = ("нарезка цела — дальше гейт graph_gate.py map" if not errs else
                   "есть нарушения полноты/дословности — смотри verify.first; блоки без назначения — переразложить")
    return res


def _resolve(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _need_cfg(root)
    idx = RS.vault_index(root, cfg)
    return {"results": [dict(RS.resolve(idx, m, cfg, args.get("service")), mention=m) for m in args["mentions"][:30]]}


def _mechanical(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return dict(ED.run_mechanical(root, _need_cfg(root)),
                next="механические рёбра предложены (status proposed); дальше — предложения модели по заметкам")


def _link_start(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return LK.start(root, _need_cfg(root))
    except LK.LinkError as e:
        raise ToolError(str(e), [e.code])


def _link_put(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    res, errs = LK.put(root, _need_cfg(root), args["item_id"], args.get("proposals") or [])
    if errs:
        raise ToolError("рёбра не приняты:\n" + "\n".join(f"- [{c}] {m}" for c, m in errs), sorted({c for c, _ in errs}))
    return res


def _sync(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return SY.sync(root, _need_cfg(root))


def _card_start(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return CD.start(root, _need_cfg(root))
    except CD.CardError as e:
        raise ToolError(str(e), [e.code])


def _card_put(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    res, errs = CD.put(root, _need_cfg(root), args["item_id"], args.get("fields"))
    if errs:
        raise ToolError("карточка не принята:\n" + "\n".join(f"- [{c}] {m}" for c, m in errs), sorted({c for c, _ in errs}))
    return res


def _review(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return RV.review(root, _need_cfg(root))


def _edges_decide(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return RV.edges_decide(root, _need_cfg(root), args.get("accept") or [], args.get("reject") or [], args.get("why") or "")
    except RV.ReviewError as e:
        raise ToolError(str(e), [e.code])


def _delta_decide(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return RV.delta_decide(root, _need_cfg(root), args.get("decisions") or [])
    except RV.ReviewError as e:
        raise ToolError(str(e), [e.code])


def _validate(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return VA.validate(root, _need_cfg(root), args.get("scope"))


def _obsidian(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return OB.build(root, _need_cfg(root))
    except O.OntologyError as e:
        raise ToolError(str(e), ["E-obsidian"])


def _traverse(fn):
    def run(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
        cfg = _need_cfg(root)
        try:
            return fn(TR.build_index(root, cfg), root, cfg, args)
        except TR.TraverseError as e:
            raise ToolError(str(e), [e.code])
    return run


_READ = {"type": "object", "additionalProperties": False}
_NAME = {"type": "string", "description": "имя узла, алиас или ключ (task:…, http:…)"}
_LIMIT = {"type": "integer", "description": f"сколько строк (потолок {TR.LIMIT_MAX})"}


def _check(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return AC.run(root, _need_cfg(root))
    except O.OntologyError as e:
        raise ToolError(str(e), ["E-check"])


_QUEUE_PROP = {"type": "string", "description": "имя очереди: " + ", ".join(Q.QUEUES)}

TOOLS: Dict[str, Dict[str, Any]] = {
    "graph_status": {
        "fn": _status,
        "description": "Состояние spec-graph в проекте: инициализирован ли, а если нет — вопросы первого "
                       "запуска с кандидатами. Первый ход команды /graph.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_init": {
        "fn": _init,
        "description": "Записать .spec-graph/config.json по ОТВЕТАМ пользователя (не по догадке). Существующий "
                       "конфиг не перезаписывается.",
        "schema": {"type": "object",
                   "properties": {
                       "sources": {"type": "array", "items": {"type": "string"},
                                   "description": "корни исходников спеки (каталоги или файлы), от корня репо"},
                       "exclude": {"type": "array", "items": {"type": "string"},
                                   "description": "glob-шаблоны исключений (черновики, архивы)"},
                       "vault": {"type": "string", "description": f"куда писать граф, по умолчанию {C.DEFAULT_VAULT}"},
                       "services": {"type": "object",
                                    "description": "каталог или файл → имя сервиса (если не совпадает с именем каталога)"},
                   },
                   "required": ["sources"], "additionalProperties": False},
    },
    "graph_next": {
        "fn": _next,
        "description": "Следующая единица работы очереди (в аренду): бриф, путь к готовому входу, инструмент "
                       "результата, прогресс. «пусто» — очередь закрыта, дальше гейт. Вызывает оркестратор; "
                       "единицу исполняет субагент.",
        "schema": {"type": "object", "properties": {"queue": _QUEUE_PROP},
                   "required": ["queue"], "additionalProperties": False},
    },
    "graph_scan": {
        "fn": _scan,
        "description": "Разобрать исходники спеки на неделимые блоки (.spec-graph/blocks/); ответ — сводка по "
                       "файлам: число блоков, размер, оглавление, предупреждения (незакрытый фенс).",
        "schema": {"type": "object",
                   "properties": {"scope": {"type": "string", "description": "all | каталог | файл; пусто — all"}},
                   "additionalProperties": False},
    },
    "graph_profile": {
        "fn": _profile,
        "description": "Профиль ФОРМЫ исходников без содержимого: заголовки (глубина, нумерация, слова — общие как есть, "
                       "прочие хешем w#…), таблицы, фенсы, ссылки, попадания шаблонов. reveal=<хеш> — только локально: "
                       "какое слово стоит за хешем.",
        "schema": {"type": "object",
                   "properties": {"scope": {"type": "string", "description": "all | каталог | файл; пусто — all"},
                                  "reveal": {"type": "string", "description": "w#xxxxxx или f#xxxxxx — раскрыть хеш"}},
                   "additionalProperties": False},
    },
    "graph_survey": {
        "fn": _survey,
        "description": "Разведка спеки: скан, экстракторы, _graph/survey.md для человека (оглавления, кандидаты, "
                       "аномалии, объём работ, предложение scope) и очередь draft-file. Первый ход шага graph-map.",
        "schema": {"type": "object",
                   "properties": {"scope": {"type": "string", "description": "all | каталог | файл; пусто — all"}},
                   "additionalProperties": False},
    },
    "graph_draft_put": {
        "fn": _draft_put,
        "description": "Сдать черновик узлов ОДНОГО файла (единица очереди draft-file): nodes — узлы с видом, именем, "
                       "сервисом, key, разделами; not_nodes — кандидаты, которые не узлы, с причиной.",
        "schema": {"type": "object",
                   "properties": {"item_id": {"type": "string"},
                                  "nodes": {"type": "array", "items": {"type": "object"}},
                                  "not_nodes": {"type": "array", "items": {"type": "object"}}},
                   "required": ["item_id", "nodes"], "x-queue": "draft-file", "additionalProperties": False},
    },
    "graph_catalog": {
        "fn": _catalog,
        "description": "Собрать каталог узлов из черновиков разведки и решений человека: _graph/catalog.md для "
                       "человека, состояние согласия graph-catalog; при действующем согласии — снимок утверждённого каталога.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_catalog_decide": {
        "fn": _catalog_decide,
        "description": "Записать решения ЧЕЛОВЕКА по каталогу (после его ответа): merge {into, names}, split {node, file, "
                       "name}, rename {node, name}, move {node, service}, drop {node}, scope {include}; у каждого — why.",
        "schema": {"type": "object", "properties": {"actions": {"type": "array", "items": {"type": "object"}}},
                   "required": ["actions"], "additionalProperties": False},
    },
    "graph_assign_start": {
        "fn": _assign_start,
        "description": "Построить очередь assign-section из УТВЕРЖДЁННОГО каталога (без согласия graph-catalog — отказ): "
                       "единица — непрерывный кусок файла в бюджете, каждый блок ровно в одной единице.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_assign_put": {
        "fn": _assign_put,
        "description": "Сдать раскладку блоков единицы assign-section: assign [{node, blocks}], skip [{blocks, reason: "
                       "toc|boilerplate|duplicate-of, of?}], unplaced [{blocks, suggested_kind?}], new_nodes [{kind, name, "
                       "service?, key?, why}].",
        "schema": {"type": "object",
                   "properties": {"item_id": {"type": "string"},
                                  "assign": {"type": "array", "items": {"type": "object"}},
                                  "skip": {"type": "array", "items": {"type": "object"}},
                                  "unplaced": {"type": "array", "items": {"type": "object"}},
                                  "new_nodes": {"type": "array", "items": {"type": "object"}}},
                   "required": ["item_id", "assign"], "x-queue": "assign-section", "additionalProperties": False},
    },
    "graph_split": {
        "fn": _split,
        "description": "Нарезать vault по утверждённому каталогу и планам раскладки: заметки с текстом дословно, "
                       "frontmatter по онтологии, _graph/index.md и unplaced.md; ответ — счётчики и проверка полноты.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_resolve": {
        "fn": _resolve,
        "description": "Упоминания (`POST /path`, имя топика, код задачи, имя или алиас узла) → узел vault, либо "
                       "ambiguous с кандидатами, либо ghost (с подсказкой path_typo). Молча не выбирает.",
        "schema": {"type": "object",
                   "properties": {"mentions": {"type": "array", "items": {"type": "string"}},
                                  "service": {"type": "string", "description": "сервис-контекст для неоднозначных путей"}},
                   "required": ["mentions"], "additionalProperties": False},
    },
    "graph_edges_mechanical": {
        "fn": _mechanical,
        "description": "Механические рёбра по форме текста (обработчик → топик, «публикуется в топик», METHOD /path "
                       "чужого сервиса, имя узла данных) с цитатой-основанием; проходят ту же проверку, что и рёбра модели.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_link_start": {
        "fn": _link_start,
        "description": "Построить очередь link-note: единица на заметку, у которой онтология разрешает исходящие "
                       "рёбра; во входе — текст заметки, допустимые рёбра, подсказки резолвера, операции, механика.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_link_put": {
        "fn": _link_put,
        "description": "Сдать рёбра ОДНОЙ заметки (единица link-note): proposals [{edge, to, quote, confidence, why?}]. "
                       "Цитата — дословно из текста; неоднозначная цель уходит вопросом человеку.",
        "schema": {"type": "object",
                   "properties": {"item_id": {"type": "string"},
                                  "proposals": {"type": "array", "items": {"type": "object"}}},
                   "required": ["item_id", "proposals"], "x-queue": "link-note", "additionalProperties": False},
    },
    "graph_sync": {
        "fn": _sync,
        "description": "Записать рёбра в заметки: прямые ключи и таблица оснований (graph:edges) у владельца, обратные "
                       "ключи у всех — пересчётом; ссылки, добавленные/убранные человеком в Obsidian, учитываются.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_card_start": {
        "fn": _card_start,
        "description": "Построить очередь card: единица на бизнес-операцию, вход — её блоки текста с id.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_card_put": {
        "fn": _card_put,
        "description": "Сдать карточку бизнес-операции: fields {purpose, when, what, result} — до 3 утверждений {text, "
                       "blocks} или «нет в источнике»; ссылки только на блоки этой операции.",
        "schema": {"type": "object",
                   "properties": {"item_id": {"type": "string"}, "fields": {"type": "object"}},
                   "required": ["item_id", "fields"], "x-queue": "card", "additionalProperties": False},
    },
    "graph_edges_review": {
        "fn": _review,
        "description": "Собрать отчёт связей _graph/edges-review.md (операции, межсервисное, сомнительное, отклонённое, "
                       "вопросы, узлы дельты) и состояние согласия на него; после «да» — принять висящие предложения.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_edges_decide": {
        "fn": _edges_decide,
        "description": "Решение пользователя по связям из отчёта: accept/reject — id `r-…` или {from, edge, to}; "
                       "отклонение — с why, больше не предлагается.",
        "schema": {"type": "object",
                   "properties": {"accept": {"type": "array"}, "reject": {"type": "array"}, "why": {"type": "string"}},
                   "additionalProperties": False},
    },
    "graph_delta_decide": {
        "fn": _delta_decide,
        "description": "Решение пользователя по узлам, добавленным раскладкой: decisions [{node, action: accept|merge|"
                       "rename|drop, into?, name?, why}] — затем повторная нарезка.",
        "schema": {"type": "object", "properties": {"decisions": {"type": "array"}},
                   "required": ["decisions"], "additionalProperties": False},
    },
    "graph_validate": {
        "fn": _validate,
        "description": "Проверить граф: счётчики по кодам E-*/W-*, первые 10 нарушений с путями; полностью — "
                       "_graph/report.md и .spec-graph/validate.json. scope — префикс пути в vault.",
        "schema": {"type": "object", "properties": {"scope": {"type": "string"}}, "additionalProperties": False},
    },
    "graph_obsidian": {
        "fn": _obsidian,
        "description": "Obsidian-слой: цвета видов в .obsidian/graph.json (слиянием), _graph/Начало.md, виды-таблицы "
                       "_graph/views (Bases, если включён, иначе Markdown). Повтор без изменений графа — 0 записей.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_find": {
        "fn": _traverse(lambda idx, root, cfg, a: TR.find(idx, a["q"], a.get("kind"), a.get("limit"))),
        "description": "Найти узел по части имени, алиасу или ключу; до 20 строк «[[имя]] (вид, сервис)».",
        "schema": dict(_READ, properties={"q": {"type": "string"}, "kind": {"type": "string"},
                                          "limit": {"type": "integer", "description": "≤ 20"}}, required=["q"]),
    },
    "graph_node": {
        "fn": _traverse(lambda idx, root, cfg, a: TR.node(idx, root, cfg, a["name"], a.get("body") or "none",
                                                          a.get("max_chars"))),
        "description": "Узел: рёбра по ключам (прямые и обратные) и выбранная часть тела: card — карточка операции, "
                       "source — текст спеки, evidence — основания рёбер.",
        "schema": dict(_READ, properties={"name": _NAME, "body": {"type": "string", "enum": ["none", "card", "source", "evidence"]},
                                          "max_chars": {"type": "integer", "description": f"≤ {TR.CHARS_MAX}"}},
                       required=["name"]),
    },
    "graph_neighbors": {
        "fn": _traverse(lambda idx, root, cfg, a: TR.neighbors(idx, a["name"], a.get("edges"), a.get("dir") or "both",
                                                               a.get("depth"), a.get("limit"))),
        "description": "Соседи узла: рёбра «[[a]] —ребро→ [[b]]»; edges — имена рёбер или обратных ключей; dir out|in|both; "
                       f"depth ≤ {TR.DEPTH_MAX}.",
        "schema": dict(_READ, properties={"name": _NAME, "edges": {"type": "array", "items": {"type": "string"}},
                                          "dir": {"type": "string", "enum": ["out", "in", "both"]},
                                          "depth": {"type": "integer"}, "limit": _LIMIT}, required=["name"]),
    },
    "graph_path": {
        "fn": _traverse(lambda idx, root, cfg, a: TR.path(idx, a["from"], a["to"], a.get("max_depth"))),
        "description": "Как связаны два узла: до 3 кратчайших путей, шаг подписан ключом свойства (closed_by — против "
                       f"ребра closes_task); max_depth ≤ {TR.PATH_DEPTH_MAX}.",
        "schema": dict(_READ, properties={"from": _NAME, "to": _NAME, "max_depth": {"type": "integer"}},
                       required=["from", "to"]),
    },
    "graph_impact": {
        "fn": _traverse(lambda idx, root, cfg, a: TR.impact(idx, a["name"], a.get("limit"))),
        "description": "Что затронет изменение узла (эндпойнта, топика, данных, шедулера): вверх до бизнес-операций, "
                       "экранов и процессов — по онтологии; у каждой строки — через что.",
        "schema": dict(_READ, properties={"name": _NAME, "limit": _LIMIT}, required=["name"]),
    },
    "graph_service_links": {
        "fn": _traverse(lambda idx, root, cfg, a: TR.service_links(idx, a["svc"], a.get("limit"))),
        "description": "Связи сервиса с другими сервисами: вызовы и топики с узлами-посредниками (вид, а не рёбра графа).",
        "schema": dict(_READ, properties={"svc": _NAME, "limit": _LIMIT}, required=["svc"]),
    },
    "graph_check": {
        "fn": _check,
        "description": "Шаг graph-check: проверка графа (ошибки, дыры, перенос исходников), Obsidian-слой, смоук обхода; "
                       "сводка для пользователя и состояние согласия на приёмку.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_queue_status": {
        "fn": _queue_status,
        "description": "Сводка очереди: сколько единиц в каждом статусе, брошенные единицы и топ кодов отказа.",
        "schema": {"type": "object", "properties": {"queue": _QUEUE_PROP},
                   "required": ["queue"], "additionalProperties": False},
    },
}


def _refs(args: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """id единиц работы из аргументов, которые схема объявила ссылками (`x-ref-args`); у инструмента
    результата очереди (`x-queue`) ссылка — всегда его `item_id`."""
    names = list(schema.get("x-ref-args", ()))
    if schema.get("x-queue") and "item_id" not in names:
        names.insert(0, "item_id")
    return [args[k] for k in names if isinstance(args.get(k), str)]


def call(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    args = args or {}
    root = Path(root)
    started = time.monotonic()
    refs = _refs(args, tool["schema"]) if isinstance(args, dict) else []
    elapsed = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    queue = tool["schema"].get("x-queue")
    leased = False
    try:
        _check_args(args, tool["schema"])
        if queue:
            try:
                Q.check_leased(root, queue, args.get("item_id"))
            except Q.QueueError as qe:
                raise ToolError(str(qe), [qe.code])
            leased = True
        fn: Callable[[Path, Dict[str, Any]], Dict[str, Any]] = tool["fn"]
        res = fn(root, args)
    except ToolError as e:
        if leased:
            st = Q.settle(root, queue, args["item_id"], False, e.codes, _cfg(root))
            if st["status"] == "abandoned":
                M.bump(root, f"queue.{queue}.abandoned")
        M.record_call(root, name, "rejected", codes=e.codes, refs=refs, duration_ms=elapsed())
        raise
    except Q.QueueError as qe:
        M.record_call(root, name, "rejected", codes=[qe.code], refs=refs, duration_ms=elapsed())
        raise ToolError(str(qe), [qe.code])
    except Exception as e:  # noqa: BLE001 — сбой движка считается и пробрасывается дальше серверу
        M.record_call(root, name, "error", refs=refs, duration_ms=elapsed(), error_type=type(e).__name__)
        raise
    if leased:
        res = dict(res)
        res["queue_item"] = Q.settle(root, queue, args["item_id"], True, None, _cfg(root))
    size = len(json.dumps(res, ensure_ascii=False, default=str))
    M.record_call(root, name, "ok", refs=refs, duration_ms=elapsed(), size=size)
    return res
