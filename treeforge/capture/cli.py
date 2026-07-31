"""capture 子命令运行逻辑：起 CdpSession + Collector + CaptureBackend，阻塞跑。

把采集层各组件串联成一个可运行的命令：
  1. 发现 Chrome ws_url（--remote-debugging-port）
  2. 起 CdpSession（连 CDP）
  3. 起 Collector（依赖注入 CdpSession）
  4. 起 CaptureBackend（依赖注入 Collector）
  5. 阻塞跑 backend（收扩展事件），直到用户 Ctrl+C
  6. 停止时 export 产物（trace + 快照）

用法：uv run treeforge capture --task "..." --host member.bilibili.com
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from treeforge.capture.backend import DEFAULT_HOST, DEFAULT_PORT, CaptureBackend
from treeforge.capture.cdp_session import CdpSession
from treeforge.capture.collector import Collector
from treeforge.capture.ws_discover import DEFAULT_CDP_HOST, DEFAULT_CDP_PORT, fetch_ws_url

logger = logging.getLogger(__name__)


async def run_capture(
    output_dir: Path,
    task: str = "",
    host: str = "",
    cdp_host: str = DEFAULT_CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
    backend_host: str = DEFAULT_HOST,
    backend_port: int = DEFAULT_PORT,
    stage_threshold: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> int:
    """跑采集会话，直到收到停止信号（扩展 popup 点停止 或 Ctrl+C）。

    stop_event: 外部传入的停止信号（由 _run_capture 的 signal handler 设置）。
                None 时内部创建（仅靠扩展 /stop 停止，Ctrl+C 不可控）。

    Returns:
        退出码（0 成功，1 错误）。
    """
    # ① 发现 Chrome ws_url
    ws_url = fetch_ws_url(cdp_host, cdp_port)
    if not ws_url:
        print(
            f"错误：无法连接 Chrome（{cdp_host}:{cdp_port}）。\n"
            f"请以远程调试端口启动 Chrome：\n"
            f"  chrome --remote-debugging-port={cdp_port} --user-data-dir=<profile>",
            flush=True,
        )
        return 1

    # ② 起 CdpSession
    cdp = CdpSession(ws_url)

    # ③ 起 Collector
    output_dir.mkdir(parents=True, exist_ok=True)
    collector = Collector(
        cdp_session=cdp,
        output_dir=str(output_dir),
        stage_threshold=stage_threshold,
    )

    # ⑤ 阻塞直到停止信号（扩展 /stop 或 Ctrl+C）
    # stop_event 由 _run_capture 的 signal handler 设置（Ctrl+C 时）或 backend 的 /stop 设置。
    if stop_event is None:
        stop_event = asyncio.Event()

    # ④ 起 Backend（on_stop 回调：扩展点「停止」时设置 stop_event，让本协程退出）
    backend = CaptureBackend(
        collector,
        host=backend_host,
        port=backend_port,
        on_stop=stop_event.set,
    )
    app = backend.make_app()

    from aiohttp import web

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, backend_host, backend_port)

    print(f"[CAPTURE] 后端监听 http://{backend_host}:{backend_port}", flush=True)
    print("[CAPTURE] 等 Chrome 扩展连接（popup 点「开始录制」）...", flush=True)
    print("[CAPTURE] 扩展点「停止」会自动导出；Ctrl+C 兜底导出", flush=True)
    print(flush=True)

    await site.start()

    # Unix: 尝试 add_signal_handler（更优雅）；Windows 不支持，靠 _run_capture 的 signal.signal
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    except (NotImplementedError, RuntimeError):
        pass  # Windows：由 _run_capture 的 signal.signal(handler) 设置 stop_event

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # 兜底（Windows Ctrl+C 残留的取消信号）

    # ⑥ 停止 + 导出
    return await _stop_and_export(collector, cdp, runner, output_dir)


async def _stop_and_export(
    collector: Collector,
    cdp: CdpSession,
    runner: object,
    output_dir: Path,
) -> int:
    """停止采集 + 导出产物 + 清理资源。

    两种触发路径：
    - 扩展点「停止」（主路径）：backend._handle_stop 已调 collector.stop() 完成导出 + 断开 CDP，
      这里只清理 runner（不重复导出）。
    - Ctrl+C（兜底）：collector.stop() 未被调过，这里调它保底导出 + 清理。
    用 collector._started 判断（stop() 会置 False）。
    """
    # 兜底：Ctrl+C 时 collector.stop() 未被调过，这里调
    if collector._started:
        print("\n[CAPTURE] Ctrl+C 兜底导出...", flush=True)
        result = await collector.stop()
        capture_dir = result.get("capture_dir")
        if capture_dir:
            print(f"[CAPTURE] 已导出：{capture_dir}", flush=True)
            print(f"  events: {result.get('events')}", flush=True)
            print(f"  stages: {result.get('stages')}", flush=True)
            print(f"  trace:  {result.get('trace_path')}", flush=True)
            print(flush=True)
            print("可蒸馏：", flush=True)
            print(f"  uv run treeforge distill {result.get('trace_path')} --output ./data/skills", flush=True)
        else:
            print("[CAPTURE] 无事件采集（未录制或扩展未连接）", flush=True)
    else:
        # 主路径：扩展已调 collector.stop()，导出已完成。只清理 runner。
        session = collector.session
        if session and session.events:
            print(f"\n[CAPTURE] 扩展已停止，产物已导出到 {collector.output_dir}", flush=True)

    # 清理 backend runner（CDP 已由 collector.stop() 断开，兜底路径也已断开）
    try:
        await runner.cleanup()  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        logger.debug("runner cleanup failed: %s", e)

    return 0
