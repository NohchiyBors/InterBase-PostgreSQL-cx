"""Сборка BLOB-значений (этап M2).

Blob id в записи ссылается на blob-запись на data page той же таблицы
(level 0: сегменты inline; level 1/2: страницы указателей на blob pages).
Реализация включается после калибровки заголовка blob-записей на реальном
файле (`gdb2pg probe`): раскладка blh проверяется по ODS 15.
"""

from __future__ import annotations


class BlobReader:  # pragma: no cover - каркас M2
    def __init__(self, pager):
        self.pager = pager

    def read(self, blob_id: tuple[int, int]) -> bytes:
        raise NotImplementedError(
            "BLOB assembly — этап M2 (после калибровки blh на реальном GDB)")
