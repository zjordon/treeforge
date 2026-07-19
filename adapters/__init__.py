"""输出 adapter 注册表。

关键缓冲设计（init-plan §四/§八）：treewalker_adapter 和 browserbc_adapter 必须分离，
对应不同的输出形态——同一份 SkillCard 可产两种格式。
"""

from __future__ import annotations

from .base import OutputAdapter
from .browserbc_adapter import BrowserBcAdapter
from .treewalker_adapter import TreeWalkerAdapter

_REGISTRY: dict[str, type[OutputAdapter]] = {
    "treewalker": TreeWalkerAdapter,
    "browserbc": BrowserBcAdapter,
}


def get_adapter(name: str) -> OutputAdapter:
    """按名字取 adapter 实例。未知名字回退到 treewalker（默认）。"""
    cls = _REGISTRY.get(name, TreeWalkerAdapter)
    return cls()


def available() -> list[str]:
    return list(_REGISTRY.keys())
