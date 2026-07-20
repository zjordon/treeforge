# 核心概念 ②：LLM 客户端（标准库 urllib）

> 代码：`harness/llm.py`
> 配套阅读：[01-architecture-overview.md 决策 3](../01-architecture-overview.md#决策-3llm-客户端用标准库-urllib不引入-sdk)

## 核心约束：零运行时依赖

TreeForge 的 LLM 客户端**不引入任何 SDK**——不用 `anthropic`，不用 `openai`，
只用 Python 标准库 `urllib.request` 发 HTTP。

这是从 Browser-BC 继承的「零运行时依赖」哲学（Pydantic 是唯一例外）。

**为什么这么坚持？**
1. **可移植**——不依赖 SDK 版本，不依赖 SDK 是否维护
2. **可控**——协议细节都在自己代码里，遇到网关问题能立刻定位
3. **轻量**——不需要为发一个 HTTP 请求引入整个 SDK

## 三个公开函数

```python
# harness/llm.py
def call_llm(prompt, *, model=None, max_tokens=None, system=None, **kwargs) -> tuple[str, dict]:
    """调蒸馏模型（Opus 级，强推理）。用于 DISTILL + playbook。"""

def call_llm_fast(prompt, *, model=None, max_tokens=None, system=None, **kwargs) -> tuple[str, dict]:
    """调分类模型（Haiku 级，快/省）。用于 CLASSIFY / consolidate / query。"""

def parse_json_from_model(text) -> dict:
    """从模型输出解析 JSON（4 级 fallback）。"""
```

返回 `(assistant_text, usage_dict)`——usage 含 token 计数，用于写进 SkillCard.meta。

## 双协议探测 ⚠️ 关键

**这是整个客户端最重要的设计**——一个函数同时支持 Anthropic 和 OpenAI 两种 API 协议。

```python
def is_anthropic(base: str) -> bool:
    b = base.lower()
    return "anthropic.com" in b or "/anthropic" in b
                                   ^^^^^^^^^^
                       这个 /anthropic 路径段是关键修复
```

根据 LLM_BASE 自动走不同协议：

| 协议 | 触发 | 端点 | 认证头 |
|---|---|---|---|
| Anthropic Messages | `anthropic.com` 或路径含 `/anthropic` | `POST {base}/v1/messages` | `x-api-key: <key>` + `anthropic-version: 2023-06-01` |
| OpenAI 兼容 | 否则 | `POST {base}/v1/chat/completions` | `Authorization: Bearer <key>` |

## 为什么需要 `/anthropic` 路径段？

这是 Browser-BC Windows 适配的关键修复。看几个例子：

| base URL | `is_anthropic` 返回 | 解释 |
|---|---|---|
| `https://api.anthropic.com` | ✅ True | 官方端点 |
| `https://open.bigmodel.cn/api/anthropic` | ✅ True | **智谱 BigModel 网关**（Anthropic API 挂在 `/api/anthropic`） |
| `https://gateway.com/anthropic-proxy` | ✅ True | 第三方代理 |
| `https://api.openai.com` | ❌ False | OpenAI 官方 |
| `https://api.deepseek.com` | ❌ False | DeepSeek |
| `https://my-gateway.com/v1` | ❌ False | 通用 OpenAI 兼容 |

**为什么这个修复关键？** 智谱 BigModel 等第三方网关把 Anthropic API 挂在 `/api/anthropic` 路径下。
如果不带 `/` 只匹配字符串 `anthropic`，会误判假阳性（如 `https://example.com/anthropic-style-openai`）；
如果完全不探测，发 OpenAI 格式请求到 Anthropic 端点直接 **404**。

错误症状很隐蔽——`classify produced 0/N labels — all calls failed`，没有其它明显错误。
带 `/` 的路径段匹配是已知兼容网关（智谱 / Bedrock proxy）的通用约定。

## 请求构造

```python
def _build_request(base, api_key, payload, *, timeout, insecure):
    url = _endpoint(base)  # 自动选 /v1/messages 或 /v1/chat/completions
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _DEFAULT_UA,  # 浏览器 UA 伪装
        "Accept": "application/json",
    }
    if is_anthropic(base):
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    ...
```

**User-Agent 为什么要伪装？** Cloudflare 会 403 默认的 `Python-urllib` UA（错误码 1010）。
所以塞个浏览器 UA 绕过。

## Payload 差异

```python
if is_anthropic(base):
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "system": system,        # Anthropic: system 是顶层字段
    }
else:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})  # OpenAI: system 是 messages 里一条
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "max_tokens": max_tokens, "messages": messages}
```

**不传 `temperature` / `thinking`**——Opus 4.x 拒绝采样参数，我们要纯 JSON 输出。

## 重试 + 退避

```python
for attempt in range(retries):       # 默认 LLM_RETRIES=6
    try:
        return _http_post(...)
    except Exception as e:
        last_err = e
        if attempt < retries - 1:
            time.sleep(min(2**attempt, 8))   # 指数退避，封顶 8s：1,2,4,8,8,8
raise RuntimeError(f"LLM call failed after {retries} retries: {last_err}")
```

**为什么 6 次？** 一些负载均衡网关后端间歇性坏（如 Bedrock deployment 没开 Anthropic 权限 → 偶发 400）。
重试能跳过坏后端，命中好后端。

## HTTPError body 透传

```python
except urllib.error.HTTPError as e:
    detail = e.read().decode("utf-8", "replace")[:300]
    raise RuntimeError(f"HTTP {e.code}: {detail}") from e
```

**为什么透传 body？** 否则只看到「HTTP 400」，没线索。body 截前 300 字告诉你「which field is invalid」，
调试效率天差地别。

## TLS 容错

```python
if insecure:   # LLM_INSECURE=true
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
```

自签证书 / 企业 MITM 网关用。`LLM_INSECURE=true` 跳过校验。

## `parse_json_from_model` 4 级 fallback ⚠️

**这是 LLM 客户端的另一个关键设计**——LLM 常把多行 markdown 裸塞进 JSON 字符串值，
导致 `json.loads` 失败。4 级 fallback 解决这个问题：

```python
def parse_json_from_model(text) -> dict:
    cleaned = _strip_fence(text)               # 剥 ```json ... ``` 围栏

    # Level 1: 直接解析
    for s in (cleaned, _escape_invalid_json_backslashes(cleaned)):
        try: return json.loads(s, strict=False)    # strict=False 容忍裸控制字符
        except: pass

    # Level 2: 截首 { 到末 } 子串，重试 Level 1
    if "{" in cleaned and "}" in cleaned:
        body = cleaned[first_{ : last_}+1]
        for s in (body, _escape_invalid_json_backslashes(body)):
            try: return json.loads(s, strict=False)
            except: pass

    raise ValueError("无法解析 JSON")
```

`_escape_invalid_json_backslashes` 把字符串值里**未转义的裸换行/制表符**转义：

```python
# 输入（非法 JSON）：
{"sop_md": "line1
line2"}

# 输出（合法 JSON）：
{"sop_md": "line1\nline2"}
```

通过逐字符扫描 + 状态机识别「是否在字符串内」+「是否被转义」，只处理字符串字面量内的物理换行。

**为什么这么重要？** DISTILL 的产物含多行 markdown（`sop_md` 等），LLM 极易输出非法 JSON。
没有这个 fallback，蒸馏基本每次都失败。

## 测试覆盖

`tests/test_llm.py` 用纯逻辑测试（不真发请求）：

- `test_is_anthropic_*`：协议探测各场景（官方 / 智谱网关 / OpenAI / 大小写）
- `test_endpoint_paths`：端点路径选择
- `test_parse_json_from_model_*`：JSON 解析各场景（plain / fence / 含散文 / 裸换行 / 垃圾抛错）

**所有测试不真发请求**——只测纯逻辑函数。

## P0 vs Browser-BC 的 LLM 配置差异

| | Browser-BC | TreeForge |
|---|---|---|
| env 前缀 | 不统一（`SF_*` / `JFL_*` / 裸） | **干净裸名**（`LLM_KEY` / `LLM_BASE` / `DISTILL_MODEL`） |
| 客户端实现 | urllib 双协议 | **同** |
| 重试策略 | 6 次 exp backoff cap 8s | **同** |
| UA 伪装 | 浏览器 UA | **同** |

详见 `.env.example`。

## 下一步

→ [03-adapter-design.md](./03-adapter-design.md)（adapter 缓冲设计：同份 SkillCard 出两种格式）
