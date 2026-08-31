"""host 级增量蒸馏测试（P4 S2）——prev_card 注入 prompt + 版本真源。

测试原则：mock LLM（patch call_llm），不真调；捕获 prompt 断言 addendum 注入。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from harness import distiller
from harness.models import Segment, SkillCard, TraceEvent

_FAKE_LLM_RESPONSE = {
    "skill_name": "Upload",
    "scope": "upload flow",
    "sop_md": "# SOP\n\n## 站点功能地图（Site Function Map）\n…\n\n## 典型操作序列\n…",
    "selectors_md": "# Selectors — x.com\n\n…",
    "quirks_md": "# Quirks — x.com\n\n…",
}


def _make_bucket(host: str = "x.com") -> object:
    seg = Segment(
        segment_id=f"{host}::0::1",
        source_track_id="test",
        domain=host,
        start_idx=0,
        end_idx=1,
        events=[TraceEvent(type="click", stage="upload", timestamp=0)],
        event_summary="click / :: 上传",
    )
    from harness.models import Bucket

    return Bucket(
        bucket_id=f"{host}::upload",
        domain=host,
        canonical_capacity="upload",
        segment_ids=[seg.segment_id],
        segments=[seg],
    )


def _prev_card(version: int = 2) -> dict:
    return {
        "host": "x.com",
        "sop_md": "# 旧 SOP（含站点功能地图 v2）",
        "selectors_md": "# 旧 Selectors",
        "quirks_md": "# 旧 Quirks：upload_file 直注",
        "meta": {"distill_version": version},
        "trace_sources": ["a.json"],
    }


def test_prev_card_injected_into_prompt():
    """有 prev_card 且走 LLM 时，prompt 应含 addendum 三文件块 + 旧内容。"""
    captured: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_host(
            "x.com", [_make_bucket()], use_llm=True, prev_card=_prev_card(version=2)
        )

    sent = captured[0]
    assert "EXISTING KNOWLEDGE (distill_version 2)" in sent
    assert "旧 SOP（含站点功能地图 v2）" in sent
    assert "旧 Selectors" in sent
    assert "旧 Quirks：upload_file 直注" in sent
    # 合并规则（2026-08-30 修订，对齐 Browser-BC）：以旧卡为基线更新 + 反丢弃 + 新证据优先
    assert "UPDATED VERSION" in sent, "增量应是基于旧卡的更新版，不是另起炉灶"
    assert "your **BASE**" in sent, "旧卡应明确为基线（carry forward 不丢主题）"
    assert "compress the wording" in sent, "允许压缩措辞"
    assert "do NOT silently drop topics" in sent, "反丢弃约束"
    assert "new evidence wins" in sent
    assert "discarding topics wholesale" in sent, "超限靠压缩不靠丢弃"
    # 版本真源：prev(2) + 1 = 3
    assert card.meta["distill_version"] == 3


def test_no_prev_card_no_addendum():
    """无 prev_card 时 prompt 不含 addendum（回归：首蒸行为不变）。"""
    captured: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_host("x.com", [_make_bucket()], use_llm=True)

    assert "EXISTING KNOWLEDGE" not in captured[0]
    # 首蒸版本：bucket 计数 + 1 = 1
    assert card.meta["distill_version"] == 1


def test_prev_card_oversize_clipped():
    """旧卡超预算被截断（sop 8000），不撑爆 prompt。"""
    big = "# " + "x" * 20000
    prev = _prev_card()
    prev["sop_md"] = big
    captured: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        distiller.distill_host("x.com", [_make_bucket()], use_llm=True, prev_card=prev)

    sent = captured[0]
    assert "…(truncated)" in sent
    assert len(sent) < 60000  # 没有整个塞进去


def test_sop_budget_pinned_at_8000():
    """sop 预算钉在 8000（Browser-BC 口径；2026-08-30 决策：不盲目上调——
    站点级是有界摘要，靠压缩措辞不靠扩预算）。"""
    assert distiller._PREV_SOP_BUDGET == 8000


def test_capacities_union_with_prev_card():
    """capacities 与旧卡并集（信息性清单随累积只增不减，修复「只剩最后一个任务」）。"""
    captured: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {})

    prev = _prev_card(version=3)
    prev["meta"]["capacities"] = ["lookup-orders", "find-products"]
    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_host(
            "x.com",
            [_make_bucket()],  # 本次 capacity = upload
            use_llm=True,
            prev_card=prev,
        )

    # prompt 的 capacities 行含旧任务的 capacity（并集，不只当前任务）
    assert "`lookup-orders`" in captured[0]
    assert "`upload`" in captured[0]
    # meta.capacities 存并集
    assert card.meta["capacities"] == ["lookup-orders", "find-products", "upload"]


def test_distill_buckets_passes_prev_cards_by_host():
    """distill_buckets 按 host 从 prev_cards 取旧卡透传。"""
    captured: list = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_LLM_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        cards = distiller.distill_buckets(
            [_make_bucket("x.com")],
            use_llm=True,
            prev_cards={"x.com": _prev_card(version=4)},
        )

    assert len(cards) == 1
    assert "EXISTING KNOWLEDGE (distill_version 4)" in captured[0]
    assert cards[0].meta["distill_version"] == 5


def test_template_mode_ignores_prev_card():
    """模板模式（--no-llm）不消费 prev_card（决策 4：模板不累积），也不调 LLM。"""
    with patch("harness.distiller.call_llm") as mock_call:
        card = distiller.distill_host(
            "x.com", [_make_bucket()], use_llm=False, prev_card=_prev_card(version=9)
        )
    mock_call.assert_not_called()
    assert card.meta["model"] == "(template)"


# ---------------------------------------------------------------------------
# localhost 事故回归（P4 修复）：LLM 间歇性输出畸形 JSON → 解析失败
# 原行为：静默退模板 + 模板卡覆盖 registry 好卡（累积倒退）。
# 修复：① 解析失败重试一次（重取新样本）② 全失败且有旧卡 → 保旧卡（不产模板垃圾）
#       ③ registry 落盘跳过模板兜底卡。
# ---------------------------------------------------------------------------


def test_parse_failure_retries_then_succeeds():
    """第 1 次返回畸形 JSON、第 2 次正常 → 重试成功，调了 2 次。"""
    responses = iter(
        [
            ('{ 这是畸形 JSON，内含未转义引号 "x"', {}),  # 第 1 次畸形
            (json.dumps(_FAKE_LLM_RESPONSE), {}),  # 第 2 次正常
        ]
    )

    def fake_call_llm(prompt, **kwargs):
        return next(responses)

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm) as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_host(
            "x.com", [_make_bucket()], use_llm=True, prev_card=_prev_card(version=2)
        )

    assert mock_call.call_count == 2, "解析失败应重试一次"
    assert card.meta["model"] != "(template)", "重试成功不应退模板"
    assert card.meta["distill_version"] == 3  # prev(2)+1


def test_all_attempts_failed_with_prev_keeps_prev_card():
    """两次都失败 + 有旧卡 → 保旧卡内容（不产模板垃圾），版本不倒退。"""
    with (
        patch(
            "harness.distiller.call_llm",
            side_effect=ValueError("无法从模型输出解析 JSON"),
        ) as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_host(
            "x.com", [_make_bucket()], use_llm=True, prev_card=_prev_card(version=2)
        )

    assert mock_call.call_count == 2, "应重试后仍失败才兜底"
    # 保旧卡：内容来自 prev，不是模板
    assert card.sop_md == "# 旧 SOP（含站点功能地图 v2）"
    assert card.quirks_md == "# 旧 Quirks：upload_file 直注"
    assert card.meta["model"] != "(template)"
    assert card.meta["distill_version"] == 2  # 版本不倒退（没成功就不 +1）
    assert card.meta.get("kept_after_llm_failure") is True  # 留排查标记


def test_all_attempts_failed_without_prev_falls_to_template():
    """两次都失败 + 无旧卡 → 退模板（原行为保留，首次蒸馏失败的兜底）。"""
    with (
        patch(
            "harness.distiller.call_llm",
            side_effect=RuntimeError("LLM call failed after 6 retries: boom"),
        ) as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_host("x.com", [_make_bucket()], use_llm=True)

    assert mock_call.call_count == 2
    assert card.meta["model"] == "(template)"


def test_save_cards_to_registry_skips_template_fallback(tmp_path):
    """registry 落盘跳过模板兜底卡（一次 LLM 失败不毁旧卡）。"""
    from harness.registry import load_card, save_card
    from server.distill_api import _save_cards_to_registry

    # 先存一张好卡（v2 真 LLM 卡）
    good = SkillCard(
        bucket_id="x.com::upload",
        domain="x.com",
        capacity="upload",
        skill_name="Good",
        scope="s",
        sop_md="# 好 SOP v2",
        selectors_md="# Sel",
        quirks_md="# Qu",
        meta={"model": "glm-5.2", "distill_version": 2},
    )
    save_card(tmp_path, good, ["a.json"])

    # 兜底模板卡（model=(template)）尝试落盘 → 应被跳过
    bad = SkillCard(
        bucket_id="x.com::upload",
        domain="x.com",
        capacity="upload",
        skill_name="Bad",
        scope="s",
        sop_md="# 模板垃圾",
        selectors_md="# Sel",
        quirks_md="# Qu",
        meta={"model": "(template)", "distill_version": 1},
    )
    _save_cards_to_registry(tmp_path, [bad], ["b.json"])

    data = load_card(tmp_path, "x.com")
    assert data["sop_md"] == "# 好 SOP v2", "模板兜底卡不应覆盖好卡"
    assert data["meta"]["distill_version"] == 2

    # 真卡正常落（version 3 覆盖）
    good3 = good.model_copy(deep=True)
    good3.sop_md = "# 好 SOP v3"
    good3.meta = {**good.meta, "distill_version": 3}
    _save_cards_to_registry(tmp_path, [good3], ["c.json"])
    data = load_card(tmp_path, "x.com")
    assert data["sop_md"] == "# 好 SOP v3"
    assert data["meta"]["distill_version"] == 3
    assert data["trace_sources"] == ["a.json", "c.json"]  # b 被跳过不进来源
