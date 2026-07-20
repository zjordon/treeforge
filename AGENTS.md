# 项目规范

> 参照姊妹项目 `TreeWalker/CLAUDE.md` 制定，按 treeforge 实际代码现状裁剪调整。
> 与 TreeWalker 的差异点已在本文件内标注「★差异」。

## 代码风格

- **缩进使用 4 个空格**，不使用 tab。★差异：TreeWalker 用 tab，treeforge 现有代码（`harness/`、`adapters/`、`treeforge/`、`tests/` 全部 `.py`）一律用空格，编辑文件时务必保持一致，否则 Edit 工具会因为字符不匹配而失败。
- **行宽 100**（见 `pyproject.toml` `[tool.ruff] line-length = 100`）。E501（超长行）在 ruff 里已 ignore，但新代码仍应尽量控制在 100 列内。
- **格式化 / lint 统一走 ruff**：`uv run ruff format .` 格式化，`uv run ruff check .` 检查。ruff 配置见 `pyproject.toml`：
  - lint 规则集：`E, F, W, I, UP, B`（isort 已启用，first-party 包为 `treeforge / harness / adapters`）。
  - target：`py311`。
- **类型注解**：公共函数尽量带类型注解，模块顶部统一加 `from __future__ import annotations`（现有代码约定）。
- **docstring**：模块级三引号 docstring 用中文，描述该阶段/模块职责（参考 `harness/distiller.py` 顶部风格）。

## 运行环境

- **开发环境是 Windows**，命令行工具主要用 Git Bash（MSYS2）。本仓库测试用的 Bash 命令在 Git Bash 下可跑。
- **优先使用专用工具**而非 shell：读文件用 Read，搜索用 Grep/Glob，改文件用 Edit。需要 `find/grep/cat` 时注意 Git Bash 的 GNU 实现与 PowerShell 行为不同。
- 路径分隔符：仓库内引用统一用正斜杠 `/`（跨平台），绝对路径按平台写。

## 包管理

- **使用 uv 管理 Python 包**，不要使用 pip。安装/更新依赖用 `uv pip install`，同步依赖用 `uv sync --extra dev`。
- **运行 Python 必须用 `uv run`**，例如 `uv run treeforge distill ...`、`uv run python -m pytest ...`、`uv run ruff check .`。直接调用系统 `python` 会因为找不到虚拟环境里的 `treeforge / harness / adapters` 而失败。
- **新增依赖**：编辑 `pyproject.toml` 的 `[project] dependencies`（运行时）或 `[project.optional-dependencies] dev`（开发期），再 `uv sync`。不要手动 `pip install` 后忘了登记。
- **不引入 LLM SDK**（anthropic / openai）——ROADMAP「不做」明确排除，LLM 走 `harness/llm.py` 的纯 urllib 实现。

## 单元测试要求

- **任何代码改动后都必须运行相关单元测试**，确保已有测试全部通过后再结束。
- **新增功能或修改功能时必须同步增加测试用例**，覆盖正常路径和关键边界情况。
- **测试一律用 mock，不要真调 LLM / 真 发网络请求**（见 `tests/test_distiller.py` 的 `patch("harness.distiller.call_llm")` 约定）。
- 测试运行命令：
  - 全量：`uv run python -m pytest tests/ -x -v`
  - 单文件：`uv run python -m pytest tests/test_xxx.py -v`
- pytest 配置见 `pyproject.toml`：`testpaths=["tests"]`、`pythonpath=["."]`、`addopts="-ra -q"`。
- 共享 fixture 放 `tests/conftest.py`，示例 trace 通过 fixture 注入（`bilibili_trace_payload` / `github_trace_payload` / `tmp_output_dir`）。

## 验收命令（P0 最小闭环）

跑通蒸馏链路的端到端命令（来自 ROADMAP），改动涉及 harness 管线时建议跑一遍：

```bash
uv sync --extra dev
uv run treewalker distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm
# 模板模式下也应在 ./data/skills/domain-skills/bilibili.com/ 产出四件套
```

调真 LLM 时需 `.env`：`LLM_KEY` / `LLM_BASE` / `DISTILL_MODEL`（见 README「快速开始」）。**不要把 `.env` 提交**（已在 `.gitignore`）。

## 目录约定

- `harness/` —— 蒸馏层五阶段管线（ADAPT → ATOMIZE → CLASSIFY → BUCKET → DISTILL），核心学习目标。
- `adapters/` —— 输出 adapter（`treewalker` 多文件 vs `browserbc` 单文件），关键缓冲设计。
- `treeforge/` —— Python 包根 + CLI 入口（`treeforge.__main__:main`，console script 名是 `treeforge`，与项目名/包名一致）。
- `examples/` —— 示例 trace JSON，测试 fixture 引用它们。
- `data/` —— 运行时产物（已 gitignore，不要提交）。
- `server/`、`extension/` —— P1/P2 占位，本期 P0 不实现。

修改 harness 五阶段时，留意「★站点特定四字段」是 TreeForge 与 Browser-BC 的核心分叉点：DISTILL 要 capture site-specific selectors，不是 abstract away。

## Git 提交规则

- **修改完代码后不要主动 `git commit`**，也不要主动 `git push`。
- **不要在任务结束时主动询问"要不要提交"**——这相当于变相催促用户提交。完成代码改动并跑完测试后直接结束汇报即可。
- 即使测试全过、ruff 无告警、改动看起来完整且符合 plan，也**不主动提交**。
- 只有当用户**明确要求提交**（如"提交一下"、"commit"、"创建 PR"）时，才执行 git 提交流程。
- 用户授权提交时，仍需遵守通用 git 安全约定：不 force push、不 amend 已发布提交、不跳过 hooks、不提交 `.env` / `data/` / `uv.lock`（`uv.lock` 在本仓库 `.gitignore` 内，与 TreeWalker 一致）。
- 当前默认分支是 `master`（注意不是 `main`）；如需开新功能分支再操作，不要直接在 `master` 上做大改动除非用户要求。
