"""aiohttp HTTP 后端：收 Chrome 扩展事件，路由到采集器。

【设计依据】docs/p2/README.md 3.2.5 节协议设计（通用 CaptureEnvelope + scenario 路由）。
协议参照 TreeWalker recording_extension/shared/types.ts + backend.ts 的 5 端点，
但用通用 envelope + scenario 标记替代 TreeWalker 的硬编码 /event /signal。

扩展端 POST 的 CaptureEnvelope 结构（与扩展 shared/envelope.ts 对齐）：
    {
      "scenario": "distill" | "replay",   # 后端按此路由
      "session_id": "...",
      "ts": 1234567890,
      "url": "...",
      "is_top_frame": true,
      "payload": { ... }                   # 场景特定 schema（策略产出）
    }

P2.2.1 阶段：只实现骨架（收事件 + scenario 路由 + 日志打印）。
  Collector（P2.2.2）通过依赖注入接入，backend 不直接 import collector，便于测试。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from aiohttp import web

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class CaptureEnvelope:  # noqa: D101 - 文档见模块 docstring
    """通用采集信封（所有场景共用）。

    字段含义见模块 docstring。此处只做类型注释，实际解析在 handler 里用 dict。
    """

    scenario: str
    session_id: str
    ts: int
    payload: dict[str, Any]


class CollectorLike(Protocol):
    """采集器协议：backend 依赖注入的接口。

    Collector（P2.2.2 实现）需满足此协议。backend 通过依赖注入接收 collector，
    不直接 import，便于测试 mock。
    """

    async def start(self, scenario: str, config: dict | None = None) -> str:
        """开始采集会话，返回 session_id。"""
        ...

    async def ingest(self, envelope: dict[str, Any]) -> None:
        """处理一个采集信封（按 scenario 已路由）。"""
        ...

    async def stop(self) -> Any:
        """停止采集，返回产物信息（如输出路径）。"""
        ...


class CaptureBackend:
    """采集 HTTP 后端：收扩展事件，路由到 collector。

    用法：
        backend = CaptureBackend(collector)
        await backend.run()  # 阻塞跑 aiohttp
    """

    def __init__(self, collector: CollectorLike, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.collector = collector
        self.host = host
        self.port = port
        self._current_scenario: str | None = None

    def make_app(self) -> web.Application:
        """建 aiohttp app，注册路由。

        端点（对齐扩展 backend.ts 调用 + 通用 /ingest）：
        - POST /start { scenario, config }    开始采集
        - POST /ingest { CaptureEnvelope }    通用事件入口（按 envelope.scenario 路由）
        - POST /stop                          停止采集，返产物
        - GET  /health                        健康检查
        """
        app = web.Application()
        app.router.add_post("/start", self._handle_start)
        app.router.add_post("/ingest", self._handle_ingest)
        app.router.add_post("/stop", self._handle_stop)
        app.router.add_get("/health", self._handle_health)
        return app

    async def run(self) -> None:
        """阻塞跑 aiohttp server（供 CLI 调用）。"""
        app = self.make_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        logger.info("CaptureBackend listening on http://%s:%s", self.host, self.port)
        try:
            await site.start()
            # 阻塞直到被取消（CLI 负责事件循环生命周期）
            import asyncio

            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    # ---- handlers ----

    async def _handle_start(self, request: web.Request) -> web.Response:
        """POST /start { scenario, config } → { ok, session_id }"""
        body = await request.json()
        scenario = body.get("scenario", "distill")
        config = body.get("config")
        self._current_scenario = scenario
        try:
            session_id = await self.collector.start(scenario=scenario, config=config)
            logger.info("Capture started: scenario=%s session=%s", scenario, session_id)
            return web.json_response({"ok": True, "session_id": session_id})
        except Exception as e:  # noqa: BLE001
            logger.exception("Start failed")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_ingest(self, request: web.Request) -> web.Response:
        """POST /ingest { CaptureEnvelope } → { ok }

        按 envelope.scenario 路由。当前只支持 distill；replay 留接口（TreeWalker 迁入时实现）。
        """
        envelope = await request.json()
        scenario = envelope.get("scenario", self._current_scenario or "distill")

        if scenario not in ("distill", "replay"):
            return web.json_response(
                {"ok": False, "error": f"unknown scenario: {scenario}"}, status=400
            )

        if scenario == "replay":
            # replay 路径留接口（TreeWalker 迁入时实现）
            logger.debug("replay ingest (not implemented yet): %s", envelope.get("ts"))
            return web.json_response({"ok": True, "note": "replay not implemented"})

        try:
            await self.collector.ingest(envelope)
            return web.json_response({"ok": True})
        except Exception as e:  # noqa: BLE001
            logger.exception("Ingest failed")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_stop(self, request: web.Request) -> web.Response:
        """POST /stop → { ok, result }"""
        try:
            result = await self.collector.stop()
            logger.info("Capture stopped: %s", result)
            return web.json_response({"ok": True, "result": _safe_result(result)})
        except Exception as e:  # noqa: BLE001
            logger.exception("Stop failed")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health → { ok: true }"""
        return web.json_response({"ok": True})


def _safe_result(result: Any) -> Any:
    """把 stop() 的返回值转成 JSON 可序列化的形式。"""
    if isinstance(result, (str, int, float, bool, type(None))):
        return result
    if isinstance(result, dict):
        return result
    # Path 等对象转 str
    return str(result)
