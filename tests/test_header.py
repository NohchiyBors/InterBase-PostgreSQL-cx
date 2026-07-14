import struct

import pytest

from gdb2pg import ods


def make_header_page(page_size=4096, ods_version=15, ods_minor=0,
                     pages_page=3, next_tx=1000, oldest_active=990):
    buf = bytearray(page_size)
    struct.pack_into(ods.PAG_FMT, buf, 0, ods.PAG_HEADER, 0, 0, 1, 0, 0)
    struct.pack_into(
        ods.HDR_FMT, buf, ods.PAG_SIZE,
        page_size, ods_version, pages_page, 0,
        900, oldest_active, next_tx,
        0, 0, 0, 0, 1, 0,
        70, ods_minor, ods_minor, 0, 0, 0, 0,
        0, 0, 0, 0,
    )
    # один clumplet: sweep_interval (4 байта) + HDR_END
    pos = ods.HDR_DATA_OFFSET_ODS10
    buf[pos] = ods.HDR_SWEEP_INTERVAL
    buf[pos + 1] = 4
    struct.pack_into("<I", buf, pos + 2, 20000)
    buf[pos + 6] = ods.HDR_END
    return bytes(buf)


def test_parse_header():
    hp = ods.HeaderPage.parse(make_header_page())
    assert hp.page_size == 4096
    assert hp.ods == "15.0"
    assert hp.pages_page == 3
    assert hp.next_transaction == 1000
    assert hp.oldest_active == 990
    assert hp.clumplets == [(ods.HDR_SWEEP_INTERVAL, struct.pack("<I", 20000))]


def test_not_a_header_page():
    buf = bytearray(4096)
    buf[0] = ods.PAG_DATA
    with pytest.raises(ValueError):
        ods.HeaderPage.parse(bytes(buf))


def test_tip_states():
    page_size = 1024
    buf = bytearray(page_size)
    struct.pack_into(ods.PAG_FMT, buf, 0, ods.PAG_TIP, 0, 0, 1, 0, 0)
    # tx0=committed(3), tx1=active(0), tx2=dead(2), tx3=limbo(1)
    buf[ods.TIP_BITS_OFFSET] = 0b01_10_00_11
    assert ods.tx_state_from_tip(bytes(buf), 0) == ods.TX_COMMITTED
    assert ods.tx_state_from_tip(bytes(buf), 1) == ods.TX_ACTIVE
    assert ods.tx_state_from_tip(bytes(buf), 2) == ods.TX_DEAD
    assert ods.tx_state_from_tip(bytes(buf), 3) == ods.TX_LIMBO


def test_pointer_page_parse():
    page_size = 1024
    buf = bytearray(page_size)
    struct.pack_into(ods.PAG_FMT, buf, 0, ods.PAG_POINTER, 0, 0, 1, 0, 0)
    struct.pack_into(ods.PPG_FMT, buf, ods.PAG_SIZE, 0, 0, 3, 42, 0, 0)
    struct.pack_into("<3i", buf, ods.PPG_HEADER_SIZE, 10, 11, 12)
    pp = ods.PointerPage.parse(bytes(buf))
    assert pp.relation == 42
    assert pp.pages == [10, 11, 12]


def test_data_page_parse():
    page_size = 1024
    buf = bytearray(page_size)
    struct.pack_into(ods.PAG_FMT, buf, 0, ods.PAG_DATA, 0, 0, 1, 0, 0)
    struct.pack_into(ods.DPG_FMT, buf, ods.PAG_SIZE, 0, 42, 2)
    struct.pack_into(ods.DPG_REPEAT_FMT, buf, ods.DPG_HEADER_SIZE, 900, 20)
    struct.pack_into(ods.DPG_REPEAT_FMT, buf, ods.DPG_HEADER_SIZE + 4, 940, 0)
    dp = ods.DataPage.parse(bytes(buf))
    assert dp.relation == 42
    assert dp.count == 2
    assert dp.slots[0].offset == 900 and dp.slots[0].length == 20
    assert dp.slots[1].length == 0
