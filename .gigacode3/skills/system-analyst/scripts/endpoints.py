"""Парсер REST-эндпойнтов в Java/Spring исходниках.

Извлекает контроллеры (@RestController/@Controller), методы с *Mapping,
поля-сервисы и их вызовы из тела метода — для последующей генерации
sequence-диаграмм Client→Controller→Service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from common import in_skipped_dir

# Аннотации маппингов: ключ — имя аннотации, значение — HTTP-метод (или None для @RequestMapping).
MAPPING_ANNOTATIONS: dict[str, str | None] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": None,
}

# Суффиксы, по которым опознаём «бин-зависимость» (поле, на которое стоит делегировать в sequence).
DEPENDENCY_SUFFIXES = (
    "Service",
    "Dao",
    "Repository",
    "Client",
    "Handler",
    "Manager",
    "Watcher",
    "Mapper",
    "Gateway",
    "Provider",
    "Cache",
    "Producer",
    "Publisher",
    "Template",
)


@dataclass
class Param:
    name: str
    type: str
    kind: str  # "path" | "query" | "body" | "header" | "other"


@dataclass
class Endpoint:
    http_method: str          # "GET"/"POST"/...
    path: str                 # полный путь, например "/api/v2/artifact/new"
    method_name: str          # java-метод
    return_type: str
    params: list[Param] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (field, method)


@dataclass
class Controller:
    class_name: str
    file: str
    base_path: str
    endpoints: list[Endpoint] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)  # field_name -> type


_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"//[^\n]*")
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _strip_comments(src: str) -> str:
    src = _COMMENT_BLOCK_RE.sub("", src)
    src = _COMMENT_LINE_RE.sub("", src)
    return src


def _balanced(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Вернуть индекс символа сразу ПОСЛЕ закрывающей скобки, парной к text[start]."""
    assert text[start] == open_ch
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            m = _STRING_RE.match(text, i)
            if m:
                i = m.end()
                continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _annotation_value(args: str) -> str:
    """Извлечь строковое значение из аргументов аннотации.
    Поддерживает: "x", value = "x", path = "x", "{a,b}" — берём первое.
    """
    if not args:
        return ""
    s = args.strip().lstrip("(").rstrip(")")
    # Если в скобках присутствуют именованные параметры
    for key in ("value", "path"):
        m = re.search(rf'{key}\s*=\s*"([^"]*)"', s)
        if m:
            return m.group(1)
        m = re.search(rf'{key}\s*=\s*\{{\s*"([^"]*)"', s)
        if m:
            return m.group(1)
    # Просто строковый литерал первым позиционным аргументом
    m = re.search(r'"([^"]*)"', s)
    if m:
        return m.group(1)
    return ""


def _request_mapping_http(args: str) -> str | None:
    """Извлечь HTTP-метод из @RequestMapping(method = RequestMethod.X)."""
    if not args:
        return None
    m = re.search(r"RequestMethod\.(\w+)", args)
    return m.group(1) if m else None


def _join_path(base: str, sub: str) -> str:
    base = base.strip()
    sub = sub.strip()
    if not base and not sub:
        return "/"
    if not sub:
        return "/" + base.lstrip("/")
    if not base:
        return "/" + sub.lstrip("/")
    return "/" + base.strip("/") + "/" + sub.lstrip("/")


def _split_params(s: str) -> list[str]:
    """Разрезать список параметров метода по запятым верхнего уровня."""
    parts: list[str] = []
    depth_a = depth_g = depth_c = 0
    buf: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            m = _STRING_RE.match(s, i)
            if m:
                buf.append(s[i:m.end()])
                i = m.end()
                continue
        if ch == "(":
            depth_a += 1
        elif ch == ")":
            depth_a -= 1
        elif ch == "<":
            depth_g += 1
        elif ch == ">":
            depth_g -= 1
        elif ch == "{":
            depth_c += 1
        elif ch == "}":
            depth_c -= 1
        if ch == "," and depth_a == depth_g == depth_c == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        last = "".join(buf).strip()
        if last:
            parts.append(last)
    return parts


def _parse_param(raw: str) -> Param | None:
    if not raw:
        return None
    kind = "other"
    s = raw
    if re.search(r"@PathVariable\b", s):
        kind = "path"
    elif re.search(r"@RequestParam\b", s):
        kind = "query"
    elif re.search(r"@RequestBody\b", s):
        kind = "body"
    elif re.search(r"@RequestHeader\b", s):
        kind = "header"
    # Удалить аннотации (вместе с возможными скобками)
    while True:
        m = re.search(r"@\w+", s)
        if not m:
            break
        start = m.start()
        end = m.end()
        # если за аннотацией скобки — пропустить их сбалансированно
        j = end
        while j < len(s) and s[j].isspace():
            j += 1
        if j < len(s) and s[j] == "(":
            close = _balanced(s, j, "(", ")")
            if close > 0:
                end = close
        s = (s[:start] + " " + s[end:]).strip()
    # Теперь s = "Type name" (возможно с пробелами в дженериках)
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.rsplit(" ", 1)
    if len(parts) != 2:
        return None
    type_, name = parts
    name = name.rstrip(",").rstrip(")")
    return Param(name=name, type=type_.strip(), kind=kind)


def _find_method_signature(
    text: str, after: int, kotlin: bool = False
) -> tuple[int, int, str, str, str] | None:
    """С позиции `after` найти ближайшую сигнатуру метода.

    Возвращает (sig_start, body_start_or_semicolon, return_type, name, params_raw)
    или None, если впереди не метод (например, поле).
    """
    n = len(text)
    i = after
    # Пропустить пробелы и другие аннотации
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "@":
            # пропустить аннотацию и её аргументы
            i += 1
            while i < n and (text[i].isalnum() or text[i] in "._"):
                i += 1
            while i < n and text[i].isspace():
                i += 1
            if i < n and text[i] == "(":
                close = _balanced(text, i, "(", ")")
                if close < 0:
                    return None
                i = close
            continue
        break
    sig_start = i
    # Найти открывающую скобку параметров; до неё — модификаторы, return type, имя.
    paren = -1
    j = i
    while j < n:
        ch = text[j]
        if ch == '"':
            m = _STRING_RE.match(text, j)
            if m:
                j = m.end()
                continue
        if ch == ";" or ch == "{" or ch == "}":
            # это поле, не метод
            return None
        if ch == "(":
            paren = j
            break
        j += 1
    if paren < 0:
        return None
    # Перед paren — имя метода (последний идентификатор)
    head = text[sig_start:paren].rstrip()
    m_name = re.search(r"([A-Za-z_]\w*)\s*$", head)
    if not m_name:
        return None
    method_name = m_name.group(1)
    # Параметры
    close = _balanced(text, paren, "(", ")")
    if close < 0:
        return None
    params_raw = text[paren + 1:close - 1]
    if kotlin:
        # Kotlin: `fun name(params): ReturnType` или `fun name(params)`.
        # Имя уже извлечено; return type — то, что после `:` за закрывающей скобкой.
        # Перед именем в head должно идти `fun` (возможно с модификаторами).
        if not re.search(r"\bfun\b", head):
            return None
        k = close
        return_type = "Unit"
        while k < n and text[k].isspace():
            k += 1
        if k < n and text[k] == ":":
            k += 1
            depth = 0
            start = k
            while k < n:
                ch = text[k]
                if ch == "<":
                    depth += 1
                elif ch == ">":
                    depth -= 1
                elif depth == 0 and (ch in "{=" or ch == "\n"):
                    break
                k += 1
            return_type = text[start:k].strip() or "Unit"
        return sig_start, close, return_type, method_name, params_raw
    before_name = head[:m_name.start()].strip()
    # Убрать модификаторы
    tokens = before_name.split()
    modifiers = {"public", "private", "protected", "static", "final",
                 "abstract", "synchronized", "default", "native", "strictfp"}
    filtered = [t for t in tokens if t not in modifiers]
    return_type = " ".join(filtered) if filtered else "void"
    # После параметров — { или ; (через возможные `throws ...`)
    return sig_start, close, return_type, method_name, params_raw


def _method_body(text: str, after_params: int) -> str:
    """Извлечь тело метода (содержимое { ... }) начиная от `after_params`."""
    n = len(text)
    i = after_params
    while i < n and text[i] != "{":
        if text[i] == ";":  # абстрактный метод — тела нет
            return ""
        i += 1
    if i >= n:
        return ""
    close = _balanced(text, i, "{", "}")
    if close < 0:
        return ""
    return text[i + 1:close - 1]


def _is_controller(text: str) -> bool:
    return bool(re.search(r"@RestController\b", text)) or bool(
        re.search(r"@Controller\b", text)
    )


def _class_info(text: str) -> tuple[str, str] | None:
    """Вернуть (class_name, base_path) или None."""
    m = re.search(r"\bclass\s+([A-Za-z_]\w*)", text)
    if not m:
        return None
    class_name = m.group(1)
    # @RequestMapping на классе — берём ближайший до объявления класса
    base = ""
    for am in re.finditer(r"@RequestMapping\b", text[:m.start()]):
        j = am.end()
        while j < len(text) and text[j].isspace():
            j += 1
        if j < len(text) and text[j] == "(":
            close = _balanced(text, j, "(", ")")
            if close > 0:
                base = _annotation_value(text[j:close])
    return class_name, base


_FIELD_RE = re.compile(
    r"(?:private|protected|public)\s+(?:final\s+|static\s+)*"
    r"([A-Z]\w*(?:<[^;=]+>)?)\s+([a-z_]\w*)\s*[;=]"
)
# Kotlin: `private val name: Type` / `val name: Type` / конструктор `name: Type`.
_FIELD_RE_KT = re.compile(
    r"(?:private\s+|protected\s+|public\s+|internal\s+)?"
    r"(?:val|var)\s+([a-z_]\w*)\s*:\s*([A-Z]\w*(?:<[^,)>]+>)?)"
)


def _dependencies(text: str, kotlin: bool = False) -> dict[str, str]:
    deps: dict[str, str] = {}
    if kotlin:
        for m in _FIELD_RE_KT.finditer(text):
            name = m.group(1)
            type_ = m.group(2)
            base_type = re.sub(r"<.*?>", "", type_)
            if base_type.endswith(DEPENDENCY_SUFFIXES):
                deps[name] = base_type
        return deps
    for m in _FIELD_RE.finditer(text):
        type_ = m.group(1)
        name = m.group(2)
        base_type = re.sub(r"<.*?>", "", type_)
        if base_type.endswith(DEPENDENCY_SUFFIXES):
            deps[name] = base_type
    return deps


def _extract_calls(body: str, deps: dict[str, str]) -> list[tuple[str, str]]:
    if not body or not deps:
        return []
    calls: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in deps.keys()) + r")\s*\.\s*([A-Za-z_]\w*)\s*\("
    )
    for m in pattern.finditer(body):
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        calls.append(key)
    return calls


def parse_file(path: Path) -> Controller | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if "@RestController" not in raw and "@Controller" not in raw:
        return None
    text = _strip_comments(raw)
    if not _is_controller(text):
        return None
    info = _class_info(text)
    if not info:
        return None
    class_name, base_path = info
    is_kotlin = path.suffix == ".kt"
    deps = _dependencies(text, kotlin=is_kotlin)
    controller = Controller(
        class_name=class_name, file=str(path), base_path=base_path, dependencies=deps
    )
    # Найти все маппинг-аннотации
    anno_pattern = re.compile(
        r"@(" + "|".join(MAPPING_ANNOTATIONS.keys()) + r")\b"
    )
    for m in anno_pattern.finditer(text):
        anno_name = m.group(1)
        j = m.end()
        # Захват аргументов аннотации
        args = ""
        k = j
        while k < len(text) and text[k].isspace():
            k += 1
        if k < len(text) and text[k] == "(":
            close = _balanced(text, k, "(", ")")
            if close > 0:
                args = text[k:close]
                j = close
        # Должен следовать метод
        sig = _find_method_signature(text, j, kotlin=is_kotlin)
        if not sig:
            continue
        _, params_end, ret_type, method_name, params_raw = sig
        sub_path = _annotation_value(args)
        http = MAPPING_ANNOTATIONS[anno_name]
        if http is None:
            http = _request_mapping_http(args) or "ANY"
        params = []
        for raw_p in _split_params(params_raw):
            p = _parse_param(raw_p)
            if p:
                params.append(p)
        body = _method_body(text, params_end)
        calls = _extract_calls(body, deps)
        endpoint = Endpoint(
            http_method=http,
            path=_join_path(base_path, sub_path),
            method_name=method_name,
            return_type=ret_type,
            params=params,
            calls=calls,
        )
        controller.endpoints.append(endpoint)
    return controller if controller.endpoints else None


def scan(root: Path) -> list[Controller]:
    controllers: list[Controller] = []
    root = root.resolve()
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if in_skipped_dir(root, path):
            continue
        if path.suffix not in (".java", ".kt"):
            continue
        ctrl = parse_file(path)
        if ctrl:
            controllers.append(ctrl)
    controllers.sort(key=lambda c: c.class_name)
    return controllers


def iter_endpoints(controllers: Iterable[Controller]) -> Iterable[tuple[Controller, Endpoint]]:
    for c in controllers:
        for e in c.endpoints:
            yield c, e
