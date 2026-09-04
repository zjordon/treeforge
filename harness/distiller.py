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

import logging
import re
from datetime import UTC, datetime
from typing import Any

from . import config, progress
from .llm import call_llm, parse_json_from_model
from .models import Bucket, SkillCard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_DISTILL_SYSTEM = (
    "You are a site-specific knowledge distiller for browser automation. "
    "You produce READY-TO-USE site knowledge, NOT generic SOPs. "
    "Output STRICT JSON only, no prose, no markdown fences."
)

# ---------------------------------------------------------------------------
# 消费端上下文（P3.7）——蒸馏 LLM 需要知道消费端（TreeWalker agent）的能力边界
# ---------------------------------------------------------------------------
# 镜像自 TreeWalker src/tree_walker/tools/models.py ACTION_DEFINITIONS（24 动作）+
# browser/session.py 自动兜底逻辑。TreeWalker 动作集稳定（变化慢），维护一份 prompt
# 片段成本可控。改 TreeWalker 动作集时需手动同步此处——test_distiller 有测试钉关键动作名。
#
# 【为什么需要这个】原 prompt 只说「消费端是读 [index]<tag attr=val /> text DOM 的 agent」
# （感知模型），完全没说能做什么动作（能力模型）——"tool" 在 prompt 里出现 0 次，唯一动作名
# upload_file 埋在一个 quirks 示例里。结果 sop 动词落不到真实 tool 名，quirks 该写「upload_file
# 直注」这类方法要求的也容易漏（A/B 测蒸馏版比手写差 38% 主要就漏这条）。
#
# 【关键洞察：「别写」比「能做」更值钱】agent 有很多能力是 session 层自动处理的（JS 点击/
# 遮挡、下拉降级、多 file input 元数据），写进 skill 反而浪费 token 甚至误导。所以这里同时给
# 「动作词汇」（让 sop/quirks 引用真实动作名）+「已自动处理别写」（避免废话）。
_CONSUMER_CONTEXT = """\
# Consumer context — the agent that will use this skill

The consumer is **TreeWalker agent**. It drives the browser via a fixed action vocabulary \
(`ACTION_DEFINITIONS`, 24 actions total). When you describe a method requirement (e.g. \
"must upload via direct injection"), reference the action by its EXACT name and signature \
so the agent recognizes it. The signatures below are the authoritative params.

## Action vocabulary (distill-relevant subset, verbatim from ACTION_DEFINITIONS)

Navigation / tabs:
- `navigate(url, new_tab=false)` — go to a URL in the current tab, or open in a new tab.
- `go_back()` — navigate back to the previous page in history.
- `switch_tab(tab_id)` — switch to a different browser tab (tab_id = last 4 chars of the id).
- `close_tab(tab_id="")` — close a browser tab ("" = current).

Element interaction (target by `index` from the DOM tree, or `element_id` from find_elements):
- `click(index=…)` — click an element by its index. index === backend_node_id.
- `input_text(index=…, text, clear=true)` — type into `<input>`/`<textarea>`/`contenteditable`; \
`clear=true` clears existing text first.
- `send_keys(keys)` — key combos / named keys. Combos use '+': `Control+a`, `Shift+T`, `Alt+F4`. \
Named keys: `Enter`, `Tab`, `Escape`, `ArrowUp`, `F5`. Plain text is typed char-by-char. \
Use this for non-printable keys (e.g. a tag input that commits with `Enter`).

Dropdowns — `select_dropdown` works on ALL of these (not just native `<select>`):
native `<select>`, `role=combobox`, `role=listbox`, **and custom dropdowns built from `<div>`/`<span>`/`<ul>`**. Many sites (e.g. 抖音/飞书) render dropdowns as `<div>`+`<span>` lists rather than `<select>` — `select_dropdown` STILL applies; do NOT conclude "must use click because it's not a `<select>`".
- `dropdown_options(index)` — get all options from a dropdown element (any of the above types).
- `select_dropdown(index, value)` — select an option. **Pass the dropdown's index — do NOT \
click it first.** (The agent handles the open+select internally.) Prefer this over \
`click(index)` on individual option nodes when the element is a dropdown (the agent's \
select logic is more robust than manual click-on-option).

File upload:
- `upload_file(index, path)` — upload a file to a file input element. **Do NOT click the input \
or an upload button first — `upload_file` sets the file directly without opening the OS file \
picker.** This is the ONLY way to handle `<input type=file>` (clicking opens an OS-native \
dialog the agent cannot drive). The `index` may target the hidden input OR its labeled upload \
area / dropzone.

Scroll / wait:
- `scroll(amount, direction)` — scroll the page up or down; `amount` is viewport-heights (1-10). \
Check the 'X pages below' hint on scrollable elements in the DOM to judge how much remains.
- `wait(seconds)` — wait 1-30 seconds (use when an async render must complete before the next step).

Completion:
- `done(text, success)` — signal task complete and stop. Must be the only action in its step.

The agent looks up `index` directly in its current DOM snapshot — there is **NO xpath/attribute \
fuzzy matching at runtime**. So for ambiguous elements, skills must guide attribute-based \
identification (that's what `selectors_md` is for); a stale index makes the agent error and \
re-read the DOM, not auto-resolve.

## What the agent ALREADY handles automatically — DO NOT write these into the skill

Writing these wastes the agent's attention budget and may mislead it. They are silent \
robustness layers in the agent's session code, not things the skill should teach.

- **JS click / occlusion**: if an element is covered by an overlay, the agent auto-falls-back \
to a JS click. Do NOT write "use JS click" / "element is occluded" / "force click".
- **Dropdown handling**: `select_dropdown(index, value)` opens and selects in one action — the \
agent does NOT need a prior `click` to open it. Do NOT write "click the dropdown first, then \
select the option"; one `select_dropdown` (or `dropdown_options` to read choices) suffices. \
**IMPORTANT: `select_dropdown` works on custom `<div>`/`<span>` dropdowns, not just native \
`<select>` — do NOT write "don't use `select_dropdown` because it's a `<div>` not a `<select>`"; \
that is FALSE. Only fall back to `click(index)` on option nodes when the element is genuinely \
not a dropdown (e.g. a modal dialog with radio-like choices, where `click` is the right action).
- **Multi file-input metadata**: the DOM already lists each `<input type=file>` with its \
`class`, `accept`, visible/hidden status in a `[File Inputs]` section. Do NOT copy this \
verbatim — only note it when the disambiguation is NON-obvious (e.g. conditional rendering, \
or `accept` is the only distinguisher among same-name inputs).
- **Runtime fuzzy matching**: there is NONE (see above). Do NOT suggest the agent will "find \
the closest element" if the index is stale.
"""

# ---------------------------------------------------------------------------
# 双 prompt 共用的 spec 块（P4 决策 8：站点级 / 任务级两套模板拼装，不复制两份）
# 注意：这些块里不能出现裸 {}——它们会被拼进模板再 .format，裸括号会被当占位符
# ---------------------------------------------------------------------------

_PAGE_CONTEXT_SECTION = """# Page context (DOM snapshots)
The following are DOM snapshots of the page at different stages (sourced from TreeWalker \\
`get_state().dom_state.element_tree_text`). They are the **PRIMARY source for quirks** — use them \\
to determine what is and isn't a genuine quirk (see the quirks judgment criteria below).

**How to use this section:**
- Compare the SAME element across stages: does it appear/disappear? does its tag/attrs change?
- Look for **genuine quirks**: things the agent CANNOT tell from reading the DOM text alone \\
(hidden dependencies, element identity ambiguities, timing/ordering requirements, SPA stage-transition \\
triggers). See full criteria in the `quirks_md` spec below.
- Do NOT use this section to "rediscover" facts the agent can already see in the DOM \\
(e.g. "立即投稿 is `<span>`" is visible in the DOM text itself — that is NOT a quirk).
- Cite the stage name when referencing a finding (e.g. "in upload-conver stage, ...").

{page_context_block}"""

_SELECTORS_SPEC = """2. `selectors_md` (appendix, LOW volume): An **ELEMENT DESCRIPTION TABLE** — NOT CSS \\
selectors. The consumer is an LLM agent that reads DOM as `[index]<tag attr=val /> text`. \\
**Most elements are already described in-place in `sop_md` — this file only collects the FEW \\
elements that need a "fingerprint" because they have no unique identity** (e.g. multiple \\
`name=buploader` file inputs that must be distinguished by `accept`; an element that only exists \\
in a specific stage). If every element is already unambiguously described in sop_md, output a \\
short note saying so. Do NOT duplicate elements already covered in sop_md.

   Output a 4-column markdown table:
   | 元素用途 (element purpose) | 怎么找到它 (how to find it) | 稳定标识 (stable identity) | 备注 (notes) |

   Column spec:
   - 元素用途: what this element is for (e.g. "投稿入口", "标题输入框").
   - 怎么找到它: natural-language location/context (e.g. "首页右上角导航区", "标题文字下方").
   - 稳定标识: whitelist attributes + visible text ONLY. Allowed attributes: `id`, `name`, `type`, \\
`placeholder`, `aria-label`, `role`, `data-testid`, `data-test`, `data-cy`, `contenteditable`, \\
visible text. Format as `attr=value` pairs separated by commas, e.g. `id=nav_upload_btn, 可见文本"投稿"`.
   - 备注: pitfalls, timing notes (e.g. "仅在 upload-conver 阶段出现", "与字幕 input 同名，靠 accept 区分").

   HARD CONSTRAINTS (must obey):
   - Do NOT produce CSS selectors: no `.class-name`, no `div > span`, no `#id` selector syntax, \\
no `tag.class:nth-of-type`, no XPath.
   - Only use the 11 whitelist attributes above in 稳定标识. Other attributes (class, style, src, \\
href-as-locator) are NOT allowed.
   - If an element has no whitelist attribute, rely on visible text + natural-language location."""

_QUIRKS_SPEC = """3. `quirks_md` (quirks): **Only write quirks the agent CANNOT tell from reading the DOM text alone.** \\
This is the SINGLE MOST IMPORTANT file for agent success, and noise here directly hurts performance \\
(verified by A/B test). Apply this judgment criteria strictly:

   **WRITE these (DOM-invisible, agent would fail or get confused without them):**
   - Hidden dependencies / sequencing triggers (e.g. "封面上传 input only appears after clicking \\
'封面设置' to open the modal — it's not in the DOM during upload stage")
   - Element identity ambiguities (e.g. "page has multiple `name=buploader` file inputs; distinguish \\
by `accept`: `.mp4`=video, `.txt`=subtitle, `.zip`=attachment")
   - Action-method requirements — when the correct ACTION from the Consumer context above matters \\
(e.g. "hidden file input — MUST use `upload_file(index, path)` direct injection, clicking opens \\
an OS dialog the agent can't drive"; "tag input commits with `send_keys('Enter')`, not blur/click"). \\
Reference the exact action name from the vocabulary so the agent recognizes it.
   - SPA stage-transition cues (e.g. "URL stays constant across upload/cover/info stages; detect \\
stage by DOM content, not URL")
   - Non-AJAX vs AJAX submission behavior (e.g. "'立即投稿' triggers a full-page redirect to \\
`/upload/video/success`, not an AJAX toast — wait for navigation, don't immediately re-query")
   - Timing/ordering requirements (e.g. "title input is absent during cover-editing stage; finish \\
cover editing first, wait for info-edit stage")

   **Do NOT WRITE these (writing them wastes the agent's attention):**
   - Element tag/attribute observations (e.g. "立即投稿 is `<span>` not `<button>`" — the DOM shows \\
`[3819]<span />立即投稿`, agent sees this directly)
   - Element-type-vs-expectation mismatches that are visible (e.g. "简介 is `<div contenteditable=true>` \\
not `<textarea>`" — DOM shows `[3788]<div contenteditable=true />`, agent sees it)
   - Field labels, placeholders, maxlength — all visible in DOM text
   - Anything that's just "describing what the DOM shows"
   - **Things the agent's session layer already handles automatically** (see Consumer context \\
"ALREADY handles automatically" list): JS click / occlusion fallback, dropdown click degradation, \\
multi file-input metadata already in the DOM. Writing "use JS click" or "click the dropdown first" \\
is noise — the agent does this silently.

   When in doubt, ask TWO questions:
   1. "If the agent reads the DOM text, can it figure this out itself?" If yes, do NOT write it.
   2. "Does the agent's session layer already handle this automatically?" (JS click / occlusion / \\
dropdown / file-input metadata) If yes, do NOT write it.
   Write it only if the answer to both is NO — i.e. the fact is about sequencing, hidden state, \\
identity ambiguity, or a required action method the agent won't infer. Cite the stage name where relevant."""

_RULES_BLOCK = """# Rules
- **产出语言：中文**（除非站点元素本身是英文，如 placeholder 文本）。所有说明文字、备注、quirks 描述用中文写，保持与站点语言一致。元素用途/可见文本保留站点原文。
- Use OBSERVED element attributes and visible text from the evidence. Quote them verbatim where possible.
- Do NOT invent selectors, attributes, or endpoints you didn't see. If unsure, say so explicitly.
- If the evidence only contains CSS selectors (legacy trace format), extract any whitelist \\
attributes embedded in them (e.g. `input[placeholder='x']` → `placeholder=x`), but PREFER \\
describing what the element IS over reusing the selector syntax. Do NOT echo CSS selectors verbatim.
- Keep markdown clean — these files are read by an LLM agent, not a human browser."""

# 任务级模板的 slug 判定规则（P4 决策 9：同任务重录必须复用 slug → 覆盖旧卡）
_TASK_SLUG_RULES = """\
# Task slug decision (IMPORTANT — a re-recorded task MUST reuse its slug)
- If this task is semantically the SAME as an existing task card listed above (a re-recording
  of the same task at a different time, possibly worded differently), you MUST return that
  card's exact slug — the old card will be overwritten and updated.
- Only return a NEW slug if this is a genuinely new task: english kebab-case, at most 5 words,
  derived from the task description (e.g. `upload-video`).
- `task_keywords`: at most 5 keywords in the site's language (usually Chinese), for future
  semantic retrieval of this task card."""

_DISTILL_PROMPT_TEMPLATE = f"""\
Distill the following browser interaction evidence into a **site-specific knowledge card** for `{{domain}}`.

# CRITICAL: This is the OPPOSITE of generic skill authoring
Do NOT abstract away site-specific selectors, IDs, or URLs. Capture them — this knowledge will be \
file-injected into a browser agent when it navigates to `{{domain}}`, and it must be actionable as-is. \
"Record the map, not the diary."

{{consumer_context}}

# Domain
`{{domain}}`

# Identified sub-capacities (task types observed on this site — informational)
{{capacities_line}}

The sub-capacities above were identified by a separate CLASSIFY stage. Treat them as the list of \
task types seen on this site (informational — helps you recognize what kinds of tasks exist). \
Do NOT write per-task step-by-step sequences in this card: each task's detailed flow lives in its \
own task-level card (`tasks/<slug>/`). This card is the **site-level digest**.

# Evidence segments ({{n_segments}} total, on {{domain}})
{{evidence_blocks}}

{_PAGE_CONTEXT_SECTION}

# Output spec — produce THREE markdown sections

1. `sop_md` (skeleton): a **BOUNDED site-level digest** — MUST start with the site function map, \
followed by site-common knowledge. Keep the whole `sop_md` concise (≤ ~6000 chars; absolute cap \
8000): when it would grow beyond that, **compress wording — never drop topics wholesale**.

   **Required opening section `## 站点功能地图（Site Function Map）`**: entry URL patterns \
(including SPA routes), and the site's major menus / function areas — one line each (where it \
is, what task it leads to). This section accumulates across tasks as more areas are discovered.

   **Then `## 站点通用操作知识`**: cross-task COMMON knowledge only — shared navigation paths \
(e.g. "order tasks start at Sales > Orders"), common control behaviors (filters / grids / date \
pickers on this site), and recurring operation patterns that apply to MULTIPLE task types. \
Write concise bullet-point guidance — NOT step-by-step task playbooks (those live in the \
task-level cards). With single-task evidence this section is thin; that's fine and expected — \
it grows as more tasks are distilled on this site.

{_SELECTORS_SPEC}

{_QUIRKS_SPEC}

{_RULES_BLOCK}
- `skill_name`: human-readable name (Title Case). `scope`: one-sentence use case.

Return ONLY JSON in this exact shape:
{{{{
  "skill_name": "...",
  "scope": "...",
  "sop_md": "# ...\\n\\n...",
  "selectors_md": "# Selectors — {{domain}}\\n\\n...",
  "quirks_md": "# Quirks — {{domain}}\\n\\n..."
}}}}
"""

# ---------------------------------------------------------------------------
# 任务级 prompt（P4 跳 B）：单任务叙事主干 + 任务描述 + 现有卡清单；共用块拼装
# ---------------------------------------------------------------------------
_TASK_PROMPT_TEMPLATE = f"""\
Distill the following browser interaction evidence into a **TASK-LEVEL skill card** for `{{domain}}`.
This card documents ONE task — the flow recorded below — and will later be retrieved by its task
description, so the slug and keywords must faithfully identify the task.

{{consumer_context}}

# Task
One task on `{{domain}}` (task-level card — the site-level function map lives in a separate host card).

# Task description (user intent — use it to guide step selection and wording)
{{task_description}}

# Existing task cards for this host
{{existing_tasks_block}}

{_TASK_SLUG_RULES}

# Evidence segments ({{n_segments}} total, on {{domain}})
{{evidence_blocks}}

{_PAGE_CONTEXT_SECTION}

# Output spec — produce THREE markdown sections

1. `sop_md`: A **COHERENT PROCEDURAL NARRATIVE** for THIS task's flow — a step-by-step playbook an \
agent can execute directly: "step 1: locate element X (how to identify it), action Y; step 2: ...". \
Bind to this site (real URLs, real button labels, real placeholder text). Do NOT write a site \
function map — that belongs to the host-level card. Describe each element **in place** where the \
step uses it (tag + whitelist attrs + visible text + what to do), so agents don't need to \
cross-reference selectors.md for routine elements.

{_SELECTORS_SPEC}

{_QUIRKS_SPEC}

{_RULES_BLOCK}
- This card documents ONE task; `task_slug` / `task_keywords` identify it for future retrieval.

Return ONLY JSON in this exact shape:
{{{{
  "task_slug": "...",
  "task_keywords": ["...", "..."],
  "sop_md": "# ...\\n\\n...",
  "selectors_md": "# Selectors — {{domain}}\\n\\n...",
  "quirks_md": "# Quirks — {{domain}}\\n\\n..."
}}}}
"""

# 增量蒸馏 addendum（host 已有 skill 时附加）。
# 【形态修订 2026-08-30】对齐 Browser-BC：以旧卡为基线更新（压缩措辞不丢主题），
# 站点级是有界摘要而非逐任务序列堆积（逐任务流程在 tasks/<slug>/ 任务卡里）。
_INCREMENTAL_ADDENDUM = """\

# EXISTING KNOWLEDGE (distill_version {prev_version})
This host has been distilled before. Produce an **UPDATED VERSION** of the existing knowledge
below, incorporating the NEW evidence (incremental update, Browser-BC style):
- The previous card below is your **BASE**: carry forward every still-valid entry/topic —
  compress the wording where needed, but do NOT silently drop topics or sections.
- Remove only what the new evidence contradicts — **new evidence wins**.
- Add newly discovered entries (site-map areas, common patterns, selectors, quirks);
  do NOT duplicate what's already there.
- Stay within the `sop_md` size bound: when it would overflow, merge similar entries into
  more general guidance — **never** fix the size by discarding topics wholesale.

Previous `sop_md` (site function map + site-common knowledge; truncated):
```
{prev_sop}
```

Previous `selectors_md` (truncated):
```
{prev_selectors}
```

Previous `quirks_md` (truncated):
```
{prev_quirks}
```
"""

# 增量 prompt 各文件的字符预算（sop 主预算沿 P3 延后项口径 8000）
_PREV_SOP_BUDGET = 8000
_PREV_SELECTORS_BUDGET = 3000
_PREV_QUIRKS_BUDGET = 4000

# LLM 调用+解析的重试次数（首次 + 1 重试）。
# 【为什么在 distiller 层重试】llm.py 只重试 HTTP 层失败；JSON 畸形（长中文 markdown
# 塞 JSON 字符串时偶发未转义引号等）发生在解析层——HTTP 是成功的，llm.py 不会重取。
# 间歇性问题，重取一次新样本大概率过（localhost 实测：同 prompt 一次失败一次成功）。
_DISTILL_ATTEMPTS = 2


def _card_from_prev(host: str, merged: Bucket, prev: dict[str, Any]) -> SkillCard:
    """LLM 全失败但有 registry 旧卡：用旧卡内容兜底（保住累积，版本不动）。

    比模板兜底好——旧卡是上次真蒸馏的知识；这次失败只是「没更新」，不应倒退成模板
    垃圾（localhost 事故：v1 真卡被模板覆盖、版本倒退）。meta 记
    ``kept_after_llm_failure=True`` 便于排查。
    """
    meta = dict(prev.get("meta") or {})
    meta.setdefault("distill_version", 1)
    meta.setdefault("model", "kept-prev")
    meta["usage"] = meta.get("usage") or {}
    meta["kept_after_llm_failure"] = True
    return SkillCard(
        bucket_id=merged.bucket_id,
        domain=host,
        capacity=merged.canonical_capacity,
        skill_name=str(prev.get("skill_name") or host),
        scope=str(prev.get("scope") or ""),
        sop_md=str(prev.get("sop_md") or ""),
        selectors_md=str(prev.get("selectors_md") or ""),
        quirks_md=str(prev.get("quirks_md") or ""),
        meta=meta,
    )


def _clip_prev(text: object, budget: int) -> str:
    """截断旧卡片段塞进 addendum；空返 "(empty)"。"""
    s = str(text or "").strip()
    if not s:
        return "(empty)"
    return s if len(s) <= budget else s[:budget] + "\n…(truncated)"


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
    prev_card: dict[str, Any] | None = None,
) -> SkillCard:
    """蒸馏一个 host 的所有 capacity bucket → 一份 host 级 SkillCard（站点级累积卡）。

    host 级蒸馏（docs/skill-simplification-plan.md 决策 1）：
    - 把同 host 所有 bucket 的 segments 合并，一次 LLM 调用看整条流程
    - capacity 标签降级为 prompt 的子能力分组提示，不作为产物维度
    - 产出「站点功能地图 + 按 capacity 分组的典型操作序列」（P4 S3）

    use_llm=None 时：有 LLM_KEY 就用 LLM，否则用模板 fallback。
    page_context: trace 级 DOM 快照（阶段名→DOM 文本）。LLM 路径注入 prompt 让它推 quirks。
    prev_card: registry 旧卡（``registry.load_card`` 产出）。有值且走 LLM 时注入
      `_INCREMENTAL_ADDENDUM`（三文件块 + 地图合并指令），版本取 ``prev + 1``
      （版本真源在 registry 卡片，bucket 内的 distill_version 恒 0 不可靠）。
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

    # 版本真源：有旧卡取 prev+1（registry），否则 bucket 计数 +1（首次）
    prev_version = int((prev_card or {}).get("meta", {}).get("distill_version") or 0)
    next_version = (prev_version + 1) if prev_card else merged.distill_version + 1

    # capacity 列表渲染为「本站点已见任务类型」信息清单（2026-08-30 修订：
    # 不再作分组结构——站点级是有界摘要；与旧卡 capacities 并集，清单随累积只增不减）
    capacity_names = [b.canonical_capacity for b in buckets if b.canonical_capacity]
    prev_caps = [str(c) for c in ((prev_card or {}).get("meta") or {}).get("capacities") or [] if c]
    # 去重保序（旧卡在前：先见的任务类型排前面）
    seen: set[str] = set()
    unique_caps: list[str] = []
    for c in [*prev_caps, *capacity_names]:
        if c not in seen:
            seen.add(c)
            unique_caps.append(c)
    capacities_line = (
        ", ".join(f"`{c}`" for c in unique_caps) if unique_caps else "(none — single flow)"
    )

    prompt = _DISTILL_PROMPT_TEMPLATE.format(
        domain=host,
        consumer_context=_CONSUMER_CONTEXT,
        capacities_line=capacities_line,
        n_segments=len(merged.segments),
        evidence_blocks=_evidence_block(merged),
        page_context_block=_render_page_context(page_context or {}),
    )

    # 增量（P4 真接通）：registry 旧卡三件套塞进 prompt，LLM 合并更新
    if prev_card:
        prompt += _INCREMENTAL_ADDENDUM.format(
            prev_version=prev_version,
            prev_sop=_clip_prev(prev_card.get("sop_md"), _PREV_SOP_BUDGET),
            prev_selectors=_clip_prev(prev_card.get("selectors_md"), _PREV_SELECTORS_BUDGET),
            prev_quirks=_clip_prev(prev_card.get("quirks_md"), _PREV_QUIRKS_BUDGET),
        )

    # LLM 调用 + 解析带重试：JSON 畸形发生在解析层（llm.py 只重试 HTTP 层），重取新样本
    card: SkillCard | None = None
    for attempt in range(1, _DISTILL_ATTEMPTS + 1):
        try:
            text, usage = call_llm(prompt, system=_DISTILL_SYSTEM)
            data = parse_json_from_model(text)
            sop = str(data.get("sop_md") or "").strip()
            sels = str(data.get("selectors_md") or "").strip()
            quirks = str(data.get("quirks_md") or "").strip()
            if not any([sop, sels, quirks]):
                raise ValueError("LLM returned all-empty fields")
            card = SkillCard(
                bucket_id=merged.bucket_id,
                domain=host,
                capacity=merged.canonical_capacity,  # capacity 列表，作为 meta 索引
                skill_name=str(data.get("skill_name") or host).strip(),
                scope=str(data.get("scope") or "").strip(),
                sop_md=sop,
                selectors_md=sels,
                quirks_md=quirks,
                meta={
                    "model": config.DISTILL_MODEL,
                    "usage": usage,
                    "segment_count": len(merged.segments),
                    "domains": [host],
                    "capacities": unique_caps,
                    "distill_version": next_version,
                    "distilled_at": _now_iso(),
                },
            )
            break
        except Exception as e:  # noqa: BLE001 - 记日志；重试或兜底（不阻断管线）
            logger.warning(
                "distill_host(%s) LLM attempt %d/%d failed: %r",
                host,
                attempt,
                _DISTILL_ATTEMPTS,
                e,
            )
            if attempt < _DISTILL_ATTEMPTS:
                progress.report("DISTILL", detail=f"LLM attempt {attempt} failed, retrying")

    if card is not None:
        progress.report("DISTILL", detail=f"→ card for host={host}")
        return card

    # 兜底优先级（P4 修复）：有旧卡保旧卡（累积不倒退），无旧卡才退模板
    if prev_card:
        logger.warning(
            "distill_host(%s) all %d attempts failed; keeping previous card (v%s)",
            host,
            _DISTILL_ATTEMPTS,
            prev_version,
        )
        progress.report(
            "DISTILL",
            detail=f"LLM failed ×{_DISTILL_ATTEMPTS}; keep previous card v{prev_version}",
        )
        return _card_from_prev(host, merged, prev_card)
    logger.warning(
        "distill_host(%s) all %d attempts failed; falling back to template",
        host,
        _DISTILL_ATTEMPTS,
    )
    progress.report("DISTILL", detail=f"LLM failed ×{_DISTILL_ATTEMPTS}, falling back to template")
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
    prev_cards: dict[str, dict[str, Any]] | None = None,
) -> list[SkillCard]:
    """蒸馏多个 bucket → host 级 SkillCard 列表（按 host 合并）。

    host 级蒸馏：按 bucket.domain 分组，每个 host 产一份 SkillCard。
    page_context 从 trace 透传给每个 host 的 distill_host。
    prev_cards: host → registry 旧卡映射（增量蒸馏用，管线在 BUCKET 后按 host 懒加载）。
    """
    # 按 host 分组
    by_host: dict[str, list[Bucket]] = {}
    for b in buckets:
        by_host.setdefault(b.domain, []).append(b)

    hosts = list(by_host.keys())
    prev_cards = prev_cards or {}
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
        card = distill_host(
            host,
            host_buckets,
            use_llm=use_llm,
            page_context=page_context,
            prev_card=prev_cards.get(host),
        )
        out.append(card)
        progress.report("DISTILL", current=i + 1, total=len(hosts), detail=f"host={host}")
    return out


# ---------------------------------------------------------------------------
# 任务级蒸馏（P4 跳 B）：单任务卡，meta 携带 task_slug / task_keywords / task_description
# ---------------------------------------------------------------------------


def _sanitize_slug(raw: str, *, fallback: str = "") -> str:
    """清洗 LLM 返回的 slug：lowercase + kebab-case，非法字符转 -，空回退 fallback。"""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > 60:
        s = s[:60].rstrip("-")
    return s or fallback


def _sanitize_keywords(raw: object, limit: int = 5) -> list[str]:
    """清洗 task_keywords：非 list 返空，去空串，截 limit。"""
    if not isinstance(raw, list):
        return []
    return [str(k).strip() for k in raw if str(k).strip()][:limit]


def distill_task(
    host: str,
    buckets: list[Bucket],
    *,
    task_description: str = "",
    existing_tasks: list[dict[str, Any]] | None = None,
    use_llm: bool | None = None,
    page_context: dict[str, str] | None = None,
    fallback_slug: str = "",
) -> SkillCard:
    """蒸馏单任务卡（P4 双产物跳 B）。

    与 ``distill_host``（站点级累积卡）共用 evidence/page_context 渲染与 spec 块，
    但 prompt 是任务级的：单任务叙事（无站点地图段）+ 任务描述注入 + 现有任务卡清单
    （slug 稳定化——同任务重录复用旧 slug 覆盖，确为新任务才新建，P4 决策 9）。

    返回的 SkillCard 在 ``meta`` 携带任务字段：
      - ``task_slug``：LLM 复用/新生成（sanitize 后；空回退 fallback_slug）
      - ``task_keywords``：≤5 个检索关键词
      - ``task_description``：本次任务描述（原样）
    模板模式（--no-llm）：同退模板卡，slug 用 fallback，keywords 空。
    """
    if use_llm is None:
        use_llm = bool(config.LLM_KEY)

    merged = _merge_buckets_for_host(host, buckets)
    fallback_slug = fallback_slug or "task"

    progress.report(
        "DISTILL",
        detail=f"task host={host} segments={len(merged.segments)} use_llm={use_llm}",
    )

    if not use_llm:
        card = _template_skill_card(merged)
        card.meta.update(
            {
                "task_slug": fallback_slug,
                "task_keywords": [],
                "task_description": task_description,
                "distilled_at": _now_iso(),
            }
        )
        progress.report("DISTILL", detail=f"→ template task card slug={fallback_slug}")
        return card

    # 现有任务卡清单（slug 稳定化参照）
    lines = [
        f"- `{t.get('slug')}` — {t.get('task_description') or '(no description)'}"
        for t in (existing_tasks or [])
        if t.get("slug")
    ]
    existing_block = "\n".join(lines) if lines else "(none — first task card for this host)"

    prompt = _TASK_PROMPT_TEMPLATE.format(
        domain=host,
        consumer_context=_CONSUMER_CONTEXT,
        task_description=task_description.strip() or "(not provided)",
        existing_tasks_block=existing_block,
        n_segments=len(merged.segments),
        evidence_blocks=_evidence_block(merged),
        page_context_block=_render_page_context(page_context or {}),
    )

    card: SkillCard | None = None
    for attempt in range(1, _DISTILL_ATTEMPTS + 1):
        try:
            text, usage = call_llm(prompt, system=_DISTILL_SYSTEM)
            data = parse_json_from_model(text)
            sop = str(data.get("sop_md") or "").strip()
            sels = str(data.get("selectors_md") or "").strip()
            quirks = str(data.get("quirks_md") or "").strip()
            if not any([sop, sels, quirks]):
                raise ValueError("LLM returned all-empty task fields")
            slug = _sanitize_slug(str(data.get("task_slug") or ""), fallback=fallback_slug)
            card = SkillCard(
                bucket_id=f"{merged.bucket_id}::task",
                domain=host,
                capacity=merged.canonical_capacity,
                skill_name=str(data.get("skill_name") or host).strip(),
                scope=str(data.get("scope") or task_description or "").strip(),
                sop_md=sop,
                selectors_md=sels,
                quirks_md=quirks,
                meta={
                    "model": config.DISTILL_MODEL,
                    "usage": usage,
                    "segment_count": len(merged.segments),
                    "domains": [host],
                    "task_slug": slug,
                    "task_keywords": _sanitize_keywords(data.get("task_keywords")),
                    "task_description": task_description,
                    "distilled_at": _now_iso(),
                },
            )
            break
        except Exception as e:  # noqa: BLE001 - 记日志；重试或退模板
            logger.warning(
                "distill_task(%s) LLM attempt %d/%d failed: %r",
                host,
                attempt,
                _DISTILL_ATTEMPTS,
                e,
            )
            if attempt < _DISTILL_ATTEMPTS:
                progress.report("DISTILL", detail=f"task LLM attempt {attempt} failed, retrying")

    if card is not None:
        progress.report("DISTILL", detail=f"→ task card slug={card.meta.get('task_slug')}")
        return card

    logger.warning(
        "distill_task(%s) all %d attempts failed; falling back to template",
        host,
        _DISTILL_ATTEMPTS,
    )
    progress.report(
        "DISTILL", detail=f"task LLM failed ×{_DISTILL_ATTEMPTS}, falling back to template"
    )
    card = _template_skill_card(merged)
    card.meta.update(
        {
            "task_slug": fallback_slug,
            "task_keywords": [],
            "task_description": task_description,
            "distilled_at": _now_iso(),
        }
    )
    return card
