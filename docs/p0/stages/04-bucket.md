# Stage ④ BUCKET：按 capacity 归并成 Bucket[]

> 代码：`harness/bucketer.py`
> 输入：`[(Segment, CapacityLabel), ...]`
> 输出：`Bucket[]`

## 这个阶段干什么

把同 `domain::capacity` 的 segment 归到同一个桶。`bucket_id` 格式：

```python
bucket_id = f"{domain}::{slugify(capacity)}"
# 例：bilibili.com::upload-content
```

**为什么归桶？** 多条 trace / 多个 segment 表达同一能力时，应该合并蒸馏成一份 skill，
而不是每个 segment 各蒸一份。桶就是「同能力的 segment 集合」。

## 主流程

```python
# harness/bucketer.py:bucket()
def bucket(classified) -> list[Bucket]:
    buckets: dict[str, Bucket] = {}
    for seg, label in classified:
        bid = f"{seg.domain}::{slugify(label.capacity)}"
        if bid in buckets:
            b = buckets[bid]
            if seg.segment_id not in b.segment_ids:
                b.segment_ids.append(seg.segment_id)
                b.segments.append(seg)
                b.dirty = True                       # 有新内容，需要重新蒸馏
                b.last_segment_added_at = now()
        else:
            buckets[bid] = Bucket(
                bucket_id=bid,
                domain=seg.domain,
                canonical_capacity=label.capacity,
                segments=[seg],
                dirty=True,
                ...
            )
    return list(buckets.values())
```

逻辑很简单：**没桶就建，有桶就追加**。重复 segment_id 去重（同一 segment 多次进桶不会重复加）。

## slugify 规则

```python
def slugify(name) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)         # 空格/下划线 → 连字符
    s = re.sub(r"[^a-z0-9-]", "", s)      # 去非字母数字
    s = re.sub(r"-+", "-", s).strip("-")  # 合并连续连字符
    return s or "capacity"
```

例：`"Login with Credentials"` → `login-with-credentials`

## Bucket 的关键字段

```python
class Bucket(BaseModel):
    bucket_id: str                    # "{domain}::{slug(capacity)}"
    domain: str
    canonical_capacity: str           # 原始 capacity 名（未 slugify）
    segment_ids: list[str]            # segment id 列表（去重）
    segments: list[Segment]           # 运行时携带的 segment 实体
    capacity_labels: list[CapacityLabel]
    distill_version: int = 0          # 蒸馏次数（>0 表示已蒸馏过）
    last_distilled_at: str | None
    dirty: bool = True                # 有未蒸馏的新内容
    created_at: str | None
    last_segment_added_at: str | None
```

**`dirty` 和 `distill_version` 是为增量蒸馏设计的：**
- 新 segment 进桶 → `dirty=True`，需要重新蒸馏
- 蒸馏成功后 → `dirty=False`, `distill_version += 1`

P0 没有持久化，每次跑都是新桶，所以 `distill_version` 永远从 0 开始。
P1+ 接 registry 后，桶会跨会话持久化，`dirty` 才真正发挥作用。

## segment_ids 去重

```python
if seg.segment_id not in b.segment_ids:
    b.segment_ids.append(seg.segment_id)
```

为什么去重？同一条 trace 在多次跑（P1+）或多次上传同一 trace 时，会产生相同 segment_id
（因为 segment_id 是 `track_id::start_idx::end_idx`，stable track_id 让同文件多次跑得到同 id）。
去重避免重复蒸馏。

## 实测

**bilibili（1 segment → 1 bucket）：**
```
[BUCKET] 1/1 → 1 buckets
# 唯一桶：bilibili.com::upload-content
```

**github（2 segments，同 capacity → 1 bucket）：**
```
[CLASSIFY] 1/2 login-with-credentials
[CLASSIFY] 2/2 login-with-credentials    ← 两个 segment 同名（串行收敛的结果）
[BUCKET] 1/1 → 1 buckets                 ← 归到同一个桶
# 唯一桶：github.com::login-with-credentials，含 2 个 segment
```

这就是归桶的价值——同能力的多个 segment 合到一个桶，下一步会蒸馏成 1 份 skill（带 2 段证据）。

## P0 vs Browser-BC 差异

| | Browser-BC | TreeForge P0 |
|---|---|---|
| bucket_id 格式 | `{domain}::{slug(capacity)}` | **同** |
| consolidate（桶合并） | 有，CLI 子命令 | **不做**（P1+） |

**consolidate 是 Browser-BC 的桶合并机制**——LLM 提议把同义桶合并
（如 `login-with-password` + `sign-in-with-credentials` → `login-with-credentials`）。
P0 不做，因为单 trace 产生的桶少，合并价值不大。

## 实现上的简化

P0 把 segment 实体也带在 `Bucket.segments` 里（不止 `segment_ids`）：

```python
segments: list[Segment] = Field(default_factory=list,
    description="运行时携带的 segment 实体（落盘时只存 segment_ids + event_summary）")
```

**为什么？** P0 不落盘，distiller 直接从 `bucket.segments` 拿 segment 用。
P1+ 接 registry 持久化后，落盘只存 id + summary，distiller 时再按需加载——
对齐 Browser-BC 的「segments 不持久化 events」瘦身设计。

## 相关测试

- 通过 `tests/test_distiller.py::_make_bucket()` 间接测试（构造 bucket 喂 distiller）
- 通过端到端 `test_atomizer.py` 的多 segment case 间接测试归并

## 下一步

→ [05-distill.md](./05-distill.md)（**核心分叉点**：桶 → SkillCard）
