# TreeForge 架构

> 四层分层 + 与 Browser-BC 的对照。本文档配合 [init-plan.md](./init-plan.md) 阅读。

## 一、四层分层

```
┌─────────────────────────────────────────────────────────────────┐
│  采集层 Capture        extension/  (WXT + React + TS, MV3)       │
│  人走一遍：录 DOM 事件 / 选择器 / 网络 / 表单                    │
│  → chunked 上传（gzip + sha256 + 可恢复）                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │ (P1: HTTP)        (P0: 直接读 JSON 文件)
┌──────────────────────────────┴──────────────────────────────────┐
│  接入层 Ingest          server/  (FastAPI, P1 引入)              │
│  分块上传协议 / 组装 trace / 异步触发蒸馏 / 进度轮询             │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│  蒸馏层 Distill         harness/  (纯标准库 Python, P0 落地)     │
│  五阶段管线：                                                    │
│    ① ADAPT     adapter.py      原始 trace → Trace                │
│    ② ATOMIZE   atomizer.py     Trace → Segment[]                 │
│    ③ CLASSIFY  classifier.py   Segment → domain::capacity        │
│    ④ BUCKET    bucketer.py     按 capacity 归桶 → Bucket[]       │
│    ⑤ DISTILL   distiller.py    Bucket → SkillCard ★核心分叉      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│  输出层 Output          adapters/ + harness/install.py           │
│  关键缓冲：同一份 SkillCard 可产两种形态                          │
│    ├── treewalker_adapter  → domain-skills/<host>/{4 文件}       │
│    └── browserbc_adapter   → skills/<domain>/<cap>/SKILL.md      │
└─────────────────────────────────────────────────────────────────┘
```

### P0 落地的层

| 层 | P0 状态 |
|---|---|
| 采集层 | 仅 WXT 脚手架（结构，不实现） |
| 接入层 | 占位（`server/.gitkeep`） |
| **蒸馏层** | **完整实现**（五阶段管线 + LLM 客户端 + 模板 fallback） |
| **输出层** | **完整实现**（treewalker + browserbc 双 adapter） |

P0 用 `CLI` 替代接入层：`uv run treeforge distill <trace.json>` 直接读文件 → 跑管线 → 写产物。

## 二、与 Browser-BC 的对照

| 维度 | Browser-BC | TreeForge |
|---|---|---|
| **采集语义** | 蒸馏语义（抽象、跨次复用） | 同（独立项目原因就是两者采集层就分叉） |
| **采集层落点** | TreeWalker 重放语义 vs Browser-BC 蒸馏语义 | 借鉴 Browser-BC 蒸馏语义 |
| **distill 产物形态** | 单 `SKILL.md`（**去站点化**通用 SOP） | **多文件站点特定知识卡**（4 文件） |
| **distill prompt 取向** | "Abstract away site-specific selectors and IDs" | **反过来**："Capture site-specific selectors" |
| **消费方式** | MCP stdio 检索（LLM-as-ranker） | **文件注入**到 `domain-skills/<host>/`（零运行时依赖） |
| **检索层** | 两层召回（query_top_k + synthesize_playbook） | P4 才做（TreeWalker 文件注入不需要） |
| **接入层** | FastAPI 单文件 + 分块上传 | P1 才做（P0 用 CLI） |
| **Python 数据模型** | `@dataclass` | **Pydantic v2** |
| **LLM 客户端** | 标准库 urllib，双协议探测 | **同**（复刻 + 双协议 `/anthropic` 修复） |
| **env 前缀** | 不统一（`JFL_*` / `SF_*` / 裸） | **干净裸名**（`LLM_KEY` / `LLM_BASE` / `DISTILL_MODEL` / `OUTPUT_DIR`） |
| **Windows 适配** | msvcrt 文件锁 + `os.replace` 原子写 | **同**（init-plan §八约束 7） |
| **运行时依赖** | 纯标准库（无 SDK） | **同**（Pydantic 是唯一例外） |

## 三、核心分叉点：distill 产物形态

这是 TreeForge 与 Browser-BC 的**主要分歧**（init-plan §五）。

### Browser-BC 的 SKILL.md（去站点化通用 SOP）

```
skills/<domain>/<capacity>/SKILL.md
├── GENERAL PATTERN
├── Entry preconditions
├── Step-by-step procedure (无硬编码 selector)
├── Milestones / Exit conditions
├── False terminal states
├── Common failure modes + recovery
├── Anti-drift boundaries
└── Red lines
```

适用场景：MCP 检索消费——agent 拿到一个抽象能力卡，自己去任意站点执行。

### TreeForge 的多文件站点特定知识卡

```
<output_dir>/domain-skills/<host>/
├── _sop.md          骨架：这个站点常见任务流程（量少）
├── selectors.md     血肉：稳定 selector、AX name（量大、可操作）★最重要
├── quirks.md        怪癖：隐藏等待、SPA 导航、框架行为、反爬
└── api.md           私有 API、URL 模式、隐藏端点
```

适用场景：文件注入消费——agent 导航到 `<host>` 时，browser-harness 直接读这批 `.md`
塞进上下文，**拿到就能用**。

### 为什么分叉

详见知识库 `skill-auto-evolution-migration.md`："record the map, not the diary"——
browser-harness 文件注入期望**站点特定**知识（真实 selector、真实 URL），不是通用 SOP。
通用 SOP 对文件注入没有直接可操作性（agent 还得自己找 selector）。

### TreeWalker 消费侧约束（驱动输出格式）

来源：知识库 `browser-agent/dev-plan.md` + browser-harness `helpers.py`：

```python
# goto_url 时，若 BH_DOMAIN_SKILLS=1
hostname = urlparse(url).hostname
domain_dir = AGENT_WORKSPACE / "domain-skills" / hostname
if domain_dir.is_dir():
    return {**result, "domain_skills": [sorted .md files][:10]}
```

四条硬约束：

1. **按 `hostname` 索引**——`domain-skills/<hostname>/`，hostname 是 `urlparse(url).hostname`
2. **只读 `.md` 文件**——按字母序排序
3. **硬上限 10 个**——`[:10]`，所以文件名顺序很重要
4. **`_sop.md` 下划线前缀**——确保字母序排第一，作为入口索引

TreeForge 的 4 文件远低于 10 上限，命名固定。

## 四、目录结构（本期 P0）

```
treeforge/
├── README.md
├── ARCHITECTURE.md            ← 本文档
├── ROADMAP.md
├── pyproject.toml
├── .python-version
├── .gitignore
│
├── harness/                   # 蒸馏层（P0 核心）
│   ├── __init__.py
│   ├── adapter.py             # ① ADAPT
│   ├── atomizer.py            # ② ATOMIZE
│   ├── classifier.py          # ③ CLASSIFY（串行增量命名）
│   ├── bucketer.py            # ④ BUCKET
│   ├── distiller.py           # ⑤ DISTILL ★核心 prompt
│   ├── registry.py            # 检索（P4，本期空实现）
│   ├── install.py             # 原子写（os.replace）
│   ├── llm.py                 # urllib 双协议客户端
│   ├── config.py              # .env 配置
│   ├── progress.py            # 进度上报
│   └── models.py              # Pydantic 模型
│
├── adapters/                  # 输出层（关键缓冲）
│   ├── __init__.py
│   ├── base.py                # OutputAdapter 抽象
│   ├── browserbc_adapter.py   # 单 SKILL.md（对照）
│   └── treewalker_adapter.py  # 多文件（默认，给 TreeWalker）
│
├── treeforge/                 # Python 包根
│   ├── __init__.py
│   └── __main__.py            # CLI（distill / info）
│
├── server/                    # 接入层（P1，占位）
├── extension/                 # 采集层（P2，WXT 脚手架）
│
├── examples/                  # 示例 trace（P0 用它跑通）
│   ├── README.md
│   ├── bilibili-upload.trace.json
│   └── github-login.trace.json
│
├── data/                      # 运行时数据（gitignore）
└── tests/                     # 测试
    ├── test_atomizer.py
    ├── test_classifier.py
    ├── test_distiller.py
    ├── test_adapters.py
    └── test_llm.py
```

## 五、五阶段数据流

```
trace.json
   │
   ▼  adapter.adapt()
Trace(host, events[TraceEvent], task_instruction)
   │
   ▼  atomizer.atomize()      去噪 + 4 边界规则 + 合并/拆分
Segment[](segment_id, domain, events, event_summary, ...)
   │
   ▼  classifier.classify()   串行 + 增量命名（LLM 或启发式）
[(Segment, CapacityLabel), ...]
   │
   ▼  bucketer.bucket()       按 domain::capacity 归并
Bucket[](bucket_id, segments, capacity_labels, dirty)
   │
   ▼  distiller.distill_buckets()   LLM 或模板 fallback
SkillCard[](domain, capacity, sop_md, selectors_md, quirks_md, api_md)
   │
   ▼  install.install_cards(adapter)
<output_dir>/domain-skills/<host>/_sop.md, selectors.md, ...
```

## 六、关键设计决策

### 6.1 零运行时依赖（Pydantic 例外）

LLM 走 `urllib`，不用 `anthropic` / `openai` SDK。配置走朴素 `KEY=VALUE` 解析，
不用 `python-dotenv`。CLI 走 `argparse`，不用 `typer`。

### 6.2 LLM 双协议探测（Windows 关键修复）

```python
def is_anthropic(base: str) -> bool:
    b = base.lower()
    return "anthropic.com" in b or "/anthropic" in b   # ← /anthropic 路径段修复
```

- Anthropic → `POST {base}/v1/messages` + `x-api-key` + `anthropic-version: 2023-06-01`
- OpenAI → `POST {base}/v1/chat/completions` + `Authorization: Bearer`

`/anthropic` 匹配第三方网关（如智谱 BigModel `/api/anthropic`），避免误判为 OpenAI 走错端点 404。

### 6.3 串行增量命名（CLASSIFY 核心）

**绝对不能并发分类**。否则同一个能力会被命名为 `login` / `sign-in` / `authenticate`
三个名字，散落到三个桶。正确做法：每个 segment 分类后，新 capacity 名立即可用于下一个 segment。

### 6.4 原子写（Windows 关键）

```python
def atomic_write_text(path, content):
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)   # NOT path.rename / os.rename（WinError 183）
```

### 6.5 adapter 缓冲（输出层解耦）

`SkillCard` 是中间表示，与输出形态解耦。`treewalker_adapter` 产多文件给 TreeWalker，
`browserbc_adapter` 产单 SKILL.md 作学习对照。CLI `--adapter` 切换。

### 6.6 模板 fallback（链路鲁棒性）

无 `LLM_KEY` 或 LLM 调用失败时，distiller 退回模板从 `event_summary` 提炼四字段。
保证 P0 链路在没配 LLM 时也能跑通、产出非空文件（用于验证 adapter/install 正确）。
