# 核心概念 ①：数据模型（Pydantic）

> 代码：`harness/models.py`
> 配套阅读：五阶段详解里的数据流图

## 整条管线的「血液」

P0 五阶段管线，每一步的输入输出都是 Pydantic 模型。理解这 4 个核心模型，
就理解了管线的「血液」类型。

```
TraceEvent ─┐
            ├─→ Trace ──→ Segment[] ──→ [(Segment, CapacityLabel)] ──→ Bucket[] ──→ SkillCard[]
TraceEvent ─┤                                                                              ↓
task_instr ─┘                                                                          4 个 .md 文件
```

## 5 个核心模型

### 1. `TraceEvent` — 单个浏览器事件

```python
class TraceEvent(BaseModel):
    type: str          # navigate/click/input/change/submit/scroll/keydown
    target: str | None # 元素的人类可读标签
    selector: str | None  # CSS/XPath 选择器
    url: str | None    # 事件发生时的页面 URL
    value: str | None  # input/change 的值（已脱敏）
    key: str | None    # keydown 的键名
    timestamp: int     # 毫秒时间戳
```

最小单位。一个事件 = 一次点击 / 输入 / 跳转。

ADAPT 阶段把各种 raw 事件 dict 规整成这个形态（详见 [stages/01-adapt.md](../stages/01-adapt.md)）。

### 2. `Trace` — 一份完整 trace

```python
class Trace(BaseModel):
    host: str          # 主域名，如 bilibili.com
    events: list[TraceEvent]
    task_instruction: str | None  # 任务描述（可空）
    track_id: str | None          # 稳定唯一 id（缺省时 ADAPT 按内容 hash 生成）
```

ADAPT 的产物。对应「人走一遍」的一份完整记录。

`host` 是关键字段——最终产物落到 `domain-skills/<host>/`。

### 3. `Segment` — 原子能力单元

```python
class Segment(BaseModel):
    segment_id: str        # "{track_id}::{start_idx}::{end_idx}" 全局唯一
    source_track_id: str
    domain: str            # 段内最高频域名（不一定等于 trace.host）
    start_idx: int
    end_idx: int
    events: list[TraceEvent]
    boundary_reason: str   # domain_change/idle_gap/path_change/submit_nav/...
    entry_url: str | None
    exit_url: str | None
    duration_ms: int
    event_summary: str     # ★喂给 LLM 的多行文本（不含原始 events）
```

ATOMIZE 的产物。一条 trace 切成 N 个 segment。

**两个关键点：**
- `segment_id` 全局唯一（含 track_id + 起止下标），用于跨次去重
- `event_summary` 是 events 的文本渲染——后续所有 LLM 调用只看 summary，不看原始 events（控 token）

**Browser-BC 的瘦身设计：** 落盘时 `events` 不存，只存 `event_summary`。P0 不落盘所以 events 还在，
P1+ 接 registry 后会遵守这个瘦身规则。

### 4. `Bucket` — 蒸馏输入单元

```python
class Bucket(BaseModel):
    bucket_id: str                    # "{domain}::{slug(capacity)}"
    domain: str
    canonical_capacity: str           # 原始 capacity 名（未 slugify）
    description: str
    segment_ids: list[str]            # segment id 列表（去重）
    segments: list[Segment]           # 运行时携带的实体
    capacity_labels: list[CapacityLabel]
    distill_version: int = 0          # ★蒸馏次数（>0 = 已蒸馏过）
    last_distilled_at: str | None
    dirty: bool = True                # ★有未蒸馏的新内容
    created_at: str | None
    last_segment_added_at: str | None
```

BUCKET 的产物。同能力的 segment 集合。

**`distill_version` + `dirty` 是为增量蒸馏设计的**——P0 没持久化所以这两个字段没真正发挥作用，
P1+ 接 registry 后才会用。

### 5. `SkillCard` — 蒸馏产物 ★关键分叉

```python
class SkillCard(BaseModel):
    bucket_id: str
    domain: str
    capacity: str
    skill_name: str
    scope: str               # 一句话用例说明

    # ★四个站点特定字段（对应 4 个文件）
    sop_md: str              # → _sop.md
    selectors_md: str        # → selectors.md
    quirks_md: str           # → quirks.md
    api_md: str              # → api.md

    meta: dict               # {model, usage, segment_count, distill_version, distilled_at}
```

DISTILL 的产物。**这是 TreeForge 与 Browser-BC 的核心分歧点——**

| | Browser-BC | TreeForge |
|---|---|---|
| 字段 | `skill_md` + `trace_guide_md` | `sop_md` + `selectors_md` + `quirks_md` + `api_md` |
| 语义 | 通用 SOP | 站点特定知识 |

详见 [stages/05-distill.md](../stages/05-distill.md)。

## 辅助模型：`CapacityLabel`

```python
class CapacityLabel(BaseModel):
    capacity: str             # kebab-case 名，如 login-with-credentials
    description: str
    entry_conditions: list[str]
    exit_conditions: list[str]
    outcome: str              # success/partial/unclear
    domain_hints: list[str]
```

CLASSIFY 给 segment 贴的标签。不是核心数据流模型，但贯穿 CLASSIFY → BUCKET → DISTILL。

## 为什么用 Pydantic v2 而不是 dataclass

| | Browser-BC | TreeForge |
|---|---|---|
| 数据模型实现 | `@dataclass` | **Pydantic v2** |

理由：
1. **自动校验**——类型不匹配时 Pydantic 抛清晰错误（如把 None 塞到 required 字段）
2. **JSON 序列化免费**——`.model_dump()` 直接出 dict，落盘 / 测试 fixture 都方便
3. **`model_copy(update={...})`**——atomizer 合并 segment 时用，比 dataclass 改字段优雅
4. **IDE 类型提示好**——Pydantic v2 用 Rust 实现，性能不是问题

## 一个 trace 在管线里的「变形记」

用 bilibili trace 举例：

```
JSON dict（trace.json 文件）
   │
   │ adapt()
   ▼
Trace(host="bilibili.com", events=[16 个 TraceEvent], task_instruction="在B站投稿...")
   │
   │ atomize() 切 1 段
   ▼
Segment(segment_id="track-xxx::0::15", domain="bilibili.com",
        events=[16 个], event_summary="navigate ...\nclick ...\n...")
   │
   │ classify() 贴标签
   ▼
(Segment, CapacityLabel(capacity="upload-content", outcome="success"))
   │
   │ bucket() 归并
   ▼
Bucket(bucket_id="bilibili.com::upload-content",
       segment_ids=["track-xxx::0::15"], segments=[1 个], dirty=True)
   │
   │ distill_bucket()
   ▼
SkillCard(domain="bilibili.com", capacity="upload-content",
          sop_md="...", selectors_md="...", quirks_md="...", api_md="...")
   │
   │ adapter.write_skill()
   ▼
4 个 .md 文件
```

每种类型职责单一，转换边界清晰。这是 P0 能用 30 行串起来的原因。

## 相关测试

模型本身没专门单测，但通过各阶段测试间接覆盖：
- `tests/test_atomizer.py` 验证 Segment 产出
- `tests/test_distiller.py` 验证 SkillCard 字段
- `tests/test_adapters.py` 验证 SkillCard → 文件

## 下一步

→ [02-llm-client.md](./02-llm-client.md)（LLM 客户端：标准库 urllib 双协议）
