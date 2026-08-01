# P3 常驻服务实施计划

> 本文档是 `serve-plan.md`（架构方案）的落地执行计划。已确认两个决策：
> - **节奏**：S1 → S3 → S4 一次全做完（每步跑测试，最后统一汇报）
> - **Chrome 缺席**：serve 照常启动，蒸馏/配置/状态 API 与 Chrome 无关；采集 /start 在 Chrome 没连时返 503
>
> S2（collector session 可循环）P2 已做完（start 重建 StageTracker + 重置 _attached_tab，stop 清空 _session），本计划跳过。

## 依赖（pyproject.toml）

- `[project] dependencies` 加 `fastapi>=0.110,<1.0`、`uvicorn[standard]>=0.27,<1.0`
- 保留 aiohttp（capture 子命令仍用；旧 `tests/test_capture.py` 仍依赖）
- `uv sync --extra dev`

## S1：FastAPI 服务骨架 + 采集 router 迁移（扩展零改动）

### 新建 `server/server.py`

FastAPI app 工厂：

```python
def create_app(cdp_host, cdp_port, captures_dir, skills_dir) -> FastAPI
```

**采集 router**（`/start` `/ingest` `/stop` `/health`，与 aiohttp backend 协议逐字一致，Pydantic 模型化）：

- Pydantic 模型：`StartRequest(scenario: str = "distill", config: dict | None = None)`、`EnvelopeModel(scenario, session_id, ts, url: str | None, payload: dict)`
- 单例 Collector 懒建：首次 /start 时 `fetch_ws_url` + 建 `CdpSession` + `Collector`，存 `app.state.collector`
- `/start`：scenario 校验（distill/replay），调 `collector.start`，返 `{ok, session_id}`；500 兜底
- `/ingest`：replay 返 `{ok:true, note:"replay not implemented"}`（保持现状），distill 调 `collector.ingest`
- `/stop`：调 `collector.stop`，返 `{ok, result}`（不再设 stop_event —— serve 常驻，session 可循环）
- `/health`：`{ok:true}`

**Chrome 缺席策略（照常启动）**：

- app 启动时不连 CDP；`fetch_ws_url` 失败不报错，懒到首次 /start 才建 CdpSession
- /start 时 Chrome 没连上 → CdpSession.start 抛错 → /start 返 503 `{ok:false, error:"Chrome 未连接"}`
- 蒸馏 / 配置 / 状态 API 与 Chrome 无关，照常可用

### 新建 `treeforge/serve.py`

```python
def run_serve(host, port, cdp_host, cdp_port, captures_dir, skills_dir):
    uvicorn.run(create_app(...))
```

### `__main__.py`

加 `serve` 子命令（`--host`/`--port`/`--cdp-host`/`--cdp-port`/`--captures-dir`/`--skills-dir`，默认端口 8765），调 `run_serve`。

### S1 测试（`tests/test_serve.py`）

用 `fastapi.testclient.TestClient`（同步，不占端口）：

- 4 个采集端点协议测试（mock collector，复用 test_capture 口径：health/start/ingest distill/ingest replay/ingest 400/stop 返 result/start 失败 500）
- Chrome 缺席：/start 返 503（mock `fetch_ws_url` 返 None）
- session 可循环：两次 /start → /stop 不串（mock collector）

## S3：蒸馏后台任务（提炼 + job dict + 进度注入）

### 新建 `server/distill_api.py`

```python
@dataclass
class DistillResult:
    ok: bool
    written: list[Path]
    host_dir: Path | None
    error: str | None
    trace_path: Path
```

- `run_distill_pipeline(trace_path, output_dir, adapter_name, no_llm) -> DistillResult`：从 `__main__._run_distill` 提炼（去 CLI 味：不 print、不返退出码、不依赖 argparse）；内部按需 `config.load()` + 跑 ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL → INSTALL
- 全局 `_jobs: dict[str, JobStatus]` + `_PIPELINE_LOCK = asyncio.Lock()`；`JobStatus{status, phase, current, total, detail, result, error, started_at, finished_at}`
- `async def start_distill_job(...) -> str`：建 job_id，`asyncio.create_task` 跑 `_run_job`（`async with _PIPELINE_LOCK` + `asyncio.to_thread(run_distill_pipeline, ...)`），`progress.set_reporter` 注入 job dict，任务结束恢复原 reporter
- `get_job(job_id)` / `list_jobs()`

### 蒸馏 router（加进 server.py）

- `POST /api/distill`（body: `trace_path`/`output_dir?`/`adapter?`/`no_llm?`）→ `{job_id}`
- `GET /api/distill/{job_id}` → job status
- `GET /api/jobs` → 全部 job 列表

### `__main__.py`

`_run_distill` 改薄包装，调 `server.distill_api.run_distill_pipeline`（CLI 行为不变：print + 退出码在 __main__ 里加回去）。

### S3 测试

mock `run_distill_pipeline`（不真跑 LLM），验证 POST /api/distill 返 job_id、轮询 /api/distill/{id} 到 done。

## S4：配置/状态/产物 router + 控制面板 SPA

### 配置 router

- `GET /api/config` → `config.describe()`
- `POST /api/config` → 原子写 `.env`（`os.replace`，白名单 key：`DISTILL_MODEL`/`CLASSIFY_MODEL`/`LLM_BASE`/`LLM_TIMEOUT`）

### 状态/产物 router

- `GET /api/status`：serve 健康 + 录制状态
- `GET /api/captures`：列 captures_dir 子目录
- `GET /api/skills`：列 skills_dir/domain-skills/*

### 控制面板 SPA

`server/app/dist/index.html` —— 纯 HTML + 原生 fetch（录制状态卡 / 触发蒸馏表单 / 配置展示），`app.mount("/", StaticFiles(directory=..., html=True))`；目录不存在时优雅跳过挂载（不阻断 API）。

### S4 测试

- `GET /api/config` 返配置
- `GET /api/captures` 列目录（tmp dir 注入）
- SPA 挂载存在/不存在两种情况

## 附带修复（serve-plan S5 残留，跨 session 状态隔离）

`treeforge/capture/cdp_session.py` 的 `stop()` 加 `self._previous_selector_map = None`（当前漏清，serve 长期运行跨 session 会污染新元素检测）—— 1 行 + 1 测试。

## 验证（每步 + 全量）

- 每步后：`uv run python -m pytest tests/test_serve.py -v`（增量）+ `uv run ruff check .` + `uv run ruff format .`
- 全部完成：`uv run python -m pytest tests/ -x -v`（全量，含旧 test_capture.py 不破坏）
- 端到端冒烟（手动说明，不入自动化测试）：
  ```bash
  uv run treeforge serve
  # 另一终端
  curl http://127.0.0.1:8765/health
  curl -X POST http://127.0.0.1:8765/api/distill \
    -H "Content-Type: application/json" \
    -d '{"trace_path":"examples/bilibili-upload.trace.json","no_llm":true}'
  curl http://127.0.0.1:8765/api/jobs   # 轮询到 done
  ```

## 不做（serve-plan 已界定）

- 控制面板完整 UI（参数表单、产物浏览树）—— 后续迭代
- 多用户/多 Chrome 同录 —— 单用户单 Chrome
- job 持久化（重启丢 job）—— 内存 dict 即可
- 不删 capture 子命令 / aiohttp（保留给无 UI 脚本场景）
- 不动扩展、不动 harness 蒸馏五阶段、不动 distill_schema 双端契约

## 改动文件清单

- **新增**：`server/server.py`、`server/distill_api.py`、`server/app/dist/index.html`、`treeforge/serve.py`、`tests/test_serve.py`、`docs/p3/serve-plan.md`（mv 自 docs/p2）、本文件
- **改**：`pyproject.toml`、`treeforge/__main__.py`、`treeforge/capture/cdp_session.py`（1 行）、`docs/p2/handoff.md`（2 处引用路径）
- **不动**：`treeforge/capture/*`（backend/cli/collector/export/stage/distill_schema/ws_discover）、`extension/`、`harness/`（仅 install/progress 已有接口复用）
