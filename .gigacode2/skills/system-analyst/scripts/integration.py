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
_NAME_ARG_RE = re.compile(r'(?:name|value)\s*=\s*"([^"]+)"')
_URL_ARG_RE = re.compile(r'url\s*=\s*"([^"]+)"')
_BASEURL_RE = re.compile(r'\.baseUrl\s*\(\s*"?([^")]+)"?\s*\)')


def parse_file(path: Path) -> list[dict]:
    raw = read_text(path)
    if not any(t in raw for t in ("@FeignClient", "WebClient", "RestTemplate")):
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

    if _WEBCLIENT_FIELD_RE.search(text) or ".baseUrl(" in text or "WebClient.create" in text:
        bm = _BASEURL_RE.search(text)
        out.append({"name": class_name, "type": "webclient", "file": str(path),
                    "target": bm.group(1).strip() if bm else ""})

    if _REST_FIELD_RE.search(text):
        out.append({"name": class_name, "type": "resttemplate", "file": str(path), "target": ""})

    return out


def scan(root: Path) -> list[dict]:
    items: list[dict] = []
    for p in iter_java(Path(root)):
        items.extend(parse_file(p))
    items.sort(key=lambda d: (d["type"], d["name"]))
    return items
