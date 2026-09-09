"""TreeWalker rerun-history JSON → TreeForge trace JSON 纯格式转换器。

【用途】把 TreeWalker agent 自动探索产出的 rerun-history（ab_treatment_*.json 等）
转成 TreeForge 能蒸馏的 trace JSON（带 element_attrs 的新格式）。

【定位】P2 采集层就绪后，这条路径已退化为「格式转换器」——不再推断 stage、
不注入 page_context（P2 扩展采集时已绑定 stage + page_context，无需事后猜测）。
rerun-history 本身不含 stage/DOM 信息，转换产出的 trace 因此没有 stage 字段，
distiller 能容错处理。

【为什么用 rerun-history 而不是手工反推】rerun-history 是 agent 真实操作的完整记录，
含时序（step_number）、操作目标（interacted_element）、填值（params.text）、无障碍名称
（ax_name）——这些都是手工标注要做的语义判断，自动转换比手工省 95% 工作量。

【输入输出】
  输入：rerun-history JSON 文件（含 history[] 数组，每步有 model_output.actions 和
        interacted_element）
  输出：TreeForge trace JSON（{host, task_instruction, events[{type, element_attrs,
        url, value, timestamp, ...}]}）

  trace 落 examples/ 后可直接 `uv run treeforge distill <trace>` 蒸馏。

【action 映射】
  navigate    → navigate（url 来自 params.url）
  click       → click（element 来自 interacted_element[0]）
  input_text  → input（value=params.text）
  send_keys   → keydown（key=params.keys）
  upload_file → change（value=params.path，element 带 accept）
  wait        → 跳过（无操作目标，非用户语义）
  evaluate    → 跳过（JS 注入，非用户语义）
  screenshot  → 跳过（非用户操作）
  done        → 跳过（结束标记，但 done.params.text 存为 task_instruction）

【element_attrs 构造】
  - tag ← node_name.lower()
  - 白名单属性 ← attributes 过滤（id/name/type/placeholder/aria-label/role/data-*/
    data-test/data-cy/contenteditable）
  - visible_text ← ax_name 清洗私有 Unicode（字体图标区 \ue000-\uf8ff）后的文本
  - 既无白名单属性也无 ax_name 时：xpath 兜底，存到 selector 字段

【用法】
  uv run python tools/rerun_to_trace.py \\
      D:/dev/git/z_jordon/TreeWalker/rerun-history/ab_treatment_1.json \\
      --output examples/bilibili-upload.trace.json \\
      --task "在 B 站投稿上传一个视频"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from harness.hostkey import extract_host_with_port

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# element_attrs 的白名单属性（对齐 harness/atomizer._ATTR_WHITELIST）
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

# 私有 Unicode 区（字体图标 / 专用区）：\ue000-\uf8ff + \U000f0000-\U000ffffd
# B 站的 ax_name 常带字体图标前缀（如 \ue66c投稿），清洗后得「投稿」。
_PRIVATE_UNICODE_RE = re.compile("[\ue000-\uf8ff\U000f0000-\U000ffffd]")

# skip 的 action（非用户操作语义）
_SKIP_ACTIONS: frozenset[str] = frozenset({"wait", "evaluate", "screenshot"})

# timestamp 基准：从第一个 step 的 step_start_time 算相对毫秒
_BASE_TS: float | None = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _clean_ax_name(ax_name: str | None) -> str:
    """清洗 ax_name：去私有 Unicode（字体图标）+ trim。

    例：'\\ue66c投稿' → '投稿'。None 或空返回 ''。
    """
    if not ax_name:
        return ""
    cleaned = _PRIVATE_UNICODE_RE.sub("", ax_name).strip()
    return cleaned


def _extract_element_attrs(element: dict) -> tuple[dict, str | None]:
    """从 interacted_element 构造 (element_attrs, selector_fallback)。

    返回：
      element_attrs: 白名单属性 + tag + visible_text（对齐 TraceEvent.element_attrs）
      selector_fallback: 当 element_attrs 缺定位信息时，返回 xpath 作兜底；
                         element_attrs 足够时返回 None
    """
    if not isinstance(element, dict):
        return {}, None

    attrs_raw = element.get("attributes") or {}
    tag = (element.get("node_name") or "").lower()

    # 白名单属性
    element_attrs: dict = {"tag": tag} if tag else {}
    for k in ATTR_WHITELIST:
        v = attrs_raw.get(k)
        if v not in (None, ""):
            element_attrs[k] = v

    # visible_text 来自 ax_name（清洗后）
    visible_text = _clean_ax_name(element.get("ax_name"))
    if visible_text:
        element_attrs["visible_text"] = visible_text

    # 判断是否需要 xpath 兜底：既无白名单属性（除 tag 外）也无 visible_text
    has_locator = any(k != "tag" for k in element_attrs) or bool(visible_text)
    if has_locator:
        return element_attrs, None

    # 兜底：用 xpath 作 selector
    xpath = element.get("x_path") or element.get("xpath")
    if xpath:
        # 补成绝对 xpath 格式
        xp = xpath if xpath.startswith("/") else "/" + xpath
        return element_attrs, xp

    return element_attrs, None


def _ts_ms(step_meta: dict) -> int:
    """从 step metadata 取相对毫秒时间戳（从第一步算起）。"""
    global _BASE_TS
    start = step_meta.get("step_start_time") or 0
    if _BASE_TS is None:
        _BASE_TS = start
    return int((start - _BASE_TS) * 1000)


def _host_from_url(url: str) -> str:
    """从 url 提取 host key（端口限定，S0b issue #9——与蒸馏产物 key 对齐）。"""
    if not url:
        return ""
    return extract_host_with_port(url) or ""


# ---------------------------------------------------------------------------
# action 转换
# ---------------------------------------------------------------------------


def _convert_action(
    action: dict,
    element: dict | None,
    step_meta: dict,
    url: str,
) -> dict | None:
    """把一个 rerun action 转成 trace event dict。返回 None 表示跳过。"""
    name = action.get("name")
    params = action.get("params") or {}

    if name in _SKIP_ACTIONS:
        return None

    base = {
        "timestamp": _ts_ms(step_meta),
        "url": url or None,
    }

    if name == "navigate":
        nav_url = params.get("url") or url
        return {**base, "type": "navigate", "url": nav_url}

    if name == "click":
        element_attrs, selector = _extract_element_attrs(element or {})
        return {
            **base,
            "type": "click",
            "element_attrs": element_attrs,
            **({"selector": selector} if selector else {}),
        }

    if name == "input_text":
        element_attrs, selector = _extract_element_attrs(element or {})
        return {
            **base,
            "type": "input",
            "value": params.get("text"),
            "element_attrs": element_attrs,
            **({"selector": selector} if selector else {}),
        }

    if name == "send_keys":
        # send_keys 通常紧跟 input_text，作为「按 Enter 确认」等
        keys = params.get("keys") or ""
        element_attrs, selector = _extract_element_attrs(element or {})
        return {
            **base,
            "type": "keydown",
            "key": keys,
            "element_attrs": element_attrs,
            **({"selector": selector} if selector else {}),
        }

    if name == "upload_file":
        element_attrs, selector = _extract_element_attrs(element or {})
        # file input 的 accept 优先取 element.attributes.accept，兜底 params.accept
        accept = (element or {}).get("attributes", {}).get("accept") or params.get("accept") or ""
        if accept:
            element_attrs["accept"] = accept
            element_attrs["type"] = "file"
        return {
            **base,
            "type": "change",
            "value": params.get("path"),
            "element_attrs": element_attrs,
            **({"selector": selector} if selector else {}),
        }

    if name == "done":
        return None  # done 不产 event，task_instruction 单独提取

    # 未知 action：跳过但不报错（向前兼容未来新增的 action 类型）
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def convert_rerun_to_trace(
    rerun_data: dict,
    *,
    task_instruction: str = "",
) -> dict:
    """把整份 rerun-history 转成 trace dict。

    返回 {host, task_instruction, events[]} 格式，直接可写 trace JSON。

    纯格式转换——不推断 stage、不注入 page_context。P2 扩展采集层产出自带
    stage + page_context 的 trace；rerun-history 本身不含这些信息，转换产出的
    trace 因此无 stage 字段（distiller 容错处理）。
    """
    global _BASE_TS
    _BASE_TS = None  # 重置基准（多次调用工具时）

    history = rerun_data.get("history") or []
    events: list[dict] = []
    inferred_task = task_instruction

    # 第一遍：确定 host（取第一个 navigate 的 url，或第一个有 url 的 step）
    host = ""
    for step in history:
        url = (step.get("state_summary") or {}).get("url") or ""
        if url:
            host = _host_from_url(url) or host
            if host:
                break

    # 如果没传 task_instruction，从最后一个 done 步骤的 params.text 推断
    if not inferred_task:
        for step in reversed(history):
            for a in step.get("model_output", {}).get("actions", []):
                if a.get("name") == "done":
                    text = (a.get("params") or {}).get("text") or ""
                    # done.text 通常是总结，截前 100 字做 task
                    inferred_task = text[:100].replace("\n", " ").strip()
                    break
            if inferred_task:
                break

    # 转换每个 step（纯格式转换，不推断 stage）
    for step in history:
        actions = (step.get("model_output") or {}).get("actions") or []
        interacted = step.get("interacted_element") or []
        step_meta = step.get("metadata") or {}
        url = (step.get("state_summary") or {}).get("url") or ""

        for i, action in enumerate(actions):
            # interacted_element 是 list，按 action 顺序对齐（多数 step 只有 1 个 action）
            element = interacted[i] if i < len(interacted) else None
            event = _convert_action(action, element, step_meta, url)
            if event is not None:
                events.append(event)

    return {
        "host": host or "unknown",
        "task_instruction": inferred_task or None,
        "events": events,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rerun_to_trace",
        description=(
            "把 TreeWalker rerun-history JSON（agent 自动探索产物）转成 "
            "TreeForge trace JSON（带 element_attrs 的新格式）。"
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        help="rerun-history JSON 文件路径（如 ab_treatment_1.json）",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="输出 trace JSON 路径（缺省打到 stdout）",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="",
        help="任务描述（覆盖 done.params.text 的自动推断）",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"错误：文件不存在：{args.input}", file=sys.stderr)
        return 2

    rerun_data = json.loads(args.input.read_text(encoding="utf-8"))
    trace = convert_rerun_to_trace(rerun_data, task_instruction=args.task)

    n_events = len(trace["events"])
    n_with_attrs = sum(1 for e in trace["events"] if e.get("element_attrs"))
    n_with_xpath = sum(1 for e in trace["events"] if e.get("selector"))
    print(
        f"[{args.input.name}] host={trace['host']} "
        f"events={n_events} (有 element_attrs: {n_with_attrs}, xpath 兜底: {n_with_xpath})",
        file=sys.stderr,
    )

    output_str = json.dumps(trace, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_str, encoding="utf-8")
        print(f"写入 {args.output}", file=sys.stderr)
    else:
        print(output_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
