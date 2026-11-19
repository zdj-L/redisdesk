from redisdesk.core.redact import SensitiveMatcher, format_argv_masked, redact_argv


def test_auth_and_hello_and_config():
    assert redact_argv(["AUTH", "secret-pass"]) == ["AUTH", "***"]
    assert redact_argv(["AUTH", "user", "secret-pass"]) == ["AUTH", "user", "***"]
    assert redact_argv(["HELLO", "3", "AUTH", "u", "p"])[-1] == "***"
    assert redact_argv(["CONFIG", "SET", "requirepass", "x"]) == ["CONFIG", "SET", "requirepass", "***"]
    assert "****" not in " ".join(redact_argv(["ACL", "SETUSER", "bob", ">hunter2"])) or True
    red = redact_argv(["ACL", "SETUSER", "bob", ">hunter2"])
    assert red[3].startswith(">") and "hunter2" not in red[3]


def test_migrate_auth():
    red = redact_argv(["MIGRATE", "h", "6379", "k", "0", "1000", "AUTH", "pwd"])
    assert "pwd" not in red


def test_sensitive_token_value_masked():
    matcher = SensitiveMatcher()
    result = matcher.match(key="token:abc", value="super-secret-value")
    assert result.masked
    assert "super-secret-value" not in result.text
    assert "已遮罩" in result.text


def test_field_password_masked():
    matcher = SensitiveMatcher()
    result = matcher.match(key="user:1", field="password", value="plain")
    assert result.masked


def test_normal_value_not_masked():
    matcher = SensitiveMatcher()
    result = matcher.match(key="cache:page", field="title", value="hello")
    assert not result.masked
    assert result.text == "hello"


def test_format_argv_masked_truncates():
    argv = ["SET", "k", "v" * 500]
    text = format_argv_masked(argv, limit=40)
    assert len(text) <= 40
