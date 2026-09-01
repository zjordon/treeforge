# Quirks — localhost

- **三类提交行为不同**：UI-Component 网格（订单/客户/商品）筛选为同页 AJAX，URL 不变；评论旧版网格（reviewGrid）点 Search/Reset Filter 后是**整页跳转**，URL 变为 `/admin/review/product/index/filter/<base64>/...`；报表（Bestsellers）点 Show Report 后也是**整页跳转**至 `/admin/reports/report_sales/bestsellers/filter/<base64>/`——两类跳转后必须重读 DOM（index 全变），勿从 URL 解码筛选条件。
- **功能性 id 稳定 vs 随机 id 混存**：评论 `reviewGrid_filter_*`、报表 `sales_report_*`、`filter_form_submit` 等稳定；但 Search/Reset Filter 按钮 id、Export 按钮/下拉 id、UI 网格 Filters 面板输入 id 均为随机串——按钮靠 title/可见文本定位，输入/下拉靠 id（报表）或 `name=`（UI 网格）。
- **提交后 DOM 整体重建**：网格行集与所有 index 更换（本页 index 从 6xxx 跳到 15xxx）；确认筛选生效看 "N records found" 与输入框保留值；报表无结果显示 "We couldn't find any records."，有结果时表格含 Total 行（Interval/Product/Price/Order Quantity 列），行按月度 Interval 分组（1/2022、2/2022…）。
- **日期选择器弹层按钮内容为 "undefined"**：弹层不可靠，对日期输入框直接 `input_text` 手输 `mm/dd/yyyy`（报表也接受 `1/1/22` 短格式，value 示例 `1/1/22`、`3/31/22`）。
- **日期输入旁的按钮 span 文本为 "undefined"**（filter_form 内 From/To 后各有一个日历按钮，内容渲染为 undefined）：非可读按钮，勿尝试点击读文本。
- **菜单展开为渐进式 DOM 更新**：点一级菜单（Sales/Marketing/Reports）后二级项（Orders/All Reviews/Bestsellers）才出现；每步点击后必须重读 DOM。
- **列表/详情/报表页 DOM 随滚动渐进加载**：后续行、表格区需多次 `scroll` 后才完整出现（Bestsellers 结果行常需向下滚才可见）。
- **每次页面切换（筛选、排序、翻页、进出详情、Back 返回、报表过滤）index 全变**，操作前重读 DOM；返回列表后筛选值仍保留，可继续操作其余行。
- **网格/报表行 checkbox 与 tr title 含实体线索**：订单/客户行 `idscheckN`；评论行 `name=reviews` value=评论 id；Dashboard/列表行 tr title 即详情 URL 可直接 navigate；**报表结果行 tr title=#（无详情链接），点击整行无导航意义**——报表数据直接从行文本读取即可。
- **评论编辑页翻页按钮组合随位置变化**：Previous/Next/Save and Previous/Save and Next 存在与否由排序位置决定。
- **Dashboard 各 tab 前的装饰性 span**（title "The information in this tab has been changed." 等）：非真实状态提示，忽略。
- **评论网格筛选行首列 massaction 下拉**与批量 Actions 下拉易混：前者在筛选行内，后者在网格上方工具栏。
- **`id=add` 跨页复用**：订单页=Create New Order、评论页=New Review、客户页=Add New Customer；勿把上一页的 add 含义带入本页。
- **报表页有两个同名 Show Report 触发点**（头部 `id=filter_form_submit` 与 filter_form 提交），点击表单区域内任意非输入元素可能意外触发提交——填筛选字段时只点目标 input/select，勿点 filter_form 空白处。
- **Scope 区可见文本跨页变化**：Dashboard 显示 "All Store Views"，Bestsellers 报表页同一 `id=store-change-button` 显示 "All Websites"——同一控件，勿误认成两个元素。