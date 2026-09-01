# Selectors — localhost

多数元素已在 sop_md 用稳定 id/name 描述。需指纹的补充：

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 网格行选中 checkbox（订单/客户/CMS） | 每行首列 | `type=checkbox`, id 形如 `idscheck25` | id/value 数字即实体 id |
| 评论网格行 checkbox | 每行首列 | `type=checkbox`, `name=reviews`, value=评论 id | 靠 name+value 定位行 |
| 行尾 Select 操作下拉（CMS/订单等 UI 网格） | 每行最后一列 | 可见文本"Select"，展开后 Edit/Delete/View | 点击后 index 变，重读 DOM 再选 |
| 评论筛选输入 | 评论网格表头下筛选行 | `id=reviewGrid_filter_name` / `_sku` / `_title` / `_nickname` / `_detail` / `_review_id` | id 稳定可复用 |
| 评论 Status/Type/massaction 下拉 | 筛选行 | `id=reviewGrid_filter_status` / `_type` / `_massaction` | visible_in 下拉靠 `name=visible_in` |
| 评论日期范围输入 | 筛选行 | `name=created_at[from]`, `name=created_at[to]`, `placeholder=mm/dd/yyyy` | id 含随机后缀，勿用 |
| 评论 Search / Reset Filter 按钮 | 网格右上方 | `title=Search` / `title=Reset Filter` | id 随机，靠 title |
| 评论每页条数 / 翻页 | 网格底部 | `id=reviewGrid_page-limit`, 可见文本"Next page" | 20|30|50|100 |
| 评论批量操作下拉 | 网格上方 | `id=reviewGrid_massaction-select` | Submit 靠可见文本 |
| 订单 Status 筛选下拉 | Filters 面板 | `name=status` | id 随机 |
| 订单金额/ID/姓名筛选 | Filters 面板 | `name=base_grand_total[from]` / `name=increment_id` / `name=billing_name` / `name=shipping_name` / `name=transaction_source` | id 均随机 |
| 客户电话/姓名/邮箱筛选 | Filters 面板 | `name=billing_telephone` / `name=name` / `name=email` / `name=billing_postcode` / `name=billing_region` | id 随机 |
| Group/Country/Purchase Point 下拉 | Filters 面板 | `name=group_id` / `name=billing_country_id` / `name=store_id` | |
| Apply Filters / Filters / Clear all | 面板底部/控制条 | 可见文本"Apply Filters" / "Filters" / "Clear all" | id 随机 |
| 激活筛选 chip 的 Remove | "Active filters:" 行 chip 旁 | 可见文本"Remove" | type=button |
| UI 网格全文搜索框 | 控制条右侧 | `id=fulltext`, `placeholder=Search by keyword`, `aria-label=Search by keyword` | 各 UI 网格通用 |
| 每页条数 input（UI 网格） | 分页条 | `type=text`, id 含 `listing_paging_sizes` | 配 "Select per page" 文本 |
| 当前页 input（UI 网格） | 分页条 | `type=number`, compound_components 含 Value | Next/Previous 靠 title |
| 详情页返回按钮 | 详情页顶部 | `id=back`, 可见文本"Back" | |
| 评论编辑页字段 | Edit Review 页 | `id=nickname` / `id=title` / `id=detail` / `id=status_id` | 均 required，提交 `id=save_button` |
| 报表 Date Used / Order Status 范围 | filter_form 内（Orders/Refunds） | `id=sales_report_report_type` / `id=sales_report_show_order_statuses` | 值数字格式；选项随报表类型不同；Bestsellers 无此二字段 |
| 报表多选订单状态下拉 | filter_form 内 | `id=sales_report_order_statuses`, `name=order_statuses[]` | multiple；可能不在初始 DOM |
| 报表 Period/From/To/Empty Rows/Actual | filter_form 内 | `id=sales_report_period_type` / `id=sales_report_from` / `id=sales_report_to` / `id=sales_report_show_empty_rows` / `id=sales_report_show_actual_columns` | From/To required |
| 报表 Show Report / Export 按钮 | 页面右上 | `id=filter_form_submit`, `title=Show Report` / 可见文本"Export" | Export 按钮 id 随机 |
| 报表 Export 下拉 | "Export to:" 旁 | `role=listbox`, 选项 CSV / Excel XML | id 每次加载都变 |
| 表头排序按钮 | 网格表头 | `<th>` 内 `title=Sort` 按钮，靠列名上下文识别 | id 随机 |
| 页头全局搜索框 | 顶部 admin/Notifications 旁 | `id=search-global`, `type=text`, `autocomplete=off` | 页面各处通用 |
| Dashboard 标签页 | Dashboard 中部 | `role=tab`, id 如 `grid_tab_ordered_products` / `grid_tab_reviewed_products` / `grid_tab_new_customers` / `grid_tab_customers`, title=标签名 | 标签内 span 均装饰性，忽略 |