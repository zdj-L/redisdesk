"""命令分词：支持引号、转义与三引号；不使用 shlex.split。"""

from __future__ import annotations

from dataclasses import dataclass

from redisdesk.core.errors import AppError, ErrorCode


class ParseError(AppError):
    """解析失败，带列号。"""

    def __init__(self, message: str, column: int, hint: str = "") -> None:
        super().__init__(
            code=ErrorCode.E10,
            message=message,
            hint=hint or f"出错位置：第 {column} 列",
            retryable=False,
        )
        self.column = column


@dataclass(frozen=True, slots=True)
class ParseResult:
    argv: tuple[str, ...]
    command: str


_FORBIDDEN_UNQUOTED = frozenset(";|>")
# 单独处理 < 与 $VAR


def parse_command(raw: str) -> ParseResult:
    """把一行（或一段）控制台输入解析为 argv。

    命令名统一大写；参数保持原样。空输入返回空 argv（调用方不应发送）。
    """
    if raw is None:
        raise ParseError("输入为空", 1)
    text = raw
    if not text.strip():
        return ParseResult(argv=(), command="")

    tokens: list[str] = []
    i = 0
    n = len(text)

    def peek(k: int = 0) -> str:
        j = i + k
        return text[j] if j < n else ""

    def is_ws(ch: str) -> bool:
        return ch in " \t\r\n"

    while i < n:
        ch = text[i]
        if is_ws(ch):
            i += 1
            continue

        # 三引号
        if text.startswith('"""', i):
            token, i = _read_triple_quote(text, i)
            tokens.append(token)
            continue

        if ch in "'\"":
            token, i = _read_quoted(text, i)
            tokens.append(token)
            continue

        token, i = _read_bare(text, i)
        tokens.append(token)

    if not tokens:
        return ParseResult(argv=(), command="")

    command = tokens[0].upper()
    argv = (command, *tokens[1:])
    return ParseResult(argv=argv, command=command)


def _read_triple_quote(text: str, start: int) -> tuple[str, int]:
    """从 start（指向第一个引号）读取 \"\"\"...\"\"\"，保留真实换行。"""
    i = start + 3
    n = len(text)
    out: list[str] = []
    while i < n:
        if text.startswith('"""', i):
            return "".join(out), i + 3
        if text[i] == "\\" and i + 1 < n:
            ch, i = _unescape(text, i)
            out.append(ch)
            continue
        out.append(text[i])
        i += 1
    raise ParseError("三引号未闭合", start + 1, "请补全 \"\"\"")


def _read_quoted(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    i = start + 1
    n = len(text)
    out: list[str] = []
    while i < n:
        ch = text[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch == "\\":
            escaped, i = _unescape(text, i)
            out.append(escaped)
            continue
        out.append(ch)
        i += 1
    raise ParseError("引号未闭合", start + 1, f"请补全 {quote}")


def _read_bare(text: str, start: int) -> tuple[str, int]:
    i = start
    n = len(text)
    out: list[str] = []
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            break
        if ch in "'\"" or text.startswith('"""', i):
            break
        if ch in _FORBIDDEN_UNQUOTED or ch == "<":
            raise ParseError(
                f"未转义的元字符 {ch!r} 不被允许",
                i + 1,
                "本工具不支持管道、重定向或多条命令串联，请只输入一条 Redis 命令",
            )
        if ch == "$" and _looks_like_var(text, i):
            raise ParseError(
                "未转义的变量展开不被允许",
                i + 1,
                "如需字面量 $，请放在引号内或使用 \\\\ 转义",
            )
        if ch == "\\":
            escaped, i = _unescape(text, i)
            out.append(escaped)
            continue
        out.append(ch)
        i += 1
    if not out:
        raise ParseError("空词元", start + 1)
    return "".join(out), i


def _looks_like_var(text: str, i: int) -> bool:
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if nxt == "{":
        return True
    return bool(nxt) and (nxt.isalpha() or nxt == "_")


def _unescape(text: str, i: int) -> tuple[str, int]:
    """读取从反斜杠开始的转义，返回 (字符, 下一个下标)。"""
    n = len(text)
    if i + 1 >= n:
        raise ParseError("反斜杠转义不完整", i + 1)
    nxt = text[i + 1]
    if nxt == "x":
        if i + 3 >= n:
            raise ParseError("非法 \\x 转义", i + 1, "需要 \\xNN 两位十六进制")
        hexpart = text[i + 2 : i + 4]
        if any(c not in "0123456789abcdefABCDEF" for c in hexpart):
            raise ParseError("非法 \\x 转义", i + 1, "需要 \\xNN 两位十六进制")
        return chr(int(hexpart, 16)), i + 4
    mapping = {
        "\\": "\\",
        '"': '"',
        "'": "'",
        "n": "\n",
        "t": "\t",
        "r": "\r",
        " ": " ",
        "$": "$",
        ";": ";",
        "|": "|",
        ">": ">",
        "<": "<",
    }
    if nxt in mapping:
        return mapping[nxt], i + 2
    # 未识别的转义保留反斜杠本身，避免 Windows 路径被吃掉。
    return "\\" + nxt, i + 2
