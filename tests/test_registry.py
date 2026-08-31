"""registry（SkillCard 按 host 持久化）测试——P4 S1。

测试原则：纯文件系统操作（tmp_path），不连 LLM / 网络。
"""

from __future__ import annotations

import json

from harness.models import SkillCard
from harness.registry import card_path, list_hosts, load_card, save_card


def _card(host: str = "x.com", version: int = 1, sop: str = "# SOP v1") -> SkillCard:
    return SkillCard(
        bucket_id=f"{host}::test",
        domain=host,
        capacity="test",
        skill_name="Test",
        scope="test scope",
        sop_md=sop,
        selectors_md="# Selectors",
        quirks_md="# Quirks",
        meta={"distill_version": version, "model": "(test)"},
    )


def test_save_load_roundtrip(tmp_path):
    """save 后 load 能读回全部字段。"""
    card = _card(version=3, sop="# SOP v3")
    p = save_card(tmp_path, card, ["data/captures/a/trace.json"])

    assert p == card_path(tmp_path, "x.com")
    data = load_card(tmp_path, "x.com")
    assert data is not None
    assert data["host"] == "x.com"
    assert data["sop_md"] == "# SOP v3"
    assert data["meta"]["distill_version"] == 3
    assert data["trace_sources"] == ["data/captures/a/trace.json"]


def test_load_missing_returns_none(tmp_path):
    """文件不存在返 None（不抛）。"""
    assert load_card(tmp_path, "nope.com") is None


def test_load_corrupted_json_returns_none(tmp_path):
    """JSON 损坏返 None（容错，不阻断蒸馏）。"""
    p = card_path(tmp_path, "bad.com")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    assert load_card(tmp_path, "bad.com") is None


def test_load_non_dict_returns_none(tmp_path):
    """顶层非 dict（异常输入）返 None。"""
    p = card_path(tmp_path, "list.com")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert load_card(tmp_path, "list.com") is None


def test_save_trace_sources_union_dedupe(tmp_path):
    """多次 save 的 trace_sources 并集去重追加（保序）。"""
    save_card(tmp_path, _card(), ["a.json", "b.json"])
    save_card(tmp_path, _card(version=2), ["b.json", "c.json"])

    data = load_card(tmp_path, "x.com")
    assert data["trace_sources"] == ["a.json", "b.json", "c.json"]


def test_save_atomic_no_tmp_leftover(tmp_path):
    """原子写不残留 .tmp 文件。"""
    save_card(tmp_path, _card())
    d = card_path(tmp_path, "x.com").parent
    leftovers = [f.name for f in d.iterdir() if ".tmp." in f.name]
    assert leftovers == []


def test_list_hosts_sorted_by_mtime_desc(tmp_path):
    """list_hosts 按 mtime 新→旧。"""
    import os

    # 写两张卡，用 os.utime 显式固定 mtime（Windows 文件系统分辨率低，快速连写可能同戳）
    old_p = save_card(tmp_path, _card("old.com"))
    new_p = save_card(tmp_path, _card("new.com"))
    os.utime(old_p, (1000, 1000))
    os.utime(new_p, (2000, 2000))

    hosts = list_hosts(tmp_path)
    assert set(hosts) == {"old.com", "new.com"}
    assert hosts[0] == "new.com"  # 最新写的前面

    # 无 registry 目录返空
    assert list_hosts(tmp_path / "nonexistent") == []
