# TreeForge（树锻）

> **人走一遍，树锻一生。**

TreeForge 把人类在浏览器里的示教操作，蒸馏成可复用的 **站点特定 skill 文件**，最终产物供 [TreeWalker](https://github.com/) 消费。

参照 [Browser-BC（Journey Forge Local）](https://github.com/) 实现机制的独立学习项目。命名致敬——从「旅程锻造」到「树之锻造」，两条路线同一片作坊。

| 锚点 | 关联 |
|---|---|
| **Tree** | TreeWalker 的「Tree」——同一棵树的不同侧面 |
| **Forge** | skill 锻造/蒸馏——把一次操作锻成永久技能 |
| **树** | 树哥 AI 的「树」——个人品牌印记 |

## 两个目标

1. **学习**——吃透 Browser-BC 的采集层（MV3 扩展）+ 蒸馏层（五阶段管线）+ 接入层（FastAPI）三块核心机制。
2. **实用**——产出的 skill 文件落到 TreeWalker 的 `domain-skills/<host>/` 目录（文件系统注入方式，零运行时依赖）。

## 为什么独立项目

TreeWalker 的采集层是**重放语义**（忠于原操作、可重新执行），Browser-BC 的采集层是**蒸馏语义**（抽象、跨次复用）。两者在采集层就分叉，强行让 TreeWalker 采集层兼顾蒸馏会两头不讨好。独立项目采集层语义纯粹，蒸馏产物通过文件注入 TreeWalker，解耦清晰。

## 与 Browser-BC 的核心分叉

**不照搬 Browser-BC 的单 `SKILL.md` 通用 SOP 格式**，采用「站点特定知识为主，SOP 为骨架」的多文件结构：

```
<output_dir>/domain-skills/<host>/
├── _sop.md          # 骨架：这个站点常见任务流程（量少，Browser-BC 风格蒸馏）
├── selectors.md     # 血肉：稳定 selector、AX name、元素定位（量大、可操作）
├── quirks.md        # 怪癖：隐藏等待、SPA 导航、框架行为、反爬检测
└── api.md           # 私有 API、URL 模式、隐藏端点
```

理由：browser-harness 文件注入期望"站点特定、拿到就能用"的知识，不是通用 SOP。`distiller.py` 的 prompt 按这个 spec 设计——这是和 Browser-BC 的主要分叉点（Browser-BC 明确要求"Abstract away site-specific selectors and IDs"，TreeForge 反过来要求"capture site-specific selectors"）。

## 技术栈

| 层 | 技术选型 | 理由 |
|---|---|---|
| 服务端 / 蒸馏层 | **Python ≥ 3.11** | 复刻 Browser-BC `harness/` 的纯标准库哲学 |
| 包管理 | **uv** | 快、现代、TreeWalker 已在用，保持一致 |
| lint / format | **ruff** | 同上 |
| 测试 | **pytest** | 同上 |
| 数据模型 | **Pydantic v2** | trace/skill 结构化 |
| LLM 客户端 | **纯标准库 urllib**（不用 SDK） | Browser-BC 哲学，零运行时依赖 |
| 扩展框架 | **WXT + React + TypeScript** | Browser-BC 同栈，MV3 最佳实践 |
| 扩展构建 | **pnpm** | WXT 推荐 |
| 扩展 UI 存储 | **Dexie (IndexedDB)** | Browser-BC 同栈，MV3 SW 崩溃恢复靠 DB |
| 服务端框架 | **FastAPI**（P1 引入，本期 CLI 跑通） | Browser-BC 同栈 |

## 快速开始（P0 最小闭环）

```bash
# 1. 安装
uv sync --extra dev

# 2. 配置 LLM（.env）
cat > .env <<EOF
LLM_KEY=sk-xxx
LLM_BASE=https://api.anthropic.com
DISTILL_MODEL=claude-opus-4-8
EOF

# 3. 跑通蒸馏
uv run treewalker distill examples/bilibili-upload.trace.json --output ./data/skills
# 或：uv run python -m treeforge distill examples/bilibili-upload.trace.json --output ./data/skills

# 4. 看产物
ls ./data/skills/domain-skills/bilibili.com/
# 应出现 _sop.md / selectors.md / quirks.md / api.md（至少一个非空）
```

## 目录结构

```
treeforge/
├── harness/        # 蒸馏层（核心学习目标，本期 P0 落地）
├── adapters/       # 输出 adapter（关键缓冲设计：treewalker 多文件 vs browserbc 单文件）
├── treeforge/      # Python 包根（CLI 入口）
├── server/         # 接入层（P1 引入，本期占位）
├── extension/      # 采集层（P2 引入，本期 WXT 脚手架）
├── examples/       # 示例 trace（本期 P0 用它跑通）
├── data/           # 运行时数据（gitignore）
└── tests/          # 测试
```

详见 [ARCHITECTURE.md](./ARCHITECTURE.md) 和 [ROADMAP.md](./ROADMAP.md)。

## 相关文档

- [初始化方案](./init-plan.md)（如果保留在本仓库）
- Browser-BC 概念总览 / 四层架构 / 五阶段蒸馏 / MV3 采集层 / server.py / Windows 适配（均在知识库 `ai/agent/` 下）

## License

MIT
