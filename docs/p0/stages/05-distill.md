# Stage ⑤ DISTILL：Bucket → SkillCard ★核心分叉点

> 代码：`harness/distiller.py`
> 输入：`Bucket[]`
> 输出：`SkillCard[]`（每个桶 → 一张知识卡，含 4 字段）

> ⚠️ **这是 TreeForge 立项的根本理由。** 如果只读一篇文档，读这篇。

## 这个阶段干什么

DISTILL 是管线的最后一步，也是**最关键的一步**。它把桶里的 segment 证据喂给 LLM，
让它提炼成结构化的「站点特定知识卡」。

**为什么这是核心？** 因为 TreeForge 与参照对象 Browser-BC 的**主要分歧就在这里**。
两者都用五阶段管线，前四阶段几乎一致，唯独 DISTILL **反着来**。

## 核心分叉：TreeForge vs Browser-BC

| | Browser-BC | TreeForge |
|---|---|---|
| **DISTILL 指令** | "produce a single SKILL.md that any browser agent can follow on ANY website" | "produce a **site-specific** knowledge card for `{domain}`" |
| **关键约束** | "**Abstract away** site-specific selectors and IDs" | "Do **NOT** abstract away. **Capture** them" |
| **产物形态** | 1 个通用 SKILL.md（去站点化） | 4 个站点特定 markdown |
| **适用消费** | MCP 检索：agent 拿抽象能力卡去任意站点 | 文件注入：agent 导航到该站点时直接读 |

**为什么 TreeForge 反着来？** 因为消费方式不同：

TreeForge 的消费方是 TreeWalker，用**文件注入**——agent 导航到 `bilibili.com` 时，
browser-harness 直接把 `domain-skills/bilibili.com/*.md` 读进上下文。这种消费期望：

> 「站点特定、**拿到就能用**」的知识——真实 selector、真实 URL，agent 不用再去找。

而通用 SOP（Browser-BC 风格）对文件注入**没有直接可操作性**——agent 还得自己根据抽象步骤
去定位元素，失去了「先验知识」的价值。

init-plan 用一句话概括这个分叉：

> **"Record the map, not the diary."** 记地图不记流水账。

## 4 字段输出 spec

DISTILL 产出的 `SkillCard` 有 4 个 markdown 字段，对应 4 个文件：

| 字段 | 文件 | 比喻 | 内容 |
|---|---|---|---|
| `sop_md` | `_sop.md` | 骨架 | 这个站点这个能力的常见任务流程（量少，Browser-BC 风格但绑定本站） |
| `selectors_md` | `selectors.md` | 血肉 | 所有稳定 selector、AX name、元素定位（量大、最重要） |
| `quirks_md` | `quirks.md` | 怪癖 | 隐藏等待、SPA 导航、框架行为、反爬检测 |
| `api_md` | `api.md` | 暗门 | 私有 API、URL 模式、隐藏端点 |

**为什么 4 个文件，不是 1 个？** 因为 TreeWalker 消费侧的硬约束：

```python
# browser-harness: goto_url 时
hostname = urlparse(url).hostname
domain_dir = AGENT_WORKSPACE / "domain-skills" / hostname
if domain_dir.is_dir():
    return {**result, "domain_skills": [sorted .md files][:10]}
```

四条约束：
1. **按 hostname 索引**：`domain-skills/<hostname>/`
2. **只读 .md**，按字母序排序
3. **硬上限 10 个**
4. **`_sop.md` 下划线前缀** → 字母序排第一，作为入口索引

4 文件远低于 10 上限，命名固定。`_sop.md` 排第一是因为它是骨架，agent 先读它建立任务全局观。

## LLM Prompt 长什么样

完整 prompt 在 `harness/distiller.py:_DISTILL_PROMPT_TEMPLATE`。关键部分：

```
Distill the following browser interaction evidence into a
**site-specific knowledge card** for `{domain}`.

# CRITICAL: This is the OPPOSITE of generic skill authoring
Do NOT abstract away site-specific selectors, IDs, or URLs. Capture them —
this knowledge will be file-injected into a browser agent when it navigates
to `{domain}`, and it must be actionable as-is.
"Record the map, not the diary."

# Domain
`{domain}`

# Capacity
`{capacity}` — {capacity_desc}

# Evidence segments ({n_segments} total, on {domain})
{evidence_blocks}                        ← segment 证据块

# Output spec — produce FOUR markdown sections
1. `sop_md` (skeleton, LOW volume): ...
2. `selectors_md` (flesh, HIGH volume, actionable): ...
   PREFER in this order: data-testid / data-cy / aria-label / name /
   [role] / #id / tag.class:nth-of-type / XPath
3. `quirks_md` (quirks): ...
4. `api_md` (private API): ...

# Rules
- Use OBSERVED selectors and URLs from the evidence. Quote them verbatim.
- Do NOT invent selectors or endpoints you didn't see.
```

**几个关键设计：**

1. **`# CRITICAL` 段直接点明「与通用写作相反」**——防止 LLM 默认走通用 SOP 模式
2. **`# Rules` 强调「use OBSERVED, do NOT invent」**——避免 LLM 编造 selector
3. **`selectors_md` 给出 selector 优先级**：`data-testid > aria-label > name > [role] > #id > xpath`
   —— 对齐 Browser-BC 扩展层的 selector fallback 顺序

## 两条路径

```python
def distill_bucket(bucket, *, use_llm=None) -> SkillCard:
    if use_llm is None:
        use_llm = bool(config.LLM_KEY)

    if not use_llm:
        return _template_skill_card(bucket)       # ① 模板路径

    try:
        text = call_llm(prompt, system=...)        # ② LLM 路径
        data = parse_json_from_model(text)
        return SkillCard(**data, meta=...)
    except Exception:
        return _template_skill_card(bucket)        # ② LLM 失败也退模板
```

| 路径 | 触发 | 产物质量 |
|---|---|---|
| **模板** | 无 LLM_KEY 或 `--no-llm` 或 LLM 失败 | 低，但结构完整（验证链路用） |
| **LLM** | 配了 LLM_KEY | 高，4 字段都有语义提炼 |

## 模板 fallback `_template_skill_card()`

无 LLM 时从 segment 机械提炼：

```python
def _template_skill_card(bucket):
    selectors = [ev.selector for seg in bucket.segments for ev in seg.events if ev.selector]
    urls = [ev.url for seg in bucket.segments for ev in seg.events if ev.url]

    selectors_md = "# Selectors — {domain}\n\n" + "\n".join(f"- `{s}` — {note}" for s in selectors)
    sop_md = "# SOP — ...\n\n```\n{event_summary}\n```"  # 直接抄事件流水账
    quirks_md = "# Quirks — ...\n\n_No quirks extracted (template mode)._"  # 占位符
    api_md = "# API — ...\n\n" + "\n".join(f"- `{u}`" for u in urls)
```

**模板产物的局限：**
- `selectors_md` 只是机械列举，没有 LLM 的「归类 + 注释」
- `sop_md` 是事件流水账，不是语义化步骤
- `quirks_md` 直接是占位符（模板模式无法推断怪癖）
- `api_md` 只是 URL 列表，没有真正的 API 提炼

**为什么留模板路径？** P0 的目标是「验证链路能跑通」。没配 LLM_KEY 或 LLM 调用失败时，
如果报错退出，你连 adapter/install 是否正确都无法验证。退模板后**结构完整**，让你能跑通
`trace → 4 文件`的完整链路。真实使用该配 LLM。

## LLM 路径的容错

```python
try:
    text, usage = call_llm(prompt, system=_DISTILL_SYSTEM)
    data = parse_json_from_model(text)
    card = SkillCard(...)
    # 兜底：LLM 返回了空字段
    if not any([card.sop_md, card.selectors_md, card.quirks_md, card.api_md]):
        return _template_skill_card(bucket)
    return card
except Exception as e:
    progress.report("DISTILL", detail=f"LLM failed ({e!r}), falling back to template")
    return _template_skill_card(bucket)
```

**三层容错：**
1. LLM 调用失败（网络/超时/HTTP 错）→ 退模板
2. JSON 解析失败 → 退模板
3. 解析成功但 4 字段全空 → 退模板

**为什么这么保守？** P0 的铁律是「链路永远跑通」。蒸馏是最后一步，挂了就什么都没有。
宁可质量低也不能完全失败。

## 增量蒸馏（P0 简化版）

如果桶已经蒸馏过（`distill_version > 0`），把旧 sop 塞进 prompt 让 LLM 更新：

```python
if bucket.distill_version > 0 and bucket.last_distilled_at:
    prompt += _INCREMENTAL_ADDENDUM.format(
        prev_version=bucket.distill_version,
        prev_sop="(previous skill not available in P0)"[:8000],
    )
```

增量 addendum 指令：

> Update the EXISTING knowledge based on NEW segment evidence:
> - Keep rules/selectors still correct
> - Remove rules contradicted by new evidence
> - Add newly discovered selectors/quirks/endpoints

**P0 的局限：** 框架在，但「旧 sop」实际是占位符——P0 没持久化 SkillCard，每次跑都是首次蒸馏。
P1+ 接 registry 后，旧 skill 会从磁盘加载真正塞进去。**这是 P1 的关键工作项。**

## distill_version 的意义

```python
meta = {
    "distill_version": bucket.distill_version + 1,   # 每次蒸馏 +1
    "distilled_at": now_iso(),
    "model": config.DISTILL_MODEL,
    "usage": usage,
    "segment_count": len(bucket.segments),
}
```

`distill_version` 是增量蒸馏的关键——它告诉系统「这个桶蒸过几次了」。
首次 = 1，每次更新 +1。P1+ 用它判断「要不要把旧内容塞进 prompt」。

## 多桶蒸馏 `distill_buckets()`

```python
def distill_buckets(buckets, *, use_llm=None):
    out = []
    for b in buckets:
        if len(b.segment_ids) < config.MIN_BUCKET_SIZE:   # 太小的桶跳过
            continue
        card = distill_bucket(b, use_llm=use_llm)
        out.append(card)
    return out
```

`MIN_BUCKET_SIZE=1`（P0 默认）—— 单条录制就能蒸馏。Browser-BC 默认更大（要求多次示教合并），
P0 调到 1 是为了让单 trace 也能产出 skill。

## 实测：bilibili（模板模式）

```
[DISTILL] bucket=bilibili.com::upload-content segments=1 use_llm=False
[DISTILL] → template card for bilibili.com::upload-content
```

产物 `selectors.md`（模板模式）：

```markdown
# Selectors — bilibili.com

Observed for `upload-content`:

- `.header-entry .upload-btn, [aria-label='投稿']` — 投稿按钮
- `input[type='file'][accept='video/*']` — 视频文件选择
- `input[placeholder='请输入标题']` — 标题输入框
...
```

LLM 模式下，这里会变成有分类、有稳定性评估、有 AX name 的结构化表格。**质量差距是模板模式 vs LLM 模式的主要差异。**

## P0 vs Browser-BC 的 DISTILL 差异总览

| | Browser-BC | TreeForge P0 |
|---|---|---|
| 产物字段 | `skill_md` + `trace_guide_md`（2 字段） | `sop_md` + `selectors_md` + `quirks_md` + `api_md`（4 字段） |
| Prompt 取向 | 抽象化（去站点） | **具体化**（站点特定） |
| 容错 | 抛错 | **退模板**（保证链路） |
| 增量 | 完整实现（旧 skill 加载） | 框架在，但旧 skill 没持久化 |
| 模型 | Opus | **同** |

## 这就是 TreeForge 存在的意义

如果 DISTILL 和 Browser-BC 一样产通用 SOP，那 TreeForge 就没必要存在——直接用 Browser-BC 就行。

TreeForge 立项的根本理由就是：**为文件注入消费场景，专门产站点特定知识**。
这个分叉完全体现在 `distiller.py` 的 prompt 设计上——前 4 阶段的代码可以基本照搬 Browser-BC，
唯独这一段必须重写。

理解了这一点，就理解了 TreeForge 整个项目。

## 相关测试

- `tests/test_distiller.py::test_distill_bucket_with_mocked_llm_returns_four_fields`（核心：mock LLM，断言 4 字段非空）
- `tests/test_distiller.py::test_distill_bucket_template_fallback_without_llm`（模板路径）
- `tests/test_distiller.py::test_distill_bucket_falls_back_on_llm_exception`（LLM 失败容错）
- `tests/test_distiller.py::test_distill_bucket_falls_back_on_unparseable_json`（JSON 解析失败容错）

> 注意：所有测试都 **mock LLM**，不真调——`patch("harness.distiller.call_llm")`。

## 下一步

DISTILL 产出 SkillCard 后，最后一步是落盘：
- 想看落盘怎么实现 → [../concepts/03-adapter-design.md](../concepts/03-adapter-design.md)
- 想看数据模型全貌 → [../concepts/01-data-models.md](../concepts/01-data-models.md)
