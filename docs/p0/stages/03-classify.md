# Stage ③ CLASSIFY：Segment → domain::capacity

> 代码：`harness/classifier.py`
> 输入：`Segment[]`
> 输出：`[(Segment, CapacityLabel), ...]`（每个 segment 配一个能力标签）

## 这个阶段干什么

给每个 segment 贴一个 **capacity 标签**——动词+宾语的 kebab-case 名字，比如：

- `upload-content`
- `login-with-credentials`
- `fill-checkout-form`
- `perform-search`

这个标签后续决定 segment 进哪个桶（`domain::capacity`）→ 决定蒸馏出哪个 skill。

**为什么用动词+宾语？** 因为它表达「这个 segment 是在做什么能力」，便于后续检索复用。

## 两条路径

```python
def classify(segments, *, use_llm=None) -> list[(Segment, CapacityLabel)]:
    if use_llm is None:
        use_llm = bool(config.LLM_KEY)
    for seg in segments:                # ← 串行！
        label = classify_segment_llm(seg, caps) if use_llm \
                else _heuristic_capacity(seg)
        ...
```

| 路径 | 触发 | 实现 |
|---|---|---|
| LLM | 配了 LLM_KEY | 调 Haiku 级 LLM 读 segment 内容起名 |
| 启发式 | 无 LLM 或 `--no-llm` | 从 event_summary 找关键词映射 |

## 核心机制：串行 + 增量命名 ⚠️

**这是 CLASSIFY 最重要的设计——绝对不能并发。**

```python
caps = list(existing or [])        # 已知 capacity 列表
seen = {c for c, _ in caps}
for seg in segments:               # 串行遍历
    label = classify_one(seg, caps)
    if label.capacity not in seen:
        seen.add(label.capacity)
        caps.append((label.capacity, label.description))  # ← 新名字立刻可用于下一个
```

**为什么必须串行？** 如果并发处理 10 个 segment，每个调用都拿到**空的** `caps` 列表，
LLM 不知道前面已经命名过什么，于是同一个能力会被命名为 3 种不同名字：

```
并发（错误）：  seg1 → "login"
              seg2 → "sign-in"
              seg3 → "authenticate"
              → 三个桶，三份重复 skill
```

```
串行（正确）：  seg1 → "login-with-credentials"
              caps += ["login-with-credentials"]   # 立刻可用
              seg2 → 看到 caps 里有 login-with-credentials
                   → 复用："login-with-credentials"   # ✅ 同名
              seg3 → 同上 → "login-with-credentials"
              → 一个桶，一份 skill
```

**Prompt 关键约束：**

> "If the segment matches one of the above [existing capacities], you MUST use the EXACT same
> capacity name. Only propose a new name if the segment genuinely does not fit any existing bucket."

把当前已知的 caps 列表喂给 LLM，让它优先复用已有名字。

## 实测：github trace 的收敛

github 登录 trace 切成 2 个 segment（输入账密 + 输 2FA），都该归到 `login-with-credentials`：

```
[CLASSIFY] 0/2 use_llm=False
[CLASSIFY] 1/2 login-with-credentials    # 第一个 segment 命名
[CLASSIFY] 2/2 login-with-credentials    # 第二个复用同名（启发式也实现了同样的收敛）
[BUCKET] 1/1 → 1 buckets                 # 归到同一个桶
```

**注意启发式也实现了串行收敛**——不是 LLM 路径专属。两条路径都遵守这个机制。

## LLM Prompt 长什么样

```python
_CLASSIFY_PROMPT_TEMPLATE = """\
Classify the following browser interaction segment into ONE atomic capability.

The segment is from domain `{domain}`.
{existing_hint}                              ← 已知 capacity 列表

Segment event summary (entry: {entry} → exit: {exit}):
```
{summary}                                    ← segment 的 event_summary
```

Return ONLY JSON:
{{
  "capacity": "kebab-case-name",
  "description": "...",
  "entry_conditions": [...],
  "exit_conditions": [...],
  "outcome": "success|partial|unclear",
  "domain_hints": [...]
}}
"""
```

`existing_hint` 是动态的——第一个 segment 时是空的，之后每次都把 caps 累积进去。

## 启发式 fallback `_heuristic_capacity()`

无 LLM 时用关键词匹配：

```python
def _heuristic_capacity(seg):
    summary = (seg.event_summary or "").lower()
    if "login" in summary or "登录" in summary:    return "login-with-credentials"
    elif "upload" in summary or "上传" in summary: return "upload-content"
    elif "checkout" in summary or "支付" in summary: return "complete-checkout"
    ...
    elif has_submit and has_input:                  return "submit-form"
    elif has_click:                                 return "navigate-and-click"
```

**启发式的局限：** 只能识别常见任务模式。罕见任务会落到 `interact-with-page` 兜底。
真实项目该用 LLM 路径，启发式只保证 P0 链路不报错。

## 容错

```python
try:
    text = call_llm_fast(prompt, ...)
    return CapacityLabel(**parse_json_from_model(text))
except Exception as e:
    progress.report("CLASSIFY", detail=f"LLM failed, falling back to heuristic")
    return _heuristic_capacity(seg)   # LLM 挂了不阻断管线
```

LLM 调用失败时退启发式——不抛错，保证管线继续。

## P0 vs Browser-BC 差异

| | Browser-BC | TreeForge P0 |
|---|---|---|
| LLM 模型 | Haiku | Haiku |
| 增量命名 | 串行 | **同** |
| 失败容错 | 抛错 | **退启发式**（保证 P0 跑通） |
| 并发 | 串行 per-domain | **同**（按 domain 分组，同 domain 内串行） |

P0 当前实现是「全局串行」，Browser-BC 是「按 domain 分组、组间并发、组内串行」。
P0 简化为全局串行——单 trace 通常没那么多 segment，性能差异可忽略。P1+ 多 trace 时再优化。

## 相关测试

- `tests/test_classifier.py::test_classify_returns_label_for_each_segment`
- `tests/test_classifier.py::test_classify_serial_naming_converges`（验证串行收敛）
- `tests/test_classifier.py::test_classify_heuristic_picks_upload_for_bilibili`

## 下一步

→ [04-bucket.md](./04-bucket.md)（按 capacity 归并成桶）
