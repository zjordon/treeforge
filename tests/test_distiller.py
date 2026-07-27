"""distiller 测试。

验收（init-plan §7.8）：mock LLM 返回，断言产出的 SkillCard 四个字段都有内容。
**不要真调 LLM**。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from harness import adapter, atomizer, bucketer, classifier, distiller
from harness.models import SkillCard

_FAKE_LLM_RESPONSE = {
    "skill_name": "Upload Video",
    "scope": "Publish a video to bilibili.com member platform.",
    "sop_md": "# SOP — bilibili.com / upload\n\n1. Navigate to https://member.bilibili.com/platform/upload/video/frame\n2. Select video file\n3. Fill title/category/tags\n4. Click publish",
    "selectors_md": (
        "# Selectors — bilibili.com\n\n"
        "| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |\n"
        "|---|---|---|---|\n"
        "| 视频文件上传 | 投稿页拖拽区 | type=file, accept 含 .mp4 | 隐藏 input |\n"
        "| 标题输入框 | 标题文字下方 | placeholder=请输入稿件标题 | 输入框 |\n"
    ),
    "quirks_md": "# Quirks — bilibili.com\n\n- Wait for upload to finish before publish button enables (async).\n- SPA navigation: history.pushState between steps.",
    "api_md": "# API — bilibili.com\n\n- `POST /x/web-interface/preupload` — pre-upload handshake",
}


def _make_bucket(bilibili_trace_payload):
    trace = adapter.adapt(bilibili_trace_payload, source="test")
    segments = atomizer.atomize(trace)
    classified = classifier.classify(segments, use_llm=False)
    buckets = bucketer.bucket(classified)
    assert buckets, "fixture 应该产生至少一个 bucket"
    return buckets[0]


def test_distill_bucket_with_mocked_llm_returns_four_fields(bilibili_trace_payload):
    """验收点：mock LLM，断言 SkillCard 四字段都有内容。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with patch("harness.distiller.call_llm") as mock_call, patch(
        "harness.distiller.config.LLM_KEY", "fake-key"
    ):
        mock_call.return_value = (json.dumps(_FAKE_LLM_RESPONSE), {"input_tokens": 10, "output_tokens": 20})
        card = distiller.distill_bucket(bucket, use_llm=True)

    assert isinstance(card, SkillCard)
    assert card.sop_md, "sop_md 为空"
    assert card.selectors_md, "selectors_md 为空"
    assert card.quirks_md, "quirks_md 为空"
    assert card.api_md, "api_md 为空"
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
    """无 LLM 时退模板，也要产出四字段非空。"""
    bucket = _make_bucket(bilibili_trace_payload)
    card = distiller.distill_bucket(bucket, use_llm=False)

    assert card.sop_md
    assert card.selectors_md
    # quirks_md 在模板模式下是「未提取」占位，也非空
    assert card.quirks_md
    assert card.api_md
    assert card.meta.get("model") == "(template)"
    # 阶段 1：模板模式 selectors_md 应含警告头注（说明是低质量机械产出）
    assert "模板模式产出" in card.selectors_md, "模板 selectors_md 应含质量警告头注"
    assert "--no-llm" in card.selectors_md


def test_distill_bucket_falls_back_on_llm_exception(bilibili_trace_payload):
    """LLM 抛错时退模板，不阻断管线。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with patch("harness.distiller.call_llm") as mock_call, patch(
        "harness.distiller.config.LLM_KEY", "fake-key"
    ):
        mock_call.side_effect = RuntimeError("HTTP 500: simulated gateway error")
        card = distiller.distill_bucket(bucket, use_llm=True)

    # 退模板了，仍要有四字段
    assert card.sop_md
    assert card.selectors_md
    assert card.meta.get("model") == "(template)"


def test_distill_bucket_falls_back_on_unparseable_json(bilibili_trace_payload):
    """LLM 返回无法解析的 JSON 时退模板。"""
    bucket = _make_bucket(bilibili_trace_payload)

    with patch("harness.distiller.call_llm") as mock_call, patch(
        "harness.distiller.config.LLM_KEY", "fake-key"
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
        "id", "name", "type", "placeholder", "aria-label", "role",
        "data-testid", "data-test", "data-cy", "contenteditable", "visible text",
    ]
    for attr in whitelist:
        assert attr in prompt, f"prompt 应含白名单属性：{attr}"

    # 阶段 3：prompt 必须含 # Page context 段（让 LLM 用 DOM 快照推 quirks）
    assert "# Page context (DOM snapshots)" in prompt, "prompt 应含 Page context 段"
    assert "page_context_block" in prompt, "prompt 应有 page_context_block 占位符"


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
            {"type": "input", "target": "标题", "selector": "#title", "value": "x", "timestamp": 1000},
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
    dom_snapshot = {"upload-conver": "[142]<a id=nav_upload_btn /> 投稿\n[3788]<div contenteditable=true />"}

    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {"input_tokens": 10, "output_tokens": 20})

    with patch("harness.distiller.call_llm", side_effect=fake_call_llm), patch(
        "harness.distiller.config.LLM_KEY", "fake-key"
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

    with patch("harness.distiller.call_llm", side_effect=fake_call_llm), patch(
        "harness.distiller.config.LLM_KEY", "fake-key"
    ):
        # page_context=None（老 trace 场景）
        distiller.distill_bucket(bucket, use_llm=True, page_context=None)

    assert "no DOM snapshots provided" in captured_prompt[0]
