# 项目规范（CLAUDE.md）

本仓库的完整开发规范在根目录 `AGENTS.md`，它是唯一权威来源（single source of truth）。
本文件仅作为入口存在，两者如有出入，一律以 `AGENTS.md` 为准。

阅读、编写或审查本仓库代码前，必须先完整阅读 `AGENTS.md` 并遵守其中约定，要点包括：

- 代码风格：4 空格缩进（不用 tab）、行宽 100、统一走 `uv run ruff format .` / `uv run ruff check .`
- 运行环境：Windows + Git Bash；运行 Python 一律 `uv run`，包管理只用 uv，不用 pip
- 单元测试：任何改动后必须跑相关测试；新增/修改功能必须同步加测试；测试一律 mock，不真调 LLM、不发真实网络请求
- 不引入 LLM SDK（anthropic / openai），LLM 调用走 `harness/llm.py` 的纯 urllib 实现
- Git：不要主动 commit / push，不要提交 `.env` / `uv.lock`
- 目录约定：`harness/` 蒸馏五阶段管线、`adapters/` 输出 adapter、`treeforge/` 包根与 CLI、`examples/` 示例 trace、`data/` 运行时产物

@AGENTS.md
