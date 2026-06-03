#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = {
    '.git', 'target', 'build', '.gradle', '.venv', '.idea',
    'node_modules', '__pycache__', '.gigacode', '.gigaide', '.mvn',
    'ground',  # каталог данных скиллов (scan-JSON, pipeline-state) — не упаковывать
}

MAX_CHUNK_BYTES = 3 * 1024 * 1024  # 3 MB


def collect_java_files(root: Path) -> list[Path]:
    result: list[Path] = []
    root_str = str(root)
    for dirpath, dirnames, files in os.walk(root_str):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        dirnames.sort()
        parts = Path(os.path.relpath(dirpath, root_str)).parts
        if 'test' in parts or 'tests' in parts:
            continue
        for fname in sorted(files):
            if fname.endswith('.java'):
                result.append(Path(dirpath) / fname)
    return result


def find_gradle_files(root: Path) -> list[Path]:
    result = []
    for name in ('build.gradle', 'build.gradle.kts'):
        top = root / name
        if top.exists():
            result.append(top)
    for dirpath, dirnames, files in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for fname in files:
            if fname in ('build.gradle', 'build.gradle.kts'):
                p = Path(dirpath) / fname
                if p not in result:
                    result.append(p)
    return result


def extract_tech_stack(root: Path) -> str:
    gradle_files = find_gradle_files(root)
    plugins: list[str] = []
    deps: list[str] = []
    java_ver = ''

    for gf in gradle_files:
        text = gf.read_text(encoding='utf-8', errors='replace')
        module = gf.parent.relative_to(root).as_posix()
        if module == '.':
            module = 'root'

        for m in re.finditer(r"id\s+['\"]([^'\"]+)['\"]\s+version\s+['\"]([^'\"]+)['\"]", text):
            plugins.append(f'- {m.group(1)} {m.group(2)}')

        for m in re.finditer(
            r"(implementation|compileOnly|runtimeOnly|annotationProcessor)"
            r"\s+['\"]([^'\"]+)['\"]",
            text,
        ):
            scope, coord = m.group(1), m.group(2)
            if 'test' not in scope.lower():
                deps.append(f'- `{coord}` ({scope}) [{module}]')

        for m in re.finditer(
            r"(implementation|compileOnly|runtimeOnly|annotationProcessor)"
            r"\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            text,
        ):
            scope, coord = m.group(1), m.group(2)
            if 'test' not in scope.lower():
                entry = f'- `{coord}` ({scope}) [{module}]'
                if entry not in deps:
                    deps.append(entry)

        m = re.search(r'JavaLanguageVersion\.of\((\d+)\)', text)
        if m:
            java_ver = m.group(1)

    lines = ['# Tech Stack', '']
    if java_ver:
        lines += [f'**Java**: {java_ver}', '']
    if plugins:
        lines += ['## Plugins', ''] + sorted(set(plugins)) + ['']
    if deps:
        lines += ['## Dependencies', ''] + sorted(set(deps)) + ['']

    return '\n'.join(lines)


def format_block(rel_path: str, content: str) -> str:
    block = f'//===FILE: {rel_path}===//\n'
    block += content
    if content and not content.endswith('\n'):
        block += '\n'
    block += '//===END_FILE===//\n'
    return block


def make_header(project_name: str, total_files: int, part: int, total_parts: int) -> str:
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    if total_parts > 1:
        return f'//===MERGED_PROJECT: {project_name} | {ts} | {total_files} files | part {part}/{total_parts}===//\n\n'
    return f'//===MERGED_PROJECT: {project_name} | {ts} | {total_files} files===//\n\n'


def chunk_path(output: Path, part: int, total_parts: int) -> Path:
    if total_parts == 1:
        return output
    return output.with_name(f'{output.stem}-part{part}{output.suffix}')


def merge_chunked(
    files: list[Path], root: Path, output: Path, max_bytes: int,
) -> tuple[int, int, int]:
    project_name = root.resolve().name
    blocks: list[tuple[str, int]] = []

    for fpath in files:
        rel = fpath.relative_to(root).as_posix()
        try:
            content = fpath.read_text(encoding='utf-8', errors='replace')
        except OSError as e:
            print(f'  SKIP {rel}: {e}', file=sys.stderr)
            continue
        block = format_block(rel, content)
        lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        blocks.append((block, lines))

    chunks: list[list[int]] = [[]]
    chunk_sizes: list[int] = [0]
    hdr_size = len(make_header(project_name, len(blocks), 1, 1).encode('utf-8'))

    for i, (block, _) in enumerate(blocks):
        bsize = len(block.encode('utf-8')) + 1
        if chunk_sizes[-1] + bsize > max_bytes and chunks[-1]:
            chunks.append([])
            chunk_sizes.append(hdr_size)
        if not chunks[-1]:
            chunk_sizes[-1] = hdr_size
        chunks[-1].append(i)
        chunk_sizes[-1] += bsize

    total_parts = len(chunks)
    total_lines = 0
    output.parent.mkdir(parents=True, exist_ok=True)

    for part_num, idxs in enumerate(chunks, 1):
        path = chunk_path(output, part_num, total_parts)
        with open(path, 'w', encoding='utf-8') as out:
            out.write(make_header(project_name, len(blocks), part_num, total_parts))
            for j, idx in enumerate(idxs):
                block, lines = blocks[idx]
                total_lines += lines
                out.write(block)
                if j < len(idxs) - 1:
                    out.write('\n')

    return len(blocks), total_lines, total_parts


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Merge Java source files + extract tech stack'
    )
    parser.add_argument('project_root', type=Path, help='Path to project root')
    parser.add_argument('-o', '--output', type=Path, required=True, help='Output file path')
    parser.add_argument('--max-size', type=int, default=MAX_CHUNK_BYTES,
                        help='Max bytes per chunk (default: 3MB)')
    args = parser.parse_args()

    root = args.project_root.resolve()
    if not root.is_dir():
        print(f'Error: {root} is not a directory', file=sys.stderr)
        return 1

    files = collect_java_files(root)
    if not files:
        print('No Java files found.', file=sys.stderr)
        return 1

    file_count, total_lines, total_parts = merge_chunked(
        files, root, args.output, args.max_size,
    )

    # Tech stack file next to output
    tech_path = args.output.with_name('tech-stack.md')
    tech_content = extract_tech_stack(root)
    tech_path.write_text(tech_content, encoding='utf-8')

    if total_parts == 1:
        print(f'Merged {file_count} Java files ({total_lines} lines) -> {args.output}')
    else:
        print(f'Merged {file_count} Java files ({total_lines} lines) -> {total_parts} parts')
        for p in range(1, total_parts + 1):
            path = chunk_path(args.output, p, total_parts)
            size_kb = path.stat().st_size / 1024
            print(f'  {path.name} ({size_kb:.0f} KB)')

    print(f'Tech stack -> {tech_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
