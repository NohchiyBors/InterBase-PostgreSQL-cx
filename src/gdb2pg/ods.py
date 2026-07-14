"""Структуры и константы InterBase On-Disk Structure (ODS).

Базовая раскладка — ODS 10 (открытый исходник InterBase 6.0, src/jrd/ods.h).
Все смещения вынесены в константы: дельты ODS 11-15 калибруются на реальных
файлах командой `gdb2pg probe` и правятся здесь, в одном месте.

Все значения little-endian (x86). SLONG/ULONG = 4 байта, USHORT = 2 байта.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --- типы страниц (pag_type) -------------------------------------------------

PAG_UNDEFINED = 0
PAG_HEADER = 1        # header page (page 0)
PAG_PIP = 2           # page inventory page
PAG_TIP = 3           # transaction inventory page
PAG_POINTER = 4       # pointer page (per relation)
PAG_DATA = 5          # data page
PAG_ROOT = 6          # index root page
PAG_INDEX = 7         # index (b-tree) page
PAG_BLOB = 8          # blob page
PAG_IDS = 9           # generator page
PAG_LOG = 10          # write-ahead log info

PAGE_TYPE_NAMES = {
    PAG_UNDEFINED: "undefined",
    PAG_HEADER: "header",
    PAG_PIP: "page_inventory",
    PAG_TIP: "transaction_inventory",
    PAG_POINTER: "pointer",
    PAG_DATA: "data",
    PAG_ROOT: "index_root",
    PAG_INDEX: "index_btree",
    PAG_BLOB: "blob",
    PAG_IDS: "generators",
    PAG_LOG: "log_info",
}

# --- базовый заголовок каждой страницы (struct pag, 16 байт в ODS 10) --------

PAG_FMT = "<BBHIII"  # type, flags, checksum, generation, seqno, offset
PAG_SIZE = struct.calcsize(PAG_FMT)  # 16


@dataclass
class PageHeader:
    type: int
    flags: int
    checksum: int
    generation: int
    seqno: int
    offset: int

    @classmethod
    def parse(cls, buf: bytes) -> "PageHeader":
        return cls(*struct.unpack_from(PAG_FMT, buf, 0))


# --- header page (page 0) -----------------------------------------------------

# смещения после struct pag (16 байт), ODS 10
HDR_FMT = "<HHiIiiiHH2iiihHHHIii4i"
# page_size, ods_version, PAGES, next_page, oldest_tx, oldest_active,
# next_tx, sequence, flags, creation_date[2], attachment_id, shadow_count,
# implementation, ods_minor, ods_minor_original, end, page_buffers,
# bumped_tx, oldest_snapshot, misc[4]
HDR_DATA_OFFSET_ODS10 = PAG_SIZE + struct.calcsize(HDR_FMT)  # начало clumplets

# флаги hdr_flags
HDR_ACTIVE_SHADOW = 0x1
HDR_FORCE_WRITE = 0x2
HDR_SHUTDOWN = 0x80  # база остановлена (в разных версиях разряд может отличаться)

# clumplet-типы в hdr_data (ODS 10; в ODS 12+ появились новые, вкл. encryption)
HDR_END = 0
HDR_ROOT_FILE_NAME = 1
HDR_FILE = 3
HDR_LAST_PAGE = 4
HDR_SWEEP_INTERVAL = 6
HDR_PASSWORD_FILE_KEY = 9
HDR_BACKUP_INFO = 10

CLUMPLET_NAMES = {
    HDR_END: "end",
    HDR_ROOT_FILE_NAME: "root_file_name",
    2: "journal_server",
    HDR_FILE: "secondary_file",
    HDR_LAST_PAGE: "last_page_in_file",
    5: "unlicensed_count",
    HDR_SWEEP_INTERVAL: "sweep_interval",
    7: "log_name",
    8: "journal_file",
    HDR_PASSWORD_FILE_KEY: "password_file_key",
    HDR_BACKUP_INFO: "backup_info",
    11: "cache_file",
    # 12+ — версионные расширения (описание/шифрование); дампим как raw
}


@dataclass
class HeaderPage:
    pag: PageHeader
    page_size: int
    ods_version: int
    ods_minor: int
    pages_page: int          # hdr_PAGES: первая pointer page таблицы RDB$PAGES
    next_page: int
    oldest_transaction: int
    oldest_active: int
    next_transaction: int
    sequence: int
    flags: int
    implementation: int
    clumplets: list[tuple[int, bytes]] = field(default_factory=list)
    raw: bytes = b""

    @classmethod
    def parse(cls, buf: bytes) -> "HeaderPage":
        pag = PageHeader.parse(buf)
        if pag.type != PAG_HEADER:
            raise ValueError(f"page 0 is not a header page (pag_type={pag.type})")
        vals = struct.unpack_from(HDR_FMT, buf, PAG_SIZE)
        (page_size, ods_version, pages_page, next_page, oldest_tx, oldest_active,
         next_tx, sequence, flags, _cd0, _cd1, _att, _shadow,
         implementation, ods_minor, _ods_minor_orig, _end, _page_buffers,
         _bumped, _oldest_snap, *_misc) = vals
        hp = cls(
            pag=pag, page_size=page_size, ods_version=ods_version,
            ods_minor=ods_minor, pages_page=pages_page, next_page=next_page,
            oldest_transaction=oldest_tx, oldest_active=oldest_active,
            next_transaction=next_tx, sequence=sequence, flags=flags,
            implementation=implementation, raw=bytes(buf[: max(256, HDR_DATA_OFFSET_ODS10)]),
        )
        hp.clumplets = _parse_clumplets(buf, HDR_DATA_OFFSET_ODS10)
        return hp

    @property
    def ods(self) -> str:
        return f"{self.ods_version}.{self.ods_minor}"


def _parse_clumplets(buf: bytes, offset: int) -> list[tuple[int, bytes]]:
    """[type:1][len:1][data:len] ... до HDR_END. При мусоре — останов."""
    out: list[tuple[int, bytes]] = []
    pos = offset
    for _ in range(256):
        if pos + 2 > len(buf):
            break
        ctype = buf[pos]
        if ctype == HDR_END:
            break
        clen = buf[pos + 1]
        data = bytes(buf[pos + 2: pos + 2 + clen])
        out.append((ctype, data))
        pos += 2 + clen
    return out


# --- pointer page (ODS 10) ----------------------------------------------------

PPG_FMT = "<iiHHHH"  # sequence, next, count, relation, min_space, max_space
PPG_HEADER_SIZE = PAG_SIZE + struct.calcsize(PPG_FMT)  # 32; далее SLONG ppg_page[]


@dataclass
class PointerPage:
    pag: PageHeader
    sequence: int
    next: int
    count: int
    relation: int
    pages: list[int]

    @classmethod
    def parse(cls, buf: bytes) -> "PointerPage":
        pag = PageHeader.parse(buf)
        seq, nxt, count, relation, _mn, _mx = struct.unpack_from(PPG_FMT, buf, PAG_SIZE)
        pages = list(struct.unpack_from(f"<{count}i", buf, PPG_HEADER_SIZE)) if count else []
        return cls(pag=pag, sequence=seq, next=nxt, count=count,
                   relation=relation, pages=pages)


# --- data page (ODS 10) --------------------------------------------------------

DPG_FMT = "<iHH"  # sequence, relation, count
DPG_HEADER_SIZE = PAG_SIZE + struct.calcsize(DPG_FMT)  # 24; далее repeat[]
DPG_REPEAT_FMT = "<HH"  # offset, length
DPG_REPEAT_SIZE = struct.calcsize(DPG_REPEAT_FMT)


@dataclass
class DataPageSlot:
    offset: int
    length: int


@dataclass
class DataPage:
    pag: PageHeader
    sequence: int
    relation: int
    count: int
    slots: list[DataPageSlot]

    @classmethod
    def parse(cls, buf: bytes) -> "DataPage":
        pag = PageHeader.parse(buf)
        seq, relation, count = struct.unpack_from(DPG_FMT, buf, PAG_SIZE)
        slots = []
        for i in range(count):
            off, ln = struct.unpack_from(DPG_REPEAT_FMT, buf, DPG_HEADER_SIZE + i * DPG_REPEAT_SIZE)
            slots.append(DataPageSlot(off, ln))
        return cls(pag=pag, sequence=seq, relation=relation, count=count, slots=slots)


# --- заголовок записи (rhd / rhdf, ODS 10) -------------------------------------

RHD_FMT = "<iiHHB"  # transaction, b_page, b_line, flags, format
RHD_SIZE = struct.calcsize(RHD_FMT)  # 13
# фрагментированная запись: + f_page (SLONG), f_line (USHORT).
# Внимание: выравнивание f_page проверяется на реальном файле (13 vs 14/16).
RHDF_EXTRA_FMT = "<iH"
RHDF_EXTRA_OFFSET = RHD_SIZE + 1  # калибровка: +1 байт выравнивания (проверить probe)

# rhd_flags
RHD_DELETED = 1
RHD_CHAIN = 2
RHD_FRAGMENT = 4
RHD_INCOMPLETE = 8
RHD_BLOB = 16
RHD_STREAM_BLOB = 32
RHD_DELTA = 32       # в ODS10 delta и stream_blob исторически перекрывались
RHD_LARGE = 64
RHD_DAMAGED = 128
RHD_GC_ACTIVE = 256


@dataclass
class RecordHeader:
    transaction: int
    b_page: int
    b_line: int
    flags: int
    format: int

    @classmethod
    def parse(cls, buf: bytes, offset: int = 0) -> "RecordHeader":
        return cls(*struct.unpack_from(RHD_FMT, buf, offset))

    @property
    def deleted(self) -> bool:
        return bool(self.flags & RHD_DELETED)

    @property
    def is_blob(self) -> bool:
        return bool(self.flags & RHD_BLOB)

    @property
    def is_fragment(self) -> bool:
        return bool(self.flags & RHD_FRAGMENT)


# --- transaction inventory page (TIP) ------------------------------------------

TIP_NEXT_OFFSET = PAG_SIZE           # SLONG tip_next
TIP_BITS_OFFSET = PAG_SIZE + 4       # далее 2 бита на транзакцию

TX_ACTIVE = 0
TX_LIMBO = 1
TX_DEAD = 2
TX_COMMITTED = 3

TX_STATE_NAMES = {TX_ACTIVE: "active", TX_LIMBO: "limbo",
                  TX_DEAD: "dead", TX_COMMITTED: "committed"}


def tip_transactions_per_page(page_size: int) -> int:
    return (page_size - TIP_BITS_OFFSET) * 4


def tx_state_from_tip(tip_buf: bytes, index_on_page: int) -> int:
    byte = tip_buf[TIP_BITS_OFFSET + index_on_page // 4]
    return (byte >> ((index_on_page % 4) * 2)) & 0x3


# --- dtype (dsc_dtype из dsc.h IB6) ---------------------------------------------

DTYPE_TEXT = 1        # CHAR(n)
DTYPE_CSTRING = 2
DTYPE_VARYING = 3     # VARCHAR(n): USHORT len + данные
DTYPE_PACKED = 6
DTYPE_BYTE = 7
DTYPE_SHORT = 8
DTYPE_LONG = 9
DTYPE_QUAD = 10
DTYPE_REAL = 11
DTYPE_DOUBLE = 12
DTYPE_D_FLOAT = 13
DTYPE_SQL_DATE = 14
DTYPE_SQL_TIME = 15
DTYPE_TIMESTAMP = 16
DTYPE_BLOB = 17
DTYPE_ARRAY = 18
DTYPE_INT64 = 19
DTYPE_BOOLEAN = 20    # InterBase 7+

DTYPE_NAMES = {
    DTYPE_TEXT: "text", DTYPE_CSTRING: "cstring", DTYPE_VARYING: "varying",
    DTYPE_PACKED: "packed", DTYPE_BYTE: "byte", DTYPE_SHORT: "short",
    DTYPE_LONG: "long", DTYPE_QUAD: "quad", DTYPE_REAL: "real",
    DTYPE_DOUBLE: "double", DTYPE_D_FLOAT: "d_float", DTYPE_SQL_DATE: "sql_date",
    DTYPE_SQL_TIME: "sql_time", DTYPE_TIMESTAMP: "timestamp",
    DTYPE_BLOB: "blob", DTYPE_ARRAY: "array", DTYPE_INT64: "int64",
    DTYPE_BOOLEAN: "boolean",
}
