# TreeForge 路线图

> 从 [init-plan.md](./init-plan.md) 抽取，2026-07-18 按核心机制重新排优先级。
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
  - [x] DISTILL（distiller.py）：★站点特定四字段 prompt + 模板 fallback
- [x] LLM 客户端（llm.py）：urllib 双协议探测（Anthropic / OpenAI）
- [x] 输出 adapter（treewalker + browserbc）
- [x] CLI（`treeforge distill` / `treeforge info`）
- [x] 示例 trace（bilibili-upload + github-login）
- [x] 测试（atomizer / classifier / distiller / adapters / llm）
- [x] 文档（README / ARCHITECTURE / ROADMAP）
- [x] WXT 扩展脚手架（仅结构）

**验收命令**：

```bash
uv sync --extra dev
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
ls ./data/skills/domain-skills/bilibili.com/
# → _sop.md / selectors.md / quirks.md / api.md
```

模板模式（不调 LLM 也能跑）：`uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm`

---

## P1 —— ★核心验证：skill 能否提升 agent 探索准确率（提前，原 P3）

> **路线图最大调整**：原 P3（质量验证）提前到 P1。这是整个闭环成立与否的判据。
> 在投入 P2 采集层（最重）之前，先用最小代价回答「skill 注入 agent 上下文后，探索准确率有没有提升」。
> 如果这个假设不成立，整个闭环断了，P2 的重投入不值得。

**目标**：手写 skill + 改 TreeWalker agent 注入 → 实测对比「有 skill vs 无 skill」的探索成功率。

- [ ] **手写一个真实站点的 skill**（推荐 B 站上传，基于 record-replay 已积累的知识）
  - [ ] `selectors.md`：手写关键元素的稳定 selector + AX name（来自 [[browser-accessibility-tree]]）
  - [ ] `quirks.md`：手写 SPA 导航、file upload 怪癖、隐藏等待（来自 [[browser-wait-and-timing]]）
  - [ ] `_sop.md`：手写任务流程骨架
  - 不依赖 TreeForge 任何代码——纯手写 markdown，直接验证下游
- [ ] **TreeWalker 加 skill 注入机制**（TreeWalker 侧改动，非 TreeForge）
  - [ ] `goto_url` / `get_browser_state_summary` 时，按 host 读 `domain-skills/<host>/*.md`
  - [ ] 把 skill 内容拼进 agent 的系统 prompt 或用户消息上下文
  - [ ] 注入时机：导航到新域名时注入，避免一次性灌所有 skill
- [ ] **A/B 实测对比**
  - [ ] 无 skill：TreeWalker agent 跑 B 站上传 N 次，记录成功率/步数/耗时
  - [ ] 有 skill：同样跑 N 次，对比
  - [ ] 判据：成功率提升 ≥ 20pp 或步数减少 ≥ 30%，则闭环成立
- [ ] **若闭环成立**：固化 skill 注入机制，作为 P2 采集层的验收标准
- [ ] **若闭环不成立**：分析原因（skill 信息没被 LLM 用上？格式不对？注入时机不对？），调整后再验；若反复不成立，重新评估 TreeForge 方向

**为什么这是最高优先级**：P2 采集层是最重的投入（MV3 扩展 + 踩 record-replay 的坑）。
在投入之前必须验证「产物有用」。这是用最小代价获取方向性判断。

---

## P2 —— 采集层（精度对准「LLM 可读」）

**目标**：MV3 扩展录制真实浏览器操作，产出可蒸馏的 trace。

**精度取向调整**（基于 P0 确认的「给 LLM 看」定位）：
- selector 记录到「LLM 能理解是哪个元素」即可，不要求 CDP 精确匹配
- xpath 记录作辅助线索，不要求与 CDP 树完全一致（skill 给 LLM 看不参与五级匹配）
- file upload / modal 怪癖要记（quirks.md 的原料），但容忍单次录制噪声（蒸馏会过滤）

**可直接借鉴 TreeWalker record-replay 采集端**（已踩过坑的成熟实现）：
- `recording_extension/capture/action-recorder.ts`（事件采集 + 去噪）
- `recording_extension/capture/selector.ts`（buildElementRef / bestSelector / xpathFor）
- `recording_extension/capture/navigation-recorder.ts`（SPA 导航 hook）
- `findInteractiveAncestor` 的 cursor:pointer + onclick 检测（对齐后端 is_interactive）

**比 record-replay 可简化**：
- 不需要实时后端算指纹（蒸馏不在乎时序）
- 不需要 D1/semantic_clue 兜底（蒸馏容错）
- 不需要 xpath 与 CDP 对齐（skill 给 LLM 看不参与匹配）

- [ ] WXT 扩展真实实现（基于现有脚手架）
  - [ ] background SW + recorder 状态机（MV3 SW 30s 回收恢复，借鉴 BrowserBC）
  - [ ] content script：DOM 事件采集（借鉴 TreeWalker action-recorder，精简到蒸馏所需）
  - [ ] injected：history.pushState/replaceState monkey-patch（SPA 导航）
  - [ ] popup：录制控制 UI
- [ ] selector 多级 fallback（`data-testid` → `aria-label` → `name` → `[role]` → xpath）
- [ ] DOM 快照（≤300 元素 + sha256 去噪 + 节流，借鉴 BrowserBC dom-snapshot）
- [ ] 表单摘要（4 阶段：opened/edited/submitted/reset，借鉴 BrowserBC form-summary）
- [ ] Dexie (IndexedDB) 存储（MV3 SW 回收不丢事件，借鉴 BrowserBC）
- [ ] **P1 验证过的 skill 注入机制作为采集端验收标准**：录一个站点 → 蒸馏 → 注入 agent → 探索成功率达标

---

## P3 —— 接入层（原 P1，缓做）

**目标**：扩展 → server → 蒸馏全自动，人录一遍就出 skill。

> **缓做理由**：接入层是工程优化（自动化、可恢复上传、进度轮询），不是核心价值。
> P1（验证）+ P2（采集）用 CLI 跑通蒸馏链路已足够。
> 接入层等闭环验证成立、采集层稳定后再做，避免过早优化。

- [ ] FastAPI 单文件 server（`server/server.py`）
  - [ ] 分块上传协议（init/finalize/status 四端点）
  - [ ] 可恢复上传（sha256 校验 + 幂等 upload_id）
  - [ ] 异步蒸馏（全局 `_PIPELINE_LOCK`）
  - [ ] 进度轮询（内存 dict + harness.progress 注入）
- [ ] 接入层 Windows 适配（msvcrt 文件锁 / `_ResilientStream` / 双写日志）
- [ ] 完整 redact（CVV / OTP / account token 正则，对齐 Browser-BC）
- [ ] distiller 增量蒸馏真接通（registry 持久化旧 SkillCard，8000 字符截断塞 prompt）
- [ ] 桶合并 consolidate（同义 capacity 合并 CLI 子命令）

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

---

## 里程碑速查

| 阶段 | 交付物 | 状态 | 备注 |
|---|---|---|---|
| P0 | CLI 跑通 + 4 文件 skill 输出 | ✅ | 完成 |
| **P1** | **手写 skill + agent 注入 + A/B 验证** | ⏳ | **核心判据，提前** |
| P2 | MV3 扩展录制（精度对准 LLM 可读） | ⏳ | P1 成立后才投入 |
| P3 | FastAPI 接入层 | ⏳ | 缓做，工程优化 |
| P4 | MCP 检索（可选） | ⏳ | 学习用，不影响主链路 |

---

## 与原路线图的差异（2026-07-18 调整）

| 项 | 原 | 现 | 理由 |
|---|---|---|---|
| 核心验证（原 P3） | P3 | **P1** | 闭环判据，提前到投入采集层前 |
| 接入层（原 P1） | P1 | **P3** | 工程优化，缓做 |
| 采集层精度 | 对标 BrowserBC | **对准「LLM 可读」** | skill 给 LLM 看，精度比 record-replay 低一档比 BrowserBC 高一档 |
| 结构化 selector | 未明确 | **明确不做** | 与「给 LLM 看」定位冲突 |

核心逻辑：先用最小代价（手写 skill + 改注入）验证闭环成立，再投入最重的采集层。
若 P1 验证失败，整个方向要重新评估，避免 P2 的重投入打水漂。
