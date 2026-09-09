# 任务级 skill 加载技术方案（TreeWalker 实现 / TreeForge 契约视角）

> 状态：v2 已定稿并接管对接基准（TreeWalker `docs/p7/03-task-skill-loading-design.md`）；
> 本文保留作 v1 存档，并承载 **TreeForge 侧待办（§十一，S0b）**——issue 从该节创建。
> 日期：2026-09-05（v1 定稿）；2026-09-05 评审修订（追加 §十一）。
> 关联：TreeWalker 优化蓝图路线三（任务级 skill 产品化）、本仓库 P4 双产物
> （`docs/p4/p4-implement-plan.md` S6 读取契约）、TreeWalker `skills/loader.py`（站点级注入现状）。
> 职责划分：**TreeForge 定义数据契约与检索锚点（P4 已交付），TreeWalker 按本文 S1-S5 实现**；
> 本文是两端的对接基准。

## 一、背景与定位

站点级 skill 注入已在 WebArena 实测：任务成功率有效提升，但失败率仍高——符合预期：
站点卡（功能地图 + 通用知识）只解决「路盲」和跨任务共性，不解决「这个具体任务的流程
怎么走」。任务级 skill 补这一层：**检索命中即走已验证流程，未命中回落自主探索**
（蓝图路线三原话）。

**定位铁律（蓝图原文，不可违背）**：任务级 skill 是**产品口径**能力（企业重复流程的
RPA 化——命中高可靠、未命中测真实下限），**不是评测手段**。对自主探索评测口径，注入
任务级 skill 等价于泄露参考轨迹，属**作弊红线**：其 SR 禁止与任何评测口径（主口径 /
with-site-knowledge 口径 / 外部 leaderboard）混合或对比。评测纪律见 §八。

## 二、数据契约（v2 修订：host key 契约有断裂，TreeForge 侧修复见 §十一；其余已交付）

> v1 原表述「TreeForge 已交付，TreeWalker 只读」**作废**：评审发现两端 host 目录名
> 分叉（本节 `<host>` 是裸 hostname，TreeWalker 读端口限定 key `localhost_7780`），
> 契约本身有 bug。裁决与证据见 v2 §2.1；TreeForge 侧修复待办见 §十一。

### 目录布局

```
domain-skills/<host_key>/
├── _sop.md / selectors.md / quirks.md     # 站点级（常驻注入，现状）
└── tasks/<slug>/                          # 任务级（命中才注入，本方案）
    ├── _sop.md                            # 该任务的连贯流程叙事（step 1..N）
    ├── selectors.md / quirks.md           # 该任务的元素指纹 / 坑
    └── _task.json                         # 检索元数据（锚点）
```

**`<host_key>`（v2 钉死，两端统一）**：URL 的 hostname；URL 显式带端口时为
`host_port`（`_` 连接，Windows 目录名不能含 `:`）——即 TreeWalker
`extract_host_with_port`（`src/tree_walker/browser/url_utils.py:41-62`）的语义。
`http://localhost:7780/admin` → `localhost_7780`；`https://member.bilibili.com` →
`member.bilibili.com`。v1 的 `<host>`（裸 hostname）与 TreeWalker 现网读取 key
分叉，修复待办见 §十一。

### `_task.json` schema

| 字段 | 类型 | 用途 |
|---|---|---|
| `slug` | str | 任务标识（kebab-case，稳定——同任务重录覆盖同 slug） |
| `task_description` | str | 用户录制作业时的任务描述**原话**（检索主锚点） |
| `task_keywords` | str[] | 蒸馏时 LLM 提炼的关键词（≤5，站点语言，辅助锚点） |
| `source_traces` | str[] | 历次录制来源（可追溯，不参与检索） |
| `distilled_at` | ISO 时间 | 蒸馏时间（时效参考，v1 不做自动失效） |

### 实测规模（localhost，44 张卡）

| 指标 | 值 | 含义 |
|---|---|---|
| 单卡三件套均值 / 最大 | 2,052 / 2,661 chars | 命中注入的上下文成本极低（≤3k） |
| catalog 全量（slug+desc+keywords） | ~6.0k chars / 44 任务 | **整个 catalog 一次喂给匹配器毫无压力**——LLM-as-ranker 不需要任何预筛 |
| 卡片分布 | 全部在单一 host 下 | 候选域 = 当前 host 的 tasks/，天然第一道精度过滤 |

### 双卡分工（注入语义）

| | 站点级（host 卡） | 任务级（task 卡） |
|---|---|---|
| 内容 | 功能地图 + 跨任务共性 | 该任务的具体流程 + 该任务的坑 |
| 注入时机 | 进入该 host 即常驻 | **检索命中才注入** |
| 解决什么 | 路盲 / 共性控件 | 流程编排 / 步骤顺序 |

## 三、总体流程（TreeWalker 侧）

```
任务开始（拿到 user task 文本）
  → 读当前 host 的 tasks/ 目录 → 无卡？→ 仅站点级（现状，零改动路径）
  → 组 catalog（slug + description + keywords）
  → LLM-as-ranker 一次匹配调用（fast 模型）
      → match=<slug>：读该卡三件套 → 注入 [Task Skill] 块
      → match=null / LLM 失败 / 无任务文本：不注入（安全降级 = 现状）
  → agent 正常跑（命中卡是指引，不是脚本）
```

匹配在**任务开始时做一次**（不在每步重匹配）——任务文本在会话内不变，重匹配只浪费。

## 四、检索设计（核心：精度优先）

**候选域**：仅当前 host 的任务卡。跨 host 任务不匹配（产品场景也是「同站重复流程」）。

**匹配方法**：LLM-as-ranker（Browser-BC 哲学，本仓库「不做」清单：no embeddings）——
catalog 全量 + 用户任务文本 → 一次轻量调用（Haiku 级 / TreeWalker 的 fast 模型通道），
返回 `{match, confidence, reason}`。44 任务 catalog 仅 6k chars，几百任务内都不需要
分片或预筛；真到了千级再加首字母/关键词预筛（远期）。

**误命中比未命中更糟**（蓝图原话）——三条防护：

1. **保守匹配 prompt**（草案全文见附录 B）：只有「本质同一操作」才命中；
   近邻变体（如「数全部评论」vs「数待审评论」、「按 SKU 查」vs「按名称查」）
   默认**不命中**；拿不准返回 null。
2. **null 是一等答案**：未命中的代价只是回落探索（现状能力），误命中会带偏流程。
3. **匹配日志**：每次命中记 `{host, task, slug, confidence, reason}` 结构化日志
   （S4），评测后复盘误命中率——这是调 prompt 的唯一依据。

**降级路径**（全部等价于「不注入」）：无任务文本 / 目录无卡 / 匹配调用失败或 JSON
解析失败（一次重试后放弃）。任何异常不得阻断 agent 启动。

## 五、注入设计

- **新块 `[Task Skill]`**，位置：`[Task]` 之后、`[Domain Skill]` **之前**——任务流程
  比站点共性更贴当前目标，放前面让 LLM 优先按流程走。
- **内容**：命中卡三件套全文（实测均值 2.1k chars，预算无忧），头部加「指引非脚本」
  声明（草案见附录 B 末尾）。
- **读序**：`_sop → selectors → quirks`（与站点级 loader 一致）。
- **开关**：`AGENT_ENABLE_TASK_SKILL_INJECTION`（默认 off，独立于站点级
  `enable_skill_injection`——评测时可只开一个，分口径对照）。
- **缓存**：catalog 与命中卡按 host 磁盘缓存，对齐 `loader.py` 既有模式。

## 六、执行语义

- **skill 是上下文提示，不是可执行脚本**：agent 仍用自己的动作循环自主执行；命中卡
  给的是步骤编排与坑位提示，元素定位、等待、重试仍是 agent 自己的能力。
- **命中卡失准不特殊处理**：prompt 已声明「页面是事实之源，步骤与现实不符时自主
  调整」——agent 自然回落探索，与未命中同路径。不做「命中即锁定步骤」的硬执行
  （那是 rerun 重放那条线的事，与 skill 注入是两个机制）。

## 七、TreeWalker 实施步骤

- **S1 catalog 扫描**：`skills/task_loader.py`——扫 `domain-skills/<host>/tasks/*/_task.json`，
  解析 + 按 mtime 缓存；无 tasks/ 目录返回空。
- **S2 匹配器**：`match_task_skill(task_text, catalog) -> slug | None`——fast 模型 +
  附录 B prompt + 解析失败重试一次后降级 null。
- **S3 注入**：`system_prompt.py` 的 `build_state_message` 加 `[Task Skill]` 块
  （位置见 §五）；env 开关。
- **S4 匹配日志**：结构化落盘（命中/未命中都记，含 reason），供评测复盘。
- **S5 评测脚本分口径**：`run_full.ps1` 变体开关只控制注入 flag，口径命名见 §八。

预估工作量：S1-S3 一天量级（对齐 loader.py 既有模式），S4-S5 半天。

## 八、评测口径（红线落地）

| 口径 | 注入 | 性质 |
|---|---|---|
| A 主口径 | 无 skill | 现状基线，对外可比 |
| B with site knowledge | 仅站点级 | 变体分列报告（已实测） |
| C with task knowledge | 站点级 + 任务级 | **产品口径**：禁止与 A/B 或外部 leaderboard 混合/对比 |

口径 C 的两种**诚实测法**：

1. **同任务回放**（RPA sanity）：蒸馏任务 = 测试任务。预期很高（本来就该高），
   只验证「录过的任务能稳跑」，数字不对外。
2. **不相交泛化**（有信息量的数字）：蒸馏任务集 A 与测试任务集 B **不相交**
   （同站的变体任务，如蒸馏「按数量筛商品/按状态筛订单」，测「按价格筛商品/按日期
   筛订单」）。衡量任务知识的近邻泛化——若这个数字也好，说明任务级 skill 不止
   RPA。**注意**：近邻变体（§四的「不命中」区）在此口径下应判未命中回落探索，
   即测的是「不误命中 + 站点级兜底」，别和口径 C 的命中路径混淆。

配套指标：命中率 / 误命中率 / 漏命中率（从 S4 日志统计）——比 SR 更早暴露
检索质量问题。

## 九、风险与边界

| 风险 | 缓解 |
|---|---|
| 误命中带偏流程 | 保守 prompt + null 自由度 + host 域 + 日志复盘（§四） |
| 上下文膨胀 | catalog 6k/44 任务一次调用；命中卡 ≤3k chars；总量远小于 DOM 预算 |
| 卡片过期（站点改版） | v1 靠「指引非脚本」自适应 + 重新蒸馏；不做自动失效 |
| 近邻任务该不该命中 | v1 严格不命中（回落探索，站点级兜底）；「cousin 档匹配」留作后续实验，先拿日志说话 |
| 多 host 任务 | v1 仅当前 host；跨 host 匹配不做（产品场景不支持） |
| 匹配调用成本 | 每任务一次 fast 调用（毫秒级成本、千 token 级），可忽略 |

## 十、明确不做

- ❌ embedding / 向量检索（LLM-as-ranker 足够，沿「不做」清单）
- ❌ 跨 host 任务泛化匹配
- ❌ 命中即硬执行 / 步骤锁定（与 rerun 重放是两个机制，不混）
- ❌ TreeForge serve 运行时依赖（文件注入零依赖原则；TreeWalker 只读磁盘）
- ❌ 口径 C 与任何评测口径混合报告（作弊红线）

## 十一、TreeForge 侧待办（S0b，2026-09-05 评审修订——issue 素材）

> 由来：v1 评审发现 host key 契约断裂（P1）等 3 个 P1 + 5 个 P2，对接基准升级为
> TreeWalker `docs/p7/03-task-skill-loading-design.md`（v2，裁决见其 §2.1，部署终局
> 见其 §2.5）。存量 44 张卡已手工迁移到 TreeWalker 侧（S0a，2026-09-05，MD5 全量
> 校验一致），本次评测不再依赖本节；**本节是让「每次蒸馏后手工拷贝」这个常驻步骤
> 从此消失的 TreeForge 侧代码改动**。

### 待办 1：host key 对齐 `extract_host_with_port` 语义（核心）

**断裂证据**：TreeForge 按 `urlparse(url).hostname` 索引产物（localhost:7780 蒸出
`domain-skills/localhost/`），TreeWalker 按端口限定 key 读取（`localhost_7780`，
`agent.py:448-450` + `url_utils.py:41-62`）——本机服务的卡对消费侧不可见。无端口
host（bilibili/douyin）两端重合，所以只有带端口的 host 踩雷，而评测站恰是
localhost:7780。降级形态是**静默**的：TreeWalker 找不到目录 = 不注入 = 不报错。

**key 语义（两端统一，写死）**：URL 的 hostname；URL 显式带端口则 `host_port`
（`_` 连接）。实现 = 复制 TreeWalker `extract_host_with_port` 的纯函数逻辑
（stdlib urlparse，零依赖），建议抽成 `harness` 内共享函数。

**改动点盘点**（host 提取的源头在 ADAPT，bucket → registry → install 全链路共用；
只改源头，下游 key 自动一致）：

| 位置 | 角色 | 动作 |
|---|---|---|
| `harness/adapter.py:64-71` `_detect_host_from_url` | **`Trace.host` 源头**——产物目录 / registry / 任务卡 key 全从这来 | 换 key 函数（核心改动） |
| `tools/rerun_to_trace.py:152-157` `_host_from_url` | rerun→trace 转换工具的 host | 同步换 |
| `treeforge/capture/collector.py:32` `_extract_real_host` | 采集期写进 trace.json 的 host 字段——**host 模式累积再蒸馏按它收集** | 同步换 |
| `server/server.py:269` `_collect_host_traces` | 扫 trace.json 的 host 字段收集同 host traces | 存量兼容：按「新 key 或裸 hostname 旧形」双匹配（不改存量 captures 数据） |
| `adapters/treewalker_adapter.py`（`write_skills_merged` / `task_card_dir:150` / `list_task_cards:197`） | 读写 `domain-skills/<host>/` | key 从上游来，自身不改；docstring:20 的 hostname 表述更新 |
| `harness/registry.py:32` `card_path` | `registry/<host>.json` | key 从上游来，自身不改 |
| `harness/atomizer.py:61-74` `_registered_domain` | stage/segment 分组语义（**非**目录 key） | **不动**——`localhost_7780` 无点号，按现逻辑原样返回，已推演无影响 |

**存量迁移（与代码同批，两半缺一不可）**：

1. `data/skills/domain-skills/localhost/` → `localhost_7780/`（44 卡 + 三件套；
   TreeWalker 侧同名目录已存在 S0a 迁移件，重蒸馏覆盖即可）；
2. `data/skills/registry/localhost.json` → `localhost_7780.json`——**漏掉这半边**，
   `load_card` 找不到 prev，下一次增量蒸馏的 `distill_version` 静默归 1（又一个
   安全降级形态的静默失效）。

**测试**：localhost:7780 trace 蒸馏 → 三处 key 一致（skills 目录 / registry 文件名 /
`tasks/` 子目录）；无端口 host 产物路径回归不变；显式默认端口（`http://x:80/`）
行为与 TreeWalker 逐字对齐（`parsed.port` 非 None 即后缀）；slug 复用清单
（`list_task_cards`）在新 key 下可读到旧卡；host 模式再蒸馏对存量裸 hostname
captures 仍能收集到（双匹配）。

### 待办 2：蒸馏 prompt 易变值规则

**证据**：`tasks/count-pending-reviews/_sop.md` 第 5 步「`N records found`（本例为
5）」——录制时的答案值固化进卡。站点数据漂移后是错的；评测同任务回放会被
parrot（读型任务测成阅读理解，v2 §六）。

**改动**：`_TASK_PROMPT_TEMPLATE`（站点级模板顺带检查）加规则：易变结果值
（计数 / 金额 / 日期 / 查询结果）不写死；确需示例时显式标注「录制时示例，以页面
当前值为准」。测试对齐现有 prompt 契约断言模式（断言模板含该规则文本）。

### 待办 3（近零代码）：蒸馏直装 runbook（v2 §2.5 形态 A）

`distill --output <TreeWalker 仓库根>` 即「蒸馏完成 = 安装完成」（`--output` 已
支持，产出 `<output>/domain-skills/<host_key>/`）。配套两行：TreeWalker 仓库
`.gitignore` 加 `registry/`（P4 registry 落 output 下，别把运行态 registry 提交进
消费仓库）；README 补直装用法一句。**前置**：待办 1 落地后才对 localhost 站点生效
（key 对齐；无端口 host 今天即可用）。

### 验收

- 蒸馏 localhost:7780 trace → 产物出现在 `domain-skills/localhost_7780/`（含
  `tasks/<slug>/`），registry 卡为 `localhost_7780.json`；
- 重蒸馏同任务 → slug 覆盖语义不破（P4 决策 9 既有测试全绿）；
- bilibili / douyin（无端口）产物路径回归不变；
- 直装一次到 TreeWalker 仓库根，TreeWalker 侧下一个 run 即读到新卡（零拷贝）；
- 任务卡 SOP 人工抽检：无未标注的写死答案值。

## 附录 A：实测数据（localhost，2026-09-05）

44 张任务卡（Magento Admin 测试站，WebArena 评测任务录制蒸馏）：单卡三件套均值
2,052 chars（最大 2,661）；catalog 全量 5,977 chars。`_task.json` 示例：

```json
{
  "slug": "count-pending-reviews",
  "task_description": "What is the total count of Pending reviews amongst all the reviews?",
  "task_keywords": ["Pending", "评价", "待审核", "数量", "reviews"],
  "source_traces": ["data\\captures\\c2f9582c\\trace.json"],
  "distilled_at": "2026-08-31T20:25:29.864244+00:00"
}
```

## 附录 B：匹配 prompt 草案（全文，双语任务均适用）

```
You are a task-matching judge. Given a user task and a catalog of recorded task skills,
decide which recorded task is ESSENTIALLY THE SAME operation as the user task.

Rules:
- Match ONLY if a recorded task has the same goal on the same kind of target object
  (e.g. "count products with 0 quantity" matches a card describing exactly that).
  Surface wording may differ (synonyms, language).
- "Similar but different" is NOT a match: different filter dimension (by SKU vs by name),
  different object (orders vs invoices), different output (count vs list vs detail).
- When in doubt, return null — a wrong match is worse than no match; the agent will
  explore fine on its own.

User task:
{task}

Catalog (same site):
- `slug` — {description} | keywords: {keywords}
...

Return STRICT JSON only:
{"match": "<slug>" | null, "confidence": "high" | "medium" | "low", "reason": "<一句话依据>"}
```

命中卡注入头部声明（置于 `[Task Skill]` 块首）：

```
A recorded task matching your current goal was found (slug: {slug}). It describes a
PROVEN flow for essentially this task — follow it as guidance. The live page is the
source of truth: if any step no longer matches reality, adapt and explore on your own.
```
