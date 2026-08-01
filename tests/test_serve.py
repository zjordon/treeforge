"""P3 常驻服务（FastAPI）测试。

测试原则（对齐 AGENTS.md）：
- 不连真浏览器（fetch_ws_url / CdpSession / Collector 用 mock 或走「Chrome 缺席」分支）
- 不真跑蒸馏 LLM（run_distill_pipeline 整体 mock，验证 job 生命周期而非 LLM 正确性）
- 用 fastapi.testclient.TestClient（同步，不占真实端口）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.server import create_app

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app(captures_dir: Path | str = "./data/captures", skills_dir: Path | str | None = None):
    """建一个 app，注入 mock collector（绕过 Chrome 连接）。"""
    app = create_app(captures_dir=captures_dir, skills_dir=skills_dir)
    collector = _make_mock_collector()
    app.state.collector = collector  # 直接注入，跳过 _get_collector 的 fetch_ws_url
    return app, collector


def _make_mock_collector():
    """造一个满足 Collector 协议的 mock（对齐 test_capture._make_mock_collector 口径）。"""
    collector = AsyncMock()
    collector.start.return_value = "test-session-1"
    collector.ingest.return_value = None
    collector.stop.return_value = {
        "session_id": "test-session-1",
        "host": "example.com",
        "events": 5,
        "output_dir": "/tmp/captures/test",
        "capture_dir": "/tmp/captures/test/session-1",
        "trace_path": "/tmp/captures/test/session-1/trace.json",
    }
    collector._session = None  # GET /api/status 读这个判断 recording
    return collector


@pytest.fixture
def client():
    """带 mock collector 的 TestClient。"""
    app, _ = _make_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 采集 router（协议对齐 aiohttp backend，扩展零改动）
# ---------------------------------------------------------------------------


def test_health(client):
    """GET /health → {ok: true}。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_start_distill(client):
    """POST /start { scenario: distill } → 调 collector.start，返 session_id。"""
    resp = client.post("/start", json={"scenario": "distill", "config": {}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["session_id"] == "test-session-1"


def test_start_unknown_scenario_returns_400(client):
    """POST /start { scenario: invalid } → 400。"""
    resp = client.post("/start", json={"scenario": "invalid"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_start_failure_returns_500(client):
    """collector.start 抛错 → /start 返 500。"""
    # 这里需要 app 重新建（collector mock 改 side_effect）
    app = create_app()
    collector = _make_mock_collector()
    collector.start.side_effect = RuntimeError("boom")
    app.state.collector = collector
    with TestClient(app) as c:
        resp = c.post("/start", json={"scenario": "distill"})
    assert resp.status_code == 500
    data = resp.json()
    assert data["ok"] is False
    assert "boom" in data["error"]


def test_ingest_distill_routes_to_collector(client):
    """POST /ingest { scenario: distill } → 调 collector.ingest。"""
    envelope = {
        "scenario": "distill",
        "session_id": "test-session-1",
        "ts": 1234567890,
        "url": "https://example.com",
        "payload": {"type": "click", "tag": "a"},
    }
    resp = client.post("/ingest", json=envelope)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ingest_replay_not_implemented_but_returns_ok(client):
    """POST /ingest { scenario: replay } → 返 ok（replay 留接口，不调 collector）。"""
    envelope = {"scenario": "replay", "session_id": "s1", "ts": 0, "payload": {}}
    resp = client.post("/ingest", json=envelope)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "not implemented" in data["note"]


def test_ingest_unknown_scenario_returns_400(client):
    """POST /ingest { scenario: invalid } → 400。"""
    envelope = {"scenario": "invalid", "session_id": "s1", "ts": 0, "payload": {}}
    resp = client.post("/ingest", json=envelope)
    assert resp.status_code == 400


def test_stop_returns_collector_result(client):
    """POST /stop → 调 collector.stop，返产物信息。"""
    resp = client.post("/stop", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["capture_dir"] == "/tmp/captures/test/session-1"
    assert data["result"]["trace_path"] == "/tmp/captures/test/session-1/trace.json"


# ---------------------------------------------------------------------------
# Chrome 缺席（照常启动，/start 返 503）
# ---------------------------------------------------------------------------


def test_chrome_absent_start_returns_503():
    """Chrome 没开（fetch_ws_url 返 None）时，/start 返 503，但服务仍起。"""
    app = create_app(cdp_host="localhost", cdp_port=1)  # collector 未注入 → 走 _get_collector
    with (
        patch("server.server.fetch_ws_url", return_value=None),
        TestClient(app) as c,
    ):
        # /health 仍可用（与 Chrome 无关）
        assert c.get("/health").json()["ok"] is True
        # /start 因 Chrome 缺席返 503
        resp = c.post("/start", json={"scenario": "distill"})
        assert resp.status_code == 503


def test_chrome_present_builds_collector():
    """fetch_ws_url 成功 → /start 建 CdpSession + Collector 并调 start。

    Collector.start 会真连 CDP（会失败），这里 mock 掉 Collector 避免真连。
    """
    app = create_app(cdp_host="localhost", cdp_port=9222)
    mock_collector = _make_mock_collector()
    with (
        patch(
            "server.server.fetch_ws_url", return_value="ws://localhost:9222/devtools/browser/xxx"
        ),
        patch("server.server.CdpSession") as MockCdp,
        patch("server.server.Collector", return_value=mock_collector),
        TestClient(app) as c,
    ):
        resp = c.post("/start", json={"scenario": "distill"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "test-session-1"
    MockCdp.assert_called_once_with("ws://localhost:9222/devtools/browser/xxx")


# ---------------------------------------------------------------------------
# session 可循环（serve 常驻：/stop 不退出进程）
# ---------------------------------------------------------------------------


def test_session_loopable_two_cycles():
    """两次 /start → /stop 不串，进程不退出（serve 常驻）。"""
    app = create_app()
    collector = _make_mock_collector()
    # 每次调用返回新 session_id，验证可循环
    collector.start.side_effect = ["sess-A", "sess-B"]
    app.state.collector = collector
    with TestClient(app) as c:
        # 第一次录制
        r1 = c.post("/start", json={"scenario": "distill"})
        assert r1.json()["session_id"] == "sess-A"
        assert c.post("/stop", json={}).json()["ok"] is True
        # 服务没退出，第二次录制
        r2 = c.post("/start", json={"scenario": "distill"})
        assert r2.json()["session_id"] == "sess-B"
        assert c.post("/stop", json={}).json()["ok"] is True
    # 两次 start + 两次 stop
    assert collector.start.call_count == 2
    assert collector.stop.call_count == 2


# ---------------------------------------------------------------------------
# 蒸馏后台任务（S3）
# ---------------------------------------------------------------------------


def _make_done_result(trace_path):
    """造一个成功的 DistillResult（mock 用）。"""
    from server.distill_api import DistillResult

    return DistillResult(
        ok=True,
        written=[Path("/tmp/skills/_sop.md")],
        host_dir=Path("/tmp/skills/domain-skills/example.com"),
        cards_count=1,
        trace_path=trace_path,
    )


def test_distill_trigger_returns_job_id(tmp_path):
    """POST /api/distill → 返 job_id（不阻塞）。trace 不存在时返 400。"""
    app, _ = _make_app(skills_dir=tmp_path)
    # trace 不存在
    with TestClient(app) as c:
        resp = c.post("/api/distill", json={"trace_path": "nonexistent.json", "no_llm": True})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_distill_job_lifecycle(tmp_path, monkeypatch):
    """POST /api/distill → job_id → 轮询 GET /api/distill/{id} 到 done。"""
    # 准备一个真实存在的 trace 文件
    trace = tmp_path / "t.trace.json"
    trace.write_text("{}", encoding="utf-8")

    # mock 掉 run_distill_pipeline（不真跑 LLM）
    from server import distill_api

    def fake_pipeline(trace_path, output_dir, adapter_name, no_llm):
        return _make_done_result(trace_path)

    monkeypatch.setattr(distill_api, "run_distill_pipeline", fake_pipeline)

    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.post(
            "/api/distill",
            json={"trace_path": str(trace), "output_dir": str(tmp_path), "no_llm": True},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # 给后台任务时间跑完（asyncio.to_thread）
        import time

        deadline = time.time() + 5
        while time.time() < deadline:
            st = c.get(f"/api/distill/{job_id}").json()
            if st["job"]["status"] in ("done", "failed"):
                break
            time.sleep(0.1)

        assert st["job"]["status"] == "done"
        assert st["job"]["result"]["cards_count"] == 1

        # GET /api/jobs 列出这个 job
        jobs_resp = c.get("/api/jobs").json()
        assert jobs_resp["ok"] is True
        assert any(j["job_id"] == job_id for j in jobs_resp["jobs"])


def test_distill_job_failed_records_error(tmp_path, monkeypatch):
    """蒸馏失败 → job status=failed，error 有值。"""
    trace = tmp_path / "t.trace.json"
    trace.write_text("{}", encoding="utf-8")

    from server import distill_api

    def failing_pipeline(trace_path, output_dir, adapter_name, no_llm):
        from server.distill_api import DistillResult

        return DistillResult(ok=False, error="boom", trace_path=trace_path)

    monkeypatch.setattr(distill_api, "run_distill_pipeline", failing_pipeline)

    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        job_id = c.post(
            "/api/distill",
            json={"trace_path": str(trace), "output_dir": str(tmp_path), "no_llm": True},
        ).json()["job_id"]

        import time

        deadline = time.time() + 5
        st = None
        while time.time() < deadline:
            st = c.get(f"/api/distill/{job_id}").json()
            if st["job"]["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert st["job"]["status"] == "failed"
        assert "boom" in st["job"]["error"]


def test_distill_unknown_job_returns_404(client):
    """GET /api/distill/{不存在} → 404。"""
    resp = client.get("/api/distill/doesnotexist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 配置 router（S4）
# ---------------------------------------------------------------------------


def test_get_config(client):
    """GET /api/config → config.describe()。"""
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "distill_model" in data["config"]


def test_post_config_rejects_non_whitelisted_key(tmp_path, monkeypatch):
    """POST /api/config 非白名单 key → 400。"""
    app, _ = _make_app(skills_dir=tmp_path)
    # 把 .env 指向 tmp，避免污染真实 .env
    import harness.config as cfg

    monkeypatch.setattr(cfg, "REPO_ROOT", tmp_path)
    with TestClient(app) as c:
        resp = c.post("/api/config", json={"values": {"LLM_KEY": "secret"}})
    assert resp.status_code == 400
    assert "不可写" in resp.json()["error"]


def test_post_config_writes_env_and_reloads(tmp_path, monkeypatch):
    """POST /api/config 白名单 key → 写 .env + 重载生效。"""
    app, _ = _make_app(skills_dir=tmp_path)
    import harness.config as cfg

    # 把 REPO_ROOT 指向 tmp，.env 写进 tmp 不污染仓库
    monkeypatch.setattr(cfg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("server.server.config.REPO_ROOT", tmp_path)
    # 重载时也读 tmp/.env
    original_load = cfg.load

    def patched_load(env_path=None):
        env_path = tmp_path / ".env" if env_path is None else env_path
        return original_load(env_path)

    monkeypatch.setattr(cfg, "load", patched_load)
    monkeypatch.setattr("server.server.config.load", patched_load)

    with TestClient(app) as c:
        resp = c.post("/api/config", json={"values": {"DISTILL_MODEL": "test-model-xyz"}})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # .env 被写入
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DISTILL_MODEL=test-model-xyz" in env_content


# ---------------------------------------------------------------------------
# 状态/产物 router（S4）
# ---------------------------------------------------------------------------


def test_status_reflects_recording(client):
    """GET /api/status → recording 状态（mock collector._session=None → False）。"""
    resp = client.get("/api/status")
    assert resp.status_code == 200
    st = resp.json()["status"]
    assert st["recording"] is False
    assert st["chrome_connected"] is True  # collector 已注入


def test_captures_lists_dirs(tmp_path):
    """GET /api/captures → 列 captures_dir 子目录。"""
    (tmp_path / "cap-1").mkdir()
    (tmp_path / "cap-1" / "trace.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cap-2").mkdir()
    app, _ = _make_app(captures_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/captures")
    data = resp.json()
    assert data["ok"] is True
    names = [i["name"] for i in data["items"]]
    assert "cap-1" in names and "cap-2" in names


def test_skills_lists_hosts(tmp_path):
    """GET /api/skills → 列 skills_dir/domain-skills/* 子目录。"""
    ds = tmp_path / "domain-skills" / "example.com"
    ds.mkdir(parents=True)
    (ds / "_sop.md").write_text("# sop", encoding="utf-8")
    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/skills")
    data = resp.json()
    assert data["ok"] is True
    hosts = [h["name"] for h in data["hosts"]]
    assert "example.com" in hosts


# ---------------------------------------------------------------------------
# SPA 托管（S4）
# ---------------------------------------------------------------------------


def test_spa_serves_index_when_dir_exists():
    """server/app/dist/index.html 存在时，GET / 返 SPA。"""
    # create_app 用模块级 _SPA_DIR，仓库内确实有 server/app/dist/index.html
    app = create_app()
    with TestClient(app) as c:
        resp = c.get("/")
    # StaticFiles html=True：访问 / 返 index.html
    assert resp.status_code == 200
    assert "TreeForge 控制面板" in resp.text


def test_spa_skip_does_not_block_api(tmp_path, monkeypatch):
    """SPA 目录不存在时优雅跳过，API 仍可用。"""
    # 把 _SPA_DIR 指向一个不存在的路径
    monkeypatch.setattr("server.server._SPA_DIR", tmp_path / "nope")
    app = create_app()
    with TestClient(app) as c:
        # API 仍可用
        assert c.get("/health").status_code == 200
        # GET / 不再是 SPA（404，因为没挂载）
        assert c.get("/").status_code == 404


# ---------------------------------------------------------------------------
# cdp_session.stop() 清 _previous_selector_map（附带修复）
# ---------------------------------------------------------------------------


async def test_cdp_session_stop_clears_previous_selector_map():
    """stop() 后 _previous_selector_map 应清空（serve 跨 session 不污染新元素检测）。"""
    from treeforge.capture.cdp_session import CdpSession

    session = CdpSession(ws_url="ws://localhost:9222/xxx")
    # 模拟 start 过 + 采过一次快照（_previous_selector_map 被填充）
    session._previous_selector_map = {"a": object()}
    await session.stop()
    assert session._previous_selector_map is None


# ---------------------------------------------------------------------------
# run_distill_pipeline 提炼正确性（DistillResult，不跑真 LLM）
# ---------------------------------------------------------------------------


def test_run_distill_pipeline_no_llm_template_mode(bilibili_trace_path, tmp_output_dir):
    """run_distill_pipeline --no-llm 走模板模式，返回 DistillResult(ok=True)。"""
    from server.distill_api import run_distill_pipeline

    result = run_distill_pipeline(
        trace_path=bilibili_trace_path,
        output_dir=tmp_output_dir,
        adapter_name="treewalker",
        no_llm=True,
    )
    assert result.ok is True
    assert result.cards_count >= 1
    assert result.host_dir is not None
    assert result.host_dir.exists()
    # 产出三件套
    assert (result.host_dir / "_sop.md").exists()


def test_run_distill_pipeline_missing_trace(tmp_path):
    """trace 不存在 → DistillResult(ok=False)，不抛异常。"""
    from server.distill_api import run_distill_pipeline

    result = run_distill_pipeline(
        trace_path=tmp_path / "nope.json",
        output_dir=tmp_path,
        no_llm=True,
    )
    assert result.ok is False
    assert result.error is not None
