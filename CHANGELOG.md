# Changelog

本文件记录 TreeForge 所有值得注意的变更。格式参照 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [Semantic Versioning](https://semver.org/)。

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
