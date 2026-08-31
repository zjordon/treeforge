"""SkillCard 按 host 持久化存储（registry，P4 重建）。

【职责演进】原 P0 是检索层占位（registry.json + query_top_k / synthesize_playbook，
MCP stdio 检索用）——检索层已明确不做，本模块重建为 **host 级累积蒸馏的卡片持久化**：

  - ``load_card(output_dir, host)``：读旧卡（增量蒸馏的 ``prev_card`` 来源）
  - ``save_card(output_dir, card, trace_sources)``：原子写卡片，来源与已有并集去重追加
  - ``list_hosts(output_dir)``：列已持久化 host（mtime 新→旧）

【存放位置】``output_dir/registry/<host>.json``——跟着 skills 产物走（不用
``config.STATE_DIR``：那边与产物分离，换 output_dir 会失联）。单文件 JSON +
``install.atomic_write_text``（tmp + os.replace，Windows 安全）；单用户工具无并发问题。

【容错】文件缺失 / JSON 损坏 / 非 dict 一律返 None（不阻断蒸馏——增量是增强不是依赖）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .install import atomic_write_text
from .models import SkillCard

logger = logging.getLogger(__name__)

_REGISTRY_DIRNAME = "registry"


def card_path(output_dir: Path | str, host: str) -> Path:
    """host 卡片文件路径：``output_dir/registry/<host>.json``。"""
    return Path(output_dir) / _REGISTRY_DIRNAME / f"{host}.json"


def load_card(output_dir: Path | str, host: str) -> dict[str, Any] | None:
    """读 host 卡片；文件缺失 / JSON 损坏 / 非 dict 返 None（容错，不抛）。"""
    p = card_path(output_dir, host)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:  # noqa: BLE001 - 损坏降级为无旧卡
        logger.warning("registry card unreadable (%s): %s", p, e)
        return None
    return data if isinstance(data, dict) else None


def save_card(
    output_dir: Path | str, card: SkillCard, trace_sources: list[str] | None = None
) -> Path:
    """原子写 host 卡片；``trace_sources`` 与已有并集去重追加（历次录制来源可追溯）。"""
    p = card_path(output_dir, card.domain)
    prev = load_card(output_dir, card.domain) or {}
    sources = list(dict.fromkeys([*(prev.get("trace_sources") or []), *(trace_sources or [])]))
    data: dict[str, Any] = {
        "host": card.domain,
        "skill_name": card.skill_name,
        "scope": card.scope,
        "sop_md": card.sop_md,
        "selectors_md": card.selectors_md,
        "quirks_md": card.quirks_md,
        "meta": dict(card.meta or {}),
        "trace_sources": sources,
    }
    atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
    return p


def list_hosts(output_dir: Path | str) -> list[str]:
    """列已持久化的 host（按卡片 mtime 新→旧）。"""
    d = Path(output_dir) / _REGISTRY_DIRNAME
    if not d.is_dir():
        return []
    items = [(f.stat().st_mtime, f.stem) for f in d.glob("*.json") if f.is_file()]
    items.sort(reverse=True)
    return [h for _, h in items]
