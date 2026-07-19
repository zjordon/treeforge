"""Pydantic 数据模型。

字段名/类型对齐 Browser-BC 的 dataclass（详见知识库 browserbc-distill-pipeline.md），
但用 Pydantic v2 实现。TreeForge 的关键分叉：``SkillCard`` 不存 skill_md/trace_guide_md，
而是四个站点特定字段（sop_md/selectors_md/quirks_md/api.md）匹配多文件输出 spec。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---- Stage ① ADAPT 产物 ----------------------------------------------------


class TraceEvent(BaseModel):
    """原始 trace 中的一个事件（最小格式，init-plan §7.6）。

    Browser-BC 的 NormalizedEvent 更丰富（target_tag/id/text/xpath/key/coords），
    本期 P0 用最小子集即可验证链路；ADAPT 层负责把更丰富的输入格式规整到这里。
    """

    type: str = Field(description="事件类型：navigate/click/input/change/submit/scroll/keydown 等")
    target: str | None = Field(default=None, description="元素的人类可读标签/文本")
    selector: str | None = Field(default=None, description="CSS/XPath 选择器")
    url: str | None = Field(default=None, description="事件发生时的页面 URL")
    value: str | None = Field(default=None, description="input/change 的值（已脱敏）")
    key: str | None = Field(default=None, description="keydown/keyup 的键名")
    timestamp: int = Field(default=0, description="毫秒时间戳")


class Trace(BaseModel):
    """一份示教 trace（对应 Browser-BC 的 NormalizedTrack）。

    最小格式：host + events[]。task_instruction 可选——若 trace 文件没有，
    ADAPT 层会从文件名或事件序列里推断一个占位。
    """

    host: str = Field(description="主域名，如 bilibili.com（用于 domain-skills/<host>/ 落盘）")
    events: list[TraceEvent] = Field(default_factory=list)
    task_instruction: str | None = Field(
        default=None, description="本次示教的任务描述（可空，蒸馏时若空会推断）"
    )
    track_id: str | None = Field(default=None, description="trace 唯一 id（缺省时 ADAPT 生成）")


# ---- Stage ② ATOMIZE 产物 --------------------------------------------------


class Segment(BaseModel):
    """原子能力单元（对应 Browser-BC 的 Segment）。

    本期 P0 最简实现可能把整条 trace 切成一个 segment——这是允许的，P0 只验证 distill 能跑通。
    """

    segment_id: str = Field(description='格式 "{track_id}::{start_idx}::{end_idx}"')
    source_track_id: str
    domain: str = Field(description="segment 的主域名（落 domain-skills/<host>/ 的 key）")
    start_idx: int
    end_idx: int
    events: list[TraceEvent] = Field(default_factory=list)
    boundary_reason: str = Field(
        default="end_of_track",
        description="domain_change/idle_gap/path_change/submit_nav/max_size_split/end_of_track",
    )
    entry_url: str | None = None
    exit_url: str | None = None
    duration_ms: int = 0
    event_summary: str = Field(default="", description="渲染后喂给 LLM 的多行文本（不含原始 events）")


# ---- Stage ③ CLASSIFY 产物 -------------------------------------------------


class CapacityLabel(BaseModel):
    """LLM 给 segment 贴的能力标签（kebab-case verb+object）。"""

    capacity: str = Field(description="kebab-case，2-6 词，动词+宾语，如 login-with-credentials")
    description: str = ""
    entry_conditions: list[str] = Field(default_factory=list)
    exit_conditions: list[str] = Field(default_factory=list)
    outcome: str = Field(default="success", description="success/partial/unclear")
    domain_hints: list[str] = Field(default_factory=list)


# ---- Stage ④ BUCKET 产物 ---------------------------------------------------


class Bucket(BaseModel):
    """按 domain::capacity 归并后的桶，是 distill 的输入单元。

    bucket_id = "{domain}::{slug(capacity)}"
    """

    bucket_id: str
    domain: str
    canonical_capacity: str
    description: str = ""
    segment_ids: list[str] = Field(default_factory=list)
    segments: list[Segment] = Field(
        default_factory=list,
        description="运行时携带的 segment 实体（落盘时只存 segment_ids + event_summary）",
    )
    capacity_labels: list[CapacityLabel] = Field(default_factory=list)
    distill_version: int = 0
    last_distilled_at: str | None = None
    dirty: bool = True
    created_at: str | None = None
    last_segment_added_at: str | None = None


# ---- Stage ⑤ DISTILL 产物（TreeForge 关键分叉）-----------------------------


class SkillCard(BaseModel):
    """蒸馏产物——站点特定知识卡。

    【关键分叉点】Browser-BC 的 DistilledSkill 存 skill_md / trace_guide_md 两个通用 SOP 字段；
    TreeForge 存四个站点特定字段，对应 init-plan §5 的多文件输出 spec：

        sop_md        → _sop.md       骨架：这个站点常见任务流程
        selectors_md  → selectors.md  血肉：稳定 selector、AX name、元素定位
        quirks_md     → quirks.md     怪癖：隐藏等待、SPA 导航、框架行为、反爬
        api_md        → api.md        私有 API、URL 模式、隐藏端点
    """

    bucket_id: str
    domain: str
    capacity: str
    skill_name: str = ""
    scope: str = Field(default="", description="一句话用例说明")
    sop_md: str = ""
    selectors_md: str = ""
    quirks_md: str = ""
    api_md: str = ""
    meta: dict = Field(default_factory=dict)
