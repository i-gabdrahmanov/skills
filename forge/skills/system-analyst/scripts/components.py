"""Детерминированный сканер Spring-компонентов по слоям (инвентарь существующих классов).

Зачем: tech-design проектирует ПО grounding, не по коду, и раньше grounding перечислял
только entity/endpoint/util-классы. Слои service/repository/mapper/dto/controller инвентаря
не имели — дизайнер называл «существующие» классы этих слоёв по памяти и выдумывал их.
Этот сканер даёт ground-truth список того, что реально есть, чтобы дизайн ссылался на
настоящие классы, а гейт (check_taskplan) ловил ссылки на несуществующие.

ADVISORY-категория: неполнота (эвристика DTO) не должна ронять coverage-gate. Для каждого
типа возвращаем {name, package, layer, file} — module проставит scan_all по полю file.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import iter_java, read_text, strip_comments

_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
# Объявление типа: class/interface/record/enum + имя. Заголовок до '{' разбираем отдельно.
_TYPE_RE = re.compile(r"\b(class|interface|record|enum)\s+([A-Za-z_]\w*)")
_ANNO_RE = re.compile(r"@([A-Za-z_]\w*)")
# Базовые интерфейсы Spring Data — по ним репозиторий опознаётся даже без @Repository.
_REPO_BASES = ("JpaRepository", "CrudRepository", "PagingAndSortingRepository",
               "ReactiveCrudRepository", "R2dbcRepository", "MongoRepository",
               "ElasticsearchRepository", "JpaSpecificationExecutor", "Repository")
_DTO_NAME_RE = re.compile(r".*(Dto|DTO|Request|Response|Payload|Command|View)$")
_TYPE_KEYWORDS = ("class", "interface", "record")


def _annotations_before(text: str, decl_start: int, prev_end: int) -> set[str]:
    """@Аннотации в окне перед объявлением типа (между предыдущим типом и этим)."""
    window = text[max(prev_end, decl_start - 500): decl_start]
    return set(_ANNO_RE.findall(window))


def _header(text: str, decl_end: int) -> str:
    """Кусок заголовка типа от имени до открывающей '{' (там extends/implements)."""
    brace = text.find("{", decl_end)
    return text[decl_end: brace] if brace != -1 else text[decl_end: decl_end + 300]


def _classify(name: str, kind: str, annos: set[str], header: str, pkg: str) -> str | None:
    """Слой компонента или None, если тип неинтересен как переиспользуемый компонент."""
    is_repo = "Repository" in annos or any(b in header for b in _REPO_BASES)
    if is_repo:
        return "repository"
    if "RestController" in annos or "Controller" in annos:
        return "controller"
    if "Service" in annos:
        return "service"
    if "Mapper" in annos or name.endswith("Mapper"):
        return "mapper"
    if "Component" in annos or "Configuration" in annos:
        return "component"
    pkg_l = pkg.lower()
    if pkg_l.endswith(".dto") or pkg_l.endswith(".dtos") or ".dto." in f"{pkg_l}." or _DTO_NAME_RE.match(name):
        # DTO — эвристика по имени/пакету; интерфейсы-клиенты (Feign) сюда не тянем.
        if kind in ("class", "record"):
            return "dto"
    return None


def parse_file(path: Path) -> list[dict]:
    raw = read_text(path)
    # Быстрый фильтр: файл без объявления типа парсить незачем. DTO аннотаций не имеют —
    # поэтому фильтруем по ключевым словам типа, а не по маркерам-аннотациям.
    if not any(k in raw for k in _TYPE_KEYWORDS):
        return []
    text = strip_comments(raw)
    pkg_m = _PKG_RE.search(text)
    pkg = pkg_m.group(1) if pkg_m else ""
    out: list[dict] = []
    prev_end = 0
    for m in _TYPE_RE.finditer(text):
        kind, name = m.group(1), m.group(2)
        annos = _annotations_before(text, m.start(), prev_end)
        header = _header(text, m.end())
        prev_end = m.end()
        layer = _classify(name, kind, annos, header, pkg)
        if layer is None:
            continue
        out.append({"name": name, "package": pkg, "layer": layer, "file": str(path)})
    return out


def scan(root: Path) -> list[dict]:
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for p in iter_java(Path(root)):
        for it in parse_file(p):
            key = (it["name"], it["file"])
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda d: (d["layer"], d["name"]))
    return items
