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


def _extract_real_host(url: str) -> str:
    """从 URL 提取真实站点 host，跳过浏览器内部页面。

    chrome://、chrome-extension://、about:、new-tab-page 等不是真实站点，
    hostname 会是 None 或浏览器内部值（如 'new-tab-page'），应跳过等待真实页面。
    """
    if not url:
        return ""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    # 只认 http/https（跳过 chrome:// chrome-extension:// about: 等）
    if parsed.scheme not in ("http", "https"):
        return ""
    hostname = parsed.hostname or ""
    # 跳过浏览器内部 host（new-tab-page 等）
    internal_hosts = {"new-tab-page", "newtab", "blank"}
    if hostname in internal_hosts:
        return ""
    return hostname


# 可触发 modal/dropdown 副作用的事件类型（attach_signal 找触发 action 用）。
# scroll/input（被动输入）不会打开 modal/dropdown，跳过；upload_file 点开文件选择器
# 是 OS 级（不产 modal signal），但上传区点击常先有个 click 触发，故 upload_file 保留
# 作候选（少见，但若上传触发了页面 modal 仍算）。
_SIGNAL_TRIGGER_TYPES: frozenset[str] = frozenset(
    {"click", "upload_file", "navigate", "select_dropdown", "send_keys"}
)

# 信号因果窗口：扩展 side-effect-observer 在动作后 1s 窗口检测副作用（见 side-effect-observer.ts
# 的 ACTION_WINDOW_MS=1000），故信号 ts 距触发 action ts 应在 1s 内（含轻微时序抖动余量）。
_SIGNAL_CAUSAL_WINDOW_MS = 1000


def _find_signal_trigger(
    events: list[CapturedEvent], signal_ts: int, window_ms: int
) -> CapturedEvent | None:
    """在 signal_ts 之前找最近的可触发副作用 action event（click/upload_file/...）。

    向前扫描（从最新到最旧），返回最后一个 timestamp ≤ signal_ts 且在 window_ms 窗口内的
    action event。scroll/input（被动）跳过——它们不触发 modal/dropdown 打开。

    为什么不直接用 events[-1]：信号到达时 events[-1] 可能是个与信号时间重叠的无关 scroll/passive
    input（/signal 与 /ingest 的处理顺序不保证反映因果）。按 action 类型 + 因果窗口找更准。
    """
    best: CapturedEvent | None = None
    for ev in reversed(events):
        if ev.type not in _SIGNAL_TRIGGER_TYPES:
            continue
        if not ev.timestamp:
            continue
        # 只认在 signal 之前发生的（timestamp ≤ signal_ts）
        if ev.timestamp > signal_ts:
            continue
        delta = signal_ts - ev.timestamp
        if delta > window_ms:
            break  # 继续向前只会更远（events 按时间序），停
        # 窗口内最近的 action（reversed 扫描遇到的第一个就是最近的）
        best = ev
        break
    return best


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

    # P3.6：副作用信号（modal/dropdown 打开），attach 到本事件作为 quirks 原料。
    # collector.attach_signal 在 2s 窗口内把信号附到最近事件；export 时落进 TraceEvent.signals。
    signals: list[dict[str, Any]] = field(default_factory=list)


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
        self._stage_threshold = stage_threshold
        # stage_tracker 在每次 start 时重建（避免跨 session stage 状态串）
        self.stage_tracker = self._new_stage_tracker()
        self._session: CaptureSession | None = None
        self._started = False
        # 已 attach 的 tab id（跟随用户切 tab）：None=用 CdpSession eager fallback
        self._attached_tab: int | None = None

    def _new_stage_tracker(self) -> StageTracker:
        """新建 StageTracker（每次 start 调用，确保跨 session 状态隔离）。"""
        if self._stage_threshold is not None:
            return StageTracker(similarity_threshold=self._stage_threshold)
        return StageTracker()

    # ---- CollectorLike 协议（backend 调用）----

    async def start(self, scenario: str = "distill", config: dict | None = None) -> str:
        """开始采集会话。

        config 可含：task_instruction（任务描述）、host（主域名）。
        会连 CdpSession（若未连）+ 初始化首阶段。
        每次调用重建 session + StageTracker（支持多次录制循环，stage 状态不串）。
        """
        import uuid

        # 重建 StageTracker（关键：避免上次录制的 stage 计数/last_dom 污染本次）
        self.stage_tracker = self._new_stage_tracker()
        self._attached_tab = None  # 重置 tab 跟随状态（跨 session 隔离）

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
        # 注意：CdpSession 连的 target 可能不是用户操作的页面（连了第一个 page target）。
        # 如果首屏是 chrome-extension:// 等内部页（如 popup），跳过首阶段采集，
        # 等第一个真实事件（envelope url 是 http 页面）时再采。
        try:
            state = await self.cdp.get_state()
            real_host = _extract_real_host(state.url)
            if real_host:  # 是真实页面才采首阶段
                dom_text = state.dom_state.element_tree_text or ""
                initial_stage = self.stage_tracker.force_new_stage(state.url, dom_text)
                self._session.page_context[initial_stage] = dom_text
                self._session.host = real_host
                logger.info(
                    "Capture started: session=%s stage=%s host=%s",
                    session_id,
                    initial_stage,
                    self._session.host,
                )
            else:
                logger.info(
                    "Capture started: session=%s (首屏非真实页面，等首个事件采快照)",
                    session_id,
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

        # url 优先级：envelope 外层（content script 报的真实页面 url）>
        #            payload（navigate 事件的 url）> CdpSession 兜底
        # 关键：不能用 CdpSession 的 url 兜底——它连的 target 可能不是用户操作的页面
        url = envelope.get("url") or fields.get("url")

        # 实时采快照 + 判 stage（实时采集原则：趁 DOM 活的）
        dom_text = ""
        try:
            # 跟随用户 tab：envelope 带 tab_id 且与当前不同 → 重 attach 精确 target。
            # 传 url 给 attach_tab 作兜底（部分 Chrome 环境 tabId=None，靠 url 匹配 target）。
            tab_id = envelope.get("tab_id")
            if tab_id is not None and tab_id != self._attached_tab:
                if await self.cdp.attach_tab(tab_id, url=url):
                    self._attached_tab = tab_id
            state = await self.cdp.get_state()
            dom_text = state.dom_state.element_tree_text or ""
            # host 兜底（首次遇到真实页面时填充，跳过 chrome:// 等内部页）
            if not self._session.host:
                self._session.host = _extract_real_host(url or state.url)
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

    async def attach_signal(self, payload: dict[str, Any]) -> bool:
        """把副作用信号（modal/dropdown 打开）attach 到触发它的 action event。

        P3.6 迁自 TreeWalker Recorder.attach_signal。信号本身不是动作（不进 events 列表），
        而是附到触发它的 action event 上，作为 distiller 写 quirks.md 的原料
        （「点这个按钮会弹出 modal」「选这个下拉会展开选项」）。

        【信号归属修复】原逻辑附到 ``events[-1]``（最近事件），但信号到达时最近事件可能
        是个无关的 scroll/passive input（与信号时间重叠、且 /signal 与 /ingest 的处理顺序
        不保证反映因果）。修复：在信号时间戳向前找最近的可触发副作用的 action event
        （click/upload_file/navigate/select_dropdown/send_keys），在因果窗口（≤1s）内才算。
        scroll/input（被动输入）不会触发 modal/dropdown 打开，跳过。

        payload = { type: 'modal_opened'|'dropdown_opened', selector, ts }
        返回是否成功 attach（找到 ≤1s 窗口内的触发 action 才算）。
        """
        if not self._session or not self._session.events:
            return False

        signal_type = payload.get("type")
        if signal_type not in ("modal_opened", "dropdown_opened"):
            return False

        ts = int(payload.get("ts", 0))

        # 向前找最近的可触发副作用的 action event（scroll/input 跳过）。
        # 扩展 side-effect-observer 在动作后 1s 窗口检测，故因果窗口用 1s（对齐扩展端）。
        trigger = _find_signal_trigger(self._session.events, ts, _SIGNAL_CAUSAL_WINDOW_MS)
        if trigger is None:
            return False

        trigger.signals.append(
            {
                "type": signal_type,
                "selector": payload.get("selector", ""),
                "ts": ts,
            }
        )
        return True

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
        self._session = None  # 清空 session，下次 start 干净启动（支持循环录制）
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
            # 新阶段：命名（DOM 特征优先）+ 存快照
            stage_name = self.stage_tracker.name_stage(url, raw, dom_text)
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
