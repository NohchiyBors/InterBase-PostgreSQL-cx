"""Обход записей таблицы: pointer pages -> data pages -> записи.

M1: выдаём распакованные (RLE) байты primary-версий записей вместе с
заголовком; интерпретация полей — в types.py/catalog.py.
Фрагментированные записи (rhd_incomplete) в M1 подсчитываются, но не
собираются; сборка — M2 после калибровки RHDF-смещений.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from . import ods, rle
from .pager import Pager


@dataclass
class RawRecord:
    page: int
    slot: int
    header: ods.RecordHeader
    data: bytes  # распакованные байты записи (после rhd)


@dataclass
class WalkStats:
    data_pages: int = 0
    slots: int = 0
    empty_slots: int = 0
    deleted: int = 0
    blobs: int = 0
    fragments: int = 0
    incomplete: int = 0
    back_versions: int = 0
    versions_skipped: int = 0  # не-committed транзакции
    records: int = 0
    bad_pages: int = 0
    bad_page_numbers: list[int] = field(default_factory=list)


def _record_bad_page(stats: WalkStats, page_number: int) -> None:
    stats.bad_pages += 1
    if page_number not in stats.bad_page_numbers:
        stats.bad_page_numbers.append(page_number)


def pointer_chain(pager: Pager, first_pointer_page: int) -> Iterator[ods.PointerPage]:
    n = first_pointer_page
    seen: set[int] = set()
    while n and n not in seen:
        seen.add(n)
        buf = pager.page(n)
        if buf[0] != ods.PAG_POINTER:
            raise ValueError(f"page {n} is not a pointer page (type={buf[0]})")
        pp = ods.PointerPage.parse(buf)
        yield pp
        n = pp.next


def walk_relation(pager: Pager, first_pointer_page: int,
                  check_tx: bool = True,
                  stats: WalkStats | None = None) -> Iterator[RawRecord]:
    """Итерация primary-версий записей отношения.

    check_tx=True: запись отдаётся только если её транзакция committed по TIP.
    """
    st = stats if stats is not None else WalkStats()
    tip_chain = pager.tip_pages() if check_tx else []
    for pp in pointer_chain(pager, first_pointer_page):
        for dp_num in pp.pages:
            if dp_num == 0:
                continue
            try:
                buf = pager.page(dp_num)
                if buf[0] != ods.PAG_DATA:
                    _record_bad_page(st, dp_num)
                    continue
                dp = ods.DataPage.parse(buf)
            except Exception:
                _record_bad_page(st, dp_num)
                continue
            st.data_pages += 1
            for slot_idx, slot in enumerate(dp.slots):
                st.slots += 1
                if slot.length == 0:
                    st.empty_slots += 1
                    continue
                if slot.offset + slot.length > pager.page_size:
                    _record_bad_page(st, dp_num)
                    continue
                rec_buf = buf[slot.offset: slot.offset + slot.length]
                if slot.length < ods.RHD_SIZE:
                    continue
                hdr = ods.RecordHeader.parse(rec_buf)
                if hdr.flags & ods.RHD_CHAIN:
                    st.back_versions += 1
                    continue
                if hdr.is_blob:
                    st.blobs += 1
                    continue
                if hdr.is_fragment:
                    st.fragments += 1
                    continue  # фрагмент-продолжение; головная запись отдаётся отдельно
                if hdr.deleted:
                    st.deleted += 1
                    continue
                if check_tx:
                    state = pager.tx_state(hdr.transaction, tip_chain)
                    if state != ods.TX_COMMITTED:
                        st.versions_skipped += 1
                        continue
                if hdr.flags & ods.RHD_INCOMPLETE:
                    st.incomplete += 1
                    payload = _assemble_fragmented(pager, rec_buf, st)
                    if payload is None:
                        continue
                else:
                    payload = rle.decompress(rec_buf[ods.RHD_SIZE:])
                st.records += 1
                yield RawRecord(page=dp_num, slot=slot_idx, header=hdr, data=payload)


def _assemble_fragmented(pager: Pager, head_buf, st: WalkStats,
                         max_fragments: int = 1024) -> bytes | None:
    """Собрать фрагментированную запись: конкатенация сжатых кусков + RLE."""
    import struct

    parts = [bytes(head_buf[ods.RHDF_DATA_OFFSET:])]
    f_page, f_line = struct.unpack_from(ods.RHDF_F_PAGE_FMT, head_buf,
                                        ods.RHDF_F_PAGE_OFFSET)
    for _ in range(max_fragments):
        if not f_page:
            break
        try:
            buf = pager.page(f_page)
            if buf[0] != ods.PAG_DATA:
                _record_bad_page(st, f_page)
                return None
            dp = ods.DataPage.parse(buf)
            slot = dp.slots[f_line]
            frag = buf[slot.offset: slot.offset + slot.length]
            fh = ods.RecordHeader.parse(frag)
        except Exception:
            _record_bad_page(st, f_page)
            return None
        if not fh.is_fragment:
            return None
        if fh.flags & ods.RHD_INCOMPLETE:
            parts.append(bytes(frag[ods.RHDF_DATA_OFFSET:]))
            f_page, f_line = struct.unpack_from(ods.RHDF_F_PAGE_FMT, frag,
                                                ods.RHDF_F_PAGE_OFFSET)
        else:
            parts.append(bytes(frag[ods.RHD_SIZE:]))
            break
    return rle.decompress(b"".join(parts))


def find_pointer_pages(pager: Pager) -> dict[int, list[int]]:
    """Линейный скан: relation_id -> [pointer pages в порядке sequence].

    Резервный способ найти таблицы без чтения RDB$PAGES (используется probe
    и как кросс-проверка каталога).
    """
    rel_pages: dict[int, list[tuple[int, int]]] = {}
    for n in range(pager.page_count):
        if pager.page_type(n) != ods.PAG_POINTER:
            continue
        try:
            pp = ods.PointerPage.parse(pager.page(n))
        except Exception:
            continue
        rel_pages.setdefault(pp.relation, []).append((pp.sequence, n))
    return {rel: [pg for _, pg in sorted(v)] for rel, v in rel_pages.items()}


def find_blob_pointer_pages(pager: Pager) -> dict[int, list[int]]:
    """Линейный скан отдельных BLOB pointer pages InterBase ODS 15+."""
    rel_pages: dict[int, list[tuple[int, int]]] = {}
    for n in range(pager.page_count):
        if pager.page_type(n) != ods.PAG_BLOB_POINTER:
            continue
        try:
            pp = ods.PointerPage.parse(pager.page(n))
        except Exception:
            continue
        rel_pages.setdefault(pp.relation, []).append((pp.sequence, n))
    return {rel: [pg for _, pg in sorted(v)] for rel, v in rel_pages.items()}
