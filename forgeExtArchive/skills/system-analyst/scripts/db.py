"""Детерминированный сканер схемы БД.

Источник 1 — миграции: Flyway (db/migration/V*.sql) и Liquibase (createTable в xml/yaml/sql).
Источник 2 (fallback, если миграций нет) — имена таблиц из @Table(name=...) и @Entity-классов.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import iter_files, iter_java, read_text, strip_comments

_CREATE_TABLE_SQL = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"']?([A-Za-z_][\w.]*)", re.IGNORECASE)
_CREATE_TABLE_LB = re.compile(r'createTable\s+tableName\s*=\s*"([^"]+)"|<createTable[^>]*tableName="([^"]+)"', re.IGNORECASE)
# YAML-формат Liquibase: `- createTable:` … `tableName: foo` (имя без кавычек или в кавычках).
_CREATE_TABLE_LB_YAML = re.compile(r'tableName\s*:\s*["\']?([A-Za-z_][\w.]*)', re.IGNORECASE)
_TABLE_ANNO = re.compile(r'@Table\s*\([^)]*name\s*=\s*"([^"]+)"')
_ENTITY_CLASS = re.compile(r"@Entity\b[\s\S]{0,400}?\bclass\s+([A-Za-z_]\w*)")


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def scan(root: Path) -> dict:
    root = Path(root)
    tables: dict[str, str] = {}   # table_name -> source
    migration_count = 0
    tool = "none"

    for p in iter_files(root, (".sql", ".xml", ".yaml", ".yml")):
        low = str(p).lower()
        if "migration" in low or "changelog" in low or "db/changelog" in low or "/db/" in low:
            text = read_text(p)
            if p.suffix == ".sql":
                if "/migration/" in low or re.match(r"^[vV]\d", p.name):
                    migration_count += 1
                    tool = "flyway"
                for m in _CREATE_TABLE_SQL.finditer(text):
                    tables.setdefault(m.group(1).split(".")[-1], "flyway")
            else:
                if "changelog" in low:
                    tool = "liquibase"
                    migration_count += 1
                if p.suffix in (".yaml", ".yml"):
                    # YAML: ловим tableName только внутри блока createTable (иначе addColumn/
                    # createIndex с tableName дали бы фантомные таблицы).
                    for cm in re.finditer(r"createTable\s*:", text, re.IGNORECASE):
                        nm = _CREATE_TABLE_LB_YAML.search(text, cm.end(), cm.end() + 200)
                        if nm:
                            tables.setdefault(nm.group(1).split(".")[-1], "liquibase")
                else:
                    for m in _CREATE_TABLE_LB.finditer(text):
                        name = m.group(1) or m.group(2)
                        if name:
                            tables.setdefault(name, "liquibase")

    if not tables:  # fallback: из @Table / @Entity
        for p in iter_java(root):
            raw = read_text(p)
            if "@Entity" not in raw and "@Table" not in raw:
                continue
            text = strip_comments(raw)
            found = False
            for m in _TABLE_ANNO.finditer(text):
                tables.setdefault(m.group(1), "annotation")
                found = True
            if not found:
                for m in _ENTITY_CLASS.finditer(text):
                    tables.setdefault(_camel_to_snake(m.group(1)), "entity-name")

    return {
        "migration_tool": tool,
        "migration_count": migration_count,
        "tables": sorted(tables.keys()),
        "table_sources": tables,
    }


def scan_items(root: Path) -> list[dict]:
    res = scan(root)
    return [{"name": t, "source": res["table_sources"][t]} for t in res["tables"]]
