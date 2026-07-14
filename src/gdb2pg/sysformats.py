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

NAME_LEN = 31  # длина метаданных-имён (CHAR(31)) в ODS >= 9; для ODS15 проверить (67 в IB7+ для некоторых полей? калибровка)


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

# RDB$RELATIONS (интересующие поля; полный формат длиннее):
#   RDB$RELATION_ID (short), RDB$RELATION_NAME (char 31), RDB$FORMAT (short)
# Смещения зависят от полного формата -> КАЛИБРОВКА обязательна.
# Для M1 достаточно имени и id: имя ищется probe-режимом по ASCII.
RDB_RELATIONS_FIELDS_OF_INTEREST = ["RDB$RELATION_ID", "RDB$RELATION_NAME",
                                    "RDB$FORMAT", "RDB$VIEW_BLR"]

# RDB$RELATION_FIELDS: RDB$FIELD_NAME, RDB$RELATION_NAME, RDB$FIELD_SOURCE,
#   RDB$FIELD_POSITION, RDB$FIELD_ID ...
RDB_RELATION_FIELDS_OF_INTEREST = ["RDB$FIELD_NAME", "RDB$RELATION_NAME",
                                   "RDB$FIELD_SOURCE", "RDB$FIELD_POSITION",
                                   "RDB$FIELD_ID"]

# RDB$FIELDS: RDB$FIELD_NAME, RDB$FIELD_TYPE, RDB$FIELD_SUB_TYPE,
#   RDB$FIELD_LENGTH, RDB$FIELD_SCALE, RDB$CHARACTER_SET_ID ...
RDB_FIELDS_OF_INTEREST = ["RDB$FIELD_NAME", "RDB$FIELD_TYPE",
                          "RDB$FIELD_SUB_TYPE", "RDB$FIELD_LENGTH",
                          "RDB$FIELD_SCALE", "RDB$CHARACTER_SET_ID"]
