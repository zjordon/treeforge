"""标准库 LLM 客户端（urllib，双协议探测）。

【核心约束】不用 anthropic / openai SDK，零运行时依赖（init-plan §三/§八）。

【双协议探测】这是 Browser-BC Windows 适配的关键修复（见知识库
browserbc-windows-adaptation.md「协议误判修复」）：

    _is_anthropic(base) = "anthropic.com" in base OR "/anthropic" in base
                                                    ^^^^^^^^^^
                                  带 / 的路径段，匹配第三方网关（如智谱 BigModel
                                  /api/anthropic），避免误判为 OpenAI 走错端点 404

    Anthropic → POST {base}/v1/messages     头：x-api-key + anthropic-version: 2023-06-01
    OpenAI    → POST {base}/v1/chat/completions  头：Authorization: Bearer <key>

【其它行为对齐 Browser-BC】
  - 不传 temperature / thinking（Opus 4.x 拒绝采样参数；我们要纯 JSON）
  - 6 次重试，指数退避封顶 8s（wait = min(2**attempt, 8)）
  - HTTPError body 透传（e.read() 截前 300 字），否则只看到「HTTP 400」没线索
  - 浏览器 UA 伪装（Cloudflare 403 默认 Python-urllib UA，错误码 1010）
  - LLM_INSECURE=True 跳 TLS（自签 / 企业 MITM 网关）
  - parse_json_from_model 4 级 fallback（模型常把多行 markdown 裸塞进 JSON 字符串）
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request

from . import config

_DEFAULT_UA = config.LLM_USER_AGENT


# ===========================================================================
# 协议探测
# ===========================================================================


def is_anthropic(base: str) -> bool:
    """是否按 Anthropic Messages 协议请求。

    匹配官方端点 (api.anthropic.com) 和第三方兼容网关（URL 路径含 /anthropic，
    如 https://open.bigmodel.cn/api/anthropic）。带斜杠避免 bare "anthropic"
    误命中假想的 /anthropic-style-openai 这类路径。
    """
    b = (base or "").lower()
    return "anthropic.com" in b or "/anthropic" in b


def _endpoint(base: str) -> str:
    base = (base or "").rstrip("/")
    if is_anthropic(base):
        return f"{base}/v1/messages"
    return f"{base}/v1/chat/completions"


# ===========================================================================
# HTTP POST（带重试 + 退避 + UA 伪装 + body 透传）
# ===========================================================================


def _build_request(
    base: str,
    api_key: str,
    payload: dict,
    *,
    timeout: int,
    insecure: bool,
) -> tuple[urllib.request.Request, ssl.SSLContext | None]:
    url = _endpoint(base)
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _DEFAULT_UA,
        "Accept": "application/json",
    }
    if is_anthropic(base):
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    ctx: ssl.SSLContext | None = None
    if insecure or url.startswith("https://"):
        # insecure=True → 跳过证书校验；https 但 secure 时用默认 ctx（None）
        if insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    return req, ctx


def _http_post(
    base: str,
    api_key: str,
    payload: dict,
    *,
    timeout: int,
    insecure: bool,
) -> str:
    req, ctx = _build_request(base, api_key, payload, timeout=timeout, insecure=insecure)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - read() 失败也别掩盖原始 HTTP 错
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _extract_text(raw_body: str, base: str) -> tuple[str, dict]:
    """从 Anthropic / OpenAI 响应里抽出 assistant 文本 + usage。"""
    data = json.loads(raw_body)
    usage: dict = {}
    if is_anthropic(base):
        # content: [{type:"text", text:"..."}]
        chunks: list[str] = []
        for block in data.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block.get("text", ""))
        text = "".join(chunks)
        u = data.get("usage") or {}
        usage = {
            "input_tokens": u.get("input_tokens"),
            "output_tokens": u.get("output_tokens"),
        }
    else:
        choices = data.get("choices") or []
        text = ""
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
        u = data.get("usage") or {}
        usage = {
            "input_tokens": u.get("prompt_tokens"),
            "output_tokens": u.get("completion_tokens"),
        }
    return text, usage


# ===========================================================================
# 公开 API
# ===========================================================================


def _call(
    prompt: str,
    *,
    model: str,
    max_tokens: int,
    base: str | None = None,
    api_key: str | None = None,
    timeout: int | None = None,
    insecure: bool | None = None,
    retries: int | None = None,
    system: str | None = None,
) -> tuple[str, dict]:
    base = config.LLM_BASE if base is None else base
    api_key = config.LLM_KEY if api_key is None else api_key
    timeout = config.LLM_TIMEOUT if timeout is None else timeout
    insecure = config.LLM_INSECURE if insecure is None else insecure
    retries = config.LLM_RETRIES if retries is None else retries

    if not api_key:
        raise RuntimeError(
            "LLM_KEY 未配置。请在 .env 写 LLM_KEY=sk-xxx（详见 README「快速开始」）。"
        )

    if is_anthropic(base):
        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
    else:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            body = _http_post(base, api_key, payload, timeout=timeout, insecure=insecure)
            return _extract_text(body, base)
        except Exception as e:  # noqa: BLE001 - 重试任何错（网络 / 5xx / 偶发网关 400）
            last_err = e
            # 最后一次不再 sleep
            if attempt < retries - 1:
                wait = min(2**attempt, 8)
                time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} retries: {last_err}")


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    system: str | None = None,
    **kwargs,
) -> tuple[str, dict]:
    """调用蒸馏模型（Opus 级，强推理）。返回 (assistant_text, usage_dict)。"""
    return _call(
        prompt,
        model=model or config.DISTILL_MODEL,
        max_tokens=max_tokens or config.DISTILL_MAX_TOKENS,
        system=system,
        **kwargs,
    )


def call_llm_fast(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    system: str | None = None,
    **kwargs,
) -> tuple[str, dict]:
    """调用分类模型（Haiku 级，快/省）。用于 classify / consolidate / query。

    timeout 默认更短（90s），max_tokens 默认更小（2048）。
    """
    kwargs.setdefault("timeout", 90)
    return _call(
        prompt,
        model=model or config.CLASSIFY_MODEL,
        max_tokens=max_tokens or config.CLASSIFY_MAX_TOKENS,
        system=system,
        **kwargs,
    )


# ===========================================================================
# JSON 解析（4 级 fallback）
# ===========================================================================


def _strip_fence(text: str) -> str:
    """剥 ```json ... ``` / ``` ... ``` 围栏。"""
    s = text.strip()
    if s.startswith("```"):
        # 去首行（可能是 ```json）
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _escape_invalid_json_backslashes(s: str) -> str:
    """把 JSON 字符串字面量里未转义的裸换行/制表符转义。

    模型常把多行 markdown 裸塞进 JSON 字符串值，导致 json.loads 失败。
    这里只处理出现在引号内的 \n / \r / \t 物理字符（保守替换）。
    """
    out: list[str] = []
    in_str = False
    escape = False
    for ch in s:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str:
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
        out.append(ch)
    return "".join(out)


def parse_json_from_model(text: str) -> dict:
    """从模型输出解析 JSON（4 级 fallback）。

    1. 整段 strip fence → json.loads(strict=False)
    2. 同上 + 反斜杠/裸换行修复
    3. 截首 { 到末 } 子串，重试 1、2
    4. 失败抛 ValueError（带原始前 200 字）
    """
    cleaned = _strip_fence(text)

    candidates = [cleaned, _escape_invalid_json_backslashes(cleaned)]
    for s in candidates:
        try:
            return json.loads(s, strict=False)
        except Exception:  # noqa: BLE001
            pass

    # 截首 { 到末 }
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        body = cleaned[first : last + 1]
        for s in (body, _escape_invalid_json_backslashes(body)):
            try:
                return json.loads(s, strict=False)
            except Exception:  # noqa: BLE001
                pass

    raise ValueError(f"无法从模型输出解析 JSON。前 200 字：{cleaned[:200]!r}")
