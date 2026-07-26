# 子任务 D：FastAPI 最小接入层

> 工作量：**大（3-4 天）** | 依赖：[B 持久化层](./02-persistence.md) + [E Windows 适配](./05-windows-adaptation.md) | 文件：`server/server.py`（新）+ `pyproject.toml`（加 fastapi/uvicorn）
>
> **范围：最小 server**（4 端点 + 认证 + 异步蒸馏 + 进度轮询）。**不做面板**（panel）、**不做 MCP 入口**。

## 这个任务干什么

引入 FastAPI server，让扩展（P2）或测试 curl 能通过 HTTP 把 trace 推进来，server 内部触发蒸馏管线。

P1 后的链路从「CLI 读文件」升级到「HTTP 接收分块上传 → 异步蒸馏 → 进度轮询」：

```
扩展 / curl
   │ 分块上传 trace（gzip NDJSON）
   ▼
┌──────────────────────────────────────────┐
│ FastAPI server（server/server.py）       │
│ ─ POST /v1/traces/init                  │
│ ─ PUT  /v1/traces/{id}/chunks/{index}   │
│ ─ POST /v1/traces/{id}/finalize         │
│ ─ GET  /v1/traces/{id}/status           │
└──────────────┬───────────────────────────┘
               │ daemon thread 异步触发
               ▼
        harness.main.run_ingest_file + run_distill
               │ （子任务 B 已实现）
               ▼
        data/harness/ 持久化 + data/skills/ 输出
```

## 新增依赖

```toml
# pyproject.toml [project] dependencies 加：
dependencies = [
    "pydantic>=2.6,<3.0",
    "fastapi>=0.110,<1.0",   # P1 新增
    "uvicorn>=0.27,<1.0",    # P1 新增（ASGI server）
]
```

**为什么是运行时依赖而不是 dev？** 因为 server 模式运行时需要（不像 ruff/pytest 只是开发期）。

## 全局结构

```python
# server/server.py（新建，单文件）
"""TreeForge 接入层（P1）。

最小 server：4 个业务端点 + 认证 + 异步蒸馏 + 进度轮询。
不做静态面板、不做 MCP 入口（P1 范围外）。

启动：
    uv run python -m server.server
    # 或
    uv run uvicorn server.server:app --host 127.0.0.1 --port 8099
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import socket
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 让 import harness.* 在进程内可用
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness import config as hconfig
from harness import main as hmain          # 子任务 B 抽出来的编排层
from harness import progress as hprogress

# ============================================================================
# 常量
# ============================================================================

MAX_EVENTS_CHUNK = 16 * 1024 * 1024   # 16MB
MAX_MEDIA_CHUNK = 64 * 1024 * 1024    # 64MB
DEFAULT_PORT = 8099

# 进程内蒸馏串行锁——避免 finalize 抢 buckets.json
_PIPELINE_LOCK = threading.Lock()
# 进程级实时进度（纯内存）
_PROGRESS: dict[str, dict] = {}

app = FastAPI(title="TreeForge Local", version="0.1.0")

# CORS（本地单用户，扩展/任意 localhost 端口都要能调）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,            # allow_origins=* 时必须 False
    allow_methods=["*"],
    allow_headers=["*"],                # 放行 X-Trace-Chunk-* 自定义头
    expose_headers=["*"],
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
```

## 认证：Bearer Token + api-keys.json

```python
# server/server.py 认证部分

API_KEYS_PATH = hconfig.DATA_DIR / "api-keys.json"
_API_KEYS: set[str] = set()


def _load_api_keys() -> None:
    """加载 API keys。首次运行写入默认 key（零配置）。"""
    global _API_KEYS
    default_key = os.environ.get("API_KEY", "treeforge-local-dev-key")
    _API_KEYS = {default_key}  # P1 简化：env 优先 + 默认 key
    if API_KEYS_PATH.is_file():
        try:
            data = json.loads(API_KEYS_PATH.read_text(encoding="utf-8"))
            _API_KEYS.update(data.get("keys", []))
        except json.JSONDecodeError:
            pass  # 配置损坏，用默认


def _check_auth(authorization: str | None) -> None:
    """所有业务接口的第一个动作。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in _API_KEYS:
        raise HTTPException(401, "invalid api key")
```

**零配置约定：** 首次启动自动用 `treeforge-local-dev-key` 作默认 key（可被 `API_KEY` env 覆盖），
让扩展连接时不用手动配。

## upload_id 生成 + 幂等性

```python
def _upload_id_for(trace_id: str) -> str:
    """同一个 trace_id 永远映射到同一个 upload_id → 天然幂等。

    支持断点续传：第二次 init 不破坏已上传 chunk。
    """
    return "upl_" + hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:12]


def _validate_upload_id(upload_id: str) -> None:
    """防路径穿越：upload_id 必须形如 upl_[a-f0-9]{12}。"""
    import re
    if not re.fullmatch(r"upl_[a-f0-9]{12}", upload_id):
        raise HTTPException(400, "bad upload_id")
```

## meta.json 的读-改-写（带文件锁）

```python
from harness.persistence import _atomic_write_json  # 子任务 B 提供

# 子任务 E 提供 _file_lock
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def update_meta(upload_id: str, *, create: bool = False):
    """带文件锁的读-改-写。所有改 meta 的唯一入口。"""
    meta_path = hconfig.TRACKS_DIR / upload_id / "meta.json"
    lock_path = meta_path.with_suffix(".json.lock")
    with _file_lock(lock_path):
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        elif create:
            meta = {}
        else:
            raise HTTPException(404, "trace not found")
        yield meta
        _atomic_write_json(meta_path, meta)
```

`_file_lock` 实现见 [05-windows-adaptation.md](./05-windows-adaptation.md)。

## 4 个端点

### 端点 1：`POST /v1/traces/init`

```python
class InitReq(BaseModel):
    trace_id: str                          # 必填
    label: str | None = None
    description: str | None = None
    tags: list[str] = []
    summary: str | None = None


@app.post("/v1/traces/init")
def init_trace(req: InitReq, authorization: str = Header(...)):
    _check_auth(authorization)
    if not req.trace_id:
        raise HTTPException(400, "trace_id required")
    upload_id = _upload_id_for(req.trace_id)
    with update_meta(upload_id, create=True) as meta:
        # create=True 时若已存在则不动（断点续传）
        meta.setdefault("trace_id", req.trace_id)
        meta.setdefault("label", req.label)
        meta.setdefault("description", req.description)
        meta.setdefault("tags", req.tags)
        meta.setdefault("summary", req.summary)
        meta.setdefault("accepted_chunks", {})   # {index: {kind, sha256, size}}
        meta.setdefault("status", "initialized")
        meta.setdefault("created_at", _utcnow())
    return {
        "upload_id": upload_id,
        "accepted_chunks": sorted(int(k) for k in meta["accepted_chunks"]),
        "status": meta["status"],
    }
```

### 端点 2：`PUT /v1/traces/{upload_id}/chunks/{index}` + 校验链

**校验链顺序很重要**（提前拒绝大 body 节省带宽）：

```python
@app.put("/v1/traces/{upload_id}/chunks/{index}")
async def put_chunk(
    upload_id: str,
    index: int,
    request: Request,
    authorization: str = Header(...),
    x_trace_chunk_kind: str = Header("events", alias="X-Trace-Chunk-Kind"),
    x_trace_chunk_sha256: str = Header(..., alias="X-Trace-Chunk-Sha256"),
    content_length: int = Header(..., alias="Content-Length"),
):
    _check_auth(authorization)
    _validate_upload_id(upload_id)
    kind = x_trace_chunk_kind
    if kind not in ("events", "media"):
        raise HTTPException(400, "bad kind")
    limit = MAX_EVENTS_CHUNK if kind == "events" else MAX_MEDIA_CHUNK

    # ── 校验 1: Content-Length 提前拒绝（413）──
    if content_length > limit:
        raise HTTPException(413, f"{kind} chunk too large")

    # ── 校验 2: 读 body + 长度复核 ──
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(413, f"{kind} chunk too large")

    # ── 校验 3: SHA-256 ──
    actual = hashlib.sha256(body).hexdigest()
    if actual != x_trace_chunk_sha256:
        raise HTTPException(409, "sha256 mismatch")

    # ── 校验 4: 幂等 ──
    with update_meta(upload_id, create=False) as meta:
        chunks = meta["accepted_chunks"]
        key = str(index)
        if key in chunks:
            if chunks[key]["sha256"] == actual and chunks[key]["kind"] == kind:
                return {"ok": True, "index": index, "dedup": True}  # 幂等成功
            raise HTTPException(409, "index exists with different hash")
        chunk_dir = hconfig.TRACKS_DIR / upload_id / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_dir / f"{index:04d}.{kind}.gz"
        chunk_path.write_bytes(body)
        chunks[key] = {"kind": kind, "sha256": actual, "size": len(body)}
    return {"ok": True, "index": index}
```

### 端点 3：`POST /v1/traces/{upload_id}/finalize` + 异步蒸馏

```python
@app.post("/v1/traces/{upload_id}/finalize")
def finalize_trace(upload_id: str, authorization: str = Header(...)):
    _check_auth(authorization)
    _validate_upload_id(upload_id)
    with update_meta(upload_id, create=False) as meta:
        meta["status"] = "processing"
        meta["finalized_at"] = _utcnow()

    # ── 组装 trace.json ──
    assembly_errors = _assemble_trace(upload_id)
    if assembly_errors:
        with update_meta(upload_id, create=False) as meta:
            meta["assembly_errors"] = assembly_errors
            meta["status"] = "degraded"
        return {"status": "degraded", "errors": assembly_errors}

    with update_meta(upload_id, create=False) as meta:
        meta["status"] = "accepted"
        meta.setdefault("distill_status", "pending")

    # ── daemon 线程异步蒸馏（不阻塞返回）──
    t = threading.Thread(
        target=_ingest_distill_install_safe, args=(upload_id,), daemon=True
    )
    t.start()
    return {"status": "accepted"}


def _assemble_trace(upload_id: str) -> list[str]:
    """按 index 排序 → gunzip → 逐行 json.loads → 单文件 trace.json。"""
    chunk_dir = hconfig.TRACKS_DIR / upload_id / "chunks"
    errors = []
    events = []
    for chunk_path in sorted(chunk_dir.glob("*.events.gz")):
        try:
            data = gzip.decompress(chunk_path.read_bytes())
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{chunk_path.name}: {e}")
    (hconfig.TRACKS_DIR / upload_id / "trace.json").write_text(
        json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8"
    )
    return errors
```

### 端点 4：`GET /v1/traces/{upload_id}/status`

```python
@app.get("/v1/traces/{upload_id}/status")
def trace_status(upload_id: str, authorization: str = Header(...)):
    _check_auth(authorization)
    _validate_upload_id(upload_id)
    meta_path = hconfig.TRACKS_DIR / upload_id / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(404, "trace not found")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "status": meta.get("status"),
        "accepted_chunks": sorted(int(k) for k in meta.get("accepted_chunks", {})),
        "distill_status": meta.get("distill_status"),
        "distill_result": meta.get("distill_result"),
        "progress": _PROGRESS.get(upload_id),
    }
```

## 异步蒸馏管线

```python
def _ingest_distill_install_safe(upload_id: str):
    """daemon 线程入口，捕获所有异常写 error。"""
    try:
        _ingest_distill_install(upload_id)
    except Exception as e:  # noqa: BLE001
        try:
            with update_meta(upload_id, create=False) as meta:
                meta["distill_status"] = "error"
                meta["distill_result"] = {"note": f"pipeline error: {e}"}
        except Exception:  # noqa: BLE001
            pass
        hprogress.set_reporter(None)
        _PROGRESS[upload_id] = {"phase": "error", "detail": str(e)}


def _ingest_distill_install(upload_id: str):
    """实际管线执行。在 _PIPELINE_LOCK 保护下串行。"""
    def _rep(phase: str, current: int = 0, total: int = 0, detail: str = ""):
        _PROGRESS[upload_id] = {
            "phase": phase, "current": current, "total": total, "detail": detail
        }

    with _PIPELINE_LOCK:                    # 串行化，防 buckets.json 并发写坏
        hprogress.set_reporter(_rep)        # 接管 harness 进度上报

        # 注入配置（从 .env / config.json）
        hconfig.load()

        _rep("ingest")
        trace_path = hconfig.TRACKS_DIR / upload_id / "trace.json"
        n_seg = hmain.run_ingest_file(trace_path)   # ① ADAPT→ATOMIZE→CLASSIFY→BUCKET

        _rep("distill")
        n_distilled = hmain.run_distill()           # ② 蒸馏 dirty 桶

        _rep("install")
        # ③ install 已在 run_distill 内调（落 skills/ 目录）

        # 智能结果判定
        note = _judge_result(n_distilled, n_seg)
        with update_meta(upload_id, create=False) as meta:
            meta["distill_status"] = "done" if n_distilled > 0 else "error"
            meta["distill_result"] = {
                "distilled": n_distilled,
                "classified_segments": n_seg,
                "note": note,
            }
        _rep("done")
        hprogress.set_reporter(None)        # ★ 复位，避免泄漏


def _judge_result(n_distilled: int, n_seg: int) -> str:
    """智能结果判定——给用户可操作的提示。"""
    if n_distilled == 0:
        return ("No skills produced — likely the classify/distill LLM call failed "
                "(check LLM_KEY/LLM_BASE/DISTILL_MODEL in .env; a custom gateway "
                "may not serve the model).")
    if n_seg == 0:
        return "No segments classified — check logs/gateway; you can Reprocess."
    return f"Distilled {n_distilled} bucket(s) from {n_seg} segment(s)."
```

## 启动入口

```python
def _find_port(default: int = DEFAULT_PORT) -> int:
    """端口冲突时递增 fallback。"""
    port = default
    for _ in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"no available port starting from {default}")


@app.get("/api/version")
def version():
    """公开接口（不走鉴权）。"""
    return {"name": "TreeForge Local", "version": "0.1.0"}


@app.get("/")
def root():
    """P1 不做面板，返回 JSON 占位。"""
    return {"name": "TreeForge Local", "status": "ok", "docs": "/docs"}


def main():
    """server 启动入口。"""
    import uvicorn

    # 确保目录存在
    hconfig.DATA_DIR.mkdir(parents=True, exist_ok=True)
    hconfig.TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    hconfig.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _load_api_keys()

    port = _find_port(int(os.environ.get("PORT", DEFAULT_PORT)))
    print(f"TreeForge server listening on http://127.0.0.1:{port}")
    print(f"API key: {next(iter(_API_KEYS), '<unset>')}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
```

## pyproject.toml 加启动脚本

```toml
[project.scripts]
treeforge = "treeforge.__main__:main"
treeforge-server = "server.server:main"   # P1 新增
```

启动方式：

```bash
uv run treeforge-server                    # console script
uv run python -m server.server             # 模块调用
uv run uvicorn server.server:app --port 8099  # 开发热重载
```

## 依赖与前置

**强依赖：**
- [B 持久化层](./02-persistence.md)——`harness.main.run_ingest_file` / `run_distill`
- [E Windows 适配](./05-windows-adaptation.md)——`_file_lock`（Windows 上 `import fcntl` 会崩）

**弱依赖：**
- [A redact](./01-redact.md)——server 不直接调，但 ADAPT 阶段会用到
- [C 增量蒸馏](./03-distill-enhancements.md)——server 不直接调，但多次上传同站点 trace 时受益

## 验收点

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | server 启动 | `uv run treeforge-server` 不报错，打印监听端口 |
| 2 | 端口冲突 fallback | 占用 8099 后启动，自动用 8100 |
| 3 | 无 auth 401 | `curl POST /v1/traces/init`（无 Authorization）→ 401 |
| 4 | 错误 key 401 | `curl -H "Authorization: Bearer wrong"` → 401 |
| 5 | init 幂等 | 同 trace_id 调两次 init，返回同 upload_id |
| 6 | put chunk 成功 | 上传一个 gzip chunk + 正确 sha256 → 200 |
| 7 | sha256 不符 409 | 上传 chunk + 错误 sha256 → 409 |
| 8 | chunk 过大 413 | 上传 > 16MB events chunk → 413 |
| 9 | chunk 幂等 | 同 index + 同 sha256 上传两次 → 第二次返回 `dedup: true` |
| 10 | finalize 触发蒸馏 | finalize 后 status 从 `accepted` → `processing` → `done` |
| 11 | 进度轮询 | 蒸馏中 `GET status` 返回 `progress.phase` |
| 12 | 错误恢复 | LLM 失败时 status → `error`，note 有提示 |
| 13 | 端到端 | curl 完整流程 → `data/skills/domain-skills/<host>/` 出现 4 文件 |
| 14 | 并发 finalize 安全 | 两个 finalize 同时到达，buckets.json 不损坏（_PIPELINE_LOCK） |

## 测试要求

新建 `tests/test_server.py`，用 `fastapi.testclient.TestClient`（**不真起 server**）：

```python
import gzip
import hashlib
import json
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立的 tmp_path 作 DATA_DIR。"""
    from harness import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TRACKS_DIR", tmp_path / "traces")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "harness")

    # reload server 模块以应用 monkeypatch
    import importlib
    import server.server as srv
    importlib.reload(srv)
    srv._load_api_keys()
    return TestClient(srv.app), srv

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer treeforge-local-dev-key"}


def test_init_requires_auth(client):
    c, _ = client
    r = c.post("/v1/traces/init", json={"trace_id": "t1"})
    assert r.status_code == 401

def test_init_returns_upload_id(client, auth_headers):
    c, _ = client
    r = c.post("/v1/traces/init", json={"trace_id": "t1"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["upload_id"].startswith("upl_")
    # 幂等：同 trace_id 同 upload_id
    r2 = c.post("/v1/traces/init", json={"trace_id": "t1"}, headers=auth_headers)
    assert r.json()["upload_id"] == r2.json()["upload_id"]

def test_put_chunk_sha256_mismatch(client, auth_headers):
    c, _ = client
    c.post("/v1/traces/init", json={"trace_id": "t1"}, headers=auth_headers)
    upload_id = "upl_" + "a" * 12  # 需匹配实际生成的 id

    body = gzip.compress(b'{"type":"click"}\n')
    wrong_sha = "0" * 64
    r = c.put(
        f"/v1/traces/{upload_id}/chunks/0",
        content=body,
        headers={**auth_headers,
                 "X-Trace-Chunk-Kind": "events",
                 "X-Trace-Chunk-Sha256": wrong_sha},
    )
    assert r.status_code == 409

def test_put_chunk_idempotent(client, auth_headers):
    """同 index + 同 sha 上传两次 → 第二次 dedup。"""
    c, _ = client
    # ... setup + 第一次上传 + 第二次上传 ...
    # assert 第二次返回 dedup: True

def test_status_404_unknown(client, auth_headers):
    c, _ = client
    r = c.get("/v1/traces/upl_000000000000/status", headers=auth_headers)
    assert r.status_code == 404

def test_version_public(client):
    """version 端点不走鉴权。"""
    c, _ = client
    r = c.get("/api/version")
    assert r.status_code == 200
    assert "version" in r.json()
```

**关键测试策略：**
- 用 `TestClient`（同步，不起真实 server，速度快）
- **mock 蒸馏管线**——不真调 LLM，patch `harness.main.run_ingest_file` 和 `run_distill`
- **每个测试独立 tmp_path**——避免状态污染
- 端到端测试可选（真跑管线，但加 `--no-llm` 模板模式）

**加 dev 依赖：**

```toml
[project.optional-dependencies]
dev = [
    "ruff>=0.5,<1.0",
    "pytest>=8.0,<9.0",
    "httpx>=0.27,<1.0",   # P1 新增：TestClient 依赖
]
```

## 难点与坑

### 坑 1：`import fcntl` 在 Windows 崩

**这是 P1 必须先解决 Windows 适配（子任务 E）的原因。** 如果 `_file_lock` 用顶层 `import fcntl`，
Windows 上 server 启动即崩。必须按 `sys.platform` 分支 import。

详见 [05-windows-adaptation.md](./05-windows-adaptation.md)。

### 坑 2：daemon 线程异常静默

```python
t = threading.Thread(target=..., daemon=True)
t.start()
```

daemon 线程抛异常**不会传播到主线程**，client 只看到 finalize 返回 200，但蒸馏失败了。
**解决：** 线程入口包一层 `_ingest_distill_install_safe`，捕获所有异常写进 `meta.distill_status = "error"`。
client 轮询 status 才能看到错误。

### 坑 3：_PIPELINE_LOCK 死锁

```python
with _PIPELINE_LOCK:
    hprogress.set_reporter(_rep)
    ...
```

如果 `_rep` 里又调了需要 `_PIPELINE_LOCK` 的代码（比如某个 nested finalize），会死锁。
**解决：** `_rep` 只写内存 dict `_PROGRESS`，不调任何锁保护的代码。

### 坑 4：harness.progress.set_reporter 泄漏

```python
hprogress.set_reporter(_rep)
# ... 跑管线 ...
hprogress.set_reporter(None)   # ★ 必须复位
```

不复位的话，下次 `_PROGRESS` 还在累积，且 `_rep` 持有的 `upload_id` 闭包引用泄漏。
**错误路径也要复位**——所以 `_ingest_distill_install_safe` 的 except 分支也要 `set_reporter(None)`。

### 坑 5：路径穿越

`PUT /v1/traces/{upload_id}/chunks/{index}` 的 `upload_id` 和 `index` 来自 URL。
如果不校验，攻击者可以构造 `../../etc/passwd` 之类的路径。

**解决：** `_validate_upload_id` 校验形如 `upl_[a-f0-9]{12}`，`index` 校验是 int。

### 坑 6：测试时的模块重载

`TestClient` 用了 `app` 对象，但 `app` 在模块加载时就创建了，里面的 `hconfig.TRACKS_DIR`
是当时的值。测试 monkeypatch 后必须 `importlib.reload(server.server)` 才生效。

**注意 reload 的副作用：** reload 会重新执行模块顶层代码（包括 `app = FastAPI()`），
所以 fixture 里要返回 reload 后的 `srv.app`。

### 坑 7：Content-Length 校验的边界

```python
content_length: int = Header(..., alias="Content-Length")
```

如果 client 用 chunked transfer encoding（没有 Content-Length），这个 header 会缺。
**解决：** P1 要求 client 必须带 Content-Length（扩展上传时用固定 body），不兼容 chunked。
文档里说明。

## 完成后下一步

→ [06-acceptance.md](./06-acceptance.md)（整体验收）
→ 或回头补 [05-windows-adaptation.md](./05-windows-adaptation.md)（如果还没做）
