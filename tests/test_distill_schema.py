"""distill payload schema 测试——collector 和扩展契约的守护测试。

这些测试钉死「扩展发什么 → collector 收什么」的字段映射，是双端对齐的依据。
扩展侧（TS）应实现同样的逻辑（clean_visible_text/extract_element_attrs/payload_to_trace_fields）。
"""

from __future__ import annotations

from treeforge.capture.distill_schema import (
    ACTION_TYPE_MAP,
    ELEMENT_ATTR_WHITELIST,
    RAW_ATTR_KEYS,
    clean_visible_text,
    extract_element_attrs,
    payload_to_trace_fields,
)

# ---------------------------------------------------------------------------
# clean_visible_text
# ---------------------------------------------------------------------------


def test_clean_visible_text_strips_font_icons():
    """字体图标（私有 Unicode 区）应被清洗掉。"""
    # \ue000-\uf8ff 是字体图标常用区
    assert clean_visible_text("\ue001投稿") == "投稿"
    assert clean_visible_text("立即投稿\uf800") == "立即投稿"
    assert clean_visible_text("\ue000\uf8ff") == ""


def test_clean_visible_text_compresses_whitespace():
    """连续空白应压缩。"""
    assert clean_visible_text("  hello   world  ") == "hello world"
    assert clean_visible_text("标签\t\n输入") == "标签 输入"


def test_clean_visible_text_handles_empty():
    """空值处理。"""
    assert clean_visible_text(None) == ""
    assert clean_visible_text("") == ""
    assert clean_visible_text("   ") == ""


def test_clean_visible_text_preserves_normal_text():
    """正常文本（含中文/emoji）不受影响。"""
    assert clean_visible_text("投稿按钮") == "投稿按钮"
    assert clean_visible_text("upload 🎬") == "upload 🎬"


# ---------------------------------------------------------------------------
# extract_element_attrs
# ---------------------------------------------------------------------------


def test_extract_attrs_keeps_whitelist_drops_others():
    """白名单属性保留，非白名单丢弃。"""
    raw = {
        "tag": "INPUT",
        "id": "title",
        "name": "title",
        "type": "text",
        "placeholder": "请输入标题",
        "class": "form-control dynamic-abc123",  # 非白名单，应丢
        "style": "color: red",  # 非白名单，应丢
        "data-testid": "title-input",
    }
    attrs = extract_element_attrs(raw)
    assert attrs["tag"] == "input"  # lowercase
    assert attrs["id"] == "title"
    assert attrs["type"] == "text"
    assert attrs["placeholder"] == "请输入标题"
    assert attrs["data-testid"] == "title-input"
    assert "class" not in attrs
    assert "style" not in attrs


def test_extract_attrs_keeps_accept_for_file_input():
    """accept 保留（file input quirks 关键，虽不在 atomizer 白名单）。"""
    raw = {"tag": "input", "type": "file", "accept": ".mp4,.flv", "name": "buploader"}
    attrs = extract_element_attrs(raw)
    assert attrs["accept"] == ".mp4,.flv"


def test_extract_attrs_aria_label_fallback_to_ariaLabel():
    """aria-label 取不到时用 ariaLabel 兜底。"""
    raw = {"tag": "button", "ariaLabel": "提交"}
    attrs = extract_element_attrs(raw)
    assert attrs["aria-label"] == "提交"


def test_extract_attrs_visible_text_cleaned():
    """visible_text 清洗字体图标后保留。"""
    raw = {"tag": "span", "visible_text": "\ue001立即投稿"}
    attrs = extract_element_attrs(raw)
    assert attrs["visible_text"] == "立即投稿"


def test_extract_attrs_empty_raw_returns_empty():
    """空输入返回空 dict。"""
    assert extract_element_attrs(None) == {}
    assert extract_element_attrs({}) == {}


def test_extract_attrs_skips_empty_values():
    """空值属性不保留。"""
    raw = {"tag": "div", "id": "", "name": None, "class": "x"}
    attrs = extract_element_attrs(raw)
    assert attrs == {"tag": "div"}  # id/name 空，class 非白名单


def test_extract_attrs_keeps_contenteditable_and_aria_labelledby():
    """contenteditable + aria-labelledby 富文本编辑器场景（如 bilibili 简介）。

    aria-labelledby 是 contenteditable 常见的语义关联（关联上方 <h3>标题</h3>），
    必须保留到 element_attrs，让 distiller 能识别编辑器用途。
    """
    raw = {
        "tag": "div",
        "contenteditable": "true",
        "aria-labelledby": "intro-label",
    }
    attrs = extract_element_attrs(raw)
    assert attrs["tag"] == "div"
    assert attrs["contenteditable"] == "true"
    assert attrs["aria-labelledby"] == "intro-label"


# ---------------------------------------------------------------------------
# payload_to_trace_fields
# ---------------------------------------------------------------------------


def test_payload_click_to_trace_fields():
    """click 事件转 TraceEvent 字段。"""
    payload = {
        "type": "click",
        "raw_attrs": {"tag": "a", "id": "nav_upload", "visible_text": "投稿"},
        "target": "投稿入口",
        "ts": 1000,
    }
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "click"
    assert fields["element_attrs"]["tag"] == "a"
    assert fields["element_attrs"]["id"] == "nav_upload"
    assert fields["element_attrs"]["visible_text"] == "投稿"
    assert fields["target"] == "投稿入口"


def test_payload_input_to_trace_fields():
    """input 事件带 value。"""
    payload = {
        "type": "input",
        "raw_attrs": {"tag": "input", "type": "text", "placeholder": "标题"},
        "value": "我的视频",
        "ts": 2000,
    }
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "input"
    assert fields["value"] == "我的视频"
    assert fields["element_attrs"]["placeholder"] == "标题"


def test_payload_keydown_to_trace_fields():
    """keydown 事件带 key。"""
    payload = {"type": "keydown", "key": "Enter", "ts": 3000}
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "keydown"
    assert fields["key"] == "Enter"
    assert "element_attrs" not in fields  # 无 raw_attrs


def test_payload_navigate_to_trace_fields():
    """navigate 事件带 url。"""
    payload = {"type": "navigate", "url": "https://x.com/page2", "ts": 4000}
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "navigate"
    assert fields["url"] == "https://x.com/page2"


def test_payload_scroll_amount_into_value():
    """scroll 的 amount 存进 value（TraceEvent 无独立字段）。"""
    payload = {"type": "scroll", "scroll_amount": 3, "ts": 5000}
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "scroll"
    assert fields["value"] == "3"


def test_payload_unknown_type_passthrough():
    """未知动作类型原样透传（不丢事件）。"""
    payload = {"type": "custom_action", "ts": 6000}
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "custom_action"


# ---------------------------------------------------------------------------
# P3.6 扩词：select_dropdown / upload_file / send_keys + upload_ctx 折叠
# ---------------------------------------------------------------------------


def test_payload_select_dropdown_passes_value():
    """select_dropdown：选中项 value 透传，raw_attrs 提炼成 element_attrs。"""
    payload = {
        "type": "select_dropdown",
        "value": "公开",
        "raw_attrs": {"tag": "select", "id": "privacy", "name": "privacy"},
        "target": "隐私设置",
    }
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "select_dropdown"
    assert fields["value"] == "公开"
    assert fields["element_attrs"]["tag"] == "select"
    assert fields["element_attrs"]["id"] == "privacy"
    assert fields["target"] == "隐私设置"


def test_payload_send_keys_passes_combo_key():
    """send_keys：组合键 key 透传（如 Control+S / Enter / F5）。"""
    payload = {"type": "send_keys", "key": "Control+S"}
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "send_keys"
    assert fields["key"] == "Control+S"


def test_payload_upload_file_value_is_filename():
    """upload_file：value = 文件名，accept 进 element_attrs，upload_ctx 折叠进 target。"""
    payload = {
        "type": "upload_file",
        "value": "cover.png",
        "raw_attrs": {"tag": "input", "type": "file", "accept": "image/*"},
        "target": "封面上传",
        "upload_ctx": {"label_text": "封面图", "in_dialog": True},
    }
    fields = payload_to_trace_fields(payload)
    assert fields["type"] == "upload_file"
    assert fields["value"] == "cover.png"
    assert fields["element_attrs"]["accept"] == "image/*"
    # upload_ctx 折叠进 target：label_text + 在弹窗内 提示
    assert "封面图" in fields["target"]
    assert "弹窗" in fields["target"]


def test_payload_upload_file_no_ctx_keeps_target():
    """upload_file 无 upload_ctx：target 原样保留（不强制折叠）。"""
    payload = {
        "type": "upload_file",
        "value": "a.jpg",
        "raw_attrs": {"tag": "input", "type": "file"},
        "target": "上传文件",
    }
    fields = payload_to_trace_fields(payload)
    assert fields["target"] == "上传文件"


def test_payload_upload_ctx_region_text_fallback():
    """upload_ctx：label/aria 都空时，region_text 兜底折叠进 target。"""
    payload = {
        "type": "upload_file",
        "value": "v.mp4",
        "raw_attrs": {"tag": "input", "type": "file"},
        "upload_ctx": {"region_text": "点击或拖拽视频到此处上传"},
    }
    fields = payload_to_trace_fields(payload)
    # 无 target 时用「文件上传」兜底 + region_text 折叠
    assert "点击或拖拽视频到此处上传" in fields["target"]


def test_data_tw_jsclick_in_whitelist():
    """P3.6：data-tw-jsclick（MAIN-world addEventListener hook 标记）应在双端白名单里。"""
    assert "data-tw-jsclick" in RAW_ATTR_KEYS
    assert "data-tw-jsclick" in ELEMENT_ATTR_WHITELIST
    # extract_element_attrs 应保留它
    attrs = extract_element_attrs({"tag": "div", "data-tw-jsclick": "1"})
    assert attrs.get("data-tw-jsclick") == "1"


# ---------------------------------------------------------------------------
# 契约一致性（schema 常量）
# ---------------------------------------------------------------------------


def test_action_type_map_covers_all_distill_actions():
    """所有 distill 动作类型都有映射。"""
    from typing import get_args

    from treeforge.capture.distill_schema import DistillActionType

    action_types = get_args(DistillActionType)
    for at in action_types:
        assert at in ACTION_TYPE_MAP, f"动作类型 {at} 缺 ACTION_TYPE_MAP 映射"


def test_whitelist_superset_of_atomizer():
    """ELEMENT_ATTR_WHITELIST 应是 atomizer 白名单的超集（多了 accept）。"""
    # atomizer 的白名单（harness/atomizer.py:130）
    atomizer_whitelist = {
        "id",
        "name",
        "type",
        "placeholder",
        "aria-label",
        "role",
        "data-testid",
        "data-test",
        "data-cy",
        "contenteditable",
    }
    assert atomizer_whitelist.issubset(set(ELEMENT_ATTR_WHITELIST))
    assert "accept" in ELEMENT_ATTR_WHITELIST  # 额外保留


def test_raw_attr_keys_cover_whitelist():
    """RAW_ATTR_KEYS（扩展采集的）应覆盖 ELEMENT_ATTR_WHITELIST（collector 保留的）。"""
    # visible_text 在 RAW_ATTR_KEYS 里作为注释说明，实际作为 raw_attrs["visible_text"] 传
    raw_set = set(RAW_ATTR_KEYS)
    for k in ELEMENT_ATTR_WHITELIST:
        assert k in raw_set, f"白名单属性 {k} 不在扩展采集键 RAW_ATTR_KEYS 里"
