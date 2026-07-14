"""Генерация PostgreSQL DDL из модели схемы (M3)."""

from __future__ import annotations

from .catalog import Schema, Table


def create_schema_sql(schema_name: str) -> str:
    return f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";'


def create_table_sql(schema_name: str, table: Table) -> str:
    if not table.columns:
        raise ValueError(f"{table.name}: колонки ещё не прочитаны (deep catalog, M2)")
    cols = []
    for c in sorted(table.columns, key=lambda c: c.position):
        null = "" if c.nullable else " NOT NULL"
        cols.append(f'    "{_col(c.name)}" {c.pg_type}{null}')
    body = ",\n".join(cols)
    return (f'CREATE TABLE "{schema_name}"."{table.pg_name}__staging" (\n{body}\n);\n'
            f'COMMENT ON TABLE "{schema_name}"."{table.pg_name}__staging" '
            f"IS 'source: {table.name} (relation_id={table.relation_id})';")


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
