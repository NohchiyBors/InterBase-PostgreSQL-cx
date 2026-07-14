"""Интеграционный smoke: синтетический мини-GDB (ODS 10-раскладка) end-to-end."""

import struct

import pytest

from gdb2pg import catalog, ods, records, rle
from gdb2pg.pager import Pager

PAGE = 1024


def _pag(buf, ptype):
    struct.pack_into(ods.PAG_FMT, buf, 0, ptype, 0, 0, 1, 0, 0)


def build_mini_gdb(tmp_path):
    pages = [bytearray(PAGE) for _ in range(5)]

    # page 0: header, RDB$PAGES pointer page = 3
    _pag(pages[0], ods.PAG_HEADER)
    struct.pack_into(ods.HDR_FMT, pages[0], ods.PAG_SIZE,
                     PAGE, 15, 3, 0, 1, 2, 3, 0, 0, 0, 0, 1, 0,
                     70, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    pages[0][ods.HDR_DATA_OFFSET_ODS10] = ods.HDR_END

    # page 1: PIP; page 2: TIP (tx1, tx2 committed)
    _pag(pages[1], ods.PAG_PIP)
    _pag(pages[2], ods.PAG_TIP)
    pages[2][ods.TIP_BITS_OFFSET] = 0b00_11_11_00  # tx0 active, tx1/tx2 committed

    # page 3: pointer page отношения 0 (RDB$PAGES) -> data page 4
    _pag(pages[3], ods.PAG_POINTER)
    struct.pack_into(ods.PPG_FMT, pages[3], ods.PAG_SIZE, 0, 0, 1, 0, 0, 0)
    struct.pack_into("<i", pages[3], ods.PPG_HEADER_SIZE, 4)

    # page 4: data page отношения 0 с одной записью RDB$PAGES:
    # (page_number=3, relation_id=0, sequence=0, page_type=pointer)
    _pag(pages[4], ods.PAG_DATA)
    fmt = catalog.sysformats.RDB_PAGES
    row = bytearray(18)
    struct.pack_into("<i", row, 4, 3)   # RDB$PAGE_NUMBER
    struct.pack_into("<h", row, 8, 0)   # RDB$RELATION_ID
    struct.pack_into("<i", row, 12, 0)  # RDB$PAGE_SEQUENCE
    struct.pack_into("<h", row, 16, ods.PAG_POINTER)
    assert fmt.fields[-1].offset + fmt.fields[-1].length == len(row)

    payload = rle.compress(bytes(row))
    rec = struct.pack(ods.RHD_FMT, 1, 0, 0, 0, 0) + payload
    rec_off = PAGE - len(rec) - (len(rec) % 4)
    pages[4][rec_off: rec_off + len(rec)] = rec
    struct.pack_into(ods.DPG_FMT, pages[4], ods.PAG_SIZE, 0, 0, 1)
    struct.pack_into(ods.DPG_REPEAT_FMT, pages[4], ods.DPG_HEADER_SIZE,
                     rec_off, len(rec))

    path = tmp_path / "mini.gdb"
    path.write_bytes(b"".join(bytes(p) for p in pages))
    return str(path)


@pytest.fixture
def mini_gdb(tmp_path):
    return build_mini_gdb(tmp_path)


def test_pager_and_header(mini_gdb):
    with Pager(mini_gdb) as p:
        assert p.page_size == PAGE
        assert p.header.ods == "15.0"
        assert p.header.pages_page == 3
        census = p.census()
        assert census[ods.PAG_DATA] == 1
        assert census[ods.PAG_POINTER] == 1


def test_walk_relation_committed_tx(mini_gdb):
    with Pager(mini_gdb) as p:
        st = records.WalkStats()
        recs = list(records.walk_relation(p, 3, check_tx=True, stats=st))
        assert len(recs) == 1
        assert st.records == 1
        assert recs[0].header.transaction == 1


def test_read_rdb_pages(mini_gdb):
    with Pager(mini_gdb) as p:
        mapping = catalog.read_rdb_pages(p)
        assert mapping == {0: [(0, 3)]}


def test_build_schema_cross_check(mini_gdb):
    with Pager(mini_gdb) as p:
        schema = catalog.build_schema(p)
        assert schema.warnings == []
        assert 0 in schema.tables
        assert schema.tables[0].pointer_pages == [3]


def test_cli_inspect_and_schema(mini_gdb, capsys):
    from gdb2pg import cli
    assert cli.main(["inspect", mini_gdb]) == 0
    out = capsys.readouterr().out
    assert "ODS             : 15.0" in out
    assert cli.main(["schema", mini_gdb]) == 0
    out = capsys.readouterr().out
    assert "| 0 |" in out
