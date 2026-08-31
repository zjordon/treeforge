# localhost (Magento Admin) 站点卡片

## 站点功能地图（Site Function Map）

- 入口: `http://localhost:7780/admin/admin/dashboard/`（Bestsellers/Most Viewed/New Customers/Customers 标签、Lifetime Sales、Last Orders 面板；表格行 `tr` 的 title 属性即详情 URL）
- 商品管理: `Catalog` → `Products` → `/admin/catalog/product/`
- 订单管理: `Sales` → `Orders` → `/admin/sales/order/`（300+ records）
- 订单详情: `sales/order/view/order_id/N/`，顶部 Back/Edit/Cancel/Invoice/Ship；Information/Invoices/Credit Memos/Shipments/Comments History 标签；底部 Notes：`id=history_status` 下拉、`id=history_comment` textarea、`title=Submit Comment`
- 发票管理: `Sales` → `Invoices` → `/admin/sales/invoice/`；详情 `sales/invoice/view/invoice_id/N/`
- 客户管理: `Customers` → `All Customers` → `/admin/customer/index/`（`Add New Customer` id=add）
- 商品评论: `Marketing` → `Reviews` → `All Reviews` → `/admin/review/product/index/`（全部约 351 records；顶部 `New Review`(id=add)、Search、Reset Filter）。按 Status 筛选（如 Pending）后剩约 5 条，URL 变为 `/admin/review/product/index/filter/<base64>/internal_reviews//form_key/<随机>/`
- 评论详情/编辑: `/admin/review/product/edit/id/N/`。字段：Status `id=status_id`（Approved|Pending|Not Approved）、Nickname `id=nickname`、Summary `id=title`、正文 `id=detail`；按钮 Back(id=back)/Reset/Delete Review/Save Review(id=save_button)/Save and Next/Next/Previous——按评论位置显示不同组合
- 侧边菜单（li id 稳定）: Dashboard `menu-magento-backend-dashboard` / Sales / Catalog / Customers / Marketing `menu-magento-backend-marketing` / Content / Reports / Stores / System / Find Partners & Extensions
- 页头全局搜索: `input id=search-global`；UI-Component 网格右上全文搜索 `id=fulltext placeholder="Search by keyword"` + Search 按钮
- Scope 切换: `id=store-change-button`（All Store Views）+ Reload Data 按钮

## 站点通用操作知识

- 侧边菜单两级：点一级项（Sales/Marketing）展开后二级项（Orders/All Reviews）才出现；每步点击后重读 DOM，index 会变。
- 两代网格：
  - 新 UI-Component 网格（订单/客户/商品）：`Default View`/`Columns`/`Export`/`Filters` 控制条；点 `Filters` 同页 AJAX 展开筛选面板，填完 `Apply Filters`（靠可见文本），清除用 `Clear all` 或单个 chip 旁 `Remove`。
  - 旧版网格（评论页 reviewGrid）：**无 Filters 按钮**，筛选行直接在表头下方；填完点顶部 `Search`（title=Search），清除用 `Reset Filter`。筛选字段 id 稳定：`reviewGrid_filter_name/sku/status/type/title/nickname/detail/review_id`、created_at[from]/[to]。
- 订单 Filters 面板字段（靠 name，id 均随机）：`created_at[from]/[to]`、`base_grand_total[from]/[to]`、`grand_total[from]/[to]`、`store_id`、`increment_id`、`billing_name`/`shipping_name`、`status`、`transaction_source`；客户面板另含 `billing_telephone`/`email`/`group_id`/`billing_country_id` 等。
- 下拉筛选（Status/Type/Store、评论网格下拉）直接 `select_dropdown(index, value)`，无需先点击展开；`dropdown_options` 可先读选项。
- 日期输入 `placeholder=mm/dd/yyyy`，手输日期最稳（弹层按钮文本为 undefined，不可靠）。
- 筛选结果确认：UI 网格看 "records found" 计数 + "Active filters:"；旧版评论网格 Search 后整页跳转，看行数、"N records found" 与输入框保留值确认。
- 列排序：点表头 th 同页 AJAX，行序与 index 变化。
- 分页：网格底部每页条数下拉（评论网格 `id=reviewGrid_page-limit`，20|30|50|100|200）、当前页 input（`id=reviewGrid_page-current`）、`of N`、Next/Previous page 按钮（title 指示方向）。
- 批量操作（评论网格）：`reviewGrid_massaction-select`（Actions|Delete|Update Status）+ `reviewGrid_massaction-mass-select`（Select All/Select Visible 等）+ 行首 `name=reviews` checkbox，最后 Submit。
- 进入详情页：点行内 View/Edit 链接、点行本身（评论行可点击，tr title 即编辑 URL），或直接 `navigate` 详情 URL；详情页长需多次 `scroll`；返回列表用顶部 `Back`（id=back）。
- 回到列表后筛选值保留，可继续操作其余结果行。