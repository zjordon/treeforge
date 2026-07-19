"""TreeWalker adapter（默认）。

把 SkillCard 写成 init-plan §5 的多文件结构：

    <output_dir>/domain-skills/<host>/
    ├── _sop.md
    ├── selectors.md
    ├── quirks.md
    └── api.md

【消费侧约束】TreeWalker 的 browser-harness 加载逻辑（详见知识库
skill-auto-evolution-migration.md + browser-agent/dev-plan.md）：

  - 目录按 ``hostname`` 索引（``urlparse(url).hostname``）
  - **只加载 .md 文件**，按字母序排序，**硬上限 10 个**
  - ``_sop.md`` 的下划线前缀确保它排第一、作为入口索引

所以文件名固定、数量 ≤ 4，远低于 10 上限。命名顺序很重要，不要乱起名。
"""

from __future__ import annotations

from pathlib import Path

from harness.install import atomic_write_text
from harness.models import SkillCard

from .base import OutputAdapter

# 文件名 → SkillCard 字段。顺序即字母序（_sop 排第一）。
# 注意：加新文件时别超过 10 个（消费侧硬上限）。
_FILES: list[tuple[str, str]] = [
    ("_sop.md", "sop_md"),
    ("selectors.md", "selectors_md"),
    ("quirks.md", "quirks_md"),
    ("api.md", "api_md"),
]


def _ensure_header(md: str, title: str) -> str:
    """保证每个文件有 H1 头（消费侧按文件读，有 H1 更友好）。"""
    if not md.strip():
        return f"# {title}\n\n_(empty — no evidence for this dimension.)_\n"
    if md.lstrip().startswith("#"):
        return md
    return f"# {title}\n\n{md}"


class TreeWalkerAdapter(OutputAdapter):
    """落到 ``<output_dir>/domain-skills/<host>/{_sop,selectors,quirks,api}.md``。"""

    name = "treewalker"

    def write_skill(self, skill: SkillCard, output_dir: Path) -> list[Path]:
        host_dir = output_dir / "domain-skills" / skill.domain
        host_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for fname, field in _FILES:
            content = getattr(skill, field, "") or ""
            title = f"{skill.domain} / {skill.capacity} — {fname.replace('_', '').replace('.md', '').title() or 'SOP'}"
            content = _ensure_header(content, title)
            p = host_dir / fname
            atomic_write_text(p, content)
            written.append(p)
        return written
