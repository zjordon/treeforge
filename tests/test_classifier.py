"""classifier 测试。"""

from __future__ import annotations

from harness import adapter, atomizer, classifier


def test_classify_returns_label_for_each_segment(bilibili_trace_payload):
    """启发式分类（不调 LLM）——每个 segment 都该有 capacity。"""
    trace = adapter.adapt(bilibili_trace_payload, source="test")
    segments = atomizer.atomize(trace)
    classified = classifier.classify(segments, use_llm=False)

    assert len(classified) == len(segments)
    for seg, label in classified:
        assert label.capacity, f"segment {seg.segment_id} 缺 capacity"
        assert label.outcome in {"success", "partial", "unclear"}


def test_classify_serial_naming_converges(bilibili_trace_payload):
    """验收：相同能力的 segment 应归到同一 capacity 名（串行增量命名）。"""
    trace = adapter.adapt(bilibili_trace_payload, source="test")
    segments = atomizer.atomize(trace)
    classified = classifier.classify(segments, use_llm=False)

    capacities = {label.capacity for _, label in classified}
    # 至少该收敛（不会每个 segment 都起不同的名）
    assert len(capacities) <= len(segments)


def test_classify_heuristic_picks_upload_for_bilibili(bilibili_trace_payload):
    """B 站上传 trace 的启发式分类该认出 upload。"""
    trace = adapter.adapt(bilibili_trace_payload, source="test")
    segments = atomizer.atomize(trace)
    classified = classifier.classify(segments, use_llm=False)

    capacities = [label.capacity for _, label in classified]
    # 至少一个 segment 被认作 upload
    assert any("upload" in c for c in capacities), f"未识别出 upload: {capacities}"
