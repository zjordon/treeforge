# 子任务 B：持久化层

> 工作量：**中（2-3 天）** | 依赖：无 | 这是 P1 的**核心前置**——接入层（D）和增强（C）都依赖它
>
> 文件：`harness/persistence.py`（新）+ `harness/checkpoint.py`（新）+ `harness/config.py`（改）+ `harness/bucketer.py`（改）+ `harness/registry.py`（改）

## 这个任务干什么

引入跨会话的持久化层，让 TreeForge 从「跑完即弃」升级到「累积复用」。

P0 的管线每次跑都是干净的：trace 进 → 蒸馏 → 文件出 → 结束，状态不保留。
P1 后变成：trace 进 → 累积到持久层 → 增量蒸馏 → 文件出，**状态跨会话保留**。

这是接入层（多 trace 累积）和增量蒸馏（旧 skill 加载）的共同基础。

## 目录结构

```
data/
├── traces/                          # 原始录制（接入层 P1 用）
│   └── <upload_id>/
│       ├── meta.json                # 上传元数据（分块进度、状态）
│       ├── chunks/                  # 分块（gzip NDJSON）
│       │   ├── 0000.events.gz
│       │   └── 0001.events.gz
│       └── trace.json               # 组装后的完整 trace
│
└── harness/                         # ← STATE_DIR（所有蒸馏状态）
    ├── checkpoint.json              # 管线进度（断点续传）
    ├── buckets.json                 # 桶定义（跨会话累积）
    ├── registry.json                # skill 索引
    ├── segments.jsonl               # 已分类 segment（append-only）
    └── skills/                      # 蒸馏产物（按 domain/capacity 组织）
        └── <domain>/
            └── <capacity>/
                ├── _sop.md          # treewalker adapter 4 文件
                ├── selectors.md
                ├── quirks.md
                ├── api.md
                ├── meta.json        # 蒸馏元数据（model/usage/version）
                └── evidence.jsonl   # 源 segment 证据
```

> 注：treewalker adapter 产 `_sop.md` / `selectors.md` 等 4 文件，browserbc adapter 产 `SKILL.md`。
> 持久化层只管 `meta.json` + `evidence.jsonl`，文件本身由 adapter 写。

## config.py 新增路径常量

```python
# harness/config.py（新增）
DATA_DIR: Path = REPO_ROOT / "data"
TRACKS_DIR: Path = DATA_DIR / "traces"       # 新增：原始 trace 存放
STATE_DIR: Path = DATA_DIR / "harness"       # 改：从 DATA_DIR/harness 改（之前注释里写的）
LOGS_DIR: Path = DATA_DIR / "logs"           # 新增：server 日志
SKILLS_ROOT: Path = STATE_DIR / "skills"     # 新增：蒸馏产物根

# env 加载（load 函数扩展）
TRACKS_DIR = _resolve(env, "TRACKS_DIR", str(TRACKS_DIR))
STATE_DIR = _resolve(env, "STATE_DIR", str(STATE_DIR))
SKILLS_ROOT = _resolve(env, "SKILLS_ROOT", str(SKILLS_ROOT))
```

## 四个状态文件的 schema

### 1. `checkpoint.json`（version=1）— 管线进度

```json
{
  "version": 1,
  "updated_at": "2026-07-01T12:00:00+00:00",
  "ingested_tracks": ["track-id-1", "track-id-2"],
  "classified_segments": 42,
  "bucket_count": 8,
  "distilled_buckets": ["httpbin.org::fill-checkout-form"],
  "pipeline": {
    "atomize": {"completed": 3, "failed": 0},
    "classify": {"completed": 42, "failed": 0, "retry_queue": []},
    "bucket":   {"last_run": "2026-07-01T12:00:00+00:00"},
    "distill":  {"completed": 5, "pending": 3, "failed": 0}
  }
}
```

**用途**：断点续传。进程崩在 distill 中间，重启后读 checkpoint 知道哪些桶蒸过、哪些待蒸。

### 2. `buckets.json`（**version=3**）— 桶定义

```json
{
  "version": 3,
  "updated_at": "...",
  "buckets": {
    "bilibili.com::upload-content": {
      "bucket_id": "bilibili.com::upload-content",
      "domain": "bilibili.com",
      "canonical_capacity": "upload-content",
      "description": "...",
      "segment_ids": ["track-xxx::0::15", "track-yyy::0::10"],
      "distill_version": 2,
      "last_distilled_at": "...",
      "dirty": false,
      "created_at": "...",
      "last_segment_added_at": "..."
    }
  }
}
```

**反序列化用 `.get(key, default)`** 容忍字段缺失（兼容旧 schema）。

### 3. `registry.json`（version=1）— skill 索引

```json
{
  "version": 1,
  "updated_at": "...",
  "skills": [
    {
      "capacity_id": "bilibili.com::upload-content",
      "skill_name": "Upload Content",
      "scope": "Use when uploading a video to bilibili.com ...",
      "domains": ["bilibili.com"],
      "preconditions": ["..."],
      "terminal_conditions": ["..."],
      "keywords": ["upload", "bilibili", "video"],
      "segment_count": 3,
      "distill_version": 2,
      "skill_path": "skills/bilibili.com/upload-content/_sop.md",
      "selectors_path": "skills/bilibili.com/upload-content/selectors.md",
      "quirks_path": "skills/bilibili.com/upload-content/quirks.md",
      "api_path": "skills/bilibili.com/upload-content/api.md"
    }
  ]
}
```

⚠️ **路径故意存相对路径**（相对 `STATE_DIR`）——让整个 `data/harness/` 可整体迁移。
读取时拼基准：`(config.STATE_DIR / entry.skill_path).read_text()`。

### 4. `segments.jsonl` — append-only 已分类 segment

**每行一个 JSON**（不是数组）：

```json
{"segment_id":"track-1::0::15","source_track_id":"track-1","domain":"bilibili.com","start_idx":0,"end_idx":15,"boundary_reason":"submit_nav","entry_url":"https://...","exit_url":"https://...","duration_ms":12345,"event_count":12,"event_summary":"...","capacity":"upload-content","description":"...","entry_conditions":[],"exit_conditions":[],"outcome":"success","domain_hints":[]}
```

⚠️ **不保存 `events` 字段**（原始事件列表），只存 `event_summary`（渲染后文本）。
重载时 `events=[]`——LLM 只需要 summary，不需要原始 DOM 事件。这是刻意瘦身。

## 核心模块：`harness/persistence.py`

把所有「读写状态文件」的逻辑集中在一个模块，统一原子写、统一时间戳格式。

```python
# harness/persistence.py（新建）
"""持久化层：读写状态文件（checkpoint/buckets/registry/segments）。

【统一约定】
  - 原子写：tmp.<pid> + os.replace（Windows 不用 Path.rename 避免 WinError 183）
  - 时间戳：datetime.now(timezone.utc).isoformat()
  - JSON：indent=2, ensure_ascii=False（中文不转义）
  - schema 版本号：checkpoint v1 / buckets v3 / registry v1
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    """跨平台原子写。必须用 os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    """文本原子写（adapter 写 markdown 用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ============================================================================
# buckets.json
# ============================================================================

def load_buckets() -> dict[str, dict]:
    """加载所有桶。返回 {bucket_id: bucket_dict}。"""
    path = config.STATE_DIR / "buckets.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # 兼容旧 schema：用 .get 容忍字段缺失
    return data.get("buckets", {})


def save_buckets(buckets: dict[str, dict]) -> None:
    """全量覆盖写。"""
    out = {"version": 3, "updated_at": _utcnow(), "buckets": buckets}
    _atomic_write_json(config.STATE_DIR / "buckets.json", out)


# ============================================================================
# registry.json
# ============================================================================

def load_registry() -> list[dict]:
    """加载 skill 索引。返回 entry 列表。"""
    path = config.STATE_DIR / "registry.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("skills", [])


def save_registry(entries: list[dict]) -> None:
    out = {"version": 1, "updated_at": _utcnow(), "skills": entries}
    _atomic_write_json(config.STATE_DIR / "registry.json", out)


def update_registry_entry(entry: dict) -> None:
    """upsert 单个 entry（按 capacity_id 匹配）。"""
    entries = load_registry()
    cap_id = entry["capacity_id"]
    for i, e in enumerate(entries):
        if e["capacity_id"] == cap_id:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    save_registry(entries)


# ============================================================================
# segments.jsonl（append-only）
# ============================================================================

def append_segments(records: list[dict]) -> None:
    """追加 segment 记录（不重写全文件）。"""
    path = config.STATE_DIR / "segments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_segments() -> dict[str, dict]:
    """全量扫 segments.jsonl 重建 {segment_id: record}。

    用于 distill 时按 segment_id 取 evidence。
    """
    path = config.STATE_DIR / "segments.jsonl"
    if not path.is_file():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            out[rec["segment_id"]] = rec
        except (json.JSONDecodeError, KeyError):
            continue  # 跳过损坏行（append 模式偶发）
    return out
```

## 管线进度：`harness/checkpoint.py`

```python
# harness/checkpoint.py（新建）
"""管线进度追踪（断点续传用）。"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config
from .persistence import _atomic_write_json, _utcnow

_CHECKPOINT_VERSION = 1


def _path():
    return config.STATE_DIR / "checkpoint.json"


def load_checkpoint() -> dict:
    """加载 checkpoint。不存在返回空骨架。"""
    if not _path().is_file():
        return _empty_checkpoint()
    import json
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_checkpoint()


def _empty_checkpoint() -> dict:
    return {
        "version": _CHECKPOINT_VERSION,
        "updated_at": _utcnow(),
        "ingested_tracks": [],
        "classified_segments": 0,
        "bucket_count": 0,
        "distilled_buckets": [],
        "pipeline": {
            "atomize": {"completed": 0, "failed": 0},
            "classify": {"completed": 0, "failed": 0, "retry_queue": []},
            "bucket": {"last_run": None},
            "distill": {"completed": 0, "pending": 0, "failed": 0},
        },
    }


def save_checkpoint(cp: dict) -> None:
    cp["updated_at"] = _utcnow()
    _atomic_write_json(_path(), cp)


# 便捷标记函数
def mark_track_ingested(cp: dict, track_id: str) -> None:
    if track_id not in cp["ingested_tracks"]:
        cp["ingested_tracks"].append(track_id)


def mark_segment_classified(cp: dict, count: int) -> None:
    cp["classified_segments"] += count
    cp["pipeline"]["classify"]["completed"] += count


def mark_bucket_distilled(cp: dict, bucket_id: str) -> None:
    if bucket_id not in cp["distilled_buckets"]:
        cp["distilled_buckets"].append(bucket_id)
    cp["pipeline"]["distill"]["completed"] += 1
```

## 改造现有模块

### `harness/bucketer.py` 加持久化

```python
# harness/bucketer.py 改造点
from . import persistence  # 新增

def bucket(classified, *, persist: bool = True) -> list[Bucket]:
    """归桶。persist=True 时累积到 buckets.json（P1 默认行为）。

    P0 行为：每次新建桶（persist=False 时退化为 P0）
    P1 行为：load_buckets → 合并 → save_buckets
    """
    # 1. 加载已有桶（P1 新）
    existing = persistence.load_buckets() if persist else {}
    bucket_map: dict[str, Bucket] = {
        bid: Bucket(**data) for bid, data in existing.items()
    }

    # 2. 归并新 classified（P0 逻辑）
    for seg, label in classified:
        # ... 原 bucket() 逻辑 ...

    # 3. 持久化（P1 新）
    if persist:
        persistence.save_buckets(
            {bid: b.model_dump() for bid, b in bucket_map.items()}
        )

    return list(bucket_map.values())
```

### `harness/registry.py` 从空实现变成真实现

P0 的 `load_registry` / `save_registry` / `update_registry_entry` 全部改为转调 `persistence.*`，
删除 `NotImplementedError`。

```python
# harness/registry.py 改造
from . import persistence

def load_registry(state_dir=None):
    return persistence.load_registry()

def save_registry(entries, state_dir=None):
    return persistence.save_registry(entries)

def update_registry_entry(entry):
    return persistence.update_registry_entry(entry)

# query_top_k / synthesize_playbook 保持 NotImplementedError（P4 范围）
```

### `harness/install.py` 增加写 meta + evidence

```python
# harness/install.py 改造点
def install_cards(cards, output_dir, adapter):
    # ... 原 P0 逻辑 ...
    # 每个桶蒸馏成功后，额外写：
    for card in cards:
        skill_dir = output_dir / card.domain / card.capacity
        # meta.json
        persistence._atomic_write_json(skill_dir / "meta.json", card.meta)
        # evidence.jsonl（每个源 segment 一行）
        with (skill_dir / "evidence.jsonl").open("w", encoding="utf-8") as f:
            for seg in bucket.segments:  # 假设 card 携带源 segment 信息
                f.write(json.dumps({
                    "segment_id": seg.segment_id,
                    "source_track": seg.source_track_id,
                    "domain": seg.domain,
                    "capacity": card.capacity,
                    "outcome": "success",
                }) + "\n")
```

## 增量加载/保存策略（关键）

**什么时候读、什么时候写**，是持久化层最关键的设计：

| 时机 | 操作 | 文件 |
|---|---|---|
| `cmd_ingest` 开始 | 读现有桶 → 获取 existing_capacities 喂 classifier | buckets.json |
| `cmd_ingest` 每段分类完 | append 到 segments.jsonl（不重写全文件） | segments.jsonl |
| `cmd_ingest` 结束 | 全量重写 buckets.json（原子覆盖） | buckets.json |
| `cmd_distill` 开始 | 全量扫 segments.jsonl 重建 segment_map | segments.jsonl |
| `cmd_distill` 每桶蒸完 | upsert registry + buckets[bid].dirty=False | registry.json + buckets.json |
| 每阶段步进 | 更新 checkpoint + 落盘 | checkpoint.json |
| 检索期（query_top_k） | 实时读盘（保证新 skill 立即可用） | registry.json |

**关键约定：**
- segments.jsonl 是 **append-only**（追加，不重写）
- buckets.json / registry.json 是 **全量覆盖**（每次写整个文件，靠 os.replace 原子）
- checkpoint.json 是 **增量更新**（读-改-写）

## 改造 harness/__init__.py 或新建 main.py

为了让 server 和 CLI 都能调「跑完整管线」，加编排函数。**P0 这部分逻辑在 `treeforge/__main__.py`**，
P1 要抽出来：

```python
# harness/main.py（新建）—— server 和 CLI 共用的编排
"""harness 编排层：server 和 CLI 共用的入口。

把 P0 在 treeforge/__main__.py 里的链路串逻辑抽到这里，
让 server 的 _ingest_distill_install 和 CLI 的 distill 子命令共用。
"""
from __future__ import annotations

from pathlib import Path

from . import adapter, atomizer, bucketer, classifier, checkpoint, config
from . import distiller, install, persistence, progress


def run_ingest_file(trace_path: Path, *, use_llm: bool | None = None) -> int:
    """跑 ADAPT → ATOMIZE → CLASSIFY → BUCKET（不蒸馏）。

    用于 server 的 finalize 后半段：先 ingest 累积证据，再触发 distill。
    """
    cp = checkpoint.load_checkpoint()
    trace = adapter.load_trace(trace_path)
    if trace.track_id in cp["ingested_tracks"]:
        progress.report("INGEST", detail=f"track {trace.track_id} 已处理过，跳过")
        return 0

    segments = atomizer.atomize(trace)
    classified = classifier.classify(segments, use_llm=use_llm)

    # append segments.jsonl（P1 新）
    persistence.append_segments([
        {
            "segment_id": seg.segment_id,
            "source_track_id": seg.source_track_id,
            "domain": seg.domain,
            "start_idx": seg.start_idx,
            "end_idx": seg.end_idx,
            "boundary_reason": seg.boundary_reason,
            "entry_url": seg.entry_url,
            "exit_url": seg.exit_url,
            "duration_ms": seg.duration_ms,
            "event_count": len(seg.events),
            "event_summary": seg.event_summary,
            "capacity": label.capacity,
            "description": label.description,
            "entry_conditions": label.entry_conditions,
            "exit_conditions": label.exit_conditions,
            "outcome": label.outcome,
            "domain_hints": label.domain_hints,
        }
        for seg, label in classified
    ])

    buckets = bucketer.bucket(classified, persist=True)
    checkpoint.mark_track_ingested(cp, trace.track_id)
    checkpoint.mark_segment_classified(cp, len(classified))
    cp["bucket_count"] = len(buckets)
    checkpoint.save_checkpoint(cp)
    return len(classified)


def run_distill(*, use_llm: bool | None = None) -> int:
    """蒸馏所有 dirty 桶。

    读 segments.jsonl 重建 segment_map，对 dirty 桶调 distill_bucket，
    成功后更新 registry + 清 dirty 标记。
    """
    cp = checkpoint.load_checkpoint()
    segment_map = persistence.load_segments()
    buckets_dict = persistence.load_buckets()
    buckets = [Bucket(**data) for data in buckets_dict.values()]

    dirty = [b for b in buckets if b.dirty and len(b.segment_ids) >= config.MIN_BUCKET_SIZE]
    progress.report("DISTILL", total=len(dirty))

    distilled = 0
    for i, bucket in enumerate(dirty):
        # 把 segment_map 里对应的 segment 实体塞进 bucket.segments
        bucket.segments = [
            _reconstruct_segment(segment_map[sid])
            for sid in bucket.segment_ids if sid in segment_map
        ]
        card = distiller.distill_bucket(bucket, use_llm=use_llm)
        # 落盘（adapter 写文件 + persistence 写 meta.json）
        # ... install 调用 ...
        # 更新 registry
        persistence.update_registry_entry({
            "capacity_id": card.bucket_id,
            "skill_name": card.skill_name,
            # ...
        })
        # 清 dirty
        bucket.dirty = False
        bucket.distill_version += 1
        checkpoint.mark_bucket_distilled(cp, bucket.bucket_id)
        distilled += 1
        progress.report("DISTILL", current=i + 1, total=len(dirty))

    persistence.save_buckets({b.bucket_id: b.model_dump() for b in buckets})
    checkpoint.save_checkpoint(cp)
    return distilled
```

## 依赖与前置

**无外部前置**——纯新建模块。

但子任务 C（增量蒸馏）和 D（接入层）都依赖它：
- C 需要 `load_segments()` 加载旧 segment、需要 `load_buckets()` 看 `distill_version`
- D 需要 `load_buckets()` 做幂等校验、需要 `save_checkpoint()` 做断点续传

## 验收点

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | buckets.json 写入 | 跑 distill 后 `data/harness/buckets.json` 存在 + schema 正确 |
| 2 | segments.jsonl append | 跑两次 distill，文件有 2 倍行数（不是覆盖） |
| 3 | registry.json upsert | 同 capacity 跑两次，registry 只有 1 个 entry（不是 2 个） |
| 4 | checkpoint.json 推进 | 跑完后 `pipeline.distill.completed` 递增 |
| 5 | 原子写（Windows） | 重复跑 distill 不报 WinError 183 |
| 6 | segments 瘦身 | segments.jsonl 行内**无** `events` 字段，**有** `event_summary` |
| 7 | 相对路径 | registry 的 skill_path 是相对路径（`skills/...`）不是绝对 |
| 8 | 跨会话累积 | 跑 trace A 后，再跑 trace B（同 capacity），bucket 的 segment_ids 含两条 |
| 9 | P0 CLI 不破 | `uv run treeforge distill ...` 仍跑通（无持久化时退化为 P0 行为） |

## 测试要求

新建 `tests/test_persistence.py`：

```python
import json
from pathlib import Path
from harness import persistence, checkpoint, config

def test_buckets_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    persistence.save_buckets({"x.com::test": {"bucket_id": "x.com::test"}})
    loaded = persistence.load_buckets()
    assert "x.com::test" in loaded

def test_segments_append_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    persistence.append_segments([{"segment_id": "s1"}])
    persistence.append_segments([{"segment_id": "s2"}])
    segs = persistence.load_segments()
    assert set(segs.keys()) == {"s1", "s2"}  # 不是只有 s2

def test_registry_upsert(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    persistence.update_registry_entry({"capacity_id": "x::c", "v": 1})
    persistence.update_registry_entry({"capacity_id": "x::c", "v": 2})  # 同 id
    entries = persistence.load_registry()
    assert len(entries) == 1
    assert entries[0]["v"] == 2

def test_atomic_write_overwrites(tmp_path, monkeypatch):
    """重复写不报错且内容更新（Windows WinError 183 验证）。"""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    persistence.save_buckets({"a": 1})
    persistence.save_buckets({"b": 2})
    assert persistence.load_buckets() == {"b": 2}

def test_segments_no_events_field(tmp_path, monkeypatch):
    """segments.jsonl 行不含 events 字段（瘦身）。"""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    persistence.append_segments([{
        "segment_id": "s1", "event_summary": "...",
        "events": ["should", "not", "be", "saved"]  # 测试输入故意带
    }])
    line = (tmp_path / "segments.jsonl").read_text()
    # 实现层应该在 append 前过滤掉 events
    # （或约定调用方不传 events，测试验证）

def test_checkpoint_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    cp = checkpoint.load_checkpoint()
    assert cp["pipeline"]["atomize"]["completed"] == 0
```

**关键测试策略**：
- 用 `monkeypatch.setattr(config, "STATE_DIR", tmp_path)` 隔离每个测试的状态目录
- **每个测试后 tmp_path 自动清理**（pytest fixture），避免状态污染
- 跨会话累积测试要显式调两次

## 难点与坑

### 坑 1：append vs 全量覆盖

`segments.jsonl` 是 append（不重写），`buckets.json` 是全量覆盖。**搞反了会丢数据**：
- segments 用全量覆盖 → 旧 segment 丢失
- buckets 用 append → 桶定义越来越多重复

**记住：**
- segments：append-only（日志型，只增不改）
- buckets/registry：read-modify-write（每次读全量 → 改 → 全量覆盖）

### 坑 2：schema 版本兼容

`buckets.json` 是 version=3。如果后续加字段：
- **反序列化用 `.get(key, default)`**，不要直接 `data["new_field"]`
- 新代码写新版本号，但能读旧版本

### 坑 3：相对路径 vs 绝对路径

registry 里的 `skill_path` **必须存相对路径**（`skills/xxx/...`），不能绝对路径。
否则整个 `data/` 目录搬家后路径全失效。读取时拼 `config.STATE_DIR / skill_path`。

### 坑 4：Windows 上的 append 并发

`segments.jsonl` 用 `"a"` 模式打开。Windows 上多线程 append 可能交织（行错乱）。
**单线程内调用没问题**（_PIPELINE_LOCK 保护）。**多进程 append 才有问题**——P1 不支持多进程，
单 server 进程内串行，OK。

### 坑 5：测试状态污染

P0 测试不用关心磁盘状态（跑完即弃）。P1 测试**必须清理状态**，否则：
- 上个测试的 buckets.json 影响下个测试
- segments.jsonl 越积越多

**解决**：所有持久化测试用 `tmp_path` + `monkeypatch.setattr(config, "STATE_DIR", tmp_path)`。

### 坑 6：event_summary 含特殊字符

event_summary 是多行文本，写进 JSONL 时 `json.dumps` 会自动转义换行。
**不要手动拼字符串写 JSONL**，永远用 `json.dumps(rec) + "\n"`。

## 完成后下一步

→ [05-windows-adaptation.md](./05-windows-adaptation.md)（server Windows 适配，独立预制件）
→ 或 [03-distill-enhancements.md](./03-distill-enhancements.md)（增量蒸馏，基于本任务的持久化）
