# TreeForge 后续工作计划：skill 形态对齐 TreeWalker

> 状态：执行计划。本文承接 `skill-format-alignment.md`（skill 形态调整方案），
> 给出"现状 → 目标 → 分步动作"的可执行清单。
>
> 约束：本文档只规划动作，不产出代码。代码改动由用户在 TreeForge 工程内执行。

---

## 一、现状盘点

### 已实施（P0 完成）

| 组件 | 现状 | 要不要改 |
|---|---|---|
| `harness/distiller.py` prompt | 产出 CSS selector 表（`selector \| what it is \| notes`） | **改**——核心调整点 |
| `harness/adapter.py` / `atomizer.py` / `classifier.py` / `bucketer.py` | 五阶段管线骨架 | 不改（与 skill 格式无关） |
| `adapters/treewalker_adapter.py` | 输出四文件（_sop/selectors/quirks/api）到 `<host>/` | **改**——selectors.md 内容格式 |
| `harness/llm.py` | urllib 双协议客户端 | 不改 |
| `examples/bilibili-upload.trace.json` | 手写假数据 | **替换**——用 TreeWalker 真实数据反推 |
| `examples/github-login.trace.json` | 手写假数据 | 同上（可后做） |
| 测试（test_distiller 等） | mock LLM 测产出 | **改**——断言新格式 |

### 未实施

| 组件 | 原计划 | 影响 |
|---|---|---|
| 采集层（P2） | MV3 扩展 | **还没开发反而是好事**——可以直接按新格式（白名单属性）设计采集，不用回退 |
| 接入层（P1） | FastAPI | 缓做（不变） |

### 关键约束：拿不到真实 trace

采集层没开发，所以拿不到 TreeForge 自己的 trace。**但能拿到 TreeWalker 给模型的文本**（`D:/temp/tree-walker-model-input/bili/`），这是 TreeWalker 真实看到并喂给 LLM 的 DOM 文本。可以**反推**出 skill 该写什么——因为 skill 的目标就是"让模型在这个 DOM 文本里能找到对应元素"。

---

## 二、目标状态

调整后的 TreeForge 应该产出这样的 skill（以 B 站为例）：

```
domain-skills/bilibili.com/
├── _sop.md          # 流程骨架（自然语言描述步骤，不写 CSS selector）
├── selectors.md     # ★元素描述表（用途 + 怎么找到它 + 白名单属性 + 可见文本）
├── quirks.md        # 怪癖（contenteditable / 隐藏 file input / 折叠区 / accept 格式）
└── api.md           # API/URL 模式（基本不变）
```

`selectors.md` 的格式从 CSS selector 表 → 元素描述表（详见 `skill-format-alignment.md` §三）。

---

## 三、分步动作（按依赖顺序）

### 阶段 0：手写新格式 skill + A/B 验证（不依赖 TreeForge 任何代码，最高优先级）

> 这是 ROADMAP P1 的核心判据。在改任何 TreeForge 代码之前，先用最小代价验证"新格式 skill 能不能提升 TreeWalker 探索成功率"。

**输入**：TreeWalker 模型输入文本（`D:/temp/tree-walker-model-input/bili/` 三个文件）

**动作**：

1. **精读三个模型输入文件**，提取 B 站上传流程的关键元素：
   - `upload.txt`（上传初始页）：投稿入口、视频 file input
   - `upload-conver.txt`（封面/信息编辑页）：标题、分区、标签、简介、立即投稿
   - `publish.txt`（发布前页）：创作声明、定时发布、更多设置

2. **手写新格式 skill**（放到 TreeWalker 的 `domain-skills/www.bilibili.com/`，不是 TreeForge）：
   - `selectors.md`：按"元素用途 / 怎么找到它 / 稳定标识（白名单属性+可见文本）/ 备注"四列写
   - `quirks.md`：记 contenteditable 简介、隐藏 file input、折叠区等
   - `_sop.md`：自然语言流程（不写 selector）
   - 每个元素的"稳定标识"必须能在模型输入文本里搜到（对照三个 txt 文件验证）

3. **A/B 实测**（需 TreeWalker 的 skill 注入机制落地，见 TreeWalker `docs/skill-injection-design.md`）：
   - 无 skill：TreeWalker agent 跑 B 站上传 N 次
   - 有 skill：同样跑 N 次
   - 对比成功率/步数

**判据**：成功率提升 ≥ 20pp 或步数减少 ≥ 30% → 新格式成立，继续阶段 1

**为什么先做这个**：如果新格式 skill 模型用不上（提升不显著），改 TreeForge 代码白费。先验证方向，再投入改造。

### 阶段 1：调整 distiller prompt + 模板 fallback（TreeForge 代码改动）

> 阶段 0 验证成立后，让 TreeForge 自动产出阶段 0 手写的新格式 skill。

**动作**：

1. **改 `harness/distiller.py` 的 prompt**：
   - `selectors_md` 的产出要求从 CSS selector 表 → 元素描述表
   - 明确禁产出 CSS selector（`不要产出 .class-name 或 div > span`）
   - 明确要求列白名单属性（给 LLM 一份允许的属性清单：id/name/type/placeholder/aria-label/role/data-testid/data-test/data-cy/visible text）
   - 要求"怎么找到它"用自然语言描述位置和上下文

2. **改 `harness/distiller.py` 的模板 fallback**（`--no-llm` 模式）：
   - 现在模板直接把 trace 的 selector 列进表（`skill-format-alignment.md` 证实这部分对不上）
   - 改成：从 trace 提取元素属性（非 selector），按元素描述表格式渲染

3. **改 `adapters/treewalker_adapter.py`**（如需要）：
   - 确认四文件输出路径不变
   - selectors.md 的内容来自 distiller 的新格式产出（可能要调整字段映射）

4. **改测试**：
   - `test_distiller.py` 的 mock LLM 返回改成新格式，断言产出是元素描述表
   - 断言不含 CSS selector 模式（`.xxx` / `div > span`）

### 阶段 2：替换示例 trace（用真实数据反推）

> 现有 `examples/bilibili-upload.trace.json` 是假数据，要换成能蒸馏出正确 skill 的数据。

**问题**：没有采集层，拿不到真实 trace。

**解法**：从 TreeWalker 模型输入文本**反推** trace。TreeWalker 的 DOM 文本（`[index]<tag attr=val /> text`）保留了元素属性和文本，可以人工把它转成 trace 格式：

```
TreeWalker DOM: [142]<a id=nav_upload_btn /> 投稿
    ↓ 反推成 trace event
{
  "type": "click",
  "target": "投稿按钮",
  "selector": null,              // 不再用 selector
  "element_attrs": {             // 新增：白名单属性
    "id": "nav_upload_btn",
    "tag": "a",
    "visible_text": "投稿"
  },
  "url": "https://www.bilibili.com/",
  "timestamp": 1200
}
```

**动作**：

1. **扩展 trace 格式**：`harness/models.py` 的 Trace/SkillCard 模型加 `element_attrs` 字段（白名单属性 dict），替代纯 selector
2. **重写 `examples/bilibili-upload.trace.json`**：对照三个模型输入文件，人工反推真实元素的 trace
3. **改 `harness/adapter.py`**：ADAPT 阶段读 `element_attrs`（不只读 selector）
4. **验证**：`uv run treeforge distill examples/bilibili-upload.trace.json` 蒸馏出的 skill，元素描述能和模型输入文本对上

### 阶段 3：采集层设计对齐（P2，未来）

> 采集层还没开发，可以直接按新格式设计，不用回退。

**动作**（P2 实施时）：

1. **采集时记录白名单属性**：content script 采集元素时，不只记 selector，要记 STATIC_ATTRIBUTES 子集（id/name/type/placeholder/aria-label/role/data-*/visible text/contenteditable）
2. **借鉴 TreeWalker record-replay 采集端**：`recording_extension/capture/selector.ts` 的 `buildElementRef` 已经采集了 tag/id/classes/role/name/text/selector/xpath/rect——可以参考
3. **trace 格式直接用 `element_attrs`**：采集产出的 trace 天然带白名单属性，distiller 直接消费

---

## 四、动作顺序与依赖

```
阶段 0（手写 skill + A/B 验证）
    ↓ 验证成立
阶段 1（改 distiller prompt + 模板 + 测试）
    ↓ 自动产出新格式
阶段 2（反推真实 trace + 替换示例）
    ↓ 蒸馏验证对得上
阶段 3（采集层按新格式设计，P2）
```

**关键依赖**：阶段 0 的 A/B 验证依赖 TreeWalker 的 skill 注入机制落地（TreeWalker `docs/skill-injection-design.md`）。所以实际执行顺序是：

1. **先落地 TreeWalker skill 注入机制**（TreeWalker 工程内，方案已有）
2. **再手写 B 站 skill 做 A/B**（阶段 0）
3. **A/B 通过后改 TreeForge**（阶段 1-2）

---

## 五、各阶段预估工作量

| 阶段 | 工作量 | 产出 | 依赖 |
|---|---|---|---|
| TreeWalker skill 注入 | 中（方案已设计，见 skill-injection-design.md） | agent 能读 domain-skills/ 注入 | 无 |
| **阶段 0** 手写 skill + A/B | 小（精读 3 个 txt + 写 3 个 md + 跑实测） | 新格式 skill 验证结论 | TreeWalker 注入 |
| 阶段 1 改 distiller | 中（prompt + 模板 + 测试） | TreeForge 自动产新格式 | 阶段 0 成立 |
| 阶段 2 反推 trace | 中（扩展模型 + 人工反推 + 验证） | 真实示例 trace | 阶段 1 |
| 阶段 3 采集层 | 大（P2，原计划） | 真实采集 | 阶段 2 |

---

## 六、风险与缓解

| 风险 | 缓解 |
|---|---|
| 阶段 0 A/B 不显著（新格式模型也用不上） | 分析原因：是 skill 没注入成功？模型没读 [Domain Skill] 段？还是元素描述还是对不上？针对性调整后再验 |
| 反推 trace 工作量大（三个页面几十个元素） | 先只反推关键路径元素（投稿/上传/标题/提交），不全量；验证链路再补全 |
| 采集层（P2）按新格式设计后，蒸馏质量不达标 | 阶段 2 的反推 trace 已经是"准真实数据"，蒸馏质量在阶段 2 就能验证，不用等 P2 |
| distiller prompt 改了但 LLM 仍产出 CSS selector | prompt 里加"禁止产出"的硬约束 + 后处理校验（正则检测 `.xxx` 模式 → 警告/重试） |

---

## 七、与现有 ROADMAP 的关系

本文的执行顺序和 TreeForge `ROADMAP.md` 的阶段划分对应：

| 本文阶段 | ROADMAP 阶段 | 关系 |
|---|---|---|
| 阶段 0 | ROADMAP P1 | 同一件事（手写 skill + A/B 验证），本文细化了"用 TreeWalker 模型输入反推" |
| 阶段 1-2 | ROADMAP P1 之后 | 新增的"形态调整"工作（原 ROADMAP 没 anticipated 格式问题） |
| 阶段 3 | ROADMAP P2 | 采集层，本文明确了"按白名单属性设计采集" |

**建议**：把本文的阶段 1-2（形态调整）补进 ROADMAP，作为"P1 验证通过后、P2 采集层之前"的必做工作。原 ROADMAP 的 P1 验证如果用的是旧格式（CSS selector）skill，可能验证不成立——必须先做形态调整。

---

## 八、立即可做的第一步

**不需要任何代码改动，今天就能做的**：

1. 打开 `D:/temp/tree-walker-model-input/bili/upload-conver.txt`
2. 找到标题/分区/标签/简介/立即投稿这 5 个关键元素在 TreeWalker DOM 里的实际呈现
3. 按"元素用途 / 怎么找到它 / 稳定标识 / 备注"四列，手写一份 `selectors.md`
4. 对照：每个元素的"稳定标识"能否在该 txt 文件里 Ctrl+F 搜到

这一步不依赖 TreeWalker 注入、不依赖 TreeForge 改造，纯手工验证"新格式 skill 的元素描述能不能和 TreeWalker DOM 对上"。如果手写都对不上，说明新格式还有问题，要继续调整格式设计；如果对上了，再进入后续阶段。
