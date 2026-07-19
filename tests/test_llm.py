"""LLM 客户端测试——只测纯逻辑（协议探测 / JSON 解析），不真发请求。"""

from __future__ import annotations

from harness.llm import (
    _endpoint,
    _escape_invalid_json_backslashes,
    _strip_fence,
    is_anthropic,
    parse_json_from_model,
)


def test_is_anthropic_official_endpoint():
    assert is_anthropic("https://api.anthropic.com") is True


def test_is_anthropic_third_party_gateway_with_path_segment():
    """验收点：智谱 BigModel 风格网关（/anthropic 路径段）该被判为 Anthropic。

    这是 Browser-BC Windows 适配的关键修复（避免误判为 OpenAI 走错端点 404）。
    """
    assert is_anthropic("https://open.bigmodel.cn/api/anthropic") is True
    assert is_anthropic("https://gateway.com/anthropic-proxy") is True


def test_is_anthropic_openai_endpoints_false():
    assert is_anthropic("https://api.openai.com") is False
    assert is_anthropic("https://api.deepseek.com") is False
    assert is_anthropic("https://my-gateway.com/v1") is False


def test_is_anthropic_case_insensitive():
    assert is_anthropic("https://API.Anthropic.COM") is True


def test_endpoint_paths():
    assert _endpoint("https://api.anthropic.com").endswith("/v1/messages")
    assert _endpoint("https://open.bigmodel.cn/api/anthropic").endswith("/v1/messages")
    assert _endpoint("https://api.openai.com").endswith("/v1/chat/completions")


def test_parse_json_from_model_plain():
    assert parse_json_from_model('{"a": 1}') == {"a": 1}


def test_parse_json_from_model_with_fence():
    text = "```json\n{\"a\": 1}\n```"
    assert parse_json_from_model(text) == {"a": 1}


def test_parse_json_from_model_with_prose_around():
    text = "Here is the result:\n```json\n{\"capacity\": \"login\"}\n```\nDone."
    assert parse_json_from_model(text) == {"capacity": "login"}


def test_parse_json_from_model_tolerates_bare_newlines_in_strings():
    """验收点：模型常把多行 markdown 裸塞进 JSON 字符串，要能恢复。"""
    # 裸换行在字符串值里（非法 JSON）
    text = '{"sop_md": "line 1\nline 2", "capacity": "x"}'
    result = parse_json_from_model(text)
    assert result["capacity"] == "x"
    assert "line 1" in result["sop_md"]


def test_parse_json_from_model_raises_on_garbage():
    import pytest

    with pytest.raises(ValueError):
        parse_json_from_model("not json at all no braces")


def test_strip_fence_bare():
    assert _strip_fence("```json\n{}\n```") == "{}"


def test_escape_invalid_json_backslashes_preserves_escaped():
    s = '{"a": "line1\\nline2"}'  # 已正确转义
    assert _escape_invalid_json_backslashes(s) == s
