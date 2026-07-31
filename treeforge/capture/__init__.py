"""TreeForge 采集层（P2）。

采集层产出两类文件（docs/p2/README.md 3.3 节）：
  - trace（用户行为痕迹，含确定 stage，无 ?）
  - 快照（DOM 文本，page_context）

模块：
  - cdp_session.py  轻量 CDP 包装（连浏览器 + get_state 委托 dom-snapshot）
  - backend.py      aiohttp 收扩展事件（通用 envelope + scenario 路由）

后续步骤（P2.2.2+）：
  - collector.py    采集器主类
  - stage.py        阶段切换判定 + 自动命名
  - export.py       导出 trace + 快照双文件
"""

from __future__ import annotations

from treeforge.capture.backend import DEFAULT_HOST, DEFAULT_PORT, CaptureBackend, CollectorLike
from treeforge.capture.cdp_session import CaptureState, CdpSession

__all__ = [
    "CdpSession",
    "CaptureState",
    "CaptureBackend",
    "CollectorLike",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]
