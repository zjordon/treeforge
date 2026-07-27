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

# Page context (DOM snapshots)
The following are DOM snapshots of the page at different stages (sourced from TreeWalker \
`get_state().dom_state.element_tree_text`). Use them to understand what elements exist on the page \
AT EACH STAGE — this is the PRIMARY source for quirks (cross-stage differences, element-type surprises).

**How to use this section:**
- Compare the SAME element across stages: does it appear/disappear? does its tag/attrs change? \
(e.g. "标题框在封面阶段不在 DOM，publish 阶段才出现" = a quirk worth recording)
- Look for elements whose tag/attrs contradict common assumptions (e.g. "立即投稿" is `<span>` not \
`<button>`; 创作声明 is `<input type=text>` not `<radio>`; 简介 is `<div contenteditable>` not `<textarea>`)
- Cite the stage name when referencing a finding (e.g. "in upload-conver stage, ...")

{page_context_block}

# Output spec — produce FOUR markdown sections

1. `sop_md` (skeleton, LOW volume): The 1-3 common task flows for THIS site that this capacity covers. \
Browser-BC-style distilled procedure, but bound to this site (real URLs, real button labels). \
This is the entry index file.

2. `selectors_md` (flesh, HIGH volume, actionable): An **ELEMENT DESCRIPTION TABLE** — NOT CSS \
selectors. The consumer is an LLM agent that reads DOM as `[index]<tag attr=val /> text`, so \
describe elements in the language that LLM can match against that DOM text. This is the most \
important file — agents reach for it first. **只收录 `{capacity}` 实际涉及的元素**——不要把整站所有元素都列进来，跨 capacity 的元素由各自的桶覆盖。

   Output a 4-column markdown table:
   | 元素用途 (element purpose) | 怎么找到它 (how to find it) | 稳定标识 (stable identity) | 备注 (notes) |

   Column spec:
   - 元素用途: what this element is for (e.g. "投稿入口", "标题输入框").
   - 怎么找到它: natural-language location/context (e.g. "首页右上角导航区", "标题文字下方").
   - 稳定标识: whitelist attributes + visible text ONLY. Allowed attributes: `id`, `name`, `type`, \
`placeholder`, `aria-label`, `role`, `data-testid`, `data-test`, `data-cy`, `contenteditable`, \
visible text. Format as `attr=value` pairs separated by commas, e.g. `id=nav_upload_btn, 可见文本"投稿"`.
   - 备注: pitfalls, timing notes, element-type surprises (e.g. "是 span 不是 button", "隐藏 input").

   HARD CONSTRAINTS (must obey):
   - Do NOT produce CSS selectors: no `.class-name`, no `div > span`, no `#id` selector syntax, \
no `tag.class:nth-of-type`, no XPath.
   - Only use the 11 whitelist attributes above in 稳定标识. Other attributes (class, style, src, \
href-as-locator) are NOT allowed.
   - If an element has no whitelist attribute, rely on visible text + natural-language location.

   After the table, you MAY add a "## 元素识别要点" section with general tips for the LLM \
(e.g. "靠可见文本优先", "警惕同名 file input").

3. `quirks_md` (quirks): **PRIORITIZE quirks inferred from the # Page context DOM snapshots** — \
cross-stage element differences (element appears/disappears across stages), tag/attribute surprises \
(span acting as button, input acting as radio, contenteditable instead of textarea), and visibility \
changes. ALSO include: hidden waits (e.g. "点投稿后等 DOM 渲染"、wait for networkidle), SPA \
navigation patterns (history.pushState routes, URL unchanged across stages), framework behavior \
(React/Vue re-render timing), anti-bot detection signals (Cloudflare challenge, rate limits). \
**只收录与 `{capacity}` 操作直接相关的 quirks**——一个站点会被蒸馏成多个 capacity，每个只覆盖自己范围内用到的元素，避免跨 capacity 重复（例如「立即投稿是 span」只在涉及提交动作的 capacity 里写，导航类 capacity 不写表单元素怪癖）。Only include quirks you actually observe or can strongly infer. Cite the stage name where relevant.

4. `api_md` (private API): Any internal/private API calls, XHR/fetch endpoints, URL patterns, \
hidden endpoints observed. If none observed, output a one-line note saying so rather than inventing.

# Rules
- **产出语言：中文**（除非站点元素本身是英文，如 placeholder 文本）。所有说明文字、备注、quirks 描述用中文写，保持与站点语言一致。元素用途/可见文本保留站点原文。
- **聚焦本 capacity**：selectors_md / quirks_md 只写与 `{capacity}` 强相关的元素和怪癖，不要把整站通用的知识塞进每个 capacity。同一站点可能蒸馏出多个 capacity（如 navigate-to-upload / fill-form / publish），各自只覆盖自己范围内涉及的元素——避免跨 capacity 内容重复。
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
    "id", "name", "type", "placeholder", "aria-label", "role",
    "data-testid", "data-test", "data-cy", "contenteditable",
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


def _render_template_selector_fallback(
    domain: str, capacity: str, selectors: list[str]
) -> str:
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
        return notice + f"Observed selectors for `{capacity}`:\n\n" + "\n".join(
            f"- `{s}`" for s in selectors
        )
    return notice + f"No selectors observed for `{capacity}`."


def _template_skill_card(bucket: Bucket) -> SkillCard:
    """无 LLM 时从 segment events 提炼一个最小可用的 SkillCard。

    目的：让 P0 链路在没配 LLM 的情况下也能产出非空文件，验证 adapter/install 正确。
    产物质量低，但结构完整。

    【阶段 2 双轨】
      - 若 events 带 element_attrs（新格式）：产出元素描述表四列（对齐 LLM 模式 prompt）
      - 若 events 只有 selector（老格式）：保留 P1 警告头注 + 机械列 selector
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
                    elements.append({
                        "element_attrs": ea,
                        "target": ev.target or "",
                        "ev_type": ev.type,
                    })
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
    page_context: dict[str, str] | None = None,
) -> SkillCard:
    """蒸馏一个桶 → SkillCard。

    use_llm=None 时：有 LLM_KEY 就用 LLM，否则用模板 fallback。
    page_context: trace 级 DOM 快照（阶段名→DOM 文本）。LLM 路径注入 prompt 让它推 quirks；
                  模板路径忽略（机械提炼不出 quirks）。bucket 是 capacity 级不带此字段，
                  由调用方从 trace 透传。
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
        page_context_block=_render_page_context(page_context or {}),
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
    page_context: dict[str, str] | None = None,
) -> list[SkillCard]:
    """蒸馏多个桶。page_context 从 trace 透传给每个桶的 distill_bucket。"""
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
        card = distill_bucket(b, use_llm=use_llm, page_context=page_context)
        out.append(card)
        progress.report("DISTILL", current=i + 1, total=len(buckets), detail=b.bucket_id)
    return out
