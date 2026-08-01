# P2 低优先级两项实施方案（stage 命名语义化 + CdpSession 跟随 tab）

> 本文档是 P2 剩余两项低优先级待办的实施方案，待用户审阅批准后执行。
> 起因：P2.3.2 + P2.4 完成后，handoff 列的低优先级项（标题输入去抖已在 P2.3.2 atomizer
> 输入合并里解决），剩下 stage 命名语义化 + CdpSession 跟随 tab 两项。用户选择「两项都做」。
> 基于代码 + 真机数据（`data/captures/72111447` / `0ddbaa84`）调研确认了关键事实。

## 调研确认的关键事实

1. **background.ts:121** 的 capture-event 分支用 `_sender`（下划线=未用），但 `sender.tab?.id` 可用——
   同文件 line 76 的 recording-active-query 分支已用 `_sender.tab?.id`。这是 tab_id 的唯一注入点。
2. **collector.py:273** 的 `name_stage(url, raw)` 调用时，`dom_text` 已在作用域内（line 270 传给
   detect_change）。传入 name_stage 只需改一行签名。
3. **CDP tabId 关联**：`Target.getTargets` 返回的 `TargetInfo` 含实验性 `tabId` 字段（数字），
   与 `chrome.tabs` API 的 tab id 同空间。`cdp_use` 的 TypedDict 未声明但运行时 `t.get("tabId")`
   可读。近年 Chrome 稳定支持。
4. **bilibili 真机 stage 区分特征**（来自快照分析）：
   - `accept=image/png|jpeg` → 封面上传阶段（仅封面 modal 打开时出现）
   - `<canvas` + `editor_4_3`/`editor_16_9` → 封面裁剪编辑器（仅裁剪阶段）
   - `accept=.mp4` → 视频上传表单（但所有 frame* 阶段都有，不能单独区分子阶段）
5. **现有测试影响**：test_collector.py 有 10 个 stage 名字面量断言，但都用合成 DOM（如
   `"[1]<a />投稿"`）不含上述特征 → 语义检测不命中 → 退化原逻辑 → 断言保持通过。

---

## 一、stage 命名语义化（先做，低风险纯后端）

### 思路：URL 命名 + DOM 特征增强（向后兼容）

不改现有 URL 命名逻辑，**在它之前加一道 DOM 特征检测**：命中特征用语义名，未命中退化原逻辑。
这样既能改善 bilibili 这类有明确 DOM 特征的站点，又不破坏无特征站点的现有行为。

### 改动 1a：`treeforge/capture/stage.py`

`name_stage` 签名加 `dom_text` 参数：`name_stage(self, url, raw_stage, dom_text="")`。

新增语义特征检测（基于真机数据验证的特征）：
```python
_STAGE_FEATURES: tuple[tuple[re.Pattern, str], ...] = (
    # (正则, 语义名) —— 按特异性排序，首个命中即用
    (re.compile(r"<canvas[^>]*>"), "edit-cover"),              # 封面裁剪编辑器
    (re.compile(r'accept=["\']?image/(?:png|jpeg)'), "upload-cover"),  # 封面上传
    (re.compile(r'accept=["\']?\.?(?:mp4|flv|avi)'), "upload-video"),  # 视频上传表单
)
```

`name_stage` 新逻辑（前置一道）：
1. 先跑 `_detect_semantic(dom_text)`，命中返回语义名
2. 语义名与 current_stage 冲突时仍用 `_N` 去重（保留现有防覆盖逻辑）
3. 未命中 → 走原 URL 命名逻辑

同步改 `force_new_stage` 也接受 `dom_text`（首阶段也语义化）。

### 改动 1b：`treeforge/capture/collector.py`

`_determine_stage` 调用点（line 273）：`name_stage(url, raw, dom_text)`。
`start()` 里首阶段调用（若有）：同样传 dom_text。

### 改动 1c：测试 `tests/test_collector.py`

现有 10 个断言用合成 DOM，不含上述特征 → 语义检测不命中 → 退化原逻辑 → **现有断言全部保持
通过**（向后兼容的收益）。新增测试：
- `test_name_stage_semantic_cover_upload`：dom_text 含 `accept=image/png` → 命名 `upload-cover`
- `test_name_stage_semantic_edit_cover`：dom_text 含 `<canvas>` → 命名 `edit-cover`
- `test_name_stage_semantic_falls_back_when_no_feature`：dom_text 无特征 → 退化 URL 命名
- `test_name_stage_semantic_dedup_suffix`：两次命中同特征 → 第二次 `upload-cover_1`

---

## 二、CdpSession 跟随 tab（方案 B：显式 tabId 关联）

### 流程
扩展 content emit → background 用 `sender.tab.id` 打 `tab_id` 进 envelope → 后端 ingest 收到 →
collector 检测 tab_id 变化 → CdpSession.attach_tab(tab_id) 重 attach → get_state 采正确 tab 的 DOM。

### 改动 2a：扩展端 envelope 加 tab_id

**`extension/src/shared/envelope.ts`**：`CaptureEnvelope` 加 `tab_id?: number`。

**`extension/src/entrypoints/background.ts`**（line 121-138 capture-event 分支）：
```ts
const envelope = msg.envelope as CaptureEnvelope;
if (!envelope.session_id) envelope.session_id = state.sessionId || "";
if (sender.tab?.id) envelope.tab_id = sender.tab.id;  // 新增
```
（`_sender` → `sender`，去掉下划线因为现在要用了）

### 改动 2b：后端 CdpSession 拆分 start/attach

**`treeforge/capture/cdp_session.py`**：
- `start()`：只连 browser-level ws（保留 client 建立），**删除 target 选择块**（line 72-98）
  移到新方法。但保留一个 eager attach 作 fallback（无 tab_id 时仍能工作）。
- 新增 `async def attach_tab(self, tab_id: int) -> bool`：
  - `Target.getTargets`，找 `t.get("tabId") == tab_id` 且 `type=="page"` 的 target
  - 若已是当前 target（`current_target_id` 匹配）→ 跳过（幂等）
  - `Target.attachToTarget` + enable 域 + setAutoAttach（移自 start）
  - 返回 True/False（找不到 target 时 False）
- `get_state()` 签名不变（用 `current_session_id`），但 collector 调用前确保已 attach

**关键**：`attach_tab` 幂等 + 缓存，避免每次事件都重 attach。

### 改动 2c：后端 collector 按 tab 切换重 attach

**`treeforge/capture/collector.py`**：
- `__init__` 加 `self._attached_tab: int | None = None`
- `start()`（line 142-143）：改为 `if not self.cdp.client: await self.cdp.start()`（只连 browser），
  保留 start 内部的 eager attach fallback（首个 http target），让无 tab_id 的老流程仍工作。
- `ingest()`（line 196 get_state 前）：
  ```python
  tab_id = envelope.get("tab_id")
  if tab_id and tab_id != self._attached_tab:
      if await self.cdp.attach_tab(tab_id):
          self._attached_tab = tab_id
  ```

### 改动 2d：测试 mock 重构

**`tests/test_collector.py`** `_make_mock_cdp`（line 138-152）：
- 加 `cdp.attach_tab = AsyncMock(return_value=True)`
- 加 `cdp.client = MagicMock()`（collector 判 `if not self.cdp.client` 用）
- 新增 `test_collector_attaches_tab_from_envelope`：envelope 带 tab_id=5 → 验证
  `cdp.attach_tab` 被调用(5)
- 新增 `test_collector_skips_reattach_same_tab`：同 tab_id 两次 → attach_tab 只调一次
- 新增 `test_collector_reattaches_on_tab_switch`：tab_id 5→7 → attach_tab 调两次

**`tests/test_capture.py`**（若有 CdpSession 直测）：加 attach_tab 单元测试（mock
client.send.Target.getTargets 返回带 tabId 的 target）。

### 改动 2e：文档

`docs/p2/handoff.md` 低优先级区把两项标记完成。
`docs/p2/debug-retrospective.md` 的「遗留限制」段（line 158-160）更新为已解决。

---

## 三、验证

1. `uv run python -m pytest tests/ -x -v` 全过
2. `uv run ruff format . && uv run ruff check .` 无告警
3. `cd extension && npm run build` 成功（TS strict + envelope.tab_id 类型）
4. 不提交

## 涉及文件
| 文件 | 改动 |
|---|---|
| `treeforge/capture/stage.py` | name_stage/force_new_stage 加 dom_text + 语义特征检测 |
| `treeforge/capture/collector.py` | _determine_stage 传 dom_text；ingest 按 tab_id 重 attach；__init__ 加 _attached_tab |
| `treeforge/capture/cdp_session.py` | 拆 start/attach_tab；start 只连 browser，attach_tab 按 tabId 选 target |
| `extension/src/shared/envelope.ts` | CaptureEnvelope 加 tab_id |
| `extension/src/entrypoints/background.ts` | capture-event 分支注入 sender.tab.id |
| `tests/test_collector.py` | mock 加 attach_tab；+4 stage 语义测试 +3 tab 跟随测试 |
| `docs/p2/handoff.md` + `debug-retrospective.md` | 标记两项完成 |

## 风险与回退
- **tabId 实验性字段**：CDP `Target.getTargets` 的 `tabId` 是实验性字段但近年 Chrome 稳定支持。
  用 `t.get("tabId")` 运行时读取（TypedDict 未声明也能读）。找不到时 fallback 到原「首个 http
  target」逻辑。
- **向后兼容**：无 tab_id 的老 envelope（如 --no-llm 模板、rerun_to_trace 产出）仍能工作
  （start 保留 eager attach fallback）。
- **测试不破坏**：stage 语义化是前置增强，现有合成 DOM 测试不含特征 → 退化原逻辑 → 断言不变。
