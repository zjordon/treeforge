"""Stage ① ADAPT：原始 trace → 内部 ``Trace`` 格式。

接受多种输入格式（本期 P0 至少支持 init-plan §7.6 的最小格式 ``{host, events[]}``），
做事件规整 + 脱敏 + 推断缺失字段。

【与 Browser-BC 的差异】Browser-BC 接受 human-tracks / recorder / journey_trace_v1 三种
格式 + 一套 NormalizedEvent 富字段。TreeForge 用最小子集，把更丰富的输入收敛到 ``TraceEvent``。

【脱敏】本期做最小脱敏（密码字段值替换、邮箱打码）。完整 Browser-BC redact 逻辑
（卡号 / CVV / OTP / account token）后续 P1+ 补——P0 示例 trace 不含真实敏感数据。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import progress
from .hostkey import bare_hostname, extract_host_with_port
from .models import Trace, TraceEvent

# 敏感字段名（值要脱敏）——大小写不敏感子串匹配
_SENSITIVE_FIELD_HINTS = ("password", "passwd", "pwd", "secret", "token", "cvv", "cvc", "otp")

# 邮箱打码
_EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)

# 数字卡号（13-19 位，允许空格/连字符）——本期保守，只在 value 字段做
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# 文件路径检测：含路径分隔符（\ 或 /）且以 .扩展名结尾。
# 用于跳过卡号脱敏——文件名里的数字串（如 2026-04-29-20-41-59.mp4）不该被当卡号。
_FILE_EXT_RE = re.compile(r"\.[a-zA-Z0-9]{2,5}$")


def _looks_like_file_path(value: str) -> bool:
    """值是否像文件路径（含分隔符 + 扩展名结尾）。"""
    has_sep = ("\\" in value) or ("/" in value)
    return has_sep and bool(_FILE_EXT_RE.search(value))


def _redact_value(field_hint: str, value: str | None) -> str | None:
    """对单个 value 做脱敏。

    field_hint 是字段名/标签（用于判断是否敏感字段）；value 是值。
    """
    if value is None or value == "":
        return value
    hint = (field_hint or "").lower()
    if any(s in hint for s in _SENSITIVE_FIELD_HINTS):
        return "<redacted>"
    # 文件路径跳过卡号脱敏（文件名数字串如 2026-04-29-20-41-59.mp4 不是卡号）
    if _looks_like_file_path(value):
        return _EMAIL_RE.sub("<runtime-email>", value)
    # 邮箱 / 卡号二级脱敏（非敏感字段也可能含）
    v = _EMAIL_RE.sub("<runtime-email>", value)
    v = _CARD_RE.sub("<runtime-payment-card>", v)
    return v


def _normalize_event(raw: dict[str, Any], fallback_idx: int) -> TraceEvent:
    """把任意 dict 收敛到 ``TraceEvent``。

    宽容处理多种 key 名：timestamp/ts/time、selector/css/xpath、target/label/text。
    """
    etype = str(raw.get("type") or raw.get("event") or "action").lower()
    # Browser-BC 的事件类型规整
    if etype in {"dblclick", "double_click"}:
        etype = "click"
    elif etype in {"wheel"}:
        etype = "scroll"
    elif etype in {"file_select"}:
        etype = "change"
    elif etype in {"focus", "blur"}:
        # Browser-BC 直接丢弃 focus/blur；这里也丢，返回一个 type=skip 哨兵由上层过滤
        etype = "_skip"
    elif etype in {"navigation", "navigate", "page_load", "pageload"}:
        etype = "navigate"

    target = raw.get("target") or raw.get("label") or raw.get("text")
    selector = raw.get("selector") or raw.get("css") or raw.get("xpath")
    url = raw.get("url") or raw.get("href")
    value = raw.get("value")
    key = raw.get("key")
    ts = raw.get("timestamp")
    if ts is None:
        ts = raw.get("ts") or raw.get("time") or fallback_idx

    # 脱敏（hint 用 target/selector 推断字段名）
    hint = " ".join(str(x) for x in (target, selector, raw.get("name"), raw.get("id")) if x)
    value = _redact_value(hint, value)

    # element_attrs（新格式）：原样保留 raw 里的 element_attrs dict，缺则空。
    # 不做白名单过滤——信任输入；阶段 3 采集层就绪后才严格过滤。
    element_attrs_raw = raw.get("element_attrs")
    element_attrs = dict(element_attrs_raw) if isinstance(element_attrs_raw, dict) else {}

    # stage（阶段 4）：指向 trace.page_context 的 key。原样读，缺则 None。
    stage = raw.get("stage")
    stage = str(stage) if stage is not None else None

    # signals（P3.6）：副作用信号（modal/dropdown 打开），原样读，缺则空 list。
    # distiller 据此写 quirks.md；采集层 attach 到事件，老 trace 无此字段。
    signals_raw = raw.get("signals")
    signals = list(signals_raw) if isinstance(signals_raw, list) else []

    return TraceEvent(
        type=etype,
        target=str(target) if target is not None else None,
        selector=str(selector) if selector is not None else None,
        element_attrs=element_attrs,
        url=str(url) if url is not None else None,
        stage=stage,
        value=value,
        key=str(key) if key is not None else None,
        timestamp=int(ts) if ts is not None else fallback_idx,
        signals=signals,
    )


def _stable_track_id(payload: dict[str, Any], source: str) -> str:
    """从 trace 内容生成稳定的 track_id（同一文件多次跑得到同一 id）。"""
    host = str(payload.get("host") or payload.get("domain") or "unknown")
    h = hashlib.sha1(
        json.dumps(
            {"source": source, "host": host, "n_events": len(payload.get("events") or [])},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"track-{h}"


def adapt(payload: dict[str, Any], *, source: str = "<inline>") -> Trace:
    """把 dict 形态的 trace 规整为 ``Trace``。

    payload 至少要有 events（list）。host 从顶层取，缺则从首个事件的 url 推断。
    """
    progress.report("ADAPT", detail=f"loading {source}")
    raw_events = payload.get("events") or payload.get("track") or []
    if not isinstance(raw_events, list):
        raise ValueError(f"trace 缺少 events 列表（source={source}）")

    # host key 语义（S0b，issue #9）：端口限定（localhost:7780 → localhost_7780），
    # 与 TreeWalker extract_host_with_port 逐字对齐——它是产物目录 / registry / 任务卡
    # 共用的索引 key。顶层 host 字段是采集期写的，老 trace 是裸 hostname（丢了端口）；
    # 事件 URL 带端口信息且与顶层同站时，升级为 URL 派生的端口限定 key（存量
    # localhost captures 重蒸由此落到 localhost_7780/，不改 captures 数据）。
    # 事件 URL 与顶层不同站（如登录跳转）时仍以顶层 host 为准。
    host = payload.get("host") or payload.get("domain")
    host = str(host).lower().strip() if host else None
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        key = extract_host_with_port(ev.get("url") or ev.get("href") or "")
        if not key:
            continue
        if host is None:
            host = key
            break
        if bare_hostname(key) == host:
            host = key
            break
    if not host:
        raise ValueError(
            f"无法确定 host：trace 既无顶层 host/domain，也无事件 url（source={source}）"
        )

    events: list[TraceEvent] = []
    for i, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            continue
        ev = _normalize_event(raw, fallback_idx=i)
        if ev.type == "_skip":
            continue
        events.append(ev)

    track_id = payload.get("track_id") or payload.get("id") or _stable_track_id(payload, source)
    task = payload.get("task_instruction") or payload.get("task") or payload.get("instruction")
    # page_context（DOM 快照）：原样保留 raw 里的 dict，缺则空。老 trace 不带时为 {}。
    page_context_raw = payload.get("page_context")
    page_context = dict(page_context_raw) if isinstance(page_context_raw, dict) else {}

    trace = Trace(
        host=host,
        events=events,
        task_instruction=task,
        page_context=page_context,
        track_id=str(track_id),
    )
    progress.report("ADAPT", current=len(events), total=len(events), detail=f"host={host}")
    return trace


def load_trace(path: str | Path) -> Trace:
    """从 JSON 文件加载并 adapt。"""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return adapt(payload, source=str(p))
