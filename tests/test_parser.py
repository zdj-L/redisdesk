from redisdesk.core.parser import ParseError, parse_command


def test_simple_and_uppercases_command():
    result = parse_command("get foo")
    assert result.argv == ("GET", "foo")
    assert result.command == "GET"


def test_double_and_single_quotes_preserve_spaces():
    result = parse_command('SET key "hello world"')
    assert result.argv == ("SET", "key", "hello world")
    result = parse_command("SET key 'a b'")
    assert result.argv == ("SET", "key", "a b")


def test_escapes():
    result = parse_command(r'SET k hello\nworld')
    assert result.argv[2] == "hello\nworld"
    result = parse_command(r'SET k "say \"hi\""')
    assert result.argv[2] == 'say "hi"'
    result = parse_command(r"SET k \x41\x42")
    assert result.argv[2] == "AB"


def test_triple_quotes_keep_newlines():
    result = parse_command('SET k """line1\nline2"""')
    assert result.argv[2] == "line1\nline2"


def test_reject_unclosed_quote():
    try:
        parse_command('SET k "oops')
        assert False, "expected ParseError"
    except ParseError as exc:
        assert exc.column == 7
        assert exc.code == "E10"


def test_reject_illegal_hex():
    try:
        parse_command(r"SET k \xZZ")
        assert False
    except ParseError:
        pass
    try:
        parse_command(r"SET k \xA")
        assert False
    except ParseError:
        pass


def test_reject_unescaped_meta():
    for raw in ("GET a; SET b", "GET a | GET b", "GET a > out", "GET $FOO"):
        try:
            parse_command(raw)
            assert False, raw
        except ParseError:
            pass


def test_empty_input():
    assert parse_command("   \n\t  ").argv == ()


def test_windows_path_keeps_backslash():
    result = parse_command(r"SET k C:\Users\foo")
    assert result.argv[2] == r"C:\Users\foo"
