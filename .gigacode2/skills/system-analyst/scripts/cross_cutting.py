"""Детерминированный сканер сквозных аспектов:
@Aspect, фильтры (OncePerRequestFilter/Filter/HandlerInterceptor), @Scheduled, @EventListener.
Категория ADVISORY в gate.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import iter_java, read_text, strip_comments
from endpoints import _find_method_signature

_CLASS_RE = re.compile(r"\b(?:class|interface)\s+([A-Za-z_]\w*)")


def _enclosing_class(text: str, pos: int) -> str:
    last = ""
    for m in _CLASS_RE.finditer(text):
        if m.start() < pos:
            last = m.group(1)
        else:
            break
    return last


def parse_file(path: Path) -> list[dict]:
    raw = read_text(path)
    markers = ("@Aspect", "OncePerRequestFilter", "HandlerInterceptor", "@Scheduled",
               "@EventListener", "implements Filter", "GenericFilterBean")
    if not any(t in raw for t in markers):
        return []
    text = strip_comments(raw)
    out: list[dict] = []
    cls = _CLASS_RE.search(text)
    class_name = cls.group(1) if cls else path.stem

    if "@Aspect" in text:
        out.append({"kind": "aspect", "class": class_name, "method": "", "file": str(path)})
    if any(t in text for t in ("OncePerRequestFilter", "HandlerInterceptor", "implements Filter", "GenericFilterBean")):
        kind = "interceptor" if "HandlerInterceptor" in text else "filter"
        out.append({"kind": kind, "class": class_name, "method": "", "file": str(path)})

    for marker, kind in (("@Scheduled", "scheduled"), ("@EventListener", "event_listener")):
        for m in re.finditer(re.escape(marker) + r"\b", text):
            j = m.end()
            if j < len(text) and text[j] == "(":
                depth, k = 0, j
                while k < len(text):
                    if text[k] == "(":
                        depth += 1
                    elif text[k] == ")":
                        depth -= 1
                        if depth == 0:
                            j = k + 1
                            break
                    k += 1
            sig = _find_method_signature(text, j, kotlin=path.suffix == ".kt")
            out.append({"kind": kind, "class": _enclosing_class(text, m.start()),
                        "method": sig[3] if sig else "", "file": str(path)})
    return out


def scan(root: Path) -> list[dict]:
    items: list[dict] = []
    for p in iter_java(Path(root)):
        items.extend(parse_file(p))
    items.sort(key=lambda d: (d["kind"], d["class"], d["method"]))
    return items
