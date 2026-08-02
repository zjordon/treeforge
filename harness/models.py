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
    selector: str | None = Field(default=None, description="CSS/XPath 选择器（老格式，双轨兼容）")
    element_attrs: dict = Field(
        default_factory=dict,
        description=(
            "白名单属性 dict（新格式，优先于 selector）："
            "id/name/type/placeholder/aria-label/role/data-testid/data-test/data-cy/"
            "contenteditable/visible_text/tag/visible。"
            "对齐 TreeWalker DOM 的 [index]<tag attr=val /> text 呈现，"
            "让 distiller 能产元素描述表（见 docs/skill-format-alignment.md）。"
        ),
    )
    url: str | None = Field(default=None, description="事件发生时的页面 URL")
    stage: str | None = Field(
        default=None,
        description=(
            "事件所属页面阶段名，指向 trace.page_context 的 key（DOM 快照阶段）。"
            "None=无对应快照（老 trace / 阶段外）；带?后缀=启发式推断（如 'upload?'）。"
            "向后兼容。详见 docs/skill-format-alignment-plan.md 阶段 4。"
        ),
    )
    value: str | None = Field(default=None, description="input/change 的值（已脱敏）")
    key: str | None = Field(default=None, description="keydown/keyup 的键名")
    timestamp: int = Field(default=0, description="毫秒时间戳")
    signals: list = Field(
        default_factory=list,
        description=(
            "副作用信号（P3.6 迁自 TreeWalker）：动作引发的 modal/dropdown 打开。"
            "每条 {type:'modal_opened'|'dropdown_opened', selector, ts}。"
            "distiller 据此写 quirks.md（如「点这个按钮会弹出 modal」）。"
            "采集层 attach 到最近事件；老 trace 无此字段（默认空 list）。"
        ),
    )


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
    page_context: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "页面阶段 → DOM 文本快照（如 {'upload-conver': '...DOM 文本...'}）。"
            "提供操作时的空间上下文，让 distiller 推出跨阶段 quirks（如标题框某阶段缺失、"
            "立即投稿是 span 不是 button）。来源：TreeWalker get_state().dom_state.element_tree_text"
            "（见 examples/debug_model_page_view.py:56）。老 trace 不带时为空 dict，"
            "distiller 跳过 DOM 段。当前阶段 DOM 来自人工导出（验证实验），"
            "P2 采集层就绪后替换成自动采集。"
        ),
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
    event_summary: str = Field(
        default="", description="渲染后喂给 LLM 的多行文本（不含原始 events）"
    )


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

    【形态演进】原四件套（sop/selectors/quirks/api）经 A/B 测试发现 api.md 在无网络采集
    时恒为「未观察到私有 API」零信息，浪费文件槽位；按 host 合并后改为三件套，删 api_md。
    详见 docs/skill-simplification-plan.md。

        sop_md        → _sop.md       骨架：连贯步骤剧本（host 级，不分 capacity）
        selectors_md  → selectors.md  附录：只收需要特征指纹的少数元素
        quirks_md     → quirks.md     怪癖：只写 DOM 看不出来的坑

    【关键分叉点】Browser-BC 的 DistilledSkill 存 skill_md / trace_guide_md 两个通用 SOP 字段；
    TreeForge 存上述三个站点特定字段，对应 init-plan §5 的多文件输出 spec。

    【capacity 字段】host 级蒸馏后 capacity 降级为 meta 索引（host 级蒸馏时存 capacity 列表
    如 "upload-video, fill-video-metadata"）。CLASSIFY 产出的 capacity 标签仍保留信息量，
    但不再作为产物组织维度。
    """

    bucket_id: str
    domain: str
    capacity: str = ""
    skill_name: str = ""
    scope: str = Field(default="", description="一句话用例说明")
    sop_md: str = ""
    selectors_md: str = ""
    quirks_md: str = ""
    meta: dict = Field(default_factory=dict)
