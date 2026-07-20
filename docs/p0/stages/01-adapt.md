# Stage ① ADAPT：原始 trace → 内部格式

> 代码：`harness/adapter.py`
> 输入：JSON dict（来自 `trace.json` 文件）
> 输出：`Trace` 对象（host + events[TraceEvent] + task_instruction）

## 这个阶段干什么

ADAPT 是管线的入口。它做三件事：

1. **解析** JSON 文件，抽出 `host` / `events` / `task_instruction`
2. **规整** 事件类型——各种历史写法/同义词统一成内部命名
3. **脱敏** 敏感字段值，避免泄露给 LLM

```python
# harness/adapter.py
def load_trace(path) -> Trace:
    payload = json.load(open(path))
    return adapt(payload, source=str(path))

def adapt(payload, *, source) -> Trace:
    raw_events = payload.get("events") or []
    host = payload.get("host") or _infer_host_from_events(raw_events)
    events = [_normalize_event(raw) for raw in raw_events if _keep(raw)]
    return Trace(host=host, events=events, ...)
```

## 关键操作 1：事件类型规整

不同来源的 trace 对同一动作可能用不同 type 名。ADAPT 把它们收敛：

```python
# harness/adapter.py:_normalize_event()
if etype in {"dblclick", "double_click"}:    etype = "click"
elif etype in {"wheel"}:                      etype = "scroll"
elif etype in {"file_select"}:                etype = "change"
elif etype in {"focus", "blur"}:              etype = "_skip"  # 丢弃
elif etype in {"navigation", "navigate",
               "page_load", "pageload"}:      etype = "navigate"
```

`focus`/`blur` 直接标记为 `_skip` 丢弃——它们对蒸馏没价值，是纯噪声。

## 关键操作 2：脱敏

**这是 ADAPT 最重要的职责——在数据进 LLM 之前把敏感信息替换掉。**

```python
# harness/adapter.py:_redact_value()
_SENSITIVE_FIELD_HINTS = ("password", "passwd", "pwd", "secret", "token", "cvv", "cvc", "otp")

def _redact_value(field_hint, value):
    if any(s in field_hint.lower() for s in _SENSITIVE_FIELD_HINTS):
        return "<redacted>"                              # 敏感字段
    v = _EMAIL_RE.sub("<runtime-email>", value)         # 邮箱
    v = _CARD_RE.sub("<runtime-payment-card>", v)       # 卡号
    return v
```

| 情况 | 替换为 |
|---|---|
| 字段名含 password/secret/token/cvv/cvc/otp | `<redacted>` |
| 值是邮箱（任意字段） | `<runtime-email>` |
| 值是 13-19 位数字（卡号） | `<runtime-payment-card>` |

**P0 vs Browser-BC 差异：** Browser-BC 的 redact 还覆盖 CVV 正则 / 6 位 OTP / account token。
P0 只做了前三个最小子集——示例 trace 不含真实敏感数据，完整 redact 留给 P1+。

## 关键操作 3：host 推断

如果 trace 没写顶层 `host`，ADAPT 会从第一个有 url 的事件里推：

```python
host = payload.get("host") or payload.get("domain")
if not host:
    for ev in raw_events:
        host = _detect_host_from_url(ev.get("url"))
        if host: break
```

为什么要 host？因为最终产物落到 `domain-skills/<host>/`，host 是关键。

## 主流程总览

```
load_trace(path)
   ↓ json.load
adapt(payload)
   ├─ _resolve_host(payload)        # 顶层 host 或从 url 推
   ├─ for raw in events:
   │    _normalize_event(raw)       # 规整 type
   │      └─ _redact_value(...)     # 脱敏
   └─ return Trace(host, events, task_instruction, track_id)
```

## track_id 怎么来

`track_id` 是 trace 的稳定唯一标识。优先级：

```python
track_id = payload.get("track_id")    # 你在 trace 里写死的
          or payload.get("id")
          or _stable_track_id(payload, source)  # 没写就按内容 hash 生成
```

`_stable_track_id` 用 sha1(host + 事件数 + source) 前 12 位。**稳定性很重要**：同一 trace 文件多次跑
得到同一 id → 多次跑产生的 segment_id 一致 → 增量蒸馏才能复用（P1+）。

## 实测：bilibili trace

```
[ADAPT] loading examples\bilibili-upload.trace.json
[ADAPT] 16/16 host=bilibili.com
```

16 个事件全保留（bilibili trace 没有 focus/blur 这种被丢的事件），host 从顶层取到 `bilibili.com`。

## 相关测试

- `tests/test_atomizer.py::test_registered_domain_basic`（host 解析）
- `tests/test_atomizer.py::test_filter_noise_*`（虽然名字是 atomizer，但测的是规整后的去噪）

## 下一步

→ [02-atomize.md](./02-atomize.md)（切原子能力单元）
