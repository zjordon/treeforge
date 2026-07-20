# 核心概念 ③：adapter 缓冲设计

> 代码：`adapters/base.py` / `adapters/treewalker_adapter.py` / `adapters/browserbc_adapter.py`
> 配套阅读：[01-architecture-overview.md 决策 4](../01-architecture-overview.md#决策-4adapter-缓冲同一份-skillcard-出两种格式)

## 这个设计干什么

P0 在输出层做了一个关键缓冲：**`SkillCard` 是中间表示，与输出形态解耦**。

```
SkillCard ──┬── treewalker_adapter ──→ domain-skills/<host>/{4 文件}  （默认）
            │
            └── browserbc_adapter  ──→ skills/<domain>/<cap>/SKILL.md  （对照）
```

CLI 用 `--adapter` 切换。**蒸馏逻辑（harness/）完全不关心输出长什么样**——加新格式只要写新 adapter。

## 为什么有这个缓冲

### 原因 1：消费方可能变

TreeForge 默认产给 TreeWalker（文件注入）。但理论上 skill 文件还能给：

- Browser-BC 自己（MCP 检索消费）
- 其它自动化框架
- 人类阅读（教学文档）

不同消费方期望不同格式。adapter 模式让你**不改蒸馏逻辑**就能适配新消费方。

### 原因 2：学习对照

TreeForge 的核心分叉点在 DISTILL——产站点特定知识 vs Browser-BC 的通用 SOP。
留一个 `browserbc_adapter` 产 Browser-BC 格式（单 SKILL.md），让你**直观对比两种产物形态的差异**。

```bash
# 默认：treewalker 多文件
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills

# 对照：browserbc 单 SKILL.md
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills_bc \
    --adapter browserbc
```

## 抽象接口 `adapters/base.py`

```python
class OutputAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    def write_skill(self, skill: SkillCard, output_dir: Path) -> list[Path]:
        """写一个 SkillCard。返回写入的文件路径列表。"""
        raise NotImplementedError
```

非常薄——只有一个 `write_skill` 方法。每个 adapter 实现自己的写盘逻辑。

## adapter 1：`treewalker_adapter`（默认）

产 init-plan §5 的多文件结构：

```
<output_dir>/domain-skills/<host>/
├── _sop.md          ← 字段 → 文件名映射
├── selectors.md
├── quirks.md
└── api.md
```

**字段 → 文件名映射写死在 `_FILES`：**

```python
_FILES = [
    ("_sop.md", "sop_md"),
    ("selectors.md", "selectors_md"),
    ("quirks.md", "quirks_md"),
    ("api.md", "api_md"),
]

def write_skill(self, skill, output_dir):
    host_dir = output_dir / "domain-skills" / skill.domain
    for fname, field in _FILES:
        content = getattr(skill, field, "") or ""
        content = _ensure_header(content, title)   # 保证有 H1 头
        atomic_write_text(host_dir / fname, content)
```

**两个细节：**

**1. `_ensure_header()` 保证每个文件有 H1 头：**

```python
def _ensure_header(md, title):
    if not md.strip():
        return f"# {title}\n\n_(empty — no evidence for this dimension.)_\n"
    if md.lstrip().startswith("#"):
        return md
    return f"# {title}\n\n{md}"
```

空字段填占位（避免 0 字节文件），没 H1 的自动补 H1。

**2. 字母序保证 `_sop.md` 排第一：** TreeWalker 消费侧按字母序读 ≤10 个 `.md` 文件，
下划线 `_` 在字母前，所以 `_sop.md` 永远排第一，作为入口索引。

## adapter 2：`browserbc_adapter`（对照）

产 Browser-BC 风格的单 SKILL.md：

```
<output_dir>/skills/<domain>/<capacity>/
├── SKILL.md          ← 单文件，含 4 个 section
└── meta.json         ← 元数据
```

把 4 字段塞进一个 SKILL.md 的不同 section：

```python
_SKILL_TEMPLATE = """\
---
name: {skill_name}
domain: {domain}
capacity: {capacity}
...

# {skill_name}

## Procedure
{sop_md}

## Selectors
{selectors_md}

## Quirks
{quirks_md}

## API
{api_md}
"""
```

**对比两种产物形态：**

| | treewalker | browserbc |
|---|---|---|
| 文件数 | 4 | 1（+ meta.json） |
| 路径 | `domain-skills/<host>/` | `skills/<domain>/<capacity>/` |
| 按什么索引 | hostname（消费侧 `urlparse(url).hostname`） | domain + capacity |
| 消费方式 | 文件注入（agent 导航到 host 时读） | MCP 检索（agent 主动查） |
| 内部组织 | 4 个独立文件 | 1 个文件分 4 section |

这个对比让你能直观看到「为什么 TreeForge 要分叉」——同样一份 SkillCard 内容，
落到两种消费场景需要的组织方式完全不同。

## 原子写 `harness/install.py:atomic_write_text()`

所有 adapter 写盘都走这个函数：

```python
def atomic_write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)    # ★ NOT path.rename / os.rename
```

**为什么用 `os.replace` 不用 `Path.rename`？** Windows 上 `Path.rename` 在目标已存在时报
`WinError 183`（文件已存在）。POSIX 的 `rename` 有覆盖语义所以 macOS 不会暴露这个 bug。
`os.replace` 是标准库的**跨平台原子替换**函数（Windows 调 `MoveFileExA`，POSIX 调 `rename`）。

这是 Browser-BC Windows 适配的「三连击」修复之一——见 [01-architecture-overview.md](../01-architecture-overview.md)。

## install 流程

`harness/install.py:install_cards()` 把 adapter 串起来：

```python
def install_cards(cards, output_dir, adapter):
    written = []
    for card in cards:
        result = adapter.write_skill(card, output_dir)
        written.extend(result if isinstance(result, list) else [result])
    return written
```

CLI 调用：

```python
# treeforge/__main__.py
adapter = get_adapter(adapter_name)       # 按名字取实例
written = install.install_cards(cards, output_dir, adapter)
```

`get_adapter()` 在 `adapters/__init__.py`：

```python
_REGISTRY = {
    "treewalker": TreeWalkerAdapter,
    "browserbc": BrowserBcAdapter,
}

def get_adapter(name):
    cls = _REGISTRY.get(name, TreeWalkerAdapter)  # 未知名字回退默认
    return cls()
```

加新 adapter 只要：写新文件 + 在 `_REGISTRY` 注册。

## 一个常被问的问题：adapter 名为什么叫 `treewalker`？

| 概念 | 例子 | 命名逻辑 |
|---|---|---|
| **命令名** | `treeforge distill` | 谁的工具 → TreeForge |
| **adapter 名** | `--adapter treewalker` | **给谁用** → TreeWalker 消费的多文件格式 |
| adapter 名 | `--adapter browserbc` | 给谁用 → Browser-BC 对照格式 |

`--adapter treewalker` 表达「产 TreeWalker 消费格式」，跟 `adapters/browserbc_adapter` 的命名法对齐
（按**消费方**命名）。改成 `--adapter treeforge` 反而丢信息——TreeForge 两种格式都产。

这是两个不同概念：命令名是「谁的工具」，adapter 名是「给谁用」。

## 相关测试

- `tests/test_adapters.py::test_treewalker_adapter_writes_four_files`
- `tests/test_adapters.py::test_treewalker_adapter_files_sorted_alphabetically_sop_first`（字母序验证）
- `tests/test_adapters.py::test_treewalker_adapter_empty_field_gets_placeholder`（空字段占位）
- `tests/test_adapters.py::test_browserbc_adapter_writes_single_skill_md`
- `tests/test_adapters.py::test_install_atomic_write_overwrites_existing`（原子写覆盖）

## 完整文档导航

读完本篇，P0 的核心概念都覆盖了。回到 [docs/p0/README.md](../README.md) 看下一步建议。

如果还想深入：
- 重读 [01-架构总览.md](../01-architecture-overview.md) 验证整体认知
- 看实际代码 `adapters/*.py`（很短，每个文件不到 80 行）
