"""轻量 CDP 会话：只做「连 CDP + get_state」，不含 agent 动作执行。

替代 TreeWalker 的 BrowserSession（3818 行大类，含 700 行 agent 动作执行 JS 焊死）。
采集层只需要「读 DOM 采快照 + 取 url/title」，不需要点击/输入/上传等动作执行能力，
所以这里只保留 CDP 握手 + 委托 dom-snapshot.build_dom_state。

【设计依据】docs/p2/README.md 3.2.3 节 cdp_session.py。
【实时采集原则】录制时每个扩展事件触发一次 get_state，趁 modal 打开 DOM 是活的采快照；
  不能挪到 stop（modal 已关 DOM 变了）。与 TreeWalker recorder.py:17-18 同源原则。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from cdp_use import CDPClient
from dom_snapshot import EMPTY_DOM_STATE, SerializedDOMState, build_dom_state

logger = logging.getLogger(__name__)


@dataclass
class CaptureState:
    """采集层需要的页面状态：url/title + DOM 快照。

    比 TreeWalker 的 BrowserStateSummary 精简——采集不需要 tabs/screenshot/recent_events。
    """

    url: str
    title: str
    dom_state: SerializedDOMState


class CdpSession:
    """轻量 CDP 会话：只做「连 CDP + get_state」。

    对比 TreeWalker BrowserSession（session.py:1115，3818 行）：
    - 保留：CDP 握手（start/_connect）、page target 发现、enable Page/DOM 域、get_state
    - 删除：click/input/navigate/upload 等动作执行（700 行 JS）、截图、高亮、下载跟踪、
      file-chooser intercept（录制反而要禁用）、网络空闲追踪、熔断器、tab 管理
    - get_state 委托 dom-snapshot.build_dom_state（与 TreeWalker 同源，保证快照格式一致）

    用法：
        session = CdpSession(ws_url="ws://localhost:9222/...")
        await session.start()
        state = await session.get_state()  # CaptureState(url, title, dom_state)
        await session.stop()
    """

    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self.client: CDPClient | None = None
        self.current_target_id: str | None = None
        self.current_session_id: str | None = None
        # 轮转缓存：上一次 selector_map，供 dom-snapshot 检测新元素（* 标记）
        self._previous_selector_map: dict | None = None

    async def start(self) -> None:
        """连 CDP，发现 page target，attach，enable Page/DOM 域。

        参照 TreeWalker session.py:_connect（1203-1269），但去掉熔断器/网络空闲/高亮/file-chooser。
        Chrome 需以 --remote-debugging-port=9222 启动。

        target 选择：优先 http/https 真实页面，跳过 chrome-extension://（popup）、
        chrome://（内部页）、devtools://。避免连到扩展 popup 导致采错 DOM。
        """
        self.client = CDPClient(self.ws_url)
        await self.client.start()

        # 发现 page target 并 attach
        # 策略：优先选 http/https 真实页面；跳过 chrome-extension://（popup）、
        # chrome://（内部页）、devtools://。否则会连到扩展 popup 采到错误 DOM。
        targets = await self.client.send.Target.getTargets({})
        page_targets = [t for t in targets.get("targetInfos", []) if t.get("type") == "page"]
        # 按优先级排序：http/https 优先，其它（chrome-extension/chrome/devtools）排后
        real_pages = [t for t in page_targets if t.get("url", "").startswith(("http://", "https://"))]
        # 优先选真实页面；没有才退而求其次（可能用户在 chrome:// 页面操作）
        candidates = real_pages or page_targets
        if candidates:
            t = candidates[0]
            self.current_target_id = t["targetId"]
            result = await self.client.send.Target.attachToTarget(
                {"targetId": self.current_target_id, "flatten": True},
            )
            self.current_session_id = result["sessionId"]

        if not self.current_session_id:
            raise RuntimeError(
                "No page target found. Is Chrome running with --remote-debugging-port?"
            )

        # enable 必需的 CDP 域（Page/DOM）。Network 可选（采集不需要网络空闲追踪）。
        await self.client.send.Page.enable({}, session_id=self.current_session_id)
        await self.client.send.DOM.enable({}, session_id=self.current_session_id)

        # 跨源 iframe 自动 attach（让 dom-snapshot 能采到 iframe 内 DOM）
        try:
            await self.client.send.Target.setAutoAttach(
                {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                session_id=self.current_session_id,
            )
        except Exception as e:  # noqa: BLE001 - setAutoAttach 失败降级（老 Chrome 可能不支持）
            logger.warning("Target.setAutoAttach failed (degrading): %s", e)

        # 录制要禁用 file-chooser intercept（让原生 picker 弹出，用户能手动选文件）。
        # 与 TreeWalker 相反（TW agent 要 intercept 拦截 picker）。best-effort。
        try:
            await self.client.send.Page.setInterceptFileChooserDialog(
                {"enabled": False}, session_id=self.current_session_id
            )
        except Exception as e:  # noqa: BLE001 - 老版本 Chrome 可能无此命令
            logger.debug("setInterceptFileChooserDialog(False) failed: %s", e)

        logger.info("CdpSession connected: target=%s", self.current_target_id)

    async def get_state(self) -> CaptureState:
        """取页面状态：url/title + DOM 快照（委托 dom-snapshot.build_dom_state）。

        参照 TreeWalker session.py:get_state（1547-1613），去掉截图/网络空闲/熔断器。
        每次调用更新轮转缓存（_previous_selector_map），供下次新元素检测。
        """
        if not self.client or not self.current_session_id:
            raise RuntimeError("CdpSession not started")

        sid = self.current_session_id
        prev_map = self._previous_selector_map

        # 取 url/title（与 TreeWalker 同方式：Runtime.evaluate JSON）
        url = ""
        title = ""
        try:
            result = await self.client.send.Runtime.evaluate(
                {
                    "expression": "JSON.stringify({url: location.href, title: document.title})",
                    "returnByValue": True,
                },
                session_id=sid,
            )
            info = json.loads(result["result"]["value"])
            url = info.get("url", "")
            title = info.get("title", "")
        except Exception as e:  # noqa: BLE001 - url/title 失败不阻断 DOM 采集
            logger.warning("Failed to read url/title: %s", e)

        # DOM 快照（委托 dom-snapshot，与 TreeWalker 同源）
        try:
            dom_state, _metrics = await build_dom_state(
                self.client, session_id=sid, previous_selector_map=prev_map
            )
        except Exception as e:  # noqa: BLE001 - DOM 采集失败用空状态兜底，不阻断录制
            logger.error("build_dom_state raised: %s", e)
            dom_state = EMPTY_DOM_STATE

        # 更新轮转缓存
        self._previous_selector_map = dom_state.selector_map if dom_state else None

        return CaptureState(url=url, title=title, dom_state=dom_state)

    async def stop(self) -> None:
        """断开 CDP 连接。"""
        if self.client:
            try:
                await self.client.stop()
            except Exception as e:  # noqa: BLE001 - stop 失败不报错（可能已断）
                logger.debug("CdpSession stop failed: %s", e)
            finally:
                self.client = None
                self.current_target_id = None
                self.current_session_id = None
