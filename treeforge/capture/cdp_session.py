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


def _url_host_path(url: str) -> str:
    """规范化 url 为 host + path（去 query/hash），用于 target url 匹配。

    例：``https://x.com/a?b=1#top`` → ``x.com/a``。非 http(s) 返回空串。
    容忍导航中的 url 细微差异（query 变化不影响 target 归属）。
    """
    if not url:
        return ""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    return f"{parsed.hostname or ''}{parsed.path or ''}"


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
        session = CdpSession(ws_url="ws://localhost:9223/...")
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
        # 已 attach 的 chrome tabId（与 current_target_id 对应），用于跟随用户切 tab。
        # None = 未按 tabId attach（用 start 的 eager fallback）。
        self.current_tab_id: int | None = None

    async def start(self) -> None:
        """连 CDP（browser-level ws），eager attach 首个 http target 作 fallback。

        参照 TreeWalker session.py:_connect（1203-1269），但去掉熔断器/网络空闲/高亮/file-chooser。
        Chrome 需以 --remote-debugging-port=9223 启动（默认端口，见 ws_discover.DEFAULT_CDP_PORT）。

        target 选择（eager fallback）：优先 http/https 真实页面，跳过 chrome-extension://（popup）、
        chrome://（内部页）、devtools://。这个 eager attach 是给「无 tab_id 的老流程」兜底；
        有 tab_id 的事件会触发 attach_tab 精确重 attach（见 collector.ingest）。
        """
        self.client = CDPClient(self.ws_url)
        await self.client.start()

        # eager attach：选首个真实页面 target（fallback，无 tab_id 时用）
        target = await self._find_first_http_target()
        if target:
            await self._attach_and_enable(target["targetId"])
            logger.info("CdpSession eager-attached: target=%s", self.current_target_id)
        else:
            # 无 page target 也允许 start（等首个事件带 tab_id 时再 attach）
            logger.info("CdpSession connected, no page target yet (waiting for tab_id)")

    async def attach_tab(self, tab_id: int | None, url: str | None = None) -> bool:
        """精确 attach 指定 chrome tab 的 CDP target。

        定位策略（两道，tabId 优先，url 兜底）：
          1. 按 tabId 匹配（Target.getTargets 的 tabId 字段，与 chrome.tabs API 同 id 空间）。
          2. tabId 缺失或找不到时，按 url 匹配——collector 知道每个事件的真实页面 url
             （content script 报的），用它找 url 匹配的 http page target。

        【为什么加 url 兜底】部分 Chrome 环境（如某些启动方式/版本）CDP 不填 tabId 字段
        （实测 tabId=None），导致纯 tabId 匹配完全失效，tab 跟随瘫痪，快照采到错误的 target
        （如扩展 popup）。url 兜底让 target 选择不依赖 tabId 这单一不稳定字段。

        若已是当前 target（幂等）跳过；否则 detach 旧的、attach 新的 + enable 域。

        Returns: True 表示已 attach 到目标 tab；False 表示找不到对应 target。
        """
        if not self.client:
            return False
        # 幂等：已是当前 tab 不重 attach
        if tab_id == self.current_tab_id and self.current_session_id:
            return True

        target = await self._find_target_by_tab_id(tab_id, url)
        if not target:
            logger.warning(
                "attach_tab: no target for tabId=%s url=%s (fallback to current)",
                tab_id,
                (url or "")[:60],
            )
            return False

        await self._attach_and_enable(target["targetId"])
        self.current_tab_id = tab_id
        logger.info("CdpSession attached tab=%s target=%s", tab_id, self.current_target_id)
        return True

    async def _find_first_http_target(self) -> dict | None:
        """发现首个 http/https page target（跳过 chrome-extension/chrome/devtools）。"""
        if not self.client:
            return None
        targets = await self.client.send.Target.getTargets({})
        page_targets = [t for t in targets.get("targetInfos", []) if t.get("type") == "page"]
        real_pages = [
            t for t in page_targets if t.get("url", "").startswith(("http://", "https://"))
        ]
        candidates = real_pages or page_targets
        return candidates[0] if candidates else None

    async def _find_target_by_tab_id(
        self, tab_id: int | None, url: str | None = None
    ) -> dict | None:
        """按 chrome tabId 找 CDP page target；tabId 缺失/找不到时按 url 匹配。

        tabId 是 Target.getTargets 的实验性字段（近年 Chrome 稳定支持），但部分环境不填
        （实测 tabId=None）。url 兜底：collector 传事件真实页面 url，按 host+path 规范化匹配
        http/https page target（忽略 query/hash，容忍导航中的 url 细微差异）。

        两道都试：tabId 命中直接返回；否则按 url 匹配（都找不到返回 None）。
        """
        if not self.client:
            return None
        targets = await self.client.send.Target.getTargets({})
        page_targets = [t for t in targets.get("targetInfos", []) if t.get("type") == "page"]

        # 1. 按 tabId 匹配（tabId 非空才试——None == None 会误命中无 tabId 的 target）
        if tab_id is not None:
            for t in page_targets:
                if t.get("tabId") == tab_id:
                    return t

        # 2. 按 url 匹配（tabId 缺失/找不到时兜底）：只认 http/https page，host+path 规范化
        if url:
            target_key = _url_host_path(url)
            if target_key:  # url 是有效 http(s) 才匹配（跳过 chrome:// 等）
                best: dict | None = None
                best_len = -1
                for t in page_targets:
                    t_url = t.get("url", "")
                    t_key = _url_host_path(t_url)
                    if not t_key:
                        continue
                    # 完全匹配 host+path 最优；否则取 host 匹配里 path 最长的（最相近的页面）
                    if t_key == target_key:
                        return t
                    if t_key.split("/")[0] == target_key.split("/")[0]:  # 同 host
                        # 记 host 匹配的候选（path 最长的优先，近似度最高）
                        tpath_len = len(t_key)
                        if tpath_len > best_len:
                            best_len = tpath_len
                            best = t
                if best:
                    return best
        return None

    async def _attach_and_enable(self, target_id: str) -> None:
        """attach 指定 target + enable Page/DOM 域 + setAutoAttach + 禁 file-chooser。

        切 tab 时会被多次调用：先 attach 新 target（旧的 sessionId 自然废弃）。
        """
        if not self.client:
            return
        self.current_target_id = target_id
        result = await self.client.send.Target.attachToTarget(
            {"targetId": target_id, "flatten": True},
        )
        self.current_session_id = result["sessionId"]

        # enable 必需的 CDP 域（Page/DOM）
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

        # 录制要禁用 file-chooser intercept（让原生 picker 弹出，用户能手动选文件）
        try:
            await self.client.send.Page.setInterceptFileChooserDialog(
                {"enabled": False}, session_id=self.current_session_id
            )
        except Exception as e:  # noqa: BLE001 - 老版本 Chrome 可能无此命令
            logger.debug("setInterceptFileChooserDialog(False) failed: %s", e)

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
                self.current_tab_id = None
        # 清轮转缓存（无条件）：serve 长期运行时跨 session 不清，会让下次首屏 DOM 把旧
        # selector_map 当「上一次」对比，新元素检测（* 标记）失真。即使 client 已断也清。
        self._previous_selector_map = None
