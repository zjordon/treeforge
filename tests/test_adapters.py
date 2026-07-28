"""adapters 测试。"""

from __future__ import annotations

from pathlib import Path

from adapters.browserbc_adapter import BrowserBcAdapter
from adapters.treewalker_adapter import TreeWalkerAdapter
from harness.models import SkillCard


def _make_card() -> SkillCard:
    return SkillCard(
        bucket_id="bilibili.com::upload-content",
        domain="bilibili.com",
        capacity="upload-content",
        skill_name="Upload Content",
        scope="Upload a video",
        sop_md="# SOP\n\nstep 1",
        selectors_md="# Selectors\n\n`#btn`",
        quirks_md="# Quirks\n\nwait async",
        meta={"model": "test", "segment_count": 1, "distill_version": 1},
    )


def test_treewalker_adapter_writes_three_files(tmp_path: Path):
    adapter = TreeWalkerAdapter()
    written = adapter.write_skill(_make_card(), tmp_path)

    host_dir = tmp_path / "domain-skills" / "bilibili.com"
    expected = {"_sop.md", "selectors.md", "quirks.md"}
    actual = {p.name for p in host_dir.glob("*.md")}
    assert expected.issubset(actual), f"缺文件: {expected - actual}"
    assert len(written) == 3


def test_treewalker_adapter_files_sorted_alphabetically_sop_first(tmp_path: Path):
    """验收点：_sop.md 的下划线前缀确保它字母序排第一（TreeWalker 消费侧按字母序读 ≤10 个）。"""
    adapter = TreeWalkerAdapter()
    adapter.write_skill(_make_card(), tmp_path)

    host_dir = tmp_path / "domain-skills" / "bilibili.com"
    names = sorted(p.name for p in host_dir.glob("*.md"))
    assert names[0] == "_sop.md", f"_sop.md 应排第一，实际: {names}"


def test_treewalker_adapter_empty_field_gets_placeholder(tmp_path: Path):
    adapter = TreeWalkerAdapter()
    card = _make_card()
    card.quirks_md = ""  # 模拟 LLM 没产 quirks
    adapter.write_skill(card, tmp_path)

    host_dir = tmp_path / "domain-skills" / "bilibili.com"
    quirks = (host_dir / "quirks.md").read_text(encoding="utf-8")
    assert quirks.startswith("#")  # 有 H1 头
    assert "empty" in quirks.lower() or len(quirks) > 10  # 占位非空


def test_browserbc_adapter_writes_single_skill_md(tmp_path: Path):
    adapter = BrowserBcAdapter()
    written = adapter.write_skill(_make_card(), tmp_path)

    skill_md = tmp_path / "skills" / "bilibili.com" / "upload-content" / "SKILL.md"
    meta_json = tmp_path / "skills" / "bilibili.com" / "upload-content" / "meta.json"
    assert skill_md.is_file()
    assert meta_json.is_file()
    assert len(written) == 2

    content = skill_md.read_text(encoding="utf-8")
    assert "Upload Content" in content
    assert "bilibili.com" in content


def test_install_atomic_write_overwrites_existing(tmp_path: Path):
    """验收点：os.replace 原子写——重复跑不报 WinError 183，且内容更新。"""
    adapter = TreeWalkerAdapter()
    card = _make_card()
    adapter.write_skill(card, tmp_path)
    # 第二次写，改内容
    card.sop_md = "# SOP v2\n\nupdated"
    adapter.write_skill(card, tmp_path)

    sop = (tmp_path / "domain-skills" / "bilibili.com" / "_sop.md").read_text(encoding="utf-8")
    assert "v2" in sop or "updated" in sop


# ---------------------------------------------------------------------------
# 多 bucket 合并（避免同 host 互相覆盖）
# ---------------------------------------------------------------------------


def test_treewalker_merge_multiple_buckets_same_host(tmp_path: Path):
    """验收点：同 host 多 bucket 合并成一组 3 文件，不互相覆盖。"""
    adapter = TreeWalkerAdapter()
    card1 = _make_card()  # bilibili.com / upload-content
    card1.sop_md = "# upload-content SOP\n\nstep A"
    card2 = _make_card()
    card2.capacity = "fill-checkout-form"
    card2.bucket_id = "bilibili.com::fill-checkout-form"
    card2.sop_md = "# checkout SOP\n\nstep B"

    written = adapter.write_skills_merged([card1, card2], tmp_path)

    # 只产出 3 个文件（不是 6 个）
    host_dir = tmp_path / "domain-skills" / "bilibili.com"
    files = sorted(p.name for p in host_dir.glob("*.md"))
    assert files == ["_sop.md", "quirks.md", "selectors.md"]
    assert len(written) == 3

    # 两个 bucket 的内容都在 _sop.md（按 capacity 分节）
    sop = (host_dir / "_sop.md").read_text(encoding="utf-8")
    assert "upload-content" in sop
    assert "fill-checkout-form" in sop
    assert "step A" in sop
    assert "step B" in sop


def test_treewalker_merge_single_bucket_keeps_original(tmp_path: Path):
    """单 bucket 合并时保留原 LLM 产出的标题（不强制改格式）。"""
    adapter = TreeWalkerAdapter()
    card = _make_card()
    card.sop_md = "# My Original Title\n\ncontent"
    adapter.write_skills_merged([card], tmp_path)

    sop = (tmp_path / "domain-skills" / "bilibili.com" / "_sop.md").read_text(encoding="utf-8")
    assert "My Original Title" in sop


def test_install_cards_uses_merge_when_available(tmp_path: Path):
    """install_cards 检测到 write_skills_merged 时走合并路径。"""
    from harness.install import install_cards

    adapter = TreeWalkerAdapter()
    card1 = _make_card()
    card2 = _make_card()
    card2.capacity = "another"
    card2.bucket_id = "bilibili.com::another"

    written = install_cards([card1, card2], tmp_path, adapter)

    # 合并路径：3 个文件（不是 6）
    host_dir = tmp_path / "domain-skills" / "bilibili.com"
    assert len(list(host_dir.glob("*.md"))) == 3
    assert len(written) == 3


def test_treewalker_merge_empty_cards_returns_empty(tmp_path: Path):
    """空 cards 列表不写任何文件。"""
    adapter = TreeWalkerAdapter()
    written = adapter.write_skills_merged([], tmp_path)
    assert written == []
    assert not (tmp_path / "domain-skills").exists() or not list(
        (tmp_path / "domain-skills").glob("**/*.md")
    )
