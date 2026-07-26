# 子任务 C：增量蒸馏 + 桶合并 consolidate

> 工作量：**中（2 天）** | 依赖：[B 持久化层](./02-persistence.md) | 文件：`harness/distiller.py`（改）+ `harness/bucketer.py`（改）+ `treeforge/__main__.py`（加 consolidate 子命令）
>
> 配套阅读：[P0 stages/05-distill.md](../p0/stages/05-distill.md)（理解 P0 的 distill 现状）

## 这个任务干什么

两件相关的事，都属于「让蒸馏更聪明」：

1. **增量蒸馏接通**——P0 的 distiller 有 `distill_version > 0` 分支但旧 skill 没持久化，永远走不到。
   P1 接通持久化层（子任务 B）后，让这个分支真正生效：同能力的多次示教不再每次从头蒸，而是
   「保留旧知识 + 更新被新证据否定的部分 + 补充新发现」。

2. **桶合并 consolidate**——新加 CLI 子命令 `treeforge consolidate`，让 LLM 提议合并同义桶
   （`login-with-password` + `sign-in-with-credentials` → `login-with-credentials`）。

## P0 现状

```python
# harness/distiller.py P0 现状（关键部分）
if bucket.distill_version > 0 and bucket.last_distilled_at:
    prev_sop = ""
    # P0：我们没有持久化旧 SkillCard，这里只放占位提示（P1+ 接 registry 后补）
    prompt += _INCREMENTAL_ADDENDUM.format(
        prev_version=bucket.distill_version,
        prev_sop=prev_sop or "(previous skill not available in P0)",
    )[:8000]
```

**问题：** `prev_sop` 永远是空字符串/占位符，增量 addendum 形同虚设。而且 P0 每次跑都是新桶
（`distill_version` 永远是 0），这个分支压根进不来。

**P1 解决：**
- 持久化层让 `distill_version` 跨会话累积（B 已实现）
- 这里改成本地从 `STATE_DIR/skills/<domain>/<capacity>/` 加载旧 skill 的 4 个 markdown
- 截断到 8000 字符塞进 prompt

## 第一部分：增量蒸馏

### 改造 `harness/distiller.py`

```python
# harness/distiller.py 改造点

from pathlib import Path
from . import config, persistence  # P1 新增 persistence

_INCREMENTAL_ADDENDUM = """\

--- INCREMENTAL UPDATE ---
You are updating an EXISTING knowledge card (version {version}) based on NEW segment evidence.

Update the knowledge following these rules:
- KEEP rules/selectors that are still correct in the EXISTING card below.
- REMOVE rules that are CONTRADICTED by the new evidence above.
- ADD newly discovered selectors, quirks, API endpoints observed in the new evidence.
- Do NOT drop site-specific details unless the new evidence shows the old rule was wrong.

=== EXISTING knowledge card (version {version}, truncated to 8000 chars) ===
{existing_card}
=== END EXISTING knowledge card ===
"""


def _load_existing_card(bucket: Bucket) -> str:
    """从持久化层加载桶已蒸馏的 4 个 markdown，拼成一个文本块。

    P1 新增：替代 P0 的占位符。
    """
    skill_dir = config.STATE_DIR / "skills" / bucket.domain / bucket.canonical_capacity
    if not skill_dir.is_dir():
        return ""  # 首次蒸馏，无旧内容

    parts: list[str] = []
    for fname in ("_sop.md", "selectors.md", "quirks.md", "api.md"):
        p = skill_dir / fname
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            parts.append(f"## {fname}\n\n{content}")
    return "\n\n".join(parts)


def distill_bucket(bucket: Bucket, *, use_llm: bool | None = None) -> SkillCard:
    # ... P0 的 prompt 构造逻辑 ...

    # P1 改造：增量分支真接通
    if bucket.distill_version > 0:
        existing_card = _load_existing_card(bucket)
        if existing_card:
            prompt += _INCREMENTAL_ADDENDUM.format(
                version=bucket.distill_version,
                existing_card=existing_card[:8000],   # ★ 截断到 8000 字符控成本
            )

    # ... P0 的 call_llm + 解析逻辑 ...
```

### 增量触发条件（两个都要满足）

```python
if bucket.distill_version > 0:           # 条件 1：桶蒸过
    existing_card = _load_existing_card(bucket)
    if existing_card:                     # 条件 2：磁盘上有旧 skill
        prompt += _INCREMENTAL_ADDENDUM...
```

**为什么两个条件：**
- `distill_version > 0` 但磁盘没文件 → 可能用户手动删了 skill 目录，退化为首次蒸馏
- 磁盘有文件但 `distill_version == 0` → 数据不一致（不应该发生），也退化为首次

### 8000 字符截断

```python
existing_card[:8000]
```

**为什么截断：** 旧 skill 可能很长（多次增量后累积），全塞进 prompt 会爆 token + 成本高。
8000 字符是 Browser-BC 验证过的平衡点——足够保留关键 selector/quirks，又不超 prompt 上限。

### SkillCard 携带 distill_version

```python
card = SkillCard(
    # ... 其它字段 ...
    meta={
        "model": config.DISTILL_MODEL,
        "usage": usage,
        "segment_count": len(bucket.segments),
        "domains": [bucket.domain],
        "distill_version": bucket.distill_version + 1,   # ★ 版本递增
        "distilled_at": _now_iso(),
    },
)
```

**这个 `distill_version` 由子任务 B 的 `persistence.save_buckets` 持久化**——下次加载时
`bucket.distill_version` 就是上次蒸完的值，增量逻辑接通。

## 第二部分：桶合并 consolidate

### 应用场景

多次示教后，同一个能力可能被命名为不同的 capacity：

```
首次示教登录 → classifier 起名 login-with-password
第二次示教登录 → classifier 起名 sign-in-with-credentials（因为 caps 列表里有 login-with-password 但 LLM 没认出来）
第三次示教登录 → enter-email-and-password

→ 3 个桶，3 份重复的 login skill
```

consolidate 让 LLM 看一个域的所有桶，提议把同义的合并：

```json
{
  "merges": [
    {
      "target": "login-with-credentials",
      "target_description": "Log into a site using credentials.",
      "sources": ["login-with-password", "sign-in-with-credentials", "enter-email-and-password"],
      "reason": "All three describe credential login."
    }
  ]
}
```

### 在 `harness/bucketer.py` 加 consolidate 函数

```python
# harness/bucketer.py 新增
from . import llm

CONSOLIDATE_PROMPT = """\
You are consolidating capability buckets for the domain: {domain}.

Current buckets (❄ = cold bucket, segment count <= {min_segment_threshold}):
{bucket_listing}

Rules:
1. MERGE buckets that represent the SAME intent (different names for the same
   capability, e.g. "login-with-password" vs "sign-in-with-credentials").
2. MERGE sub-steps that ALWAYS co-occur into a single canonical capability
   (e.g. "enter-email" + "enter-password" -> "login-with-credentials").
3. DO NOT merge genuinely different capabilities.
4. Prefer merging COLD buckets (marked ❄) — they have weak evidence on their own.
5. Only output merge groups with 2 or more source buckets.

Output JSON:
{{
  "merges": [
    {{
      "target": "canonical-kebab-case-name",
      "target_description": "1-2 sentence description",
      "sources": ["old-name-1", "old-name-2"],
      "reason": "why these are the same intent"
    }}
  ]
}}
If no merges are needed, return {{"merges": []}}.
"""


def consolidate_domain(
    domain: str,
    buckets: dict[str, "Bucket"],
    *,
    min_segment_threshold: int = 2,
) -> list[dict]:
    """让 LLM 提议合并同义桶。返回 merges 列表（未应用）。"""
    domain_buckets = {bid: b for bid, b in buckets.items() if b.domain == domain}
    if len(domain_buckets) < 2:
        return []  # 少于 2 个桶，合并无意义

    bucket_listing = "\n".join(
        f"- {b.canonical_capacity} "
        f"(segments={len(b.segment_ids)}"
        f"{', ❄' if len(b.segment_ids) <= min_segment_threshold else ''}): "
        f"{b.description}"
        for b in domain_buckets.values()
    )
    prompt = CONSOLIDATE_PROMPT.format(
        domain=domain,
        min_segment_threshold=min_segment_threshold,
        bucket_listing=bucket_listing,
    )
    try:
        text, _ = llm.call_llm_fast(prompt)
        return llm.parse_json_from_model(text).get("merges", [])
    except Exception as e:  # noqa: BLE001
        # consolidate 失败不影响主流程
        return []


def apply_merges(
    buckets: dict[str, "Bucket"],
    domain: str,
    merges: list[dict],
) -> int:
    """应用 consolidate 提议，把多桶合并成一桶。返回实际合并数。"""
    from datetime import datetime, timezone
    applied = 0
    for merge in merges:
        sources = merge.get("sources", [])
        target_cap = slugify(merge["target"])
        target_bid = f"{domain}::{target_cap}"

        # 校验 sources 都是真实存在的桶
        valid_source_bids = []
        for src in sources:
            src_bid = _find_bucket_by_capacity(buckets, domain, src)
            if src_bid and src_bid != target_bid:
                valid_source_bids.append(src_bid)
        if len(valid_source_bids) < 2:  # 单源不算合并
            continue

        # 创建/复用 target 桶
        now = datetime.now(timezone.utc).isoformat()
        if target_bid not in buckets:
            buckets[target_bid] = Bucket(
                bucket_id=target_bid, domain=domain,
                canonical_capacity=target_cap,
                description=merge.get("target_description", ""),
                segment_ids=[], dirty=True, created_at=now,
            )
        target = buckets[target_bid]

        # 合并 segment_ids（去重）
        for src_bid in valid_source_bids:
            src_bucket = buckets[src_bid]
            for sid in src_bucket.segment_ids:
                if sid not in target.segment_ids:
                    target.segment_ids.append(sid)
            del buckets[src_bid]  # 删除被合并的源桶

        target.dirty = True  # 合并后需重蒸
        target.last_segment_added_at = now
        applied += 1
    return applied


def _find_bucket_by_capacity(
    buckets: dict[str, "Bucket"], domain: str, capacity: str
) -> str | None:
    """按 capacity 名（slug 后）找桶 id。"""
    target_slug = slugify(capacity)
    for bid, b in buckets.items():
        if b.domain == domain and slugify(b.canonical_capacity) == target_slug:
            return bid
    return None
```

### CLI 子命令 `treeforge consolidate`

```python
# treeforge/__main__.py 新增子命令
def _run_consolidate(threshold: int = 8, min_segment_threshold: int = 2) -> int:
    """手动触发桶合并。"""
    config.load()
    from collections import defaultdict
    from harness import bucketer, persistence, progress

    buckets_dict = persistence.load_buckets()
    if not buckets_dict:
        progress.report("CONSOLIDATE", detail="无桶，退出")
        return 0

    buckets = {bid: bucketer.Bucket(**data) for bid, data in buckets_dict.items()}

    # 按 domain 分组
    by_domain: dict[str, list[str]] = defaultdict(list)
    for bid, b in buckets.items():
        by_domain[b.domain].append(bid)

    total_merges = 0
    for domain, bids in by_domain.items():
        if len(bids) < threshold:  # 默认只处理桶数≥8的域
            progress.report(
                "CONSOLIDATE",
                detail=f"skip {domain} (only {len(bids)} buckets, < {threshold})",
            )
            continue

        merges = bucketer.consolidate_domain(
            domain, buckets, min_segment_threshold=min_segment_threshold
        )
        n = bucketer.apply_merges(buckets, domain, merges)
        total_merges += n
        if n:
            progress.report(
                "CONSOLIDATE",
                detail=f"{domain}: {n} merge group(s) applied",
            )

    if total_merges:
        persistence.save_buckets({bid: b.model_dump() for bid, b in buckets.items()})
        progress.report("CONSOLIDATE", detail=f"共合并 {total_merges} 组，已落盘")
        print(f"\n合并完成：{total_merges} 组")
        print("提示：合并后的桶标记为 dirty，跑 `treeforge distill --incremental` 重新蒸馏")
    else:
        progress.report("CONSOLIDATE", detail="无需合并")
        print("\n无需合并")
    return 0


# argparse 注册
p_consolidate = sub.add_parser("consolidate", help="合并同义桶（LLM 提议）")
p_consolidate.add_argument(
    "--threshold", type=int, default=8,
    help="只处理桶数≥此值的域（默认 8）",
)
p_consolidate.add_argument(
    "--min-segment-threshold", type=int, default=2,
    help="冷桶阈值：segment 数≤此值标记为❄（默认 2）",
)
```

## 依赖与前置

**强依赖：** [B 持久化层](./02-persistence.md)
- 增量蒸馏需要 `persistence` 加载旧 skill
- consolidate 需要 `persistence.load_buckets` / `save_buckets`

**弱依赖：** [A redact](./01-redact.md)（不直接依赖，但建议先做）

## 验收点

### 增量蒸馏验收

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | 首次蒸馏正常 | 跑 trace A，`distill_version` 从 0 → 1 |
| 2 | 增量分支进入 | 同 capacity 跑第二次，`distill_version` 1 → 2 |
| 3 | 旧 skill 加载 | 第二次跑时日志/进度显示「update existing」 |
| 4 | 8000 字符截断 | 构造超长旧 skill，验证 prompt 不超长（mock LLM 看 prompt 长度） |
| 5 | 旧文件不存在时退化 | 手动删 skills 目录，跑 distill 不报错（退首次） |
| 6 | 增量不丢关键 selector | 旧 skill 的 selector 在新 skill 里仍存在（LLM 遵守 KEEP 规则） |

### consolidate 验收

| # | 验收项 | 验证方式 |
|---|---|---|
| 7 | CLI 子命令可用 | `treeforge consolidate --help` 显示帮助 |
| 8 | 桶数 < threshold 跳过 | 构造 3 个桶，跑 `--threshold 8`，输出「skip」 |
| 9 | 同义桶合并 | 构造 3 个同义桶，跑 consolidate，剩 1 个 |
| 10 | 合并后 dirty=True | 合并产生的 target 桶 `dirty=True`（需重蒸） |
| 11 | segment_ids 去重合并 | 两桶各含同 segment_id，合并后只 1 个 |
| 12 | mock LLM 测试 | mock call_llm_fast 返回 merges，验证 apply_merges 逻辑 |

## 测试要求

新建 `tests/test_distill_incremental.py`：

```python
from unittest.mock import patch
from harness import distiller, persistence, config
from harness.models import Bucket, Segment, CapacityLabel

def test_incremental_loads_existing_card(tmp_path, monkeypatch):
    """验收 2/3：增量分支加载旧 skill。"""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    # 准备：先写一个旧 skill
    skill_dir = tmp_path / "skills" / "x.com" / "upload"
    skill_dir.mkdir(parents=True)
    (skill_dir / "_sop.md").write_text("# Old SOP\nexisting rule")

    bucket = Bucket(
        bucket_id="x.com::upload", domain="x.com",
        canonical_capacity="upload", distill_version=1,  # ★ 已蒸过
        segments=[_make_segment()],
    )

    captured_prompt = []
    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return ('{"skill_name":"U","scope":"","sop_md":"new","selectors_md":"s","quirks_md":"q","api_md":"a"}', {})

    with patch("harness.distiller.call_llm", side_effect=fake_call_llm), \
         patch("harness.distiller.config.LLM_KEY", "fake"):
        card = distiller.distill_bucket(bucket, use_llm=True)

    # 验证 prompt 里含旧 skill 内容
    assert "Old SOP" in captured_prompt[0]
    assert "existing rule" in captured_prompt[0]
    assert card.meta["distill_version"] == 2  # 1 → 2

def test_incremental_truncates_to_8000(tmp_path, monkeypatch):
    """验收 4：8000 字符截断。"""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    skill_dir = tmp_path / "skills" / "x.com" / "upload"
    skill_dir.mkdir(parents=True)
    (skill_dir / "_sop.md").write_text("x" * 20000)  # 超长

    bucket = Bucket(
        bucket_id="x.com::upload", domain="x.com",
        canonical_capacity="upload", distill_version=1,
        segments=[_make_segment()],
    )

    captured = []
    with patch("harness.distiller.call_llm", side_effect=lambda p, **k: (captured.append(p), ('{}', {}))[1]), \
         patch("harness.distiller.config.LLM_KEY", "fake"):
        distiller.distill_bucket(bucket, use_llm=True)

    # 旧 skill 部分不应超过 8000 字符
    assert captured[0].count("x") <= 8000 + 100  # 留一点容差给模板文字

def test_first_distill_no_incremental(tmp_path, monkeypatch):
    """验收 5：首次蒸馏（distill_version=0）不进入增量分支。"""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    bucket = Bucket(
        bucket_id="x.com::upload", domain="x.com",
        canonical_capacity="upload", distill_version=0,  # ★ 首次
        segments=[_make_segment()],
    )
    captured = []
    with patch("harness.distiller.call_llm", side_effect=lambda p, **k: (captured.append(p), ('{}', {}))[1]), \
         patch("harness.distiller.config.LLM_KEY", "fake"):
        distiller.distill_bucket(bucket, use_llm=True)
    assert "INCREMENTAL UPDATE" not in captured[0]
```

新建 `tests/test_consolidate.py`：

```python
from unittest.mock import patch
from harness import bucketer
from harness.models import Bucket

def test_apply_merges_combines_segment_ids(tmp_path):
    """验收 9/11：同义桶合并 + segment_ids 去重。"""
    buckets = {
        "x.com::login-with-password": Bucket(
            bucket_id="x.com::login-with-password", domain="x.com",
            canonical_capacity="login-with-password",
            segment_ids=["s1", "s2"],
        ),
        "x.com::sign-in-with-credentials": Bucket(
            bucket_id="x.com::sign-in-with-credentials", domain="x.com",
            canonical_capacity="sign-in-with-credentials",
            segment_ids=["s2", "s3"],  # s2 重复
        ),
    }
    merges = [{
        "target": "login-with-credentials",
        "target_description": "...",
        "sources": ["login-with-password", "sign-in-with-credentials"],
        "reason": "same intent",
    }]
    n = bucketer.apply_merges(buckets, "x.com", merges)
    assert n == 1
    assert "x.com::login-with-credentials" in buckets
    assert "x.com::login-with-password" not in buckets  # 被合并删除
    # s2 去重
    target = buckets["x.com::login-with-credentials"]
    assert sorted(target.segment_ids) == ["s1", "s2", "s3"]
    assert target.dirty is True

def test_apply_merges_single_source_skipped():
    """单 source 不算合并。"""
    buckets = {"x.com::a": _make_bucket("a")}
    merges = [{"target": "b", "sources": ["a"]}]  # 只 1 个 source
    n = bucketer.apply_merges(buckets, "x.com", merges)
    assert n == 0

def test_consolidate_domain_returns_empty_when_one_bucket():
    """验收：少于 2 个桶，合并无意义。"""
    buckets = {"x.com::a": _make_bucket("a")}
    merges = bucketer.consolidate_domain("x.com", buckets)
    assert merges == []

def test_consolidate_mock_llm():
    """mock LLM 返回 merges，验证解析。"""
    buckets = {
        "x.com::a": _make_bucket("a", seg_count=1),  # ❄ 冷桶
        "x.com::b": _make_bucket("b", seg_count=1),  # ❄ 冷桶
    }
    fake_resp = ('{"merges":[{"target":"c","target_description":"...","sources":["a","b"],"reason":"..."}]}', {})
    with patch("harness.bucketer.llm.call_llm_fast", return_value=fake_resp):
        merges = bucketer.consolidate_domain("x.com", buckets)
    assert len(merges) == 1
    assert merges[0]["target"] == "c"
```

## 难点与坑

### 坑 1：增量 prompt 过长

旧 skill 4 个 markdown 拼起来可能上万字。**必须截断到 8000**，否则：
- prompt 超 LLM 上下文窗口
- 成本飙升（Opus 输入 token 贵）

**截断策略**：`existing_card[:8000]` 简单粗暴但有效。更精细的可以「按文件重要性按比例分配」
（selectors.md 多分点，quirks.md 少分点），P1 先用简单版。

### 坑 2：LLM 不遵守 KEEP/REMOVE 规则

增量 addendum 要求 LLM 「保留有效规则、移除被否定的规则」，但 LLM 可能：
- 把旧规则全删了重写（退化为全量）
- 把旧规则全保留（不更新）

**缓解：**
- prompt 里用大写强调 `KEEP` / `REMOVE` / `ADD`
- 在测试里验证关键 selector 是否保留（mock LLM 时控制返回）
- 真实 LLM 的行为只能靠人工 spot check（P3 质量验证层的事）

### 坑 3：consolidate 误合并

LLM 可能把真正不同的能力合并了（如 `login` 和 `register` 都是「账户操作」但语义不同）。

**缓解：**
- 默认 `threshold=8`（只处理桶数 ≥ 8 的域）——桶少时合并价值低、误合并风险高
- consolidate 是 **CLI 手动触发**，用户看完提议再决定（可以加 `--dry-run` 先看提议不应用）
- P1 先不加 `--dry-run`，但 `apply_merges` 设计成幂等可重入（再次跑同一 merges 不会重复合并）

### 坑 4：consolidate 后需要重蒸

合并产生的 target 桶 `dirty=True`，但 consolidate 本身不蒸馏。用户需要手动跑：

```bash
treeforge consolidate          # 合并
treeforge distill --incremental  # 重蒸 dirty 桶（含合并后的 target）
```

**提示信息要明确**（CLI 输出「提示：合并后的桶标记为 dirty，跑 distill 重蒸」），
否则用户以为合并完就完了，实际产物还是旧的。

### 坑 5：测试要构造双状态

P0 的 distill 测试只构造 bucket。P1 增量测试要构造**两个状态**：
1. 磁盘上的旧 skill（`tmp_path/skills/...`）
2. 内存里的 bucket（`distill_version > 0`）

测试 setup 比 P0 复杂。用 `tmp_path` + `monkeypatch.setattr(config, "STATE_DIR", tmp_path)` 隔离。

## 完成后下一步

→ [04-server.md](./04-server.md)（FastAPI 接入层，依赖 B + E）
→ 或先做 [05-windows-adaptation.md](./05-windows-adaptation.md)（server 的前置预制件）
