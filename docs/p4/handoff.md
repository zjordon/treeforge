# P4 工作交接文档（站点级 + 任务级双产物）

> 本文档用于上下文压缩后的工作交接。记录 P4 的架构决策、两次生产事故复盘、
> 数据现状、任务级 skill 加载方案，以及待办。压缩上下文后从本文档继续。
> 覆盖会话区间：P4 规划 → 实施 → 两次事故修复 → 形态修订 → 15 轮重放 →
> WebArena 实测 → 任务级加载方案（2026-08-23 ~ 2026-09-05）。

## 一、P4 整体目标与定位

**目标**：把「一个任务录一遍 → 蒸馏任务 SOP」升级为「同 host 多任务累积蒸馏」，
对齐 TreeWalker 优化蓝图路线二（站点级消导航不确定性）+ 路线三（任务级产品化）。

**核心成果（已达成并实测）**：
- **双产物**：一次蒸馏（用户操作一次）同时产站点级累积卡（`domain-skills/<host>/`
  三件套）+ 任务级独立卡（`tasks/<slug>/` 三件套 + `_task.json` 检索锚点）
- **增量不丢知识**：registry 持久化旧卡，Browser-BC 式「以旧卡为基线更新」
- **WebArena 实测**（用户在 TreeWalker 侧做）：站点级注入有效提升任务成功率，
  失败率仍高 → 引出任务级加载需求（见 §六）

**演进关键认知**（P4 中段被实测推翻又重建的）：
- ❌ 最初形态：「按 capacity 每任务一节」堆积序列 → 15 任务实测**内容逐轮丢失**
- ✅ 最终形态（用户拍板）：**站点级 = 有界摘要**（功能地图 + 站点通用知识，
  ≤8000 内「压缩措辞不丢主题」），逐任务序列归任务卡——Browser-BC 的 8000 之所以
  够用是因为它按 capacity 分桶，站点级一张卡要有界就得靠摘要而非堆积

## 二、当前状态快照（截至 2026-09-05）

| 项 | 状态 |
|---|---|
| 分支 | `master`（feat/p4 已经 PR #8 squash 合入并删分支，issue #7 CLOSED） |
| 版本 | **v0.3.0** 已发布（`56fd228`）；P4 主体 = `ad39107` |
| 测试 | 268 全过，ruff clean |
| 未提交 | `ROADMAP.md`（P4 S6 加了任务级加载方案链接）、`docs/task-skill-loading-design.md`（新，§六）、`CLAUDE.md`（**用户自建**的 AGENTS.md 指针，非本项目产物，别动） |
| localhost 数据 | 站点卡 **v48**（用户 WebArena 测试持续蒸馏中）、**44 张任务卡、40 个 capacity** |
| serve | 运行中（SPA 双产物展示已修复；重启才加载新 Python 代码，SPA 强刷即生效） |

## 三、P4 架构精髓（改代码前必读）

### 管线两跳（`server/distill_api.py::run_distill_pipeline`）

```
ADAPT(×N trace, stage 冲突重映射 stage@N) → ATOMIZE(×N) 拼接 → CLASSIFY → BUCKET
  → 跳 A 站点级：distill_buckets(prev_cards) → INSTALL host 三件套 + registry 落卡
  → 跳 B 任务级：distill_task(任务描述, 现有卡清单) → write_task_card(tasks/<slug>/)
```

### 关键机制清单（都在代码注释里有完整理由）

| 机制 | 位置 | 一句话 |
|---|---|---|
| registry 卡片持久化 | `harness/registry.py` | `output_dir/registry/<host>.json`；损坏容错返 None；trace_sources 并集 |
| 增量 addendum | `harness/distiller.py::_INCREMENTAL_ADDENDUM` | 三文件块（预算 **8000/3000/4000，sop 预算有测试钉死**）；「旧卡是 BASE，压缩措辞不丢主题，新证据优先」 |
| 站点级有界摘要 | `_DISTILL_PROMPT_TEMPLATE` sop spec | 必写「站点功能地图」+「站点通用操作知识」；≤~6000（硬上限 8000）；**禁止**逐任务序列（那是任务卡的事） |
| capacities 信息化 + 并集 | `distill_host` | 与旧卡 meta.capacities 并集只增不减（曾因只含当前任务导致丢内容） |
| 任务级模板 | `_TASK_PROMPT_TEMPLATE` | 与站点级**共用 spec 块拼装**（`_SELECTORS_SPEC`/`_QUIRKS_SPEC`/`_RULES_BLOCK`/`_PAGE_CONTEXT_SECTION`），不复制两份 |
| slug 稳定化 | `distill_task` + adapter `list_task_cards` | prompt 注入现有任务卡清单，同任务重录（不同时段/措辞）**复用 slug 覆盖**；无现有卡才 LLM 生成 / 回退 trace 目录名 |
| 任务卡落盘 | `adapters/treewalker_adapter.py::write_task_card` | 同 slug 覆盖 + `source_traces` 并集；`_task.json` = 检索锚点契约 |
| LLM+解析重试 | `distill_host`/`distill_task`（`_DISTILL_ATTEMPTS=2`） | **llm.py 只重试 HTTP 层**；畸形 JSON 发生在解析层，必须在此层重取新样本 |
| 失败保旧卡 | `_card_from_prev` | 重试耗尽且有旧卡 → 返回旧卡内容（版本不动，meta 留 `kept_after_llm_failure`）；绝不产模板垃圾覆盖好卡 |
| registry 跳模板卡 | `distill_api._save_cards_to_registry` | `model=="(template)"` 的兜底卡不落盘——一次 LLM 失败不毁好卡 |
| 模板模式不累积 | 决策 4 | `--no-llm` 跳过 registry 读写与 slug 复用（无 LLM 判不了） |
| 多 trace stage 冲突 | `distill_api._remap_stage_conflicts` | 第 N≥2 个 trace 冲突 stage 改名 `stage@N` + 事件重映射 |

### 任务描述优先级

显式参数（CLI `--task` / SPA 文本框）> trace 自带 `task_instruction`（popup 无输入 UI，实测恒空）。

## 四、两次生产事故复盘（本会话最有价值的经验，防复发）

### 事故 #1：第二次蒸馏站点卡变模板（2026-08-30）

- **现象**：首蒸正常；第二任务蒸馏后站点级变模板产物，任务级正常
- **根因**：glm-5.2 把长中文 markdown 塞 JSON 字符串时**间歇性输出畸形 JSON**
  （未转义内引号等）；`parse_json_from_model` 4 级容错救不回 → `distill_host`
  except 静默兜底模板 → 模板卡又被当正常卡覆盖 registry 好卡（版本倒退）
- **修复三件套**：解析失败重试（§三）+ 失败保旧卡 + registry 跳模板卡 + 全部
  logger.warning（原来只进被覆盖的瞬时 job detail，无法诊断）
- **教训**：**兜底路径必须与成功路径可区分**（meta 标记 + 落盘策略差异）；
  LLM 输出解析失败 ≠ LLM 不可用，值得重试

### 事故 #2：任务越多站点卡内容越少（2026-08-31）

- **现象**：15 任务 16 轮后，`_sop.md` 典型操作序列只剩 2 节 + 模型自造的
  「历史操作序列」归档节
- **根因**（三个缺口叠加）：① capacities_line 只含当前任务 + prompt 指示按它分组
  → 旧任务无槽位；② 8000 截断让旧内容后半不可见；③ 合并指令没有「不丢主题」约束
  → 模型每轮对「历史」节再压缩（传话游戏）
- **修复**：站点级形态整体改有界摘要（§三）；8000 预算**保留**（用户判断正确，
  实测 15 任务才 3.2k chars）；验证 = `scripts/redistill_site.py` 15 轮重放
  （1247→…→3196 单调增长、首轮知识存活、无归档节）
- **教训**：给 LLM 的结构指令（分组/清单）决定了它保留什么——**只列当前项的清单
  等于授权丢弃其余**；累积系统的 prompt 必须显式声明「基线 + 只增的清单」

### 事故 #2.5（易误判，不是 bug）

新任务蒸馏后站点卡内容不变 = 该任务**没有带来新的站点级共性**（如
count-pending-reviews vs count-total-reviews 同类）→ 按设计不变，任务差异在任务卡里。
判据：registry `version` 递增且 `kept_after_llm_failure` 为 None = 真跑了 LLM 且成功。

## 五、重蒸脚本与数据现状

- **`scripts/redistill_site.py`**：按任务卡逐个重蒸重建站点卡——读每张
  `_task.json` 的 desc + source_traces；**首个成功轮 fresh**（首败不污染），后续增量；
  `--dry-run` 看计划；无 LLM_KEY 拒跑
- localhost 实测：15 轮重放 0 失败 → v15 新形态；用户后续 WebArena 测试持续增量到
  **v48 / 44 任务卡 / 40 capacities**
- **SPA 双产物展示已修**（`305dacc` squash 入 master）：任务行显示「站点级 N 文件 +
  任务级 skill（slug=…）→ tasks/<slug>/」，历史 job 刷新即回显

## 六、任务级 skill 加载方案（本会话最后产物，下一步的主线）

**文档**：`docs/task-skill-loading-design.md`（未提交）——TreeWalker 侧实现设计，
TreeForge 契约视角。要点：

- **检索**：LLM-as-ranker（no embeddings）——catalog 全量（44 任务仅 ~6k chars）+
  用户任务文本 → 一次 fast 调用；**保守匹配**（只有「本质同一操作」才命中，近邻变体
  不命中，null 是一等答案——误命中比未命中更糟）；所有异常降级为不注入
- **注入**：`[Task Skill]` 块，`[Task]` 后 `[Domain Skill]` 前；命中卡三件套全文
  （均值 2.1k chars）；「指引非脚本」声明——失准自然回落探索
- **评测红线**：任务级 = 产品口径，禁止与任何评测口径混合；诚实测法 = 不相交泛化
  （蒸馏 A 组测 B 组变体）
- **TreeWalker 实施 S1-S5**（task_loader / 匹配器 / 注入块 / 匹配日志 / 分口径脚本，
  预估一天半）；**TreeForge 零改动**——可能回流点仅两个：契约加字段、keywords 双语优化

## 七、待办与开放项

| 项 | 状态 | 说明 |
|---|---|---|
| 未提交文档 | 待同步 | `ROADMAP.md`（S6 链接）+ `docs/task-skill-loading-design.md`；`CLAUDE.md` 是用户自建，勿动勿提交（除非用户说） |
| TreeWalker 侧 S1-S5 | **下一主线** | 按 §六 文档实施；评测日志是调匹配 prompt 的唯一依据 |
| trace_sources 路径重复 bug | 已发现未修 | 重蒸脚本传绝对路径、CLI/serve 传相对路径 → `save_card` 并集去重失效（registry 里 14 对重复）；修法 = union 前 `resolve()` 归一化 |
| `--site-only` 开关 | 提过未做 | 只重蒸站点级不动任务卡；需求确认后再加 |
| cousin 档匹配 | 远期实验 | 近邻任务命中放宽；先拿匹配日志说话 |
| serve 旧进程 | 注意 | 运行中的 serve 不自动加载新 Python 代码——改完代码要重启；SPA 是 StaticFiles 直读盘，强刷即可 |

## 八、关键文件速查

| 文件 | 职责 |
|---|---|
| `harness/distiller.py` | 双 prompt（站点有界摘要 / 任务单任务叙事，共用 spec 块）+ `distill_host`（增量+重试+保旧卡）+ `distill_task`（slug 稳定化） |
| `harness/registry.py` | host 卡持久化（P4 重建，原检索层占位已废弃删除） |
| `server/distill_api.py` | 管线编排（两跳 + registry + stage 重映射 + 模板卡跳落盘） |
| `adapters/treewalker_adapter.py` | `write_task_card` / `list_task_cards`（tasks/ 布局所有者） |
| `scripts/redistill_site.py` | 站点卡重建脚本 |
| `docs/p4/p4-implement-plan.md` | P4 实施方案（含 2026-08-30 形态修订记录） |
| `docs/task-skill-loading-design.md` | 任务级加载方案（§六） |
| `tests/test_distill_incremental.py` / `test_distill_task.py` / `test_registry.py` | P4 新测试（增量 10 / 任务级 12 / registry 7） |

## 九、关键约束（沿 AGENTS.md，含本次修订）

- 4 空格缩进 / 行宽 100 / ruff；`uv run` 一切；测试 mock 不真调 LLM
- **不主动 commit/push**（用户明确说「同步」才提交）；中文 commit 走 `git commit -F`
- **`data/` 已于 2026-08-31 解除 gitignore、可入库**（AGENTS.md 已同步）；
  `.env` / `uv.lock` 仍不提交；captures 含本机路径，提交前自行评估
- 站点级形态是**用户拍板的决策**（有界摘要 8000），别回退成逐任务堆积；
  `_PREV_SOP_BUDGET` 有测试钉死在 8000，改它要先过用户
- LLM 间歇畸形 JSON 是已知事实——任何新的「调 LLM + 解析」路径都应带重试 +
  可区分的兜底（参照 §三 机制表）
