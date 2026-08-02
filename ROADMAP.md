# TreeForge 路线图

> 从 init-plan 抽取，2026-07-18 按核心机制重新排优先级，2026-07-28 / 2026-08-01 按实际进展更新。
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
- [ ] distiller 增量蒸馏真接通（registry 持久化旧 SkillCard，8000 字符截断塞 prompt）
- [ ] host 级增量蒸馏（P0.5 host 级蒸馏后，增量逻辑改为 host 级）

> **注**：原 P3 设想的「分块上传协议（init/finalize/status）+ sha256 可恢复上传」最终未采用——
> serve 走的是直接 POST CaptureEnvelope（采集）+ HTTP 触发蒸馏（读已落盘 trace），不需要分块上传。
> 这条从路线图移除（采集层扩展直连后端 POST 事件，无大文件上传场景）。

---

## P3.5 —— 控制面板优化与配置增强（进行中 ⏳）

**目标**：把 P3 落地的最小 SPA（纯 HTML+fetch 骨架）打磨成可日常使用的运维台，并补齐配置管理。

> P3 只做了最小可用 SPA（录制状态卡 / 触发蒸馏表单 / 配置展示只读）。P3.5 聚焦「能真正
> 替代 CLI 日常使用」的体验闭环，不引入前端框架（保持纯 HTML+fetch 哲学，与 P3 一致）。

### 计划

- [ ] **控制面板细节优化**
  - [ ] 蒸馏任务列表视图（历史 job + 状态/耗时/产物，替代单纯轮询单个 job）
  - [ ] 蒸馏进度条可视化（phase/current/total 实时渲染，失败原因高亮）
  - [ ] 产物浏览树（captures/ 与 skills/ 下钻：点 host → 看 md 文件 → 预览内容）
  - [ ] 录制状态实时刷新（已有 3s 轮询，加 session_id / 已采事件数 / 当前 stage 展示）
  - [ ] 触发蒸馏时支持「从最近一次 capture 选 trace」（下拉，免手填路径）
  - [ ] 错误/通知反馈（toast 或内联，替代 alert）
  - [ ] 响应式与暗色模式打磨（现有 prefers-color-scheme 基础上完善）
- [ ] **配置功能增强**
  - [ ] 配置编辑表单（当前 `POST /api/config` 只支持白名单 key，UI 加表单校验 + 提交）
  - [ ] LLM 连通性自检（新增 `GET /api/config/check`：实际调一次最小 LLM 请求验证 key/base/model 可用）
  - [ ] 配置项分组（模型参数 / 管线参数 / 路径，对齐 `config.describe()` 字段）
  - [ ] 配置变更后蒸馏产物的输出目录可视化（改 OUTPUT_DIR 后 skills 列表跟着变）
- [ ] **配套 API 补充**
  - [ ] `GET /api/captures/{name}`：单个 capture 详情（事件数 / stages / trace 路径）
  - [ ] `GET /api/skills/{host}/files`：列某 host 下的 md 文件 + 内容预览
  - [ ] `GET /api/status` 扩展：当前 session 的 session_id / 事件数 / stage

**验收**：浏览器访问控制面板能完成「录一段 → 看状态 → 选 trace → 触发蒸馏 → 看进度 → 浏览产物」
全流程，无需回到命令行；配置改动有表单 + 自检反馈。

---


## P4 —— 检索层（可选）

**目标**：MCP stdio 检索。

> **明确不做**：TreeWalker 用文件注入（P1 落地的机制）不需要 MCP 检索。
> 此阶段仅作学习 BrowserBC 检索层用，不影响主链路。

- [ ] registry.json 持久化
- [ ] `query_top_k`：LLM-as-ranker 语义召回（无 embedding）
- [ ] `synthesize_playbook`：LLM 编排多 skill playbook
- [ ] MCP stdio server（`treeforge mcp-skill` 子命令）
- [ ] 两层召回路由（单强匹配直返 / 多匹配 playbook / degrade 链）

---

## 不做（明确排除）

- **向量化检索** —— Browser-BC 哲学：LLM-as-ranker, no embeddings
- **DB 持久化** —— 文件系统是唯一持久层（checkpoint.json / registry.json / skills/）
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
| **P3.5** | **控制面板优化与配置增强** | ⏳ | **P3 最小 SPA 打磨成可日常使用的运维台** |
| P4 | MCP 检索（可选） | ⏳ | 学习用，不影响主链路 |

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
