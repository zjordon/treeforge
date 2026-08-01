"""蒸馏后台任务管理（P3）。

把 CLI 的 ``_run_distill`` 提炼成「去 CLI 味」的可复用管线函数，并在此基础上提供
HTTP 可触发的后台任务（job dict + 进度注入 + 串行锁）。

【设计要点】
  - ``run_distill_pipeline`` 是同步函数（内部 LLM 走 urllib），handler 用
    ``asyncio.to_thread`` 包它丢后台线程，不阻塞事件循环。
  - 全局 ``_PIPELINE_LOCK`` 串行化蒸馏（同时只跑一个，防 LLM 配额/状态串）。
  - 进度通过 ``harness.progress.set_reporter`` 注入 job dict（progress.py 已预留接口），
    前端轮询 ``GET /api/distill/{job_id}`` 看实时进度。
  - job 状态只在内存（重启丢失，P3 不做持久化，见 serve-plan §七）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters import get_adapter
from harness import atomizer, bucketer, classifier, config, distiller, install, progress
from harness.models import SkillCard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 蒸馏管线（从 treeforge.__main__._run_distill 提炼，去 CLI 味）
# ---------------------------------------------------------------------------


@dataclass
class DistillResult:
    """蒸馏管线产物（run_distill_pipeline 的返回值，去 CLI 味：不返退出码、不 print）。"""

    ok: bool
    written: list[Path] = field(default_factory=list)
    host_dir: Path | None = None
    cards_count: int = 0
    error: str | None = None
    trace_path: Path = field(default_factory=Path)


def run_distill_pipeline(
    trace_path: Path,
    output_dir: Path,
    adapter_name: str = "treewalker",
    no_llm: bool = False,
) -> DistillResult:
    """跑完整蒸馏链路（ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL → INSTALL）。

    与 ``treeforge.__main__._run_distill`` 等价，但不 print、不返退出码，
    返回 :class:`DistillResult`，供 CLI 薄包装和 HTTP 后台任务共用。

    失败时 ``ok=False`` + ``error`` 填原因（不抛异常，便于 job dict 记录）。
    """
    config.load()  # 刷新 .env（幂等）

    use_llm = (not no_llm) and bool(config.LLM_KEY)
    if not no_llm and not config.LLM_KEY:
        progress.report(
            "DISTILL",
            detail="LLM_KEY 未配置，自动退回模板模式（产物质量低，仅供链路验证）",
        )

    try:
        # ① ADAPT
        from harness import adapter as adapt_mod

        trace = adapt_mod.load_trace(trace_path)

        # ② ATOMIZE
        segments = atomizer.atomize(trace)
        if not segments:
            return DistillResult(ok=False, error="无 segment，退出", trace_path=trace_path)

        # ③ CLASSIFY
        classified = classifier.classify(segments, use_llm=use_llm)

        # ④ BUCKET
        buckets = bucketer.bucket(classified)
        if not buckets:
            return DistillResult(ok=False, error="无 bucket，退出", trace_path=trace_path)

        # ⑤ DISTILL（透传 trace 级 page_context，让 LLM 能看到 DOM 快照推 quirks）
        cards: list[SkillCard] = distiller.distill_buckets(
            buckets, use_llm=use_llm, page_context=trace.page_context
        )
        if not cards:
            return DistillResult(ok=False, error="无 card 产出，退出", trace_path=trace_path)

        # INSTALL
        adp = get_adapter(adapter_name)
        written = install.install_cards(cards, output_dir, adp)

        progress.report("DONE", detail=f"wrote {len(written)} files to {output_dir}")

        host_dir = None
        if adapter_name == "treewalker" and cards:
            host_dir = output_dir / "domain-skills" / cards[0].domain

        return DistillResult(
            ok=True,
            written=written,
            host_dir=host_dir,
            cards_count=len(cards),
            trace_path=trace_path,
        )
    except Exception as e:  # noqa: BLE001 - 管线失败记进 error，不抛（job dict 用）
        logger.exception("distill pipeline failed")
        return DistillResult(ok=False, error=f"{type(e).__name__}: {e}", trace_path=trace_path)


# ---------------------------------------------------------------------------
# 后台任务（job dict + 进度注入 + 串行锁）
# ---------------------------------------------------------------------------

_VALID_STATUSES = ("pending", "running", "done", "failed")


@dataclass
class JobStatus:
    """一个蒸馏任务的实时状态（前端轮询 GET /api/distill/{job_id} 读这个）。"""

    job_id: str
    status: str  # pending / running / done / failed
    phase: str = ""
    current: int = 0
    total: int = 0
    detail: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # written 是 Path 列表，转 str
        if self.result and "written" in self.result:
            d["result"]["written"] = [str(p) for p in self.result["written"]]
        if self.result and self.result.get("host_dir"):
            d["result"]["host_dir"] = str(self.result["host_dir"])
        if self.result and self.result.get("trace_path"):
            d["result"]["trace_path"] = str(self.result["trace_path"])
        return d


# 全局 job 存储（内存，重启丢失——P3 范围，见 serve-plan §七）
_jobs: dict[str, JobStatus] = {}

# 串行化蒸馏（同时只跑一个，防 LLM 配额/状态串）
_PIPELINE_LOCK: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """惰性创建锁（事件循环启动后才创建，避免模块导入时无 loop）。"""
    global _PIPELINE_LOCK
    if _PIPELINE_LOCK is None:
        _PIPELINE_LOCK = asyncio.Lock()
    return _PIPELINE_LOCK


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_job(job_id: str) -> JobStatus | None:
    """查一个蒸馏任务。"""
    return _jobs.get(job_id)


def list_jobs() -> list[JobStatus]:
    """列所有蒸馏任务（按启动时间倒序）。"""
    return sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)


async def start_distill_job(
    trace_path: Path,
    output_dir: Path,
    adapter_name: str = "treewalker",
    no_llm: bool = False,
) -> str:
    """触发一个蒸馏后台任务，立即返回 job_id（不阻塞）。

    实际蒸馏在 ``_run_job`` 里跑：拿 ``_PIPELINE_LOCK`` + ``asyncio.to_thread`` 包同步管线。
    进度通过 ``progress.set_reporter`` 注入 job dict，跑完恢复原 reporter。
    """
    job_id = uuid.uuid4().hex[:12]
    job = JobStatus(
        job_id=job_id,
        status="pending",
        started_at=_now_iso(),
    )
    _jobs[job_id] = job

    asyncio.create_task(_run_job(job_id, trace_path, output_dir, adapter_name, no_llm))
    return job_id


async def _run_job(
    job_id: str,
    trace_path: Path,
    output_dir: Path,
    adapter_name: str,
    no_llm: bool,
) -> None:
    """后台跑蒸馏（串行 + 进度注入）。"""
    job = _jobs[job_id]
    job.status = "running"

    # 注入 reporter：进度实时写 job dict（progress.py:22 预留的接口）
    def _reporter(phase: str, current: int, total: int, detail: str) -> None:
        job.phase = phase
        job.current = current
        job.total = total
        job.detail = detail

    prev_reporter = progress.get_reporter()
    progress.set_reporter(_reporter)

    try:
        async with _get_lock():
            result = await asyncio.to_thread(
                run_distill_pipeline, trace_path, output_dir, adapter_name, no_llm
            )
        if result.ok:
            job.status = "done"
            job.result = {
                "written": result.written,
                "host_dir": result.host_dir,
                "cards_count": result.cards_count,
                "trace_path": result.trace_path,
            }
        else:
            job.status = "failed"
            job.error = result.error
    except Exception as e:  # noqa: BLE001
        logger.exception("distill job %s crashed", job_id)
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
    finally:
        progress.set_reporter(prev_reporter)
        job.finished_at = _now_iso()
