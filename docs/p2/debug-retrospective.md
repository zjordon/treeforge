# P2 采集层调试复盘：从「采集不到」到「端到端跑通」

> 记录 P2 采集层开发中遇到的真实问题、定位过程、根因和修复。
> 这是一次典型的「单元测试全过但真机跑不通」的调试经历，复盘价值在于：
> 单元测试覆盖不到 MV3 扩展 + CDP + 跨进程通信的集成问题，必须靠真机诊断定位。

## 背景

P2 采集层架构：
- **Chrome 扩展**（TS/WXT）：采用户操作事件（click/input/scroll），POST 到后端
- **Python 后端**（treeforge/capture/）：经 CDP（dom-snapshot）采 DOM 快照 + 判 stage + 导出 trace

采集层代码层面（CdpSession/Collector/StageTracker/backend/export）单元测试 140+ 全过，
但真机录制时出现一系列问题：**没有产物 / 产物是扩展 popup 而非目标页面 / stage 切碎 / 蒸馏崩溃**。
本文档复盘从「没看到产物」到「端到端跑通」的完整调试过程。

---

## 问题 1：Ctrl+C 后无产物（Windows 信号处理）

### 现象
按扩展 popup「停止录制」或命令行 Ctrl+C 后，`data/captures/` 目录不存在，无任何产物。

### 定位过程
1. 先用 `curl` 直接测后端 4 个端点（/start /ingest /stop /health）——**全部正常**，curl 触发的
   ingest/stop 能正确导出 trace.json。证明后端逻辑本身没问题。
2. 排查扩展端：让用户看 background Service Worker 的 console——显示 `recording started` + 
   `recording stopped`，证明扩展和后端连通。
3. 但 popup 显示「事件数一直 0」——说明 content script 没发出事件（见问题 3）。
4. 用户报告 Ctrl+C 时 Python 报 `asyncio.exceptions.CancelledError` 堆栈。

### 根因
`asyncio.run` 在 Windows 上收到 Ctrl+C 时：
- 取消主任务（在 `await stop_event.wait()` 处抛 `CancelledError`）
- 原代码 `except KeyboardInterrupt` **捕获不到 `CancelledError`**（类型不匹配）
- `CancelledError` 上抛 → `asyncio.run` 转成 `KeyboardInterrupt` → **export 逻辑从未执行**

### 修复
不用 `asyncio.run`（Windows Ctrl+C 行为不可控），改用**手动 event loop + 传统 `signal.signal`**：
```python
loop = asyncio.new_event_loop()
stop_event = asyncio.Event()

def _sigint_handler(signum, frame):
    loop.call_soon_threadsafe(stop_event.set)  # 线程安全设置，不取消主任务

signal.signal(signal.SIGINT, _sigint_handler)
loop.run_until_complete(run_capture(stop_event=stop_event, ...))
```
`signal.signal` 的 handler 不会取消主任务（和 `asyncio.run` 内置 SIGINT 不同），
让协程优雅退出并完成导出。

### 教训
- Windows 的 `asyncio.run` + Ctrl+C 行为和 Unix 不同，不能假设跨平台一致
- 长驻命令的信号处理要单独测试（用 `signal.signal` 直接调 handler 模拟，而非 `os.kill`）

---

## 问题 2：导出时机错误（停止录制不导出，等 Ctrl+C）

### 现象
即使按扩展「停止录制」，产物也不生成；必须再按 Ctrl+C 才（可能）导出。
用户质疑：「停止录制」就该产出结果，不该等 Ctrl+C。

### 根因
设计错误：把 Ctrl+C 当成了导出的主路径（开发时图方便），扩展的 `/stop` 只返回元数据不导出。
```
原逻辑：扩展点停止 → /stop 返回元数据 → 不导出 → 用户必须 Ctrl+C → 才导出（但 Windows bug 导致也失败）
```

### 修复
把导出归到 `collector.stop()`（「停止录制」的正确归属），Ctrl+C 降为兜底：
- `collector.stop()`：调 `export_capture` 导出产物，返回 `capture_dir`/`trace_path`
- backend `/stop`：调 `collector.stop()` 后触发 `on_stop` 回调（设置 stop_event 让 cli 退出）
- cli `_stop_and_export`：检测 `collector._started`——扩展已停止则跳过重复导出，Ctrl+C 则保底导出

### 教训
设计时要区分「主路径」和「兜底路径」：用户正常操作（点停止）是主路径，必须产出结果；
异常退出（Ctrl+C）是兜底，保底导出。不能把兜底当主路径。

---

## 问题 3：事件数一直 0（content script 没装配）

### 现象
扩展 popup 显示「录制中」，但事件数始终 0；产物里只有 popup 自己的事件，没有目标页面的事件。

### 定位过程
1. background console 显示 `recording started` → /start 成功，host_permissions 没问题
2. popup 显示「录制中」→ setState 广播了
3. 但事件数 0 → content script 没采到事件

### 根因（双重）
**根因 A**：`recording-active-query` 无 handler。content script 启动时发此消息询问「是否在录」，
   但 background 没处理 → 页面刷新/新开时 content 不知道在录制，永远不装配 recorder。

**根因 B**：MV3 host_permissions 的 localhost vs 127.0.0.1 问题（初版怀疑，后验证 `http://*/*` 已覆盖，
   非真实根因，但加了显式声明更稳）。

### 修复
- background 处理 `recording-active-query`：回复当前状态 + 补发广播给该 tab
- content script 接收回复后据此装配 recorder
- 加诊断 console.log（`recorder installed on <url>`），便于后续排查

### 教训
MV3 消息通信要处理「时序问题」：content script 可能在 background 广播**之后**才加载
（页面刷新/新开），必须有「主动询问」机制让 content 恢复状态。

---

## 问题 4：产物全是 popup 内容（CdpSession 连错 target + url 取错）

### 现象
链路通了（30+ 事件），但产物的 host 是 `new-tab-page`/空，event url 全是
`chrome-extension://...popup.html`，page_context 只有 7 行（popup 的 DOM，不是 bilibili）。
用户的关键质疑：「这套机制在 TreeWalker 中运行正常，现在有什么不同？」

### 定位过程（这是最关键的一次诊断）
对比 TreeWalker 和 treeforge 的 CdpSession 连接时机：

| | TreeWalker | treeforge |
|---|---|---|
| 连接时机 | 进程启动**立即**连 Chrome | 用户点「开始录制」后**懒连接** |
| popup 状态 | 未开（不是 target） | 已开（是 target） |
| 第一个 page target | 用户操作页面（bilibili） | 可能是 popup |

根因：treeforge 把 CdpSession 连接延迟到了用户点开始之后，那时 popup 已是 page target，
「取第一个 page target」选到了 popup。

### 双重根因
**根因 A（CdpSession target 选择）**：`Target.getTargets` 取第一个 `type=page`，不区分 url。
   popup 也是 page target，被错误选中。

**根因 B（collector url 取错）**：collector 用 `fields.get("url")`（payload 里的 url，只有
   navigate 事件有），click/input 没有 url 字段 → 走兜底用 CdpSession 的错误 url（popup）。
   忽略了 envelope **外层**的 url（content script 报的真实页面 url）。

### 修复
**修 A**：CdpSession 选 target 时优先 http/https，跳过 chrome-extension://（popup）、chrome://：
```python
real_pages = [t for t in page_targets if t.get("url", "").startswith(("http://", "https://"))]
candidates = real_pages or page_targets  # 没有真实页面才退而求其次
```

**修 B**：collector url 优先级改为 envelope 外层 > payload > CdpSession 兜底：
```python
url = envelope.get("url") or fields.get("url")  # 优先 content script 报的真实页面
```

**额外**：host 提取跳过 chrome://、new-tab-page 等内部页；start 时首屏若是内部页跳过采快照。

### 教训
- **「取第一个」在多 target 环境是脆弱的**——必须按业务语义排序（真实页面优先）
- **url 来源要分清**：content script 报的（用户实际操作的页面）vs CdpSession 取的（连的 target，
  可能不是用户操作的页面）。两者可能不一致，要以前者为准
- TreeWalker 能跑是因为它的使用场景（启动即连）恰好避开了 target 选择问题，不代表机制本身健壮

### 遗留限制
CdpSession 仍连固定 target，不跟随用户切 tab。如果用户开了多个 http tab，可能选错。
彻底解决需要 content script 报告 tab id，CdpSession 精确 attach——较大改动，留后续。

---

## 问题 5：stage 切碎（DOM 相似度阈值 + 累积漂移）

### 现象
采集到的事件 url/host 正确了，但 stage 被切成 7-8 个（`frame`/`frame_2`/`frame_3`/.../`frame_5`），
理想应该是 3-4 个（upload/publish/upload-conver）。

### 定位过程（数据驱动调参）
写 `tools/analyze_stage_threshold.py` 分析真实产物的相邻 stage 相似度：

| 切换 | 相似度 | 判定 |
|---|---|---|
| article→home | 0.070 | 真切换（不同页面） |
| home→frame | 0.045 | 真切换 |
| frame→frame_1 | 0.407 | 误切（同页） |
| frame_1→frame_2 | 0.123 | 真切换 |
| frame_4→frame_5 | 0.618 | 误切（滚动） |

真实数据显示：**真切换相似度 0.04~0.25，误切 0.41~0.62**。原阈值 0.7 太高（全判为切换）。

### 双重根因
**根因 A（阈值）**：0.7 是拍脑袋定的经验值，远高于真实分界（~0.33）。

**根因 B（累积漂移）**：原逻辑每次比较都更新 `_last_dom_text`（即使没切换）。
   连续滚动 3 次，每次 vs 上一次相似度 0.6（不切），但第 1 次 vs 第 3 次可能差到 0.2（误切）。
   **基准漂移导致误切**。

### 修复
**修 A**：阈值 0.7 → 0.33（数据驱动，卡在真切换/误切之间）。

**修 B**：只在判为切换时才更新基准（始终和**上一个 stage** 的 DOM 比，不和上一个事件比）：
```python
if similarity < threshold:
    self._last_dom_text = dom_text  # 切换了：更新基准
    return f"dom:{similarity:.2f}"
# 未切换：不更新 _last_dom_text，下次仍和上一个 stage 比（避免累积漂移）
return None
```

### 效果
| 配置 | stage 数（8 个原始） |
|---|---|
| 原始（0.7 + 漂移） | 8（全碎） |
| 仅改阈值（0.33 + 漂移） | 5 |
| **改阈值 + 不漂移** | **4** ✅ |

### 教训
- **阈值不能拍脑袋**——必须用真实数据算分布，找分界点
- **状态更新策略要谨慎**：「每次都更新基准」看似自然，但会让连续小变化累积成大变化。
  「只在切换时更新」（和锚点比，不和相邻比）是更稳健的策略

---

## 问题 6：蒸馏崩溃（atomizer 折叠 off-by-one）

### 现象
采集产物正确了，跑 `treeforge distill` 蒸馏时崩溃：
`ValueError: invalid literal for int() with base 10: ''`

### 定位过程
崩溃在 `atomizer._render_summary` 的「折叠连续重复行」逻辑。用采集数据复现：
连续 3 个相同的 input 事件（标题输入被切成多段）触发崩溃。

### 根因
```python
tail = folded[-1][len(base):]  # tail = " x2"
n = int(tail[3:]) + 1          # tail[3:] = ""（" x2" 只有 3 字符，[3:] 越界）
```
off-by-one：`tail[3:]` 想取 `" x2"` 的数字 `2`，但 `" x2"` 长度 3，`[3:]` 是空。

第三次重复时：前两次已折叠成 `" x2"`，第三次进 if 分支，`tail[3:]` 越界 → `int('')` 崩溃。

### 修复
`tail[3:]` → `tail[2:]`（跳过 `" x"` 两字符取数字）+ 回归测试（3 个相同行折叠成 x3）。

### 教训
- **字符串切片要验证边界**：`" x2"`[3:] 是空不是 `"2"`，off-by-one 在单元测试里不容易发现
  （之前测试只测了 2 个重复，没测 3 个）
- **单元测试要覆盖「重复次数」边界**：折叠逻辑的 x2→x3→x4 转换是状态机，每个状态转换都要测

---

## 复盘总结

### 调试方法论
1. **先用 curl 隔离后端**：curl 测端点能快速确认后端逻辑是否正常，把问题缩定到扩展端
2. **console 日志是 MV3 调试的生命线**：background/content/popup 三处 console 都要看，
   分别确认各自状态（background 连通、content 装配、popup 显示）
3. **数据驱动调参**：阈值不能用经验值，必须用真实数据算分布（analyze_stage_threshold.py）
4. **对比同类项目**：TreeWalker 能跑不代表机制健壮，要问「为什么它能跑」（使用场景恰好避开缺陷）

### 架构反思
- **「延迟连接」引入的复杂性**：treeforge 把 CdpSession 延迟到用户点开始后才连，
  导致 popup 已是 target。TreeWalker 启动即连避开了此问题。延迟连接省了点资源，但引入了
  target 选择的不确定性——权衡上不值
- **跨进程通信的 url 来源**：content script 报的 url（用户实际操作页面）和 CdpSession 取的
  url（连的 target）可能不一致。这是扩展+CDP 混合架构的固有复杂性，必须明确以谁为准
- **MV3 的时序问题**：content script 加载时机晚于 background 广播是常态，必须有主动询问机制

### 单元测试的局限
140+ 单元测试全过，但真机仍跑不通——因为单元测试覆盖不到：
- MV3 扩展的注入/装配时序
- CDP target 选择的实际行为
- Windows 信号处理的平台差异
- 真实 DOM 的相似度分布

**结论**：单元测试保证「逻辑正确」，但集成正确性必须靠真机端到端验证。
P2 的经验是：每个模块单元测试过后，尽快做真机联调，不要等所有模块都写完才集成。

### 最终成果
6 个 bug 修复后，采集层端到端跑通：
- 用户操作 → 扩展采集事件 → 后端采 DOM 快照 → stage 判定 → 导出 trace → 蒸馏出 skill
- 真机录制 bilibili 投稿（36 事件，4 stage）成功蒸馏出三件套 skill

### 产物质量（修复后）
| 维度 | 表现 |
|---|---|
| host | ✅ member.bilibili.com |
| 事件覆盖 | ✅ 36 个，完整投稿流程 |
| stage | ✅ 4 个（article/home/frame/frame_1），封面编辑正确分离 |
| page_context | ✅ 真实 bilibili DOM（331 行/7KB） |
| 蒸馏链路 | ✅ trace → 三件套 skill |
