import struct
from datetime import date, datetime
from decimal import Decimal

from gdb2pg import ods, types


def test_mjd_epoch():
    assert types.decode_date(0) == date(1858, 11, 17)
    # 1.1.2000 = MJD 51544
    assert types.decode_date(51544) == date(2000, 1, 1)


def test_time_ticks():
    t = types.decode_time(12 * 3600 * 10_000 + 30 * 60 * 10_000 + 15 * 10_000 + 5000)
    assert (t.hour, t.minute, t.second, t.microsecond) == (12, 30, 15, 500_000)


def test_timestamp():
    ts = types.decode_timestamp(51544, 10_000)
    assert ts == datetime(2000, 1, 1, 0, 0, 1)


def test_scaled_numeric():
    assert types.decode_scaled(12345, -2) == Decimal("123.45")
    assert types.decode_scaled(7, 0) == 7


def test_decode_varchar_cp1251():
    payload = "Бершугир".encode("cp1251")
    buf = struct.pack("<H", len(payload)) + payload + b"\x00" * 10
    val = types.decode_value(ods.DTYPE_VARYING, buf, 0, 60)
    assert val == "Бершугир"


def test_decode_char_trailing_spaces():
    raw = ("АТЫРАУ".encode("cp1251") + b" " * 10)
    val = types.decode_value(ods.DTYPE_TEXT, raw, 0, len(raw))
    assert val == "АТЫРАУ"


def test_decode_ints():
    buf = struct.pack("<hiq", -5, 123456, 9_000_000_000)
    assert types.decode_value(ods.DTYPE_SHORT, buf, 0, 2) == -5
    assert types.decode_value(ods.DTYPE_LONG, buf, 2, 4) == 123456
    assert types.decode_value(ods.DTYPE_INT64, buf, 6, 8) == 9_000_000_000


def test_pg_type_mapping():
    assert types.pg_type(ods.DTYPE_LONG) == "integer"
    assert types.pg_type(ods.DTYPE_LONG, scale=-2) == "numeric(12,2)"
    assert types.pg_type(ods.DTYPE_TEXT, length=31) == "varchar(31)"
    assert types.pg_type(ods.DTYPE_BLOB, sub_type=1) == "text"
    assert types.pg_type(ods.DTYPE_BLOB, sub_type=0) == "bytea"


def test_pg_ident():
    assert types.pg_ident("RDB$RELATION_NAME") == "rdb_relation_name"
    assert types.pg_ident("Вагоны") == "x"  # не-ASCII -> подчёркивания -> fallback
    assert types.pg_ident("2COL") == "t_2col"
