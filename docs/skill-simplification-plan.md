# Skill 精简重构方案

> 基于实测 A/B 测试发现：加载当前蒸馏产物对 TreeWalker agent 的成功率与稳定性均无提升。
> 经三组数据对比（当前蒸馏产物 vs 模型实际输入 DOM vs 手写参考 skill），诊断为产物形态偏离模型实际需求。
> 本方案对蒸馏产物形态作一次重构，目标是「让 skill 真正帮上模型，而不是制造噪声」。

## 一、问题诊断

### 数据来源

- **当前蒸馏产物**：`data/skills/domain-skills/bilibili.com/{_sop,selectors,quirks,api}.md`
- **模型实际输入 DOM**：`D:/temp/tree-walker-model-input/bili/{upload,publish,upload-conver}.txt`
- **手写参考 skill**：`D:/dev/git/z_jordon/TreeWalker/domain-skills/member.bilibili.com/`（在另一个工作空间由模型手工整理，质量更高）

### 三个核心问题

#### 问题 1：capacity 割裂

B 站投稿本质是**一个连续动作流**，但被 CLASSIFY 强行切成 `upload-video` + `fill-video-metadata` 两个 capacity。当前 `_sop.md` 顶部：

```
# Sop — bilibili.com (2 capacities)
## upload-video
...
## fill-video-metadata
```

后果：`selectors.md` / `quirks.md` 两份各自重复描述标题框、标签框、简介框（见 `data/skills/.../selectors.md`：两个 capacity 都列了「标题输入框」）。模型读了反而要自己判断「现在该用哪份」——这是典型的负向价值。

#### 问题 2：quirks 充斥模型已能从 DOM 自己读到的事实

当前 `quirks.md` 里：

- 「简介区域是 `<div contenteditable=true>`」——DOM 里 `[3788]<div contenteditable=true />` 写得清清楚楚
- 「立即投稿/存草稿是 `<span>` 不是 `<button>`」——DOM 里 `[3818]<span /> 存草稿 / [3819]<span /> 立即投稿` 摆在那
- 「创作声明是 input 不是 radio」——DOM 里 `[3685]<input type=text placeholder=请选择符合您视频内容的创作声明 />` 一目了然

这些不是「坑」，是模型自己能看见的事实。写进 quirks 等于把 DOM 抄一遍，浪费 attention，还会让模型怀疑「是不是有别的版本」。

**真正的坑**反而被埋没：例如封面上传要先点「封面设置」打开 modal，打开 modal 后才有 `accept=image/png` 的 input——这个**时序依赖**模型读 DOM 看不出来，但被埋在 quirks.md 第 21 行一段普通文字里，和上述噪声并列。

#### 问题 3：api.md 零信息

无网络采集时，`api.md` 永远是「未观察到私有 API」（见 `data/skills/.../api.md`）。浪费一个文件槽位，且 TreeWalker 消费侧硬上限 10 个 .md 文件，槽位是稀缺资源。

## 二、核心决策

### 决策 1：DISTILL 阶段就按 host 合并（不是 adapter 层硬合并）

**关键洞察**：capacity 在 ADAPT / ATOMIZE / CLASSIFY / BUCKET 四阶段是**健康的归整维度**，不应删除。问题只出在 DISTILL 产物层面。所以重构点收敛在 DISTILL 及之后。

- BUCKET 仍按 `domain::capacity` 分桶（保留归整维度，**不动 classifier / bucketer**）
- 但 `distill_buckets` 入口处按 `bucket.domain` 二次聚合，每个 host 调一次 LLM，产**一份 host 级 SkillCard**
- 一次 LLM 调用看到整条流程 → 自然产出连贯的步骤剧本，无 capacity 割裂
- capacity 标签降级为 prompt 里的「子能力分组提示」（告诉 LLM「这条 trace 含 upload-video + fill-video-metadata 两个子能力，可按此结构组织 _sop」），保留 CLASSIFY 的信息量但不造成产物割裂

**为何不选「adapter 层硬合并」**：当前 `_merge_field` 只是按 capacity 物理拼接，无去重逻辑。两个 capacity 都写了标题框，机械拼接会裸露重复内容。LLM 层合并才能真去重和叙事连贯。

### 决策 2：quirks 判定标准量化

新增明确判据——「模型拿到 DOM 文本能自己判断的事实不写；只写隐藏依赖、同名元素区分、时序依赖、SPA 阶段切换触发条件、反直觉行为」。

具体：

| 该写（DOM 看不出来） | 不该写（DOM 看得见） |
|---|---|
| 多个隐藏 file input，靠 `accept` 区分用途 | 简介是 `<div contenteditable=true>` |
| 封面上传要先点「封面设置」打开 modal | 「立即投稿」是 `<span>` |
| 标题框在封面编辑阶段不在 DOM | 创作声明是 input 不是 radio |
| 「立即投稿」点击后是整页跳转而非 AJAX | 标签输入要按 Enter |
| 同名 `name=buploader` 的视频/字幕/附件区分 | 分区是两步操作 |

### 决策 3：三件套，删除 api.md

```
domain-skills/member.bilibili.com/
├── _sop.md       # 连贯步骤剧本：第1步点哪个元素做什么，第2步...（不分 capacity）
├── selectors.md  # 附录：只收需要特征指纹的少数元素（如同名 file input 区分）
└── quirks.md     # 只写 DOM 看不出来的坑
```

`selectors.md` 降级说明：多数元素在 _sop 就地描述即可，selectors 只收需要「特征指纹」的少数元素（如多个同名 `name=buploader` 怎么区分）。

## 三、改动清单

### 源文件（5 个）

#### 1. `harness/models.py` — SkillCard 瘦身

- `api_md: str = ""` → **删除**
- `capacity: str`（必填）→ `capacity: str = ""`（可选，host 级蒸馏时存 capacity 列表如 `"upload-video, fill-video-metadata"`，作为 meta 索引）

#### 2. `harness/distiller.py` — 核心重构

- 新增 `distill_host(host, buckets, page_context, use_llm)` 函数：把同 host 所有 buckets 的 segments 聚合，一次 LLM 调用产 host 级 SkillCard
- `distill_buckets` 改为：按 `bucket.domain` 分组，每组调 `distill_host`
- **Prompt 重写**（`_DISTILL_PROMPT_TEMPLATE`）：
  - 从「为 `{capacity}` 蒸馏」改为「为 `{host}` 蒸馏整条流程」
  - 删除「只写本 capacity 强相关元素，避免跨 capacity 重复」的约束（不再分 capacity）
  - 新增「已识别子能力」段：把 CLASSIFY 产出的 capacity 列表作为步骤分组提示
  - **quirks 判定标准量化**：新增明确判据（见决策 2）
  - 输出从四件套（sop/selectors/quirks/api）改为三件套，删除 api_md 输出要求
  - selectors_md 降级说明：多数元素在 _sop 就地描述，selectors 只收需要特征指纹的少数元素
- 模板 fallback（`_template_skill_card`）同步调整：去 api_md，按 host 聚合 segments

#### 3. `adapters/treewalker_adapter.py` — 输出简化

- `_FILES` 删除 `("api.md", "api_md")`（三件套）
- `write_skills_merged` 简化：同 host 只有一个 card，无需 `_merge_field` 的多 card 分节逻辑（保留函数但简化为单 card 路径）
- `_merge_field` 的多 card 分节分支（`## <capacity>`）可删除或保留为防御逻辑

#### 4. `adapters/browserbc_adapter.py` — 小改

删除对 `skill.api_md` 的引用（改读其它字段或省略），避免 AttributeError。

#### 5. `harness/install.py` — **不动**

合并钩子逻辑通用，无需改动。

### 测试文件（2 个，约 14 个函数改写）

#### 6. `tests/test_adapters.py`（8 个函数改写）

- `_make_card()` 去 `api_md=` 参数
- `test_treewalker_adapter_writes_four_files` → 改名 `..._three_files`，文件数 4→3，去 `"api.md"`
- `test_treewalker_merge_multiple_buckets_same_host`：去 api.md 断言，capacity 分节断言视新逻辑调整
- `test_install_cards_uses_merge_when_available`：文件数 4→3
- 其余 4 个（排序/占位/原子写/单 bucket）基本不动

#### 7. `tests/test_distiller.py`（6 个函数改写）

- `_FAKE_LLM_RESPONSE` 去 `api_md` key
- `test_distill_bucket_with_mocked_llm_returns_four_fields` → 改名 `..._three_fields`，去 api_md 断言
- `test_distill_bucket_template_fallback_without_llm`：去 api_md 断言
- `test_distill_prompt_requires_element_description_format`：更新 prompt 契约（新增 quirks 判定标准、host 级蒸馏的断言）
- 新增 host 级蒸馏的测试（验证同 host 多 bucket 合并成一份 SkillCard）

### 不受影响（50 个测试函数）

- `test_adapter.py`（20）— 只测脱敏 + payload 读取
- `test_atomizer.py`（14）— 只测切片 + 渲染
- `test_classifier.py`（3）— 测的是 `CapacityLabel.capacity`，不是 SkillCard.capacity
- `test_llm.py`（11）— LLM 协议/JSON 解析
- `test_distiller.py` 中 7 个、`test_adapters.py` 中 4 个不读 `card.api_md`

## 四、实施步骤（分 5 步，每步可独立验证）

| 步骤 | 内容 | 验证方式 | 状态 |
|---|---|---|---|
| **Step 1** | 写本方案文档（本文档） | 人工审阅 | ✅ 完成 |
| **Step 2** | 模型层 — `models.py` SkillCard 瘦身 + `browserbc_adapter.py` 小改 | `uv run python -m pytest` 确认除已知失败外全过 | ✅ 完成 |
| **Step 3** | distiller 重构 — 新增 `distill_host`、重写 prompt、调整模板 fallback | 更新 `test_distiller.py` 后跑测试 | ✅ 完成 |
| **Step 4** | adapter 输出 — `_FILES` 去 api.md、简化 `_merge_field` | 更新 `test_adapters.py` 后跑测试 | ✅ 完成 |
| **Step 5** | 端到端验证 — 真 LLM 重跑 bilibili trace，对照手写参考 | 对照 `D:/dev/git/z_jordon/TreeWalker/domain-skills/member.bilibili.com/` 确认形态对齐 | ✅ 完成 |

### 实施记录

**最终验收**：
- `uv run python -m pytest tests/` → 73 个测试全过（原 71 + 新增 2 个 host 级蒸馏测试）
- `uv run ruff check .` → All checks passed
- 真 LLM 端到端 → 产出三件套，形态对齐手写参考

**实际改动文件**（与改动清单一致）：
- 源文件 5 个：`models.py` / `distiller.py` / `treewalker_adapter.py` / `browserbc_adapter.py` / `install.py`（未动，符合预期）
- 测试文件 2 个：`test_adapters.py`（8 处改写）/ `test_distiller.py`（6 处改写 + 2 个新增）

**端到端产物质量验证**（三个核心目标达成）：

1. **capacity 割裂消除** ✅
   - 重构前：`# Sop — bilibili.com (2 capacities)` + 按 capacity 分节，selectors/quirks 重复描述
   - 重构后：`# Bilibili 视频投稿流程` 连贯 13 步剧本，从导航到提交一气呵成

2. **quirks 只收 DOM 看不出来的坑** ✅
   - 重构前：9 条 quirks，含「简介是 contenteditable」「立即投稿是 span」等 DOM 可见噪声
   - 重构后：4 条 quirks，全部是 DOM 看不出来的真坑：
     - 多同名 file input 靠 accept 区分
     - 封面上传框时序依赖（必须先开 modal）
     - SPA 无刷新阶段切换（URL 不变）
     - 标签必须按 Enter

3. **三件套，删 api.md** ✅
   - 产出 `_sop.md` / `selectors.md` / `quirks.md`，无 api.md

## 五、预期产物形态（新三件套）

### `_sop.md`（连贯步骤剧本，不分 capacity）

```markdown
# Flow: B 站视频投稿

## 1. 进入投稿页
- 起点：创作者中心首页 `member.bilibili.com/platform/home`
- 点击侧边导航的「投稿」（id=nav_upload_btn），再点「视频投稿」
- 等页面加载完（上传区出现）

## 2. 上传视频文件
- 在投稿页找到视频文件上传区（多个 file input，选 accept 含 `.mp4` 的那个）
- **用 `upload_file` 直接注入文件，不要点击上传按钮**（详见 quirks.md）
- 等待上传完成（页面进入信息编辑阶段）

## 3. 填写基本信息
- **标题**：在「标题」区输入（placeholder=请输入稿件标题，maxlength=80）
- **分区**：点「分区」展开下拉，选目标分区（两步操作）
- **标签**：输入标签后**按 Enter 确认**（placeholder=按回车键Enter创建标签）
...
```

### `quirks.md`（只写 DOM 看不出来的坑）

```markdown
# Quirks — member.bilibili.com

## 1. 隐藏 file input（必须用 upload_file 直注）
- 视频上传是隐藏的 `<input type=file>`，点击会弹 OS 文件框，TreeWalker 无法驱动
- 必须用 `upload_file(index, path)` 直接注入
- 页面有多个同名 file input（name=buploader），靠 accept 区分：
  - accept 含 `.mp4` → 视频文件
  - accept 是 `.txt` → 字幕文件
  - accept 是 `.zip` → 附件

## 2. 封面上传要先打开 modal
- 封面上传 input 在 upload 阶段不在 DOM，必须先点「封面设置」打开封面编辑器
- 打开后才出现 accept=image/png, image/jpeg 的 file input
- 如果当前页找不到封面上传框，说明还在 upload 阶段，先完成上传

## 3. 标题框时序
- 标题框在封面编辑阶段不在 DOM
- 如果找不到标题 input，说明还在封面编辑阶段，先完成/关闭封面编辑
```

### `selectors.md`（附录：只收需要特征指纹的少数元素）

```markdown
# Selectors — member.bilibili.com

> 多数元素在 _sop.md 就地描述。本文件只收录 DOM 里无唯一标识、需要特征指纹区分的元素。

| 元素用途 | 特征指纹 | 备注 |
|---|---|---|
| 视频文件上传 | `type=file`, `name=buploader`, accept 含 `.mp4` | 与字幕/附件同名，靠 accept 区分 |
| 字幕文件上传 | `type=file`, `name=buploader`, accept=`.txt` | 同上 |
| 附件上传 | `type=file`, `name=buploader`, accept=`.zip` | 同上 |
| 封面图片上传 | `type=file`, accept=`image/png, image/jpeg` | 仅封面编辑 modal 内可见 |
```

## 六、风险与权衡

| 风险 | 影响 | 应对 |
|---|---|---|
| **token 量**：host 级蒸馏单次 LLM 调用 token 量大（整条 trace evidence + 全部 page_context） | B 站 case（17 events / 3 stages / ~50KB DOM）完全在上下文范围内。超大 trace（100+ events）需分批 | P0 不做分批，留 TODO |
| **capacity 信息保留** | CLASSIFY 产出的 capacity 标签是否丢失 | 作为 prompt 子能力提示，信息不丢失 |
| **增量蒸馏** | 当前 P0 增量逻辑是占位（prev_sop 为空） | host 级蒸馏后增量逻辑改为 host 级，先留 TODO 不实现 |
| **不引入新依赖** | — | 纯 stdlib + 现有 pydantic，符合 AGENTS.md 约束 |

## 七、分支策略

当前在 `feat/skill-format` 分支（Stage 4 改动未提交）。本方案属于 skill 形态调整的延续，建议在同分支继续；若需隔离改动可新开分支。Step 1 写完文档后再确认。

## 八、与既有方案的关系

本方案是 `docs/skill-format-alignment-plan.md` 的延续与调整：

- **保留**：element_attrs 字段、page_context 字段、stage 字段、白名单属性方案、元素描述表格式（这些都已实现且有测试覆盖）
- **调整**：蒸馏产物从「按 capacity 四件套」改为「按 host 三件套」；quirks 判定标准量化；selectors 降级为附录
- **删除**：api.md（无网络采集时零信息）

`docs/skill-format-alignment-plan.md` 阶段 1/2/3/4 的工作不被推翻，只是其产出的组织方式在 DISTILL 阶段重整。
