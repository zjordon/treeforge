"""atomizer 测试。

验收（init-plan §7.8）：喂一个 fixture trace，断言切出 ≥1 个 segment。
"""

from __future__ import annotations

from harness import adapter, atomizer
from harness.atomizer import (
    _filter_noise,
    _find_boundaries,
    _registered_domain,
    _same_input_target,
)
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


# ---------------------------------------------------------------------------
# click 合并 bug 回归（P3.7 期间发现）：
# 原逻辑只比 selector，新格式 trace 用 element_attrs 不填 selector，两个空 selector
# 恒等（"" == ""）导致同页面任意两个 < 2s 的 click 都被判重被吞（误杀「选择合集」「确定」
# 等不同按钮）。修复后按 element_attrs 稳定标识判等，都无标识时保守判不同（不合并）。
# ---------------------------------------------------------------------------


def test_filter_noise_keeps_different_clicks_with_element_attrs():
    """不同按钮的 click（element_attrs 稳定标识不同，selector 都空）不应被合并。

    这是 ae99467f 抖音 trace 漏「选择合集」的根因回归：同页面连续点击不同按钮，
    selector 都为空时旧逻辑误判为重复。修复后必须全保留。
    """
    events = [
        TraceEvent(
            type="click",
            element_attrs={"tag": "div", "aria-label": "请选择合集"},
            url="https://x.com/up",
            timestamp=0,
        ),
        TraceEvent(
            type="click",
            element_attrs={"tag": "div", "aria-label": "请选择自主声明"},
            url="https://x.com/up",
            timestamp=500,
        ),
        TraceEvent(
            type="click",
            element_attrs={"tag": "button", "aria-label": "确定"},
            url="https://x.com/up",
            timestamp=900,
        ),
    ]
    cleaned = _filter_noise(events)
    # 三个不同按钮（aria-label 不同）必须全保留——修复前会被合并成 1 个
    assert len(cleaned) == 3, "不同按钮的 click（element_attrs 标识不同）不应被合并"


def test_filter_noise_merges_duplicate_clicks_by_element_attrs():
    """同一按钮的连续重复 click（element_attrs 稳定标识相同）应合并。

    修复后判等改走 element_attrs，要确保真正的重复点击（同 id/aria-label）仍能合并。
    """
    attrs = {"tag": "button", "id": "submit-btn", "aria-label": "提交"}
    events = [
        TraceEvent(type="click", element_attrs=attrs, url="https://x.com/", timestamp=0),
        TraceEvent(type="click", element_attrs=attrs, url="https://x.com/", timestamp=500),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 1, "同 id 同 aria-label 的连续 click 应合并"


def test_filter_noise_keeps_clicks_when_both_attrs_missing():
    """两边都无 element_attrs 且都无 selector 时保守判不同（不合并）。

    防止回到「空 selector 恒等」的旧 bug：信息不足时不合并，宁可多保留也不误杀。
    """
    events = [
        TraceEvent(type="click", url="https://x.com/", timestamp=0),  # 无 selector 无 attrs
        TraceEvent(type="click", url="https://x.com/", timestamp=500),  # 同上
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 2, "都无 selector 且都无 element_attrs 时应保守保留（不合并）"


def test_filter_noise_merges_duplicate_clicks_by_visible_text():
    """同 tag + 同 visible_text 的连续 click（无稳定标识）应合并（同种按钮重复点）。"""
    events = [
        TraceEvent(
            type="click",
            element_attrs={"tag": "span", "visible_text": "展开"},
            url="https://x.com/",
            timestamp=0,
        ),
        TraceEvent(
            type="click",
            element_attrs={"tag": "span", "visible_text": "展开"},
            url="https://x.com/",
            timestamp=800,
        ),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 1, "同 tag+visible_text 的连续 click（无稳定标识）应合并"


# ---------------------------------------------------------------------------
# input 合并（P2.3.2）：同目标连续 input 合并保留终值
# 真机场景：标题一次输入被扩展 debounce 切成 5 条（人类停顿 0.6–3.8s 远超 400ms 窗口）
# ---------------------------------------------------------------------------


def test_filter_noise_merges_consecutive_inputs_same_target():
    """同 placeholder 连续 input（模拟真机标题场景）合并成 1 个，value 是终值。"""
    attrs = {"tag": "input", "placeholder": "请输入稿件标题"}
    events = [
        TraceEvent(
            type="input", value="", element_attrs=attrs, url="https://x.com/up", timestamp=0
        ),
        TraceEvent(
            type="input", value="ai", element_attrs=attrs, url="https://x.com/up", timestamp=630
        ),
        TraceEvent(
            type="input",
            value="ai浏览器第5a",
            element_attrs=attrs,
            url="https://x.com/up",
            timestamp=4408,
        ),
        TraceEvent(
            type="input",
            value="ai浏览器第5期-b",
            element_attrs=attrs,
            url="https://x.com/up",
            timestamp=6235,
        ),
        TraceEvent(
            type="input",
            value="ai浏览器第5期-browser-use",
            element_attrs=attrs,
            url="https://x.com/up",
            timestamp=8452,
        ),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 1
    assert cleaned[0].value == "ai浏览器第5期-browser-use"  # 终值


def test_filter_noise_keeps_inputs_different_target():
    """切换目标（标题→标签）不合并，各自保留。"""
    title_attrs = {"tag": "input", "placeholder": "请输入稿件标题"}
    tag_attrs = {"tag": "input", "placeholder": "按回车键Enter创建标签"}
    events = [
        TraceEvent(
            type="input", value="ai", element_attrs=title_attrs, url="https://x.com/up", timestamp=0
        ),
        TraceEvent(
            type="input",
            value="ai浏览器",
            element_attrs=title_attrs,
            url="https://x.com/up",
            timestamp=1000,
        ),
        TraceEvent(
            type="input",
            value="测试",
            element_attrs=tag_attrs,
            url="https://x.com/up",
            timestamp=2000,
        ),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 2
    assert cleaned[0].value == "ai浏览器"  # 标题合并终值
    assert cleaned[1].value == "测试"  # 标签独立


def test_filter_noise_keeps_inputs_over_30s():
    """间隔 > 30s 的同目标 input 不合并（视为两次独立输入）。"""
    attrs = {"tag": "input", "placeholder": "搜索"}
    events = [
        TraceEvent(type="input", value="a", element_attrs=attrs, url="https://x.com/", timestamp=0),
        TraceEvent(
            type="input", value="b", element_attrs=attrs, url="https://x.com/", timestamp=35_000
        ),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 2


def test_filter_noise_input_merge_preserves_final_value():
    """值序列 ai → aib → aic，合并后只保留终值 aic。"""
    attrs = {"tag": "input", "name": "title"}
    events = [
        TraceEvent(
            type="input", value="ai", element_attrs=attrs, url="https://x.com/", timestamp=0
        ),
        TraceEvent(
            type="input", value="aib", element_attrs=attrs, url="https://x.com/", timestamp=500
        ),
        TraceEvent(
            type="input", value="aic", element_attrs=attrs, url="https://x.com/", timestamp=900
        ),
    ]
    cleaned = _filter_noise(events)
    assert len(cleaned) == 1
    assert cleaned[0].value == "aic"


def test_same_input_target_stable_attr_match():
    """同 id 视为同目标；不同 id 视为不同目标。"""
    a = TraceEvent(type="input", element_attrs={"tag": "input", "id": "t1"}, timestamp=0)
    b = TraceEvent(type="input", element_attrs={"tag": "input", "id": "t1"}, timestamp=1)
    c = TraceEvent(type="input", element_attrs={"tag": "input", "id": "t2"}, timestamp=2)
    assert _same_input_target(a, b) is True
    assert _same_input_target(a, c) is False


def test_same_input_target_no_stable_attr_falls_back_to_tag_and_selector():
    """都无稳定标识时退化到同 tag + 同 selector。"""
    a = TraceEvent(type="input", element_attrs={"tag": "div"}, selector=".ed", timestamp=0)
    b = TraceEvent(type="input", element_attrs={"tag": "div"}, selector=".ed", timestamp=1)
    c = TraceEvent(type="input", element_attrs={"tag": "div"}, selector=".other", timestamp=2)
    assert _same_input_target(a, b) is True
    assert _same_input_target(a, c) is False


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

    out = _format_attrs_summary(
        {
            "tag": "input",
            "id": "title",
            "class": "form-control",  # 不在白名单，应被丢
            "style": "color:red",  # 不在白名单，应被丢
            "placeholder": "请输入",
            "visible_text": "标题",
        }
    )
    assert "input" in out  # tag
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


# ---------------------------------------------------------------------------
# 阶段 4：stage 标记
# ---------------------------------------------------------------------------


def test_render_summary_marks_stage_suffix():
    """有 stage 的 event，summary 行尾应带 [stage=xxx] 标记。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(
        type="click",
        target="投稿",
        element_attrs={"tag": "a", "id": "nav_upload_btn"},
        stage="upload",
        timestamp=0,
    )
    summary = _render_summary([ev])
    assert "[stage=upload]" in summary


def test_render_summary_inferred_stage_with_question_mark():
    """带? 的推断 stage 也要正确标记。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(type="click", target="x", stage="publish?", timestamp=0)
    summary = _render_summary([ev])
    assert "[stage=publish?]" in summary


def test_render_summary_no_stage_suffix_when_none():
    """无 stage（None）的 event 不带 stage 标记。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(type="click", target="x", stage=None, timestamp=0)
    summary = _render_summary([ev])
    assert "[stage=" not in summary


def test_render_summary_different_stages_not_folded():
    """不同 stage 的相同动作不应被折叠（不同阶段是不同上下文）。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    # 两个相同 type/target/selector 但不同 stage 的 event
    events = [
        TraceEvent(type="click", target="btn", selector="#x", stage="upload", timestamp=0),
        TraceEvent(type="click", target="btn", selector="#x", stage="publish", timestamp=100),
    ]
    summary = _render_summary(events)
    # 应有两行（不折叠成 x2）
    assert summary.count("[stage=upload]") == 1
    assert summary.count("[stage=publish]") == 1
    assert "x2" not in summary


def test_render_summary_fold_three_same_lines():
    """连续 3 个相同行折叠成 x3（回归测试：tail[3:] off-by-one 崩溃）。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    # 3 个完全相同的 input 事件（同 stage，模拟连续输入被切成多段）
    events = [
        TraceEvent(
            type="input", target="标题", selector="#title", value="a", stage="frame", timestamp=0
        ),
        TraceEvent(
            type="input", target="标题", selector="#title", value="a", stage="frame", timestamp=100
        ),
        TraceEvent(
            type="input", target="标题", selector="#title", value="a", stage="frame", timestamp=200
        ),
    ]
    summary = _render_summary(events)
    # 应折叠成 1 行 x3（不崩溃）
    assert "x3" in summary
    assert summary.count("\n") == 0  # 只剩一行


# ---------------------------------------------------------------------------
# P3.6：signal 标记渲染（让 LLM 在 summary 看到动作触发的弹窗/下拉）
# ---------------------------------------------------------------------------


def test_render_summary_marks_signal_suffix():
    """有 signals 的 event，summary 行尾应带 [signal=modal_opened] 标记。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(
        type="click",
        target="上传",
        selector="#upload-btn",
        signals=[{"type": "modal_opened", "selector": "div.modal", "ts": 100}],
        timestamp=0,
    )
    summary = _render_summary([ev])
    assert "[signal=modal_opened]" in summary


def test_render_summary_marks_multiple_signal_kinds():
    """多个信号（modal + dropdown）应合并到一行标记里。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(
        type="click",
        target="x",
        signals=[
            {"type": "modal_opened"},
            {"type": "dropdown_opened"},
        ],
        timestamp=0,
    )
    summary = _render_summary([ev])
    assert "modal_opened" in summary
    assert "dropdown_opened" in summary


def test_render_summary_no_signal_suffix_when_empty():
    """无 signals 的 event 不带 signal 标记。"""
    from harness.atomizer import _render_summary
    from harness.models import TraceEvent

    ev = TraceEvent(type="click", target="x", signals=[], timestamp=0)
    summary = _render_summary([ev])
    assert "[signal=" not in summary


def test_format_attrs_includes_accept_and_jsclick():
    """P3.6：accept（upload）+ data-tw-jsclick（JS 点击标记）应进 attrs summary。"""
    from harness.atomizer import _format_attrs_summary

    summary = _format_attrs_summary(
        {"tag": "input", "type": "file", "accept": "image/*", "data-tw-jsclick": "1"}
    )
    assert "accept=image/*" in summary
    assert "data-tw-jsclick=1" in summary
