import struct

import pytest

from gdb2pg import blobs, catalog, ods

PAGE_SIZE = 1024
RELATION = 42


class FakePager:
    def __init__(self, pages):
        self.pages = pages
        self.page_size = PAGE_SIZE
        self.page_count = max(pages) + 1

    def page(self, number):
        return memoryview(self.pages[number])


def _page(page_type, flags=0):
    buf = bytearray(PAGE_SIZE)
    struct.pack_into(ods.PAG_FMT, buf, 0, page_type, flags, 0, 1, 0, 0)
    return buf


def _blob_fixture(raw_records, external_pages=None):
    pointer = _page(ods.PAG_BLOB_POINTER)
    struct.pack_into(ods.PPG_FMT, pointer, ods.PAG_SIZE,
                     0, 0, 1, RELATION, 0, 0)
    struct.pack_into("<i", pointer, ods.PPG_HEADER_SIZE, 2)

    data = _page(ods.PAG_BLOB_DATA)
    struct.pack_into(ods.DPG_FMT, data, ods.PAG_SIZE,
                     0, RELATION, len(raw_records))
    offset = PAGE_SIZE
    for index, raw in enumerate(raw_records):
        offset -= len(raw)
        data[offset:offset + len(raw)] = raw
        struct.pack_into(ods.DPG_REPEAT_FMT, data,
                         ods.DPG_HEADER_SIZE + index * ods.DPG_REPEAT_SIZE,
                         offset, len(raw))

    pages = {1: pointer, 2: data}
    pages.update(external_pages or {})
    table = catalog.Table(
        relation_id=RELATION,
        name="BLOBS",
        blob_pointer_pages=[1],
        blob_blocking_factor=10,
    )
    return FakePager(pages), table


def _header(*, flags=ods.RHD_BLOB, count=0, length=0, level=0,
            max_sequence=0, max_segment=0):
    return struct.pack(
        ods.BLH_FMT,
        0,
        max_sequence,
        max_segment,
        flags,
        0,
        count,
        length,
        0,
        0,
        level,
    )


def test_reads_inline_segmented_and_stream_blobs():
    segmented = _header(count=2, length=5, max_segment=3) + b"\x03\x00abc\x02\x00de"
    stream = _header(flags=ods.RHD_BLOB | ods.RHD_STREAM_BLOB,
                     length=4) + b"data"
    pager, table = _blob_fixture([segmented, stream])
    reader = blobs.BlobReader(pager, table)

    assert reader.read((RELATION, 0)) == b"abcde"
    assert reader.read((RELATION, 1)) == b"data"


def test_reads_level_one_blob_page():
    value = b"page data"
    encoded = struct.pack("<H", len(value)) + value
    external = _page(ods.PAG_BLOB)
    struct.pack_into(ods.BLP_FMT, external, ods.PAG_SIZE,
                     3, 0, len(encoded), 0)
    external[ods.BLP_HEADER_SIZE:ods.BLP_HEADER_SIZE + len(encoded)] = encoded
    header = (_header(count=1, length=len(value), level=1,
                      max_sequence=0, max_segment=len(value)) +
              struct.pack("<I", 3))
    pager, table = _blob_fixture([header], {3: external})

    assert blobs.BlobReader(pager, table).read((RELATION, 0)) == value


def test_blob_id_decodes_40_bit_record_number():
    value = ((7 << 24) | RELATION, 123)
    blob_id = blobs.BlobId.decode(value)
    assert blob_id.relation_id == RELATION
    assert blob_id.record_number == (7 << 32) | 123


def test_rejects_non_blob_record():
    pager, table = _blob_fixture([_header(flags=0)])
    with pytest.raises(blobs.BlobError, match="is not a BLOB"):
        blobs.BlobReader(pager, table).read((RELATION, 0))
