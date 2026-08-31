"""任务级双产物测试（P4 S4）——distill_task / write_task_card / slug 稳定化 / 管线双跳。

测试原则：mock LLM（patch call_llm）捕获 prompt；文件系统用 tmp_path；不真调 LLM。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from adapters.treewalker_adapter import list_task_cards, task_card_dir, write_task_card
from harness import distiller
from harness.models import Bucket, Segment, SkillCard, TraceEvent

_FAKE_TASK_RESPONSE = {
    "task_slug": "upload-video",
    "task_keywords": ["上传", "视频", "发布"],
    "sop_md": "# 任务 SOP\n\nstep 1: …",
    "selectors_md": "# Selectors — x.com\n\n…",
    "quirks_md": "# Quirks — x.com\n\n…",
}


def _make_bucket(host: str = "x.com") -> Bucket:
    seg = Segment(
        segment_id=f"{host}::0::1",
        source_track_id="test",
        domain=host,
        start_idx=0,
        end_idx=1,
        events=[TraceEvent(type="click", stage="upload", timestamp=0)],
        event_summary="click / :: 上传",
    )
    return Bucket(
        bucket_id=f"{host}::upload",
        domain=host,
        canonical_capacity="upload",
        segment_ids=[seg.segment_id],
        segments=[seg],
    )


def _task_card(**meta_over: object) -> SkillCard:
    meta = {"task_slug": "upload-video", "task_keywords": ["上传"], "distilled_at": "2026"}
    meta.update(meta_over)  # type: ignore[arg-type]
    return SkillCard(
        bucket_id="x.com::upload::task",
        domain="x.com",
        capacity="upload",
        skill_name="Upload",
        scope="upload flow",
        sop_md="# SOP",
        selectors_md="# Sel",
        quirks_md="# Qu",
        meta=meta,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# distill_task：prompt 契约 + slug/keywords 解析
# ---------------------------------------------------------------------------


def test_task_prompt_contains_description_and_existing_cards():
    """任务 prompt 含任务描述 + 现有任务卡清单 + slug 判定规则；不含站点地图要求。"""
    captured: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_TASK_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_task(
            "x.com",
            [_make_bucket()],
            task_description="上传并发布视频",
            existing_tasks=[{"slug": "old-task", "task_description": "旧任务"}],
            use_llm=True,
        )

    sent = captured[0]
    assert "TASK-LEVEL skill card" in sent
    assert "上传并发布视频" in sent  # 任务描述注入
    assert "`old-task`" in sent and "旧任务" in sent  # 现有卡清单注入
    assert "MUST return that" in sent  # slug 复用判定规则
    assert "task_slug" in sent
    # 任务级不要求写站点地图（那是 host 卡的事）
    assert "Required opening section" not in sent
    # 共用块拼装（不复制两份的验证）：spec 片段在
    assert "ELEMENT DESCRIPTION TABLE" in sent
    assert "WRITE these" in sent
    # 返回解析
    assert card.meta["task_slug"] == "upload-video"
    assert card.meta["task_keywords"] == ["上传", "视频", "发布"]
    assert card.meta["task_description"] == "上传并发布视频"


def test_task_prompt_empty_existing_cards_placeholder():
    """无现有任务卡时清单段显示占位。"""
    captured: list[str] = []

    def fake_call_llm(prompt, **kwargs):
        captured.append(prompt)
        return (json.dumps(_FAKE_TASK_RESPONSE), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        distiller.distill_task("x.com", [_make_bucket()], task_description="t", use_llm=True)

    assert "(none — first task card for this host)" in captured[0]


def test_task_slug_reuse_when_llm_returns_existing_slug():
    """LLM 判定同任务返回旧 slug → 复用（调用方据此覆盖，不新增）。"""

    def fake_call_llm(prompt, **kwargs):
        resp = dict(_FAKE_TASK_RESPONSE)
        resp["task_slug"] = "old-task"  # 复用已有 slug
        return (json.dumps(resp), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_task(
            "x.com",
            [_make_bucket()],
            task_description="传视频",  # 措辞与旧任务不同，但 LLM 判同任务
            existing_tasks=[{"slug": "old-task", "task_description": "上传发布视频"}],
            use_llm=True,
        )
    assert card.meta["task_slug"] == "old-task"


def test_task_slug_sanitize_and_fallback():
    """slug 清洗：非法字符转 -；LLM 未给/给垃圾 → 回退 fallback_slug。"""
    assert distiller._sanitize_slug("Upload Video!") == "upload-video"
    assert distiller._sanitize_slug("上传视频！") == ""  # 非 kebab 字符全清
    assert distiller._sanitize_slug("上传视频！", fallback="trace123") == "trace123"

    def fake_call_llm(prompt, **kwargs):
        resp = dict(_FAKE_TASK_RESPONSE)
        resp["task_slug"] = "上传视频！"  # 纯中文垃圾 slug → 清洗后空 → 回退
        return (json.dumps(resp), {})

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm),
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_task(
            "x.com", [_make_bucket()], task_description="t", use_llm=True, fallback_slug="8161dae4"
        )
    assert card.meta["task_slug"] == "8161dae4"  # 回退 trace 目录名


def test_task_template_mode_slug_fallback():
    """模板模式：不调 LLM，slug 用 fallback，keywords 空，描述照存。"""
    with patch("harness.distiller.call_llm") as mock_call:
        card = distiller.distill_task(
            "x.com",
            [_make_bucket()],
            task_description="上传视频",
            use_llm=False,
            fallback_slug="cap001",
        )
    mock_call.assert_not_called()
    assert card.meta["task_slug"] == "cap001"
    assert card.meta["task_keywords"] == []
    assert card.meta["task_description"] == "上传视频"


def test_task_parse_failure_retries_then_succeeds():
    """任务级同样有解析重试（localhost 事故修复）：第 1 次畸形、第 2 次正常 → 成功。"""
    responses = iter(
        [
            ('{ 畸形 "未转义', {}),
            (json.dumps(_FAKE_TASK_RESPONSE), {}),
        ]
    )

    def fake_call_llm(prompt, **kwargs):
        return next(responses)

    with (
        patch("harness.distiller.call_llm", side_effect=fake_call_llm) as mock_call,
        patch("harness.distiller.config.LLM_KEY", "fake"),
    ):
        card = distiller.distill_task(
            "x.com", [_make_bucket()], task_description="上传视频", use_llm=True
        )

    assert mock_call.call_count == 2
    assert card.meta["task_slug"] == "upload-video"  # 重试成功，非模板
    assert card.meta["model"] != "(template)"


# ---------------------------------------------------------------------------
# write_task_card / list_task_cards：落盘 schema + 覆盖合并
# ---------------------------------------------------------------------------


def test_write_task_card_schema(tmp_path):
    """任务卡落盘：三件套 + _task.json（slug/描述/keywords/来源）。"""
    d = write_task_card(
        tmp_path,
        "x.com",
        "upload-video",
        _task_card(),
        {"task_description": "上传视频", "task_keywords": ["上传"], "distilled_at": "2026-08-28"},
        ["data/captures/a/trace.json"],
    )
    assert d == task_card_dir(tmp_path, "x.com", "upload-video")
    for fname in ("_sop.md", "selectors.md", "quirks.md"):
        assert (d / fname).is_file()
    meta = json.loads((d / "_task.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "upload-video"
    assert meta["task_description"] == "上传视频"
    assert meta["task_keywords"] == ["上传"]
    assert meta["source_traces"] == ["data/captures/a/trace.json"]


def test_write_task_card_overwrite_unions_source_traces(tmp_path):
    """同 slug 覆盖 = 同任务重蒸：source_traces 并集追加（历次录制可追溯）。"""
    write_task_card(
        tmp_path,
        "x.com",
        "upload-video",
        _task_card(),
        {"task_description": "d", "task_keywords": [], "distilled_at": "t1"},
        ["a.json"],
    )
    write_task_card(
        tmp_path,
        "x.com",
        "upload-video",
        _task_card(),
        {"task_description": "d", "task_keywords": [], "distilled_at": "t2"},
        ["a.json", "b.json"],  # 不同时间段的重录
    )
    meta = json.loads(
        (task_card_dir(tmp_path, "x.com", "upload-video") / "_task.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["source_traces"] == ["a.json", "b.json"]
    assert meta["distilled_at"] == "t2"  # 刷新
    # 不新增第二张卡
    tasks = list_task_cards(tmp_path, "x.com")
    assert len(tasks) == 1


def test_list_task_cards_skips_corrupted(tmp_path):
    """损坏的 _task.json 跳过；目录不存在返空。"""
    write_task_card(
        tmp_path,
        "x.com",
        "good-task",
        _task_card(),
        {"task_description": "好任务", "task_keywords": [], "distilled_at": "t"},
        [],
    )
    bad_dir = task_card_dir(tmp_path, "x.com", "bad-task")
    bad_dir.mkdir(parents=True)
    (bad_dir / "_task.json").write_text("{broken", encoding="utf-8")

    tasks = list_task_cards(tmp_path, "x.com")
    assert tasks == [{"slug": "good-task", "task_description": "好任务"}]
    assert list_task_cards(tmp_path, "nohost.com") == []


# ---------------------------------------------------------------------------
# 管线双跳（--no-llm 模板模式走通：host 三件套 + 任务卡同时产出）
# ---------------------------------------------------------------------------


def test_pipeline_dual_output_no_llm(bilibili_trace_payload, tmp_path):
    """--no-llm 管线：host 三件套 + tasks/<slug>/ 任务卡同时落盘。"""
    from server.distill_api import run_distill_pipeline

    trace_path = tmp_path / "cap001" / "trace.json"
    trace_path.parent.mkdir()
    trace_path.write_text(json.dumps(bilibili_trace_payload, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "skills"
    result = run_distill_pipeline(
        trace_paths=trace_path, output_dir=out, adapter_name="treewalker", no_llm=True
    )
    assert result.ok is True
    # 跳 A：host 三件套
    host = out / "domain-skills" / "bilibili.com"  # bucketer 用 eTLD+1 注册域归并
    assert (host / "_sop.md").is_file()
    # 跳 B：任务卡（模板模式 slug 回退 trace 目录名）
    assert result.task_slug == "cap001"
    assert result.task_dir is not None and result.task_dir.is_dir()
    assert (result.task_dir / "_task.json").is_file()
    meta = json.loads((result.task_dir / "_task.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "cap001"
    # 描述优先级：无 --task → 回退 trace 的 task_instruction
    assert meta["task_description"] == "在 B 站投稿上传一个视频"


def test_pipeline_task_description_priority(bilibili_trace_payload, tmp_path):
    """--task 显式参数 > trace 自带 task_instruction。"""
    from server.distill_api import run_distill_pipeline

    trace_path = tmp_path / "cap002" / "trace.json"
    trace_path.parent.mkdir()
    trace_path.write_text(json.dumps(bilibili_trace_payload, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "skills"
    result = run_distill_pipeline(
        trace_paths=trace_path,
        output_dir=out,
        adapter_name="treewalker",
        no_llm=True,
        task_description="上传 B 站视频（显式）",
    )
    assert result.ok is True
    meta = json.loads((result.task_dir / "_task.json").read_text(encoding="utf-8"))
    assert meta["task_description"] == "上传 B 站视频（显式）"


def test_pipeline_template_mode_skips_registry(bilibili_trace_payload, tmp_path):
    """模板模式不累积（决策 4）：不读不写 registry（换 --fresh 真模式才增量）。"""
    from server.distill_api import run_distill_pipeline

    trace_path = tmp_path / "cap003" / "trace.json"
    trace_path.parent.mkdir()
    trace_path.write_text(json.dumps(bilibili_trace_payload, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "skills"
    result = run_distill_pipeline(
        trace_paths=trace_path, output_dir=out, adapter_name="treewalker", no_llm=True
    )
    assert result.ok is True
    assert not (out / "registry").exists()  # 模板模式不落 registry
