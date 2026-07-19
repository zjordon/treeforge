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
    "selectors_md": "# Selectors — bilibili.com\n\n| selector | what | notes |\n|---|---|---|\n| `input[type='file'][accept='video/*']` | file picker | |\n| `input[placeholder='请输入标题']` | title | required |",
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
