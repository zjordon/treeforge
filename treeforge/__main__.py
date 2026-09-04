"""TreeForge CLI 入口。

支持 ``python -m treeforge`` 和 console script ``treeforge`` 两种调用（等价）。

子命令：
  distill <trace.json> [--output <dir>] [--adapter {treewalker,browserbc}] [--no-llm]
    串起 ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL → INSTALL 全链路。

  capture [--task <desc>] [--host <domain>] [--output <dir>] [--cdp-port <n>]
    起采集后端 + 连 Chrome CDP，等扩展事件，Ctrl+C 导出 trace + 快照。
    需配合 Chrome 扩展（extension/）使用。一次性命令（录完即退出）。

  serve [--host <ip>] [--port <n>] [--cdp-port <n>] [--captures-dir <dir>] [--skills-dir <dir>]
    起 FastAPI 常驻服务：采集（4 端点，扩展零改动）+ 蒸馏 API + 配置/状态 API + 控制面板 SPA。
    Chrome 未开也能启动（蒸馏/配置/状态可用）；扩展点「停止」不退出进程（session 可循环）。

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

from harness import config, progress

# ---------------------------------------------------------------------------
# distill 子命令
# ---------------------------------------------------------------------------


def _run_distill(
    trace_paths: list[Path],
    output_dir: Path,
    adapter_name: str,
    no_llm: bool,
    fresh: bool = False,
    task_description: str = "",
) -> int:
    """跑完整蒸馏链路（CLI 薄包装，P4 支持多 trace 累积 + 双产物）。

    实际管线在 ``server.distill_api.run_distill_pipeline``（与 HTTP 后台任务共用），
    这里只负责 CLI 的 print + 退出码。
    """
    from server.distill_api import run_distill_pipeline

    result = run_distill_pipeline(
        trace_paths=trace_paths,
        output_dir=output_dir,
        adapter_name=adapter_name,
        no_llm=no_llm,
        fresh=fresh,
        task_description=task_description or None,
    )

    if not result.ok:
        progress.report("DISTILL", detail=result.error or "失败")
        return 1

    for p in result.written:
        print(f"  wrote: {p}")

    # 验收提示
    if adapter_name == "treewalker" and result.host_dir:
        print()
        print(f"TreeWalker 注入目录：{result.host_dir}")
        print("包含文件：", sorted(p.name for p in result.host_dir.glob("*.md")))
        if result.task_dir:
            print(f"任务级 skill：{result.task_dir}（slug={result.task_slug}）")
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
# serve 子命令（P3 常驻服务）
# ---------------------------------------------------------------------------


def _run_serve(args: argparse.Namespace) -> int:
    """起 FastAPI 常驻服务（uvicorn 阻塞跑，Ctrl+C 由 uvicorn 管）。"""
    from treeforge.serve import run_serve

    skills_dir = args.skills_dir or config.OUTPUT_DIR
    return run_serve(
        host=args.host,
        port=args.port,
        cdp_host=args.cdp_host,
        cdp_port=args.cdp_port,
        captures_dir=args.captures_dir,
        skills_dir=skills_dir,
        reload=args.reload,
    )


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="treeforge",
        description=("TreeForge（树锻）—— 把浏览器示教 trace 蒸馏成 site-specific skill 文件。"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # distill
    p_distill = sub.add_parser(
        "distill", help="蒸馏 trace → skill 文件（可多 trace 同 host 累积，产站点级+任务级）"
    )
    p_distill.add_argument(
        "trace",
        type=Path,
        nargs="+",
        help="trace JSON 文件路径（可多个：同 host 多任务累积蒸馏）",
    )
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
    p_distill.add_argument(
        "--fresh",
        action="store_true",
        help="忽略 registry 旧卡从头蒸馏（默认增量：同 host 累积合并）",
    )
    p_distill.add_argument(
        "--task",
        default="",
        help="任务描述（可选；进任务级 skill 元数据与 prompt，缺省用 trace 的 task_instruction）",
    )

    # info
    sub.add_parser("info", help="打印当前生效配置")

    # capture（P2 采集层）
    p_capture = sub.add_parser(
        "capture",
        help="起采集后端 + 连 Chrome CDP，等扩展事件，Ctrl+C 导出 trace + 快照",
    )
    p_capture.add_argument(
        "--task", "-t", default="", help="任务描述（写进 trace.task_instruction）"
    )
    p_capture.add_argument("--host", default="", help="目标站点主域名（默认自动从 URL 提取）")
    p_capture.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("./data/captures"),
        help="采集产物输出目录（默认 ./data/captures）",
    )
    p_capture.add_argument(
        "--cdp-host", default="localhost", help="Chrome 远程调试 host（默认 localhost）"
    )
    p_capture.add_argument(
        "--cdp-port", type=int, default=9223, help="Chrome 远程调试端口（默认 9223）"
    )
    p_capture.add_argument(
        "--backend-host", default="127.0.0.1", help="采集后端监听 host（默认 127.0.0.1）"
    )
    p_capture.add_argument(
        "--backend-port", type=int, default=8765, help="采集后端监听端口（默认 8765）"
    )
    p_capture.add_argument(
        "--stage-threshold",
        type=float,
        default=None,
        help="阶段切换 DOM 相似度阈值（默认 0.7，低于此值视为新阶段）",
    )

    # serve（P3 常驻服务）
    p_serve = sub.add_parser(
        "serve",
        help="起 FastAPI 常驻服务（采集 + 蒸馏 API + 控制面板），阻塞跑",
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="服务监听 host（默认 127.0.0.1）")
    p_serve.add_argument(
        "--port", type=int, default=8765, help="服务监听端口（默认 8765，扩展默认连此端口）"
    )
    p_serve.add_argument(
        "--cdp-host", default="localhost", help="Chrome 远程调试 host（默认 localhost）"
    )
    p_serve.add_argument(
        "--cdp-port", type=int, default=9223, help="Chrome 远程调试端口（默认 9223）"
    )
    p_serve.add_argument(
        "--captures-dir",
        type=Path,
        default=Path("./data/captures"),
        help="采集产物根目录（默认 ./data/captures）",
    )
    p_serve.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help="蒸馏产物根目录（默认 ./data/skills 或 .env 的 OUTPUT_DIR）",
    )
    p_serve.add_argument(
        "--reload",
        action="store_true",
        help="开发模式热重载（uvicorn reload）",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "info":
        return _run_info()

    if args.command == "capture":
        return _run_capture(args)

    if args.command == "serve":
        return _run_serve(args)

    if args.command == "distill":
        trace_paths: list[Path] = list(args.trace)
        for tp in trace_paths:
            if not tp.is_file():
                print(f"错误：trace 文件不存在：{tp}", file=sys.stderr)
                return 2

        output_dir = args.output or config.OUTPUT_DIR
        adapter_name = args.adapter or config.DEFAULT_ADAPTER

        return _run_distill(
            trace_paths,
            output_dir,
            adapter_name,
            args.no_llm,
            fresh=args.fresh,
            task_description=args.task,
        )

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
