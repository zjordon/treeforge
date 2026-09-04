"""TreeWalker adapter（默认）。

把 SkillCard 写成 init-plan §5 的多文件结构（三件套，docs/skill-simplification-plan.md 决策 3）：

    <output_dir>/domain-skills/<host>/
    ├── _sop.md
    ├── selectors.md
    └── quirks.md

P4 起同一 host 下还有任务卡子目录（``tasks/<slug>/``，P4 双产物跳 B 的落点）——
TreeWalker 注入不受影响（按固定三件文件名读，不递归子目录）：

    <output_dir>/domain-skills/<host>/
    ├── _sop.md / selectors.md / quirks.md   # 站点级累积卡（host 卡）
    └── tasks/<slug>/                        # 任务级独立卡（含 _task.json 元数据）

【消费侧约束】TreeWalker 的 browser-harness 加载逻辑（详见知识库
skill-auto-evolution-migration.md + browser-agent/dev-plan.md）：

  - 目录按 ``hostname`` 索引（``urlparse(url).hostname``）
  - **只加载 .md 文件**，按字母序排序，**硬上限 10 个**
  - ``_sop.md`` 的下划线前缀确保它排第一、作为入口索引

所以文件名固定、数量 ≤ 3，远低于 10 上限。命名顺序很重要，不要乱起名。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.install import atomic_write_text
from harness.models import SkillCard

from .base import OutputAdapter

# 文件名 → SkillCard 字段。顺序即字母序（_sop 排第一）。
# 注意：加新文件时别超过 10 个（消费侧硬上限）。
_FILES: list[tuple[str, str]] = [
    ("_sop.md", "sop_md"),
    ("selectors.md", "selectors_md"),
    ("quirks.md", "quirks_md"),
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
        """单 card 写盘（向后兼容）。多 card 同 host 会互相覆盖——
        多 bucket 场景应优先用 write_skills_merged。
        """
        host_dir = output_dir / "domain-skills" / skill.domain
        host_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for fname, field in _FILES:
            content = getattr(skill, field, "") or ""
            # host 级蒸馏后 capacity 可能是列表字符串或空；title 用 domain 为主
            label = fname.replace("_", "").replace(".md", "").title() or "SOP"
            title = f"{skill.domain} — {label}"
            content = _ensure_header(content, title)
            p = host_dir / fname
            atomic_write_text(p, content)
            written.append(p)
        return written

    def write_skills_merged(self, cards: list[SkillCard], output_dir: Path) -> list[Path]:
        """合并同 host 的所有 card 到一组 3 文件（避免多 bucket 互相覆盖）。

        host 级蒸馏后同 host 通常只有一个 card，走 _merge_field 单 card 路径。
        保留多 card 分节作为防御逻辑（老调用方可能传多 capacity card）。

          - _sop.md / selectors.md / quirks.md 各自合并
          - 单个 host 只产出一组 3 文件（消费侧 domain-skills/<host>/*.md 约定不变）

        cards 假定同 host（调用方按 host 分组后传入）。空列表返回 []。
        """
        if not cards:
            return []
        # 按 host 分组（防御：调用方应已分组，这里再保一次）
        by_host: dict[str, list[SkillCard]] = {}
        for card in cards:
            by_host.setdefault(card.domain, []).append(card)

        written: list[Path] = []
        for host, host_cards in by_host.items():
            host_dir = output_dir / "domain-skills" / host
            host_dir.mkdir(parents=True, exist_ok=True)
            for fname, field in _FILES:
                merged = _merge_field(host_cards, field, host)
                p = host_dir / fname
                atomic_write_text(p, merged)
                written.append(p)
        return written


def _merge_field(cards: list[SkillCard], field: str, host: str) -> str:
    """合并多个 card 的同一字段为一个 markdown 文件内容。

    单 card：直接用其内容（加 H1）。
    多 card：H1 是 host 总标题，每个 card 一个 `## <capacity>` 二级标题分节（防御逻辑）。
    """
    # 过滤掉空内容的 card
    parts = [(c.capacity, getattr(c, field, "") or "") for c in cards]
    parts = [(cap, md) for cap, md in parts if md.strip()]

    if not parts:
        title = f"{host} — {field.replace('_md', '').title()}"
        return f"# {title}\n\n_(empty — no evidence for this dimension.)_\n"

    # 单 card 且内容已有 H1：直接返回（保持原 LLM 产出的标题）
    if len(parts) == 1:
        _, md = parts[0]
        if md.lstrip().startswith("#"):
            return md
        return f"# {host}\n\n{md}"

    # 多 card：H1 总标题 + 每个 card 一个 ## capacity 分节
    field_label = field.replace("_md", "").title() or "Skill"
    out = [f"# {field_label} — {host} ({len(parts)} capacities)\n"]
    for capacity, md in parts:
        # 去掉 card 自带的 H1（避免标题层级混乱），保留其余
        body_lines = md.splitlines()
        while body_lines and body_lines[0].lstrip().startswith("#"):
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()
        out.append(f"\n## {capacity}\n\n{body}\n")
    return "".join(out)


# ---------------------------------------------------------------------------
# 任务卡（P4 双产物跳 B）：domain-skills/<host>/tasks/<slug>/
# 模块函数（非 adapter 方法）——任务卡不走 INSTALL 合并路径，管线直接调。
# ---------------------------------------------------------------------------


def task_card_dir(output_dir: Path | str, host: str, slug: str) -> Path:
    """任务卡目录：``output_dir/domain-skills/<host>/tasks/<slug>``。"""
    return Path(output_dir) / "domain-skills" / host / "tasks" / slug


def write_task_card(
    output_dir: Path | str,
    host: str,
    slug: str,
    card: SkillCard,
    task_meta: dict[str, Any],
    source_traces: list[str] | None = None,
) -> Path:
    """写任务卡：三件套 + ``_task.json`` 元数据。

    同 slug 已存在 → 覆盖（同任务重蒸的预期行为）；``source_traces`` 与旧卡
    ``_task.json`` **并集追加**（历次录制来源可追溯）。
    """
    d = task_card_dir(output_dir, host, slug)
    d.mkdir(parents=True, exist_ok=True)
    for fname, field in _FILES:
        content = getattr(card, field, "") or ""
        label = fname.replace("_", "").replace(".md", "").title() or "SOP"
        title = f"{host} · {slug} — {label}"
        atomic_write_text(d / fname, _ensure_header(content, title))

    # 覆盖时与旧 _task.json 并集 source_traces
    old: dict[str, Any] = {}
    meta_path = d / "_task.json"
    try:
        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        old = loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        old = {}
    sources = list(dict.fromkeys([*(old.get("source_traces") or []), *(source_traces or [])]))

    meta = {
        "slug": slug,
        "task_description": task_meta.get("task_description", ""),
        "task_keywords": task_meta.get("task_keywords", []),
        "source_traces": sources,
        "distilled_at": task_meta.get("distilled_at", ""),
    }
    atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    return d


def list_task_cards(output_dir: Path | str, host: str) -> list[dict[str, Any]]:
    """列 host 的现有任务卡（slug + task_description），供 slug 稳定化参照。

    损坏的 ``_task.json`` 跳过（容错）。返回按 slug 字母序。
    """
    tasks_root = Path(output_dir) / "domain-skills" / host / "tasks"
    if not tasks_root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for meta_path in sorted(tasks_root.glob("*/_task.json")):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("slug"):
            out.append(
                {
                    "slug": str(data["slug"]),
                    "task_description": str(data.get("task_description") or ""),
                }
            )
    return out
