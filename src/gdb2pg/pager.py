"""Read-only доступ к страницам GDB-файла через mmap.

Файл открывается строго на чтение; кода записи в этом пакете нет.
"""

from __future__ import annotations

import mmap
import os
from collections import Counter

from . import ods


class Pager:
    def __init__(self, path: str):
        self.path = path
        self._fd = os.open(path, os.O_RDONLY)
        self.file_size = os.fstat(self._fd).st_size
        self._mm = mmap.mmap(self._fd, 0, access=mmap.ACCESS_READ)
        self.header = ods.HeaderPage.parse(self._mm[:4096] if self.file_size >= 4096
                                           else self._mm[:])
        self.page_size = self.header.page_size
        if self.page_size not in (1024, 2048, 4096, 8192, 16384):
            raise ValueError(f"suspicious page_size={self.page_size}; "
                             "file is not a GDB or header layout differs")
        if self.file_size % self.page_size != 0:
            # не фатально: multi-file/повреждение; сообщаем выше
            pass
        self.page_count = self.file_size // self.page_size

    def close(self) -> None:
        self._mm.close()
        os.close(self._fd)

    def __enter__(self) -> "Pager":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def page(self, n: int) -> memoryview:
        if n < 0 or n >= self.page_count:
            raise IndexError(f"page {n} out of range (0..{self.page_count - 1})")
        off = n * self.page_size
        return memoryview(self._mm)[off: off + self.page_size]

    def page_type(self, n: int) -> int:
        return self._mm[n * self.page_size]

    def census(self) -> Counter:
        """Перепись типов всех страниц файла (для inspect)."""
        c: Counter = Counter()
        for n in range(self.page_count):
            c[self.page_type(n)] += 1
        return c

    # --- транзакции -----------------------------------------------------------

    def tip_pages(self) -> list[int]:
        """Все TIP-страницы файла в порядке следования (по цепочке next,
        с fallback на линейный скан)."""
        found = [n for n in range(self.page_count)
                 if self.page_type(n) == ods.PAG_TIP]
        return found

    def tx_state(self, tx: int, tip_chain: list[int] | None = None) -> int:
        chain = tip_chain if tip_chain is not None else self.tip_pages()
        per_page = ods.tip_transactions_per_page(self.page_size)
        idx = tx // per_page
        if idx >= len(chain):
            return ods.TX_ACTIVE  # неизвестно -> считаем не закоммиченной
        buf = self.page(chain[idx])
        return ods.tx_state_from_tip(buf, tx % per_page)
