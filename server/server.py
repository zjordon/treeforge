"""FastAPI 常驻服务（P3 核心）。

把「一次性 capture 命令」的采集 router 迁移到 FastAPI，并新增蒸馏 / 配置 / 状态 / 产物 router，
外加控制面板 SPA 托管。一个进程既收扩展事件（采集），又接受蒸馏触发（API）。

【采集 router 协议】与 ``treeforge/capture/backend.py``（aiohttp）逐字一致，
扩展零改动（只认 ``/start /ingest /stop /health`` + DEFAULT_ENDPOINT=http://127.0.0.1:8765）。
aiohttp 的 ``web.Application`` 非 ASGI，不能挂 FastAPI 下，所以 4 端点用 Pydantic 模型重写。

【Chrome 缺席策略】app 启动时不连 CDP；``fetch_ws_url`` 失败不报错，懒到首次 ``/start``
才建 CdpSession。``/start`` 时 Chrome 没连上 → CdpSession.start 抛错 → 503。
蒸馏 / 配置 / 状态 API 与 Chrome 无关，照常可用。

用法（见 ``treeforge/serve.py``）：
    app = create_app(...)
    uvicorn.run(app, host="127.0.0.1", port=8765)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from harness import config
from server import distill_api
from treeforge.capture.cdp_session import CdpSession
from treeforge.capture.collector import Collector
from treeforge.capture.ws_discover import DEFAULT_CDP_HOST, DEFAULT_CDP_PORT, fetch_ws_url

logger = logging.getLogger(__name__)

# SPA 静态资源目录（相对仓库根）。不存在时优雅跳过挂载（不阻断 API）。
_SPA_DIR = Path(__file__).resolve().parent / "app" / "dist"

# .env 可写的 key 白名单（POST /api/config 用），防止任意改写敏感项。
_CONFIG_WRITABLE = {"DISTILL_MODEL", "CLASSIFY_MODEL", "LLM_BASE", "LLM_TIMEOUT"}


# ---------------------------------------------------------------------------
# Pydantic 请求模型（采集 router）
# ---------------------------------------------------------------------------


class StartRequest(BaseModel):
    """POST /start body（对齐 aiohttp backend._handle_start）。"""

    scenario: str = "distill"
    config: dict[str, Any] | None = None


class EnvelopeModel(BaseModel):
    """POST /ingest body（通用 CaptureEnvelope，对齐扩展 shared/envelope.ts）。

    字段含义见 ``treeforge/capture/backend.py`` 模块 docstring。
    """

    scenario: str = "distill"
    session_id: str = ""
    ts: int = 0
    url: str | None = None
    tab_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DistillRequest(BaseModel):
    """POST /api/distill body（触发蒸馏）。"""

    trace_path: str
    output_dir: str | None = None
    adapter: str = "treewalker"
    no_llm: bool = False


class ConfigUpdate(BaseModel):
    """POST /api/config body（改配置，写 .env）。

    只接受白名单 key（DISTILL_MODEL/CLASSIFY_MODEL/LLM_BASE/LLM_TIMEOUT）。
    """

    values: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# app 工厂
# ---------------------------------------------------------------------------


def create_app(
    cdp_host: str = DEFAULT_CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
    captures_dir: Path | str = "./data/captures",
    skills_dir: Path | str | None = None,
) -> FastAPI:
    """建 FastAPI app，注册全部 router。

    Args:
        cdp_host/cdp_port: Chrome 远程调试地址（Chrome 缺席时不报错，懒到 /start 才连）
        captures_dir: 采集产物根目录（GET /api/captures 列这里）
        skills_dir: 蒸馏产物根目录（默认 config.OUTPUT_DIR；GET /api/skills 列这里）
    """
    skills_dir_resolved = Path(skills_dir) if skills_dir else config.OUTPUT_DIR
    app = FastAPI(title="TreeForge Serve", version="0.1.0")
    app.state.cdp_host = cdp_host
    app.state.cdp_port = cdp_port
    app.state.captures_dir = Path(captures_dir)
    app.state.skills_dir = Path(skills_dir_resolved)
    # 单例 Collector 懒建：首次 /start 才建（Chrome 可能没开，懒到那时才连）
    app.state.collector = None

    _register_capture_router(app)
    _register_distill_router(app)
    _register_config_router(app)
    _register_status_router(app)
    _mount_spa(app)

    return app


# ---------------------------------------------------------------------------
# 采集 router（/start /ingest /stop /health，协议对齐 aiohttp backend）
# ---------------------------------------------------------------------------


def _register_capture_router(app: FastAPI) -> None:
    @app.post("/start")
    async def start(req: StartRequest) -> JSONResponse:
        if req.scenario not in ("distill", "replay"):
            return JSONResponse(
                {"ok": False, "error": f"unknown scenario: {req.scenario}"}, status_code=400
            )
        try:
            collector = await _get_collector(app)
            session_id = await collector.start(scenario=req.scenario, config=req.config)
            logger.info("Capture started: scenario=%s session=%s", req.scenario, session_id)
            return JSONResponse({"ok": True, "session_id": session_id})
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Start failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/ingest")
    async def ingest(env: EnvelopeModel) -> JSONResponse:
        scenario = env.scenario
        if scenario not in ("distill", "replay"):
            return JSONResponse(
                {"ok": False, "error": f"unknown scenario: {scenario}"}, status_code=400
            )
        if scenario == "replay":
            logger.debug("replay ingest (not implemented yet): %s", env.ts)
            return JSONResponse({"ok": True, "note": "replay not implemented"})
        try:
            collector = app.state.collector
            if collector is None:
                return JSONResponse(
                    {"ok": False, "error": "no active session (POST /start first)"},
                    status_code=400,
                )
            await collector.ingest(env.model_dump())
            return JSONResponse({"ok": True})
        except Exception as e:  # noqa: BLE001
            logger.exception("Ingest failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/stop")
    async def stop() -> JSONResponse:
        try:
            collector = app.state.collector
            if collector is None:
                return JSONResponse({"ok": False, "error": "no active session"}, status_code=400)
            result = await collector.stop()
            logger.info("Capture stopped: %s", result)
            # serve 常驻：不设 stop_event、不退出进程；session 可循环（下次 /start 重建）
            return JSONResponse({"ok": True, "result": _safe_result(result)})
        except Exception as e:  # noqa: BLE001
            logger.exception("Stop failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}


async def _get_collector(app: FastAPI) -> Collector:
    """懒建单例 Collector：首次 /start 时 fetch_ws_url + 建 CdpSession + Collector。

    Chrome 缺席（fetch_ws_url 返 None）→ 抛 HTTPException(503)，由 handler 转 503 响应。
    已建过则直接返回（app.state.collector）。
    """
    if app.state.collector is not None:
        return app.state.collector

    ws_url = fetch_ws_url(app.state.cdp_host, app.state.cdp_port)
    if not ws_url:
        # Chrome 没开：照常启动服务（蒸馏/配置/状态可用），但采集不可用
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": (
                    f"Chrome 未连接（{app.state.cdp_host}:{app.state.cdp_port}）。"
                    f"请以 --remote-debugging-port={app.state.cdp_port} 启动 Chrome 后重试。"
                ),
            },
        )

    cdp = CdpSession(ws_url)
    Path(app.state.captures_dir).mkdir(parents=True, exist_ok=True)
    collector = Collector(cdp_session=cdp, output_dir=str(app.state.captures_dir))
    app.state.collector = collector
    return collector


def _safe_result(result: Any) -> Any:
    """把 collector.stop() 的返回值转成 JSON 可序列化的形式（Path 等转 str）。"""
    if isinstance(result, (str, int, float, bool, type(None))):
        return result
    if isinstance(result, dict):
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in result.items()}
    return str(result)


# ---------------------------------------------------------------------------
# 蒸馏 router（/api/distill 系列）
# ---------------------------------------------------------------------------


def _register_distill_router(app: FastAPI) -> None:
    @app.post("/api/distill")
    async def distill_start(req: DistillRequest) -> JSONResponse:
        trace_path = Path(req.trace_path)
        if not trace_path.is_file():
            return JSONResponse(
                {"ok": False, "error": f"trace 文件不存在：{req.trace_path}"}, status_code=400
            )
        output_dir = Path(req.output_dir) if req.output_dir else config.OUTPUT_DIR
        try:
            job_id = await distill_api.start_distill_job(
                trace_path=trace_path,
                output_dir=output_dir,
                adapter_name=req.adapter,
                no_llm=req.no_llm,
            )
            return JSONResponse({"ok": True, "job_id": job_id})
        except Exception as e:  # noqa: BLE001
            logger.exception("distill start failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/distill/{job_id}")
    async def distill_status(job_id: str) -> JSONResponse:
        job = distill_api.get_job(job_id)
        if job is None:
            return JSONResponse({"ok": False, "error": "job 不存在"}, status_code=404)
        return JSONResponse({"ok": True, "job": job.to_dict()})

    @app.get("/api/jobs")
    async def jobs() -> JSONResponse:
        return JSONResponse({"ok": True, "jobs": [j.to_dict() for j in distill_api.list_jobs()]})


# ---------------------------------------------------------------------------
# 配置 router（GET/POST /api/config）
# ---------------------------------------------------------------------------


def _register_config_router(app: FastAPI) -> None:
    @app.get("/api/config")
    async def get_config() -> JSONResponse:
        return JSONResponse({"ok": True, "config": config.describe()})

    @app.post("/api/config")
    async def update_config(req: ConfigUpdate) -> JSONResponse:
        # 白名单校验
        bad = set(req.values) - _CONFIG_WRITABLE
        if bad:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"不可写的 key：{sorted(bad)}（允许：{sorted(_CONFIG_WRITABLE)}）",
                },
                status_code=400,
            )
        try:
            _write_env(req.values)
            config.load()  # 重载生效
            return JSONResponse({"ok": True, "config": config.describe()})
        except Exception as e:  # noqa: BLE001
            logger.exception("config update failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/config/check")
    async def config_check() -> JSONResponse:
        """LLM 连通性自检：调 call_llm_fast 发一个最小请求验证 key/base/model 可用。

        用户在控制面板主动点「LLM 自检」触发（非自动化）。LLM 是同步 urllib，
        用 asyncio.to_thread 包，不阻塞事件循环（对齐 distill_api 模式）。
        成功返 {ok,model,reply_len,usage}；失败返 {ok:false,model,error}（不 500，便于前端展示）。
        """
        import asyncio

        from harness.llm import call_llm_fast

        if not config.LLM_KEY:
            return JSONResponse(
                {"ok": False, "model": config.CLASSIFY_MODEL, "error": "LLM_KEY 未配置"}
            )
        try:
            text, usage = await asyncio.to_thread(call_llm_fast, "ping", max_tokens=8)
            return JSONResponse(
                {
                    "ok": True,
                    "model": config.CLASSIFY_MODEL,
                    "reply_len": len(text),
                    "usage": usage,
                }
            )
        except Exception as e:  # noqa: BLE001 - 自检失败记进 error，不 500（前端友好）
            logger.warning("LLM self-check failed: %s", e)
            return JSONResponse({"ok": False, "model": config.CLASSIFY_MODEL, "error": str(e)})


def _write_env(updates: dict[str, str]) -> None:
    """原子写 .env（保留已有行，更新/追加白名单 key）。

    对齐 harness/install.atomic_write_text 的 tmp + os.replace 模式（Windows WinError 183）。
    """
    env_path = config.REPO_ROOT / ".env"
    existing: dict[str, str] = {}
    lines: list[str] = []
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            key, _, val = stripped.partition("=")
            existing[key.strip()] = val.strip()
            # 用占位标记，后续按 key 决定是否覆盖
            lines.append(line)

    # 合并：更新已有 key 的行 + 追加新 key
    written_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                written_keys.add(key)
                continue
        new_lines.append(line)
    for key, val in updates.items():
        if key not in written_keys:
            new_lines.append(f"{key}={val}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = env_path.with_suffix(env_path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.replace(tmp, env_path)


# ---------------------------------------------------------------------------
# 状态/产物 router（/api/status /api/captures /api/skills）
# ---------------------------------------------------------------------------


def _register_status_router(app: FastAPI) -> None:
    @app.get("/api/status")
    async def status() -> JSONResponse:
        collector = app.state.collector
        session_obj = getattr(collector, "_session", None) if collector is not None else None
        recording = session_obj is not None
        out: dict[str, Any] = {
            "recording": recording,
            "chrome_connected": collector is not None,
            "captures_dir": str(app.state.captures_dir),
            "skills_dir": str(app.state.skills_dir),
        }
        # 录制中时附带 session 详情（session_id / 事件数 / stages / 当前 stage / host）
        if recording:
            events = getattr(session_obj, "events", []) or []
            page_context = getattr(session_obj, "page_context", {}) or {}
            out["session"] = {
                "session_id": getattr(session_obj, "session_id", ""),
                "host": getattr(session_obj, "host", ""),
                "task_instruction": getattr(session_obj, "task_instruction", ""),
                "events": len(events),
                "stages": list(page_context.keys()),
                "current_stage": events[-1].stage if events else None,
            }
        return JSONResponse({"ok": True, "status": out})

    @app.get("/api/captures")
    async def captures() -> JSONResponse:
        """列采集产物：每个含 name / md_count / mtime（创建时间，ISO + 毫秒戳）。

        mtime 取 trace.json 的（导出落盘时刻，最准），不存在时退到目录 mtime。
        前端用 mtime 显示创建时间 + 按新→旧排序，让用户一眼看到最新产物。
        """
        from datetime import UTC, datetime

        caps_root = Path(app.state.captures_dir)
        items: list[dict[str, Any]] = []
        if caps_root.is_dir():
            for child in sorted(caps_root.iterdir()):
                if not child.is_dir():
                    continue
                # mtime 优先 trace.json（精确导出时刻），否则目录本身
                trace_path = child / "trace.json"
                ref = trace_path if trace_path.is_file() else child
                # st_mtime 是秒级浮点，前端 new Date(ms) 要毫秒 → 乘 1000
                mtime = ref.stat().st_mtime
                items.append(
                    {
                        "name": child.name,
                        "md_count": sum(1 for _ in child.glob("*.md")),
                        "mtime_ms": int(mtime * 1000),  # 毫秒戳（前端 new Date 用）
                        "mtime_iso": datetime.fromtimestamp(mtime, UTC).isoformat(),
                    }
                )
        # 按创建时间倒序（最新在前）
        items.sort(key=lambda x: x["mtime_ms"], reverse=True)
        return JSONResponse(
            {"ok": True, "captures_dir": str(app.state.captures_dir), "items": items}
        )

    @app.get("/api/captures/{name}")
    async def capture_detail(name: str) -> JSONResponse:
        """单个 capture 详情：读 <captures_dir>/<name>/trace.json 摘要 + snapshots 列表。"""
        cap_dir = Path(app.state.captures_dir) / name
        trace_path = cap_dir / "trace.json"
        if not trace_path.is_file():
            return JSONResponse({"ok": False, "error": f"capture 不存在：{name}"}, status_code=404)
        import json

        try:
            data = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return JSONResponse(
                {"ok": False, "error": f"trace.json 解析失败：{e}"}, status_code=500
            )
        events = data.get("events", []) or []
        page_context = data.get("page_context", {}) or {}
        # snapshots/ 下的 .txt 文件名
        snaps_dir = cap_dir / "snapshots"
        snapshots = sorted(p.name for p in snaps_dir.glob("*.txt")) if snaps_dir.is_dir() else []
        return JSONResponse(
            {
                "ok": True,
                "name": name,
                "trace_path": str(trace_path),
                "host": data.get("host", ""),
                "task_instruction": data.get("task_instruction", ""),
                "events": len(events),
                "stages": list(page_context.keys()),
                "snapshots": snapshots,
            }
        )

    @app.get("/api/skills")
    async def skills() -> JSONResponse:
        # skills_dir/domain-skills/<host>/
        skills_root = app.state.skills_dir / "domain-skills"
        hosts = _list_subdirs(skills_root) if skills_root.is_dir() else []
        return JSONResponse({"ok": True, "skills_dir": str(app.state.skills_dir), "hosts": hosts})

    @app.get("/api/skills/{host}/files")
    async def skill_files(host: str) -> JSONResponse:
        """列 <skills_dir>/domain-skills/<host>/ 下的 md 文件（名 + 大小）。"""
        host_dir = Path(app.state.skills_dir) / "domain-skills" / host
        if not host_dir.is_dir():
            return JSONResponse({"ok": False, "error": f"host 不存在：{host}"}, status_code=404)
        files = [{"name": p.name, "size": p.stat().st_size} for p in sorted(host_dir.glob("*.md"))]
        return JSONResponse({"ok": True, "host": host, "files": files})

    @app.get("/api/skills/{host}/files/{filename}")
    async def skill_file_content(host: str, filename: str) -> JSONResponse:
        """返回某 md 文件原文（前端预览用）。路径越界防护：filename 不含分隔符。"""
        import re

        if not re.fullmatch(r"[^/\\]+\.md", filename):
            return JSONResponse({"ok": False, "error": f"非法文件名：{filename}"}, status_code=400)
        fpath = Path(app.state.skills_dir) / "domain-skills" / host / filename
        if not fpath.is_file():
            return JSONResponse(
                {"ok": False, "error": f"文件不存在：{host}/{filename}"}, status_code=404
            )
        return JSONResponse(
            {
                "ok": True,
                "host": host,
                "filename": filename,
                "content": fpath.read_text(encoding="utf-8"),
            }
        )


def _list_subdirs(root: Path | str) -> list[dict[str, Any]]:
    """列目录下的子目录（名字 + 每个子目录的 .md 文件数）。"""
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    items = []
    for child in sorted(root_path.iterdir()):
        if child.is_dir():
            md_count = sum(1 for _ in child.glob("*.md"))
            items.append({"name": child.name, "md_count": md_count})
    return items


# ---------------------------------------------------------------------------
# 控制面板 SPA 托管（StaticFiles，目录不存在时优雅跳过）
# ---------------------------------------------------------------------------


def _mount_spa(app: FastAPI) -> None:
    if _SPA_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_SPA_DIR), html=True), name="spa")
        logger.info("SPA mounted at %s", _SPA_DIR)
    else:
        logger.info("SPA dir %s 不存在，跳过挂载（API 仍可用）", _SPA_DIR)
