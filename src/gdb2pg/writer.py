"""Запись в PostgreSQL через COPY (psycopg3)."""

from __future__ import annotations

from itertools import islice
from typing import Iterable, Sequence


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
