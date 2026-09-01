# localhost (Magento Admin) 站点卡片

## 站点功能地图（Site Function Map）

- 入口: `http://localhost:7780/admin/admin/dashboard/`（Bestsellers/Most Viewed/New Customers/Customers 标签、Lifetime Sales、Last Orders 面板；表格行 `tr` 的 title 属性即详情 URL）
- 商品管理: `Catalog` → `Products` → `/admin/catalog/product/`
- 订单管理: `Sales` → `Orders` → `/admin/sales/order/`（顶部 `Create New Order` 按钮，其 id=add）
- 订单详情: `sales/order/view/order_id/N/`，顶部 Back/edit/Cancel/Invoice/Ship；Information/Invoices/Credit Memos/Shipments/Comments History 标签；底部 Notes：`id=history_status` 下拉、`id=history_comment` textarea、`title=Submit Comment`
- 发票管理: `Sales` → `Invoices` → `/admin/sales/invoice/`；详情 `sales/invoice/view/invoice_id/N/`
- 客户管理: `Customers` → `All Customers` → `/admin/customer/index/`（`Add New Customer` id=add）
- 商品评论: `Marketing` → `Reviews` → `All Reviews` → `/admin/review/product/index/`（约 351 条；`New Review`(id=add)、Search、Reset Filter）；筛选后 URL 变为 `/admin/review/product/index/filter/<base64>/internal_reviews//form_key/<随机>/`
- 评论详情/编辑: `/admin/review/product/edit/id/N/`。Status `id=status_id`、Nickname `id=nickname`、Summary `id=title`、正文 `id=detail`；按钮 Back(id=back)/Reset/Delete Review/Save Review(id=save_button)/Save and Next/Save and Previous/Next/Previous——按位置显示不同组合
- 报表-Bestsellers: `Reports` → `Bestsellers` → `/admin/reports/report_sales/bestsellers/`。筛选表单 `id=filter_form`：Period 下拉 `id=sales_report_period_type`（Day|Month|Year）、From `id=sales_report_from`、To `id=sales_report_to`（均 required，`placeholder=mm/dd/yyyy`，也接受 `1/1/22` 短格式）、Empty Rows `id=sales_report_show_empty_rows`（Yes|No）、Export 下拉（CSV|Excel XML，id 随机）；提交按钮 `id=filter_form_submit`（title=Show Report）。提交后 URL 变为 `/admin/reports/report_sales/bestsellers/filter/<base64>/`，结果按 Interval（如 1/2022、2/2022）分组，行含 Product/Price/Order Quantity
- 侧边菜单（li id 稳定）: Dashboard `menu-magento-backend-dashboard` / Sales / Catalog / Customers / Marketing `menu-magento-backend-marketing` / Content / Reports / Stores / System / Find Partners & Extensions
- 页头全局搜索: `input id=search-global`；UI-Component 网格右上全文搜索 `id=fulltext placeholder="Search by keyword"` + Search 按钮
- Scope 切换（Dashboard/报表头部）: `id=store-change-button`（All Store Views / All Websites）+ Reload Data；`#store_switcher` 等 change 事件触发 switchScope

## 站点通用操作知识

- 侧边菜单两级：点一级项（Sales/Marketing/Reports）展开后二级项（Orders/All Reviews/Bestsellers）才出现；每步点击后重读 DOM，index 会变。
- 三类网格/表单：
  - 新 UI-Component 网格（订单/客户/商品）：控制条 `Filters` 同页 AJAX 展开面板，填完 `Apply Filters`（靠可见文本），清除用 `Clear all` 或 chip 旁 `Remove`。
  - 旧版网格（评论页 reviewGrid）：**无 Filters 按钮**，筛选行在表头下方；填完点顶部 `Search`，清除用 `Reset Filter`。
  - 报表筛选表单（Bestsellers 等）：独立 `id=filter_form`，填完点 `id=filter_form_submit`（Show Report）触发**整页跳转**至 base64 filter URL。
- 订单 Filters 面板字段（靠 name，id 均随机）：`created_at[from]/[to]`、`base_grand_total[from]/[to]`、`grand_total[from]/[to]`、`store_id`、`increment_id`、`billing_name`/`shipping_name`、`status`、`transaction_source`；客户面板另含 `billing_telephone`/`email`/`group_id`/`billing_country_id` 等。
- 下拉筛选（Status/Type/Period 等）直接 `select_dropdown(index, value)`，无需先点击；`dropdown_options` 可先读选项。
- 日期输入 `placeholder=mm/dd/yyyy`，手输日期最稳（弹层按钮文本为 undefined，不可靠）；报表日期格式也接受 `1/1/22` 短格式。
- 筛选结果确认：看 "records found" 计数（报表为表格上方 "N records found"、Total 行）；报表无结果时显示 "We couldn't find any records."。
- 列排序：点表头 th 同页 AJAX，行序与 index 变化。
- 分页：网格底部每页条数下拉（`id=reviewGrid_page-limit`，20|30|50|100）、当前页 input、`of N`、Next/Previous 按钮（title 指示方向）。
- 进入详情页：点行内 View/Edit 链接、点行本身（tr title 即详情 URL），或直接 `navigate`；长页需多次 `scroll`；返回列表用顶部 `Back`（id=back）。
- 回到列表后筛选值保留，可继续操作其余结果行；再搜索前视需要先 `Reset Filter`。
- `id=add` 在不同列表页含义不同：订单页=Create New Order，客户页=Add New Customer，评论页=New Review——按所在页与 title/可见文本区分。