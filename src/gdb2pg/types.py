"""Декодирование значений полей и маппинг типов в PostgreSQL."""

from __future__ import annotations

import struct
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from . import ods

# InterBase DATE: дни от 17.11.1858 (Modified Julian Day 0)
_MJD_EPOCH = date(1858, 11, 17)
_TIME_TICKS_PER_SEC = 10_000  # 1/10000 секунды


def decode_date(days: int) -> date:
    return _MJD_EPOCH + timedelta(days=days)


def decode_time(ticks: int) -> time:
    total = ticks / _TIME_TICKS_PER_SEC
    h = int(total // 3600)
    m = int(total % 3600 // 60)
    s = total % 60
    micro = round((s - int(s)) * 1_000_000)
    return time(h, m, int(s), micro)


def decode_timestamp(days: int, ticks: int) -> datetime:
    d = decode_date(days)
    t = decode_time(ticks)
    return datetime.combine(d, t)


def decode_scaled(raw: int, scale: int) -> Decimal | int:
    if scale == 0:
        return raw
    return Decimal(raw).scaleb(scale)


def decode_value(dtype: int, buf: bytes, offset: int, length: int,
                 scale: int = 0, encoding: str = "cp1251"):
    """Декодировать одно значение из распакованной записи."""
    if dtype == ods.DTYPE_SHORT:
        return decode_scaled(struct.unpack_from("<h", buf, offset)[0], scale)
    if dtype == ods.DTYPE_LONG:
        return decode_scaled(struct.unpack_from("<i", buf, offset)[0], scale)
    if dtype == ods.DTYPE_INT64 or dtype == ods.DTYPE_QUAD:
        return decode_scaled(struct.unpack_from("<q", buf, offset)[0], scale)
    if dtype == ods.DTYPE_REAL:
        return struct.unpack_from("<f", buf, offset)[0]
    if dtype in (ods.DTYPE_DOUBLE, ods.DTYPE_D_FLOAT):
        return struct.unpack_from("<d", buf, offset)[0]
    if dtype == ods.DTYPE_SQL_DATE:
        return decode_date(struct.unpack_from("<i", buf, offset)[0])
    if dtype == ods.DTYPE_SQL_TIME:
        return decode_time(struct.unpack_from("<I", buf, offset)[0])
    if dtype == ods.DTYPE_TIMESTAMP:
        days, ticks = struct.unpack_from("<iI", buf, offset)
        return decode_timestamp(days, ticks)
    if dtype == ods.DTYPE_TEXT:
        raw = bytes(buf[offset: offset + length])
        return raw.decode(encoding, errors="replace").rstrip(" \x00")
    if dtype == ods.DTYPE_VARYING:
        (vlen,) = struct.unpack_from("<H", buf, offset)
        vlen = min(vlen, length)
        raw = bytes(buf[offset + 2: offset + 2 + vlen])
        return raw.decode(encoding, errors="replace")
    if dtype == ods.DTYPE_BOOLEAN:
        return bool(buf[offset])
    if dtype == ods.DTYPE_BLOB:
        # blob id: (relation page space) quad — интерпретация в blobs.py (M2)
        return struct.unpack_from("<II", buf, offset)
    raise NotImplementedError(f"dtype {dtype} ({ods.DTYPE_NAMES.get(dtype)})")


# --- каталожные карты (RDB$FIELD_TYPE -> dtype), выравнивание и размер хранения ---

FIELD_TYPE_TO_DTYPE = {
    7: ods.DTYPE_SHORT, 8: ods.DTYPE_LONG, 9: ods.DTYPE_QUAD,
    10: ods.DTYPE_REAL, 11: ods.DTYPE_D_FLOAT, 12: ods.DTYPE_SQL_DATE,
    13: ods.DTYPE_SQL_TIME, 14: ods.DTYPE_TEXT, 16: ods.DTYPE_INT64,
    17: ods.DTYPE_BOOLEAN, 27: ods.DTYPE_DOUBLE, 35: ods.DTYPE_TIMESTAMP,
    37: ods.DTYPE_VARYING, 40: ods.DTYPE_CSTRING, 261: ods.DTYPE_BLOB,
}


def dtype_align(dtype: int) -> int:
    if dtype in (ods.DTYPE_TEXT, ods.DTYPE_CSTRING, ods.DTYPE_BOOLEAN):
        return 1
    if dtype in (ods.DTYPE_VARYING, ods.DTYPE_SHORT):
        return 2
    return 4  # long/real/date/time/int64/double/timestamp/quad/blob (x86 IB)


def dtype_storage(dtype: int, length: int) -> int:
    if dtype == ods.DTYPE_TEXT or dtype == ods.DTYPE_CSTRING:
        return length
    if dtype == ods.DTYPE_VARYING:
        return length + 2
    if dtype == ods.DTYPE_SHORT:
        return 2
    if dtype == ods.DTYPE_BOOLEAN:
        return 1
    if dtype in (ods.DTYPE_LONG, ods.DTYPE_REAL, ods.DTYPE_SQL_DATE,
                 ods.DTYPE_SQL_TIME):
        return 4
    return 8  # int64/double/quad/timestamp/blob id


# --- каталожные карты (RDB$FIELD_TYPE -> dtype), выравнивание и размер хранения ---

FIELD_TYPE_TO_DTYPE = {
    7: ods.DTYPE_SHORT, 8: ods.DTYPE_LONG, 9: ods.DTYPE_QUAD,
    10: ods.DTYPE_REAL, 11: ods.DTYPE_D_FLOAT, 12: ods.DTYPE_SQL_DATE,
    13: ods.DTYPE_SQL_TIME, 14: ods.DTYPE_TEXT, 16: ods.DTYPE_INT64,
    17: ods.DTYPE_BOOLEAN, 27: ods.DTYPE_DOUBLE, 35: ods.DTYPE_TIMESTAMP,
    37: ods.DTYPE_VARYING, 40: ods.DTYPE_CSTRING, 261: ods.DTYPE_BLOB,
}


def dtype_align(dtype: int) -> int:
    if dtype in (ods.DTYPE_TEXT, ods.DTYPE_CSTRING, ods.DTYPE_BOOLEAN):
        return 1
    if dtype in (ods.DTYPE_VARYING, ods.DTYPE_SHORT):
        return 2
    return 4  # long/real/date/time/int64/double/timestamp/quad/blob (x86 IB)


def dtype_storage(dtype: int, length: int) -> int:
    if dtype in (ods.DTYPE_TEXT, ods.DTYPE_CSTRING):
        return length
    if dtype == ods.DTYPE_VARYING:
        return length + 2
    if dtype == ods.DTYPE_SHORT:
        return 2
    if dtype == ods.DTYPE_BOOLEAN:
        return 1
    if dtype in (ods.DTYPE_LONG, ods.DTYPE_REAL, ods.DTYPE_SQL_DATE,
                 ods.DTYPE_SQL_TIME):
        return 4
    return 8  # int64/double/quad/timestamp/blob id


# --- маппинг в PostgreSQL -------------------------------------------------------

def pg_type(dtype: int, length: int = 0, scale: int = 0, sub_type: int = 0) -> str:
    if dtype == ods.DTYPE_SHORT:
        return "smallint" if scale == 0 else f"numeric(6,{-scale})"
    if dtype == ods.DTYPE_LONG:
        return "integer" if scale == 0 else f"numeric(12,{-scale})"
    if dtype in (ods.DTYPE_INT64, ods.DTYPE_QUAD):
        return "bigint" if scale == 0 else f"numeric(20,{-scale})"
    if dtype == ods.DTYPE_REAL:
        return "real"
    if dtype in (ods.DTYPE_DOUBLE, ods.DTYPE_D_FLOAT):
        return "double precision"
    if dtype == ods.DTYPE_SQL_DATE:
        return "date"
    if dtype == ods.DTYPE_SQL_TIME:
        return "time"
    if dtype == ods.DTYPE_TIMESTAMP:
        return "timestamp"
    if dtype == ods.DTYPE_TEXT:
        return f"varchar({max(length, 1)})"
    if dtype == ods.DTYPE_VARYING:
        return f"varchar({max(length, 1)})"
    if dtype == ods.DTYPE_BOOLEAN:
        return "boolean"
    if dtype == ods.DTYPE_BLOB:
        return "text" if sub_type == 1 else "bytea"
    return "bytea"  # неизвестное -> сырые байты, с warning на уровне выше


def pg_ident(name: str) -> str:
    """RDB$-имя -> идентификатор PostgreSQL (lowercase snake, безопасный)."""
    out = []
    for ch in name.strip():
        if ch.isalnum() and ch.isascii():
            out.append(ch.lower())
        else:
            out.append("_")
    ident = "".join(out).strip("_") or "x"
    if ident[0].isdigit():
        ident = "t_" + ident
    return ident
