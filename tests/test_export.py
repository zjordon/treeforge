"""export.py 测试（P2.2.5）。

验证采集会话 → trace.json + snapshots/ 的导出，且产出能被 harness 蒸馏消费。
"""

from __future__ import annotations

import json

from treeforge.capture.collector import CapturedEvent, CaptureSession
from treeforge.capture.export import export_capture


def _make_session(
    host="member.bilibili.com",
    stages=None,
    events=None,
) -> CaptureSession:
    """造一个测试用的 CaptureSession。"""
    session = CaptureSession(
        scenario="distill",
        session_id="test123",
        task_instruction="测试投稿",
        host=host,
    )
    session.page_context = stages if stages is not None else {
        "upload": "[1]<a id=nav_upload />投稿",
        "publish": "[100]<input type=text placeholder=标题 />",
    }
    if events is None:
        session.events = [
            CapturedEvent(type="click", target="投稿", element_attrs={"tag": "a", "id": "nav_upload"}, stage="upload", timestamp=0),
            CapturedEvent(type="input", element_attrs={"tag": "input", "type": "text"}, value="视频标题", stage="publish", timestamp=1000),
        ]
    else:
        session.events = events
    return session


def test_export_creates_trace_and_snapshots(tmp_path):
    """导出产出 trace.json + snapshots/ 目录 + 快照文件。"""
    session = _make_session()
    capture_dir = export_capture(session, tmp_path, name="bili-test")

    assert capture_dir.exists()
    assert (capture_dir / "trace.json").is_file()
    snapshots_dir = capture_dir / "snapshots"
    assert snapshots_dir.is_dir()
    assert (snapshots_dir / "upload.txt").is_file()
    assert (snapshots_dir / "publish.txt").is_file()


def test_export_trace_format_matches_examples(tmp_path):
    """trace.json 格式对齐 examples（host/events/page_context 顶层字段）。"""
    session = _make_session()
    capture_dir = export_capture(session, tmp_path, name="bili")
    trace = json.loads((capture_dir / "trace.json").read_text(encoding="utf-8"))

    assert set(trace.keys()) == {"host", "task_instruction", "events", "page_context"}
    assert trace["host"] == "member.bilibili.com"
    assert trace["task_instruction"] == "测试投稿"
    assert len(trace["events"]) == 2
    assert "upload" in trace["page_context"]
    assert "publish" in trace["page_context"]


def test_export_event_fields(tmp_path):
    """event dict 含 type/stage/element_attrs/timestamp（对齐 TraceEvent）。"""
    session = _make_session()
    capture_dir = export_capture(session, tmp_path, name="bili")
    trace = json.loads((capture_dir / "trace.json").read_text(encoding="utf-8"))

    click_event = trace["events"][0]
    assert click_event["type"] == "click"
    assert click_event["stage"] == "upload"  # 确定绑定，无 ?
    assert click_event["element_attrs"]["tag"] == "a"
    assert click_event["timestamp"] == 0


def test_export_page_context_inline_dom_text(tmp_path):
    """page_context 的 value 是 element_tree_text（纯 DOM 文本）。"""
    session = _make_session(stages={"frame": "[1]<div />\n[2]<span />"})
    capture_dir = export_capture(session, tmp_path, name="bili")
    trace = json.loads((capture_dir / "trace.json").read_text(encoding="utf-8"))

    assert trace["page_context"]["frame"] == "[1]<div />\n[2]<span />"


def test_export_snapshot_content_matches_page_context(tmp_path):
    """快照文件内容与 trace.page_context 一致。"""
    session = _make_session(stages={"upload": "[1]<a />投稿"})
    capture_dir = export_capture(session, tmp_path, name="bili")
    trace = json.loads((capture_dir / "trace.json").read_text(encoding="utf-8"))

    snapshot_content = (capture_dir / "snapshots" / "upload.txt").read_text(encoding="utf-8")
    assert snapshot_content == trace["page_context"]["upload"]


def test_export_trace_consumable_by_distill(tmp_path):
    """产出能被 harness ADAPT 加载（Trace 模型校验通过）。"""
    from harness.adapter import adapt

    session = _make_session()
    capture_dir = export_capture(session, tmp_path, name="bili")
    trace_path = capture_dir / "trace.json"

    # 读成 dict，走 adapt（与 treeforge distill 入口一致）
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    trace = adapt(payload, source="test")
    assert trace.host == "member.bilibili.com"
    assert len(trace.events) == 2
    assert trace.page_context["upload"] == "[1]<a id=nav_upload />投稿"
    # event.stage 确定绑定（无 ?）
    assert all("?" not in (e.stage or "") for e in trace.events)


def test_export_stage_filename_sanitized(tmp_path):
    """stage 名含特殊字符时快照文件名被清理。"""
    session = _make_session(stages={"upload/conver": "[1]<div />"})
    capture_dir = export_capture(session, tmp_path, name="bili")
    # / 被替换成 _
    assert (capture_dir / "snapshots" / "upload_conver.txt").is_file()


def test_export_empty_events_still_writes_trace(tmp_path):
    """空 events 也能导出（边界）。"""
    session = _make_session(events=[])
    capture_dir = export_capture(session, tmp_path, name="empty")
    trace = json.loads((capture_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["events"] == []
    assert len(trace["page_context"]) == 2  # 快照照存


def test_export_uses_session_id_when_no_name(tmp_path):
    """不传 name 时用 session_id 作目录名。"""
    session = _make_session()
    capture_dir = export_capture(session, tmp_path)
    assert capture_dir.name == "test123"  # session_id


def test_export_overwrites_existing(tmp_path):
    """重复导出覆盖（原子写，不报错）。"""
    session = _make_session()
    export_capture(session, tmp_path, name="bili")
    session.events.append(CapturedEvent(type="click", stage="upload", timestamp=2000))
    # 再次导出
    capture_dir = export_capture(session, tmp_path, name="bili")
    trace = json.loads((capture_dir / "trace.json").read_text(encoding="utf-8"))
    assert len(trace["events"]) == 3  # 新事件已写
