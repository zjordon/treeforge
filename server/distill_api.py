"""蒸馏后台任务管理（P3 起，P4 扩多 trace / 增量 / 双产物）。

把 CLI 的 ``_run_distill`` 提炼成「去 CLI 味」的可复用管线函数，并在此基础上提供
HTTP 可触发的后台任务（job dict + 进度注入 + 串行锁）。

【P4 要点】
  - 多 trace 输入：同 host 多任务累积蒸馏（stage 冲突重映射 ``stage@N``）。
  - registry（host 卡持久化）：LLM 模式且非 fresh 时，BUCKET 后按 host 懒加载旧卡
    （增量蒸馏 prev_card），DISTILL 后落新卡。模板模式不累积（决策 4）。
  - 双产物两跳：跳 A 站点级（distill_buckets → host 三件套）+ 跳 B 任务级
    （distill_task → tasks/<slug>/，slug 稳定化 + 覆盖合并）。

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
from adapters.treewalker_adapter import list_task_cards, write_task_card
from harness import atomizer, bucketer, classifier, config, distiller, install, progress, registry
from harness.models import SkillCard, Trace

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
    # 兼容字段：首个 trace（老测试 / job dict 引用）
    trace_path: Path = field(default_factory=Path)
    # P4：全部输入 trace（多 trace 累积蒸馏）
    trace_paths: list[Path] = field(default_factory=list)
    # P4 跳 B：任务卡落点与 slug（无任务卡时 None）
    task_dir: Path | None = None
    task_slug: str | None = None


def _remap_stage_conflicts(traces: list[Trace]) -> None:
    """多 trace 的 page_context stage 同名冲突 → 第 N≥2 个改名 ``stage@N`` 并重映射事件。

    ADAPT 后、ATOMIZE 前调用。stage 是 evidence（事件指向快照的 key）：不同任务的
    stage 同名（如两次都有 ``upload``）会让 trace2 的事件指到 trace1 的快照——
    宁可名字丑，不可指错快照。就地修改传入的 traces。
    """
    used: set[str] = set()
    for n, tr in enumerate(traces):
        renames: dict[str, str] = {}
        new_pc: dict[str, str] = {}
        for k, v in (tr.page_context or {}).items():
            key = k
            if n > 0 and key in used:
                nk = f"{key}@{n + 1}"
                while nk in used or nk in new_pc:
                    nk += "x"
                renames[k] = nk
                key = nk
            new_pc[key] = v
            used.add(key)
        if renames:
            tr.page_context = new_pc
            for ev in tr.events:
                if ev.stage in renames:
                    ev.stage = renames[ev.stage]


def _save_cards_to_registry(
    output_dir: Path, cards: list[SkillCard], trace_sources: list[str]
) -> None:
    """LLM 模式蒸馏后按 host 落卡；**模板兜底卡跳过**（保住旧卡）。

    【P4 修复·localhost 事故】此前兜底模板卡也被落盘——一次 LLM/解析失败会用模板
    垃圾覆盖 registry 里的好卡（v1 真卡被顶掉、版本倒退）。配合 distill_host 的
    「全失败保旧卡」，registry 只存成功的 LLM 卡。
    """
    for c in cards:
        if (c.meta or {}).get("model") == "(template)":
            progress.report(
                "DISTILL",
                detail=f"skip registry save host={c.domain} (template fallback; keep old card)",
            )
            continue
        registry.save_card(output_dir, c, trace_sources)


def run_distill_pipeline(
    trace_paths: Path | list[Path],
    output_dir: Path,
    adapter_name: str = "treewalker",
    no_llm: bool = False,
    fresh: bool = False,
    task_description: str | None = None,
) -> DistillResult:
    """跑完整蒸馏链路：ADAPT(×N) → ATOMIZE(×N) → CLASSIFY → BUCKET → DISTILL → INSTALL。

    P4：支持多 trace 输入（同 host 累积）；registry 增量（``fresh=True`` 忽略旧卡）；
    双产物（跳 A 站点级 host 卡 + 跳 B 任务卡）；``task_description`` 优先级
    显式参数 > trace 自带 ``task_instruction``。

    与 ``treeforge.__main__._run_distill`` 等价，但不 print、不返退出码，
    返回 :class:`DistillResult`，供 CLI 薄包装和 HTTP 后台任务共用。

    失败时 ``ok=False`` + ``error`` 填原因（不抛异常，便于 job dict 记录）。
    """
    config.load()  # 刷新 .env（幂等）

    # 归一化输入（单 Path 兼容旧调用）
    if isinstance(trace_paths, (str, Path)):
        trace_paths = [Path(trace_paths)]
    trace_paths = [Path(p) for p in trace_paths]
    first = trace_paths[0] if trace_paths else Path()

    use_llm = (not no_llm) and bool(config.LLM_KEY)
    if not no_llm and not config.LLM_KEY:
        progress.report(
            "DISTILL",
            detail="LLM_KEY 未配置，自动退回模板模式（产物质量低，仅供链路验证）",
        )

    def _fail(err: str) -> DistillResult:
        return DistillResult(ok=False, error=err, trace_path=first, trace_paths=trace_paths)

    try:
        # ① ADAPT（逐 trace）+ stage 冲突重映射 + page_context 合并
        from harness import adapter as adapt_mod

        traces = [adapt_mod.load_trace(p) for p in trace_paths]
        _remap_stage_conflicts(traces)
        merged_page_context: dict[str, str] = {}
        for tr in traces:
            merged_page_context.update(tr.page_context or {})

        # 任务描述优先级：显式参数 > trace 自带（首个非空）
        if task_description is None:
            task_description = next(
                (t.task_instruction for t in traces if getattr(t, "task_instruction", "")), ""
            )
        task_description = (task_description or "").strip()

        # ② ATOMIZE（逐 trace）→ 拼接
        segments: list[Any] = []
        for tr in traces:
            segments.extend(atomizer.atomize(tr))
        if not segments:
            return _fail("无 segment，退出")

        # ③ CLASSIFY ④ BUCKET（一次，跨 trace 归并）
        classified = classifier.classify(segments, use_llm=use_llm)
        buckets = bucketer.bucket(classified)
        if not buckets:
            return _fail("无 bucket，退出")

        # registry（P4）：LLM 模式且非 fresh 时按 host 懒加载旧卡（增量蒸馏）
        prev_cards: dict[str, dict[str, Any]] = {}
        if use_llm and not fresh:
            for h in {b.domain for b in buckets}:
                prev = registry.load_card(output_dir, h)
                if prev:
                    prev_cards[h] = prev
                    progress.report(
                        "DISTILL",
                        detail=(
                            f"incremental host={h} "
                            f"prev_version={prev.get('meta', {}).get('distill_version')}"
                        ),
                    )
        elif use_llm and fresh:
            progress.report("DISTILL", detail="fresh=True：忽略 registry 旧卡，从头蒸馏")

        # ⑤ 跳 A：站点级蒸馏（透传合并后的 page_context，让 LLM 看到 DOM 快照推 quirks）
        cards: list[SkillCard] = distiller.distill_buckets(
            buckets,
            use_llm=use_llm,
            page_context=merged_page_context,
            prev_cards=prev_cards,
        )
        if not cards:
            return _fail("无 card 产出，退出")

        # INSTALL（host 三件套）
        adp = get_adapter(adapter_name)
        written = install.install_cards(cards, output_dir, adp)

        # registry 落卡（仅 LLM 模式；模板模式不累积——P4 决策 4；
        # 模板兜底卡跳过——一次 LLM 失败不毁旧卡，localhost 事故修复）
        if use_llm:
            _save_cards_to_registry(output_dir, cards, [str(p) for p in trace_paths])

        # ⑤ 跳 B：任务级双产物（treewalker adapter 专属布局）
        task_dir: Path | None = None
        task_slug: str | None = None
        if adapter_name == "treewalker" and cards:
            host = cards[0].domain
            host_buckets = [b for b in buckets if b.domain == host]
            existing_tasks = list_task_cards(output_dir, host)
            fallback_slug = trace_paths[0].parent.name if trace_paths else "task"
            tcard = distiller.distill_task(
                host,
                host_buckets,
                task_description=task_description,
                existing_tasks=existing_tasks,
                use_llm=use_llm,
                page_context=merged_page_context,
                fallback_slug=fallback_slug,
            )
            slug = str(tcard.meta.get("task_slug") or fallback_slug)
            task_meta = {
                "task_description": task_description,
                "task_keywords": tcard.meta.get("task_keywords") or [],
                "distilled_at": str(tcard.meta.get("distilled_at") or ""),
            }
            task_dir = write_task_card(
                output_dir,
                host,
                slug,
                tcard,
                task_meta,
                [str(p) for p in trace_paths],
            )
            task_slug = slug

        progress.report("DONE", detail=f"wrote {len(written)} files to {output_dir}")

        host_dir = None
        if adapter_name == "treewalker" and cards:
            host_dir = output_dir / "domain-skills" / cards[0].domain

        return DistillResult(
            ok=True,
            written=written,
            host_dir=host_dir,
            cards_count=len(cards),
            trace_path=first,
            trace_paths=trace_paths,
            task_dir=task_dir,
            task_slug=task_slug,
        )
    except Exception as e:  # noqa: BLE001 - 管线失败记进 error，不抛（job dict 用）
        logger.exception("distill pipeline failed")
        return DistillResult(
            ok=False, error=f"{type(e).__name__}: {e}", trace_path=first, trace_paths=trace_paths
        )


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
        if self.result and self.result.get("trace_paths"):
            d["result"]["trace_paths"] = [str(p) for p in self.result["trace_paths"]]
        if self.result and self.result.get("task_dir"):
            d["result"]["task_dir"] = str(self.result["task_dir"])
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
    trace_paths: Path | list[Path],
    output_dir: Path,
    adapter_name: str = "treewalker",
    no_llm: bool = False,
    fresh: bool = False,
    task_description: str | None = None,
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

    asyncio.create_task(
        _run_job(job_id, trace_paths, output_dir, adapter_name, no_llm, fresh, task_description)
    )
    return job_id


async def _run_job(
    job_id: str,
    trace_paths: Path | list[Path],
    output_dir: Path,
    adapter_name: str,
    no_llm: bool,
    fresh: bool = False,
    task_description: str | None = None,
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
                run_distill_pipeline,
                trace_paths,
                output_dir,
                adapter_name,
                no_llm,
                fresh,
                task_description,
            )
        if result.ok:
            job.status = "done"
            job.result = {
                "written": result.written,
                "host_dir": result.host_dir,
                "cards_count": result.cards_count,
                "trace_path": result.trace_path,
                "trace_paths": result.trace_paths,
                "task_dir": result.task_dir,
                "task_slug": result.task_slug,
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
