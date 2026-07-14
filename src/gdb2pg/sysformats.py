"""Захардкоженные форматы системных таблиц для bootstrap каталога.

Движок InterBase читает RDB$-каталог, используя вкомпилированные форматы
(jrd/relations.h, jrd/fields.h). Оффлайн-парсеру приходится делать то же.

ВНИМАНИЕ: раскладки ниже выведены из исходников InterBase 6.0 (ODS 10) и
являются СТАРТОВОЙ ГИПОТЕЗОЙ для ODS 15. Порядок калибровки:
  1. `gdb2pg probe FILE --relation-id 6 --limit 5 --hex`
  2. сверить смещения по видимым ASCII-именам таблиц (RDB$RELATION_NAME)
  3. поправить OFFSETS ниже; больше нигде правок не требуется.

Идентификаторы системных отношений (jrd/ids.h, стабильны с IB4):
  0 RDB$PAGES, 1 RDB$DATABASE, 2 RDB$FIELDS, 3 RDB$INDEX_SEGMENTS,
  4 RDB$INDICES, 5 RDB$RELATION_FIELDS, 6 RDB$RELATIONS,
  7 RDB$VIEW_RELATIONS, 8 RDB$FORMATS, 9 RDB$SECURITY_CLASSES,
  10 RDB$FILES, 11 RDB$TYPES, 12 RDB$TRIGGERS, 13 RDB$DEPENDENCIES,
  14 RDB$FUNCTIONS, 15 RDB$FUNCTION_ARGUMENTS, 16 RDB$FILTERS,
  ... 28 RDB$CHARACTER_SETS, 29 RDB$COLLATIONS ...
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ods

REL_PAGES = 0
REL_DATABASE = 1
REL_FIELDS = 2
REL_RELATION_FIELDS = 5
REL_RELATIONS = 6
REL_FORMATS = 8
REL_CHARACTER_SETS = 28
REL_COLLATIONS = 29

NAME_LEN = 67  # CHAR(67): длина метаданных-имён в ODS 15 (подтверждено probe на ASUSS.GDB 2026-07-14; в IB6/ODS10 было 31)


@dataclass
class SysField:
    name: str
    dtype: int
    offset: int
    length: int
    scale: int = 0


@dataclass
class SysFormat:
    relation_id: int
    name: str
    null_bytes: int          # байт под null-маску в начале данных записи
    fields: list[SysField]


def _f(name: str, dtype: int, offset: int, length: int) -> SysField:
    return SysField(name, dtype, offset, length)


# RDB$PAGES: RDB$PAGE_NUMBER (long), RDB$RELATION_ID (short),
#            RDB$PAGE_SEQUENCE (long), RDB$PAGE_TYPE (short)
# Гипотеза раскладки: null-маска 1 байт -> выравнивание long на 4.
RDB_PAGES = SysFormat(
    relation_id=REL_PAGES,
    name="RDB$PAGES",
    null_bytes=1,
    fields=[
        _f("RDB$PAGE_NUMBER", ods.DTYPE_LONG, 4, 4),
        _f("RDB$RELATION_ID", ods.DTYPE_SHORT, 8, 2),
        _f("RDB$PAGE_SEQUENCE", ods.DTYPE_LONG, 12, 4),
        _f("RDB$PAGE_TYPE", ods.DTYPE_SHORT, 16, 2),
    ],
)

# RDB$FORMATS: RDB$RELATION_ID (short), RDB$FORMAT (short), RDB$DESCRIPTOR (blob)
RDB_FORMATS = SysFormat(
    relation_id=REL_FORMATS,
    name="RDB$FORMATS",
    null_bytes=1,
    fields=[
        _f("RDB$RELATION_ID", ods.DTYPE_SHORT, 2, 2),
        _f("RDB$FORMAT", ods.DTYPE_SHORT, 4, 2),
        _f("RDB$DESCRIPTOR", ods.DTYPE_BLOB, 8, 8),
    ],
)

# RDB$RELATIONS — ОТКАЛИБРОВАНО на ASUSS.GDB (ODS 15.0, 2026-07-14, len=616):
#   0x00 null mask (ULONG), 0x04 VIEW_BLR (blob id 8), 0x0C VIEW_SOURCE (8),
#   0x14 DESCRIPTION (8), 0x1C RELATION_ID (short), 0x1E SYSTEM_FLAG (short),
#   0x20 DBKEY_LENGTH (short, =8), 0x22 FORMAT (short), 0x24 FIELD_ID (short),
#   0x26 RELATION_NAME (char 67), 0x69 SECURITY_CLASS (char 67), ...
RDB_RELATIONS = SysFormat(
    relation_id=REL_RELATIONS,
    name="RDB$RELATIONS",
    null_bytes=4,
    fields=[
        _f("RDB$RELATION_ID", ods.DTYPE_SHORT, 0x1C, 2),
        _f("RDB$SYSTEM_FLAG", ods.DTYPE_SHORT, 0x1E, 2),
        _f("RDB$FORMAT", ods.DTYPE_SHORT, 0x22, 2),
        _f("RDB$FIELD_ID", ods.DTYPE_SHORT, 0x24, 2),
        _f("RDB$RELATION_NAME", ods.DTYPE_TEXT, 0x26, NAME_LEN),
    ],
)

# RDB$RELATION_FIELDS — ОТКАЛИБРОВАНО на ASUSS.GDB (ODS 15.0, len=672):
#   mask ULONG @0, FIELD_NAME c67 @0x04, RELATION_NAME c67 @0x47,
#   FIELD_SOURCE c67 @0x8A, QUERY_NAME c67 @0xCD, BASE_FIELD c67 @0x110,
#   EDIT_STRING vc125 @0x154, FIELD_POSITION @0x1D4, QUERY_HEADER blob @0x1D8,
#   UPDATE_FLAG @0x1E0, FIELD_ID @0x1E2, VIEW_CONTEXT @0x1E4, SYSTEM_FLAG @0x1F8
RDB_RELATION_FIELDS = SysFormat(
    relation_id=REL_RELATION_FIELDS,
    name="RDB$RELATION_FIELDS",
    null_bytes=4,
    fields=[
        _f("RDB$FIELD_NAME", ods.DTYPE_TEXT, 0x04, NAME_LEN),
        _f("RDB$RELATION_NAME", ods.DTYPE_TEXT, 0x47, NAME_LEN),
        _f("RDB$FIELD_SOURCE", ods.DTYPE_TEXT, 0x8A, NAME_LEN),
        _f("RDB$FIELD_POSITION", ods.DTYPE_SHORT, 0x1D4, 2),
        _f("RDB$FIELD_ID", ods.DTYPE_SHORT, 0x1E2, 2),
    ],
)

# RDB$FIELDS — ОТКАЛИБРОВАНО на ASUSS.GDB (ODS 15.0, len=380):
#   mask ULONG @0, FIELD_NAME c67 @0x04, QUERY_NAME c67 @0x47, блобы...,
#   FIELD_LENGTH @0xBC, FIELD_SCALE @0xBE, FIELD_TYPE @0xC0,
#   FIELD_SUB_TYPE @0xC2, CHARACTER_SET_ID @0x178
RDB_FIELDS = SysFormat(
    relation_id=REL_FIELDS,
    name="RDB$FIELDS",
    null_bytes=4,
    fields=[
        _f("RDB$FIELD_NAME", ods.DTYPE_TEXT, 0x04, NAME_LEN),
        _f("RDB$FIELD_LENGTH", ods.DTYPE_SHORT, 0xBC, 2),
        _f("RDB$FIELD_SCALE", ods.DTYPE_SHORT, 0xBE, 2),
        _f("RDB$FIELD_TYPE", ods.DTYPE_SHORT, 0xC0, 2),
        _f("RDB$FIELD_SUB_TYPE", ods.DTYPE_SHORT, 0xC2, 2),
        _f("RDB$CHARACTER_SET_ID", ods.DTYPE_SHORT, 0x178, 2),
    ],
)
