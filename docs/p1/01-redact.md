# 子任务 A：完整 redact

> 工作量：**小（半天）** | 依赖：无 | 文件：`harness/adapter.py`（改）+ `harness/event_utils.py`（新）
>
> 配套阅读：[P0 stages/01-adapt.md](../p0/stages/01-adapt.md)（redact 在 ADAPT 阶段执行）

## 这个任务干什么

把 P0 的最小 redact（只覆盖 password 字段 / 邮箱 / 卡号）**扩展到完整 Browser-BC 正则集**，
让真实 trace 里的 CVV / OTP / account token 等敏感数据也能在进 LLM 之前被替换。

**为什么放第一个做：**
- 工作量最小（半天）
- 完全独立（不动其它模块）
- 顺便验证 P1 的开发环境（uv/pytest/ruff 都还正常）

## P0 现状

`harness/adapter.py` 当前只做了三件事：

```python
# P0 现状
_SENSITIVE_FIELD_HINTS = ("password", "passwd", "pwd", "secret", "token", "cvv", "cvc", "otp")
_EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

def _redact_value(field_hint, value):
    if any(s in hint for s in _SENSITIVE_FIELD_HINTS):
        return "<redacted>"                          # ← 字段名命中 → 整体替换
    v = _EMAIL_RE.sub("<runtime-email>", value)
    v = _CARD_RE.sub("<runtime-payment-card>", v)
    return v
```

**P0 的不足：**
1. CVV 没有独立正则（只在字段名命中时整体替换，但「123」出现在非敏感字段不会替换）
2. 6 位 OTP 没正则
3. account token（`cb` 前缀）没正则
4. 字段名敏感词列表不全（缺 email/card/otp/code 等变体）
5. 没有 `label_of` 的截断处理（target 文本过长可能泄漏）

## P1 目标实现

### 1. 抽出独立模块 `harness/event_utils.py`

把 redact 逻辑从 `adapter.py` 抽到独立模块，便于测试 + 后续扩展。

```python
# harness/event_utils.py（新建）
"""事件工具：脱敏 + 字段处理。

【执行时机】在 ADAPT 归一化阶段（adapter._normalize_event），
确保敏感数据在进 LLM 之前被替换。

【对齐 Browser-BC】event_utils.py 的 redact/value_of/label_of 三件套。
"""
from __future__ import annotations

import re

# ============================================================================
# 完整 redact 正则集
# ============================================================================

_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 邮箱
    (re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE),
     "<runtime-email>"),
    # 银行卡号（13-19 位，可含空格/连字符）
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"),
     "<runtime-payment-card>"),
    # CVC/CVV（3-4 位数字，且附近 20 字符窗口内出现 cvv/cvc/security 字样）
    (re.compile(r"\b\d{3,4}\b(?=.{0,20}(?:cvv|cvc|security))", re.IGNORECASE),
     "<runtime-cvc>"),
    # 6 位验证码（OTP）
    (re.compile(r"\b\d{6}\b"),
     "<runtime-verification-code>"),
    # 账户 token（cb 前缀 + 8 位以上 hex）
    (re.compile(r"\bcb[a-f0-9]{8,}\b"),
     "<runtime-account-token>"),
]


def redact(text: str | None) -> str:
    """对任意文本应用所有 redact 正则。"""
    if not text:
        return text or ""
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ============================================================================
# 字段名敏感词（用于 value_of 二级处理）
# ============================================================================

# 归一化比较：lower + 去 - 和 _ 后匹配
_SENSITIVE_FIELD_NAMES = {
    # 密码类
    "password", "pwd", "passwd", "pass",
    # 联系方式
    "email", "email", "mail",  # e-mail 归一化后是 email
    # 卡类
    "card", "creditcard", "cardnumber",
    # 安全码
    "cvc", "cvv", "securitycode",
    # 验证码
    "otp", "verificationcode", "code",
    # 令牌
    "token", "accesstoken", "secret",
    # 其它
    "ssn", "socialsecurity",
}


def _is_sensitive_field(field_name: str) -> bool:
    """字段名是否敏感（归一化后命中敏感词集合）。"""
    normalized = (field_name or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    return normalized in _SENSITIVE_FIELD_NAMES


# ============================================================================
# value_of / label_of：ADAPT 调用的二级处理
# ============================================================================

def value_of(field_name: str, raw_value: str | None) -> str:
    """input/change 字段的值脱敏。

    二级处理：先 redact 正则，再针对敏感字段名强制 <redacted>。
    """
    v = redact(raw_value)
    if _is_sensitive_field(field_name):
        return "<redacted>"
    return v


def label_of(field_name: str, raw_text: str | None, *, max_len: int = 180) -> str:
    """target 文本脱敏 + 截断。

    截断到 180 字符防止超长 label 污染 event_summary。
    """
    return redact(raw_text)[:max_len]
```

### 2. 修改 `harness/adapter.py` 调用新模块

```python
# harness/adapter.py（修改）
from . import event_utils  # 新增

def _redact_value(field_hint: str, value: str | None) -> str | None:
    """保留原签名（向后兼容），内部改调 event_utils.value_of。"""
    if value is None:
        return None
    return event_utils.value_of(field_hint, value)

def _normalize_event(raw, fallback_idx):
    # ... 原逻辑不变 ...
    target = raw.get("target") or raw.get("label") or raw.get("text")
    if target is not None:
        target = event_utils.label_of("target", str(target))   # ← 新增截断
    # ... 其余不变 ...
```

## 实现要点

### 1. CVC 正则的 lookahead 设计

```python
r"\b\d{3,4}\b(?=.{0,20}(?:cvv|cvc|security))"
```

**为什么这么写？** 不能直接 `\b\d{3,4}\b`——会误杀所有 3-4 位数字（如年龄、数量）。
lookahead `(?=.{0,20}(?:cvv|cvc|security))` 要求 3-4 位数字后**20 字符窗口内**出现 cvv/cvc/security，
才认定是 CVC。

### 2. 字段名归一化比较

```python
normalized = field_name.lower().replace("-", "").replace("_", "").replace(" ", "")
```

`"CVC Code"` / `"cvc_code"` / `"cvc-code"` / `"cvccode"` 都归一化成 `"cvccode"`，
然后命中 `_SENSITIVE_FIELD_NAMES`。避免遗漏大小写/分隔符变体。

### 3. `<runtime-*>` vs `<redacted>` 的语义

- `<runtime-*>`（邮箱/卡号/CVC/OTP/token）：**值替换**，保留字段存在性
- `<redacted>`（字段名敏感）：**整体替换**，连值类型都不留

LLM 看到 `<runtime-email>` 知道「这里该填邮箱」，看到 `<redacted>` 知道「这里不该看」。

## 依赖与前置

**无前置**——纯独立的工具模块。

## 验收点

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | 邮箱替换 | `redact("contact: user@example.com")` → 含 `<runtime-email>` |
| 2 | 卡号替换 | `redact("4111 1111 1111 1111")` → `<runtime-payment-card>` |
| 3 | CVV 替换（有上下文） | `redact("CVV: 123")` → 含 `<runtime-cvc>` |
| 4 | CVV 不误杀（无上下文） | `redact("age: 123")` → 不变（仍是 123） |
| 5 | OTP 替换 | `redact("code: 123456")` → 含 `<runtime-verification-code>` |
| 6 | account token 替换 | `redact("tok: cba1b2c3d4e5f6a7")` → 含 `<runtime-account-token>` |
| 7 | 字段名命中 | `value_of("password", "any")` → `<redacted>` |
| 8 | 字段名变体命中 | `value_of("CVC Code", "123")` → `<redacted>` |
| 9 | label 截断 | `label_of("x", "a" * 300)` → 长度 180 |
| 10 | P0 链路不破 | `uv run treeforge distill examples/bilibili-upload.trace.json --no-llm` 仍跑通 |

## 测试要求

新建 `tests/test_event_utils.py`，覆盖：

```python
import pytest
from harness.event_utils import redact, value_of, label_of, _is_sensitive_field

# 每条验收点一个测试，至少 10 个：
def test_redact_email():
    assert "<runtime-email>" in redact("contact user@example.com")

def test_redact_card_with_spaces():
    assert redact("4111 1111 1111 1111") == "<runtime-payment-card>"

def test_redact_cvv_with_context():
    assert "<runtime-cvc>" in redact("CVV: 123")

def test_redact_cvv_no_context_not_redacted():
    # 无 cvv/cvc/security 上下文，3 位数字不该被替换
    assert redact("age 123") == "age 123"

def test_redact_otp_six_digits():
    assert "<runtime-verification-code>" in redact("code 123456")

def test_redact_account_token():
    assert "<runtime-account-token>" in redact("tok cba1b2c3d4e5f6a7")

def test_value_of_sensitive_password():
    assert value_of("password", "secret123") == "<redacted>"

def test_value_of_sensitive_field_name_variant():
    # "CVC Code" 归一化后命中
    assert value_of("CVC Code", "123") == "<redacted>"

def test_label_of_truncates():
    assert len(label_of("x", "a" * 300)) == 180

def test_label_of_redacts_email_inside():
    assert "<runtime-email>" in label_of("target", "contact user@example.com")
```

**边界用例至少加：**
- 空 text / None
- 中文字段名（不命中，但能跑过）
- 多种分隔符（`-` / `_` / 空格）

## 难点与坑

### 坑 1：CVC 正则误杀

直接 `\b\d{3,4}\b` 会误杀年龄、数量、版本号等。**必须用 lookahead 限定上下文**。

### 坑 2：6 位 OTP 误杀

`\b\d{6}\b` 会误杀日期、邮政编码。Browser-BC 也接受这个误杀率（脱敏偏好过度而非不足）。
**如果后续发现误杀太多，可以学 CVC 加 lookahead**（`(?=.{0,20}(?:otp|code|verification))`）。

### 坑 3：向后兼容

P0 的 `_redact_value(field_hint, value)` 签名要保留，内部转调 `event_utils.value_of`。
**不要直接改函数签名**，否则调用点全要改。

### 坑 4：性能

redact 在 ADAPT 阶段对每个 event 的 value/target 都执行一次。trace 有几百个事件时，
5 个正则 × N 次调用 = 几千次正则匹配。**Python 正则编译后很快**，但注意把 `_REDACT_PATTERNS`
放模块级（编译一次），不要每次调用 recompile。

## 完成后下一步

→ [02-persistence.md](./02-persistence.md)（持久化层，接入层和增强的前置）
