# Selectors — localhost

多数元素已在 sop_md 用稳定 id/name 描述。需指纹的补充：

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 网格行选中 checkbox（订单/客户） | 每行首列 | `type=checkbox`, id 形如 `idscheck25` | id/value 数字即实体 id |
| 评论网格行 checkbox | 每行首列 | `type=checkbox`, `name=reviews`, value=评论 id | 靠 name+value 定位行 |
| 评论筛选输入（商品名/SKU/标题等） | 评论网格表头下筛选行 | `id=reviewGrid_filter_name` / `_sku` / `_title` / `_nickname` / `_detail` / `_review_id` | id 稳定可复用 |
| 评论 Status/Type/massaction 筛选下拉 | 评论网格筛选行 | `id=reviewGrid_filter_status` / `id=reviewGrid_filter_type` / `id=reviewGrid_filter_massaction` | visible_in 下拉靠 `name=visible_in` |
| 评论日期范围输入 | 筛选行 | `name=created_at[from]`, `name=created_at[to]`, `placeholder=mm/dd/yyyy` | id 含随机后缀，勿用 |
| 评论 Search / Reset Filter 按钮 | 评论页网格右上方 | `title=Search` / `title=Reset Filter` | id 为随机长串，每次加载变 |
| 评论每页条数 / 当前页 / 翻页 | 网格底部 | `id=reviewGrid_page-limit` / `id=reviewGrid_page-current`, 可见文本"Next page" | page-limit 选项 20|30|50|100 |
| 评论批量操作下拉 | 网格上方 | `id=reviewGrid_massaction-select` / `id=reviewGrid_massaction-mass-select` | Submit 按钮靠可见文本 |
| 订单 Status 筛选下拉 | 订单 Filters 面板 | `name=status` | id 随机；直接 select_dropdown |
| 订单金额/ID/姓名筛选 | Filters 面板 | `name=base_grand_total[from]` / `name=increment_id` / `name=billing_name` / `name=shipping_name` / `name=transaction_source` | id 均随机 |
| 客户电话/姓名/邮箱筛选 | Filters 面板 | `name=billing_telephone` / `name=name` / `name=email` / `name=billing_postcode` / `name=billing_region` | id 随机，靠 name |
| Group/Country/Purchase Point 下拉 | Filters 面板 | `name=group_id` / `name=billing_country_id` / `name=store_id` | |
| Apply Filters / Filters / Clear all 按钮 | Filters 面板底部 / 控制条 | 可见文本"Apply Filters" / "Filters" / "Clear all" | id 随机，靠可见文本 |
| 激活筛选 chip 的 Remove 按钮 | "Active filters:" 行 chip 旁 | 可见文本"Remove" | type=button |
| UI 网格全文搜索框 | 列表页控制条右侧 | `id=fulltext`, `placeholder=Search by keyword` | 各 UI 网格通用 |
| 详情页返回按钮 | 订单/发票/评论详情页顶部 | `id=back`, 可见文本"Back" | |
| 评论编辑页字段 | Edit Review 页 | `id=nickname` / `id=title` / `id=detail` / `id=status_id` | 均 required，提交用 `id=save_button` |
| Dashboard Scope 切换 | Dashboard 页头 "Scope:" 旁 | `id=store-change-button`, 可见文本"All Store Views" | 旁有 "What is this?" 与 Reload Data |