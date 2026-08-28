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
# stage 语义命名（DOM 特征检测）
# ---------------------------------------------------------------------------


def test_name_stage_semantic_upload_cover():
    """DOM 含 accept=image/png → 命名 upload-cover（封面上传）。"""
    tracker = StageTracker()
    dom_text = '[133]<input type="file" accept="image/png, image/jpeg" />'
    name = tracker.name_stage("https://x.com/up", "dom:0.20", dom_text)
    assert name == "upload-cover"


def test_name_stage_semantic_edit_cover():
    """DOM 含 <canvas> → 命名 edit-cover（封面裁剪，最特异优先于 upload-cover）。"""
    tracker = StageTracker()
    dom_text = '[149]<canvas id="editor_4_3" />\n[165]<canvas id="editor_16_9" />'
    name = tracker.name_stage("https://x.com/up", "dom:0.10", dom_text)
    assert name == "edit-cover"  # canvas 优先于 accept


def test_name_stage_semantic_falls_back_when_no_feature():
    """DOM 无特征 → 退化 URL 命名（向后兼容）。"""
    tracker = StageTracker()
    dom_text = "[1]<a />投稿\n[2]<button />登录"
    name = tracker.name_stage("https://x.com/platform/home", "url:/home", dom_text)
    assert name == "home"  # 无特征，退化 URL 段


def test_name_stage_semantic_dedup_suffix():
    """两次命中同语义特征 → 第二次加 _N 后缀（防 page_context 覆盖）。"""
    tracker = StageTracker()
    dom_text = '[1]<input type="file" accept="image/png" />'
    first = tracker.name_stage("https://x.com/up", "dom:0.20", dom_text)
    assert first == "upload-cover"
    # 第二次同特征（封面 modal 关了又开）
    second = tracker.name_stage("https://x.com/up", "dom:0.15", dom_text)
    assert second != "upload-cover"
    assert second.startswith("upload-cover_")


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
    cdp.current_tab_id = None
    cdp.get_state = AsyncMock(return_value=CaptureState(url=url, title="Test", dom_state=dom_state))
    cdp.start = AsyncMock()
    cdp.stop = AsyncMock()
    cdp.attach_tab = AsyncMock(return_value=True)
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
        await collector.ingest(
            {
                "scenario": "distill",
                "session_id": "t",
                "ts": i * 1000,
                "payload": {"type": "click", "raw_attrs": {"tag": "button"}},
            }
        )

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
    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
        }
    )

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

    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
        }
    )
    # 事件仍累积
    assert len(collector.session.events) == 1


async def test_collector_navigation_event_triggers_stage_change():
    """navigate 事件触发新阶段（整页跳转）。"""
    cdp = _make_mock_cdp(url="https://x.com/page1")
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()  # 首页 page1

    # 模拟导航到 page2
    cdp.get_state.return_value.url = "https://x.com/page2"
    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 1000,
            "payload": {"type": "navigate", "url": "https://x.com/page2"},
        }
    )

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
    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "url": "https://member.bilibili.com/platform/upload/video/frame",
            "payload": {"type": "click", "raw_attrs": {"tag": "a", "id": "nav_upload"}},
        }
    )

    event = collector.session.events[-1]
    # 事件 url 应是 envelope 外层的 bilibili，不是 CdpSession 的 popup
    assert "member.bilibili.com" in (event.url or "")
    assert "popup" not in (event.url or "")
    # host 也应从 envelope url 提取，不是 popup
    assert collector.session.host == "member.bilibili.com"


# ---------------------------------------------------------------------------
# tab 跟随（envelope.tab_id → CdpSession.attach_tab 精确重 attach）
# ---------------------------------------------------------------------------


async def test_collector_attaches_tab_from_envelope():
    """envelope 带 tab_id → collector 调 cdp.attach_tab 精确 attach（含 url 兜底参数）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()
    cdp.attach_tab.reset_mock()

    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "tab_id": 5,
            "url": "https://x.com/up",
            "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
        }
    )
    # attach_tab 现在收 (tab_id, url=...)——url 给 CdpSession 作 tabId 缺失时的兜底
    cdp.attach_tab.assert_called_once_with(5, url="https://x.com/up")
    assert collector._attached_tab == 5


async def test_collector_skips_reattach_same_tab():
    """同 tab_id 连续事件 → attach_tab 只调一次（幂等）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()
    cdp.attach_tab.reset_mock()

    envelope = {
        "scenario": "distill",
        "session_id": "t",
        "ts": 0,
        "tab_id": 5,
        "url": "https://x.com/up",
        "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
    }
    await collector.ingest(envelope)
    await collector.ingest({**envelope, "ts": 1000})  # 同 tab 第二个事件

    cdp.attach_tab.assert_called_once_with(5, url="https://x.com/up")  # 只调一次


async def test_collector_reattaches_on_tab_switch():
    """tab_id 变化（用户切 tab）→ attach_tab 再调一次。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()
    cdp.attach_tab.reset_mock()

    base = {
        "scenario": "distill",
        "session_id": "t",
        "ts": 0,
        "url": "https://x.com/up",
        "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
    }
    await collector.ingest({**base, "tab_id": 5})
    await collector.ingest({**base, "tab_id": 7, "ts": 1000})  # 切到 tab 7

    assert cdp.attach_tab.call_count == 2
    cdp.attach_tab.assert_any_call(5, url="https://x.com/up")
    cdp.attach_tab.assert_any_call(7, url="https://x.com/up")
    assert collector._attached_tab == 7


async def test_collector_no_tab_id_uses_eager_fallback():
    """无 tab_id 的老 envelope → 不调 attach_tab（用 start 的 eager fallback）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir="/tmp/test")
    await collector.start()
    cdp.attach_tab.reset_mock()

    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "url": "https://x.com/up",
            "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
        }
    )
    cdp.attach_tab.assert_not_called()  # 无 tab_id，不重 attach
    assert collector._attached_tab is None


# ---------------------------------------------------------------------------
# CdpSession._find_target_by_tab_id url 兜底回归（e708be22 抖音 trace 发现的 bug）：
# 部分 Chrome 环境 CDP 不填 tabId 字段（实测 tabId=None），纯 tabId 匹配完全失效，
# 快照采到错误的 target（扩展 popup）。修复后 tabId 缺失/找不到时按 url 匹配 http page target。
# ---------------------------------------------------------------------------


async def test_find_target_by_tab_id_url_fallback_when_tabid_none():
    """tabId 全 None（环境不填）时，按 url 匹配 http page target。"""
    from treeforge.capture.cdp_session import CdpSession

    cdp = CdpSession(ws_url="ws://x")
    cdp.client = MagicMock()
    # 模拟 e708be22 场景：douyin 页 tabId=None，但 url 可用
    cdp.client.send.Target.getTargets = AsyncMock(
        return_value={
            "targetInfos": [
                {
                    "type": "page",
                    "targetId": "T1",
                    "tabId": None,
                    "url": "chrome-extension://abc/popup.html",
                },
                {
                    "type": "page",
                    "targetId": "T2",
                    "tabId": None,
                    "url": "https://creator.douyin.com/creator-micro/content/upload?enter_from=publish",
                },
            ]
        }
    )
    # tab_id=5 在 CDP 里匹配不上（都 None）→ url 兜底应找到 T2（douyin）
    target = await cdp._find_target_by_tab_id(
        5, "https://creator.douyin.com/creator-micro/content/upload?enter_from=publish"
    )
    assert target is not None
    assert target["targetId"] == "T2", "tabId=None 时应按 url 找到 douyin target，不是 popup"


async def test_find_target_by_tab_id_prefers_tabid_when_available():
    """tabId 可用时优先按 tabId 匹配（不退化到 url）。"""
    from treeforge.capture.cdp_session import CdpSession

    cdp = CdpSession(ws_url="ws://x")
    cdp.client = MagicMock()
    cdp.client.send.Target.getTargets = AsyncMock(
        return_value={
            "targetInfos": [
                {"type": "page", "targetId": "T1", "tabId": 5, "url": "https://x.com/a"},
                {
                    "type": "page",
                    "targetId": "T2",
                    "tabId": 7,
                    "url": "https://x.com/a",
                },  # 同 url 不同 tab
            ]
        }
    )
    # tab_id=5 应精确命中 T1，不是 T2（即使 url 相同）
    target = await cdp._find_target_by_tab_id(5, "https://x.com/a")
    assert target["targetId"] == "T1"


async def test_find_target_by_tab_id_url_fallback_ignores_non_http():
    """url 兜底只认 http/https page，跳过 chrome-extension/chrome 内部页。"""
    from treeforge.capture.cdp_session import CdpSession

    cdp = CdpSession(ws_url="ws://x")
    cdp.client = MagicMock()
    cdp.client.send.Target.getTargets = AsyncMock(
        return_value={
            "targetInfos": [
                {
                    "type": "page",
                    "targetId": "T1",
                    "tabId": None,
                    "url": "chrome-extension://abc/popup.html",
                },
                {"type": "page", "targetId": "T2", "tabId": None, "url": "chrome://newtab/"},
            ]
        }
    )
    # 没有 http page，url 兜底也找不到 → None（不应误命中 popup/newtab）
    target = await cdp._find_target_by_tab_id(5, "https://x.com/a")
    assert target is None


async def test_find_target_by_tab_id_url_normalizes_query():
    """url 匹配按 host+path 规范化（忽略 query），容忍导航中 query 变化。"""
    from treeforge.capture.cdp_session import CdpSession

    cdp = CdpSession(ws_url="ws://x")
    cdp.client = MagicMock()
    cdp.client.send.Target.getTargets = AsyncMock(
        return_value={
            "targetInfos": [
                {"type": "page", "targetId": "T1", "tabId": None, "url": "https://x.com/a?foo=1"},
            ]
        }
    )
    # 事件 url 的 query 不同（?bar=2），但 host+path 相同 → 应命中
    target = await cdp._find_target_by_tab_id(5, "https://x.com/a?bar=2#top")
    assert target is not None
    assert target["targetId"] == "T1"


async def test_find_target_by_tab_id_no_match_returns_none():
    """tabId 和 url 都匹配不上 → None（不乱选）。"""
    from treeforge.capture.cdp_session import CdpSession

    cdp = CdpSession(ws_url="ws://x")
    cdp.client = MagicMock()
    cdp.client.send.Target.getTargets = AsyncMock(
        return_value={
            "targetInfos": [
                {"type": "page", "targetId": "T1", "tabId": 9, "url": "https://other.com/"},
            ]
        }
    )
    # tab_id=5 不匹配 T1(tabId=9)，url(x.com) 也不匹配 other.com → None
    target = await cdp._find_target_by_tab_id(5, "https://x.com/a")
    assert target is None


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
    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
        }
    )
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
        await collector.ingest(
            {
                "scenario": "distill",
                "session_id": f"rec{i}",
                "ts": i * 1000,
                "payload": {"type": "click", "raw_attrs": {"tag": "button", "id": f"btn{i}"}},
            }
        )
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
    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 0,
            "payload": {"type": "click", "raw_attrs": {"tag": "a"}},
        }
    )
    assert collector.session.host == "member.bilibili.com"  # host 兜底填充
    await collector.stop()


# ---------------------------------------------------------------------------
# P3.6：attach_signal（副作用信号 → attach 到最近 capture event）
# ---------------------------------------------------------------------------


async def test_attach_signal_appends_to_recent_event(tmp_path):
    """attach_signal 把 modal_opened 信号附到最近 capture event（2s 窗口内）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest(
        {
            "scenario": "distill",
            "session_id": "t",
            "ts": 1000,
            "payload": {"type": "click", "raw_attrs": {"tag": "button"}},
        }
    )

    attached = await collector.attach_signal(
        {"type": "modal_opened", "selector": "div.ant-modal", "ts": 1500}
    )
    assert attached is True
    assert len(collector.session.events) == 1
    sigs = collector.session.events[-1].signals
    assert len(sigs) == 1
    assert sigs[0]["type"] == "modal_opened"
    assert sigs[0]["selector"] == "div.ant-modal"
    await collector.stop()


async def test_attach_signal_rejects_unknown_type(tmp_path):
    """未知信号类型（如 navigation）→ 不 attach（distill 只收 modal/dropdown）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({"payload": {"type": "click"}, "ts": 0})

    attached = await collector.attach_signal({"type": "navigation", "selector": "x"})
    assert attached is False
    assert collector.session.events[-1].signals == []
    await collector.stop()


async def test_attach_signal_outside_window_not_attached(tmp_path):
    """信号 ts 距最近事件超 2s → 不 attach（动作引发副作用的合理窗口外）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({"payload": {"type": "click"}, "ts": 1000})

    attached = await collector.attach_signal(
        {"type": "dropdown_opened", "selector": "ul", "ts": 5000}  # 4s 后，超 2s 窗口
    )
    assert attached is False
    await collector.stop()


async def test_attach_signal_no_events_returns_false(tmp_path):
    """无 capture event 时 attach_signal 返 False（没事件可附）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    attached = await collector.attach_signal({"type": "modal_opened", "ts": 0})
    assert attached is False
    await collector.stop()


async def test_signals_exported_to_trace_json(tmp_path):
    """信号 attach 后，stop → export 落进 trace.json 的 event.signals 字段。"""
    import json

    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({"payload": {"type": "click"}, "ts": 1000})
    await collector.attach_signal({"type": "modal_opened", "selector": "div.modal", "ts": 1200})
    result = await collector.stop()

    trace_path = tmp_path / result["session_id"] / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert len(trace["events"]) == 1
    assert trace["events"][0]["signals"] == [
        {"type": "modal_opened", "selector": "div.modal", "ts": 1200}
    ]


# ---------------------------------------------------------------------------
# attach_signal 信号归属修复回归（ae99467f 抖音 trace 发现的 bug）：
# 原逻辑附到 events[-1]（最近事件），信号到达时最近事件可能是无关的 scroll/passive input
# （与信号时间重叠）。修复后按 action 类型 + 1s 因果窗口向前找触发 action event。
# ---------------------------------------------------------------------------


async def test_attach_signal_goes_to_triggering_click_not_later_scroll(tmp_path):
    """信号应附到触发它的 click，不是信号到达后才 ingest 的 scroll。

    ae99467f 抖音 trace 的真机场景：点「选择封面」(click) → 弹 modal →
    信号 modal_opened 到达 → 同期 scroll 被 ingest 成 events[-1]。原逻辑把信号挂到 scroll，
    修复后应挂到 click。
    """
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    # click 选择封面 (ts=1000)
    await collector.ingest({"payload": {"type": "click", "raw_attrs": {"tag": "div"}}, "ts": 1000})
    # scroll 在 click 之后、信号之前 ingest（成为 events[-1]）
    await collector.ingest({"payload": {"type": "scroll"}, "ts": 1500})
    # 信号 ts=1150（click 后 150ms，scroll 前）—— click 是真正触发者
    attached = await collector.attach_signal(
        {"type": "modal_opened", "selector": "div.modal", "ts": 1150}
    )
    assert attached is True
    events = collector.session.events
    assert len(events) == 2
    # 信号应挂在 click（events[0]），不是 scroll（events[1]）
    assert len(events[0].signals) == 1, "信号应挂到触发 click，不是后来的 scroll"
    assert events[0].signals[0]["type"] == "modal_opened"
    assert events[1].signals == [], "scroll 不应被挂信号"
    await collector.stop()


async def test_attach_signal_skips_passive_input_finds_click(tmp_path):
    """信号应跳过被动 input，向前找最近的 action event（click）。

    场景：点按钮 (click) → 继续打字 (input) → 弹出 modal 信号。input 不触发 modal，
    信号应回溯到 click。
    """
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest(
        {"payload": {"type": "click", "raw_attrs": {"tag": "button"}}, "ts": 1000}
    )
    await collector.ingest(
        {"payload": {"type": "input", "raw_attrs": {"tag": "input"}}, "ts": 1200}
    )
    # 信号 ts=1300（input 后 100ms，但 input 不是触发者；click 在 300ms 内）
    attached = await collector.attach_signal(
        {"type": "dropdown_opened", "selector": "ul.opts", "ts": 1300}
    )
    assert attached is True
    events = collector.session.events
    # 信号挂在 click（events[0]），不挂在 input（events[1]）
    assert len(events[0].signals) == 1
    assert events[1].signals == []
    await collector.stop()


async def test_attach_signal_window_is_1s_not_2s(tmp_path):
    """因果窗口是 1s（对齐扩展 side-effect-observer 的 ACTION_WINDOW_MS）。

    click ts=1000，信号 ts=2100（>1s 后）→ 不 attach（超因果窗口）。
    旧逻辑用 2s 窗口会误 attach；修复后 1s。
    """
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({"payload": {"type": "click"}, "ts": 1000})
    # 1100ms 后——超 1s 因果窗口
    attached = await collector.attach_signal(
        {"type": "modal_opened", "selector": "div", "ts": 2100}
    )
    assert attached is False
    assert collector.session.events[-1].signals == []
    await collector.stop()


async def test_attach_signal_no_trigger_action_returns_false(tmp_path):
    """无 action event（只有 scroll/input）时 → 不 attach（没触发者可附）。

    场景：纯 scroll/input 序列里来了个 modal 信号——这些事件不触发 modal，找不到触发者。
    """
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({"payload": {"type": "scroll"}, "ts": 1000})
    await collector.ingest({"payload": {"type": "input"}, "ts": 1100})
    attached = await collector.attach_signal(
        {"type": "modal_opened", "selector": "div", "ts": 1200}
    )
    assert attached is False
    for ev in collector.session.events:
        assert ev.signals == []
    await collector.stop()


async def test_attach_signal_picks_nearest_of_multiple_clicks(tmp_path):
    """多个候选 click 时，选最近的（时间上离信号最近的那个）。"""
    cdp = _make_mock_cdp()
    collector = Collector(cdp, output_dir=str(tmp_path))
    await collector.start()
    await collector.ingest({"payload": {"type": "click", "raw_attrs": {"tag": "a"}}, "ts": 1000})
    await collector.ingest(
        {"payload": {"type": "click", "raw_attrs": {"tag": "button"}}, "ts": 1500}
    )
    # 信号 ts=1600（最近的 click 在 100ms 前，更早的 click 在 600ms 前）
    attached = await collector.attach_signal(
        {"type": "modal_opened", "selector": "div", "ts": 1600}
    )
    assert attached is True
    events = collector.session.events
    # 挂到最近的 click（events[1]，button），不是早的 click（events[0]，a）
    assert events[1].signals == [{"type": "modal_opened", "selector": "div", "ts": 1600}]
    assert events[0].signals == []
    await collector.stop()
