"""Сборка markdown-документа со спецификацией.

Каждая диаграмма пишется как:
    ![title](path/to/diagram_NN.svg)
    <details><summary>PlantUML source</summary>
    ```plantuml ... ```
    </details>

SVG генерируется собственным рендером (`svg_render.py`), PlantUML
текст оставлен как fallback — для тех, кто хочет править исходник
в IDE с PlantUML Integration плагином.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from endpoints import Controller
from kafka import KafkaModel
import diagrams
import svg_render


@dataclass
class _DiagramOutput:
    svg_filename: str
    svg_content: str
    plantuml: str
    alt_text: str


@dataclass
class _BuildContext:
    """Контекст сборки: куда складывать SVG-файлы и под каким префиксом ссылаться."""
    svg_dir: Path
    rel_prefix: str  # как ссылаться из MD на файлы в svg_dir
    diagrams: list[_DiagramOutput] = field(default_factory=list)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    line_sep = "|" + "|".join(["---"] * len(headers)) + "|"
    head = "| " + " | ".join(headers) + " |"
    body = "\n".join("| " + " | ".join(_escape_cell(c) for c in r) + " |" for r in rows)
    return "\n".join([head, line_sep, body])


def _escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _diagram_block(ctx: _BuildContext, svg: str, plantuml_text: str, alt: str, slug: str) -> str:
    idx = len(ctx.diagrams) + 1
    filename = f"diagram_{idx:02d}_{slug}.svg"
    ctx.diagrams.append(
        _DiagramOutput(
            svg_filename=filename, svg_content=svg, plantuml=plantuml_text, alt_text=alt
        )
    )
    rel_path = f"{ctx.rel_prefix}/{filename}" if ctx.rel_prefix else filename
    return (
        f"![{alt}]({rel_path})\n"
        f"\n<details><summary>PlantUML source</summary>\n\n"
        f"```plantuml\n{plantuml_text}\n```\n\n"
        f"</details>"
    )


def _slugify(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "x"


def _build_endpoints_section(ctx: _BuildContext, controllers: list[Controller]) -> str:
    if not controllers:
        return ""
    parts: list[str] = ["## Endpoints", ""]
    rows: list[list[str]] = []
    for c in controllers:
        for e in c.endpoints:
            params = ", ".join(f"{p.kind}:{p.name}" for p in e.params) or "—"
            rows.append([e.http_method, e.path, c.class_name + "." + e.method_name, params])
    parts.append(_md_table(["Method", "Path", "Handler", "Params"], rows))
    parts.append("")
    for c in controllers:
        parts.append(f"### {c.class_name}")
        parts.append("")
        parts.append(f"_File: `{c.file}`_")
        parts.append("")
        if c.dependencies:
            dep_rows = [[name, t] for name, t in c.dependencies.items()]
            parts.append(_md_table(["Field", "Type"], dep_rows))
            parts.append("")
        svg = svg_render.endpoint_sequence_svg(c)
        puml = diagrams.endpoint_sequence(c)
        parts.append(
            _diagram_block(
                ctx, svg, puml, alt=f"{c.class_name} sequence",
                slug=_slugify(c.class_name),
            )
        )
        parts.append("")
    return "\n".join(parts)


def _build_kafka_section(ctx: _BuildContext, model: KafkaModel) -> str:
    if model.empty:
        return ""
    parts: list[str] = ["## Kafka", ""]
    topic_rows: list[list[str]] = []
    for topic in sorted(model.topics):
        producers = sorted({p.class_name for p in model.producers if topic in p.topics and p.class_name})
        consumers = sorted({c.class_name for c in model.consumers if topic in c.topics and c.class_name})
        topic_rows.append([topic, ", ".join(producers) or "—", ", ".join(consumers) or "—"])
    parts.append("### Topics")
    parts.append("")
    parts.append(_md_table(["Topic", "Producers", "Consumers"], topic_rows))
    parts.append("")
    if model.producers:
        parts.append("### Producers")
        parts.append("")
        prod_rows = [
            [p.class_name or "—", p.method or "—", ", ".join(p.topics), p.file]
            for p in model.producers
        ]
        parts.append(_md_table(["Class", "Method", "Topics", "File"], prod_rows))
        parts.append("")
    if model.consumers:
        parts.append("### Consumers")
        parts.append("")
        cons_rows = [
            [c.class_name or "—", c.method or "—", ", ".join(c.topics), c.group_id or "—", c.file]
            for c in model.consumers
        ]
        parts.append(_md_table(["Class", "Method", "Topics", "Group", "File"], cons_rows))
        parts.append("")
    parts.append("### Component diagram")
    parts.append("")
    parts.append(
        _diagram_block(
            ctx,
            svg_render.kafka_component_svg(model),
            diagrams.kafka_component(model),
            alt="Kafka component",
            slug="kafka_component",
        )
    )
    parts.append("")
    parts.append("### Sequence diagram")
    parts.append("")
    parts.append(
        _diagram_block(
            ctx,
            svg_render.kafka_sequence_svg(model),
            diagrams.kafka_sequence(model),
            alt="Kafka sequence",
            slug="kafka_sequence",
        )
    )
    parts.append("")
    return "\n".join(parts)


def build_document(
    title: str,
    controllers: list[Controller],
    kafka_model: KafkaModel,
    output_md: Path,
) -> tuple[str, list[_DiagramOutput]]:
    """Сформировать MD-текст + список SVG-файлов на запись.

    Файлы кладутся в `<output_md_stem>_diagrams/` рядом с MD.
    """
    svg_dir = output_md.parent / f"{output_md.stem}_diagrams"
    ctx = _BuildContext(svg_dir=svg_dir, rel_prefix=svg_dir.name)

    sections: list[str] = [f"# {title}", ""]
    eps_count = sum(len(c.endpoints) for c in controllers)
    summary = [
        f"- Controllers: **{len(controllers)}**, endpoints: **{eps_count}**",
        f"- Kafka topics: **{len(kafka_model.topics)}** "
        f"(producers: {len(kafka_model.producers)}, consumers: {len(kafka_model.consumers)})",
    ]
    sections.append("\n".join(summary))
    sections.append("")
    ep_section = _build_endpoints_section(ctx, controllers)
    if ep_section:
        sections.append(ep_section)
    kafka_section = _build_kafka_section(ctx, kafka_model)
    if kafka_section:
        sections.append(kafka_section)
    md = "\n".join(sections).rstrip() + "\n"
    return md, ctx.diagrams
