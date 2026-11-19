"""哨兵发现 + 主节点跟随（P1）。"""

from redisdesk.adapters.gateway import GatewayNotImplemented


class SentinelGateway(GatewayNotImplemented):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002,ANN003
        super().__init__("Sentinel")
