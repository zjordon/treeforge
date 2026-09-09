"""Stage ② ATOMIZE：``Trace`` → ``Segment[]``。

把一条 trace 切成多个「原子能力单元」。本期 P0 实现完整的 4 条边界规则 + 去噪 +
合并/拆分（对齐 Browser-BC），但允许退化到「整条 trace 一个 segment」——P0 只验证 distill 能跑通。

【4 条边界规则】（见知识库 browserbc-distill-pipeline.md）
  1. 主域名切换（仅当回到 track 主域时切）
  2. 静默 > 15s
  3. 同域 URL path 前缀变化（depth=2）
  4. submit 后 lookahead=5 内出现 pageLoad/navigate

【去噪】
  - iframe 域名黑名单（stripe/recaptcha/cloudflare/google...）的 pageLoad 丢弃
  - 孤立修饰键（Shift/Meta/Alt/Ctrl/CapsLock，500ms 内无其它键）
  - 连续重复点击（同 xpath+url，间隔 < 2s）合并
  - 连续同目标 input（同输入框 + url，间隔 < 30s）合并保留终值

【后处理】
  - 长度 < 3 的 segment 合并进前一个（**绝不跨域边界**）
  - 长度 > 80 的 segment 在最近的 navigate 边界拆分
  - segment.domain = 段内出现频率最高的 registered_domain
"""

from __future__ import annotations

from urllib.parse import urlparse

from . import config, progress
from .hostkey import bare_hostname, extract_host_with_port
from .models import Segment, Trace, TraceEvent

# iframe / 第三方域黑名单：这些域的 pageLoad 是噪声，不切边界
_IFRAME_DOMAINS = {
    "stripe.com",
    "recaptcha.net",
    "recaptcha.com",
    "cloudflare.com",
    "hcaptcha.com",
    "google.com",
    "gstatic.com",
    "betterbugs.com",
}

# 孤立修饰键
_MODIFIER_KEYS = {"Shift", "Meta", "Alt", "Control", "CapsLock"}

# input 合并窗口（毫秒）：同目标连续输入在此窗口内合并保留终值。
# 比 click 的 2s 宽——真实打字停顿（思考/选词）可达数秒（实测 0.6–3.8s）。
_INPUT_MERGE_WINDOW_MS = 30_000

# input 同目标判定的「稳定标识」属性：任一相同即视为同一输入框。
# 都无标识时退化到同 tag（兜底，避免漏合并）。
_INPUT_STABLE_ATTRS: tuple[str, ...] = (
    "id",
    "name",
    "placeholder",
    "aria-label",
    "aria-labelledby",
)


def _registered_domain(host: str | None) -> str:
    """简化版 eTLD+1：取最后两段。完整 publicsuffix 后续 P1+ 补。

    ``www.bilibili.com → bilibili.com``，``api.x.com → x.com``，``localhost → localhost``，
    ``localhost_7780 → localhost_7780``（端口限定 key 原样）
    """
    if not host:
        return ""
    host = host.lower().strip()
    # 端口限定 key（127.0.0.1_7780）按点号分段会算出损坏 key（0.1_7780）——
    # 裸 hostname 命中本机特例集的原样返回（localhost_7780 无点号本就原样走到这）
    if host in {"localhost", "127.0.0.1"} or bare_hostname(host) in {"localhost", "127.0.0.1"}:
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _host_of(url: str | None) -> str:
    """事件 URL → host（端口限定 key：``localhost:7780`` → ``localhost_7780``）。

    【产物 key 链路】_host_of → _registered_domain → Segment.domain → domain-skills/
    落盘目录 / registry / 任务卡（issue #9 S0b）——这里若剥掉端口，localhost:7780
    会蒸到 ``localhost/``，对 TreeWalker 静默不可见。
    """
    return extract_host_with_port(url) or ""


def _path_prefix(url: str | None, depth: int) -> str:
    if not url:
        return "/"
    try:
        path = urlparse(url).path or "/"
    except Exception:  # noqa: BLE001
        return "/"
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join(parts[:depth])


def _same_input_target(a: TraceEvent, b: TraceEvent) -> bool:
    """判定两个 input 事件是否作用于同一输入框（用于连续输入合并）。

    优先用 element_attrs 的稳定标识（id/name/placeholder/aria-label/aria-labelledby）
    任一相同即同一目标；都无标识时退化到同 tag + 同 selector（老格式兜底）。
    """
    ea, eb = a.element_attrs or {}, b.element_attrs or {}
    # 稳定标识命中
    for k in _INPUT_STABLE_ATTRS:
        va, vb = ea.get(k), eb.get(k)
        if va and vb and va == vb:
            return True
    # 都无稳定标识：退化到同 tag + 同 selector（兜底）
    has_stable_a = any(ea.get(k) for k in _INPUT_STABLE_ATTRS)
    has_stable_b = any(eb.get(k) for k in _INPUT_STABLE_ATTRS)
    if not has_stable_a and not has_stable_b:
        same_tag = (ea.get("tag") or "") == (eb.get("tag") or "")
        same_sel = (a.selector or "") == (b.selector or "")
        return bool(same_tag and same_sel)
    return False


def _same_click_target(a: TraceEvent, b: TraceEvent) -> bool:
    """判定两个 click 事件是否作用于同一元素（用于重复点击合并）。

    【修复】原逻辑只比 selector，新格式 trace 用 element_attrs 不填 selector，
    导致两个空 selector 恒等（"" == ""），同页面任意两个 < 2s 的 click 都被判重被吞
    （误杀「选择合集」「确定」等不同按钮）。修复策略对齐 _same_input_target：

      1. 都有 element_attrs：按稳定标识（id/name/aria-label/role/data-testid 等）判等，
         任一稳定标识相同才算同一目标；稳定标识都无时，退化到 tag + visible_text 联合判
         （二者都必须非空且相同——避免「同 tag 同空文本」误判）。
      2. 都无 element_attrs（老格式）：退化到 selector 判等，但 selector 必须非空
         （两个空 selector 不算同——避免回到原 bug）。
      3. 一方有 element_attrs 一方无：保守判不同（不合并，避免误杀）。

    核心原则：信息不足时返回 False（保守保留），宁可多保留也不误杀不同按钮。
    """
    ea, eb = a.element_attrs or {}, b.element_attrs or {}
    has_attrs_a = bool(ea)
    has_attrs_b = bool(eb)

    # 两边都有 element_attrs（新格式）
    if has_attrs_a and has_attrs_b:
        # 任一稳定标识相同 → 同一目标
        for k in _CLICK_STABLE_ATTRS:
            va, vb = ea.get(k), eb.get(k)
            if va and vb and va == vb:
                return True
        # 稳定标识都不同：退化到 tag + visible_text 联合判（都非空且相同才算同）
        tag_a, tag_b = ea.get("tag") or "", eb.get("tag") or ""
        text_a, text_b = ea.get("visible_text") or "", eb.get("visible_text") or ""
        return bool(tag_a and tag_b and tag_a == tag_b and text_a and text_b and text_a == text_b)

    # 两边都无 element_attrs（老格式）：退化到 selector，但必须非空
    if not has_attrs_a and not has_attrs_b:
        sel_a, sel_b = a.selector or "", b.selector or ""
        return bool(sel_a and sel_a == sel_b)

    # 一方有一方无：保守判不同（不合并）
    return False


# click 判等用的稳定标识（比 input 多 role/data-testid/data-test/data-cy——按钮常用）。
# 注意：不含 placeholder（按钮没有），不含 visible_text（visible_text 单独按 tag+text 联合判）。
_CLICK_STABLE_ATTRS: tuple[str, ...] = (
    "id",
    "name",
    "aria-label",
    "aria-labelledby",
    "role",
    "data-testid",
    "data-test",
    "data-cy",
    "data-tw-jsclick",
)


def _filter_noise(events: list[TraceEvent]) -> list[TraceEvent]:
    """四类去噪：iframe pageLoad / 孤立修饰键 / 重复点击合并 / 连续同目标 input 合并。"""
    out: list[TraceEvent] = []
    i = 0
    while i < len(events):
        ev = events[i]

        # 1. iframe 域 pageLoad（navigate）丢弃
        if ev.type == "navigate":
            h = _registered_domain(_host_of(ev.url))
            if h and h in _IFRAME_DOMAINS:
                i += 1
                continue

        # 2. 孤立修饰键：keydown 修饰键 + 500ms 内无其它非修饰键
        if ev.type == "keydown" and (ev.key or "") in _MODIFIER_KEYS:
            has_other = False
            for j in range(i + 1, min(i + 6, len(events))):
                nxt = events[j]
                if nxt.timestamp - ev.timestamp > 500:
                    break
                if nxt.type == "keydown" and (nxt.key or "") in _MODIFIER_KEYS:
                    continue
                has_other = True
                break
            if not has_other:
                i += 1
                continue

        # 3. 重复点击合并：同目标元素 + 同 url，间隔 < 2s，保留后者。
        # 【修复】原逻辑只比 selector，新格式 trace 用 element_attrs 不填 selector，
        # 两个空 selector 恒等导致同页面任意两个 < 2s 的 click 都被判重（误杀「选择合集」
        # 等不同按钮）。改用 _same_click_target：按 element_attrs 稳定标识判等，
        # 都无标识时保守判不同（不合并）。
        if ev.type == "click" and out:
            prev = out[-1]
            same = (
                prev.type == "click"
                and (prev.url or "") == (ev.url or "")
                and _same_click_target(prev, ev)
            )
            if same and 0 <= ev.timestamp - prev.timestamp < 2000:
                out[-1] = ev  # 用后者覆盖
                i += 1
                continue

        # 4. 连续同目标 input 合并：同输入框 + 同 url，间隔 < 30s，保留终值。
        # 真机录制里一次标题输入常被扩展 debounce 切成 N 条（人类打字停顿 0.6–3.8s
        # 远超 400ms 窗口），每条带完整累积值——合并后只保留终值，summary 不再被噪声撑大。
        if ev.type == "input" and out:
            prev = out[-1]
            if (
                prev.type == "input"
                and (prev.url or "") == (ev.url or "")
                and 0 <= ev.timestamp - prev.timestamp < _INPUT_MERGE_WINDOW_MS
                and _same_input_target(prev, ev)
            ):
                out[-1] = ev  # 终值覆盖（保留 ev 的 value + timestamp）
                i += 1
                continue

        out.append(ev)
        i += 1
    return out


# element_attrs 里「稳定标识」属性的白名单——对齐 distiller prompt 的 selectors.md 要求。
# 只渲染这些属性，过滤 class/style 等不稳定属性。
_ATTR_WHITELIST: tuple[str, ...] = (
    "id",
    "name",
    "type",
    "placeholder",
    "aria-label",
    "aria-labelledby",
    "role",
    "data-testid",
    "data-test",
    "data-cy",
    "contenteditable",
    # P3.6：upload 的 accept（file input 专属，quirks 原料）+ JS 点击标记（MAIN-world hook 打的）
    "accept",
    "data-tw-jsclick",
    "tag",
)


def _format_attrs_summary(element_attrs: dict) -> str:
    """把 element_attrs 渲染成单行「tag=#id name=x placeholder=y」文本。

    用于 event_summary 里让 LLM 看到元素的白名单属性。空 dict 返回 ""。
    """
    if not element_attrs:
        return ""
    parts: list[str] = []
    tag = element_attrs.get("tag")
    if tag:
        parts.append(str(tag))
    for k in _ATTR_WHITELIST:
        if k == "tag":
            continue
        v = element_attrs.get(k)
        if v in (None, "", False):
            continue
        parts.append(f"{k}={v}")
    # 可见文本单独处理（不是 attr=val 格式）
    visible_text = element_attrs.get("visible_text")
    if visible_text:
        parts.append(f'可见文本"{visible_text}"')
    return " ".join(parts)


def _render_summary(events: list[TraceEvent], cap_lines: int = 120) -> str:
    """把 events 渲染成多行文本喂给 LLM。

    格式：``{type:<10} {path} :: {label}``。连续重复折叠为 ``x{N}``。
    超过 cap_lines 行时，头部 35% + 尾部拼接（Browser-BC 同款截断）。
    """
    lines: list[str] = []
    for ev in events:
        # 双轨：优先用 element_attrs（新格式），fallback 到 selector（老格式）
        attrs_summary = _format_attrs_summary(ev.element_attrs)
        if attrs_summary:
            path = attrs_summary
        else:
            path = ev.selector or ev.url or "/"
        label = ev.target or ev.value or ev.key or ""
        # stage 标记（阶段 4）：行尾追加 [stage=xxx]，让 LLM 在 evidence 段看到每步阶段。
        # 带? 的推断值也参与折叠判断（upload? 和 upload 视为不同 stage，不折叠——期望行为）。
        stage_suffix = f" [stage={ev.stage}]" if ev.stage else ""
        # 副作用信号（P3.6）：行尾追加 [signal=modal_opened/dropdown_opened]，
        # 让 LLM 看到该动作触发了弹窗/下拉——写 quirks.md 的关键原料。
        signal_suffix = ""
        if ev.signals:
            kinds = [s.get("type", "") for s in ev.signals if isinstance(s, dict)]
            kinds = [k for k in kinds if k]  # 滤空（缺 type 字段的信号）
            if kinds:
                signal_suffix = " [signal=" + ",".join(kinds) + "]"
        line = f"{ev.type:<10} {path} :: {label}{stage_suffix}{signal_suffix}".rstrip()
        lines.append(line)

    # 折叠连续重复
    folded: list[str] = []
    for line in lines:
        if folded and folded[-1].split(" x")[0] == line:
            # 已经有 " x{N}" 后缀 → N+1
            base = line
            tail = folded[-1][len(base) :]
            if tail.startswith(" x"):
                # tail 形如 " x2" / " x3"，数字部分在 [2:]（跳过 " x" 两字符）
                n = int(tail[2:]) + 1
                folded[-1] = f"{base} x{n}"
            else:
                folded[-1] = f"{line} x2"
        else:
            folded.append(line)

    if len(folded) <= cap_lines:
        return "\n".join(folded)
    head_n = max(1, int(cap_lines * 0.35))
    tail_n = cap_lines - head_n
    head = folded[:head_n]
    tail = folded[-tail_n:]
    return "\n".join(head + [f"... ({len(folded) - head_n - tail_n} more lines) ..."] + tail)


def _find_boundaries(events: list[TraceEvent], track_domain: str) -> list[tuple[int, str]]:
    """返回 [(cut_idx, reason), ...]。cut_idx 是新 segment 的起始事件下标。"""
    cuts: list[tuple[int, str]] = [(0, "end_of_track")]
    prev_domain = _registered_domain(_host_of(events[0].url)) if events else track_domain
    prev_prefix = _path_prefix(events[0].url, config.PATH_DEPTH) if events else "/"

    for i in range(1, len(events)):
        ev = events[i]
        cur_host = _host_of(ev.url)
        cur_domain = _registered_domain(cur_host) or prev_domain
        cur_prefix = _path_prefix(ev.url, config.PATH_DEPTH)

        # Rule 1: 主域切换（仅当回到 track 主域）
        if cur_domain != prev_domain and cur_domain == track_domain:
            cuts.append((i, "domain_change"))
        # Rule 2: 静默 > 15s
        elif ev.timestamp - events[i - 1].timestamp > config.IDLE_GAP_MS:
            cuts.append((i, "idle_gap"))
        # Rule 3: 同域 path 前缀变化
        elif (
            ev.type == "navigate"
            and cur_domain == prev_domain
            and cur_prefix != prev_prefix
            and prev_prefix != "/"
        ):
            cuts.append((i, "path_change"))
        # Rule 4: submit 后 lookahead 内出现 navigate
        elif ev.type == "submit":
            lookahead = events[i + 1 : i + 1 + config.SUBMIT_LOOKAHEAD]
            if any(la.type == "navigate" for la in lookahead):
                # 切点在 navigate 那个事件
                nav_idx = i + 1 + next(k for k, la in enumerate(lookahead) if la.type == "navigate")
                # 切点延后到 nav_idx（submit_nav 边界）
                cuts.append((nav_idx, "submit_nav"))
                # prev 推进
                prev_domain = cur_domain
                prev_prefix = cur_prefix
                continue

        prev_domain = cur_domain
        prev_prefix = cur_prefix

    # 去重 + 排序 + 去掉 nav_idx=0 重复
    seen: set[int] = set()
    deduped: list[tuple[int, str]] = []
    for idx, reason in sorted(cuts, key=lambda x: x[0]):
        if idx in seen:
            continue
        seen.add(idx)
        deduped.append((idx, reason))
    return deduped


def _slice_segments(
    events: list[TraceEvent], cuts: list[tuple[int, str]], trace: Trace
) -> list[Segment]:
    """按切点切成 Segment 列表。"""
    if not events:
        return []
    segs: list[Segment] = []
    boundaries = [idx for idx, _ in cuts] + [len(events)]
    reasons = [r for _, r in cuts]
    for k in range(len(boundaries) - 1):
        start = boundaries[k]
        end = boundaries[k + 1]
        # 该 segment 的边界 reason 用其起始切点的 reason（最后一段用前一段 reason 的 end_of_track）
        reason = reasons[k] if k < len(reasons) else "end_of_track"
        seg_events = events[start:end]
        if not seg_events:
            continue
        domain = _dominant_domain(seg_events) or trace.host
        segs.append(
            Segment(
                segment_id=f"{trace.track_id}::{start}::{end - 1}",
                source_track_id=trace.track_id or "track-unknown",
                domain=domain,
                start_idx=start,
                end_idx=end - 1,
                events=seg_events,
                boundary_reason=reason,
                entry_url=seg_events[0].url,
                exit_url=seg_events[-1].url,
                duration_ms=seg_events[-1].timestamp - seg_events[0].timestamp,
                event_summary=_render_summary(seg_events),
            )
        )
    return segs


def _dominant_domain(events: list[TraceEvent]) -> str:
    """段内出现频率最高的 registered_domain。"""
    counts: dict[str, int] = {}
    for ev in events:
        d = _registered_domain(_host_of(ev.url))
        if d:
            counts[d] = counts.get(d, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda x: x[1])[0]


def _merge_short(segs: list[Segment]) -> list[Segment]:
    """长度 < MIN_SEGMENT_EVENTS 的 segment 合并进前一个（**绝不跨域边界**）。"""
    if len(segs) <= 1:
        return segs
    out: list[Segment] = [segs[0]]
    for seg in segs[1:]:
        if len(seg.events) < config.MIN_SEGMENT_EVENTS and out and out[-1].domain == seg.domain:
            prev = out[-1]
            merged_events = prev.events + seg.events
            out[-1] = prev.model_copy(
                update={
                    "end_idx": seg.end_idx,
                    "events": merged_events,
                    "exit_url": seg.exit_url,
                    "duration_ms": seg.events[-1].timestamp - prev.events[0].timestamp
                    if merged_events
                    else prev.duration_ms,
                    "event_summary": _render_summary(merged_events),
                    "boundary_reason": prev.boundary_reason,
                }
            )
        else:
            out.append(seg)
    return out


def _split_long(segs: list[Segment]) -> list[Segment]:
    """长度 > MAX_SEGMENT_EVENTS 的 segment 在最近的 navigate 边界拆分。"""
    out: list[Segment] = []
    for seg in segs:
        if len(seg.events) <= config.MAX_SEGMENT_EVENTS:
            out.append(seg)
            continue
        # 找最近的 navigate 位置作为切点
        evs = seg.events
        nav_indices = [i for i, e in enumerate(evs, start=seg.start_idx) if e.type == "navigate"]
        if not nav_indices:
            out.append(seg)
            continue
        # 在 MAX 附近找一个 nav
        target = seg.start_idx + config.MAX_SEGMENT_EVENTS
        nav_idx = min(nav_indices, key=lambda i: abs(i - target))
        local = nav_idx - seg.start_idx
        if local <= 0 or local >= len(evs):
            out.append(seg)
            continue
        left = evs[:local]
        right = evs[local:]
        out.append(
            seg.model_copy(
                update={
                    "end_idx": nav_idx - 1,
                    "events": left,
                    "exit_url": left[-1].url if left else seg.entry_url,
                    "duration_ms": left[-1].timestamp - left[0].timestamp if left else 0,
                    "event_summary": _render_summary(left),
                }
            )
        )
        out.append(
            Segment(
                segment_id=f"{seg.source_track_id}::{nav_idx}::{seg.end_idx}",
                source_track_id=seg.source_track_id,
                domain=seg.domain,
                start_idx=nav_idx,
                end_idx=seg.end_idx,
                events=right,
                boundary_reason="max_size_split",
                entry_url=right[0].url if right else seg.exit_url,
                exit_url=seg.exit_url,
                duration_ms=right[-1].timestamp - right[0].timestamp if right else 0,
                event_summary=_render_summary(right),
            )
        )
    return out


def atomize(trace: Trace) -> list[Segment]:
    """主入口：trace → Segment[]。"""
    progress.report("ATOMIZE", total=len(trace.events), detail=f"host={trace.host}")
    if not trace.events:
        progress.report("ATOMIZE", detail="empty trace")
        return []

    cleaned = _filter_noise(trace.events)
    track_domain = _registered_domain(trace.host) or trace.host

    cuts = _find_boundaries(cleaned, track_domain)
    segs = _slice_segments(cleaned, cuts, trace)
    segs = _merge_short(segs)
    segs = _split_long(segs)

    # 兜底：如果一切规则都没切出 segment，整条 trace 一个 segment
    if not segs and cleaned:
        segs = [
            Segment(
                segment_id=f"{trace.track_id}::0::{len(cleaned) - 1}",
                source_track_id=trace.track_id or "track-unknown",
                domain=track_domain,
                start_idx=0,
                end_idx=len(cleaned) - 1,
                events=cleaned,
                boundary_reason="end_of_track",
                entry_url=cleaned[0].url,
                exit_url=cleaned[-1].url,
                duration_ms=cleaned[-1].timestamp - cleaned[0].timestamp,
                event_summary=_render_summary(cleaned),
            )
        ]

    progress.report("ATOMIZE", current=len(segs), total=len(segs), detail=f"→ {len(segs)} segments")
    return segs
