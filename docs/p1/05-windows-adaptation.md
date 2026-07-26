# 子任务 E：server 层 Windows 适配

> 工作量：**小（1 天）** | 依赖：无（可独立预制） | 文件：`server/server.py` 的几个工具函数（与 D 同文件）
>
> 配套阅读：[P0 concepts/02-llm-client.md](../p0/concepts/02-llm-client.md) 已覆盖 LLM 双协议探测（Windows 网关修复），本篇只讲 server 层

## 这个任务干什么

把 Browser-BC Windows 适配的「三连击」修复移植到 TreeForge server 层：

1. **`import fcntl` 崩溃** → msvcrt 分支 import
2. **`Path.rename` WinError 183** → os.replace（P0 已做，P1 复用）
3. **stdout BrokenPipe 崩管线** → `_ResilientStream` 包装

外加 server 层的健壮性配套：双写日志、CORS、启动顺序。

**为什么独立成子任务：** 这些是 server 的**通用工具**，可以在 D 之前预制好，D 直接调用。
拆出来也便于单独测试。

## 工具 1：`_file_lock`（Windows msvcrt + POSIX fcntl 分支）

### 问题根因

Browser-BC 的 `server/server.py:29` 顶层 `import fcntl`（POSIX 专属）。Windows 上模块加载即：

```
ModuleNotFoundError: No module named 'fcntl'
```

server 起不来。fcntl 唯一服务于 `_file_lock()`，保护 per-upload `meta.json` 的读-改-写。

### 实现

```python
# server/server.py 顶部 import
import sys
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _file_lock(lock_path: Path):
    """跨平台文件锁。Windows 用 msvcrt.locking，POSIX 用 fcntl.flock。

    Windows 锁失败降级为无锁（单用户本地产品可接受）。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # w+ 模式：msvcrt.locking 在 Windows 需要可写且可 seek 的句柄
    # POSIX flock 不读文件内容，模式对它无影响
    with open(lock_path, "w+") as lf:
        if sys.platform == "win32":
            try:
                lf.seek(0)
                # LK_LOCK 阻塞重试到拿到锁
                msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lf.seek(0)
                    try:
                        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            except OSError:
                yield  # 降级为无锁（单用户产品可接受）
        else:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
```

### 关键设计决策

| 细节 | 理由 |
|---|---|
| `open(lock_path, "w+")` 而非 `"w"` | msvcrt.locking 需要可写可 seek 句柄；`"w"` 模式锁空文件行为不稳定 |
| Windows 用 `LK_LOCK`（阻塞重试） | 对应 POSIX `LOCK_EX` 语义 |
| Windows `try/except OSError` 降级 | 单用户本地产品，锁失败也不崩；上层 `_PIPELINE_LOCK` 已基本串行化 |
| POSIX 保持 `flock` | 向后兼容 macOS/Linux，行为零变化 |

**为什么降级为无锁是安全的：**
- TreeForge 是**本地单用户**产品，并发来源只有同进程多线程
- `_PIPELINE_LOCK`（子任务 D）已经串行化了蒸馏管线
- 每个 upload_id 的 finalize 后台线程时序上不会重叠
- meta.json 的读-改-写窗口极短，竞态概率低

## 工具 2：`_atomic_write`（os.replace）

**P0 已在 `harness/install.py:atomic_write_text` 实现**，P1 在 `server/server.py` 复用。

如果 server 需要自己的版本（写 config.json 等）：

```python
# server/server.py
import os

def _atomic_write(path: Path, text: str) -> None:
    """写 xxx.tmp.<pid> 再 os.replace。

    Windows 上必须用 os.replace（不是 Path.rename / os.rename）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: dict) -> None:
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
```

**为什么不复用 `harness.persistence._atomic_write_json`：**
- 复用更好（DRY），但 server 启动早期可能还没初始化 harness 路径
- P1 实现时优先复用 `harness.persistence._atomic_write_json`，只有循环依赖才本地 copy

**候选项评价（为什么是 os.replace）：**
- ✅ `os.replace`：标准库专为跨平台原子替换设计
- ❌ `Path.rename`：Windows 目标存在时 WinError 183
- ❌ `os.rename`：Windows 同样 WinError 183
- ❌ `if dst.exists(): dst.unlink()` 后 rename：引入 TOCTOU 竞态，失去原子性

## 工具 3：`_ResilientStream`（防 BrokenPipe 崩管线）

### 问题根因

GUI 启动时 sidecar 的 stdout 是连到 Tauri shell 的管道；读取端消失时 `print()` / `log.write`
会抛 `BrokenPipeError`，曾经**崩溃整个蒸馏管线**。

虽然 TreeForge P1 没有 GUI 壳层，但:
- pytest 在某些捕获模式下也会让 stdout 行为异常
- 未来 P3 可能有桌面壳

提前包装是廉价的保险。

### 实现

```python
# server/server.py
import sys


class _ResilientStream:
    """包装 stdout/stderr，写入永远不抛 BrokenPipeError。"""

    def __init__(self, inner):
        self._inner = inner

    def write(self, s):
        try:
            self._inner.write(s)
            try:
                self._inner.flush()
            except Exception:  # noqa: BLE001
                pass
        except (BrokenPipeError, OSError, ValueError):
            # 读取端消失：丢弃写入，绝不向上抛
            pass
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        try:
            self._inner.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self):
        try:
            return self._inner.isatty()
        except Exception:  # noqa: BLE001
            return False

    def fileno(self):
        return self._inner.fileno()

    def __getattr__(self, name):
        return getattr(self._inner, name)


# 模块加载时即包装（必须在日志配置前）
sys.stdout = _ResilientStream(sys.stdout)
sys.stderr = _ResilientStream(sys.stderr)
```

## 工具 4：双写日志（stdout + 文件轮转）

```python
# server/server.py
import logging
from logging.handlers import RotatingFileHandler

# handler 出错绝不崩服务
logging.raiseExceptions = False


def _setup_logging() -> logging.Logger:
    """配置双写日志：stdout（已被 _ResilientStream 包装）+ 文件轮转。"""
    log_dir = hconfig.LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "treeforge-server.log"

    logger = logging.getLogger("treeforge")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:                    # 防重复加载
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = RotatingFileHandler(              # 4MB × 3 文件轮转
        log_path, maxBytes=4 * 1024 * 1024,
        backupCount=3, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


LOG = _setup_logging()                     # 模块加载即配
```

**关键点：**
- `logging.raiseExceptions = False`：handler 出错（如磁盘满）也不崩服务
- 同时配 stdout（实时看）+ 文件（持久化 + 轮转防膨胀）
- `logger.handlers` 检查防重复（测试 reload 时）

## CORS 配置

```python
# server/server.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 本地单用户，扩展/任意 localhost 端口都要能调
    allow_credentials=False,      # allow_origins=* 时必须 False
    allow_methods=["*"],
    allow_headers=["*"],          # 必须放行 X-Trace-Chunk-Kind / X-Trace-Chunk-Sha256
    expose_headers=["*"],
)
```

**为什么 allow_origins=*：** 本地 server，扩展从 `chrome-extension://...` 来、面板从 `http://localhost:xxxx` 来，
来源不固定。单用户产品不需要严格 CORS。

## 启动顺序（panel mount 最后）

**P1 不做面板**，所以这条暂不直接适用，但留备忘（P3 加面板时注意）：

```python
# 错误顺序（面板 catch-all 抢匹配）：
app.mount("/", StaticFiles(directory=panel, html=True))  # ← 所有 /api/* 被吃
@app.get("/api/version")  # 永远到不了
def version(): ...

# 正确顺序（业务路由优先）：
@app.get("/api/version")
def version(): ...
# 静态 mount 最后
if PANEL_BUILD.exists():
    app.mount("/", StaticFiles(directory=PANEL_BUILD, html=True))
```

## 依赖与前置

**无前置**——纯工具模块。

**是 D（接入层）的前置：** D 的 `update_meta` 用 `_file_lock`、写 config 用 `_atomic_write`。

## 验收点

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | Windows 启动不崩 | Windows 上 `uv run treeforge-server` 不报 `ModuleNotFoundError: fcntl` |
| 2 | 锁基本工作 | 并发调 `_file_lock` 同一路径，串行执行（unittest mock 验证） |
| 3 | 锁失败降级 | mock msvcrt.locking 抛 OSError，验证 `yield` 仍执行 |
| 4 | 原子写覆盖 | 重复 `_atomic_write` 同路径不报 WinError 183 |
| 5 | ResilientStream 吞 BrokenPipe | 构造 inner.write 抛 BrokenPipeError，验证不传播 |
| 6 | 双写日志 | 启动后 `data/logs/treeforge-server.log` 存在 |
| 7 | 日志不崩服务 | 日志磁盘满时（mock），server 继续响应 |

## 测试要求

新建 `tests/test_windows_compat.py`：

```python
import io
import pytest
from server.server import _ResilientStream, _atomic_write, _file_lock


def test_resilient_stream_swallows_broken_pipe():
    """验收 5：BrokenPipe 被吞。"""
    class BrokenPipeInner:
        def write(self, s):
            raise BrokenPipeError()
        def flush(self):
            raise BrokenPipeError()
    stream = _ResilientStream(BrokenPipeInner())
    stream.write("anything")  # 不抛
    stream.flush()             # 不抛


def test_resilient_stream_passes_through_normal():
    """正常写入透传。"""
    inner = io.StringIO()
    stream = _ResilientStream(inner)
    stream.write("hello")
    assert inner.getvalue() == "hello"


def test_atomic_write_creates_file(tmp_path):
    p = tmp_path / "x.json"
    _atomic_write(p, "{}")
    assert p.is_file()
    assert p.read_text() == "{}"


def test_atomic_write_overwrites(tmp_path):
    """验收 4：重复写不报错。"""
    p = tmp_path / "x.json"
    _atomic_write(p, '{"a":1}')
    _atomic_write(p, '{"a":2}')   # 重复
    assert p.read_text() == '{"a":2}'


def test_file_lock_serializes(tmp_path):
    """两个 _file_lock 调用串行（不并发执行 yield 内）。"""
    lock_path = tmp_path / "test.lock"
    order = []
    import threading

    def worker(name):
        with _file_lock(lock_path):
            order.append(f"{name}-start")
            order.append(f"{name}-end")

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    # 每个 worker 的 start/end 应相邻（不交错）
    for i in range(0, len(order), 2):
        assert "-start" in order[i]
        assert "-end" in order[i + 1]
        # start 和 end 应是同一个 worker
        assert order[i].split("-")[0] == order[i + 1].split("-")[0]


def test_file_lock_yields_on_lock_failure(tmp_path, monkeypatch):
    """验收 3：锁失败时仍 yield（降级无锁）。"""
    lock_path = tmp_path / "test.lock"
    # mock msvcrt.locking 抛 OSError（模拟 Windows 锁失败）
    import sys
    if sys.platform == "win32":
        import msvcrt
        def fake_locking(*args, **kwargs):
            raise OSError("mock lock failure")
        monkeypatch.setattr(msvcrt, "locking", fake_locking)

    entered = False
    with _file_lock(lock_path):
        entered = True
    assert entered  # 即使锁失败也进入了临界区
```

**测试策略：**
- `_ResilientStream` 用假 inner 对象模拟异常
- `_file_lock` 用多线程验证串行（但注意 Windows/POSIX 行为差异）
- Windows 特定分支用 `sys.platform == "win32"` + monkeypatch mock msvcrt

## 难点与坑

### 坑 1：测试跨平台行为

`_file_lock` 在 Linux CI 上跑不到 msvcrt 分支。**两个方案：**
1. 用 monkeypatch 强制走 msvcrt 分支（即使平台不对）——纯逻辑测试
2. 在 Windows CI 上真测——行为测试

**P1 建议两者都做**——逻辑测试日常跑、Windows CI 定期跑。

### 坑 2：`_ResilientStream` 包装时机

```python
sys.stdout = _ResilientStream(sys.stdout)
```

这行**必须在 logging 配置前**。否则 logging 拿到的是原始 stdout，包装没生效。
**模块顶层执行**（import 时），早于 `_setup_logging()`。

### 坑 3：日志重复加载

测试 reload server 模块时，`_setup_logging()` 会被多次调用。

```python
if logger.handlers:    # 防重复
    return logger
```

这个检查防止 handler 累加（否则同样日志写 N 次）。

### 坑 4：`logging.raiseExceptions = False` 是全局

```python
logging.raiseExceptions = False
```

这影响**所有 logger**，不只是 treeforge 的。如果 pytest 依赖 logging 异常暴露测试失败，
这个设置可能掩盖问题。

**缓解：** 在 conftest.py 的 pytest fixture 里临时恢复（如果需要）。
但生产环境保持 False——服务稳定性优先。

### 坑 5：模块重载副作用

测试 reload `server.server` 时，模块顶层代码（包括 `sys.stdout = _ResilientStream(sys.stdout)`）
会重新执行。第二次包装时 `sys.stdout` 已经是 `_ResilientStream`，包成两层。

**缓解：** 包装前检查：

```python
if not isinstance(sys.stdout, _ResilientStream):
    sys.stdout = _ResilientStream(sys.stdout)
```

## 完成后下一步

→ [04-server.md](./04-server.md)（接入层，用到这里实现的工具）
→ 或 [06-acceptance.md](./06-acceptance.md)（整体验收）
