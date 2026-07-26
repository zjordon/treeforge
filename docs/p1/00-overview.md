# P1 总览：目标、边界、与 P0 的衔接

> 配套阅读：[README.md](./README.md)（5 子任务概览 + 执行顺序）

## 一、P1 要解决什么

P0 已经让 TreeForge 能跑通最小闭环：

```
trace JSON → 五阶段管线 → 4 markdown 文件（跑完即弃）
```

但 P0 有 4 个明显短板，P1 来补：

| P0 短板 | P1 解决方式 | 对应子任务 |
|---|---|---|
| **跑完即弃**——每次跑都是全新状态，无法累积多份 trace 的知识 | 引入持久化层（registry.json / buckets.json / segments.jsonl / checkpoint.json） | [B 持久化层](./02-persistence.md) |
| **必须 CLI**——扩展没法把录制的数据推进来 | 引入 FastAPI 接入层（分块上传 + 异步蒸馏 + 进度轮询） | [D 接入层](./04-server.md) |
| **distill 增量是空壳**——代码有 `distill_version > 0` 分支但旧 skill 没持久化，永远走不到 | 接通 registry 加载旧 skill → 真增量蒸馏 | [C 增量蒸馏](./03-distill-enhancements.md) |
| **redact 只做最小子集**——CVV/OTP/account token 没覆盖，真实 trace 易泄露 | 补齐完整 redact 正则集 | [A redact](./01-redact.md) |
| **server 层未做 Windows 适配**——`import fcntl` 会崩，`Path.rename` 会 WinError 183 | 引入 msvcrt 分支 + os.replace + ResilientStream | [E Windows 适配](./05-windows-adaptation.md) |

## 二、P1 完成后的形态

```
                ┌─────────────────────────────┐
                │  扩展（P2）/ 测试 curl       │
                │  分块上传 trace JSON         │
                └──────────────┬──────────────┘
                               │ HTTP
┌──────────────────────────────┴──────────────────────────────┐
│  接入层（P1 新）server/server.py                              │
│  ─ 4 端点（init/put chunk/finalize/status）                  │
│  ─ 认证（Bearer + api-keys.json）                            │
│  ─ 异步蒸馏触发（daemon thread + _PIPELINE_LOCK）            │
│  ─ 进度轮询（_PROGRESS dict + harness.progress 注入）        │
└──────────────┬──────────────────────────────────────────────┘
               │ 进程内 import
┌──────────────┴──────────────────────────────────────────────┐
│  蒸馏层 harness/（P0 已实现 + P1 增强）                       │
│  ─ 五阶段管线（不变）                                        │
│  ─ 完整 redact（P1 增强）                                    │
│  ─ 真增量蒸馏（P1 增强：旧 skill 从持久化层加载）            │
│  ─ consolidate CLI 子命令（P1 新）                           │
└──────────────┬──────────────────────────────────────────────┘
               │ 读写
┌──────────────┴──────────────────────────────────────────────┐
│  持久化层（P1 新）data/harness/                              │
│  ─ checkpoint.json（管线进度）                              │
│  ─ buckets.json（桶定义，跨会话累积）                       │
│  ─ segments.jsonl（append-only 已分类 segment）             │
│  ─ registry.json（skill 索引）                              │
│  ─ skills/<domain>/<capacity>/{SKILL.md, meta.json, ...}    │
└─────────────────────────────────────────────────────────────┘
```

**关键变化**：P0 是「trace 进 → 文件出 → 结束」，P1 是「trace 进 → 累积到持久层 → 增量蒸馏 → 文件出」，**状态跨会话保留**。

## 三、P1 的边界（不做什么）

明确排除，避免范围蔓延：

| 范围 | P1 状态 | 何时做 |
|---|---|---|
| MV3 扩展真实录制 | ❌ | P2 |
| server 静态面板（panel） | ❌ P1 只做 API | 不做（TreeForge 是 CLI/库项目） |
| MCP stdio 检索 | ❌ | P4（TreeWalker 用文件注入不需要） |
| 质量验证层 | ❌ | P3 |
| 桶合并 consolidate 自动触发 | ❌ 只做 CLI 手动 | 未来可选 |

## 四、与 P0 的衔接（代码改动面）

P1 会改动 P0 的以下模块，需要保持向后兼容（CLI `treeforge distill` 仍要能跑）：

| P0 模块 | P1 改动 |
|---|---|
| `harness/adapter.py` | `_redact_value()` 扩展完整正则集 + `value_of`/`label_of` 二级处理 |
| `harness/bucketer.py` | 加 `load_buckets()` / `save_buckets()` + `consolidate_domain()` / `apply_merges()` |
| `harness/distiller.py` | `distill_bucket()` 增量分支真接通（从 registry 加载旧 skill） |
| `harness/registry.py` | 从空实现变成完整实现（`load_registry` / `save_registry` / `update_registry_entry`） |
| `harness/config.py` | 加持久化相关路径常量（`STATE_DIR` / `TRACKS_DIR`）+ server 配置项 |
| `harness/__init__.py`（新） | 加 `run_ingest_file()` / `run_distill()` 编排函数（server 和 CLI 共用） |
| `harness/checkpoint.py`（新） | 管线进度追踪 |
| `harness/install.py` | `install_cards()` 增加写 `evidence.jsonl` + `meta.json` |
| `treeforge/__main__.py` | 加 `consolidate` 子命令 |
| `server/server.py`（新） | FastAPI 接入层 |

**P0 的 CLI（`treeforge distill`）必须保持可用**——P1 所有改动都不能破坏 P0 验收命令。

## 五、技术栈新增

P1 引入一个新运行时依赖：

| 依赖 | 用途 | 引入位置 |
|---|---|---|
| `fastapi` | 接入层 HTTP 服务 | 子任务 D |
| `uvicorn` | ASGI 服务器（开发模式） | 子任务 D |

加入 `pyproject.toml` 的 `[project] dependencies`（不是 dev 依赖，因为运行 server 时需要）。

**为什么是 FastAPI？** Browser-BC 同栈，且 FastAPI 的 Pydantic 集成与 TreeForge 已有的 Pydantic 模型天然契合（请求/响应 schema 直接复用）。

**其它依赖**：不引入。LLM 仍走 urllib（不引入 SDK），文件锁用 msvcrt/fcntl（标准库），日志用标准库 logging。

## 六、环境变量新增（裸名风格）

延续 P0 的裸名约定（详见 `.env.example`），P1 新增：

```bash
# server 配置
PORT=8099                          # server 监听端口
API_KEY=                           # Bearer token，空则用默认 jfl-local-dev-key
LLM_INSECURE=true                  # server 层强制 true（自签/网关用）

# 持久化路径（一般用默认）
# STATE_DIR=./data/harness         # 状态文件根
# TRACKS_DIR=./data/traces         # 原始 trace 存放
```

这些会加到 `.env.example`。

## 七、P1 的难点预告

P1 比 P0 难在三个地方，提前预警：

### 难点 1：跨会话状态的正确性

P0 没有持久化，每次跑都是干净的。P1 引入持久化后，要处理：

- **增量加载**：什么时候读、读哪些文件、读到什么版本
- **并发写**：多个 finalize 同时到达怎么办（_PIPELINE_LOCK）
- **断点续传**：进程崩在 distill 中间，重启后怎么续上（checkpoint）
- **schema 演进**：buckets.json version=3，将来加字段怎么兼容旧文件（`.get(key, default)`）

### 难点 2：增量蒸馏的逻辑验证

P0 的 distill 永远是首次蒸馏。P1 让 `distill_version > 0` 分支真生效后，要验证：

- 旧 skill 正确加载 + 截断到 8000 字符
- LLM 真的「保留有效规则、移除被新证据否定的规则、补充新发现」
- 不会因为增量 prompt 过长导致 token 爆掉

测试要构造「已有旧 skill + 新 segment」的双状态场景，比 P0 的单状态测试复杂。

### 难点 3：Windows 文件锁的测试覆盖

`msvcrt.locking` 在 Linux 测试机上跑不到那个分支。要专门设计：

- 在 Windows CI 上跑锁测试
- 或用 mock 验证锁调用的协议（不真测锁行为）
- 至少要有一个「并发 finalize 不损坏 buckets.json」的集成测试

## 八、下一步

按推荐顺序开始：

1. 读 [01-redact.md](./01-redact.md)（最轻，热身）
2. 读 [02-persistence.md](./02-persistence.md)（接入层前置，最重要）
3. 按 README 的执行顺序往下推

或者先看 [06-acceptance.md](./06-acceptance.md) 了解整体验收标准，再回头看具体实现。
