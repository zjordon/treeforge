"""把采集会话导出成 treeforge trace + 快照双文件（P2.2.5）。

【设计依据】docs/p2/README.md 3.3 节（产物格式与关联）。
【产物】
  <output_dir>/<name>/
  ├── trace.json      文件 A：treeforge trace（host/events/page_context，可被 treeforge distill 直接消费）
  └── snapshots/      文件 B：每阶段一份 DOM 文本（人工审阅/调试用，page_context 已内联进 trace）
      ├── <stage1>.txt
      └── <stage2>.txt

【关联】event.stage === page_context 的 key（1:N，SPA 多步共享一快照）。

page_context 的 value 是 element_tree_text（纯 DOM 文本树，不带人工导出的 ==== 分隔线装饰），
distiller 直接消费（_render_page_context 渲染进 prompt）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from treeforge.capture.collector import CaptureSession

logger = logging.getLogger(__name__)


def export_capture(
    session: CaptureSession,
    output_dir: str | Path,
    name: str | None = None,
) -> Path:
    """把采集会话导出成 trace.json + snapshots/。

    Args:
        session: 采集会话（含 events + page_context）
        output_dir: 输出根目录
        name: 产物目录名（默认用 session_id）

    Returns:
        产物目录路径（trace.json 所在目录）
    """
    out_root = Path(output_dir)
    name = name or session.session_id
    capture_dir = out_root / name
    snapshots_dir = capture_dir / "snapshots"
    capture_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    # 转 CapturedEvent → trace event dict（对齐 examples trace 格式）
    events = []
    for ev in session.events:
        event_dict: dict = {
            "timestamp": ev.timestamp,
            "type": ev.type,
        }
        if ev.url:
            event_dict["url"] = ev.url
        if ev.stage:
            event_dict["stage"] = ev.stage
        if ev.target:
            event_dict["target"] = ev.target
        if ev.element_attrs:
            event_dict["element_attrs"] = ev.element_attrs
        if ev.value is not None:
            event_dict["value"] = ev.value
        if ev.key is not None:
            event_dict["key"] = ev.key
        # P3.6：副作用信号（modal/dropdown 打开）——非空才写，保持老 trace 兼容。
        # distiller 据此写 quirks.md（「点这个按钮会弹 modal」）。
        if ev.signals:
            event_dict["signals"] = ev.signals
        events.append(event_dict)

    # page_context（stage → element_tree_text）
    page_context = dict(session.page_context)

    # trace.json（对齐 examples/bilibili-upload.trace.json 格式）
    trace = {
        "host": session.host,
        "task_instruction": session.task_instruction,
        "events": events,
        "page_context": page_context,
    }
    trace_path = capture_dir / "trace.json"
    _atomic_write_json(trace_path, trace)

    # snapshots/（每阶段一份 .txt，便于人工审阅；文件名 = stage 名）
    for stage, dom_text in page_context.items():
        # stage 名可能含特殊字符（如 upload_2），清理成安全文件名
        safe_name = _sanitize_filename(stage)
        snapshot_path = snapshots_dir / f"{safe_name}.txt"
        snapshot_path.write_text(dom_text, encoding="utf-8")

    logger.info(
        "Exported capture: %s (%d events, %d stages)",
        trace_path,
        len(events),
        len(page_context),
    )
    return capture_dir


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写 JSON（tmp + os.replace，对齐 harness/install.atomic_write_text）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)  # 原子替换（Windows 避免 WinError 183）


# 文件名非法字符（Windows + 通用）
_FILENAME_UNSAFE = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    """清理 stage 名成安全文件名（去掉路径分隔符等）。"""
    safe = "".join("_" if c in _FILENAME_UNSAFE else c for c in name).strip("._ ")
    return safe or "unnamed"
