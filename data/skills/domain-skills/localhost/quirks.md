# Quirks — localhost

- **两类网格行为不同**：UI-Component 网格（订单/客户/商品）筛选为同页 AJAX，URL 不变；评论旧版网格（reviewGrid）点 Search 或 Reset Filter 后是**整页跳转**，URL 变为 `/admin/review/product/index/filter/<base64>/internal_reviews//form_key/<随机>/`——提交后必须重读 DOM（index 全变），勿从 URL 解码筛选条件。
- **功能性 id 稳定 vs 随机 id 混存**：评论筛选输入 id `reviewGrid_filter_name`、`reviewGrid_page-limit`、`reviewGrid_massaction-select` 等稳定；但 Search/Reset Filter 按钮 id、created_at 输入 id、UI 网格 Filters 面板输入/下拉 id 均为随机串，每次加载变化——按钮靠 title/可见文本定位，输入/下拉靠 `name=` 属性。
- **Search/Reset Filter/Apply Filters 提交后 DOM 整体重建**：网格行集与所有 index 更换；确认筛选生效看 "N records found"（如 Pending 筛后 "5 records found"）与 "Active filters:"，勿复用旧 index。
- **日期选择器弹层按钮内容为 "undefined"**：弹层不可靠，对日期输入框直接 `input_text` 手输 `mm/dd/yyyy`。
- **菜单展开为渐进式 DOM 更新**：点一级菜单（Sales/Marketing）后二级项（Orders/All Reviews）才出现；每步点击后必须重读 DOM。
- **列表/详情页 DOM 随滚动渐进加载**：网格后续行、详情页各区需多次 `scroll` 后才出现（评论筛选结果页滚动多次后底部 massaction/分页区才完整可见）。
- **每次页面切换（筛选、排序、翻页、进出详情、Back 返回）index 全变**，操作前重读 DOM；Back 返回列表后筛选值仍保留。
- **移除激活筛选**：chip 旁 `Remove` 按钮（单个）或 `Clear all`（全部）；移除后网格刷新。
- **点表头 th 排序是同页 AJAX**，点击后行序与 index 变化，勿复用旧行 index。
- **网格行 checkbox 含实体 id**：订单/客户行 `idscheckN`；评论行 `name=reviews` value=评论 id——按实体定位行用此指纹，勿依赖行 index。
- **评论编辑页翻页按钮组合随位置变化**：按钮存在与否由评论在结果集中的排序位置决定，勿假设固定按钮集。
- **Dashboard 各 tab 前的装饰性 span**（title "The information in this tab has been changed." 等）：非真实状态提示，忽略。
- **Dashboard/评论表格行详情 URL 在 `tr` 的 title 属性**中，可直接 navigate；评论列表行同样可整行点击进入编辑页。