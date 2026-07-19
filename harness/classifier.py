"""Stage ③ CLASSIFY：``Segment`` → ``domain::capacity``。

【核心机制】**串行增量命名**（绝对不能并发）。详见知识库 browserbc-distill-pipeline.md。

如果并发分类、每个调用都拿到空的 existing_capacities，同一个能力会被命名为
``login`` / ``sign-in`` / ``authenticate`` 三个名字 → 散落到三个桶。

正确做法（串行）：

    caps = list(existing_capacities)
    for seg in segments:                # 必须 serial
        cs = classify_segment_sync(seg, caps)
        if cs.label.capacity not in seen:
            caps.append((capacity, description))   # 新名字立即可用于下一个 seg

Prompt 关键约束：*"If the segment matches one of the above, you MUST use the EXACT same
capacity name. Only propose a new name if the segment genuinely does not fit any existing bucket."*

【本期 P0】提供真实 LLM 路径 + 一个零依赖的启发式 fallback（无 LLM_KEY 时用）。
"""

from __future__ import annotations

from . import config, progress
from .llm import call_llm_fast, parse_json_from_model
from .models import CapacityLabel, Segment

_CLASSIFY_SYSTEM = (
    "You are a capability classifier for browser automation. "
    "Output STRICT JSON only, no prose, no markdown fences."
)

_CLASSIFY_PROMPT_TEMPLATE = """\
Classify the following browser interaction segment into ONE atomic capability.

The segment is from domain `{domain}`.
{existing_hint}

Segment event summary (entry: {entry} → exit: {exit}):
```
{summary}
```

Decide:
- `capacity`: a kebab-case name (2-6 words, verb+object). Example: `login-with-credentials`, `upload-video`, `fill-checkout-form`.
- `description`: 1-2 sentences.
- `entry_conditions`: list of observable start conditions.
- `exit_conditions`: list of observable end conditions.
- `outcome`: one of `success` / `partial` / `unclear`.
- `domain_hints`: list of domains this capacity typically applies to.

Rules:
- If the segment matches one of the EXISTING CAPACITIES above, you MUST use the EXACT same capacity name. Only propose a new name if the segment genuinely does not fit any existing bucket.
- Keep capacity names verb+object, lowercase kebab-case.

Return ONLY JSON in this exact shape:
{{
  "capacity": "...",
  "description": "...",
  "entry_conditions": ["..."],
  "exit_conditions": ["..."],
  "outcome": "success",
  "domain_hints": ["..."]
}}
"""


def _existing_hint(existing: list[tuple[str, str]]) -> str:
    if not existing:
        return "No existing capacities yet — propose the first one."
    bullets = "\n".join(f"- `{cap}`: {desc[:80]}" for cap, desc in existing)
    return f"Existing capacities in this domain:\n{bullets}\n"


# ---- 启发式 fallback（无 LLM 时用，保证 P0 链路不报错）-----------------------


def _heuristic_capacity(seg: Segment) -> CapacityLabel:
    """无 LLM 时的启发式分类：从事件类型序列猜一个 capacity 名。"""
    types = [e.type for e in seg.events]
    summary = (seg.event_summary or "").lower()
    has_submit = "submit" in types
    has_input = "input" in types or "change" in types
    has_click = "click" in types

    cap = "interact-with-page"
    if "login" in summary or "sign in" in summary or "登录" in summary or "signin" in summary:
        cap = "login-with-credentials"
    elif "upload" in summary or "上传" in summary:
        cap = "upload-content"
    elif "checkout" in summary or "支付" in summary or "pay" in summary:
        cap = "complete-checkout"
    elif "register" in summary or "注册" in summary or "sign up" in summary:
        cap = "register-account"
    elif "search" in summary or "搜索" in summary:
        cap = "perform-search"
    elif has_submit and has_input:
        cap = "submit-form"
    elif has_input:
        cap = "fill-form"
    elif has_click:
        cap = "navigate-and-click"

    return CapacityLabel(
        capacity=cap,
        description=f"Heuristic classification for {seg.domain} segment.",
        entry_conditions=[f"On {seg.domain}"] if seg.domain else [],
        exit_conditions=["Desired action completed"],
        outcome="success",
        domain_hints=[seg.domain] if seg.domain else [],
    )


def classify_segment_llm(seg: Segment, existing: list[tuple[str, str]]) -> CapacityLabel:
    """调 LLM 分类一个 segment。失败时退回启发式（不抛错，保证管线继续）。"""
    prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
        domain=seg.domain,
        existing_hint=_existing_hint(existing),
        entry=seg.entry_url or "?",
        exit=seg.exit_url or "?",
        summary=seg.event_summary or "(no events)",
    )
    try:
        text, _usage = call_llm_fast(prompt, system=_CLASSIFY_SYSTEM)
        data = parse_json_from_model(text)
        return CapacityLabel(
            capacity=str(data.get("capacity") or "interact-with-page").strip(),
            description=str(data.get("description") or ""),
            entry_conditions=list(data.get("entry_conditions") or []),
            exit_conditions=list(data.get("exit_conditions") or []),
            outcome=str(data.get("outcome") or "success"),
            domain_hints=list(data.get("domain_hints") or []),
        )
    except Exception as e:  # noqa: BLE001 - 分类失败不阻断管线
        progress.report("CLASSIFY", detail=f"LLM failed ({e!r}), falling back to heuristic")
        return _heuristic_capacity(seg)


def classify(
    segments: list[Segment],
    *,
    use_llm: bool | None = None,
    existing: list[tuple[str, str]] | None = None,
) -> list[tuple[Segment, CapacityLabel]]:
    """串行增量分类。

    use_llm=None 时：有 LLM_KEY 就用 LLM，否则用启发式。
    返回 [(segment, label), ...]，顺序与输入一致。
    """
    if use_llm is None:
        use_llm = bool(config.LLM_KEY)

    caps: list[tuple[str, str]] = list(existing or [])
    seen: set[str] = {c for c, _ in caps}
    out: list[tuple[Segment, CapacityLabel]] = []

    progress.report("CLASSIFY", total=len(segments), detail=f"use_llm={use_llm}")
    for i, seg in enumerate(segments):
        if use_llm:
            label = classify_segment_llm(seg, caps)
        else:
            label = _heuristic_capacity(seg)
        if label.capacity not in seen:
            seen.add(label.capacity)
            caps.append((label.capacity, label.description))
        out.append((seg, label))
        progress.report("CLASSIFY", current=i + 1, total=len(segments), detail=label.capacity)

    return out
