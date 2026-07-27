"""atomizer 测试。

验收（init-plan §7.8）：喂一个 fixture trace，断言切出 ≥1 个 segment。
"""

from __future__ import annotations

from harness import adapter, atomizer
from harness.atomizer import _filter_noise, _find_boundaries, _registered_domain
from harness.models import TraceEvent


def test_registered_domain_basic():
    assert _registered_domain("www.bilibili.com") == "bilibili.com"
    assert _registered_domain("member.bilibili.com") == "bilibili.com"
    assert _registered_domain("api.x.com") == "x.com"
    assert _registered_domain("bilibili.com") == "bilibili.com"
    assert _registered_domain("localhost") == "localhost"
    assert _registered_domain("") == ""


def test_filter_noise_drops_iframe_pageload():
    events = [
        TraceEvent(type="navigate", url="https://www.bilibili.com/", timestamp=0),
        TraceEvent(type="navigate", url="https://www.recaptcha.net/recaptcha", timestamp=100),
        TraceEvent(type="click", selector="#btn", url="https://www.bilibili.com/", timestamp=200),
    ]
    cleaned = _filter_noise(events)
    # recaptcha 的 navigate 应该被丢
    assert len(cleaned) == 2
    assert all("recaptcha" not in (e.url or "") for e in cleaned)


def test_filter_noise_merges_duplicate_clicks():
    events = [
        TraceEvent(type="click", selector="#btn", url="https://x.com/", timestamp=0),
        TraceEvent(type="click", selector="#btn", url="https://x.com/", timestamp=500),
        TraceEvent(type="click", selector="#btn", url="https://x.com/", timestamp=900),
    ]
    cleaned = _filter_noise(events)
    # 连续重复点击间隔 < 2s → 合并保留后者，最终剩 1 个
    assert len(cleaned) == 1


def test_filter_noise_keeps_clicks_over_2s():
    events = [
        TraceEvent(type="click", selector="#btn", url="https://x.com/", timestamp=0),
        TraceEvent(type="click", selector="#btn", url="https://x.com/", timestamp=3000),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 2


def test_atomize_bilibili_yields_at_least_one_segment(bilibili_trace_payload):
    """验收点：fixture trace 切出 ≥1 个 segment。"""
    trace = adapter.adapt(bilibili_trace_payload, source="bilibili-upload.trace.json")
    segments = atomizer.atomize(trace)
    assert len(segments) >= 1
    # 每段都该有非空 event_summary（喂给 LLM 用）
    for seg in segments:
        assert seg.event_summary, f"segment {seg.segment_id} 缺 event_summary"
        assert seg.domain, f"segment {seg.segment_id} 缺 domain"
        assert "::" in seg.segment_id


def test_atomize_github_yields_segments(github_trace_payload):
    trace = adapter.adapt(github_trace_payload, source="github-login.trace.json")
    segments = atomizer.atomize(trace)
    assert len(segments) >= 1
    # github trace 跨 /login → /sessions/two-factor → /dashboard，至少该切出几段
    domains = {seg.domain for seg in segments}
    assert "github.com" in domains


def test_find_boundaries_idle_gap():
    events = [
        TraceEvent(type="click", selector="#a", url="https://x.com/p1", timestamp=0),
        TraceEvent(type="click", selector="#b", url="https://x.com/p1", timestamp=1000),
        # 20s 静默
        TraceEvent(type="click", selector="#c", url="https://x.com/p1", timestamp=21000),
    ]
    cuts = _find_boundaries(events, track_domain="x.com")
    # 20s > 15s 阈值 → 应该有 idle_gap 切点
    reasons = [r for _, r in cuts]
    assert "idle_gap" in reasons


def test_atomize_empty_trace():
    from harness.models import Trace

    trace = Trace(host="x.com", events=[])
    segments = atomizer.atomize(trace)
    assert segments == []


# ---------------------------------------------------------------------------
# 阶段 2：element_attrs 双轨测试
# ---------------------------------------------------------------------------


def test_format_attrs_summary_empty():
    """空 dict 返回空串。"""
    from harness.atomizer import _format_attrs_summary

    assert _format_attrs_summary({}) == ""
    assert _format_attrs_summary(None) == ""  # type: ignore[arg-type]


def test_format_attrs_summary_whitelist_only():
    """只渲染白名单属性，过滤 class/style 等不稳定属性。"""
    from harness.atomizer import _format_attrs_summary

    out = _format_attrs_summary({
        "tag": "input",
        "id": "title",
        "class": "form-control",  # 不在白名单，应被丢
        "style": "color:red",     # 不在白名单，应被丢
        "placeholder": "请输入",
        "visible_text": "标题",
    })
    assert "input" in out           # tag
    assert "id=title" in out
    assert "placeholder=请输入" in out
    assert '可见文本"标题"' in out
    assert "class" not in out
    assert "style" not in out


def test_render_summary_prefers_element_attrs():
    """有 element_attrs 时，summary 用 attrs 渲染（不用 selector）。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(
        type="click",
        target="投稿按钮",
        selector=".legacy-selector",  # 应被忽略
        element_attrs={"tag": "a", "id": "nav_upload_btn", "visible_text": "投稿"},
        timestamp=0,
    )
    summary = _render_summary([ev])
    assert "nav_upload_btn" in summary  # 用了 element_attrs
    assert ".legacy-selector" not in summary  # 没用 selector


def test_render_summary_falls_back_to_selector_when_no_attrs():
    """无 element_attrs（老 trace）时，summary 退化用 selector。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(
        type="click",
        target="按钮",
        selector=".btn",
        element_attrs={},  # 空
        timestamp=0,
    )
    summary = _render_summary([ev])
    assert ".btn" in summary  # 退化用 selector
