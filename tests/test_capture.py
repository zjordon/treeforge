"""采集层测试（P2）。

测试原则（对齐 AGENTS.md）：
- 不连真浏览器（cdp_session 用 mock）
- backend 用 aiohttp test_utils 起内存 server
- collector 用 mock 满足 CollectorLike 协议
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from treeforge.capture.backend import CaptureBackend
from treeforge.capture.cdp_session import CaptureState, CdpSession

# ---------------------------------------------------------------------------
# CaptureState 数据结构
# ---------------------------------------------------------------------------


def test_capture_state_dataclass():
    """CaptureState 是轻量 dataclass，含 url/title/dom_state。"""
    from dom_snapshot import EMPTY_DOM_STATE

    state = CaptureState(url="https://x.com", title="X", dom_state=EMPTY_DOM_STATE)
    assert state.url == "https://x.com"
    assert state.title == "X"
    assert state.dom_state is EMPTY_DOM_STATE


def test_cdp_session_init_defaults():
    """CdpSession 初始化：client/target/session 都未连接。"""
    session = CdpSession(ws_url="ws://localhost:9223/devtools/browser/xxx")
    assert session.ws_url == "ws://localhost:9223/devtools/browser/xxx"
    assert session.client is None
    assert session.current_target_id is None
    assert session.current_session_id is None


async def test_cdp_session_stop_without_start_no_error():
    """未 start 直接 stop 不报错（防御）。"""
    session = CdpSession(ws_url="ws://localhost:9223/xxx")
    # 不应抛异常
    await session.stop()
    assert session.client is None


async def test_cdp_session_get_state_before_start_raises():
    """未 start 调 get_state 应抛 RuntimeError。"""
    session = CdpSession(ws_url="ws://localhost:9223/xxx")
    with pytest.raises(RuntimeError, match="not started"):
        await session.get_state()


# ---------------------------------------------------------------------------
# CaptureBackend scenario 路由
# ---------------------------------------------------------------------------


def _make_mock_collector():
    """造一个满足 CollectorLike 协议的 mock。"""
    collector = AsyncMock()
    collector.start.return_value = "test-session-1"
    collector.ingest.return_value = None
    collector.stop.return_value = {
        "output_dir": "/tmp/captures/test",
        "capture_dir": "/tmp/captures/test/session-1",
        "trace_path": "/tmp/captures/test/session-1/trace.json",
        "events": 5,
    }
    return collector


@pytest.fixture
async def backend_client():
    """起一个内存 backend + TestClient（不占真实端口）。"""
    collector = _make_mock_collector()
    backend = CaptureBackend(collector)
    server = TestServer(backend.make_app())
    client = TestClient(server)
    await client.start_server()
    yield client, collector
    await client.close()


@pytest.fixture
async def backend_with_stop_callback():
    """带 on_stop 回调的 backend（验证 /stop 触发回调）。"""
    collector = _make_mock_collector()
    stop_called = []
    backend = CaptureBackend(collector, on_stop=lambda: stop_called.append(True))
    server = TestServer(backend.make_app())
    client = TestClient(server)
    await client.start_server()
    yield client, collector, stop_called
    await client.close()


async def test_health(backend_client):
    """GET /health 返回 {ok: true}。"""
    client, _ = backend_client
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


async def test_start_distill(backend_client):
    """POST /start { scenario: distill } → 调 collector.start，返 session_id。"""
    client, collector = backend_client
    resp = await client.post("/start", json={"scenario": "distill", "config": {}})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["session_id"] == "test-session-1"
    collector.start.assert_called_once_with(scenario="distill", config={})


async def test_ingest_distill_routes_to_collector(backend_client):
    """POST /ingest { scenario: distill } → 调 collector.ingest。"""
    client, collector = backend_client
    envelope = {
        "scenario": "distill",
        "session_id": "test-session-1",
        "ts": 1234567890,
        "payload": {"type": "click", "tag": "a"},
    }
    resp = await client.post("/ingest", json=envelope)
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    collector.ingest.assert_called_once_with(envelope)


async def test_ingest_replay_not_implemented_but_returns_ok(backend_client):
    """POST /ingest { scenario: replay } → 返回 ok（replay 留接口，不调 collector）。"""
    client, collector = backend_client
    envelope = {"scenario": "replay", "session_id": "s1", "ts": 0, "payload": {}}
    resp = await client.post("/ingest", json=envelope)
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert "not implemented" in data["note"]
    collector.ingest.assert_not_called()


async def test_ingest_unknown_scenario_returns_400(backend_client):
    """POST /ingest { scenario: invalid } → 400。"""
    client, _ = backend_client
    envelope = {"scenario": "invalid", "session_id": "s1", "ts": 0, "payload": {}}
    resp = await client.post("/ingest", json=envelope)
    assert resp.status == 400


async def test_stop_returns_collector_result(backend_client):
    """POST /stop → 调 collector.stop，返产物信息（含 capture_dir/trace_path）。"""
    client, collector = backend_client
    resp = await client.post("/stop", json={})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["result"]["capture_dir"] == "/tmp/captures/test/session-1"
    assert data["result"]["trace_path"] == "/tmp/captures/test/session-1/trace.json"
    collector.stop.assert_called_once()


async def test_stop_triggers_on_stop_callback(backend_with_stop_callback):
    """POST /stop → 调 collector.stop 后触发 on_stop 回调（让 cli 退出）。"""
    client, collector, stop_called = backend_with_stop_callback
    resp = await client.post("/stop", json={})
    assert resp.status == 200
    collector.stop.assert_called_once()
    assert len(stop_called) == 1, "on_stop 回调应被调用一次"


async def test_start_failure_returns_500(backend_client):
    """collector.start 抛错 → /start 返回 500。"""
    client, collector = backend_client
    collector.start.side_effect = RuntimeError("boom")
    resp = await client.post("/start", json={"scenario": "distill"})
    assert resp.status == 500
    data = await resp.json()
    assert data["ok"] is False
    assert "boom" in data["error"]
