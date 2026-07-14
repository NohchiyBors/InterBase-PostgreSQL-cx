from pathlib import Path

import pytest

from gdb2pg import catalog, conversion, ddl, ods, writer


def _column(name, position, dtype=ods.DTYPE_LONG, length=4):
    return catalog.Column(
        name=name,
        position=position,
        dtype=dtype,
        length=length,
        scale=0,
        sub_type=0,
        field_id=position,
        offset=4 + position * 4,
    )


def _schema():
    first = catalog.Table(
        relation_id=100,
        name="FOO-BAR",
        pointer_pages=[10],
        columns=[_column("A-B", 0), _column("A B", 1)],
    )
    second = catalog.Table(
        relation_id=101,
        name="FOO BAR",
        pointer_pages=[20],
        columns=[_column("VALUE", 0)],
    )
    system = catalog.Table(
        relation_id=1,
        name="RDB$DATABASE",
        pointer_pages=[2],
        columns=[_column("VALUE", 0)],
        is_system=True,
    )
    return catalog.Schema("15.0", 4096, {1: system, 100: first, 101: second})


def test_plan_resolves_table_and_column_collisions():
    plan = conversion.build_plan(_schema())
    assert [table.target_name for table in plan.tables] == ["foo_bar", "foo_bar_2"]
    assert [column.target_name for column in plan.tables[0].columns] == ["a_b", "a_b_2"]
    assert "RDB$DATABASE: system table" in plan.skipped


def test_plan_include_exclude_patterns():
    plan = conversion.build_plan(_schema(), include=["FOO*"], exclude=["*BAR"])
    assert [table.source.name for table in plan.tables] == []
    plan = conversion.build_plan(_schema(), include=["FOO-BAR"])
    assert [table.source.name for table in plan.tables] == ["FOO-BAR"]


def test_render_ddl_uses_staging_and_safe_identifiers():
    plan = conversion.build_plan(_schema(), include=["FOO-BAR"])
    sql = conversion.render_ddl("legacy_test", plan)
    assert 'CREATE SCHEMA IF NOT EXISTS "legacy_test"' in sql
    assert 'CREATE TABLE "legacy_test"."foo_bar__staging"' in sql
    assert '"a_b_2" integer' in sql
    with pytest.raises(ValueError, match="unsafe PostgreSQL identifier"):
        ddl.create_schema_sql('legacy";drop')


class FakeWriter:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def ensure_schema(self):
        self.calls.append(("schema",))

    def prepare_table(self, table, statements):
        self.calls.append(("prepare", table, len(statements)))

    def load_table(self, table, columns, rows):
        materialized = list(rows)
        self.calls.append(("load", table, tuple(columns), materialized))
        if self.fail:
            raise RuntimeError("copy failed")
        return len(materialized)

    def promote(self, table):
        self.calls.append(("promote", table))


def _rows(_table, stats):
    stats.rows_read = 2
    stats.walk.back_versions = 3
    return iter([(1, 2), (3, 4)])


def test_execute_plan_and_resume(tmp_path):
    source = tmp_path / "source.gdb"
    source.write_bytes(b"read-only fixture")
    manifest_path = tmp_path / "manifest.json"
    plan = conversion.build_plan(_schema(), include=["FOO-BAR"])
    writer = FakeWriter()

    manifest = conversion.execute_plan(
        None, str(source), "legacy_test", plan, writer, manifest_path,
        row_provider=_rows,
    )
    assert manifest.status == "done"
    assert manifest.tables["foo_bar"].rows_written == 2
    assert manifest.tables["foo_bar"].back_versions_skipped == 3
    assert ("promote", "foo_bar") in writer.calls
    assert not Path(str(manifest_path) + ".tmp").exists()

    resumed = FakeWriter()
    manifest = conversion.execute_plan(
        None, str(source), "legacy_test", plan, resumed, manifest_path,
        resume=True, row_provider=_rows,
    )
    assert manifest.status == "done"
    assert resumed.calls == [("schema",)]


def test_execute_plan_records_partial_failure(tmp_path):
    source = tmp_path / "source.gdb"
    source.write_bytes(b"fixture")
    plan = conversion.build_plan(_schema(), include=["FOO-BAR"])
    manifest = conversion.execute_plan(
        None,
        str(source),
        "legacy_test",
        plan,
        FakeWriter(fail=True),
        tmp_path / "manifest.json",
        row_provider=_rows,
    )
    assert manifest.status == "partial"
    assert manifest.tables["foo_bar"].status == "failed"
    assert manifest.tables["foo_bar"].error == "copy failed"


def test_pg_writer_splits_copy_batches(monkeypatch):
    import psycopg

    copy_batches = []

    class FakeCopy:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            copy_batches.append(self.rows)
            return self

        def __exit__(self, *_args):
            return False

        def write_row(self, row):
            self.rows.append(row)

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def copy(self, _statement):
            return FakeCopy()

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(psycopg, "connect", lambda _dsn: FakeConnection())
    pg_writer = writer.PgWriter("postgresql://test", "legacy_test", batch_size=2)

    written = pg_writer.load_table("foo", ["value"], [(1,), (2,), (3,), (4,), (5,)])

    assert written == 5
    assert [len(batch) for batch in copy_batches] == [2, 2, 1]
    with pytest.raises(ValueError, match="batch_size must be positive"):
        writer.PgWriter("postgresql://test", "legacy_test", batch_size=0)
