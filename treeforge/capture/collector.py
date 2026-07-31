"""采集器主类：收扩展事件 → get_state → 加 stage → 累积 TraceEvent（P2.2.2）。

【设计依据】docs/p2/README.md 3.2.3 节 collector.py。
【职责】
  1. 收 backend 转发的 CaptureEnvelope（scenario=distill）
  2. 每个事件触发 CdpSession.get_state（实时采集，趁 DOM 活的）
  3. 判定阶段切换（StageTracker），stage 确定绑定（无 ?）
  4. 转 DistillEventPayload → TraceEvent，累积进 trace

【实时采集原则】（对齐 TreeWalker recorder.py:17-18）
  快照必须在事件到达时采集，不能挪到 stop（modal 打开时 DOM 是活的）。

【与 TreeWalker Recorder 的区别】
  - browser 用轻量 CdpSession（非 BrowserSession）
  - 不产 rerun-history，累积 TraceEvent 列表（由 export.py 落盘）
  - 每事件取 element_tree_text + 判 stage（TW Recorder 丢弃 element_tree_text）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from treeforge.capture.cdp_session import CdpSession
from treeforge.capture.distill_schema import payload_to_trace_fields
from treeforge.capture.stage import StageTracker

logger = logging.getLogger(__name__)


@dataclass
class CapturedEvent:
    """采集到的一个事件（对应一个扩展事件 + 采集时的页面状态）。

    落盘前累积在 collector 里，export.py 转成 TraceEvent + page_context。
    """

    # TraceEvent 字段（从 payload 转换）
    type: str
    target: str | None = None
    element_attrs: dict[str, Any] = field(default_factory=dict)
    value: str | None = None
    key: str | None = None
    url: str | None = None
    timestamp: int = 0

    # 采集时绑定（P2.2.3）：确定的 stage，无 ?
    stage: str | None = None


@dataclass
class CaptureSession:
    """一次采集会话的状态。"""

    scenario: str
    session_id: str
    task_instruction: str = ""
    host: str = ""
    events: list[CapturedEvent] = field(default_factory=list)
    # stage → DOM 文本（page_context，阶段切换时填充）
    page_context: dict[str, str] = field(default_factory=dict)
    started: bool = False


class Collector:
    """采集器：收扩展事件，实时采快照 + 判 stage，累积 TraceEvent。

    满足 CollectorLike 协议（backend.py），通过依赖注入接入 CaptureBackend。
    用法：
        collector = Collector(cdp_session, output_dir)
        backend = CaptureBackend(collector)
        await backend.run()
    """

    def __init__(
        self,
        cdp_session: CdpSession,
        output_dir: str,
        stage_threshold: float | None = None,
    ) -> None:
        self.cdp = cdp_session
        self.output_dir = output_dir
        if stage_threshold is not None:
            self.stage_tracker = StageTracker(similarity_threshold=stage_threshold)
        else:
            self.stage_tracker = StageTracker()
        self._session: CaptureSession | None = None
        self._started = False

    # ---- CollectorLike 协议（backend 调用）----

    async def start(self, scenario: str = "distill", config: dict | None = None) -> str:
        """开始采集会话。

        config 可含：task_instruction（任务描述）、host（主域名）。
        会连 CdpSession（若未连）+ 初始化首阶段。
        """
        import uuid

        session_id = str(uuid.uuid4())[:8]
        config = config or {}
        self._session = CaptureSession(
            scenario=scenario,
            session_id=session_id,
            task_instruction=config.get("task_instruction", ""),
            host=config.get("host", ""),
        )

        # 连 CdpSession（采集层依赖 CDP 采快照）
        if not self.cdp.current_session_id:
            await self.cdp.start()

        # 首阶段：采首页快照作为 stage 0
        try:
            state = await self.cdp.get_state()
            initial_stage = self.stage_tracker.force_new_stage(state.url)
            self._session.page_context[initial_stage] = state.dom_state.element_tree_text or ""
            if not self._session.host and state.url:
                from urllib.parse import urlparse

                self._session.host = urlparse(state.url).hostname or ""
            logger.info(
                "Capture started: session=%s stage=%s host=%s",
                session_id, initial_stage, self._session.host,
            )
        except Exception as e:  # noqa: BLE001 - 首页采集失败不阻断录制（后续事件会补救）
            logger.warning("Initial state capture failed (will retry on first event): %s", e)

        self._started = True
        return session_id

    async def ingest(self, envelope: dict[str, Any]) -> None:
        """处理一个采集信封（backend 已按 scenario 路由到 distill）。

        envelope = { scenario, session_id, ts, url?, payload: DistillEventPayload }
        """
        if not self._session:
            logger.warning("ingest before start, ignoring")
            return

        payload = envelope.get("payload") or {}
        ts = envelope.get("ts") or payload.get("ts") or 0

        # 转换 payload → TraceEvent 字段
        fields = payload_to_trace_fields(payload)

        # 实时采快照 + 判 stage（实时采集原则：趁 DOM 活的）
        dom_text = ""
        url = fields.get("url")
        try:
            state = await self.cdp.get_state()
            dom_text = state.dom_state.element_tree_text or ""
            if not url:
                url = state.url
            # host 兜底
            if not self._session.host and state.url:
                from urllib.parse import urlparse

                self._session.host = urlparse(state.url).hostname or ""
        except Exception as e:  # noqa: BLE001 - 快照失败不阻断事件记录
            logger.warning("get_state failed for event (recording without snapshot): %s", e)

        # 判定阶段切换
        is_nav = fields.get("type") == "navigate"
        stage = self._determine_stage(url or "", dom_text, is_nav)

        # 累积事件
        event = CapturedEvent(
            type=fields.get("type", "unknown"),
            target=fields.get("target"),
            element_attrs=fields.get("element_attrs", {}),
            value=fields.get("value"),
            key=fields.get("key"),
            url=url,
            timestamp=int(ts),
            stage=stage,
        )
        self._session.events.append(event)

    async def stop(self) -> dict[str, Any]:
        """停止采集，导出产物，返回产物信息。

        这是「停止录制」的正确归属：扩展 popup 点「停止」→ POST /stop → backend 调此方法。
        导出产物（trace.json + snapshots/）在这里完成，返回产物路径供扩展展示。
        Ctrl+C（cli.py 的兜底路径）也会调这里保底导出。
        """
        if not self._session:
            return {"error": "no active session"}

        # 导出产物（trace.json + snapshots/）
        capture_dir = None
        if self._session.events:
            # 延迟 import 避免 collector 依赖 export（export import collector，会循环）
            from treeforge.capture.export import export_capture

            capture_dir = export_capture(self._session, self.output_dir)
            logger.info("Exported capture: %s", capture_dir)

        # 断开 CdpSession
        try:
            await self.cdp.stop()
        except Exception as e:  # noqa: BLE001
            logger.debug("CdpSession stop failed: %s", e)

        result = {
            "session_id": self._session.session_id,
            "host": self._session.host,
            "events": len(self._session.events),
            "stages": list(self._session.page_context.keys()),
            "output_dir": self.output_dir,
            "capture_dir": str(capture_dir) if capture_dir else None,
            "trace_path": str(capture_dir / "trace.json") if capture_dir else None,
        }
        logger.info("Capture stopped: %s", result)
        self._started = False
        return result

    # ---- 内部 ----

    def _determine_stage(self, url: str, dom_text: str, is_navigation: bool) -> str | None:
        """判定当前事件的 stage（确定绑定，无 ?）。

        阶段切换时存快照进 page_context；同阶段继承 current_stage。
        """
        if not self._session:
            return None

        raw = self.stage_tracker.detect_change(url, dom_text, is_navigation=is_navigation)
        if raw:
            # 新阶段：命名 + 存快照
            stage_name = self.stage_tracker.name_stage(url, raw)
            if dom_text:
                self._session.page_context[stage_name] = dom_text
            return stage_name
        # 同阶段：继承
        return self.stage_tracker.current_stage

    # ---- 供 export.py 取数据 ----

    @property
    def session(self) -> CaptureSession | None:
        """当前采集会话（export.py 读取落盘）。"""
        return self._session
