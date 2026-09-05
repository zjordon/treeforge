"""adapter 脱敏测试。

覆盖 ADAPT 阶段的 `_redact_value` 逻辑——脱敏是进 LLM 前的关键防线。
"""

from __future__ import annotations

from harness.adapter import _looks_like_file_path, _redact_value, adapt

# ---------------------------------------------------------------------------
# 文件路径检测（阶段 2 bug 修复）
# ---------------------------------------------------------------------------


def test_looks_like_file_path_windows_backslash():
    assert _looks_like_file_path(r"D:\Videos\test\final\2026-04-29-20-41-59.mp4")


def test_looks_like_file_path_forward_slash():
    assert _looks_like_file_path("D:/dev/git/x/横封面.png")


def test_looks_like_file_path_relative():
    assert _looks_like_file_path("./data/test.json")


def test_not_file_path_pure_text():
    assert not _looks_like_file_path("ai浏览器第五期-browse-use")
    assert not _looks_like_file_path("浏览器Agent")


def test_not_file_path_card_number():
    """真卡号不该被当文件路径。"""
    assert not _looks_like_file_path("4111 1111 1111 1111")


def test_not_file_path_no_extension():
    """无扩展名的 URL/路径不算文件路径（但这无害——只是不跳过卡号脱敏）。"""
    assert not _looks_like_file_path("https://example.com/path")


# ---------------------------------------------------------------------------
# _redact_value 脱敏
# ---------------------------------------------------------------------------


def test_redact_sensitive_password_field():
    assert _redact_value("user password input", "my-secret-123") == "<redacted>"


def test_redact_email_in_normal_field():
    assert _redact_value("username", "user@example.com") == "<runtime-email>"


def test_redact_card_number():
    assert _redact_value("card", "4111 1111 1111 1111") == "<runtime-payment-card>"


def test_redact_normal_value_unchanged():
    assert _redact_value("title", "my video") == "my video"


def test_redact_file_path_not_redacted_as_card():
    """阶段 2 bug 修复：文件路径里的数字串不该被当卡号脱敏。

    回归用例：D:\\Videos\\...\\2026-04-29-20-41-59.mp4 里的 16 位数字
    之前被 _CARD_RE 误杀成 <runtime-payment-card>。
    """
    path = r"D:\Videos\test\final\2026-04-29-20-41-59.mp4"
    result = _redact_value("file", path)
    assert result == path, f"文件路径被误脱敏: {result}"
    assert "<runtime-payment-card>" not in result


def test_redact_file_path_with_chinese():
    """含中文的文件路径也不该被脱敏。"""
    path = "D:/dev/git/claude/skills-deom/ppt/browser-use/横封面.png"
    result = _redact_value("file", path)
    assert result == path


def test_redact_none_and_empty():
    assert _redact_value("x", None) is None
    assert _redact_value("x", "") == ""


def test_redact_cvv_field_name():
    assert _redact_value("cvv", "123") == "<redacted>"


# ---------------------------------------------------------------------------
# page_context 读取（阶段 3）
# ---------------------------------------------------------------------------


def test_adapt_reads_page_context():
    """带 page_context 的 payload 应正确读取到 Trace.page_context。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "timestamp": 0}],
        "page_context": {"stage1": "[142]<a id=btn /> 按钮", "stage2": "[200]<input />"},
    }
    trace = adapt(payload, source="test")
    assert trace.page_context == {
        "stage1": "[142]<a id=btn /> 按钮",
        "stage2": "[200]<input />",
    }


def test_adapt_old_trace_without_page_context_defaults_empty():
    """老 trace（无 page_context 字段）应为空 dict，不报错。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "selector": ".btn", "timestamp": 0}],
    }
    trace = adapt(payload, source="test")
    assert trace.page_context == {}


def test_adact_page_context_non_dict_defaults_empty():
    """page_context 不是 dict（异常输入）时应退化为空，不抛错。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "timestamp": 0}],
        "page_context": "not a dict",  # 异常输入
    }
    trace = adapt(payload, source="test")
    assert trace.page_context == {}


# ---------------------------------------------------------------------------
# stage 读取（阶段 4）
# ---------------------------------------------------------------------------


def test_normalize_event_reads_stage():
    """带 stage 的 event 应正确读取（含带? 的推断值）。"""
    payload = {
        "host": "x.com",
        "events": [
            {"type": "click", "timestamp": 0, "stage": "upload"},
            {"type": "input", "timestamp": 100, "stage": "publish?"},
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.events[0].stage == "upload"
    assert trace.events[1].stage == "publish?"


def test_normalize_event_stage_defaults_none():
    """老 event（无 stage 字段）应为 None，不报错。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "selector": ".btn", "timestamp": 0}],
    }
    trace = adapt(payload, source="test")
    assert trace.events[0].stage is None


def test_normalize_event_stage_non_string_coerced():
    """stage 是非字符串（异常输入）时应规整为 str 或 None。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "timestamp": 0, "stage": 123}],
    }
    trace = adapt(payload, source="test")
    # 数字被 str() 规整
    assert trace.events[0].stage == "123"


# ---------------------------------------------------------------------------
# P3.6：signals 透传（采集层 attach → TraceEvent.signals → distiller）
# ---------------------------------------------------------------------------


def test_normalize_event_reads_signals():
    """带 signals 的 event（采集层 attach 的 modal/dropdown）应透传到 TraceEvent。"""
    payload = {
        "host": "x.com",
        "events": [
            {
                "type": "click",
                "timestamp": 0,
                "signals": [{"type": "modal_opened", "selector": "div.ant-modal", "ts": 1200}],
            },
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.events[0].signals == [
        {"type": "modal_opened", "selector": "div.ant-modal", "ts": 1200}
    ]


def test_normalize_event_signals_defaults_empty():
    """老 event（无 signals 字段）应为空 list，不报错。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "selector": ".btn", "timestamp": 0}],
    }
    trace = adapt(payload, source="test")
    assert trace.events[0].signals == []


def test_normalize_event_signals_non_list_defaults_empty():
    """signals 是非 list（异常输入）时应规整为空 list，不报错。"""
    payload = {
        "host": "x.com",
        "events": [{"type": "click", "timestamp": 0, "signals": "oops"}],
    }
    trace = adapt(payload, source="test")
    assert trace.events[0].signals == []


# ---------------------------------------------------------------------------
# host key 语义（S0b，issue #9）：端口限定 key 对齐 TreeWalker
# ---------------------------------------------------------------------------


def test_adapt_upgrades_bare_host_to_port_qualified_key():
    """顶层 host 裸 hostname（老 captures 旧形）+ 事件 URL 带端口 → 升级 localhost_7780。

    存量 localhost captures 的事件 URL 都是 http://localhost:7780/...，不改数据、
    重蒸即落到新 key（issue #9 待办 1 的存量兼容路径）。
    """
    payload = {
        "host": "localhost",
        "events": [
            {"type": "navigate", "timestamp": 0, "url": "http://localhost:7780/admin/dashboard/"},
            {"type": "click", "timestamp": 500, "url": "http://localhost:7780/admin/sales/"},
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.host == "localhost_7780"


def test_adapt_keeps_port_qualified_host_as_is():
    """顶层 host 已是端口限定 key（新采集）→ 原样保留（幂等）。"""
    payload = {
        "host": "localhost_7780",
        "events": [
            {"type": "click", "timestamp": 0, "url": "http://localhost:7780/admin/"},
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.host == "localhost_7780"


def test_adapt_payload_host_wins_over_different_site_url():
    """事件 URL 与顶层 host 不同站（如登录跳转）→ 仍以顶层 host 为准。"""
    payload = {
        "host": "member.bilibili.com",
        "events": [
            {"type": "navigate", "timestamp": 0, "url": "https://passport.bilibili.com/login"},
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.host == "member.bilibili.com"


def test_adapt_no_top_host_falls_back_to_port_qualified_url():
    """顶层缺 host：URL 推断兜底也是端口限定 key。"""
    payload = {
        "events": [
            {"type": "click", "timestamp": 0, "url": "http://localhost:7780/a"},
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.host == "localhost_7780"


def test_adapt_no_port_host_unchanged():
    """无端口回归：bilibili 等公网站点的 host 不受影响。"""
    payload = {
        "host": "member.bilibili.com",
        "events": [
            {"type": "click", "timestamp": 0, "url": "https://member.bilibili.com/platform/home"},
        ],
    }
    trace = adapt(payload, source="test")
    assert trace.host == "member.bilibili.com"
