# TreeForge 路线图

> 从 [init-plan.md](./init-plan.md) 第二节抽取，按优先级排列。

## P0 —— 最小闭环（本期已完成 ✅）

**目标**：手写 trace JSON → 蒸馏 → 输出 skill 文件，最小可跑链路。

- [x] Python 项目脚手架（uv + ruff + pytest + Pydantic v2）
- [x] harness 五阶段管线骨架 + 实现
  - [x] ADAPT（adapter.py）：最小格式 + 多格式兼容 + 脱敏
  - [x] ATOMIZE（atomizer.py）：4 边界规则 + 去噪 + 合并/拆分
  - [x] CLASSIFY（classifier.py）：串行增量命名 + 启发式 fallback
  - [x] BUCKET（bucketer.py）：domain::capacity 归并
  - [x] DISTILL（distiller.py）：★站点特定四字段 prompt + 模板 fallback
- [x] LLM 客户端（llm.py）：urllib 双协议探测（Anthropic / OpenAI）
- [x] 输出 adapter（treewalker + browserbc）
- [x] CLI（`treeforge distill` / `treeforge info`）
- [x] 示例 trace（bilibili-upload + github-login）
- [x] 测试（atomizer / classifier / distiller / adapters / llm）
- [x] 文档（README / ARCHITECTURE / ROADMAP）
- [x] WXT 扩展脚手架（仅结构）

**验收命令**（init-plan §六）：

```bash
uv sync --extra dev
echo "LLM_KEY=sk-xxx" > .env
echo "LLM_BASE=https://api.example.com" >> .env
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
ls ./data/skills/domain-skills/bilibili.com/
# → _sop.md / selectors.md / quirks.md / api.md
```

模板模式（不调 LLM 也能跑）：

```bash
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm
```

---

## P1 —— 接入层 + 真链路

**目标**：扩展 → server → 蒸馏全自动，人录一遍就出 skill。

- [ ] FastAPI 单文件 server（`server/server.py`）
  - [ ] 分块上传协议（init/finalize/status 四端点）
  - [ ] 可恢复上传（sha256 校验 + 幂等 upload_id）
  - [ ] 异步蒸馏（全局 `_PIPELINE_LOCK`）
  - [ ] 进度轮询（内存 dict + harness.progress 注入）
- [ ] 接入层 Windows 适配（msvcrt 文件锁 / `_ResilientStream` / 双写日志）
- [ ] 完整 redact（CVV / OTP / account token 正则，对齐 Browser-BC）
- [ ] distiller 增量蒸馏真接通（registry 持久化旧 SkillCard，8000 字符截断塞 prompt）
- [ ] 桶合并 consolidate（同义 capacity 合并 CLI 子命令）

---

## P2 —— 采集层（最重）

**目标**：MV3 扩展录制真实浏览器操作。

- [ ] WXT 扩展真实实现（基于本期脚手架）
  - [ ] background SW + recorder 状态机（MV3 SW 30s 回收恢复）
  - [ ] content script：14 种 DOM 事件采集
  - [ ] injected：fetch/XHR/WebSocket/history.pushState monkey-patch
  - [ ] popup：录制控制 UI
- [ ] DOM 快照（≤300 元素 + sha256 去噪 + 10s 节流）
- [ ] 表单摘要（4 阶段：opened/edited/submitted/reset）
- [ ] selector 多级 fallback（`data-testid` → `aria-label` → `name` → `[role]` → xpath）
- [ ] Dexie (IndexedDB) 存储（5 表，SW 回收不丢事件）
- [ ] 脱敏模型 `RedactedValue`（digest 可比不可逆）
- [ ] 分块上传到 server（gzip NDJSON + media）

---

## P3 —— 质量验证层

**目标**：蒸馏产物质量把关（init-plan §二列为「需单独设计」）。

- [ ] distiller 输出 schema 校验（四字段非空 + selector 可解析）
- [ ] 蒸馏评分（LLM 自评：selectors 是否覆盖 evidence、quirks 是否有据）
- [ ] 回放验证（用产出的 skill 让 TreeWalker 跑一遍，看是否成功）
- [ ] 人工反馈通道（thumbs up/down → registry 标记）

---

## P4 —— 检索层（可选）

**目标**：MCP stdio 检索（init-plan §二列为「TreeWalker 用文件注入不需要」）。

- [ ] registry.json 持久化
- [ ] `query_top_k`：LLM-as-ranker 语义召回（无 embedding）
- [ ] `synthesize_playbook`：LLM 编排多 skill playbook
- [ ] MCP stdio server（`treeforge mcp-skill` 子命令）
- [ ] 两层召回路由（单强匹配直返 / 多匹配 playbook / degrade 链）

---

## 不做（明确排除）

- **向量化检索** —— Browser-BC 哲学：LLM-as-ranker, no embeddings
- **DB 持久化** —— 文件系统是唯一持久层（checkpoint.json / registry.json / skills/）
- **SDK 依赖** —— anthropic / openai SDK 不引入，LLM 走 urllib
- **subprocess 管线** —— in-process import（避免 PyInstaller frozen-subprocess 坑）

---

## 里程碑速查

| 阶段 | 交付物 | 状态 |
|---|---|---|
| P0 | CLI 跑通 + 4 文件 skill 输出 | ✅ |
| P1 | FastAPI + 增量蒸馏 + 完整 redact | ⏳ |
| P2 | MV3 扩展录制真实操作 | ⏳ |
| P3 | 蒸馏质量验证 | ⏳ |
| P4 | MCP 检索（可选） | ⏳ |
