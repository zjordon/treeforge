# 常驻服务重构方案（P3 范围）

> **状态：P3 范围，暂不实施**。P2 先把采集链路跑通验证质量，P3 再做服务化。
> 本文档保留作为 P3 实施时的方案参考。
>
> 把「一次性 capture 命令」重构为「FastAPI 常驻服务 + 控制面板」，
> 对齐 Browser-BC 架构（常驻后端、扩展随时连、录完不退出）。
> 决策：① 新增 `serve`，保留 `capture` ② 换 FastAPI ③ P3 实施。

## 一、背景：当前架构的问题

当前 `treeforge capture` 是「录完就退出」的一次性命令，不符合常驻服务定位。

**理想架构（对齐 Browser-BC）**：
- 后端是常驻进程，扩展随时连，录完不退出
- 后续支持控制面板（配模型参数、触发蒸馏）
- 采集（扩展发事件）+ 快照（后端经 CDP 用 dom-snapshot）协同

**当前问题**：
- `run_capture` 的 `await stop_event.wait()` 后直接 return → 进程退出
- 扩展点「停止」会触发 `on_stop` 设 stop_event → 整个命令退出（不是只结束 session）
- Collector 单 session + StageTracker 跨 session 不重置 → 无法循环录制
- distill 是独立 CLI 命令，无法从控制面板触发

## 二、整体目标

把一次性 capture 改造为常驻 FastAPI 服务 `treeforge serve`：
- 采集层（4 端点）+ 蒸馏 API + 控制面板 共用一个常驻服务
- session 可循环（start/stop 多次录制不退出进程）
- CdpSession 每次 start 连 / stop 断（session 级生命周期）
- distill 提炼为可被 HTTP 触发的后台任务（asyncio.to_thread 包同步 LLM）

```
┌─────────────────────────────────────────────────────────────┐
│              treeforge serve（常驻 FastAPI 服务）             │
│                                                              │
│  采集 router（迁移自 aiohttp，协议不变）                       │
│    POST /start /ingest /stop   GET /health                   │
│    ↓ 扩展零改动（只认这 4 端点）                               │
│                                                              │
│  蒸馏 router（新增，对标 Browser-BC finalize）                 │
│    POST /api/distill → job_id（后台 asyncio.to_thread）       │
│    GET /api/distill/{id}  GET /api/jobs                      │
│    ↓ _PIPELINE_LOCK 串行化 + progress 注入 job dict           │
│                                                              │
│  配置 router（新增，控制面板用）                               │
│    GET/POST /api/config                                      │
│                                                              │
│  状态/产物 router（新增）                                      │
│    GET /api/status  GET /api/captures  GET /api/skills       │
│                                                              │
│  控制面板 SPA（app/dist/index.html，StaticFiles 托管）         │
└─────────────────────────────────────────────────────────────┘
         ▲                          ▲
         │ 4 端点（事件）             │ API（蒸馏/配置/状态）
         │                          │
   Chrome 扩展                    控制面板浏览器
   （popup 录制控制）              （全局运维台）
```

## 三、改动清单

### 1. 新增 FastAPI 依赖（pyproject.toml）
- 加 `fastapi` + `uvicorn[standard]`
- 保留 aiohttp（capture 命令仍用，serve 不用它）

### 2. 新增 `server/server.py`（FastAPI 常驻服务，核心新增）
- **采集 router**（迁移自 aiohttp CaptureBackend）：
  - `POST /start` / `POST /ingest` / `POST /stop` / `GET /health`（协议不变，扩展 popup 零改动）
  - 改为 FastAPI router + Pydantic model
- **蒸馏 router**（新增，对标 Browser-BC finalize 触发）：
  - `POST /api/distill` — 触发蒸馏，body: `{trace_path, output_dir?, adapter?, no_llm?}`，立即返回 `job_id`
  - `GET /api/distill/{job_id}` — 查蒸馏状态（running/done/failed + 进度）
  - `GET /api/jobs` — 列所有蒸馏任务
- **配置 router**（新增，控制面板用）：
  - `GET /api/config` — 读模型参数（调 `config.describe()`）
  - `POST /api/config` — 改模型参数
- **状态/产物 router**（新增）：
  - `GET /api/status` — 录制状态 + 后端健康
  - `GET /api/captures` / `GET /api/skills` — 列产物目录
- **控制面板 SPA 托管**：`app.mount("/", StaticFiles(directory="app/dist", html=True))`

### 3. 新增 `server/distill_api.py`（蒸馏任务管理）
- `run_distill_pipeline(trace_path, ...) -> DistillResult`（从 `__main__._run_distill` 提炼，去 CLI 味，返回 dataclass 不返回退出码）
- 全局 `_PIPELINE_LOCK`（asyncio.Lock，串行化蒸馏，防 LLM 配额/状态串）
- 内存 job dict（`job_id → {status, phase, current, total, detail, result}`）
- `progress.set_reporter` 注入（progress.py:22 已预留），蒸馏进度实时写 job dict
- handler 用 `asyncio.to_thread(run_distill_pipeline, ...)` 包同步 LLM 调用，不阻塞事件循环

### 4. 改 `treeforge/capture/collector.py`（session 可循环）
- `start()`：每次新建 `StageTracker` + 新建 `_session`（避免跨 session stage 状态串）
- `stop()`：导出后清空 `_session`（下次 start 干净启动）
- `_started` 标志保持（用于区分主路径/兜底）

### 5. 改 `treeforge/capture/cdp_session.py`（session 级连断）
- `stop()` 里清空 `_previous_selector_map`（避免新 session 新元素检测被旧缓存污染）
- 保持「每次 start 连 / stop 断」语义（现状已如此，确认即可）

### 6. 新增 `treeforge serve` 子命令（__main__.py）
- argparse 加 `serve` 子命令（参数：`--host` / `--port` / `--reload`）
- 起 `uvicorn.run(app, host, port)`，常驻不退出
- Ctrl+C 由 uvicorn 管（成熟，无 Windows bug）

### 7. 保留 `treeforge capture` 子命令（不动）
- 现有 capture（一次性 + aiohttp）保留，给无 UI 脚本/快速测试场景
- 不和 serve 冲突（端口可不同）

### 8. 不改的部分（协议契约不变）
- 扩展全部（popup/background/content/shared）—— 只认 4 个 HTTP 端点 + DEFAULT_ENDPOINT
- harness 蒸馏 6 stage（纯函数）
- distill_schema 双端契约
- CdpSession 的 start/get_state/stop 三方法签名

## 四、实施步骤（分 4 步，每步可独立验证）

| 步骤 | 内容 | 验证 |
|---|---|---|
| **S1** | pyproject 加 FastAPI 依赖 + `server/server.py` 骨架（FastAPI app + 采集 router 迁移 + Pydantic model） | `uv run treeforge serve` 起来，curl /health 通 |
| **S2** | collector/cdp_session session 可循环改造（start 重建 + stop 清空） | 单元测试：连续两次 start/stop，stage 不串 |
| **S3** | distill_api.py（提炼 run_distill_pipeline + _PIPELINE_LOCK + job dict + progress 注入）+ 蒸馏 router | curl POST /api/distill 触发蒸馏，轮询状态 |
| **S4** | 配置/状态/产物 router + serve 子命令接入 __main__.py + 控制面板 SPA 骨架（app/dist/index.html 占位） | serve 跑起来，浏览器访问控制面板 |

## 五、关键设计点

### 1. 采集和蒸馏共用服务
serve 一个进程，既收扩展事件（采集），又接受蒸馏触发（API）。
CdpSession 只在采集时连 CDP，蒸馏时不需要 CDP（蒸馏读已落盘的 trace）。

### 2. session 可循环（核心修复）
Collector 支持多次 start/stop：
- 每次 start 重建 session + StageTracker（避免跨 session stage 状态串）
- stop 导出后清空 `_session`，下次 start 干净启动
- 进程不退出，继续等下一次 /start

### 3. 蒸馏不阻塞
LLM 是同步 urllib（每次最多 180s），handler 不能直接 await（会卡死事件循环）：
- `asyncio.to_thread(run_distill_pipeline, ...)` 丢后台线程
- 立即返 `job_id`，前端轮询 `GET /api/distill/{job_id}`
- `_PIPELINE_LOCK` 串行化（同时只跑一个蒸馏，防 LLM 配额/状态串）

### 4. 控制面板分阶段
- 先做后端 API + 最小 SPA（录制状态 / 触发蒸馏），纯 HTML+fetch
- 后续迭代加参数配置表单、产物浏览树

## 六、风险与权衡

| 风险 | 应对 |
|---|---|
| aiohttp → FastAPI 迁移引入采集回归 | S1 迁移后跑现有 capture 测试（test_capture.py）确认协议不变 |
| 蒸馏后台任务状态管理复杂 | 内存 job dict + progress 注入，参考 Browser-BC _PIPELINE_LOCK 模式 |
| 控制面板 SPA 工作量 | S4 先做最小骨架（纯 HTML+fetch，不引框架），后续迭代 |
| serve 和 capture 端口冲突 | 默认都用 8765，文档说明不要同时跑；或 serve 默认换端口 |

## 七、不在本次范围

- 控制面板完整 UI（参数配置表单、产物浏览树）—— 后续迭代
- 多用户/多 Chrome 同时录制 —— 单用户单 Chrome（对齐 Browser-BC 定位）
- 蒸馏任务持久化（重启丢失 job 状态）—— 内存 dict 即可，P3+ 再做持久化

## 八、与现有方案的关系

- 本方案是 `docs/p2/README.md` 3.2 节（采集层）的延续——采集层的后端从「一次性命令」升级为「常驻服务」
- 吸收了 ROADMAP P3（FastAPI 接入层）的内容，提前到 P2 实施
- 采集层核心逻辑（CdpSession / Collector / distill_schema / export）不变，只是后端载体从 aiohttp CLI 命令换成 FastAPI 常驻服务
- 扩展协议不变（4 端点），扩展零改动
