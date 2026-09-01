# Quirks — localhost

- **三类提交行为不同**：UI-Component 网格（订单/客户/商品/CMS Pages）筛选为同页 AJAX，URL 不变；评论旧版网格 Search/Reset Filter 整页跳转 base64 filter URL；报表 Show Report 同样整页跳转——跳转后必须重读 DOM（index 全变），勿从 URL 解码筛选条件。
- **报表多选状态下拉可能不在初始 DOM**：`id=sales_report_order_statuses` 在部分阶段缺失，与表单交互后才出现——若找不到先 `select_dropdown(show_order_statuses, Specified)` 再重读 DOM。
- **报表下拉选项值为内部 value 而非显示文本**：show_order_statuses/show_empty_rows/show_actual_columns 为数字；order_statuses[] 传 value（如 `complete`）。
- **Date Used（report_type）选项随报表类型不同**：Orders=Order Created|Order Updated，Refunds=Order Created|Last Credit Memo Created Date——同 id，先 `dropdown_options` 读实际选项；Bestsellers 完全没有该字段。
- **功能性 id 稳定 vs 随机 id 混存**：`sales_report_*`、`filter_form_submit` 稳定；Search/Reset/Export 按钮 id、UI 网格 Filters 面板输入 id、Export 下拉 id 均随机——按钮靠 title/可见文本，输入/下拉靠 id（报表）或 name（UI 网格）。
- **提交后 DOM 整体重建**：行集与所有 index 更换；确认筛选生效看 "N records found"（如订单 153、CMS 6）与输入保留值；无结果显示 "We couldn't find any records."。报表结果行 tr title=#（无详情链接），Bestsellers 行按日期分组，同日期产品行需向上追溯到最近带日期的行。
- **日期选择器弹层按钮内容为 "undefined"**：弹层不可靠，对日期输入框直接 `input_text` 手输 `mm/dd/yyyy`（报表也接受 `5/1/21` 短格式）。日历按钮 span 文本同为 undefined，勿点击。
- **filter_form 区域点击易误触提交**：点击表单内非输入空白处会触发表单提交——只点目标 input/select 本身，最后用 `id=filter_form_submit` 提交。
- **菜单展开为渐进式 DOM 更新**：点一级菜单后二级项才出现；页面切换后侧边菜单可能渲染为折叠的扁平 `<a>` 列表（无 li id，如 page_1/dashboard_2 阶段前后的导航态）——此时按可见文本（Dashboard/Sales/Reports…）识别菜单项。每步点击后必须重读 DOM。
- **列表/详情/报表页 DOM 随滚动渐进加载**：后续行、表格区、filter_form、Dashboard 下方标签与面板需多次 `scroll` 后才完整出现（报表结果常需滚 2~10 屏）。
- **每次页面切换（筛选、排序、翻页、进出详情、Back、报表过滤）index 全变**，操作前重读 DOM；返回列表后筛选值仍保留。
- **网格行 checkbox 与 tr title 含实体线索**：订单/客户/CMS 行 `idscheckN`（value 即实体 id）；评论行 `name=reviews`；Dashboard/列表行 tr title 即详情 URL 可直接 navigate。
- **评论编辑页翻页按钮组合随位置变化**：Previous/Next 等按钮存在与否由排序位置决定。
- **Dashboard 各 tab 前的装饰性 span**（title "The information in this tab has been changed." / "Loading..." 等）：非真实状态提示，忽略。
- **`id=add` 跨页复用**：订单页=Create New Order、评论页=New Review、客户页=Add New Customer、CMS Pages 页=Add New Page；勿把上一页含义带入。
- **Scope 区可见文本跨页变化**：Dashboard 显示 "All Store Views"，报表页同一 `id=store-change-button` 显示 "All Websites"——同一控件。
- **CMS Pages 网格筛选面板字段 label 显示异常**：日期范围 label 呈 "Created from undefined to undefined" 样式（page 阶段），但输入框可用——靠 name/placeholder 定位输入框而非 label 文本。下拉（Layout/Status/Custom Theme/Custom Layout）配 "Done" 按钮的自定义下拉 UI。