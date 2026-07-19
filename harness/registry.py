"""检索层（registry）。

【本期 P0】空实现——init-plan §二明确 MCP stdio 检索是 P4（TreeWalker 用文件注入不需要）。
模块保留接口占位，后续 P1+ 可在此实现：

  - query_top_k(task, k=5)：LLM-as-ranker 语义召回（无 embedding）
  - synthesize_playbook(task, top_k)：LLM 编排多 skill playbook
  - load_registry() / save_registry()：registry.json 持久化

对齐 Browser-BC 哲学：**LLM-as-ranker, no embeddings**——把整个 catalog 喂给 LLM 打分，
不引入向量检索（源码里 embedding/vector/similarity 三个词不出现）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_registry(state_dir: Path | None = None) -> list[dict[str, Any]]:
    """加载 registry.json。P0 返回空列表（未实现持久化）。"""
    return []


def save_registry(entries: list[dict[str, Any]], state_dir: Path | None = None) -> None:
    """保存 registry.json。P0 空实现。"""
    # P1+：tmp + os.replace 原子写（Windows 用 os.replace 不用 Path.rename）
    raise NotImplementedError("registry 持久化是 P1+ 范围（init-plan §二）")


def query_top_k(task: str, k: int = 5) -> list[dict[str, Any]]:
    """LLM-as-ranker 语义召回。P4 范围。"""
    raise NotImplementedError("registry.query_top_k 是 P4 范围（init-plan §二）")


def synthesize_playbook(task: str, top_k: int = 6) -> str:
    """LLM 编排多 skill playbook。P4 范围。"""
    raise NotImplementedError("registry.synthesize_playbook 是 P4 范围（init-plan §二）")
