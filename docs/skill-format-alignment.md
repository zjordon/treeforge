# TreeForge Skill 形态调整方案：对齐 TreeWalker 的消化能力

> 状态：方案设计，待实施决策。
> 本文基于 B 站发布视频的真实数据对照（TreeForge 蒸馏产物 vs TreeWalker 模型输入），
> 分析两者"语言不兼容"的根因，给出 skill 形态调整方向。
>
> 关键结论：TreeForge 蒸馏出的 skill 用 CSS selector 标识元素，但 TreeWalker 的模型
> 用 [index] + 语义文本 + 白名单属性 决策——**两套元素标识体系完全对不上**。skill 要
> 真正能提升 TreeWalker 探索准确率，必须换"语言"。

---

## 一、问题：11 个 selector，0 个对得上

### 对照证据（B 站上传流程真实数据）

把 TreeForge 蒸馏出的 `selectors.md` 里的 selector，和 TreeWalker 模型实际看到的 DOM 文本（`upload-conver.txt`）逐一对照：

| skill 里的 selector | TreeWalker DOM 里的实际呈现 | 对上？ |
|---|---|---|
| `.header-entry .upload-btn`（投稿按钮） | `[142]<a id=nav_upload_btn /> 投稿` | ❌ 无此 class |
| `input[type='file'][accept='video/*']`（视频文件） | `[332]<input type=file name=buploader accept=.mp4,.flv,... />` | ❌ accept 是扩展名列表不是 `video/*` |
| `input[placeholder='请输入标题']`（标题） | 标题区无 input，是纯文本 + 计数（可能是 contenteditable 或动态渲染） | ❌ 元素都不在 |
| `.select-wrap .select-text`（分区下拉） | `[3729]<div /> 分区 科技数码` | ❌ 无此 class |
| `.select-list li:nth-child(3)`（科技选项） | 分区是已选状态文本，非下拉列表 | ❌ |
| `textarea[placeholder='填写更加全面的信息']`（简介） | `[3788]<div contenteditable=true />` | ❌ 是 div 不是 textarea |
| `.tag-input .btn-add`（标签按钮） | `[3686]<input type=text placeholder=按回车键Enter创建标签 />` | ❌ 无此 class |
| `input.tag-input`（标签输入） | 同上 `[3686]` | ❌ |
| `radio[value='original']`（原创声明） | 整个 DOM 无 radio（在折叠区"更多设置"里） | ❌ |
| `.submit-btn .submit-add`（立即投稿） | `[3819]<span /> 立即投稿` | ❌ 是 span 无此 class |

**11 个 selector，0 个能和 TreeWalker 的 DOM 对上。**

### 两个根因

**根因一：skill 用 CSS selector，TreeWalker 用 index + 语义文本**

TreeWalker 喂给模型的 DOM 格式是 `[index]<tag attr=val /> 可见文本`——带索引的语义化文本树，不是 CSS selector 可查询的 DOM。模型看到的是：

```
[142]<a id=nav_upload_btn />
	投稿
[3686]<input type=text placeholder=按回车键Enter创建标签 maxlength=20 />
[3788]<div contenteditable=true />
```

模型决策输出 `click(index=142)`，用的是 **index**。skill 里写的 `.header-entry .upload-btn`，模型看到了也用不上——它不能把 selector 翻译成 index，因为 TreeWalker 的 DOM 文本经过五步过滤序列化，**class 层级信息大量被精简掉**，没有 `.header-entry > .upload-btn` 这种结构可查。

**根因二：skill 的 selector 是"想象"的，不是"观察"的**

`bilibili-upload.trace.json` 是手写构造的假数据（init-plan §六明确"手写示例，不需要真录制"）。里面的 selector 是按"想象中 B 站该长这样"写的。真实的 B 站 DOM（`upload-conver.txt` 证明）根本不长这样：

- 投稿按钮是 `id=nav_upload_btn` 不是 `.upload-btn`
- 简介是 `contenteditable div` 不是 `textarea`
- 原创声明在这个页面根本没有 radio（在折叠区）
- 分区显示已选状态文本，非下拉列表

即使格式对上了，内容也是错的——因为基于假数据蒸馏。

---

## 二、TreeWalker 的"消化能力"是什么（skill 要对齐的目标语言）

要让 skill 能提升 TreeWalker 探索准确率，必须用 TreeWalker 能消化的方式标识元素。TreeWalker 的消化能力由两个东西决定：

### 决定因素一：STATIC_ATTRIBUTES 白名单（45 个属性）

TreeWalker 的 DOM 文本只保留这 45 个属性（`src/tree_walker/browser/views.py:82-131`），其他属性一律丢弃：

```
基础交互：class, id, name, type, placeholder, title, role
测试友好：data-testid, data-test, data-cy, data-selenium
表单：for, required, disabled, readonly, checked, selected, multiple, accept
链接：href, target, rel
ARIA（语义）：aria-label, aria-describedby, aria-labelledby, aria-controls,
              aria-owns, aria-live, aria-atomic, aria-busy, aria-hidden,
              aria-pressed, aria-autocomplete, aria-checked, aria-selected,
              aria-valuemin, aria-valuemax, aria-valuenow, aria-placeholder
其他：list, tabindex, alt, src, lang, itemscope, itemtype, itemprop, pseudo
```

**含义**：skill 里写的元素属性，只有在这 45 个里的，模型才可能在 DOM 文本里看到并对应。比如 `data-action`、`ng-model`、`v-bind:class` 这些不在白名单的属性，TreeWalker DOM 里根本没有，skill 写了也白写。

### 决定因素二：模型怎么"找"元素

TreeWalker 的模型不是用 selector 查询 DOM，是**读语义化文本树**找元素。它能靠三类信号识别一个元素：

1. **可见文本**：`投稿`、`立即投稿`、`按回车键Enter创建标签`——模型能直接在文本里搜到
2. **白名单属性**：`id=nav_upload_btn`、`placeholder=请选择符合您视频内容的创作声明`、`role=button`——模型能在 DOM 文本里匹配
3. **结构位置**：缩进层级表示父子关系——"投稿按钮在导航区"这类相对位置

**模型的查找逻辑是"语义搜索 + 属性匹配"，不是"CSS selector 查询"**。这是 skill 要适配的核心。

---

## 三、调整方向：skill 用"元素描述"替代 CSS selector

### 新的元素标识格式

skill 应该用**自然语言描述 + 白名单稳定属性 + 可见文本**标识元素，而不是 CSS selector：

**现状（CSS selector 表，对不上）**：
```markdown
| Selector | Purpose | Notes |
|---|---|---|
| `.header-entry .upload-btn` | Open upload page | Also matches `[aria-label='投稿']` |
| `input[placeholder='请输入标题']` | Video title input | |
```

**调整后（元素描述表，对得上）**：
```markdown
| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 投稿入口 | 首页右上角，可见文本"投稿" | id=nav_upload_btn | 点击后跳转投稿页 |
| 视频文件上传 | 投稿页拖拽区，accept 含 .mp4 | name=buploader, type=file | 隐藏的，用 upload_file 直注 |
| 标题输入 | 标题区，placeholder 或可见文本"标题" | （需真实采集确认） | 计数 19/80 |
| 标签输入 | 标签区，可见文本"按回车键Enter创建标签" | placeholder=按回车键Enter创建标签 | 输入后按 Enter 确认 |
| 简介输入 | 简介区，可见文本"简介" | contenteditable=true | 是富文本 div 不是 textarea |
| 立即投稿 | 页面底部，可见文本"立即投稿" | span 元素 | 可能在"更多设置"折叠区外 |
```

**关键差异**：
- "怎么找到它"用**自然语言描述位置/上下文**（模型读 DOM 文本时能理解）
- "稳定标识"只列**白名单内的属性**（模型能在 DOM 文本里匹配到）
- 不写 CSS selector（模型用不上）

### 为什么这样模型能用上

模型读 TreeWalker DOM 文本时，是逐元素扫语义。当它看到 skill 说"投稿入口：可见文本'投稿'，id=nav_upload_btn"，再在 DOM 文本里遇到：

```
[142]<a id=nav_upload_btn />
	投稿
```

模型能**立即对应**——"这就是 skill 说的投稿入口，index=142"。这比给它一个 `.header-entry .upload-btn`（DOM 里根本没这个 class）有用得多。

---

## 四、四个 skill 文件的具体调整

### `selectors.md`（调整最大）

从 CSS selector 表 → 元素描述表（见上节示例）。每个关键元素记：
- **元素用途**（这是什么）
- **怎么找到它**（自然语言位置/上下文描述）
- **稳定标识**（白名单属性 + 可见文本）
- **备注**（操作要点，如"用 upload_file 直注不点击"）

### `quirks.md`（调整中）

现状的 quirks 是对的（记 SPA 导航、两步下拉等），但要补充**对 TreeWalker 决策有用的怪癖**：

- **contenteditable 识别**：简介是 div 不是 textarea，模型要理解 contenteditable 也能 input_text
- **隐藏 file input**：视频上传是隐藏的 input，必须用 upload_file 直注不能点击
- **折叠区**：原创声明在"更多设置"折叠区，要先展开才能操作
- **动态渲染**：标题输入框可能延迟渲染，要等页面加载完
- **accept 格式**：file input 的 accept 是扩展名列表（`.mp4,.flv,...`）不是 `video/*`，匹配时要注意

### `_sop.md`（调整小）

流程骨架基本对的，但步骤里的 selector 引用要换成元素描述。从：

```
1. Click `.header-entry .upload-btn` (Label: 投稿)
```

改成：

```
1. 点击"投稿"入口（首页右上角，id=nav_upload_btn），跳转投稿页
```

### `api.md`（基本不动）

API 记录（URL 模式、私有端点）不依赖元素标识，保持现状。

---

## 五、对 TreeForge 蒸馏层的影响

### distiller prompt 要改

现状 prompt 产出 CSS selector 表（对齐 BrowserBC 的"去站点化"传统，但 TreeForge 反过来要"站点特定"）。调整后 prompt 要明确要求：

```
产出"元素描述表"，每行包含：
- 元素用途（这个元素是干什么的）
- 怎么找到它（用自然语言描述在页面上的位置和上下文，让另一个 AI 读了能在页面 DOM 文本里定位）
- 稳定标识（只列以下白名单属性：id, name, type, placeholder, aria-label, role,
  data-testid, data-test, data-cy, visible text；不要写 CSS selector）
- 备注（操作要点）

不要产出 CSS selector（如 .class-name 或 div > span）。消费方读的是语义化 DOM 文本，
不是可查询的 DOM 树，CSS selector 用不上。
```

### 蒸馏产物的"消费者契约"

明确 TreeForge skill 的消费者是 **TreeWalker 的 LLM**，它的"消化能力"是：
- 读 `[index]<tag attr /> text` 格式的 DOM 文本
- 靠白名单属性 + 可见文本识别元素
- 用 index 决策

distiller 的产出必须适配这个契约，而不是套用 BrowserBC 的 CSS selector 传统（BrowserBC 的消费者是 MCP 检索 + Claude 执行，和 TreeWalker 不同）。

---

## 六、对 TreeForge 采集层的影响

### 手写 trace 蒸馏不出正确 skill

`bilibili-upload.trace.json` 是假数据，selector 是想象的。要产出能用的 skill，trace 必须来自**真实采集**，且采集的元素属性必须覆盖 TreeWalker 的白名单。

### 采集层要采什么属性

TreeForge 的采集层（P2）采集元素时，必须记录这些属性（对齐 TreeWalker STATIC_ATTRIBUTES 的子集——最常用于元素识别的）：

| 属性类别 | 具体属性 | 为什么重要 |
|---|---|---|
| 标识 | id, name | 最稳定的元素标识 |
| 语义 | aria-label, role, placeholder, title | 模型靠这些识别元素用途 |
| 测试 | data-testid, data-test, data-cy | 测试友好属性，最稳 |
| 类型 | type, accept | 区分 input 种类（file/text/checkbox） |
| 文本 | 可见文本（innerText） | 模型搜索元素的主要信号 |
| 状态 | checked, selected, disabled, contenteditable | 影响操作方式 |

**不能只采 CSS selector**——CSS selector 在 TreeWalker DOM 文本里不可见（class 层级被精简）。要采上述属性的原始值，蒸馏时才能产出对得上的元素描述。

---

## 七、验证方法

### 最小验证（不依赖 TreeForge 采集层）

1. 拿 TreeWalker 真实录制的 B 站 trace（`upload-conver.txt` 对应的真实采集数据）
2. 手动按新格式（元素描述表）写一份 `selectors.md`
3. 注入 TreeWalker agent（用 skill 注入机制），跑 B 站上传
4. 对比：无 skill vs 有（新格式）skill 的探索成功率

这个验证不依赖 TreeForge 蒸馏代码——手写新格式 skill + TreeWalker 注入即可。先验证"新格式 skill 能不能提升成功率"，再回头改 distiller prompt 让它自动产出新格式。

### 完整验证（依赖采集层 + 蒸馏层）

1. TreeForge P2 采集层真实录制 B 站（采白名单属性）
2. distiller 按新 prompt 蒸馏出元素描述表格式 skill
3. 注入 TreeWalker，A/B 实测

---

## 八、与现有设计的关系

- **TreeForge `init-plan.md` §五**：原四文件结构（_sop/selectors/quirks/api）保留，但 `selectors.md` 的**内容格式**要从 CSS selector 表换成元素描述表
- **TreeForge `ARCHITECTURE.md`**：与 BrowserBC 的核心分叉点（"Capture site-specific selectors"）要进一步明确——capture 的是**白名单属性 + 可见文本**，不是 CSS selector
- **TreeWalker `docs/skill-injection-design.md`**：注入机制不变（skill 文本进 `[Domain Skill]` 段），但 skill 文本内容要按本文调整
- **知识库 `browser-accessibility-tree.md`**：本文的属性白名单来自那里的 STATIC_ATTRIBUTES 分析

---

## 九、核心判断

**TreeForge 的 skill 形态要按 TreeWalker 的"消化能力"重新设计，而不是套用 BrowserBC 的 CSS selector 传统。**

具体三个调整：
1. **格式**：selectors.md 从 CSS selector 表 → 元素描述表（用途 + 自然语言位置 + 白名单属性 + 可见文本）
2. **数据源**：trace 必须真实采集（不能手写假数据），采集白名单属性
3. **distiller prompt**：明确要求产出元素描述，禁产出 CSS selector

这三个调整的共同目标：**让 skill 里的元素信息，模型在 TreeWalker DOM 文本里能搜到、能对应、能用 index 操作**。否则 skill 写得再漂亮，模型用不上，等于白搭。
