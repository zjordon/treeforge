# TreeForge（树锻）

> **人走一遍，树锻一生。**

TreeForge 把人类在浏览器里的示教操作，蒸馏成可复用的 **站点特定 skill 文件**，最终产物供 [TreeWalker](https://github.com/zjordon/treewalker) agent 消费——不改 agent 逻辑，给 agent 喂知识，提升探索准确率。

参照 [Browser-BC（Journey Forge Local）](https://github.com/) 实现机制的独立学习项目。命名致敬——从「旅程锻造」到「树之锻造」，两条路线同一片作坊。

| 锚点 | 关联 |
|---|---|
| **Tree** | TreeWalker 的「Tree」——同一棵树的不同侧面 |
| **Forge** | skill 锻造/蒸馏——把一次操作锻成永久技能 |
| **树** | 树哥 AI 的「树」——个人品牌印记 |

## 闭环定位

TreeForge 在「人工 → 蒸馏 → agent → 重放」闭环中的位置：

```
人工探索（一次，慢但准）
    ↓ TreeForge 蒸馏
domain-skills/<host>/ skill 文件（给 LLM 看的站点知识）
    ↓ 注入 TreeWalker agent 上下文
agent 自动探索（多次，快，有 skill 加持更准）   ← TreeForge 的价值落点
```

**核心价值**：skill 是「给 LLM 看的上下文提示」，精度要求「LLM 能看懂」而非「CDP 能精确匹配」。这让采集层比 record-replay 轻，但比 Browser-BC（去站点化）重。

## 与 Browser-BC 的核心分叉

**不照搬 Browser-BC 的单 `SKILL.md` 通用 SOP 格式**，采用「站点特定知识为主，SOP 为骨架」的多文件结构：

```
<output_dir>/domain-skills/<host>/
├── _sop.md          # 骨架：这个站点常见任务流程
├── selectors.md     # 血肉：稳定 selector、AX name、元素定位
└── quirks.md        # 怪癖：隐藏等待、SPA 导航、框架行为、反爬检测
```

> P0.5 实测后已删 `api.md`（无网络采集时恒为「未观察到私有 API」零信息）。

理由：browser-harness 文件注入期望「站点特定、拿到就能用」的知识，不是通用 SOP。`distiller.py` 的 prompt 按这个 spec 设计——这是和 Browser-BC 的主要分叉点（Browser-BC 明确要求「Abstract away site-specific selectors and IDs」，TreeForge 反过来要求「capture site-specific selectors」）。

## 技术栈

| 层 | 技术选型 | 理由 |
|---|---|---|
| 蒸馏层（Python） | **Python ≥ 3.11** | 纯标准库哲学（不用 LLM SDK） |
| 包管理 | **uv** | 快、现代 |
| lint / format | **ruff** | 零配置 |
| 测试 | **pytest**（183 测试，asyncio） | mock，不连真 LLM / 不发真网络请求 |
| 数据模型 | **Pydantic v2** | trace/skill 结构化 |
| LLM 客户端 | **纯标准库 urllib**（不用 SDK） | 零运行时依赖，双协议探测（Anthropic / OpenAI 兼容） |
| 采集层（扩展） | **WXT + React + TypeScript**（MV3） | Chrome 扩展最佳实践 |
| 扩展 UI 存储 | **Dexie (IndexedDB)** | MV3 SW 崩溃恢复靠 DB |
| 接入层 | **FastAPI + uvicorn**（P3） | 常驻服务，采集 + 蒸馏 + 控制面板 |

## 快速开始

### 安装

```bash
uv sync --extra dev
```

### 配置 LLM（.env）

```bash
cp .env.example .env
# 编辑 .env：至少填 LLM_KEY（其余默认值可用）
#   LLM_KEY      API key
#   LLM_BASE     兼容 Anthropic/OpenAI 的端点（默认 https://api.anthropic.com）
#   DISTILL_MODEL / CLASSIFY_MODEL  蒸馏/分类模型名
```

> 不配 LLM_KEY 也能跑（自动退回模板模式，产物质量低，仅供链路验证）。

### 蒸馏一份 trace → skill（CLI）

```bash
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
# 或：uv run python -m treeforge distill examples/bilibili-upload.trace.json --output ./data/skills

ls ./data/skills/domain-skills/bilibili.com/
# → _sop.md / selectors.md / quirks.md（三件套）
```

模板模式（不调 LLM）：`uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm`

### 三种子命令

| 命令 | 用途 |
|---|---|
| `treeforge distill <trace.json>` | 蒸馏一份 trace → skill 文件 |
| `treeforge capture` | 起 aiohttp 采集后端 + 连 Chrome CDP，扩展发事件，录完导出 trace（一次性命令） |
| `treeforge serve` | 起 FastAPI **常驻服务**：采集（4 端点，扩展零改动）+ 蒸馏 API + 配置/状态 API + 控制面板 SPA |
| `treeforge info` | 打印当前生效配置（脱敏 key） |

### 常驻服务模式（P3）

```bash
uv run treeforge serve --port 8765
# Chrome 未开也能启动（蒸馏/配置/状态可用）；扩展请连此地址
# 浏览器访问 http://127.0.0.1:8765/ 看控制面板
```

关键端点：
- 采集（扩展用）：`POST /start` `/ingest` `/stop`，`GET /health`
- 蒸馏：`POST /api/distill`（返 job_id）→ 轮询 `GET /api/distill/{id}`
- 配置：`GET/POST /api/config`；状态/产物：`GET /api/status` `/api/captures` `/api/skills`

### 采集真实操作（配合扩展）

1. 以远程调试端口启动 Chrome：`chrome --remote-debugging-port=9223 --user-data-dir=<profile>`
2. 加载扩展：`cd extension && npm install && npm run build`，然后 Chrome 加载 `extension/.output/chrome-mv3`（或开发模式 `npm run dev`）
3. 起后端：`uv run treeforge serve`（或一次性 `treeforge capture`）
4. 扩展 popup 点「开始录制」→ 操作浏览器 → 点「停止」→ 后端导出 trace 到 `data/captures/`
5. 蒸馏：`uv run treeforge distill data/captures/<name>/trace.json --output ./data/skills`

## 目录结构

```
treeforge/
├── harness/        # 蒸馏层：五阶段管线（ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL）
├── adapters/       # 输出 adapter（treewalker 多文件 vs browserbc 单文件）
├── treeforge/      # Python 包根 + CLI 入口（distill/capture/serve/info）
│   └── capture/    # 采集层：CdpSession / Collector / stage 判定 / distill_schema 双端契约
├── server/         # 接入层：FastAPI 常驻服务（serve）+ 蒸馏后台任务 + 控制面板 SPA
├── extension/      # 采集层：WXT + React MV3 扩展（background/content/popup/shared）
├── examples/       # 示例 trace（bilibili-upload + github-login）
├── tools/          # 辅助脚本（rerun_to_trace / reverse_trace / analyze_stage_threshold）
├── data/           # 运行时数据（gitignore：captures/skills）
├── tests/          # 测试（183 个）
└── docs/           # 设计文档（p0/p1/p2/p3 阶段归档）
```

## 相关文档

- [架构（四层分层 + 五阶段蒸馏）](./ARCHITECTURE.md)
- [路线图（P0–P3.5，已完成里程碑；P4 检索层明确不做）](./ROADMAP.md)
- P2 采集层调试复盘：`docs/p2/debug-retrospective.md`
- P3 常驻服务方案：`docs/p3/serve-plan.md` + 落地计划 `docs/p3/p3-implement-plan.md`

## License

本项目采用 [CC BY-NC 4.0](LICENSE) 协议开源，仅供非商业用途。
