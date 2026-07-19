"""adapter 抽象接口。

init-plan §7.5：``OutputAdapter`` 抽象 ``write_skill(skill: SkillCard, output_dir: Path) -> None``。
这里把返回值改为 ``list[Path]`` 以便上层汇报写入的文件（不破坏契约，只是更信息丰富）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from harness.models import SkillCard


class OutputAdapter(ABC):
    """把 SkillCard 写成目标目录下的文件。"""

    name: str = "abstract"

    @abstractmethod
    def write_skill(self, skill: SkillCard, output_dir: Path) -> list[Path]:
        """写一个 SkillCard。返回写入的文件路径列表。"""
        raise NotImplementedError
