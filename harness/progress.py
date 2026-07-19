"""进度上报。

本期 P0 先用 print 实现一个可替换的 reporter 接口（对齐 Browser-BC 的 set_reporter 模式），
后续 server 层（P1）注入内存 dict reporter 时无需改调用点。

用法：

    from harness import progress
    progress.set_reporter(my_reporter)   # 可选
    progress.report("distill", current=1, total=3, detail="bilibili.com")
"""

from __future__ import annotations

from collections.abc import Callable

Reporter = Callable[[str, int, int, str], None]

_reporter: Reporter | None = None


def set_reporter(reporter: Reporter | None) -> None:
    global _reporter
    _reporter = reporter


def get_reporter() -> Reporter | None:
    return _reporter


def report(phase: str, *, current: int = 0, total: int = 0, detail: str = "") -> None:
    """上报进度。

    phase: ADAPT/ATOMIZE/CLASSIFY/BUCKET/DISTILL/INSTALL 之一。
    """
    if _reporter is not None:
        try:
            _reporter(phase, current, total, detail)
            return
        except Exception:  # noqa: BLE001 - reporter 不能阻塞管线
            pass
    # 默认：打到 stderr，避免污染 stdout（便于管道 / 测试断言）
    if total:
        print(f"[{phase}] {current}/{total} {detail}".rstrip(), flush=True)
    else:
        print(f"[{phase}] {detail}".rstrip(), flush=True)
