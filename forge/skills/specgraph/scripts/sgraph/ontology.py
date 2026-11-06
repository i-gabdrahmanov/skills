"""ontology.py — онтология графа как данные (задача 0.2): загрузка, самопроверка, вопросы к ней.

Виды узлов и рёбер, допустимые концы, обратные ключи, раскладка vault и грамматика имён живут в
`references/ontology.json` (плюс только-добавления проекта `.spec-graph/ontology.local.json`, 4.9).
Весь код движка спрашивает онтологию через этот модуль и не знает имён видов сам: новый вид (процесс
ARIS, шаг клиентского пути) — запись в файле, а не правка валидатора (ТЗ §8.6). Прототип оброс
27 инвариантами именно потому, что виды были зашиты в код.

Концы рёбер — вид или «вид:роль» (`kafka-op:publish`): ребро `consumes` может идти только от
обработчика, `publishes_to` — от публикации, но не от обработчика.

Литералы видов в этом модуле — только в описаниях (докстринги); в остальном коде движка их нет
вовсе (пинит test_ontology.py).
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE = Path(__file__).resolve().parents[2] / "references" / "ontology.json"
LOCAL_REL = ".spec-graph/ontology.local.json"
_KIND_FIELDS = {"title_ru", "path", "owner", "name", "key", "required", "roles", "subkinds"}
_EDGE_FIELDS = {"title_ru", "from", "to", "inverse", "symmetric", "evidence", "impact", "same_service"}
_GRAMMARS = ("phrase", "endpoint", "topic", "as_is")
_OWNERS = ("none", "service", "self")
_IMPACT = ("forward", "inverse", "none")
_EVIDENCE = ("required", "optional")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_NAME_MAX = 150


class OntologyError(Exception):
    pass


def _ends(spec: List[str]) -> List[Tuple[str, Optional[str]]]:
    out = []
    for e in spec:
        kind, _, role = e.partition(":")
        out.append((kind, role or None))
    return out


def self_check(data: Dict[str, Any]) -> List[str]:
    """Ошибки самой онтологии (пусто — согласована)."""
    errors: List[str] = []
    kinds = data.get("kinds")
    edges = data.get("edges")
    if not isinstance(kinds, dict) or not kinds:
        return ["kinds: непустой объект"]
    if not isinstance(edges, dict):
        return ["edges: объект"]
    scalars = set(data.get("scalar_keys") or [])
    for name, k in kinds.items():
        where = f"kind «{name}»"
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            errors.append(f"{where}: имя — латиница в нижнем регистре и дефис")
        if not isinstance(k, dict):
            errors.append(f"{where}: объект")
            continue
        for f in sorted(set(k) - _KIND_FIELDS):
            errors.append(f"{where}: неизвестное поле «{f}»")
        path = k.get("path", "")
        if not isinstance(path, str) or "{name}" not in path or not path.endswith(".md") or path.startswith("/"):
            errors.append(f"{where}: path — относительный шаблон с {{name}} и .md")
        if k.get("owner") not in _OWNERS:
            errors.append(f"{where}: owner — {', '.join(_OWNERS)}")
        if (k.get("owner") == "service") != ("{service}" in str(path)):
            errors.append(f"{where}: {{service}} в path ровно тогда, когда owner = service")
        if ("{subkind}" in str(path)) != bool(k.get("subkinds")):
            errors.append(f"{where}: {{subkind}} в path ровно тогда, когда заданы subkinds")
        if k.get("name") not in _GRAMMARS:
            errors.append(f"{where}: name — одна из грамматик {', '.join(_GRAMMARS)}")
        for f in k.get("required", []):
            if f not in scalars:
                errors.append(f"{where}: обязательный ключ «{f}» не объявлен в scalar_keys")
    forward = set(edges)
    inverse = set()
    for name, e in edges.items():
        where = f"edge «{name}»"
        if not isinstance(e, dict):
            errors.append(f"{where}: объект")
            continue
        for f in sorted(set(e) - _EDGE_FIELDS):
            errors.append(f"{where}: неизвестное поле «{f}»")
        for side in ("from", "to"):
            ends = e.get(side)
            if not isinstance(ends, list) or not ends:
                errors.append(f"{where}: {side} — непустой список")
                continue
            for kind, role in _ends(ends):
                if kind not in kinds:
                    errors.append(f"{where}.{side}: нет вида «{kind}»")
                elif role and role not in (kinds[kind].get("roles") or []):
                    errors.append(f"{where}.{side}: у вида «{kind}» нет роли «{role}»")
        inv = e.get("inverse")
        if not isinstance(inv, str) or not inv:
            errors.append(f"{where}: inverse — имя обратного ключа")
            continue
        if e.get("symmetric"):
            if inv != name:
                errors.append(f"{where}: у симметричного ребра inverse совпадает с ним самим")
            if sorted(e.get("from", [])) != sorted(e.get("to", [])):
                errors.append(f"{where}: у симметричного ребра одинаковые концы")
        else:
            if inv in forward:
                errors.append(f"{where}: обратный ключ «{inv}» совпадает с прямым ребром")
            if inv in inverse:
                errors.append(f"{where}: обратный ключ «{inv}» уже занят другим ребром")
            inverse.add(inv)
        if inv in scalars or name in scalars:
            errors.append(f"{where}: ключ ребра совпадает со скалярным ключом")
        if e.get("impact") not in _IMPACT:
            errors.append(f"{where}: impact — {', '.join(_IMPACT)}")
        if e.get("evidence") not in _EVIDENCE:
            errors.append(f"{where}: evidence — {', '.join(_EVIDENCE)}")
        if e.get("same_service"):
            for kind, _ in _ends(e.get("from", []) + e.get("to", [])):
                if kind in kinds and kinds[kind].get("owner") != "service":
                    errors.append(f"{where}: same_service, но у «{kind}» нет сервиса-владельца")
    return errors


def merge_local(base: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    """Только добавления: новые виды и рёбра; базовое не удаляется и не переопределяется."""
    out = copy.deepcopy(base)
    for section in ("kinds", "edges"):
        for name, spec in (local.get(section) or {}).items():
            if name in out[section]:
                raise OntologyError(f"локальная онтология: {section[:-1]} «{name}» уже есть в базовой — "
                                    f"переопределять базу нельзя")
            out[section][name] = spec
    for k in sorted(set(local) - {"kinds", "edges", "_comment"}):
        raise OntologyError(f"локальная онтология: только kinds и edges (лишнее «{k}»)")
    return out


class Ontology:
    def __init__(self, data: Dict[str, Any]):
        errors = self_check(data)
        if errors:
            raise OntologyError("; ".join(errors))
        self.data = data
        self.version = data.get("version")
        self.kinds: Dict[str, Dict[str, Any]] = data["kinds"]
        self.edges: Dict[str, Dict[str, Any]] = data["edges"]
        self.statuses: List[str] = list(data.get("statuses") or [])
        self.forbidden = data.get("name_forbidden", "")
        self.scalars: List[str] = list(data.get("scalar_keys") or [])
        self._by_inverse = {e["inverse"]: n for n, e in self.edges.items()}

    # — рёбра —
    def inverse_of(self, edge: str) -> str:
        return self.edges[edge]["inverse"]

    def edge_of_inverse(self, key: str) -> Optional[str]:
        return self._by_inverse.get(key)

    def is_symmetric(self, edge: str) -> bool:
        return bool(self.edges[edge].get("symmetric"))

    def edge_keys(self) -> List[str]:
        """Все ключи-рёбра frontmatter: прямые и обратные (симметричное — один раз)."""
        out = list(self.edges)
        for n, e in self.edges.items():
            if e["inverse"] not in out:
                out.append(e["inverse"])
        return out

    @staticmethod
    def _match(ends: Iterable[Tuple[str, Optional[str]]], node: Dict[str, Any]) -> bool:
        for kind, role in ends:
            if node.get("kind") == kind and (role is None or node.get("role") == role):
                return True
        return False

    def edge_allowed(self, edge: str, src: Dict[str, Any], dst: Dict[str, Any]) -> Tuple[bool, str]:
        """Допустимо ли ребро между узлами {kind, role?, service?}; причина отказа — коротко."""
        e = self.edges.get(edge)
        if e is None:
            return False, f"нет ребра «{edge}»"
        if not self._match(_ends(e["from"]), src):
            return False, f"«{edge}» не идёт от {self._label(src)} (допустимо от: {', '.join(e['from'])})"
        if not self._match(_ends(e["to"]), dst):
            return False, f"«{edge}» не идёт к {self._label(dst)} (допустимо к: {', '.join(e['to'])})"
        if e.get("same_service") and src.get("service") != dst.get("service"):
            return False, f"«{edge}» — только внутри одного сервиса"
        return True, ""

    def allowed_edges(self, src: Dict[str, Any], dst: Dict[str, Any]) -> List[str]:
        return [n for n in self.edges if self.edge_allowed(n, src, dst)[0]]

    def edges_from(self, src: Dict[str, Any]) -> List[str]:
        return [n for n, e in self.edges.items() if self._match(_ends(e["from"]), src)]

    @staticmethod
    def _label(node: Dict[str, Any]) -> str:
        return node.get("kind", "?") + (f":{node['role']}" if node.get("role") else "")

    # — виды —
    def owned_by_service(self, kind: str) -> bool:
        """У вида есть сервис-владелец (путь заметки — под каталогом сервиса)."""
        return self.kinds[kind].get("owner") == "service"

    def fm_keys(self, kind: str, role: Optional[str] = None) -> List[str]:
        """Порядок ключей frontmatter заметки вида: скаляры, прямые рёбра, обратные рёбра.

        С ролью — только рёбра этой роли (обработчику не нужен `publishes_to`); без роли — все роли
        вида (для заметки, роль которой ещё не решена)."""
        k = self.kinds[kind]
        node = {"kind": kind}
        if role is not None and role not in (k.get("roles") or []):
            raise OntologyError(f"у вида «{kind}» нет роли «{role}»")
        roles = [role] if role is not None else (k.get("roles") or [None])
        scal = ["kind"]
        if k.get("subkinds"):
            scal.append("subkind")
        if k.get("roles"):
            scal.append("role")
        for f in k.get("required", []):
            if f not in scal:
                scal.append(f)
        scal += [s for s in ("aliases",) if s not in scal]
        fwd: List[str] = []
        inv: List[str] = []
        for n, e in self.edges.items():
            if any(self._match(_ends(e["from"]), dict(node, role=r)) for r in roles) and n not in fwd:
                fwd.append(n)
        for n, e in self.edges.items():
            if any(self._match(_ends(e["to"]), dict(node, role=r)) for r in roles):
                if e["inverse"] not in fwd and e["inverse"] not in inv:
                    inv.append(e["inverse"])
        return scal + fwd + inv + ["status", "source", "blocks"]

    def path_for(self, kind: str, name: str, service: Optional[str] = None, subkind: Optional[str] = None) -> str:
        k = self.kinds[kind]
        if "{service}" in k["path"] and not service:
            raise OntologyError(f"«{kind}»: путь требует сервис-владельца")
        if "{subkind}" in k["path"] and subkind not in (k.get("subkinds") or []):
            raise OntologyError(f"«{kind}»: subkind — {', '.join(k.get('subkinds') or [])}")
        return k["path"].format(name=name, service=service or "", subkind=subkind or "")

    def check_name(self, kind: str, name: Any, service: Optional[str] = None) -> List[str]:
        """Нарушения грамматики имени узла (пусто — имя годное)."""
        if not isinstance(name, str) or not name.strip():
            return ["имя пустое"]
        errors = []
        if name != name.strip() or "  " in name:
            errors.append("лишние пробелы в имени")
        if len(name) > _NAME_MAX:
            errors.append(f"имя длиннее {_NAME_MAX} знаков")
        bad = sorted({c for c in name if c in self.forbidden or ord(c) < 32})
        if bad:
            errors.append("запрещённые символы в имени: " + " ".join(repr(c) for c in bad))
        if "`" in name:
            errors.append("бэктики в имени")
        grammar = self.kinds[kind]["name"]
        if grammar == "endpoint":
            parts = name.split(" ")
            if len(parts) != 3:
                errors.append("имя эндпойнта — «<сервис> <METHOD> <путь-через-дефисы>»")
            else:
                svc, method, path = parts
                if service and svc != service:
                    errors.append(f"имя эндпойнта начинается с сервиса «{service}»")
                if method not in _METHODS:
                    errors.append(f"метод «{method}» — один из {', '.join(_METHODS)}")
                if not path or path.startswith("-"):
                    errors.append("путь эндпойнта в имени — через дефисы, без ведущего")
        elif grammar == "topic" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            errors.append("имя топика — как в спеке: латиница, цифры, . _ -")
        return errors


_cache: Dict[str, Ontology] = {}


def load(root: Optional[Path] = None) -> Ontology:
    """Базовая онтология плюс локальные добавления проекта (если есть)."""
    base = json.loads(BASE.read_text(encoding="utf-8"))
    local_p = Path(root) / LOCAL_REL if root is not None else None
    if local_p is not None and local_p.is_file():
        data = merge_local(base, json.loads(local_p.read_text(encoding="utf-8")))
        return Ontology(data)
    if "base" not in _cache:
        _cache["base"] = Ontology(base)
    return _cache["base"]
