"""Stage ⑤ DISTILL：``Bucket`` → ``SkillCard``。

【核心分叉点】这是 TreeForge 与 Browser-BC 的主要分歧（init-plan §五/§八）：

  Browser-BC 的 DISTILL_PROMPT：*"produce a single SKILL.md that any browser agent can
  follow to perform this capacity on ANY website — Abstract away site-specific selectors
  and IDs."* —— 产**去站点化的通用 SOP**。

  TreeForge 的 DISTILL_PROMPT（本模块）：反过来——产**站点特定知识卡**，四个维度：

      sop_md        骨架：这个站点常见任务流程（量少，Browser-BC 风格蒸馏但绑定本站）
      selectors_md  血肉：稳定 selector、AX name、元素定位（量大、可操作、拿到就能用）
      quirks_md     怪癖：隐藏等待、SPA 导航、框架行为、反爬检测
      api_md        私有 API、URL 模式、隐藏端点

  理由（init-plan §五）：browser-harness 文件注入期望「站点特定、拿到就能用」的知识，
  不是通用 SOP。

【本期 P0】
  - ``distill_bucket(bucket)`` 调一次 LLM，解析返回，填四个字段
  - 无 LLM_KEY 时退回模板填充（从 segment event_summary 提炼），保证链路不报错
  - 支持增量（bucket 已有 skill 时，把旧内容塞进 prompt 让 LLM 更新）——P0 简化版
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import config, progress
from .llm import call_llm, parse_json_from_model
from .models import Bucket, SkillCard

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_DISTILL_SYSTEM = (
    "You are a site-specific knowledge distiller for browser automation. "
    "You produce READY-TO-USE site knowledge, NOT generic SOPs. "
    "Output STRICT JSON only, no prose, no markdown fences."
)

_DISTILL_PROMPT_TEMPLATE = """\
Distill the following browser interaction evidence into a **site-specific knowledge card** for `{domain}`.

# CRITICAL: This is the OPPOSITE of generic skill authoring
Do NOT abstract away site-specific selectors, IDs, or URLs. Capture them — this knowledge will be \
file-injected into a browser agent when it navigates to `{domain}`, and it must be actionable as-is. \
"Record the map, not the diary."

# Domain
`{domain}`

# Capacity
`{capacity}` — {capacity_desc}

# Evidence segments ({n_segments} total, on {domain})
{evidence_blocks}

# Output spec — produce FOUR markdown sections

1. `sop_md` (skeleton, LOW volume): The 1-3 common task flows for THIS site that this capacity covers. \
Browser-BC-style distilled procedure, but bound to this site (real URLs, real button labels). \
This is the entry index file.

2. `selectors_md` (flesh, HIGH volume, actionable): Every stable selector you can extract from the \
evidence. PREFER in this order: `data-testid` / `data-cy` / `aria-label` / `name` / `[role]` / \
`#id` / `tag.class:nth-of-type` / XPath. Include the accessibility name (AX name) where known. \
Format as a markdown table or bullet list: `selector | what it is | notes`. This is the most \
important file — agents reach for it first.

3. `quirks_md` (quirks): Hidden waits (e.g. "wait for networkidle after clicking Publish — the \
success toast is async"), SPA navigation patterns (history.pushState routes), framework behavior \
(React/Vue re-render timing), anti-bot detection signals (Cloudflare challenge, rate limits). \
Only include quirks you actually observe or can strongly infer from the evidence.

4. `api_md` (private API): Any internal/private API calls, XHR/fetch endpoints, URL patterns, \
hidden endpoints observed. If none observed, output a one-line note saying so rather than inventing.

# Rules
- Use OBSERVED selectors and URLs from the evidence. Quote them verbatim where possible.
- Do NOT invent selectors or endpoints you didn't see. If unsure, say so explicitly.
- Keep markdown clean — these files are read by an LLM agent, not a human browser.
- `skill_name`: human-readable name (Title Case). `scope`: one-sentence use case.

Return ONLY JSON in this exact shape:
{{
  "skill_name": "...",
  "scope": "...",
  "sop_md": "# ...\\n\\n...",
  "selectors_md": "# Selectors — {domain}\\n\\n...",
  "quirks_md": "# Quirks — {domain}\\n\\n...",
  "api_md": "# API — {domain}\\n\\n..."
}}
"""

# 增量蒸馏 addendum（bucket 已有 skill 时附加）
_INCREMENTAL_ADDENDUM = """\

# EXISTING KNOWLEDGE (distill_version {prev_version})
Update the above EXISTING knowledge based on the NEW segment evidence:
- Keep rules/selectors that are still correct.
- Remove rules contradicted by new evidence.
- Add newly discovered selectors, quirks, or endpoints.
- Do NOT duplicate what's already there.

Previous `sop_md` (truncated to 8000 chars):
```
{prev_sop}
```
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_block(bucket: Bucket) -> str:
    """把桶内 segments 渲染成 evidence blocks 喂给 LLM。"""
    parts: list[str] = []
    for idx, seg in enumerate(bucket.segments, start=1):
        entry = seg.entry_url or "?"
        exit_ = seg.exit_url or "?"
        label = bucket.capacity_labels[0] if bucket.capacity_labels else None
        outcome = label.outcome if label else "success"
        parts.append(
            f"### Segment {idx} (id={seg.segment_id})\n"
            f"Entry: {entry} → Exit: {exit_} | Outcome: {outcome}\n"
            f"Events ({len(seg.events)} total):\n"
            f"```\n{seg.event_summary or '(empty)'}\n```\n---"
        )
    return "\n\n".join(parts) if parts else "(no segments)"


# ---------------------------------------------------------------------------
# 模板 fallback（无 LLM 时用）
# ---------------------------------------------------------------------------


def _template_skill_card(bucket: Bucket) -> SkillCard:
    """无 LLM 时从 segment event_summary 提炼一个最小可用的 SkillCard。

    目的：让 P0 链路在没配 LLM 的情况下也能产出非空文件，验证 adapter/install 正确。
    产物质量低，但结构完整。
    """
    # 收集所有 selector
    selectors: list[str] = []
    urls: list[str] = []
    for seg in bucket.segments:
        for ev in seg.events:
            if ev.selector and ev.selector not in selectors:
                selectors.append(ev.selector)
            if ev.url and ev.url not in urls:
                urls.append(ev.url)

    domain = bucket.domain
    capacity = bucket.canonical_capacity

    # selectors.md
    if selectors:
        sel_lines = []
        for sel in selectors:
            # 找一个匹配的 event 作为注释
            note = ""
            for seg in bucket.segments:
                for ev in seg.events:
                    if ev.selector == sel:
                        note = ev.target or ev.type
                        break
                if note:
                    break
            sel_lines.append(f"- `{sel}` — {note}" if note else f"- `{sel}`")
        selectors_md = f"# Selectors — {domain}\n\nObserved for `{capacity}`:\n\n" + "\n".join(sel_lines)
    else:
        selectors_md = f"# Selectors — {domain}\n\nNo stable selectors observed."

    # sop.md（从 event_summary 直接抽前若干行）
    summary_lines: list[str] = []
    for seg in bucket.segments:
        summary_lines.extend((seg.event_summary or "").splitlines())
    summary_trimmed = "\n".join(summary_lines[:30]) if summary_lines else "(no events)"
    sop_md = (
        f"# SOP — {domain} / {capacity}\n\n"
        f"Template distill (no LLM). Observed event sequence:\n\n"
        f"```\n{summary_trimmed}\n```\n"
    )

    quirks_md = f"# Quirks — {domain}\n\n_No quirks extracted (template mode — configure LLM for real distillation)._"
    api_md = f"# API — {domain}\n\nObserved URLs:\n\n" + "\n".join(f"- `{u}`" for u in urls[:20]) if urls else f"# API — {domain}\n\nNo URLs observed."

    return SkillCard(
        bucket_id=bucket.bucket_id,
        domain=domain,
        capacity=capacity,
        skill_name=capacity.replace("-", " ").title(),
        scope=f"Template distillation of `{capacity}` on {domain}.",
        sop_md=sop_md,
        selectors_md=selectors_md,
        quirks_md=quirks_md,
        api_md=api_md,
        meta={
            "model": "(template)",
            "usage": {},
            "segment_count": len(bucket.segments),
            "domains": [domain],
            "distill_version": bucket.distill_version + 1,
            "distilled_at": _now_iso(),
        },
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def distill_bucket(
    bucket: Bucket,
    *,
    use_llm: bool | None = None,
) -> SkillCard:
    """蒸馏一个桶 → SkillCard。

    use_llm=None 时：有 LLM_KEY 就用 LLM，否则用模板 fallback。
    """
    if use_llm is None:
        use_llm = bool(config.LLM_KEY)

    progress.report(
        "DISTILL",
        detail=f"bucket={bucket.bucket_id} segments={len(bucket.segments)} use_llm={use_llm}",
    )

    if not use_llm:
        card = _template_skill_card(bucket)
        progress.report("DISTILL", detail=f"→ template card for {bucket.bucket_id}")
        return card

    prompt = _DISTILL_PROMPT_TEMPLATE.format(
        domain=bucket.domain,
        capacity=bucket.canonical_capacity,
        capacity_desc=bucket.description or "(no description)",
        n_segments=len(bucket.segments),
        evidence_blocks=_evidence_block(bucket),
    )

    # 增量：如果 bucket 已经蒸馏过，把旧 sop 塞进去
    if bucket.distill_version > 0 and bucket.last_distilled_at:
        prev_sop = ""
        # P0：我们没有持久化旧 SkillCard，这里只放占位提示（P1+ 接 registry 后补）
        prompt += _INCREMENTAL_ADDENDUM.format(
            prev_version=bucket.distill_version,
            prev_sop=prev_sop or "(previous skill not available in P0)",
        )[:8000]

    try:
        text, usage = call_llm(prompt, system=_DISTILL_SYSTEM)
        data = parse_json_from_model(text)
        card = SkillCard(
            bucket_id=bucket.bucket_id,
            domain=bucket.domain,
            capacity=bucket.canonical_capacity,
            skill_name=str(data.get("skill_name") or bucket.canonical_capacity).strip(),
            scope=str(data.get("scope") or "").strip(),
            sop_md=str(data.get("sop_md") or "").strip(),
            selectors_md=str(data.get("selectors_md") or "").strip(),
            quirks_md=str(data.get("quirks_md") or "").strip(),
            api_md=str(data.get("api_md") or "").strip(),
            meta={
                "model": config.DISTILL_MODEL,
                "usage": usage,
                "segment_count": len(bucket.segments),
                "domains": [bucket.domain],
                "distill_version": bucket.distill_version + 1,
                "distilled_at": _now_iso(),
            },
        )
        # 兜底：如果 LLM 返回了空字段，用模板补，保证四个文件非空
        if not any([card.sop_md, card.selectors_md, card.quirks_md, card.api_md]):
            progress.report("DISTILL", detail="LLM returned all-empty, falling back to template")
            return _template_skill_card(bucket)
        progress.report("DISTILL", detail=f"→ card for {bucket.bucket_id}")
        return card
    except Exception as e:  # noqa: BLE001 - 蒸馏失败退回模板，不阻断管线
        progress.report("DISTILL", detail=f"LLM failed ({e!r}), falling back to template")
        return _template_skill_card(bucket)


def distill_buckets(
    buckets: list[Bucket],
    *,
    use_llm: bool | None = None,
) -> list[SkillCard]:
    """蒸馏多个桶。"""
    out: list[SkillCard] = []
    progress.report("DISTILL", total=len(buckets))
    for i, b in enumerate(buckets):
        # 只蒸馏 segment 数达标 + dirty 的桶（P0 单 trace 一般都达标）
        if len(b.segment_ids) < config.MIN_BUCKET_SIZE:
            progress.report(
                "DISTILL",
                current=i + 1,
                total=len(buckets),
                detail=f"skip {b.bucket_id} (too small)",
            )
            continue
        card = distill_bucket(b, use_llm=use_llm)
        out.append(card)
        progress.report("DISTILL", current=i + 1, total=len(buckets), detail=b.bucket_id)
    return out
