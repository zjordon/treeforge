"""Stage ④ BUCKET：按 ``domain::capacity`` 归并 segment。

``bucket_id = "{domain}::{slug(capacity)}"``

逻辑：
  - 同 bucket_id 的 segment 追加进 segment_ids（去重），标记 dirty=True
  - 新 bucket_id 创建新桶
  - segment 实体也带在 Bucket.segments 里，方便 distiller 直接用（P0 不落盘）

【本期 P0】不做 consolidate（Browser-BC 的桶合并子命令）——单 trace 通常只产生 1-N 个桶，
合并价值不大，P1+ 再加。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from . import progress
from .models import Bucket, CapacityLabel, Segment


def slugify(name: str) -> str:
    """capacity 名 → slug：lower / 空格下划线转连字符 / 去非 [a-z0-9-]。"""
    s = (name or "").lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "capacity"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def bucket(
    classified: list[tuple[Segment, CapacityLabel]],
) -> list[Bucket]:
    """把 [(segment, label), ...] 归并成 Bucket[]。"""
    progress.report("BUCKET", total=len(classified))
    buckets: dict[str, Bucket] = {}
    label_map: dict[str, CapacityLabel] = {}

    for seg, label in classified:
        cap_slug = slugify(label.capacity)
        bid = f"{seg.domain}::{cap_slug}"
        if bid in buckets:
            b = buckets[bid]
            if seg.segment_id not in b.segment_ids:
                b.segment_ids.append(seg.segment_id)
                b.segments.append(seg)
                b.dirty = True
                b.last_segment_added_at = _now_iso()
            label_map.setdefault(bid, label)
        else:
            buckets[bid] = Bucket(
                bucket_id=bid,
                domain=seg.domain,
                canonical_capacity=label.capacity,
                description=label.description,
                segment_ids=[seg.segment_id],
                segments=[seg],
                capacity_labels=[label],
                dirty=True,
                created_at=_now_iso(),
                last_segment_added_at=_now_iso(),
            )
            label_map[bid] = label

    # 把 label 的 entry/exit conditions 收进桶里（distiller 可能用）
    for bid, label in label_map.items():
        if label not in buckets[bid].capacity_labels:
            buckets[bid].capacity_labels.append(label)

    result = list(buckets.values())
    progress.report(
        "BUCKET", current=len(result), total=len(result), detail=f"→ {len(result)} buckets"
    )
    return result
