from gdb2pg import rle


def test_roundtrip_simple():
    data = b"hello world" + b"\x00" * 50 + b"tail"
    assert rle.decompress(rle.compress(data)) == data


def test_literal_run():
    # 3 литерала
    assert rle.decompress(bytes([3]) + b"abc") == b"abc"


def test_repeat_run():
    # -5 -> повторить 'x' 5 раз (256-5=251)
    assert rle.decompress(bytes([251, ord("x")])) == b"xxxxx"


def test_mixed():
    stream = bytes([2]) + b"ab" + bytes([253, 0]) + bytes([1]) + b"z"
    assert rle.decompress(stream) == b"ab" + b"\x00" * 3 + b"z"


def test_expected_cutoff():
    stream = bytes([251, ord("x")])
    assert rle.decompress(stream, expected=3) == b"xxx"


def test_zero_control_byte_skipped():
    stream = bytes([0, 2]) + b"ok"
    assert rle.decompress(stream) == b"ok"
