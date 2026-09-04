# Changelog

本文件记录 TreeForge 所有值得注意的变更。格式参照 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

## [0.3.0] - 2026-09-05

P3.7（蒸馏注入消费端上下文）+ P4（站点级 + 任务级双产物，多任务累积蒸馏）。

### Added
- **P3.7 消费端上下文**：distill prompt 注入 TreeWalker agent 能力模型——动作词汇表（蒸馏相关 13 个，带真实签名与语义）+「Agent 已自动处理，别写进 skill」清单；quirks 决策启发式从一问（DOM 可见不写）扩为两问（+自动兜底不写）；动作词汇表镜像自 TreeWalker `ACTION_DEFINITIONS`，测试钉住关键动作名防漂移。
- **P4 站点级 + 任务级双产物**：同 host 多任务累积蒸馏，一次蒸馏同时产出站点级累积卡（功能地图 + 通用操作知识，≤8000 有界）+ 任务级独立卡（`tasks/<slug>/` 三件套 + `_task.json`）；蒸馏前可选输入任务描述（检索锚点 + 反哺蒸馏意图）。
- **P4 registry 重建**：`harness/registry.py` 重建为 SkillCard 按 host 持久化（原子写 + trace_sources 并集去重），版本真源挪 registry。
- **P4 host 级增量蒸馏接通**：`distill_host` 加 prev_card，`_INCREMENTAL_ADDENDUM` 三文件块（sop 8000 / selectors 3000 / quirks 4000），BUCKET 后懒加载旧卡 / DISTILL 后落卡。
- **P4 多任务工作流**：CLI distill 支持多 trace（`--fresh` / `--task`，stage 冲突重映射 stage@N）；serve 加 host 累积模式 + 任务描述输入 + host 节点「累积再蒸馏」按钮；任务卡 slug 稳定化（同任务重录复用 slug 覆盖）。
- **P4 配套工具**：`scripts/redistill_site.py`——站点级产物形态升级 / 旧卡损坏后按任务卡逐个重蒸重建（slug 复用 + 逐轮增量累积，首个成功轮 fresh）。

### Changed
- data/ 运行时产物（skills / captures / registry）纳入版本跟踪；CDP 默认端口 9222→9223。

### Fixed
- **P3.7 附带（真机蒸馏反馈发现）**：atomizer 重复点击合并误杀（空 selector 恒等吞掉不同按钮，改按 element_attrs 稳定标识判等）；collector 副作用信号因果归属（附到触发 action 而非 events[-1]）；cdp `attach_tab` url 兜底（tabId=None 环境按 host+path 匹配 target）。
- **P4 localhost 事故**：增量蒸馏 LLM 畸形 JSON → 静默模板兜底覆盖好卡——加 LLM+解析重试、重试耗尽保旧卡（版本不倒退）、registry 跳过模板兜底卡。
- 控制面板蒸馏结果双产物展示：任务级卡原本界面上不可见（易误以为没产出），改为站点级 + 任务级双行显示。

### Docs
- ROADMAP 收尾 P3.6/P3.7、新增 P4 章节；新增 docs/p3.7、docs/p4 实施方案。

## [0.2.0] - 2026-08-02

P3.5（控制面板体验闭环）+ P3.6（迁移 TreeWalker 扩展端事件词汇作 distill 采集补充）。

### Added
- **P3.6 事件词汇扩宽**：distill 采集加 `select_dropdown`（`<select>` change → value）、`upload_file`（`<input type=file>` change → 文件名 + upload_ctx 站点无关语义身份）、`send_keys`（修饰键组合 + 命名非打印键）；contenteditable 富文本用 MutationObserver 观察（替代 bilibili 专用启发式）。
- **P3.6 SPA 导航 hook**：新增 `injected.ts` MAIN-world 脚本（wrap `pushState`/`replaceState` → 派发 `tf:nav`；wrap `addEventListener` 给点击监听器打 `data-tw-jsclick` 标记），新增 `navigation-recorder.ts` 收 `tf:nav`/`popstate`/`hashchange`，补 distill 一直缺的 navigate 接线；`findInteractiveAncestor` 加第四道回退。
- **P3.6 副作用信号**：新增 `side-effect-observer.ts`（动作后 1s 窗口检测 modal/dropdown 新增），协议 4→5 端点加 `POST /signal`，`Collector.attach_signal` attach 到最近 capture event，`CapturedEvent.signals` 落进 trace.json，atomizer 渲染进 summary 行（`[signal=modal_opened]`）作 quirks.md 原料。
- **P3.6 双端 schema 扩展**：`DistillActionType` 加 3 新类型 + `UploadCtx` + `DistillSignal`/`SignalKind`；`RAW_ATTR_KEYS` + 白名单加 `data-tw-jsclick`/`accept`。
- **P3.5 配套 API**：`GET /api/captures/{name}`、`GET /api/skills/{host}/files`、`GET /api/skills/{host}/files/{filename}`、`GET /api/config/check`（LLM 自检）。
- **P3.5 控制面板**：蒸馏任务列表 + 进度条 + 产物浏览树 + capture 下拉 + toast + 配置编辑表单；采集产物显示创建时间（mtime_ms）+ 录制结束边缘检测自动刷新 + 手动刷新按钮。

### Changed
- input coalesce 1200→400ms 对齐 TreeWalker；`TraceEvent` 加 `signals` 字段（默认空 list，向后兼容）。

### Fixed
- **P3.5 产物时间显示错误（00:00/23:59）**：`st_mtime` 秒级浮点未乘 1000；改 `mtime_ms` + 回归断言。
- **P3.5 蒸馏完成后进度条不停止**：done 时 `total=0` 触发 indeterminate；改为 done/failed 不渲染进度条。

### Docs
- ROADMAP.md 加 P3.5/P3.6 章节 + 里程碑 + 历史调整；P4 检索层明确不做（删除）；README/ARCHITECTURE 同步。

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
