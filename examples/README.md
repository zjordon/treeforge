# 示例 trace

本目录的 trace JSON 用于 P0 验证蒸馏链路（不依赖真实录制）。

## 最小格式（init-plan §7.6）

```json
{
  "host": "bilibili.com",
  "task_instruction": "上传一个视频",
  "events": [
    {
      "type": "navigate",
      "target": "首页",
      "selector": null,
      "url": "https://www.bilibili.com/",
      "value": null,
      "timestamp": 0
    },
    {
      "type": "click",
      "target": "上传按钮",
      "selector": "[aria-label='投稿']",
      "url": "https://www.bilibili.com/",
      "value": null,
      "timestamp": 1234
    }
  ]
}
```

### 必填字段

| 字段 | 说明 |
|---|---|
| `host` | 主域名（用于落 `domain-skills/<host>/`），如 `bilibili.com` |
| `events[]` | 事件列表，至少 1 个 |

### 每个 event 的字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | `navigate`/`click`/`input`/`change`/`submit`/`scroll`/`keydown` 等 |
| `timestamp` | 是 | 毫秒时间戳（相对或绝对都行，链路只用相对差值） |
| `target` | 否 | 元素的人类可读标签/文本 |
| `selector` | 否 | CSS/XPath 选择器（落到 `selectors.md`） |
| `url` | 否 | 事件发生时的页面 URL |
| `value` | 否 | input/change 的值（**会自动脱敏**，密码字段替换为 `<redacted>`） |
| `key` | 否 | keydown 的键名 |

### 顶层可选字段

| 字段 | 说明 |
|---|---|
| `task_instruction` | 本次示教的任务描述（喂给 LLM 做分类提示） |
| `track_id` | trace 唯一 id（缺省时按内容 hash 生成） |
| `domain` | `host` 的别名（兼容写法） |

## 兼容的事件类型别名

ADAPT 层会做规整：

| 输入 | 规整为 |
|---|---|
| `dblclick` / `double_click` | `click` |
| `wheel` | `scroll` |
| `file_select` | `change` |
| `navigation` / `navigate` / `page_load` / `pageload` | `navigate` |
| `focus` / `blur` | 丢弃 |

## 脱敏

ADAPT 层自动脱敏（详见 `harness/adapter.py`）：

- 字段名含 `password`/`passwd`/`pwd`/`secret`/`token`/`cvv`/`cvc`/`otp` → 值替换为 `<redacted>`
- 邮箱（任意字段）→ `<runtime-email>`
- 卡号 13-19 位（任意字段）→ `<runtime-payment-card>`

> 完整 Browser-BC redact（CVV/OTP/account token 正则）后续 P1+ 补。

## 现有示例

| 文件 | 场景 | 步数 |
|---|---|---|
| `bilibili-upload.trace.json` | B 站投稿（上传视频） | ~15 步 |
| `github-login.trace.json` | GitHub 登录 | ~7 步 |

## 跑通

```bash
# 模板模式（不调 LLM，验证链路）
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm

# 真调 LLM（先配 .env，见 README）
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
```
