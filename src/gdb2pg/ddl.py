"""Генерация PostgreSQL DDL из модели схемы (M3)."""

from __future__ import annotations

import re

from .catalog import Schema, Table


PG_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validate_pg_ident(name: str) -> str:
    if not PG_IDENT_RE.fullmatch(name):
        raise ValueError(f"unsafe PostgreSQL identifier: {name!r}")
    return name


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_schema_sql(schema_name: str) -> str:
    validate_pg_ident(schema_name)
    return f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";'


def create_table_statements(schema_name: str, table: Table,
                            table_name: str | None = None,
                            column_names: list[str] | None = None) -> list[str]:
    if not table.columns:
        raise ValueError(f"{table.name}: колонки ещё не прочитаны (deep catalog, M2)")
    validate_pg_ident(schema_name)
    target_table = validate_pg_ident(table_name or table.pg_name)
    ordered = sorted(table.columns, key=lambda c: c.position)
    targets = column_names or [_col(column.name) for column in ordered]
    if len(targets) != len(ordered):
        raise ValueError("column_names count does not match source columns")
    cols = []
    for c, target in zip(ordered, targets):
        validate_pg_ident(target)
        null = "" if c.nullable else " NOT NULL"
        cols.append(f'    "{target}" {c.pg_type}{null}')
    body = ",\n".join(cols)
    staging = validate_pg_ident(f"{target_table}__staging")
    comment = _literal(f"source: {table.name} (relation_id={table.relation_id})")
    statements = [
        f'CREATE TABLE "{schema_name}"."{staging}" (\n{body}\n);',
        f'COMMENT ON TABLE "{schema_name}"."{staging}" IS {comment};',
    ]
    for column, target in zip(ordered, targets):
        source_comment = _literal(f"source column: {column.name}")
        statements.append(
            f'COMMENT ON COLUMN "{schema_name}"."{staging}"."{target}" '
            f"IS {source_comment};"
        )
    return statements


def create_table_sql(schema_name: str, table: Table,
                     table_name: str | None = None,
                     column_names: list[str] | None = None) -> str:
    return "\n".join(create_table_statements(
        schema_name, table, table_name=table_name, column_names=column_names
    ))


def _col(name: str) -> str:
    from .types import pg_ident
    return pg_ident(name)


def full_ddl(schema_name: str, schema: Schema, include_system: bool = False) -> str:
    parts = [create_schema_sql(schema_name)]
    for _, t in sorted(schema.tables.items()):
        if t.is_system and not include_system:
            continue
        if t.columns:
            parts.append(create_table_sql(schema_name, t))
    return "\n\n".join(parts) + "\n"
