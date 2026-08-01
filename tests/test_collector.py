"""collector + stage 测试（P2.2.2/P2.2.3）。

测试原则：mock CdpSession（不连真浏览器），验证事件累积 + stage 判定逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from treeforge.capture.collector import Collector
from treeforge.capture.stage import StageTracker, dom_similarity

# ---------------------------------------------------------------------------
# dom_similarity
# ---------------------------------------------------------------------------


def test_similarity_identical_text():
    """相同文本相似度 1.0。"""
    assert dom_similarity("a\nb\nc", "a\nb\nc") == 1.0


def test_similarity_disjoint_text():
    """完全不同相似度 0.0。"""
    assert dom_similarity("a\nb", "c\nd") == 0.0


def test_similarity_partial_overlap():
    """部分重叠按 Jaccard 算。"""
    # {a,b} vs {a,b,c} → 交集2/并集3 = 0.667
    assert abs(dom_similarity("a\nb", "a\nb\nc") - 2 / 3) < 0.01


def test_similarity_empty():
    """空文本处理。"""
    assert dom_similarity("", "") == 1.0
    assert dom_similarity("a", "") == 0.0
    assert dom_similarity("", "a") == 0.0


# ---------------------------------------------------------------------------
# StageTracker
# ---------------------------------------------------------------------------


def test_tracker_force_new_stage_from_url():
    """force_new_stage 从 URL 提取命名。"""
    tracker = StageTracker()
    name = tracker.force_new_stage("https://member.bilibili.com/platform/upload/video/frame")
    assert name == "frame"  # 最后一个非跳过段
    assert tracker.current_stage == "frame"


def test_tracker_force_new_stage_skip_generic_segments():
    """跳过 api/platform/www 等通用段。"""
    tracker = StageTracker()
    # /platform/home → home（platform 被跳过）
    name = tracker.force_new_stage("https://x.com/platform/home")
    assert name == "home"


def test_tracker_force_new_stage_fallback_stage_n():
    """URL 无 path 时用 stage_N。"""
    tracker = StageTracker()
    name = tracker.force_new_stage("https://x.com/")
    assert name == "stage_1"
    assert tracker.current_stage == "stage_1"


def test_tracker_detect_change_url_path():
    """URL path 变化触发切换。"""
    tracker = StageTracker()
    tracker.force_new_stage("https://x.com/page1")
    raw = tracker.detect_change("https://x.com/page2", "dom", is_navigation=False)
    assert raw is not None
    assert raw.startswith("url:")
    name = tracker.name_stage("https://x.com/page2", raw)
    assert name == "page2"


def test_tracker_detect_change_dom_similarity():
    """DOM 变化率超阈值触发切换（SPA）。"""
    tracker = StageTracker(similarity_threshold=0.7)
    tracker.force_new_stage("https://x.com/app")  # 同 URL，SPA
    dom1 = "\n".join(f"[{i}]<div />" for i in range(100))
    # 初始化 _last_dom_text
    tracker.detect_change("https://x.com/app", dom1, is_navigation=False)
    # DOM 大变（相似度 < 0.7）
    dom2 = "\n".join(f"[{i}]<span />" for i in range(100))
    raw = tracker.detect_change("https://x.com/app", dom2, is_navigation=False)
    assert raw is not None
    assert raw.startswith("dom:")


def test_tracker_no_change_same_dom():
    """同阶段 DOM 微调不触发切换。"""
    tracker = StageTracker(similarity_threshold=0.7)
    tracker.force_new_stage("https://x.com/app")
    dom1 = "\n".join(f"[{i}]<div />" for i in range(100))
    tracker.detect_change("https://x.com/app", dom1, is_navigation=False)
    # 只加一行（相似度 > 0.7）
    dom2 = dom1 + "\n[100]<div />"
    raw = tracker.detect_change("https://x.com/app", dom2, is_navigation=False)
    assert raw is None  # 无切换


def test_tracker_navigation_always_changes():
    """整页导航总触发切换。"""
    tracker = StageTracker()
    tracker.force_new_stage("https://x.com/a")
    raw = tracker.detect_change("https://x.com/b", "dom", is_navigation=True)
    assert raw is not None
    assert raw.startswith("nav:")


def test_tracker_name_stage_dom_signal_avoids_duplicate():
    """dom 信号（SPA 切换，URL 不变）不应复用 URL 名（否则 page_context key 冲突）。

    正确行为：URL 不变时用 name_N 消歧，避免和上个阶段同名。
    """
    tracker = StageTracker()
    first = tracker.force_new_stage("https://x.com/upload")  # 首阶段 = upload
    assert first == "upload"

    # SPA 切换，URL 不变 → dom 信号
    raw = "dom:0.30"
    second = tracker.name_stage("https://x.com/upload", raw)
    # 不应还是 upload（会和首阶段 page_context key 冲突），用 upload_N 消歧
    assert second != "upload"
    assert second.startswith("upload_")  # upload_2（原名 + 序号消歧）


# ---------------------------------------------------------------------------
# Collector（mock CdpSession）
# ---------------------------------------------------------------------------


def _make_mock_cdp(url="https://member.bilibili.com/platform/home", dom_text="[1]<a />投稿"):
    """造一个 mock CdpSession，get_state 返回固定 CaptureState。"""
    from treeforge.capture.cdp_session import CaptureState, CdpSession

    # 构造一个带 element_tree_text 的 SerializedDOMState mock
    dom_state = MagicMock()
    dom_state.element_tree_text = dom_text
    dom_state.selector_map = {}

    cdp = MagicMock(spec=CdpSession)
    cdp.current_session_id = None
    cdp.get_state = AsyncMock(return_value=CaptureState(url=url, title="Test", dom_state=dom_state))
    cdp.start = AsyncMock()
    cdp.stop = AsyncMock()
    return cdp


async def test_collector_start_initializes_first_stage():
    """start 连 CdpSession + 采首页快照 + 初始化首阶段。"""
    cdp = _make_mock_cdp(url="https://member.bilibili.com/platform/home")
    collector = Collector(cdp, output_dir="/tmp/test")
    session_id = await collector.start(scenario="distill", config={"task_instruction": "test"})
    assert session_id  # 返回 session_id
    assert collector.session is not None
    assert collector.session.host == "member.bilibili.com"
    # 首阶段快照已存
    assert len(collector.session.page_context) >= 1
    cdp.start.assert_called_once()
    cdp.get_state.assert_called_once()


async def test_collector_ingest_accumulates_event():
    """ingest 把 payload 转成 CapturedEvent 累积。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()
    cdp.get_state.reset_mock()

    envelope = {
        "scenario": "distill",
        "session_id": "test",
        "ts": 1000,
        "payload": {
            "type": "click",
            "raw_attrs": {"tag": "a", "id": "nav_upload", "visible_text": "投稿"},
            "target": "投稿入口",
        },
    }
    await collector.ingest(envelope)

    assert len(collector.session.events) == 1
    event = collector.session.events[0]
    assert event.type == "click"
    assert event.element_attrs["tag"] == "a"
    assert event.element_attrs["id"] == "nav_upload"
    assert event.element_attrs["visible_text"] == "投稿"
    assert event.target == "投稿入口"
    assert event.stage is not None  # 确定绑定，无 ?
    assert event.timestamp == 1000


async def test_collector_ingest_no_stage_marker():
    """collector 产出的 stage 不带 ? 标记（确定绑定，非启发式）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()

    for i in range(3):
        await collector.ingest({
            "scenario": "distill", "session_id": "t", "ts": i * 1000,
            "payload": {"type": "click", "raw_attrs": {"tag": "button"}},
        })

    for event in collector.session.events:
        assert event.stage is not None
        assert "?" not in event.stage  # 无 ? 标记


async def test_collector_ingest_before_start_ignored():
    """未 start 的 ingest 被忽略（不报错）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.ingest({"payload": {"type": "click"}})
    assert collector.session is None or len(collector.session.events) == 0


async def test_collector_stop_exports_and_disconnects(tmp_path):
    """stop 导出产物 + 断开 CdpSession + 返回产物路径。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({
        "scenario": "distill", "session_id": "t", "ts": 0,
        "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
    })

    result = await collector.stop()
    cdp.stop.assert_called_once()
    assert result["events"] == 1
    assert result["host"] == "member.bilibili.com"
    assert "stages" in result
    # 新增：stop 应导出产物并返回路径
    assert result["capture_dir"] is not None
    assert result["trace_path"] is not None
    from pathlib import Path
    trace_path = Path(result["trace_path"])
    assert trace_path.is_file()  # trace.json 真的写了


async def test_collector_get_state_failure_doesnt_block_event():
    """get_state 失败时事件仍记录（无快照，但有 stage 兜底）。"""
    cdp = _make_mock_cdp()
    cdp.get_state = AsyncMock(side_effect=RuntimeError("CDP disconnect"))
    collector = Collector(cdp, output_dir="/tmp/test")
    # start 也会失败 get_state，但不阻断
    await collector.start()

    await collector.ingest({
        "scenario": "distill", "session_id": "t", "ts": 0,
        "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
    })
    # 事件仍累积
    assert len(collector.session.events) == 1


async def test_collector_navigation_event_triggers_stage_change():
    """navigate 事件触发新阶段（整页跳转）。"""
    cdp = _make_mock_cdp(url="https://x.com/page1")
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()  # 首页 page1

    # 模拟导航到 page2
    cdp.get_state.return_value.url = "https://x.com/page2"
    await collector.ingest({
        "scenario": "distill", "session_id": "t", "ts": 1000,
        "payload": {"type": "navigate", "url": "https://x.com/page2"},
    })

    event = collector.session.events[-1]
    assert event.type == "navigate"
    assert event.stage == "page2"  # 新阶段命名


async def test_collector_envelope_url_overrides_cdp_url():
    """envelope 外层 url（content script 报的真实页面）优先于 CdpSession 的 url。

    场景：CdpSession 连的 target 是 popup（url=popup.html），但用户在 bilibili 操作。
    content script 报 envelope.url=bilibili。事件应记录 bilibili 的 url，不是 popup。
    """
    # mock CdpSession 返回 popup 的 url（模拟连错 target）
    cdp = _make_mock_cdp(url="chrome-extension://abc/popup.html")
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()

    # 事件的 envelope 外层 url 是真实 bilibili 页面
    await collector.ingest({
        "scenario": "distill", "session_id": "t", "ts": 0,
        "url": "https://member.bilibili.com/platform/upload/video/frame",
        "payload": {"type": "click", "raw_attrs": {"tag": "a", "id": "nav_upload"}},
    })

    event = collector.session.events[-1]
    # 事件 url 应是 envelope 外层的 bilibili，不是 CdpSession 的 popup
    assert "member.bilibili.com" in (event.url or "")
    assert "popup" not in (event.url or "")
    # host 也应从 envelope url 提取，不是 popup
    assert collector.session.host == "member.bilibili.com"


# ---------------------------------------------------------------------------
# session 可循环（start/stop 多次录制，stage 状态不串）
# ---------------------------------------------------------------------------


async def test_collector_start_stop_loop_clears_session(tmp_path):
    """stop 后 session 清空，下次 start 重建（支持连续录制多次）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))

    # 第一次录制
    await collector.start()
    assert collector.session is not None
    sid1 = collector.session.session_id
    await collector.ingest({
        "scenario": "distill", "session_id": "t", "ts": 0,
        "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
    })
    assert len(collector.session.events) == 1
    await collector.stop()

    # stop 后 session 应清空
    assert collector.session is None
    assert collector._started is False

    # 第二次录制（新 session_id，不继承上次的 events）
    await collector.start()
    assert collector.session is not None
    sid2 = collector.session.session_id
    assert sid1 != sid2  # 新 session_id
    assert len(collector.session.events) == 0  # 不继承上次的事件
    await collector.stop()


async def test_collector_start_rebuilds_stage_tracker(tmp_path):
    """每次 start 重建 StageTracker，stage 计数/last_dom 不跨 session 串。"""
    cdp = _make_mock_cdp(url="https://x.com/upload")
    collector = Collector(cdp, output_dir=str(tmp_path))

    # 第一次录制：触发一个 stage，counter 应该是某个值
    await collector.start()
    tracker1 = collector.stage_tracker
    first_stage = tracker1.current_stage
    assert first_stage == "upload"
    await collector.stop()

    # 第二次录制：StageTracker 应是全新实例，current_stage 重新从 force_new_stage 开始
    await collector.start()
    tracker2 = collector.stage_tracker
    assert tracker1 is not tracker2  # 不同实例
    # 第二次的首阶段名应和第一次相同（同 URL），但 counter 不应叠加
    assert tracker2.current_stage == "upload"
    assert tracker2._stage_counter == 0  # 新实例 counter 归零
    await collector.stop()


async def test_collector_multiple_recordings_all_export(tmp_path):
    """连续多次录制，每次都能独立导出产物（产物不互相覆盖）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))

    for i in range(3):
        await collector.start()
        await collector.ingest({
            "scenario": "distill", "session_id": f"rec{i}", "ts": i * 1000,
            "payload": {"type": "click", "raw_attrs": {"tag": "button", "id": f"btn{i}"}},
        })
        result = await collector.stop()
        assert result["events"] == 1
        assert result["trace_path"] is not None

    # 三个录制的 trace.json 都应存在（session_id 不同，目录不覆盖）
    import json
    from pathlib import Path

    traces = list(Path(tmp_path).rglob("trace.json"))
    assert len(traces) == 3
    # 每个含各自的 element id
    for t in traces:
        data = json.loads(t.read_text(encoding="utf-8"))
        assert len(data["events"]) == 1


# ---------------------------------------------------------------------------
# host 提取（跳过浏览器内部页面）
# ---------------------------------------------------------------------------


def test_extract_real_host_skips_chrome_internal():
    """chrome:// / chrome-extension:// / about: 等内部页面不返回 host。"""
    from treeforge.capture.collector import _extract_real_host

    assert _extract_real_host("chrome://newtab/") == ""
    assert _extract_real_host("chrome-extension://abc123/popup.html") == ""
    assert _extract_real_host("about:blank") == ""


def test_extract_real_host_skips_new_tab_page():
    """new-tab-page 等浏览器内部 host 跳过（hostname 是 new-tab-page 不是真实站点）。"""
    from treeforge.capture.collector import _extract_real_host

    assert _extract_real_host("chrome://newtab/") == ""
    assert _extract_real_host("https://new-tab-page/") == ""


def test_extract_real_host_real_site():
    """真实站点返回 hostname。"""
    from treeforge.capture.collector import _extract_real_host

    assert _extract_real_host("https://member.bilibili.com/platform/home") == "member.bilibili.com"
    assert _extract_real_host("http://localhost:8080/") == "localhost"


async def test_collector_host_filled_on_real_page(tmp_path):
    """录制开始时在 chrome:// 页，首个真实页面事件后 host 正确填充。"""
    # 模拟：start 时 url 是 chrome://newtab（首页），ingest 时 url 是真实页面
    cdp = _make_mock_cdp(url="chrome://newtab/")
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    assert collector.session.host == ""  # 首页是 chrome://，host 暂空

    # 第一个事件在真实页面
    cdp.get_state.return_value.url = "https://member.bilibili.com/platform/home"
    await collector.ingest({
        "scenario": "distill", "session_id": "t", "ts": 0,
        "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
    })
    assert collector.session.host == "member.bilibili.com"  # host 兜底填充
    await collector.stop()
