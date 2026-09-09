# TreeForge 路线图

> 从 init-plan 抽取，2026-07-18 按核心机制重新排优先级，2026-07-28 / 2026-08-01 按实际进展更新，
> 2026-08-23 对齐 TreeWalker 优化蓝图（路线二站点级 skill）新增 P4。
>
> **核心机制确认**：skill 是「给 LLM 看的上下文提示」，不是「给 CDP 直接执行的结构化 selector 库」。
> 这个定位决定了整个路线图——验证「skill 能不能让 agent 探索更准」是最高优先级，
> 比采集层（最重）和接入层（工程优化）都重要。

---

## 战略定位：闭环中的角色

TreeForge 在「人工 → 蒸馏 → agent → 重放」闭环中的位置：

```
人工探索（一次，慢但准）
    ↓ TreeForge 蒸馏
domain-skills/<host>/skill 文件（给 LLM 看的站点知识）
    ↓ 注入 TreeWalker agent 上下文
agent 自动探索（多次，快，有 skill 加持更准）   ← TreeForge 的价值落点
    ↓ agent 探索成功 → 自动录制
AgentHistory 回放文件（天然全对齐）
    ↓ load_and_rerun
重放（多次，零 LLM，最稳）
```

**TreeForge 的核心价值**：不改 agent 逻辑，给 agent 喂知识，提升探索准确率。
这比改 agent 本身的可靠性更省力，且效果可预测。详见知识库 `manual-vs-agent-recording.md`。

**精度约束**：skill 给 LLM 看 = 精度要求「LLM 能看懂」而非「CDP 能精确匹配」，
比 TreeWalker record-replay 的精度要求低一档（record-replay 要五级匹配，skill 只要语义可读）。
这让采集层比 record-replay 轻——但比 BrowserBC（去站点化）重。

---

## P0 —— 最小闭环（已完成 ✅）

**目标**：手写 trace JSON → 蒸馏 → 输出 skill 文件，最小可跑链路。

- [x] Python 项目脚手架（uv + ruff + pytest + Pydantic v2）
- [x] harness 五阶段管线骨架 + 实现
  - [x] ADAPT（adapter.py）：最小格式 + 多格式兼容 + 脱敏
  - [x] ATOMIZE（atomizer.py）：4 边界规则 + 去噪 + 合并/拆分
  - [x] CLASSIFY（classifier.py）：串行增量命名 + 启发式 fallback
  - [x] BUCKET（bucketer.py）：domain::capacity 归并
  - [x] DISTILL（distiller.py）：站点特定 prompt + 模板 fallback
- [x] LLM 客户端（llm.py）：urllib 双协议探测（Anthropic / OpenAI）
- [x] 输出 adapter（treewalker + browserbc）
- [x] CLI（`treeforge distill` / `treeforge info`）
- [x] 示例 trace（bilibili-upload + github-login）
- [x] 测试（adapter / atomizer / classifier / distiller / adapters / llm）
- [x] 文档（README / ARCHITECTURE / ROADMAP）
- [x] WXT 扩展脚手架（仅结构）

**验收命令**（形态经 P0.5 调整为三件套）：

```bash
uv sync --extra dev
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
ls ./data/skills/domain-skills/bilibili.com/
# → _sop.md / selectors.md / quirks.md（三件套，无 api.md）
```

模板模式（不调 LLM 也能跑）：`uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm`

---

## P0.5 —— skill 形态对齐与精简（已完成 ✅）

> P0 跑通后实测发现：蒸馏产物的 CSS selector 与 TreeWalker DOM（`[index]<tag attr=val /> text` 格式）
> 对不上（11 个 selector 0 命中），且 A/B 测试加载旧 skill 对 agent 无提升。两轮迭代修正了产物形态。
> 详见 [docs/skill-format-alignment-plan.md](./docs/skill-format-alignment-plan.md)（阶段 1-4）
> 和 [docs/skill-simplification-plan.md](./docs/skill-simplification-plan.md)。

**目标**：让蒸馏产物形态真正匹配「TreeWalker agent 的 LLM 实际读到的 DOM」。

### 阶段 1-3：skill 格式对齐（已完成 ✅）

- [x] **element_attrs 字段**（阶段 2）：TraceEvent 加 `element_attrs: dict`，用 11 个白名单属性
  （id/name/type/placeholder/aria-label/role/data-testid/data-test/data-cy/contenteditable/visible text）
  替代 CSS selector，对齐 TreeWalker DOM 呈现格式
- [x] **page_context 字段**（阶段 3）：Trace 加 `page_context: dict[str,str]`（阶段名→DOM 文本快照），
  让 distiller 推出跨阶段 quirks（如标题框某阶段缺失）。来源：TreeWalker `get_state().dom_state.element_tree_text`
- [x] **distiller prompt 改元素描述表**（阶段 1）：selectors_md 从 CSS selector 改为
  4 列元素描述表（元素用途 / 怎么找到它 / 稳定标识 / 备注），硬约束禁 CSS selector
- [x] **rerun_to_trace.py 工具**：把 TreeWalker rerun-history JSON 自动转成 treeforge trace
  （含 element_attrs + page_context + xpath 兜底），替代手工标注，95% 自动化
- [x] **多 bucket 合并**：`write_skills_merged` 把同 host 多 capacity bucket 合并成一组文件

### 阶段 4：event.stage 字段（已完成 ✅）

- [x] **stage 字段**：TraceEvent 加 `stage: str | None`，指向 page_context 的 key
  （None=无快照；带 `?` 后缀=启发式推断）
- [x] **启发式 stage 推断**：URL 严格匹配 / 元素指纹 / 时序外推（带 `?` 标记）三条规则
- [x] **evidence_block 加 Stages 行**：让 LLM 看到每个 segment 涉及的页面阶段

### skill 精简重构（已完成 ✅）

> 基于 A/B 测试「加载旧 skill 无提升」的诊断，对产物形态作一次重构。

- [x] **host 级蒸馏**：DISTILL 阶段按 host 合并（`distill_host`），一次 LLM 调用看整条流程，
  消除 capacity 割裂（B 站投稿不再被切成 upload-video + fill-video-metadata 两份重复描述）。
  capacity 降级为 prompt 子能力分组提示，CLASSIFY/BUCKET 逻辑不动
- [x] **quirks 判定标准量化**：prompt 明确「DOM 看得见的不写，只写隐藏依赖/同名区分/时序依赖/
  SPA 阶段切换/反直觉行为」，附 WRITE/Do NOT WRITE 对照表。实测 LLM 严格遵守，
  quirks 从 9 条含噪声降到 5 条真坑
- [x] **三件套**：删 api.md（无网络采集时恒为「未观察到私有 API」零信息），
  selectors 降级为附录（多数元素在 _sop 就地描述）

**验收**：73 测试全过，ruff clean，真 LLM 端到端产出三件套质量显著提升
（quirks 全部是 DOM 看不出来的真坑，无「span 不是 button」这类可见事实噪声）。

---

## P1 —— ★核心验证：skill 能否提升 agent 探索准确率（已完成 ✅，原 P3 提前）

> **路线图最大调整**：原 P3（质量验证）提前到 P1。这是整个闭环成立与否的判据。
> 在投入 P2 采集层（最重）之前，先用最小代价回答「skill 注入 agent 上下文后，探索准确率有没有提升」。
> 如果这个假设不成立，整个闭环断了，P2 的重投入不值得。
>
> **结论：核心假设成立**——蒸馏精简版 skill 达到手写精简版水平（均 100% 成功、零异常），
> 证明「TreeForge 自动蒸馏产出可用 skill」可行。详见 TreeWalker `docs/skill/` 下六份 A/B 报告。

**目标**：手写 skill + 改 TreeWalker agent 注入 → 实测对比「有 skill vs 无 skill」的探索成功率。

### 已完成

- [x] **手写参考 skill**：`TreeWalker/domain-skills/member.bilibili.com/`（在另一个工作空间手工整理，质量高于早期蒸馏产物）
- [x] **TreeWalker agent 注入机制**：已落地（按 host 读 `domain-skills/<host>/*.md`）
- [x] **首轮 A/B 实测**（07-27/07-28）：加载旧形态 skill（四件套 + capacity 分节）——**结果：无提升**，
  且暴露 host 不匹配陷阱（skill 目录建在 `www.bilibili.com`，实际访问 `member.bilibili.com`）
- [x] **诊断 + 修正**（即 P0.5 的 skill 精简重构）：旧形态三问题（capacity 割裂 / quirks 噪声 / api.md 零信息）已修正
- [x] **A/B 复测**（四版 skill × N=5 横向对比）：**蒸馏精简版 100% 成功、零异常，达手写精简版水平**

### A/B 测试结论（核心判据达成）

四版 skill × N=5 横向对比（B 站投稿任务，智谱 LLM）：

| skill 版本 | 来源 | 字符数 | 成功率 | 平均步数 | 异常轮 |
|---|---|---|---|---|---|
| 原始手写版 | 人工 | 7013 | 80% (4/5) | 27.0 | t#3 失败 |
| 原始蒸馏版 | TreeForge | 5052 | 80% (4/5) | 20.4 | t#1 失败 + t#2 异常慢(30min) |
| 手写精简版 | 人工精简 | 1793 | **100% (5/5)** | 22.0 | **零异常** |
| **蒸馏精简版** | **TreeForge** | **2936** | **100% (5/5)** | **23.2** | **零异常** |

**三个核心结论**：

1. **精简系统性更稳**：两个精简版都 100%（10 轮全成功），两个原始版都 80%（各有失败/异常）→
   验证 P0.5 精简重构方向正确（删冗余 DOM 抄录、只留 DOM 看不出的指导）
2. **TreeForge 蒸馏产出可用**：蒸馏精简版 = 手写精简版效果（均 100% + 零异常）→
   「自动蒸馏产出可用 skill，不依赖人工手写」的核心假设成立
3. **P0.5 决策全部命中**：删 api.md（三件套）、只留 DOM 看不出的指导、精简原则——
   均被实测验证为正确方向

**评估方法论修正（重要）**：

- ✅ **成功率是可靠指标**（能区分 skill 质量，手写/精简 treatment 稳定 > baseline）
- ❌ **步数是噪声**（蒸馏版 N=3 +37% vs N=5 −24% 方向相反，被 LLM 随机性主导）
- ❌ **单次 A/B（尤其 N=3）不可信**，要同 N 横向对比 + N≥5

**局限**：N=5 小样本，100% vs 80% 仅 1 轮差异；精简版没跑 baseline 对照（无法算 pp），
但「10 轮精简全成功 vs 10 轮原始各有失败」模式比单次可信。要强结论需 N≥10 + baseline 对照。

### 对后续阶段的意义

- **P0.5 精简重构完全验证** ✅：方向正确，实测效果最好
- **P1 核心判据达成** ✅：蒸馏能产出可用 skill，闭环成立
- **P2 采集层值得投入** ✅：核心风险解除，可投入最重的采集层

---

## P2 —— 采集层（精度对准「LLM 可读」，已完成 ✅）

**目标**：MV3 扩展录制真实浏览器操作，产出可蒸馏的 trace。

**精度取向调整**（基于 P0 确认的「给 LLM 看」定位）：
- selector 记录到「LLM 能理解是哪个元素」即可，不要求 CDP 精确匹配
- xpath 记录作辅助线索，不要求与 CDP 树完全一致（skill 给 LLM 看不参与五级匹配）
- file upload / modal 怪癖要记（quirks.md 的原料），但容忍单次录制噪声（蒸馏会过滤）
- **element_attrs（11 白名单属性）+ page_context（DOM 快照）**：P0.5 已确定这两组字段是
  蒸馏产物的关键输入，采集层必须产出它们（不再只产 CSS selector）

**可直接借鉴 TreeWalker record-replay 采集端**（已踩过坑的成熟实现）：
- `recording_extension/capture/action-recorder.ts`（事件采集 + 去噪）
- `recording_extension/capture/selector.ts`（buildElementRef / bestSelector / xpathFor）
- `recording_extension/capture/navigation-recorder.ts`（SPA 导航 hook）
- `findInteractiveAncestor` 的 cursor:pointer + onclick 检测（对齐后端 is_interactive）

**比 record-replay 可简化**：
- 不需要实时后端算指纹（蒸馏不在乎时序）
- 不需要 D1/semantic_clue 兜底（蒸馏容错）
- 不需要 xpath 与 CDP 对齐（skill 给 LLM 看不参与匹配）

### 已完成

- [x] WXT 扩展真实实现（`extension/`：WXT + React + TypeScript，MV3）
  - [x] background SW + recorder 状态机（MV3 SW 回收恢复，借鉴 BrowserBC）
  - [x] content script：DOM 事件采集 + 实时去噪（input 合并 / 孤立修饰键 / 重复点击 / 同目标连续输入）
  - [x] injected：history.pushState/replaceState monkey-patch（SPA 导航）
  - [x] popup：录制控制 UI
- [x] **element_attrs 采集**：白名单属性提取（id/name/type/placeholder/aria-label/aria-labelledby/role 等）
- [x] **page_context 采集**：阶段性 DOM 快照（`element_tree_text` 格式，对齐 P0.5 已验证的输入）
- [x] selector 多级 fallback（`data-testid` → `aria-label` → `name` → `[role]` → xpath）
- [x] Dexie (IndexedDB) 存储（MV3 SW 回收不丢事件，借鉴 BrowserBC）
- [x] **采集后端**（`treeforge/capture/`）：
  - [x] CdpSession：轻量 CDP 包装，委托 dom-snapshot.build_dom_state 采快照
  - [x] Collector：收扩展事件 → 实时采快照 → 判 stage（实时采集原则：趁 DOM 活的）
  - [x] StageTracker：URL/DOM Jaccard 相似度（0.33 阈值）+ 导航信号 + 语义化命名（DOM 特征检测）
  - [x] tab 跟随：envelope 带 tab_id → CdpSession.attach_tab 精确重 attach
  - [x] distill_schema 双端契约（Python ↔ TS）：RAW_ATTR_KEYS / ELEMENT_ATTR_WHITELIST
  - [x] export：trace.json + snapshots/ 双文件，原子写（os.replace）
  - [x] session 可循环（start 重建 StageTracker + stop 清空，支持连续多次录制）
- [x] 通用 CaptureEnvelope + scenario 路由（distill/replay），4 端点协议对齐扩展

**P2 端到端调试**（6 bug 修复，见 `docs/p2/debug-retrospective.md`）：URL/DOM host 提取、
stage 切碎（阈值 + 累积漂移）、CdpSession target 选择、input 去噪、contenteditable 语义等。

---


## P3 —— 接入层（已完成 ✅）

**目标**：扩展 → server → 蒸馏全自动，人录一遍就出 skill。

> 接入层是工程优化（常驻服务、蒸馏可 HTTP 触发、进度轮询）。P1（验证）+ P2（采集）用 CLI
> 跑通蒸馏链路后，P3 把「一次性 capture 命令」升级为 FastAPI 常驻服务。详见 `docs/p3/serve-plan.md`。

### 已完成

- [x] FastAPI 常驻服务（`server/server.py`：`create_app()` app 工厂）
  - [x] 采集 router：`POST /start` `/ingest` `/stop` + `GET /health`（协议与 aiohttp backend 逐字一致，扩展零改动）
  - [x] 蒸馏 router：`POST /api/distill`（后台任务）+ `GET /api/distill/{id}` + `GET /api/jobs`
  - [x] 配置 router：`GET/POST /api/config`（白名单 key 原子写 .env）
  - [x] 状态/产物 router：`GET /api/status` `/api/captures` `/api/skills`
  - [x] 控制面板 SPA 托管（StaticFiles，目录不存在优雅跳过）
- [x] 蒸馏后台任务（`server/distill_api.py`）
  - [x] 提炼 `run_distill_pipeline`（去 CLI 味，返 DistillResult dataclass）
  - [x] 异步蒸馏（全局 `_PIPELINE_LOCK` 串行化 + `asyncio.to_thread` 包同步 LLM 不阻塞事件循环）
  - [x] 进度轮询（内存 job dict + `harness.progress.set_reporter` 注入）
- [x] `treeforge serve` 子命令（uvicorn 阻塞跑，Ctrl+C 由 uvicorn 管，无 Windows bug）
- [x] Chrome 缺席策略：照常启动（蒸馏/配置/状态可用），`/start` 时 Chrome 没连返 503
- [x] session 可循环：`/stop` 不退出进程，下次 `/start` 重建 session（serve 常驻）
- [x] cdp_session.stop() 清轮转缓存（跨 session 不污染新元素检测）
- [x] 控制面板 SPA（`server/app/dist/index.html`：纯 HTML+fetch，状态卡 / 触发蒸馏表单 / 配置展示）
- [x] `_run_distill` 提炼为 CLI 薄包装（与 HTTP 后台任务共用 `run_distill_pipeline`）
- [x] 端到端冒烟：起 serve → curl /health → POST /api/distill → 轮询到 done，三件套产出

**P3 决策**（已确认）：① 新增 `serve`，保留 `capture`（一次性命令给无 UI 脚本场景）② 换 FastAPI
③ P3 实施（不提前到 P2）。验证：183 测试全过（含 26 个 serve 测试），ruff clean。

### 延后（P3 未做，列入 P3.5 / 后续）

以下原计划项 P3 未实现，挪到后续阶段：
- [ ] 接入层 Windows 适配（msvcrt 文件锁 / `_ResilientStream` / 双写日志）
- [ ] 完整 redact（CVV / OTP / account token 正则，对齐 Browser-BC）
- [x] distiller 增量蒸馏真接通（registry 持久化旧 SkillCard，8000 字符截断塞 prompt）→ **移入 P4**（S1/S2）
- [x] host 级增量蒸馏（P0.5 host 级蒸馏后，增量逻辑改为 host 级）→ **移入 P4**（S2）

> **注**：原 P3 设想的「分块上传协议（init/finalize/status）+ sha256 可恢复上传」最终未采用——
> serve 走的是直接 POST CaptureEnvelope（采集）+ HTTP 触发蒸馏（读已落盘 trace），不需要分块上传。
> 这条从路线图移除（采集层扩展直连后端 POST 事件，无大文件上传场景）。

---

## P3.5 —— 控制面板优化与配置增强（已完成 ✅）

**目标**：把 P3 落地的最小 SPA（纯 HTML+fetch 骨架）打磨成可日常使用的运维台，并补齐配置管理。

> P3 只做了最小可用 SPA（录制状态卡 / 触发蒸馏表单 / 配置展示只读）。P3.5 聚焦「能真正
> 替代 CLI 日常使用」的体验闭环，不引入前端框架（保持纯 HTML+fetch 哲学，与 P3 一致）。

### 已完成

- [x] **控制面板细节优化**
  - [x] 蒸馏任务列表视图（历史 job + 状态/耗时/产物，替代单纯轮询单个 job；耗时从 started_at/finished_at 算，每 4s 刷新）
  - [x] 蒸馏进度条可视化（current/total 渲染百分比条；total=0 时 indeterminate 脉冲动画；done/failed 行高亮边框）
  - [x] 产物浏览树（captures/ 与 skills/ 下钻：点 capture → 详情 + 「用此 trace 蒸馏」；点 host → md 文件列表 → 点文件内联预览）
  - [x] 录制状态实时刷新（3s 轮询，加 session_id / 已采事件数 / 当前 stage / host 展示，录制中状态卡高亮）
  - [x] 触发蒸馏时支持「从最近一次 capture 选 trace」（下拉 + 浏览时按钮两种入口）
  - [x] 错误/通知反馈（顶部 toast，success/error/info，3.5s 消失，替换所有 alert）
  - [x] 响应式与暗色模式打磨（窄屏 flex-wrap、产物树展开不撑破布局、暗色 badge 配色完善）
- [x] **配置功能增强**
  - [x] 配置编辑表单（白名单 4 key 可编辑：DISTILL_MODEL/CLASSIFY_MODEL/LLM_BASE/LLM_TIMEOUT，前端校验 LLM_TIMEOUT 正整数）
  - [x] LLM 连通性自检（新增 `GET /api/config/check`：`asyncio.to_thread` 包 `call_llm_fast` 发最小请求，真环境验证通过返 model/reply_len/usage；失败返 error 不 500）
  - [x] 配置项分组（模型参数可编辑 / 只读项分组展示，对齐 `config.describe()` 字段）
  - [~] 配置变更后蒸馏产物的输出目录可视化——**部分实现**：output_dir 只读展示 + skills 卡顶部显示当前 skills_dir；OUTPUT_DIR 暂不加白名单（路径错丢产物风险，留后续）
- [x] **配套 API 补充**
  - [x] `GET /api/captures/{name}`：单个 capture 详情（host/task/events/stages/snapshots，读 trace.json）
  - [x] `GET /api/skills/{host}/files` + `GET /api/skills/{host}/files/{filename}`：列 md 文件（名+大小）+ 内容预览（路径越界防护 `[^/\\]+\.md`）
  - [x] `GET /api/status` 扩展：录制中时返 session 的 session_id / events / stages / current_stage / host

**验收**：浏览器访问控制面板能完成「录一段 → 看状态 → 选 trace → 触发蒸馏 → 看进度 → 浏览产物」
全流程，无需回到命令行；配置改动有表单 + 自检反馈。真环境冒烟通过（含 `/api/config/check` 真调 LLM 返 usage）。194 测试全过（+11 个 P3.5 测试），ruff clean。

---

## P3.6 —— 录制功能扩展（迁移自 TreeWalker，作为现有采集的补充，已完成 ✅ v0.2.0）

**目标**：把 TreeWalker 扩展端的 DOM 事件采集能力作为「补充」迁入 TreeForge，扩宽 distill
采集的事件词汇（select / upload / send_keys / SPA 导航 / modal·dropdown 副作用），让一些
「现有 distill 策略采不全」的简单操作也能录。**不动**现有 distill 采集主链路，**不迁**
TreeWalker replay 专有的指纹 / 定位 / 重放格式重机器。

> 由来：P3.5 收尾后，TreeForge 主链路已完成。但实测 distill 采集的事件词汇偏窄
> （click / input 仅 Enter 的 keydown / scroll；navigate 与 change 声明了没接线），一些操作
> （下拉选择 / 文件上传 / 快捷键 / SPA 内跳转 / 弹窗副作用）采不到，影响 quirks.md 原料
> 完整度。TreeWalker 同源的扩展端正好覆盖这些场景，作「补充」迁入——同团队、同采集哲学
> （扩展采 DOM 事件 + 后端采 CDP 快照），但服务不同产物（trace.json 喂蒸馏 vs
> AgentHistoryList 喂重放）。

### 定位与复用边界（避免两份相似代码）

迁移只取 TreeForge 缺的四件能力（事件词汇广度 / SPA 导航 hook / JS 点击标记 / 副作用信号），
**复用** TreeForge 已有的策略模式 / Envelope / Collector / CdpSession / export 全链路。
精度约束不变——仍对准「LLM 可读」，不追求 CDP 精确匹配，所以 TreeWalker 的指纹 / 定位那套
搬过来反而过度工程。

| TreeWalker 文件 | TreeForge 现状 | 处置 |
|---|---|---|
| `capture/action-recorder.ts` | `core/recorder-engine.ts` + `strategies/distill/` | **不迁整体**——在 distill 策略内补 click/input 之外的词汇 |
| `capture/navigation-recorder.ts` | 缺（navigate 接线缺口） | **迁**——补 SPA nav 监听（distill 一直缺这块） |
| `entrypoints/injected.ts` | 不存在 | **迁**——MAIN-world hook（pushState 拦截 + addEventListener 给 JS 点击监听打标） |
| `capture/side-effect-observer.ts` | 不存在 | **迁**——modal/dropdown MutationObserver 信号（quirks.md 原料） |
| `capture/selector.ts` | 已有更简单的白名单 attrs | **不迁**——现有已满足「LLM 可读」精度 |
| `shared/types.ts` RecorderEvent | `shared/distill-schema.ts` DistillEventPayload | **不另建**——在 distill-schema 内扩 DistillActionType 联合类型 |
| `shared/backend.ts` postSignal | 4 端点 | **扩展**——加 postSignal（4 端点 → 5 端点） |
| `recorder/*` 后端全套 | `treeforge/capture/` 已有轻量版 | **不迁**——replay 重机器，与「给 LLM 看」定位冲突 |

### 计划项

- [x] **S1 扩展端事件词汇补齐**（distill 策略内扩展，不动 RecorderEngine）
  - [x] `select_dropdown`：`change` on `<select>` → emit value
  - [x] `upload_file`：`change` on `<input type=file>` → emit accept + upload_ctx
    （label_text / aria-labelledby / region_text / in_dialog 语义身份）
  - [x] `send_keys`：Ctrl/Alt/Meta 组合键 + 命名非打印键（Enter/Tab/Esc/方向键/F1-12）；
    纯字符仍走 input
  - [x] `input` 对齐 TreeWalker 400ms coalesce（final value only）+ IME-aware
  - [x] contentEditable：MutationObserver 读 innerText（替代当前 bilibili 专用启发式）
- [x] **S2 SPA 导航 hook + JS 点击标记**（补 distill 的 navigate 接线缺口）
  - [x] `injected.ts` MAIN-world 脚本（web_accessible_resources）：wrap
    `history.pushState`/`replaceState` → 派发 `tf:nav` CustomEvent
  - [x] content script 监听 `tf:nav` + `popstate` + `hashchange` → emit navigate
  - [x] wrap `EventTarget.prototype.addEventListener` 给 click/mousedown/pointerdown 监听元素
    打 `data-tw-jsclick` 标记；`findInteractiveAncestor` 加该属性一级回退
- [x] **S3 副作用信号**（modal/dropdown MutationObserver）
  - [x] `side-effect-observer.ts`：每动作后 1s 窗口 MutationObserver 检测
    MODAL/DROPDOWN_SELECTOR 新增节点 → emit `modal_opened`/`dropdown_opened`
  - [x] 扩展协议加 `POST /signal`（4 端点 → 5 端点），background 转发
  - [x] Collector.attach_signal：信号 attach 到最近 2s 内的 capture event
- [x] **S4 双端 schema 扩展 + 蒸馏层适配**
  - [x] `distill_schema.py` / `distill-schema.ts`：DistillActionType 加
    `select_dropdown`/`upload_file`/`send_keys`；RAW_ATTR_KEYS 加 `data-tw-jsclick`、
    select 选项文本、upload 的 `accept`
  - [x] `payload_to_trace_fields` 加新事件分支；trace.json 增选填 `signals` 字段
  - [x] harness ADAPT 适配新事件类型；atomizer 把 signal 渲染进 summary 行
- [x] **测试**：扩展端 + 后端 schema/collector 测试（mock，不连真 Chrome / 真 LLM），216 全过（+21 新）

### 明确不迁（避免重复 / 定位不符）

- ❌ TreeWalker `BrowserSession`（3818 行 CDP action 执行器）—— TreeForge 已有轻量 `CdpSession`
  （只读不执行），定位相反
- ❌ `compute_stable_hash` 指纹计算 —— 服务 replay 五级匹配，distill 只要 LLM 可读
- ❌ `locate_by_ref` 四级定位（TEXT/XPATH/ATTR/RECT）—— 实时定位给 replay，distill 采集时快照
- ❌ `flatten` → AgentHistoryList —— replay 重放格式，distill 走 trace.json
- ❌ React popup —— TreeForge popup 已是纯 JS（避免重复前端栈）
- ❌ `_semantic_clue` 兜底 / `user_pause_seconds` —— replay 专用语义

### 验收

- 现有 distill 采集链路行为不变（回归测试全过，P0-P3.5 的 194 测试不破）
- 新增词汇在真实站点能采到（select / upload / send_keys / SPA 跳转 / 弹窗），蒸馏产物
  quirks.md 出现对应条目
- 扩展端不出现两份相似代码（RecorderEngine / Envelope / popup / background 单一来源）
- 后端 `/signal` 端点与 `/ingest` 协议风格一致（Pydantic 模型 + 同样的 ok/error 返包）

---

## P3.7 —— 蒸馏注入消费端上下文（TreeWalker agent 能力模型，已完成 ✅ 代码 + 真 LLM 验证；A/B 测待做）

**目标**：让蒸馏 LLM 看到消费端（TreeWalker agent）的能力边界，产出更对口的 skill——
sop 动作动词落到真实 tool 名，quirks 只写 agent 真推断不出来的坑（不浪费 token 教 agent
已经自动做的事）。

> 由来：TreeForge 蒸馏的 skill 是 TreeWalker 专用、不需通用。但现状 distill prompt 只告诉 LLM
> 「消费端是读 `[index]<tag attr=val /> text` DOM 的 agent」（**感知**模型），完全没说这个
> agent **能做什么 / 已自动做什么**（**能力**模型）。证据：整个 prompt 里 "tool" 出现 0 次，
> 唯一动作名 `upload_file` 埋在一个 quirks 示例里；蒸馏版 skill 因漏了「upload_file 直注」
> 这条最值钱的 quirk，A/B 测比手写差 38%。手写的高质量 skill 全都直接引用真实动作名
> （`upload_file(index, path)` / `select_dropdown(index, value)`）。

### 核心洞察：两层都要给，且「别写」比「能做」更值钱

1. **动作词汇表**（让 sop 动词落到真实 tool 名）：click / input_text / select_dropdown /
   upload_file / send_keys / scroll / navigate / go_back / wait… 带语义（如「`upload_file(index, path)`
   — 隐藏 file input 必须用这个，click 会开 OS 对话框 agent 驱动不了」）。
2. **「Agent 已自动处理，别写进 skill」清单**（比动作表更值钱——避免 LLM 写废话/误导）：
   JS 点击 / 元素遮挡自动兜底（`_js_click_fallback`）、下拉点击自动降级、多 file input
   已带 `class`/`accept`、index===backend_node_id（无运行时模糊匹配）。
   这层与现有「If the agent reads the DOM text, can it figure this out itself? If yes,
   do NOT write it.」启发式一脉相承，但把它从「DOM 能看出来的」扩到「agent 自动处理的」。

### 计划项

- [x] **S1 消费端上下文段**（distill prompt 注入，纯 prompt 改动）
  - [x] 新增 `_CONSUMER_CONTEXT` 常量：TreeWalker agent 动作词汇表（蒸馏相关 13 个，
    按类别分组带语义）+ 「已自动处理，别写」清单
  - [x] 注入 `_DISTILL_PROMPT_TEMPLATE`（紧跟「消费端是 LLM agent」描述后）
  - [x] quirks WRITE/Do-NOT-WRITE 规则对齐：把「action-method requirements」从孤立示例
    提升为有动作词汇支撑的规则；Do-NOT-WRITE 加「agent 自动处理的（JS 点击/遮挡/下拉降级）」；
    决策启发式从一问（DOM 可见）扩成两问（+ 自动兜底）
- [x] **S2 镜像维护策略**（不耦合两个项目）
  - [x] 动作词汇表放 TreeForge 仓库（不 import TreeWalker 源码）
  - [x] 文件头注释标明「镜像自 TreeWalker ACTION_DEFINITIONS，需手动同步」+ 来源文件路径
- [x] **S3 测试**（4 个新测试，含防镜像漂移）
  - [x] prompt 契约测试（含消费端上下文段 + 关键动作名 + 自动处理清单）
  - [x] captured-prompt 测试（mock LLM 验证 user message 含消费端上下文）
  - [x] 镜像漂移测试（13 个动作签名 + select_dropdown「不要先点击」/upload_file「不要先点
    input」/send_keys 组合键格式 + custom dropdown 能力声明）
- [ ] **S4 真环境验证**（手动说明，不入自动化）
  - [x] 真 LLM 重跑蒸馏验证（douyin trace 实测：quirks 不再错误否定 `select_dropdown`、
    动作名落到真实 tool 名；两轮镜像修正——补 `dropdown_options` + 补「custom dropdown
    能力声明」，均由真机蒸馏反馈发现）
  - [ ] A/B 测 agent 成功率（TreeWalker 侧，参考 `docs/skill/` 已有 A/B 方法论）

### 明确不做（边界）

- ❌ TreeForge 运行时不依赖 TreeWalker（两项目独立发布，版本不同步）
- ❌ 搬 TreeWalker DOM 序列化 / 匹配逻辑过来（replay 的事，P3.6 已确认不搬）
- ❌ 改 TreeWalker 那一端任何东西（skill 注入逻辑不动）
- ❌ 期望模板模式（--no-llm）受益——无 LLM，注入再多也没用；此改进主要服务真 LLM 蒸馏

### 验收

- 现有蒸馏链路行为不变（回归测试全过，216 测试不破）
- 真 LLM 蒸馏出的 quirks.md 相比改动前，多出动作方法类条目（upload_file 直注 / select_dropdown
  语义等），且不出现「用 JS 点击」「先点下拉」这类 agent 自动处理的废话
- prompt 注入是纯字符串拼接，无新增依赖、无运行时跨项目调用

---

## P4 —— 站点级 skill（多任务累积蒸馏，已完成 ✅ 代码；S3 形态 2026-08-30 按 Browser-BC 修订）

**目标**：把「一个任务录一遍 → 蒸馏任务 SOP」升级为「同一站点多任务累积蒸馏 → 站点级知识
（布局 / 菜单 / 功能地图 / 站点通用知识，有界摘要）」，消 TreeWalker agent 的导航不确定性（「路盲」）。
一次蒸馏同时产出**站点级累积卡**与**任务级独立卡**（双产物，用户操作一次）；蒸馏前可选
输入任务描述——作 TreeWalker 侧未来语义检索的锚点，同时反哺蒸馏意图（2026-08-28 融入）。

> 依据：TreeWalker 优化蓝图路线二
> （`D:\dev\git\z_jordon\evals\webarena\docs\treewalker-optimization-blueprint.md`）。
> 88 个失败中约一到两成卡在「路盲」（找不到入口反复摸索），27% 翻转不稳定的一半来自
> 导航路径随机性。skill 形态 = 布局/菜单/功能地图/典型操作序列，browser-bc 式注入。
> 预期收益 10-20pp，天花板明确——skill 只解决「路盲」，解决不了路线一的墙
> （网格渲染/表单交互/dialog 是 TreeWalker 工具层的事，「地图告诉你表格在哪，
> 表格照样渲染不出行」）。评测口径：TreeWalker 侧标注 "with site knowledge" 变体分列
> 报告，不进无 skill 主口径。

### 现状：任务级 skill（蓝图路线三的蒸馏侧）已就绪 ✅

| 蓝图路线三概念 | TreeForge 现状 |
|---|---|
| 「每个常见任务录一遍人类轨迹」 | ✅ 扩展录制 + serve 常驻（P2/P3，5 端点含 /signal） |
| 「各蒸馏一个 skill」 | ✅ 五阶段管线 + host 级蒸馏 + 三件套（P0-P0.5，216→234 测试） |
| 「注入上下文」 | ✅ TreeWalker 文件注入 `domain-skills/<host>/`（P1 A/B 验证闭环成立） |
| 「检索命中即走流程」 | ❌ 明确不做（检索层 P4 编号已废弃删除；误命中比未命中更糟，按 host 文件注入已够） |

任务级 skill 按蓝图定位是**产品口径**（企业重复流程 RPA 化），不是评测手段——其 SR
禁止与评测口径对比（等价泄露参考轨迹，作弊红线）。TreeForge 只负责蒸馏侧，已就绪。

### 差距：站点级要补的三件事

1. **跨 trace 累积**：同一 host 多次录制目前各自独立蒸馏、产物互相覆盖。增量蒸馏未接通——
   `distill_version` 有计数但 `prev_sop` 是占位（"previous skill not available in P0"），
   `harness/registry.py` 是 P0 空实现（load/save 骨架，原检索层遗留）。
2. **产物形态**：三件套是**单任务流程叙事**导向；站点级要「功能地图 + 按 capacity 分组的
   典型操作序列」——让 agent 拿到「这个站点长什么样、入口在哪」，而非「这一条流程怎么走」。
3. **工作流**：需要「按 host 累积」的蒸馏入口（多 trace → 同一 host 目录合并演进，
   而非每次覆盖）。

### 参照 Browser-BC（多 demo 归并机器，TreeForge 已继承）

Browser-BC 的五阶段管线（atomize → classify 到 capacity → bucket 归并 → distill）本质就是
「多次操作累积成知识」的机器——TreeForge 已继承并在单 trace 内用了 host 级合并；P4 把
它用到**跨 trace** 维度。与 Browser-BC 的既有分叉点保持不变：

| 维度 | Browser-BC | TreeForge P4 |
|---|---|---|
| 知识取向 | 去站点化（"abstract away site-specific"） | **站点特定**（"capture site-specific"，反向） |
| 消费方式 | MCP stdio 检索（两层召回） | **文件注入** `domain-skills/<host>/`（零运行时依赖） |
| 归并单位 | domain::capacity bucket | **host**（跨 trace 累积演进） |

### 计划项（细化落 docs/p4/）

- [x] **S1 SkillCard 持久化**：按 host 落盘蒸馏产物（重建 `harness/registry.py` 为
  host → 卡片索引，旧卡可读回；原子写对齐 install.py）
- [x] **S2 host 级增量蒸馏**：新 trace 与旧卡合并（`_INCREMENTAL_ADDENDUM` 真接 prev_sop，
  8000 字符截断塞 prompt）；冲突以新证据为准、仍有效的旧知识保真保留
- [x] **S3 站点级产物形态**（2026-08-30 按 Browser-BC 修订）：`_sop.md` = 有界站点摘要
  ——「站点功能地图」+「站点通用操作知识」（≤8000，压缩措辞不丢主题）；逐任务序列归
  任务卡；capacities 降级为信息性清单并与旧卡并集（**不新增文件**：loader 固定三件名）
- [x] **S4 任务级双产物**：管线蒸馏段拆两跳（host 增量 + task 独立），任务卡落
  `domain-skills/<host>/tasks/<slug>/`（三件套 + `_task.json`：任务描述 / 关键词 /
  来源 trace）；**slug 稳定化**——prompt 注入现有任务卡清单，同任务重录（不同时段/
  描述措辞不同）复用 slug **覆盖旧卡**，确为新任务才新建；任务描述入口 = SPA 文本框 +
  CLI `--task`（可选，双用途：检索锚点 + 反哺蒸馏）
- [x] **S5 多任务工作流**：`treeforge distill` 支持多 trace 输入 + `--fresh`；serve 侧
  控制面板加「按 host 再蒸馏（累积）」入口
- [x] **S6 评测对接**：配合 TreeWalker "with site knowledge" 变体口径（分列报告，
  不进主口径——评测红线）；任务卡目录约定即 TreeWalker 检索的读取契约——TreeWalker
  侧加载任务级 skill 的技术方案见 [docs/task-skill-loading-design.md](./docs/task-skill-loading-design.md)
  （LLM-as-ranker 命中注入 + 保守匹配 + 三口径评测纪律，2026-09-05）
- [x] **测试**：mock LLM 验证增量合并 / 多 trace 归并 / 任务卡双产物 / 解析失败重试+保旧卡（268 测试）
  （不破坏现有单任务行为）

### 明确不做（边界）

- ❌ **任务级检索机制**（蓝图路线三的检索）——误命中比未命中更糟；按 host 文件注入已够。
  未来产品化再议（前置：P4 基建）
- ❌ **去站点化通用 skill**——与 TreeForge「站点特定」定位相反（那是 Browser-BC 的路）
- ❌ **工具层能力**（网格渲染等待/表单交互/dialog 自动处理）——蓝图路线一，TreeWalker
  自己的事，skill 解决不了
- ❌ **向量检索**——Browser-BC 哲学 LLM-as-ranker, no embeddings（沿袭「不做」清单）

### 验收

- 同一 host 录 ≥3 个不同任务（分次录制）→ 蒸馏产物含站点级信息（菜单结构 / 功能入口 /
  站点通用知识），且增量更新以旧卡为基线——**旧主题不被丢弃**（localhost 15 任务实测教训：
  原形态 16 轮后 15 任务只剩 2 序列，已按 Browser-BC 修订）
- 双产物：一次蒸馏（带任务描述）同时产出 host 累积卡 + 任务卡
  （`tasks/<slug>/_task.json` 含描述与关键词）；TreeWalker 现有注入不受 `tasks/`
  子目录影响
- 增量：第 N+1 次蒸馏保留前 N 次仍有效的知识；与旧证据冲突时以新证据为准
- 回归：现有单任务蒸馏行为不变（234 测试不破）
- TreeWalker 侧（with site knowledge 变体）：导航类失败减少 + 同任务多次跑路径方差收敛

---

## 不做（明确排除）

- **向量化检索** —— Browser-BC 哲学：LLM-as-ranker, no embeddings
- **DB 持久化** —— 文件系统是唯一持久层（skills/、captures/ 等运行时产物）
- **SDK 依赖** —— anthropic / openai SDK 不引入，LLM 走 urllib
- **subprocess 管线** —— in-process import（避免 PyInstaller frozen-subprocess 坑）
- **结构化 selector 库** —— skill 给 LLM 看自然语言描述，不做「给 CDP 直接执行」的结构化 selector（精度要求与 record-replay 同档，违背「给 LLM 看」定位）
- **api.md 文件** —— P0.5 实测在无网络采集时恒为「未观察到私有 API」零信息，已从三件套删除；
  若 P2 采集层接入网络采集后有真实 API 数据，可考虑作为可选项重新引入

---

## 里程碑速查

| 阶段 | 交付物 | 状态 | 备注 |
|---|---|---|---|
| P0 | CLI 跑通 + skill 输出（最小闭环） | ✅ | 完成 |
| **P0.5** | **skill 形态对齐（element_attrs/page_context/stage）+ 精简重构（host 级三件套）** | ✅ | **A/B 失败后诊断修正，73 测试** |
| **P1** | **A/B 验证 skill 能否提升 agent 准确率** | ✅ | **蒸馏精简版 100% 达手写水平，核心闭环成立** |
| **P2** | **MV3 扩展录制（精度对准 LLM 可读，含 element_attrs/page_context + 采集后端）** | ✅ | **采集层 + 后端全链路打通，端到端调试 6 bug 修复** |
| **P3** | **FastAPI 常驻服务（采集 + 蒸馏 API + 控制面板 SPA）** | ✅ | **serve 子命令，183 测试（含 26 serve），端到端冒烟通过** |
| **P3.5** | **控制面板优化与配置增强** | ✅ | **P3 最小 SPA 打磨成可日常使用的运维台，194 测试（+11）** |
| **P3.6** | **录制功能扩展（迁移自 TreeWalker，作 distill 采集补充）** | ✅ | **v0.2.0，扩事件词汇 + SPA nav hook + modal/dropdown 信号，216 测试（+21）** |
| **P3.7** | **蒸馏注入消费端上下文（TreeWalker agent 能力模型）** | ✅ | **代码 + 真 LLM 验证（quirks 不再否定 select_dropdown）；A/B 测待做** |
| **P4** | **站点级 + 任务级双产物（多任务累积蒸馏：registry 持久化 + host 级增量 + 站点地图 + 任务卡）** | ⏳ | **计划中：对齐 TreeWalker 优化蓝图路线二（消导航不确定性），参照 Browser-BC 多 demo 归并；任务描述作未来检索锚点** |

---

## 与原路线图的差异

### 2026-07-18 调整（核心机制重新排优先级）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| 核心验证（原 P3） | P3 | **P1** | 闭环判据，提前到投入采集层前 |
| 接入层（原 P1） | P1 | **P3** | 工程优化，缓做 |
| 采集层精度 | 对标 BrowserBC | **对准「LLM 可读」** | skill 给 LLM 看，精度比 record-replay 低一档比 BrowserBC 高一档 |
| 结构化 selector | 未明确 | **明确不做** | 与「给 LLM 看」定位冲突 |

### 2026-07-28 更新（按实际进展）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| skill 形态 | CSS selector 四件套 | **element_attrs + page_context + 三件套** | 实测 CSS selector 与 TreeWalker DOM 对不上（11 个 0 命中）；A/B 失败后精简 |
| 产物组织 | 按 capacity 分桶四件套 | **host 级合并三件套** | A/B 诊断：capacity 割裂导致重复描述，quirks 噪声淹没真坑 |
| P1 状态 | 未开始 | **✅ 达成（蒸馏精简版 100% 达手写水平）** | 四版 skill × N=5 横向对比，蒸馏精简版 = 手写精简版，核心闭环成立 |
| api.md | 四件套之一 | **删除（明确不做）** | 无网络采集时零信息，浪费文件槽位 |
| A/B 指标 | 成功率 + 步数双判据 | **只用成功率（步数是噪声）** | 蒸馏版 N=3 +37% vs N=5 −24% 方向相反，步数被 LLM 随机性主导 |

核心逻辑：先用最小代价（手写 skill + 改注入）验证闭环成立，再投入最重的采集层。
**P1 已验证闭环成立**（蒸馏精简版 100% 成功、零异常，达手写精简版水平）——
P2 采集层核心风险解除，可投入最重的采集层开发。

### 2026-08-01 更新（P2/P3 完成，新增 P3.5）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| P2 状态 | ⏳ 未开始 | **✅ 完成** | 采集层（MV3 扩展 + CdpSession + Collector + stage）全链路打通，端到端调试 6 bug 修复 |
| P3 状态 | ⏳ 缓做 | **✅ 完成** | FastAPI 常驻服务（serve）落地：采集 router + 蒸馏后台任务 + 配置/状态 API + 控制面板 SPA |
| P3 上传协议 | 分块上传（init/finalize/status + sha256 可恢复） | **直接 POST envelope**（移除分块上传） | 采集层扩展直连后端 POST 事件，无大文件上传场景；蒸馏读已落盘 trace |
| 蒸馏管线 | CLI 专用 `_run_distill` | **提炼 `run_distill_pipeline`，CLI/HTTP 共用** | serve 蒸馏 router 与 CLI distill 共用同一管线（DistillResult dataclass） |
| 接入层 Windows 适配 / 完整 redact / 增量蒸馏 | P3 范围 | **延后到后续** | P3 聚焦常驻服务骨架，这些工程加固项后做 |
| **P3.5** | — | **新增（控制面板优化 + 配置增强）** | P3 只做了最小 SPA，P3.5 把它打磨成可日常使用的运维台（产物浏览树 / 配置编辑表单 / LLM 自检等） |

核心逻辑：P0→P3 主链路全部打通（蒸馏 → 采集 → 常驻服务），v0.1.0 已发布。
P3.5 聚焦控制面板体验闭环，让浏览器能完成「录一段 → 蒸馏 → 浏览产物」全流程，无需回到命令行。

### 2026-08-02 更新（P4 明确不做，路线图收尾）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| **P4 检索层** | ⏳ 可选（MCP stdio 检索） | **明确不做（从路线图删除）** | TreeWalker 用文件注入（P1 落地）已足够，无需 MCP 检索；曾留作学习 BrowserBC 检索层用，现确认不做 |

核心逻辑：TreeForge 主链路（蒸馏 → 采集 → 常驻服务 → 控制面板）已全部完成。
检索层（MCP）明确不做——文件注入零运行时依赖，比 MCP stdio 更简单可靠。

### 2026-08-02 更新（新增 P3.6：迁移 TreeWalker 录制能力作 distill 采集补充）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| distill 采集事件词汇 | click / input(仅 Enter) / scroll / navigate(声明未接线) / change(声明未接线) | **加 select_dropdown / upload_file / send_keys / SPA nav hook / modal·dropdown 信号** | 实测部分简单操作（下拉/上传/快捷键/弹窗）采不到，quirks.md 原料不完整 |
| TreeWalker 录制扩展 | 不在 TreeForge 范围 | **部分迁入作 P3.6**（只取事件词汇广度 + SPA nav + JS 点击标记 + 副作用信号，复用现有策略/Envelope/Collector） | 同团队同采集哲学，互补不重复；TreeWalker replay 重机器（指纹/定位/flatten）定位相反不迁 |
| 路线图终点 | P3.5 | **P3.6**（P3.5 是主链路终点，P3.6 是采集能力的横向补充，非新阶段） | 主链路已完成，P3.6 只是补 distill 采集的事件覆盖度 |

核心逻辑：P3.6 不是新阶段，是「distill 采集横向扩词」的补充——把 TreeWalker 同源扩展里
TreeForge 缺的四件能力迁过来，不重复已有代码，不迁定位相反的 replay 重机器。

### 2026-08-09 更新（新增 P3.7：蒸馏注入消费端上下文）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| distill prompt 的消费端模型 | 只说「读 `[index]<tag attr=val /> text` DOM 的 LLM agent」（感知模型），无动作词汇 | **加 agent 动作词汇表 + 「已自动处理别写」清单**（能力模型） | 蒸馏版 skill 因漏「upload_file 直注」比手写差 38%；prompt 里 "tool" 出现 0 次，LLM 只能猜动作名 |
| quirks Do-NOT-WRITE 启发式 | 「agent 读 DOM 能推断出来的不写」 | **扩到「agent 自动处理的不写」**（JS 点击/遮挡/下拉降级/多 file input 已带 class·accept） | 现有启发式只覆盖 DOM 可见，没覆盖 agent session 层自动兜底；不加约束 LLM 会写废话/误导 |
| 路线图终点 | P3.6 | **P3.7**（仍是主链路外的质量优化补充，非新阶段） | P3.6 是采集层扩词，P3.7 是蒸馏层提质——两者都服务「让 skill 更对口 TreeWalker」 |

核心逻辑：TreeForge skill 是 TreeWalker 专用、不需通用。让蒸馏 LLM 看到消费端能力边界，
sop 动词落到真实 tool 名、quirks 只写 agent 真推断不出来的坑。**关键洞察：「别写」比
「能做」更值钱**——agent 已自动处理的事写进 skill 反而浪费 token 甚至误导。纯 prompt
改动，无运行时跨项目依赖；主要服务真 LLM 蒸馏（模板模式无 LLM 不受益）。

### 2026-08-23 更新（新增 P4：站点级 skill——对齐 TreeWalker 优化蓝图路线二）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| **P4（新）** | —（P4 编号随检索层删除而空出） | **站点级 skill：多任务累积蒸馏** | TreeWalker 优化蓝图路线二——88 个失败约一到两成卡「路盲」，27% 翻转不稳一半来自导航随机；skill 形态 = 布局/菜单/功能地图/典型操作序列，预期 10-20pp |
| 任务级 vs 站点级 | 只有任务级（一个任务录一遍 → 蒸馏 SOP） | **任务级蒸馏侧标记就绪**（路线三的检索明确不做，评测红线）；P4 补站点级 | 蓝图路线三定位产品口径非评测手段；路线二才是评测可口径（with site knowledge 变体分列） |
| 增量蒸馏（原 P3 延后项） | 挂账未做（`prev_sop` 占位 + registry.py 空实现） | **移入 P4 S1/S2**（SkillCard 按 host 持久化 + host 级增量合并） | 站点级的本质就是跨 trace 累积——增量蒸馏从「工程加固项」升格为「P4 核心」 |
| 产物形态 | 三件套 = 单任务流程叙事 | **P4 S3：演进为功能地图 + 按 capacity 分组的典型操作序列** | agent 要「站点长什么样、入口在哪」，不是「这一条流程怎么走」 |
| 参照对象 | Browser-BC（五阶段管线来源） | **多 demo 归并机器复用到跨 trace 维度**；知识取向/消费方式/归并单位三分叉点保持不变 | Browser-BC 的 atomize→classify→bucket→distill 正是「多次操作累积成知识」；TreeForge 反向（站点特定 + 文件注入 + host 归并） |

核心逻辑：TreeForge 作为 TreeWalker 配套项目，任务级 skill（录一遍任务 → 蒸馏 SOP →
文件注入）的蒸馏侧已闭环；下一阶段对齐蓝图路线二做**站点级**——同一 host 多任务累积
蒸馏出「功能地图 + 典型操作序列」，消导航不确定性。**边界清晰**：skill 只解决「路盲」，
解决不了路线一的墙（工具层是 TreeWalker 的事）；任务级检索维持不做（评测红线 + 误命中
比未命中更糟）。

