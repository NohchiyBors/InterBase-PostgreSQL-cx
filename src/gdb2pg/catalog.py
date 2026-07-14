"""Чтение системного каталога RDB$ -> модель схемы.

Bootstrap:
  1. header.pages_page -> pointer chain RDB$PAGES (relation 0);
  2. RDB$PAGES -> pointer pages остальных отношений;
  3. RDB$RELATIONS / RDB$RELATION_FIELDS / RDB$FIELDS -> модель таблиц;
  4. RDB$FORMATS -> дескрипторы форматов записей (смещения полей).

M1: шаги 1-2 реализованы на захардкоженном формате RDB$PAGES
(sysformats.py); шаги 3-4 включаются после калибровки probe-режимом.
Кросс-проверка: records.find_pointer_pages() линейным сканом.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ods, records, sysformats, types
from .pager import Pager


@dataclass
class Column:
    name: str
    position: int
    dtype: int
    length: int
    scale: int
    sub_type: int
    charset_id: int | None = None
    offset: int | None = None  # из RDB$FORMATS
    nullable: bool = True

    @property
    def pg_type(self) -> str:
        return types.pg_type(self.dtype, self.length, self.scale, self.sub_type)


@dataclass
class Table:
    relation_id: int
    name: str
    pointer_pages: list[int] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    is_system: bool = False

    @property
    def pg_name(self) -> str:
        return types.pg_ident(self.name)


@dataclass
class Schema:
    ods: str
    page_size: int
    tables: dict[int, Table] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def read_rdb_pages(pager: Pager) -> dict[int, list[tuple[int, int]]]:
    """RDB$PAGES -> {relation_id: [(sequence, page_number) для pointer pages]}.

    Использует захардкоженный формат sysformats.RDB_PAGES (калибруется).
    """
    fmt = sysformats.RDB_PAGES
    result: dict[int, list[tuple[int, int]]] = {}
    for rec in records.walk_relation(pager, pager.header.pages_page, check_tx=False):
        buf = rec.data
        need = max(f.offset + f.length for f in fmt.fields)
        if len(buf) < need:
            continue
        vals = {f.name: types.decode_value(f.dtype, buf, f.offset, f.length)
                for f in fmt.fields}
        if vals["RDB$PAGE_TYPE"] != ods.PAG_POINTER:
            continue
        rel = vals["RDB$RELATION_ID"]
        result.setdefault(rel, []).append(
            (vals["RDB$PAGE_SEQUENCE"], vals["RDB$PAGE_NUMBER"]))
    return {rel: sorted(v) for rel, v in result.items()}


def build_schema(pager: Pager, deep: bool = False) -> Schema:
    """Построить модель схемы.

    deep=False (M1): только карта отношений (id -> pointer pages) двумя
    независимыми способами (RDB$PAGES и линейный скан) с кросс-проверкой.
    deep=True (M2+): плюс имена/колонки из RDB$RELATIONS и др.
    """
    schema = Schema(ods=pager.header.ods, page_size=pager.page_size)

    scan_map = records.find_pointer_pages(pager)

    try:
        cat_map = read_rdb_pages(pager)
    except Exception as exc:  # калибровка ещё не выполнена
        schema.warnings.append(f"RDB$PAGES bootstrap failed: {exc}; "
                               "using linear scan only (run `gdb2pg probe`)")
        cat_map = {}

    if cat_map:
        cat_simple = {rel: [pg for _, pg in pgs] for rel, pgs in cat_map.items()}
        if cat_simple != scan_map:
            only_cat = set(cat_simple) - set(scan_map)
            only_scan = set(scan_map) - set(cat_simple)
            schema.warnings.append(
                f"RDB$PAGES vs scan mismatch: only-in-catalog={sorted(only_cat)} "
                f"only-in-scan={sorted(only_scan)} — форматы требуют калибровки")
        source = cat_simple
    else:
        source = scan_map

    for rel_id, pages in sorted(source.items()):
        schema.tables[rel_id] = Table(
            relation_id=rel_id,
            name=f"RELATION_{rel_id}",
            pointer_pages=pages,
            is_system=rel_id <= 50,  # системные id; уточняется по RDB$RELATIONS
        )

    if deep:
        schema.warnings.append("deep catalog (RDB$RELATIONS names/columns) — "
                               "M2, требует калибровки sysformats")
    return schema
