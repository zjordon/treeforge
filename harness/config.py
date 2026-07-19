"""配置层。

从环境变量 / .env 读 LLM_KEY/LLM_BASE/DISTILL_MODEL/OUTPUT_DIR。

【与 Browser-BC 的差异】Browser-BC 的 env 前缀不统一（JFL_* 路径、SF_* 模型、裸 max_tokens）。
TreeForge 用干净的裸名，与 init-plan §7.2 对齐。.env 文件用最朴素的 KEY=VALUE 解析，
不引入 python-dotenv（保持零运行时依赖哲学）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 仓库根（config.py 在 harness/ 下，往上两层）
REPO_ROOT = Path(__file__).resolve().parent.parent

# ---- LLM 配置（init-plan §7.2）-------------------------------------------

LLM_KEY: str = ""
LLM_BASE: str = "https://api.anthropic.com"
LLM_INSECURE: bool = False  # 1/true/yes 跳过 TLS 验证（自签 / 企业 MITM 网关）
DISTILL_MODEL: str = "claude-opus-4-8"
CLASSIFY_MODEL: str = "claude-haiku-4-5"

LLM_TIMEOUT: int = 180          # 秒
LLM_RETRIES: int = 6
LLM_USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DISTILL_MAX_TOKENS: int = 16384
CLASSIFY_MAX_TOKENS: int = 2048

# ---- 管线参数（对齐 Browser-BC 默认值）------------------------------------

MIN_BUCKET_SIZE: int = 1        # 单条录制即可蒸馏（P0 验收需要）
MAX_SEGMENT_EVENTS: int = 80
MIN_SEGMENT_EVENTS: int = 3
IDLE_GAP_MS: int = 15_000       # 15s 静默 → 切 segment
SUBMIT_LOOKAHEAD: int = 5
PATH_DEPTH: int = 2

# ---- 路径 -------------------------------------------------------------------

DATA_DIR: Path = REPO_ROOT / "data"
OUTPUT_DIR: Path = DATA_DIR / "skills"  # 默认输出根（CLI 可用 --output 覆盖）
STATE_DIR: Path = DATA_DIR / "harness"  # buckets.json / registry.json 落这里（P0 暂不用）

# ---- 默认输出 adapter -------------------------------------------------------

DEFAULT_ADAPTER: str = "treewalker"


def _coerce_bool(s: str) -> bool:
    return str(s).strip().lower() in {"1", "true", "yes", "on"}


def _parse_env_file(path: Path) -> dict[str, str]:
    """最朴素的 KEY=VALUE 解析，支持引号、# 注释、空行。不抛错。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                val = val[1:-1]
            out[key] = val
    except OSError:
        # 读不到就当没有，env vars 仍可生效
        pass
    return out


def _resolve(
    env: dict[str, str],
    key: str,
    default: str,
    *,
    cast: type | None = None,
    current: object | None = None,
) -> object:
    """优先级：进程已 set 的 env var > .env 文件 > 已加载值 > default。

    `current` 允许调用方传入"已被代码改过的值"，比 default 优先级高。
    """
    raw: str | None = None
    if key in os.environ:
        raw = os.environ[key]
    elif key in env:
        raw = env[key]
    if raw is None or raw == "":
        return current if current is not None else default
    if cast is int:
        try:
            return int(raw)
        except ValueError:
            return current if current is not None else default
    if cast is bool:
        return _coerce_bool(raw)
    return raw


def load(env_path: Path | None = None) -> None:
    """从 .env + 环境变量加载配置到本模块的全局变量。

    幂等：可重复调用。env_path 缺省时尝试 REPO_ROOT/.env。
    """
    global LLM_KEY, LLM_BASE, LLM_INSECURE, DISTILL_MODEL, CLASSIFY_MODEL
    global LLM_TIMEOUT, LLM_RETRIES, DISTILL_MAX_TOKENS, CLASSIFY_MAX_TOKENS
    global MIN_BUCKET_SIZE, MAX_SEGMENT_EVENTS
    global OUTPUT_DIR, DATA_DIR

    env_path = env_path or (REPO_ROOT / ".env")
    env = _parse_env_file(env_path)

    LLM_KEY = _resolve(env, "LLM_KEY", LLM_KEY, current=LLM_KEY)  # type: ignore[assignment]
    LLM_BASE = _resolve(env, "LLM_BASE", LLM_BASE, current=LLM_BASE)  # type: ignore[assignment]
    LLM_INSECURE = _resolve(  # type: ignore[assignment]
        env, "LLM_INSECURE", "false" if not LLM_INSECURE else "true", cast=bool, current=LLM_INSECURE
    )
    DISTILL_MODEL = _resolve(  # type: ignore[assignment]
        env, "DISTILL_MODEL", DISTILL_MODEL, current=DISTILL_MODEL
    )
    CLASSIFY_MODEL = _resolve(  # type: ignore[assignment]
        env, "CLASSIFY_MODEL", CLASSIFY_MODEL, current=CLASSIFY_MODEL
    )
    LLM_TIMEOUT = _resolve(  # type: ignore[assignment]
        env, "LLM_TIMEOUT", str(LLM_TIMEOUT), cast=int, current=LLM_TIMEOUT
    )
    LLM_RETRIES = _resolve(  # type: ignore[assignment]
        env, "LLM_RETRIES", str(LLM_RETRIES), cast=int, current=LLM_RETRIES
    )
    DISTILL_MAX_TOKENS = _resolve(  # type: ignore[assignment]
        env, "DISTILL_MAX_TOKENS", str(DISTILL_MAX_TOKENS), cast=int, current=DISTILL_MAX_TOKENS
    )
    CLASSIFY_MAX_TOKENS = _resolve(  # type: ignore[assignment]
        env, "CLASSIFY_MAX_TOKENS", str(CLASSIFY_MAX_TOKENS), cast=int, current=CLASSIFY_MAX_TOKENS
    )
    MIN_BUCKET_SIZE = _resolve(  # type: ignore[assignment]
        env, "MIN_BUCKET_SIZE", str(MIN_BUCKET_SIZE), cast=int, current=MIN_BUCKET_SIZE
    )
    MAX_SEGMENT_EVENTS = _resolve(  # type: ignore[assignment]
        env, "MAX_SEGMENT_EVENTS", str(MAX_SEGMENT_EVENTS), cast=int, current=MAX_SEGMENT_EVENTS
    )

    output_raw = _resolve(env, "OUTPUT_DIR", str(OUTPUT_DIR), current=str(OUTPUT_DIR))
    assert isinstance(output_raw, str)
    OUTPUT_DIR = Path(output_raw)
    if not OUTPUT_DIR.is_absolute():
        OUTPUT_DIR = (REPO_ROOT / OUTPUT_DIR).resolve()


def describe() -> dict[str, object]:
    """返回当前生效配置（脱敏 key），用于诊断 / 进度打印。"""
    return {
        "llm_base": LLM_BASE,
        "llm_key_set": bool(LLM_KEY),
        "llm_insecure": LLM_INSECURE,
        "distill_model": DISTILL_MODEL,
        "classify_model": CLASSIFY_MODEL,
        "output_dir": str(OUTPUT_DIR),
        "min_bucket_size": MIN_BUCKET_SIZE,
        "python": sys.version.split()[0],
    }


# 模块导入时自动尝试加载一次 .env（若存在），让 `python -m treeforge` 不显式 load 也能用
load()
