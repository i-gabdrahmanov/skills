#!/usr/bin/env python3
"""Сканировать Java/Spring проект и сгенерировать MD-спецификацию.

Usage:
    scan.py <src-root> [-o <output.md>] [--title <text>]

Каждая диаграмма пишется в MD как `![](...svg)` ссылка на файл,
сгенерированный собственным Python SVG-рендером (без jar/Java).
PlantUML исходник складывается в свёрнутый <details> блок —
для редактирования вручную в IDE с PlantUML Integration плагином.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import endpoints  # noqa: E402
import kafka      # noqa: E402
import md_writer  # noqa: E402

_PROJECT_MARKERS = (
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "settings.gradle",
    "settings.gradle.kts",
)


def _project_name(src_root: Path) -> str:
    """Подняться от src_root до ближайшего предка с маркером проекта."""
    candidate = src_root
    for _ in range(8):
        if any((candidate / m).exists() for m in _PROJECT_MARKERS):
            return candidate.name
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return src_root.name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan a Java/Spring project and emit a Markdown spec with Mermaid diagrams."
    )
    parser.add_argument("src", help="path to source root (e.g. src/main/java or a module dir)")
    parser.add_argument(
        "-o",
        "--output",
        default="docs/spec.md",
        help="output markdown path (default: docs/spec.md)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="document title (default: project name from build.gradle/pom.xml)",
    )
    args = parser.parse_args()

    src_root = Path(args.src).resolve()
    if not src_root.exists():
        print(f"ERROR: source root not found: {src_root}", file=sys.stderr)
        return 1
    if src_root.is_file():
        src_root = src_root.parent

    controllers = endpoints.scan(src_root)
    kafka_model = kafka.scan(src_root)

    title = args.title or f"{_project_name(src_root)} — API & Kafka spec"
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    md, svgs = md_writer.build_document(title, controllers, kafka_model, out_path)

    svg_dir = out_path.parent / f"{out_path.stem}_diagrams"
    if svgs:
        svg_dir.mkdir(parents=True, exist_ok=True)
        for svg in svgs:
            (svg_dir / svg.svg_filename).write_text(svg.svg_content, encoding="utf-8")

    out_path.write_text(md, encoding="utf-8")
    eps = sum(len(c.endpoints) for c in controllers)
    print(
        f"OK: wrote {out_path}\n"
        f"  controllers: {len(controllers)}, endpoints: {eps}\n"
        f"  kafka topics: {len(kafka_model.topics)}, "
        f"producers: {len(kafka_model.producers)}, consumers: {len(kafka_model.consumers)}\n"
        f"  svg files: {len(svgs)} → {svg_dir}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
