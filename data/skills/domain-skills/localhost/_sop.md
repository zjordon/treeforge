# localhost (Magento Admin) 站点卡片

## 站点功能地图（Site Function Map）

- 入口: `http://localhost:7780/admin/admin/dashboard/`（Bestsellers/Most Viewed/New Customers/Customers 标签、Lifetime Sales、Last Orders、Last/Top Search Terms 面板；表格行 `tr` 的 title 属性即详情 URL；另有 Advanced Reporting 推广区与 `Go to Advanced Reporting` 链接）
- 商品管理: `Catalog` → `Products` → `/admin/catalog/product/`
- 订单管理: `Sales` → `Orders` → `/admin/sales/order/`（顶部 `Create New Order` 按钮 id=add；列表页可从侧边菜单点 `Dashboard` 一步返回 dashboard）
- 订单详情: `sales/order/view/order_id/N/`，顶部 Back/edit/Cancel/Invoice/Ship；Information/Invoices/Credit Memos/Shipments/Comments History 标签；底部 Notes：`id=history_status` 下拉、`id=history_comment` textarea、`title=Submit Comment`
- 发票管理: `Sales` → `Invoices` → `/admin/sales/invoice/`；详情 `sales/invoice/view/invoice_id/N/`
- 客户管理: `Customers` → `All Customers` → `/admin/customer/index/`（`Add New Customer` id=add）
- 商品评论: `Marketing` → `Reviews` → `All Reviews` → `/admin/review/product/index/`；筛选后 URL `/admin/review/product/index/filter/<base64>/`
- 评论详情/编辑: `/admin/review/product/edit/id/N/`。Status `id=status_id`、Nickname `id=nickname`、Summary `id=title`、正文 `id=detail`；按钮 Back(id=back)/Reset/Delete/Save(id=save_button)/Save and Next/Save and Previous/Next/Previous——按位置显示不同组合
- CMS 页面管理: `Content` → `Pages` → `/admin/cms/page/`（`Add New Page` id=add；UI-Component 网格：ID/Title/URL Key/Layout/Store View/Status/Created/Modified 列，行内 Select 下拉含 Edit/Delete/View；筛选含日期范围 from/to、Layout/Status/Custom Theme/Custom Layout 下拉；示例数据 6 条：404 Not Found、Home Page、Enable Cookies、Privacy Policy、About us、Customer Service）
- 报表-Bestsellers: `Reports` → `Products` → `Bestsellers` → `/admin/reports/report_sales/bestsellers/`；筛选仅 Period/From/To/Empty Rows/Export。提交后整页跳转 `.../bestsellers/filter/<base64>/`，结果列 Interval/Product/Price/Order Quantity
- 报表-Orders(Sales): `Reports` → `Sales` → `Orders` → `/admin/reports/report_sales/sales/`（两步导航）。Date Used `id=sales_report_report_type`、Period/From/To/Order Status/多选 order_statuses[]/Empty Rows/Show Actual Values/Export。结果列 Interval/Orders/Sales Items/Sales Total/Invoiced/Refunded/Sales Tax/Sales Shipping/Sales Discount/Canceled，含 Total 行
- 报表-Refunds: `Reports` → `Sales` → `Refunds` → `/admin/reports/report_sales/refunded/`。Date Used 选项为 Order Created|Last Credit Memo Created Date；结果列 Interval/Refunded Orders/Total Refunded/Online/Offline Refunds
- 报表页公共提示：结果页显示 "Last updated…" 与刷新链接；Order Created 类报表依赖统计刷新
- 侧边菜单（li id 稳定）: Dashboard `menu-magento-backend-dashboard` / Sales / Catalog / Customers / Marketing `menu-magento-backend-marketing` / Content `menu-magento-backend-content` / Reports `menu-magento-reports-report` / Stores / System / Find Partners & Extensions；二级分组：Marketing、Reviews(By Customers/By Products)、Sales(Orders/Tax/Invoiced/Shipping/Refunds/Coupons/PayPal/Braintree Settlement)、Customers(Order Total/Order Count/New)、Products(Views/Bestsellers/Low Stock/Ordered/Downloads)、Statistics(Refresh Statistics)、Business Intelligence
- 页头全局搜索: `input id=search-global`；UI-Component 网格右上全文搜索 `id=fulltext placeholder="Search by keyword"` + Search 按钮；全局搜索预览含 Products/Orders/Customers/Pages 四类入口
- Scope 切换（Dashboard/报表头部）: `id=store-change-button` + Reload Data

## 站点通用操作知识

- 侧边菜单两级：点一级项展开后二级项才出现；每步点击后重读 DOM，index 会变。跨区跳转（如 CMS Pages→Dashboard）直接点侧边菜单对应一级项即可，一步到达。
- 报表间切换：在任一报表页直接点侧边菜单另一报表链接，不必先回 Dashboard。
- 三类网格/表单：
  - 新 UI-Component 网格（订单/客户/商品/CMS Pages）：`Filters` 同页 AJAX 展开面板，`Apply Filters`（靠可见文本），清除用 `Clear all` 或 chip 旁 `Remove`；已应用筛选以 "Active filters:" chip 行展示（如 `Status: Complete`）。
  - 旧版网格（评论页 reviewGrid）：无 Filters 按钮，筛选行在表头下方；`Search` 提交，`Reset Filter` 清除。
  - 报表筛选表单：独立 `id=filter_form`，`id=filter_form_submit`（Show Report）整页跳转 base64 filter URL。
- UI 网格公共控件：控制条 Default View/Save View As.../Submit/Columns（列显隐）、`id=fulltext` 全文搜索、Filters；行首列 checkbox（`idscheckN`）+ 行尾 `Select` 下拉（Edit/Delete/View 等）；底部每页条数（20/30/50/100/200/Custom）、当前页 number input、`of N`、Next/Previous（单页 disabled）。
- 报表 filter_form 字段通用（id 稳定）：`sales_report_report_type`（Date Used，仅 Orders/Refunds，选项随报表不同）、`sales_report_period_type`（Day|Month|Year）、`sales_report_from/to`（required，placeholder=mm/dd/yyyy，接受 `5/1/21` 短格式）、`sales_report_show_order_statuses`（Any|Specified，数字值）、多选 `id=sales_report_order_statuses` name=order_statuses[]、`sales_report_show_empty_rows`、`sales_report_show_actual_columns`（仅 Orders）、Export 下拉（CSV|Excel XML）+ Export 按钮（id 随机，靠可见文本）。
- 订单 Filters 面板字段（靠 name，id 随机）：`created_at[from]/[to]`、`base_grand_total[from]/[to]`、`grand_total[from]/[to]`、`store_id`、`increment_id`、`billing_name`/`shipping_name`、`status`、`transaction_source`；客户面板另含 `billing_telephone`/`email`/`group_id`/`billing_country_id` 等；CMS Pages 面板含 `created_at[from]/[to]`、`modified_at[from]/[to]`、ID from/to、Layout/Status/Store View 等下拉。
- 下拉筛选直接 `select_dropdown(index, value)` 无需先点击；`dropdown_options` 可先读选项；传 value 而非显示文本（见 quirks）。
- 日期输入手输最稳（弹层按钮文本 undefined）；报表日期接受 `5/1/22` 短格式；修改用 `input_text(index, text, clear=true)`。
- 筛选结果确认：看 "N records found" 计数（如订单 153 records found）与 Total 行；无结果显示 "We couldn't find any records."。
- 列排序：点表头 th 同页 AJAX，行序与 index 变化；也可用表头 Options（Select All/Deselect All/All in Column）做范围选择。
- 分页：底部每页条数下拉、当前页 input、`of N`、Next/Previous 按钮（单页时 disabled=true）。
- 进详情：行内 View/Edit 链接、行尾 Select 下拉、点行本身（tr title 即详情 URL）或直接 `navigate`；长页需多次 `scroll`（报表常 2~10 屏，Dashboard 面板常需滚到底）；返回用顶部 `Back`（id=back）。
- 回到列表后筛选值保留；再搜索前视需要先 Reset/Clear all。
- `id=add` 跨页含义不同：订单页=Create New Order，客户页=Add New Customer，评论页=New Review，CMS Pages 页=Add New Page——按 title/可见文本区分。
- Dashboard 顶部含 "JavaScript may be disabled" 警告块（noscript 提示），可忽略；页面顶部 System Messages: 0 表示无待处理通知。