# P1 整体验收标准

> P1 完成后必须满足的验收点。每个子任务的细节验收见各自文档，这里是**端到端整合验收**。

## 一、端到端命令验收

### 验收 1：CLI 仍能跑（向后兼容 P0）

P1 所有改动**不能破坏 P0 的 CLI 验收命令**。

```bash
# P0 验收命令仍要能跑
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills --no-llm
ls ./data/skills/domain-skills/bilibili.com/
# → _sop.md / selectors.md / quirks.md / api.md（至少一个非空）
```

✅ 通过条件：4 个文件出现，内容非空。

### 验收 2：CLI 真调 LLM（P0 + P1 redact）

```bash
cp .env.example .env
# 编辑 .env：LLM_KEY=你的key，LLM_BASE=你的端点
uv run treeforge distill examples/bilibili-upload.trace.json --output ./data/skills
ls ./data/skills/domain-skills/bilibili.com/
```

✅ 通过条件：4 个文件出现，且 `selectors.md` 有语义化提炼（不是模板的机械列举）。

### 验收 3：跨会话累积（P1 持久化核心价值）

```bash
# 第一次跑 bilibili 上传 trace
uv run treeforge distill examples/bilibili-upload.trace.json --no-llm
# 查 buckets.json，记下 upload-content 桶的 segment_ids
cat data/harness/buckets.json | grep segment_ids

# 第二次跑同 trace（或同 capacity 的不同 trace）
uv run treeforge distill examples/bilibili-upload.trace.json --no-llm
# 再查 buckets.json
cat data/harness/buckets.json | grep segment_ids
```

✅ 通过条件：
- `data/harness/buckets.json` 存在
- 第二次跑后，`upload-content` 桶的 `segment_ids` 数量 ≥ 第一次（累积而非覆盖）
- `distill_version` ≥ 1

### 验收 4：server 启动 + 端到端 HTTP

```bash
# 启动 server
uv run treeforge-server &
SERVER_PID=$!
sleep 2

# 端到端：init → put chunk → finalize → poll status
UPLOAD_ID=$(curl -s -X POST http://127.0.0.1:8099/v1/traces/init \
    -H "Authorization: Bearer treeforge-local-dev-key" \
    -H "Content-Type: application/json" \
    -d '{"trace_id":"acceptance-test-1"}' | python -c "import sys,json; print(json.load(sys.stdin)['upload_id'])")
echo "upload_id=$UPLOAD_ID"

# 构造一个简单 trace 的 gzip NDJSON chunk
python -c "
import gzip, hashlib, json
events = [{'type':'navigate','url':'https://x.com/','timestamp':0},
          {'type':'click','selector':'#btn','url':'https://x.com/','timestamp':1000}]
body = gzip.compress(b''.join(json.dumps(e).encode()+b'\n' for e in events))
sha = hashlib.sha256(body).hexdigest()
open('/tmp/chunk.bin','wb').write(body)
open('/tmp/chunk.sha','w').write(sha)
"

curl -X PUT "http://127.0.0.1:8099/v1/traces/$UPLOAD_ID/chunks/0" \
    -H "Authorization: Bearer treeforge-local-dev-key" \
    -H "X-Trace-Chunk-Kind: events" \
    -H "X-Trace-Chunk-Sha256: $(cat /tmp/chunk.sha)" \
    --data-binary @/tmp/chunk.bin

# finalize
curl -X POST "http://127.0.0.1:8099/v1/traces/$UPLOAD_ID/finalize" \
    -H "Authorization: Bearer treeforge-local-dev-key"

# 轮询 status（最多 60 秒）
for i in $(seq 1 30); do
    STATUS=$(curl -s "http://127.0.0.1:8099/v1/traces/$UPLOAD_ID/status" \
        -H "Authorization: Bearer treeforge-local-dev-key")
    echo "poll $i: $STATUS"
    DISTILL=$(echo "$STATUS" | python -c "import sys,json; print(json.load(sys.stdin).get('distill_status',''))" 2>/dev/null)
    if [ "$DISTILL" = "done" ] || [ "$DISTILL" = "error" ]; then break; fi
    sleep 2
done

kill $SERVER_PID
```

✅ 通过条件：
- init 返回 `upl_` 开头的 upload_id
- put chunk 返回 200
- finalize 返回 `{"status": "accepted"}`
- status 最终达到 `distill_status: "done"` 或 `"error"`（有明确终态，不卡在 processing）

### 验收 5：consolidate CLI 子命令

```bash
# 先构造多个同义桶（可能需要手写多个相似 trace 跑 distill）
# 然后跑 consolidate
uv run treeforge consolidate --threshold 2  # 降低阈值便于测试
```

✅ 通过条件：
- 命令不报错
- 输出「合并 N 组」或「无需合并」
- 合并后 `data/harness/buckets.json` 的桶数减少

## 二、自动化测试验收

### 验收 6：全量测试通过

```bash
uv run python -m pytest tests/ -x -v
```

✅ 通过条件：所有测试通过（P0 测试 + P1 新增测试）。

**P1 新增测试文件：**

| 文件 | 子任务 | 重点 |
|---|---|---|
| `tests/test_event_utils.py` | A redact | 5 个正则 + 字段名归一化 |
| `tests/test_persistence.py` | B 持久化 | 原子写 / append / upsert |
| `tests/test_distill_incremental.py` | C 增量蒸馏 | 旧 skill 加载 + 8000 截断 |
| `tests/test_consolidate.py` | C consolidate | apply_merges 去重 + mock LLM |
| `tests/test_server.py` | D 接入层 | 4 端点 + auth + 幂等 |
| `tests/test_windows_compat.py` | E Windows | _ResilientStream / _file_lock / _atomic_write |

### 验收 7：ruff 干净

```bash
uv run ruff check .
uv run ruff format --check .
```

✅ 通过条件：无 lint 错误。

### 验收 8：测试覆盖关键路径

至少以下路径要有测试覆盖：

- [x] redact 5 类敏感数据各一个测试
- [x] redact 字段名归一化（`CVC Code` / `cvc_code` 都命中）
- [x] buckets.json 原子写 + 重复写不报错
- [x] segments.jsonl append（两次 append 后有两条）
- [x] registry upsert（同 capacity_id 两次只 1 个 entry）
- [x] 增量蒸馏加载旧 skill（mock LLM 验证 prompt 含旧内容）
- [x] 增量蒸馏 8000 字符截断
- [x] 首次蒸馏不进入增量分支
- [x] apply_merges 合并 segment_ids 去重
- [x] consolidate_domain 少于 2 桶返回空
- [x] server init 幂等（同 trace_id 同 upload_id）
- [x] server put chunk sha256 不符 409
- [x] server put chunk 幂等（dedup）
- [x] server 无 auth 401
- [x] _ResilientStream 吞 BrokenPipe
- [x] _atomic_write 覆盖写

## 三、Windows 特定验收

### 验收 9：Windows 上 server 启动

```powershell
# Windows PowerShell
uv run treeforge-server
```

✅ 通过条件：
- 不报 `ModuleNotFoundError: No module named 'fcntl'`
- 监听 8099 端口
- Ctrl+C 能干净退出

### 验收 10：Windows 上持久化原子写

```powershell
# Windows 上重复跑 distill
uv run treeforge distill examples/bilibili-upload.trace.json --no-llm
uv run treeforge distill examples/bilibili-upload.trace.json --no-llm
```

✅ 通过条件：第二次不报 `WinError 183`（文件已存在），`buckets.json` 正确更新。

### 验收 11：Windows 上完整端到端

在 Windows 上跑验收 4 的 server 端到端流程。

✅ 通过条件：同 Linux，status 达到终态。

## 四、文档验收

### 验收 12：文档更新

P1 完成后，以下文档要同步更新：

- [ ] `README.md`：快速开始加 server 启动方式
- [ ] `.env.example`：加 PORT / API_KEY / TRACKS_DIR / STATE_DIR / SKILLS_ROOT
- [ ] `ROADMAP.md`：P1 各项打勾，更新 P2 起点
- [ ] `ARCHITECTURE.md`：四层架构图标注 P1 已落地「接入层 + 持久化」
- [ ] `docs/p1/README.md`：状态从「实施计划」改为「已完成」（可选）
- [ ] 新增 `docs/p1/` 的实施记录（可选，记录实际遇到的坑）

## 五、范围边界确认

### 验收 13：P1 没做范围外的事

P1 **不应该**引入以下内容（如果引入了说明范围蔓延）：

- [ ] MV3 扩展真实录制（应是 P2）
- [ ] server 静态面板（不在 P1 范围）
- [ ] MCP stdio 检索（应是 P4）
- [ ] 质量验证层（应是 P3）
- [ ] consolidate 自动触发（P1 只做 CLI 手动）
- [ ] anthropic / openai SDK（永远不引入）

## 六、完成 P1 后的状态

P1 完成后 TreeForge 的能力：

```
✅ CLI 端到端（P0 + P1 增强）
✅ 跨会话累积（持久化层）
✅ 真增量蒸馏（旧 skill 加载）
✅ 桶合并（CLI 手动）
✅ HTTP 接入层（扩展可联）
✅ 完整 redact（防泄露）
✅ Windows 全适配

⏳ MV3 扩展录制（P2）
⏳ 质量验证（P3）
⏳ MCP 检索（P4，可选）
```

P1 完成后，**只差扩展录制（P2）就能让「人走一遍浏览器 → 自动出 skill」真正自动化**。
P2 把「人手写 trace JSON」替换成「扩展录真人操作」。

## 七、下一步

- P1 实施过程中遇到的设计变更 → 更新到对应子任务文档
- P1 完成后 → 切 P2 分支，参照 [ROADMAP.md](../../ROADMAP.md) 的 P2 段开始
- P2 的规划文档可放到 `docs/p2/`，参考本目录的结构（README + overview + 各子任务 + acceptance）
