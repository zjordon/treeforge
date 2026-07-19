"""输出安装层（install）。

把 SkillCard 通过 ``adapters/`` 写到目标目录。本模块本身只负责选 adapter + 调用，
**不关心**输出形态（treewalker 多文件 vs browserbc 单文件）——那是 adapter 的事。

【Windows 关键】原子写用 ``os.replace``，不用 ``Path.rename``（WinError 183）。
详见知识库 browserbc-windows-adaptation.md「Path.rename → WinError 183」。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import progress
from .models import SkillCard


def atomic_write_text(path: Path, content: str) -> None:
    """原子写文本：tmp + os.replace。

    Windows 上 Path.rename 在目标已存在时报 WinError 183；os.replace 是跨平台原子替换。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)  # NOT path.rename / os.rename


def install_cards(cards: list[SkillCard], output_dir: Path, adapter) -> list[Path]:
    """把 SkillCard[] 通过 adapter 写到 output_dir。

    adapter 须实现 ``write_skill(card: SkillCard, output_dir: Path) -> Path | list[Path]``。
    返回所有写入的文件路径列表。
    """
    written: list[Path] = []
    progress.report("INSTALL", total=len(cards))
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, card in enumerate(cards):
        result = adapter.write_skill(card, output_dir)
        if isinstance(result, list):
            written.extend(result)
        elif isinstance(result, Path):
            written.append(result)
        progress.report("INSTALL", current=i + 1, total=len(cards), detail=card.bucket_id)
    return written
