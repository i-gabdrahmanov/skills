"""Генераторы PlantUML диаграмм по результатам парсинга.

PlantUML текст оставлен как человекочитаемый формат (его удобно
ревьюить в git, редактировать в IDE с PlantUML Integration плагином).
А визуальный рендер делает свой Python-движок в `svg_render.py` —
там диаграммы строятся напрямую из AST `Controller`/`KafkaModel`,
без парсинга PlantUML и без jar.
"""

from __future__ import annotations

import re

from endpoints import Controller
from kafka import KafkaModel


def _safe_alias(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    return s or "X"


def endpoint_sequence(controller: Controller) -> str:
    lines: list[str] = ["@startuml", "skinparam responseMessageBelowArrow true", ""]
    lines.append("actor Client")
    lines.append(f'participant "{controller.class_name}" as Ctrl')
    used: dict[str, str] = {}
    for e in controller.endpoints:
        for field_name, _ in e.calls:
            if field_name in controller.dependencies:
                used[field_name] = controller.dependencies[field_name]
    aliases: dict[str, str] = {}
    for field_name, type_name in used.items():
        alias = _safe_alias(type_name)
        if alias in aliases.values():
            alias = _safe_alias(field_name)
        aliases[field_name] = alias
        lines.append(f'participant "{type_name}" as {alias}')
    lines.append("")
    for e in controller.endpoints:
        param_summary = ", ".join(p.name for p in e.params)
        lines.append(f"== {e.http_method} {e.path} ==")
        msg = e.method_name + (f"({param_summary})" if param_summary else "()")
        lines.append(f"Client -> Ctrl: {msg}")
        for field_name, call_method in e.calls:
            alias = aliases.get(field_name)
            if not alias:
                continue
            lines.append(f"Ctrl -> {alias}: {call_method}()")
            lines.append(f"{alias} --> Ctrl")
        ret = e.return_type.replace("ResponseEntity<", "").rstrip(">") or "void"
        lines.append(f"Ctrl --> Client: {ret}")
        lines.append("")
    lines.append("@enduml")
    return "\n".join(lines)


def kafka_component(model: KafkaModel) -> str:
    lines: list[str] = [
        "@startuml",
        "!pragma layout smetana",
        "skinparam componentStyle rectangle",
        "",
    ]
    services: set[str] = set()
    for c in model.consumers:
        if c.class_name:
            services.add(c.class_name)
    for p in model.producers:
        if p.class_name:
            services.add(p.class_name)
    for svc in sorted(services):
        lines.append(f'component "{svc}" as {_safe_alias(svc)}')
    for topic in sorted(model.topics):
        lines.append(f'queue "{topic}" as {_safe_alias("topic_" + topic)}')
    lines.append("")
    seen: set[tuple[str, str, str]] = set()
    for p in model.producers:
        for topic in p.topics:
            key = (p.class_name, "->", topic)
            if not p.class_name or key in seen:
                continue
            seen.add(key)
            lines.append(
                f'{_safe_alias(p.class_name)} --> {_safe_alias("topic_" + topic)} : produce'
            )
    for c in model.consumers:
        for topic in c.topics:
            key = (topic, "->", c.class_name)
            if not c.class_name or key in seen:
                continue
            seen.add(key)
            label = "consume"
            if c.group_id:
                label += f" ({c.group_id})"
            lines.append(
                f'{_safe_alias("topic_" + topic)} --> {_safe_alias(c.class_name)} : {label}'
            )
    lines.append("")
    lines.append("@enduml")
    return "\n".join(lines)


def kafka_sequence(model: KafkaModel) -> str:
    lines: list[str] = ["@startuml", "skinparam responseMessageBelowArrow true", ""]
    by_topic_p: dict[str, list[tuple[str, str]]] = {}
    by_topic_c: dict[str, list[tuple[str, str]]] = {}
    for p in model.producers:
        for t in p.topics:
            by_topic_p.setdefault(t, []).append((p.class_name, p.method))
    for c in model.consumers:
        for t in c.topics:
            by_topic_c.setdefault(t, []).append((c.class_name, c.method))
    topics = sorted(set(by_topic_p) | set(by_topic_c))
    producers_seen: set[str] = set()
    consumers_seen: set[str] = set()
    for t in topics:
        for cls, _ in by_topic_p.get(t, []):
            if cls:
                producers_seen.add(cls)
        for cls, _ in by_topic_c.get(t, []):
            if cls:
                consumers_seen.add(cls)
    for cls in sorted(producers_seen):
        lines.append(f'participant "{cls}" as {_safe_alias(cls)}')
    lines.append('queue "Kafka" as Kafka')
    for cls in sorted(consumers_seen):
        if cls in producers_seen:
            continue
        lines.append(f'participant "{cls}" as {_safe_alias(cls)}')
    lines.append("")
    for t in topics:
        lines.append(f"== {t} ==")
        for cls, method in by_topic_p.get(t, []):
            if not cls:
                continue
            label = f"{method}()" if method else "send"
            lines.append(f"{_safe_alias(cls)} -> Kafka: send({t}, {label})")
        for cls, method in by_topic_c.get(t, []):
            if not cls:
                continue
            handler = f"{method}()" if method else "onMessage"
            lines.append(f"Kafka -> {_safe_alias(cls)}: {handler}")
        lines.append("")
    lines.append("@enduml")
    return "\n".join(lines)
