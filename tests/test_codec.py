from redisdesk.adapters.codec import decode_text, is_binary, to_hex_dump, try_pretty_json, preview


def test_text_is_not_binary():
    assert not is_binary(b"hello world\n")


def test_null_byte_is_binary():
    assert is_binary(b"abc\x00def")


def test_high_nonprint_ratio_is_binary():
    data = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 10)
    assert is_binary(data)


def test_hex_dump_format():
    text = to_hex_dump(b"AB")
    assert "41 42" in text
    assert "AB" in text.split()[-1] or text.endswith("AB")


def test_pretty_json_and_reject_non_json():
    pretty = try_pretty_json(b'{"a":1}')
    assert pretty is not None and '"a"' in pretty
    assert try_pretty_json(b"not-json") is None
    assert try_pretty_json(b"hello") is None


def test_decode_binary_switches_to_hex():
    text, flag = decode_text(b"\x00\x01\x02")
    assert flag is True
    assert "00 01 02" in text


def test_preview_truncates():
    assert preview("abc", 10) == "abc"
    assert preview("abcdefghijklmnopqrstuvwxyz", 5) == "abcde…"
