# TreeForge P0 文档集

> 把「人走一遍浏览器操作」变成「可复用的 skill 文件」的最小可跑闭环。
> 本目录文档只讲 **P0 阶段实现了什么、为什么这么实现**，不讲 P1+ 的规划。

---

## 这套文档怎么读

根据你的状态挑入口：

### 🚀 第一次接触 P0 → 从这里开始

按顺序读这两篇，能建立完整的「主线 + 直觉」认知：

1. **[01-架构总览.md](./01-architecture-overview.md)** — 一张图看懂 P0 全貌
   - 整条数据流（trace JSON → 4 个 markdown 文件）
   - 5 个关键设计决策的 why
   - P0 为什么是「最小」闭环（哪些不做）

2. **[02-新手向导.md](./02-beginner-walkthrough.md)** — 跟着 bilibili 例子走一遍
   - 一条具体的 trace 怎么一步步变成 `_sop.md` / `selectors.md` / `quirks.md` / `api.md`
   - 每一步进去什么、出来什么
   - 跑命令、看产物

### 🔍 想深入某个阶段 → stages/

读完上面两篇后，按管线顺序逐阶段深入：

| 文档 | 阶段 | 一句话职责 |
|---|---|---|
| [stages/01-adapt.md](./stages/01-adapt.md) | ADAPT | 原始 trace JSON → 内部 `Trace`（规整 + 脱敏） |
| [stages/02-atomize.md](./stages/02-atomize.md) | ATOMIZE | `Trace` → `Segment[]`（切原子能力单元） |
| [stages/03-classify.md](./stages/03-classify.md) | CLASSIFY | `Segment` → `domain::capacity`（增量命名） |
| [stages/04-bucket.md](./stages/04-bucket.md) | BUCKET | 按 capacity 归并 → `Bucket[]` |
| [stages/05-distill.md](./stages/05-distill.md) | DISTILL ★ | `Bucket` → `SkillCard`（**核心分叉点**） |

### 🧠 想理解横切概念 → concepts/

不按管线顺序，按主题讲清楚几个跨阶段的设计：

| 文档 | 主题 | 为什么重要 |
|---|---|---|
| [concepts/01-data-models.md](./concepts/01-data-models.md) | 数据模型（Trace/Segment/Bucket/SkillCard） | 整条管线的「血液」类型 |
| [concepts/02-llm-client.md](./concepts/02-llm-client.md) | LLM 客户端（urllib 双协议） | 零运行时依赖的关键 |
| [concepts/03-adapter-design.md](./concepts/03-adapter-design.md) | adapter 缓冲设计 | 为什么同一份 SkillCard 能出两种格式 |

---

## 一句话 P0

> **手写 trace JSON →（五阶段管线）→ 落到 `domain-skills/<host>/` 下的 4 个 markdown 文件，给 TreeWalker 文件注入消费。**

跑通命令：

```bash
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm
ls ./data/skills/domain-skills/bilibili.com/
# _sop.md  selectors.md  quirks.md  api.md
```

---

## 配套阅读

- 项目根 [README.md](../../README.md) — 项目定位、技术栈、快速开始
- 项目根 [ARCHITECTURE.md](../../ARCHITECTURE.md) — 四层分层架构（采集/接入/蒸馏/输出），P0 只落地其中两层
- 项目根 [ROADMAP.md](../../ROADMAP.md) — P0~P4 分期，本目录只讲 P0
- `init-plan.md` — 原始规划文档（位于知识库 `knowledge-garden/projects/treeforge/`，不在本仓库）

## 一条提醒

P0 的 **DISTILL 阶段** 是 TreeForge 立项的根本理由——它和参照对象 Browser-BC 在这里**反着来**
（Browser-BC 产「通用 SOP」，TreeForge 产「站点特定知识」）。如果只读一篇，读 [stages/05-distill.md](./stages/05-distill.md)。
