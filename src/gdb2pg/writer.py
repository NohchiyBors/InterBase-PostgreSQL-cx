"""Запись в PostgreSQL через COPY (psycopg3)."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import islice
from typing import TYPE_CHECKING
from typing import Iterable, Sequence

if TYPE_CHECKING:
    from .manifest import Manifest, TableState


class PgWriter:
    """COPY-загрузка батчами в <schema>.<table>__staging с финальным RENAME."""

    def __init__(self, dsn: str, schema: str, batch_size: int = 10_000):
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install 'gdb2pg[pg]' для этапа convert") from exc
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.dsn = dsn
        self.schema = schema
        self.batch_size = batch_size

    def ensure_schema(self) -> None:
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self.schema)
                ))

    def prepare_table(self, table: str, statements: Sequence[str]) -> None:
        import psycopg
        from psycopg import sql

        staging = f"{table}__staging"
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                    sql.Identifier(self.schema), sql.Identifier(staging)
                ))
                for statement in statements:
                    cur.execute(statement)

    def load_table(self, table: str, columns: Sequence[str],
                   rows: Iterable[Sequence]) -> int:
        import psycopg

        from psycopg import sql

        staging = f"{table}__staging"
        copy_sql = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
            sql.Identifier(self.schema),
            sql.Identifier(staging),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        count = 0
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                iterator = iter(rows)
                while batch := list(islice(iterator, self.batch_size)):
                    with cur.copy(copy_sql) as copy:
                        for row in batch:
                            copy.write_row(row)
                            count += 1
        return count

    def promote(self, table: str) -> None:
        """staging -> боевое имя (атомарно в транзакции)."""
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                    sql.Identifier(self.schema), sql.Identifier(table)
                ))
                cur.execute(sql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
                    sql.Identifier(self.schema),
                    sql.Identifier(f"{table}__staging"),
                    sql.Identifier(table),
                ))

    def save_manifest(self, manifest: "Manifest",
                      target_table: str | None = None) -> None:
        """Синхронизировать локальный manifest с таблицей целевой схемы."""
        import psycopg
        from psycopg import sql

        manifest_table = sql.Identifier("gdb2pg_manifest")
        qualified = sql.SQL("{}.{}").format(sql.Identifier(self.schema), manifest_table)
        create = sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                target_table text PRIMARY KEY,
                source_table text NOT NULL,
                relation_id integer NOT NULL,
                source_path text NOT NULL,
                run_status text NOT NULL,
                table_status text NOT NULL,
                started_at timestamptz NOT NULL,
                finished_at timestamptz,
                rows_read bigint NOT NULL,
                rows_written bigint NOT NULL,
                bad_pages bigint NOT NULL,
                bad_page_numbers bigint[] NOT NULL,
                back_versions_skipped bigint NOT NULL,
                decode_errors bigint NOT NULL,
                blobs_read bigint NOT NULL,
                blobs_skipped bigint NOT NULL,
                error text,
                warnings text[] NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
        """).format(qualified)
        upsert = sql.SQL("""
            INSERT INTO {} (
                target_table, source_table, relation_id, source_path,
                run_status, table_status, started_at, finished_at,
                rows_read, rows_written, bad_pages, bad_page_numbers,
                back_versions_skipped, decode_errors, blobs_read, blobs_skipped,
                error, warnings, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, clock_timestamp()
            )
            ON CONFLICT (target_table) DO UPDATE SET
                source_table = EXCLUDED.source_table,
                relation_id = EXCLUDED.relation_id,
                source_path = EXCLUDED.source_path,
                run_status = EXCLUDED.run_status,
                table_status = EXCLUDED.table_status,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                rows_read = EXCLUDED.rows_read,
                rows_written = EXCLUDED.rows_written,
                bad_pages = EXCLUDED.bad_pages,
                bad_page_numbers = EXCLUDED.bad_page_numbers,
                back_versions_skipped = EXCLUDED.back_versions_skipped,
                decode_errors = EXCLUDED.decode_errors,
                blobs_read = EXCLUDED.blobs_read,
                blobs_skipped = EXCLUDED.blobs_skipped,
                error = EXCLUDED.error,
                warnings = EXCLUDED.warnings,
                updated_at = EXCLUDED.updated_at
        """).format(qualified)

        states = manifest.tables
        if target_table is not None:
            state = states.get(target_table)
            states = {target_table: state} if state is not None else {}
        started = datetime.fromtimestamp(manifest.started_at, tz=timezone.utc)
        finished = (datetime.fromtimestamp(manifest.finished_at, tz=timezone.utc)
                    if manifest.finished_at is not None else None)

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(create)
                for state in states.values():
                    cur.execute(upsert, self._manifest_row(
                        manifest, state, started, finished
                    ))
                if target_table is None:
                    cur.execute(
                        sql.SQL("DELETE FROM {} WHERE NOT (target_table = ANY(%s))")
                        .format(qualified),
                        (list(manifest.tables),),
                    )

    @staticmethod
    def _manifest_row(manifest: "Manifest", state: "TableState",
                      started: datetime, finished: datetime | None) -> tuple:
        return (
            state.target_name, state.name, state.relation_id, manifest.gdb_path,
            manifest.status, state.status, started, finished,
            state.rows_read, state.rows_written, state.bad_pages,
            state.bad_page_numbers, state.back_versions_skipped,
            state.decode_errors, state.blobs_read, state.blobs_skipped,
            state.error, manifest.warnings,
        )
