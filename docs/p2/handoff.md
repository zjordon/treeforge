# P2 工作交接文档

> 本文档用于上下文压缩后的工作交接。记录 P2 当前进展、已完成工作、P2 后续待办，
> 以及关键的代码位置和调试经验。压缩上下文后从本文档继续。

## 一、P2 整体目标与定位

**P2 目标**：MV3 扩展录制真实浏览器操作 → 产出可蒸馏的 trace（含 element_attrs + page_context）。

**核心成果（已达成）**：自动采集 + LLM 蒸馏产出与「人工采集 + LLM 蒸馏」同等质量的 skill。
这是继 P0.5/P1（人工采集验证）之后，把采集自动化的关键一步。

**P2 vs P0.5 的替代关系**：
- 手写 trace → 扩展自动采事件（含 element_attrs）
- 手动导出 DOM 快照 → 后端自动经 CDP 采（dom-snapshot 公共库）
- 手动 stage 标注 → StageTracker 自动判定（采集时绑定，无 `?`）

## 二、当前进展（截至提交 57a4c10）

### P2 各阶段完成状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| **P2.1 dom-snapshot 公共库** | ✅ 完成 | 独立仓库 `D:/dev/git/z_jordon/dom-snapshot`，0.1.0 已发版，TreeWalker + treeforge 均已接入 |
| **P2.2 采集后端（Python）** | ✅ 完成 | treeforge/capture/ 全模块，144 测试全过 |
| **P2.3 扩展（Chrome MV3）** | ✅ 最小可用 | P2.3.1 完成（click/input/keydown/scroll），P2.3.2/3 待补 |
| **P2.2.4 阈值调参** | ✅ 完成 | 0.7→0.33，数据驱动 |
| **P2.4 ADAPT 简化** | ⏳ 待做 | 删除 _infer_stages（低优先级） |
| **端到端验证** | ✅ 通过 | 真机录 bilibili 投稿 → LLM 蒸馏出高质量 skill |

### 关键里程碑

**采集层端到端跑通**：用户操作 → 扩展采集事件 → 后端经 CDP 采 DOM 快照 → StageTracker 判 stage →
导出 trace.json + snapshots/ → `treeforge distill` 蒸馏出三件套 skill。

真机实测：录制 bilibili 投稿（36 事件 / 4 stage），LLM 蒸馏产出连贯步骤剧本 + 4 条真坑 quirks，
质量接近 TreeWalker 手写参考（`D:/dev/git/z_jordon/TreeWalker/domain-skills/member.bilibili.com/`）。

## 三、已完成工作详解

### 3.1 dom-snapshot 公共库（P2.1）

- **位置**：`D:/dev/git/z_jordon/dom-snapshot`（独立 git 仓库）
- **接入**：treeforge 和 TreeWalker 的 pyproject.toml 都加了 `dom-snapshot>=0.1.0`（本地 editable）
- **作用**：从 CDP 采集网页 DOM，经三源采集 + 五步过滤，产出 `[index]<tag attr=val /> text` 格式文本树
- **方案文档**：`docs/p2/README.md` 3.1 节

### 3.2 采集后端（P2.2，treeforge/capture/）

| 模块 | 职责 |
|---|---|
| `cdp_session.py` | 轻量 CDP 包装（连浏览器 + get_state 委托 dom-snapshot）；start 时优先选 http/https target，跳过 chrome-extension://（避免连 popup） |
| `ws_discover.py` | 发现 Chrome ws_url（GET /json/version，stdlib urllib） |
| `backend.py` | aiohttp HTTP 后端（/start /ingest /stop /health）；on_stop 回调让 cli 退出 |
| `distill_schema.py` | collector + 扩展共同契约（raw_attrs → element_attrs 提炼；字体图标清洗） |
| `collector.py` | 采集器主类（收事件 → get_state → 判 stage → 累积）；session 可循环（start 重建 StageTracker + stop 清空）；url 优先用 envelope 外层 |
| `stage.py` | 阶段切换判定（URL/DOM 相似度 0.33/导航三信号）+ 自动命名；不累积漂移（只在切换时更新基准） |
| `export.py` | 导出 trace.json + snapshots/（可被 treeforge distill 直接消费） |
| `cli.py` | capture 命令运行逻辑（手动 event loop + signal.signal 处理 Ctrl+C） |

### 3.3 Chrome 扩展（P2.3.1，extension/src/）

WXT + TypeScript MV3 扩展，自研面向蒸馏采集（不复用 TreeWalker 扩展，因其为重放深度定制）。

| 模块 | 职责 |
|---|---|
| `shared/envelope.ts` | 通用 CaptureEnvelope（scenario + payload） |
| `shared/distill-schema.ts` | 双端契约镜像 Python（RAW_ATTR_KEYS / extractRawAttrs / cleanVisibleText） |
| `shared/backend.ts` | HTTP 传输（postStart/postIngest/postStop） |
| `shared/types.ts` | MV3 内部消息类型（含 recording-active-query） |
| `core/strategy.ts` | CollectionStrategy 可插拔接口（distill/replay 各一套） |
| `core/recorder-engine.ts` | 通用采集骨架（click/input/keydown/scroll 监听 + 去抖，剥离重放定制） |
| `strategies/distill/index.ts` | 蒸馏策略（click 不跳过误操作 + raw_attrs + 找祖先前三道） |
| `entrypoints/background.ts` | MV3 service worker（状态管理 + 与后端通信 + 广播 + 处理 recording-active-query） |
| `entrypoints/content.ts` | content script（只匹配 http/https，装配/卸载 recorder）；emit 时 url 用 location.href |
| `entrypoints/popup/` | 控制 UI（开始/停止 + 状态显示，纯 HTML/JS） |

**构建**：`cd extension && npm run build` → 产出 `.output/chrome-mv3/`（约 38KB）

### 3.4 CLI 入口

`treeforge capture` 子命令（参数：--task / --host / --output / --cdp-port / --backend-port / --stage-threshold）。

### 3.5 端到端调试（6 个 bug 修复）

详见 `docs/p2/debug-retrospective.md`（完整复盘）。6 个 bug：
1. Windows Ctrl+C 无产物（asyncio.run 信号处理）→ 改手动 loop + signal.signal
2. 导出时机错误（停止不导出等 Ctrl+C）→ collector.stop() 负责导出
3. 事件数 0（content 没装配）→ 处理 recording-active-query
4. 产物全是 popup（CdpSession 连错 target + url 取错）★最关键 → 优先 http target + envelope url 优先
5. stage 切碎（阈值 + 累积漂移）→ 0.33 + 不漂移
6. 蒸馏崩溃（atomizer 折叠 off-by-one）→ tail[2:]

## 四、P2 后续待办

### 高优先级

无（P2 核心目标已达成）。

### 中优先级

#### P2.3.2 扩展补全
- IME（compositionstart/end）完善（当前只处理了 compositionend flush）
- scroll 去抖优化
- contenteditable MutationObserver（富文本编辑器，如 bilibili 简介）
- extractor 注册表（字段提取从单体改为可配置，为 replay 策略铺路）
- 位置：`extension/src/core/recorder-engine.ts` + `extension/src/strategies/distill/`

#### P2.4 ADAPT 层简化
- 删除 `tools/rerun_to_trace.py` 的 `_infer_stages`（stage 已采集时绑定，无需事后推断）
- 删除 `dom_dir` 参数（P2 trace 自带 page_context，无需外部注入）
- rerun_to_trace 退化为纯格式转换器
- 位置：`tools/rerun_to_trace.py`

### 低优先级（已知限制，非阻塞）

#### CdpSession 跟随 tab
- 当前：start 时选第一个 http target，之后固定。多 tab 场景可能选错。
- 彻底解决：content script 报告 tab id，CdpSession 精确 attach 用户操作的 tab。
- 位置：`treeforge/capture/cdp_session.py` + `extension/src/entrypoints/content.ts`

#### stage 命名语义化
- 当前：URL path 段命名（如 frame / frame_1），不够语义化。
- 优化：用 DOM 特征命名（如检测到 canvas + accept=image/png → upload-conver）。
- 位置：`treeforge/capture/stage.py` 的 name_stage

#### 标题输入切成多段 input
- 现象：连续输入标题被切成 5 个 input 事件（去抖没合并好）。
- 影响：不影响蒸馏（都有 placeholder 标识同一框），后续可优化。
- 位置：`extension/src/core/recorder-engine.ts` 的 input 去抖逻辑

## 五、P3（常驻服务）预告

P3 的方案已写在 `docs/p2/serve-plan.md`（标记为 P3 范围，暂不实施）：
- 把一次性 capture 命令改成 FastAPI 常驻服务（`treeforge serve`）
- 控制面板（配模型参数 + 触发蒸馏 + 查看产物）
- distill 提炼为可被 HTTP 触发的后台任务
- session 可循环（已提前修了部分，见 collector 改造）

P3 的三个已确认决策：① 新增 serve，保留 capture ② 换 FastAPI ③ P3 实施（不提前到 P2）。

## 六、关键代码位置速查

### treeforge 采集层
```
treeforge/capture/
├── cdp_session.py      # CDP 包装（target 选择在 start()）
├── ws_discover.py      # Chrome ws_url 发现
├── backend.py          # aiohttp 4 端点
├── distill_schema.py   # 双端契约（raw_attrs → element_attrs）
├── collector.py        # 采集器（url 优先级在 ingest()，host 提取在 _extract_real_host）
├── stage.py            # stage 判定（阈值 0.33，不漂移逻辑在 detect_change）
├── export.py           # 导出 trace + snapshots
└── cli.py              # capture 命令（signal 处理在 _run_capture）
```

### 扩展
```
extension/src/
├── shared/             # 契约 + 传输 + 类型
├── core/               # 采集骨架 + 策略接口
├── strategies/distill/ # 蒸馏策略
└── entrypoints/        # background / content / popup
```

### 工具
```
tools/
├── rerun_to_trace.py       # rerun-history → trace（待 P2.4 简化）
└── analyze_stage_threshold.py  # stage 阈值分析（调试用）
```

### 文档
```
docs/p2/
├── README.md                # P2 完整设计方案（采集层架构）
├── serve-plan.md            # P3 常驻服务方案（暂不实施）
└── debug-retrospective.md   # 端到端调试复盘（6 bug）
```

## 七、端到端使用流程（备忘）

```bash
# 1. 构建扩展
cd treeforge/extension && npm run build

# 2. Chrome 以远程调试端口启动
chrome --remote-debugging-port=9222 --user-data-dir=<profile>
# chrome://extensions → 加载 extension/.output/chrome-mv3/

# 3. 启动采集后端
cd treeforge
uv run treeforge capture --task "在 B 站投稿上传一个视频" --output ./data/captures

# 4. 扩展 popup 点「开始录制」→ 操作 bilibili → 点「停止录制」
#    （建议录制前只开一个目标 tab，避免 CdpSession 选错 target）

# 5. 蒸馏（LLM 模式，高质量）
uv run treeforge distill ./data/captures/<session>/trace.json --output ./data/skills

# 6. 查看产物
cat data/skills/domain-skills/<host>/_sop.md
```

## 八、关键约束（来自 AGENTS.md，必须遵守）

- 4 空格缩进（不用 tab），行宽 100，ruff format/check
- 用 uv（不用 pip），运行用 `uv run`
- 测试用 mock，不连真 LLM / 不发真网络请求
- 不主动 git commit/push（除非用户明确要求）
- 当前分支 `feat/skill-format`（不在 master 上做大改动）
- LLM 走 urllib（不引入 anthropic/openai SDK）
