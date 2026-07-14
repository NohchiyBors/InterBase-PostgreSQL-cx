"""Чтение системного каталога RDB$ -> модель схемы.

Bootstrap:
  1. header.pages_page -> pointer chain RDB$PAGES (relation 0);
  2. RDB$PAGES -> pointer pages остальных отношений;
  3. RDB$RELATIONS / RDB$RELATION_FIELDS / RDB$FIELDS -> модель таблиц;
  4. RDB$FORMATS -> дескрипторы форматов записей (смещения полей).

M1: шаги 1-2 + имена из RDB$RELATIONS (откалибровано на ASUSS.GDB ODS 15).
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
    field_id: int = 0
    charset_id: int | None = None
    offset: int | None = None  # вычислено по алгоритму выравнивания движка
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
    """RDB$PAGES -> {relation_id: [(sequence, page_number) для pointer pages]}."""
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


def read_rdb_relations(pager: Pager,
                       pointer_map: dict[int, list[int]]) -> dict[int, dict]:
    """RDB$RELATIONS -> {relation_id: {name, system, format, field_count}}."""
    fmt = sysformats.RDB_RELATIONS
    if sysformats.REL_RELATIONS not in pointer_map:
        return {}
    first = pointer_map[sysformats.REL_RELATIONS][0]
    out: dict[int, dict] = {}
    for rec in records.walk_relation(pager, first, check_tx=False):
        buf = rec.data
        need = max(f.offset + f.length for f in fmt.fields)
        if len(buf) < need:
            continue
        vals = {f.name: types.decode_value(f.dtype, buf, f.offset, f.length)
                for f in fmt.fields}
        rel_id = vals["RDB$RELATION_ID"]
        out[rel_id] = {
            "name": vals["RDB$RELATION_NAME"],
            "system": bool(vals["RDB$SYSTEM_FLAG"]),
            "format": vals["RDB$FORMAT"],
            "field_count": vals["RDB$FIELD_ID"],
        }
    return out


def _read_sysformat(pager: Pager, pointer_map: dict[int, list[int]],
                    fmt) -> list[dict]:
    if fmt.relation_id not in pointer_map:
        return []
    first = pointer_map[fmt.relation_id][0]
    need = max(f.offset + f.length for f in fmt.fields)
    out = []
    for rec in records.walk_relation(pager, first, check_tx=False):
        if len(rec.data) < need:
            continue
        out.append({f.name: types.decode_value(f.dtype, rec.data, f.offset,
                                               f.length)
                    for f in fmt.fields})
    return out


def compute_offsets(columns: list[Column], field_count: int) -> None:
    """Раскладка данных записи по алгоритму движка.

    Верифицировано на системных таблицах ASUSS.GDB: null-маска
    ceil(n/8), выровненная до 4; поля в порядке FIELD_ID, каждое по
    выравниванию своего типа (text/bool 1, short/varchar 2, прочие 4).
    """
    n = max(field_count, (max((c.field_id for c in columns), default=0) + 1))
    off = ((n + 7) // 8 + 3) & ~3
    for c in sorted(columns, key=lambda c: c.field_id):
        a = types.dtype_align(c.dtype)
        off = (off + a - 1) & ~(a - 1)
        c.offset = off
        off += types.dtype_storage(c.dtype, c.length)


def load_columns(pager: Pager, schema: Schema,
                 pointer_map: dict[int, list[int]]) -> None:
    """Заполнить колонки таблиц из RDB$RELATION_FIELDS + RDB$FIELDS."""
    domains = {}
    for row in _read_sysformat(pager, pointer_map, sysformats.RDB_FIELDS):
        domains[row["RDB$FIELD_NAME"]] = row
    by_table: dict[str, list[Column]] = {}
    for row in _read_sysformat(pager, pointer_map,
                               sysformats.RDB_RELATION_FIELDS):
        dom = domains.get(row["RDB$FIELD_SOURCE"])
        if dom is None:
            continue
        ftype = dom["RDB$FIELD_TYPE"]
        dtype = types.FIELD_TYPE_TO_DTYPE.get(ftype)
        if dtype is None:
            continue  # неизвестный тип: колонка пропускается без падения
        by_table.setdefault(row["RDB$RELATION_NAME"], []).append(Column(
            name=row["RDB$FIELD_NAME"],
            position=row["RDB$FIELD_POSITION"],
            dtype=dtype,
            length=dom["RDB$FIELD_LENGTH"],
            scale=dom["RDB$FIELD_SCALE"],
            sub_type=dom["RDB$FIELD_SUB_TYPE"] or 0,
            field_id=row["RDB$FIELD_ID"],
            charset_id=dom["RDB$CHARACTER_SET_ID"],
        ))
    by_name = {t.name: t for t in schema.tables.values()}
    for tname, cols in by_table.items():
        t = by_name.get(tname)
        if t is None:
            continue
        t.columns = sorted(cols, key=lambda c: c.position)
        compute_offsets(t.columns, len(t.columns))


def decode_row(table: Table, data: bytes, encoding: str = "cp1251",
               errors: list[str] | None = None) -> dict:
    """Декодировать распакованную запись таблицы в dict по колонкам."""
    row = {}
    for c in table.columns:
        fid = c.field_id
        mask_byte = data[fid // 8] if fid // 8 < len(data) else 0xFF
        if (mask_byte >> (fid % 8)) & 1:
            row[c.name] = None
            continue
        if c.offset is None or c.offset + 1 > len(data):
            row[c.name] = None
            continue
        try:
            row[c.name] = types.decode_value(c.dtype, data, c.offset,
                                             c.length, c.scale, encoding)
        except Exception as exc:
            row[c.name] = None
            if errors is not None:
                errors.append(f"{c.name}: {exc}")
    return row


def build_schema(pager: Pager, deep: bool = False) -> Schema:
    """Построить модель схемы (M1: карта отношений + имена таблиц)."""
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

    try:
        rel_info = read_rdb_relations(pager, source)
    except Exception as exc:
        schema.warnings.append(f"RDB$RELATIONS read failed: {exc}")
        rel_info = {}

    for rel_id, pages in sorted(source.items()):
        info = rel_info.get(rel_id, {})
        schema.tables[rel_id] = Table(
            relation_id=rel_id,
            name=info.get("name") or f"RELATION_{rel_id}",
            pointer_pages=pages,
            is_system=info.get("system", rel_id <= 50),
        )

    if deep:
        try:
            load_columns(pager, schema, source)
            missing = [t.name for t in schema.tables.values()
                       if not t.is_system and not t.columns]
            if missing:
                schema.warnings.append(
                    f"нет колонок у {len(missing)} таблиц: {missing[:5]}...")
        except Exception as exc:
            schema.warnings.append(f"load_columns failed: {exc}")
    return schema
