"""TreeForge 常驻服务（P3）。

把「一次性 capture 命令」重构为「FastAPI 常驻服务 + 控制面板」，
对齐 Browser-BC 架构（常驻后端、扩展随时连、录完不退出）。

模块：
  - server.py       FastAPI app 工厂（采集 router + 蒸馏 router + 配置/状态/产物 router + SPA 托管）
  - distill_api.py  蒸馏后台任务管理（提炼 run_distill_pipeline + job dict + 进度注入）

启动：``uv run treeforge serve``（见 treeforge/serve.py + __main__.py serve 子命令）。
"""

from __future__ import annotations

from server.server import create_app

__all__ = ["create_app"]
