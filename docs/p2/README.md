# P2 采集层设计方案

> 基于 P0/P0.5/P1 的成果，设计 P2 采集层：录制用户行为 + 采集 DOM 快照，
> 产出两类文件（trace + 快照）并保持关联，替代当前的人工标注流程。
>
> **前置结论（P1 A/B 验证）**：蒸馏精简版 skill 达手写精简版水平（均 100% 成功、零异常），
> 证明「TreeForge 自动蒸馏产出可用 skill」可行。P2 采集层的目标是让「采集」也自动化，
> 形成完整的「采集 → 蒸馏 → skill」闭环。

## 一、背景与问题

### 当前流程的痛点

P0.5 阶段我们用 `tools/rerun_to_trace.py` 把 TreeWalker 的 rerun-history 转成 treeforge trace，
实现了 95% 自动化。dom-snapshot 公共库已完成（快照产出能力就绪），但 trace 与快照的**自动关联**
仍是关键瓶颈：

| 痛点 | 现状 | 影响 |
|---|---|---|
| ~~两个工程的快照代码耦合~~ | ✅ 已解决：dom-snapshot 0.1.0 已发版，TreeWalker 已接入，两工程共享同一份快照实现 | — |
| **快照产出与 trace 关联靠人工** | 快照可由 dom-snapshot 自动产出（`D:/temp/dom-snapshot-model-input/bili/*.txt`），但「哪个快照对应哪步操作」仍需人工在 trace 里标注 stage | 每个站点都要手动对齐快照与操作步骤，易错位 |
| **stage 关联靠事后猜测** | `_infer_stages` 用元素指纹反查 + 时序外推，带 `?` 标记不确定（bilibili 17 步里 8 步带 `?`） | 关联质量受启发式限制，无法保证准确 |

### 用户需求（来自实际验证）

经过 P0.5/P1 几轮迭代，采集层最终要产出**两类文件**：

1. **文件 A：用户行为痕迹 trace**（类似 `examples/bilibili-upload.trace.json`）
   - 含 events[]（每个带 element_attrs + 确定的 stage，无 `?`）

2. **文件 B：给模型的 DOM 快照**（类似 `D:/temp/dom-snapshot-model-input/bili/*.txt`）
   - 含 page_context（阶段名 → `element_tree_text`）

**关联关系**：`event.stage` === `page_context` 的 key（1:N，SPA 多步共享一快照）。

> **当前进展**：文件 B 的快照产出能力已就绪（dom-snapshot 0.1.0 已发版，TreeWalker 已接入，
> `D:/temp/dom-snapshot-model-input/bili/*.txt` 是 dom-snapshot 的实际产出）。
> 文件 A（trace）目前是半自动产出（`rerun_to_trace.py` + 人工 stage 标注，8/17 步带 `?`）。
> P2 的核心目标是让 A/B 两类文件都**自动产出且采集时绑定关联**（消除 `?`）。
> 快照公共库（三源采集 + 五步过滤）已完成抽取（dom-snapshot），两个工程共享。

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    公共库：dom-snapshot                       │
│  （新独立 git 仓库，从 TreeWalker 抽取 5 个核心文件）            │
│                                                              │
│  build_dom_state(client) → SerializedDOMState               │
│  ├─ element_tree_text     ← 给 LLM 看的 [index]<tag /> 文本  │
│  ├─ selector_map          ← index → 节点（定位用）            │
│  ├─ file_inputs_meta      ← [File Inputs] 段数据             │
│  └─ page_stats            ← [Page Stats] 段数据              │
│                                                              │
│  对外接口：只依赖 CDP 客户端鸭子类型（CDPLikeClient Protocol）   │
└─────────────────────────────────────────────────────────────┘
           ▲                              ▲
           │ pip install                  │ pip install
           │                              │
┌──────────┴──────────────┐    ┌──────────┴────────────────────────────┐
│      TreeWalker          │    │         treeforge                       │
│  （agent 运行时）          │    │   （采集层 P2 + 蒸馏层；采集的唯一归属）   │
│                          │    │                                         │
│  browser/session.py      │    │  capture/  ← 采集后端（自实现，不 import │
│  ├─ get_state()          │    │  │           TreeWalker 内部模块）        │
│  │  用 dom-snapshot 产    │    │  ├─ backend.py    aiohttp 收扩展事件     │
│  │  element_tree_text    │    │  │   （/ingest + scenario 路由）          │
│  │                       │    │  ├─ collector.py  采集器主类              │
│  prompts/                │    │  │   （借鉴 TW Recorder 结构自实现）       │
│  └─ build_state_message()│    │  ├─ cdp_session.py 轻量 CDP 包装          │
│     用 dom-snapshot 拼    │    │  │   （~50 行，绕开 BrowserSession）      │
│     [Page DOM] 段         │    │  ├─ stage.py      阶段切换判定+命名       │
│                          │    │  └─ export.py     产 trace+快照双文件    │
│                          │    │                                         │
│                          │    │  extension/  ← 自研 Chrome 扩展          │
│                          │    │  ├─ core/        通用采集骨架             │
│                          │    │  │   （绑定/去抖/IME/observer/hook）      │
│                          │    │  ├─ extractors/  字段提取器注册表         │
│                          │    │  ├─ strategies/  可插拔策略               │
│                          │    │  │   ├─ distill/  蒸馏策略（P2 实现）      │
│                          │    │  │   └─ replay/   重放策略（TW 迁入）      │
│                          │    │  └─ shared/      通用 CaptureEnvelope     │
│                          │    │                                         │
│                          │    │  tools/rerun_to_trace.py                 │
│                          │    │  ├─ 简化：stage 直接透传                  │
│                          │    │  └─ 删除 _infer_stages 启发式             │
└──────────────────────────┘    └─────────────────────────────────────────┘
           ▲                                        ▲
           │                                        │ 通用骨架复用
           │                                        │（未来 replay 策略迁入）
           │    ┌────────────────────────────────────┘
           │    │
    TreeWalker 的 recording_extension/（为重放深度定制，不复用）
           │    迁入 treeforge/extension/strategies/replay/
           │
    ┌──────┴───────────────────────────────────────────────────────┐
    │  Chrome 扩展（treeforge/extension/）                          │
    │  ├─ 蒸馏场景：core 骨架 + distill 策略 + 通用 CaptureEnvelope  │
    │  └─ POST /ingest {scenario:'distill', payload}                │
    └───────────────────────────────────────────────────────────────┘
```

**核心设计原则**：
- **单一快照实现**：三源采集 + 五步过滤只维护一份（dom-snapshot），避免格式漂移
- **采集层归属 TreeForge**：后端 + 扩展都在 treeforge，TreeForge 是采集的唯一归属；
  TreeWalker 保持纯 agent 运行时，未来采集层（含扩展）整体迁入 TreeForge
- **自实现不跨仓库 import**：采集逻辑借鉴 TreeWalker 蓝本自实现，不 import TreeWalker 内部模块；
  可共用部分未来抽公共库（capture-protocol/record-core，见 3.2.6）
- **扩展通用骨架 + 可插拔策略**：采集机制骨架（绑定/去抖/IME/observer）复用，
  采集策略（找祖先/字段集/跳过规则）按场景实现（distill/replay 各一套）
- **采集时绑定**：stage 在录制时就确定（不再事后猜），消除 `?` 标记
- **阶段级快照**：一个 stage 一份 DOM 文本，多步共享（对齐现有 trace 格式）

---

## 三、详细设计

### 3.1 公共库：dom-snapshot（✅ 已完成，0.1.0 已发版）

> **当前状态**：dom-snapshot 公共库已开发完成并发版 0.1.0，TreeWalker 已接入（本地 editable），
> treeforge 待接入（P2.2 前置）。独立仓库地址：`D:/dev/git/z_jordon/dom-snapshot`。
> 以下为抽取的设计记录（已完成），保留作为架构参考。

#### 3.1.1 抽取范围（已完成）

从 `TreeWalker/src/tree_walker/browser/` 抽取 5 个核心文件（约 3453 行）：

| 源文件 | 行数 | 职责 | 抽取后位置 |
|---|---|---|---|
| `views.py`（DOM 部分） | ~400 | 数据模型（EnhancedDOMTreeNode 等，纯 dataclass） | `dom_snapshot/models.py` |
| `cdp_timeout.py` | 195 | 两阶段超时批处理（采集基础设施） | `dom_snapshot/cdp_timeout.py` |
| `paint_order.py` | 200 | Step 2 遮挡算法（独立算法模块） | `dom_snapshot/paint_order.py` |
| `dom.py` | 1055 | 三源采集 + 增强树构建 + 交互检测 | `dom_snapshot/collector.py` |
| `serializer.py` | 1173 | 五步过滤管线 + 文本格式化 | `dom_snapshot/serializer.py` |

**新仓库结构**：

```
dom-snapshot/                          # 新 git 仓库
├── pyproject.toml
├── src/dom_snapshot/
│   ├── __init__.py                    # 暴露 public API
│   ├── models.py                      # DOM 数据模型（从 views.py 拆出）
│   ├── cdp_timeout.py                 # 零改动迁移
│   ├── paint_order.py                 # 仅 import 路径调整
│   ├── collector.py                   # 原 dom.py，处理循环依赖
│   ├── serializer.py                  # 原 serializer.py，处理循环依赖
│   └── _protocol.py                   # CDPLikeClient Protocol（解耦 cdp-use）
├── tests/
└── README.md
```

#### 3.1.2 三个必须处理的耦合点

**耦合点 1：`collector.py ↔ serializer.py` 循环依赖**
- 现状：`dom.py:1037` 懒导入 `DOMTreeSerializer`，`serializer.py:688` 懒导入 `dom.is_interactive`
- 方案：把 `is_interactive` / `ClickableElementDetector`（`dom.py:74-218`）独立成 `dom_snapshot/interactive.py`，打破循环

```python
# dom_snapshot/interactive.py（新文件，从 dom.py 抽出）
class ClickableElementDetector: ...
def is_interactive(node) -> bool: ...

# collector.py 和 serializer.py 都从 interactive.py 导入，无循环
```

**耦合点 2：`views.py` 是混合文件，含两类模型**
- DOM 快照核心（纯 dataclass，应随库走）：`EnhancedDOMTreeNode` / `SimplifiedNode` / `SerializedDOMState` / `FileInputInfo` 等
- 浏览器聚合状态（pydantic，应留在 TreeWalker）：`BrowserStateSummary` / `TabInfo` / `BrowserEvent` / `DOMInteractedElement`
- 方案：`views.py` 拆成两半——DOM 核心进 `dom_snapshot/models.py`，聚合状态留在 TreeWalker 的 `browser/views.py`

**耦合点 3：对 `cdp_use.CDPClient` 的硬依赖**
- 现状：`dom.py:21` `from cdp_use import CDPClient`
- 方案：定义 `CDPLikeClient` Protocol（鸭子类型），库本身用 `TYPE_CHECKING` 引用，不硬依赖 `cdp-use` 包

```python
# dom_snapshot/_protocol.py
from typing import Protocol, TYPE_CHECKING, Any, Awaitable
if TYPE_CHECKING:
    from cdp_use import CDPClient

class CDPLikeClient(Protocol):
    """快照库只依赖这个鸭子类型，调用方传 cdp-use 客户端或任何兼容实现。"""
    def send(self, domain: str, method: str, params: dict | None = None, *,
             session_id: str | None = None) -> Awaitable[Any]: ...
```

#### 3.1.3 Public API

```python
# dom_snapshot/__init__.py
from .collector import build_dom_state
from .models import SerializedDOMState, EnhancedDOMTreeNode, FileInputInfo
from ._protocol import CDPLikeClient

__all__ = [
    "build_dom_state",      # async (client, session_id, prev_map, cfg) -> (SerializedDOMState, metrics)
    "SerializedDOMState",   # 含 element_tree_text / selector_map / file_inputs_meta / page_stats
    "EnhancedDOMTreeNode",  # selector_map 的 value 类型
    "FileInputInfo",        # file_inputs_meta 的元素类型
    "CDPLikeClient",        # CDP 客户端鸭子类型
]
```

#### 3.1.4 迁移策略（已完成）

| 阶段 | 动作 | 状态 |
|---|---|---|
| **M1** | 创建 dom-snapshot 仓库，复制 5 文件，处理 3 个耦合点，跑通独立测试 | ✅ 完成 |
| **M2** | dom-snapshot 发版 0.1.0 | ✅ 完成（`dom-snapshot 0.1.0`） |
| **M3** | TreeWalker 改为依赖 dom-snapshot（删本地 5 文件，改 import） | ✅ 完成（本地 editable `../dom-snapshot`） |
| **M4** | treeforge 依赖 dom-snapshot | ⏳ P2.2 前置（从「可选」提级，因采集层 cdp_session.py 直接调 build_dom_state） |

**M3 的 TreeWalker 侧改动点**（已完成）
- `session.py`：`from dom_snapshot import build_dom_state`
- `agent/step.py` / `prompts/system_prompt.py` / `tools/actions.py` / `recorder/recorder.py`：从 `dom_snapshot` import DOM 类型 + 本地 import 聚合类型
- `browser/__init__.py`：重导出调整

### 3.2 采集层实现：TreeForge 自实现，未来承接 TreeWalker 采集层

#### 3.2.1 战略定位：TreeForge 是采集的唯一归属

**关键决策（用户确认）**：TreeForge 自己实现采集层的全部逻辑（不跨仓库 import TreeWalker 内部模块），
且**后续 TreeWalker 的采集层（录制器）要整个迁到 TreeForge**。所以采集层的设计从一开始就要考虑
「未来 TreeWalker 也要用」，但落地在 treeforge。

这与 dom-snapshot 的思路完全一致——**可共用部分后续抽成公共库，业务专属部分各自实现**。

**为什么不让 treeforge 跨仓库 import TreeWalker 的 recorder 模块？**

跨仓库 import 看似「复用」，实则是更隐蔽的耦合，有三个实质问题：

1. **反向耦合**：treeforge import tree_walker.recorder 内部包，等于 treeforge 强依赖 TreeWalker 仓库的
   内部模块结构。TreeWalker 重构 recorder 包（改名、拆分、改签名）会直接打碎 treeforge。
2. **依赖图混乱**：dom-snapshot 是「TreeWalker 和 treeforge 的共同下游」（方向正确）。但若 treeforge
   import tree_walker，tree_walker 处在尴尬的中间位置（既是 treeforge 的依赖，又是 dom-snapshot 的消费者）。
3. **pip 安装不现实**：TreeWalker 是带 CLI 的应用（`src/` 布局，未发 PyPI），跨仓库 import 只能靠
   git submodule 或 `pip install -e ../TreeWalker`，都是脆弱的本地开发耦合。

**正确的复用方式**：像 dom-snapshot 那样，把「真正可共用」的部分抽成有 public API + 发版的公共库。
录制能力是否抽公共库，等 TreeForge 采集层跑通、TreeWalker 采集层迁入时，基于实际重合度再决定
（见 3.2.6 的 record-core 公共库规划）。

**为何用 CDP 后端而非 MV3 扩展**：

| 维度 | CDP 后端（本方案） | MV3 扩展 |
|---|---|---|
| 快照质量 | ✅ 完整三源采集（CDP 域可用） | ❌ MV3 沙箱无法访问 CDP 域，快照降级 |
| 复用成熟代码 | ✅ 借鉴 TW 录制器结构自己实现 | ❌ 重写一遍采集+去噪 |
| 用户门槛 | ⚠️ 需 `--remote-debugging-port` 启动 Chrome | ✅ 装扩展即可 |
| 实现成本 | ⚠️ 自实现，但换彻底解耦 | ❌ 全新开发 |

**结论**：P2 用 CDP 后端保证快照质量（对齐 P0.5 验证过的格式），P2.5 再考虑 MV3 扩展
降低使用门槛（但 MV3 的快照质量天花板低于 CDP，需评估）。

#### 3.2.2 TreeWalker 录制器模块的「通用 vs 专属」分析（自实现的参考蓝本）

虽然 TreeForge 自实现，但 TreeWalker 的录制器是宝贵的参考蓝本（已踩过坑的成熟实现）。
基于对其代码的逐模块分析，把录制器模块按「通用算法/协议（可共用蓝本）」vs
「TreeWalker 业务专属（自实现时不照搬）」分类：

**通用算法/协议（TreeForge 自实现时借鉴，未来可抽公共库）**：

| TreeWalker 模块 | 职责 | 通用性判定 |
|---|---|---|
| `recorder/models.py` | ActionRecord/Signal/SignalKind/ElementRef/Recording | ✅ 通用录制语义，零外部依赖。`action_name` 取值集（click/input_text 等）是通用浏览器动作名 |
| `recorder/event_mapper.py` | 扩展事件 type → action 名映射 | ✅ 纯 1:1 映射（scroll clamp 阈值需参数化） |
| `recorder/translation.py` | 事件→ActionRecord（映射+input 聚合+状态机） | ✅ 通用「连续输入合并」+ Selenium IDE 式状态机 |
| `recorder/locator.py` | 四级定位 TEXT→XPATH→ATTRIBUTE→RECT | ✅ 通用算法，依赖 selector_map 节点鸭子类型。**已是事实公共件**（rerun.py:32 已 import） |
| `recorder/rules.py`（部分） | `rule_merge_inputs`/`rule_redundant_click`/`rule_merge_scrolls` | ✅ 通用去噪（合并连续输入/折叠重复 click/合并同向 scroll） |
| `recorder/server.py`（骨架） | aiohttp 5 端点路由（/start /event /signal /stop /health） | ✅ 通用 HTTP 协议骨架 |
| 扩展 `recording_extension/`（全部 TS） | DOM 事件采集器（action/navigation/side-effect） | ✅ 零 TreeWalker 业务逻辑，纯通用 DOM 事件源 |

**TreeWalker 业务专属（TreeForge 自实现时不照搬）**：

| TreeWalker 模块 | 专属理由 | TreeForge 怎么处理 |
|---|---|---|
| `recorder/rules.py`（部分） | `rule_navigation_signal` / `rule_file_upload`（click 吸收）**是为重放幂等性定制的**——丢弃用户真实 navigate/upload-click 是因为「回放会再次触发」。TreeForge 只采集不重放，照搬会错误丢弃真实操作 | TreeForge 只用 3 条通用去噪规则，不要这 2 条重放对齐规则 |
| `recorder/flatten.py` | 产 `AgentHistoryList`（TreeWalker 重放端格式），含 `user_pause_seconds` 等重放专用字段 | TreeForge 产 treeforge trace，用自己的 reshape（`export.py`） |
| `recorder/recorder.py`（Recorder 类） | 强耦合 BrowserSession + agent.views + rerun.resolve_rerun_path + 重放语义线索契约（`_semantic_clue`/`_upload_clue`） | TreeForge 写自己的 `collector.py`，借鉴其「实时定位+指纹+三重兜底」结构 |
| `browser/session.BrowserSession` | 3818 行大类，录制只用 get_state 那点，但焊死了 700 行 agent 动作执行 JS | TreeForge 写轻量 `cdp_session.py`（~50 行，只做 CDP 连接 + 委托 dom-snapshot） |

**关键洞察**：录制和重放是两条解耦链路，中间契约只是 rerun-history JSON 文件。
TreeForge 采集层不产 rerun-history（产 treeforge trace），**不影响 TreeWalker 重放**——
重放只认 JSON dict，不碰 recorder 包任何类型。唯一要注意的是：若未来要让 TreeWalker 重放
TreeForge 采集的产物，需一个适配器把 treeforge trace 转成 AgentHistoryList（可选，P2 不做）。

**语义线索契约的注意事项**：TreeWalker 的 `_store_semantic_clue`（recorder.py:284）产出的
`interacted_element` 形态，与重放端 `_match_element_index`/`locate_by_ref` 是隐式契约。
TreeForge 自实现时若也要产语义线索（定位失败的兜底），字段格式要参照 TreeWalker 文档化，
否则未来跨工程重放会静默坏掉。

#### 3.2.3 treeforge/capture/ 模块设计

```
treeforge/capture/
├── __init__.py
├── backend.py        # aiohttp 后端，收扩展事件（复用 TW server 骨架 + 5 端点）
├── collector.py      # 采集器主类（借鉴 TW Recorder 结构，stop 产 treeforge 格式）
├── cdp_session.py    # 轻量 CDP 包装（~50 行，连 CDP + 委托 build_dom_state）
├── stage.py          # 阶段切换判定 + 自动命名
└── export.py         # ActionRecord → treeforge trace + 快照双文件
```

**`collector.py` 核心流程**（借鉴 TW `recorder.py:_handle_event_impl`，实时采集原则不变）：

```python
class Collector:
    """TreeForge 采集器：收扩展事件 → 实时定位+快照+stage → 产 treeforge trace。

    与 TW Recorder 的区别：
    - browser 用轻量 CdpSession（非 BrowserSession）
    - 不产 rerun-history，stop 时调 export 产 treeforge trace + 快照
    - 每事件额外取 element_tree_text + 判定 stage（TW Recorder 丢弃 element_tree_text）
    """
    def __init__(self, browser: CdpSession, output_dir: Path): ...

    async def handle_event(self, event: dict) -> None:
        async with self._lock:
            # Stage 1: 事件映射 + 聚合（复用 TW translation.translate_event）
            action = translate_event(event, self.recording)
            if action is None:
                return

            # Stage 2: 实时 get_state（复用 dom-snapshot.build_dom_state 经 CdpSession）
            state = await self.browser.get_state()
            selector_map = state.dom_state.selector_map
            dom_text = state.dom_state.element_tree_text or ""  # TW Recorder 在此丢弃，这里保留

            # Stage 3: 阶段切换判定 + stage 打标（TreeForge 专属，见 stage.py）
            new_stage = self.stage_tracker.detect_change(state.url, dom_text, action.signals)
            if new_stage:
                action.dom_snapshot = dom_text  # 新阶段才存快照（阶段级，非每步）
                action.stage = self.stage_tracker.name_stage(state.url, new_stage)
            else:
                action.stage = self.stage_tracker.current_stage

            # Stage 4: 实时定位 + 指纹（复用 TW locator.locate_by_ref）
            if needs_target(action.action_name):
                index, node = locate_by_ref(action.element_ref, selector_map)
                if index is not None:
                    action.params["index"] = index
                    action.interacted_element = [DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict()]

            # Stage 5: 填 url/title（同 TW Recorder）
            action.page_url = state.url
            action.page_title = state.title

    async def stop(self) -> Path:
        """去噪（复用 TW rules.apply_rules）+ 导出 treeforge 双文件。"""
        self.recording.actions = apply_rules(self.recording.actions)
        return export_treeforge_trace(self.recording, self.output_dir)
```

**关键约束**（来自 TW `recorder.py:17-18` 的实时采集原则）：快照必须在事件到达时采集，
不能挪到 stop（modal 打开时 DOM 是活的，stop 时 modal 已关失真）。

**`cdp_session.py` 轻量包装**（照抄 TW `session.py:_connect` ~30 行 + 委托 build_dom_state）：

```python
class CdpSession:
    """轻量 CDP 会话：只做「连 CDP + get_state」，不含动作执行。

    替代 TW BrowserSession（3818 行，含 700 行动作 JS）。录制只用 get_state，
    所以这里只保留 CDP 握手 + 委托 dom-snapshot.build_dom_state。
    """
    def __init__(self, ws_url: str): ...
    async def start(self) -> None:
        # 照抄 session.py:_connect 的 Target.attachToTarget + Page/DOM.enable
        ...
    async def get_state(self) -> BrowserStateSummary:
        dom_state, _ = await build_dom_state(self.client, self.session_id, ...)
        return BrowserStateSummary(url=..., title=..., dom_state=dom_state)
    async def stop(self) -> None: ...
```

#### 3.2.4 阶段切换判定 + stage 自动命名（`stage.py`）

不能只靠 URL（SPA 无效，rerun_to_trace.py:178 已验证）。组合信号：

```python
class StageTracker:
    """阶段切换判定 + 自动命名。状态在采集器实例内累积。"""

    def detect_change(self, url: str, dom_text: str, signals: list) -> str | None:
        """判定是否进入新页面阶段。返回新 stage 的「原始标识」，由 name_stage 命名。"""
        dom_hash = hashlib.sha256(dom_text.encode()).hexdigest()[:8]

        # 信号 1：显式跨页导航（多页表单站点）
        if any(s.kind == SignalKind.NAVIGATION for s in signals):
            return f"nav:{url}"

        # 信号 2：URL 变化（path 段变了）
        new_path = urlparse(url).path
        if new_path != self._last_url_path:
            self._last_url_path = new_path
            return f"url:{new_path}"

        # 信号 3：DOM 文本变化率超阈值（SPA 阶段切换）
        # 用相似度而非严格相等（DOM 有少量动态内容如时间戳）
        if self._last_dom_hash and dom_hash != self._last_dom_hash:
            similarity = _dom_similarity(self._last_dom_text, dom_text)  # Jaccard 行集合相似度
            if similarity < 0.7:   # 阈值待调参，初版 0.7
                return f"dom:{dom_hash}"
        self._last_dom_hash = dom_hash
        self._last_dom_text = dom_text
        return None  # 无切换

    def name_stage(self, url: str, raw_stage: str) -> str:
        """URL path 段优先，无法提取时用 stage_N。"""
        kind, _ = raw_stage.split(":", 1)
        if kind in ("url", "nav"):
            path = urlparse(url).path.strip("/")
            if path:
                # 取最后一个有意义的 path 段（如 /platform/upload/video/frame → frame）
                segments = [s for s in path.split("/") if s and s not in ("api", "platform", "www")]
                if segments:
                    return segments[-1].lower()
        # 兜底：序号命名
        self._stage_counter += 1
        return f"stage_{self._stage_counter}"
```

**阈值调参**：初版 DOM 相似度阈值 0.7（经验值），需要用 bilibili 实测数据校准。
Bilibili 三个阶段（upload/publish/upload-conver）的 DOM 差异应远大于 0.3（upload 阶段无表单，
publish 阶段有完整表单，upload-conver 阶段有封面编辑器）。

**命名示例**：
- bilibili upload 阶段：URL `/platform/upload/video/frame` → `frame`
- bilibili upload-conver 阶段：URL 不变，DOM 变化触发 → `stage_2`（兜底）
- 实际命名可能不如人工的 `upload-conver` 语义化，但**确定**（无 `?`），distiller 能正常用

#### 3.2.5 Chrome 扩展：TreeForge 自研可复用扩展

**战略定调（用户确认）**：TreeForge 自写面向蒸馏采集的 Chrome 扩展，且后续 TreeWalker 的扩展
工作也迁到 TreeForge。扩展从一开始就按「TreeForge 是采集的唯一归属」设计，保证可复用、可扩展。

##### 为何不复用 TreeWalker 现成的扩展

初版方案曾判断「TreeWalker 扩展零业务逻辑，可复用」，深入核查后**这个判断是错的**。准确结论：
**采集机制骨架通用（~50%），但每一处过滤/格式决策都嵌着 TreeWalker 重放假设（~50%）**。

三个**结构性缺口**导致复用 TreeWalker 扩展不可行：

1. **DOM 快照完全不采集**：扩展 grep 零 `element_tree_text` 引用，但 TreeForge 蒸馏把 DOM 快照
   列为 quirks 的 PRIMARY source——必须另建采集层
2. **被扩展过滤的真实操作不可逆**：扩展按「重放定位不到」丢弃非可交互 click（`action-recorder.ts:226-230`）、
   跳过 radio/checkbox/file（`:256-259`）——这些是用户真实操作，TreeForge 蒸馏想看，但已被永久丢弃
3. **事件格式深度对齐重放**：type 枚举是 TreeWalker action 注册表的镜像（非通用浏览器动作）；
   `upload_ctx` 整块、`xpath/rect` 字段、SignalEvent 两种类型都是为后端 rules/重放 matcher 定制

复用 TreeWalker 扩展 + 后端转换，**实际节省有限**（仍需自建快照采集 + 重写部分逻辑 + 反解析 test-* 属性），
且数据被重放假设污染。自研扩展反而能减 30-50% 工作量且数据贴合蒸馏需求。

##### 通用骨架 vs 可插拔策略（架构核心）

TreeWalker 扩展的代码可精确拆成两层：

**通用采集骨架（~50%，复用 + TreeWalker 迁入共用）**：

| 骨架能力 | TreeWalker 源位置 |
|---|---|
| 事件绑定/解绑机制（on() 工厂 + cleanup） | `action-recorder.ts:372-376` |
| input 400ms 去抖合并状态机 | `:166,189-218` |
| IME 组合输入抑制（compositionstart/end） | `:169,343-344` |
| contenteditable MutationObserver 机制 | `:390-415` |
| scroll 累计 + 500ms 空闲去抖 | `:347-369` |
| MAIN-world 注入（history hook + AEL hook 架构） | `injected.ts` |
| content 编排层（install/uninstall + 状态广播响应） | `content.ts:41-74` |
| background 传输/状态层（state mgmt + routing） | `background.ts` |
| HTTP 传输层（端点/schema 可参数化） | `backend.ts` |

**采集策略（~50%，可插拔，各场景各一套）**：

| 策略决策 | TreeWalker 源位置 | 蒸馏场景怎么处理 |
|---|---|---|
| `findInteractiveAncestor` 四启发式 | `:50-83` | 接口化；蒸馏用前三道（去掉 `data-tw-jsclick` 私有标记） |
| 跳过非可交互 click（重放定位假设） | `:229-230` | **蒸馏不跳过**——保留误操作/无效点击（蒸馏反而需要） |
| 跳过 radio/checkbox/file input | `:256-259` | **蒸馏不跳过**——如实记录 |
| EDIT_KEYS 抑制 / 键盘分类 | `:37-44,289-295` | 接口化；蒸馏策略按需 |
| scroll 方向反转冲刷（重放幂等） | `:365` | 蒸馏去掉（如实记录） |
| `upload_ctx` 整块（重放匹配专属） | `:96-143,308-340` | 蒸馏不要；改采 placeholder/type/accept |
| emit 字段集（xpath/rect/classes） | `:240-246` | 蒸馏改采 data-testid/data-cy/placeholder 等 |
| SignalEvent（为后端 rules 服务） | `side-effect-observer.ts` | 蒸馏按需（如保留 modal 检测供 stage 判定） |

##### 字段采集：extractor 注册表（关键改造）

TreeWalker 的 `buildElementRef`（`selector.ts:10-30`）是单体函数，无条件算全部字段（含昂贵的 xpathFor）。
自研扩展要改成 **extractor 注册表**——每个字段一个独立提取器，由策略声明「本次要哪些」，骨架按需懒调。

```
extractors/
├── tag.ts id.ts name.ts role.ts aria-label.ts text.ts   # 通用指纹
├── test-attrs.ts          # data-testid/data-test/data-cy（蒸馏新增，TreeWalker 内嵌未外露）
├── placeholder.ts type.ts contenteditable.ts visible-text.ts  # 蒸馏新增
├── xpath.ts rect.ts classes.ts selector.ts               # 重放定位字段（可选）
└── registry.ts            # 按 FieldSpec 选调，贵计算（xpath/rect）懒执行
```

蒸馏策略只注册前两组（通用指纹 + 蒸馏新增），跳过 xpath/rect；重放策略（TreeWalker 迁入）注册全部。

##### 建议的扩展目录结构

```
treeforge/extension/                # 或独立仓库 treeforge-extension
├── wxt.config.ts
├── entrypoints/
│   ├── background.ts        # 通用：状态 + 传输 + routing
│   ├── content.ts           # 通用：编排，装配时读策略
│   ├── injected.ts          # 通用：history + AEL hook（标记名改中性 data-tf-jsclick）
│   └── popup/               # 通用 UI
├── core/                    # 通用骨架（无策略，未来 TreeWalker 迁入共用）
│   ├── recorder-engine.ts   # 从 action-recorder.ts 抽出机制层
│   ├── event-bus.ts         # on()/cleanup/emit
│   ├── debounce.ts          # coalesce/flush
│   ├── ime.ts               # composition 处理
│   ├── ce-observer.ts       # contenteditable 观察机制
│   ├── scroll-accumulator.ts
│   └── transport.ts         # 从 backend.ts 泛化（端点/schema 参数化）
├── extractors/              # 字段提取器注册表（见上）
├── strategies/              # 可插拔策略
│   ├── types.ts             # CollectionStrategy 接口
│   ├── distill/             # TreeForge 蒸馏策略（P2 实现）
│   │   ├── index.ts
│   │   └── schema.ts        # 蒸馏事件 schema
│   └── replay/              # TreeWalker 重放策略（TreeWalker 迁入时实现）
│       ├── click-heuristic.ts
│       ├── upload.ts
│       └── schema.ts
├── shared/
│   ├── envelope.ts          # 通用 CaptureEnvelope（见下）
│   └── protocol.ts          # 后端协议抽象
└── config/
    └── scenarios.ts         # 场景 → 策略绑定
```

##### 协议设计：通用 envelope + scenario 标记

蒸馏和重放共享同一传输层，但走不同后端 handler。用通用 envelope + scenario 标记实现：

```typescript
// 通用信封（所有场景共用）
interface CaptureEnvelope {
  scenario: 'distill' | 'replay';   // 后端按此路由
  session_id: string;
  ts: number;
  url?: string;
  is_top_frame?: boolean;
  payload: unknown;                 // 场景特定 schema（策略产出）
}
```

- 后端 `POST /ingest`（通用入口）按 `scenario` 分发：`distill` → treeforge 采集落盘 pipeline；
  `replay` → 现有 event_mapper/translation/rules（TreeWalker 迁入后）
- `POST /start { scenario, config }` 让后端预置对应 handler 链
- 字段 schema 由策略在 `payload` 内自描述，后端不强校验重放专属字段

这样蒸馏和重放共享骨架和传输层，仅策略层和后端 handler 链不同，满足「可复用、可扩展、TreeWalker 迁入」。

##### 阶段安排

| 阶段 | 扩展工作 | 说明 |
|---|---|---|
| **P2.3（采集层扩展）** | 实现 `treeforge/extension/`：core 骨架 + extractors + distill 策略 + 通用 envelope | 蒸馏场景可用；骨架为 TreeWalker 迁入留好结构 |
| **P2.4（TreeWalker 迁入，可选）** | 把 TreeWalker 扩展的重放逻辑封装成 replay 策略迁入 | strategies/replay/ + 重放 handler；TreeWalker 删本地扩展 |

> 注意：P2.3 是扩展从零实现，工作量较大（WXT/TS 工程 + 骨架抽取 + 策略接口设计）。
> 如果想先快速验证采集后端链路，可先用一个最小扩展（只采 click/input + 几个字段）跑通，
> 再逐步补全骨架和策略。

#### 3.2.6 未来公共库规划（TreeWalker 采集层迁入时的架构指引）

当 TreeForge 采集层跑通、TreeWalker 采集层开始迁入时，基于实际代码重合度，可把「真正可共用」
的部分抽成公共库（和 dom-snapshot 同样的模式）。这是**未来工作**，P2 不做，但当下自实现时
要为它留好结构（模块边界清晰、不绑死 TreeForge 业务）。

基于对 TreeWalker 录制器的逐模块分析，未来可抽的公共件有三个（按依赖顺序）：

**库 A：capture-protocol（TS+Py 双端，采集协议）**
- 内容：`RecorderEvent`/`SignalEvent`/`ElementRef` 类型定义、`upload_ctx`/`_semantic_clue`/
  `data-*-jsclick` 等字段约定、5 个 HTTP 端点契约
- 不含：任何 DOM 采集实现或后端逻辑
- 消费者：扩展（TS）+ TreeForge 采集层（Py）+（过渡期）TreeWalker recorder（Py）
- 价值：锁定扩展↔后端的协议契约，防字段名漂移

**库 B：record-core（Py，录制算法）**
- 内容：`models.py`（ActionRecord/Signal/...）+ `event_mapper.map_event` +
  `translation.translate_event` + `locator.py`（四级定位）+ 通用去噪规则
  （merge_inputs/redundant_click/merge_scrolls）
- **不含**：重放对齐规则（navigation_signal/file_upload click 吸收——这些留 TreeWalker 重放端）、
  `flatten.py`（AgentHistoryList reshape）、Recorder 编排类、BrowserSession 依赖
- 消费者：TreeForge 采集层 +（过渡期）TreeWalker recorder
- 依赖：dom-snapshot（selector_map 节点鸭子类型）
- 价值：录制语义和算法单一实现，TreeWalker 重构 recorder 不影响 TreeForge

**库 C：cdp-session（Py，CDP 编排壳，可选）**
- 内容：BrowserSession 里「CDP 连接 + target 选择 + switch_tab + get_tabs +
  file-chooser intercept 开关 + 调 build_dom_state 的 get_state 薄壳」
- **不含**：所有动作执行能力（click/input/upload/select 脚本、highlight、download）
- 消费者：TreeForge 采集层 +（过渡期）TreeWalker recorder
- 依赖：dom-snapshot + cdp-use
- 价值：录制/采集共用轻量 CDP 编排，不拖入 700 行动作执行 JS

**当下自实现的纪律（为未来抽库留结构）**：

- treeforge/capture/ 内部模块按「算法/协议 vs 业务」分文件，不混在一个大类里
  （collector.py 是业务编排，stage.py/export.py 是 TreeForge 专属，算法部分独立成函数）
- 数据模型（ActionRecord 等）字段名与 TreeWalker 对齐（未来抽 record-core 时零迁移成本）
- 扩展协议字段严格按 `shared/types.ts` 实现，不自创字段名
- **不**为了未来抽库而提前抽象——先在 treeforge 跑通，重合度实证后再抽（避免过度设计）

### 3.3 产物格式与关联

#### 3.3.1 两类文件

采集层产出（替代当前人工流程）：

```
<rerun_dir>/<name>/
├── trace.json                    # 文件 A：treeforge trace（直接可蒸馏）
│   ├─ host
│   ├─ task_instruction
│   ├─ events[]
│   │   ├─ element_attrs          （从 interacted_element 提取）
│   │   ├─ stage                  （采集时确定，无 ?）
│   │   ├─ type / value / url
│   │   └─ timestamp
│   └─ page_context               （阶段名 → element_tree_text）
│       ├─ "frame": "[122]<i />\n..."
│       ├─ "stage_2": "[122]<i />\n..."  （upload-conver）
│       └─ ...
├── rerun.json                    # 副产物：原始 rerun-history（兼容 TreeWalker 重放）
└── snapshots/                    # 副产物：每阶段 DOM 文本（人工审阅/调试用）
    ├── frame.txt
    ├── stage_2.txt
    └── ...
```

**设计选择**：page_context 内联进 trace.json（而非外部引用），与现有 `bilibili-upload.trace.json` 格式完全一致，distiller 无需改动。

#### 3.3.2 关联关系（采集时绑定，非事后猜测）

| 维度 | 当前（人工 + 启发式） | P2（采集时绑定） |
|---|---|---|
| stage 来源 | 人工 .txt 文件名 | 采集层 `_name_stage` 自动生成 |
| stage 准确性 | `_infer_stages` 猜测，8/17 带 `?` | 采集时确定，0 带 `?` |
| 快照时机 | 人工手动导出，可能错过阶段 | DOM hash 自动检测，精确到事件级 |
| 关联强度 | 事后元素指纹反查 | 采集时直接绑定 |

#### 3.3.3 ADAPT 层简化（P2.4 已完成）

P2 产物确定后，`tools/rerun_to_trace.py` 已大幅简化为纯格式转换器：

```python
# 已删除：_infer_stages 三规则（URL/元素指纹/时序外推），约 50 行
# 已删除：dom_dir 参数读人工 .txt 注入 page_context
# 已删除：_url_to_stage_hint / _element_stage_fingerprint / _disambiguate_publish_vs_cover
```

rerun_to_trace.py 现在是纯「格式转换器」：rerun-history → treeforge trace，
不推断 stage、不注入 page_context（rerun-history 本身不含这些信息，产出 trace
无 stage 字段，distiller 容错处理）。

---

## 四、实施步骤

### 阶段 P2.1：dom-snapshot 公共库（✅ 已完成）

| 步骤 | 内容 | 状态 |
|---|---|---|
| **P2.1.1** | 创建 dom-snapshot git 仓库，复制 5 文件，处理 3 个耦合点 | ✅ 完成 |
| **P2.1.2** | dom-snapshot 发版 0.1.0 | ✅ 完成 |
| **P2.1.3** | TreeWalker 改为依赖 dom-snapshot（删本地 5 文件，改 import） | ✅ 完成 |
| **P2.1.4** | treeforge 依赖 dom-snapshot | ⏳ P2.2.0 前置（见下） |

### 阶段 P2.2：采集后端（treeforge Python 采集层）

| 步骤 | 内容 | 验证 | 依赖 |
|---|---|---|---|
| **P2.2.0** | **treeforge 接入 dom-snapshot**：`pyproject.toml` 加 `dom-snapshot>=0.1.0`（本地 editable `../dom-snapshot`，对齐 TreeWalker 配置）；`uv sync` 验证 | `uv run python -c "from dom_snapshot import build_dom_state"` 不报错 | P2.1.2 |
| **P2.2.1** | 建 `treeforge/capture/` 包骨架：cdp_session.py（轻量 CDP 包装，调 `from dom_snapshot import build_dom_state`）+ backend.py（aiohttp 收扩展事件，实现 /ingest + /start + /stop 端点，支持 scenario 路由） | backend.py 跑起来，能用 curl POST /ingest 收到 envelope 并按 scenario 分发 | P2.2.0 |
| **P2.2.2** | collector.py 采集器主类：handle_event 走通「事件映射 + get_state + 定位」，借鉴 TW Recorder 结构但不产 rerun-history | 配合 P2.3 的最小扩展，录制 bilibili，事件流跑通，interacted_element 正确填充 | P2.2.1 + P2.3.1 |
| **P2.2.3** | stage.py 阶段切换判定 + 自动命名；collector 在 get_state 后取 element_tree_text + 打 stage 标 | 录制 bilibili，确认 stage 无 `?`，快照在阶段切换时落盘 | P2.2.2 |
| **P2.2.4** | 阶段切换阈值调参（DOM 相似度 0.7 校准） | bilibili 三阶段都被正确识别（upload/publish/upload-conver 不漏切不多切） | P2.2.3 |
| **P2.2.5** | export.py：stop 时去噪 + 导出 treeforge trace + 快照双文件 | 产出的 trace.json 能被 treeforge distill 跑通 | P2.2.3 |
| **P2.2.6** | 端到端验证：录制 bilibili → treeforge distill → 对照 P1 的手写 skill | 蒸馏质量 ≥ P0.5 人工流程 | P2.2.5 + P2.3.2 |

### 阶段 P2.3：采集扩展（treeforge 自研 Chrome 扩展）

> 扩展是采集层的事件源。鉴于 TreeWalker 扩展为重放深度定制（三个结构性缺口，见 3.2.5），
> TreeForge 自研面向蒸馏的扩展，且按「通用骨架 + 可插拔策略」设计为 TreeWalker 迁入铺路。

| 步骤 | 内容 | 验证 | 依赖 |
|---|---|---|---|
| **P2.3.1** | **最小可用扩展**：WXT 工程 + entrypoints（background/content/popup）+ core 骨架（事件绑定/input 去抖）+ distill 策略最小集（click/input + tag/id/name/role/text 字段）+ 通用 CaptureEnvelope | Chrome 加载扩展，popup 点录制，操作页面，backend.py 能收到 click/input 事件 | 无（可与 P2.2.1 并行） |
| **P2.3.2** | **扩展补全**：加 IME/contenteditable observer/scroll 去抖 + extractor 注册表（补 data-testid/placeholder/type/contenteditable/visible_text）+ distill 策略完整 click 处理（不跳过误操作） | 录制 bilibili 完整流程（含富文本/标签 Enter/封面上传），事件字段覆盖蒸馏白名单 | P2.3.1 |
| **P2.3.3** | **策略接口固化**：定义 `CollectionStrategy` 接口；distill 策略实现完整；为 replay 策略留接口和目录占位（strategies/replay/） | distill 策略可独立工作；replay 目录有接口骨架（空实现） | P2.3.2 |

### 阶段 P2.4：ADAPT 层简化 + 文档

| 步骤 | 内容 | 验证 | 依赖 |
|---|---|---|---|
| **P2.4.1** ✅ | 删除 `rerun_to_trace.py` 的 `_infer_stages` + `dom_dir` 逻辑 | treeforge 测试全过（149 passed） | P2.2.5 |
| **P2.4.2** | 更新 examples（用 P2 产物替换人工 trace） | distill 跑通 | P2.4.1 |
| **P2.4.3** | 更新 ROADMAP / 文档 | 人工审阅 | P2.4.2 |

---

## 五、风险与权衡

| 风险 | 影响 | 应对 |
|---|---|---|
| **dom-snapshot 抽取引入回归** | TreeWalker agent 行为变化（快照格式漂移） | M3 强制端到端验证；保留 M2 回滚能力；抽取是纯重组不改逻辑 |
| **阶段切换阈值难调** | DOM 相似度 0.7 可能误判（漏切或多切） | P2.2.3 专项调参；bilbili 三阶段差异大（应远超阈值），难点是同阶段微调；预留人工 review 出口 |
| **stage 自动命名不够语义化** | `stage_2` 不如人工的 `upload-conver` 直观 | distiller 对 stage 名不敏感（只作 key 关联）；蒸馏后 skill 不暴露 stage 名；可在 stop 后加可选的 LLM 重命名 |
| **MV3 扩展未来要重做** | P2.5 若做 MV3，快照质量降级 | 明确 MV3 无法访问 CDP 域；P2 用 CDP 后端保证质量，MV3 作为可选的「易用性层」而非「质量层」 |
| **三个工程依赖协调** | dom-snapshot 改动要同步 TreeWalker + treeforge；treeforge 还 import TW 录制通用模块 | 用 git submodule 或 pip install -e 本地开发；CI 跨工程测试；treeforge 对 TW 的依赖限定在通用模块（models/translation/rules/locator），不碰重放专属 |
| **实时采集增加延迟** | 每事件多取 element_tree_text（18KB） | element_tree_text 已由 build_dom_state 生成（get_state 本就调），只是从丢弃改为保留，几乎零新增成本 |
| **轻量 CDP 包装可能遗漏 BrowserSession 的某个细节** | 照抄 `_connect` 可能漏掉 target 发现/attach 的边界 case | 先用 TW Recorder 的 `_ensure_target`/`_resolve_tab_id` 逻辑验证；CdpSession 出问题可回退用 BrowserSession（拖入动作 JS 但能跑） |

---

## 六、与现有方案的关系

### 继承 P0.5 的成果（不推翻）

| P0.5 成果 | P2 处理 |
|---|---|
| `element_attrs` 字段（11 白名单属性） | ✅ 保留，采集层从 interacted_element 提取 |
| `page_context` 字段（阶段→DOM） | ✅ 保留，采集层自动填充替代人工 .txt |
| `stage` 字段（带 `?` = 推断） | ⚠️ 升级：采集时确定，消除 `?`；老 trace 仍兼容 |
| 元素描述表格式（selectors.md 四列） | ✅ 保留，distiller 不动 |
| host 级蒸馏 + 三件套 | ✅ 保留，P2 只改采集不改蒸馏 |

### 与 P1 A/B 结论的对齐

P1 验证了「蒸馏精简版 skill 可用」，但那是在**人工采集**的 trace 上。
P2 的目标：让**自动采集**的 trace 也能蒸馏出同等质量的 skill。
验收标准：P2 自动采集 + 蒸馏的 skill，A/B 测试成功率 ≥ 人工流程的 80%（P1 基线）。

### dom-snapshot 的长期价值

- TreeWalker agent 运行时：复用同一份快照，避免格式漂移
- treeforge 采集层：复用同一份快照，保证蒸馏输入与 agent 运行时一致
- 未来其它消费者（如可视化调试工具、diff 工具）：共享同一份实现

---

## 七、决策记录（已与用户确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| DOM 快照代码共享方式 | 独立仓库抽取完整快照库 | 彻底解耦，三个工程共享同一份，避免格式漂移 |
| 采集后端归属 | **TreeForge 自实现采集层全部逻辑，未来承接 TreeWalker 采集层迁入** | 跨仓库 import 内部模块是隐蔽耦合（反向依赖 + 依赖图混乱 + pip 不可装）；自实现换彻底解耦，且为 TreeWalker 采集层迁入铺路 |
| 采集层复用策略 | **不跨仓库 import TreeWalker**；TreeWalker 录制器作为参考蓝本，通用算法/协议部分借鉴自实现，业务专属部分不照搬；可共用部分未来抽公共库（capture-protocol/record-core/cdp-session，见 3.2.6） | dom-snapshot 模式：可共用部分抽公共库 + 发版，业务专属各自实现；当下自实现留好模块边界，重合度实证后再抽库 |
| 重放对齐规则处理 | TreeForge 只用 3 条通用去噪规则（merge_inputs/redundant_click/merge_scrolls），**不要** TreeWalker 的 navigation_signal/file_upload click 吸收（这俩是为重放幂等性定制，照搬会丢弃用户真实操作） | 录制和重放是两条解耦链路；TreeForge 只采集不重放，去噪目标不同 |
| 扩展策略 | **TreeForge 自研可复用扩展**（不复用 TreeWalker 扩展） | TreeWalker 扩展为重放深度定制（三个结构性缺口：不采 DOM 快照/过滤真实操作/事件格式对齐重放）；自研用「通用骨架+可插拔策略」架构，distill 策略 + 未来 replay 策略共用骨架 |
| 扩展架构 | 通用采集骨架（core/，~50%）+ 可插拔策略（strategies/，~50%）+ extractor 注册表 + 通用 CaptureEnvelope | 骨架复用（绑定/去抖/IME/observer/hook），策略按场景实现（distill 不跳过误操作/采 test-* 属性；replay 保留 TreeWalker 重放逻辑）；满足「可复用、可扩展、TreeWalker 迁入」 |
| 快照采集技术 | CDP（非 MV3 扩展内采） | MV3 沙箱无法访问 CDP 域（DOMSnapshot/DOM/Accessibility），快照质量会降级；扩展只做事件采集，快照由后端经 CDP 采 |
| 快照粒度 | 阶段级快照（1:N，多步共享） | 对齐现有 trace 格式；SPA 友好；冗余小 |
| stage 命名 | URL path 段 / stage_N 自动命名 | 简单确定，无 LLM 成本；distiller 对 stage 名不敏感 |

---

## 八、后续可选扩展（非 P2 范围）

- **P2.5 MV3 扩展**：降低用户使用门槛（装扩展即用，无需 `--remote-debugging-port`），但快照质量受 MV3 沙箱限制
- **LLM 语义 stage 重命名**：stop 后可选调一次轻量 LLM，把 `stage_2` 重命名为 `upload-conver`（提升可读性，不影响功能）
- **跨 session 增量采集**：同一站点多次录制，复用已识别的 stage（减少重复快照）
- **快照 diff 可视化**：用 dom-snapshot 产出对比工具，可视化展示阶段间 DOM 差异（辅助 quirks 蒸馏）
