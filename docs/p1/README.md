# TreeForge P1 拆解文档集

> P1 = 把 TreeForge 从「CLI 能跑」变成「扩展能联」+「跨会话累积」+「蒸馏质量提升」。
>
> 本目录是 P1 的**实施计划**（不是已完成文档）。每个子任务一篇文档，含：
> 输入输出 / 算法 / 验收点 / 依赖 / 难点。

---

## 一句话 P1

> **持久化层 + 接入层 + 管线增强 + Windows 适配**——让 TreeForge 从一次性 CLI 升级成可累积、可联动的本地服务。

## 5 个子任务

| # | 子任务 | 文档 | 工作量 | 依赖 |
|---|---|---|---|---|
| A | **完整 redact** | [01-redact.md](./01-redact.md) | 小（半天） | 无 |
| B | **持久化层** | [02-persistence.md](./02-persistence.md) | 中（2-3 天） | 无 |
| C | **增量蒸馏 + consolidate** | [03-distill-enhancements.md](./03-distill-enhancements.md) | 中（2 天） | B |
| D | **FastAPI 最小接入层** | [04-server.md](./04-server.md) | 大（3-4 天） | B + E |
| E | **server 层 Windows 适配** | [05-windows-adaptation.md](./05-windows-adaptation.md) | 小（1 天） | 无（可独立预制） |

## 依赖图

```
   ┌─────────────────────────────────────────────────────┐
   │                                                     │
   ▼                                                     │
[A redact]          （独立，可随时做）                    │
                                                       │
                                                       │
[B 持久化层] ─────┬─────────────► [C 增量蒸馏+consolidate]
   │              │
   │              │
   │              │              [E Windows 适配]（独立预制）
   │              │                    │
   │              │                    │
   │              └────────────► [D FastAPI 接入层] ◄──┘
   │
   └── B 是 C 和 D 的共同前置（都需要持久化 buckets/registry）
```

## 推荐执行顺序

按依赖关系 + 风险递增排序：

```
Step 1: A redact（热身，验证开发环境 + 测试流程）
   ↓
Step 2: B 持久化层（接入层和增强的前置）
   ↓
Step 3: E Windows 适配（server 之前的预制件，独立模块）
   ↓
Step 4: C 增量蒸馏 + consolidate（基于 B，CLI 可验证）
   ↓
Step 5: D FastAPI 接入层（最后做，依赖 B + E）
```

**为什么这个顺序：**
- **A 先做**：最轻，半天搞定，验证 P1 开发环境（uv/pytest/ruff 都还跑得通）
- **B 第二**：是 C 和 D 的共同前置。没它 C 的「旧 skill 加载」无处可来，D 的「断点续传」无处可去
- **E 第三**：是 D 的前置但可独立。先把 `_ResilientStream`/`_file_lock`/`_atomic_write`/双写日志这几个**通用工具**写好，D 直接用
- **C 第四**：基于 B，纯 CLI 可验证（不依赖 server）。先做完它，D 接入时蒸馏链路已经稳了
- **D 最后**：最重，集成 B + E，风险最高。等前面都稳了再做

## 配套阅读

- **P0 文档集** [../p0/README.md](../p0/README.md) — P1 的所有讨论假设你已理解 P0 五阶段管线
- **项目根 ROADMAP.md** [../../ROADMAP.md](../../ROADMAP.md) — P1 在 P0~P4 分期里的位置
- **项目根 ARCHITECTURE.md** [../../ARCHITECTURE.md](../../ARCHITECTURE.md) — 四层架构，P1 主要落地「接入层」+「持久化」（跨在蒸馏层和输出层之间）

## 关键决策（已确认，写文档时已采纳）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 配置命名 | **裸名**（PORT / API_KEY 等） | 与 P0 的 LLM_KEY/LLM_BASE 一致 |
| skills_root 默认 | **./data/skills** | 与 P0 输出目录一致，treewalker adapter 直接落 domain-skills/<host>/ |
| consolidate 触发 | **CLI 手动**（`treeforge consolidate`） | 与 Browser-BC 一致，安全可控 |
| server 范围 | **最小 server**（4 端点 + 认证 + 异步蒸馏 + 进度） | 不做面板、不做 MCP 入口（P1 不需要） |

## 每篇文档的结构

为了便于实现，每篇子任务文档都遵循统一结构：

1. **这个任务干什么**——一句话目标
2. **输入输出**——具体的数据形态变化
3. **实现细节**——copy-paste 级代码（来自 Browser-BC 参考实现，按 TreeForge 实际裁剪）
4. **依赖与前置**——需要先做什么
5. **验收点**——怎么知道做完了
6. **测试要求**——至少要加哪些测试
7. **难点与坑**——提前预警

## 一条提醒

P1 引入了**第一次跨会话状态**（持久化）。这意味着：
- 测试要清理状态目录（避免污染）
- 增量逻辑的正确性比首次逻辑更难验证（需要构造「旧状态 + 新输入」）
- Windows 文件锁和原子写在测试里要专门覆盖

这些是 P1 比 P0 难的地方，也是 P1 价值所在。
