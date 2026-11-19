"""错误分类 E01~E15。UI 只消费 code / message / hint，不解析异常字符串。"""

from __future__ import annotations

from dataclasses import dataclass


class ErrorCode:
    E01 = "E01"
    E02 = "E02"
    E03 = "E03"
    E04 = "E04"
    E05 = "E05"
    E06 = "E06"
    E07 = "E07"
    E08 = "E08"
    E09 = "E09"
    E10 = "E10"
    E11 = "E11"
    E12 = "E12"
    E13 = "E13"
    E14 = "E14"
    E15 = "E15"


ERROR_NAMES: dict[str, str] = {
    ErrorCode.E01: "连接失败",
    ErrorCode.E02: "认证失败",
    ErrorCode.E03: "读超时",
    ErrorCode.E04: "服务端错误",
    ErrorCode.E05: "集群异常",
    ErrorCode.E06: "哨兵发现失败",
    ErrorCode.E07: "SSH 隧道失败",
    ErrorCode.E08: "策略拒绝",
    ErrorCode.E09: "用户取消",
    ErrorCode.E10: "解码失败",
    ErrorCode.E11: "本地存储失败",
    ErrorCode.E12: "环境缺失",
    ErrorCode.E13: "审计不可写",
    ErrorCode.E14: "未认证/已锁定",
    ErrorCode.E15: "完整性校验失败",
}


@dataclass(slots=True)
class AppError(Exception):
    """统一应用错误。"""

    code: str
    message: str
    hint: str = ""
    retryable: bool = False

    def __str__(self) -> str:  # pragma: no cover - 展示用
        name = ERROR_NAMES.get(self.code, "")
        prefix = f"{self.code} {name}".strip()
        return f"{prefix}: {self.message}" if prefix else self.message


def app_error(
    code: str,
    message: str,
    hint: str = "",
    *,
    retryable: bool = False,
) -> AppError:
    return AppError(code=code, message=message, hint=hint, retryable=retryable)
