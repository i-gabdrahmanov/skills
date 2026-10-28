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
     Имя в форме класса слоя, которого нет среди сущностей grounding (`CreateTaskConfig.name`),
     — подозрение на сущность: grounding сверяется с кодом (inventory_for), и сущность из кода
     освобождается. Не нашлось ни среди сущностей, ни среди классов кода (новая сущность фичи
     или выдуманный класс) — решает аналитик: такие имена уходят отдельным списком (правило
     entity), утверждённые им (согласие spec-entity-<Имя>) — предметная область.

Что НЕ ловим намеренно — это законный язык спеки: сущности предметной области (`Claim`,
`LegalEntity`; имена entity/таблиц из инвентаря освобождены от обоих слоёв во всех написаниях:
`TaskUpz` = `taskUpz` = `task_upz` = `TASK_UPZ`), поля сущностей и таблиц через точку в обоих
форматах — SQL (`task_upz.name`, `npf.task_upz.NAME`, `task_upz.name(varchar 255)`) и Java
(`taskUpz.name`, `taskUpz.name(String)`), поля JSON (`orderId`, `isActive`), пути API, топики,
коды ошибок (`CLAIM_NOT_FOUND`), имена схем запроса/ответа (`ClaimRequest` — часть контракта,
поэтому и DTO-слой инвентаря не сверяется), функции СУБД (`now()`).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

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
    # Сегмент пакета — без «_»: snake_case — это SQL (`npf.task_upz.config` — схема.таблица.
    # колонка), а класс в FQN — с строчной буквой (`npf.taskupz.NAME` — колонка, не класс).
    ("package", "пакеты и полные имена классов", re.compile(
        r"\b(?:[a-z][a-z0-9]*\.){2,}[A-Z](?=[A-Za-z0-9_]*[a-z])[A-Za-z0-9_]*\b"  # ru.bank.claims.ClaimService
        r"|\b[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+\.(?:" + "|".join(_LAYER_PACKAGES) + r")"
        r"(?![A-Za-z0-9_-]|\.[A-Za-z])", _A)),                       # ru.bank.claims.service
    ("file", "файлы исходников", re.compile(
        r"\b[A-Za-z_][A-Za-z0-9_]*\.(?:java|kt)\b|\bsrc/(?:main|test)/", _A)),
    ("annotation", "аннотации", re.compile(
        r"(?<![A-Za-z0-9_@./-])@[A-Z][A-Za-z0-9]+\b", _A)),
    ("generic", "generic-типы", re.compile(
        r"\b[A-Z][A-Za-z0-9]*<[A-Z?][A-Za-z0-9_, <>?]*>", _A)),
)
INVENTORY_RULE = ("inventory", "классы и методы проекта из grounding")
ENTITY_RULE = ("entity", "объекты на согласование аналитика (в форме класса, но нет ни среди "
                         "сущностей, ни среди классов кода)")
# Согласие аналитика «это сущность предметной области»: ключ на каждое имя, пишет только
# record_approval.py с цитатой ответа (класс CONSENT_PREFIXES — снимает гейт с имени).
ENTITY_APPROVAL_PREFIX = "spec-entity-"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", _A)
_COMPOUND = re.compile(r"[a-z0-9][A-Z]", _A)      # составное имя: getClaims, CftGateway


def _compound(name: str) -> bool:
    return bool(_IDENT.match(name) and _COMPOUND.search(name))


def _words(name: str) -> "list[str]":
    if "_" in name:
        return [w for w in name.lower().split("_") if w]
    return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", name)]


def _case_variants(names: Iterable[str]) -> "set[str]":
    """Сущность и её таблица — одно имя в разных написаниях: TaskUpz = taskUpz = task_upz =
    TASK_UPZ. Спека пишет и так, и так, а grounding даёт entity или таблицу — не обязательно обе."""
    out: set[str] = set()
    for n in names:
        out.add(n)
        words = _words(n) if _IDENT.match(n) else []
        if not words:
            continue
        snake = "_".join(words)
        pascal = "".join(w.capitalize() for w in words)
        out |= {snake, snake.upper(), pascal, words[0] + pascal[len(words[0]):]}
    return out


# Тип в скобках сразу за полем — объявление поля, а не вызов: `taskUpz.name(String)`,
# `task_upz.name(varchar 255)`. Аргументы вызова — переменные, а не типы.
_FIELD_TYPES = frozenset("""
    varchar varchar2 nvarchar char text clob blob bytea int integer bigint smallint serial
    bigserial numeric decimal number real float double boolean bool uuid date time timestamp
    timestamptz interval json jsonb
    string long short byte character bigdecimal biginteger localdate localdatetime localtime
    offsetdatetime zoneddatetime instant duration enum
""".split())
_PAREN_HEAD = re.compile(r"\s*(?:(?P<word>[A-Za-z][A-Za-z0-9]*)|[А-Яа-яЁё])")
_CODE_RECEIVER = re.compile(r"(?:" + "|".join(LAYER_SUFFIXES) + r")\Z", re.I | _A)


def _field_decl(tok: str, raw: str, end: int) -> bool:
    """`head.field(` — поле с типом/пояснением в скобках, а не вызов метода. SQL-голова
    (`task_upz`, с «_») — всегда поле: в Java переменных в snake_case нет. Иначе решает
    содержимое скобок: тип (`String`, `varchar`) или кириллица (`(обязательное)`). Голова-класс
    слоя (`claimService.accept(UUID)`) — вызов в любом случае."""
    head, _, field = tok[:-1].rpartition(".")
    if not head or not field or _CODE_RECEIVER.search(head):
        return False
    if "_" in head:
        return True
    m = _PAREN_HEAD.match(raw, end)
    return bool(m) and (m.group("word") is None or m.group("word").lower() in _FIELD_TYPES)


def excerpt_path(project_root: Path) -> Path:
    """grounding-excerpt.json проекта. Путь фиксированный (hooks/_project.grounding_excerpt_path):
    инвентарь эфемерный и за docs.* не следует."""
    return Path(project_root) / "ground" / "inventory" / "grounding-excerpt.json"


def _read_excerpt(excerpt: Optional[Path]) -> Optional[dict]:
    if excerpt is None or not excerpt.is_file():
        return None
    try:
        data = json.loads(excerpt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _rows(data: dict, key: str) -> list:
    v = data.get(key)
    return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def _utils(data: dict) -> "set[str]":
    reuse = data.get("reuse")
    utils = reuse.get("project_utils") if isinstance(reuse, dict) else None
    return {str(u).rsplit(".", 1)[-1] for u in utils} if isinstance(utils, list) else set()


def load_inventory(excerpt: Optional[Path]) -> "tuple[set[str], set[str]]":
    """(имена кода, имена сущностей) из grounding-excerpt.json. Нет/битый файл — пустые множества:
    слой формы токена работает и без инвентаря."""
    data = _read_excerpt(excerpt)
    if data is None:
        return set(), set()
    domain = {str(r.get("name") or "") for r in _rows(data, "entities")}
    domain |= {str(r.get("name") or "") for r in _rows(data, "tables")}
    code: set[str] = set()
    code |= {str(r.get("name") or "") for r in _rows(data, "components")
             if r.get("layer") in _INVENTORY_LAYERS}
    code |= {str(r.get("name") or "") for r in _rows(data, "external_clients")}
    code |= {str(r.get("handler") or "") for r in _rows(data, "api_endpoints")}
    code |= _utils(data)
    domain.discard("")
    domain = _case_variants(domain)
    return {n for n in code if _compound(n) and n not in domain}, domain


def code_classes(excerpt: Optional[Path]) -> "set[str]":
    """Все классы кода из grounding — любого слоя, включая dto, клиенты и утилиты. Имя отсюда —
    точно код, а не сущность: аналитику его на согласование не носим."""
    data = _read_excerpt(excerpt)
    if data is None:
        return set()
    names = {str(r.get("name") or "") for r in _rows(data, "components")}
    names |= {str(r.get("name") or "") for r in _rows(data, "external_clients")}
    names |= _utils(data)
    names.discard("")
    return names


_HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"


def approved_entities(project_root: Path) -> "set[str]":
    """Имена, которые аналитик утвердил сущностями: активные согласия spec-entity-<Имя> в
    ground/approvals.jsonl. Читаем через forge_events — с проверкой провенанса record_approval и
    отзыва откатом; рукописная строка в журнале имя не освобождает."""
    if str(_HOOKS_DIR) not in sys.path and _HOOKS_DIR.is_dir():
        sys.path.append(str(_HOOKS_DIR))
    try:
        import forge_events as FE  # noqa: N812
    except ImportError:
        return set()
    root = Path(project_root)
    keys = {str(r.get("key") or "") for r in FE.read_log(FE.approvals_path(root))}
    return {k[len(ENTITY_APPROVAL_PREFIX):] for k in keys
            if k.startswith(ENTITY_APPROVAL_PREFIX) and len(k) > len(ENTITY_APPROVAL_PREFIX)
            and FE.approval(root, k) is not None}


# Единственный легитимный писатель ground/inventory (hooks/state-write-guard.py) — его и зовём.
_ENSURE_INVENTORY = (Path(__file__).resolve().parents[2] / "system-analyst" / "scripts"
                     / "ensure_inventory.py")


class Inventory(NamedTuple):
    known: "set[str]"            # имена кода для слоя 2
    domain: "set[str]"           # сущности/таблицы grounding + утверждённые аналитиком
    code: "Optional[set[str]]"   # все классы кода; None — не проект пайплайна, спросить негде
    rebuilt: "list[str]"         # сущности, которые добавила пересборка grounding


def inventory_for(project_root: Optional[Path], raw: str) -> Inventory:
    """Инвентарь для линтера: при подозрении на сущность сверенный с кодом и с аналитиком.

    Имя в форме класса слоя (`CreateTaskConfig.name`) может оказаться сущностью: суффиксы
    Config/Handler/… у entity бывают. Нет его среди сущностей grounding — не верим форме, а
    сверяемся с кодом: grounding пересобирается ensure_inventory.py (идемпотентно: пересканирует,
    только если инвентаря нет или код новее него) и перечитывается. Класс — сущность в коде →
    он теперь в grounding, и спека вправе его называть. Руками в grounding не пишем: так
    «сущностью» объявлялся бы любой ClaimService. Пересобираем только в проекте пайплайна
    (ground/policy.json, legacy pipeline.json) или там, где инвентарь уже снимали: голый ground/
    бывает и остатком логов, заводить в таком каталоге инвентарь гейту не с руки.

    Что и после сверки с кодом не нашлось ни среди сущностей, ни среди классов кода, решает
    аналитик (правило entity): имена, которые он утвердил (spec-entity-<Имя>), — в domain."""
    if project_root is None:
        return Inventory(set(), set(), None, [])
    root = Path(project_root)
    ground = root / "ground"
    pipeline = any((ground / n).exists() for n in ("policy.json", "pipeline.json", "inventory"))
    approved = approved_entities(root) if pipeline else set()
    excerpt = excerpt_path(root)
    known, domain = load_inventory(excerpt)
    domain |= approved
    suspects = {h["token"] for h in find(raw, known=known, domain=domain) if h["rule"] == "class"}
    rebuilt: list[str] = []
    if suspects and pipeline and _ENSURE_INVENTORY.is_file():
        try:
            subprocess.run([sys.executable, str(_ENSURE_INVENTORY), "--root", str(root),
                            "--quiet"], capture_output=True, timeout=300, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        else:
            known, fresh = load_inventory(excerpt)
            fresh |= approved
            rebuilt = sorted(suspects & (fresh - domain))
            domain = fresh
    return Inventory(known, domain, code_classes(excerpt) if pipeline else None, rebuilt)


def added_warning(added: "list[str]") -> "list[str]":
    if not added:
        return []
    names = ", ".join(f"`{n}`" for n in added)
    return [f"grounding устарел и пересобран по коду: {names} — сущности проекта, их имена и поля "
            f"в спеке законны"]


def find(raw: str, *, known: Iterable[str] = (), domain: Iterable[str] = (),
         ignore: Iterable[str] = (), code: Optional[Iterable[str]] = None) -> "list[dict]":
    """Находки [{rule, token, line}] в порядке документа; токен — первое вхождение на правило.

    known  — имена кода из инвентаря (слой 2);
    code   — все классы кода из grounding (Inventory.code): класс-находка не из этого списка и
             не тип фреймворка уходит аналитику (правило entity). None — спросить негде,
             остаётся классом;
    domain — имена сущностей/таблиц: освобождены от обоих слоёв (предметная область) во всех
             написаниях, как и их поля через точку (`taskUpz.taskCreate` — поле, даже если
             хендлер зовётся так же);
    ignore — токены, которые не ставятся в вину документу (у дельты фикса — уже лежащие в
             мастере: дельта обязана нести требование мастера целиком, долг не её)."""
    domain = _case_variants(domain)
    skip = domain | set(ignore) | _NOT_CODE
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
            if rule == "method" and _field_decl(m.group(0), raw, m.end()):
                continue
            add(rule, m.group(0), m.start())

    def field_of_domain(pos: int, end: int) -> bool:
        if pos == 0 or raw[pos - 1] != "." or raw[end:end + 1] == "(":
            return False
        head = re.search(r"[A-Za-z_][A-Za-z0-9_]*\Z", raw[:pos - 1], _A)
        return bool(head) and head.group(0) in domain

    names = sorted({n for n in known if n and n not in skip}, key=len, reverse=True)
    if names:
        rx = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(map(re.escape, names))
                        + r")(?![A-Za-z0-9_])")
        flagged = {h["token"].rstrip("(").lstrip("@") for h in hits}
        for m in rx.finditer(raw):
            if m.group(0) in flagged:          # уже пойман формой — второй раз не шумим
                continue
            if field_of_domain(m.start(), m.end()):
                continue
            add(INVENTORY_RULE[0], m.group(0), m.start())

    if code is not None:
        code = set(code)
        for h in hits:
            if (h["rule"] == "class" and h["token"] not in code
                    and h["token"] not in FRAMEWORK_TYPES):
                h["rule"] = ENTITY_RULE[0]

    hits.sort(key=lambda h: h["line"])
    return hits


def pending_entities(hits: "list[dict]") -> "list[str]":
    """Имена, ждущие решения аналитика (правило entity), в порядке документа."""
    return [h["token"] for h in hits if h["rule"] == ENTITY_RULE[0]]


_TITLES = {rid: title for rid, title, _rx in RULES}
_TITLES[INVENTORY_RULE[0]] = INVENTORY_RULE[1]
_TITLES[ENTITY_RULE[0]] = ENTITY_RULE[1]
_HINT = ("спека описывает поведение, а не код: вместо `ClaimService.accept()` — «система "
         "принимает заявку», вместо `ClaimController` — «эндпойнт POST /api/v1/claims», соседний "
         "сервис — по имени деплоя (`claims-service`); классы и методы — уровень tech-design")
_INVENTORY_HINT = ("это настоящие имена из кода проекта — grounding дан для сверки, а не для "
                   "цитирования в спеке")
_ENTITY_HINT = ("сущность это (в т.ч. новая в этой фиче) или класс, решает аналитик, не ты: не "
                "переименовывай и не переписывай их сам — покажи ему этот список; утверждённое им "
                "фиксируется record_approval.py --key " + ENTITY_APPROVAL_PREFIX + "<Имя> "
                "(--evidence — его дословный ответ), и гейт имя пропускает; остальное перепиши "
                "словами поведения")
MAX_SAMPLES = 6


def errors(hits: "list[dict]", where: str = "в sdd.md") -> "list[str]":
    """Одна ошибка на правило: что нашли (строка + токен, до MAX_SAMPLES). Как переписать —
    один раз, в первой ошибке формы; у инвентаря — своя подсказка."""
    out: list[str] = []
    hinted = False
    for rule in [r[0] for r in RULES] + [INVENTORY_RULE[0], ENTITY_RULE[0]]:
        group = [h for h in hits if h["rule"] == rule]
        if not group:
            continue
        if rule == ENTITY_RULE[0]:        # список целиком: аналитик решает по каждому имени
            names = ", ".join(f"`{h['token']}` (стр. {h['line']})" for h in group)
            out.append(f"{where} {_TITLES[rule]}: {names} — {_ENTITY_HINT}")
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
