#!/usr/bin/env python3
"""impl_names.py — линтер имён реализации в спеке: классы, методы, пакеты, файлы исходников.

Спека — «что», tech-design — «как». Код-блоки и сигнатуры (`public class`, `import …;`) гейт
ловил и раньше, а имена из кода просачивались прозой: «вызвать `ClaimService.accept()`»,
«`ClaimController` отдаёт 404», «метод `findByStatus`». Главный источник — grounding: в выжимке
есть классы компонентов, хендлеры эндпойнтов и клиенты, и модель переносит их в спеку как есть.

Два слоя, оба детерминированные:
  1. ФОРМА токена — то, чем в спеке бывает только код:
     вызов/ссылка на метод (`findByStatus(`, `claimService.accept(`, `Claim::getId`,
     `ClaimService#accept`), класс с суффиксом технического слоя (`*Service`, `*Controller`,
     `*Repository`, `*Mapper`, `*Dto`, `*Exception`, …) и типы фреймворка (`RestTemplate`,
     `ResponseEntity`), FQN (`ru.bank.claims.ClaimService`) и пакет слоя
     (`ru.bank.claims.service`), файл исходника (`ClaimService.java`, `src/main/java`),
     аннотация (`@Transactional`), generic-тип (`List<Claim>`).
  2. ИНВЕНТАРЬ проекта — настоящие имена из grounding-excerpt.json: компоненты слоёв
     controller/service/repository/mapper/component, внешние клиенты, утилиты, хендлеры
     эндпойнтов. Ловит имена без «говорящего» суффикса (`CftGateway`, `getClaims`), которые
     форма токена пропускает. Берутся только составные имена (`getClaims`, не `list`): имя из
     одного слова — обычное слово прозы.

Что НЕ ловим намеренно — это законный язык спеки: сущности предметной области (`Claim`,
`LegalEntity`; имена entity из инвентаря освобождены от обоих слоёв), таблицы и поля
(`claim.accepted_at`), поля JSON (`orderId`, `isActive`), пути API, топики, коды ошибок
(`CLAIM_NOT_FOUND`), имена схем запроса/ответа (`ClaimRequest` — часть контракта, поэтому и
DTO-слой инвентаря не сверяется), функции СУБД (`now()`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

_A = re.ASCII   # \b и классы — по латинице: кириллица рядом с именем не склеивается с ним

# Суффиксы технических слоёв. Отобраны те, что в предметной области банка не встречаются:
# без Client/Manager/Processor/Strategy/Factory/Converter/Advice (CorporateClient,
# RelationshipManager, InvestmentStrategy, «кредитная фабрика» — это бизнес-термины).
LAYER_SUFFIXES = (
    "ServiceImpl", "Service", "Impl", "Controller", "Repository", "Dao", "DAO", "Mapper",
    "Dto", "DTO", "Exception", "Handler", "Listener", "Interceptor", "Configuration", "Config",
    "Properties", "Utils", "Util", "Helper", "Validator", "Facade", "Adapter", "Scheduler",
    "Bean", "Resolver",
)
# Типы фреймворка, у которых суффикса слоя нет, а в спеке они — только код.
FRAMEWORK_TYPES = (
    "RestTemplate", "JdbcTemplate", "NamedParameterJdbcTemplate", "KafkaTemplate",
    "ResponseEntity", "CompletableFuture", "WebClient", "RestClient", "FeignClient",
    "KafkaConsumer", "KafkaProducer", "EntityManager", "ApplicationContext",
    "ControllerAdvice", "RestControllerAdvice", "Pageable", "MultipartFile",
    "HttpServletRequest", "SecurityContextHolder",
)
_LAYER_PACKAGES = ("service", "services", "controller", "controllers", "repository",
                   "repositories", "dto", "dtos", "mapper", "mappers", "impl", "config",
                   "entity", "entities", "persistence")
# Слои инвентаря, имена которых в спеке — утечка. dto не сверяем: схемы запроса/ответа
# (ClaimRequest) — часть API-контракта, их спека называть вправе.
_INVENTORY_LAYERS = ("controller", "service", "repository", "mapper", "component")
# Термины протоколов/продуктов, совпавшие по форме с классом: «SOAP WebService ЦФТ» — не код.
_NOT_CODE = frozenset({"WebService", "WebServices"})

# (id, что это, regex). Порядок = порядок сообщений.
RULES: "tuple[tuple[str, str, re.Pattern], ...]" = (
    ("method", "вызовы и ссылки на методы", re.compile(
        r"\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+[A-Za-z0-9]*\("        # findByStatus(
        r"|\b[A-Za-z_][A-Za-z0-9_]*\.[a-z][A-Za-z0-9_]*\("          # claimService.accept( / Map.of(
        r"|\b[A-Z][A-Za-z0-9]*(?:#|::)[a-z][A-Za-z0-9_]*", _A)),    # ClaimService#accept / Claim::getId
    ("class", "имена классов", re.compile(
        r"\b[A-Z][A-Za-z0-9]*(?:" + "|".join(LAYER_SUFFIXES) + r")\b"
        r"|\b(?:" + "|".join(FRAMEWORK_TYPES) + r")\b", _A)),
    ("package", "пакеты и полные имена классов", re.compile(
        r"\b(?:[a-z][a-z0-9_]*\.){2,}[A-Z][A-Za-z0-9_]*\b"           # ru.bank.claims.ClaimService
        r"|\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.(?:" + "|".join(_LAYER_PACKAGES) + r")"
        r"(?![A-Za-z0-9_-]|\.[A-Za-z])", _A)),                       # ru.bank.claims.service
    ("file", "файлы исходников", re.compile(
        r"\b[A-Za-z_][A-Za-z0-9_]*\.(?:java|kt)\b|\bsrc/(?:main|test)/", _A)),
    ("annotation", "аннотации", re.compile(
        r"(?<![A-Za-z0-9_@./-])@[A-Z][A-Za-z0-9]+\b", _A)),
    ("generic", "generic-типы", re.compile(
        r"\b[A-Z][A-Za-z0-9]*<[A-Z?][A-Za-z0-9_, <>?]*>", _A)),
)
INVENTORY_RULE = ("inventory", "классы и методы проекта из grounding")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", _A)
_COMPOUND = re.compile(r"[a-z0-9][A-Z]", _A)      # составное имя: getClaims, CftGateway


def _compound(name: str) -> bool:
    return bool(_IDENT.match(name) and _COMPOUND.search(name))


def excerpt_path(project_root: Path) -> Path:
    """grounding-excerpt.json проекта. Путь фиксированный (hooks/_project.grounding_excerpt_path):
    инвентарь эфемерный и за docs.* не следует."""
    return Path(project_root) / "ground" / "inventory" / "grounding-excerpt.json"


def load_inventory(excerpt: Optional[Path]) -> "tuple[set[str], set[str]]":
    """(имена кода, имена сущностей) из grounding-excerpt.json. Нет/битый файл — пустые множества:
    слой формы токена работает и без инвентаря."""
    if excerpt is None or not excerpt.is_file():
        return set(), set()
    try:
        data = json.loads(excerpt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), set()
    if not isinstance(data, dict):
        return set(), set()

    def rows(key: str) -> list:
        v = data.get(key)
        return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []

    domain = {str(r.get("name") or "") for r in rows("entities")}
    domain |= {str(r.get("name") or "") for r in rows("tables")}
    code: set[str] = set()
    code |= {str(r.get("name") or "") for r in rows("components")
             if r.get("layer") in _INVENTORY_LAYERS}
    code |= {str(r.get("name") or "") for r in rows("external_clients")}
    code |= {str(r.get("handler") or "") for r in rows("api_endpoints")}
    reuse = data.get("reuse")
    utils = reuse.get("project_utils") if isinstance(reuse, dict) else None
    if isinstance(utils, list):
        code |= {str(u).rsplit(".", 1)[-1] for u in utils}
    domain.discard("")
    return {n for n in code if _compound(n) and n not in domain}, domain


def find(raw: str, *, known: Iterable[str] = (), domain: Iterable[str] = (),
         ignore: Iterable[str] = ()) -> "list[dict]":
    """Находки [{rule, token, line}] в порядке документа; токен — первое вхождение на правило.

    known  — имена кода из инвентаря (слой 2);
    domain — имена сущностей/таблиц: освобождены от обоих слоёв (предметная область);
    ignore — токены, которые не ставятся в вину документу (у дельты фикса — уже лежащие в
             мастере: дельта обязана нести требование мастера целиком, долг не её)."""
    skip = set(domain) | set(ignore) | _NOT_CODE
    line_starts = [0] + [m.end() for m in re.finditer(r"\n", raw)]

    def line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(rule: str, tok: str, pos: int) -> None:
        bare = tok.rstrip("(").lstrip("@")
        if tok in skip or bare in skip or bare.split("<", 1)[0] in skip:
            return
        if (rule, bare) in seen:
            return
        seen.add((rule, bare))
        hits.append({"rule": rule, "token": tok, "line": line_of(pos)})

    for rule, _title, rx in RULES:
        for m in rx.finditer(raw):
            add(rule, m.group(0), m.start())

    names = sorted({n for n in known if n and n not in skip}, key=len, reverse=True)
    if names:
        rx = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(map(re.escape, names))
                        + r")(?![A-Za-z0-9_])")
        flagged = {h["token"].rstrip("(").lstrip("@") for h in hits}
        for m in rx.finditer(raw):
            if m.group(0) not in flagged:      # уже пойман формой — второй раз не шумим
                add(INVENTORY_RULE[0], m.group(0), m.start())

    hits.sort(key=lambda h: h["line"])
    return hits


_TITLES = {rid: title for rid, title, _rx in RULES}
_TITLES[INVENTORY_RULE[0]] = INVENTORY_RULE[1]
_HINT = ("спека описывает поведение, а не код: вместо `ClaimService.accept()` — «система "
         "принимает заявку», вместо `ClaimController` — «эндпойнт POST /api/v1/claims», соседний "
         "сервис — по имени деплоя (`claims-service`); классы и методы — уровень tech-design")
_INVENTORY_HINT = ("это настоящие имена из кода проекта — grounding дан для сверки, а не для "
                   "цитирования в спеке")
MAX_SAMPLES = 6


def errors(hits: "list[dict]", where: str = "в sdd.md") -> "list[str]":
    """Одна ошибка на правило: что нашли (строка + токен, до MAX_SAMPLES). Как переписать —
    один раз, в первой ошибке формы; у инвентаря — своя подсказка."""
    out: list[str] = []
    hinted = False
    for rule in [r[0] for r in RULES] + [INVENTORY_RULE[0]]:
        group = [h for h in hits if h["rule"] == rule]
        if not group:
            continue
        sample = ", ".join(f"стр. {h['line']} `{h['token']}`" for h in group[:MAX_SAMPLES])
        more = f" и ещё {len(group) - MAX_SAMPLES}" if len(group) > MAX_SAMPLES else ""
        if rule == INVENTORY_RULE[0]:
            hint = f" — {_INVENTORY_HINT}" + ("" if hinted else f"; {_HINT}")
        else:
            hint = "" if hinted else f" — {_HINT}"
        hinted = True
        out.append(f"{where} {_TITLES[rule]}: {sample}{more}{hint}")
    return out
