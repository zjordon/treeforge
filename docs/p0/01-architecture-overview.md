# P0 架构总览

> 一张图看懂 P0 全貌：数据流、关键决策、为什么是「最小」闭环。

## 一、P0 在做什么

一句话：

> **把一份浏览器操作的 trace JSON，经过五阶段管线，蒸馏成 4 个站点特定的 markdown 文件，给 TreeWalker 用。**

```
你手写的 trace JSON（人走一遍的记录）
        ↓
   ┌─────────────────────┐
   │  harness/ 五阶段管线 │  ← P0 的核心
   └─────────────────────┘
        ↓
4 个 markdown 文件（site-specific skill）
        ↓
   TreeWalker 文件注入消费
```

P0 的全部价值就是**让这条链路能跑通**。不录制、不联 server、不验证质量——这些是 P1/P2/P3 的事。

## 二、整条数据流（关键的一张图）

```
examples/bilibili-upload.trace.json
    │
    │  ① ADAPT     harness/adapter.py
    │  原始 JSON dict → Trace(host, events[TraceEvent])
    │  做两件事：规整事件类型、脱敏（密码/邮箱/卡号）
    ▼
Trace
    │
    │  ② ATOMIZE   harness/atomizer.py
    │  一条 trace 切成 N 个 Segment
    │  四条边界规则 + 去噪 + 合并/拆分
    ▼
Segment[]
    │
    │  ③ CLASSIFY  harness/classifier.py
    │  给每个 Segment 贴一个 capacity 标签（如 upload-content）
    │  串行 + 增量命名（关键机制）
    ▼
[(Segment, CapacityLabel), ...]
    │
    │  ④ BUCKET    harness/bucketer.py
    │  按 domain::capacity 归并（同能力的 segment 进同个桶）
    ▼
Bucket[]
    │
    │  ⑤ DISTILL   harness/distiller.py  ★核心分叉点
    │  每个桶 → 一个 SkillCard（4 字段：sop/selectors/quirks/api）
    │  调 LLM 或退模板 fallback
    ▼
SkillCard[]
    │
    │  INSTALL     harness/install.py + adapters/
    │  按 adapter 写盘：treewalker 写 4 文件，browserbc 写单 SKILL.md
    ▼
data/skills/domain-skills/bilibili.com/{_sop,selectors,quirks,api}.md
```

每个箭头都是一个独立函数，输入输出都是 Pydantic 模型（见 [concepts/01-data-models.md](./concepts/01-data-models.md)）。
**整个 P0 没有持久化中间状态**——trace 进、文件出，跑完就结束。增量蒸馏 / registry 持久化是 P1+。

## 三、串起整条链路的代码

就 30 行，在 `treeforge/__main__.py:_run_distill()`：

```python
trace = adapter.load_trace(trace_path)              # ① ADAPT
segments = atomizer.atomize(trace)                  # ② ATOMIZE
classified = classifier.classify(segments, use_llm=use_llm)  # ③ CLASSIFY
buckets = bucketer.bucket(classified)               # ④ BUCKET
cards = distiller.distill_buckets(buckets, use_llm=use_llm)  # ⑤ DISTILL
written = install.install_cards(cards, output_dir, adapter)  # INSTALL
```

这就是 P0 的全部主线。每一步都是纯函数式的：进去什么、出来什么，一眼可见。

## 四、5 个关键设计决策

理解了这 5 个决策，就理解了 P0 为什么是这个样子。

### 决策 1：DISTILL 产「站点特定知识」，不产「通用 SOP」 ★最核心

TreeForge 参照的对象叫 Browser-BC。两者在 DISTILL 阶段**反着来**：

| | Browser-BC | TreeForge |
|---|---|---|
| DISTILL 指令 | "Abstract away site-specific selectors and IDs" | **"Capture site-specific selectors"** |
| 产物 | 1 个通用 `SKILL.md`（去站点化） | **4 个站点特定 markdown** |
| 适用场景 | MCP 检索：agent 拿抽象能力卡去任意站点执行 | **文件注入**：agent 导航到这个站点时直接读 |

**为什么 TreeForge 反着来？** 因为消费方不同。TreeWalker（TreeForge 的消费方）用的是**文件注入**——
agent 导航到 `bilibili.com` 时，直接把 `domain-skills/bilibili.com/*.md` 读进上下文。
这种消费方式期望「**拿到就能用**」的知识（真实 selector、真实 URL），不是通用 SOP
（通用 SOP 还得 agent 自己再去找 selector，没有直接可操作性）。

init-plan 里用一句话概括这个分叉：**"record the map, not the diary"**——记地图不记流水账。

**4 个文件各自存什么：**

| 文件 | 比喻 | 内容 |
|---|---|---|
| `_sop.md` | 骨架 | 这个站点常见任务流程（量少，Browser-BC 风格但绑定本站） |
| `selectors.md` | 血肉 | 稳定 selector、AX name、元素定位（量大、最重要） |
| `quirks.md` | 怪癖 | 隐藏等待、SPA 导航、框架行为、反爬检测 |
| `api.md` | 暗门 | 私有 API、URL 模式、隐藏端点 |

详见 [stages/05-distill.md](./stages/05-distill.md)。

### 决策 2：CLASSIFY 必须串行，不能并发

CLASSIFY 给 segment 贴标签（如 `login-with-credentials` / `upload-content`）。
P0 用了一个看起来「低效」的设计：**串行**处理每个 segment，不是并发。

```python
caps = []                           # 已知 capacity 列表
for seg in segments:                # ← 必须 serial！
    label = classify_one(seg, caps) # 把 caps 喂给 LLM
    if label.capacity not in seen:
        caps.append(label.capacity) # 新名字立刻可用于下一个 segment
```

**为什么不能并发？** 如果并发，每个调用都拿到空的 `caps`，同一个能力会被命名为
`login` / `sign-in` / `authenticate` 三个不同名字 → 散落到三个桶 → 蒸馏出三份重复的 skill。

**关键 prompt 约束**："If the segment matches one of the above, you MUST use the EXACT same
capacity name."——见 [stages/03-classify.md](./stages/03-classify.md)。

### 决策 3：LLM 客户端用标准库 urllib，不引入 SDK

`harness/llm.py` 用 `urllib.request` 直接发 HTTP，**不引入** `anthropic` / `openai` SDK。
这是从 Browser-BC 继承的「零运行时依赖」哲学（Pydantic 是唯一例外）。

附带一个关键修复——**双协议探测**：

```python
def is_anthropic(base: str) -> bool:
    b = base.lower()
    return "anthropic.com" in b or "/anthropic" in b
                                   ^^^^^^^^^^
                       这个 /anthropic 路径段匹配第三方网关
```

- URL 含 `anthropic.com` 或路径段 `/anthropic` → Anthropic Messages 协议
- 否则 → OpenAI 兼容协议

**为什么需要这个？** 智谱 BigModel 等第三方网关把 Anthropic API 挂在 `/api/anthropic` 路径下。
如果不带 `/` 只匹配字符串 `anthropic`，会误判；如果完全不探测，发 OpenAI 格式请求到 Anthropic
端点直接 404。详见 [concepts/02-llm-client.md](./concepts/02-llm-client.md)。

### 决策 4：adapter 缓冲——同一份 SkillCard 出两种格式

P0 在输出层做了一个关键缓冲：`SkillCard` 是中间表示，与输出形态解耦。

```
SkillCard ──┬── treewalker_adapter ──→ domain-skills/<host>/{4 文件}  （默认）
            └── browserbc_adapter  ──→ skills/<domain>/<cap>/SKILL.md  （对照）
```

CLI 用 `--adapter` 切换。**这意味着蒸馏逻辑完全不关心输出长什么样**——加新格式只要写新 adapter。

为什么留 browserbc adapter？**学习对照**。TreeForge 的核心分叉点在 DISTILL，留一个 Browser-BC
格式的输出，让你能直观对比「站点特定」和「通用 SOP」两种产物形态的差异。
详见 [concepts/03-adapter-design.md](./concepts/03-adapter-design.md)。

### 决策 5：无 LLM 时退模板 fallback，保证链路永远跑通

`distiller.distill_bucket()` 有两条路径：

```python
if not use_llm:
    return _template_skill_card(bucket)   # 模板：从 event_summary 抽 selector/URL 填四字段
# 否则调 LLM，失败也退模板
try:
    text = call_llm(...)
    return parse_and_build(text)
except Exception:
    return _template_skill_card(bucket)   # 退模板
```

**为什么这么设计？** P0 的目标是「验证链路能跑通」。如果没配 LLM_KEY 或 LLM 调用失败就报错退出，
你连 adapter/install 是否正确都无法验证。退模板后产物质量低（quirks_md 只是个占位符），但**结构完整**，
让你能跑通 `examples/*.trace.json → 4 文件`的完整链路。

跑命令时加 `--no-llm` 强制走模板路径，验证链路本身：

```bash
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm
```

## 五、P0 为什么是「最小」闭环（哪些不做）

理解 P0 也需要理解它**故意不做**什么：

| 范围 | P0 状态 | 何时做 |
|---|---|---|
| 采集层（MV3 扩展录制） | ❌ 仅 WXT 脚手架（空目录） | P2 |
| 接入层（FastAPI server） | ❌ 仅占位文件 | P1 |
| 增量蒸馏（旧 skill 进 prompt） | ⚠️ 框架在，但旧 skill 没持久化 | P1（接 registry） |
| 持久化中间状态 | ❌ 跑完即弃 | P1（checkpoint.json / buckets.json） |
| MCP stdio 检索 | ❌ 完全不做 | P4（TreeWalker 用文件注入不需要） |
| 质量验证层 | ❌ 不做 | P3 |
| **五阶段蒸馏管线** | ✅ **完整实现** | — |
| **adapter 输出层** | ✅ **完整实现** | — |
| **CLI 入口** | ✅ **完整实现** | — |

P0 用 **CLI 替代接入层**：直接 `treeforge distill <trace.json>` 读文件跑管线，绕开扩展录制和 server 接收。

## 六、下一步

- 想跟着具体例子走一遍 → [02-新手向导.md](./02-beginner-walkthrough.md)
- 想逐阶段深入 → [stages/](./stages/) 目录
- 想理解横切概念 → [concepts/](./concepts/) 目录
