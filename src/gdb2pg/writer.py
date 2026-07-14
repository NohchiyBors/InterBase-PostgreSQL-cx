"""Запись в PostgreSQL через COPY (psycopg3). Активируется на этапе M3."""

from __future__ import annotations

from typing import Iterable, Sequence


class PgWriter:
    """COPY-загрузка батчами в <schema>.<table>__staging с финальным RENAME."""

    def __init__(self, dsn: str, schema: str, batch_size: int = 10_000):
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install 'gdb2pg[pg]' для этапа convert") from exc
        self.dsn = dsn
        self.schema = schema
        self.batch_size = batch_size

    def load_table(self, table: str, columns: Sequence[str],
                   rows: Iterable[Sequence]) -> int:
        import psycopg

        staging = f'"{self.schema}"."{table}__staging"'
        cols = ", ".join(f'"{c}"' for c in columns)
        count = 0
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                with cur.copy(f"COPY {staging} ({cols}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
                        count += 1
            conn.commit()
        return count

    def promote(self, table: str) -> None:
        """staging -> боевое имя (атомарно в транзакции)."""
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP TABLE IF EXISTS "{self.schema}"."{table}"')
                cur.execute(f'ALTER TABLE "{self.schema}"."{table}__staging" '
                            f'RENAME TO "{table}"')
            conn.commit()
