#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

FILE_START_RE = re.compile(r'^//===FILE:\s*(.+?)\s*===//\s*$')
FILE_END_RE = re.compile(r'^//===END_FILE===//\s*$')


def parse_merged(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    current_path: str | None = None
    current_lines: list[str] = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            if current_path is None:
                m = FILE_START_RE.match(line)
                if m:
                    current_path = m.group(1)
                    current_lines = []
            else:
                if FILE_END_RE.match(line):
                    entries.append((current_path, ''.join(current_lines)))
                    current_path = None
                    current_lines = []
                else:
                    current_lines.append(line)

    if current_path is not None:
        raise ValueError(
            f'Unclosed file block: {current_path} (missing //===END_FILE===//)'
        )

    return entries


def validate_path(rel_path: str) -> None:
    if os.path.isabs(rel_path):
        raise ValueError(f'Absolute path not allowed: {rel_path}')
    if '..' in Path(rel_path).parts:
        raise ValueError(f'Path traversal not allowed: {rel_path}')


def write_files(entries: list[tuple[str, str]], output: Path) -> int:
    total_bytes = 0
    for rel_path, content in entries:
        validate_path(rel_path)
        target = output / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        total_bytes += len(content.encode('utf-8'))
    return total_bytes


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Split a merged project file back into directory structure'
    )
    parser.add_argument(
        'merged_files', type=Path, nargs='+',
        help='Path(s) to merged file(s) — supports multiple parts',
    )
    parser.add_argument('-o', '--output', type=Path, required=True, help='Output directory')
    parser.add_argument('--force', action='store_true', help='Write into non-empty directory')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be created')
    args = parser.parse_args()

    entries: list[tuple[str, str]] = []
    for mf in sorted(args.merged_files):
        if not mf.is_file():
            print(f'Error: {mf} not found', file=sys.stderr)
            return 1
        try:
            entries.extend(parse_merged(mf))
        except ValueError as e:
            print(f'Parse error in {mf.name}: {e}', file=sys.stderr)
            return 1

    if not entries:
        print('No file blocks found in merged file(s).', file=sys.stderr)
        return 1

    print(f'Found {len(entries)} files across {len(args.merged_files)} part(s)')

    if args.dry_run:
        for rel_path, content in entries:
            lines = content.count('\n')
            print(f'  {rel_path} ({lines} lines)')
        return 0

    if args.output.exists() and any(args.output.iterdir()):
        if not args.force:
            print(
                f'Error: {args.output} is not empty. Use --force to overwrite.',
                file=sys.stderr,
            )
            return 1

    args.output.mkdir(parents=True, exist_ok=True)

    try:
        for rel_path, _ in entries:
            validate_path(rel_path)
    except ValueError as e:
        print(f'Security error: {e}', file=sys.stderr)
        return 1

    total_bytes = write_files(entries, args.output)
    print(f'Extracted {len(entries)} files ({total_bytes:,} bytes) -> {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
