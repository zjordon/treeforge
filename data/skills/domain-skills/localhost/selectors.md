# Selectors — localhost

多数元素已在 sop_md 用稳定 id/name 描述。需指纹的补充：

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 网格行选中 checkbox（订单/客户/CMS/商品） | 每行首列 | `type=checkbox`, id 形如 `idscheck350` | id/value 数字即实体 id |
| 商品行 Edit 链接 | 每行最后一列 Action | `aria-label=Edit <商品全名>` | 同名变体靠完整名称（含尺码-颜色后缀）区分 |
| 商品网格 Filters 面板文本输入 | Filters 展开面板 | `name=name` / `name=sku` / `name=entity_id[from]` / `name=price[from]` / `name=qty[from]` / `name=updated_at[from]`（[to] 同理） | id 均随机，靠 name；数量筛选即 `qty[from]/[to]` |
| 商品网格 Filters 下拉 | Filters 展开面板 | `name=store_id` / `name=type_id` / `name=attribute_set_id` / `name=visibility` / `name=status` | 原生 select，id 随机 |
| Apply Filters / Filters / Clear all | 面板底部/控制条 | 可见文本"Apply Filters" / "Filters" / "Clear all" | id 随机 |
| 激活筛选 chip 及移除 | "Active filters:" 行 | 可见文本 "Keyword:" / "Name:" / "SKU:" 等 + chip 旁按钮 | 显示当前已应用筛选值 |
| UI 网格全文搜索框+Search 按钮 | 控制条右侧 | `id=fulltext`, `placeholder=Search by keyword`；按钮 `aria-label=Search` | 各 UI 网格通用 |
| 每页条数/当前页 input（UI 网格） | 分页条 | id 含 `listing_paging_sizes`；`type=number` 当前页 | Next/Previous 靠 title，禁用即无更多页 |
| 详情页返回按钮 | 详情页顶部 | `id=back`, 可见文本"Back" | |
| 商品编辑 Price/Name/SKU/Qty/Stock/Weight/Enable Product | 对应 label 旁 | `name=product[price]` / `product[name]` / `product[sku]` / `product[quantity_and_stock_status][qty]` / `product[quantity_and_stock_status][is_in_stock]` / `product[weight]` / `product[status]`（`type=checkbox`，value=2） | id 随机每次加载变 |
| 商品编辑多选属性 select | 编辑页下方属性区 | `name=product[activity]` / `product[style_general]` / `product[material]` / `product[pattern]` / `product[climate]`, `role=listbox` | 需 scroll 后才出现 |
| 评论筛选输入 | 评论网格表头下筛选行 | `id=reviewGrid_filter_name` / `_sku` / `_title` / `_nickname` / `_detail` / `_review_id` | id 稳定 |
| 评论 Status/Type 下拉 | 筛选行 | `id=reviewGrid_filter_status` / `_type` | |
| 评论 Search / Reset Filter | 网格右上方 | `title=Search` / `title=Reset Filter` | id 随机 |
| 订单 Status 筛选下拉 | Filters 面板 | `name=status` | id 随机 |
| 订单金额/ID/姓名筛选 | Filters 面板 | `name=base_grand_total[from]` / `name=increment_id` / `name=billing_name` / `name=shipping_name` / `name=transaction_source` | id 均随机 |
| 客户电话/姓名/邮箱筛选 | Filters 面板 | `name=billing_telephone` / `name=name` / `name=email` | id 随机 |
| Group/Country/Store View 下拉 | Filters 面板 | `name=group_id` / `name=billing_country_id` / `name=store_id` | |
| 评论编辑页字段 | Edit Review 页 | `id=nickname` / `id=title` / `id=detail` / `id=status_id` | 均 required，提交 `id=save_button` |
| 报表 Date Used / Order Status 范围 | filter_form 内 | `id=sales_report_report_type` / `id=sales_report_show_order_statuses` | 值数字格式；Bestsellers 无此二字段 |
| 报表多选订单状态下拉 | filter_form 内 | `id=sales_report_order_statuses`, `name=order_statuses[]` | 可能不在初始 DOM |
| 报表 Period/From/To/Empty/Actual | filter_form 内 | `id=sales_report_period_type` / `_from` / `_to` / `sales_report_show_empty_rows` / `sales_report_show_actual_columns` | From/To required |
| 报表 Show Report / Export | 页面右上 | `id=filter_form_submit`, `title=Show Report` / 可见文本"Export" | Export 按钮 id 随机 |