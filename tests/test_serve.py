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
    """带 mock collector 的 TestClient。collector 挂到 c._collector 便于断言调用。"""
    app, collector = _make_app()
    collector.attach_signal = AsyncMock(return_value=True)
    with TestClient(app) as c:
        c._collector = collector  # type: ignore[attr-defined]
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
# /signal 端点（P3.6 副作用信号，迁自 TreeWalker）
# ---------------------------------------------------------------------------


def test_signal_attaches_to_collector(client):
    """POST /signal → 调 collector.attach_signal，返 {ok, attached}。"""
    client._collector.attach_signal.return_value = True  # type: ignore[attr-defined]
    resp = client.post(
        "/signal",
        json={
            "session_id": "test-session-1",
            "payload": {"type": "modal_opened", "selector": "div.ant-modal", "ts": 1700000000000},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["attached"] is True
    # 确认 collector.attach_signal 被调，payload 透传
    client._collector.attach_signal.assert_called_once()  # type: ignore[attr-defined]
    called_payload = client._collector.attach_signal.call_args[0][0]  # type: ignore[attr-defined]
    assert called_payload["type"] == "modal_opened"


def test_signal_no_active_session_returns_400():
    """POST /signal 但 collector 未注入（无活跃 session）→ 400。"""
    app = create_app()
    # 不注入 collector（app.state.collector 仍是 None）
    with TestClient(app) as c:
        resp = c.post("/signal", json={"session_id": "s1", "payload": {"type": "modal_opened"}})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_signal_failure_returns_500():
    """collector.attach_signal 抛错 → /signal 返 500。"""
    app = create_app()
    collector = _make_mock_collector()
    collector.attach_signal.side_effect = RuntimeError("boom")
    app.state.collector = collector
    with TestClient(app) as c:
        resp = c.post("/signal", json={"session_id": "s1", "payload": {"type": "modal_opened"}})
    assert resp.status_code == 500
    assert "boom" in resp.json()["error"]


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
    app = create_app(cdp_host="localhost", cdp_port=9223)
    mock_collector = _make_mock_collector()
    with (
        patch(
            "server.server.fetch_ws_url", return_value="ws://localhost:9223/devtools/browser/xxx"
        ),
        patch("server.server.CdpSession") as MockCdp,
        patch("server.server.Collector", return_value=mock_collector),
        TestClient(app) as c,
    ):
        resp = c.post("/start", json={"scenario": "distill"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "test-session-1"
    MockCdp.assert_called_once_with("ws://localhost:9223/devtools/browser/xxx")


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

    def fake_pipeline(
        trace_paths, output_dir, adapter_name, no_llm, fresh=False, task_description=None
    ):
        first = trace_paths[0] if isinstance(trace_paths, list) else trace_paths
        return _make_done_result(first)

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

    def failing_pipeline(
        trace_paths, output_dir, adapter_name, no_llm, fresh=False, task_description=None
    ):
        from server.distill_api import DistillResult

        first = trace_paths[0] if isinstance(trace_paths, list) else trace_paths
        return DistillResult(ok=False, error="boom", trace_path=first)

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
# P4：host 模式累积蒸馏 + 任务描述透传
# ---------------------------------------------------------------------------


def test_distill_requires_trace_or_host_exactly_one(client):
    """POST /api/distill：trace_path 与 host 必须恰好提供一个（都空/都给 → 400）。"""
    # 都空
    resp = client.post("/api/distill", json={"no_llm": True})
    assert resp.status_code == 400
    assert "二选一" in resp.json()["error"]
    # 都给
    resp = client.post(
        "/api/distill", json={"trace_path": "a.json", "host": "x.com", "no_llm": True}
    )
    assert resp.status_code == 400
    assert "二选一" in resp.json()["error"]


def test_distill_host_mode_collects_traces(tmp_path, monkeypatch):
    """host 模式：扫 captures 收集该 host 全部 trace → 多 trace 走管线。"""
    import json as json_mod

    # 造两份同 host trace + 一份其它 host
    caps = tmp_path / "caps"
    for name, host in [("cap-a", "x.com"), ("cap-b", "x.com"), ("cap-c", "other.com")]:
        d = caps / name
        d.mkdir(parents=True)
        (d / "trace.json").write_text(
            json_mod.dumps({"host": host, "events": []}), encoding="utf-8"
        )
    # 损坏 trace（应跳过）
    bad = caps / "cap-bad"
    bad.mkdir()
    (bad / "trace.json").write_text("{broken", encoding="utf-8")

    from server import distill_api

    captured_kwargs: list = []

    async def fake_start(
        trace_paths,
        output_dir,
        adapter_name="treewalker",
        no_llm=False,
        fresh=False,
        task_description=None,
    ):
        captured_kwargs.append({"traces": list(trace_paths), "task_description": task_description})
        return "job-1"

    monkeypatch.setattr(distill_api, "start_distill_job", fake_start)

    app = create_app(captures_dir=caps, skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.post(
            "/api/distill",
            json={"host": "x.com", "no_llm": True, "task_description": "上传视频"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["traces"] == 2  # cap-a + cap-b（cap-c 不同 host、cap-bad 损坏均跳过）
    # 收集到的 trace 路径
    got = captured_kwargs[0]["traces"]
    assert len(got) == 2
    assert all("trace.json" in str(p) for p in got)
    # 任务描述透传
    assert captured_kwargs[0]["task_description"] == "上传视频"


def test_distill_host_mode_no_traces_returns_400(tmp_path):
    """host 模式：captures 里没有该 host 的 trace → 400。"""
    app = create_app(captures_dir=tmp_path / "empty", skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.post("/api/distill", json={"host": "nope.com"})
    assert resp.status_code == 400
    assert "nope.com" in resp.json()["error"]


def test_distill_task_description_passthrough(tmp_path, monkeypatch):
    """trace_path 模式：task_description 透传进 start_distill_job。"""
    trace = tmp_path / "t.trace.json"
    trace.write_text("{}", encoding="utf-8")

    from server import distill_api

    captured: list = []

    async def fake_start(
        trace_paths,
        output_dir,
        adapter_name="treewalker",
        no_llm=False,
        fresh=False,
        task_description=None,
    ):
        captured.append(task_description)
        return "job-2"

    monkeypatch.setattr(distill_api, "start_distill_job", fake_start)

    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.post(
            "/api/distill",
            json={"trace_path": str(trace), "task_description": "发布抖音视频"},
        )
    assert resp.status_code == 200
    assert captured == ["发布抖音视频"]


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
# 配置自检（P3.5 S3：GET /api/config/check）
# ---------------------------------------------------------------------------


def test_config_check_no_key_returns_error(monkeypatch):
    """LLM_KEY 未配置 → /api/config/check 返 {ok:false, error}（不 500）。"""
    import harness.config as cfg

    monkeypatch.setattr(cfg, "LLM_KEY", "")
    monkeypatch.setattr("server.server.config.LLM_KEY", "")
    app, _ = _make_app()
    with TestClient(app) as c:
        resp = c.get("/api/config/check")
    data = resp.json()
    assert resp.status_code == 200
    assert data["ok"] is False
    assert "LLM_KEY" in data["error"]


def test_config_check_success(monkeypatch):
    """call_llm_fast 成功 → 返 {ok:true, model, reply_len}（mock，不真发）。"""
    import harness.config as cfg

    monkeypatch.setattr(cfg, "LLM_KEY", "fake-key")
    monkeypatch.setattr("server.server.config.LLM_KEY", "fake-key")
    monkeypatch.setattr(cfg, "CLASSIFY_MODEL", "test-cls")
    monkeypatch.setattr("server.server.config.CLASSIFY_MODEL", "test-cls")
    # mock 掉 call_llm_fast（不真发网络请求，对齐 AGENTS.md 测试原则）
    monkeypatch.setattr(
        "harness.llm.call_llm_fast",
        lambda prompt, **kw: ("pong", {"in": 1, "out": 1}),
    )
    app, _ = _make_app()
    with TestClient(app) as c:
        resp = c.get("/api/config/check")
    data = resp.json()
    assert resp.status_code == 200
    assert data["ok"] is True
    assert data["model"] == "test-cls"
    assert data["reply_len"] == 4


def test_config_check_failure_returns_error(monkeypatch):
    """call_llm_fast 抛异常 → 返 {ok:false, error}（不 500，前端友好）。"""
    import harness.config as cfg

    monkeypatch.setattr(cfg, "LLM_KEY", "fake-key")
    monkeypatch.setattr("server.server.config.LLM_KEY", "fake-key")
    monkeypatch.setattr(
        "harness.llm.call_llm_fast",
        lambda prompt, **kw: (_ for _ in ()).throw(ConnectionError("refused")),
    )
    app, _ = _make_app()
    with TestClient(app) as c:
        resp = c.get("/api/config/check")
    data = resp.json()
    assert resp.status_code == 200  # 不 500（设计：自检失败返 200 + ok:false）
    assert data["ok"] is False
    assert "refused" in data["error"]


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
    """GET /api/captures → 列 captures_dir 子目录（含 mtime 创建时间字段）。"""
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
    # 每个 item 含创建时间（mtime_ms 毫秒整数戳 + ISO 字符串，前端展示/排序用）
    for item in data["items"]:
        assert "mtime_ms" in item and isinstance(item["mtime_ms"], int)
        # 毫秒戳应在「最近」量级（> 2000 年），防 st_mtime 秒级浮点未乘 1000 的回归
        assert item["mtime_ms"] > 946684800000  # 2000-01-01 的毫秒戳
        assert "mtime_iso" in item and "T" in item["mtime_iso"]  # ISO 格式


def test_captures_sorted_by_mtime_newest_first(tmp_path):
    """GET /api/captures → 按 mtime 倒序（最新在前），让用户一眼看到最新产物。"""
    import os
    import time

    # 建两个 capture，old 先建（mtime 早），new 后建（mtime 晚）
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "trace.json").write_text("{}", encoding="utf-8")
    # 强制 old 的 mtime 比 now 早 1 小时
    old_ts = time.time() - 3600
    os.utime(old_dir / "trace.json", (old_ts, old_ts))

    time.sleep(0.05)  # 确保 new 的 mtime 严格晚于 old
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    (new_dir / "trace.json").write_text("{}", encoding="utf-8")

    app, _ = _make_app(captures_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/captures")
    items = resp.json()["items"]
    # 最新（new）应排第一
    assert [i["name"] for i in items] == ["new", "old"]
    assert items[0]["mtime_ms"] > items[1]["mtime_ms"]


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
# 配套 API（P3.5 S1：status 扩展 / capture 详情 / skills 文件列表与预览）
# ---------------------------------------------------------------------------


def test_status_includes_session_when_recording():
    """录制中时 /api/status 返 session 详情（session_id/events/stages/current_stage）。"""
    app, collector = _make_app()
    # 模拟录制中：collector._session 有值，含 events + page_context
    session = type(
        "S",
        (),
        {
            "session_id": "sess-xyz",
            "host": "example.com",
            "task_instruction": "demo",
            "events": [type("E", (), {"stage": "upload"})(), type("E", (), {"stage": "upload"})()],
            "page_context": {"upload": "<dom>", "publish": "<dom2>"},
        },
    )()
    collector._session = session
    with TestClient(app) as c:
        resp = c.get("/api/status")
    st = resp.json()["status"]
    assert st["recording"] is True
    assert st["session"]["session_id"] == "sess-xyz"
    assert st["session"]["host"] == "example.com"
    assert st["session"]["events"] == 2
    assert st["session"]["current_stage"] == "upload"
    assert st["session"]["stages"] == ["upload", "publish"]


def test_status_no_session_when_not_recording(client):
    """未录制时 /api/status 不含 session 字段。"""
    resp = client.get("/api/status")
    st = resp.json()["status"]
    assert st["recording"] is False
    assert "session" not in st


def test_capture_detail_returns_trace_summary(tmp_path):
    """GET /api/captures/{name} → 读 trace.json 摘要（events 数 + stages + snapshots）。"""
    cap = tmp_path / "cap-1"
    cap.mkdir()
    (cap / "trace.json").write_text(
        '{"host":"example.com","task_instruction":"demo",'
        '"events":[{"type":"click"},{"type":"input"}],"page_context":{"upload":"<dom>"}}',
        encoding="utf-8",
    )
    snaps = cap / "snapshots"
    snaps.mkdir()
    (snaps / "upload.txt").write_text("dom", encoding="utf-8")
    app, _ = _make_app(captures_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/captures/cap-1")
    data = resp.json()
    assert resp.status_code == 200
    assert data["ok"] is True
    assert data["host"] == "example.com"
    assert data["events"] == 2
    assert data["stages"] == ["upload"]
    assert data["snapshots"] == ["upload.txt"]


def test_capture_detail_404_when_missing(tmp_path):
    """GET /api/captures/{不存在} → 404。"""
    app, _ = _make_app(captures_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/captures/nope")
    assert resp.status_code == 404


def test_skill_files_lists_md(tmp_path):
    """GET /api/skills/{host}/files → 列 md 文件（名 + 大小）。"""
    ds = tmp_path / "domain-skills" / "example.com"
    ds.mkdir(parents=True)
    (ds / "_sop.md").write_text("# sop content", encoding="utf-8")
    (ds / "quirks.md").write_text("# quirks", encoding="utf-8")
    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/skills/example.com/files")
    data = resp.json()
    assert resp.status_code == 200
    names = [f["name"] for f in data["files"]]
    assert "_sop.md" in names and "quirks.md" in names
    assert all("size" in f for f in data["files"])


def test_skill_file_content_returns_text(tmp_path):
    """GET /api/skills/{host}/files/{filename} → 返 md 原文。"""
    ds = tmp_path / "domain-skills" / "example.com"
    ds.mkdir(parents=True)
    (ds / "_sop.md").write_text("# 标题\n\n正文内容", encoding="utf-8")
    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/skills/example.com/files/_sop.md")
    data = resp.json()
    assert resp.status_code == 200
    assert "# 标题" in data["content"]
    assert "正文内容" in data["content"]


def test_skill_file_content_404_when_missing(tmp_path):
    """GET /api/skills/{host}/files/{不存在} → 404。"""
    ds = tmp_path / "domain-skills" / "example.com"
    ds.mkdir(parents=True)
    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/skills/example.com/files/nope.md")
    assert resp.status_code == 404


def test_skill_file_content_rejects_path_traversal(tmp_path):
    """GET /api/skills/{host}/files/{含分隔符} → 400（防路径越界）。"""
    app, _ = _make_app(skills_dir=tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/skills/example.com/files/..%2F..md")
    # 文件名含 / （URL 解码后）→ 不匹配 [^/\\]+\.md → 400 或不存在的安全路径
    # 关键：不能读到 domain-skills 之外的文件
    assert resp.status_code in (400, 404)


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

    session = CdpSession(ws_url="ws://localhost:9223/xxx")
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
        trace_paths=bilibili_trace_path,
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
        trace_paths=tmp_path / "nope.json",
        output_dir=tmp_path,
        no_llm=True,
    )
    assert result.ok is False
    assert result.error is not None
