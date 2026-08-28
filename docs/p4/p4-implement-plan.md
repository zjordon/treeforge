# P4 实施方案——站点级 + 任务级双产物（多任务累积蒸馏）

> 状态：计划中（待实施）。
> 关联：ROADMAP.md `P4` 章节、issue 待建、TreeWalker 优化蓝图路线二
> （`D:\dev\git\z_jordon\evals\webarena\docs\treewalker-optimization-blueprint.md`）。
> 前置调研：本文「二、现状盘点」全部为代码核实事实（file:line）。
> 2026-08-28 修订：融入「一次蒸馏双产物（站点级 + 任务级）+ 蒸馏前任务描述」——
> 任务描述作 TreeWalker 侧未来语义检索的锚点，同时反哺蒸馏意图。

## 一、背景与动机

TreeForge 任务级 skill（录一遍任务 → 蒸馏 SOP → 文件注入）已闭环。蓝图路线二要的是
**站点级**：同一站点多任务累积蒸馏出「布局 / 菜单 / 功能地图 / 典型操作序列」，消
TreeWalker agent 的导航不确定性（「路盲」：88 个失败约一到两成卡在找不到入口反复摸索，
27% 翻转不稳的一半来自导航路径随机）。

参照 Browser-BC：其五阶段管线（atomize → classify 到 capacity → bucket 归并 → distill）
本质就是「多次操作累积成知识」的机器，TreeForge 已继承并在**单 trace 内**用了 host 级
合并；P4 把它用到**跨 trace** 维度。三个分叉点保持不变：站点特定（vs 去站点化）、
文件注入（vs MCP 检索）、host 归并（vs domain::capacity）。

**双产物定位（2026-08-28 融入）**：一次蒸馏同时产出**站点级累积卡**与**任务级独立卡**——
站点级消「路盲」（路线二），任务级是蓝图路线三的产品化原料（每任务一份 SOP，未来
TreeWalker 侧语义检索命中即走流程）。关键认知：现有单 trace 蒸馏产物本来就是任务级
形态，P4 不是「新增」任务级而是「别丢掉」它；用户操作一次，管线内部两次 LLM 调用
各自聚焦。蒸馏前可选输入**任务描述**（双用途：检索锚点 + 反哺蒸馏意图）。

## 二、现状盘点（代码事实）

| 事实 | 位置 | 含义 |
|---|---|---|
| 增量蒸馏是占位 | `harness/distiller.py:613-620`：`prev_sop = ""` + `"(previous skill not available in P0)"` | `_INCREMENTAL_ADDENDUM` 模板在但没接真数据 |
| registry 是空壳 | `harness/registry.py`（38 行）：`load_registry` 返 `[]`，`save_registry` 抛 NotImplementedError；全仓只有注释引用（distiller.py:616 / config.py:49 / `harness/__init__.py:13`），无任何调用方 | 可安全重建，测试无引用 |
| 产物每次覆盖 | `adapters/treewalker_adapter.py:71-98` `write_skills_merged`：每次蒸馏对 `domain-skills/<host>/` 三件套 `atomic_write_text` 原子覆盖 | 「多次录制互相覆盖」坐实——旧知识随 md 覆盖丢失 |
| 版本计数来源 | `harness/models.py:144-145` `Bucket.distill_version/last_distilled_at` 恒为 0（bucketer 每次新建 bucket，无持久化回填） | 累积版本的真源必须挪到 registry 卡片 |
| 管线入口单 trace | `server/distill_api.py:49-99` `run_distill_pipeline(trace_path, ...)`：ADAPT→ATOMIZE→CLASSIFY→BUCKET→DISTILL→INSTALL | 多 trace 在此扩展 |
| CLI 单 trace | `treeforge/__main__.py:164-186`：`distill` 子命令 `trace` 位置参数单个 | 改 `nargs="+"` |
| classify 兼容拼接 | `harness/classifier.py:139` `classify(segments, *, use_llm, existing)` 收扁平 Segment 列表 | 多 trace 逐个 ADAPT+ATOMIZE 后拼接 segments 即可 |
| TreeWalker loader 硬编码三文件名 | TreeWalker `skills/loader.py:17-21`（`_sop → selectors → quirks` 固定读序） | **不能新增第 4 个文件**（P3.7 边界：不动 TreeWalker 端）——站点地图必须长在 `_sop.md` 内部结构里 |
| 任务描述字段全链路已有但恒空 | `treeforge/capture/collector.py` `CaptureSession.task_instruction`（`/start` config 传入）；实测 captures 各 trace.json 该字段全为空（popup 无输入 UI） | 采集/trace 链路零改动——缺的只是输入入口 + 蒸馏侧消费 |
| loader 不递归子目录 | 同上（按固定文件名直读，无目录扫描） | `tasks/` 子目录放任务卡**不污染现有注入**，安全 |

## 三、关键设计决策

1. **registry 存哪**：`output_dir/registry/<host>.json`（每 host 一文件，跟着 skills 走）。
   不用 `config.STATE_DIR`（`data/harness/`，与产物分离，换 output_dir 会失联）。
   单文件 JSON + `atomic_write_text`（复用 `install.py` 的 tmp + os.replace），无并发问题（单用户工具）。
2. **不新增产物文件**：TreeWalker loader 只认三件套。站点地图 = `_sop.md` 的**必写开头段**
   （「## 站点功能地图」），不是新文件。
3. **版本真源挪到 registry**：`meta.distill_version` 以 registry 卡片为准（`prev + 1`）；
   bucket 内的 `distill_version` 保持现状（单 trace 内语义，恒 0+1）。
4. **模板模式（--no-llm）不累积**：无 LLM 无法合并知识。模板模式跳过 registry 读写
   （只覆盖 md），progress 提示「模板模式不累积；配置 LLM 后重蒸馏可增量」。避免把
   低质模板卡喂给下一次真 LLM 蒸馏。
5. **多 trace page_context 冲突**：不同任务的 stage 同名（如两次都有 `upload`）会让
   trace2 的事件指到 trace1 的快照。合并 helper 对冲突 key 重命名 `stage@N`
   （N=trace 序号，从 2 起），并同步重映射该 trace 所有 `TraceEvent.stage`——在
   ADAPT 后、ATOMIZE 前做（stage 是 evidence，宁可名字丑不可指错快照）。
6. **冲突以新证据为准**：沿用 `_INCREMENTAL_ADDENDUM` 既有规则（Keep 正确的 / Remove
   被新证据否定的 / Add 新发现），addendum 扩展后明确写出。
7. **检索函数删除**：`query_top_k` / `synthesize_playbook` 随重建移除（检索层 P4 旧编号
   已明确不做），模块 docstring 重写为「SkillCard 按 host 持久化存储」。
8. **一次蒸馏双产物，两次 LLM 调用、两套 prompt**：用户操作一次，管线内部跳 A
   （站点级增量）+ 跳 B（任务级独立）两次调用各自聚焦。不合并成一次——站点级要抽象
   地图、任务级要具体流程，取向不同塞一个 prompt 会互相干扰；多一次调用的成本可接受。
   任务级沿用现行单任务 spec（无站点地图段），`--no-llm` 模板模式双产物同退模板。

   两套 prompt 的差异（演进关系：现行 `_DISTILL_PROMPT_TEMPLATE` 分家——升级为
   站点级专用，同时派生任务级模板）：

   | 维度 | 跳 A 站点级（改造 `_DISTILL_PROMPT_TEMPLATE`，S3） | 跳 B 任务级（新建 `_TASK_PROMPT_TEMPLATE`，S4） |
   |---|---|---|
   | sop_md spec | 必写「站点功能地图」开头段 + 按 capacity 分组的典型操作序列 | 现行单任务连贯流程叙事（无地图段） |
   | 增量 addendum | 有（prev 三文件块 + 地图合并指令，S2） | 无（任务卡每次独立，同 slug 覆盖） |
   | 任务描述注入 | 不注入（站点级与单任务意图无关） | 有「# Task description」段（反哺意图，决策 10） |
   | 现有任务卡清单注入 | 无 | 有（slug+description 清单，同任务重录**复用 slug 覆盖**，决策 9） |
   | 返回 schema | skill_name / scope / 三 md 字段 | 同左 + `task_slug` + `task_keywords` |
   | 证据输入 | 多 trace 合并后的 host 全量 buckets（S5） | 本次 trace 的 buckets |
   | 共用部分 | `_DISTILL_SYSTEM` / `_CONSUMER_CONTEXT` / evidence 与 page_context 渲染——抽公共渲染函数复用，**不复制两份** | 同左 |
9. **任务卡存放与 slug 稳定化（同任务重录必须覆盖旧卡）**：任务卡存
   `domain-skills/<host>/tasks/<slug>/`（三件套 + `_task.json` 元数据）。覆盖的前提是
   slug 跨次稳定，纯靠 LLM 从描述生成**不可靠**（同任务不同措辞可能生成不同 slug；
   无描述回退 trace 目录名——session uuid 必然不同 → 永不覆盖、陈旧卡无限堆积）。
   **稳定化机制**：跳 B prompt 注入该 host **现有任务卡清单**（slug + description），
   指示 LLM「若本次任务与已有卡语义相同（同一任务重录），必须**复用其 slug**（覆盖
   更新）；确为新任务才生成新 slug」（kebab-case ≤5 词）——LLM-as-ranker 式一致性
   判定，有参照物跨次稳定。host 无现有任务卡时才走「描述生成 / 回退 trace 目录名」。
   覆盖时 `_task.json` 的 `source_traces` 与旧卡**并集追加**（历次录制来源可追溯）。
   TreeWalker loader 按固定三件文件名读、不递归子目录（现状盘点），`tasks/` 不污染
   现有注入；该目录约定即未来 TreeWalker 侧检索的读取契约。
10. **任务描述可选、双用途**：① 检索锚点——进任务卡元数据（`task_description` +
    LLM 顺手提炼 `task_keywords`），供 TreeWalker 语义匹配（LLM-as-ranker，无
    embedding——检索由 TreeWalker 侧未来实现，TreeForge 只存锚点）；② 反哺蒸馏——
    喂进任务级 prompt，LLM 从「猜意图」变「知意图」。入口：serve SPA 蒸馏表单文本框 +
    CLI `--task`；扩展端不动（避免重新构建发布；未来要在录制前填，popup 加输入框即可，
    `/start` config 链路已支持）。优先级：显式参数 > trace 自带 `task_instruction`。

## 四、实施步骤

### S1 registry 重建（SkillCard 按 host 持久化）

重写 `harness/registry.py`（模块 docstring 同步重写）：

```python
def card_path(output_dir: Path, host: str) -> Path:
    """output_dir/registry/<host>.json"""

def load_card(output_dir: Path, host: str) -> dict[str, Any] | None:
    """读 host 卡片；文件缺失 / JSON 损坏返 None（容错，不阻断蒸馏）。"""

def save_card(output_dir: Path, card: SkillCard, trace_sources: list[str]) -> None:
    """原子写卡片；trace_sources 与已有Sources 并集去重追加。"""

def list_hosts(output_dir: Path) -> list[str]:
    """列已持久化的 host（按 mtime 新→旧）。"""
```

卡片 JSON schema：

```json
{
  "host": "creator.douyin.com",
  "skill_name": "...",
  "scope": "...",
  "sop_md": "...",
  "selectors_md": "...",
  "quirks_md": "...",
  "meta": {
    "distill_version": 3,
    "distilled_at": "2026-08-23T...",
    "model": "glm-5.2",
    "capacities": ["upload", "edit-meta"],
    "usage": {}
  },
  "trace_sources": ["data/captures/8161dae4/trace.json", "..."]
}
```

测试（新 `tests/test_registry.py`）：save/load 往返、list_hosts 排序、原子写不残留 tmp、
损坏 JSON 返 None、trace_sources 并集去重。

### S2 host 级增量蒸馏接通

**distiller.py**：

- `distill_host(host, buckets, *, use_llm=None, page_context=None, prev_card: dict | None = None)`
- 替换 613-620 行占位块：`if prev_card and use_llm:` 时用真数据填 addendum
  （`prev_version` 取 `prev_card["meta"]["distill_version"]`）。
- `_INCREMENTAL_ADDENDUM` 模板扩展（原只有 `prev_sop`）：

  ```
  # EXISTING KNOWLEDGE (distill_version {prev_version})
  合并规则：Keep 仍正确的 / Remove 被新证据否定的 / Add 新发现 / 不重复；
  冲突以新证据为准。

  Previous `sop_md`（含站点功能地图，截断 {sop_budget} 字符）：
  Previous `selectors_md`（截断 {sel_budget} 字符）：
  Previous `quirks_md`（截断 {quirk_budget} 字符）：
  ```

  预算：sop 8000 / selectors 3000 / quirks 4000（原 P3 延后项口径「8000 截断」为 sop 主预算）。
- `meta.distill_version`：`prev + 1`（有 prev_card 时），否则维持 `merged.distill_version + 1`。
- `distill_buckets(buckets, *, use_llm=None, page_context=None, prev_cards: dict[str, dict] | None = None)`
  ——按 host 透传。

**distill_api.py（管线编排）**：

- BUCKET 后、DISTILL 前：`use_llm 且非 fresh` 时按 bucket 出现的 host 逐个
  `registry.load_card(output_dir, host)` 组 `prev_cards`（懒加载，host 少时开销可忽略）。
- DISTILL 成功后、INSTALL 前：每张卡 `registry.save_card(output_dir, card, trace_sources)`。
- 模板模式：跳过 load/save，progress 提示（决策 4）。

测试（`tests/test_distiller.py` / 新 `tests/test_distill_incremental.py`）：
mock LLM 捕获 prompt——含 prev sop/selectors/quirks 片段；版本 `prev+1`；
无 prev_card 时 prompt 不含 addendum（回归）；冲突规则文案在 addendum 里。

### S3 站点地图产物形态（长在 _sop.md 里）

`_DISTILL_PROMPT_TEMPLATE` 的 `sop_md` spec 调整（单模板，无分支）：

- **必写开头段 `## 站点功能地图（Site Function Map）`**：入口 URL 模式（含 SPA 路由）、
  主要菜单 / 功能区清单（每个区一句话：在哪、通向什么任务）。单任务蒸馏时此段薄
  （只有本次任务涉及的入口）——无害，且为累积打底。
- 之后 `## 典型操作序列`：按 sub-capacity 分组的步骤剧本（现有「连贯叙事 + 可按
  sub-capacity 分组」的 spec 收紧为「按 sub-capacity 分组」）。
- addendum 加一条合并指令：**合并上一版站点功能地图**——仍有效的入口/功能区保留、
  新任务发现的新区补入、被新证据否定的移除。

测试：prompt 契约测试加断言（`Site Function Map` / `典型操作序列` 在模板里；addendum
含地图合并指令）。

### S4 任务级双产物（task 卡 + 任务描述消费）

**管线（`distill_api.py`）**：

- `run_distill_pipeline(..., task_description: str | None = None)`。描述优先级：
  显式参数 > trace 自带 `task_instruction`（现状盘点：字段全链路已有）。
- 蒸馏段拆两跳（决策 8），同一次管线先后跑：
  - **跳 A 站点级**：`distill_host(..., prev_card=...)`（S2）→ host 累积卡 + registry
    落卡 + INSTALL 三件套（流程不变）；
  - **跳 B 任务级**：新 `distiller.distill_task(host, buckets, *, task_description,
    use_llm, page_context)` → 任务卡。
- 任务卡落盘：`adapters/treewalker_adapter.py` 加模块函数
  `write_task_card(output_dir, host, slug, card, task_meta) -> Path`——写
  `domain-skills/<host>/tasks/<slug>/` 三件套（复用 `_FILES` / `atomic_write_text` /
  `_ensure_header`）+ `_task.json`（slug / task_description / task_keywords /
  source_traces / distilled_at）。输出形态归 adapter 管（install.py 既有约定），
  不走 INSTALL 合并路径（任务卡独立，不并入 host 三件套）。
- slug（**同任务重录覆盖的关键**）：跳 B prompt 注入现有任务卡清单（slug +
  description，决策 9）——LLM 判定与已有卡语义相同则**复用旧 slug**（覆盖更新），
  确为新任务才生成新 `task_slug`（英文 kebab-case ≤5 词，从描述提炼）；host 无现有
  任务卡且无描述 → 首个 trace 目录名。覆盖时 `_task.json` 的 `source_traces` 与旧卡
  **并集追加**，`distilled_at` 刷新。
- `DistillResult` 扩展：`task_dir: Path | None` / `task_slug: str | None`。

**distiller.py**：新 `_TASK_PROMPT_TEMPLATE`——现行单任务 spec 为主干（连贯流程叙事，
**无**站点地图段——地图只长在 host 卡），有描述时加「# Task description（用户意图，
蒸馏时参考）」注入段；另有「# Existing task cards」段注入现有任务卡清单（slug +
description + 复用/新建判定指示，决策 9——清单空则省略该段）；返回 schema 加
`task_slug` / `task_keywords`（≤5 个，中文或站点语言）。`--no-llm` 模板模式：任务卡
同退模板（描述照存，slug 用 trace 名，keywords 空；模板模式无 LLM 做不了复用判定，
每次新卡——progress 提示配 LLM 可启用覆盖合并）。

测试（新 `tests/test_distill_task.py`）：mock LLM 捕获——任务 prompt 含描述 / 不含站点
地图段 / 含现有任务卡清单（有卡时）；`task_slug` / `task_keywords` 解析；**slug 复用
判定**（prompt 清单含旧卡时 LLM 返回旧 slug → 覆盖而非新增）；slug 回退（无描述 →
trace 目录名）；覆盖时 `source_traces` 并集；`_task.json` schema 断言；同次管线
host 卡 + 任务卡都落盘；`--task` > trace `task_instruction` 优先级。

### S5 多任务工作流

**CLI（`__main__.py`）**：

- `p_distill.add_argument("trace", type=Path, nargs="+", ...)`（1+ 个 trace）；
  逐个校验存在性。
- 新增 `--fresh`：忽略 registry 旧卡从头蒸馏（默认增量）。
- 新增 `--task <描述>`：任务描述（可选，进任务卡元数据 + 任务级 prompt；缺省用 trace
  自带 `task_instruction`）。
- `_run_distill(trace_paths, ...)` 薄包装透传。

**管线（`distill_api.py`）**：

- `run_distill_pipeline(trace_paths: list[Path] | Path, output_dir, adapter_name,
  no_llm, fresh=False) -> DistillResult`（单 Path 兼容旧调用）。
- 多 trace：逐个 `load_trace` + ADAPT；**stage 冲突重映射**（决策 5）——helper
  `_merge_traces(traces) -> (events?, page_context)`：对第 N≥2 个 trace，其
  page_context 与已合并 key 冲突者改 `stage@N` 并同步改该 trace 全部
  `TraceEvent.stage`；然后各自 ATOMIZE，segments 拼接 → CLASSIFY → BUCKET 一次。
- `DistillResult.trace_paths: list[Path]`；保留 `trace_path` property 返首个（兼容
  旧测试 / job dict 字段）。

**serve（`server/server.py` + SPA）**：

- `DistillRequest`：`trace_path: str | None = None` + `host: str | None = None` +
  `task_description: str | None = None`（trace/host 二选一校验不变）。
- host 模式：扫 `captures_dir/*/trace.json` 的 `host` 字段收集同 host 全部 trace →
  走多 trace 管线（无匹配 → 400 带提示）。
- SPA：产物树 host 节点加「累积再蒸馏」按钮 → `POST /api/distill {host}`；蒸馏表单加
  「任务描述（可选，存进任务级 skill 供检索）」文本框 → `task_description`；job 列表
  沿用现有进度 / 产物展示（新增仅一按钮 + 一文本框）。

测试（`tests/test_serve.py`）：host 模式收集逻辑（mock 目录）、参数二选一校验、
`task_description` 透传进管线、多 trace 管线产出单 host 卡（两份 fixture trace）。

### S6 评测对接与约定（说明性，无代码）

- 注入目录即 `output_dir/domain-skills/`（现状不变）；任务卡目录
  `domain-skills/<host>/tasks/<slug>/`（含 `_task.json`）即未来 TreeWalker 检索的
  读取契约——语义匹配用 `task_description` / `task_keywords`（LLM-as-ranker，无
  embedding；TreeWalker 侧实现，TreeForge 只负责存好锚点）。
- README「快速开始」补多 trace 用法 + 累积语义 + `--task` 各一句；TreeWalker 侧
  "with site knowledge" 变体口径由蓝图自行约定（分列报告，不进主口径）。

## 五、验证（每步 + 全量）

- 每步：`uv run python -m pytest tests/<相关文件> -v` + `uv run ruff check .` + `uv run ruff format .`
- 全量：`uv run python -m pytest tests/`（234 现有测试不破 + 新增）
- 端到端冒烟（真 LLM，手动）：
  1. `uv run treeforge distill data/captures/8161dae4/trace.json --output ./data/skills
     --task "上传并发布抖音视频"` → registry 出 host 卡（version 1）+ `_sop.md` 含
     站点功能地图段 + `tasks/<slug>/` 任务卡（`_task.json` 含描述与 keywords）；
  2. 录制 douyin 第二个不同任务（如「查看作品数据」），带描述再蒸 → host 卡
     version 2（功能地图叠加 / 典型序列两组，冲突以新证据为准）+ 第二张任务卡
     （不同 slug，不覆盖第一张）；
  3. **同一任务不同时间段重录**（描述措辞可不同，如「传视频」vs「上传发布视频」）
     再蒸 → 任务卡**覆盖**（复用同 slug，不新增第二张），`_task.json` 的
     `source_traces` 累积两条录制来源；
  4. `--fresh` 重蒸 → host 卡 version 归 1；不带 `--task` 蒸 → 任务卡描述回退 trace
     自带 `task_instruction`（空则留空，不阻断）。
- 回归口径：单 trace 单卡行为不变；模板模式产物与现状等价（任务卡同退模板）。

## 六、改动文件清单

- 改：`harness/registry.py`（重建为卡片存储）、`harness/distiller.py`（增量接通 +
  addendum 扩展 + sop spec 站点地图 + `distill_task` / `_TASK_PROMPT_TEMPLATE`）、
  `adapters/treewalker_adapter.py`（加 `write_task_card` 模块函数）、
  `server/distill_api.py`（多 trace + registry 编排 + 双产物两跳 + `--fresh` +
  `task_description`）、`treeforge/__main__.py`（nargs + 校验 + `--fresh` + `--task`）、
  `server/server.py`（DistillRequest host 模式 + task_description）、
  `server/app/dist/index.html`（累积再蒸馏按钮 + 任务描述文本框）、
  `harness/config.py`（STATE_DIR 注释里 registry.json 措辞，顺手）
- 新增测试：`tests/test_registry.py`、`tests/test_distill_incremental.py`、
  `tests/test_distill_task.py`
- 更新：`tests/test_distiller.py`（契约断言）、`tests/test_serve.py`（host 模式 +
  task_description 透传）
- 文档：README 多 trace + `--task` 用法（各一句）

## 七、明确不做（沿 ROADMAP P4 边界）

- ❌ 检索实现（`query_top_k` / `synthesize_playbook` 随 registry 重建删除；TreeForge
  只存任务描述 / 关键词锚点，语义匹配检索由 TreeWalker 侧未来实现——LLM-as-ranker，
  无 embedding）
- ❌ 新增第 4 个产物文件（TreeWalker loader 硬编码三件名，不动 TreeWalker 端——
  任务卡是**子目录**不是第 4 文件，不违反此条）
- ❌ 去站点化通用 skill、工具层能力、向量检索
