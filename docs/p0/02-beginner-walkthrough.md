# 新手向导：跟着 bilibili 例子走一遍

> 这篇文档用一个具体例子（B 站投稿）从头走一遍 P0 链路。
> 你不需要先读任何代码，读完能知道每一步进去什么、出来什么。
>
> 配套阅读：[01-架构总览.md](./01-architecture-overview.md)（建立主线认知）

## 准备：跑起来

```bash
cd D:\dev\git\z_jordon\treeforge
uv sync --extra dev
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm
```

`--no-llm` 让链路走模板模式（不调 LLM），便于先验证链路本身能跑通。
产物质量低，但结构完整。读完本文你会知道为什么。

## 起点：trace JSON 长什么样

打开 `examples/bilibili-upload.trace.json`，结构是这样的（精简版）：

```json
{
  "host": "bilibili.com",
  "task_instruction": "在 B 站投稿上传一个视频",
  "events": [
    {"type": "navigate", "target": "B 站首页",
     "url": "https://www.bilibili.com/", "timestamp": 0},
    {"type": "click", "target": "投稿按钮",
     "selector": ".header-entry .upload-btn, [aria-label='投稿']",
     "url": "https://www.bilibili.com/", "timestamp": 1200},
    {"type": "navigate", "target": "投稿页",
     "url": "https://member.bilibili.com/platform/upload/video/frame", "timestamp": 2400},
    {"type": "change", "target": "视频文件选择",
     "selector": "input[type='file'][accept='video/*']",
     "value": "my-video.mp4", "timestamp": 3600},
    ... (共 16 步)
    {"type": "submit", "target": "投稿表单", "selector": "form.upload-form",
     "url": "https://member.bilibili.com/platform/upload/video/frame", "timestamp": 32100},
    {"type": "navigate", "target": "投稿成功页",
     "url": "https://member.bilibili.com/platform/upload/video/success", "timestamp": 40000}
  ]
}
```

**trace 就是「人走一遍的流水账」**——你点了什么、输入了什么、跳转到了哪。
P0 不录制，需要你手写（P2 的扩展才会真的去录制）。

最小必备字段：
- `host`：主域名（最终落到 `domain-skills/<host>/`）
- `events[]`：事件列表，每个事件至少有 `type` 和 `timestamp`

## 第 ① 步：ADAPT — 规整成内部格式

代码：`harness/adapter.py` → `adapter.load_trace()`

进去：上面的 JSON dict
出来：一个 `Trace` 对象（host + events[TraceEvent] + task_instruction）

ADAPT 做两件事：

**1. 规整事件类型。** 各种写法统一：

| 输入 | 规整为 |
|---|---|
| `dblclick` / `double_click` | `click` |
| `wheel` | `scroll` |
| `file_select` | `change` |
| `navigation` / `navigate` / `page_load` / `pageload` | `navigate` |
| `focus` / `blur` | **丢弃**（噪声） |

bilibili trace 的事件类型已经规整，所以这步主要做的是字段收敛。

**2. 脱敏。** 把敏感值替换，避免泄露给 LLM：

| 情况 | 处理 |
|---|---|
| 字段名含 `password`/`secret`/`token`/`cvv`/`otp` | 值 → `<redacted>` |
| 值像邮箱 | → `<runtime-email>` |
| 值像卡号（13-19 位） | → `<runtime-payment-card>` |

bilibili trace 没有敏感字段，所以这步也没实际改动。要看脱敏效果，跑 `examples/github-login.trace.json`——
里面的 `password` 字段值会被替换为 `<redacted>`。

> 详见 [stages/01-adapt.md](./stages/01-adapt.md)

## 第 ② 步：ATOMIZE — 切原子能力单元

代码：`harness/atomizer.py` → `atomizer.atomize()`

进去：`Trace`（一条完整流程）
出来：`Segment[]`（切成 N 个原子能力单元）

**为什么切？** 一条 trace 可能跨多个独立任务（先登录、再上传、再发评论）。
把每个独立任务切出来，后面才能分别蒸馏成独立的 skill。

**怎么切？** 四条边界规则：

1. **主域切换**（回到 track 主域时切）
2. **静默 > 15 秒**（用户停下来思考 → 大概率是新任务起点）
3. **同域 URL path 前缀变化**（如 `/login` → `/dashboard`，深 2 层）
4. **submit 后 lookahead 5 个事件内出现 navigate**（表单提交跳转）

还附带**去噪**：
- iframe 第三方域（stripe/recaptcha/google...）的 pageLoad 丢弃
- 孤立修饰键（Shift/Ctrl 单独按）丢弃
- 连续重复点击（同 selector + 同 url，间隔 < 2 秒）合并

bilibili trace 这步的产出（控制台输出）：

```
[ATOMIZE] 1/1 → 1 segments
```

只切出 **1 个 segment**。为什么？因为这 16 步从「打开 B 站」到「投稿成功」是一气呵成的
一个任务，没有符合上面 4 条切点的情况（主域没切、没静默、submit 后的 navigate 触发了
`submit_nav` 边界但切出来还是属于同一个投稿任务）。

**这是个重要现象：P0 经常一条 trace 切成 1 个 segment。** 这正常——单任务示教本来就该这样。
多条 trace 跨多个任务才会切出多个 segment。

> 详见 [stages/02-atomize.md](./stages/02-atomize.md)

## 第 ③ 步：CLASSIFY — 贴能力标签

代码：`harness/classifier.py` → `classifier.classify()`

进去：`Segment[]`
出来：`[(Segment, CapacityLabel), ...]`（每个 segment 配一个能力标签）

CLASSIFY 给每个 segment 贴一个 **capacity 标签**（动词+宾语的 kebab-case 名字），
比如 `upload-content` / `login-with-credentials` / `fill-checkout-form`。

bilibili 这步的产出：

```
[CLASSIFY] 1/1 upload-content
```

那个 segment 被贴上 `upload-content` 标签。

**P0 有两条路径：**

1. **LLM 路径**（配了 `LLM_KEY`）：调 LLM（Haiku 级，快/省）让它读 segment 内容起名
2. **启发式路径**（没配 LLM 或 `--no-llm`）：从 event_summary 里找关键词（"上传"/"upload"→`upload-content`）

上面跑的是启发式——`event_summary` 里有"投稿/上传"，命中了启发式规则。

**关键机制：串行 + 增量命名。** 如果有多个 segment，必须一个接一个分类，每分完一个就把
新名字加入「已知列表」，下一个 segment 就能看到。**绝对不能并发**——否则同一个能力会被
命名为 `login` / `sign-in` / `authenticate` 三个名字，散落到三个桶。

详见 [01-架构总览.md 决策 2](./01-architecture-overview.md#决策-2classify-必须串行不能并发)
和 [stages/03-classify.md](./stages/03-classify.md)。

## 第 ④ 步：BUCKET — 按 capacity 归并

代码：`harness/bucketer.py` → `bucketer.bucket()`

进去：`[(Segment, CapacityLabel), ...]`
出来：`Bucket[]`

把同 `domain::capacity` 的 segment 归到同一个桶。`bucket_id` 格式：

```
bucket_id = "{domain}::{slug(capacity)}"
# 例：bilibili.com::upload-content
```

bilibili 这步的产出：

```
[BUCKET] 1/1 → 1 buckets
```

只有 1 个 segment + 1 个 capacity → 1 个桶 `bilibili.com::upload-content`。

如果有 3 个 segment 都是上传操作，会归到同一个桶里——这就是「跨次复用」的基础：
同能力的多次示教合并成一个 skill。**但 P0 单条 trace 通常只有 1 个桶**。

> 详见 [stages/04-bucket.md](./stages/04-bucket.md)

## 第 ⑤ 步：DISTILL — 蒸馏成知识卡 ★核心

代码：`harness/distiller.py` → `distiller.distill_buckets()`

进去：`Bucket[]`
出来：`SkillCard[]`（每个桶 → 一个知识卡，含 4 字段）

这是 **TreeForge 的核心**，也是和参照对象 Browser-BC 的**主要分歧**。

**LLM 路径的 prompt 核心**（简化版）：

> 把这些浏览器操作证据，蒸馏成一份 **bilibili.com 的站点特定知识卡**。
>
> # 关键：这与通用 skill 写作相反
> **不要** 抽象掉站点特定的 selector / ID / URL。**抓住它们** ——
> 这份知识会被文件注入给导航到 `bilibili.com` 的浏览器 agent，必须**拿到就能用**。
> "Record the map, not the diary."
>
> 产出 4 个 markdown 段：
> 1. `sop_md`：这个站点这个能力的常见任务流程（量少）
> 2. `selectors_md`：所有稳定 selector（量大、最重要）
> 3. `quirks_md`：隐藏等待、SPA 导航、反爬等怪癖
> 4. `api_md`：私有 API、URL 模式、隐藏端点

**对照 Browser-BC 的 prompt**（它要求相反）：

> 产出一个**任何浏览器 agent 都能在任何网站执行**的 SKILL.md —— **抽象掉站点特定的 selector 和 ID**。

为什么 TreeForge 反着来？因为消费方式不同。TreeWalker 用**文件注入**消费——agent 导航到
`bilibili.com` 时直接把 `domain-skills/bilibili.com/*.md` 读进上下文。这种消费期望「站点特定、
拿到就能用」的知识，不是通用 SOP（通用 SOP 还得 agent 自己找 selector）。

bilibili 这步的产出（`--no-llm` 走的是模板路径）：

```
[DISTILL] bucket=bilibili.com::upload-content segments=1 use_llm=False
[DISTILL] → template card for bilibili.com::upload-content
```

**两条路径的产物质量差异很大：**

- **LLM 路径**：4 字段都有语义化的提炼内容
- **模板路径**（这次跑的）：4 字段结构完整但内容机械——`selectors.md` 就是把所有 selector 列出来，
  `quirks.md` 只是个占位符

> 详见 [stages/05-distill.md](./stages/05-distill.md)

## 第 ⑥ 步：INSTALL — adapter 写盘

代码：`harness/install.py` + `adapters/`

进去：`SkillCard[]` + `output_dir`
出来：磁盘上的 markdown 文件

默认用 `treewalker_adapter`，写到 `domain-skills/<host>/` 下 4 个文件：

```
data/skills/domain-skills/bilibili.com/
├── _sop.md
├── selectors.md
├── quirks.md
└── api.md
```

bilibili 这步的产出：

```
[INSTALL] 1/1 bilibili.com::upload-content
[DONE] wrote 4 files to data\skills
  wrote: data\skills\domain-skills\bilibili.com\_sop.md
  wrote: data\skills\domain-skills\bilibili.com\selectors.md
  wrote: data\skills\domain-skills\bilibili.com\quirks.md
  wrote: data\skills\domain-skills\bilibili.com\api.md
```

### 实际产物长什么样

打开 `data/skills/domain-skills/bilibili.com/selectors.md`（模板模式产出）：

```markdown
# Selectors — bilibili.com

Observed for `upload-content`:

- `.header-entry .upload-btn, [aria-label='投稿']` — 投稿按钮
- `input[type='file'][accept='video/*']` — 视频文件选择
- `input[placeholder='请输入标题']` — 标题输入框
- `.select-wrap .select-text, [data-testid='category-select']` — 分区选择
- `.select-list li:nth-child(3), [role='option']:has-text('科技')` — 分区：科技
- `textarea[placeholder='填写更加全面的信息']` — 简介输入框
- `.tag-input .btn-add, [aria-label='添加标签']` — 标签添加按钮
- `input.tag-input, [data-testid='tag-input']` — 标签输入
- `input.tag-input` — 确认标签
- `radio[value='original'], [data-testid='original-radio']` — 原创声明
- `.submit-btn .submit-add, [data-testid='submit-btn']` — 立即投稿按钮
- `form.upload-form` — 投稿表单
```

这就是 TreeWalker 的 agent 导航到 bilibili 时会读到的内容。它知道点哪个 selector 干什么——
**这就是"拿到就能用"的意思**。

`_sop.md`（事件流水账，模板模式没蒸馏）：

```markdown
# SOP — bilibili.com / upload-content

Template distill (no LLM). Observed event sequence:

\`\`\`
navigate   https://www.bilibili.com/ :: B 站首页
click      .header-entry .upload-btn, [aria-label='投稿'] :: 投稿按钮
navigate   https://member.bilibili.com/platform/upload/video/frame :: 投稿页
change     input[type='file'][accept='video/*'] :: 视频文件选择
... (共 16 步)
\`\`\`
```

LLM 模式下这里会变成语义化的步骤描述（"先导航到投稿页，选文件，填元数据..."），而不是事件流水账。

## 想看完整 LLM 蒸馏效果？

配 `.env`（从 `.env.example` 复制后填 `LLM_KEY`），然后不加 `--no-llm`：

```bash
cp .env.example .env
# 编辑 .env：LLM_KEY=你的key，LLM_BASE=你的端点
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
```

这回 4 个文件都会有 LLM 提炼后的语义内容，不是模板占位。

## 回顾：整条链路的数据形态变化

```
trace.json (dict)            ← 你手写的
    │
    │  ADAPT
    ▼
Trace                        ← Pydantic 模型，规整后
    │
    │  ATOMIZE
    ▼
Segment[]                    ← 切出来的原子单元（bilibili: 1 个）
    │
    │  CLASSIFY
    ▼
[(Segment, CapacityLabel)]   ← 贴了能力标签（bilibili: upload-content）
    │
    │  BUCKET
    ▼
Bucket[]                     ← 按 domain::capacity 归并（bilibili: 1 个桶）
    │
    │  DISTILL
    ▼
SkillCard[]                  ← 4 字段知识卡
    │
    │  INSTALL
    ▼
4 个 .md 文件                ← 落到 domain-skills/<host>/
```

bilibili 这条 trace 一路都是「1 个」——1 个 segment、1 个 capacity、1 个桶、1 张卡、4 个文件。
这是 P0 的典型形态：**单条单任务 trace → 1 张 skill 卡**。

## 下一步

- 想理解每个阶段的具体算法 → [stages/](./stages/)
- 想理解 Pydantic 模型 / LLM 客户端 / adapter 设计 → [concepts/](./concepts/)
- 想跑另一条 trace（GitHub 登录）看看多 segment 情况 → `uv run treeforge distill examples/github-login.trace.json --output ./data/skills --no-llm`
  （它跨 /login → /sessions/two-factor → /dashboard，会切出 2 个 segment）
