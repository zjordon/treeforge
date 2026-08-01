"""``treeforge serve`` 子命令运行逻辑（P3）。

起 FastAPI 常驻服务（create_app）+ uvicorn 跑。Ctrl+C 由 uvicorn 管（成熟，无 Windows bug）。

用法：uv run treeforge serve --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import logging
from pathlib import Path

from harness import config
from server.server import create_app
from treeforge.capture.ws_discover import DEFAULT_CDP_HOST, DEFAULT_CDP_PORT

logger = logging.getLogger(__name__)


def run_serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    cdp_host: str = DEFAULT_CDP_HOST,
    cdp_port: int = DEFAULT_CDP_PORT,
    captures_dir: Path | str = "./data/captures",
    skills_dir: Path | str | None = None,
    reload: bool = False,
) -> int:
    """起 FastAPI 常驻服务（阻塞，Ctrl+C 退出）。

    Returns:
        退出码（正常 Ctrl+C 退出返 0）。
    """
    import uvicorn

    config.load()
    app = create_app(
        cdp_host=cdp_host,
        cdp_port=cdp_port,
        captures_dir=captures_dir,
        skills_dir=skills_dir,
    )

    print(f"[SERVE] 监听 http://{host}:{port}", flush=True)
    print(f"[SERVE] Chrome 调试：{cdp_host}:{cdp_port}（未开也可启动，蒸馏/配置可用）", flush=True)
    print(f"[SERVE] 采集产物目录：{Path(captures_dir)}", flush=True)
    print(f"[SERVE] 蒸馏产物目录：{skills_dir or config.OUTPUT_DIR}", flush=True)
    print(f"[SERVE] 扩展请连此地址；控制面板浏览器访问 http://{host}:{port}/", flush=True)
    print(flush=True)

    uvicorn.run(app, host=host, port=port, reload=reload)
    return 0
