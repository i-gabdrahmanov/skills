#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

FILE_START_RE = re.compile(r'^//===FILE:\s*(.+?)\s*===//\s*$')
FILE_END_RE = re.compile(r'^//===END_FILE===//\s*$')


# ---------------------------------------------------------------------------
# 1. Parse merged file
# ---------------------------------------------------------------------------

def parse_merged(paths: list[Path]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for p in sorted(paths):
        current: str | None = None
        lines: list[str] = []
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if current is None:
                    m = FILE_START_RE.match(line)
                    if m:
                        current = m.group(1)
                        lines = []
                else:
                    if FILE_END_RE.match(line):
                        entries.append((current, ''.join(lines)))
                        current = None
                        lines = []
                    else:
                        lines.append(line)
        if current is not None:
            raise ValueError(f'Unclosed file block: {current}')
    return entries


# ---------------------------------------------------------------------------
# 2. Detect modules from file paths
# ---------------------------------------------------------------------------

def detect_modules(entries: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    modules: dict[str, list[tuple[str, str]]] = {}
    for rel_path, content in entries:
        parts = Path(rel_path).parts
        try:
            src_idx = parts.index('src')
        except ValueError:
            modules.setdefault('root', []).append((rel_path, content))
            continue
        if src_idx == 0:
            modules.setdefault('root', []).append((rel_path, content))
        else:
            module_name = '/'.join(parts[:src_idx])
            local_path = '/'.join(parts[src_idx:])
            modules.setdefault(module_name, []).append((local_path, content))
    return modules


# ---------------------------------------------------------------------------
# 3. Parse tech-stack.md
# ---------------------------------------------------------------------------

def parse_tech_stack(path: Path) -> dict:
    text = path.read_text(encoding='utf-8', errors='replace')
    result: dict = {
        'java_version': '21',
        'plugins': [],
        'deps': {},  # module -> [(scope, coordinate)]
    }

    m = re.search(r'\*\*Java\*\*:\s*(\d+)', text)
    if m:
        result['java_version'] = m.group(1)

    for m in re.finditer(r'^- (.+)$', text, re.MULTILINE):
        line = m.group(1).strip()

        dep_match = re.match(r'`([^`]+)`\s+\((\w+)\)\s+\[([^\]]+)\]', line)
        if dep_match:
            coord, scope, module = dep_match.group(1), dep_match.group(2), dep_match.group(3)
            result['deps'].setdefault(module, []).append((scope, coord))
            continue

        plugin_match = re.match(r'([\w.-]+)\s+([\d.]+)', line)
        if plugin_match:
            result['plugins'].append((plugin_match.group(1), plugin_match.group(2)))

    return result


# ---------------------------------------------------------------------------
# 4. Detect base package from Java files
# ---------------------------------------------------------------------------

def detect_base_package(entries: list[tuple[str, str]]) -> str:
    for _, content in entries:
        m = re.search(r'^\s*package\s+([\w.]+)\s*;', content, re.MULTILINE)
        if m:
            pkg = m.group(1)
            parts = pkg.split('.')
            if len(parts) >= 2:
                return '.'.join(parts[:2])
            return pkg
    return 'com.example'


# ---------------------------------------------------------------------------
# 5. Generate build files
# ---------------------------------------------------------------------------

def generate_root_build_gradle(tech: dict, module_name: str = 'root') -> str:
    lines = ['plugins {']
    for pid, ver in tech['plugins']:
        lines.append(f"    id '{pid}' version '{ver}'")
    if not any(p[0] == 'java' for p in tech['plugins']):
        lines.append("    id 'java'")
    lines.append('}')
    lines.append('')
    lines.append("group = 'com.example'")
    lines.append("version = '0.0.1-SNAPSHOT'")
    lines.append('')
    lines.append('java {')
    lines.append('    toolchain {')
    lines.append(f'        languageVersion = JavaLanguageVersion.of({tech["java_version"]})')
    lines.append('    }')
    lines.append('}')
    lines.append('')
    lines.append('configurations {')
    lines.append('    compileOnly {')
    lines.append('        extendsFrom annotationProcessor')
    lines.append('    }')
    lines.append('}')
    lines.append('')
    lines.append('repositories {')
    lines.append('    mavenCentral()')
    lines.append('}')
    lines.append('')

    deps = tech['deps'].get(module_name, [])
    if deps:
        lines.append('dependencies {')
        for scope, coord in sorted(deps, key=lambda x: (x[0], x[1])):
            lines.append(f"    {scope} '{coord}'")
        lines.append('')
        lines.append("    testImplementation 'org.springframework.boot:spring-boot-starter-test'")
        lines.append('}')
    else:
        lines.append('dependencies {')
        lines.append("    testImplementation 'org.springframework.boot:spring-boot-starter-test'")
        lines.append('}')

    lines.append('')
    return '\n'.join(lines)


def generate_submodule_build_gradle(tech: dict, module_name: str) -> str:
    deps = tech['deps'].get(module_name, [])

    is_micronaut = any('micronaut' in c for _, c in deps)

    lines = ['plugins {']
    if is_micronaut:
        lines.append("    id 'java'")
        lines.append("    id 'application'")
    else:
        for pid, ver in tech['plugins']:
            lines.append(f"    id '{pid}' version '{ver}'")
        if not any(p[0] == 'java' for p in tech['plugins']):
            lines.append("    id 'java'")
    lines.append('}')
    lines.append('')
    lines.append("group = 'com.example'")
    lines.append("version = '0.0.1-SNAPSHOT'")
    lines.append('')
    lines.append(f'java {{')
    lines.append('    toolchain {')
    lines.append(f'        languageVersion = JavaLanguageVersion.of({tech["java_version"]})')
    lines.append('    }')
    lines.append('}')
    lines.append('')
    lines.append('repositories {')
    lines.append('    mavenCentral()')
    lines.append('}')
    lines.append('')

    if deps:
        lines.append('dependencies {')
        for scope, coord in sorted(deps, key=lambda x: (x[0], x[1])):
            lines.append(f"    {scope} '{coord}'")
        lines.append('}')
    lines.append('')
    return '\n'.join(lines)


def generate_settings_gradle(project_name: str, submodules: list[str]) -> str:
    lines = [f"rootProject.name = '{project_name}'"]
    for m in sorted(submodules):
        lines.append(f"include '{m}'")
    return '\n'.join(lines) + '\n'


def generate_application_yml() -> str:
    return """spring:
  application:
    name: app
"""


# ---------------------------------------------------------------------------
# 6. Write project
# ---------------------------------------------------------------------------

def validate_path(rel_path: str) -> None:
    if os.path.isabs(rel_path):
        raise ValueError(f'Absolute path: {rel_path}')
    if '..' in Path(rel_path).parts:
        raise ValueError(f'Path traversal: {rel_path}')


def write_project(
    output: Path,
    modules: dict[str, list[tuple[str, str]]],
    tech: dict,
    project_name: str,
) -> int:
    file_count = 0
    submodules = [m for m in modules if m != 'root']

    # Java source files
    for module, entries in modules.items():
        for rel_path, content in entries:
            validate_path(rel_path)
            if module == 'root':
                target = output / rel_path
            else:
                target = output / module / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            file_count += 1

    # Root build.gradle
    bg = generate_root_build_gradle(tech, 'root')
    (output / 'build.gradle').write_text(bg, encoding='utf-8')
    file_count += 1

    # settings.gradle
    sg = generate_settings_gradle(project_name, submodules)
    (output / 'settings.gradle').write_text(sg, encoding='utf-8')
    file_count += 1

    # Submodule build.gradle files
    for m in submodules:
        bg = generate_submodule_build_gradle(tech, m)
        mod_dir = output / m
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / 'build.gradle').write_text(bg, encoding='utf-8')
        file_count += 1

    # Minimal application.yml for root
    res_dir = output / 'src' / 'main' / 'resources'
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / 'application.yml').write_text(generate_application_yml(), encoding='utf-8')
    file_count += 1

    # application.yml for Spring Boot submodules (skip Micronaut ones)
    for m in submodules:
        sub_deps = tech['deps'].get(m, [])
        is_micronaut = any('micronaut' in c for _, c in sub_deps)
        if not is_micronaut:
            sub_res = output / m / 'src' / 'main' / 'resources'
            sub_res.mkdir(parents=True, exist_ok=True)
            (sub_res / 'application.yml').write_text(generate_application_yml(), encoding='utf-8')
            file_count += 1

    return file_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Assemble a buildable project from merged Java file + tech-stack.md'
    )
    parser.add_argument(
        'merged_files', type=Path, nargs='+',
        help='Merged Java file(s) from project-packer',
    )
    parser.add_argument(
        '-t', '--tech-stack', type=Path, required=True,
        help='Path to tech-stack.md',
    )
    parser.add_argument('-o', '--output', type=Path, required=True, help='Output project directory')
    parser.add_argument('--name', type=str, default=None, help='Project name (auto-detected if omitted)')
    parser.add_argument('--force', action='store_true', help='Write into non-empty directory')
    args = parser.parse_args()

    for f in args.merged_files:
        if not f.is_file():
            print(f'Error: {f} not found', file=sys.stderr)
            return 1
    if not args.tech_stack.is_file():
        print(f'Error: {args.tech_stack} not found', file=sys.stderr)
        return 1

    if args.output.exists() and any(args.output.iterdir()) and not args.force:
        print(f'Error: {args.output} is not empty. Use --force.', file=sys.stderr)
        return 1

    # Parse inputs
    entries = parse_merged(args.merged_files)
    if not entries:
        print('No files found in merged archive.', file=sys.stderr)
        return 1

    tech = parse_tech_stack(args.tech_stack)
    modules = detect_modules(entries)

    project_name = args.name
    if not project_name:
        # Try to get from merged header
        with open(args.merged_files[0], 'r') as f:
            first_line = f.readline()
            m = re.search(r'MERGED_PROJECT:\s*(\S+)', first_line)
            if m:
                project_name = m.group(1)
            else:
                project_name = 'assembled-project'

    print(f'Assembling project: {project_name}')
    print(f'  Java files: {len(entries)}')
    print(f'  Modules: {", ".join(modules.keys())}')
    print(f'  Java version: {tech["java_version"]}')
    print(f'  Dependencies: {sum(len(v) for v in tech["deps"].values())}')

    args.output.mkdir(parents=True, exist_ok=True)
    file_count = write_project(args.output, modules, tech, project_name)

    print(f'\nAssembled {file_count} files -> {args.output}')
    print(f'\nTo build:')
    print(f'  cd {args.output}')
    print(f'  gradle wrapper')
    print(f'  ./gradlew build')
    return 0


if __name__ == '__main__':
    sys.exit(main())
