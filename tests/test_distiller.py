"""distiller 测试。

验收（init-plan §7.8 + docs/skill-simplification-plan.md）：mock LLM 返回，
断言产出的 SkillCard 三个字段（sop_md / selectors_md / quirks_md）都有内容。
**不要真调 LLM**。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from harness import adapter, atomizer, bucketer, classifier, distiller
from harness.models import SkillCard, TraceEvent

_FAKE_LLM_RESPONSE = {
    "skill_name": "Upload Video",
    "scope": "Publish a video to bilibili.com member platform.",
    "sop_md": "# SOP — bilibili.com\n\n1. Navigate to https://member.bilibili.com/platform/upload/video/frame\n2. Select video file\n3. Fill title/category/tags\n4. Click publish",
    "selectors_md": (
        "# Selectors — bilibili.com\n\n"
        "| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |\n"
        "|---|---|---|---|\n"
        "| 视频文件上传 | 投稿页拖拽区 | type=file, accept 含 .mp4 | 隐藏 input |\n"
        "| 标题输入框 | 标题文字下方 | placeholder=请输入稿件标题 | 输入框 |\n"
    ),
    "quirks_md": "# Quirks — bilibili.com\n\n- Wait for upload to finish before publish button enables (async).\n- SPA navigation: history.pushState between steps.",
}


def _make_bucket(bilibili_trace_payload):
    trace = adapter.adapt(bilibili_trace_payload, source="test")
    segments = atomizer.atomize(trace)
    classified = classifier.classify(segments, use_llm=False)
    buckets = bucketer.bucket(classified)
    assert buckets, "fixture 应该产生至少一个 bucket"
    return buckets[0]


def test_distill_bucket_with_mocked_llm_returns_three_fields(bilibili_trace_payload):
    """验收点：mock LLM，断言 SkillCard 三字段（sop/selectors/quirks）都有内容。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with (
        patch("harness.distiller.call_llm") as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        mock_call.return_value = (
            json.dumps(_FAKE_LLM_RESPONSE),
            {"input_tokens": 10, "output_tokens": 20},
        )
        card = distiller.distill_bucket(bucket, use_llm=True)

    assert isinstance(card, SkillCard)
    assert card.sop_md, "sop_md 为空"
    assert card.selectors_md, "selectors_md 为空"
    assert card.quirks_md, "quirks_md 为空"
    # api_md 字段已删除（docs/skill-simplification-plan.md 决策 3）
    assert not hasattr(card, "api_md"), "api_md 字段应已删除"
    assert card.skill_name == "Upload Video"
    assert card.domain == bucket.domain
    assert card.meta.get("model")  # 记了模型名
    # 阶段 1：selectors_md 应是元素描述表（四列表头），不含 CSS selector 模式
    assert "元素用途" in card.selectors_md, "selectors_md 应含新格式表头「元素用途」"
    assert "怎么找到它" in card.selectors_md
    assert "稳定标识" in card.selectors_md
    # mock 遵守新 prompt：不应出现 CSS selector 模式（`input[` 或 `.class`）
    assert "`input[" not in card.selectors_md, "selectors_md 不应含 CSS selector 模式"
    assert "| `.header" not in card.selectors_md


def test_distill_bucket_template_fallback_without_llm(bilibili_trace_payload):
    """无 LLM 时退模板，也要产出三字段非空。"""
    bucket = _make_bucket(bilibili_trace_payload)
    card = distiller.distill_bucket(bucket, use_llm=False)

    assert card.sop_md
    assert card.selectors_md
    # quirks_md 在模板模式下是「未提取」占位，也非空
    assert card.quirks_md
    assert not hasattr(card, "api_md"), "api_md 字段应已删除"
    assert card.meta.get("model") == "(template)"
    # 阶段 1：模板模式 selectors_md 应含警告头注（说明是低质量机械产出）
    assert "模板模式产出" in card.selectors_md, "模板 selectors_md 应含质量警告头注"
    assert "--no-llm" in card.selectors_md


def test_distill_bucket_falls_back_on_llm_exception(bilibili_trace_payload):
    """LLM 抛错时退模板，不阻断管线。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with (
        patch("harness.distiller.call_llm") as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        mock_call.side_effect = RuntimeError("HTTP 500: simulated gateway error")
        card = distiller.distill_bucket(bucket, use_llm=True)

    # 退模板了，仍要有三字段
    assert card.sop_md
    assert card.selectors_md
    assert card.meta.get("model") == "(template)"


def test_distill_bucket_falls_back_on_unparseable_json(bilibili_trace_payload):
    """LLM 返回无法解析的 JSON 时退模板。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with (
        patch("harness.distiller.call_llm") as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        mock_call.return_value = ("this is not json at all {{{", {})
        card = distiller.distill_bucket(bucket, use_llm=True)

    assert card.sop_md  # 退模板


# ---------------------------------------------------------------------------
# 阶段 1：prompt 契约测试——防止后续误改 prompt 丢掉硬约束
# ---------------------------------------------------------------------------


def test_distill_prompt_requires_element_description_format():
    """验收点：prompt 文本必须含元素描述表的硬约束 + 11 个白名单属性。

    这是个「prompt 契约测试」——distiller 的产出格式完全由 prompt 决定，
    后续误改 prompt（比如删掉「Do NOT produce CSS selectors」）会让格式退化，
    此测试守住阶段 1 的核心约束。
    """
    prompt = distiller._DISTILL_PROMPT_TEMPLATE

    # 硬约束措辞
    assert "ELEMENT DESCRIPTION TABLE" in prompt, "prompt 应明确要求产元素描述表"
    assert "Do NOT produce CSS selectors" in prompt, "prompt 应明确禁止 CSS selector"
    assert "HARD CONSTRAINTS" in prompt

    # 4 列表头（中英对照）
    for col in ("元素用途", "怎么找到它", "稳定标识", "备注"):
        assert col in prompt, f"prompt 应含 4 列表头之一：{col}"

    # 11 个白名单属性必须全部出现在 prompt 里
    whitelist = [
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
        "visible text",
    ]
    for attr in whitelist:
        assert attr in prompt, f"prompt 应含白名单属性：{attr}"

    # 阶段 3：prompt 必须含 # Page context 段（让 LLM 用 DOM 快照推 quirks）
    assert "# Page context (DOM snapshots)" in prompt, "prompt 应含 Page context 段"
    assert "page_context_block" in prompt, "prompt 应有 page_context_block 占位符"

    # skill 精简重构（docs/skill-simplification-plan.md）：三件套 + quirks 判定标准 + host 级
    assert "THREE markdown sections" in prompt, "prompt 应要求产三件套（非四件套）"
    assert "Identified sub-capacities" in prompt, "prompt 应含 host 级子能力分组段"
    assert "capacities_line" in prompt, "prompt 应有 capacities_line 占位符"
    # quirks 判定标准量化：必须含「WRITE these」和「Do NOT WRITE these」对照
    assert "WRITE these" in prompt, "prompt 应含 quirks 该写的判据"
    assert "Do NOT WRITE these" in prompt, "prompt 应含 quirks 不该写的判据"
    # 关键判据措辞：agent 能从 DOM 看到的不写
    assert "CANNOT tell from reading the DOM" in prompt, "prompt 应明确 quirks 判定核心标准"


# ---------------------------------------------------------------------------
# skill 精简重构：host 级蒸馏测试
# ---------------------------------------------------------------------------


def test_distill_host_merges_multiple_buckets_into_one_card(bilibili_trace_payload):
    """验收点：同 host 多 bucket 合并成一份 SkillCard（host 级蒸馏）。

    构造同 host 两个 capacity bucket，调 distill_host，断言：
    - 只产出一份 SkillCard（不是两份）
    - capacity 字段含两个 capacity 名（作为 meta 索引）
    - meta.capacities 列表完整
    - prompt 里含子能力分组提示
    """
    bucket = _make_bucket(bilibili_trace_payload)
    # 复制一份，改 capacity，构造同 host 两个 capacity bucket
    bucket2 = bucket.model_copy(deep=True)
    bucket2.bucket_id = f"{bucket.domain}::fill-video-metadata"
    bucket2.canonical_capacity = "fill-video-metadata"
    bucket2.capacity_labels = []  # 避免重复 label

    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {"input_tokens": 10, "output_tokens": 20})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        card = distiller.distill_host(bucket.domain, [bucket, bucket2], use_llm=True)

    assert isinstance(card, SkillCard)
    # host 级只产一份
    assert card.domain == bucket.domain
    # capacity 字段含两个 capacity 名
    assert "upload-content" in card.capacity, f"capacity 应含 upload-content，实际: {card.capacity}"
    assert "fill-video-metadata" in card.capacity, (
        f"capacity 应含 fill-video-metadata，实际: {card.capacity}"
    )
    # meta.capacities 列表完整
    capacities = card.meta.get("capacities", [])
    assert "upload-content" in capacities
    assert "fill-video-metadata" in capacities
    # prompt 含子能力分组提示
    assert "Identified sub-capacities" in captured_prompt[0]
    assert "upload-content" in captured_prompt[0]
    assert "fill-video-metadata" in captured_prompt[0]


def test_distill_buckets_returns_one_card_per_host(bilibili_trace_payload):
    """验收点：distill_buckets 按 host 分组，每个 host 产一份 SkillCard。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with (
        patch("harness.distiller.call_llm") as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        mock_call.return_value = (
            json.dumps(_FAKE_LLM_RESPONSE),
            {"input_tokens": 10, "output_tokens": 20},
        )
        cards = distiller.distill_buckets([bucket], use_llm=True)

    assert len(cards) == 1, "单 host 应只产一份 SkillCard"
    assert cards[0].domain == bucket.domain


# ---------------------------------------------------------------------------
# 阶段 2：element_attrs 双轨测试（模板模式）
# ---------------------------------------------------------------------------


def _make_bucket_with_element_attrs():
    """构造一个带 element_attrs 的 bucket（模拟新格式 trace 反推后的场景）。"""
    from harness.models import Segment, TraceEvent

    events = [
        TraceEvent(
            type="click",
            target="投稿入口",
            selector=None,
            element_attrs={"tag": "a", "id": "nav_upload_btn", "visible_text": "投稿"},
            url="https://www.bilibili.com/",
            timestamp=0,
        ),
        TraceEvent(
            type="change",
            target="视频文件上传",
            selector=None,
            element_attrs={
                "tag": "input",
                "name": "buploader",
                "type": "file",
            },
            url="https://member.bilibili.com/platform/upload/video/frame",
            timestamp=1000,
        ),
        TraceEvent(
            type="input",
            target="标题输入框",
            selector=None,
            element_attrs={
                "tag": "input",
                "type": "text",
                "placeholder": "请输入稿件标题",
            },
            url="https://member.bilibili.com/platform/upload/video/frame",
            timestamp=2000,
        ),
    ]
    # 手工构造 segment（不走 atomize，确保 element_attrs 保留）
    seg = Segment(
        segment_id="test-ea::0::2",
        source_track_id="test-ea",
        domain="bilibili.com",
        start_idx=0,
        end_idx=2,
        events=events,
        boundary_reason="end_of_track",
        entry_url=events[0].url,
        exit_url=events[-1].url,
        duration_ms=2000,
        event_summary="click a id=nav_upload_btn :: 投稿入口\n"
        "change input name=buploader :: 视频文件上传\n"
        "input input type=text placeholder=请输入稿件标题 :: 标题输入框",
    )
    from harness.models import Bucket

    return Bucket(
        bucket_id="bilibili.com::upload-content",
        domain="bilibili.com",
        canonical_capacity="upload-content",
        segment_ids=[seg.segment_id],
        segments=[seg],
    )


def test_template_skill_card_with_element_attrs():
    """阶段 2 验收：带 element_attrs 的 trace，模板产出元素描述表（不是警告头注）。"""
    bucket = _make_bucket_with_element_attrs()
    card = distiller.distill_bucket(bucket, use_llm=False)

    assert card.selectors_md, "selectors_md 为空"
    # 应是元素描述表四列表头行
    assert "| 元素用途 |" in card.selectors_md
    assert "|---|---|---|---|" in card.selectors_md
    # 应含 element_attrs 的属性（非 selector）
    assert "nav_upload_btn" in card.selectors_md
    assert "buploader" in card.selectors_md
    assert "请输入稿件标题" in card.selectors_md
    # 不应出现「模板模式产出（--no-llm）」警告头注（那是无 element_attrs 的老格式 fallback）
    assert "无法产出真正的元素描述表" not in card.selectors_md


def test_template_skill_card_old_format_keeps_warning():
    """阶段 2 验收：老格式 trace（无 element_attrs）模板仍走警告头注 fallback。

    用内联构造的老格式 payload（只 selector，无 element_attrs），不依赖 examples
    文件——examples/bilibili-upload.trace.json 可能被新格式覆盖。
    """
    old_format_payload = {
        "host": "bilibili.com",
        "task_instruction": "test",
        "events": [
            {"type": "click", "target": "投稿按钮", "selector": ".upload-btn", "timestamp": 0},
            {
                "type": "input",
                "target": "标题",
                "selector": "#title",
                "value": "x",
                "timestamp": 1000,
            },
        ],
    }
    bucket = _make_bucket(old_format_payload)
    card = distiller.distill_bucket(bucket, use_llm=False)

    # 老 bilibili trace 只有 selector，应保留警告头注
    assert "模板模式产出" in card.selectors_md
    # 不应是元素描述表（表头行 | 元素用途 | 怎么找到它 | 不应出现；
    # 注意警告头注里「4 列：元素用途 / ...」提示句含「元素用途」，但那不是表头行）
    assert "| 元素用途 |" not in card.selectors_md
    assert "|---|---|---|---|" not in card.selectors_md


# ---------------------------------------------------------------------------
# 阶段 3：page_context（DOM 快照）测试
# ---------------------------------------------------------------------------


def test_distill_bucket_passes_page_context_to_prompt(bilibili_trace_payload):
    """阶段 3 验收：传非空 page_context，LLM prompt 应含 DOM 快照文本。"""
    bucket = _make_bucket(bilibili_trace_payload)
    dom_snapshot = {
        "upload-conver": "[142]<a id=nav_upload_btn /> 投稿\n[3788]<div contenteditable=true />"
    }

    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {"input_tokens": 10, "output_tokens": 20})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        distiller.distill_bucket(bucket, use_llm=True, page_context=dom_snapshot)

    # prompt 应含 DOM 快照内容 + Page context 段标题
    assert "# Page context (DOM snapshots)" in captured_prompt[0]
    assert "nav_upload_btn" in captured_prompt[0], "DOM 文本应进 prompt"
    assert "contenteditable" in captured_prompt[0]
    assert "upload-conver" in captured_prompt[0], "阶段名应进 prompt"


def test_distill_bucket_empty_page_context_shows_placeholder(bilibili_trace_payload):
    """阶段 3 验收：空 page_context，prompt 应显示占位符（不报错）。"""
    bucket = _make_bucket(bilibili_trace_payload)

    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        # page_context=None（老 trace 场景）
        distiller.distill_bucket(bucket, use_llm=True, page_context=None)

    assert "no DOM snapshots provided" in captured_prompt[0]


# ---------------------------------------------------------------------------
# 阶段 4：evidence_block 的 Stages 行
# ---------------------------------------------------------------------------


def test_evidence_block_includes_stages_line():
    """阶段 4 验收：evidence_block 应含 Stages 行（聚合 segment 内各 event 的 stage）。"""
    from harness.distiller import _evidence_block
    from harness.models import Bucket, Segment

    events = [
        TraceEvent(type="click", stage="upload", timestamp=0),
        TraceEvent(type="input", stage="publish?", timestamp=100),
        TraceEvent(type="click", stage=None, timestamp=200),  # 无 stage 不计入
    ]
    seg = Segment(
        segment_id="test::0::2",
        source_track_id="test",
        domain="x.com",
        start_idx=0,
        end_idx=2,
        events=events,
        event_summary="...",
    )
    bucket = Bucket(
        bucket_id="x.com::test",
        domain="x.com",
        canonical_capacity="test",
        segments=[seg],
    )
    block = _evidence_block(bucket)
    # Stages 行应含两个 stage（upload + publish?），None 不计
    assert "Stages:" in block
    assert "upload" in block
    assert "publish?" in block


def test_evidence_block_stages_unknown_when_no_stage():
    """所有 event 都没 stage 时，Stages 行应显示 (unknown)。"""
    from harness.distiller import _evidence_block
    from harness.models import Bucket, Segment

    seg = Segment(
        segment_id="test::0::0",
        source_track_id="test",
        domain="x.com",
        start_idx=0,
        end_idx=0,
        events=[TraceEvent(type="click", stage=None, timestamp=0)],
        event_summary="...",
    )
    bucket = Bucket(
        bucket_id="x.com::test",
        domain="x.com",
        canonical_capacity="test",
        segments=[seg],
    )
    block = _evidence_block(bucket)
    assert "Stages: (unknown)" in block


# ---------------------------------------------------------------------------
# P3.7：蒸馏注入消费端上下文（TreeWalker agent 能力模型）
# ---------------------------------------------------------------------------


def test_prompt_contains_consumer_context_section():
    """P3.7 验收：prompt 模板应含消费端上下文段（agent 动作词汇 + 自动处理清单）。

    这是个 prompt 契约测试——防止后续误改 prompt 时把消费端上下文段删掉。
    """
    prompt = distiller._DISTILL_PROMPT_TEMPLATE

    # 占位符必须在模板里（format 调用填 _CONSUMER_CONTEXT）
    assert "{consumer_context}" in prompt, "prompt 应有 consumer_context 占位符"

    # 消费端上下文常量含关键片段
    ctx = distiller._CONSUMER_CONTEXT
    assert "TreeWalker agent" in ctx, "消费端上下文应指明消费端是 TreeWalker agent"
    # 动作词汇：关键动作名必须列（钉住镜像同步——改 TreeWalker 动作集时这里会红）
    for action in ("upload_file", "select_dropdown", "send_keys", "click", "input_text"):
        assert action in ctx, f"消费端上下文应含动作词汇：{action}"
    # 「已自动处理别写」清单（比动作表更值钱的部分）
    assert "ALREADY handles automatically" in ctx
    assert "JS click" in ctx or "occlusion" in ctx, "应列 JS 点击/遮挡自动兜底"


def test_prompt_quirks_rules_reference_auto_handling():
    """P3.7 验收：quirks Do-NOT-WRITE 规则应含「agent 自动处理的不写」+ 两问决策启发式。"""
    prompt = distiller._DISTILL_PROMPT_TEMPLATE

    # Do-NOT-WRITE 加了「session 层自动处理」一条
    assert "already handles automatically" in prompt, (
        "quirks Do-NOT-WRITE 应含「agent session 层自动处理的不写」规则"
    )
    # 决策启发式从一问扩成两问（DOM 可见 + 自动兜底）
    assert "TWO questions" in prompt or "two questions" in prompt, (
        "决策启发式应扩成两问（DOM 可见 + 自动兜底）"
    )
    # Action-method requirements 规则应指向 Consumer context 的动作词汇
    assert "Consumer context above" in prompt or "action name from the vocabulary" in prompt, (
        "Action-method requirements 规则应指向消费端动作词汇"
    )


def test_distill_bucket_prompt_includes_consumer_context(bilibili_trace_payload):
    """P3.7 验收：真发 LLM 时，user message 应含消费端上下文（mock LLM，不真调）。"""
    bucket = _make_bucket(bilibili_trace_payload)
    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {"input_tokens": 10, "output_tokens": 20})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake-key"),
    ):
        distiller.distill_bucket(bucket, use_llm=True)

    sent = captured_prompt[0]
    # 消费端上下文确实进了 LLM 看到的 prompt
    assert "TreeWalker agent" in sent, "发往 LLM 的 prompt 应含消费端上下文"
    assert "upload_file(index, path)" in sent
    assert "select_dropdown(index, value)" in sent
    assert "ALREADY handles automatically" in sent


def test_consumer_context_mirrors_treewalker_key_actions():
    """P3.7 验收：消费端上下文的动作名应对齐 TreeWalker 动作集关键成员。

    防镜像漂移——TreeWalker 动作集变化时改一边漏改另一边，此测试会红。
    （TreeWalker ACTION_DEFINITIONS 见 src/tree_walker/tools/models.py）
    """
    ctx = distiller._CONSUMER_CONTEXT
    # 这些是 TreeWalker 动作集里蒸馏最相关的、且动作名/签名稳定的成员。
    # 签名串对齐 ACTION_DEFINITIONS + 各 Params 模型（index/value/path/keys 等参数名）。
    must_have = {
        "click": "click(index=…)",
        "input_text": "input_text(index=…, text, clear=true)",
        "select_dropdown": "select_dropdown(index, value)",
        "dropdown_options": "dropdown_options(index)",  # 读下拉选项（select 前探查）
        "upload_file": "upload_file(index, path)",
        "send_keys": "send_keys(keys)",
        "navigate": "navigate(url, new_tab=false)",
        "go_back": "go_back()",
        "wait": "wait(seconds)",
        "done": "done(text, success)",
        "scroll": "scroll(amount, direction)",
        "switch_tab": "switch_tab(tab_id)",
        "close_tab": "close_tab(tab_id",
    }
    for name, signature in must_have.items():
        assert signature in ctx, (
            f"消费端上下文应含 TreeWalker 动作 {name} 的签名（防镜像漂移）：期望含「{signature}」"
        )

    # 关键操作约束（对齐 TreeWalker 原文）——这些是 distiller 必须传达给 LLM 的高价值信息：
    # select_dropdown / upload_file 都明确「不要先点击」，否则 LLM 写出的 sop 会误导 agent。
    assert "click it first" in ctx, (
        "select_dropdown 应传达「不要先点击下拉」（对齐 TreeWalker 原文）"
    )
    assert "Do NOT click the input" in ctx, (
        "upload_file 应传达「不要先点击 input/上传按钮」（对齐 TreeWalker 原文）"
    )
    # send_keys 的组合键格式（对齐 SendKeysParams 原文）
    assert "Control+a" in ctx, "send_keys 应含组合键格式示例（对齐 TreeWalker 原文）"

    # select_dropdown 能力边界（对齐 TreeWalker 原文 "native <select>, role=combobox,
    # role=listbox, or custom dropdown"）——必须明确支持自定义下拉，否则 LLM 看到
    # <div> 下拉会误判「不能用 select_dropdown」（抖音 trace 实测踩过这个坑）。
    assert "custom dropdown" in ctx.lower(), (
        "select_dropdown 应明确支持自定义下拉（对齐 TreeWalker 原文），否则 LLM 会误判"
    )
    # 必须明确警告：不要因为「是 div 不是 select」就否定 select_dropdown
    assert "not a `<select>`" in ctx or "not a `<select>`".lower() in ctx.lower(), (
        "消费端上下文应明确警告：不要因「是 div 不是 select」就否定 select_dropdown"
    )
