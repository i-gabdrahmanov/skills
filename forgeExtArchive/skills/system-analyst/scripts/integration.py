"""Детерминированный сканер внешних интеграций: @FeignClient / WebClient / RestTemplate.

«Внешний клиент» — нечёткая категория (один WebClient может звать много сервисов),
поэтому в gate она ADVISORY. Считаем классы-обёртки интеграции, а не каждое упоминание.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import iter_java, read_text, strip_comments

_FEIGN_RE = re.compile(r"@FeignClient\s*\(([^)]*)\)")
_CLASS_RE = re.compile(r"\b(?:class|interface)\s+([A-Za-z_]\w*)")
_WEBCLIENT_FIELD_RE = re.compile(r"\bWebClient\b\s+[a-z_]\w*\s*[;=)]")
_REST_FIELD_RE = re.compile(r"\bRestTemplate\b\s+[a-z_]\w*\s*[;=)]")
# Spring RestClient (6.1+): поле или фабрика.
_RESTCLIENT_FIELD_RE = re.compile(r"\bRestClient\b\s+[a-z_]\w*\s*[;=)]")
_OKHTTP_FIELD_RE = re.compile(r"\bOkHttpClient\b\s+[a-z_]\w*\s*[;=)]")
# gRPC: аннотация net.devh @GrpcClient("name") или сгенерированные стабы *BlockingStub/*Stub/*FutureStub.
_GRPC_CLIENT_RE = re.compile(r'@GrpcClient\s*\(\s*"([^"]+)"')
_GRPC_STUB_FIELD_RE = re.compile(r"\b([A-Za-z_]\w*(?:BlockingStub|FutureStub|Stub))\b\s+[a-z_]\w*\s*[;=)]")
_NAME_ARG_RE = re.compile(r'(?:name|value)\s*=\s*"([^"]+)"')
_URL_ARG_RE = re.compile(r'url\s*=\s*"([^"]+)"')
_BASEURL_RE = re.compile(r'\.baseUrl\s*\(\s*"?([^")]+)"?\s*\)')

_TRIGGERS = ("@FeignClient", "WebClient", "RestTemplate", "RestClient", "OkHttpClient",
             "@GrpcClient", "Stub")


def parse_file(path: Path) -> list[dict]:
    raw = read_text(path)
    if not any(t in raw for t in _TRIGGERS):
        return []
    text = strip_comments(raw)
    cls = _CLASS_RE.search(text)
    class_name = cls.group(1) if cls else path.stem
    out: list[dict] = []

    for m in _FEIGN_RE.finditer(text):
        args = m.group(1)
        nm = _NAME_ARG_RE.search(args)
        um = _URL_ARG_RE.search(args)
        out.append({"name": class_name, "type": "feign", "file": str(path),
                    "target": (nm.group(1) if nm else (um.group(1) if um else ""))})

    # strip_comments теперь string-aware (сохраняет `//` в URL-литерале), поэтому ищем
    # baseUrl в очищенном тексте — заодно игнорируя закомментированные вызовы.
    def _base_url() -> str:
        bm = _BASEURL_RE.search(text)
        return bm.group(1).strip().strip('"') if bm else ""

    # WebClient: требуем токен WebClient (иначе RestClient.builder().baseUrl(...) ложно матчится).
    if _WEBCLIENT_FIELD_RE.search(text) or "WebClient.create" in text or (
            "WebClient" in text and ".baseUrl(" in text):
        out.append({"name": class_name, "type": "webclient", "file": str(path),
                    "target": _base_url()})

    if _REST_FIELD_RE.search(text):
        out.append({"name": class_name, "type": "resttemplate", "file": str(path), "target": ""})

    if _RESTCLIENT_FIELD_RE.search(text) or "RestClient.create" in text or "RestClient.builder" in text:
        out.append({"name": class_name, "type": "restclient", "file": str(path),
                    "target": _base_url()})

    if _OKHTTP_FIELD_RE.search(text) or "new OkHttpClient" in text:
        out.append({"name": class_name, "type": "okhttp", "file": str(path), "target": ""})

    grpc_targets = {m.group(1) for m in _GRPC_CLIENT_RE.finditer(text)}
    for tgt in sorted(grpc_targets):
        out.append({"name": class_name, "type": "grpc", "file": str(path), "target": tgt})
    if not grpc_targets and _GRPC_STUB_FIELD_RE.search(text):
        out.append({"name": class_name, "type": "grpc", "file": str(path), "target": ""})

    return out


def scan(root: Path) -> list[dict]:
    items: list[dict] = []
    for p in iter_java(Path(root)):
        items.extend(parse_file(p))
    items.sort(key=lambda d: (d["type"], d["name"]))
    return items
