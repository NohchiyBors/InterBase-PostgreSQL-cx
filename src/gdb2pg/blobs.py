"""Чтение постоянных InterBase BLOB без подключения к серверу."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import ods

if TYPE_CHECKING:
    from .catalog import Table
    from .pager import Pager


class BlobError(ValueError):
    """BLOB-ссылка или физическая цепочка повреждена/не поддерживается."""


@dataclass(frozen=True)
class BlobId:
    relation_id: int
    record_number: int

    @classmethod
    def decode(cls, value: tuple[int, int]) -> "BlobId":
        """Packed bid: relation:16, reserved:8, record high:8, record low:32."""
        high, low = value
        relation_id = high & 0xFFFF
        reserved = (high >> 16) & 0xFF
        if reserved:
            raise BlobError(f"unsupported BLOB relation extension: {reserved}")
        record_number = low | (((high >> 24) & 0xFF) << 32)
        return cls(relation_id=relation_id, record_number=record_number)


class BlobReader:
    def __init__(self, pager: "Pager", table: "Table"):
        self.pager = pager
        self.table = table

    def read(self, value: tuple[int, int]) -> bytes:
        blob_id = BlobId.decode(value)
        if blob_id.relation_id != self.table.relation_id:
            raise BlobError(
                f"BLOB relation mismatch: {blob_id.relation_id} != "
                f"{self.table.relation_id}"
            )
        header, raw = self._read_header(blob_id.record_number)
        if header.level == 0:
            encoded = raw[ods.BLH_SIZE:]
        elif header.level in (1, 2):
            encoded = self._read_external_pages(header, raw)
        else:
            raise BlobError(f"unsupported BLOB level: {header.level}")
        return self._decode_segments(header, encoded)

    def _read_header(self, record_number: int) -> tuple[ods.BlobHeader, bytes]:
        dedicated = bool(self.table.blob_pointer_pages)
        default_factor = (ods.default_blob_blocking_factor(self.pager.page_size)
                          if dedicated else
                          ods.max_records_per_data_page(self.pager.page_size))
        factor = self.table.blob_blocking_factor or default_factor
        if factor <= 0:
            raise BlobError(f"invalid BLOB blocking factor: {factor}")
        data_sequence, line = divmod(record_number, factor)

        pointer_pages = (self.table.blob_pointer_pages if dedicated
                         else self.table.pointer_pages)
        data_page_number = self._data_page_number(pointer_pages, data_sequence)
        expected_type = ods.PAG_BLOB_DATA if dedicated else ods.PAG_DATA
        try:
            page_buf = self.pager.page(data_page_number)
            if page_buf[0] != expected_type:
                raise BlobError(
                    f"BLOB data page {data_page_number} has type {page_buf[0]}, "
                    f"expected {expected_type}"
                )
            data_page = ods.DataPage.parse(page_buf)
            if data_page.sequence != data_sequence:
                raise BlobError(
                    f"BLOB data page sequence mismatch: {data_page.sequence} != "
                    f"{data_sequence}"
                )
            if line >= len(data_page.slots):
                raise BlobError(
                    f"BLOB slot {line} is outside page {data_page_number} "
                    f"(count={data_page.count})"
                )
            slot = data_page.slots[line]
            if slot.length == 0 or slot.offset + slot.length > self.pager.page_size:
                raise BlobError(f"invalid BLOB slot {data_page_number}:{line}")
            raw = bytes(page_buf[slot.offset:slot.offset + slot.length])
            header = ods.BlobHeader.parse(raw)
        except BlobError:
            raise
        except Exception as exc:
            raise BlobError(
                f"cannot read BLOB header {data_page_number}:{line}: {exc}"
            ) from exc
        if not (header.flags & ods.RHD_BLOB):
            raise BlobError(
                f"record {data_page_number}:{line} is not a BLOB "
                f"(flags={header.flags})"
            )
        return header, raw

    def _data_page_number(self, pointer_pages: list[int], sequence: int) -> int:
        remaining = sequence
        for pointer_page_number in pointer_pages:
            try:
                pp = ods.PointerPage.parse(self.pager.page(pointer_page_number))
            except Exception as exc:
                raise BlobError(
                    f"cannot read BLOB pointer page {pointer_page_number}: {exc}"
                ) from exc
            if remaining < len(pp.pages):
                page_number = pp.pages[remaining]
                if page_number == 0:
                    raise BlobError(
                        f"empty BLOB data page pointer at sequence {sequence}"
                    )
                return page_number
            remaining -= len(pp.pages)
        raise BlobError(f"BLOB data page sequence {sequence} is outside pointer chain")

    def _read_external_pages(self, header: ods.BlobHeader, raw: bytes) -> bytes:
        page_count = header.max_sequence + 1
        if page_count <= 0 or page_count > self.pager.page_count:
            raise BlobError(f"invalid BLOB page count: {page_count}")

        if header.level == 1:
            data_pages = self._unpack_page_vector(raw, page_count)
        else:
            pointers_per_page = (self.pager.page_size - ods.BLP_HEADER_SIZE) // 4
            pointer_count = (page_count + pointers_per_page - 1) // pointers_per_page
            pointer_pages = self._unpack_page_vector(raw, pointer_count)
            data_pages = []
            for page_number in pointer_pages:
                page, buf = self._blob_page(page_number, pointers=True)
                count = page.length // 4
                data_pages.extend(struct.unpack_from(f"<{count}I", buf,
                                                     ods.BLP_HEADER_SIZE))
            data_pages = data_pages[:page_count]

        chunks = []
        for sequence, page_number in enumerate(data_pages):
            page, buf = self._blob_page(page_number, pointers=False)
            if page.sequence != sequence:
                raise BlobError(
                    f"BLOB page {page_number} sequence mismatch: "
                    f"{page.sequence} != {sequence}"
                )
            chunks.append(bytes(buf[ods.BLP_HEADER_SIZE:
                                    ods.BLP_HEADER_SIZE + page.length]))
        return b"".join(chunks)

    @staticmethod
    def _unpack_page_vector(raw: bytes, count: int) -> list[int]:
        needed = ods.BLH_SIZE + count * 4
        if len(raw) < needed:
            raise BlobError(f"short BLOB page vector: {len(raw)} < {needed}")
        return list(struct.unpack_from(f"<{count}I", raw, ods.BLH_SIZE))

    def _blob_page(self, page_number: int, pointers: bool):
        try:
            buf = self.pager.page(page_number)
            if buf[0] != ods.PAG_BLOB:
                raise BlobError(
                    f"page {page_number} has type {buf[0]}, expected {ods.PAG_BLOB}"
                )
            page = ods.BlobPage.parse(buf)
        except BlobError:
            raise
        except Exception as exc:
            raise BlobError(f"cannot read BLOB page {page_number}: {exc}") from exc
        is_pointer = bool(page.pag.flags & ods.BLP_POINTERS)
        if is_pointer != pointers:
            kind = "pointer" if pointers else "data"
            raise BlobError(f"BLOB page {page_number} is not a {kind} page")
        return page, buf

    @staticmethod
    def _decode_segments(header: ods.BlobHeader, encoded: bytes) -> bytes:
        if header.flags & ods.RHD_STREAM_BLOB:
            if len(encoded) < header.length:
                raise BlobError(
                    f"short stream BLOB: {len(encoded)} < {header.length}"
                )
            return bytes(encoded[:header.length])

        position = 0
        segments = []
        for _ in range(header.count):
            if position + 2 > len(encoded):
                raise BlobError("truncated BLOB segment length")
            (length,) = struct.unpack_from("<H", encoded, position)
            position += 2
            if position + length > len(encoded):
                raise BlobError("truncated BLOB segment data")
            segments.append(encoded[position:position + length])
            position += length
        value = b"".join(segments)
        if len(value) != header.length:
            raise BlobError(
                f"BLOB length mismatch: decoded={len(value)}, header={header.length}"
            )
        return value
