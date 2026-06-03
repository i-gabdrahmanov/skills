#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Phase A: package/path/name renames, longest-first
PACKAGE_RENAMES = [
    ('com.storage.storageservice', 'com.example.app'),
    ('com/storage/storageservice', 'com/example/app'),
    ('com.storage.springproxy', 'com.example.springproxy'),
    ('com/storage/springproxy', 'com/example/springproxy'),
    ('com.storage.proxy', 'com.example.proxy'),
    ('com/storage/proxy', 'com/example/proxy'),
    ('com.storage', 'com.example'),
    ('com/storage', 'com/example'),
    ('StorageService', 'ExampleService'),
    ('storageService', 'exampleService'),
    ('storage-service', 'example-service'),
    ('storage_service', 'example_service'),
    ('storageservice', 'exampleservice'),
]

# Phase B: line-level scan — delete or replace sensitive content in Java code
# Each pattern: if line matches, the string literal in that line gets emptied
SENSITIVE_LINE_PATTERNS = [
    re.compile(r'"[^"]*:\d{4,5}"'),           # "host:port" literals
    re.compile(r'"https?://[^"]*"'),           # URL literals
    re.compile(r'"/zookeeper/[^"]*"'),         # ZK path literals
    re.compile(r'getAddresses\s*\(\s*"'),      # XMemcached address calls
    re.compile(r'password\s*=\s*"[^"]*"', re.IGNORECASE),
    re.compile(r'secret\s*=\s*"[^"]*"', re.IGNORECASE),
    re.compile(r'token\s*=\s*"[^"]*"', re.IGNORECASE),
    re.compile(r'apiKey\s*=\s*"[^"]*"', re.IGNORECASE),
    re.compile(r'jdbc:\w+://'),
    # @Value with sensitive defaults (localhost, URLs, IPs)
    re.compile(r'@Value.*localhost'),
    re.compile(r'@Value.*https?://'),
    re.compile(r'@Value.*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'),
    # General: any line with localhost or IP outside imports/class names
    re.compile(r'(?<!import )(?<!class )\blocalhost\b'),
    re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
]

# Specific replacements for known patterns (applied before generic fallback)
LITERAL_REPLACEMENTS = [
    (re.compile(r'"memcached:\d+"'), '""'),
    (re.compile(r'"zookeeper:\d+"'), '""'),
    (re.compile(r'"redis:\d+"'), '""'),
    (re.compile(r'"kafka:\d+"'), '""'),
    (re.compile(r'"https?://[^"]*"'), '""'),
    (re.compile(r'"jdbc:\w+://[^"]*"'), '""'),
    (re.compile(r'"/zookeeper/[^"]*"'), '""'),
    (re.compile(r'"[a-zA-Z][\w.-]*:\d{4,5}"'), '""'),
    # @Value defaults: remove default value, keep property key
    (re.compile(r'(\$\{[^}:]*):https?://[^}]*(})'), r'\1\2'),
    (re.compile(r'(\$\{[^}:]*):localhost[^}]*(})'), r'\1\2'),
    (re.compile(r'(\$\{[^}:]*:)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}[^}]*(})'), r'\1\2'),
]

NEVER_DELETE = [
    re.compile(r'^\s*//===FILE:'),
    re.compile(r'^\s*//===END_FILE'),
    re.compile(r'^\s*//===MERGED'),
]


def sanitize_packages(text: str) -> tuple[str, dict[str, int]]:
    total = 0
    for old, new in PACKAGE_RENAMES:
        n = text.count(old)
        if n > 0:
            text = text.replace(old, new)
            total += n
    return text, {'packages': total} if total else (text, {})


def sanitize_java_lines(text: str) -> tuple[str, dict[str, int]]:
    lines = text.split('\n')
    result: list[str] = []
    cleaned = 0

    for line in lines:
        if any(p.search(line) for p in NEVER_DELETE):
            result.append(line)
            continue

        hit = any(p.search(line) for p in SENSITIVE_LINE_PATTERNS)
        if not hit:
            result.append(line)
            continue

        modified = line
        replaced = False
        for pattern, replacement in LITERAL_REPLACEMENTS:
            modified, n = pattern.subn(replacement, modified)
            if n > 0:
                replaced = True

        if replaced:
            result.append(modified)
            cleaned += 1
        else:
            result.append(line)

    return '\n'.join(result), {'sensitive_literals': cleaned}


def sanitize(text: str) -> tuple[str, dict[str, int]]:
    all_counts: dict[str, int] = {}

    text, c = sanitize_packages(text)
    all_counts.update(c)

    text, c = sanitize_java_lines(text)
    all_counts.update(c)

    return text, all_counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Sanitize merged Java code: remove sensitive literals, rename packages'
    )
    parser.add_argument('input_file', type=Path, help='Path to merged file')
    parser.add_argument('-o', '--output', type=Path, required=True, help='Output sanitized file')
    parser.add_argument('--dry-run', action='store_true', help='Show counts without writing')
    args = parser.parse_args()

    if not args.input_file.is_file():
        print(f'Error: {args.input_file} not found', file=sys.stderr)
        return 1

    text = args.input_file.read_text(encoding='utf-8', errors='replace')
    sanitized, counts = sanitize(text)

    total = sum(counts.values())
    print(f'Sanitization report ({total} total actions):')
    for cat, n in counts.items():
        print(f'  {cat:>20}: {n}')

    if args.dry_run:
        print('\nDry run — no file written.')
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sanitized, encoding='utf-8')
    print(f'\nSanitized output -> {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
