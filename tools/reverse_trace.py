"""TreeWalker DOM 文本 → trace 候选元素清单 反推辅助工具。

【用途】采集层（P2）未开发前，从 TreeWalker 给模型的 DOM 文本（[index]<tag attr=val /> text
格式）反推出「带 element_attrs 的候选元素清单」。用户在清单上做语义标注（时序/选目标/填值）
后，再手工产成最终 trace。

【为什么需要】bilibili-upload.trace.json 是假数据，蒸馏出的 skill 与真实 DOM 对不上（见
docs/skill-format-alignment.md）。本工具把真实 DOM 结构化，让你不用从零手写 trace。

【输入输出】
  输入：一个或多个 DOM txt 文件（如 D:/temp/tree-walker-model-input/bili/*.txt）
  输出：JSON（stdout 或 --output 指定文件），结构：
    {
      "source_files": ["upload.txt", ...],
      "candidates": [{tag, attrs, visible_text, location_path, interactive_type, element_attrs}],
      "file_inputs": [{accept, visible}]
    }

  注意：TreeWalker DOM 文本里的 [index] 是运行时给 DOM 元素动态分配的索引
  （每次页面加载/DOM 变化都会变），扩展录制时拿不到——所以工具只在内部用它去重，
  不输出。候选元素靠 element_attrs（白名单属性 + 可见文本）标识，与 trace/skill
  的设计对齐。

  工具不产最终 trace——只产候选清单。最终 trace 由你标注后产出。

【用法】
  uv run python tools/reverse_trace.py \\
      D:/temp/tree-walker-model-input/bili/upload.txt \\
      D:/temp/tree-walker-model-input/bili/upload-conver.txt \\
      D:/temp/tree-walker-model-input/bili/publish.txt \\
      --output examples/bilibili-upload.candidates.json

【白名单属性】只保留（对齐 distiller prompt 的 selectors.md 白名单）：
  id / name / type / placeholder / aria-label / role /
  data-testid / data-test / data-cy / contenteditable
其余（class/style/src/href/...）丢弃。tag 和 visible 作为状态字段单独保留。

【可交互元素过滤】只输出候选，不做时序/意图判断（那需要人工）：
  tag ∈ {input, a, button, textarea, select} 或 attrs 含 role/data-*/contenteditable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 白名单与可交互元素定义
# ---------------------------------------------------------------------------

# element_attrs 的白名单属性（对齐 harness/atomizer._ATTR_WHITELIST + distiller prompt）
ATTR_WHITELIST: tuple[str, ...] = (
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
)

# 可交互元素：这些 tag 或带这些 attrs 的元素才进候选清单
INTERACTIVE_TAGS: tuple[str, ...] = ("input", "a", "button", "textarea", "select")
INTERACTIVE_ATTR_KEYS: tuple[str, ...] = (
    "role",
    "data-testid",
    "data-test",
    "data-cy",
    "contenteditable",
)

# 动作词：B 站很多交互元素是无 role/class 的 <span>/<div>/<i>，靠可见文本触发
# （如「立即投稿」「存草稿」「分区」）。带这些词的可见文本也算可交互候选。
# 用「包含」匹配，覆盖各种变体（投稿/立即投稿/确认投稿、保存/存草稿 等）。
ACTION_KEYWORDS: tuple[str, ...] = (
    # 提交/确认类
    "投稿", "提交", "确认", "发布", "发布视频", "保存", "存草稿", "下一步",
    # 选择/展开类
    "分区", "选择", "展开", "更多", "折叠",
    # 通用动作
    "上传", "下载", "添加", "删除", "编辑", "修改", "取消", "返回",
    # 英文
    "submit", "publish", "save", "cancel", "select", "upload",
)

# 这类「靠文本触发的非标准可交互元素」的 tag 限定——避免给所有 div/span 都检测
# （否则每个含「上传」二字的提示文案都会进候选，噪声太大）。
TEXT_TRIGGER_TAGS: tuple[str, ...] = ("span", "div", "i", "a", "button")

# ---------------------------------------------------------------------------
# 解析正则
# ---------------------------------------------------------------------------

# DOM 树行：[142]<a id=nav_upload_btn /> 或 [332]<input type=file name=buploader />
# 缩进（\t）表示层级。index/tag/attrs_string 三组。
DOM_LINE_RE = re.compile(r"^(?P<indent>\s*)\[(?P<index>\d+)\]<(?P<tag>\w+)(?P<attrs>[^>]*?)\s*/>")

# attrs 里的单个属性：key=val 或 key="val with space" 或裸 key（如 contenteditable）
ATTR_RE = re.compile(r'(?P<key>[\w-]+)(?:=(?:"(?P<qv>[^"]*)"|(?P<nv>\S+)))?')

# file input 对照段行：[index 332] backend_node_id=332 accept='...' visible=True
FILE_INPUT_RE = re.compile(
    r"\[index\s+(?P<index>\d+)\]\s+backend_node_id=(?P<backend>\d+)\s+accept='(?P<accept>[^']*)'(?:\s+visible=(?P<visible>True|False))?"
)


# ---------------------------------------------------------------------------
# 解析逻辑
# ---------------------------------------------------------------------------


def _parse_attrs(attrs_str: str) -> dict[str, str]:
    """从 ` id=x name=y placeholder="a b"` 串里提取属性 dict。

    只保留白名单属性。contenteditable 这类裸 key（无 =）值为 True。
    """
    out: dict[str, str] = {}
    for m in ATTR_RE.finditer(attrs_str):
        key = m.group("key")
        if key not in ATTR_WHITELIST:
            continue
        qv = m.group("qv")
        nv = m.group("nv")
        if qv is not None:
            out[key] = qv
        elif nv is not None:
            out[key] = nv
        else:
            # 裸 key（如 contenteditable、hidden）——值为 True
            out[key] = "true"
    return out


def _is_interactive(tag: str, attrs: dict, visible_text: str = "") -> bool:
    """是否为可交互元素（应进候选清单）。

    三类：
      1. 可交互 tag（input/a/button/textarea/select）
      2. 带 role/data-*/contenteditable 属性
      3. 文本触发的 span/div/i——可见文本含动作词（如「立即投稿」「分区」）
    """
    if tag in INTERACTIVE_TAGS:
        return True
    if any(k in attrs for k in INTERACTIVE_ATTR_KEYS):
        return True
    # 动作词检测：只在 TEXT_TRIGGER_TAGS 里查，且 visible_text 短（长文本多半是文案不是按钮）
    if visible_text and tag in TEXT_TRIGGER_TAGS and len(visible_text) <= 12:
        text_lower = visible_text.lower()
        if any(kw in visible_text or kw in text_lower for kw in ACTION_KEYWORDS):
            return True
    return False


def _interactive_type(tag: str, attrs: dict, visible_text: str = "") -> str:
    """给可交互元素一个类型标签，方便人工标注。"""
    if tag == "input":
        t = attrs.get("type", "text")
        if t == "file":
            return "file_input"
        if t in ("checkbox", "radio"):
            return t
        return "input"
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag in ("textarea", "select"):
        return tag
    if "contenteditable" in attrs:
        return "contenteditable"
    if attrs.get("role"):
        return f"role:{attrs['role']}"
    # 文本触发的 span/div/i——标记为 text_trigger 方便人工识别
    if visible_text:
        return "text_trigger"
    return "interactive"


def parse_dom_text(text: str, source_name: str) -> tuple[list[dict], list[dict]]:
    """解析一个 DOM txt 文件，返回 (candidates, file_inputs)。

    candidates: 可交互元素清单（含 element_attrs）
    file_inputs: file input 对照段（单独收集）
    """
    lines = text.splitlines()
    candidates: list[dict] = []
    file_inputs: list[dict] = []
    # 同一文件内 DOM 段可能重复（Page DOM 出现 2 次），按 index 去重
    seen_dom_indices: set[int] = set()
    seen_file_indices: set[int] = set()

    # 段落标记：识别当前在哪一段（file input 对照 / DOM 树 / 其它）
    section: str | None = None

    # DOM 树用缩进栈推祖先链，算 location_path
    # 栈元素：(indent_level, tag)
    ancestor_stack: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 段标题识别（[1] file input index 对照 / [Page DOM] element_tree_text 等）
        # 段标题特征：[ 开头 + 有 ] + 无 < + 无 = （file input 数据行含 backend_node_id=）
        if (
            stripped.startswith("[")
            and "]" in stripped
            and "<" not in stripped
            and "=" not in stripped
        ):
            lower = stripped.lower()
            if "file input" in lower:
                section = "file_inputs"
            elif "page dom" in lower or "element_tree" in lower:
                section = "dom"
            else:
                section = "other"
            ancestor_stack = []  # 段切换重置栈
            continue

        # 分隔符行（====...）
        if stripped.startswith("===="):
            continue

        # file input 对照段
        if section == "file_inputs":
            m = FILE_INPUT_RE.search(line)
            if m:
                fi_index = int(m.group("index"))
                if fi_index in seen_file_indices:
                    continue
                seen_file_indices.add(fi_index)
                # 注意：index / backend_node_id 是 TreeWalker 运行时分配的，
                # 扩展录制时拿不到，不输出——只用 accept + visible 标识。
                file_inputs.append(
                    {
                        "accept": m.group("accept"),
                        "visible": m.group("visible") == "True" if m.group("visible") else None,
                        "source": source_name,
                    }
                )
            continue

        # DOM 树段
        if section != "dom":
            continue

        m = DOM_LINE_RE.match(line)
        if not m:
            continue

        indent_str = m.group("indent")
        # 缩进层级：用 tab 数（每 \t 一级）。混合空格/tab 时按字符宽估算。
        indent_level = indent_str.count("\t") + indent_str.count("    ") + (
            len(indent_str.replace("\t", "").replace("    ", "")) // 2
        )
        index = int(m.group("index"))
        tag = m.group("tag")
        attrs = _parse_attrs(m.group("attrs"))

        # 更新祖先栈：弹出比当前层级深或等的祖先
        while ancestor_stack and ancestor_stack[-1][0] >= indent_level:
            ancestor_stack.pop()
        parent_tag = ancestor_stack[-1][1] if ancestor_stack else None
        ancestor_stack.append((indent_level, tag))

        # 可见文本：看下一非空行（不是 DOM 行的）是否是文本
        # 【顺序】visible_text 必须在 _is_interactive 之前算——动作词检测依赖它
        visible_text = ""
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            next_stripped = next_line.strip()
            # 下一行如果不是 DOM 行、不是分隔符、不是空，就是可见文本
            if (
                next_stripped
                and not next_stripped.startswith("[")
                and not next_stripped.startswith("====")
                and not DOM_LINE_RE.match(next_line)
            ):
                visible_text = next_stripped

        # 只收可交互元素（含动作词文本触发的 span/div）
        if not _is_interactive(tag, attrs, visible_text=visible_text):
            continue

        # 文件内按 index 去重（DOM 段可能重复）
        if index in seen_dom_indices:
            continue
        seen_dom_indices.add(index)

        location_path = f"{parent_tag} > {tag}" if parent_tag else tag

        # 构造 element_attrs（对齐 harness TraceEvent.element_attrs 格式）
        element_attrs = dict(attrs)
        element_attrs["tag"] = tag
        if visible_text:
            element_attrs["visible_text"] = visible_text

        # 注意：index 是 TreeWalker 运行时给 DOM 分配的索引（每次页面加载/DOM 变化都会变），
        # 扩展录制时拿不到，不输出——靠 element_attrs（白名单属性 + 可见文本）标识元素。
        candidates.append(
            {
                "tag": tag,
                "attrs": attrs,  # 纯白名单属性（不含 tag/visible_text）
                "element_attrs": element_attrs,  # 含 tag/visible_text（可直接喂 TraceEvent）
                "visible_text": visible_text,
                "location_path": location_path,
                "interactive_type": _interactive_type(tag, attrs, visible_text=visible_text),
                "source": source_name,
            }
        )

    return candidates, file_inputs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reverse_trace",
        description=(
            "把 TreeWalker DOM 文本（[index]<tag attr=val /> text 格式）反推成 "
            "trace 候选元素清单 JSON。用户在清单上标注时序/目标/值后产最终 trace。"
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="一个或多个 DOM txt 文件路径",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="输出 JSON 文件路径（缺省打到 stdout）",
    )
    parser.add_argument(
        "--all-elements",
        action="store_true",
        help="输出所有元素（默认只输出可交互元素，加此开关全量输出，工作量更大）",
    )
    args = parser.parse_args(argv)

    all_candidates: list[dict] = []
    all_file_inputs: list[dict] = []
    source_names: list[str] = []

    for txt_path in args.inputs:
        if not txt_path.is_file():
            print(f"错误：文件不存在：{txt_path}", file=sys.stderr)
            return 2
        text = txt_path.read_text(encoding="utf-8")
        candidates, file_inputs = parse_dom_text(text, txt_path.name)
        if not args.all_elements:
            # 默认已过滤可交互元素；all-elements 时重新解析全量（这里简化：默认行为已是对的）
            pass
        all_candidates.extend(candidates)
        all_file_inputs.extend(file_inputs)
        source_names.append(txt_path.name)
        print(
            f"[{txt_path.name}] 候选 {len(candidates)} 个，file input {len(file_inputs)} 个",
            file=sys.stderr,
        )

    result = {
        "source_files": source_names,
        "candidates": all_candidates,
        "file_inputs": all_file_inputs,
        "annotation_guide": {
            "说明": "这是反推工具产出的候选元素清单，不是最终 trace。请按下列步骤标注：",
            "步骤": [
                "1. 按 trace 顺序重排 candidates（DOM 文本无时序，需人工按真实操作顺序排）",
                "2. 删掉非操作目标的候选（DOM 里有很多元素，只有少数是真实操作目标）",
                "3. 给每个保留的候选补 type（click/input/change/submit/navigate）",
                "4. input/change 事件补 value（DOM 里只有 placeholder，没有真实输入值）",
                "5. 最终产成 {host, events:[{type, element_attrs, url, timestamp, ...}]} 格式 trace",
            ],
            "element_attrs 格式": "直接用每个候选的 element_attrs 字段（已含 tag + 白名单属性 + visible_text）",
            "file_inputs": "file input 单独列出（含 accept/visible），按 accept 区分视频/字幕/附件，选 visible=True 的",
        },
    }

    output_str = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_str, encoding="utf-8")
        print(f"\n写入 {args.output}", file=sys.stderr)
        print(f"共 {len(all_candidates)} 候选，{len(all_file_inputs)} file input", file=sys.stderr)
    else:
        print(output_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
