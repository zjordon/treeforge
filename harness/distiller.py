"""Stage ⑤ DISTILL：``Bucket`` → ``SkillCard``。

【核心分叉点】这是 TreeForge 与 Browser-BC 的主要分歧（init-plan §五/§八）：

  Browser-BC 的 DISTILL_PROMPT：*"produce a single SKILL.md that any browser agent can
  follow to perform this capacity on ANY website — Abstract away site-specific selectors
  and IDs."* —— 产**去站点化的通用 SOP**。

  TreeForge 的 DISTILL_PROMPT（本模块）：反过来——产**站点特定知识卡**，三个维度：

      sop_md        骨架：连贯步骤剧本（host 级，不分 capacity）
      selectors_md  附录：只收需要特征指纹的少数元素
      quirks_md     怪癖：只写 DOM 看不出来的坑

  理由（init-plan §五 + docs/skill-format-alignment.md + docs/skill-simplification-plan.md）：
  browser-harness 文件注入期望「站点特定、拿到就能用」的知识，不是通用 SOP。

【形态演进】原四件套（sop/selectors/quirks/api）经 A/B 测试发现：
  - api.md 无网络采集时恒为「未观察到私有 API」零信息 → 删除
  - 按 capacity 分桶导致同动作流被切成多份，selectors/quirks 重复描述同一批元素 → host 级合并
  - quirks 充斥模型能从 DOM 自己读到的事实（如「立即投稿是 span」）→ 判定标准量化
  详见 docs/skill-simplification-plan.md。

【host 级蒸馏】`distill_buckets` 按 `bucket.domain` 二次聚合，每个 host 调一次 LLM，
产**一份 host 级 SkillCard**。CLASSIFY 产出的 capacity 标签降级为 prompt 的子能力分组提示，
保留信息量但不造成产物割裂。capacity 在 ADAPT/ATOMIZE/CLASSIFY/BUCKET 四阶段仍是健康的归整
维度，不动 classifier/bucketer。

【本期 P0】
  - ``distill_host(host, buckets)`` 调一次 LLM，解析返回，填三个字段
  - 无 LLM_KEY 时退回模板填充（从 segment event_summary 提炼），保证链路不报错
  - 支持增量（host 已有 skill 时，把旧内容塞进 prompt 让 LLM 更新）——P0 简化版
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

# Identified sub-capacities (CLASSIFY output, use as step-grouping hints)
{capacities_line}

The sub-capacities above were identified by a separate CLASSIFY stage. Use them as **soft** grouping \
hints for organizing `sop_md` (e.g. you may group steps under these sub-capacity headings), but do NOT \
treat them as hard boundaries — the whole flow is one continuous action sequence on this site, and \
you should produce ONE coherent procedural narrative, not separate disconnected sections. Do NOT \
duplicate element/quirk descriptions across sub-capacity sections.

# Evidence segments ({n_segments} total, on {domain})
{evidence_blocks}

# Page context (DOM snapshots)
The following are DOM snapshots of the page at different stages (sourced from TreeWalker \
`get_state().dom_state.element_tree_text`). They are the **PRIMARY source for quirks** — use them \
to determine what is and isn't a genuine quirk (see the quirks judgment criteria below).

**How to use this section:**
- Compare the SAME element across stages: does it appear/disappear? does its tag/attrs change?
- Look for **genuine quirks**: things the agent CANNOT tell from reading the DOM text alone \
(hidden dependencies, element identity ambiguities, timing/ordering requirements, SPA stage-transition \
triggers). See full criteria in the `quirks_md` spec below.
- Do NOT use this section to "rediscover" facts the agent can already see in the DOM \
(e.g. "立即投稿 is `<span>`" is visible in the DOM text itself — that is NOT a quirk).
- Cite the stage name when referencing a finding (e.g. "in upload-conver stage, ...").

{page_context_block}

# Output spec — produce THREE markdown sections

1. `sop_md` (skeleton): A **COHERENT PROCEDURAL NARRATIVE** for THIS site's task flow. \
Write it as a step-by-step playbook an agent can execute directly: "step 1: locate element X \
(how to identify it), action Y; step 2: ...". Bind to this site (real URLs, real button labels, \
real placeholder text). This is the entry index file. \
You MAY group steps under the sub-capacity headings from the Identified sub-capacities section, \
but keep the narrative continuous. Describe each element **in place** where the step uses it \
(tag + whitelist attrs + visible text + what to do), so agents don't need to cross-reference \
selectors.md for routine elements.

2. `selectors_md` (appendix, LOW volume): An **ELEMENT DESCRIPTION TABLE** — NOT CSS \
selectors. The consumer is an LLM agent that reads DOM as `[index]<tag attr=val /> text`. \
**Most elements are already described in-place in `sop_md` — this file only collects the FEW \
elements that need a "fingerprint" because they have no unique identity** (e.g. multiple \
`name=buploader` file inputs that must be distinguished by `accept`; an element that only exists \
in a specific stage). If every element is already unambiguously described in sop_md, output a \
short note saying so. Do NOT duplicate elements already covered in sop_md.

   Output a 4-column markdown table:
   | 元素用途 (element purpose) | 怎么找到它 (how to find it) | 稳定标识 (stable identity) | 备注 (notes) |

   Column spec:
   - 元素用途: what this element is for (e.g. "投稿入口", "标题输入框").
   - 怎么找到它: natural-language location/context (e.g. "首页右上角导航区", "标题文字下方").
   - 稳定标识: whitelist attributes + visible text ONLY. Allowed attributes: `id`, `name`, `type`, \
`placeholder`, `aria-label`, `role`, `data-testid`, `data-test`, `data-cy`, `contenteditable`, \
visible text. Format as `attr=value` pairs separated by commas, e.g. `id=nav_upload_btn, 可见文本"投稿"`.
   - 备注: pitfalls, timing notes (e.g. "仅在 upload-conver 阶段出现", "与字幕 input 同名，靠 accept 区分").

   HARD CONSTRAINTS (must obey):
   - Do NOT produce CSS selectors: no `.class-name`, no `div > span`, no `#id` selector syntax, \
no `tag.class:nth-of-type`, no XPath.
   - Only use the 11 whitelist attributes above in 稳定标识. Other attributes (class, style, src, \
href-as-locator) are NOT allowed.
   - If an element has no whitelist attribute, rely on visible text + natural-language location.

3. `quirks_md` (quirks): **Only write quirks the agent CANNOT tell from reading the DOM text alone.** \
This is the SINGLE MOST IMPORTANT file for agent success, and noise here directly hurts performance \
(verified by A/B test). Apply this judgment criteria strictly:

   **WRITE these (DOM-invisible, agent would fail or get confused without them):**
   - Hidden dependencies / sequencing triggers (e.g. "封面上传 input only appears after clicking \
'封面设置' to open the modal — it's not in the DOM during upload stage")
   - Element identity ambiguities (e.g. "page has multiple `name=buploader` file inputs; distinguish \
by `accept`: `.mp4`=video, `.txt`=subtitle, `.zip`=attachment")
   - Action-method requirements (e.g. "hidden file input — MUST use `upload_file(index, path)` \
direct injection, clicking opens an OS dialog the agent can't drive")
   - SPA stage-transition cues (e.g. "URL stays constant across upload/cover/info stages; detect \
stage by DOM content, not URL")
   - Non-AJAX vs AJAX submission behavior (e.g. "'立即投稿' triggers a full-page redirect to \
`/upload/video/success`, not an AJAX toast — wait for navigation, don't immediately re-query")
   - Timing/ordering requirements (e.g. "title input is absent during cover-editing stage; finish \
cover editing first, wait for info-edit stage")

   **Do NOT WRITE these (agent can see them in the DOM text — writing them wastes attention):**
   - Element tag/attribute observations (e.g. "立即投稿 is `<span>` not `<button>`" — the DOM shows \
`[3819]<span />立即投稿`, agent sees this directly)
   - Element-type-vs-expectation mismatches that are visible (e.g. "简介 is `<div contenteditable=true>` \
not `<textarea>`" — DOM shows `[3788]<div contenteditable=true />`, agent sees it)
   - Field labels, placeholders, maxlength — all visible in DOM text
   - Anything that's just "describing what the DOM shows"

   When in doubt, ask: "If the agent reads the DOM text, can it figure this out itself?" If yes, \
do NOT write it. If no (because the fact is about sequencing, hidden state, identity ambiguity, or \
expected action method), write it. Cite the stage name where relevant.

# Rules
- **产出语言：中文**（除非站点元素本身是英文，如 placeholder 文本）。所有说明文字、备注、quirks 描述用中文写，保持与站点语言一致。元素用途/可见文本保留站点原文。
- Use OBSERVED element attributes and visible text from the evidence. Quote them verbatim where possible.
- Do NOT invent selectors, attributes, or endpoints you didn't see. If unsure, say so explicitly.
- If the evidence only contains CSS selectors (legacy trace format), extract any whitelist \
attributes embedded in them (e.g. `input[placeholder='x']` → `placeholder=x`), but PREFER \
describing what the element IS over reusing the selector syntax. Do NOT echo CSS selectors verbatim.
- Keep markdown clean — these files are read by an LLM agent, not a human browser.
- `skill_name`: human-readable name (Title Case). `scope`: one-sentence use case.

Return ONLY JSON in this exact shape:
{{
  "skill_name": "...",
  "scope": "...",
  "sop_md": "# ...\\n\\n...",
  "selectors_md": "# Selectors — {domain}\\n\\n...",
  "quirks_md": "# Quirks — {domain}\\n\\n..."
}}
"""

# 增量蒸馏 addendum（host 已有 skill 时附加）
_INCREMENTAL_ADDENDUM = """\

# EXISTING KNOWLEDGE (distill_version {prev_version})
Update the above EXISTING knowledge based on the NEW segment evidence:
- Keep rules/selectors that are still correct.
- Remove rules contradicted by new evidence.
- Add newly discovered selectors or quirks.
- Do NOT duplicate what's already there.

Previous `sop_md` (truncated to 8000 chars):
```
{prev_sop}
```
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_block(bucket: Bucket) -> str:
    """把桶内 segments 渲染成 evidence blocks 喂给 LLM。

    host 级蒸馏时传入的 bucket 是聚合后的合并 bucket（segments 含所有 capacity 的事件），
    所以这里天然支持多 capacity——只渲染 segments，不关心 capacity 边界。
    """
    parts: list[str] = []
    for idx, seg in enumerate(bucket.segments, start=1):
        entry = seg.entry_url or "?"
        exit_ = seg.exit_url or "?"
        label = bucket.capacity_labels[0] if bucket.capacity_labels else None
        outcome = label.outcome if label else "success"
        # stage 聚合（阶段 4）：该 segment 涉及哪些页面阶段，喂给 LLM 做精确锚点。
        # 带? 的是推断阶段；LLM 对照 # Page context 段看对应快照。
        stages = sorted({ev.stage for ev in seg.events if ev.stage})
        stages_line = f"Stages: {', '.join(stages)}" if stages else "Stages: (unknown)"
        parts.append(
            f"### Segment {idx} (id={seg.segment_id})\n"
            f"Entry: {entry} → Exit: {exit_} | Outcome: {outcome}\n"
            f"{stages_line}\n"
            f"Events ({len(seg.events)} total):\n"
            f"```\n{seg.event_summary or '(empty)'}\n```\n---"
        )
    return "\n\n".join(parts) if parts else "(no segments)"


def _merge_buckets_for_host(host: str, buckets: list[Bucket]) -> Bucket:
    """把同 host 的多个 capacity bucket 合并成一个 host 级 bucket。

    用于 host 级蒸馏：capacity 在 DISTILL 阶段不再作为产物维度，所以把同 host 所有
    bucket 的 segments 拼进一个聚合 bucket，evidence_block 渲染时天然连续。
    canonical_capacity 字段拼成 capacity 列表（如 "upload-video, fill-video-metadata"），
    作为 prompt 的子能力分组提示。
    """
    all_segments: list = []
    all_labels: list = []
    capacity_names: list[str] = []
    total_version = 0
    last_distilled: str | None = None
    for b in buckets:
        all_segments.extend(b.segments)
        all_labels.extend(b.capacity_labels)
        if b.canonical_capacity and b.canonical_capacity not in capacity_names:
            capacity_names.append(b.canonical_capacity)
        total_version = max(total_version, b.distill_version)
        if b.last_distilled_at and (last_distilled is None or b.last_distilled_at > last_distilled):
            last_distilled = b.last_distilled_at

    return Bucket(
        bucket_id=f"{host}::__host__",
        domain=host,
        canonical_capacity=", ".join(capacity_names) if capacity_names else host,
        description=f"Host-level merge of {len(buckets)} capacity bucket(s): {', '.join(capacity_names)}",
        segment_ids=[s.segment_id for s in all_segments],
        segments=all_segments,
        capacity_labels=all_labels,
        distill_version=total_version,
        last_distilled_at=last_distilled,
    )


def _render_page_context(page_context: dict[str, str]) -> str:
    """把 trace.page_context（阶段名→DOM 文本）渲染成 prompt 段。

    空 dict 返回占位（distiller 跳过 DOM 推断）。非空按阶段渲染，
    让 LLM 看到每个页面阶段的 DOM 快照，重点对照跨阶段差异推 quirks。
    """
    if not page_context:
        return "(no DOM snapshots provided — quirks 只能从操作时序推)"
    parts: list[str] = []
    for stage, dom_text in page_context.items():
        n_chars = len(dom_text or "")
        parts.append(f"## Stage: {stage} ({n_chars} chars)\n{dom_text or '(empty)'}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 模板 fallback（无 LLM 时用）
# ---------------------------------------------------------------------------


# element_attrs 里渲染到「稳定标识」列的属性顺序（对齐 distiller prompt 的白名单）
_TEMPLATE_ATTR_ORDER: tuple[str, ...] = (
    "id",
    "name",
    "type",
    "placeholder",
    "aria-label",
    "role",
    "data-testid",
    "data-test",
    "data-cy",
    "contenteditable",
)


def _format_stable_identity(element_attrs: dict) -> str:
    """把 element_attrs 渲染成「稳定标识」列文本：`id=x, name=y, 可见文本"z"`。"""
    parts: list[str] = []
    for k in _TEMPLATE_ATTR_ORDER:
        v = element_attrs.get(k)
        if v in (None, "", False):
            continue
        parts.append(f"{k}={v}")
    vt = element_attrs.get("visible_text")
    if vt:
        parts.append(f'可见文本"{vt}"')
    return ", ".join(parts)


def _render_template_element_table(domain: str, capacity: str, elements: list[dict]) -> str:
    """新格式：把带 element_attrs 的元素渲染成元素描述表四列。

    对齐 LLM 模式 prompt 的 selectors_md 要求。模板模式虽不如 LLM 智能，
    但至少能产出结构正确的表（不依赖 selector 字符串）。
    """
    header = f"# Selectors — {domain}\n\n"
    header += (
        "> 模板模式产出（--no-llm），基于 trace 的 element_attrs 字段。"
        "以下为机械提炼的元素描述表，配置 LLM_KEY 用 LLM 模式可获更智能的描述。\n\n"
    )
    header += f"为 `{capacity}` 观察到的元素：\n\n"
    header += "| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |\n"
    header += "|---|---|---|---|\n"
    rows: list[str] = []
    for el in elements:
        ea = el["element_attrs"]
        purpose = el["target"] or ea.get("visible_text") or el["ev_type"]
        # 「怎么找到它」模板模式只能给 tag + 是否可见，位置上下文需 LLM 模式才能给
        tag = ea.get("tag") or "?"
        visible = ea.get("visible")
        location = f"<{tag}>"
        if visible is not None:
            location += f" (visible={visible})"
        stable = _format_stable_identity(ea) or "(无白名单属性)"
        note = el["ev_type"]
        rows.append(f"| {purpose} | {location} | {stable} | {note} |")
    return header + "\n".join(rows)


def _render_template_selector_fallback(domain: str, capacity: str, selectors: list[str]) -> str:
    """老格式 fallback：trace 只有 selector（无 element_attrs）时用。

    保留 P1 的警告头注，说明质量低、引导用 LLM 模式或带 element_attrs 的 trace。
    """
    notice = (
        f"# Selectors — {domain}\n\n"
        "> ⚠️ **模板模式产出（--no-llm）**。当前 trace 格式只有 CSS selector 字符串，"
        "无法产出真正的元素描述表。以下为机械列出的 selector，与真实 DOM 可能对不上。\n"
        "> 配置 LLM_KEY 用 LLM 模式可产出符合新格式的元素描述表（4 列："
        "元素用途 / 怎么找到它 / 稳定标识 / 备注），详见 "
        "`docs/skill-format-alignment.md`。\n\n"
    )
    if selectors:
        return (
            notice
            + f"Observed selectors for `{capacity}`:\n\n"
            + "\n".join(f"- `{s}`" for s in selectors)
        )
    return notice + f"No selectors observed for `{capacity}`."


def _template_skill_card(bucket: Bucket) -> SkillCard:
    """无 LLM 时从 segment events 提炼一个最小可用的 SkillCard。

    目的：让 P0 链路在没配 LLM 的情况下也能产出非空文件，验证 adapter/install 正确。
    产物质量低，但结构完整。

    【阶段 2 双轨】
      - 若 events 带 element_attrs（新格式）：产出元素描述表四列（对齐 LLM 模式 prompt）
      - 若 events 只有 selector（老格式）：保留 P1 警告头注 + 机械列 selector

    【host 级】bucket 可能是 host 级聚合（canonical_capacity 是 capacity 列表），
    也可能是单 capacity bucket（向后兼容老测试）。模板模式不区分，统一渲染。
    """
    # 收集元素（双轨）：优先收集 element_attrs，同时兜底 selector + url
    seen_keys: set[tuple] = set()
    elements: list[dict] = []  # 每个 dict 含 element_attrs / target / selector / ev_type
    selectors: list[str] = []
    urls: list[str] = []
    for seg in bucket.segments:
        for ev in seg.events:
            if ev.url and ev.url not in urls:
                urls.append(ev.url)
            if ev.element_attrs:
                # 用 (tag, id, name, placeholder) 元组去重
                ea = ev.element_attrs
                key = (
                    ea.get("tag"),
                    ea.get("id"),
                    ea.get("name"),
                    ea.get("placeholder"),
                    ea.get("aria-label"),
                    ea.get("visible_text"),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    elements.append(
                        {
                            "element_attrs": ea,
                            "target": ev.target or "",
                            "ev_type": ev.type,
                        }
                    )
            elif ev.selector and ev.selector not in selectors:
                selectors.append(ev.selector)

    domain = bucket.domain
    capacity = bucket.canonical_capacity

    # selectors.md —— 双轨渲染
    if elements:
        # 新格式：元素描述表四列
        selectors_md = _render_template_element_table(domain, capacity, elements)
    else:
        # 老格式：警告头注 + selector 列表
        selectors_md = _render_template_selector_fallback(domain, capacity, selectors)

    # sop.md（从 event_summary 直接抽前若干行）
    summary_lines: list[str] = []
    for seg in bucket.segments:
        summary_lines.extend((seg.event_summary or "").splitlines())
    summary_trimmed = "\n".join(summary_lines[:30]) if summary_lines else "(no events)"
    sop_md = (
        f"# SOP — {domain}\n\n"
        f"Template distill (no LLM). Observed event sequence:\n\n"
        f"```\n{summary_trimmed}\n```\n"
    )

    quirks_md = (
        f"# Quirks — {domain}\n\n"
        f"_No quirks extracted (template mode — configure LLM for real distillation)._"
    )

    return SkillCard(
        bucket_id=bucket.bucket_id,
        domain=domain,
        capacity=capacity,
        skill_name=domain.split(".")[0].title(),
        scope=f"Template distillation on {domain}.",
        sop_md=sop_md,
        selectors_md=selectors_md,
        quirks_md=quirks_md,
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


def distill_host(
    host: str,
    buckets: list[Bucket],
    *,
    use_llm: bool | None = None,
    page_context: dict[str, str] | None = None,
) -> SkillCard:
    """蒸馏一个 host 的所有 capacity bucket → 一份 host 级 SkillCard。

    host 级蒸馏（docs/skill-simplification-plan.md 决策 1）：
    - 把同 host 所有 bucket 的 segments 合并，一次 LLM 调用看整条流程
    - capacity 标签降级为 prompt 的子能力分组提示，不作为产物维度
    - 产出连贯步骤剧本（sop_md），无 capacity 割裂

    use_llm=None 时：有 LLM_KEY 就用 LLM，否则用模板 fallback。
    page_context: trace 级 DOM 快照（阶段名→DOM 文本）。LLM 路径注入 prompt 让它推 quirks。
    """
    if use_llm is None:
        use_llm = bool(config.LLM_KEY)

    merged = _merge_buckets_for_host(host, buckets)

    progress.report(
        "DISTILL",
        detail=f"host={host} buckets={len(buckets)} segments={len(merged.segments)} use_llm={use_llm}",
    )

    if not use_llm:
        card = _template_skill_card(merged)
        progress.report("DISTILL", detail=f"→ template card for host={host}")
        return card

    # capacity 列表渲染为 prompt 的子能力分组提示
    capacity_names = [b.canonical_capacity for b in buckets if b.canonical_capacity]
    # 去重保序
    seen: set[str] = set()
    unique_caps: list[str] = []
    for c in capacity_names:
        if c not in seen:
            seen.add(c)
            unique_caps.append(c)
    capacities_line = (
        ", ".join(f"`{c}`" for c in unique_caps) if unique_caps else "(none — single flow)"
    )

    prompt = _DISTILL_PROMPT_TEMPLATE.format(
        domain=host,
        capacities_line=capacities_line,
        n_segments=len(merged.segments),
        evidence_blocks=_evidence_block(merged),
        page_context_block=_render_page_context(page_context or {}),
    )

    # 增量：如果 host 已经蒸馏过，把旧 sop 塞进去
    if merged.distill_version > 0 and merged.last_distilled_at:
        prev_sop = ""
        # P0：我们没有持久化旧 SkillCard，这里只放占位提示（P1+ 接 registry 后补）
        prompt += _INCREMENTAL_ADDENDUM.format(
            prev_version=merged.distill_version,
            prev_sop=prev_sop or "(previous skill not available in P0)",
        )[:8000]

    try:
        text, usage = call_llm(prompt, system=_DISTILL_SYSTEM)
        data = parse_json_from_model(text)
        card = SkillCard(
            bucket_id=merged.bucket_id,
            domain=host,
            capacity=merged.canonical_capacity,  # capacity 列表，作为 meta 索引
            skill_name=str(data.get("skill_name") or host).strip(),
            scope=str(data.get("scope") or "").strip(),
            sop_md=str(data.get("sop_md") or "").strip(),
            selectors_md=str(data.get("selectors_md") or "").strip(),
            quirks_md=str(data.get("quirks_md") or "").strip(),
            meta={
                "model": config.DISTILL_MODEL,
                "usage": usage,
                "segment_count": len(merged.segments),
                "domains": [host],
                "capacities": unique_caps,
                "distill_version": merged.distill_version + 1,
                "distilled_at": _now_iso(),
            },
        )
        # 兜底：如果 LLM 返回了空字段，用模板补，保证三个文件非空
        if not any([card.sop_md, card.selectors_md, card.quirks_md]):
            progress.report("DISTILL", detail="LLM returned all-empty, falling back to template")
            return _template_skill_card(merged)
        progress.report("DISTILL", detail=f"→ card for host={host}")
        return card
    except Exception as e:  # noqa: BLE001 - 蒸馏失败退回模板，不阻断管线
        progress.report("DISTILL", detail=f"LLM failed ({e!r}), falling back to template")
        return _template_skill_card(merged)


def distill_bucket(
    bucket: Bucket,
    *,
    use_llm: bool | None = None,
    page_context: dict[str, str] | None = None,
) -> SkillCard:
    """蒸馏单个 bucket → SkillCard（向后兼容入口）。

    内部走 host 级逻辑：把单个 bucket 包成 host 下只有一个 capacity 的退化场景。
    新代码应直接用 distill_host 或 distill_buckets。

    use_llm=None 时：有 LLM_KEY 就用 LLM，否则用模板 fallback。
    page_context: trace 级 DOM 快照（阶段名→DOM 文本）。
    """
    return distill_host(
        bucket.domain,
        [bucket],
        use_llm=use_llm,
        page_context=page_context,
    )


def distill_buckets(
    buckets: list[Bucket],
    *,
    use_llm: bool | None = None,
    page_context: dict[str, str] | None = None,
) -> list[SkillCard]:
    """蒸馏多个 bucket → host 级 SkillCard 列表（按 host 合并）。

    host 级蒸馏：按 bucket.domain 分组，每个 host 产一份 SkillCard。
    page_context 从 trace 透传给每个 host 的 distill_host。
    """
    # 按 host 分组
    by_host: dict[str, list[Bucket]] = {}
    for b in buckets:
        by_host.setdefault(b.domain, []).append(b)

    hosts = list(by_host.keys())
    out: list[SkillCard] = []
    progress.report("DISTILL", total=len(hosts))
    for i, host in enumerate(hosts):
        host_buckets = by_host[host]
        # 过滤掉 segment 数全不达标的 host（P0 单 trace 一般都达标）
        total_segments = sum(len(b.segment_ids) for b in host_buckets)
        if total_segments < config.MIN_BUCKET_SIZE:
            progress.report(
                "DISTILL",
                current=i + 1,
                total=len(hosts),
                detail=f"skip host={host} (too small: {total_segments} segments)",
            )
            continue
        card = distill_host(host, host_buckets, use_llm=use_llm, page_context=page_context)
        out.append(card)
        progress.report("DISTILL", current=i + 1, total=len(hosts), detail=f"host={host}")
    return out
