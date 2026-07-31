"""TreeForge CLI 入口。

支持 ``python -m treeforge`` 和 console script ``treeforge`` 两种调用（等价）。

子命令：
  distill <trace.json> [--output <dir>] [--adapter {treewalker,browserbc}] [--no-llm]
    串起 ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL → INSTALL 全链路。

  capture [--task <desc>] [--host <domain>] [--output <dir>] [--cdp-port <n>]
    起采集后端 + 连 Chrome CDP，等扩展事件，Ctrl+C 导出 trace + 快照。
    需配合 Chrome 扩展（extension/）使用。

  info
    打印当前生效配置（脱敏 key），用于诊断。

注意：``--adapter treewalker`` 的 ``treewalker`` 是 adapter 名（按消费方命名，
产 TreeWalker 消费的多文件格式），与命令名 ``treeforge`` 是两个不同概念。

用 argparse（零额外依赖）。init-plan §7.4 提到可选用 typer，但 argparse 零依赖更符合哲学。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from adapters import get_adapter
from harness import adapter, atomizer, bucketer, classifier, config, distiller, install, progress
from harness.models import SkillCard

# ---------------------------------------------------------------------------
# distill 子命令
# ---------------------------------------------------------------------------


def _run_distill(trace_path: Path, output_dir: Path, adapter_name: str, no_llm: bool) -> int:
    """跑完整蒸馏链路。返回退出码。"""
    config.load()  # 刷新 .env（幂等）

    use_llm = (not no_llm) and bool(config.LLM_KEY)
    if not no_llm and not config.LLM_KEY:
        progress.report(
            "DISTILL",
            detail="LLM_KEY 未配置，自动退回模板模式（产物质量低，仅供链路验证）",
        )

    # ① ADAPT
    trace = adapter.load_trace(trace_path)

    # ② ATOMIZE
    segments = atomizer.atomize(trace)
    if not segments:
        progress.report("DISTILL", detail="无 segment，退出")
        return 1

    # ③ CLASSIFY
    use_llm_classify = use_llm
    classified = classifier.classify(segments, use_llm=use_llm_classify)

    # ④ BUCKET
    buckets = bucketer.bucket(classified)
    if not buckets:
        progress.report("DISTILL", detail="无 bucket，退出")
        return 1

    # ⑤ DISTILL（透传 trace 级 page_context，让 LLM 能看到 DOM 快照推 quirks）
    cards: list[SkillCard] = distiller.distill_buckets(
        buckets, use_llm=use_llm, page_context=trace.page_context
    )
    if not cards:
        progress.report("DISTILL", detail="无 card 产出，退出")
        return 1

    # INSTALL
    adp = get_adapter(adapter_name)
    written = install.install_cards(cards, output_dir, adp)

    # 汇总
    progress.report("DONE", detail=f"wrote {len(written)} files to {output_dir}")
    for p in written:
        print(f"  wrote: {p}")

    # 验收提示
    if adapter_name == "treewalker" and cards:
        host_dir = output_dir / "domain-skills" / cards[0].domain
        print()
        print(f"TreeWalker 注入目录：{host_dir}")
        print("包含文件：", sorted(p.name for p in host_dir.glob("*.md")))
    return 0


# ---------------------------------------------------------------------------
# info 子命令
# ---------------------------------------------------------------------------


def _run_info() -> int:
    config.load()
    cfg = config.describe()
    print("TreeForge 配置：")
    for k, v in cfg.items():
        print(f"  {k}: {v}")
    return 0


# ---------------------------------------------------------------------------
# capture 子命令（P2 采集层）
# ---------------------------------------------------------------------------


def _run_capture(args: argparse.Namespace) -> int:
    """起采集后端，阻塞收扩展事件，Ctrl+C 导出产物。

    Ctrl+C 处理：不用 asyncio.run（它在 Windows 的 Ctrl+C 会取消主任务，
    导致 export 逻辑跑不完）。改用手动 event loop + 传统 signal.signal 注册，
    Ctrl+C 时通过 loop.call_soon_threadsafe 设置 stop_event，让协程优雅退出并导出。
    """
    import signal as signal_mod

    from treeforge.capture.cli import run_capture
    from treeforge.capture.ws_discover import DEFAULT_CDP_HOST, DEFAULT_CDP_PORT

    output_dir = args.output or Path("./data/captures")
    kwargs = {
        "output_dir": output_dir,
        "task": args.task or "",
        "host": args.host or "",
        "cdp_host": args.cdp_host or DEFAULT_CDP_HOST,
        "cdp_port": args.cdp_port or DEFAULT_CDP_PORT,
        "backend_host": args.backend_host or "127.0.0.1",
        "backend_port": args.backend_port or 8765,
        "stage_threshold": args.stage_threshold,
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    # 注册 SIGINT handler：Ctrl+C 时线程安全地设置 stop_event（不取消主任务）
    def _sigint_handler(signum, frame):
        loop.call_soon_threadsafe(stop_event.set)

    original_handler = signal_mod.signal(signal_mod.SIGINT, _sigint_handler)
    try:
        return loop.run_until_complete(run_capture(stop_event=stop_event, **kwargs))
    finally:
        signal_mod.signal(signal_mod.SIGINT, original_handler)
        loop.close()


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="treeforge",
        description=(
            "TreeForge（树锻）—— 把浏览器示教 trace 蒸馏成 site-specific skill 文件。"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # distill
    p_distill = sub.add_parser("distill", help="蒸馏一份 trace → skill 文件")
    p_distill.add_argument("trace", type=Path, help="trace JSON 文件路径")
    p_distill.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="输出根目录（默认 ./data/skills 或 .env 的 OUTPUT_DIR）",
    )
    p_distill.add_argument(
        "--adapter",
        "-a",
        choices=["treewalker", "browserbc"],
        default=None,
        help="输出 adapter（默认 treewalker，可用 .env 的 DEFAULT_ADAPTER 覆盖）",
    )
    p_distill.add_argument(
        "--no-llm",
        action="store_true",
        help="强制用模板模式，不调 LLM（即使配了 key）",
    )

    # info
    sub.add_parser("info", help="打印当前生效配置")

    # capture（P2 采集层）
    p_capture = sub.add_parser(
        "capture",
        help="起采集后端 + 连 Chrome CDP，等扩展事件，Ctrl+C 导出 trace + 快照",
    )
    p_capture.add_argument("--task", "-t", default="", help="任务描述（写进 trace.task_instruction）")
    p_capture.add_argument("--host", default="", help="目标站点主域名（默认自动从 URL 提取）")
    p_capture.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("./data/captures"),
        help="采集产物输出目录（默认 ./data/captures）",
    )
    p_capture.add_argument("--cdp-host", default="localhost", help="Chrome 远程调试 host（默认 localhost）")
    p_capture.add_argument("--cdp-port", type=int, default=9222, help="Chrome 远程调试端口（默认 9222）")
    p_capture.add_argument("--backend-host", default="127.0.0.1", help="采集后端监听 host（默认 127.0.0.1）")
    p_capture.add_argument("--backend-port", type=int, default=8765, help="采集后端监听端口（默认 8765）")
    p_capture.add_argument(
        "--stage-threshold",
        type=float,
        default=None,
        help="阶段切换 DOM 相似度阈值（默认 0.7，低于此值视为新阶段）",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "info":
        return _run_info()

    if args.command == "capture":
        return _run_capture(args)

    if args.command == "distill":
        trace_path: Path = args.trace
        if not trace_path.is_file():
            print(f"错误：trace 文件不存在：{trace_path}", file=sys.stderr)
            return 2

        output_dir = args.output or config.OUTPUT_DIR
        adapter_name = args.adapter or config.DEFAULT_ADAPTER

        return _run_distill(trace_path, output_dir, adapter_name, args.no_llm)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
