"""RLE-декомпрессия записей InterBase.

Формат (jrd/sqz.c, InterBase 6.0): поток управляющих байтов (signed):
  n > 0  — следующие n байт копируются как есть;
  n < 0  — следующий 1 байт повторяется (-n) раз;
  n == 0 — пусто (пропускается).
"""

from __future__ import annotations


def decompress(data: bytes | memoryview, expected: int | None = None) -> bytes:
    out = bytearray()
    i = 0
    ln = len(data)
    while i < ln:
        n = data[i]
        i += 1
        if n == 0:
            continue
        if n < 128:  # положительный signed byte: литералы
            out += bytes(data[i: i + n])
            i += n
        else:        # отрицательный: повтор следующего байта
            count = 256 - n
            if i < ln:
                out += bytes([data[i]]) * count
                i += 1
        if expected is not None and len(out) >= expected:
            return bytes(out[:expected])
    return bytes(out)


def compress(data: bytes) -> bytes:
    """Простой компрессор для unit-тестов (не для записи в GDB)."""
    out = bytearray()
    i = 0
    ln = len(data)
    while i < ln:
        # ищем повтор
        run = 1
        while i + run < ln and data[i + run] == data[i] and run < 128:
            run += 1
        if run >= 3:
            out += bytes([256 - run, data[i]])
            i += run
            continue
        # литеральный блок до следующего повтора
        j = i
        while j < ln:
            r = 1
            while j + r < ln and data[j + r] == data[j] and r < 128:
                r += 1
            if r >= 3 or (j - i) >= 127:
                break
            j += r
        chunk = data[i:j] if j > i else data[i:i + 1]
        out += bytes([len(chunk)]) + chunk
        i += len(chunk)
    return bytes(out)
