"""Планирование и выполнение переноса GDB -> PostgreSQL."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from . import catalog, ddl, ods, records, types
from .manifest import Manifest, TableState


@dataclass(frozen=True)
class PlannedColumn:
    source: catalog.Column
    target_name: str


@dataclass(frozen=True)
class PlannedTable:
    source: catalog.Table
    target_name: str
    columns: tuple[PlannedColumn, ...]


@dataclass(frozen=True)
class ConversionPlan:
    tables: tuple[PlannedTable, ...]
    skipped: tuple[str, ...] = ()


@dataclass
class TableReadStats:
    rows_read: int = 0
    decode_errors: int = 0
    blobs_skipped: int = 0
    walk: records.WalkStats = field(default_factory=records.WalkStats)


class ConversionWriter(Protocol):
    def ensure_schema(self) -> None: ...

    def prepare_table(self, table: str, statements: Sequence[str]) -> None: ...

    def load_table(self, table: str, columns: Sequence[str],
                   rows: Iterable[Sequence]) -> int: ...

    def promote(self, table: str) -> None: ...


RowProvider = Callable[[PlannedTable, TableReadStats], Iterable[Sequence]]


def _matches(table: catalog.Table, patterns: Sequence[str]) -> bool:
    names = (table.name.casefold(), table.pg_name.casefold())
    return any(fnmatch.fnmatchcase(name, pattern.casefold())
               for pattern in patterns for name in names)


def _unique_name(base: str, used: set[str], max_length: int) -> str:
    root = base[:max_length] or "x"
    candidate = root
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = root[: max_length - len(tail)] + tail
        suffix += 1
    used.add(candidate)
    return candidate


def build_plan(schema: catalog.Schema, include: Sequence[str] = (),
               exclude: Sequence[str] = (), include_system: bool = False) -> ConversionPlan:
    selected: list[catalog.Table] = []
    skipped: list[str] = []
    for _, table in sorted(schema.tables.items()):
        reason = None
        if table.is_system and not include_system:
            reason = "system table"
        elif not table.columns:
            reason = "no decoded columns"
        elif not table.pointer_pages:
            reason = "no pointer pages"
        elif include and not _matches(table, include):
            reason = "not included"
        elif exclude and _matches(table, exclude):
            reason = "excluded"
        if reason:
            skipped.append(f"{table.name}: {reason}")
        else:
            selected.append(table)

    used_tables: set[str] = set()
    planned: list[PlannedTable] = []
    for table in selected:
        # Reserve space for the __staging suffix under PostgreSQL's 63-byte limit.
        target = _unique_name(table.pg_name, used_tables, max_length=52)
        used_columns: set[str] = set()
        columns = tuple(
            PlannedColumn(
                source=column,
                target_name=_unique_name(
                    types.pg_ident(column.name), used_columns, max_length=63
                ),
            )
            for column in sorted(table.columns, key=lambda item: item.position)
        )
        planned.append(PlannedTable(source=table, target_name=target, columns=columns))
    return ConversionPlan(tables=tuple(planned), skipped=tuple(skipped))


def table_statements(schema_name: str, table: PlannedTable) -> list[str]:
    return ddl.create_table_statements(
        schema_name,
        table.source,
        table_name=table.target_name,
        column_names=[column.target_name for column in table.columns],
    )


def render_ddl(schema_name: str, plan: ConversionPlan) -> str:
    parts = [ddl.create_schema_sql(schema_name)]
    for table in plan.tables:
        parts.extend(table_statements(schema_name, table))
    return "\n\n".join(parts) + "\n"


def iter_table_rows(pager, table: PlannedTable, stats: TableReadStats,
                    check_tx: bool = True, encoding: str = "cp1251"):
    for record in records.walk_relation(
        pager,
        table.source.pointer_pages[0],
        check_tx=check_tx,
        stats=stats.walk,
    ):
        errors: list[str] = []
        decoded = catalog.decode_row(table.source, record.data, encoding=encoding,
                                     errors=errors)
        stats.rows_read += 1
        stats.decode_errors += len(errors)
        values = []
        for column in table.columns:
            value = decoded.get(column.source.name)
            if column.source.dtype == ods.DTYPE_BLOB and value is not None:
                # BlobReader is not calibrated yet; preserve the row and report NULL.
                value = None
                stats.blobs_skipped += 1
            values.append(value)
        yield tuple(values)


def _new_manifest(gdb_path: str, schema_name: str,
                  plan: ConversionPlan) -> Manifest:
    manifest = Manifest(gdb_path=str(Path(gdb_path).resolve()), schema=schema_name)
    for table in plan.tables:
        manifest.tables[table.target_name] = TableState(
            relation_id=table.source.relation_id,
            name=table.source.name,
            target_name=table.target_name,
        )
    return manifest


def execute_plan(pager, gdb_path: str, schema_name: str, plan: ConversionPlan,
                 writer: ConversionWriter, manifest_path: str | Path,
                 resume: bool = False, strict: bool = False,
                 check_tx: bool = True, encoding: str = "cp1251",
                 row_provider: RowProvider | None = None) -> Manifest:
    manifest_file = Path(manifest_path)
    resolved_source = str(Path(gdb_path).resolve())
    if resume and manifest_file.exists():
        manifest = Manifest.load(manifest_file)
        if manifest.gdb_path != resolved_source or manifest.schema != schema_name:
            raise ValueError("manifest source/schema does not match this conversion")
    else:
        manifest = _new_manifest(gdb_path, schema_name, plan)

    writer.ensure_schema()
    for table in plan.tables:
        state = manifest.tables.setdefault(
            table.target_name,
            TableState(table.source.relation_id, table.source.name, table.target_name),
        )
        if resume and state.status == "done":
            continue

        state.status = "loading"
        state.error = None
        state.rows_read = 0
        state.rows_written = 0
        manifest.status = "running"
        manifest.save(manifest_file)
        read_stats = TableReadStats()
        try:
            writer.prepare_table(table.target_name,
                                 table_statements(schema_name, table))
            rows = (row_provider(table, read_stats) if row_provider else
                    iter_table_rows(pager, table, read_stats, check_tx, encoding))
            written = writer.load_table(
                table.target_name,
                [column.target_name for column in table.columns],
                rows,
            )
            state.rows_read = read_stats.rows_read
            state.rows_written = written
            state.bad_pages = read_stats.walk.bad_pages
            state.decode_errors = read_stats.decode_errors
            state.blobs_skipped = read_stats.blobs_skipped
            if written != read_stats.rows_read:
                raise RuntimeError(
                    f"row count mismatch: read={read_stats.rows_read}, written={written}"
                )
            writer.promote(table.target_name)
            state.status = "done"
        except Exception as exc:
            state.rows_read = read_stats.rows_read
            state.bad_pages = read_stats.walk.bad_pages
            state.decode_errors = read_stats.decode_errors
            state.blobs_skipped = read_stats.blobs_skipped
            state.status = "failed"
            state.error = str(exc)
            manifest.status = "partial"
            manifest.save(manifest_file)
            if strict:
                raise
        manifest.save(manifest_file)

    failed = any(state.status == "failed" for state in manifest.tables.values())
    manifest.finish("partial" if failed else "done")
    manifest.save(manifest_file)
    return manifest


def render_report(manifest: Manifest) -> str:
    lines = [
        "# gdb2pg conversion report",
        "",
        f"- Source: `{manifest.gdb_path}`",
        f"- PostgreSQL schema: `{manifest.schema}`",
        f"- Status: `{manifest.status}`",
        "",
        "| source table | target table | status | read | written | bad pages | decode errors | blobs skipped |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, state in sorted(manifest.tables.items()):
        lines.append(
            f"| {state.name} | {state.target_name} | {state.status} | "
            f"{state.rows_read} | {state.rows_written} | {state.bad_pages} | "
            f"{state.decode_errors} | {state.blobs_skipped} |"
        )
        if state.error:
            lines.append(f"\n> **{state.name}:** {state.error}\n")
    if manifest.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in manifest.warnings)
    return "\n".join(lines) + "\n"
