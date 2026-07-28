"""Browser-BC adapter（学习/对照用）。

把 SkillCard 写成标准**单 SKILL.md**——Browser-BC 的原始输出形态。

用途：对照学习。Browser-BC 明确要求 *"Abstract away site-specific selectors and IDs"*，
产通用 SOP；TreeForge 反其道而行。这个 adapter 让你能用同一份 SkillCard 同时产出两种
形态，直观对比差异。

输出路径：``<output_dir>/skills/<domain>/<capacity>/SKILL.md``
"""

from __future__ import annotations

from pathlib import Path

from harness.bucketer import slugify
from harness.install import atomic_write_text
from harness.models import SkillCard

from .base import OutputAdapter

_SKILL_TEMPLATE = """\
---
name: {skill_name}
domain: {domain}
capacity: {capacity}
scope: {scope}
distill_version: {distill_version}
---

# {skill_name}

> Scope: {scope}
> Domain: `{domain}` · Capacity: `{capacity}`
> Segments: {segment_count} · Model: {model}

## Procedure

{sop_md}

## Selectors

{selectors_md}

## Quirks

{quirks_md}
"""


class BrowserBcAdapter(OutputAdapter):
    """单 SKILL.md 输出（Browser-BC 风格，学习对照用）。"""

    name = "browserbc"

    def write_skill(self, skill: SkillCard, output_dir: Path) -> list[Path]:
        cap_slug = slugify(skill.capacity)
        skill_dir = output_dir / "skills" / skill.domain / cap_slug
        skill_dir.mkdir(parents=True, exist_ok=True)

        meta = skill.meta or {}
        content = _SKILL_TEMPLATE.format(
            skill_name=skill.skill_name or skill.capacity,
            domain=skill.domain,
            capacity=skill.capacity,
            scope=skill.scope or "(unspecified)",
            distill_version=meta.get("distill_version", 1),
            segment_count=meta.get("segment_count", "?"),
            model=meta.get("model", "?"),
            sop_md=skill.sop_md or "_(empty)_",
            selectors_md=skill.selectors_md or "_(empty)_",
            quirks_md=skill.quirks_md or "_(empty)_",
        )

        p = skill_dir / "SKILL.md"
        atomic_write_text(p, content)

        # meta.json（对齐 Browser-BC 的 4 文件产物之一）
        import json

        meta_p = skill_dir / "meta.json"
        atomic_write_text(meta_p, json.dumps(meta, indent=2, ensure_ascii=False))

        return [p, meta_p]
