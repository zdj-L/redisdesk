"""槽路由 + 逐节点扫描（P1）。"""

from redisdesk.adapters.gateway import GatewayNotImplemented


class ClusterGateway(GatewayNotImplemented):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002,ANN003
        super().__init__("Cluster")
