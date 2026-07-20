# Stage ② ATOMIZE：Trace → Segment[]

> 代码：`harness/atomizer.py`
> 输入：`Trace`（一条完整流程）
> 输出：`Segment[]`（切成 N 个原子能力单元）

## 这个阶段干什么

把一条长 trace 切成多个「原子能力单元」。每个 segment 是一个独立的小任务，
后续会被独立 classify / distill 成独立的 skill。

**为什么切？** 一条 trace 可能跨多个任务（登录 → 上传 → 发评论）。把它们各切出来，
才能分别蒸馏成独立的 skill；否则混在一起蒸馏出来的 skill 不清晰。

## 主流程

```python
# harness/atomizer.py:atomize()
def atomize(trace) -> list[Segment]:
    cleaned = _filter_noise(trace.events)         # ① 去噪
    cuts = _find_boundaries(cleaned, track_domain) # ② 找切点（4 条规则）
    segs = _slice_segments(cleaned, cuts, trace)   # ③ 按切点切片
    segs = _merge_short(segs)                      # ④ 合并过短
    segs = _split_long(segs)                       # ⑤ 拆分过长
    return segs
```

## ① 去噪 `_filter_noise()`

三类噪声要丢：

| 噪声类型 | 处理 | 原因 |
|---|---|---|
| iframe 第三方域的 pageLoad | 丢弃 | recaptcha/stripe/cloudflare/google 等弹窗，与主任务无关 |
| 孤立修饰键（Shift/Ctrl/CapsLock 单独按） | 丢弃 | 500ms 内没其它键 = 误按 |
| 连续重复点击（同 selector+url，<2s） | 合并保留后者 | 双击或抖动 |

iframe 黑名单：
```python
_IFRAME_DOMAINS = {"stripe.com", "recaptcha.net", "cloudflare.com",
                   "google.com", "gstatic.com", "hcaptcha.com", ...}
```

## ② 找切点 `_find_boundaries()` — 4 条规则

**这是 ATOMIZE 的核心算法。** 按顺序检查每个事件：

```python
for i in range(1, len(events)):
    ev = events[i]
    cur_domain = registered_domain(host_of(ev.url))
    cur_prefix = path_prefix(ev.url, depth=2)

    # Rule 1: 主域切换（仅当回到 track 主域时切）
    if cur_domain != prev_domain and cur_domain == track_domain:
        cut(i, "domain_change")

    # Rule 2: 静默 > 15s
    elif ev.timestamp - events[i-1].timestamp > 15_000:
        cut(i, "idle_gap")

    # Rule 3: 同域 URL path 前缀变化（depth=2）
    elif ev.type == "navigate" and cur_domain == prev_domain \
         and cur_prefix != prev_prefix and prev_prefix != "/":
        cut(i, "path_change")

    # Rule 4: submit 后 lookahead 5 内出现 navigate
    elif ev.type == "submit":
        if any(la.type == "navigate" for la in events[i+1:i+6]):
            cut(nav_idx, "submit_nav")
```

**Rule 1 的微妙之处：** 只有「**回到** track 主域」才切。为什么？因为用户可能短暂跳到第三方
（如 OAuth 弹窗），但那不算独立任务，是主任务的一部分。只有当他切回主站时才认为「这个子任务结束了」。

**Rule 4 的实现细节：** 切点不在 submit 本身，而在它后面的 navigate（表单提交 → 跳转才是边界）。
用 lookahead 5 找那个 navigate。

## ③ 切片 `_slice_segments()`

按切点把 events 切成段，每段生成一个 `Segment`：

```python
Segment(
    segment_id=f"{track_id}::{start_idx}::{end_idx}",  # 全局唯一
    domain=_dominant_domain(seg_events),                # 段内最高频域名
    events=seg_events,
    boundary_reason="submit_nav",                       # 这段的切点 reason
    entry_url=seg_events[0].url,
    exit_url=seg_events[-1].url,
    duration_ms=seg_events[-1].timestamp - seg_events[0].timestamp,
    event_summary=_render_summary(seg_events),          # 喂给 LLM 的多行文本
)
```

**`event_summary` 是关键：** 它是 events 的文本渲染（`{type:<10} {path} :: {label}`），
后续所有 LLM 调用都只看这个 summary，不看原始 events。这样 token 消耗可控。

**`domain` 取段内最高频域，不是 track 主域。** 这处理 segment 里短暂跨域的情况。

## ④ 合并过短 `_merge_short()`

```python
if len(seg.events) < MIN_SEGMENT_EVENTS(=3) and prev.domain == seg.domain:
    merge_into(prev, seg)
```

3 个事件以下的 segment 通常没意义（误切）。合并进前一个——**但绝不跨域边界**
（否则一个 B 站 segment 会被 A 站 segment 污染）。

## ⑤ 拆分过长 `_split_long()`

```python
if len(seg.events) > MAX_SEGMENT_EVENTS(=80):
    # 在最近的 navigate 边界拆分
    nav_idx = nearest_navigate(around=80)
    split_at(seg, nav_idx, reason="max_size_split")
```

太长的 segment（>80 事件）喂给 LLM 会爆 token + 信息过载。在最近的 navigate 边界拆开。

## event_summary 的渲染细节

`_render_summary()` 把 events 渲染成给 LLM 看的文本。两个关键处理：

**1. 连续重复折叠：** 5 次相同 click 不写 5 行，写成 `x5`：
```
click      #btn :: 提交 x5
```

**2. 超长截断：** 超过 120 行时，头部 35% + 尾部拼接：
```
（前 42 行）
... (N more lines) ...
（后 78 行）
```

这两个处理都在控制喂给 LLM 的 token 量。

## 实测对比：bilibili vs github

```
bilibili trace（16 事件）→ 1 个 segment
github trace（10 事件）→ 2 个 segment
```

**bilibili 为什么只切 1 个？** 因为这 16 步是一气呵成的投稿任务，没符合 4 条切点
（主域没切、没静默 15s、path 变化不够深、submit 后 navigate 触发了 submit_nav 但本就是同任务的一部分）。

**github 为什么切 2 个？** 因为它跨 `/login` → `/sessions/two-factor` → `/dashboard`，
path 前缀变化（Rule 3）和 submit_nav（Rule 4）都触发了。两个 segment 分别是「输入账密」和「输 2FA」。

## P0 的容错

```python
# 兜底：如果一切规则都没切出 segment，整条 trace 一个 segment
if not segs and cleaned:
    segs = [Segment(boundary_reason="end_of_track", ...)]
```

确保 trace 永远至少产出 1 个 segment——空 trace 单独处理（返回 `[]`）。

## 相关测试

- `tests/test_atomizer.py::test_filter_noise_*`（去噪三类）
- `tests/test_atomizer.py::test_find_boundaries_idle_gap`（Rule 2）
- `tests/test_atomizer.py::test_atomize_*_yields_*_segments`（端到端切片数）

## 下一步

→ [03-classify.md](./03-classify.md)（给 segment 贴能力标签）
