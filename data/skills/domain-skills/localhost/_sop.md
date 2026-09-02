# localhost (Magento Admin) 站点卡片

## 站点功能地图（Site Function Map）

- 入口: `http://localhost:7780/admin/admin/dashboard/`（Bestsellers/Most Viewed/New Customers/Customers 标签、Lifetime Sales、Last Orders、Last/Top Search Terms 面板；表格行 `tr` 的 title 属性即详情 URL）
- 商品管理: `Catalog` → `Products` → `/admin/catalog/product/`（Add Product `id=add_new_product-button`）
- 商品编辑: `/admin/catalog/product/edit/id/N/`；顶部 Save(`id=save-button`)/Save options(`aria-label=Save options`)/Back(`id=back`)/Add Attribute(`id=addAttribute`)；字段靠 `name=product[...]`（Name、SKU、Price、Tax Class、Quantity、Stock Status `product[quantity_and_stock_status][is_in_stock]`、Enable Product `product[status]`（checkbox，value=2）、Weight、Visibility、Country of Manufacture、日期、多选属性 activity/style_general/material/pattern/climate 等）；保存成功提示 "You saved the product."
- 订单管理: `Sales` → `Orders` → `/admin/sales/order/`（`Create New Order` id=add）
- 订单详情: `sales/order/view/order_id/N/`，Information/Invoices/Credit Memos/Shipments/Comments History 标签；底部 `id=history_status`、`id=history_comment`、`title=Submit Comment`
- 发票管理: `Sales` → `Invoices`；详情 `sales/invoice/view/invoice_id/N/`
- 客户管理: `Customers` → `All Customers` → `/admin/customer/index/`
- 商品评论: `Marketing` → `Reviews` → `All Reviews` → `/admin/review/product/index/`；筛选后 URL 含 base64 filter
- 评论编辑: `/admin/review/product/edit/id/N/`。Status `id=status_id`、Nickname `id=nickname`、Summary `id=title`、正文 `id=detail`；Back/Reset/Delete/Save(`id=save_button`)/Save and Next/Previous
- CMS 页面: `Content` → `Pages`（`Add New Page` id=add）
- 报表-Bestsellers: `Reports` → `Products` → `Bestsellers` → `/admin/reports/report_sales/bestsellers/`；仅 Period/From/To/Empty Rows/Export
- 报表-Orders(Sales): `Reports` → `Sales` → `Orders` → `/admin/reports/report_sales/sales/`；Date Used `id=sales_report_report_type` 等
- 报表-Refunds: `Reports` → `Sales` → `Refunds` → `/admin/reports/report_sales/refunded/`
- 侧边菜单（li id 稳定）: Dashboard `menu-magento-backend-dashboard` / Sales / Catalog / Customers / Marketing / Content / Reports `menu-magento-reports-report` / Stores / System / Find Partners & Extensions
- 页头全局搜索: `input id=search-global`；UI-Component 网格右上 `id=fulltext`（`aria-label=Search` 按钮提交）
- Scope 切换: `id=store-change-button` + Reload Data

## 站点通用操作知识

- 侧边菜单两级：点一级项展开后二级项才出现；每步点击后重读 DOM。跨区跳转直接点侧边菜单。
- 三类网格/表单：
  - 新 UI-Component 网格（订单/客户/商品/CMS）：`Filters` 同页 AJAX 展开，`Apply Filters`（靠可见文本），清除用 `Clear all`；已应用筛选以 "Active filters:" chip 展示。
  - 旧版网格（评论页 reviewGrid）：筛选行在表头下方；`Search` 提交，`Reset Filter` 清除。
  - 报表筛选表单：`id=filter_form`，`id=filter_form_submit` 整页跳转。
- UI 网格公共控件：Default View/Columns/Filters、`id=fulltext` 关键字搜索；行首 checkbox（`idscheckN`，value 即实体 id）+ 行尾 `aria-label=Edit <完整名>` 链接；底部每页条数/页码/Next/Previous。关键字搜索与 Filters 叠加生效（如 keyword 搜 "Teton Pullover Hoodie" 返回 16 条含父商品+全部变体）。
- fulltext 搜索流程：换关键词前先点 "Clear all" → `input_text` 到 `id=fulltext` → 点 `aria-label=Search` 按钮 → 同页 AJAX，看 "N records found"（无结果 "We couldn't find any records."）。
- 商品网格 Filters 面板字段（靠 name）：`name`、`sku`、`entity_id[from]/[to]`、`price[from]/[to]`、`qty[from]/[to]`、`updated_at[from]/[to]`（mm/dd/yyyy）、下拉 `store_id`/`type_id`/`attribute_set_id`/`visibility`/`status`。
- 报表 filter_form 字段：`sales_report_report_type`（仅 Orders/Refunds）、`sales_report_period_type`、`sales_report_from/to`（required，接受 `5/1/21` 短格式）、`sales_report_show_order_statuses`、多选 `order_statuses[]`、`show_empty_rows`、`show_actual_columns`（仅 Orders）、Export 下拉（CSV|Excel XML）+ Export 按钮。
- 订单 Filters：`created_at[from]/[to]`、`base_grand_total[from]/[to]`、`increment_id`、`billing_name`/`shipping_name`、`status`、`transaction_source`；客户面板另含 `billing_telephone`/`email`/`group_id` 等。
- 商品编辑页字段一律靠 `name=product[...]` 定位（input/select id 随机每次加载变化）；改价格/数量用 `input_text(index, text, clear=true)`，下拉（如 Stock Status）用 `select_dropdown(index, value)`，Enable Product 开关用 `click` 勾选/取消 checkbox（或点其 label），然后点 `id=save-button`。多变体批量编辑：列表点 `aria-label=Edit <完整变体名>`（同款变体仅尺码-颜色后缀不同），Save 后 Back(`id=back`) 回列表，重读 DOM 再下一个；筛选保留，无需重搜。
- 下拉筛选直接 `select_dropdown(index, value)` 无需先点击；`dropdown_options` 可先读选项；传 value 而非显示文本（见 quirks）。
- 日期输入手输最稳（弹层按钮文本 undefined）。
- 筛选/保存结果确认：看 "N records found"；商品保存生效后列表 Last Updated At 变为当前时间、Price/Quantity 列更新（Quantity 与 Salable Quantity 可能略有差值，属正常）。
- 列排序：点表头 th 同页 AJAX，index 变化。
- 进详情：行内 `aria-label=Edit <name>` 链接、点行本身（tr title 即详情 URL）或直接 `navigate`；长页需多次 `scroll`（商品编辑页属性区约 2~6 屏）；返回用顶部 `Back`（id=back）。
- `id=add` 跨页含义不同：订单=Create New Order、客户=Add New Customer、评论=New Review、CMS=Add New Page——按 title/可见文本区分。
- Dashboard "JavaScript may be disabled" 警告块与各 tab 前装饰性 span 可忽略。