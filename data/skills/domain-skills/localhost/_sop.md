# localhost (Magento 2.4.6) 站点卡片

## 站点功能地图（Site Function Map）

- 管理后台入口: `http://localhost:7780/admin/`（登录用户 admin）；店面入口 `http://localhost:7780/`
- Dashboard: `/admin/admin/dashboard/`
- 商品管理: `Catalog` → `Products` → `/admin/catalog/product/`（约 2040 条；Add Product `id=add_new_product-button`，下拉含 Simple/Configurable/Grouped/Virtual/Bundle/Downloadable，选后进 `/admin/catalog/product/new/set/N/type/<type>/`）
- 商品编辑: `/admin/catalog/product/edit/id/N/`；顶部 Save(`id=save-button`)/Save options/Back(`id=back`)/Add Attribute(`id=addAttribute`)；字段靠 `name=product[...]`
- 可配置商品变体（编辑页 Configurations 区）：`Edit Configurations` 三步向导；`Create Configurations`（新建页）；`Generate Products` 生成简单变体；生成后 Current Variations 含 `name=configurable-matrix[N][...]` 输入框
- 订单: `Sales` → `Orders`（`id=add`=Create New Order）；发票/Credit Memos 同级菜单
- 客户: `Customers` → `All Customers` → `/admin/customer/index/`
- 商品评论: `Marketing` → `Reviews` → `All Reviews` → `/admin/review/product/index/`（约 351 条）；编辑 `/admin/review/product/edit/id/N/`；编辑页可删评论（`id=delete`）
- 缓存管理: `System` → `Cache Management`（`id=flush_magento`、`id=flush_system`）
- CMS: `Content` → `Pages`（`id=add`=Add New Page）
- 报表: `Reports` → `Products`→Bestsellers、`Sales`→Orders/Refunds
- 侧边菜单（li id 稳定）: Dashboard/Sales/Catalog/Customers/Marketing/Content/Reports/Stores/System/Find Partners & Extensions
- 页头全局搜索 `id=search-global`；UI 网格右上 `id=fulltext`
- 店面商品页标签: `Details`(`id=tab-label-description-title`)/`More Information`/`Reviews`

## 站点通用操作知识

- 侧边菜单两级：点一级展开后二级才出现；每步点击后重读 DOM；跨区直接点侧边菜单。
- 三类网格/表单：新 UI-Component 网格（订单/客户/商品/CMS）Filters 同页 AJAX、`Apply Filters`/`Clear all`；旧版网格（评论 reviewGrid）筛选行在表头下方，`title=Search` 提交、`Reset Filter` 清除，整页跳转；报表 `filter_form` + `filter_form_submit`。
- 评论筛选字段（id 稳定）：`reviewGrid_filter_name`/`_sku`/`_title`/`_nickname`/`_detail`/`_review_id`/`_status`/`_type`；日期 `name=created_at[from]/[to]`。填后点 `title=Search`，看 "N records found"。状态/类型筛选用 `select_dropdown(index, value)` 直接选，无需先点开。
- **评论筛选+编辑流程**（stage nnsvenpa6mloayfq）：列表页 `select_dropdown` 选 `reviewGrid_filter_status`（Approved/Pending/Not Approved）+ 填 `reviewGrid_filter_name` 等 → 点 `title=Search`（整页跳转 base64 filter URL，重读 DOM）→ 滚动找目标行 → 点行尾 "Edit" → 编辑页改 `id=status_id`（`select_dropdown`）→ `id=save_button` 保存 → 整页回列表 "You saved the review."。用筛选缩小范围比翻页更快。
- **删除评论流程**（stage 353/353_5）：编辑页点 `id=delete`（"Delete Review"）→ 弹出确认对话框（`role=dialog`，内含 Cancel/OK 按钮，OK 无稳定 id，靠可见文本"OK"）→ 点 OK → 整页回评论列表，显示 "The review has been deleted."；若筛选后无剩余记录显示 "We couldn't find any records."（删除成功即代表完成，勿因空列表误判失败）。
- **新建商品流程**：Products 页点 `id=add_new_product` 选类型 → `/new/set/4/type/simple/`。先选 Attribute Set（点击 "Default" 打开面板）——切属性集会增减属性字段，选完再填字段。核心字段：`name=product[name]`/`[sku]`/`[price]`/`[quantity_and_stock_status][qty]`/`[...][is_in_stock]`。Categories：点 "Select..." 勾树 checkbox 后点 "Done"。最后 `id=save-button`，成功 URL 变 `/edit/id/<新id>/...` 并显示 "You saved the product."。
- 商品编辑页字段靠 `name=product[...]`（id 每次加载随机）；文本 `input_text(clear=true)`，下拉 `select_dropdown(index, value)`，布尔 checkbox 用 `click`，保存 `id=save-button`。
- 多选属性（activity/style_general/material/sleeve/collar/pattern/climate）是 multiselect select，选项 value 为数字 id 而非文本，带 "X pages below" 滚动提示。
- 可配置变体：编辑页向下 `scroll` 到 Configurations 区，点 `Edit Configurations`；向导三步用 `Next` 推进，最后 `Generate Products`，回到编辑页后 Current Variations 变为可编辑矩阵。
- 矩阵输入框靠 `name=configurable-matrix[N][price]/[qty]/[weight]/[name]/[sku]` 识别；每行另有 `type=file` 图片输入和 Select 操作。
- 后台改商品后前台可能被 Full Page Cache 缓存——`System → Cache Management` 点 `id=flush_magento` 后再刷新前台验证。
- 结果确认："N records found"、"You saved the product."/"You saved the review."/"The review has been deleted."、缓存 "The Magento cache storage has been flushed."；无结果 "We couldn't find any records."。
- 列排序点表头 th；翻页 title=Next/Previous Page；长页 `scroll`（"X pages below" 提示）。
- `id=add` 跨页含义不同，按 title 区分。