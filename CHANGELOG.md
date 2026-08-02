# Changelog

本文件记录 TreeForge 所有值得注意的变更。格式参照 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

P3.5 控制面板优化与配置增强（issue #1）。把 P3 最小 SPA 打磨成可日常使用的运维台。

### Added
- **配套 API**：`GET /api/captures/{name}`（capture 详情：host/events/stages/snapshots）、`GET /api/skills/{host}/files`（列 md 文件 + 大小）、`GET /api/skills/{host}/files/{filename}`（md 内容预览，路径越界防护）。
- **LLM 连通性自检**：`GET /api/config/check`（`asyncio.to_thread` 包 `call_llm_fast` 发最小请求，返 model/reply_len/usage；失败返 error 不 500）。
- **控制面板**：蒸馏任务列表视图（历史 job + 耗时 + 产物，每 4s 刷新）、进度条可视化（百分比/indeterminate 脉冲/done-failed 行高亮）、产物浏览树（captures/skills 下钻 + md 内联预览）、capture 下拉选 trace、顶部 toast 通知。
- **配置编辑表单**：白名单 4 key 可编辑（DISTILL_MODEL/CLASSIFY_MODEL/LLM_BASE/LLM_TIMEOUT）+ 分组展示 + LLM 自检按钮。
- **status 扩展**：录制中时 `/api/status` 返 session 的 session_id/events/stages/current_stage/host。
- **采集产物创建时间**：`/api/captures` 每个 item 含 `mtime_ms`（毫秒戳）+ `mtime_iso`，按新→旧排序；控制面板显示相对时间 + 绝对本地时间（如「16 小时前（08-01 18:10）」）。
- **录制结束自动刷新**：status 轮询做边缘检测，`recording` 从 true→false 时延迟 500ms 自动刷新采集产物 + 下拉；另加「↻ 刷新产物」手动按钮兜底。

### Changed
- `/api/status` 录制中时附带 session 详情（向后兼容：未录制时无 session 字段）。
- 蒸馏任务行 done/failed 时不渲染进度条（避免结束后 indeterminate 滚动条不停）；done 时展开列出产出的每个文件路径（对齐旧版行为）。

### Fixed
- **产物时间显示错误（00:00/23:59）**：`/api/captures` 的 mtime 误传秒级浮点（`st_mtime`），前端 `new Date(ms)` 按毫秒解析导致时刻错乱；改为乘 1000 转毫秒戳（字段重命名 `mtime_ms`），并加 `> 946684800000` 回归断言。
- **蒸馏完成后进度条不停止**：done 时 `total=0`（DISTILL 阶段无 total）触发 indeterminate 滚动条；改为 done/failed 不渲染进度条。

### 部分实现
- OUTPUT_DIR 可写：暂只读展示 + skills 卡显示当前 skills_dir（路径错丢产物风险，可写留后续）。

## [0.1.0] - 2026-08-01

首个发布版本。完成「人工示教 → 蒸馏 → skill 文件」核心闭环，并打通 MV3 扩展采集层与 FastAPI 常驻服务。

### Added
- **蒸馏五阶段管线**（`harness/`）：ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL，纯标准库 urllib LLM 客户端（双协议探测 Anthropic / OpenAI 兼容，不引 SDK）。
- **输出 adapter**（`adapters/`）：treewalker 多文件三件套（`_sop.md`/`selectors.md`/`quirks.md`）+ browserbc 单文件。
- **CLI**（`treeforge`）：`distill` / `capture` / `serve` / `info` 四个子命令。
- **skill 形态**：element_attrs（11 白名单属性）+ page_context（DOM 快照）+ stage 字段（阶段绑定），host 级合并蒸馏。
- **采集层**（`treeforge/capture/`）：CdpSession（轻量 CDP 包装，委托 dom-snapshot）+ Collector（实时采快照 + stage 判定，session 可循环）+ stage 命名语义化 + tab 跟随 + distill_schema 双端契约。
- **MV3 扩展**（`extension/`）：WXT + React + TypeScript，background/content/popup/shared/strategies，通用 CaptureEnvelope + scenario 路由，IndexedDB 持久化。
- **P3 常驻服务**（`server/`）：FastAPI app 工厂——采集 router（4 端点，扩展零改动）+ 蒸馏后台任务（job dict + `_PIPELINE_LOCK` + 进度注入）+ 配置/状态/产物 router + 控制面板 SPA。
- **示例**：bilibili-upload + github-login 两份 trace，183 个单元测试（mock，不连真 LLM/网络）。

### Fixed
- P2 端到端调试：URL/DOM host 提取、stage 切碎（DOM 相似度阈值 + 累积漂移）、CdpSession target 选择等 6 个 bug。
- input 事件去噪：连续同目标输入合并、contenteditable 语义标签解析、孤立修饰键过滤。
- `cdp_session.stop()` 清轮转缓存 `_previous_selector_map`，避免 serve 长期运行跨 session 污染新元素检测。

### Changed
- 蒸馏产物从四件套精简为三件套（删 `api.md`：无网络采集时恒零信息）。
- 蒸馏策略从 capacity 分桶改为 host 级合并（消除重复描述）。
- console script 由 `treewalker` 重命名为 `treeforge`。

### Docs
- ARCHITECTURE.md（四层分层 + 五阶段蒸馏）、ROADMAP.md（P0–P4 路线图 + A/B 验证结论）。
- 阶段设计文档归档：docs/p0、docs/p1、docs/p2、docs/p3。
- README 按代码现状重构；LICENSE 对齐 TreeWalker（CC BY-NC 4.0）。
