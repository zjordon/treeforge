"""发现 Chrome 的 WebSocket 调试 URL（ws_url）。

Chrome 以 --remote-debugging-port=9222 启动后，可通过 HTTP GET /json/version
拿到 webSocketDebuggerUrl，供 CDPClient 连接。

用 stdlib urllib（不引 httpx/requests，符合 treeforge 不引额外依赖原则）。
"""

from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_CDP_HOST = "localhost"
DEFAULT_CDP_PORT = 9222


def fetch_ws_url(host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT, timeout: float = 5.0) -> str | None:
    """从 Chrome 的 /json/version 拿 webSocketDebuggerUrl。

    Returns:
        ws_url（如 ws://localhost:9222/devtools/browser/xxx）；Chrome 未启动或不可达返回 None。
    """
    url = f"http://{host}:{port}/json/version"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ws_url = data.get("webSocketDebuggerUrl")
        if ws_url:
            logger.debug("Discovered ws_url: %s", ws_url)
            return ws_url
        logger.warning("/json/version 响应无 webSocketDebuggerUrl：%s", data)
        return None
    except Exception as e:  # noqa: BLE001 - 连接失败返回 None（Chrome 未启动）
        logger.warning(
            "无法连接 Chrome（%s）。确认以 --remote-debugging-port=%d 启动：%s",
            url, port, e,
        )
        return None
