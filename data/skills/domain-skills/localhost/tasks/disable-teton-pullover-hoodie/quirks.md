# Quirks — localhost

- 只需禁用父商品（Configurable Product，ID 78）：禁用父商品即整品下架，无需逐个禁用 15 个 simple 变体（stage product 中可见变体均为 "Not Visible Individually"）。
- Enable Product 的 checkbox 元素 id 每次页面加载都不同（录制中为 BQG6PKS），只能靠 `name=product[status]` 识别——agents 按 index 操作时要在当前 DOM 快照中找 name 为 `product[status]` 的 checkbox。
- 点击 label 与点击 checkbox 本身均生效（录制中两次点击均触发同一开关）；一次取消勾选即可，不要重复点击导致状态翻转。
- 点击 Save 后页面短暂显示 "Please wait..." 遮罩（stage 78），需等待保存完成后再操作（可 `wait(1~2)`），随后页面回到编辑页而非列表页；需手动点 Back 返回网格验证。
- 搜索前若存在遗留过滤条件，结果可能不全（录制中先点了 Clear all）；产品网格默认只显示第 1 页/20 条，确保 keyword 过滤生效后再找 Edit 链接。