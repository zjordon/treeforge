"""distill 场景的 payload schema——collector 和扩展的共同契约。

【为什么单独成文件】
collector（Python）和扩展（TS）是双端，必须共享同一份 payload schema 定义。
本文件是 Python 侧的权威定义；扩展侧（treeforge/extension/shared/distill-schema.ts）
应与之严格对齐（字段名、类型、语义）。

【设计依据】
- treeforge 蒸馏需求：TraceEvent.element_attrs 的 11 白名单 + visible_text + tag + accept
  （见 harness/atomizer._ATTR_WHITELIST + 实际 trace 字段分布）
- rerun_to_trace._extract_element_attrs 的转换逻辑（从 interacted_element 提取 element_attrs）
- 扩展采集能力：DOM 事件 + 元素属性（见 TreeWalker recording_extension/capture/selector.ts）

【核心设计决策：扩展采原始属性，collector 提炼】
扩展采「DOM 原始属性 raw_attrs」，collector 负责过滤成白名单 element_attrs。
理由：
  1. 白名单是蒸馏业务逻辑，不该焊进扩展（扩展要通用，未来 replay 场景字段需求不同）
  2. 白名单可能演进（如未来加 data-qa），改 collector 一处即可，不用改扩展重新发版
  3. 扩展采原始属性更简单（不用判断哪些属性要），且能给 collector 更多上下文做决策
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# ---------------------------------------------------------------------------
# 扩展采集的原始元素属性（raw_attrs）
# ---------------------------------------------------------------------------
# 扩展 content script 从 DOM 元素读的属性。这是「采集层产出」，collector 据此提炼 element_attrs。
# 字段名用 DOM 原生命名（如 aria-label 而非 ariaLabel），便于扩展直接 el.getAttribute()。
#
# 注意：这里列的是「扩展应采集的」，不是「collector 最终保留的」。collector 会按
# _ATTR_WHITELIST 过滤成 element_attrs（见 harness/atomizer.py:130）。

RAW_ATTR_KEYS: tuple[str, ...] = (
    # 通用身份
    "tag",  # tagName.toLowerCase()
    "id",
    "name",
    # 类型/状态
    "type",  # input 的 type（text/file/checkbox/radio...）
    "role",
    "aria-label",
    "ariaLabel",  # 兼容：部分场景 getAttribute('aria-label') 取不到，用可访问名计算
    "aria-labelledby",  # contenteditable 常用：关联标题元素（如 bilibili 简介 <h3>）
    # 表单提示
    "placeholder",
    # 测试标识（蒸馏「稳定标识」关键来源）
    "data-testid",
    "data-test",
    "data-cy",
    # 富文本
    "contenteditable",
    # 文件上传（file input 专属，对 quirks 很关键）
    "accept",
    # P3.6：MAIN-world addEventListener hook 给注册了点击监听器的元素打的标记
    # （data-tw-jsclick="1"）。让 distiller 能识别「无可交互特征但 JS 监听了点击」的 div。
    # 对齐 TreeWalker has_js_click_listener；详见 injected.ts。
    "data-tw-jsclick",
    # 可见文本（扩展用 textContent/innerText 计算，已清洗字体图标 \ue000-\uf8ff）
    # 作为独立字段而非属性，因为它是「计算的」不是 getAttribute 的
)


# ---------------------------------------------------------------------------
# distill 事件 payload（扩展发出，collector 消费）
# ---------------------------------------------------------------------------

# 动作类型（扩展按 DOM 事件映射，collector 再转成 TraceEvent.type）
# 注意：这里用「通用浏览器动作」，不是 TreeWalker action 注册表的镜像。
# collector 转成 TraceEvent.type（见 ACTION_TYPE_MAP）。
DistillActionType = Literal[
    "click",  # click 事件
    "input",  # input 事件（文本输入 / contenteditable 富文本）
    "change",  # change 事件（select/checkbox/file）—— 老通用槽位，P3.6 后大多由更具体的类型承担
    "keydown",  # keydown 事件（Enter/快捷键）—— 老通用槽位
    "scroll",  # wheel 事件（已去抖合并）
    "navigate",  # SPA 导航（pushState/popstate）或整页跳转
    # P3.6 扩词（迁移自 TreeWalker 扩展，作 distill 采集补充）：
    "select_dropdown",  # <select> 的 change → 选中项 value
    "upload_file",  # <input type=file> 的 change → 文件名 + upload_ctx 语义身份
    "send_keys",  # 修饰键组合（Ctrl+S）+ 命名非打印键（Tab/Escape/方向键/F1-12）
]


class UploadCtx(TypedDict, total=False):
    """upload_file 的站点无关通用身份线索（迁移自 TreeWalker issue #139 通用化）。

    让 distiller 在 quirks.md 里描述「这个上传框是什么」，而不是依赖站点特定 selector。
    重放端不再用——distill 只要「LLM 可读」，不参与五级匹配。
    """

    label_text: str  # 原生 <label for> 关联文本（input.labels）
    aria_text: str  # aria-labelledby IDREF 解析的目标文本
    region_text: str  # 就近可见文本祖先（≤5 层）
    in_dialog: bool  # 在 ARIA dialog 内（[role=dialog] / [aria-modal=true]）


class DistillEventPayload(TypedDict, total=False):
    """distill 场景下，扩展 POST /ingest 的 payload 内每个事件的 schema。

    外层是 CaptureEnvelope { scenario:'distill', payload: DistillEventPayload }。
    collector 收到后转成 TraceEvent（见 collector.event_to_trace_event）。
    """

    # 动作类型（必填）
    type: DistillActionType

    # 操作目标元素的原始属性（collector 过滤成 element_attrs）
    # 扩展用 RAW_ATTR_KEYS 列的字段采集。可能为空（如 navigate 无目标元素）。
    raw_attrs: dict[str, str]

    # 输入值（input/select_dropdown 用）：用户输入的文本或选择的值
    # upload_file 用：文件名（浏览器安全限制只给文件名）
    value: str

    # 按键（keydown/send_keys 用）：键名，如 "Enter" "Escape" "Control+S"
    key: str

    # 滚动量（scroll 用）：归一化后的滚动量
    scroll_amount: int

    # 导航目标 URL（navigate 用）
    url: str

    # 目标元素的可读标签（扩展从 DOM 算的人类可读描述，作为 target 字段）
    # 如 "投稿按钮" "标题输入框"。可选，扩展尽力采。
    target: str

    # upload_file 的站点无关身份线索（P3.6 迁自 TreeWalker）。distiller 据此写 quirks。
    upload_ctx: UploadCtx

    # 时间戳（毫秒，扩展填）
    ts: int


# ---------------------------------------------------------------------------
# 副作用信号（P3.6 迁自 TreeWalker side-effect-observer，POST /signal 单独通道）
# ---------------------------------------------------------------------------

# 信号类型：modal/dropdown 打开（动作引发的 DOM 变化，作为 quirks.md 原料）。
# 与 TreeWalker SignalKind 对齐子集（navigation/dialog/download 是 replay 专用，distill 不采）。
SignalKind = Literal["modal_opened", "dropdown_opened"]


class DistillSignal(TypedDict, total=False):
    """副作用信号（POST /signal 的 body 内层 payload）。

    扩展 side-effect-observer 在每动作后 1s 窗口检测 modal/dropdown 新增节点，
    通过 /signal 通道发到 collector，attach 到最近 capture event 作为 quirks 原料。
    """

    type: SignalKind  # modal_opened / dropdown_opened
    selector: str  # 新增节点的简易选择器（tag + #id + 前两 class），作 detail 不参与定位
    ts: int  # 毫秒时间戳（扩展填）


# ---------------------------------------------------------------------------
# collector 转换：DistillEventPayload → TraceEvent
# ---------------------------------------------------------------------------

# 扩展动作类型 → treeforge TraceEvent.type 的映射
# （TraceEvent.type 见 harness/models.py:22，蒸馏用）
# 新增 P3.6 类型原样透传（TraceEvent.type 是自由字符串，下游 ADAPT/atomizer 不按枚举过滤）
ACTION_TYPE_MAP: dict[str, str] = {
    "click": "click",
    "input": "input",
    "change": "change",
    "keydown": "keydown",
    "scroll": "scroll",
    "navigate": "navigate",
    # P3.6 扩词（迁自 TreeWalker）：原样透传成 TraceEvent.type
    "select_dropdown": "select_dropdown",
    "upload_file": "upload_file",
    "send_keys": "send_keys",
}

# collector 提炼 element_attrs 时保留的白名单（对齐 harness/atomizer._ATTR_WHITELIST + accept）
# 注意：tag 和 visible_text 单独处理（tag 总是保留，visible_text 从 raw_attrs.visible_text 取）
ELEMENT_ATTR_WHITELIST: tuple[str, ...] = (
    "id",
    "name",
    "type",
    "placeholder",
    "aria-label",
    "aria-labelledby",
    "role",
    "data-testid",
    "data-test",
    "data-cy",
    "contenteditable",
    # accept 不在 atomizer 白名单，但 rerun_to_trace 保留了对 file input quirks 关键
    "accept",
    # P3.6：JS 点击标记（MAIN-world addEventListener hook 打的），见 RAW_ATTR_KEYS 注释
    "data-tw-jsclick",
)

# 私有 Unicode 区（字体图标），清洗 visible_text 用（对齐 rerun_to_trace._clean_ax_name）
_PRIVATE_UNICODE_RANGES = (
    (0xE000, 0xF8FF),  # BMP Private Use Area（字体图标常用）
    (0xF0000, 0xFFFFD),  # Supplementary PUA-A
    (0x100000, 0x10FFFD),  # Supplementary PUA-B
)


def clean_visible_text(text: str | None) -> str:
    """清洗可见文本：去除私有 Unicode 区字符（字体图标）+ 压缩空白。"""
    if not text:
        return ""
    cleaned = []
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _PRIVATE_UNICODE_RANGES):
            continue
        cleaned.append(ch)
    result = "".join(cleaned).strip()
    # 压缩连续空白
    return " ".join(result.split())


def extract_element_attrs(raw_attrs: dict[str, Any] | None) -> dict[str, Any]:
    """从扩展采集的 raw_attrs 提炼 element_attrs（对齐 TraceEvent.element_attrs）。

    提炼规则（对齐 rerun_to_trace._extract_element_attrs）：
    1. tag 总是保留（lowercase）
    2. 白名单属性过滤保留
    3. visible_text 清洗后保留
    4. aria-label 兜底用 ariaLabel（部分 DOM 取不到 aria-label 用计算可访问名）
    """
    if not raw_attrs:
        return {}

    result: dict[str, Any] = {}

    # tag
    tag = (raw_attrs.get("tag") or "").lower()
    if tag:
        result["tag"] = tag

    # 白名单属性
    for k in ELEMENT_ATTR_WHITELIST:
        v = raw_attrs.get(k)
        if v not in (None, ""):
            result[k] = v

    # aria-label 兜底：如果 aria-label 没采到，用 ariaLabel
    if "aria-label" not in result:
        aria = raw_attrs.get("ariaLabel")
        if aria:
            result["aria-label"] = aria

    # visible_text（清洗字体图标）
    visible_text = clean_visible_text(raw_attrs.get("visible_text"))
    if visible_text:
        result["visible_text"] = visible_text

    return result


def payload_to_trace_fields(payload: DistillEventPayload) -> dict[str, Any]:
    """把 DistillEventPayload 转成 TraceEvent 的字段（不含 timestamp，由 collector 填）。

    返回 dict 含：type, target, element_attrs, value, key, url（按 payload 内容有的才填）。
    collector 拿这个 dict + timestamp + stage 组装成 TraceEvent。

    P3.6 新事件类型落地：
      - select_dropdown：value = 选中项 value
      - upload_file：value = 文件名；upload_ctx 折叠进 target 后缀（让 LLM 读到「在上传区域」）
      - send_keys：key = 键名（可能含修饰符，如 "Control+S"）
    """
    action_type = payload.get("type", "")
    trace_type = ACTION_TYPE_MAP.get(action_type, action_type)  # 未知类型原样透传

    fields: dict[str, Any] = {
        "type": trace_type,
    }

    # 目标元素
    raw_attrs = payload.get("raw_attrs")
    if raw_attrs:
        element_attrs = extract_element_attrs(raw_attrs)
        if element_attrs:
            fields["element_attrs"] = element_attrs

    # target（可读标签，可选）
    target = payload.get("target")
    if target:
        fields["target"] = target

    # upload_ctx 折叠进 target 后缀（TraceEvent 无独立 upload_ctx 字段，但 distiller 要能读到）
    upload_ctx = payload.get("upload_ctx")
    if upload_ctx and isinstance(upload_ctx, dict):
        ctx_hint = _format_upload_ctx(upload_ctx)
        if ctx_hint:
            base = fields.get("target") or target or "文件上传"
            fields["target"] = f"{base}（{ctx_hint}）"

    # 动作值
    if "value" in payload:
        fields["value"] = payload["value"]
    if "key" in payload:
        fields["key"] = payload["key"]
    if "url" in payload:
        fields["url"] = payload["url"]
    if "scroll_amount" in payload:
        # scroll 的 amount 存进 value（TraceEvent 无独立 scroll_amount 字段）
        fields["value"] = str(payload["scroll_amount"])

    return fields


def _format_upload_ctx(ctx: dict[str, Any]) -> str:
    """把 upload_ctx 折叠成一句人类可读提示，附进 target 让 distiller 写 quirks。

    优先级：label_text > aria_text > region_text，再补 in_dialog 标记。
    例：{label_text:"封面图", in_dialog:true} → "封面图（在弹窗内）"
    """
    parts: list[str] = []
    label = ctx.get("label_text") or ctx.get("aria_text") or ctx.get("region_text")
    if label:
        parts.append(str(label)[:40])
    if ctx.get("in_dialog"):
        parts.append("在弹窗内")
    return "，".join(parts)
